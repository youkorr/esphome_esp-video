// SPDX-License-Identifier: MIT AND Apache-2.0
// Copyright (c) 2024-2026 youkorr -- all of it but usb_parse_next_descriptor().
// Not ours, and not licensed by this file: usb_parse_next_descriptor() is
// ESP-IDF v5.5.5's own (components/usb/usb_helpers.c), Copyright Espressif
// Systems (Shanghai) CO LTD, Apache-2.0, copied as it is there.
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// A stand-in for ESP-IDF v5.5.5's USB Host Library, with a Bluetooth dongle
// behind it, for tools/checkidfusb.py.
//
// It behaves the way the library's own source says it does, in the places
// portall_bt's layer depends on (read in components/usb/usb_host.c and
// hcd_dwc.c at v5.5.5):
//
//   - a transfer's callback runs from usb_host_client_handle_events(), on the
//     client's task, and nowhere else
//   - a transfer is never timed out; an IN with nothing to read stays queued
//   - submitting a transfer that is still in flight is ESP_ERR_NOT_FINISHED
//   - an endpoint is usable only once its interface is claimed
//   - a transfer that ends in error halts its endpoint, and nothing more
//     happens on it until usb_host_endpoint_clear()
//   - flushing an endpoint completes what is queued on it CANCELED
//   - unplugging completes everything in flight NO_DEVICE, then DEV_GONE
//
// And the dongle answers HCI the way the two this project has measured do,
// in the event shapes portall_bt.cpp was proved against: a Broadcom
// BCM20702A1, and a TP-Link UB500 (Realtek RTL8761BU) running its ROM until it
// is sent a patch. One packet per IN transfer, 16 bytes at most on the event
// endpoint and 64 on ACL, and an event never shares a packet with the next.
#include "fakehost.h"

#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>

namespace {

using Clock = std::chrono::steady_clock;

uint64_t now_ms() {
  static const auto start = Clock::now();
  return (uint64_t) std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count();
}

struct Item {
  std::vector<uint8_t> bytes;
  size_t at = 0;
  uint64_t due = 0;
};

struct Ep {
  bool halted = false;
  std::deque<usb_transfer_t *> pending;
  bool fail_armed = false;
  usb_transfer_status_t fail_status = USB_TRANSFER_STATUS_ERROR;
  unsigned halts = 0, flushes = 0, clears = 0;
};

struct Fake {
  std::mutex m;
  std::condition_variable cv;
  fake::Dongle model = fake::Dongle::BROADCOM;
  bool installed = false;
  unsigned peripheral_map = 0;
  usb_host_client_event_cb_t callback = nullptr;
  void *callback_arg = nullptr;
  bool present = false;
  bool opened = false;
  bool closed = false;
  std::set<uint8_t> claimed;
  std::map<uint8_t, Ep> eps;
  std::deque<Item> events;
  std::deque<Item> acl_in;
  std::deque<usb_host_client_event_msg_t> client_events;
  std::deque<usb_transfer_t *> done;
  std::set<usb_transfer_t *> inflight;
  std::map<uint16_t, uint32_t> latency;
  std::set<uint16_t> swallowed;
  bool silent_inquiry = false;
  bool out_stuck = false;
  bool loopback = false;
  bool rtl_patched = false;
  // The root port, which ESP-IDF can switch off and on: the dongle stays in
  // its socket while it is off, and enumerates anew when it comes back on.
  bool port_off = false;
  bool in_socket = false;
  unsigned restarts = 0;
  unsigned set_interfaces = 0;
  std::vector<uint16_t> commands;
  std::vector<std::vector<uint8_t>> command_bytes;
  std::vector<std::vector<uint8_t>> acl_out;
  std::vector<std::vector<uint8_t>> fragments;
  std::vector<uint8_t> config;
  usb_device_desc_t device{};
  std::vector<uint8_t> manufacturer, product;
};

Fake g;

int dummy_device;
usb_device_handle_t const DEV = reinterpret_cast<usb_device_handle_t>(&dummy_device);
int dummy_client;

uint16_t mps_of(uint8_t address) { return address == 0x81 ? 16 : 64; }

std::vector<uint8_t> string_desc(const char *text) {
  std::vector<uint8_t> out = {0, 0x03};
  for (const char *c = text; *c; c++) {
    out.push_back((uint8_t) *c);
    out.push_back(0);
  }
  out[0] = (uint8_t) out.size();
  return out;
}

// The configuration a dongle carries, in the order the USB Bluetooth class
// lays it out: the HCI interface with its interrupt and bulk pair, then the
// SCO interface with SIX alternate settings -- the reason CherryUSB needed
// patching -- then, on the Broadcom, a vendor interface and a DFU one.
std::vector<uint8_t> build_config(uint8_t cls, bool vendor_extras) {
  std::vector<uint8_t> c = {9, 0x02, 0, 0, 0, 1, 0, 0xE0, 50};
  auto intf = [&](uint8_t number, uint8_t alt, uint8_t endpoints, uint8_t ic, uint8_t sub, uint8_t prot) {
    c.insert(c.end(), {9, 0x04, number, alt, endpoints, ic, sub, prot, 0});
  };
  auto ep = [&](uint8_t address, uint8_t attributes, uint16_t mps, uint8_t interval) {
    c.insert(c.end(), {7, 0x05, address, attributes, (uint8_t) (mps & 0xFF), (uint8_t) (mps >> 8), interval});
  };
  intf(0, 0, 3, cls, 0x01, 0x01);
  ep(0x81, 0x03, 16, 1);
  ep(0x82, 0x02, 64, 1);
  ep(0x02, 0x02, 64, 1);
  const uint16_t sco[6] = {0, 9, 17, 25, 33, 49};
  for (uint8_t alt = 0; alt < 6; alt++) {
    intf(1, alt, 2, cls, 0x01, 0x01);
    ep(0x83, 0x01, sco[alt], 1);
    ep(0x03, 0x01, sco[alt], 1);
  }
  uint8_t interfaces = 2;
  if (vendor_extras) {
    intf(2, 0, 2, 0xFF, 0xFF, 0xFF);
    ep(0x84, 0x02, 32, 1);
    ep(0x04, 0x02, 32, 1);
    intf(3, 0, 0, 0xFE, 0x01, 0x01);
    c.insert(c.end(), {9, 0x21, 0x05, 0x88, 0x13, 0x40, 0x00, 0x10, 0x01});  // DFU functional
    interfaces = 4;
  }
  c[2] = (uint8_t) (c.size() & 0xFF);
  c[3] = (uint8_t) (c.size() >> 8);
  c[4] = interfaces;
  return c;
}

// A USB 2.0 hub, which is what somebody plugs in when the socket is wanted
// for two things: device class 09, one interface of class 09 with its status
// endpoint, and no Bluetooth anywhere.
std::vector<uint8_t> build_hub_config() {
  std::vector<uint8_t> c = {9, 0x02, 25, 0, 1, 1, 0, 0xE0, 50};
  c.insert(c.end(), {9, 0x04, 0, 0, 1, 0x09, 0x00, 0x00, 0});
  c.insert(c.end(), {7, 0x05, 0x81, 0x03, 1, 0, 12});
  return c;
}

void set_model(fake::Dongle model) {
  g.model = model;
  if (model == fake::Dongle::HUB) {
    memset(&g.device, 0, sizeof(g.device));
    g.device.bLength = 18;
    g.device.bDescriptorType = 0x01;
    g.device.bcdUSB = 0x0200;
    g.device.bDeviceClass = 0x09;
    g.device.bMaxPacketSize0 = 64;
    g.device.idVendor = 0x05E3;
    g.device.idProduct = 0x0608;
    g.device.bNumConfigurations = 1;
    g.config = build_hub_config();
    g.manufacturer = string_desc("GenesysLogic");
    g.product = string_desc("USB2.0 Hub");
    g.rtl_patched = false;
    return;
  }
  const bool broadcom = model == fake::Dongle::BROADCOM;
  memset(&g.device, 0, sizeof(g.device));
  g.device.bLength = 18;
  g.device.bDescriptorType = 0x01;
  g.device.bcdUSB = 0x0200;
  g.device.bDeviceClass = broadcom ? 0xFF : 0xE0;
  g.device.bDeviceSubClass = 0x01;
  g.device.bDeviceProtocol = 0x01;
  g.device.bMaxPacketSize0 = 64;
  g.device.idVendor = broadcom ? 0x0A5C : 0x2357;
  g.device.idProduct = broadcom ? 0x21E8 : 0x0604;
  g.device.bNumConfigurations = 1;
  g.config = build_config(broadcom ? 0xFF : 0xE0, broadcom);
  g.manufacturer = string_desc(broadcom ? "Broadcom Corp" : "Realtek");
  g.product = string_desc(broadcom ? "BCM20702A0" : "TP-Link UB500 Adapter");
  g.rtl_patched = false;
}

// Called with the lock held: hands every IN endpoint whatever is due to it,
// one packet per transfer.
void pump() {
  const uint64_t now = now_ms();
  for (uint8_t address : {(uint8_t) 0x81, (uint8_t) 0x82, (uint8_t) 0x02}) {
    Ep &ep = g.eps[address];
    while (!ep.halted && !ep.pending.empty()) {
      usb_transfer_t *t = ep.pending.front();
      if (ep.fail_armed) {
        ep.fail_armed = false;
        ep.pending.pop_front();
        t->status = ep.fail_status;
        t->actual_num_bytes = 0;
        g.done.push_back(t);
        // What the library does on a transfer error: halt, and flush the rest.
        ep.halted = true;
        while (!ep.pending.empty()) {
          usb_transfer_t *other = ep.pending.front();
          ep.pending.pop_front();
          other->status = USB_TRANSFER_STATUS_CANCELED;
          other->actual_num_bytes = 0;
          g.done.push_back(other);
        }
        break;
      }
      if ((address & 0x80) == 0) {
        if (g.out_stuck)
          break;
        ep.pending.pop_front();
        std::vector<uint8_t> bytes(t->data_buffer, t->data_buffer + t->num_bytes);
        g.acl_out.push_back(bytes);
        if (g.loopback)
          g.acl_in.push_back(Item{bytes, 0, now});
        t->status = USB_TRANSFER_STATUS_COMPLETED;
        t->actual_num_bytes = t->num_bytes;
        g.done.push_back(t);
        continue;
      }
      std::deque<Item> &queue = address == 0x81 ? g.events : g.acl_in;
      if (queue.empty() || queue.front().due > now)
        break;
      Item &item = queue.front();
      size_t chunk = item.bytes.size() - item.at;
      if (chunk > mps_of(address))
        chunk = mps_of(address);
      if (chunk > (size_t) t->num_bytes)
        chunk = (size_t) t->num_bytes;
      memcpy(t->data_buffer, item.bytes.data() + item.at, chunk);
      item.at += chunk;
      if (item.at >= item.bytes.size())
        queue.pop_front();
      ep.pending.pop_front();
      t->status = USB_TRANSFER_STATUS_COMPLETED;
      t->actual_num_bytes = (int) chunk;
      g.done.push_back(t);
    }
  }
  g.cv.notify_all();
}

void push_event(std::vector<uint8_t> event, uint64_t due) { g.events.push_back(Item{std::move(event), 0, due}); }

void command_complete(uint16_t op, std::vector<uint8_t> params, uint64_t due) {
  std::vector<uint8_t> ev = {0x0E, (uint8_t) (3 + params.size()), 0x01, (uint8_t) (op & 0xFF), (uint8_t) (op >> 8)};
  ev.insert(ev.end(), params.begin(), params.end());
  push_event(ev, due);
}

// The dongle's half of HCI. Called with the lock held.
void hci_command(const uint8_t *cmd, size_t len) {
  if (len < 3)
    return;
  const uint16_t op = (uint16_t) (cmd[0] | (cmd[1] << 8));
  const uint8_t plen = cmd[2];
  const uint8_t *p = cmd + 3;
  g.commands.push_back(op);
  g.command_bytes.push_back(std::vector<uint8_t>(cmd, cmd + len));
  const uint64_t due = now_ms() + (g.latency.count(op) ? g.latency[op] : 0);
  const bool broadcom = g.model == fake::Dongle::BROADCOM;
  if (g.swallowed.count(op))
    return;  // taken, and never answered
  switch (op) {
    // The settings Bluedroid gives a controller, which both dongles take.
    case 0x0C01:  // Set Event Mask
    case 0x0C13:  // Write Local Name
    case 0x0C1A:  // Write Scan Enable
    case 0x0C24:  // Write Class Of Device
    case 0x0C56:  // Write Simple Pairing Mode
    case 0x080F:  // Write Default Link Policy Settings
      command_complete(op, {0x00}, due);
      break;
    case 0x0405: {  // Create Connection: a status now, the link a moment later
      push_event({0x0F, 0x04, 0x00, 0x01, 0x05, 0x04}, due);
      std::vector<uint8_t> cc = {0x03, 0x0B, 0x00, 0x0B, 0x00};
      cc.insert(cc.end(), p, p + (plen >= 6 ? 6 : plen));
      cc.resize(11, 0x00);
      cc.push_back(0x01);
      cc.push_back(0x00);
      push_event(cc, due + 30);
      break;
    }
    case 0x0C03:  // Reset. A Broadcom says something of its own first.
      if (broadcom)
        push_event({0xFF, 0x03, 0x2A, 0x00, 0x00}, due);
      command_complete(op, {0x00}, due);
      break;
    case 0x1001: {  // Read Local Version
      uint8_t hci = 6, lmp = 6;
      uint16_t rev = 0x1000, manufacturer = 15, sub = 0x220E;
      if (!broadcom) {
        hci = 10;
        lmp = 10;
        manufacturer = 93;
        rev = g.rtl_patched ? 0xDFC6 : 0x000B;
        sub = g.rtl_patched ? 0xD922 : 0x8761;
      }
      command_complete(op,
                       {0x00, hci, (uint8_t) (rev & 0xFF), (uint8_t) (rev >> 8), lmp, (uint8_t) (manufacturer & 0xFF),
                        (uint8_t) (manufacturer >> 8), (uint8_t) (sub & 0xFF), (uint8_t) (sub >> 8)},
                       due);
      break;
    }
    case 0x1009:  // Read BD Address, little-endian
      if (broadcom)
        command_complete(op, {0x00, 0x59, 0x33, 0xDC, 0x72, 0x02, 0x00}, due);
      else
        command_complete(op, {0x00, 0x00, 0x40, 0xC8, 0xB8, 0x48, 0xE8}, due);
      break;
    case 0x0C14: {  // Read Local Name: 248 bytes, and the TP-Link pads with junk
      std::vector<uint8_t> params = {0x00};
      const char *name = broadcom ? "BCM20702A" : "TP-Link UB5A Adapter";
      for (size_t i = 0; i < 248; i++)
        params.push_back(i < strlen(name) ? (uint8_t) name[i] : (broadcom ? 0x00 : 0xFF));
      command_complete(op, params, due);
      break;
    }
    case 0x0C45:  // Write Inquiry Mode
      command_complete(op, {0x00}, due);
      break;
    case 0x0401: {  // Inquiry: a status now, a result and the end later
      push_event({0x0F, 0x04, 0x00, 0x01, 0x01, 0x04}, due);
      if (g.silent_inquiry)
        break;
      std::vector<uint8_t> res = {0x2F, 0xFF, 0x01, 0xDD, 0x88, 0x8A, 0x1C, 0xE8, 0x46, 0x01, 0x00,
                                  0x04, 0x04, 0x24, 0x00, 0x00, 0xC0};
      const char *speaker = "UGREEN-90748";
      res.push_back((uint8_t) (1 + strlen(speaker)));
      res.push_back(0x09);
      for (const char *c = speaker; *c; c++)
        res.push_back((uint8_t) *c);
      while (res.size() < 257)
        res.push_back(0x00);
      push_event(res, due + 100);
      push_event({0x01, 0x01, 0x00}, due + 200);
      break;
    }
    case 0xFC6D:  // Realtek Read ROM Version
      if (broadcom)
        command_complete(op, {0x01}, due);
      else
        command_complete(op, {0x00, 0x01}, due);
      break;
    case 0xFC20:  // Realtek firmware download
      g.fragments.push_back(std::vector<uint8_t>(p, p + plen));
      command_complete(op, {0x00, plen ? p[0] : (uint8_t) 0}, due);
      if (plen && (p[0] & 0x80))
        g.rtl_patched = true;
      break;
    default:  // Unknown HCI Command
      command_complete(op, {0x01}, due);
      break;
  }
}

bool endpoint_usable(uint8_t address) {
  // The HCI interface's three endpoints, usable once interface 0 is claimed.
  if (address == 0x81 || address == 0x82 || address == 0x02)
    return g.claimed.count(0) != 0;
  return false;
}

}  // namespace

namespace fake {

void use(Dongle dongle) {
  std::lock_guard<std::mutex> lock(g.m);
  set_model(dongle);
}

void plug_locked() {
  g.present = true;
  g.closed = false;
  g.rtl_patched = false;
  g.events.clear();
  g.acl_in.clear();
  for (auto &entry : g.eps) {
    entry.second.halted = false;
    entry.second.pending.clear();
  }
  usb_host_client_event_msg_t msg;
  memset(&msg, 0, sizeof(msg));
  msg.event = USB_HOST_CLIENT_EVENT_NEW_DEV;
  msg.new_dev.address = 1;
  g.client_events.push_back(msg);
  g.cv.notify_all();
}

void unplug_locked() {
  g.present = false;
  for (auto &entry : g.eps) {
    Ep &ep = entry.second;
    while (!ep.pending.empty()) {
      usb_transfer_t *t = ep.pending.front();
      ep.pending.pop_front();
      t->status = USB_TRANSFER_STATUS_NO_DEVICE;
      t->actual_num_bytes = 0;
      g.done.push_back(t);
    }
  }
  if (g.opened) {
    usb_host_client_event_msg_t msg;
    memset(&msg, 0, sizeof(msg));
    msg.event = USB_HOST_CLIENT_EVENT_DEV_GONE;
    msg.dev_gone.dev_hdl = DEV;
    g.client_events.push_back(msg);
  }
  g.cv.notify_all();
}

void plug() {
  std::lock_guard<std::mutex> lock(g.m);
  g.in_socket = true;
  if (!g.port_off)
    plug_locked();
}

void unplug() {
  std::lock_guard<std::mutex> lock(g.m);
  g.in_socket = false;
  unplug_locked();
}

unsigned restarts() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.restarts;
}

void set_latency(uint16_t opcode, uint32_t ms) {
  std::lock_guard<std::mutex> lock(g.m);
  g.latency[opcode] = ms;
}

void set_swallowed(uint16_t opcode) {
  std::lock_guard<std::mutex> lock(g.m);
  g.swallowed.insert(opcode);
}

void set_silent_inquiry(bool on) {
  std::lock_guard<std::mutex> lock(g.m);
  g.silent_inquiry = on;
}

void fail_next_in(uint8_t address, usb_transfer_status_t status) {
  std::lock_guard<std::mutex> lock(g.m);
  g.eps[address].fail_armed = true;
  g.eps[address].fail_status = status;
  pump();
}

void set_out_stuck(bool on) {
  std::lock_guard<std::mutex> lock(g.m);
  g.out_stuck = on;
  pump();
}

void queue_event(const std::vector<uint8_t> &event) {
  std::lock_guard<std::mutex> lock(g.m);
  push_event(event, now_ms());
  pump();
}

void set_acl_loopback(bool on) {
  std::lock_guard<std::mutex> lock(g.m);
  g.loopback = on;
}

std::vector<uint16_t> commands() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.commands;
}

std::vector<std::vector<uint8_t>> command_bytes() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.command_bytes;
}

std::vector<std::vector<uint8_t>> acl_out() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.acl_out;
}

std::vector<std::vector<uint8_t>> rtl_fragments() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.fragments;
}

unsigned peripheral_map() { return g.peripheral_map; }
unsigned halts(uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  return g.eps[address].halts;
}
unsigned flushes(uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  return g.eps[address].flushes;
}
unsigned clears(uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  return g.eps[address].clears;
}
unsigned set_interface_requests() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.set_interfaces;
}
bool interface_claimed(uint8_t number) {
  std::lock_guard<std::mutex> lock(g.m);
  return g.claimed.count(number) != 0;
}
bool closed() {
  std::lock_guard<std::mutex> lock(g.m);
  return g.closed;
}

}  // namespace fake

// ---------------------------------------------------------------------------
// The library's API
// ---------------------------------------------------------------------------

const char *esp_err_to_name(esp_err_t code) {
  switch (code) {
    case ESP_OK:
      return "ESP_OK";
    case ESP_ERR_INVALID_ARG:
      return "ESP_ERR_INVALID_ARG";
    case ESP_ERR_INVALID_STATE:
      return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_NOT_FOUND:
      return "ESP_ERR_NOT_FOUND";
    case ESP_ERR_NOT_FINISHED:
      return "ESP_ERR_NOT_FINISHED";
    default:
      return "ESP_FAIL";
  }
}

esp_err_t usb_host_install(const usb_host_config_t *config) {
  std::lock_guard<std::mutex> lock(g.m);
  if (g.installed)
    return ESP_ERR_INVALID_STATE;
  g.installed = true;
  g.peripheral_map = config->peripheral_map;
  return ESP_OK;
}

// What hub.c and hcd_dwc.c do at v5.5.5: off disconnects whatever is on the
// port (and is ESP_OK when it is already off), on is ESP_ERR_INVALID_STATE
// unless it was off. A dongle still in its socket enumerates again -- and the
// stand-in's dongle comes back reset, which is the case worth testing: one
// that a restart does not cure stays away, and Bluedroid stays answered.
esp_err_t usb_host_lib_set_root_port_power(bool enable) {
  std::lock_guard<std::mutex> lock(g.m);
  if (!enable) {
    if (g.port_off)
      return ESP_OK;
    g.port_off = true;
    g.restarts++;
    fake::unplug_locked();
    return ESP_OK;
  }
  if (!g.port_off)
    return ESP_ERR_INVALID_STATE;
  g.port_off = false;
  if (g.in_socket) {
    g.swallowed.clear();
    fake::plug_locked();
  }
  return ESP_OK;
}

esp_err_t usb_host_lib_handle_events(TickType_t, uint32_t *flags) {
  if (flags != nullptr)
    *flags = 0;
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  return ESP_ERR_TIMEOUT;
}

esp_err_t usb_host_client_register(const usb_host_client_config_t *config, usb_host_client_handle_t *out) {
  std::lock_guard<std::mutex> lock(g.m);
  g.callback = config->async.client_event_callback;
  g.callback_arg = config->async.callback_arg;
  *out = reinterpret_cast<usb_host_client_handle_t>(&dummy_client);
  return ESP_OK;
}

esp_err_t usb_host_client_handle_events(usb_host_client_handle_t, TickType_t ticks) {
  std::deque<usb_host_client_event_msg_t> events;
  std::deque<usb_transfer_t *> done;
  {
    std::unique_lock<std::mutex> lock(g.m);
    pump();
    const uint32_t wait = ticks < 10 ? ticks : 10;
    g.cv.wait_for(lock, std::chrono::milliseconds(wait),
                  [] { return !g.client_events.empty() || !g.done.empty(); });
    pump();
    events.swap(g.client_events);
    done.swap(g.done);
    for (usb_transfer_t *t : done)
      g.inflight.erase(t);
  }
  for (const auto &msg : events)
    if (g.callback != nullptr)
      g.callback(&msg, g.callback_arg);
  for (usb_transfer_t *t : done)
    t->callback(t);
  return events.empty() && done.empty() ? ESP_ERR_TIMEOUT : ESP_OK;
}

esp_err_t usb_host_device_open(usb_host_client_handle_t, uint8_t addr, usb_device_handle_t *out) {
  std::lock_guard<std::mutex> lock(g.m);
  if (!g.present || addr != 1)
    return ESP_ERR_NOT_FOUND;
  g.opened = true;
  *out = DEV;
  return ESP_OK;
}

esp_err_t usb_host_device_close(usb_host_client_handle_t, usb_device_handle_t) {
  std::lock_guard<std::mutex> lock(g.m);
  g.opened = false;
  g.closed = true;
  g.claimed.clear();
  return ESP_OK;
}

esp_err_t usb_host_device_info(usb_device_handle_t, usb_device_info_t *info) {
  std::lock_guard<std::mutex> lock(g.m);
  memset(info, 0, sizeof(*info));
  info->speed = USB_SPEED_FULL;
  info->dev_addr = 1;
  info->bMaxPacketSize0 = 64;
  info->bConfigurationValue = 1;
  info->str_desc_manufacturer = reinterpret_cast<const usb_str_desc_t *>(g.manufacturer.data());
  info->str_desc_product = reinterpret_cast<const usb_str_desc_t *>(g.product.data());
  info->str_desc_serial_num = nullptr;
  return ESP_OK;
}

esp_err_t usb_host_get_device_descriptor(usb_device_handle_t, const usb_device_desc_t **out) {
  *out = &g.device;
  return ESP_OK;
}

esp_err_t usb_host_get_active_config_descriptor(usb_device_handle_t, const usb_config_desc_t **out) {
  *out = reinterpret_cast<const usb_config_desc_t *>(g.config.data());
  return ESP_OK;
}

esp_err_t usb_host_interface_claim(usb_host_client_handle_t, usb_device_handle_t, uint8_t number, uint8_t) {
  std::lock_guard<std::mutex> lock(g.m);
  if (!g.opened)
    return ESP_ERR_INVALID_STATE;
  g.claimed.insert(number);
  return ESP_OK;
}

esp_err_t usb_host_interface_release(usb_host_client_handle_t, usb_device_handle_t, uint8_t number) {
  std::lock_guard<std::mutex> lock(g.m);
  if (number == 0)
    for (uint8_t address : {(uint8_t) 0x81, (uint8_t) 0x82, (uint8_t) 0x02})
      if (!g.eps[address].pending.empty())
        return ESP_ERR_INVALID_STATE;
  g.claimed.erase(number);
  return ESP_OK;
}

esp_err_t usb_host_endpoint_halt(usb_device_handle_t, uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  g.eps[address].halted = true;
  g.eps[address].halts++;
  return ESP_OK;
}

esp_err_t usb_host_endpoint_flush(usb_device_handle_t, uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  Ep &ep = g.eps[address];
  ep.flushes++;
  while (!ep.pending.empty()) {
    usb_transfer_t *t = ep.pending.front();
    ep.pending.pop_front();
    t->status = USB_TRANSFER_STATUS_CANCELED;
    t->actual_num_bytes = 0;
    g.done.push_back(t);
  }
  g.cv.notify_all();
  return ESP_OK;
}

esp_err_t usb_host_endpoint_clear(usb_device_handle_t, uint8_t address) {
  std::lock_guard<std::mutex> lock(g.m);
  g.eps[address].halted = false;
  g.eps[address].clears++;
  pump();
  return ESP_OK;
}

esp_err_t usb_host_transfer_alloc(size_t size, int, usb_transfer_t **out) {
  auto *t = static_cast<usb_transfer_t *>(calloc(1, sizeof(usb_transfer_t)));
  auto *buffer = static_cast<uint8_t *>(calloc(1, size));
  if (t == nullptr || buffer == nullptr)
    return ESP_ERR_NO_MEM;
  // The library writes these once, through its own copy of the struct.
  *const_cast<uint8_t **>(&t->data_buffer) = buffer;
  *const_cast<size_t *>(&t->data_buffer_size) = size;
  *const_cast<int *>(&t->num_isoc_packets) = 0;
  *out = t;
  return ESP_OK;
}

esp_err_t usb_host_transfer_free(usb_transfer_t *t) {
  if (t != nullptr) {
    free(t->data_buffer);
    free(t);
  }
  return ESP_OK;
}

esp_err_t usb_host_transfer_submit(usb_transfer_t *t) {
  std::lock_guard<std::mutex> lock(g.m);
  if (t->device_handle == nullptr || (t->bEndpointAddress & 0x0F) == 0)
    return ESP_ERR_INVALID_ARG;
  if (!g.present)
    return ESP_ERR_INVALID_STATE;
  if (!endpoint_usable(t->bEndpointAddress))
    return ESP_ERR_NOT_FOUND;
  if (g.inflight.count(t))
    return ESP_ERR_NOT_FINISHED;
  if ((t->bEndpointAddress & 0x80) && t->num_bytes % mps_of(t->bEndpointAddress) != 0)
    return ESP_ERR_INVALID_ARG;  // an IN transfer is whole packets
  if (t->num_bytes > (int) t->data_buffer_size)
    return ESP_ERR_INVALID_ARG;
  g.inflight.insert(t);
  g.eps[t->bEndpointAddress].pending.push_back(t);
  pump();
  return ESP_OK;
}

esp_err_t usb_host_transfer_submit_control(usb_host_client_handle_t, usb_transfer_t *t) {
  std::lock_guard<std::mutex> lock(g.m);
  if (t->device_handle == nullptr || (t->bEndpointAddress & 0x0F) != 0)
    return ESP_ERR_INVALID_ARG;
  if (!g.present)
    return ESP_ERR_INVALID_STATE;
  if (g.inflight.count(t))
    return ESP_ERR_NOT_FINISHED;
  if (t->num_bytes < 8 || t->num_bytes > (int) t->data_buffer_size)
    return ESP_ERR_INVALID_ARG;
  const uint8_t *s = t->data_buffer;
  const uint16_t length = (uint16_t) (s[6] | (s[7] << 8));
  if (s[0] == 0x20 && s[1] == 0x00) {
    hci_command(s + 8, length);
  } else if (s[0] == 0x01 && s[1] == 0x0B) {
    g.set_interfaces++;
  }
  g.inflight.insert(t);
  t->status = USB_TRANSFER_STATUS_COMPLETED;
  t->actual_num_bytes = t->num_bytes;
  g.done.push_back(t);
  pump();
  return ESP_OK;
}

// ESP-IDF v5.5.5's own descriptor walker (components/usb/usb_helpers.c,
// Apache-2.0, Espressif), as it is there.
const usb_standard_desc_t *usb_parse_next_descriptor(const usb_standard_desc_t *cur_desc, uint16_t wTotalLength,
                                                     int *offset) {
  if (cur_desc->bLength < sizeof(usb_standard_desc_t))
    return NULL;
  if (*offset >= wTotalLength)
    return NULL;
  const int next_offset = *offset + cur_desc->bLength;
  if (next_offset + (int) sizeof(usb_standard_desc_t) > (int) wTotalLength)
    return NULL;
  const usb_standard_desc_t *ret_desc =
      (const usb_standard_desc_t *) (((uintptr_t) cur_desc) + cur_desc->bLength);
  *offset = next_offset;
  return ret_desc;
}
