// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* A controller's or a remote's battery, as the device itself reports it.
 *
 * Read from the device's own report descriptor -- Battery Strength on the
 * Generic Device Controls page, or the Battery System page's state of charge
 * -- and scaled by the range the device declares, so 0..100 and 0..255 are
 * the same battery. It goes into the device's own entity line, so a
 * controller left on the sofa says it is nearly flat before it stops
 * answering, and it is read whether or not the buttons go anywhere.
 */
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
#include <cstring>
#include <string>

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

static bool says(const std::string &s, const char *bit) { return s.find(bit) != std::string::npos; }

/* A remote that reports its battery: report id 4, one byte of Battery
   Strength. `max` is its declared logical maximum. */
static std::string with_battery(uint8_t max, uint8_t value, bool keys) {
  const uint8_t desc[] = {
      0x05, 0x06,  // Usage Page (Generic Device Controls)
      0x09, 0x20,  // Usage (Battery Strength)
      0xA1, 0x01,  // Collection (Application)
      0x85, 0x04,  //   Report ID (4)
      0x15, 0x00,  //   Logical Minimum (0)
      0x26, max, 0x00,  // Logical Maximum
      0x75, 0x08,  //   Report Size (8)
      0x95, 0x01,  //   Report Count (1)
      0x09, 0x20,  //   Usage (Battery Strength)
      0x81, 0x02,  //   Input (Data,Var,Abs)
      0xC0,
  };
  static const uint8_t REMOTE[6] = {0xA4, 0xC1, 0x38, 0x9E, 0x22, 0x07};
  esphome::global_preferences->wipe();
  PortallBT bt;
  bt.set_hid_host(true);
  bt.profiles_up_ = true;
  if (keys)
    bt.set_key_sink([](uint16_t, uint16_t) {});
  bt.on_hid_open(REMOTE, 5);
  bt.on_hid_descriptor(desc, sizeof(desc), 0x1234, 0x5678, 5);
  bt.drain_reports_();
  const uint8_t report[2] = {0x04, value};
  bt.on_hid_report(report, sizeof(report), 5);
  bt.drain_reports_();
  return bt.describe_input(0);
}

int main() {
  std::printf("a device's battery, from its own descriptor\n");
  std::string line = with_battery(100, 80, true);
  ok(("a remote at 80 of 100 reads 80% (\"" + line + "\")").c_str(), says(line, "connected, battery 80%"));
  line = with_battery(255, 128, true);
  ok(("the same device declared 0..255 at 128 reads 50% (\"" + line + "\")").c_str(),
     says(line, "battery 50%"));
  line = with_battery(100, 78, true);
  ok("rounded to five, so a flickering level is not news", says(line, "battery 80%"));
  line = with_battery(100, 80, false);
  ok("read even with no keys: set", says(line, "battery 80%"));
  line = with_battery(100, 200, true);
  ok("a value outside the declared range is not a level", !says(line, "battery"));
  return failures == 0 ? 0 : 1;
}
