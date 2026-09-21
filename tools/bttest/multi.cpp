/* Two input devices on one panel, which is what a household asked for.
 *
 * Put plainly: "si je dispose de plus peripherique bluetooth que je voudrais
 * le connecter comment les text_sensor alors qu'il que que deux text_sensor".
 * One slot was never a design, it was the first version -- pairing a remote
 * replaced the gamepad, silently, and the entity could only ever name one.
 *
 * WHAT MAKES THIS MORE THAN A LIST OF ADDRESSES is the decode. Bluedroid's
 * ESP_HIDH_DATA_IND_EVT carries a HANDLE and no address at all, so a report
 * has to be routed to the device that sent it -- and reading one controller's
 * report against another's report descriptor is exactly the confidently-wrong
 * answer this whole path was rewritten to stop giving. The same is true one
 * step down, of the edge state: two gamepads sharing a button word means each
 * one's press reads to the other as a release.
 *
 * So the cases below are in two halves. The bookkeeping -- who is remembered,
 * who is paged for, who the entity names -- and the decode, which is the half
 * that cannot be seen from a configuration and is where the fault would be.
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

using esphome::portall_bt::MAX_INPUTS;
using esphome::portall_bt::PortallBT;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

// What crossed to the page, so a test asks what ARRIVED rather than what the
// component thought about it.
static std::vector<uint16_t> g_sent;

static bool says(const std::string &s, const char *bit) {
  return s.find(bit) != std::string::npos;
}

// HID's Keyboard/Keypad page, which is what every key this component makes is
// on -- the launcher is driven by arrow usages.
static constexpr uint16_t KEY_RIGHT = 0x4F;
static constexpr uint16_t KEY_LEFT = 0x50;
static constexpr uint16_t KEY_DOWN = 0x51;
static constexpr uint16_t KEY_UP = 0x52;
static constexpr uint16_t KEY_A = 0x28;  // Enter, which Button 1 becomes

static const uint8_t PAD_A[6] = {0x00, 0x04, 0x4B, 0x93, 0xA9, 0xB2};
static const uint8_t PAD_B[6] = {0xA4, 0xC1, 0x38, 0x9E, 0x22, 0x07};
static const uint8_t PAD_C[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const uint8_t PAD_D[6] = {0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC};
static const uint8_t PAD_E[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x01};

// The gamepad report PAD_DESC describes: id, four axes centred, the hat, two
// button bytes. Stated by MEANING, so the test says what was pressed and the
// component works out from the descriptor where that landed.
static void pad_report(uint8_t hat, uint16_t buttons, uint8_t *out) {
  std::memset(out, 0, 8);
  out[0] = 0x01;
  out[1] = out[2] = out[3] = out[4] = 0x80;
  out[5] = (uint8_t) (hat & 0x0F);
  out[6] = (uint8_t) (buttons & 0xFF);
  out[7] = (uint8_t) (buttons >> 8);
}

static PortallBT *fresh() {
  esphome::global_preferences->wipe();
  auto *bt = new PortallBT();
  g_sent.clear();
  g_calls.clear();
  g_hid_connects.clear();
  bt->set_hid_host(true);
  bt->set_key_sink([](uint16_t page, uint16_t usage) {
    if (page == 0x07)
      g_sent.push_back(usage);
  });
  bt->profiles_up_ = true;
  return bt;
}

int main() {
  std::printf("two devices, each decoded against its OWN descriptor\n");
  {
    PortallBT *bt = fresh();
    // A gamepad on handle 3 and a keyboard on handle 7, in that order,
    // exactly as Bluedroid reports them.
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 3);
    bt->on_hid_descriptor(KBD_DESC, sizeof(KBD_DESC), 0x0000, 0x0000, 7);
    bt->drain_reports_();

    ok("both slots are in use", bt->inputs_[0].used && bt->inputs_[1].used);
    ok("and each parsed a map of its own",
       bt->inputs_[0].map != nullptr && bt->inputs_[1].map != nullptr &&
           bt->inputs_[0].map != bt->inputs_[1].map);
    // The gamepad's descriptor declares report ids and the keyboard's does
    // not, which is the cheapest proof that neither read the other's.
    // Asked null-safely on purpose: with one shared map -- which is what this
    // was before -- the second slot has none at all, and a test that segfaults
    // against the old code reports nothing about which case it was.
    ok("the gamepad's map says its reports carry ids",
       bt->inputs_[0].map != nullptr && bt->inputs_[0].map->uses_ids());
    ok("and the keyboard's says they do not",
       bt->inputs_[1].map != nullptr && !bt->inputs_[1].map->uses_ids());

    // THE CASE THE WHOLE CHANGE IS FOR. A single map would by now hold the
    // keyboard's, because it arrived second -- so the gamepad's report would
    // decode to nothing and reach the page as silence.
    uint8_t up[8];
    pad_report(0x0, 0x0000, up);
    g_sent.clear();
    bt->on_hid_report(up, sizeof(up), 3);
    bt->drain_reports_();
    ok("the gamepad still moves up after a keyboard connected beside it",
       g_sent.size() == 1 && g_sent[0] == KEY_UP);

    // And the keyboard still works on its own handle: usage 0x51 is Down.
    const uint8_t kbd_down[8] = {0, 0, KEY_DOWN, 0, 0, 0, 0, 0};
    g_sent.clear();
    bt->on_hid_report(kbd_down, sizeof(kbd_down), 7);
    bt->drain_reports_();
    ok("and the keyboard beside it still moves down",
       g_sent.size() == 1 && g_sent[0] == KEY_DOWN);
    delete bt;
  }

  std::printf("\nand neither one's buttons are the other's\n");
  {
    // TWO GAMEPADS, same descriptor, which is the harder half: with one
    // shared button word the second one's press looks like a repeat of the
    // first's and is swallowed.
    PortallBT *bt = fresh();
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 3);
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 7);
    bt->drain_reports_();

    uint8_t press[8];
    pad_report(0x0F, 0x0001, press);  // hat centred, Button 1 down
    g_sent.clear();
    bt->on_hid_report(press, sizeof(press), 3);
    bt->drain_reports_();
    ok("the first controller's A arrives", g_sent.size() == 1 && g_sent[0] == KEY_A);

    g_sent.clear();
    bt->on_hid_report(press, sizeof(press), 7);
    bt->drain_reports_();
    ok("and the second controller's A arrives too, rather than reading as a repeat",
       g_sent.size() == 1 && g_sent[0] == KEY_A);

    // The first one holding it down still repeats nothing, which is the rule
    // the per-device state exists to keep rather than to break.
    g_sent.clear();
    bt->on_hid_report(press, sizeof(press), 3);
    bt->drain_reports_();
    ok("and a finger left on the first one repeats nothing", g_sent.empty());
    delete bt;
  }

  std::printf("\nand the hat of one is not the hat of the other\n");
  {
    PortallBT *bt = fresh();
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 3);
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 7);
    bt->drain_reports_();

    uint8_t east[8];
    pad_report(0x2, 0x0000, east);  // east -> right
    g_sent.clear();
    bt->on_hid_report(east, sizeof(east), 3);
    bt->on_hid_report(east, sizeof(east), 7);
    bt->drain_reports_();
    ok("both controllers pushed right, and both were heard",
       g_sent.size() == 2 && g_sent[0] == KEY_RIGHT && g_sent[1] == KEY_RIGHT);
    delete bt;
  }

  std::printf("\nthe entity names every one of them\n");
  {
    PortallBT *bt = fresh();
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    bt->note_remote_name(PAD_A, "NVIDIA Controller v01.04");
    bt->note_remote_name(PAD_B, "Orange TV remote");
    const std::string line = bt->describe_role(false);
    ok("the first is there by name", says(line, "NVIDIA Controller v01.04"));
    ok("and so is the second", says(line, "Orange TV remote"));
    ok("with both addresses beside them",
       says(line, "00:04:4B:93:A9:B2") && says(line, "A4:C1:38:9E:22:07"));
    ok("and both read as connected", line.find("connected") != line.rfind("connected"));

    // One away and one here must not read alike.
    bt->on_hid_closed(7);
    const std::string mixed = bt->describe_role(false);
    ok("a device that hung up reads as away", says(mixed, "paired, away"));
    ok("and the one still here does not", says(mixed, "connected"));
    delete bt;
  }

  std::printf("\nclosing one link leaves the other alone\n");
  {
    PortallBT *bt = fresh();
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    ok("both are open to begin with", bt->inputs_[0].open && bt->inputs_[1].open);

    // A PAGE THAT NEVER OPENED reports itself through the same callback, with
    // nothing to say about which link it was. It must touch neither.
    bt->on_hid_closed(-1);
    ok("a failed page hangs up nobody", bt->inputs_[0].open && bt->inputs_[1].open);

    bt->on_hid_closed(3);
    ok("closing one handle closes that one", !bt->inputs_[0].open);
    ok("and leaves the other connected", bt->inputs_[1].open);
    ok("and the panel still has an input device", bt->any_input_open_());
    delete bt;
  }

  std::printf("\neach remembered device gets its own turn to be paged\n");
  {
    PortallBT *bt = fresh();
    bt->on_hid_open(PAD_A, 3);
    bt->on_hid_open(PAD_B, 7);
    bt->on_hid_closed(3);
    bt->on_hid_closed(7);
    g_hid_connects.clear();

    // ONE PER TICK, taking turns. A page's own timeout is 5.12 s, so four
    // sent together are four overlapping pages -- the shape that once had a
    // panel paging without pause while somebody tried to run an inquiry.
    bt->hid_reconnect_();
    bt->hid_reconnect_();
    ok("two ticks page two devices", g_hid_connects.size() == 2);
    ok("and they are different devices",
       g_hid_connects.size() == 2 && g_hid_connects[0] != g_hid_connects[1]);
    bt->hid_reconnect_();
    ok("and the third tick comes back round to the first",
       g_hid_connects.size() == 3 && g_hid_connects[2] == g_hid_connects[0]);

    // A device that IS connected is not paged at all.
    bt->on_hid_open(PAD_A, 3);
    g_hid_connects.clear();
    bt->hid_reconnect_();
    ok("a device that is already here is not asked for",
       g_hid_connects.size() == 1 && g_hid_connects[0] == "A4:C1:38:9E:22:07");
    delete bt;
  }

  std::printf("\nthe slots run out out loud rather than replacing somebody's device\n");
  {
    PortallBT *bt = fresh();
    const uint8_t *all[5] = {PAD_A, PAD_B, PAD_C, PAD_D, PAD_E};
    for (uint8_t i = 0; i < 5; i++)
      bt->on_hid_open(all[i], (uint8_t) (i + 1));
    ok("four are remembered and no more",
       bt->remembered_inputs_.count == MAX_INPUTS);
    ok("and the first one paired is still one of them",
       bt->remembered_input_(PAD_A));
    ok("and the fifth is not remembered",
       !bt->remembered_input_(PAD_E));

    // AND THE FIFTH'S BUTTONS ARE NOT DECODED AGAINST SOMEBODY ELSE'S MAP.
    // It has no slot, so it has no descriptor, no hat and no button word of
    // its own -- and reading its report against the nearest device's is the
    // confidently-wrong answer this whole path exists to stop giving. The
    // bytes still reach on_hid_report; they become no key.
    bt->on_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214, 1);
    bt->drain_reports_();
    uint8_t east[8];
    pad_report(0x2, 0x0000, east);
    g_sent.clear();
    bt->on_hid_report(east, sizeof(east), 1);
    bt->drain_reports_();
    ok("the device that got a slot is decoded", g_sent.size() == 1 && g_sent[0] == KEY_RIGHT);
    // A DIFFERENT direction from the phantom handle, deliberately: the same
    // one would be swallowed as "no change" by whichever slot it was
    // misrouted into, and a check that cannot fail on the broken code is not
    // a check.
    uint8_t west[8];
    pad_report(0x6, 0x0000, west);
    g_sent.clear();
    bt->on_hid_report(west, sizeof(west), 5);  // the fifth device's handle
    bt->drain_reports_();
    ok("and the one with no slot reaches the page as nothing rather than as a guess",
       g_sent.empty());

    // Forgetting is the way back, and it clears the list rather than one of
    // them: there is no index a household could name a single device by.
    bt->forget_one(false);
    ok("forgetting the input devices clears all of them",
       bt->remembered_inputs_.count == 0);
    ok("and says nothing is left", bt->describe_role(false) == "none");
    delete bt;
  }

  std::printf("\na panel upgrading from the one-device version keeps its device\n");
  {
    // The OLD record, written by a firmware that had one slot. Nothing has
    // ever written the new one on this panel.
    esphome::global_preferences->wipe();
    {
      PortallBT older;
      older.load_remembered_();
      older.remembered_.has_hid = true;
      memcpy(older.remembered_.hid, PAD_A, 6);
      older.remembered_pref_.save(&older.remembered_);
    }
    PortallBT bt;
    bt.set_hid_host(true);
    bt.load_remembered_();
    ok("the device from the old record is in the list",
       bt.remembered_inputs_.count == 1 && bt.remembered_input_(PAD_A));
    ok("and the live table is paging for it",
       bt.inputs_[0].used && bt.inputs_[0].remembered && !bt.inputs_[0].open);

    // AND THE OLD RECORD IS KEPT AS A MIRROR, so a firmware rolled back to
    // the one-slot build still finds a device rather than an empty list.
    bt.profiles_up_ = true;
    bt.on_hid_open(PAD_B, 4);
    ok("a second device joins it", bt.remembered_inputs_.count == 2);
    ok("and the old single slot still names the first",
       bt.remembered_.has_hid && memcmp(bt.remembered_.hid, PAD_A, 6) == 0);
  }

  std::printf("\nand a remembered list survives a restart\n");
  {
    esphome::global_preferences->wipe();
    {
      PortallBT first;
      first.set_hid_host(true);
      first.profiles_up_ = true;
      first.load_remembered_();
      first.on_hid_open(PAD_A, 1);
      first.on_hid_open(PAD_B, 2);
    }
    PortallBT again;
    again.set_hid_host(true);
    again.load_remembered_();
    ok("both come back", again.remembered_inputs_.count == 2 &&
                             again.remembered_input_(PAD_A) &&
                             again.remembered_input_(PAD_B));
    ok("and both read as away rather than connected",
       !again.any_input_open_() &&
           says(again.describe_role(false), "paired, away"));
  }

  if (failures) {
    std::printf("\n%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("\nok\n");
  return 0;
}
