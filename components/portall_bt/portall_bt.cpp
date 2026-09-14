#include "portall_bt.h"

#ifdef USE_ESP32

#include "esphome/core/log.h"

#include <cstring>

// CherryUSB comes from Espressif's component registry -- __init__.py writes it
// into idf_component.yml -- so these resolve only in an ESP-IDF build with
// that dependency present. usbh_core.h pulls CherryUSB's own usb_config.h,
// which is where ESP_USB_HS0_BASE and ESP_USB_FS0_BASE are defined, so the
// register addresses are never repeated here.
#include "usbh_core.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt";

// CherryUSB takes a plain C function pointer and gives no user pointer with
// it, so the instance has to be reachable from a file-scope variable. There is
// one host bus and one component; the schema does not allow a second.
static PortallBT *g_instance = nullptr;

static void usb_event_handler(uint8_t busid, uint8_t hub_index, uint8_t hub_port, uint8_t intf,
                              uint8_t event) {
  (void) busid;
  (void) intf;
  if (g_instance != nullptr) {
    g_instance->on_usb_event(hub_index, hub_port, event);
  }
}

static const char *speed_name(uint8_t speed) {
  switch (speed) {
    case USB_SPEED_LOW:
      return "low";
    case USB_SPEED_FULL:
      return "full";
    case USB_SPEED_HIGH:
      return "high";
    default:
      return "unknown";
  }
}

void PortallBT::setup() {
  g_instance = this;

  // Which register block is which is CherryUSB's to say. The high-speed one is
  // the default because that is where the sockets are: the Tab5's USB-A host
  // port is on it. Note what the port glue does with this choice -- the PHY is
  // configured for high speed on HS0 and full speed otherwise, so a full-speed
  // dongle on the high-speed port has to negotiate down, and that is exactly
  // the case this first firmware exists to test.
  const uintptr_t base = this->high_speed_ ? ESP_USB_HS0_BASE : ESP_USB_FS0_BASE;

  const int err = usbh_initialize(0, base, usb_event_handler);
  if (err != 0) {
    // Not mark_failed(): a board whose host will not start should still boot,
    // say so once, and let everything else on it work. This component is an
    // accessory and must never cost the rest of the firmware.
    ESP_LOGE(TAG, "The USB host would not start on the %s controller (error %d)",
             this->high_speed_ ? "high-speed" : "full-speed", err);
    return;
  }

  this->started_ = true;
  ESP_LOGI(TAG, "USB host running on the %s controller; waiting for a device",
           this->high_speed_ ? "high-speed" : "full-speed");
}

void PortallBT::on_usb_event(uint8_t hub_index, uint8_t hub_port, uint8_t event) {
  // CONFIGURED rather than CONNECTED: the descriptors are only filled in once
  // the device has been enumerated and a configuration chosen, and the
  // descriptors are the whole point of this firmware.
  if (event != USBH_EVENT_DEVICE_CONFIGURED) {
    return;
  }

  const uint8_t next = (uint8_t) ((this->head_ + 1) % QUEUE);
  if (next == this->tail_) {
    return;  // full: keep what is already waiting to be read
  }
  this->queue_[this->head_].hub_index = hub_index;
  this->queue_[this->head_].hub_port = hub_port;
  this->head_ = next;
}

void PortallBT::loop() {
  while (this->tail_ != this->head_) {
    const Arrival arrival = this->queue_[this->tail_];
    this->tail_ = (uint8_t) ((this->tail_ + 1) % QUEUE);
    this->report_(arrival.hub_index, arrival.hub_port);
  }
}

// ---------------------------------------------------------------------------
// Just enough HCI to make a dongle say who it is.
//
// CherryUSB has a Bluetooth class driver and it is NOT used here, for a reason
// that is worth writing down: it is switched off for ESP-IDF in CherryUSB's own
// CMakeLists, its class-info registration goes through a linker section and a
// forced symbol, and none of that is reachable from an ESPHome component
// without carrying the file and its build rules. What that driver actually
// does on the wire is small -- commands out on the control endpoint as a class
// request, events in on the interrupt endpoint -- so this does that directly
// against the enumerated port. It is the transport, not a stack: the moment
// this prints a name and an address, the question of which host stack to run
// on top can be answered with measurements instead of guesses.
//
// The transport is the USB Bluetooth class's, not an invention: bmRequestType
// 0x20 (host to device, class, recipient device), bRequest 0, wIndex the
// interface number, and the command sitting in the data stage.

static constexpr uint16_t HCI_RESET = 0x0C03;
static constexpr uint16_t HCI_READ_LOCAL_NAME = 0x0C14;
static constexpr uint16_t HCI_READ_LOCAL_VERSION = 0x1001;
static constexpr uint16_t HCI_READ_BD_ADDR = 0x1009;

static constexpr uint8_t HCI_EVENT_COMMAND_COMPLETE = 0x0E;

// How long to leave the dongle alone after it enumerates, how many times to
// ask it to reset, and how long between tries. See probe_hci for why these
// exist at all -- the same dongle and the same firmware answered on one run
// and said nothing on the next.
static constexpr uint32_t RESET_SETTLE_MS = 300;
static constexpr uint8_t RESET_ATTEMPTS = 5;
static constexpr uint32_t RESET_RETRY_MS = 400;

// DMA reads and writes these, so they go where CherryUSB puts its own.
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_hci_cmd[64];
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_hci_evt[260];

// ONE urb for the event endpoint, reused for every read, and this is not
// tidiness -- it is the whole difference between a dongle that answers and one
// that answers every other time.
//
// CherryUSB keeps an interrupt endpoint's DATA0/DATA1 toggle in the URB:
// usb_hc_dwc2.c starts a transfer with `urb->data_toggle == 0 ? HC_PID_DATA0 :
// HC_PID_DATA1` and writes the new toggle back into the same field when the
// transfer completes. A urb declared on the stack therefore begins every read
// at DATA0, while the device dutifully alternates -- so every second packet
// arrives with the wrong PID, is discarded by the host, and the read ends in a
// NAK or a timeout.
//
// Measured on a Tab5, and the pattern is unmistakable once it is named:
//
//     HCI Reset answered, status 00           <- toggle happened to match
//     Read Local Version: no answer, 137 reads, last error -10 (NAK)
//     address 00:02:72:DC:33:59               <- matched again
//     Read Local Name: no answer, last error -14 (timeout)
//
// One, miss, one, miss. Every class driver in CherryUSB keeps its urbs in its
// own structure for exactly this reason; this one had been stack-allocated.
static struct usbh_urb g_event_urb;

static int hci_command(struct usbh_hubport *hport, uint8_t intf, uint16_t opcode,
                       const uint8_t *params, uint8_t len) {
  struct usb_setup_packet *setup = hport->setup;
  setup->bmRequestType = USB_REQUEST_DIR_OUT | USB_REQUEST_CLASS | USB_REQUEST_RECIPIENT_DEVICE;
  setup->bRequest = 0x00;
  setup->wValue = 0;
  setup->wIndex = intf;
  setup->wLength = (uint16_t) (len + 3);

  g_hci_cmd[0] = (uint8_t) (opcode & 0xFF);
  g_hci_cmd[1] = (uint8_t) (opcode >> 8);
  g_hci_cmd[2] = len;
  if (len != 0) {
    memcpy(&g_hci_cmd[3], params, len);
  }
  return usbh_control_transfer(hport, setup, g_hci_cmd);
}

// What the waiting saw, so that a failure can say something. The first version
// of this reported only "nothing came back", which is the shape of message
// this repository keeps having to apologise for: it cost a whole round trip
// to a board to learn what a number would have said on the first run.
struct Waited {
  uint32_t reads;    // how many times the endpoint was asked
  uint32_t events;   // how many of those brought any bytes at all
  int last_error;    // what usbh_submit_urb said the last time it refused
  int last_length;   // and how many bytes the last successful read held
  uint8_t last_code; // the event code of the last thing that arrived
};

static uint32_t now_ms() { return (uint32_t) (xTaskGetTickCount() * portTICK_PERIOD_MS); }

// Reads events until one of them is the Command Complete for `opcode`, or the
// patience runs out. A controller answers other things while it settles -- a
// Broadcom emits vendor events of its own after a reset -- so taking the first
// event that arrives and calling it the answer would be wrong about half the
// time.
//
// The budget is a REAL CLOCK, and that is a correction rather than a
// refinement. It used to count attempts and assume each one cost the half
// second it asked for, which is true only when a read times out: a refusal
// comes back in microseconds, so sixteen of those spent a three-second
// allowance instantly and the caller reported silence after no wait at all.
// Measured on a Tab5 -- the command went out and the failure was logged in the
// same second.
static int hci_await(struct usbh_hubport *hport, struct usb_endpoint_descriptor *ep,
                     uint16_t opcode, uint32_t patience_ms, Waited *saw) {
  struct usbh_urb &urb = g_event_urb;
  const uint32_t started = now_ms();

  *saw = Waited{};
  while (now_ms() - started < patience_ms) {
    usbh_int_urb_fill(&urb, hport, ep, g_hci_evt, sizeof(g_hci_evt), 500, nullptr, nullptr);
    const int err = usbh_submit_urb(&urb);
    saw->reads++;
    if (err < 0) {
      saw->last_error = err;
      if (err == -USB_ERR_NODEV || err == -USB_ERR_NOTCONN || err == -USB_ERR_SHUTDOWN) {
        return -1;  // it was unplugged; there is nothing to be patient about
      }
      // Only for a refusal, and only 10 ms: a read that really waited has
      // already spent its time, while a refusal that returns at once would
      // otherwise spin a core for the whole patience.
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (urb.actual_length <= 0) {
      continue;  // an empty read is silence, not an answer to discard
    }
    saw->events++;
    saw->last_length = urb.actual_length;
    saw->last_code = g_hci_evt[0];
    if (urb.actual_length < 6 || g_hci_evt[0] != HCI_EVENT_COMMAND_COMPLETE) {
      continue;
    }
    const uint16_t answered = (uint16_t) (g_hci_evt[3] | (g_hci_evt[4] << 8));
    if (answered != opcode) {
      continue;
    }
    return (int) urb.actual_length;
  }
  return -1;
}

// One command and its answer, and NEVER silent about either.
//
// The first version logged only when a read succeeded, so a dongle that
// answered Reset and then stopped produced a log that simply ended, and it
// cost a round trip to a board to notice. That is the second time in this
// file that a quiet failure has cost a round trip; there is no third path
// out of this function that says nothing.
static int hci_ask(struct usbh_hubport *hport, uint8_t intf,
                   struct usb_endpoint_descriptor *events, uint16_t opcode, const char *what,
                   uint32_t patience_ms) {
  Waited saw;

  const int sent = hci_command(hport, intf, opcode, nullptr, 0);
  if (sent < 0) {
    // -3 is USB_ERR_NODEV: somebody pulled the dongle out. Worth naming,
    // because five identical lines saying a command would not go out read like
    // a fault in the dongle rather than an empty socket.
    ESP_LOGW(TAG, "  %s (%04x) would not go out (%d)%s", what, opcode, sent,
             sent == -USB_ERR_NODEV ? " -- the dongle is no longer there" : "");
    return -1;
  }

  const int len = hci_await(hport, events, opcode, patience_ms, &saw);
  if (len < 0) {
    // `sent` is in here because "the command went out" was an inference from
    // a non-negative return, and an inference is what this file keeps having
    // to correct. -10 in `last error` is USB_ERR_NAK, which is the endpoint
    // saying it has nothing -- a controller that never answered rather than a
    // transfer that failed.
    ESP_LOGW(TAG,
             "  %s (%04x): no answer -- control said %d, then %u reads, %u with bytes, "
             "last error %d, last length %d, last event %02x",
             what, opcode, sent, (unsigned) saw.reads, (unsigned) saw.events, saw.last_error,
             saw.last_length, saw.last_code);
    return -1;
  }

  if (g_hci_evt[5] != 0x00) {
    // A controller that answers and refuses is a different animal from one
    // that says nothing, and the status byte is the whole difference.
    ESP_LOGW(TAG, "  %s (%04x) answered but refused, status %02x", what, opcode, g_hci_evt[5]);
    return -1;
  }
  return len;
}

// What the task is handed. The PORT is carried, never the hubport pointer: an
// unplug frees that structure, and a pointer taken before the unplug would be
// read after the free. Finding the port again costs nothing and cannot be
// stale.
struct ProbeRequest {
  PortallBT *self;
  uint8_t hub_index;
  uint8_t hub_port;
  uint8_t intf_index;
};

// The task's whole existence is that usbh_submit_urb blocks. ESPHome's loop
// may not block, and CherryUSB's own hub task must not be made to wait on a
// device either.
static void hci_probe_task(void *arg) {
  ProbeRequest *request = (ProbeRequest *) arg;
  request->self->probe_hci(request->hub_index, request->hub_port, request->intf_index);
  delete request;
  vTaskDelete(nullptr);
}

void PortallBT::report_(uint8_t hub_index, uint8_t hub_port) {
  struct usbh_hubport *hport = usbh_find_hubport(0, hub_index, hub_port);
  if (hport == nullptr || !hport->connected) {
    // It was unplugged between the event and this line, which is a race a
    // person can lose by being quick, not a fault.
    ESP_LOGW(TAG, "a device enumerated on hub %u port %u and was gone before it could be read",
             hub_index, hub_port);
    return;
  }

  ESP_LOGI(TAG, "device %u on bus 0: %04x:%04x, %s speed", hport->dev_addr,
           hport->device_desc.idVendor, hport->device_desc.idProduct, speed_name(hport->speed));
  // Printed because it is where a dongle can say what it is without any
  // interface saying so, and because the first version of this looked only at
  // the interfaces and therefore could not explain what it had found.
  ESP_LOGI(TAG, "  device class %02x subclass %02x protocol %02x",
           hport->device_desc.bDeviceClass, hport->device_desc.bDeviceSubClass,
           hport->device_desc.bDeviceProtocol);
  if (hport->iManufacturer != nullptr || hport->iProduct != nullptr) {
    ESP_LOGI(TAG, "  %s %s", hport->iManufacturer != nullptr ? hport->iManufacturer : "",
             hport->iProduct != nullptr ? hport->iProduct : "");
  }

  uint8_t interfaces = hport->config.config_desc.bNumInterfaces;
  if (interfaces > CONFIG_USBHOST_MAX_INTERFACES) {
    // The descriptor is the device's word for how many it has; the array is
    // ours. Believing the device over our own array is how a stack walks off
    // the end of one.
    ESP_LOGW(TAG, "  it claims %u interfaces and only %d were stored", interfaces,
             CONFIG_USBHOST_MAX_INTERFACES);
    interfaces = CONFIG_USBHOST_MAX_INTERFACES;
  }

  int hci_interface = -1;
  bool vendor_class = false;
  for (uint8_t i = 0; i < interfaces; i++) {
    const struct usb_interface_descriptor *desc = &hport->config.intf[i].altsetting[0].intf_desc;
    ESP_LOGI(TAG, "  interface %u: class %02x subclass %02x protocol %02x", i,
             desc->bInterfaceClass, desc->bInterfaceSubClass, desc->bInterfaceProtocol);
    if (hci_interface >= 0 || desc->bInterfaceSubClass != 0x01 ||
        desc->bInterfaceProtocol != 0x01) {
      continue;
    }
    // E0/01/01 is the Bluetooth primary controller as the USB class defines
    // it. FF/01/01 is the same interface wearing a vendor coat, which whole
    // families of dongles do so that Windows loads the manufacturer's stack
    // instead of the generic one -- the Broadcom this was measured against is
    // one of them, reporting ff/01/01 on interfaces 0 and 1 and even ff at the
    // device level. Linux does not treat that as a different kind of device
    // either: btusb.c binds it with
    //
    //     USB_VENDOR_AND_INTERFACE_INFO(0x0a5c, 0xff, 0x01, 0x01)
    //
    // and carries a dozen more vendors on the same line. So the subclass and
    // the protocol are what say Bluetooth here; the class only says who wrote
    // the driver they were hoping for.
    if (desc->bInterfaceClass == 0xE0 || desc->bInterfaceClass == 0xFF) {
      hci_interface = (int) i;
      vendor_class = desc->bInterfaceClass == 0xFF;
    }
  }

  if (hci_interface < 0) {
    return;
  }
  if (vendor_class) {
    ESP_LOGI(TAG,
             "  interface %d is vendor class but 01/01, which is how Broadcom and friends "
             "ship an HCI interface; asking it who it is",
             hci_interface);
  } else {
    ESP_LOGI(TAG, "  interface %d is a Bluetooth controller; asking it who it is", hci_interface);
  }

  // One at a time. A second dongle would need a second set of buffers, and
  // there is one socket on the boards this runs on.
  if (this->probing_) {
    ESP_LOGW(TAG, "  already talking to one, leaving this one alone");
    return;
  }
  this->probing_ = true;

  auto *request = new ProbeRequest{this, hub_index, hub_port, (uint8_t) hci_interface};  // NOLINT
  if (xTaskCreate(hci_probe_task, "portall_bt", 4096, request, 5, nullptr) != pdPASS) {
    ESP_LOGE(TAG, "  could not start the task that would have asked");
    delete request;
    this->probing_ = false;
  }
}

void PortallBT::probe_hci(uint8_t hub_index, uint8_t hub_port, uint8_t intf_index) {
  struct usbh_hubport *hport = usbh_find_hubport(0, hub_index, hub_port);
  if (hport == nullptr || !hport->connected) {
    this->probing_ = false;
    return;
  }

  // The FIRST interface that answered the description is the one, and it is
  // passed in rather than assumed: the SCO interface carries the same subclass
  // and protocol, so taking the last match instead of the first would leave
  // this talking HCI to the voice channel.
  struct usbh_interface_altsetting *alt = &hport->config.intf[intf_index].altsetting[0];
  const uint8_t intf = alt->intf_desc.bInterfaceNumber;

  // Events arrive on the interrupt IN endpoint. Finding it by its attributes
  // rather than by its number, because a number is a convention and these
  // descriptors are the device's own word.
  struct usb_endpoint_descriptor *events = nullptr;
  uint8_t endpoints = alt->intf_desc.bNumEndpoints;
  if (endpoints > CONFIG_USBHOST_MAX_ENDPOINTS) {
    endpoints = CONFIG_USBHOST_MAX_ENDPOINTS;
  }
  for (uint8_t i = 0; i < endpoints; i++) {
    struct usb_endpoint_descriptor *ep = &alt->ep[i].ep_desc;
    if ((ep->bEndpointAddress & 0x80) != 0 && USB_GET_ENDPOINT_TYPE(ep->bmAttributes) == 3) {
      events = ep;
      break;
    }
  }
  if (events == nullptr) {
    ESP_LOGE(TAG, "interface %u has no interrupt endpoint, so events have nowhere to arrive", intf);
    this->probing_ = false;
    return;
  }
  ESP_LOGI(TAG, "  events on endpoint %02x, packet %u, interval %u", events->bEndpointAddress,
           (unsigned) USB_GET_MAXPACKETSIZE(events->wMaxPacketSize), events->bInterval);

  // A device that has just been enumerated starts its endpoint at DATA0, so
  // the urb that carries the toggle starts there too. Everything after this
  // reuses it and lets the toggle alternate on its own.
  g_event_urb = usbh_urb{};

  // CherryUSB's own driver selects alternate setting 0 before it starts, so
  // this does too. A device that has alternate settings at all is entitled to
  // be told which one is wanted, and a vendor-class one that refuses costs a
  // log line rather than the probe.
  const int selected = usbh_set_interface(hport, intf, 0);
  ESP_LOGI(TAG, "  selecting altsetting 0 on interface %u returned %d", intf, selected);

  // A dongle that has just been given power and enumerated is entitled to a
  // moment before it is asked anything. Measured rather than assumed to be
  // needed: on two runs of the same firmware, the same dongle answered the
  // first Reset once and NAKed 273 reads the next time, which is what an
  // intermittent readiness looks like from here.
  vTaskDelay(pdMS_TO_TICKS(RESET_SETTLE_MS));

  // And the reset is RETRIED, for the same reason. One attempt turns a
  // controller that was not ready yet into a dongle that does not work, and
  // the two are not the same thing at all. Each attempt says so, so an
  // intermittent fault stays visible instead of being papered over.
  int len = -1;
  for (uint8_t attempt = 1; attempt <= RESET_ATTEMPTS; attempt++) {
    len = hci_ask(hport, intf, events, HCI_RESET, "HCI Reset", 1500);
    if (len >= 0) {
      if (attempt > 1) {
        ESP_LOGI(TAG, "  it answered on attempt %u of %u", attempt, RESET_ATTEMPTS);
      }
      break;
    }
    if (!hport->connected) {
      ESP_LOGW(TAG, "  the dongle was unplugged; giving up on this one");
      this->probing_ = false;
      return;
    }
    if (attempt < RESET_ATTEMPTS) {
      vTaskDelay(pdMS_TO_TICKS(RESET_RETRY_MS));
    }
  }
  if (len < 0) {
    ESP_LOGE(TAG, "the dongle did not answer HCI Reset in %u attempts", RESET_ATTEMPTS);
    this->probing_ = false;
    return;
  }
  // num_HCI_Command_Packets is how many commands the controller will accept
  // before it says otherwise. Printed because a zero there is the one reason a
  // controller that just answered would ignore everything after, and it would
  // be invisible from anywhere else.
  ESP_LOGI(TAG, "HCI Reset answered, status 00, credit %u -- the dongle is talking", g_hci_evt[2]);

  // Linux waits here too, in btbcm_reset(): "100 msec delay for module to
  // complete reset process". Taken from their code rather than invented.
  vTaskDelay(pdMS_TO_TICKS(150));

  // Everything below is decoration compared with that line, and it is what
  // turns "it answered" into something a person can check against the same
  // dongle on a PC.
  len = hci_ask(hport, intf, events, HCI_READ_LOCAL_VERSION, "Read Local Version", 1500);
  if (len >= 14) {
    const uint16_t manufacturer = (uint16_t) (g_hci_evt[10] | (g_hci_evt[11] << 8));
    ESP_LOGI(TAG, "  HCI version %u, LMP version %u, manufacturer %u", g_hci_evt[6], g_hci_evt[9],
             manufacturer);
  }

  len = hci_ask(hport, intf, events, HCI_READ_BD_ADDR, "Read BD Address", 1500);
  if (len >= 12) {
    // Little-endian on the wire, printed the way everybody writes it.
    ESP_LOGI(TAG, "  address %02X:%02X:%02X:%02X:%02X:%02X", g_hci_evt[11], g_hci_evt[10],
             g_hci_evt[9], g_hci_evt[8], g_hci_evt[7], g_hci_evt[6]);
  }

  len = hci_ask(hport, intf, events, HCI_READ_LOCAL_NAME, "Read Local Name", 1500);
  if (len > 6) {
    // The field is 248 bytes padded with NULs, and a controller with no name
    // set sends 248 of them -- which is a valid answer and not worth a line.
    g_hci_evt[sizeof(g_hci_evt) - 1] = 0;
    const char *name = (const char *) &g_hci_evt[6];
    if (name[0] != 0) {
      ESP_LOGI(TAG, "  name \"%s\"", name);
    }
  }

  this->probing_ = false;
}

void PortallBT::dump_config() {
  ESP_LOGCONFIG(TAG, "Portall Bluetooth:");
  ESP_LOGCONFIG(TAG, "  Controller: %s", this->high_speed_ ? "high-speed" : "full-speed");
  ESP_LOGCONFIG(TAG, "  USB host: %s", this->started_ ? "running" : "NOT running");
  if (this->high_speed_) {
    // Worth saying on every boot rather than in documentation nobody reads:
    // this is the controller portall puts in device mode, so the two cannot
    // share it and this is meant to be a firmware of its own for now.
    ESP_LOGCONFIG(TAG, "  This controller is the one portall uses for USB device mode.");
  }
  ESP_LOGCONFIG(TAG, "  The host supplies 5V: on the Tab5 that is the second PI4IOE5V6408,");
  ESP_LOGCONFIG(TAG, "  bit 3 (usb_5v_power), which this component does not touch.");
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_ESP32
