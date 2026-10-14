// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* The transport buttons reach whatever is playing on the page.
 *
 * A car's steering wheel and a headset's button speak AVRCP; a television
 * remote, a keyboard's media row and a Shield speak HID's Consumer page. All
 * of them used to stop at the board: AVRCP's play and pause went to the YAML
 * and nowhere else, and a Consumer button declared as an ARRAY -- which is
 * how a remote's media keys are nearly always declared -- was thrown away
 * with every other array, Home and Back included.
 *
 * Now they cross as Consumer usages, one vocabulary from every device, and
 * never on the keyboard page, so no transport button can move a tile.
 */
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_avrc_api.h"
#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "descfixtures.h"
#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

static std::vector<std::pair<uint16_t, uint16_t>> g_sent;
static int g_home = 0;

static PortallBT *fresh() {
  auto *bt = new PortallBT();
  g_sent.clear();
  g_home = 0;
  bt->set_home_sink([]() { g_home++; });
  bt->set_key_sink([](uint16_t page, uint16_t usage) { g_sent.push_back({page, usage}); });
  return bt;
}

static bool only(uint16_t page, uint16_t usage) {
  return g_sent.size() == 1 && g_sent[0].first == page && g_sent[0].second == usage;
}

static void press(PortallBT *bt, uint16_t a, uint16_t b = 0) {
  const uint8_t report[5] = {0x03, (uint8_t) (a & 0xFF), (uint8_t) (a >> 8), (uint8_t) (b & 0xFF),
                             (uint8_t) (b >> 8)};
  bt->feed_hid_keys(report, sizeof(report));
}

static PortallBT *remote() {
  PortallBT *bt = fresh();
  bt->feed_hid_descriptor(REMOTE_DESC, sizeof(REMOTE_DESC), 0x1234, 0x5678);
  g_sent.clear();
  return bt;
}

int main() {
  std::printf("a car's and a headset's transport buttons (AVRCP)\n");
  struct {
    uint8_t code;
    uint16_t usage;
    const char *what;
  } transport[] = {
      {ESP_AVRC_PT_CMD_PLAY, 0xB0, "play arrives as Consumer Play"},
      {ESP_AVRC_PT_CMD_PAUSE, 0xB1, "pause as Consumer Pause"},
      {ESP_AVRC_PT_CMD_STOP, 0xB7, "stop as Consumer Stop"},
      {ESP_AVRC_PT_CMD_FORWARD, 0xB5, "next track as Scan Next Track"},
      {ESP_AVRC_PT_CMD_BACKWARD, 0xB6, "previous track as Scan Previous Track"},
      {ESP_AVRC_PT_CMD_FAST_FORWARD, 0xB3, "fast forward as Fast Forward"},
      {ESP_AVRC_PT_CMD_REWIND, 0xB4, "rewind as Rewind"},
  };
  for (const auto &one : transport) {
    PortallBT *bt = fresh();
    bt->feed_avrc_key(one.code);
    ok(one.what, only(0x0C, one.usage));
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    bt->feed_avrc_key(ESP_AVRC_PT_CMD_VOL_UP);
    ok("volume is the speaker's own and goes nowhere", g_sent.empty());
    delete bt;
  }

  std::printf("\na remote whose media keys are an ARRAY\n");
  {
    PortallBT *bt = remote();
    press(bt, 0xCD);
    ok("Play/Pause arrives", only(0x0C, 0xCD));
    press(bt, 0xCD);
    ok("and held, it is not pressed again", g_sent.size() == 1);
    press(bt, 0);
    press(bt, 0xCD);
    ok("let go and pressed again, it is", g_sent.size() == 2);
    delete bt;
  }
  {
    PortallBT *bt = remote();
    press(bt, 0x223);
    ok("AC Home leaves the link", g_home == 1 && g_sent.empty());
    press(bt, 0);
    press(bt, 0x224);
    ok("AC Back is Escape", only(0x07, 0x29));
    delete bt;
  }
  {
    PortallBT *bt = remote();
    press(bt, 0x42);
    ok("a television remote's Menu Up is the up arrow", only(0x07, 0x52));
    press(bt, 0);
    press(bt, 0x41);
    ok("and Menu Pick is OK", g_sent.size() == 2 && g_sent[1] == std::make_pair<uint16_t, uint16_t>(0x07, 0x28));
    delete bt;
  }
  {
    PortallBT *bt = remote();
    press(bt, 0xCD);
    press(bt, 0xCD, 0xB5);
    ok("a second key while the first is held: only the new one",
       g_sent.size() == 2 && g_sent[1] == std::make_pair<uint16_t, uint16_t>(0x0C, 0xB5));
    delete bt;
  }
  {
    PortallBT *bt = remote();
    press(bt, 0xE9);  // Volume Increment: no meaning on a page
    ok("a Consumer key with no meaning here sends nothing", g_sent.empty());
    delete bt;
  }

  std::printf("\na gamepad's report between two of the remote's\n");
  {
    // The Shield shape: a gamepad report and a Consumer array report on one
    // device. A report that does not CARRY the array says nothing about it.
    std::vector<uint8_t> both(PAD_DESC, PAD_DESC + sizeof(PAD_DESC));
    both.insert(both.end(), REMOTE_DESC, REMOTE_DESC + sizeof(REMOTE_DESC));
    PortallBT *bt = fresh();
    bt->feed_hid_descriptor(both.data(), (uint16_t) both.size(), 0x0955, 0x7214);
    g_sent.clear();
    press(bt, 0xCD);
    const uint8_t pad[8] = {0x01, 0x80, 0x80, 0x80, 0x80, 0x0F, 0x00, 0x00};
    bt->feed_hid_keys(pad, sizeof(pad));
    press(bt, 0xCD);
    ok("Play/Pause held across a gamepad report is pressed once", g_sent.size() == 1);
    delete bt;
  }

  return failures == 0 ? 0 : 1;
}
