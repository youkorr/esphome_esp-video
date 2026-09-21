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
#include <cstring>
#include <string>
#include <vector>

#include "descfixtures.h"
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
// And how many times the way home was asked for, which is a different question
// from which key crossed: going home tells the page nothing at all.
static int g_home = 0;

static PortallBT *fresh() {
  auto *bt = new PortallBT();
  g_sent.clear();
  g_home = 0;
  bt->set_home_sink([]() { g_home++; });
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

// A diagnostic is checked by CAPTURING what it printed, never by restating it:
// a test that repeats the sentence proves only that it can copy.
static std::string g_captured;
static FILE *g_old_stdout = nullptr;
static char g_capture_path[] = "/tmp/portall_bt_capture.XXXXXX";

static void capture_begin() {
  std::fflush(stdout);
  /* mkstemp REWRITES its template in place, so the second call would be
     handed a path with no XXXXXX left in it, fail, and leave stdout NULL --
     which is a segfault in whichever test happened to print next, with
     nothing wrong in the component at all. The template is restored every
     time rather than declared once. */
  std::strcpy(g_capture_path, "/tmp/portall_bt_capture.XXXXXX");
  int fd = mkstemp(g_capture_path);
  g_old_stdout = stdout;
  stdout = fdopen(fd, "w+");
}

static std::string capture_end() {
  std::fflush(stdout);
  FILE *mine = stdout;
  stdout = g_old_stdout;
  std::rewind(mine);
  std::string out;
  char line[512];
  while (std::fgets(line, sizeof(line), mine) != nullptr)
    out += line;
  std::fclose(mine);
  std::remove(g_capture_path);
  return out;
}

static size_t count_of(const std::string &hay, const char *needle) {
  size_t n = 0, at = 0;
  const std::string what(needle);
  while ((at = hay.find(what, at)) != std::string::npos) {
    n++;
    at += what.size();
  }
  return n;
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

  std::printf("MENU is the way out of a link, and Back is not\n");
  {
    PortallBT *bt = fresh();
    bt->feed_avrc_key(ESP_AVRC_PT_CMD_ROOT_MENU);
    // THE fix. Both of these were Escape, so a remote could move around inside
    // a link and never leave one: the only way back to the launcher was a
    // finger held in the corner of the glass, which is exactly what somebody
    // sitting down with a remote does not have.
    ok("MENU goes home", g_home == 1);
    ok("and tells the page nothing at all", g_sent.empty());
    delete bt;
  }
  {
    PortallBT *bt = fresh();
    bt->feed_avrc_key(ESP_AVRC_PT_CMD_EXIT);
    // Back stays back: WITHIN the page, which is what Jellyfin's television
    // layout and YouTube's television interface both do with Escape. Two
    // buttons that both leave the page would be one button wasted.
    ok("Back is still Escape into the page, not home",
       only(0x29) && g_home == 0);
    delete bt;
  }
  {
    // A panel whose YAML never set `keys:` has no home sink either, and MENU
    // must cost it a log line rather than a crash.
    auto *bt = new PortallBT();
    g_home = 0;
    bt->feed_avrc_key(ESP_AVRC_PT_CMD_ROOT_MENU);
    ok("with no `keys:` set, MENU does nothing and does not crash",
       g_home == 0);
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
    // An eight-byte report whose RESERVED byte is not zero is not a keyboard
    // and is not the Shield's shape either, so nothing is decoded from it.
    const uint8_t gamepad[8] = {0x80, 0x7F, 0x80, 0x7F, 0x08, 0x00, 0x00, 0x00};
    bt->feed_hid_keys(gamepad, sizeof(gamepad));
    ok("a report that is neither shape is left alone", g_sent.empty());
    delete bt;
  }

  std::printf("a gamepad, decoded from its OWN report descriptor\n");
  // WHY THIS SECTION WAS REWRITTEN. It used to feed 33-byte reports laid out
  // from a measurement of one NVIDIA Shield -- hat in the high nibble of byte
  // 2, face buttons in byte 3 -- and it PASSED, because the test and the code
  // shared the same wrong table. The panel is what failed: every direction
  // printed `up` and X and Y printed nothing. A test written against the same
  // assumption as the code can only ever confirm it.
  //
  // So the fixture is now a real report descriptor, in the item encoding of
  // HID 1.11 section 6.2.2, and NOTHING below says which byte anything is in.
  // The component works the offsets out of the descriptor, the way it will on
  // a device nobody here owns.

  // The report this descriptor describes: id, four axes, hat + padding, two
  // button bytes. Built by a helper that takes the hat and the buttons by
  // MEANING, so the test states what was pressed and the component works out
  // where that landed.
  auto pad_report = [](uint8_t hat, uint16_t buttons, uint8_t *out) {
    std::memset(out, 0, 8);
    out[0] = 0x01;
    out[1] = out[2] = out[3] = out[4] = 0x80;  // sticks centred
    out[5] = (uint8_t) (hat & 0x0F);
    out[6] = (uint8_t) (buttons & 0xFF);
    out[7] = (uint8_t) (buttons >> 8);
  };
  // The same report with the sticks somewhere other than centred. 0x80 is the
  // middle of the declared 0..255, so it is what letting go looks like, and
  // 0x0F is outside the hat's declared 0..7, which is its null value.
  auto pad_stick = [](uint8_t x, uint8_t y, uint8_t z, uint8_t rz, uint8_t *out) {
    std::memset(out, 0, 8);
    out[0] = 0x01;
    out[1] = x;
    out[2] = y;
    out[3] = z;
    out[4] = rz;
    out[5] = 0x0F;
  };
  auto with_descriptor = [&]() {
    PortallBT *bt = fresh();
    bt->feed_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214);
    return bt;
  };
  {
    PortallBT *bt = with_descriptor();
    ok("the descriptor parses into fields", bt->map_()->ready());
    ok("and it says the reports carry ids", bt->map_()->uses_ids());
    ok("and it fits, so nothing was dropped", !bt->map_()->truncated());
    delete bt;
  }
  {
    struct { uint8_t hat; uint16_t usage; const char *name; } hats[] = {
        {0x0, 0x52, "hat north -> up"},
        {0x2, 0x4F, "hat east  -> right"},
        {0x4, 0x51, "hat south -> down"},
        {0x6, 0x50, "hat west  -> left"},
    };
    for (const auto &one : hats) {
      PortallBT *bt = with_descriptor();
      uint8_t pad[8];
      pad_report(one.hat, 0, pad);
      bt->feed_hid_keys(pad, sizeof(pad));
      ok(one.name, only(one.usage));
      delete bt;
    }
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x08, 0x0001, pad);   // hat centred (the null value), button 1
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("button 1 is A, which is ok", only(0x28));
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x08, 0x0002, pad);   // button 2
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("button 2 is B, which is back", only(0x29));
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x01, 0, pad);        // north-east
    bt->feed_hid_keys(pad, sizeof(pad));
    // A grid of tiles has no diagonal, and choosing one of the two axes for
    // the caller would be the invention this file exists to keep out.
    ok("a diagonal on the hat moves nothing", g_sent.empty());
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x08, 0x0001, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    bt->feed_hid_keys(pad, sizeof(pad));
    bt->feed_hid_keys(pad, sizeof(pad));
    // A thumb held on a button is in EVERY report, exactly as a key is.
    ok("a button held down is sent once, not once per report", only(0x28));
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x08, 0, pad);
    for (int i = 0; i < 4; i++)
      bt->feed_hid_keys(pad, sizeof(pad));
    ok("a resting controller sends nothing at all", g_sent.empty());
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_report(0x08, 0x0008, pad);   // button 4 -- X, no meaning in a page
    capture_begin();
    bt->feed_hid_keys(pad, sizeof(pad));
    const std::string said = capture_end();
    ok("an unmapped button reaches the page as nothing", g_sent.empty());
    // AND IT SAYS WHICH ONE. The release this replaces printed nothing at all
    // for X and Y, which is how two wrong guesses stayed indistinguishable
    // from a device that was not sending anything.
    ok("but it names itself in the log", said.find("button 4") != std::string::npos);
    delete bt;
  }
  {
    // HOME IS A CONSUMER USAGE ON ITS OWN REPORT, which is the finding that
    // cost this thread a round: Linux's hid-nvidia-shield.c maps AC Home
    // 0x223 through the consumer path, so it never touches a gamepad button
    // byte. A reader told to press Home and watch for a button bit would have
    // watched for ever.
    PortallBT *bt = with_descriptor();
    const uint8_t home[2] = {0x02, 0x01};
    bt->feed_hid_keys(home, sizeof(home));
    ok("AC Home leaves the link", g_home == 1 && g_sent.empty());
    delete bt;
  }
  {
    PortallBT *bt = with_descriptor();
    const uint8_t back[2] = {0x02, 0x02};
    bt->feed_hid_keys(back, sizeof(back));
    ok("AC Back is Escape, which is back WITHIN the page",
       only(0x29) && g_home == 0);
    delete bt;
  }
  {
    // The four-byte Usage form carries its page in the high half, and a
    // parser that ignores it would have put AC Home on the Generic Desktop
    // page -- where this component's mapping has nothing for it, so the one
    // button somebody most wants would silently do nothing.
    PortallBT *bt = with_descriptor();
    bool found = false;
    for (uint8_t n = 0; n < bt->map_()->field_count(); n++) {
      const auto &f = bt->map_()->field(n);
      found = found || (f.usage_page == 0x0C && f.usage == 0x223);
    }
    ok("a four-byte Usage keeps its own page", found);
    delete bt;
  }
  {
    // A KEYBOARD THROUGH THE SAME PATH, because the descriptor route has to
    // serve one too -- its keycodes are an ARRAY field, six instances each
    // holding a usage rather than a bit per key.

    PortallBT *bt = fresh();
    bt->feed_hid_descriptor(KBD_DESC, sizeof(KBD_DESC), 0x0000, 0x0000);
    ok("a keyboard descriptor declares no report id", !bt->map_()->uses_ids());
    const uint8_t down[8] = {0, 0, 0x51, 0, 0, 0, 0, 0};
    bt->feed_hid_keys(down, sizeof(down));
    ok("and its arrow arrives as the usage it is", only(0x51));
    bt->feed_hid_keys(down, sizeof(down));
    ok("and a key still held is not sent again", only(0x51));
    delete bt;
  }

  {
    // A SHAPE THIS CANNOT READ NAMES ITSELF -- once per shape, not once in
    // total. THE FIX: the Shield carries its Home button on a different
    // report from its sticks (Linux's hid-nvidia-shield.c maps Home as
    // CONSUMER usage 0x223, not a gamepad button), so a single line spent on
    // whichever report arrived first would leave Home permanently silent.
    PortallBT *bt = fresh();
    const uint8_t odd_a[5] = {0x07, 0x11, 0x22, 0x33, 0x44};
    const uint8_t odd_b[3] = {0x08, 0x23, 0x02};   // a different shape
    capture_begin();
    bt->feed_hid_keys(odd_a, sizeof(odd_a));
    bt->feed_hid_keys(odd_a, sizeof(odd_a));   // same shape again: silent
    bt->feed_hid_keys(odd_b, sizeof(odd_b));
    bt->feed_hid_keys(odd_b, sizeof(odd_b));
    const std::string said = capture_end();
    ok("an unreadable report names its own shape",
       said.find("5 bytes, id 0x07") != std::string::npos);
    ok("and a SECOND shape gets its own line",
       said.find("3 bytes, id 0x08") != std::string::npos);
    ok("but a shape already named stays quiet",
       count_of(said, "a report this cannot read") == 2);
    ok("and none of it reaches the page", g_sent.empty());
    delete bt;
  }
  {
    // AND WITHOUT A DESCRIPTOR nothing is guessed at. A device whose
    // descriptor never arrived gets the plain boot-keyboard reading and
    // nothing else -- the byte offsets that used to stand in for one are what
    // put `up` under every direction on a real panel.
    PortallBT *bt = fresh();
    uint8_t pad[33] = {};
    pad[0] = 0x01;
    pad[2] = 0x40;
    capture_begin();
    bt->feed_hid_keys(pad, sizeof(pad));
    const std::string said = capture_end();
    ok("with no descriptor, a gamepad report moves nothing", g_sent.empty());
    ok("and the log says that is why",
       said.find("no report descriptor") != std::string::npos);
    delete bt;
  }
  // THE ANALOG STICKS. Reported from a panel as "les joystiks ne sont pas
  // fonctionnel", and the cause was an absence: feed_hid_usage handled the hat,
  // the buttons and the consumer page, and Generic Desktop X/Y/Z/Rz fell off
  // the end of it. Every one of these cases sends NOTHING against that code,
  // which is the whole reason to trust them.
  //
  // A stick is an absolute position rather than an edge, so the test streams
  // reports the way a controller does: centred first, then pushed.
  {
    struct { const char *name; uint8_t x, y, z, rz; uint16_t usage; } pushes[] = {
        {"the left stick pushed right is right", 0xFF, 0x80, 0x80, 0x80, 0x4F},
        {"pushed left is left", 0x00, 0x80, 0x80, 0x80, 0x50},
        // HID counts Y downwards, so away from the person is the LOW end.
        {"pushed away is up, because HID counts Y downwards", 0x80, 0x00, 0x80, 0x80, 0x52},
        {"pulled back is down", 0x80, 0xFF, 0x80, 0x80, 0x51},
        {"and the right stick steers too", 0x80, 0x80, 0xFF, 0x80, 0x4F},
        {"on both of its axes", 0x80, 0x80, 0x80, 0x00, 0x52},
    };
    for (const auto &one : pushes) {
      PortallBT *bt = with_descriptor();
      uint8_t pad[8];
      pad_stick(0x80, 0x80, 0x80, 0x80, pad);
      bt->feed_hid_keys(pad, sizeof(pad));
      g_sent.clear();
      pad_stick(one.x, one.y, one.z, one.rz, pad);
      bt->feed_hid_keys(pad, sizeof(pad));
      ok(one.name, only(one.usage));
      delete bt;
    }
  }
  {
    // A thumb held over is one move, not a hundred a second -- the same rule
    // the hat and the keyboard above live under, made here from the crossing
    // because the device reports a position rather than a press.
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    g_sent.clear();
    for (int i = 0; i < 20; i++) {
      pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
      bt->feed_hid_keys(pad, sizeof(pad));
    }
    ok("a stick held over moves one tile, not twenty", only(0x4F));
    // Let go and push again: that is a second move.
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("and letting it come back arms the next one", g_sent.size() == 2);
    delete bt;
  }
  {
    // Hysteresis: half travel to push, a third to let go. Coming back only as
    // far as the band between them must NOT arm another move, or a thumb
    // resting near the threshold walks the whole list.
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    g_sent.clear();
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    pad_stick(0xB0, 0x80, 0x80, 0x80, pad);  // 38% -- under the push, over the release
    bt->feed_hid_keys(pad, sizeof(pad));
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("easing off inside the hysteresis band does not re-arm it", only(0x4F));
    delete bt;
  }
  {
    // A diagonal moves along ONE axis. Ramped rather than teleported, because
    // that is what a thumb does and it is the case the guard is written for.
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    g_sent.clear();
    pad_stick(0xA0, 0x90, 0x80, 0x80, pad);  // both still inside the deadzone
    bt->feed_hid_keys(pad, sizeof(pad));
    pad_stick(0xFF, 0xC8, 0x80, 0x80, pad);  // right hard, down somewhat
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("a diagonal push moves along its dominant axis only", only(0x4F));
    delete bt;
  }
  {
    // THE TRIGGER GUARD. An axis that has never been seen near its own centre
    // is not steered with: a trigger declared on one of these usages rests at
    // an end of its range for ever, and reading that as a direction would be a
    // key nobody can let go of.
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_stick(0x00, 0x80, 0x80, 0x80, pad);  // "X" sits at its minimum, like a trigger
    for (int i = 0; i < 5; i++)
      bt->feed_hid_keys(pad, sizeof(pad));
    ok("an axis resting at one end of its range steers nothing", g_sent.empty());
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);  // pulled fully the other way
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("and still nothing, because it has never been centred", g_sent.empty());
    // Centre it once and it becomes a stick.
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("once it has been centred it steers like any other axis", only(0x4F));
    delete bt;
  }
  {
    // A reconnection forgets where the sticks were, exactly as it forgets the
    // hat -- and forgets that they were ever centred, because the panel has
    // not been told where they are now.
    PortallBT *bt = with_descriptor();
    uint8_t pad[8];
    pad_stick(0x80, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    bt->feed_hid_descriptor(PAD_DESC, sizeof(PAD_DESC), 0x0955, 0x7214);
    g_sent.clear();
    pad_stick(0xFF, 0x80, 0x80, 0x80, pad);
    bt->feed_hid_keys(pad, sizeof(pad));
    ok("after a reconnection a stick is armed by being centred again",
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
