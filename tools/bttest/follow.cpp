// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* The sound follows whichever remembered speaker is switched on.
 *
 * A panel remembers several speakers -- a car receiver, a headset, a speaker
 * in the kitchen -- and plays through one at a time. The reconnection used to
 * page only the one last playing, so with the car gone the headset sitting
 * switched on beside the panel was never asked, and the sound went nowhere
 * until somebody pressed Use on its row.
 *
 * Now, while no speaker is connected, the same clock takes the remembered
 * speakers in turn, starting with the one last playing; whichever answers
 * becomes the panel's speaker. One page per tick, so it costs no more radio
 * time than paging one speaker did.
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
#include <set>
#include <string>
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

static uint8_t CAR[6] = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
static uint8_t HEADSET[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const char *CAR_TEXT = "46:E8:1C:8A:88:DD";
static const char *HEADSET_TEXT = "11:22:33:44:55:66";

/* Remember the headset and then the car -- the car last, so it is the one
   playing -- and then the car is driven away. */
static PortallBT *car_then_gone(bool follow) {
  esphome::global_preferences->wipe();
  esphome::g_now_ms = 0;
  g_calls.clear();
  g_a2dp_connects.clear();
  auto *bt = new PortallBT();
  bt->set_a2dp(true);
  bt->set_speaker_follows(follow);
  bt->start_profiles_();
  bt->on_a2dp_ready();
  bt->on_a2dp_open(HEADSET);
  bt->on_a2dp_closed(false, HEADSET);
  bt->on_a2dp_open(CAR);
  bt->on_a2dp_closed(true, CAR);
  g_a2dp_connects.clear();
  return bt;
}

/* Run the clock, refusing every page except one to `answers`, which takes the
   call the way a switched-on speaker does. */
static void run(PortallBT *bt, uint32_t seconds, const char *answers) {
  size_t seen = 0;
  // The end is fixed before the clock moves: computed from g_now_ms inside
  // the condition, it would recede with every step.
  const uint32_t start = esphome::g_now_ms;
  const uint32_t end = start + seconds * 1000;
  for (uint32_t ms = start; ms <= end; ms += 100) {
    esphome::g_now_ms = ms;
    bt->loop();
    while (seen < g_a2dp_connects.size()) {
      const std::string who = g_a2dp_connects[seen++];
      if (answers != nullptr && who == answers) {
        bt->on_a2dp_open(who == CAR_TEXT ? CAR : HEADSET);
        return;
      }
      bt->on_a2dp_closed(false);
    }
  }
}

int main() {
  std::printf("the car is gone and the headset is switched on\n");
  {
    PortallBT *bt = car_then_gone(true);
    run(bt, 120, nullptr);
    std::set<std::string> asked(g_a2dp_connects.begin(), g_a2dp_connects.end());
    ok("both remembered speakers are asked for", asked.count(CAR_TEXT) && asked.count(HEADSET_TEXT));
    ok("the one last playing first", !g_a2dp_connects.empty() && g_a2dp_connects[0] == CAR_TEXT);
    ok("one page at a time on the same clock: no more pages than one speaker had",
       g_a2dp_connects.size() < 15);
    delete bt;
  }
  {
    PortallBT *bt = car_then_gone(true);
    run(bt, 120, HEADSET_TEXT);
    ok("the headset answers and is connected", bt->a2dp_open_ && memcmp(bt->open_sink_, HEADSET, 6) == 0);
    ok("and becomes the panel's speaker", memcmp(bt->remembered_.sink, HEADSET, 6) == 0);
    ok("the car is still remembered", bt->remembered_sinks_.count == 2);
    delete bt;
  }

  std::printf("\nwith speaker_follows: false, only the one chosen\n");
  {
    PortallBT *bt = car_then_gone(false);
    run(bt, 120, nullptr);
    std::set<std::string> asked(g_a2dp_connects.begin(), g_a2dp_connects.end());
    ok("only the car is ever asked for", asked.size() == 1 && asked.count(CAR_TEXT));
    delete bt;
  }

  std::printf("\none speaker remembered: nothing changes\n");
  {
    esphome::global_preferences->wipe();
    esphome::g_now_ms = 0;
    g_a2dp_connects.clear();
    auto *bt = new PortallBT();
    bt->set_a2dp(true);
    bt->start_profiles_();
    bt->on_a2dp_ready();
    bt->on_a2dp_open(CAR);
    bt->on_a2dp_closed(true, CAR);
    g_a2dp_connects.clear();
    run(bt, 60, nullptr);
    std::set<std::string> asked(g_a2dp_connects.begin(), g_a2dp_connects.end());
    ok("the car alone is asked for", asked.size() == 1 && asked.count(CAR_TEXT));
    delete bt;
  }

  return failures == 0 ? 0 : 1;
}
