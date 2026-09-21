// Does the panel back off when a paired device does not answer?
//
// A household's log, with the speaker switched off, asking every three or four
// seconds for ever:
//
//   04:37:46  asking the speaker to connect (no scan, by address)
//   04:37:50  asking the speaker to connect (no scan, by address)
//   04:37:53  ...  :57  ...  04:38:01  :05  :08  :12
//   BT_HCI: hcif conn complete: hdl 0x1, st 0x4      <- 0x4 is Page Timeout
//   BT_BTC: BTA_AV_OPEN_EVT::FAILED status: 2
//
// The design says 2 s growing to 60. What defeated it is that Bluedroid
// reports a connection that never opened as a DISCONNECTED state, so every
// failed page ran on_a2dp_closed(), which put the clock back to its shortest.
// A page's own timeout is 5.12 s, so attempts at 3.5 s overlap -- the
// controller pages without pause, next to the Wi-Fi antenna, and the inquiry
// that household was running heard nothing at all.
//
// This drives the SHIPPED loop() against a clock it can move, and counts what
// the panel really does over two minutes rather than reading a field.
// The same two lines every other test here uses: reconnect_tick_() and the
// clock it reads are private, and a test that could only reach public entry
// points would have to drive this through loop(), which it does anyway.
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

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;

static int failures = 0;

static void check(const char *what, bool ok) {
  std::printf("  %s     %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    failures++;
}

static size_t counted(const char *what) {
  size_t n = 0;
  for (const std::string &call : g_calls)
    if (call == what)
      n++;
  return n;
}

/* Run the panel forward, with the remembered speaker refusing every page the
 * way a switched-off one does: the stack answers a failed attempt with the
 * same DISCONNECTED event it uses for a real disconnection. Returns when each
 * attempt happened, in seconds. */
static std::vector<uint32_t> attempts_over(PortallBT &bt, uint32_t seconds) {
  std::vector<uint32_t> when;
  size_t seen = 0;
  for (uint32_t ms = 0; ms <= seconds * 1000; ms += 100) {
    esphome::g_now_ms = ms;
    bt.loop();
    const size_t now = counted("esp_a2d_source_connect");
    while (seen < now) {
      when.push_back(ms / 1000);
      seen++;
      // What the controller answers a page that nobody picks up. `false` is
      // the ordinary close rather than a lost signal, which is what
      // BTA_AV_OPEN_EVT::FAILED becomes.
      bt.on_a2dp_closed(false);
    }
  }
  return when;
}

static void paired_and_then_gone(PortallBT &bt) {
  esp_bd_addr_t speaker = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  bt.set_a2dp(true);
  bt.start_profiles_();
  bt.on_a2dp_ready();
  bt.on_a2dp_open(speaker);
  // And now it is switched off. This one IS a real disconnection, so the short
  // interval is right here and the test does not argue with it.
  bt.on_a2dp_closed(true);
}

int main() {
  std::printf("a speaker that does not answer\n");

  esphome::g_now_ms = 0;
  g_calls.clear();
  PortallBT bt;
  paired_and_then_gone(bt);
  const std::vector<uint32_t> when = attempts_over(bt, 120);

  std::printf("    asked at:");
  for (uint32_t s : when)
    std::printf(" %us", s);
  std::printf("  (%zu times in two minutes)\n", when.size());

  /* The reported behaviour, stated as the test's own premise so a reader can
   * check it: every three or four seconds is roughly 34 attempts in 120 s, and
   * a backoff of 2/4/8/16/32/60/60 is nine. Anything near the first number is
   * the fault back. */
  check("it does not page over and over for two minutes", when.size() < 15);
  check("and it does get asked for, rather than being given up on",
        when.size() >= 5);

  // The gaps must GROW. That is the whole of what a backoff is, and the fault
  // is precisely that they did not.
  bool grew = false;
  if (when.size() >= 4) {
    const uint32_t early = when[1] - when[0];
    const uint32_t late = when[when.size() - 1] - when[when.size() - 2];
    grew = late > early * 3;
    std::printf("    first gap %us, last gap %us\n", early, late);
  }
  check("the gap between attempts grows", grew);

  // And the far end is the minute the design says, not something unbounded.
  bool sane = true;
  for (size_t i = 1; i < when.size(); i++)
    if (when[i] - when[i - 1] > 70)
      sane = false;
  check("but never grows past about a minute", sane);

  /* A real disconnection is the case the short interval was written for, and
   * it has to keep working: a speaker switched off and on again within a
   * moment must not wait out whatever the backoff had reached. */
  esphome::g_now_ms = 0;
  g_calls.clear();
  PortallBT again;
  esp_bd_addr_t speaker = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};
  again.set_a2dp(true);
  again.start_profiles_();
  again.on_a2dp_ready();
  again.on_a2dp_open(speaker);
  esphome::g_now_ms = 50000;
  again.on_a2dp_closed(true);
  size_t asked = 0;
  for (uint32_t ms = 50000; ms <= 56000; ms += 100) {
    esphome::g_now_ms = ms;
    again.loop();
    if (counted("esp_a2d_source_connect") > asked) {
      asked = counted("esp_a2d_source_connect");
      std::printf("    a real disconnection is followed up after %u ms\n", ms - 50000);
      break;
    }
  }
  check("a speaker that really went away is asked for within a few seconds",
        asked > 0);

  if (failures)
    std::printf("\n%d probleme(s)\n", failures);
  return failures ? 1 : 0;
}
