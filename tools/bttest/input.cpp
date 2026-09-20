// Does a remote's button become the right key, and does a media button stay
// out of it?
//
// The second half is the one worth a test. The first version of this feature
// mapped ESP_AVRC_PT_CMD_FORWARD -- next track -- onto "move down a tile", in
// a lambda a household had to write itself. It was pushed back on as "le
// comportement du bluetooth doit gerer toutes les peripherique qu'il dispose
// du bluetooth", and reading esp_avrc_api.h afterwards showed the objection
// was righter than it knew: AVRCP has SELECT, UP, DOWN, LEFT, RIGHT and EXIT
// of its own, so that mapping was an invention standing in front of an answer
// the standard already gives.
//
// So this asserts both directions. The navigation commands must arrive as the
// usages the launcher is driven by, AND the transport commands must produce
// NOTHING -- a check that cannot fail against the old behaviour is not a
// check, and against the old table every one of those would have fired.
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_avrc_api.h"
#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
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

// What the sink was handed, so a test asks what CROSSED rather than what the
// component thought about it.
static std::vector<uint16_t> g_sent;

static PortallBT *fresh() {
  auto *bt = new PortallBT();
  g_sent.clear();
  bt->set_key_sink([](uint16_t page, uint16_t usage) {
    // The page is checked here rather than collected: every key this component
    // produces is on HID's Keyboard/Keypad page, and one that was not would be
    // a fault the usage alone could not show.
    if (page != 0x07)
      failures++;
    g_sent.push_back(usage);
  });
  return bt;
}

static bool only(uint16_t usage) {
  return g_sent.size() == 1 && g_sent[0] == usage;
}

// A boot-protocol keyboard report: modifiers, the RESERVED byte the
// specification requires to be zero, then six keycodes.
static void keyboard(PortallBT *bt, uint8_t k1, uint8_t k2 = 0) {
  const uint8_t report[8] = {0, 0, k1, k2, 0, 0, 0, 0};
  bt->feed_hid_keys(report, sizeof(report));
}

int main() {
  std::printf("AVRCP: a real remote's own commands\n");
  struct { uint8_t code; uint16_t usage; const char *name; } nav[] = {
      {ESP_AVRC_PT_CMD_UP, 0x52, "UP -> up"},
      {ESP_AVRC_PT_CMD_DOWN, 0x51, "DOWN -> down"},
      {ESP_AVRC_PT_CMD_LEFT, 0x50, "LEFT -> left"},
      {ESP_AVRC_PT_CMD_RIGHT, 0x4F, "RIGHT -> right"},
      {ESP_AVRC_PT_CMD_SELECT, 0x28, "SELECT -> ok"},
      {ESP_AVRC_PT_CMD_ENTER, 0x28, "ENTER -> ok"},
      {ESP_AVRC_PT_CMD_EXIT, 0x29, "EXIT -> back"},
      {ESP_AVRC_PT_CMD_PAGE_UP, 0x4B, "PAGE_UP -> page up"},
      {ESP_AVRC_PT_CMD_PAGE_DOWN, 0x4E, "PAGE_DOWN -> page down"},
  };
  for (const auto &one : nav) {
    PortallBT *bt = fresh();
    bt->feed_avrc_key(one.code);
    ok(one.name, only(one.usage));
    delete bt;
  }

  std::printf("and the transport buttons mean what they say\n");
  const uint8_t transport[] = {
      ESP_AVRC_PT_CMD_PLAY,    ESP_AVRC_PT_CMD_PAUSE,
      ESP_AVRC_PT_CMD_STOP,    ESP_AVRC_PT_CMD_FORWARD,
      ESP_AVRC_PT_CMD_BACKWARD, ESP_AVRC_PT_CMD_VOL_UP,
  };
  {
    PortallBT *bt = fresh();
    for (uint8_t code : transport)
      bt->feed_avrc_key(code);
    // THE fix: the old table turned FORWARD into "down". If any of these
    // reaches the page the invention is back.
    ok("play, pause, stop, next, previous and volume move nothing",
       g_sent.empty());
    delete bt;
  }

  std::printf("HID: a keyboard or a television remote\n");
  {
    PortallBT *bt = fresh();
    keyboard(bt, 0x51);
    ok("a boot keyboard report's arrow crosses", only(0x51));
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    keyboard(bt, 0x51);
    keyboard(bt, 0x51);
    keyboard(bt, 0x51);
    // A key still held is in EVERY report a keyboard sends. Without this a
    // finger resting on Down walks the whole list in a second.
    ok("a key held down is sent once, not once per report", only(0x51));
    keyboard(bt, 0);        // released
    keyboard(bt, 0x51);     // and pressed again
    ok("and pressing it again after the release sends it again",
       g_sent.size() == 2 && g_sent[1] == 0x51);
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    keyboard(bt, 0x51, 0x4F);
    ok("two keys down in one report both cross", g_sent.size() == 2);
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    const uint8_t id_first[9] = {0x01, 0, 0, 0x52, 0, 0, 0, 0, 0};
    bt->feed_hid_keys(id_first, sizeof(id_first));
    ok("a nine-byte report with a report id in front is read too", only(0x52));
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    // A gamepad's report is not this shape, and this component does not
    // pretend to read one: the RESERVED byte being non-zero is the whole of
    // the test, and it is why `keys:` has to be asked for.
    const uint8_t gamepad[8] = {0x80, 0x7F, 0x80, 0x7F, 0x08, 0x00, 0x00, 0x00};
    bt->feed_hid_keys(gamepad, sizeof(gamepad));
    ok("a report that is not a boot keyboard report is left alone",
       g_sent.empty());
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    const uint8_t rollover[8] = {0, 0, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01};
    bt->feed_hid_keys(rollover, sizeof(rollover));
    ok("ErrorRollOver is not a key", g_sent.empty());
    delete bt;
  }
  {
    // With no sink -- which is every panel that never set `keys:` -- nothing
    // is decoded and nothing is reached for.
    auto *bt = new PortallBT();
    g_sent.clear();
    bt->feed_avrc_key(ESP_AVRC_PT_CMD_DOWN);
    keyboard(bt, 0x51);
    ok("with no `keys:` set, nothing crosses and nothing crashes",
       g_sent.empty());
    delete bt;
  }

  if (failures != 0)
    std::printf("\n%d failure(s)\n", failures);
  return failures == 0 ? 0 : 1;
}
