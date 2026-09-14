#include "portall_bt.h"

#ifdef USE_ESP32

#include "esphome/core/log.h"

#include <cstdio>
#include <cstring>

// CherryUSB comes from Espressif's component registry -- __init__.py writes it
// into idf_component.yml -- so these resolve only in an ESP-IDF build with
// that dependency present. usbh_core.h pulls CherryUSB's own usb_config.h,
// which is where ESP_USB_HS0_BASE and ESP_USB_FS0_BASE are defined, so the
// register addresses are never repeated here.
#include "usbh_core.h"

// Only present when host_stack: bluedroid put CONFIG_BT_ENABLED in the
// sdkconfig. See __init__.py for why that configuration is possible on a chip
// with no Bluetooth of its own.
#ifdef CONFIG_BT_BLUEDROID_ENABLED
#include "esp_bluedroid_hci.h"
#include "esp_bt_device.h"
#include "esp_bt_main.h"
#endif

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

  this->try_host_stack_();
}

// Half a step on purpose, and the half that can be taken before the glue
// exists. `esp_bluedroid_init()` builds the stack's own structures and talks to
// nothing; `esp_bluedroid_enable()` is where a host reaches for a controller,
// and there is not yet anything for it to reach.
//
// What this is really for is the BUILD. Whether Bluedroid compiles and LINKS
// for an esp32p4 target cannot be read out of a Kconfig -- that file says the
// configuration is selectable, which is a different claim. A link error here
// is not a setback: the undefined symbols are the specification for the glue,
// the host stack naming in the linker's own words exactly what it expects a
// controller to provide.
void PortallBT::try_host_stack_() {
  if (!this->host_stack_) {
    return;
  }
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  // Nothing is started here any more, and the order is the reason.
  // `esp_bluedroid_attach_hci_driver()` has to be called BEFORE
  // `esp_bluedroid_init()` -- Espressif's own header says so -- and the driver
  // cannot be attached until there is a dongle to attach it to. So the whole
  // sequence moved to start_host_stack_(), which runs once the probe has
  // finished with a controller that answers.
  //
  // This line stays because the alternative is silence, and silence is what
  // cost the round trip that proved Bluedroid links: a board built with
  // host_stack: bluedroid said nothing whatever about it.
  ESP_LOGI(TAG, "Bluedroid is wanted; waiting for a dongle to hand it");
#else
  // Belt and braces: the option sets the sdkconfig, so reaching here means the
  // two disagreed, and a silent nothing is exactly what this file keeps having
  // to apologise for.
  ESP_LOGE(TAG, "host_stack: bluedroid was asked for and CONFIG_BT_BLUEDROID_ENABLED is not set");
#endif
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

static constexpr uint16_t HCI_INQUIRY = 0x0401;
static constexpr uint16_t HCI_WRITE_INQUIRY_MODE = 0x0C45;

static constexpr uint8_t HCI_EVENT_INQUIRY_COMPLETE = 0x01;
static constexpr uint8_t HCI_EVENT_INQUIRY_RESULT = 0x02;
static constexpr uint8_t HCI_EVENT_COMMAND_COMPLETE = 0x0E;
static constexpr uint8_t HCI_EVENT_COMMAND_STATUS = 0x0F;
static constexpr uint8_t HCI_EVENT_INQUIRY_RESULT_RSSI = 0x22;
static constexpr uint8_t HCI_EVENT_EXTENDED_INQUIRY_RESULT = 0x2F;

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

// Every read lands HERE first and is copied out, rather than being read
// straight into the middle of the accumulation buffer.
//
// The obvious version -- point the controller at `g_hci_evt + filled` -- was
// written, shipped, and killed the firmware on the second packet:
//
//     ASSERT FAIL [!((uintptr_t)urb->transfer_buffer % CONFIG_USB_ALIGN_SIZE)]
//     urb->setup or urb->transfer_buffer is not aligned 64
//
// The commit that introduced it claimed "every full packet is a multiple of
// the packet size, so the offset stays aligned", which was reasoning from an
// alignment of 4. With the data cache on, this port wants **64**, and this
// endpoint's packets are 16 -- so the second read was 16 bytes past a
// 64-byte boundary and the assert took the whole task down with it, watchdog
// and all. An assumption about someone else's constant, stated as a fact.
//
// One staging buffer, aligned by the same macro the rest of them use, a whole
// cache line wide so an invalidate cannot reach anything else.
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_hci_pkt[64];

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

// HOW LONG A PACKET IS, asked of the packet itself rather than guessed from a
// short read. This is the fault that stopped Bluedroid on its fifth command.
//
// The first version ended an event when a read came back SHORTER than the
// endpoint's packet size, which is how CherryUSB's own driver is written and
// how the probe was proved -- and it is wrong for an event whose length is an
// exact multiple of that size, because there is no short read to wait for. A
// Bluetooth interrupt endpoint is 16 bytes, and of the seven commands
// Bluedroid sends while it starts, exactly ONE answers in exactly 16:
//
//     0x0C03 Reset                          6 bytes, last packet  6
//     0x1001 Read Local Version            13              ...   13
//     0x1002 Read Local Supported Commands 70              ...    6
//     0x1003 Read Local Supported Features 14              ...   14
//     0x1004 Read Local Extended Features  16              ...    0   <-
//     0x1005 Read Buffer Size              13              ...   13
//     0x1009 Read BD Address               12              ...   12
//
// and the panel's log stopped on precisely that one:
//
//     BT_HCI: command_timed_out ... opcode: 0x1004
//
// The reader was still waiting for a packet the controller had no reason to
// send, the next read NAKed, and a NAK throws the part-read frame away. The
// probe never met it because none of its four answers is a multiple of 16 --
// it worked by arithmetic nobody had done.
//
// HCI says how long everything is, in the frame, which is what btusb reads
// instead of counting packets. So do these.
//
// An event: code, parameter length, then that many bytes.
static uint32_t event_length(const uint8_t *event, uint32_t filled) {
  return filled >= 2 ? 2u + (uint32_t) event[1] : 0u;  // 0 = not known yet
}

// ACL: handle and flags, then a little-endian length, then that many bytes.
static uint32_t acl_length(const uint8_t *acl, uint32_t filled) {
  return filled >= 4 ? 4u + (uint32_t) (acl[2] | (acl[3] << 8)) : 0u;
}

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
// Reads ONE complete event into g_hci_evt, whatever it is, or gives up.
//
// ONE PACKET PER READ, and the event reassembled from them. Asking for the
// whole buffer at once works for the short events and fails for the long ones:
// Read Local Name answers with 255 bytes, which on a 16-byte endpoint is
// seventeen packets in a single periodic transfer, and that came back -14
// (timeout) on a Tab5 five times running while Reset, Read Local Version and
// Read BD Address -- all under 16 bytes -- answered at once.
//
// Reading a packet at a time is also what both references do: CherryUSB's own
// driver fills its urb with `ep_mps` and accumulates until a short packet, and
// Linux's btusb reads wMaxPacketSize and reassembles in hci_recv_fragment. A
// short packet ends the event.
static int hci_next_event(struct usbh_hubport *hport, struct usb_endpoint_descriptor *ep,
                          uint32_t patience_ms, Waited *saw) {
  struct usbh_urb &urb = g_event_urb;
  const uint32_t started = now_ms();

  uint32_t mps = USB_GET_MAXPACKETSIZE(ep->wMaxPacketSize);
  if (mps == 0 || mps > sizeof(g_hci_pkt)) {
    // A Bluetooth interrupt endpoint is 16 bytes and the staging buffer is 64,
    // so this cannot happen on a dongle -- but a descriptor is the device's
    // word, and believing it over our own array is how a stack walks off the
    // end of one.
    ESP_LOGE(TAG, "endpoint %02x says its packets are %u bytes, which will not fit",
             ep->bEndpointAddress, (unsigned) mps);
    return -1;
  }
  uint32_t filled = 0;

  while (now_ms() - started < patience_ms) {
    usbh_int_urb_fill(&urb, hport, ep, g_hci_pkt, mps, 500, nullptr, nullptr);
    const int err = usbh_submit_urb(&urb);
    saw->reads++;
    if (err < 0) {
      saw->last_error = err;
      filled = 0;  // half an event is not an event
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

    uint32_t take = (uint32_t) urb.actual_length;
    if (filled + take > sizeof(g_hci_evt)) {
      take = (uint32_t) sizeof(g_hci_evt) - filled;
    }
    memcpy(&g_hci_evt[filled], g_hci_pkt, take);
    filled += take;

    // The event's own length field, never the shape of the read. See
    // event_length() above for what counting packets cost.
    const uint32_t want = event_length(g_hci_evt, filled);
    if (want == 0 || filled < want) {
      continue;  // the header has not arrived yet, or the event has not
    }

    saw->events++;
    saw->last_length = (int) want;
    saw->last_code = g_hci_evt[0];
    return (int) want;
  }
  return -1;
}

// Reads until one of them is the Command Complete for `opcode`, or the
// patience runs out. A controller answers other things while it settles -- a
// Broadcom emits vendor events of its own after a reset -- so taking the first
// event that arrives and calling it the answer would be wrong about half the
// time.
static int hci_await(struct usbh_hubport *hport, struct usb_endpoint_descriptor *ep,
                     uint16_t opcode, uint32_t patience_ms, Waited *saw) {
  const uint32_t started = now_ms();

  *saw = Waited{};
  while (now_ms() - started < patience_ms) {
    const int length = hci_next_event(hport, ep, patience_ms - (now_ms() - started), saw);
    if (length < 0) {
      return -1;
    }
    if (length < 6 || g_hci_evt[0] != HCI_EVENT_COMMAND_COMPLETE) {
      continue;
    }
    const uint16_t answered = (uint16_t) (g_hci_evt[3] | (g_hci_evt[4] << 8));
    if (answered == opcode) {
      return length;
    }
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

  // Those `[E/usbh_core] Do not support Class:...` lines just above are
  // CherryUSB's, and they are expected rather than a fault. It prints one per
  // interface for which no class driver is registered -- and none is, on
  // purpose: its own Bluetooth driver is switched off for ESP-IDF, and this
  // component drives the device itself from the enumeration event instead.
  // Read its source rather than its log level: after that message it fires
  // USBH_EVENT_INTERFACE_UNSUPPORTED and `continue`s, leaving `ret` untouched,
  // so enumeration still succeeded and the device stays exactly where it is.
  // Four red lines that mean nothing are worth one line that says so.
  ESP_LOGI(TAG, "  the \"Do not support Class\" lines above are CherryUSB's and are expected:");
  ESP_LOGI(TAG, "  no class driver is registered for this, and none is wanted -- it is driven here");

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

// The major device class, out of the 24-bit Class of Device. Worth decoding
// rather than printing the number, because it is what tells a headset from a
// telephone at a glance -- and a headset is the reason this whole route
// exists.
static const char *device_kind(uint32_t cod) {
  switch ((cod >> 8) & 0x1F) {
    case 0x01:
      return "computer";
    case 0x02:
      return "phone";
    case 0x03:
      return "network";
    case 0x04:
      return "audio/video";
    case 0x05:
      return "peripheral";
    case 0x06:
      return "imaging";
    case 0x07:
      return "wearable";
    case 0x08:
      return "toy";
    case 0x09:
      return "health";
    default:
      return "unclassified";
  }
}

// What an inquiry has already reported, so a device seen six times is one
// line. Addresses rather than a count: a controller repeats a device for as
// long as it keeps hearing it, and a log that repeats with it cannot be read.
struct Seen {
  uint8_t address[8][6];
  uint8_t count;

  bool add(const uint8_t *addr) {
    for (uint8_t i = 0; i < this->count; i++) {
      if (memcmp(this->address[i], addr, 6) == 0) {
        return false;
      }
    }
    if (this->count < 8) {
      memcpy(this->address[this->count], addr, 6);
      this->count++;
    }
    return true;
  }
};

// One entry of an inquiry result. The three event shapes carry the same
// fourteen bytes in two different arrangements, and the difference is a single
// reserved byte:
//
//   Inquiry Result (0x02)        addr[0..5] psrm[6] resv[7..8] cod[9..11]  clk[12..13]
//   ...with RSSI   (0x22)        addr[0..5] psrm[6] resv[7]    cod[8..10]  clk[11..12] rssi[13]
//   Extended       (0x2F)        as 0x22, then 240 bytes of advertising data
//
// So both are fourteen bytes long and the Class of Device is NOT in the same
// place. Reading it at one offset for both would put the reserved byte in the
// middle of it and call a headset a toy.
static constexpr uint8_t INQUIRY_ENTRY = 14;

// The friendly name, out of an extended inquiry response. EIR is a run of
// (length, type, value) pieces, and 0x09 is the complete local name with 0x08
// the shortened one -- so a speaker says "JBL Flip 5" here and an address
// stops being the only thing a person can go on.
static bool eir_name(const uint8_t *eir, uint32_t len, char *out, uint32_t room) {
  uint32_t at = 0;
  while (at + 1 < len) {
    const uint32_t piece = eir[at];
    if (piece == 0) {
      break;  // the padding that ends every EIR
    }
    if (at + 1 + piece > len) {
      break;  // a length the bytes do not back up
    }
    const uint8_t type = eir[at + 1];
    if (type == 0x09 || type == 0x08) {
      uint32_t take = piece - 1;
      if (take > room - 1) {
        take = room - 1;
      }
      memcpy(out, &eir[at + 2], take);
      out[take] = 0;
      return take > 0;
    }
    at += 1 + piece;
  }
  return false;
}

static void report_found(const uint8_t *entry, bool has_rssi, const char *name, Seen *seen) {
  if (!seen->add(entry)) {
    return;
  }
  const uint8_t at = has_rssi ? 8 : 9;
  const uint32_t cod =
      (uint32_t) entry[at] | ((uint32_t) entry[at + 1] << 8) | ((uint32_t) entry[at + 2] << 16);
  // A device that publishes no extended inquiry response has no name to give,
  // and in inquiry mode 2 the controller reports it with the plain
  // with-RSSI event rather than the extended one. That is the specification
  // working, not a lookup that failed, so the line simply ends.
  char tail[40] = "";
  if (name != nullptr) {
    snprintf(tail, sizeof(tail), "  \"%s\"", name);
  }
  if (has_rssi) {
    ESP_LOGI(TAG, "  found %02X:%02X:%02X:%02X:%02X:%02X  %-12s %4d dBm%s", entry[5], entry[4],
             entry[3], entry[2], entry[1], entry[0], device_kind(cod), (int) (int8_t) entry[13],
             tail);
  } else {
    ESP_LOGI(TAG, "  found %02X:%02X:%02X:%02X:%02X:%02X  %-12s%s", entry[5], entry[4], entry[3],
             entry[2], entry[1], entry[0], device_kind(cod), tail);
  }
}

void PortallBT::inquire_(struct usbh_hubport *hport, uint8_t intf,
                         struct usb_endpoint_descriptor *events) {
  if (this->inquiry_seconds_ == 0) {
    return;
  }

  // The general inquiry access code, 0x9E8B33, little-endian on the wire. Then
  // the duration in units of 1.28 seconds, and 0 for "report everything you
  // hear" rather than a number of devices.
  uint8_t length = (uint8_t) ((this->inquiry_seconds_ * 100 + 127) / 128);
  if (length < 1) {
    length = 1;
  }
  if (length > 0x30) {
    length = 0x30;  // the specification's own ceiling, 61 seconds
  }
  const uint8_t params[5] = {0x33, 0x8B, 0x9E, length, 0x00};

  // Ask for results that carry an RSSI and an extended inquiry response before
  // inquiring, because the default -- mode 0 -- gives an address and a class
  // and nothing else, which is what the first run of this printed. Mode 2 is
  // where a speaker's own name comes from. A controller that will not do 2 is
  // offered 1, and one that will not do either keeps the plain results: this
  // is a better log, never a requirement.
  static const char *const modes[] = {"extended results", "results with RSSI"};
  for (uint8_t mode = 2; mode >= 1; mode--) {
    const uint8_t wanted[1] = {mode};
    if (hci_command(hport, intf, HCI_WRITE_INQUIRY_MODE, wanted, 1) < 0) {
      break;
    }
    Waited asked{};
    const int len = hci_await(hport, events, HCI_WRITE_INQUIRY_MODE, 1500, &asked);
    if (len >= 6 && g_hci_evt[5] == 0x00) {
      ESP_LOGI(TAG, "  asking for %s", modes[2 - mode]);
      break;
    }
  }

  ESP_LOGI(TAG, "listening for Bluetooth devices for about %u seconds",
           (unsigned) ((length * 128) / 100));

  const int sent = hci_command(hport, intf, HCI_INQUIRY, params, sizeof(params));
  if (sent < 0) {
    ESP_LOGW(TAG, "  the inquiry would not go out (%d)", sent);
    return;
  }

  // An inquiry answers with Command STATUS, not Command Complete: it is a
  // command that takes time, so the controller says "started" and the result
  // arrives later as its own events. Asking hci_ask for it would wait for a
  // Command Complete that is never coming.
  Seen seen{};
  Waited saw{};
  const uint32_t deadline = (uint32_t) (length * 1280) + 4000;
  const uint32_t started = now_ms();
  bool accepted = false;
  uint8_t found = 0;

  while (now_ms() - started < deadline) {
    const int len = hci_next_event(hport, events, deadline - (now_ms() - started), &saw);
    if (len < 3) {
      // Three is an event: a code, a parameter length, and one byte of
      // parameters. SIX was the threshold here for one run and it is the
      // minimum of a Command COMPLETE, borrowed from the function that waits
      // for one -- and Inquiry Complete is three bytes long, so the end of
      // every inquiry was being thrown away at the door. The panel's log said
      // "the inquiry never finished" fourteen seconds after a scan that had
      // finished on time. Each case below checks its own length now.
      if (len < 0) {
        break;
      }
      continue;
    }

    switch (g_hci_evt[0]) {
      case HCI_EVENT_COMMAND_STATUS: {
        if (len < 6) {
          break;  // status, credit and the opcode it is about
        }
        const uint16_t about = (uint16_t) (g_hci_evt[4] | (g_hci_evt[5] << 8));
        if (about == HCI_INQUIRY) {
          if (g_hci_evt[2] != 0x00) {
            ESP_LOGW(TAG, "  the controller refused the inquiry, status %02x", g_hci_evt[2]);
            return;
          }
          accepted = true;
        }
        break;
      }
      case HCI_EVENT_INQUIRY_RESULT:
      case HCI_EVENT_INQUIRY_RESULT_RSSI: {
        const bool rssi = g_hci_evt[0] == HCI_EVENT_INQUIRY_RESULT_RSSI;
        const uint8_t entries = g_hci_evt[2];
        for (uint8_t i = 0; i < entries; i++) {
          const uint32_t at = 3u + (uint32_t) i * INQUIRY_ENTRY;
          if (at + INQUIRY_ENTRY > (uint32_t) len) {
            break;  // the device's own count, believed no further than the bytes
          }
          const uint8_t before = seen.count;
          report_found(&g_hci_evt[at], rssi, nullptr, &seen);
          if (seen.count != before) {
            found++;
          }
        }
        break;
      }
      case HCI_EVENT_EXTENDED_INQUIRY_RESULT: {
        if (len >= 3 + INQUIRY_ENTRY) {
          char name[32];
          const bool named = eir_name(&g_hci_evt[3 + INQUIRY_ENTRY],
                                      (uint32_t) len - (3 + INQUIRY_ENTRY), name, sizeof(name));
          const uint8_t before = seen.count;
          report_found(&g_hci_evt[3], true, named ? name : nullptr, &seen);
          if (seen.count != before) {
            found++;
          }
        }
        break;
      }
      case HCI_EVENT_INQUIRY_COMPLETE:
        ESP_LOGI(TAG, "inquiry finished, status %02x, %u device%s heard", g_hci_evt[2],
                 (unsigned) found, found == 1 ? "" : "s");
        return;
      default:
        break;
    }
  }

  if (!accepted) {
    ESP_LOGW(TAG, "  the controller never acknowledged the inquiry");
  } else {
    ESP_LOGW(TAG, "  the inquiry never finished; %u device%s heard", (unsigned) found,
             found == 1 ? "" : "s");
  }
}

// ---------------------------------------------------------------------------
// The HCI transport: what makes a USB dongle Bluedroid's controller.
// ---------------------------------------------------------------------------
//
// This is NOT VHCI, which is what it was nearly written against.
// `esp_vhci_host_send_packet()` belongs to the ESP32's own controller, and
// components/bt/controller/CMakeLists.txt exposes that header only when the
// controller is enabled -- so on a P4 there is no esp_bt.h to call at all.
// Bluedroid's own hci_hal_h4.c guards its include with
// `#if (BT_CONTROLLER_INCLUDED == TRUE)` and goes through esp_bluedroid_hci.h
// instead, which is Espressif's supported hook for exactly this case: a host
// with somebody else's controller.
//
// What crosses those three function pointers is H4 -- one leading byte saying
// what the rest is. The useful part is that USB has already made the same
// split physically, so this is a demultiplex rather than a translation, and it
// is why Linux's btusb.c has the same shape:
//
//     0x01 command  -> the control endpoint, as a class request
//     0x02 ACL      -> the bulk pair
//     0x03 SCO      -> isochronous, which is not carried here
//     0x04 event    <- the interrupt IN endpoint
#ifdef CONFIG_BT_BLUEDROID_ENABLED

static constexpr uint8_t H4_COMMAND = 0x01;
static constexpr uint8_t H4_ACL = 0x02;
static constexpr uint8_t H4_SCO = 0x03;
static constexpr uint8_t H4_EVENT = 0x04;

// Where the dongle is, filled in by the probe once it has one that answers.
// File scope for the same reason g_instance is: these are plain C function
// pointers with no user argument, and there is one bus and one dongle.
struct HciLink {
  struct usbh_hubport *hport;
  struct usb_endpoint_descriptor *events;
  struct usb_endpoint_descriptor *acl_in;
  struct usb_endpoint_descriptor *acl_out;
  uint8_t intf;
  volatile bool up;
};
static HciLink g_link{};
static const esp_bluedroid_hci_driver_callbacks_t *g_host_cb = nullptr;

// DMA touches these, so they go where CherryUSB puts its own, exactly like the
// probe's. Separate from the probe's buffers because they are live at the same
// time as each other: Bluedroid sends from its task while two tasks of ours
// read.
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_tx_cmd[264];
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_tx_acl[1100];
static USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX uint8_t g_rx_acl_pkt[512];

// These two are NOT written by DMA -- every packet lands in a staging buffer
// and is memcpy'd here -- so they are ordinary memory and may start at any
// offset. That distinction is the whole of the alignment fault this file
// already records: it is the buffer handed to the controller that must be on a
// 64-byte line, not the one assembled by hand.
static uint8_t g_rx_evt_frame[264];
static uint8_t g_rx_acl_frame[1100];

// One urb per endpoint, kept for the life of the link, because CherryUSB
// stores the DATA0/DATA1 toggle IN THE URB. The event reader deliberately
// reuses g_event_urb, the one the probe just finished with: a fresh urb would
// restart at DATA0 against a device that has been alternating since the reset,
// and every second packet would be discarded -- the "one, miss, one, miss"
// fault recorded above, which cost a round trip to a board to find once.
static struct usbh_urb g_acl_in_urb;
static struct usbh_urb g_acl_out_urb;

// Errors are counted rather than printed, and only the first of each kind
// reaches the log. A transport fault repeats at the rate of the traffic, and a
// log that scrolls is a log nobody reads -- the same rule the sender's error
// caps live under.
static uint32_t g_tx_errors = 0;
static uint32_t g_sco_dropped = 0;

static bool link_gone(int err) {
  return err == -USB_ERR_NODEV || err == -USB_ERR_NOTCONN || err == -USB_ERR_SHUTDOWN;
}

// Bluedroid calls this from its own HCI task. It blocks until the dongle has
// taken the bytes, which is what check_send_available() being unconditionally
// true means: there is never a send in flight to wait for.
static void hci_drv_send(uint8_t *data, uint16_t len) {
  if (!g_link.up || data == nullptr || len < 2) {
    return;
  }
  const uint8_t kind = data[0];
  const uint8_t *body = data + 1;
  const uint16_t body_len = (uint16_t) (len - 1);

  if (kind == H4_COMMAND) {
    if (body_len > sizeof(g_tx_cmd)) {
      ESP_LOGE(TAG, "a %u-byte HCI command will not fit; dropping it", (unsigned) body_len);
      return;
    }
    // The same class request the probe uses, and deliberately not sharing its
    // hci_command(): that one builds an opcode, and Bluedroid hands over a
    // packet that is already built.
    struct usb_setup_packet *setup = g_link.hport->setup;
    setup->bmRequestType = USB_REQUEST_DIR_OUT | USB_REQUEST_CLASS | USB_REQUEST_RECIPIENT_DEVICE;
    setup->bRequest = 0x00;
    setup->wValue = 0;
    setup->wIndex = g_link.intf;
    setup->wLength = body_len;
    memcpy(g_tx_cmd, body, body_len);
    const int err = usbh_control_transfer(g_link.hport, setup, g_tx_cmd);
    if (err < 0) {
      if (g_tx_errors++ == 0) {
        ESP_LOGE(TAG, "the dongle refused an HCI command (%d); further ones are counted only", err);
      }
      if (link_gone(err)) {
        g_link.up = false;
      }
    }
    return;
  }

  if (kind == H4_ACL) {
    if (g_link.acl_out == nullptr || body_len > sizeof(g_tx_acl)) {
      return;
    }
    memcpy(g_tx_acl, body, body_len);
    usbh_bulk_urb_fill(&g_acl_out_urb, g_link.hport, g_link.acl_out, g_tx_acl, body_len, 1000,
                       nullptr, nullptr);
    const int err = usbh_submit_urb(&g_acl_out_urb);
    if (err < 0) {
      if (g_tx_errors++ == 0) {
        ESP_LOGE(TAG, "the dongle refused ACL data (%d); further ones are counted only", err);
      }
      if (link_gone(err)) {
        g_link.up = false;
      }
    }
    return;
  }

  if (kind == H4_SCO) {
    // SCO is the isochronous alternate settings, and nothing here selects one.
    // That means a headset's MICROPHONE and a telephone call, not music: A2DP
    // is ACL. Said once so it is a known gap rather than a mystery.
    if (g_sco_dropped++ == 0) {
      ESP_LOGW(TAG, "SCO audio is not carried over this transport (voice calls, not music)");
    }
  }
}

// Always true, and that is a statement about hci_drv_send rather than about
// the dongle: it does not return until the transfer is done, so there is never
// anything outstanding for the host to wait behind. The consequence is that
// notify_host_send_available() is never needed -- and never calling it from
// inside send() also keeps this off the reentrant path back into Bluedroid's
// own task.
static bool hci_drv_check_send_available(void) { return g_link.up; }

static esp_err_t hci_drv_register_host_callback(
    const esp_bluedroid_hci_driver_callbacks_t *callback) {
  g_host_cb = callback;
  return ESP_OK;
}

static const esp_bluedroid_hci_driver_operations_t g_hci_ops = {
    .send = hci_drv_send,
    .check_send_available = hci_drv_check_send_available,
    .register_host_callback = hci_drv_register_host_callback,
};

// Events, reassembled a packet at a time exactly the way the probe proved, and
// handed up with the H4 byte in front. A short packet ends an event.
//
// A refusal is the NORMAL state here: an interrupt IN with nothing to report
// NAKs, and the probe measured that coming back in about eleven milliseconds.
// So a refusal is not an error to report, it is silence -- which is also why
// the pause after one is short.
static void hci_event_task(void *arg) {
  uint32_t mps = USB_GET_MAXPACKETSIZE(g_link.events->wMaxPacketSize);
  if (mps == 0 || mps > sizeof(g_hci_pkt)) {
    ESP_LOGE(TAG, "the event endpoint says its packets are %u bytes, which will not fit",
             (unsigned) mps);
    g_link.up = false;
    vTaskDelete(nullptr);
    return;
  }
  uint32_t filled = 0;

  while (g_link.up) {
    usbh_int_urb_fill(&g_event_urb, g_link.hport, g_link.events, g_hci_pkt, mps, 1000, nullptr,
                      nullptr);
    const int err = usbh_submit_urb(&g_event_urb);
    if (err < 0) {
      if (link_gone(err)) {
        break;
      }
      filled = 0;  // half an event is not an event
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    if (g_event_urb.actual_length <= 0) {
      continue;
    }

    uint32_t take = (uint32_t) g_event_urb.actual_length;
    if (1 + filled + take > sizeof(g_rx_evt_frame)) {
      take = (uint32_t) sizeof(g_rx_evt_frame) - 1 - filled;
    }
    memcpy(&g_rx_evt_frame[1 + filled], g_hci_pkt, take);
    filled += take;

    const uint32_t want = event_length(&g_rx_evt_frame[1], filled);
    if (want == 0 || filled < want) {
      continue;  // the header has not arrived yet, or the event has not
    }

    g_rx_evt_frame[0] = H4_EVENT;
    if (g_host_cb != nullptr && g_host_cb->notify_host_recv != nullptr) {
      g_host_cb->notify_host_recv(g_rx_evt_frame, (uint16_t) (want + 1));
    }
    filled = 0;
  }

  g_link.up = false;
  ESP_LOGW(TAG, "the dongle stopped answering; Bluedroid has no controller now");
  vTaskDelete(nullptr);
}

// ACL, which is where everything above a connection lives -- A2DP included.
// Its own task rather than a turn in the one above, because both endpoints
// block and a reader cannot wait on two at once. btusb submits both at the
// same time for the same reason.
static void hci_acl_task(void *arg) {
  uint32_t mps = USB_GET_MAXPACKETSIZE(g_link.acl_in->wMaxPacketSize);
  if (mps == 0 || mps > sizeof(g_rx_acl_pkt)) {
    ESP_LOGE(TAG, "the ACL endpoint says its packets are %u bytes, which will not fit",
             (unsigned) mps);
    vTaskDelete(nullptr);
    return;
  }
  uint32_t filled = 0;

  while (g_link.up) {
    usbh_bulk_urb_fill(&g_acl_in_urb, g_link.hport, g_link.acl_in, g_rx_acl_pkt, mps, 1000, nullptr,
                       nullptr);
    const int err = usbh_submit_urb(&g_acl_in_urb);
    if (err < 0) {
      if (link_gone(err)) {
        break;
      }
      filled = 0;
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    if (g_acl_in_urb.actual_length <= 0) {
      continue;
    }

    uint32_t take = (uint32_t) g_acl_in_urb.actual_length;
    if (1 + filled + take > sizeof(g_rx_acl_frame)) {
      take = (uint32_t) sizeof(g_rx_acl_frame) - 1 - filled;
    }
    memcpy(&g_rx_acl_frame[1 + filled], g_rx_acl_pkt, take);
    filled += take;

    // Same rule, ACL's own header. A bulk endpoint makes this worse rather
    // than better: 64 bytes on this dongle, so every ACL payload of 60, 124,
    // 188 ... would have hung on the short packet that never came.
    const uint32_t want = acl_length(&g_rx_acl_frame[1], filled);
    if (want == 0 || filled < want) {
      continue;
    }

    g_rx_acl_frame[0] = H4_ACL;
    if (g_host_cb != nullptr && g_host_cb->notify_host_recv != nullptr) {
      g_host_cb->notify_host_recv(g_rx_acl_frame, (uint16_t) (want + 1));
    }
    filled = 0;
  }

  vTaskDelete(nullptr);
}

#endif  // CONFIG_BT_BLUEDROID_ENABLED

// Hands the dongle to Bluedroid, in the one order Espressif's header allows:
// attach the transport, THEN initialise, THEN enable. The readers start before
// enable, because the first thing enable does is send an HCI Reset and wait
// for its Command Complete -- with nothing reading the endpoint that wait can
// only time out.
void PortallBT::start_host_stack_(struct usbh_hubport *hport, uint8_t intf,
                                  struct usb_endpoint_descriptor *events,
                                  struct usb_endpoint_descriptor *acl_in,
                                  struct usb_endpoint_descriptor *acl_out) {
  if (!this->host_stack_ || this->stack_up_) {
    return;
  }
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  g_link.hport = hport;
  g_link.intf = intf;
  g_link.events = events;
  g_link.acl_in = acl_in;
  g_link.acl_out = acl_out;
  g_link.up = true;
  g_acl_in_urb = usbh_urb{};
  g_acl_out_urb = usbh_urb{};

  esp_err_t err = esp_bluedroid_attach_hci_driver(&g_hci_ops);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Bluedroid would not take the HCI transport (%d)", (int) err);
    g_link.up = false;
    return;
  }

  if (xTaskCreate(hci_event_task, "portall_bt_evt", 4096, nullptr, 6, nullptr) != pdPASS) {
    ESP_LOGE(TAG, "could not start the task that reads events, so nothing would answer Bluedroid");
    g_link.up = false;
    return;
  }
  if (acl_in != nullptr) {
    if (xTaskCreate(hci_acl_task, "portall_bt_acl", 4096, nullptr, 6, nullptr) != pdPASS) {
      // Events alone are enough to bring the stack up, so this costs the data
      // path and not the milestone. An accessory must never cost the picture.
      ESP_LOGW(TAG, "could not start the ACL reader; the stack will come up but carry no data");
    }
  } else {
    ESP_LOGW(TAG, "this interface has no bulk IN endpoint, so no ACL data can arrive");
  }

  err = esp_bluedroid_init();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Bluedroid would not initialise (%d)", (int) err);
    g_link.up = false;
    return;
  }
  ESP_LOGI(TAG, "Bluedroid initialised; handing it the dongle");

  err = esp_bluedroid_enable();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Bluedroid would not enable (%d) -- the transport is attached and the", (int) err);
    ESP_LOGE(TAG, "  controller answered the probe, so read the HCI traffic above this line");
    g_link.up = false;
    return;
  }

  this->stack_up_ = true;
  ESP_LOGI(TAG, "Bluedroid is ENABLED on a USB dongle -- a Classic host on a chip with no radio");

  // The proof, and it costs one line: this address comes out of Bluedroid,
  // which has never seen the dongle except through the three function pointers
  // above. If it matches the address the probe read for itself -- a different
  // code path, a different buffer, a different task -- then the transport is
  // carrying real answers rather than plausible ones.
  const uint8_t *addr = esp_bt_dev_get_address();
  if (addr != nullptr) {
    ESP_LOGI(TAG, "  Bluedroid reports address %02X:%02X:%02X:%02X:%02X:%02X (it should match above)",
             addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
  }
#else
  (void) hport;
  (void) intf;
  (void) events;
  (void) acl_in;
  (void) acl_out;
  ESP_LOGE(TAG, "host_stack: bluedroid was asked for and CONFIG_BT_BLUEDROID_ENABLED is not set");
#endif
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
  // ACL is on the bulk pair of the same interface, and it is found here rather
  // than later for the same reason: a descriptor read once, in one place, by
  // its attributes. Only the events matter to the probe; the bulk pair matters
  // to whatever carries data afterwards, which is Bluedroid.
  struct usb_endpoint_descriptor *events = nullptr;
  struct usb_endpoint_descriptor *acl_in = nullptr;
  struct usb_endpoint_descriptor *acl_out = nullptr;
  uint8_t endpoints = alt->intf_desc.bNumEndpoints;
  if (endpoints > CONFIG_USBHOST_MAX_ENDPOINTS) {
    endpoints = CONFIG_USBHOST_MAX_ENDPOINTS;
  }
  for (uint8_t i = 0; i < endpoints; i++) {
    struct usb_endpoint_descriptor *ep = &alt->ep[i].ep_desc;
    const bool inbound = (ep->bEndpointAddress & 0x80) != 0;
    const uint8_t kind = USB_GET_ENDPOINT_TYPE(ep->bmAttributes);
    if (inbound && kind == 3 && events == nullptr) {
      events = ep;
    } else if (inbound && kind == 2 && acl_in == nullptr) {
      acl_in = ep;
    } else if (!inbound && kind == 2 && acl_out == nullptr) {
      acl_out = ep;
    }
  }
  if (events == nullptr) {
    ESP_LOGE(TAG, "interface %u has no interrupt endpoint, so events have nowhere to arrive", intf);
    this->probing_ = false;
    return;
  }
  ESP_LOGI(TAG, "  events on endpoint %02x, packet %u, interval %u", events->bEndpointAddress,
           (unsigned) USB_GET_MAXPACKETSIZE(events->wMaxPacketSize), events->bInterval);
  if (acl_in != nullptr && acl_out != nullptr) {
    ESP_LOGI(TAG, "  ACL on endpoints %02x in / %02x out, packets %u / %u",
             acl_in->bEndpointAddress, acl_out->bEndpointAddress,
             (unsigned) USB_GET_MAXPACKETSIZE(acl_in->wMaxPacketSize),
             (unsigned) USB_GET_MAXPACKETSIZE(acl_out->wMaxPacketSize));
  }

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

  // And the thing this was all for: put the radio to work. An inquiry is
  // Bluetooth CLASSIC, which is precisely what the C6 on these panels cannot
  // do -- so a device heard here is a device no panel could have heard before.
  this->inquire_(hport, intf, events);

  // And then, if it was asked for, hand the whole dongle to a real host stack.
  // Last on purpose: everything above is this component reading the endpoint
  // for itself, and from here on the reader tasks own it.
  this->start_host_stack_(hport, intf, events, acl_in, acl_out);

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
