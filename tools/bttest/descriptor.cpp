// Does the report-descriptor walker put the fields where the descriptor says?
//
// WHY THIS FILE EXISTS, and it is a fault rather than an itch. This component
// used to decode an NVIDIA Shield controller from FIXED BYTE OFFSETS taken off
// a real one: the hat in the high nibble of byte 2, the face buttons in byte
// 3. tools/bttest/input.cpp tested exactly that, and PASSED -- because the
// test and the code were written from the same table. The panel is what
// failed: every d-pad direction printed `up`, and X and Y printed nothing at
// all. A test that shares the code's assumption can only ever confirm it.
//
// So the offsets are gone and a walk of the device's own descriptor stands
// where they were, and this is the check on the arithmetic of that walk. Every
// case below states the answer from the DESCRIPTOR -- the item encoding of HID
// 1.11 section 6.2.2 -- rather than from anything measured, which is the whole
// difference: a descriptor nobody here has seen is decoded by the same rules
// as these.
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
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::HidField;
using esphome::portall_bt::HidReportMap;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

// Find the one field carrying this usage, or nullptr.
static const HidField *find(const HidReportMap &map, uint16_t page, uint16_t usage) {
  for (uint8_t n = 0; n < map.field_count(); n++) {
    const HidField &f = map.field(n);
    if (f.usage_page == page && f.usage == usage)
      return &f;
  }
  return nullptr;
}

// What the map decodes one report into, as a flat list, so a case says what
// came out rather than poking at the component's state.
struct Got {
  uint16_t page;
  uint16_t usage;
  int32_t value;
};
static std::vector<Got> decode(const HidReportMap &map, const uint8_t *report, uint16_t len) {
  std::vector<Got> out;
  map.decode(report, len, [&](const HidField &f, int32_t v) {
    out.push_back({f.usage_page, f.usage, v});
  });
  return out;
}
static bool has(const std::vector<Got> &got, uint16_t page, uint16_t usage, int32_t value) {
  for (const auto &g : got)
    if (g.page == page && g.usage == usage && g.value == value)
      return true;
  return false;
}

int main() {
  std::printf("the boot mouse of the specification's own appendix\n");
  {
    // HID 1.11, appendix E.10, verbatim. Worth using because the answer is
    // published rather than derived here: three buttons in the low three bits
    // of byte 0, five bits of padding, then two SIGNED eight-bit axes.
    static const uint8_t MOUSE[] = {
        0x05, 0x01,  // Usage Page (Generic Desktop)
        0x09, 0x02,  // Usage (Mouse)
        0xA1, 0x01,  // Collection (Application)
        0x09, 0x01,  //   Usage (Pointer)
        0xA1, 0x00,  //   Collection (Physical)
        0x05, 0x09,  //     Usage Page (Buttons)
        0x19, 0x01,  //     Usage Minimum (1)
        0x29, 0x03,  //     Usage Maximum (3)
        0x15, 0x00,  //     Logical Minimum (0)
        0x25, 0x01,  //     Logical Maximum (1)
        0x95, 0x03,  //     Report Count (3)
        0x75, 0x01,  //     Report Size (1)
        0x81, 0x02,  //     Input (Data, Variable, Absolute)
        0x95, 0x01,  //     Report Count (1)
        0x75, 0x05,  //     Report Size (5)
        0x81, 0x01,  //     Input (Constant)
        0x05, 0x01,  //     Usage Page (Generic Desktop)
        0x09, 0x30,  //     Usage (X)
        0x09, 0x31,  //     Usage (Y)
        0x15, 0x81,  //     Logical Minimum (-127)
        0x25, 0x7F,  //     Logical Maximum (127)
        0x75, 0x08,  //     Report Size (8)
        0x95, 0x02,  //     Report Count (2)
        0x81, 0x06,  //     Input (Data, Variable, Relative)
        0xC0,        //   End Collection
        0xC0,        // End Collection
    };
    HidReportMap map;
    ok("it parses", map.parse(MOUSE, sizeof(MOUSE)));
    ok("and declares no report id", !map.uses_ids());
    // Three buttons and two axes. The five padding bits are CONSTANT and are
    // not fields -- but they must still move the cursor, which is what the
    // axis offsets below prove.
    ok("five fields, the padding not among them", map.field_count() == 5);

    const HidField *b1 = find(map, 0x09, 1);
    const HidField *b3 = find(map, 0x09, 3);
    ok("button 1 is bit 0", b1 != nullptr && b1->bit_offset == 0 && b1->bit_size == 1);
    ok("button 3 is bit 2", b3 != nullptr && b3->bit_offset == 2);

    const HidField *x = find(map, 0x01, 0x30);
    const HidField *y = find(map, 0x01, 0x31);
    // 3 + 5 = 8. A walker that skipped the constant item entirely would put X
    // at bit 3 and every reading after it would be nonsense.
    ok("X begins at bit 8, so the padding was counted",
       x != nullptr && x->bit_offset == 8 && x->bit_size == 8);
    ok("Y begins at bit 16", y != nullptr && y->bit_offset == 16);
    ok("and both are signed", x != nullptr && x->logical_min == -127 && x->logical_max == 127);

    // A mouse moved left and up, with the middle button down.
    const uint8_t moved[3] = {0x02, 0xFF, 0xFE};
    const auto got = decode(map, moved, sizeof(moved));
    ok("the middle button reads down", has(got, 0x09, 2, 1));
    ok("and the others read up", has(got, 0x09, 1, 0) && has(got, 0x09, 3, 0));
    ok("-1 on a signed axis is -1, not 255", has(got, 0x01, 0x30, -1));
    ok("and -2 is -2", has(got, 0x01, 0x31, -2));
  }

  std::printf("what a fixed byte offset cannot know\n");
  {
    // The regression, stated as arithmetic. This is the gamepad shape
    // input.cpp uses: id, four axes, a FOUR-BIT hat, four bits of padding,
    // sixteen buttons. The hat is at bit 32 of the payload -- the low nibble
    // of the fifth byte after the id -- and the old code read the high nibble
    // of byte 2 of the whole report, which here is a stick axis.
    static const uint8_t PAD[] = {
        0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,
        0x85, 0x01,
        0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
        0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
        0x09, 0x39, 0x15, 0x00, 0x25, 0x07, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42,
        0x75, 0x04, 0x95, 0x01, 0x81, 0x03,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x10, 0x81, 0x02,
        0xC0,
    };
    HidReportMap map;
    map.parse(PAD, sizeof(PAD));
    const HidField *hat = find(map, 0x01, 0x39);
    ok("the hat is where the descriptor put it, not where anybody guessed",
       hat != nullptr && hat->report_id == 1 && hat->bit_offset == 32 &&
           hat->bit_size == 4);
    // And the fixed offset the panel disproved: the high nibble of byte 2 of
    // the report is inside the FIRST axis, which is why every direction came
    // out as the same one.
    const HidField *first_axis = find(map, 0x01, 0x30);
    ok("byte 2 of that report is an axis, which is what the old table read",
       first_axis != nullptr && first_axis->bit_offset == 0);

    // Hat south, with the sticks centred and nothing pressed.
    const uint8_t report[8] = {0x01, 0x80, 0x80, 0x80, 0x80, 0x04, 0x00, 0x00};
    const auto got = decode(map, report, sizeof(report));
    ok("and south reads as south", has(got, 0x01, 0x39, 4));
    ok("button 1 reads up", has(got, 0x09, 1, 0));
    ok("button 16 exists and reads up", has(got, 0x09, 16, 0));

    // THE REPORTED FAULT, REPRODUCED AS ARITHMETIC. A panel said every d-pad
    // direction printed `up`. Here is why, in one loop: the old rule read the
    // high nibble of byte 2 of the report, and with the hat where the
    // descriptor really puts it that nibble does not move at all between the
    // four directions. A mapping that answers the same thing whatever is
    // pressed is indistinguishable from one that is not reading the hat --
    // because it is not.
    uint8_t old_rule[4];
    for (uint8_t dir = 0; dir < 4; dir++) {
      uint8_t r[8] = {0x01, 0x80, 0x80, 0x80, 0x80, 0x00, 0x00, 0x00};
      r[5] = (uint8_t) (dir * 2);            // north, east, south, west
      old_rule[dir] = (uint8_t) (r[2] >> 4); // what the old code read
      const auto one = decode(map, r, sizeof(r));
      if (!has(one, 0x01, 0x39, dir * 2))
        failures++;
    }
    ok("the old fixed offset answered the same for all four directions",
       old_rule[0] == old_rule[1] && old_rule[1] == old_rule[2] &&
           old_rule[2] == old_rule[3]);

    const uint8_t pressed[8] = {0x01, 0x80, 0x80, 0x80, 0x80, 0x08, 0x00, 0x80};
    const auto got2 = decode(map, pressed, sizeof(pressed));
    // Bit 7 of the SECOND button byte is button 16: the bit numbering runs
    // little-endian across the whole payload, which a walker that restarted
    // it per byte would get right and one that ran it backwards would not.
    ok("button 16 is the top bit of the second button byte",
       has(got2, 0x09, 16, 1) && has(got2, 0x09, 15, 0));
  }

  std::printf("report ids each keep their own cursor\n");
  {
    static const uint8_t TWO[] = {
        0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,
        0x85, 0x01,                                      // Report ID 1
        0x05, 0x09, 0x19, 0x01, 0x29, 0x08,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x85, 0x02,                                      // Report ID 2
        0x05, 0x0C, 0x0A, 0x23, 0x02, 0x0A, 0x24, 0x02,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
        0xC0,
    };
    HidReportMap map;
    map.parse(TWO, sizeof(TWO));
    ok("ids are declared", map.uses_ids());
    const HidField *home = find(map, 0x0C, 0x223);
    // THE CASE THIS IS REALLY FOR: report 2's first field must start at bit 0
    // of ITS payload, not at bit 8 where report 1 left off. One shared cursor
    // is the classic way to land a second report's fields exactly one byte
    // out, which here would have made Home unpressable.
    ok("report 2 starts its own bits at zero",
       home != nullptr && home->report_id == 2 && home->bit_offset == 0);
    // A four-byte Usage carries the page in its high half -- ignore that and
    // AC Home lands on the Generic Desktop page, where nothing maps it.
    ok("and it kept the page its four-byte Usage named", home->usage_page == 0x0C);

    const uint8_t r2[2] = {0x02, 0x02};
    const auto got = decode(map, r2, sizeof(r2));
    ok("report 2 decodes only report 2's fields", got.size() == 2);
    ok("AC Back is the one down", has(got, 0x0C, 0x224, 1));

    const uint8_t r1[2] = {0x01, 0x01};
    const auto got1 = decode(map, r1, sizeof(r1));
    ok("and report 1 decodes only its own", got1.size() == 8 && has(got1, 0x09, 1, 1));
  }

  std::printf("the awkward corners\n");
  {
    // A sixteen-bit field, little-endian across two bytes, at an offset that
    // is not a multiple of eight.
    static const uint8_t WIDE[] = {
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x04,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x04, 0x81, 0x02,
        0x05, 0x01, 0x09, 0x30,
        0x16, 0x00, 0x80,        // Logical Minimum (-32768)
        0x26, 0xFF, 0x7F,        // Logical Maximum (32767)
        0x75, 0x10, 0x95, 0x01, 0x81, 0x02,
        0xC0,
    };
    HidReportMap map;
    map.parse(WIDE, sizeof(WIDE));
    const HidField *x = find(map, 0x01, 0x30);
    ok("a wide field starts on the bit the count left it on",
       x != nullptr && x->bit_offset == 4 && x->bit_size == 16);
    ok("and it is signed both ways",
       x->logical_min == -32768 && x->logical_max == 32767);
    // 0x1234 placed at bit 4: low nibble of byte 0 is the buttons, then the
    // value runs little-endian from there.
    uint8_t report[3] = {0x40, 0x23, 0x01};
    const auto got = decode(map, report, sizeof(report));
    ok("and it reads 0x1234 rather than a byte-swapped anything",
       has(got, 0x01, 0x30, 0x1234));
    // The same bits with the top one set are NEGATIVE, because the field's
    // own logical minimum says so.
    // The field's own top bit is bit 19 of the report -- bit 3 of byte 2 --
    // NOT bit 7 of it, which is four bits past the end of the field. Getting
    // that wrong in the test is the ruler being wrong, which this file's
    // whole reason for existing is an instance of.
    uint8_t low[3] = {0x00, 0x00, 0x08};
    const auto neg = decode(map, low, sizeof(low));
    ok("and the top bit makes it negative", has(neg, 0x01, 0x30, -32768));
  }
  {
    // An unsigned maximum written with its top bit set -- 0xFF meaning 255,
    // which the specification does not sanction and real devices do anyway. A
    // maximum below its minimum cannot be anything else.
    static const uint8_t ODD[] = {
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,
        0x09, 0x30, 0x15, 0x00, 0x25, 0xFF,   // Logical Maximum (255, written as -1)
        0x75, 0x08, 0x95, 0x01, 0x81, 0x02,
        0xC0,
    };
    HidReportMap map;
    map.parse(ODD, sizeof(ODD));
    const HidField *x = find(map, 0x01, 0x30);
    ok("a maximum below its minimum is read back as unsigned",
       x != nullptr && x->logical_max == 255);
    const uint8_t report[1] = {0xF0};
    ok("so the value stays positive", has(decode(map, report, 1), 0x01, 0x30, 240));
  }
  {
    // Push and Pop. Skipping them silently leaves every field after the pop
    // reading the pushed state, which is a whole report decoded wrong.
    static const uint8_t PUSHED[] = {
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08,
        0xA4,                                 // Push
        0x05, 0x09, 0x19, 0x01, 0x29, 0x08, 0x81, 0x02,
        0xB4,                                 // Pop
        0x09, 0x32, 0x95, 0x01, 0x81, 0x02,   // back on Generic Desktop
        0xC0,
    };
    HidReportMap map;
    map.parse(PUSHED, sizeof(PUSHED));
    ok("after a pop the usage page is the pushed one again",
       find(map, 0x01, 0x32) != nullptr);
    ok("and the pushed section kept its own", find(map, 0x09, 1) != nullptr);
  }
  {
    // An ARRAY field: six instances each holding a USAGE rather than a bit
    // per key. Get this wrong and a keyboard reports key number 0x51 as the
    // value 81 of some usage nobody named.
    static const uint8_t KBD[] = {
        0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,
        0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00,
        0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,   // Input (Data, ARRAY, Absolute)
        0xC0,
    };
    HidReportMap map;
    map.parse(KBD, sizeof(KBD));
    ok("six array fields", map.field_count() == 6);
    ok("and they are marked as arrays", map.field(0).array);
    ok("carrying the usage RANGE rather than one usage",
       map.field(0).usage == 0x00 && map.field(0).usage_max == 0xFF);
  }
  {
    // A SHORT report. A device may send fewer bytes than the descriptor
    // describes, and the right answer is to leave those fields alone rather
    // than to read past the buffer -- which on a panel is a reboot.
    static const uint8_t WIDE[] = {
        0x05, 0x01, 0x09, 0x04, 0xA1, 0x01,
        0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35,
        0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
        0xC0,
    };
    HidReportMap map;
    map.parse(WIDE, sizeof(WIDE));
    const uint8_t two_bytes[2] = {0x11, 0x22};
    const auto got = decode(map, two_bytes, sizeof(two_bytes));
    ok("a short report yields only the fields it holds", got.size() == 2);
    ok("and those are right", has(got, 0x01, 0x30, 0x11) && has(got, 0x01, 0x31, 0x22));
  }
  {
    // Rubbish in. A descriptor that ends mid-item, and one that is nothing at
    // all: both must come back empty rather than walking off the end.
    HidReportMap map;
    const uint8_t cut[] = {0x05, 0x01, 0x09, 0x04, 0xA1, 0x01, 0x26};
    ok("a descriptor cut off mid-item yields nothing", !map.parse(cut, sizeof(cut)));
    ok("and an empty one too", !map.parse(nullptr, 0));
    ok("and decoding against no map is a no-op",
       !map.decode(cut, sizeof(cut), [](const HidField &, int32_t) {}));
  }
  {
    // More fields than this can hold. It keeps what fits and SAYS so -- a
    // truncated map decodes its own fields perfectly and never mentions the
    // rest, which is exactly the silent half-answer this repository keeps
    // having to dig out of a log.
    std::vector<uint8_t> big = {0x05, 0x09, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01};
    for (int i = 0; i < 20; i++) {
      big.insert(big.end(), {0x19, 0x01, 0x29, 0x08, 0x95, 0x08, 0x81, 0x02});
    }
    HidReportMap map;
    map.parse(big.data(), (uint16_t) big.size());
    ok("a descriptor past the limit keeps what fits",
       map.field_count() == HidReportMap::MAX_FIELDS);
    ok("and says it was truncated", map.truncated());
  }

  if (failures != 0)
    std::printf("\n%d failure(s)\n", failures);
  return failures == 0 ? 0 : 1;
}
