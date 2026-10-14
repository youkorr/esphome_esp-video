// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* Three faults found reading the component back, each one quiet:
 *
 *  - a report descriptor longer than the 512 bytes staged between Bluedroid's
 *    task and the loop was cut there and then reported as though the device
 *    had sent exactly 512;
 *  - the speaker playing kept its name in one place only, so its row in the
 *    list of remembered speakers went nameless the moment another was chosen;
 *  - a dongle plugged in after Bluedroid was up was probed, answered, and then
 *    never used, with nothing in the log to say so.
 *
 * Each is checked by what the component SAID or what it SHOWS, captured
 * rather than restated.
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

static bool says(const std::string &s, const char *bit) { return s.find(bit) != std::string::npos; }

static FILE *g_old_stdout = nullptr;
static char g_capture_path[] = "/tmp/portall_bt_capture.XXXXXX";

static void capture_begin() {
  std::fflush(stdout);
  // mkstemp rewrites its template, so it is put back every time.
  std::strcpy(g_capture_path, "/tmp/portall_bt_capture.XXXXXX");
  const int fd = mkstemp(g_capture_path);
  g_old_stdout = stdout;
  stdout = fdopen(fd, "w+");
}

static std::string capture_end() {
  std::fflush(stdout);
  FILE *mine = stdout;
  stdout = g_old_stdout;
  std::rewind(mine);
  std::string out;
  char line[512];
  while (std::fgets(line, sizeof(line), mine) != nullptr)
    out += line;
  std::fclose(mine);
  std::remove(g_capture_path);
  return out;
}

static const uint8_t PAD[6] = {0x00, 0x04, 0x4B, 0x93, 0xA9, 0xB2};
static const uint8_t CAR[6] = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
static const uint8_t HEADSET[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};

int main() {
  std::printf("a report descriptor longer than the staging buffer\n");
  {
    esphome::global_preferences->wipe();
    auto *bt = new PortallBT();
    bt->set_hid_host(true);
    bt->profiles_up_ = true;
    bt->on_hid_open(PAD, 3);

    // A real gamepad's descriptor, then Usage Page items that change nothing,
    // to 700 bytes -- longer than MAX_DESC, which is what the case is about.
    std::vector<uint8_t> desc(PAD_DESC, PAD_DESC + sizeof(PAD_DESC));
    while (desc.size() + 2 <= 700) {
      desc.push_back(0x05);
      desc.push_back(0x01);
    }
    const unsigned declared = (unsigned) desc.size();
    bt->on_hid_descriptor(desc.data(), (uint16_t) desc.size(), 0x0955, 0x7214, 3);
    capture_begin();
    bt->drain_reports_();
    const std::string said = capture_end();

    char size[16];
    std::snprintf(size, sizeof(size), "%u bytes", declared);
    ok("the log names the size the device really sent", says(said, size));
    ok("and how much of it was kept", says(said, "only the first 512"));
    ok("and never claims the device described itself in 512 bytes",
       !says(said, "described itself: 512 bytes"));
    ok("what fitted is still read: the gamepad has a map",
       bt->inputs_[0].map != nullptr && bt->inputs_[0].map->field_count() > 0);
    delete bt;
  }

  std::printf("\nthe speaker playing keeps its name in its own row\n");
  {
    esphome::global_preferences->wipe();
    auto *bt = new PortallBT();
    bt->set_a2dp(true);
    bt->start_profiles_();
    bt->on_a2dp_ready();
    bt->on_a2dp_open(CAR);
    bt->on_a2dp_closed(false, CAR);
    bt->on_a2dp_open(HEADSET);
    // The name arrives while the headset is the one playing -- the ordinary
    // case, since a pairing is what names a device and pairing a speaker is
    // what makes it the one playing.
    bt->note_remote_name(HEADSET, "Casque salon");
    ok("the headset's row is named while it plays", says(bt->describe_speaker(1), "Casque salon"));

    bt->use_speaker(0);  // back to the car
    bt->on_a2dp_closed(false, HEADSET);
    bt->sink_step_();
    bt->on_a2dp_open(CAR);
    ok("and still named once the car is the one playing",
       says(bt->describe_speaker(1), "Casque salon"));
    ok("its row says it is not in use", says(bt->describe_speaker(1), "not in use"));
    delete bt;
  }

  std::printf("\na dongle plugged in after Bluedroid is already up\n");
  {
    esphome::global_preferences->wipe();
    auto *bt = new PortallBT();
    bt->set_host_stack(true);
    bt->stack_up_ = true;
    capture_begin();
    bt->start_host_stack_(nullptr, 0, nullptr, nullptr, nullptr);
    const std::string said = capture_end();
    ok("says it will not be used, rather than nothing at all", says(said, "restart the panel"));
    delete bt;
  }

  return failures == 0 ? 0 : 1;
}
