// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// usb_bluetooth, from Portall: https://github.com/youkorr/esphome_esp-video
//
// usb_bluetooth's own USB host layer, on ESP-IDF's USB Host Library. What it is
// and why it speaks CherryUSB's names is in idf_usb.h. Compiled only with
// `transport: idf`, which defines HCI_USB_IDF; without it this file is empty.
#include "usb_bluetooth.h"

#if defined(USE_ESP32) && defined(HCI_USB_IDF)

#include "idf_usb.h"

#include <cstring>

#include "esphome/core/log.h"

#include "esp_err.h"
#include "esp_intr_alloc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {

const char *const TAG = "usb_bluetooth.usb";

// How long a control transfer may take before its caller is told it timed out.
// CherryUSB's own default is 500 ms. A Realtek fragment is 256 bytes on a
// 64-byte control endpoint, so this gives it a second.
constexpr uint32_t CONTROL_TIMEOUT_MS = 1000;

// The largest data stage that goes out on the control endpoint. An HCI command
// is 3 bytes of header and at most 255 of parameters; usb_bluetooth.cpp's own
// command buffer is 264, so anything it can build fits.
constexpr uint32_t CONTROL_DATA_MAX = 264;

// How long a cancelled transfer is given to come back. Flushing an endpoint
// completes what was queued on it with CANCELED, from the client task.
constexpr uint32_t CANCEL_WAIT_MS = 200;

// How often the client task looks up from the library to open a device that
// has arrived or close one that has gone.
constexpr uint32_t CLIENT_SLICE_MS = 20;

// The HCI interface has three endpoints. A fourth slot costs nothing.
constexpr int MAX_PIPES = 4;

// Task names are at most 15 characters on FreeRTOS; these survive the rename
// the usb_bluetooth copy is made with, where a component's name would not.
constexpr uint32_t TASK_STACK = 4096;
constexpr unsigned TASK_PRIORITY = 5;

// One endpoint: its transfer, and the semaphore its callback gives.
//
// `in_flight` is the LIBRARY's view: it holds the transfer, and the callback
// clears this. `pending` is the CALLER's: a transfer was submitted and nobody
// has collected its result yet. They differ in one case, which is the reason
// both exist. A read that timed out leaves its transfer pending, and the
// next read on that endpoint collects it instead of submitting another.
struct Pipe {
  uint8_t address = 0;  // 0 = unused, except for the control pipe
  usb_transfer_t *xfer = nullptr;
  SemaphoreHandle_t done = nullptr;
  volatile bool in_flight = false;
  bool pending = false;
  // Ended in error. The library halts such an endpoint and it stays halted
  // until usb_host_endpoint_clear(); the next submit clears it first.
  bool halted = false;
  bool said_too_long = false;
};

struct Host {
  usb_host_client_handle_t client = nullptr;
  usb_device_handle_t dev = nullptr;
  usbh_event_handler_t handler = nullptr;
  volatile bool open = false;       // a device is open and its descriptors read
  volatile bool gone = false;       // it was unplugged; closed once idle
  volatile bool want_open = false;  // a device has arrived and waits to be opened
  volatile uint8_t new_addr = 0;
  uint16_t claimed = 0;  // interface numbers claimed, one bit each
  Pipe pipes[MAX_PIPES];
  Pipe control;
  SemaphoreHandle_t control_lock = nullptr;
};

Host g_host;
usbh_hubport g_port{};
usb_setup_packet g_setup{};
char g_manufacturer[64];
char g_product[64];
char g_serial[64];

// ---------------------------------------------------------------------------
// Transfers
// ---------------------------------------------------------------------------

// The library calls this from usb_host_client_handle_events(), on the client
// task below and nowhere else.
void on_transfer(usb_transfer_t *transfer) {
  Pipe *pipe = static_cast<Pipe *>(transfer->context);
  pipe->in_flight = false;
  xSemaphoreGive(pipe->done);
}

// What a finished transfer's status means, in CherryUSB's numbers.
// CANCELED is deliberately not SHUTDOWN: usb_bluetooth.cpp reads SHUTDOWN as the
// dongle being gone for good, and the only thing that cancels a transfer here
// is this layer, giving up on one that took too long.
int status_error(usb_transfer_status_t status) {
  switch (status) {
    case USB_TRANSFER_STATUS_COMPLETED:
      return 0;
    case USB_TRANSFER_STATUS_NO_DEVICE:
      return -USB_ERR_NODEV;
    case USB_TRANSFER_STATUS_STALL:
      return -USB_ERR_STALL;
    case USB_TRANSFER_STATUS_OVERFLOW:
      return -USB_ERR_BABBLE;
    case USB_TRANSFER_STATUS_TIMED_OUT:
      return -USB_ERR_TIMEOUT;
    default:
      return -USB_ERR_IO;
  }
}

// What a failure looks like to a caller once the device may have gone.
int failed() { return g_host.gone || !g_host.open ? -USB_ERR_NODEV : -USB_ERR_IO; }

bool wait_done(Pipe *pipe, uint32_t ms) {
  const TickType_t ticks = ms == 0 ? portMAX_DELAY : pdMS_TO_TICKS(ms);
  return xSemaphoreTake(pipe->done, ticks) == pdTRUE;
}

// A transfer buffer at least `size` long. Taken from the library, which puts it
// where its DMA can reach. Kept for the life of the firmware and grown only
// when nothing is using it.
bool ensure_buffer(Pipe *pipe, uint32_t size) {
  if (pipe->xfer != nullptr && pipe->xfer->data_buffer_size >= size)
    return true;
  if (pipe->pending || pipe->in_flight)
    return false;
  if (pipe->xfer != nullptr) {
    usb_host_transfer_free(pipe->xfer);
    pipe->xfer = nullptr;
  }
  if (usb_host_transfer_alloc(size, 0, &pipe->xfer) != ESP_OK) {
    pipe->xfer = nullptr;
    return false;
  }
  pipe->xfer->callback = on_transfer;
  pipe->xfer->context = pipe;
  return true;
}

// Collects a finished transfer. Errors that halt the endpoint are remembered,
// so the next transfer on it clears the halt first.
int reap(Pipe *pipe) {
  pipe->pending = false;
  const usb_transfer_status_t status = pipe->xfer->status;
  if (status == USB_TRANSFER_STATUS_ERROR || status == USB_TRANSFER_STATUS_STALL ||
      status == USB_TRANSFER_STATUS_OVERFLOW)
    pipe->halted = pipe != &g_host.control;
  return status_error(status);
}

int submit(Pipe *pipe, int num_bytes) {
  if (pipe->halted) {
    usb_host_endpoint_clear(g_host.dev, pipe->address);
    pipe->halted = false;
  }
  pipe->xfer->num_bytes = num_bytes;
  pipe->xfer->device_handle = g_host.dev;
  pipe->xfer->bEndpointAddress = pipe->address;
  pipe->xfer->flags = 0;
  pipe->xfer->timeout_ms = 0;
  pipe->in_flight = true;
  if (usb_host_transfer_submit(pipe->xfer) != ESP_OK) {
    pipe->in_flight = false;
    return failed();
  }
  pipe->pending = true;
  return 0;
}

// Gives up on a transfer that will not finish: halt the endpoint, flush it,
// which completes the transfer CANCELED, then clear the halt. Only ever for
// an OUT. An IN that has not finished is left to finish.
void cancel(Pipe *pipe) {
  usb_host_endpoint_halt(g_host.dev, pipe->address);
  usb_host_endpoint_flush(g_host.dev, pipe->address);
  if (wait_done(pipe, CANCEL_WAIT_MS))
    pipe->pending = false;
  usb_host_endpoint_clear(g_host.dev, pipe->address);
  pipe->halted = false;
}

uint32_t round_up(uint32_t length, uint32_t mps) { return ((length + mps - 1) / mps) * mps; }

// One IN transfer, or the rest of one that an earlier call gave up waiting
// for.
//
// THE ONE PLACE THIS LAYER MUST NOT LOSE A PACKET. The library never times a
// transfer out, so the timeout is ours -- and the obvious way to honour it,
// cancelling the transfer, throws away whatever the dongle sends a moment
// later. Every packet that went that way would be part of an event: the rest
// of that event would then be read as a new one, which is the desynchronised
// stream usb_bluetooth.cpp's readers were rewritten twice to prevent. So a read
// that times out leaves its transfer where it is, and the next read on the
// endpoint collects it. The readers ask for one packet at a time, so the next
// read wants exactly what this one asked for.
int read_in(Pipe *pipe, uint8_t *out, uint32_t length, uint32_t mps, uint32_t ms, int *got) {
  *got = 0;
  if (!pipe->pending) {
    // The library wants an IN transfer to be whole packets.
    const uint32_t want = round_up(length, mps);
    if (!ensure_buffer(pipe, want))
      return -USB_ERR_NOMEM;
    const int err = submit(pipe, (int) want);
    if (err < 0)
      return err;
  }
  if (!wait_done(pipe, ms))
    return g_host.gone ? -USB_ERR_NODEV : -USB_ERR_TIMEOUT;
  const int err = reap(pipe);
  if (err < 0)
    return err;
  int n = pipe->xfer->actual_num_bytes;
  if (n > (int) length) {
    // Cannot happen with one-packet reads; said once if it ever does, rather
    // than writing past the caller's buffer.
    if (!pipe->said_too_long) {
      pipe->said_too_long = true;
      ESP_LOGW(TAG, "endpoint %02x returned %d bytes to a read of %u; the rest is dropped",
               pipe->address, n, (unsigned) length);
    }
    n = (int) length;
  }
  memcpy(out, pipe->xfer->data_buffer, n);
  *got = n;
  return 0;
}

int write_out(Pipe *pipe, const uint8_t *data, uint32_t length, uint32_t ms) {
  if (pipe->pending) {
    // The last one timed out and was cancelled without coming back. If it has
    // come back since, collect it; if not, try once more to cancel it.
    if (wait_done(pipe, 0)) {
      reap(pipe);
    } else {
      cancel(pipe);
      if (pipe->pending)
        return -USB_ERR_BUSY;
    }
  }
  if (!ensure_buffer(pipe, length < 64 ? 64 : length))
    return -USB_ERR_NOMEM;
  memcpy(pipe->xfer->data_buffer, data, length);
  const int err = submit(pipe, (int) length);
  if (err < 0)
    return err;
  if (!wait_done(pipe, ms)) {
    if (g_host.gone)
      return -USB_ERR_NODEV;
    cancel(pipe);
    return -USB_ERR_TIMEOUT;
  }
  return reap(pipe);
}

Pipe *find_pipe(uint8_t address) {
  for (Pipe &pipe : g_host.pipes)
    if (pipe.address == address)
      return &pipe;
  return nullptr;
}

// One pipe per endpoint of a claimed interface, from the descriptors read when
// the device was opened. Done here, by the probe that claims the interface, so
// the reader tasks that come later only look a pipe up and never race each
// other to create one.
void assign_pipes(uint8_t number) {
  for (const usbh_interface &intf : g_port.config.intf) {
    const usbh_interface_altsetting &alt = intf.altsetting[0];
    if (alt.intf_desc.bLength == 0 || alt.intf_desc.bInterfaceNumber != number)
      continue;
    uint8_t endpoints = alt.intf_desc.bNumEndpoints;
    if (endpoints > CONFIG_USBHOST_MAX_ENDPOINTS)
      endpoints = CONFIG_USBHOST_MAX_ENDPOINTS;
    for (uint8_t i = 0; i < endpoints; i++) {
      const uint8_t address = alt.ep[i].ep_desc.bEndpointAddress;
      if (address == 0 || find_pipe(address) != nullptr)
        continue;
      Pipe *free_pipe = find_pipe(0);
      if (free_pipe == nullptr) {
        ESP_LOGW(TAG, "no room for endpoint %02x of interface %u", address, number);
        continue;
      }
      free_pipe->address = address;
      free_pipe->pending = false;
      free_pipe->halted = false;
    }
    return;
  }
}

// ---------------------------------------------------------------------------
// The device
// ---------------------------------------------------------------------------

const char *ascii(const usb_str_desc_t *desc, char *out, size_t room) {
  if (desc == nullptr || room == 0)
    return nullptr;
  const size_t chars = desc->bLength >= 2 ? (size_t) (desc->bLength - 2) / 2 : 0;
  size_t k = 0;
  for (size_t i = 0; i < chars && k + 1 < room; i++) {
    const uint16_t c = desc->wData[i];
    out[k++] = (c >= 0x20 && c < 0x7F) ? (char) c : '?';
  }
  out[k] = 0;
  return out;
}

// Reads what CherryUSB would have filled in: the device descriptor, the
// strings, and alternate setting 0 of every interface with its endpoints.
void fill_port(const usb_device_desc_t *dd, const usb_device_info_t &info,
               const usb_config_desc_t *cd) {
  memset(&g_port, 0, sizeof(g_port));
  g_port.port = 1;
  g_port.dev_addr = info.dev_addr;
  g_port.speed = (uint8_t) info.speed;
  g_port.setup = &g_setup;

  usb_device_descriptor &d = g_port.device_desc;
  d.bLength = dd->bLength;
  d.bDescriptorType = dd->bDescriptorType;
  d.bcdUSB = dd->bcdUSB;
  d.bDeviceClass = dd->bDeviceClass;
  d.bDeviceSubClass = dd->bDeviceSubClass;
  d.bDeviceProtocol = dd->bDeviceProtocol;
  d.bMaxPacketSize0 = dd->bMaxPacketSize0;
  d.idVendor = dd->idVendor;
  d.idProduct = dd->idProduct;
  d.bcdDevice = dd->bcdDevice;
  d.iManufacturer = dd->iManufacturer;
  d.iProduct = dd->iProduct;
  d.iSerialNumber = dd->iSerialNumber;
  d.bNumConfigurations = dd->bNumConfigurations;

  g_port.iManufacturer = ascii(info.str_desc_manufacturer, g_manufacturer, sizeof(g_manufacturer));
  g_port.iProduct = ascii(info.str_desc_product, g_product, sizeof(g_product));
  g_port.iSerialNumber = ascii(info.str_desc_serial_num, g_serial, sizeof(g_serial));

  usb_configuration_descriptor &c = g_port.config.config_desc;
  c.bLength = cd->bLength;
  c.bDescriptorType = cd->bDescriptorType;
  c.wTotalLength = cd->wTotalLength;
  c.bNumInterfaces = cd->bNumInterfaces;
  c.bConfigurationValue = cd->bConfigurationValue;
  c.iConfiguration = cd->iConfiguration;
  c.bmAttributes = cd->bmAttributes;
  c.bMaxPower = cd->bMaxPower;

  // Interfaces in the order they appear, alternate setting 0 only. The six SCO
  // settings of a dongle's second interface are walked past rather than
  // stored -- which is the whole of what cherryusb_patch exists to allow.
  int offset = 0;
  const usb_standard_desc_t *desc = (const usb_standard_desc_t *) cd;
  usbh_interface_altsetting *keep = nullptr;
  uint8_t count = 0;
  uint8_t endpoints = 0;
  while ((desc = usb_parse_next_descriptor(desc, cd->wTotalLength, &offset)) != nullptr) {
    if (desc->bDescriptorType == USB_B_DESCRIPTOR_TYPE_INTERFACE) {
      const usb_intf_desc_t *i = (const usb_intf_desc_t *) desc;
      keep = nullptr;
      if (i->bAlternateSetting != 0 || count >= CONFIG_USBHOST_MAX_INTERFACES)
        continue;
      usbh_interface &slot = g_port.config.intf[count++];
      slot.altsetting_num = 1;
      keep = &slot.altsetting[0];
      usb_interface_descriptor &id = keep->intf_desc;
      id.bLength = i->bLength;
      id.bDescriptorType = i->bDescriptorType;
      id.bInterfaceNumber = i->bInterfaceNumber;
      id.bAlternateSetting = i->bAlternateSetting;
      id.bNumEndpoints = i->bNumEndpoints;
      id.bInterfaceClass = i->bInterfaceClass;
      id.bInterfaceSubClass = i->bInterfaceSubClass;
      id.bInterfaceProtocol = i->bInterfaceProtocol;
      id.iInterface = i->iInterface;
      endpoints = 0;
    } else if (desc->bDescriptorType == USB_B_DESCRIPTOR_TYPE_ENDPOINT && keep != nullptr &&
               endpoints < CONFIG_USBHOST_MAX_ENDPOINTS) {
      const usb_ep_desc_t *e = (const usb_ep_desc_t *) desc;
      usb_endpoint_descriptor &ed = keep->ep[endpoints++].ep_desc;
      ed.bLength = e->bLength;
      ed.bDescriptorType = e->bDescriptorType;
      ed.bEndpointAddress = e->bEndpointAddress;
      ed.bmAttributes = e->bmAttributes;
      ed.wMaxPacketSize = e->wMaxPacketSize;
      ed.bInterval = e->bInterval;
    }
  }
}

void reset_pipe(Pipe *pipe) {
  pipe->pending = false;
  pipe->halted = false;
  pipe->said_too_long = false;
  // A completion nobody collected -- a dongle pulled out under a read --
  // must not be taken for the first answer of the next one.
  while (xSemaphoreTake(pipe->done, 0) == pdTRUE) {
  }
}

void open_device(uint8_t address) {
  usb_device_handle_t dev = nullptr;
  esp_err_t err = usb_host_device_open(g_host.client, address, &dev);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "a USB device arrived at address %u and would not open: %s", address,
             esp_err_to_name(err));
    return;
  }
  const usb_device_desc_t *dd = nullptr;
  const usb_config_desc_t *cd = nullptr;
  usb_device_info_t info;
  memset(&info, 0, sizeof(info));
  if (usb_host_get_device_descriptor(dev, &dd) != ESP_OK ||
      usb_host_device_info(dev, &info) != ESP_OK ||
      usb_host_get_active_config_descriptor(dev, &cd) != ESP_OK || dd == nullptr || cd == nullptr) {
    ESP_LOGW(TAG, "a USB device arrived at address %u and its descriptors could not be read",
             address);
    usb_host_device_close(g_host.client, dev);
    return;
  }

  g_host.dev = dev;
  g_host.claimed = 0;
  for (Pipe &pipe : g_host.pipes) {
    pipe.address = 0;
    reset_pipe(&pipe);
  }
  reset_pipe(&g_host.control);
  fill_port(dd, info, cd);
  g_port.connected = true;
  g_host.gone = false;
  g_host.open = true;

  if (g_host.handler != nullptr)
    g_host.handler(0, 1, g_port.port, 0, USBH_EVENT_DEVICE_CONFIGURED);
}

bool idle() {
  if (g_host.control.in_flight)
    return false;
  for (const Pipe &pipe : g_host.pipes)
    if (pipe.in_flight)
      return false;
  return true;
}

// Once the dongle has been pulled out and the library has handed back every
// transfer that was waiting on it. Its interfaces cannot be released before
// that: the library refuses while an endpoint still holds one.
void close_device() {
  for (uint8_t number = 0; number < 16; number++)
    if (g_host.claimed & (1u << number))
      usb_host_interface_release(g_host.client, g_host.dev, number);
  usb_host_device_close(g_host.client, g_host.dev);
  g_host.dev = nullptr;
  g_host.claimed = 0;
  g_host.open = false;
  g_host.gone = false;
  ESP_LOGI(TAG, "the USB device is gone and closed");
}

void on_client_event(const usb_host_client_event_msg_t *msg, void *arg) {
  (void) arg;
  if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
    if (g_host.open || g_host.want_open) {
      // One dongle at a time, as with CherryUSB: one socket, one set of
      // buffers.
      ESP_LOGW(TAG, "a second USB device arrived (address %u); it is left alone",
               msg->new_dev.address);
      return;
    }
    g_host.new_addr = msg->new_dev.address;
    g_host.want_open = true;
  } else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
    if (g_host.open && msg->dev_gone.dev_hdl == g_host.dev) {
      g_host.gone = true;
      g_port.connected = false;
    }
  }
}

// The library's own housekeeping: enumeration, and freeing a device that has
// gone. It must run for anything else to.
void lib_task(void *arg) {
  (void) arg;
  while (true) {
    uint32_t flags = 0;
    usb_host_lib_handle_events(portMAX_DELAY, &flags);
  }
}

// The client: every transfer callback and every device event is delivered from
// usb_host_client_handle_events(), so this task does nothing that can block
// on a transfer. Opening a device is done here, between calls, the way
// ESP-IDF's own examples do it, rather than inside the event callback.
void client_task(void *arg) {
  (void) arg;
  while (true) {
    usb_host_client_handle_events(g_host.client, pdMS_TO_TICKS(CLIENT_SLICE_MS));
    if (g_host.want_open && !g_host.open) {
      g_host.want_open = false;
      open_device(g_host.new_addr);
    }
    if (g_host.open && g_host.gone && idle())
      close_device();
  }
}

bool make_pipe(Pipe *pipe) {
  pipe->done = xSemaphoreCreateBinary();
  return pipe->done != nullptr;
}

}  // namespace

// ---------------------------------------------------------------------------
// The nine calls, with CherryUSB's names. See idf_usb.h.
// ---------------------------------------------------------------------------

int usbh_initialize(uint8_t busid, uintptr_t reg_base, usbh_event_handler_t handler) {
  (void) busid;
  if (g_host.client != nullptr)
    return -USB_ERR_BUSY;
  g_host.handler = handler;

  for (Pipe &pipe : g_host.pipes)
    if (!make_pipe(&pipe))
      return -USB_ERR_NOMEM;
  if (!make_pipe(&g_host.control))
    return -USB_ERR_NOMEM;
  g_host.control.address = 0;
  g_host.control_lock = xSemaphoreCreateMutex();
  if (g_host.control_lock == nullptr)
    return -USB_ERR_NOMEM;

  usb_host_config_t config;
  memset(&config, 0, sizeof(config));
  config.skip_phy_setup = false;
  config.intr_flags = ESP_INTR_FLAG_LEVEL1;
  // 0 is the library's default, the high-speed controller on a P4; bit 1 is
  // the full-speed one. See ESP_USB_FS0_BASE in idf_usb.h.
  config.peripheral_map = reg_base == ESP_USB_FS0_BASE ? (1u << 1) : 0u;
  esp_err_t err = usb_host_install(&config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ESP-IDF's USB host would not install: %s", esp_err_to_name(err));
    return -USB_ERR_IO;
  }
  if (xTaskCreate(lib_task, "bt_usb_lib", TASK_STACK, nullptr, TASK_PRIORITY, nullptr) != pdPASS)
    return -USB_ERR_NOMEM;

  usb_host_client_config_t client;
  memset(&client, 0, sizeof(client));
  client.is_synchronous = false;
  client.max_num_event_msg = 5;
  client.async.client_event_callback = on_client_event;
  client.async.callback_arg = nullptr;
  err = usb_host_client_register(&client, &g_host.client);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ESP-IDF's USB host would not take a client: %s", esp_err_to_name(err));
    g_host.client = nullptr;
    return -USB_ERR_IO;
  }
  if (xTaskCreate(client_task, "bt_usb_client", TASK_STACK, nullptr, TASK_PRIORITY, nullptr) !=
      pdPASS)
    return -USB_ERR_NOMEM;

  ESP_LOGI(TAG, "using ESP-IDF's USB Host Library through usb_bluetooth's own layer, not CherryUSB");
  return 0;
}

struct usbh_hubport *usbh_find_hubport(uint8_t busid, uint8_t hub_index, uint8_t hub_port) {
  (void) busid;
  (void) hub_index;
  if (!g_host.open || g_host.gone || !g_port.connected || hub_port != g_port.port)
    return nullptr;
  return &g_port;
}

int usbh_control_transfer(struct usbh_hubport *hport, struct usb_setup_packet *setup, uint8_t *buffer) {
  if (!g_host.open || g_host.gone || hport != &g_port)
    return -USB_ERR_NODEV;
  if (setup == nullptr)
    return -USB_ERR_INVAL;
  const uint16_t length = setup->wLength;
  const bool in = (setup->bmRequestType & USB_REQUEST_DIR_IN) != 0;
  if (length > CONTROL_DATA_MAX || (length != 0 && buffer == nullptr))
    return -USB_ERR_INVAL;
  if (xSemaphoreTake(g_host.control_lock, pdMS_TO_TICKS(2 * CONTROL_TIMEOUT_MS)) != pdTRUE)
    return -USB_ERR_BUSY;

  Pipe *pipe = &g_host.control;
  int result = 0;
  // A control transfer cannot be cancelled the way an endpoint's can, so one
  // that timed out is waited for here, once more, before another goes out.
  if (pipe->pending && !wait_done(pipe, CONTROL_TIMEOUT_MS)) {
    result = -USB_ERR_BUSY;
  } else {
    if (pipe->pending)
      reap(pipe);
    // Rounded up to whole 64-byte packets: an IN data stage is received in
    // packets, and the buffer has to hold the last one whole.
    if (!ensure_buffer(pipe, sizeof(usb_setup_packet) + round_up(CONTROL_DATA_MAX, 64))) {
      result = -USB_ERR_NOMEM;
    } else {
      memcpy(pipe->xfer->data_buffer, setup, sizeof(usb_setup_packet));
      if (!in && length != 0)
        memcpy(pipe->xfer->data_buffer + sizeof(usb_setup_packet), buffer, length);
      pipe->xfer->num_bytes = (int) (sizeof(usb_setup_packet) + length);
      pipe->xfer->device_handle = g_host.dev;
      pipe->xfer->bEndpointAddress = 0;
      pipe->xfer->flags = 0;
      pipe->xfer->timeout_ms = 0;
      pipe->in_flight = true;
      if (usb_host_transfer_submit_control(g_host.client, pipe->xfer) != ESP_OK) {
        pipe->in_flight = false;
        result = failed();
      } else {
        pipe->pending = true;
        if (!wait_done(pipe, CONTROL_TIMEOUT_MS)) {
          result = g_host.gone ? -USB_ERR_NODEV : -USB_ERR_TIMEOUT;
        } else {
          result = reap(pipe);
          if (result == 0) {
            // The setup packet is counted, as CherryUSB counts it.
            result = pipe->xfer->actual_num_bytes;
            if (in && length != 0) {
              int data = result - (int) sizeof(usb_setup_packet);
              if (data > (int) length)
                data = length;
              if (data > 0)
                memcpy(buffer, pipe->xfer->data_buffer + sizeof(usb_setup_packet), data);
            }
          }
        }
      }
    }
  }
  xSemaphoreGive(g_host.control_lock);
  return result;
}

int usbh_set_interface(struct usbh_hubport *hport, uint8_t intf, uint8_t altsetting) {
  if (!g_host.open || g_host.gone || hport != &g_port)
    return -USB_ERR_NODEV;
  if (intf >= 16)
    return -USB_ERR_INVAL;
  if ((g_host.claimed & (1u << intf)) == 0) {
    const esp_err_t err = usb_host_interface_claim(g_host.client, g_host.dev, intf, altsetting);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "the library would not claim interface %u: %s", intf, esp_err_to_name(err));
      return -USB_ERR_IO;
    }
    g_host.claimed |= (uint16_t) (1u << intf);
    assign_pipes(intf);
  }
  // Claiming sends nothing to the device. SET_INTERFACE is what CherryUSB's
  // usbh_set_interface() sends, and what usb_bluetooth.cpp logs the answer to.
  usb_setup_packet setup;
  setup.bmRequestType = USB_REQUEST_DIR_OUT | USB_REQUEST_STANDARD | USB_REQUEST_RECIPIENT_INTERFACE;
  setup.bRequest = USB_B_REQUEST_SET_INTERFACE;
  setup.wValue = altsetting;
  setup.wIndex = intf;
  setup.wLength = 0;
  return usbh_control_transfer(hport, &setup, nullptr);
}

int usbh_submit_urb(struct usbh_urb *urb) {
  if (urb == nullptr || urb->ep == nullptr)
    return -USB_ERR_INVAL;
  if (!g_host.open || g_host.gone || urb->hport != &g_port)
    return -USB_ERR_NODEV;
  const uint8_t address = urb->ep->bEndpointAddress;
  Pipe *pipe = find_pipe(address);
  if (pipe == nullptr) {
    ESP_LOGE(TAG, "endpoint %02x belongs to no interface usbh_set_interface() claimed", address);
    return -USB_ERR_INVAL;
  }
  const uint32_t mps = USB_GET_MAXPACKETSIZE(urb->ep->wMaxPacketSize);
  if (mps == 0)
    return -USB_ERR_INVAL;
  urb->actual_length = 0;
  if (address & 0x80) {
    int got = 0;
    const int err = read_in(pipe, urb->transfer_buffer, urb->transfer_buffer_length, mps, urb->timeout, &got);
    if (err < 0)
      return err;
    urb->actual_length = got;
    return 0;
  }
  const int err = write_out(pipe, urb->transfer_buffer, urb->transfer_buffer_length, urb->timeout);
  if (err < 0)
    return err;
  urb->actual_length = (int) urb->transfer_buffer_length;
  return 0;
}

#endif  // USE_ESP32 && HCI_USB_IDF
