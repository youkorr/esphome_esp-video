// One input device forgotten by its slot, beside the others, through the
// SHIPPED forget_input() / describe_input() -- what a Settings screen with a
// Forget on each row calls. The rows must name the slots the way the action
// counts them, or the Forget beside a row hangs up a different device.
#define private public
#define protected public
#define main component_main_unused
#include "usb_bluetooth.cpp"
#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main
#include <cstdio>
#include <string>
#include "linkstubs.h"

using esphome::usb_bluetooth::UsbBluetooth;

static int failures = 0;
static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}
static bool says(const std::string &s, const char *bit) { return s.find(bit) != std::string::npos; }

static const uint8_t PAD[6] = {0x00, 0x04, 0x4B, 0x93, 0xA9, 0xB2};
static const uint8_t REMOTE[6] = {0xA4, 0xC1, 0x38, 0x9E, 0x22, 0x07};

int main() {
  std::printf("  forget one input device by its slot\n");
  auto *bt = new UsbBluetooth();
  bt->set_hid_host(true);
  bt->profiles_up_ = true;
  bt->on_hid_open(PAD, 3);
  bt->on_hid_open(REMOTE, 7);

  ok("slot 1 is the gamepad", says(bt->describe_input(0), "00:04:4B:93:A9:B2"));
  ok("slot 2 is the remote", says(bt->describe_input(1), "A4:C1:38:9E:22:07"));
  ok("an empty slot says none", bt->describe_input(2) == "none");
  ok("a slot past the end says none", bt->describe_input(9) == "none");

  g_calls.clear();
  bt->forget_input(1);  // the remote, slot 2 on the screen
  ok("forgetting slot 2 hangs up and unbonds one device",
     called("esp_bt_hid_host_disconnect") && called("esp_bt_gap_remove_bond_device"));
  ok("and hangs up before it unbonds",
     called_before("esp_bt_hid_host_disconnect", "esp_bt_gap_remove_bond_device"));
  ok("slot 2 is empty afterwards", bt->describe_input(1) == "none");
  ok("the gamepad in slot 1 is untouched", says(bt->describe_input(0), "00:04:4B:93:A9:B2") &&
                                                  bt->inputs_[0].open);
  ok("the list names the gamepad alone", says(bt->describe_role(false), "00:04:4B") &&
                                              !says(bt->describe_role(false), "A4:C1"));

  g_calls.clear();
  bt->forget_input(1);
  ok("forgetting an empty slot does nothing", g_calls.empty());
  bt->forget_input(7);
  ok("nor does a slot that does not exist", g_calls.empty());

  // The action counts from 1, as the screen does.
  esphome::usb_bluetooth::ForgetInputAction<> one;
  one.set_parent(bt);
  one.set_slot(1);
  one.play();
  ok("the action's slot 1 is describe_input(0)", bt->describe_input(0) == "none");

  bt->on_hid_open(PAD, 3);
  bt->on_hid_open(REMOTE, 7);
  esphome::usb_bluetooth::ForgetInputAction<> all;
  all.set_parent(bt);
  all.play();
  ok("with no slot, the action still forgets every input device",
     bt->describe_input(0) == "none" && bt->describe_input(1) == "none");
  return failures ? 1 : 0;
}
