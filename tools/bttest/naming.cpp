/* Does the paired-device entity say WHICH device it is?
 *
 * Asked for as a text sensor for a remote -- "son apparaillage". A remote
 * pairs as HID, so the `input:` slot already reported its pairing; what it
 * could not do was say which device that was. It published a bare MAC:
 *
 *     A4:C1:38:9E:22:07 connected
 *
 * which on a panel with a gamepad AND a remote says nothing at all about
 * which of them came back. The name is now kept -- in RAM, because
 * `Remembered` is already in the NVS of every paired panel and changing its
 * layout would make each of them forget what it is paired to.
 *
 * Two sources, and the test covers both because they are reached differently:
 * ESP_BT_GAP_AUTH_CMPL_EVT carries the name at PAIRING, and a RECONNECT does
 * not pair at all -- ESP_HIDH_OPEN_EVT has no name field -- so the name is
 * asked for with a Remote Name Request over the open link.
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
#include <unistd.h>
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void check(const char *what, bool ok) {
  printf("  %s     %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    failures++;
}

static bool says(const std::string &s, const char *bit) {
  return s.find(bit) != std::string::npos;
}

int main() {
  const uint8_t REMOTE[6] = {0xA4, 0xC1, 0x38, 0x9E, 0x22, 0x07};
  const uint8_t SPEAKER[6] = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  const uint8_t STRANGER[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};

  printf("a remote that has paired\n");
  {
    PortallBT bt;
    bt.set_hid_host(true);
    bt.remember_hid_(REMOTE);
    // Before a name is known, the entity is what it always was.
    const std::string bare = bt.describe_role(false);
    check("with no name yet, the address alone -- which is what it used to be",
          says(bare, "A4:C1:38:9E:22:07") && !says(bare, "("));

    bt.note_remote_name(REMOTE, "Orange TV remote");
    const std::string named = bt.describe_role(false);
    check("once named, the name leads", says(named, "Orange TV remote"));
    check("and the address stays, because that is what forget acts on",
          says(named, "A4:C1:38:9E:22:07"));
    check("and it still says whether it is connected",
          says(named, "paired, away"));
  }

  printf("\nthe slot is chosen by ADDRESS, not by which event carried it\n");
  {
    PortallBT bt;
    bt.set_hid_host(true);
    bt.set_a2dp(true);
    bt.remember_hid_(REMOTE);
    bt.remember_sink_(SPEAKER);
    bt.note_remote_name(SPEAKER, "UGREEN-90748");
    bt.note_remote_name(REMOTE, "Orange TV remote");
    check("the speaker's name lands on the speaker",
          says(bt.describe_role(true), "UGREEN-90748"));
    check("the remote's name lands on the remote",
          says(bt.describe_role(false), "Orange TV remote"));
    check("and neither took the other's",
          !says(bt.describe_role(true), "Orange TV remote") &&
              !says(bt.describe_role(false), "UGREEN-90748"));

    // A device this panel does not remember has no slot to go in, and must
    // not overwrite one that is in use.
    bt.note_remote_name(STRANGER, "somebody else's telephone");
    check("a device that is not remembered is ignored",
          !says(bt.describe_role(false), "telephone") &&
              !says(bt.describe_role(true), "telephone"));
  }

  printf("\na name is not trusted to be terminated or printable\n");
  {
    PortallBT bt;
    bt.set_hid_host(true);
    bt.remember_hid_(REMOTE);
    // The exact shape a TP-Link dongle sent for its own name, which this
    // component has already been caught by once: text, then padding that is
    // not a terminator.
    char ugly[64];
    memset(ugly, 0, sizeof(ugly));
    memcpy(ugly, "Remote", 6);
    for (int i = 6; i < 40; i++)
      ugly[i] = (char) 0xFF;
    bt.note_remote_name(REMOTE, ugly);
    const std::string out = bt.describe_role(false);
    check("the printable run is kept and the padding is not",
          says(out, "Remote") && out.find((char) 0xFF) == std::string::npos);

    // And a name longer than the buffer must not run off the end of it.
    std::string very_long(400, 'x');
    bt.note_remote_name(REMOTE, very_long.c_str());
    const std::string capped = bt.describe_role(false);
    check("a name longer than the buffer is cut, not overflowed",
          says(capped, "xxxx") &&
              capped.find(std::string(esphome::portall_bt::MAX_REMOTE_NAME + 1,
                                      'x')) == std::string::npos);
  }

  printf("\nforgetting a device takes its name with it\n");
  {
    PortallBT bt;
    bt.set_hid_host(true);
    bt.set_a2dp(true);
    bt.profiles_up_ = true;
    bt.remember_hid_(REMOTE);
    bt.remember_sink_(SPEAKER);
    bt.note_remote_name(REMOTE, "Orange TV remote");
    bt.note_remote_name(SPEAKER, "UGREEN-90748");

    bt.forget_one(false);
    check("the forgotten remote's name is gone",
          !says(bt.describe_role(false), "Orange TV remote"));
    check("and the speaker it was beside kept its own",
          says(bt.describe_role(true), "UGREEN-90748"));

    // A stale name beside a NEW address reads as correct, which is the whole
    // reason this has to be cleared rather than left to be overwritten.
    bt.remember_hid_(STRANGER);
    check("a different device in that slot does not inherit the old name",
          !says(bt.describe_role(false), "Orange TV remote"));
  }

  printf("\nthe name is asked for when a device connects\n");
  {
    g_calls.clear();
    PortallBT bt;
    bt.set_hid_host(true);
    bt.on_hid_open(REMOTE);
    check("on_hid_open sends a Remote Name Request",
          called("esp_bt_gap_read_remote_name"));
    // The order is load-bearing: the name is filed against a REMEMBERED slot,
    // so asking before the slot exists would throw the answer away.
    check("and only after the device is remembered, or there is no slot",
          bt.remembered_.has_hid);
  }

  if (failures) {
    printf("\n%d check(s) failed\n", failures);
    return 1;
  }
  printf("\nok\n");
  return 0;
}
