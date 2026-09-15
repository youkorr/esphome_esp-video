// Does pressing Pair actually look for anything, and does it say so when it
// cannot?
//
// Reported from a panel as "je n'arrive plus a me connecter au bluetooth meme
// si je vais reset Forget Bluetooth devices". The log ended at the three
// encouraging lines pair() prints and then said NOTHING -- not even "scan
// finished", which is the line the stack's own event produces. Two faults, and
// this exercises both:
//
//   * an inquiry cannot find a device that is already connected to this panel,
//     and this component reconnects by address for ever, so the second pairing
//     scans for a speaker that is busy talking to us;
//   * nothing checked what the stack answered, so a refused scan and a scan
//     that heard nothing were the same silence.
/* The state every pairing after the first one starts from is only reachable
 * through the USB probe, which needs a dongle. So the test reaches in: it
 * includes the component's own source, and this one line opens the class to
 * it. Crude, confined to the test, and the alternative is a test-only method
 * on the shipped class -- which is a worse trade, because it would be a door
 * nobody uses in the firmware everybody flashes. */
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

/// Everything pair() and forget() print, so a test can assert on the words a
/// household reads rather than on a boolean nobody sees.
static std::string say(void (PortallBT::*what)(), PortallBT &bt) {
  char path[] = "/tmp/portall_bt_pairXXXXXX";
  const int hole = mkstemp(path);
  const int saved = dup(fileno(stdout));
  fflush(stdout);
  dup2(hole, fileno(stdout));
  (bt.*what)();
  fflush(stdout);
  dup2(saved, fileno(stdout));
  close(saved);
  close(hole);

  std::string said;
  FILE *back = fopen(path, "rb");
  char buf[512];
  size_t n;
  while (back != nullptr && (n = fread(buf, 1, sizeof(buf), back)) > 0)
    said.append(buf, n);
  if (back != nullptr)
    fclose(back);
  remove(path);
  return said;
}

/// A panel with a speaker paired and connected, which is the state every
/// pairing after the first one starts from.
static void with_a_speaker_connected(PortallBT &bt) {
  esp_bd_addr_t speaker = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  bt.set_a2dp(true);
  bt.start_profiles_();
  bt.on_a2dp_ready();
  bt.on_a2dp_open(speaker);
  g_calls.clear();
}

int main() {
  // THE REPORTED CASE. Pair, with the speaker already connected to us.
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    const std::string said = say(&PortallBT::pair, bt);

    check("pairing hangs up the connected speaker before it scans",
          called_before("esp_a2d_source_disconnect", "esp_bt_gap_start_discovery"));
    check("and says why, because an unexplained disconnection is alarming",
          said.find("answers no inquiry") != std::string::npos);
    check("the scan is actually asked for", called("esp_bt_gap_start_discovery"));
  }

  // The stack refusing, which is what the panel's silence looked like.
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_discovery_result = ESP_FAIL;
    const std::string said = say(&PortallBT::pair, bt);
    g_discovery_result = ESP_OK;

    check("a refused scan says so instead of going quiet",
          said.find("did not start") != std::string::npos);
  }

  // Forget, which the report says did not help either.
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_bonded = 1;  // the stack has a key, which is what forget() is there for
    say(&PortallBT::forget, bt);
    g_bonded = 0;

    check("forgetting hangs up BEFORE it deletes the key",
          called_before("esp_a2d_source_disconnect", "esp_bt_gap_remove_bond_device"));
  }

  // And a panel with nothing connected must not pretend to hang anything up.
  {
    PortallBT bt;
    bt.set_a2dp(true);
    bt.start_profiles_();
    bt.on_a2dp_ready();
    g_calls.clear();
    const std::string said = say(&PortallBT::pair, bt);

    check("with nothing connected, nothing is hung up",
          !called("esp_a2d_source_disconnect") && called("esp_bt_gap_start_discovery"));
    check("and no disconnection is announced",
          said.find("hanging up") == std::string::npos);
  }

  return failures == 0 ? 0 : 1;
}
