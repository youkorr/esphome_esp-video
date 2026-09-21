/* What a Bluetooth input device's button MEANS, in one place.
 *
 * WHY THIS FILE EXISTS. The first version of this feature asked a household to
 * write the mapping itself, as a lambda over AVRCP command codes in its own
 * YAML. That was pushed back on -- "le comportement du bluetooth doit gerer
 * toutes les peripherique qu'il dispose du bluetooth" -- and the objection was
 * right twice over.
 *
 * It was right about who should write it: a person configuring a panel should
 * name what they want, not enumerate somebody else's constants.
 *
 * And it was right about something worse, which only showed up when the
 * specification was finally read instead of remembered. That lambda mapped
 * ESP_AVRC_PT_CMD_FORWARD -- next track -- onto "move down a tile". AVRCP has
 * had SELECT, UP, DOWN, LEFT, RIGHT, ROOT_MENU and EXIT since it was derived
 * from the AV/C panel subunit, and a television-style Bluetooth remote sends
 * exactly those. So the mapping was an invention standing in front of an
 * answer the standard already gives -- the shape this project has paid for
 * more than once.
 *
 * NOTHING IN THIS FILE IS GUESSED. Both tables are transcriptions: the AVRCP
 * one from esp_avrc_api.h, and the HID one from the Keyboard/Keypad usage
 * page, which is what a boot-protocol keyboard report carries by definition.
 *
 * A GAMEPAD is here too, and getting it right took one more round than it
 * should have. The version before this one decoded an NVIDIA Shield from
 * FIXED BYTE OFFSETS measured on a real one -- the hat in the high nibble of
 * byte 2, the face buttons in byte 3. The panel reported back that two of the
 * three were wrong: every direction printed `up`, and X and Y printed nothing
 * at all. A hand-written table is only ever right about the device it was
 * written against, and here being wrong is worse than being absent, because
 * it does not fail quietly -- it MOVES THE FOCUS to the wrong tile.
 *
 * So there are no byte offsets in this file. hid_descriptor.cpp walks the
 * device's OWN report descriptor and this maps the usages that come out of
 * it, which is the only shape that answers what was actually asked for: "le
 * comportement du bluetooth doit gerer toutes les peripherique".
 *
 * THE MAPPING FOLLOWS bluepad32's uni_hid_parser_android.c (Apache 2.0,
 * Ricardo Quesada), which the user pointed at. Their Button-page numbering --
 * 1 is A, 2 is B, 4 is X, 5 is Y, with 3 and 6 skipped -- is Android's
 * convention rather than anything the HID specification fixes, and it is what
 * a controller built for an Android device reports. The hat handling is the
 * specification: eight compass points clockwise from north, one past the last
 * meaning centred.
 */
#include "portall_bt.h"

#include "esphome/core/log.h"

#include <cstring>

#ifdef CONFIG_BT_BLUEDROID_ENABLED
#include "esp_avrc_api.h"
#endif

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt";

namespace {

/* HID Keyboard/Keypad usage page, and the handful of usages a panel is driven
 * with. These numbers are the HID specification's; portall's own key table
 * carries the same ones, and tools/checkkeys.py is what keeps the two honest.
 */
constexpr uint16_t PAGE_KEYBOARD = 0x07;
constexpr uint16_t KEY_ENTER = 0x28;
constexpr uint16_t KEY_ESCAPE = 0x29;
constexpr uint16_t KEY_RIGHT = 0x4F;
constexpr uint16_t KEY_LEFT = 0x50;
constexpr uint16_t KEY_DOWN = 0x51;
constexpr uint16_t KEY_UP = 0x52;
constexpr uint16_t KEY_PAGE_UP = 0x4B;
constexpr uint16_t KEY_PAGE_DOWN = 0x4E;

/* AVRCP passthrough -> the same usages.
 *
 * Read out of esp_avrc_api.h rather than recalled: SELECT is 0x00 and the four
 * arrows are 0x01..0x04, which is why a real remote needs no per-device work
 * at all. The media transport commands -- play, pause, next, previous -- are
 * deliberately NOT here: they already reach the YAML through on_media_key and
 * mean what they say, and bending "next track" into "next tile" is exactly the
 * invention this file was written to remove. */
struct AvrcNav {
  uint8_t code;
  uint16_t usage;
};

constexpr AvrcNav AVRC_NAV[] = {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
    {ESP_AVRC_PT_CMD_SELECT, KEY_ENTER},
    {ESP_AVRC_PT_CMD_UP, KEY_UP},
    {ESP_AVRC_PT_CMD_DOWN, KEY_DOWN},
    {ESP_AVRC_PT_CMD_LEFT, KEY_LEFT},
    {ESP_AVRC_PT_CMD_RIGHT, KEY_RIGHT},
    {ESP_AVRC_PT_CMD_ENTER, KEY_ENTER},
    {ESP_AVRC_PT_CMD_EXIT, KEY_ESCAPE},
    {ESP_AVRC_PT_CMD_PAGE_UP, KEY_PAGE_UP},
    {ESP_AVRC_PT_CMD_PAGE_DOWN, KEY_PAGE_DOWN},
#endif
};

const char *usage_name(uint16_t usage) {
  switch (usage) {
    case KEY_UP: return "up";
    case KEY_DOWN: return "down";
    case KEY_LEFT: return "left";
    case KEY_RIGHT: return "right";
    case KEY_ENTER: return "ok";
    case KEY_ESCAPE: return "back";
    case KEY_PAGE_UP: return "page up";
    case KEY_PAGE_DOWN: return "page down";
    default: return "";
  }
}

/* Whether these bytes are a boot-protocol keyboard report.
 *
 * Eight bytes -- modifiers, a RESERVED byte the specification requires to be
 * zero, then six keycodes -- or nine with a report id in front. The reserved
 * byte is the whole of the test and it is a weak one, so this is behind
 * `keys:` being asked for, and every decode says out loud what it decoded: a
 * mapping that is wrong must be visible rather than silent, which is the one
 * rule this repository has had to relearn in every costume.
 *
 * A gamepad's report can be eight bytes too. That is precisely why a gamepad
 * needs its own report descriptor read, and why this does not pretend to
 * cover one. */
/* The usage pages and usages this maps, all of them from the HID usage
   tables rather than from any one device. */
constexpr uint16_t PAGE_DESKTOP = 0x01;
constexpr uint16_t PAGE_BUTTON = 0x09;
constexpr uint16_t PAGE_CONSUMER = 0x0C;

constexpr uint16_t DESKTOP_HAT = 0x39;
constexpr uint16_t DESKTOP_DPAD_UP = 0x90;
constexpr uint16_t DESKTOP_DPAD_DOWN = 0x91;
constexpr uint16_t DESKTOP_DPAD_RIGHT = 0x92;
constexpr uint16_t DESKTOP_DPAD_LEFT = 0x93;

/* The analog sticks. X and Y are the left one and Z and Rz are the right,
   which is bluepad32's Android parser -- the same source this file's button
   numbering comes from. Rx and Ry are absent on purpose: see the note on
   pad_axis_live_ in the header. */
constexpr uint16_t DESKTOP_X = 0x30;
constexpr uint16_t DESKTOP_Y = 0x31;
constexpr uint16_t DESKTOP_Z = 0x32;
constexpr uint16_t DESKTOP_RZ = 0x35;

/* How far a stick has to be pushed to count as a direction, and how far back
   it has to come before it can be pushed again, both as a percentage of full
   travel from the axis's own declared centre.

   These two are a JUDGEMENT rather than a transcription, which is worth
   saying because nothing else in this file is. The specification fixes no
   deadzone -- it only says what the axis reports -- so the numbers answer
   what this is for: moving between tiles across a room. Half travel is a
   deliberate push rather than a thumb resting on the stick, and letting go
   at a THIRD rather than at the same point is hysteresis: one threshold for
   both would chatter a whole list past somebody holding the stick near it. */
constexpr int32_t STICK_PUSH_PCT = 50;
constexpr int32_t STICK_RELEASE_PCT = 30;

/* Which of the four this usage is, or -1. */
int axis_index(uint16_t page, uint16_t usage) {
  if (page != PAGE_DESKTOP)
    return -1;
  switch (usage) {
    case DESKTOP_X: return 0;
    case DESKTOP_Y: return 1;
    case DESKTOP_Z: return 2;
    case DESKTOP_RZ: return 3;
    default: return -1;
  }
}

/* AC Home and AC Back on the Consumer page.
   These are where a Shield's Home and Back buttons really live, and it took
   reading Linux's hid-nvidia-shield.c to find out: its android_input_mapping()
   returns early unless the page is Consumer, then maps AC Home 0x223 and AC
   Back 0x224. A reader told to press Home and watch a gamepad button byte
   would have watched for ever, which is what the release before this one
   asked them to do. */
constexpr uint16_t CONSUMER_HOME = 0x223;
constexpr uint16_t CONSUMER_BACK = 0x224;

/* bluepad32's Android button numbering, which is a convention rather than a
   rule: 3 and 6 are skipped because Android's own mapping has no C or Z. */
constexpr uint16_t BUTTON_A = 1;
constexpr uint16_t BUTTON_B = 2;

/* The eight compass points a Hat Switch reports, clockwise from north. Only
   the four cardinals reach the page: a grid of tiles has no diagonal, and
   choosing one of a diagonal's two axes for the caller would be exactly the
   invention this file exists to keep out. */
uint16_t hat_usage(uint8_t hat) {
  switch (hat) {
    case 0: return KEY_UP;
    case 2: return KEY_RIGHT;
    case 4: return KEY_DOWN;
    case 6: return KEY_LEFT;
    default: return 0;
  }
}

bool looks_like_keyboard(const uint8_t *data, uint16_t len, uint16_t *at) {
  if (len == 8 && data[1] == 0) {
    *at = 2;
    return true;
  }
  if (len == 9 && data[2] == 0) {
    *at = 3;
    return true;
  }
  return false;
}

}  // namespace

void PortallBT::feed_avrc_key(uint8_t code) {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  /* MENU is the way OUT of a link, and nothing else on a remote is.
   *
   * It used to be one more Escape beside EXIT, which is two buttons doing the
   * same thing and no button doing the thing somebody with a remote actually
   * needs: once a tile is open, the only way back to the launcher was a finger
   * held in the top-left corner of the glass. That is a gesture for somebody
   * standing at the panel, and a remote is for somebody who is not.
   *
   * So the two part company the way a television's do. EXIT/Back stays Escape
   * -- back WITHIN the page, which is what Jellyfin and YouTube's television
   * interface both do with it. MENU leaves the page entirely.
   *
   * This costs no sender change at all: portall's 'H' message and the
   * sender's half of it have both been in place since 4.9.0, when the board
   * half was removed and the sender's was deliberately kept. */
  if (code == ESP_AVRC_PT_CMD_ROOT_MENU) {
    if (this->home_sink_) {
      ESP_LOGI(TAG, "remote: menu -- back to this panel's own page");
      this->home_sink_();
    } else {
      ESP_LOGW(TAG, "remote: menu pressed, but 'keys:' is not set, so there is "
                    "nowhere to go home to");
    }
    return;
  }
#endif
  if (!this->key_sink_)
    return;
  for (const auto &entry : AVRC_NAV) {
    if (entry.code != code)
      continue;
    ESP_LOGI(TAG, "remote: %s", usage_name(entry.usage));
    this->key_sink_(PAGE_KEYBOARD, entry.usage);
    return;
  }
}

void PortallBT::feed_hid_keys(const uint8_t *data, uint16_t len) {
  if (!this->key_sink_) {
    /* Said once rather than returning in silence, because a device paired,
       connected and pressing buttons at a panel that decodes none of them is
       the exact shape of the report this whole path was rewritten for. The
       one setting that turns it on is named here rather than in a document
       somebody has to already know to look for. */
    if (this->said_shape_count_ == 0) {
      this->said_shape_count_ = 1;
      ESP_LOGI(TAG, "this device is sending buttons, but 'keys:' is not set on "
                    "portall_bt, so nothing is decoded and nothing reaches the "
                    "page");
    }
    return;
  }
  /* THE DESCRIPTOR FIRST, whenever there is one, because it is the only
     answer that can be right about a device nobody here owns. Everything
     below it is the fallback for a device whose descriptor never arrived. */
  if (this->hid_map_.ready()) {
    uint8_t keys_now[6] = {};
    uint8_t key_count = 0;
    bool decoded = this->hid_map_.decode(
        data, len, [&](const HidField &f, int32_t value) {
          /* A keyboard's keycodes arrive as an ARRAY, several instances of
             one field each holding a usage rather than a value, so they are
             gathered here and compared against the last report in one go --
             a key still held is in every report a keyboard sends. */
          if (f.array && f.usage_page == PAGE_KEYBOARD) {
            if (value >= f.logical_min && value <= f.logical_max) {
              const uint16_t usage = (uint16_t)(f.usage + (value - f.logical_min));
              if (usage > 1 && key_count < 6)
                keys_now[key_count++] = (uint8_t) usage;
            }
            return;
          }
          if (f.array)
            return;
          this->feed_hid_usage(f.usage_page, f.usage, value, f.logical_min, f.logical_max);
        });
    if (decoded) {
      this->note_keyboard_keys_(keys_now);
      return;
    }
    /* A report the descriptor has no fields for is not a failure of this
       code, and saying so by shape is what turns "the controller does
       nothing" into one readable line. */
    this->say_unreadable_report_(data, len);
    return;
  }

  if (!this->said_no_descriptor_) {
    this->said_no_descriptor_ = true;
    ESP_LOGI(TAG, "this device sent no report descriptor, so only a plain "
                  "keyboard report can be read from it");
  }

  uint16_t at = 0;
  if (!looks_like_keyboard(data, len, &at)) {
    this->say_unreadable_report_(data, len);
    return;
  }

  uint8_t now[6] = {};
  const uint16_t count = (uint16_t) (len - at) < 6 ? (uint16_t) (len - at) : 6;
  memcpy(now, data + at, count);
  this->note_keyboard_keys_(now);
}

void PortallBT::note_keyboard_keys_(const uint8_t *now) {
  /* Only what is NEW. A key still held down is in every report a keyboard
     sends, so comparing against the last one is what stops one press becoming
     fifty -- the same reason portall dedupes a finger resting on the glass. */
  for (uint16_t i = 0; i < 6; i++) {
    const uint8_t code = now[i];
    if (code == 0 || code == 1)  // 1 is ErrorRollOver, not a key
      continue;
    bool was_held = false;
    for (uint16_t j = 0; j < 6; j++)
      was_held = was_held || this->held_[j] == code;
    if (was_held)
      continue;
    const char *name = usage_name(code);
    ESP_LOGI(TAG, "keyboard: usage %#04x%s%s", code, name[0] != '\0' ? " -- " : "",
             name);
    this->key_sink_(PAGE_KEYBOARD, code);
  }
  memcpy(this->held_, now, 6);
}

void PortallBT::feed_hid_descriptor(const uint8_t *desc, uint16_t len, uint16_t vendor,
                                    uint16_t product) {
  /* Every arrival resets what is REMEMBERED about the device's buttons -- a
     controller that hung up and came back is not still holding whatever it
     was holding, and a stale hat position would swallow the first press. */
  this->pad_dpad_ = 0xFF;
  this->pad_buttons_ = 0;
  this->pad_said_ = 0;
  this->consumer_held_ = 0;
  memset(this->pad_axis_, 0, sizeof(this->pad_axis_));
  memset(this->pad_axis_dir_, 0, sizeof(this->pad_axis_dir_));
  /* Not live until centred again: a controller that has just come back has
     not told this panel where its sticks are. */
  this->pad_axis_live_ = 0;
  this->said_no_descriptor_ = false;
  memset(this->held_, 0, sizeof(this->held_));

  /* THE SAME DESCRIPTOR AGAIN IS NOT NEWS, and on this hardware it is the
     common case: a panel's own log shows a Shield reconnecting every eleven
     seconds, which would otherwise re-parse 379 bytes and print the same two
     lines each time, burying whatever else was being looked for. */
  uint32_t sum = 0;
  for (uint16_t i = 0; i < len; i++)
    sum = sum * 31u + desc[i];
  if (len != 0 && len == this->desc_seen_len_ && sum == this->desc_seen_sum_ &&
      this->hid_map_.ready())
    return;
  this->desc_seen_len_ = len;
  this->desc_seen_sum_ = sum;

  this->hid_map_.clear();
  if (!this->hid_map_.parse(desc, len)) {
    ESP_LOGW(TAG,
             "input device %04x:%04x sent a %u-byte report descriptor this "
             "could not read, so its buttons reach on_hid_report and go no "
             "further",
             (unsigned) vendor, (unsigned) product, (unsigned) len);
    this->say_descriptor_(desc, len);
    return;
  }
  if (this->hid_map_.truncated()) {
    /* Named with BOTH numbers, because a limit that only says it was reached
       leaves the next person guessing at what to raise it to -- which is
       exactly what the line this replaces did, at the cost of a round. */
    ESP_LOGW(TAG,
             "input device %04x:%04x described itself in %u bytes and %u "
             "fields, of which only %u fit -- buttons past that one are not "
             "decoded",
             (unsigned) vendor, (unsigned) product, (unsigned) len,
             (unsigned) this->hid_map_.wanted_fields(),
             (unsigned) this->hid_map_.field_count());
  } else {
    ESP_LOGI(TAG, "input device %04x:%04x described itself: %u bytes, %u fields",
             (unsigned) vendor, (unsigned) product, (unsigned) len,
             (unsigned) this->hid_map_.field_count());
  }
  this->say_descriptor_(desc, len);
}

void PortallBT::say_descriptor_(const uint8_t *desc, uint16_t len) {
  /* THE WHOLE DESCRIPTOR, under `show_reports:`, and the reason it is all of
   * it rather than a taste is that a descriptor is only useful entire: it is
   * a stream of items where every one shifts the meaning of what follows, so
   * the first thirty-two bytes of it say nothing at all about where a button
   * is. A previous version printed thirty-two, which was decoration.
   *
   * This is also the one thing this repository has never had. Every gamepad
   * round so far has been worked from a measurement of reports, because the
   * descriptor those reports are laid out by was never in anybody's hands --
   * it goes straight from Bluedroid into the parse. One flash with this on
   * and it is a fixture, and then a controller nobody here owns can be
   * decoded on a workstation. */
  if (!this->show_reports_ || len == 0)
    return;
  char line[3 * 16 + 1];
  for (uint16_t at = 0; at < len; at += 16) {
    size_t put = 0;
    for (uint16_t i = at; i < len && i < (uint16_t)(at + 16); i++)
      put += (size_t) snprintf(line + put, sizeof(line) - put, "%02x ", desc[i]);
    line[put] = '\0';
    ESP_LOGI(TAG, "  descriptor %3u: %s", (unsigned) at, line);
  }
}

void PortallBT::feed_hid_usage(uint16_t page, uint16_t usage, int32_t value,
                               int32_t logical_min, int32_t logical_max) {
  if (!this->key_sink_)
    return;

  if (page == PAGE_DESKTOP && usage == DESKTOP_HAT) {
    /* The hat's own zero is its logical minimum, which is 0 on some devices
       and 1 on others, and anything outside the declared range is the null
       value meaning centred. Both of those come from the descriptor rather
       than from a table, which is the whole point of this path. */
    uint8_t hat = 0xFF;
    if (value >= logical_min && value <= logical_max)
      hat = (uint8_t)(value - logical_min);
    if (hat == this->pad_dpad_)
      return;
    this->pad_dpad_ = hat;
    const uint16_t key = hat_usage(hat);
    if (key == 0)
      return;
    ESP_LOGI(TAG, "gamepad: %s", usage_name(key));
    this->key_sink_(PAGE_KEYBOARD, key);
    return;
  }

  /* THE STICKS. Reported as an absolute position a hundred times a second,
     where every other input on this path is an edge -- so the edge has to be
     made here, from the crossing, or one push would walk the whole list.

     Everything about the arithmetic comes from the DESCRIPTOR: a stick that
     runs 0..255 and one that runs -32768..32767 are the same axis expressed
     twice, and the centre of either is its own declared midpoint rather than
     a number anybody typed. That is the same rule the hat above follows and
     the reason the byte offsets this file used to carry are gone. */
  const int idx = axis_index(page, usage);
  if (idx >= 0 && logical_max > logical_min) {
    const int32_t span = logical_max - logical_min;
    /* Twice the value less twice the centre, over the span: -100 at one end
       of the declared range, 0 at its middle, +100 at the other. */
    int32_t pct = ((int32_t) value * 2 - (logical_min + logical_max)) * 100 / span;
    if (pct > 100)
      pct = 100;
    if (pct < -100)
      pct = -100;
    const int32_t away = pct < 0 ? -pct : pct;
    const uint8_t live = (uint8_t)(1u << idx);

    if (away < STICK_RELEASE_PCT) {
      /* Home. This is also the ONLY place an axis earns the right to steer
         anything: a trigger declared on one of these usages sits at an end
         of its range for ever and therefore never reaches this line. */
      this->pad_axis_live_ |= live;
      this->pad_axis_dir_[idx] = 0;
      this->pad_axis_[idx] = (int8_t) pct;
      return;
    }

    const uint8_t want = (uint8_t)(pct < 0 ? 1 : 2);
    const bool crossing = (this->pad_axis_dir_[idx] != want) &&
                          (this->pad_axis_live_ & live) != 0 &&
                          away >= STICK_PUSH_PCT;
    /* The other half of this stick, so a diagonal moves along ONE axis
       rather than both. A grid of tiles has no diagonal, and firing both
       would move twice for one push.

       What this is exactly, rather than what it would be nice to claim: the
       partner's figure comes from this same report where that axis was
       decoded first, and from the report before it otherwise. That is enough
       because a thumb does not teleport -- it crosses the threshold on one
       axis while the other is already on its way, so by the crossing report
       the two are comparable. What it does NOT do is hold for a stick
       slammed from dead centre to a perfect forty-five degrees between two
       reports, where whichever axis the descriptor lists first wins by
       having seen the other still centred. No thumb produces that, and
       reaching for it would mean holding every axis back until the end of a
       report for a case that does not occur. */
    const int partner = idx ^ 1;
    const int32_t other = this->pad_axis_[partner] < 0 ? -this->pad_axis_[partner]
                                                       : this->pad_axis_[partner];
    this->pad_axis_[idx] = (int8_t) pct;
    if (!crossing)
      return;
    this->pad_axis_dir_[idx] = want;
    if (away <= other)
      return;
    /* HID counts Y downwards, so a stick pushed away from the person is
       NEGATIVE on its vertical axis. Getting this backwards is the kind of
       fault that reads as the panel being possessed rather than as a sign
       being wrong, which is why it is written down. */
    const bool vertical = (idx == 1 || idx == 3);
    uint16_t key;
    if (vertical)
      key = pct < 0 ? KEY_UP : KEY_DOWN;
    else
      key = pct < 0 ? KEY_LEFT : KEY_RIGHT;
    ESP_LOGI(TAG, "gamepad: %s (stick at %d%%)", usage_name(key), (int) pct);
    this->key_sink_(PAGE_KEYBOARD, key);
    return;
  }

  /* Some controllers declare four separate d-pad bits instead of a hat. One
     bit each, so each is its own edge. */
  if (page == PAGE_DESKTOP && usage >= DESKTOP_DPAD_UP && usage <= DESKTOP_DPAD_LEFT) {
    const uint8_t bit = (uint8_t)(usage - DESKTOP_DPAD_UP);
    const uint8_t mask = (uint8_t)(1u << bit);
    const bool down = value != 0;
    const bool was = (this->consumer_held_ & mask) != 0;
    if (down == was)
      return;
    this->consumer_held_ = (uint8_t)(down ? (this->consumer_held_ | mask)
                                          : (this->consumer_held_ & ~mask));
    if (!down)
      return;
    uint16_t key = 0;
    switch (usage) {
      case DESKTOP_DPAD_UP: key = KEY_UP; break;
      case DESKTOP_DPAD_DOWN: key = KEY_DOWN; break;
      case DESKTOP_DPAD_RIGHT: key = KEY_RIGHT; break;
      default: key = KEY_LEFT; break;
    }
    ESP_LOGI(TAG, "gamepad: %s", usage_name(key));
    this->key_sink_(PAGE_KEYBOARD, key);
    return;
  }

  if (page == PAGE_BUTTON && usage >= 1 && usage <= 32) {
    const uint32_t mask = (uint32_t) 1 << (usage - 1);
    const bool down = value != 0;
    const bool was = (this->pad_buttons_ & mask) != 0;
    if (down == was)
      return;
    this->pad_buttons_ = down ? (this->pad_buttons_ | mask) : (this->pad_buttons_ & ~mask);
    if (!down)
      return;
    if (usage == BUTTON_A) {
      ESP_LOGI(TAG, "gamepad: A -- ok");
      this->key_sink_(PAGE_KEYBOARD, KEY_ENTER);
      return;
    }
    if (usage == BUTTON_B) {
      ESP_LOGI(TAG, "gamepad: B -- back");
      this->key_sink_(PAGE_KEYBOARD, KEY_ESCAPE);
      return;
    }
    /* EVERY OTHER BUTTON NAMES ITSELF, ONCE. X, Y, the shoulders and the
       stick clicks have no agreed meaning in a page, so an unmapped one says
       which usage it is the first time it is pressed and never again --
       enough to map it from one line of somebody's log, and cheap enough that
       a controller with sixteen buttons costs sixteen lines in total. */
    if ((this->pad_said_ & mask) == 0) {
      this->pad_said_ |= mask;
      ESP_LOGI(TAG,
               "gamepad: button %u is pressed and has no meaning in a page -- "
               "say which button that is and it can be given one",
               (unsigned) usage);
    }
    return;
  }

  if (page == PAGE_CONSUMER && (usage == CONSUMER_HOME || usage == CONSUMER_BACK)) {
    const uint8_t mask = (uint8_t)(usage == CONSUMER_HOME ? 0x10 : 0x20);
    const bool down = value != 0;
    const bool was = (this->consumer_held_ & mask) != 0;
    if (down == was)
      return;
    this->consumer_held_ = (uint8_t)(down ? (this->consumer_held_ | mask)
                                          : (this->consumer_held_ & ~mask));
    if (!down)
      return;
    if (usage == CONSUMER_BACK) {
      ESP_LOGI(TAG, "gamepad: back");
      this->key_sink_(PAGE_KEYBOARD, KEY_ESCAPE);
      return;
    }
    /* Home LEAVES the link, which is the one thing a remote most needs and
       the one thing no button could do two releases ago. Same sink AVRCP's
       Menu uses, for the same reason: there is one notion of home. */
    if (this->home_sink_) {
      ESP_LOGI(TAG, "gamepad: home -- back to this panel's own page");
      this->home_sink_();
    }
    return;
  }
}

void PortallBT::say_unreadable_report_(const uint8_t *data, uint16_t len) {
  /* The fault this was written for, in the user's own words: "j'ai appuye sur
   * toutes les touches de la manette rien ne fonctionne". A Shield was paired,
   * connected, and sending reports -- and every one of them was dropped here
   * without a word, so a panel doing nothing and a panel receiving nothing
   * read exactly alike. That is this repository's most-recorded fault shape,
   * and it had reached the newest code.
   *
   * Once, with the bytes, because a device that sends something unreadable
   * sends it for ever and a thumbstick sends it a hundred times a second. */
  if (len == 0)
    return;
  /* ONE LINE PER SHAPE. The first version of this said one line in TOTAL, and
     that was wrong for the one button anybody would go looking for: the
     Shield carries its Home button on a different report from its sticks, so
     whichever arrived first would have spent the only line and Home would
     have stayed as silent as before. See the note in the header. */
  const uint16_t shape = (uint16_t) ((len << 8) | data[0]);
  for (uint8_t i = 0; i < this->said_shape_count_; i++)
    if (this->said_shapes_[i] == shape)
      return;
  if (this->said_shape_count_ >= SHAPES)
    return;
  this->said_shapes_[this->said_shape_count_++] = shape;

  char hex[3 * 12 + 1];
  size_t at = 0;
  const uint16_t show = len < 12 ? len : 12;
  for (uint16_t i = 0; i < show && at + 3 < sizeof(hex); i++)
    at += (size_t) snprintf(hex + at, sizeof(hex) - at, "%02x ", data[i]);
  hex[at] = '\0';
  ESP_LOGI(TAG,
           "a report this cannot read: %u bytes, id %#04x, starting %s-- its "
           "buttons reach on_hid_report and go no further. Send this line and "
           "say which button you pressed.",
           (unsigned) len, (unsigned) data[0], hex);
}

}  // namespace portall_bt
}  // namespace esphome
