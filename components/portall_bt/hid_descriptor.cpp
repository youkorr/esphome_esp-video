/* A HID report descriptor, walked, so a device says where its own buttons are.
 *
 * WHY THIS FILE HAD TO BE WRITTEN, and it is the correction of a shipped
 * fault rather than a new idea. The previous version decoded an NVIDIA Shield
 * controller from FIXED BYTE OFFSETS taken from a measurement: the hat in the
 * high nibble of byte 2, the face buttons in byte 3. A panel reported back
 * that two of those three were wrong -- every d-pad direction printed `up`,
 * and X and Y printed nothing at all -- which is what a hand-written table
 * does the moment it meets a report it was not written against. And a wrong
 * mapping here is worse than none, because it does not merely fail: it MOVES
 * THE FOCUS, to the wrong tile, on every press.
 *
 * The ask underneath was "le comportement du bluetooth doit gerer toutes les
 * peripherique", and a table of byte offsets can never be that. A descriptor
 * can: every HID device carries one, it says which bits of which report mean
 * which usage, and Bluedroid already hands it to us whole --
 * ESP_HIDH_GET_DSCP_EVT, dsc_list and dl_len.
 *
 * WHERE THIS CAME FROM. The user pointed at bluepad32, saying it has
 * everything needed, and they were most of the way right. bluepad32 carries
 * the MAPPING -- uni_hid_parser_android.c turns (usage page, usage) into a
 * gamepad, Apache 2.0, and keys.cpp follows it. What bluepad32 does NOT carry
 * is the walk from raw bytes to those usages: uni_hid_parser.c calls
 * btstack_hid_parser_init / _has_more / _get_field, which is BlueKitchen's
 * BTstack rather than bluepad32's own. That one piece is this file, and it is
 * written from the specification rather than copied: HID 1.11 section 6.2.2,
 * the item format, which is the same document every one of those parsers is
 * reading.
 *
 * NOTHING HERE IS DEVICE-SPECIFIC. There is no vendor id, no product id and
 * no report length in this file, deliberately -- the moment one appears, the
 * fault above is back.
 */
#include "portall_bt.h"

#include <cstring>

namespace esphome {
namespace portall_bt {

namespace {

/* Sign-extend a descriptor item's data to 32 bits.
 *
 * Logical Minimum and Logical Maximum are SIGNED in the specification, so a
 * one-byte 0xFF is -1 and a stick that swings either side of centre depends
 * on it. The unsigned globals -- Usage Page, Report Size, Report Count,
 * Report ID -- take the raw value instead, which is why both are kept. */
int32_t signed_item(uint32_t raw, uint8_t size) {
  switch (size) {
    case 1: return (int32_t)(int8_t) raw;
    case 2: return (int32_t)(int16_t) raw;
    default: return (int32_t) raw;
  }
}

}  // namespace

void HidReportMap::clear() {
  this->count_ = 0;
  this->ids_ = false;
  this->truncated_ = false;
}

bool HidReportMap::parse(const uint8_t *desc, uint16_t len) {
  this->clear();
  if (desc == nullptr || len == 0)
    return false;

  /* The globals, which survive from one Main item to the next, and the
     locals, which do not. That asymmetry is the whole of the item format and
     getting it backwards is how a descriptor parser silently assigns every
     field the same usage. */
  uint16_t usage_page = 0;
  int32_t logical_min = 0;
  int32_t logical_max = 0;
  uint32_t logical_max_raw = 0;
  uint8_t report_size = 0;
  uint16_t report_count = 0;
  uint8_t report_id = 0;

  /* Push and Pop save and restore the globals. Rare, and supported rather
     than ignored: a descriptor that pushes and pops is not malformed, and
     skipping those two items would leave every field after the pop reading
     the pushed state. */
  struct Saved {
    uint16_t usage_page;
    int32_t logical_min;
    int32_t logical_max;
    uint32_t logical_max_raw;
    uint8_t report_size;
    uint16_t report_count;
    uint8_t report_id;
  } stack[4];
  uint8_t depth = 0;

  uint16_t usages[MAX_LOCAL_USAGES];
  uint16_t usage_pages[MAX_LOCAL_USAGES];
  uint8_t usage_count = 0;
  uint32_t usage_min = 0;
  uint32_t usage_max = 0;
  bool has_range = false;

  /* One bit cursor per report id, for INPUT reports only. Output and Feature
     reports have their own numbering and must not move this one -- sharing a
     cursor across all three is the classic way to land every input field a
     few bits off. */
  struct Cursor {
    uint8_t id;
    uint16_t bits;
  } cursors[MAX_REPORT_IDS];
  uint8_t cursor_count = 0;

  uint16_t i = 0;
  while (i < len) {
    const uint8_t prefix = desc[i++];

    /* A long item carries its own size byte and no meaning this needs. The
       specification defines exactly one prefix for it and no tags at all, so
       stepping over it is complete rather than a shortcut. */
    if (prefix == 0xFE) {
      if ((uint16_t)(i + 1) > len)
        break;
      const uint8_t data_size = desc[i];
      i = (uint16_t)(i + 2 + data_size);
      continue;
    }

    uint8_t size = prefix & 0x03;
    if (size == 3)
      size = 4;  // the one irregularity in the encoding
    const uint8_t type = (uint8_t)((prefix >> 2) & 0x03);
    const uint8_t tag = (uint8_t)((prefix >> 4) & 0x0F);
    if ((uint32_t) i + size > len)
      break;

    uint32_t raw = 0;
    for (uint8_t k = 0; k < size; k++)
      raw |= (uint32_t) desc[i + k] << (8 * k);
    const int32_t value = signed_item(raw, size);
    i = (uint16_t)(i + size);

    if (type == 1) {  // Global
      switch (tag) {
        case 0x0: usage_page = (uint16_t) raw; break;
        case 0x1: logical_min = value; break;
        case 0x2: logical_max = value; logical_max_raw = raw; break;
        case 0x7: report_size = (uint8_t) raw; break;
        case 0x8:
          report_id = (uint8_t) raw;
          this->ids_ = true;
          break;
        case 0x9: report_count = (uint16_t) raw; break;
        case 0xA:  // Push
          if (depth < 4) {
            stack[depth++] = {usage_page,  logical_min,  logical_max, logical_max_raw,
                              report_size, report_count, report_id};
          }
          break;
        case 0xB:  // Pop
          if (depth > 0) {
            const Saved &s = stack[--depth];
            usage_page = s.usage_page;
            logical_min = s.logical_min;
            logical_max = s.logical_max;
            logical_max_raw = s.logical_max_raw;
            report_size = s.report_size;
            report_count = s.report_count;
            report_id = s.report_id;
          }
          break;
        default: break;  // physical, unit, exponent: nothing here reads them
      }
      continue;
    }

    if (type == 2) {  // Local
      /* A four-byte Usage carries its page in the high half. That is how a
         descriptor names a usage from a page other than the one currently in
         force, and a parser that ignores it puts Consumer-page buttons on the
         Generic Desktop page -- which on this project's own mapping would
         turn a Home button into nothing at all. */
      const uint16_t item_page = size == 4 ? (uint16_t)(raw >> 16) : usage_page;
      const uint16_t item_usage = size == 4 ? (uint16_t)(raw & 0xFFFF) : (uint16_t) raw;
      switch (tag) {
        case 0x0:
          if (usage_count < MAX_LOCAL_USAGES) {
            usages[usage_count] = item_usage;
            usage_pages[usage_count] = item_page;
            usage_count++;
          }
          break;
        case 0x1: usage_min = item_usage; has_range = true; break;
        case 0x2: usage_max = item_usage; has_range = true; break;
        default: break;
      }
      continue;
    }

    if (type != 0)
      continue;

    /* Main. Every one of them clears the locals, which is why the clearing
       sits after the switch rather than inside the Input branch. */
    if (tag == 0x8 && report_size != 0) {  // Input
      const bool constant = (raw & 0x01) != 0;
      const bool variable = (raw & 0x02) != 0;

      /* Some descriptors write an unsigned maximum with the top bit set --
         0xFF meaning 255 rather than -1 -- which the specification does not
         sanction and real devices do anyway. A negative maximum under a
         non-negative minimum cannot be anything else, so it is read back as
         unsigned rather than producing a field whose range runs backwards. */
      int32_t field_max = logical_max;
      if (logical_min >= 0 && field_max < 0)
        field_max = (int32_t) logical_max_raw;

      Cursor *cursor = nullptr;
      for (uint8_t c = 0; c < cursor_count; c++)
        if (cursors[c].id == report_id)
          cursor = &cursors[c];
      if (cursor == nullptr) {
        if (cursor_count >= MAX_REPORT_IDS) {
          this->truncated_ = true;
          continue;
        }
        cursors[cursor_count] = {report_id, 0};
        cursor = &cursors[cursor_count++];
      }

      for (uint16_t n = 0; n < report_count; n++) {
        if (!constant) {
          if (this->count_ >= MAX_FIELDS) {
            this->truncated_ = true;
          } else {
            HidField &f = this->fields_[this->count_++];
            f.report_id = report_id;
            f.bit_offset = cursor->bits;
            f.bit_size = report_size;
            f.logical_min = logical_min;
            f.logical_max = field_max;
            f.array = !variable;
            if (variable) {
              /* One usage per instance, in order. Where the descriptor named
                 fewer usages than instances the last one repeats, which is
                 what the specification says and what a run of identical
                 buttons declared once relies on. */
              if (n < usage_count) {
                f.usage_page = usage_pages[n];
                f.usage = usages[n];
              } else if (has_range) {
                const uint32_t u = usage_min + n;
                f.usage_page = usage_page;
                f.usage = (uint16_t)(u > usage_max ? usage_max : u);
              } else if (usage_count > 0) {
                f.usage_page = usage_pages[usage_count - 1];
                f.usage = usages[usage_count - 1];
              } else {
                f.usage_page = usage_page;
                f.usage = 0;
              }
              f.usage_max = f.usage;
            } else {
              /* An ARRAY field does not hold a value, it holds an INDEX into
                 a range of usages -- which is how a keyboard sends six keys
                 in six bytes rather than declaring a bit per key. So the
                 range is what is stored, and the decode turns a value back
                 into the usage it names. */
              f.usage_page = usage_page;
              f.usage = (uint16_t) usage_min;
              f.usage_max = (uint16_t) usage_max;
            }
          }
        }
        cursor->bits = (uint16_t)(cursor->bits + report_size);
      }
    }

    usage_count = 0;
    usage_min = 0;
    usage_max = 0;
    has_range = false;
  }

  return this->count_ > 0;
}

/* Pull one field's bits out of a report.
 *
 * Little-endian within the report, which is what the specification means by
 * the bit numbering, and sign-extended only when the field's own logical
 * minimum says it is signed. A field running past the end of the report is
 * not an error to shout about: a device may send a short report, and the
 * right answer is to leave that field alone. */
bool HidReportMap::field_value(const HidField &f, const uint8_t *payload, uint16_t len,
                               int32_t *out) {
  const uint32_t last_bit = (uint32_t) f.bit_offset + f.bit_size;
  if (f.bit_size == 0 || f.bit_size > 32 || last_bit > (uint32_t) len * 8)
    return false;

  uint32_t bits = 0;
  for (uint8_t b = 0; b < f.bit_size; b++) {
    const uint32_t at = (uint32_t) f.bit_offset + b;
    if ((payload[at / 8] >> (at % 8)) & 0x01)
      bits |= (uint32_t) 1 << b;
  }

  int32_t v = (int32_t) bits;
  if (f.logical_min < 0 && f.bit_size < 32) {
    const uint32_t sign = (uint32_t) 1 << (f.bit_size - 1);
    if (bits & sign)
      v = (int32_t)(bits | ~(((uint32_t) 1 << f.bit_size) - 1));
  }
  *out = v;
  return true;
}

bool HidReportMap::decode(const uint8_t *report, uint16_t len,
                          const std::function<void(const HidField &, int32_t)> &fn) const {
  if (this->count_ == 0 || report == nullptr || len == 0)
    return false;

  /* Where the payload starts is decided by the DESCRIPTOR, not by the report.
     A descriptor that declared any Report ID puts one in front of every
     report; one that declared none never does, and looking for an id there
     would read the first field's bits as one. */
  uint8_t id = 0;
  const uint8_t *payload = report;
  uint16_t plen = len;
  if (this->ids_) {
    id = report[0];
    payload = report + 1;
    plen = (uint16_t)(len - 1);
  }

  bool any = false;
  for (uint8_t n = 0; n < this->count_; n++) {
    const HidField &f = this->fields_[n];
    if (f.report_id != id)
      continue;
    int32_t value = 0;
    if (!field_value(f, payload, plen, &value))
      continue;
    any = true;
    fn(f, value);
  }
  return any;
}

}  // namespace portall_bt
}  // namespace esphome
