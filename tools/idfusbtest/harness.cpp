// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// portall_bt's own USB layer (components/portall_bt/idf_usb.cpp), run on a
// workstation against a stand-in for ESP-IDF's USB Host Library with a
// Bluetooth dongle behind it -- with portall_bt's SHIPPED probe on top, the
// code that runs on four boards today through CherryUSB.
//
// One scenario per run, named on the command line, because the layer is
// installed once per firmware and keeps its state, exactly as on a board:
//
//   broadcom    the whole probe against a BCM20702A1: vendor class, vendor
//               event before the reset's answer, a 254-byte name, an inquiry
//               that hears a speaker
//   realtek     a TP-Link UB500 on its ROM: 120 fragments of firmware down the
//               control endpoint, then the version the patch reports
//   slow        a dongle that answers Reset 700 ms late, past the read's own
//               500 ms timeout: the answer must still arrive on the first
//               attempt, and the event endpoint must never be cancelled
//   error       the first event read ends in error and halts its endpoint;
//               the layer must clear it and the probe must carry on
//   bluedroid   Bluedroid's transport over the layer: a command out on the
//               control endpoint, its answer in through the event reader, and
//               ACL out on bulk and back in through the ACL reader
//   layer       the layer's own promises, called directly: a read that timed
//               out keeps its transfer and delivers its packet once, an OUT
//               that will not finish is cancelled, an error is cleared, and a
//               dongle pulled out under a read leaves nothing stale behind
//               for the next one
//   rejoin      Bluedroid up on a dongle with a link open and a command
//               waiting; the dongle is pulled out. Bluedroid must get its
//               answer, its link closed and its credit back, be answered while
//               the dongle is away, and have the SAME dongle back -- told its
//               settings again, latest value each -- with no restart
//   other       the same, and a DIFFERENT dongle plugged in: refused, naming
//               both addresses, and Bluedroid still answered in its place
//   watchdog    a dongle that is there and never answers one command: it is
//               answered for after five seconds, and Bluetooth carries on
//   hub         a USB hub in the socket: said to be one, with why a dongle
//               cannot be reached through it, and the dongle plugged in
//               afterwards is probed as usual
//   restart     a dongle that stops answering altogether: after the second
//               command answered for, the layer switches its USB port off and
//               on, and the dongle that enumerates again is taken back
//
// Checks print as "  ok" or "  ECHEC" and the exit status is the count.
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main
#undef private
#undef protected

#include "linkstubs.h"

#include "fakehost.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

using esphome::portall_bt::PortallBT;

static int g_real_out = 1;
static std::string g_log_path;
static int failures = 0;

static void ok(bool good, const std::string &what) {
  dprintf(g_real_out, "  %s %s\n", good ? "ok    " : "ECHEC ", what.c_str());
  if (!good)
    failures++;
}

// Everything the component logs goes to a file, read back by the checks. The
// checks themselves go to the real stdout.
static void capture() {
  char path[] = "/tmp/idfusbtestXXXXXX";
  const int fd = mkstemp(path);
  g_log_path = path;
  fflush(stdout);
  g_real_out = dup(1);
  dup2(fd, 1);
  close(fd);
  setvbuf(stdout, nullptr, _IONBF, 0);
}

static std::string log_text() {
  fflush(stdout);
  std::string out;
  FILE *f = fopen(g_log_path.c_str(), "r");
  if (f == nullptr)
    return out;
  char buf[4096];
  size_t n;
  while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
    out.append(buf, n);
  fclose(f);
  return out;
}

static bool has(const std::string &needle) { return log_text().find(needle) != std::string::npos; }

// One line of the log that says both: a check that two things were said
// TOGETHER, which two lines anywhere in the log would pass by accident.
static bool line_has(const std::string &a, const std::string &b) {
  const std::string text = log_text();
  size_t start = 0;
  while (start < text.size()) {
    size_t end = text.find('\n', start);
    if (end == std::string::npos)
      end = text.size();
    const std::string line = text.substr(start, end - start);
    if (line.find(a) != std::string::npos && line.find(b) != std::string::npos)
      return true;
    start = end + 1;
  }
  return false;
}

static void sleep_ms(uint32_t ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }

// Runs the component's loop() until the log says `needle`, or gives up.
static bool run_until(PortallBT *bt, const std::string &needle, uint32_t ms) {
  for (uint32_t waited = 0; waited < ms; waited += 10) {
    if (bt != nullptr)
      bt->loop();
    if (has(needle))
      return true;
    sleep_ms(10);
  }
  return has(needle);
}

static bool wait_until(bool (*done)(), uint32_t ms) {
  for (uint32_t waited = 0; waited < ms; waited += 10) {
    if (done())
      return true;
    sleep_ms(10);
  }
  return done();
}

// ---------------------------------------------------------------------------
// Bluedroid's half, for the `bluedroid` scenario: what a host does to a
// controller first, through the three function pointers portall_bt attaches.
// ---------------------------------------------------------------------------

static const esp_bluedroid_hci_driver_operations_t *g_ops = nullptr;
static std::mutex g_rx_m;
static std::vector<std::vector<uint8_t>> g_rx;
static bool g_reset_answered = false;
static bool g_acl_echoed = false;

// Bluedroid's own bookkeeping, as bluedroid/hci/hci_layer.c keeps it at
// v5.5.5: ONE credit to start with, spent by every command sent, given back
// only by a Command Complete or a Command Status -- which also takes the
// command off the list of those waiting. An answer for a command nobody is
// waiting for is what hci_layer.c warns about as "no matching command".
static int g_credits = 1;
static std::vector<uint16_t> g_waiting;
static unsigned g_unmatched = 0;
static PortallBT *g_bt = nullptr;

// What enable() does: `bluedroid` sends a reset and some ACL as it always did;
// `startup` brings a controller up the way Bluedroid does, settings and all.
enum class Host { BASIC, STARTUP };
static Host g_host_mode = Host::BASIC;

static int host_recv(uint8_t *data, uint16_t len) {
  std::lock_guard<std::mutex> lock(g_rx_m);
  g_rx.emplace_back(data, data + len);
  if (len >= 7 && data[0] == 0x04 && (data[1] == 0x0E || data[1] == 0x0F)) {
    const bool complete = data[1] == 0x0E;
    g_credits = complete ? data[3] : data[4];
    const uint16_t opcode = complete ? (uint16_t) (data[4] | (data[5] << 8)) : (uint16_t) (data[5] | (data[6] << 8));
    bool matched = false;
    for (size_t i = 0; i < g_waiting.size(); i++) {
      if (g_waiting[i] == opcode) {
        g_waiting.erase(g_waiting.begin() + (long) i);
        matched = true;
        break;
      }
    }
    if (!matched && opcode != 0)
      g_unmatched++;
  }
  return 0;
}

static size_t rx_mark() {
  std::lock_guard<std::mutex> lock(g_rx_m);
  return g_rx.size();
}

// The first frame since `mark` that starts with `head`, or an empty one.
static std::vector<uint8_t> rx_since(size_t mark, const std::vector<uint8_t> &head) {
  std::lock_guard<std::mutex> lock(g_rx_m);
  for (size_t i = mark; i < g_rx.size(); i++)
    if (g_rx[i].size() >= head.size() && std::equal(head.begin(), head.end(), g_rx[i].begin()))
      return g_rx[i];
  return {};
}

static bool waiting_on(uint16_t opcode) {
  std::lock_guard<std::mutex> lock(g_rx_m);
  for (uint16_t op : g_waiting)
    if (op == opcode)
      return true;
  return false;
}

static int credits() {
  std::lock_guard<std::mutex> lock(g_rx_m);
  return g_credits;
}

static void sleep_ms(uint32_t ms);

// Waits, turning the component's loop if asked: an answer given in place of
// a dongle that is gone reaches Bluedroid from the loop, never from a send.
static bool settle(bool (*done)(), uint32_t ms, bool turn_loop) {
  for (uint32_t waited = 0; waited < ms; waited += 10) {
    if (turn_loop && g_bt != nullptr)
      g_bt->loop();
    if (done())
      return true;
    sleep_ms(10);
  }
  return done();
}

// A command from Bluedroid: it needs a credit, and spends it.
static uint16_t g_last_sent = 0;
static bool g_turn_loop = false;
static bool host_send(uint16_t opcode, const std::vector<uint8_t> &params) {
  if (!settle([] { return credits() > 0; }, 6000, g_turn_loop))
    return false;
  std::vector<uint8_t> h4 = {0x01, (uint8_t) (opcode & 0xFF), (uint8_t) (opcode >> 8), (uint8_t) params.size()};
  h4.insert(h4.end(), params.begin(), params.end());
  {
    std::lock_guard<std::mutex> lock(g_rx_m);
    g_credits--;
    g_waiting.push_back(opcode);
  }
  g_last_sent = opcode;
  g_ops->send(h4.data(), (uint16_t) h4.size());
  return true;
}

static bool last_answered() { return !waiting_on(g_last_sent); }
static void host_send_available() {}
static const esp_bluedroid_hci_driver_callbacks_t g_callbacks = {host_send_available, host_recv};

static bool received(const std::vector<uint8_t> &frame) {
  std::lock_guard<std::mutex> lock(g_rx_m);
  for (const auto &one : g_rx)
    if (one == frame)
      return true;
  return false;
}

static bool wait_received(const std::vector<uint8_t> &frame, uint32_t ms) {
  for (uint32_t waited = 0; waited < ms; waited += 10) {
    if (received(frame))
      return true;
    sleep_ms(10);
  }
  return received(frame);
}

esp_err_t esp_bluedroid_attach_hci_driver(const esp_bluedroid_hci_driver_operations_t *ops) {
  g_ops = ops;
  return ESP_OK;
}

static const std::vector<uint8_t> EVENT_MASK = {0xFF, 0xFF, 0xFB, 0xFF, 0x07, 0xF8, 0xBF, 0x3D};
static const std::vector<uint8_t> CLASS_OF_DEVICE = {0x14, 0x04, 0x24};
static std::vector<uint8_t> local_name() {
  std::vector<uint8_t> name(248, 0x00);
  memcpy(name.data(), "Salon", 5);
  return name;
}

// What controller.c and btm_devctl.c do to a controller while Bluedroid
// starts, in miniature: every command waits for its answer before the next.
static bool g_startup_answered = false;
static void bring_up() {
  bool all = true;
  const std::pair<uint16_t, std::vector<uint8_t>> steps[] = {
      {0x0C03, {}}, {0x0C01, EVENT_MASK}, {0x0C24, CLASS_OF_DEVICE}, {0x0C13, local_name()}, {0x0C1A, {0x02}},
  };
  for (const auto &step : steps) {
    all = all && host_send(step.first, step.second) && settle(last_answered, 3000, false);
  }
  g_startup_answered = all;
}

esp_err_t esp_bluedroid_enable(void) {
  if (g_ops == nullptr)
    return ESP_FAIL;
  g_ops->register_host_callback(&g_callbacks);
  if (g_host_mode == Host::STARTUP) {
    bring_up();
    return ESP_OK;
  }
  uint8_t reset[] = {0x01, 0x03, 0x0C, 0x00};
  g_ops->send(reset, sizeof(reset));
  g_reset_answered = wait_received({0x04, 0x0E, 0x04, 0x01, 0x03, 0x0C, 0x00}, 3000);
  uint8_t acl[] = {0x02, 0x01, 0x20, 0x04, 0x00, 'a', 'b', 'c', 'd'};
  g_ops->send(acl, sizeof(acl));
  g_acl_echoed = wait_received(std::vector<uint8_t>(acl, acl + sizeof(acl)), 3000);
  return ESP_OK;
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

static PortallBT *make(bool high_speed, uint16_t inquiry, bool host_stack) {
  auto *bt = new PortallBT();
  bt->set_high_speed(high_speed);
  bt->set_inquiry_seconds(inquiry);
  bt->set_host_stack(host_stack);
  bt->setup();
  return bt;
}

static void broadcom() {
  fake::use(fake::Dongle::BROADCOM);
  PortallBT *bt = make(true, 2, false);
  ok(fake::peripheral_map() == 0, "controller: high_speed installs the library on peripheral 0");
  ok(has("using ESP-IDF's USB Host Library through portall_bt's own layer"), "the layer says it is the one in use");
  fake::plug();
  const bool finished = run_until(bt, "inquiry finished", 15000);
  ok(has("device 1 on bus 0: 0a5c:21e8, full speed"), "the device is read: vendor, product, speed");
  ok(has("device class ff subclass 01 protocol 01"), "and its class, from the device descriptor");
  ok(has("interface 0: class ff subclass 01 protocol 01") && has("interface 1: class ff subclass 01 protocol 01") &&
         has("interface 3: class fe subclass 01 protocol 01"),
     "every interface at alternate setting 0, the six SCO settings walked past");
  ok(!has("interface 4:"), "and no interface counted twice");
  ok(has("interface 0 is vendor class but 01/01"), "the vendor-coated HCI interface is recognised");
  ok(has("events on endpoint 81, packet 16") && has("ACL on endpoints 82 in / 02 out, packets 64 / 64"),
     "its three endpoints are found from the descriptors");
  ok(has("selecting altsetting 0 on interface 0 returned 8"), "SET_INTERFACE goes out and answers 8, as CherryUSB's");
  ok(fake::interface_claimed(0) && fake::set_interface_requests() == 1, "interface 0 is claimed and selected once");
  ok(has("HCI Reset answered, status 00") && !has("it answered on attempt"),
     "HCI Reset is answered on the first attempt, past the Broadcom's own vendor event");
  ok(has("a Broadcom controller, Bluetooth 4.0"), "Read Local Version: a Broadcom, Bluetooth 4.0");
  ok(has("address 00:02:72:DC:33:59"), "Read BD Address: the BCM20702A1's own address");
  ok(has("name \"BCM20702A\""), "Read Local Name: 254 bytes in sixteen packets, reassembled");
  ok(finished && has("found 46:E8:1C:8A:88:DD") && has("UGREEN-90748"),
     "the inquiry hears the speaker, its 257-byte extended result whole");
  ok(has("inquiry finished, status 00, 1 device heard"), "and finishes");
  ok(!has("Do not support Class"), "no CherryUSB lines, and no apology for them");
  const auto cmds = fake::commands();
  const std::vector<uint16_t> want = {0x0C03, 0x1001, 0x1009, 0x0C14, 0x0C45, 0x0401};
  ok(cmds == want, "the dongle was sent Reset, Version, Address, Name, Inquiry Mode, Inquiry -- in that order");
}

static const uint32_t RTL_IMAGE = 30210;
static uint8_t g_image[RTL_IMAGE];

static void realtek() {
  fake::use(fake::Dongle::REALTEK_ROM);
  for (uint32_t i = 0; i < RTL_IMAGE; i++)
    g_image[i] = (uint8_t) (i * 7 + 3);
  auto *bt = new PortallBT();
  bt->set_high_speed(false);
  bt->set_inquiry_seconds(0);
  bt->add_realtek_firmware(1, g_image, RTL_IMAGE);
  bt->setup();
  ok(fake::peripheral_map() == 2, "controller: full_speed installs the library on peripheral 1 (bit 1)");
  fake::plug();
  run_until(bt, "name \"", 30000);
  ok(has("device 1 on bus 0: 2357:0604"), "the TP-Link UB500 is read");
  ok(has("interface 0 is a Bluetooth controller"), "its e0/01/01 HCI interface is recognised");
  ok(has("this is a RTL8761BU running its ROM"), "it is recognised as a Realtek on its ROM");
  ok(has("loading 30210 bytes of firmware into it, 120 fragments"), "the patch is sent: 30210 bytes, 120 fragments");
  const auto frags = fake::rtl_fragments();
  bool indices = frags.size() == 120;
  uint32_t total = 0;
  for (size_t i = 0; indices && i < frags.size(); i++) {
    const uint8_t want = (uint8_t) (esphome::portall_bt::rtl_fragment_index((uint32_t) i) | (i + 1 == frags.size() ? 0x80 : 0));
    indices = !frags[i].empty() && frags[i][0] == want;
    total += (uint32_t) frags[i].size() - 1;
  }
  ok(indices, "every fragment carries its index, the last one marked 0x80");
  bool same = total == RTL_IMAGE;
  uint32_t at = 0;
  for (size_t i = 0; same && i < frags.size(); i++)
    for (size_t k = 1; same && k < frags[i].size(); k++)
      same = frags[i][k] == g_image[at++];
  ok(same, "and the bytes that reached the dongle are the image, 256-byte control transfers and all");
  ok(has("firmware loaded -- it now reports revision dfc6 subversion d922"), "the patched controller reports dfc6 d922");
  ok(has("address E8:48:B8:C8:40:00"), "Read BD Address: the TP-Link's own");
  ok(has("name \"TP-Link UB5A Adapter\"") && !has("Adapter?"), "the name stops at its padding");
}

static void slow() {
  fake::use(fake::Dongle::BROADCOM);
  fake::set_latency(0x0C03, 700);
  PortallBT *bt = make(true, 0, false);
  fake::plug();
  run_until(bt, "name \"", 15000);
  ok(has("HCI Reset answered, status 00") && !has("it answered on attempt"),
     "an answer 700 ms late, past a 500 ms read, still arrives on the first attempt");
  ok(fake::halts(0x81) == 0 && fake::flushes(0x81) == 0,
     "the event endpoint is never halted or flushed to honour a timeout");
  size_t resets = 0;
  for (uint16_t op : fake::commands())
    if (op == 0x0C03)
      resets++;
  ok(resets == 1, "and Reset is sent once");
  ok(has("address 00:02:72:DC:33:59"), "everything after it reads true");
}

static void error() {
  fake::use(fake::Dongle::BROADCOM);
  fake::fail_next_in(0x81, USB_TRANSFER_STATUS_ERROR);
  PortallBT *bt = make(true, 0, false);
  fake::plug();
  run_until(bt, "name \"", 15000);
  ok(fake::clears(0x81) >= 1, "the endpoint halted by the error is cleared");
  ok(has("HCI Reset answered, status 00"), "and the probe carries on: Reset is answered");
  ok(has("address 00:02:72:DC:33:59"), "and so is everything after it");
}

static void bluedroid() {
  fake::use(fake::Dongle::BROADCOM);
  fake::set_acl_loopback(true);
  PortallBT *bt = make(true, 0, true);
  fake::plug();
  run_until(bt, "Bluedroid is ENABLED", 20000);
  ok(g_ops != nullptr, "the transport is attached to Bluedroid");
  ok(g_reset_answered, "a command sent through it is answered, through the event reader task");
  ok(g_acl_echoed, "ACL sent through it goes out on bulk and comes back in through the ACL reader task");
  const auto out = fake::acl_out();
  ok(!out.empty() && out.back() == std::vector<uint8_t>({0x01, 0x20, 0x04, 0x00, 'a', 'b', 'c', 'd'}),
     "the dongle received the ACL packet without its H4 byte");
  ok(has("Bluedroid is ENABLED on a USB dongle"), "Bluedroid comes up");
}

// The layer's own promises, called directly.
static uint8_t g_buf[64];
static bool g_closed() { return fake::closed(); }

static void layer() {
  fake::use(fake::Dongle::BROADCOM);
  static volatile bool configured = false;
  usbh_initialize(0, ESP_USB_HS0_BASE, [](uint8_t, uint8_t, uint8_t, uint8_t, uint8_t event) {
    if (event == USBH_EVENT_DEVICE_CONFIGURED)
      configured = true;
  });
  fake::plug();
  for (int i = 0; i < 200 && !configured; i++)
    sleep_ms(10);
  ok(configured, "a device that arrives is opened and reported CONFIGURED");
  usbh_hubport *port = usbh_find_hubport(0, 1, 1);
  ok(port != nullptr && port->connected, "and can be found");
  if (port == nullptr)
    return;
  usb_endpoint_descriptor *events = &port->config.intf[0].altsetting[0].ep[0].ep_desc;
  usb_endpoint_descriptor *acl_out = &port->config.intf[0].altsetting[0].ep[2].ep_desc;

  usbh_urb urb{};
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 50, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_INVAL, "an endpoint of an interface nobody claimed is refused");

  ok(usbh_set_interface(port, 0, 0) == 8, "selecting interface 0 answers 8");

  usb_setup_packet setup{};
  setup.bmRequestType = USB_REQUEST_DIR_OUT | USB_REQUEST_CLASS | USB_REQUEST_RECIPIENT_DEVICE;
  setup.wIndex = 0;
  setup.wLength = 3;
  uint8_t reset[3] = {0x03, 0x0C, 0x00};
  ok(usbh_control_transfer(port, &setup, reset) == 11, "a command on the control endpoint answers 8 + 3");
  ok(!fake::commands().empty() && fake::commands().back() == 0x0C03, "and reaches the dongle as Reset");
  // Read its answer away: a vendor event, then the Command Complete.
  for (int i = 0; i < 2; i++) {
    usbh_int_urb_fill(&urb, port, events, g_buf, 16, 500, nullptr, nullptr);
    usbh_submit_urb(&urb);
  }

  // A read with nothing to read times out and keeps its transfer.
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 50, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_TIMEOUT, "a read with nothing to read times out");
  ok(fake::halts(0x81) == 0 && fake::flushes(0x81) == 0, "without halting or flushing the endpoint");
  fake::queue_event({0x0E, 0x04, 0x01, 0x34, 0x12, 0x00});
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 500, nullptr, nullptr);
  const int got = usbh_submit_urb(&urb);
  ok(got == 0 && urb.actual_length == 6 && g_buf[3] == 0x34 && g_buf[4] == 0x12,
     "the packet that arrives after it is delivered by the next read");
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 50, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_TIMEOUT, "once, and not again");

  // An error halts the endpoint; the next read clears it and works.
  fake::fail_next_in(0x81, USB_TRANSFER_STATUS_ERROR);
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 500, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_IO, "a transfer that ends in error says so");
  fake::queue_event({0x0E, 0x04, 0x01, 0x78, 0x56, 0x00});
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 500, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == 0 && urb.actual_length == 6 && g_buf[3] == 0x78,
     "and the next read on that endpoint clears the halt and reads");
  ok(fake::clears(0x81) == 1, "cleared exactly once");

  // An OUT the dongle will not take is cancelled, and the next one goes out.
  fake::set_out_stuck(true);
  uint8_t acl[8] = {0x01, 0x20, 0x04, 0x00, 1, 2, 3, 4};
  usbh_bulk_urb_fill(&urb, port, acl_out, acl, sizeof(acl), 50, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_TIMEOUT, "an OUT the dongle will not take times out");
  ok(fake::halts(0x02) == 1 && fake::flushes(0x02) == 1 && fake::clears(0x02) == 1,
     "and is cancelled: halted, flushed and cleared");
  fake::set_out_stuck(false);
  usbh_bulk_urb_fill(&urb, port, acl_out, acl, sizeof(acl), 500, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == 0 && urb.actual_length == 8, "the next OUT goes out whole");

  // A dongle pulled out under a read that nobody is waiting on any more.
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 50, nullptr, nullptr);
  usbh_submit_urb(&urb);  // times out, its transfer left in flight
  fake::unplug();
  ok(wait_until(g_closed, 2000), "a dongle that is pulled out is closed once its transfers come back");
  ok(usbh_find_hubport(0, 1, 1) == nullptr, "and can no longer be found");
  ok(usbh_submit_urb(&urb) == -USB_ERR_NODEV, "and a read on it says the device is gone");

  configured = false;
  fake::plug();
  for (int i = 0; i < 200 && !configured; i++)
    sleep_ms(10);
  port = usbh_find_hubport(0, 1, 1);
  ok(configured && port != nullptr, "the next dongle is opened and reported");
  if (port == nullptr)
    return;
  usbh_set_interface(port, 0, 0);
  events = &port->config.intf[0].altsetting[0].ep[0].ep_desc;
  // Nothing queued yet: the old dongle's last transfer came back NO_DEVICE
  // and gave its semaphore after nobody was waiting. A layer that kept that
  // token answers this read at once with the old transfer's status.
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 100, nullptr, nullptr);
  ok(usbh_submit_urb(&urb) == -USB_ERR_TIMEOUT,
     "its first read waits for an answer rather than taking the one the last dongle left");
  fake::queue_event({0x0E, 0x04, 0x01, 0xBC, 0x9A, 0x00});
  usbh_int_urb_fill(&urb, port, events, g_buf, 16, 500, nullptr, nullptr);
  const int fresh = usbh_submit_urb(&urb);
  ok(fresh == 0 && urb.actual_length == 6 && g_buf[3] == 0xBC,
     "and its first read is its own, not what the last one left behind");
}

// ---------------------------------------------------------------------------
// The dongle goes away and comes back
// ---------------------------------------------------------------------------

static const std::vector<uint8_t> SPEAKER = {0xDD, 0x88, 0x8A, 0x1C, 0xE8, 0x46};

static std::vector<uint8_t> page_params() {
  std::vector<uint8_t> p = SPEAKER;
  p.insert(p.end(), {0x18, 0xCC, 0x01, 0x00, 0x00, 0x00, 0x01});
  return p;
}

// Up, with a setting changed after startup, a link open and one command the
// dongle will never answer. What `rejoin` and `other` both start from.
static PortallBT *up_with_a_link() {
  fake::use(fake::Dongle::BROADCOM);
  fake::set_acl_loopback(true);
  g_host_mode = Host::STARTUP;
  PortallBT *bt = make(true, 0, true);
  g_bt = bt;
  fake::plug();
  run_until(bt, "Bluedroid is ENABLED", 20000);
  g_turn_loop = true;
  ok(g_startup_answered, "Bluedroid's startup is answered, command by command");

  host_send(0x0C1A, {0x03});
  ok(settle(last_answered, 3000, true), "a setting changed after startup is answered by the dongle");
  const size_t mark = rx_mark();
  host_send(0x0405, page_params());
  settle([] { return !rx_since(0, {0x04, 0x03, 0x0B, 0x00, 0x0B, 0x00}).empty(); }, 3000, true);
  ok(!rx_since(mark, {0x04, 0x03, 0x0B, 0x00, 0x0B, 0x00}).empty(), "a link comes up: handle 000b");
  fake::set_latency(0x1405, 600000);
  host_send(0x1405, {0x0B, 0x00});
  return bt;
}

static bool s_gone() { return has("the dongle is gone"); }

static void rejoin() {
  PortallBT *bt = up_with_a_link();
  const size_t sent_before = fake::command_bytes().size();
  const size_t mark = rx_mark();
  fake::unplug();

  ok(settle(last_answered, 5000, true), "the command the dongle took with it is answered for");
  const auto rssi = rx_since(mark, {0x04, 0x0E});
  ok(rssi.size() > 7 && rssi[4] == 0x05 && rssi[5] == 0x14 && rssi[6] == 0x0C,
     "refused, as Read RSSI's own Command Complete (status 0c)");
  ok(!rx_since(mark, {0x04, 0x05, 0x04, 0x00, 0x0B, 0x00, 0x08}).empty(),
     "the link is closed: Disconnection Complete, handle 000b, connection timeout");
  ok(credits() == 1, "and Bluedroid has its credit back");
  ok(settle(s_gone, 2000, true), "the log says the dongle is gone and Bluetooth carries on");

  // While it is away.
  size_t away = rx_mark();
  host_send(0x0405, page_params());
  settle(last_answered, 3000, true);
  ok(!rx_since(away, {0x04, 0x0F, 0x04, 0x0C, 0x01, 0x05, 0x04}).empty(),
     "a page asked for while it is away is refused at once, with a Command Status");
  // Not paged at all: a value neither the startup (02) nor the change after it
  // (03) gave, so the dongle that comes back can only have it from the ledger.
  away = rx_mark();
  host_send(0x0C1A, {0x00});
  settle(last_answered, 3000, true);
  ok(!rx_since(away, {0x04, 0x0E, 0x04, 0x01, 0x1A, 0x0C, 0x00}).empty(),
     "a setting asked for while it is away is told yes");
  ok(fake::command_bytes().size() == sent_before, "and nothing reached a dongle while there was none");
  ok(credits() == 1 && g_unmatched == 0, "every answer matched a command Bluedroid was waiting on");

  // Back.
  fake::plug();
  ok(run_until(bt, "is back, and Bluetooth carries on without a restart", 20000),
     "the same dongle plugged back in is taken back, with no restart");
  const auto all = fake::command_bytes();
  std::vector<std::vector<uint8_t>> since(all.begin() + (long) sent_before, all.end());
  auto find = [&](const std::vector<uint8_t> &cmd) -> long {
    for (size_t i = 0; i < since.size(); i++)
      if (since[i] == cmd)
        return (long) i;
    return -1;
  };
  auto has_opcode = [&](uint16_t op) {
    for (const auto &c : since)
      if (c.size() >= 2 && (uint16_t) (c[0] | (c[1] << 8)) == op)
        return true;
    return false;
  };
  std::vector<uint8_t> mask = {0x01, 0x0C, 0x08};
  mask.insert(mask.end(), EVENT_MASK.begin(), EVENT_MASK.end());
  std::vector<uint8_t> cod = {0x24, 0x0C, 0x03};
  cod.insert(cod.end(), CLASS_OF_DEVICE.begin(), CLASS_OF_DEVICE.end());
  std::vector<uint8_t> name = {0x13, 0x0C, 0xF8};
  const auto n = local_name();
  name.insert(name.end(), n.begin(), n.end());
  const long at_mask = find(mask), at_cod = find(cod), at_name = find(name), at_scan = find({0x1A, 0x0C, 0x01, 0x00});
  ok(at_mask >= 0 && at_cod >= 0 && at_name >= 0, "it is told its event mask, class and name again");
  ok(at_scan >= 0 && find({0x1A, 0x0C, 0x01, 0x03}) < 0 && find({0x1A, 0x0C, 0x01, 0x02}) < 0,
     "and whether it can be paged at the value set while it was away, and that value only");
  ok(at_mask < at_cod && at_cod < at_name && at_name < at_scan, "in the order Bluedroid first gave them");
  ok(!has_opcode(0x0405) && !has_opcode(0x1405), "but not the page or the read: those were activities, not settings");
  size_t resets = 0;
  for (const auto &c : since)
    if (c.size() >= 2 && c[0] == 0x03 && c[1] == 0x0C)
      resets++;
  ok(resets == 1, "and reset once, by the probe");

  // And it carries on.
  const size_t back = rx_mark();
  host_send(0x0405, page_params());
  settle([] { return !rx_since(0, {0x04, 0x0F, 0x04, 0x00}).empty(); }, 3000, true);
  settle(last_answered, 3000, true);
  ok(!rx_since(back, {0x04, 0x0F, 0x04, 0x00, 0x01, 0x05, 0x04}).empty(),
     "a page after it is back reaches the dongle and is accepted");
  settle([] { return false; }, 200, true);
  ok(!rx_since(back, {0x04, 0x03, 0x0B, 0x00, 0x0B, 0x00}).empty(), "and the link comes up again");
  uint8_t acl[] = {0x02, 0x0B, 0x20, 0x04, 0x00, 'b', 'a', 'c', 'k'};
  g_ops->send(acl, sizeof(acl));
  ok(wait_received(std::vector<uint8_t>(acl, acl + sizeof(acl)), 3000),
     "ACL goes out and comes back through the new readers");
  ok(credits() == 1 && g_unmatched == 0, "and every answer still matches");
}

static bool s_refused() { return has("is not the one Bluetooth was started on"); }

static void other() {
  PortallBT *bt = up_with_a_link();
  fake::unplug();
  settle(s_gone, 5000, true);
  fake::use(fake::Dongle::REALTEK_ROM);
  fake::plug();
  ok(run_until(bt, "is not the one Bluetooth was started on", 30000),
     "a different dongle is refused rather than handed to Bluedroid");
  ok(line_has("E8:48:B8:C8:40:00", "00:02:72:DC:33:59"), "naming both addresses, in the one line");
  ok(has("restart the panel"), "and what it takes to use it");
  ok(!has("is back"), "it is not taken back");
  const size_t mark = rx_mark();
  host_send(0x0C1A, {0x02});
  settle(last_answered, 3000, true);
  ok(!rx_since(mark, {0x04, 0x0E, 0x04, 0x01, 0x1A, 0x0C, 0x00}).empty(),
     "and Bluedroid is still answered in its place");
}

static bool s_answered_for() { return has("has not answered command 1405"); }

static void watchdog() {
  fake::use(fake::Dongle::BROADCOM);
  g_host_mode = Host::STARTUP;
  PortallBT *bt = make(true, 0, true);
  g_bt = bt;
  fake::plug();
  run_until(bt, "Bluedroid is ENABLED", 20000);
  g_turn_loop = true;
  fake::set_swallowed(0x1405);
  const size_t mark = rx_mark();
  host_send(0x1405, {0x0B, 0x00});
  settle([] { return false; }, 3000, true);
  ok(waiting_on(0x1405), "a command the dongle does not answer is still waited on at 3 s");
  ok(settle(last_answered, 4000, true), "and answered for by 7 s");
  const auto answer = rx_since(mark, {0x04, 0x0E});
  ok(answer.size() > 7 && answer[4] == 0x05 && answer[5] == 0x14 && answer[6] == 0x0C, "refused, status 0c");
  ok(settle(s_answered_for, 1000, true), "and the log names the command");
  host_send(0x0C1A, {0x03});
  ok(settle(last_answered, 3000, true), "Bluetooth carries on: the next command reaches the dongle");
  ok(credits() == 1, "with its credit");
}

static void hub() {
  fake::use(fake::Dongle::HUB);
  PortallBT *bt = make(true, 0, false);
  fake::plug();
  ok(run_until(bt, "this is a USB hub", 5000), "a hub in the socket is said to be one, not left in silence");
  ok(has("no transaction translator"), "with the reason a dongle behind it cannot be reached");
  ok(!has("asking it who it is"), "and nothing asks it HCI questions");
  fake::unplug();
  sleep_ms(300);
  fake::use(fake::Dongle::BROADCOM);
  fake::plug();
  ok(run_until(bt, "HCI Reset answered", 15000), "the dongle plugged in after it is probed as usual");
}

static bool s_restarting() { return has("restarting it"); }

static void restart() {
  fake::use(fake::Dongle::BROADCOM);
  g_host_mode = Host::STARTUP;
  PortallBT *bt = make(true, 0, true);
  g_bt = bt;
  fake::plug();
  run_until(bt, "Bluedroid is ENABLED", 20000);
  g_turn_loop = true;
  fake::set_swallowed(0x1405);

  host_send(0x1405, {0x0B, 0x00});
  ok(settle(last_answered, 7000, true), "the first command it does not answer is answered for");
  ok(fake::restarts() == 0, "and one is not a reason to restart the dongle");

  host_send(0x1405, {0x0B, 0x00});
  ok(settle(last_answered, 7000, true), "nor is the second left waiting");
  ok(settle(s_restarting, 2000, true), "after the second, the log says the dongle is being restarted");
  // Asked for by the transport, done by the layer's client task a slice later.
  settle([] { return fake::restarts() > 0; }, 2000, true);
  ok(fake::restarts() == 1, "its USB port is switched off, once");
  ok(run_until(bt, "is back, and Bluetooth carries on without a restart", 20000),
     "and the dongle that enumerates again is taken back");

  const size_t mark = rx_mark();
  host_send(0x1405, {0x0B, 0x00});
  ok(settle(last_answered, 3000, true), "the command it stopped answering is answered again");
  const auto answer = rx_since(mark, {0x04, 0x0E});
  ok(answer.size() > 6 && answer[6] == 0x01, "by the dongle itself this time (its own status, not 0c)");
  ok(credits() == 1 && g_unmatched == 0, "with Bluedroid's credit, and nothing unmatched");
}

int main(int argc, char **argv) {
  const std::string which = argc > 1 ? argv[1] : "";
  capture();
  if (which == "broadcom")
    broadcom();
  else if (which == "realtek")
    realtek();
  else if (which == "slow")
    slow();
  else if (which == "error")
    error();
  else if (which == "bluedroid")
    bluedroid();
  else if (which == "layer")
    layer();
  else if (which == "rejoin")
    rejoin();
  else if (which == "other")
    other();
  else if (which == "watchdog")
    watchdog();
  else if (which == "restart")
    restart();
  else if (which == "hub")
    hub();
  else {
    dprintf(g_real_out, "usage: %s broadcom|realtek|slow|error|bluedroid|layer|rejoin|other|watchdog|restart|hub\n", argv[0]);
    std::_Exit(2);
  }
  if (getenv("IDFUSB_SHOW_LOG") != nullptr)
    dprintf(g_real_out, "%s", log_text().c_str());
  unlink(g_log_path.c_str());
  // The library's tasks never end, as on a board; leave without waiting.
  std::_Exit(failures);
}
