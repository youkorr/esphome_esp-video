// Does the panel say what the dongle in its socket actually is?
//
// A household asked for "the Bluetooth 5 TP-Link, much better than the
// Bluetooth 4 one" because an iPhone could not see the panel. The version was
// not the reason -- but the log could not have settled that either way, since
// the one line about the controller read `HCI version 6, LMP version 6,
// manufacturer 15` and left every word of it to the reader.
//
// This includes the component's own source so it exercises the SHIPPED tables
// rather than a copy: a lookup table restated in its own test proves nothing
// at all, and these three were transcribed from somebody else's source.
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
#include <cstring>

#include "linkstubs.h"

using esphome::portall_bt::bluetooth_release;
using esphome::portall_bt::maker_name;
using esphome::portall_bt::realtek_rom_part;

static int failures = 0;

static void check(const char *what, bool ok) {
  std::printf("  %s %s\n", ok ? "ok  " : "FAIL", what);
  if (!ok)
    failures++;
}

static bool same(const char *got, const char *want) {
  return got != nullptr && want != nullptr && std::strcmp(got, want) == 0;
}

int main() {
  std::printf("what the dongle is\n");

  // The two dongles this project has actually run, by the numbers each of
  // them really reports. The Broadcom's are from a panel's own log -- the very
  // first one in this repository's history -- so this half is a measurement
  // rather than a reading of a table.
  check("the Broadcom in the first log is named", same(maker_name(15), "Broadcom"));
  check("and LMP 6 is Bluetooth 4.0", same(bluetooth_release(6), "4.0"));

  // The TP-Link UB500's part, whose ROM values come from Linux's own
  // ic_id_table. Its hci_ver is 10, so it is Bluetooth 5.1 -- not the 5.0 the
  // box says, and this is the line that would say so.
  check("the TP-Link's part is recognised",
        same(realtek_rom_part(0x8761, 0x000B, 10), "RTL8761BU"));
  check("and LMP 10 is Bluetooth 5.1", same(bluetooth_release(10), "5.1"));
  check("and 93 is Realtek", same(maker_name(93), "Realtek"));

  // The whole point of matching that table is the statement it makes: these
  // are ROM values, so a hit means no firmware has been loaded. A controller
  // carrying a patch reports the firmware's own version and must NOT match --
  // otherwise the panel would warn about a patch that is already there.
  check("a patched controller matches nothing",
        realtek_rom_part(0x8761, 0x0F23, 10) == nullptr);
  check("and so does a near miss on the revision",
        realtek_rom_part(0x8761, 0x000A, 10) == nullptr);

  // Every other Realtek USB part in that table, spot-checked at both ends, so
  // a transcription that dropped or shifted a row is caught.
  check("the oldest row survived the transcription",
        same(realtek_rom_part(0x1200, 0x000B, 6), "RTL8723AU"));
  check("and the newest one did too",
        same(realtek_rom_part(0x8852, 0x0087, 12), "RTL8852BTU"));

  // A release this component has never heard of must say so rather than read
  // out of the end of its own array -- the failure that would reach a panel
  // as a corrupted log line on the first Bluetooth 7 dongle.
  check("a release past the end of the list is nullptr, not rubbish",
        bluetooth_release(16) == nullptr && bluetooth_release(200) == nullptr);
  check("and 6.0 is there, because Bluetooth 6 was the next thing asked for",
        same(bluetooth_release(14), "6.0"));

  // A maker nobody listed is a number, not a wrong name.
  check("an unlisted maker is nullptr", maker_name(4242) == nullptr);

  if (failures)
    std::printf("\n%d problem(s)\n", failures);
  return failures ? 1 : 0;
}
