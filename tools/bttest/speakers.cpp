// Several speakers remembered and one playing, through the SHIPPED
// use_speaker() / forget_speaker_slot() / describe_speaker() -- what a Settings
// screen with a Use and a Forget on each speaker row calls.
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

static const uint8_t CAR[6] = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
static const uint8_t HEADSET[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const uint8_t EARBUDS[6] = {0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC};

int main() {
  std::printf("  several speakers remembered, one playing\n");
  esphome::global_preferences->wipe();
  auto *bt = new PortallBT();
  bt->set_a2dp(true);
  bt->start_profiles_();
  bt->on_a2dp_ready();

  bt->on_a2dp_open(CAR);
  bt->on_a2dp_closed(false, CAR);
  bt->on_a2dp_open(HEADSET);
  ok("a second speaker is remembered beside the first", bt->remembered_sinks_.count == 2);
  ok("and it is the one playing", memcmp(bt->remembered_.sink, HEADSET, 6) == 0);
  ok("slot 1 is the car receiver, not in use", says(bt->describe_speaker(0), "46:E8:1C:8A:88:DD") &&
                                                    says(bt->describe_speaker(0), "not in use"));
  ok("slot 2 is the headset, connected", says(bt->describe_speaker(1), "11:22:33:44:55:66") &&
                                              says(bt->describe_speaker(1), "connected"));
  ok("an empty slot says none", bt->describe_speaker(2) == "none");

  g_calls.clear();
  bt->use_speaker(0);  // back to the car
  ok("using another hangs up the one playing", called("esp_a2d_source_disconnect"));
  ok("and does not ask for the new one while the old is closing", !called("esp_a2d_source_connect"));
  ok("the chosen one is the speaker now, so it is the one paged", memcmp(bt->remembered_.sink, CAR, 6) == 0);
  bt->on_a2dp_closed(false, HEADSET);
  bt->sink_step_();
  ok("and it is asked for once the old one has gone", called_before("esp_a2d_source_disconnect", "esp_a2d_source_connect"));
  bt->on_a2dp_open(CAR);
  ok("both are still remembered", bt->remembered_sinks_.count == 2);

  g_calls.clear();
  bt->use_speaker(0);
  ok("using the one already playing asks nothing", g_calls.empty());
  bt->use_speaker(3);
  ok("nor does a slot with nothing in it", g_calls.empty());

  g_calls.clear();
  bt->forget_speaker_slot(1);  // the headset, not playing
  ok("forgetting one not playing removes its pairing", called("esp_bt_gap_remove_bond_device"));
  ok("and leaves the one playing alone", !called("esp_a2d_source_disconnect") &&
                                             memcmp(bt->remembered_.sink, CAR, 6) == 0);
  ok("and its row is gone", bt->remembered_sinks_.count == 1 && bt->describe_speaker(1) == "none");

  g_calls.clear();
  bt->forget_speaker_slot(0);  // the car, playing
  ok("forgetting the one playing hangs it up first",
     called_before("esp_a2d_source_disconnect", "esp_bt_gap_remove_bond_device"));
  ok("and no speaker is remembered any more", bt->remembered_sinks_.count == 0 && !bt->remembered_.has_sink);

  std::printf("  an older board's single speaker is carried into the list\n");
  esphome::global_preferences->wipe();
  {
    auto *old = new PortallBT();
    old->set_a2dp(true);
    old->start_profiles_();
    old->on_a2dp_ready();
    old->on_a2dp_open(EARBUDS);
  }
  // Take the list away again, as a board flashed from before it existed has
  // only the single speaker's record.
  esphome::global_preferences->store.erase(((uint64_t) esphome::fnv1_hash("portall_bt_sinks") << 32) |
                                           (uint64_t) sizeof(esphome::portall_bt::RememberedSinks));
  auto *next = new PortallBT();
  next->set_a2dp(true);
  next->start_profiles_();
  ok("the speaker it had is slot 1", next->remembered_sinks_.count == 1 &&
                                         memcmp(next->remembered_sinks_.addr[0], EARBUDS, 6) == 0);
  return failures ? 1 : 0;
}
