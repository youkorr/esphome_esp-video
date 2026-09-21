// Does a panel stop streaming at a speaker it is sending nothing to?
//
// The stream used to start when a sink connected and never end, and fill_pcm
// answers every request with silence when nothing feeds it -- so a panel
// transmitted 44.1 kHz stereo SBC of digital nothing, for ever. A household's
// log is what showed the cost:
//
//     E BT_L2CAP: l2cab is_cong_cback_context     (eight times a second)
//
// which Bluedroid's own comment describes as its recursion guard firing while
// a CONGESTED channel drains -- on a dongle also carrying a gamepad.
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
#include <cstring>

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void check(const char *what, bool ok) {
  std::printf("  %s     %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    failures++;
}

static bool asked(int what) {
  for (int one : g_media_ctrl)
    if (one == what)
      return true;
  return false;
}

/// A panel with a speaker connected and the stream running, which is where
/// every one of these cases starts.
static void streaming(PortallBT &bt) {
  esp_bd_addr_t speaker = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  bt.set_a2dp(true);
  bt.start_profiles_();
  bt.on_a2dp_ready();
  bt.on_a2dp_open(speaker);
  bt.on_a2dp_audio(true);
  g_media_ctrl.clear();
}

int main() {
  std::printf("a speaker being sent nothing\n");

  // Ten seconds of quiet: the stream goes down.
  esphome::g_now_ms = 0;
  PortallBT bt;
  streaming(bt);
  for (uint32_t ms = 0; ms <= 12000; ms += 500) {
    esphome::g_now_ms = ms;
    bt.loop();
  }
  check("a stream nobody feeds is suspended", asked(ESP_A2D_MEDIA_CTRL_SUSPEND));
  // ONCE. a2dp_playing_ only moves when the stack answers, so a loop keyed on
  // it alone would ask on every turn until then -- which on a stack that has
  // stopped answering is a command a second, for ever.
  size_t suspends = 0;
  for (int one : g_media_ctrl)
    if (one == ESP_A2D_MEDIA_CTRL_SUSPEND)
      suspends++;
  if (suspends != 1)
    std::printf("    asked %zu times\n", suspends);
  check("and asked once, not on every turn of the loop", suspends == 1);

  // And it is not suspended before the quiet is really quiet: a gap between
  // two announcements must not cost an AVDTP round trip.
  esphome::g_now_ms = 0;
  g_media_ctrl.clear();
  PortallBT busy;
  streaming(busy);
  const uint8_t frame[4] = {1, 2, 3, 4};
  for (uint32_t ms = 0; ms <= 12000; ms += 500) {
    esphome::g_now_ms = ms;
    busy.feed_audio(frame, sizeof(frame));
    busy.loop();
  }
  check("and one that is fed is left alone", !asked(ESP_A2D_MEDIA_CTRL_SUSPEND));

  // Sound arriving after a suspend brings the stream back without anybody
  // reconnecting anything.
  esphome::g_now_ms = 0;
  g_media_ctrl.clear();
  PortallBT again;
  streaming(again);
  for (uint32_t ms = 0; ms <= 12000; ms += 500) {
    esphome::g_now_ms = ms;
    again.loop();
  }
  again.on_a2dp_audio(false);  // what the stack reports back
  g_media_ctrl.clear();
  esphome::g_now_ms = 13000;
  again.feed_audio(frame, sizeof(frame));
  again.loop();
  check("and sound afterwards starts it again",
        asked(ESP_A2D_MEDIA_CTRL_CHECK_SRC_RDY));

  // A test tone is somebody asking for a sound that never stops.
  esphome::g_now_ms = 0;
  g_media_ctrl.clear();
  PortallBT tone;
  streaming(tone);
  tone.set_test_tone(440);
  for (uint32_t ms = 0; ms <= 12000; ms += 500) {
    esphome::g_now_ms = ms;
    tone.loop();
  }
  check("but a test tone is never suspended",
        !asked(ESP_A2D_MEDIA_CTRL_SUSPEND));

  // With no speaker at all nothing is asked of the stack, which is what stops
  // this firing at a panel that has never paired.
  esphome::g_now_ms = 0;
  g_media_ctrl.clear();
  PortallBT alone;
  alone.set_a2dp(true);
  alone.start_profiles_();
  alone.on_a2dp_ready();
  for (uint32_t ms = 0; ms <= 12000; ms += 500) {
    esphome::g_now_ms = ms;
    alone.loop();
  }
  check("with nothing connected, no media control is asked for at all",
        g_media_ctrl.empty());

  if (failures)
    std::printf("\n%d probleme(s)\n", failures);
  return failures ? 1 : 0;
}
