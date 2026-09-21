// Does pressing Pair actually look for anything, does it say so when it
// cannot, and does it leave alone what this panel already has?
//
// Reported first as "je n'arrive plus a me connecter au bluetooth meme si je
// vais reset Forget Bluetooth devices". The log ended at the three encouraging
// lines pair() prints and then said NOTHING -- not even "scan finished", which
// is the line the stack's own event produces. Two faults there, and this
// exercises both: a refused scan that went quiet, and a bond removed under a
// live ACL by forget().
//
// Reported again a release later, from the other side: "pour faire un
// appareillage c'est contraignant je suis obliger d'appuis sur forget meme si
// il y a 0 paire". Pressing Pair hung up the speaker that was playing and the
// gamepad somebody was holding, then walked off and re-paired that same
// speaker -- because the scan took the first device of a wanted kind, and the
// devices quickest to answer are the ones already in the room and already
// paired. So a NEW device never got a turn, and the only way through was to
// Forget everything first. Pair means ADD now, and the cases below fail
// against the version that did not.
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
#include <functional>
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
  // From an empty flash: the preferences stand-in really stores now, so a
  // block that did not wipe would inherit whatever the one before it paired.
  esphome::global_preferences->wipe();
  esp_bd_addr_t speaker = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  bt.set_a2dp(true);
  bt.start_profiles_();
  bt.on_a2dp_ready();
  bt.on_a2dp_open(speaker);
  g_calls.clear();
}

/// The same capture for anything that prints, not only a no-argument method.
static std::string say_run(const std::function<void()> &what) {
  char path[] = "/tmp/portall_bt_pairXXXXXX";
  const int hole = mkstemp(path);
  const int saved = dup(fileno(stdout));
  fflush(stdout);
  dup2(hole, fileno(stdout));
  what();
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

// The Class of Device each kind really reports. The speaker's is the one a
// panel's own log printed for a UGREEN car receiver; the gamepad's is a plain
// PERIPHERAL, which is what a remote and a keyboard say too.
static constexpr uint32_t COD_SPEAKER = 0x240404;
static constexpr uint32_t COD_INPUT = 0x000508;

static const uint8_t SPEAKER[6] = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
static const uint8_t OTHER_SPEAKER[6] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
static const uint8_t SHIELD[6] = {0x00, 0x04, 0x4B, 0x93, 0xA9, 0xB2};
static const uint8_t REMOTE[6] = {0xA4, 0xC1, 0x38, 0x9E, 0x22, 0x07};

int main() {
  printf("the reported case: Pair with a speaker already here\n");
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    const std::string said = say(&PortallBT::pair, bt);

    // IT USED TO HANG THIS UP, and that is what stopped the music every time
    // somebody wanted to add a remote. There is nothing to gain by it: the
    // device is skipped below whether or not it is connected.
    check("pairing leaves the connected speaker alone",
          !called("esp_a2d_source_disconnect"));
    check("and says nothing about hanging anything up",
          said.find("hanging up") == std::string::npos);
    check("the scan is actually asked for", called("esp_bt_gap_start_discovery"));

    // And the scan hears that same speaker, which is what it did on the panel.
    g_calls.clear();
    const std::string heard =
        say_run([&] { bt.heard_device(SPEAKER, COD_SPEAKER, "UGREEN-90748"); });
    check("a speaker this panel already has is not paired again",
          !called("esp_a2d_source_connect"));
    check("and the scan keeps running for the device somebody meant",
          !called("esp_bt_gap_cancel_discovery"));
    check("and it says which button replaces it",
          heard.find("already has") != std::string::npos &&
              heard.find("Forget Bluetooth speaker") != std::string::npos);

    // The device somebody actually pressed the button for.
    g_calls.clear();
    say_run([&] { bt.heard_device(OTHER_SPEAKER, COD_SPEAKER, "a new one"); });
    check("a speaker it does NOT have is taken",
          called("esp_a2d_source_connect") && called("esp_bt_gap_cancel_discovery"));
  }

  printf("\nand the same for an input device\n");
  {
    PortallBT bt;
    esphome::global_preferences->wipe();
    bt.set_hid_host(true);
    bt.set_a2dp(true);
    bt.start_profiles_();
    bt.on_hid_open(SHIELD, 3);
    g_calls.clear();
    g_hid_connects.clear();

    const std::string said = say(&PortallBT::pair, bt);
    check("pairing leaves the connected controller alone",
          !called("esp_bt_hid_host_disconnect"));

    g_calls.clear();
    g_hid_connects.clear();
    const std::string heard =
        say_run([&] { bt.heard_device(SHIELD, COD_INPUT, "NVIDIA Controller v01.04"); });
    check("a controller this panel already has is not paired again",
          g_hid_connects.empty() && !called("esp_bt_gap_cancel_discovery"));
    check("and it names the controllers' own Forget button",
          heard.find("Forget Bluetooth controllers") != std::string::npos);

    // Cleared again, so this case asserts what it says rather than leaning on
    // whether the step before it connected to anything.
    g_calls.clear();
    g_hid_connects.clear();
    say_run([&] { bt.heard_device(REMOTE, COD_INPUT, "Orange TV remote"); });
    check("and a remote it does not have is taken",
          g_hid_connects.size() == 1 && g_hid_connects[0] == "A4:C1:38:9E:22:07" &&
              called("esp_bt_gap_cancel_discovery"));
    (void) said;
  }

  printf("\nwith nothing remembered, the first device is still taken\n");
  {
    PortallBT bt;
    esphome::global_preferences->wipe();
    bt.set_a2dp(true);
    bt.set_hid_host(true);
    bt.start_profiles_();
    g_calls.clear();
    say_run([&] { bt.heard_device(SPEAKER, COD_SPEAKER, "UGREEN-90748"); });
    check("a first pairing is untouched by any of this",
          called("esp_a2d_source_connect") && called("esp_bt_gap_cancel_discovery"));
  }

  printf("\nand the end of the scan says which of the two it was\n");
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    say(&PortallBT::pair, bt);
    say_run([&] { bt.heard_device(SPEAKER, COD_SPEAKER, "UGREEN-90748"); });
    bt.say_pairing_later();
    esphome::g_now_ms += 4000;
    const std::string report = say_run([&] { bt.pair_report_tick_(); });

    // "none of them connected" reads as a fault and sends somebody to look at
    // the class of device. What happened is that the panel already had it.
    check("it says nothing was changed, rather than that nothing connected",
          report.find("already has") != std::string::npos);
    check("and it does not blame the device's kind",
          report.find("none of them connected") == std::string::npos);
    /* THE FALSE SUCCESS this change could have introduced. The speaker that
       has been playing all along is connected, and it is no longer hung up
       for the scan -- so a report that asks "is anything connected?" would
       call every failed pairing a success. It asks about the device this run
       reached for instead. */
    check("and it does not call the speaker that was already here a success",
          report.find("is connected") == std::string::npos);
  }

  printf("\nForget acts on the LINK, not only on the record\n");
  {
    /* THE REPORTED CASE: "il ya des problemes sur Forget pour deconnecter le
       device ou speaker". A speaker connected with nothing in the record --
       which a blanket forget leaves behind for as long as the disconnection
       takes to land -- used to be answered "no speaker is remembered" while
       it went on playing. */
    PortallBT bt;
    with_a_speaker_connected(bt);
    bt.remembered_.has_sink = false;   // the record gone, the link still up
    memset(bt.remembered_.sink, 0, 6);
    g_calls.clear();
    const std::string said = say_run([&] { bt.forget_one(true); });

    check("a connected speaker with no record is still hung up",
          called("esp_a2d_source_disconnect"));
    check("and it is not answered with nothing to forget",
          said.find("nothing to forget") == std::string::npos);
    check("and the panel stops believing it has one", !bt.speaker_connected());
  }
  {
    // And the ordinary case must not have moved: hang up, then remove the key.
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_calls.clear();
    say_run([&] { bt.forget_one(true); });
    check("forgetting a remembered speaker still hangs up before the key goes",
          called_before("esp_a2d_source_disconnect", "esp_bt_gap_remove_bond_device"));
    check("and the live state goes with the record",
          !bt.speaker_connected() && !bt.remembered_.has_sink);

    // A second press must not claim there is something there.
    g_calls.clear();
    const std::string again = say_run([&] { bt.forget_one(true); });
    check("and a second press says there is nothing left",
          again.find("nothing to forget") != std::string::npos &&
              !called("esp_bt_gap_remove_bond_device"));
  }
  {
    /* THE DISCONNECT IS NOT GATED ON OUR OWN FLAG. `a2dp_open_` is this
       component's opinion of the link; the stack's state is the fact, and a
       bond removed under a live ACL is the fault this whole area was written
       for. A spurious disconnect costs an error code nobody reads. */
    PortallBT bt;
    with_a_speaker_connected(bt);
    bt.a2dp_open_ = false;  // the flag lies; the link is up
    g_calls.clear();
    say_run([&] { bt.forget_one(true); });
    check("a remembered speaker is hung up even when the flag says otherwise",
          called("esp_a2d_source_disconnect"));
  }
  {
    // An input device connected with every slot full holds no record at all,
    // and used to be reachable by no button in this component.
    PortallBT bt;
    esphome::global_preferences->wipe();
    bt.set_hid_host(true);
    bt.start_profiles_();
    bt.on_hid_open(SHIELD, 3);
    const int8_t slot = bt.slot_for_addr_(SHIELD);
    bt.inputs_[slot].remembered = false;  // connected, never remembered
    g_calls.clear();
    const std::string said = say_run([&] { bt.forget_one(false); });
    check("a connected controller with no record is hung up too",
          called("esp_bt_hid_host_disconnect"));
    check("and no key is removed for one there never was a key for",
          !called("esp_bt_gap_remove_bond_device"));
    check("and it is not answered with nothing to forget",
          said.find("nothing to forget") == std::string::npos);
  }

  printf("\nand replacing a speaker is the one pairing that hangs one up\n");
  {
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_calls.clear();
    // A DIFFERENT speaker: this panel drives one, so the new one takes the
    // old one's place rather than joining it.
    say_run([&] { bt.heard_device(OTHER_SPEAKER, COD_SPEAKER, "a new one"); });
    check("the old speaker is hung up before the new one is asked for",
          called_before("esp_a2d_source_disconnect", "esp_a2d_source_connect"));
  }
  {
    PortallBT bt;
    esphome::global_preferences->wipe();
    bt.set_hid_host(true);
    bt.start_profiles_();
    bt.on_hid_open(SHIELD, 3);
    g_calls.clear();
    g_hid_connects.clear();
    // A SECOND controller is added beside the first, not in its place.
    say_run([&] { bt.heard_device(REMOTE, COD_INPUT, "Orange TV remote"); });
    check("but a second controller does not hang up the first",
          !called("esp_bt_hid_host_disconnect") && g_hid_connects.size() == 1);
  }

  printf("\nthe faults the first report was about, which must still hold\n");
  {
    // The stack refusing, which is what the panel's silence looked like.
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_discovery_result = ESP_FAIL;
    const std::string said = say(&PortallBT::pair, bt);
    g_discovery_result = ESP_OK;
    check("a refused scan says so instead of going quiet",
          said.find("did not start") != std::string::npos);
  }
  {
    // Forget, which the first report says did not help either. It is the one
    // that HAS to hang up: removing a bond under a live ACL leaves a
    // connection whose key the stack has just deleted.
    PortallBT bt;
    with_a_speaker_connected(bt);
    g_bonded = 1;  // the stack has a key, which is what forget() is there for
    say(&PortallBT::forget, bt);
    g_bonded = 0;

    check("forgetting hangs up BEFORE it deletes the key",
          called_before("esp_a2d_source_disconnect", "esp_bt_gap_remove_bond_device"));
  }

  if (failures) {
    printf("\n%d check(s) failed\n", failures);
    return 1;
  }
  printf("\nok\n");
  return 0;
}
