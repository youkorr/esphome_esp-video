// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* The ledger a dongle that goes away is answered from, byte by byte.
 *
 * Every event it makes is something Bluedroid will parse as though a
 * controller had sent it, so every expected answer below is written out from
 * the Bluetooth Core Specification (5.4, Vol 4 Part E 7.7) -- the event code,
 * the parameter length, each field in its place -- and never read back out of
 * hci_ledger.cpp. A test that built its expectation from the code would agree
 * with any mistake the code made.
 *
 * tools/idfusbtest/ runs the same ledger through the transport, a fake
 * Bluedroid that counts its credits the way hci_layer.c does, and a dongle
 * pulled out and put back. This is the half that can say WHICH byte is wrong.
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
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::HciLedger;
using Bytes = std::vector<uint8_t>;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

static std::vector<Bytes> g_events;
static void record(const uint8_t *event, uint16_t len, void *) { g_events.emplace_back(event, event + len); }

static void sent(HciLedger &l, uint16_t opcode, Bytes params, uint32_t at = 0) {
  Bytes cmd = {(uint8_t) (opcode & 0xFF), (uint8_t) (opcode >> 8), (uint8_t) params.size()};
  cmd.insert(cmd.end(), params.begin(), params.end());
  l.note_sent(cmd.data(), (uint16_t) cmd.size(), at);
}

static void got(HciLedger &l, Bytes event) { l.note_received(event.data(), (uint16_t) event.size()); }

static Bytes kept(const HciLedger &l, size_t i) {
  uint8_t out[258];
  const uint16_t n = l.kept_command(i, out);
  return Bytes(out, out + n);
}

// Command Complete as 7.7.14 lays it out: code, length, num_hci_command_packets,
// opcode, then the return parameters.
static Bytes complete(uint16_t opcode, Bytes returned) {
  Bytes ev = {0x0E, (uint8_t) (3 + returned.size()), 0x01, (uint8_t) (opcode & 0xFF), (uint8_t) (opcode >> 8)};
  ev.insert(ev.end(), returned.begin(), returned.end());
  return ev;
}

// Command Status, 7.7.15: status, num_hci_command_packets, opcode.
static Bytes status(uint16_t opcode, uint8_t st) {
  return {0x0F, 0x04, st, 0x01, (uint8_t) (opcode & 0xFF), (uint8_t) (opcode >> 8)};
}

static const Bytes SPEAKER = {0xDD, 0x88, 0x8A, 0x1C, 0xE8, 0x46};  // 46:E8:1C:8A:88:DD, little-endian
static const Bytes PHONE = {0x66, 0x55, 0x44, 0x33, 0x22, 0x11};

int main() {
  std::printf("What counts as what, from the specification\n");
  ok("Write Scan Enable is a setting; Create Connection is not",
     HciLedger::is_setting(0x0C1A) && !HciLedger::is_setting(0x0405));
  ok("Create Connection, Inquiry and Disconnect are answered by a Command Status",
     HciLedger::answered_by_status(0x0405) && HciLedger::answered_by_status(0x0401) &&
         HciLedger::answered_by_status(0x0406));
  ok("Inquiry Cancel and Read RSSI by a Command Complete",
     !HciLedger::answered_by_status(0x0402) && !HciLedger::answered_by_status(0x1405));
  ok("Host Number Of Completed Packets by nothing at all", HciLedger::expects_no_answer(0x0C35));

  std::printf("\nWhat a controller that comes back is told\n");
  {
    HciLedger l;
    l.set_recording_startup(true);
    sent(l, 0x0C03, {});                                                   // Reset
    sent(l, 0x0C01, {0xFF, 0xFF, 0xFB, 0xFF, 0x07, 0xF8, 0xBF, 0x3D});   // Set Event Mask
    sent(l, 0x1005, {});                                                   // Read Buffer Size
    sent(l, 0x0C1A, {0x02});                                               // Write Scan Enable: page
    sent(l, 0x0C35, {0x01, 0x0B, 0x00, 0x01, 0x00});                       // Host Num Completed
    sent(l, 0x0401, {0x33, 0x8B, 0x9E, 0x08, 0x00});                       // Inquiry
    ok("at startup everything is kept but the reset, the unanswered and the activities", l.kept() == 3);
    ok("in the order it was given",
       kept(l, 0) == Bytes({0x01, 0x0C, 0x08, 0xFF, 0xFF, 0xFB, 0xFF, 0x07, 0xF8, 0xBF, 0x3D}) &&
           kept(l, 1) == Bytes({0x05, 0x10, 0x00}) && kept(l, 2) == Bytes({0x1A, 0x0C, 0x01, 0x02}));

    l.set_recording_startup(false);
    const uint32_t before = l.changes();
    sent(l, 0x0C1A, {0x03});        // page and inquiry scan: the panel pairing
    sent(l, 0x1405, {0x0B, 0x00});  // Read RSSI: not a setting
    sent(l, 0x0C24, {0x14, 0x04, 0x24});
    ok("afterwards, only the settings", l.kept() == 4);
    ok("a setting given again keeps its place and takes its latest value",
       kept(l, 2) == Bytes({0x1A, 0x0C, 0x01, 0x03}) && kept(l, 3) == Bytes({0x24, 0x0C, 0x03, 0x14, 0x04, 0x24}));
    const uint32_t after = l.changes();
    ok("and every change is counted", after == before + 2);
    sent(l, 0x0C1A, {0x03});
    ok("the same value again changes nothing", l.changes() == after);

    // A long value in the middle: everything after it moves, nothing is lost.
    Bytes name(248, 0);
    memcpy(name.data(), "Salon", 5);
    sent(l, 0x0C01, Bytes(8, 0x11));
    l.set_recording_startup(true);
    sent(l, 0x0C13, name);
    l.set_recording_startup(false);
    Bytes shorter(248, 0);
    memcpy(shorter.data(), "Cuisine", 7);
    sent(l, 0x0C13, shorter);
    ok("a value replaced in place, the rest still whole",
       kept(l, 0) == Bytes({0x01, 0x0C, 0x08, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11}) &&
           kept(l, 2) == Bytes({0x1A, 0x0C, 0x01, 0x03}) && kept(l, 4).size() == 251 && kept(l, 4)[3] == 'C');
  }

  std::printf("\nThe controller goes\n");
  {
    HciLedger l;
    // A link that came up the ordinary way: a page, its status, its completion.
    Bytes create = SPEAKER;
    create.insert(create.end(), {0x18, 0xCC, 0x01, 0x00, 0x00, 0x00, 0x01});
    sent(l, 0x0405, create);
    ok("a page is waiting for its answer", l.waiting() == 1);
    got(l, status(0x0405, 0x00));
    ok("its Command Status leaves it owing a Connection Complete", l.waiting() == 0 && l.pending() == 1);
    Bytes cc = {0x03, 0x0B, 0x00, 0x0B, 0x00};
    cc.insert(cc.end(), SPEAKER.begin(), SPEAKER.end());
    cc.insert(cc.end(), {0x01, 0x00});
    got(l, cc);
    ok("and the Connection Complete is a link", l.links() == 1 && l.pending() == 0);

    // Then: an inquiry running, a name being asked for, a read waiting.
    sent(l, 0x0401, {0x33, 0x8B, 0x9E, 0x08, 0x00});
    got(l, status(0x0401, 0x00));
    Bytes rnr = PHONE;
    rnr.insert(rnr.end(), {0x01, 0x00, 0x00, 0x00});
    sent(l, 0x0419, rnr);
    got(l, status(0x0419, 0x00));
    sent(l, 0x1405, {0x0B, 0x00});
    ok("one command waiting, two operations running, one link",
       l.waiting() == 1 && l.pending() == 2 && l.links() == 1);

    g_events.clear();
    const unsigned n = l.lost(record, nullptr);
    ok("lost() answers all four", n == 4 && g_events.size() == 4);

    // 1. Read RSSI refused, in a Command Complete as long as the longest
    // return, its handle echoed after the status.
    Bytes rssi = complete(0x1405, {0x0C, 0x0B, 0x00});
    rssi.resize(2 + 4 + 248, 0x00);
    rssi[1] = 4 + 248;
    ok("first the read that was waiting: refused, its handle echoed", g_events.size() > 0 && g_events[0] == rssi);
    // 2. The inquiry ends.
    ok("then the inquiry ends", g_events.size() > 1 && g_events[1] == Bytes({0x01, 0x01, 0x00}));
    // 3. The name request ends: status, BD_ADDR, 248 bytes of name.
    Bytes name = {0x07, 0xFF, 0x04};
    name.insert(name.end(), PHONE.begin(), PHONE.end());
    name.resize(2 + 255, 0x00);
    ok("then the name request, with a page timeout", g_events.size() > 2 && g_events[2] == name);
    // 4. The link drops: status 0, handle, reason Connection Timeout.
    ok("and last the link, closed with a connection timeout",
       g_events.size() > 3 && g_events[3] == Bytes({0x05, 0x04, 0x00, 0x0B, 0x00, 0x08}));
    ok("nothing is left to answer", l.waiting() == 0 && l.pending() == 0 && l.links() == 0);
    g_events.clear();
    ok("and a second lost() says nothing", l.lost(record, nullptr) == 0 && g_events.empty());
  }
  {
    HciLedger l;
    Bytes create = SPEAKER;
    create.insert(create.end(), {0x18, 0xCC, 0x01, 0x00, 0x00, 0x00, 0x01});
    sent(l, 0x0405, create);
    got(l, status(0x0405, 0x00));
    sent(l, 0x0C1A, {0x03});
    g_events.clear();
    l.lost(record, nullptr);
    Bytes page = {0x03, 0x0B, 0x04, 0x00, 0x00};
    page.insert(page.end(), SPEAKER.begin(), SPEAKER.end());
    page.insert(page.end(), {0x01, 0x00});
    ok("a setting that was waiting is told yes -- the next controller will be given it",
       g_events.size() == 2 && g_events[0] == Bytes({0x0E, 0x04, 0x01, 0x1A, 0x0C, 0x00}));
    ok("a page that had started ends in a page timeout, for its address", g_events.size() == 2 && g_events[1] == page);
    ok("and the setting is kept", l.kept() == 1 && kept(l, 0) == Bytes({0x1A, 0x0C, 0x01, 0x03}));
  }

  std::printf("\nWhile it is gone\n");
  {
    HciLedger l;
    g_events.clear();
    Bytes create = SPEAKER;
    create.insert(create.end(), {0x18, 0xCC, 0x01, 0x00, 0x00, 0x00, 0x01});
    Bytes cmd = {0x05, 0x04, (uint8_t) create.size()};
    cmd.insert(cmd.end(), create.begin(), create.end());
    l.answer_for(cmd.data(), (uint16_t) cmd.size(), record, nullptr);
    ok("a page is refused with a Command Status", g_events.size() == 1 && g_events[0] == status(0x0405, 0x0C));
    const Bytes scan = {0x1A, 0x0C, 0x01, 0x02};
    l.answer_for(scan.data(), (uint16_t) scan.size(), record, nullptr);
    ok("a setting is told yes", g_events.size() == 2 && g_events[1] == Bytes({0x0E, 0x04, 0x01, 0x1A, 0x0C, 0x00}));
    ok("and kept, for the controller that comes back", l.kept() == 1 && kept(l, 0) == scan);
    const Bytes acks = {0x35, 0x0C, 0x05, 0x01, 0x0B, 0x00, 0x01, 0x00};
    l.answer_for(acks.data(), (uint16_t) acks.size(), record, nullptr);
    ok("and the one command no controller answers is not answered here either", g_events.size() == 2);
    ok("nothing is left waiting", l.waiting() == 0);
  }

  std::printf("\nA controller that is there and does not answer\n");
  {
    HciLedger l;
    sent(l, 0x1405, {0x0B, 0x00}, 1000);
    sent(l, 0x0C1A, {0x03}, 3000);
    g_events.clear();
    uint16_t first = 0;
    ok("not yet at 4999 ms", l.overdue(5999, 5000, record, nullptr, &first) == 0 && g_events.empty());
    ok("at 5000 ms the oldest is answered", l.overdue(6000, 5000, record, nullptr, &first) == 1 && first == 0x1405);
    ok("refused, as lost() would", g_events.size() == 1 && g_events[0][5] == 0x0C && g_events[0][3] == 0x05);
    ok("and the younger one still waits", l.waiting() == 1);
    l.overdue(8000, 5000, record, nullptr, &first);
    ok("a setting that is never answered is refused, not told yes: the controller is still there",
       g_events.size() == 2 && g_events[1][0] == 0x0E && g_events[1][5] == 0x0C);
  }

  std::printf("\nBookkeeping\n");
  {
    HciLedger l;
    sent(l, 0x0401, {0x33, 0x8B, 0x9E, 0x08, 0x00});
    got(l, status(0x0401, 0x00));
    sent(l, 0x0402, {});
    got(l, complete(0x0402, {0x00}));
    ok("an inquiry cancelled is not ended again", l.pending() == 0);

    sent(l, 0x0405, SPEAKER);
    got(l, status(0x0405, 0x0C));
    ok("a page the controller refused owes nothing", l.pending() == 0 && l.waiting() == 0);

    got(l, {0x3E, 0x13, 0x01, 0x00, 0x40, 0x00, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0x18, 0, 0, 0, 0x48, 0, 0});
    ok("an LE connection is a link", l.links() == 1);
    got(l, {0x05, 0x04, 0x00, 0x40, 0x00, 0x13});
    ok("and its Disconnection Complete ends it", l.links() == 0);

    for (uint16_t i = 0; i < 9; i++)
      sent(l, (uint16_t) (0x1400 + i), {});
    ok("nine unanswered commands keep the newest eight", l.waiting() == HciLedger::MAX_WAITING);
    g_events.clear();
    l.lost(record, nullptr);
    ok("the oldest went", !g_events.empty() && g_events[0][3] == 0x01 && g_events[0][4] == 0x14);
  }
  {
    HciLedger l;
    l.set_recording_startup(true);
    for (uint16_t i = 0; i < 7; i++)
      sent(l, (uint16_t) (0x0C60 + i), Bytes(250, (uint8_t) i));
    ok("settings past the arena are counted, not written over each other",
       l.overflowed() > 0 && l.kept() == 6 && kept(l, 5)[3] == 5 && kept(l, 5).size() == 253);
  }

  return failures == 0 ? 0 : 1;
}
