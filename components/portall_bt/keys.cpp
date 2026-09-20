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
 * A GAMEPAD is here now, and the same rule held. This file used to say a
 * gamepad "cannot be" covered because its layout is only knowable from its
 * own report descriptor -- true, and it stopped being the obstacle the moment
 * somebody read one off a real device. The layout below is the USER'S OWN,
 * measured from an NVIDIA Shield controller against this component's
 * `show_reports:`. It reached this file by way of a `universal_hid.h` they
 * wrote and this repository carried, included by nothing, while a panel
 * reported that no button did anything; that file is gone now, because two
 * copies of one mapping drift the moment anybody adds a device.
 *
 * And it corroborates itself, which is what made it safe to take. Their hat
 * values -- 0 up, 2 right, 4 down, 6 left, 8 at rest -- are not arbitrary:
 * they are HID's own Hat Switch encoding, eight compass points clockwise from
 * north with one past the last meaning centred. So this is the specification
 * arriving by way of a measurement rather than one device's quirk, and a
 * second gamepad laying its hat out the same way is the normal case.
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
/* The NVIDIA Shield controller's report, measured by the user against a real
   one. 33 bytes behind report id 0x01; the hat in the HIGH nibble of byte 2,
   the face buttons in byte 3. The id AND the length are both checked, which
   is a far tighter test than the boot-keyboard one -- a report that is 33
   bytes long and announces itself as id 1 is not something another kind of
   device produces by accident. */
constexpr uint16_t PAD_LEN = 33;
constexpr uint8_t PAD_REPORT_ID = 0x01;
constexpr uint16_t PAD_HAT_BYTE = 2;
constexpr uint16_t PAD_BUTTON_BYTE = 3;
constexpr uint8_t PAD_A = 0x01;
constexpr uint8_t PAD_B = 0x02;

bool looks_like_shield_pad(const uint8_t *data, uint16_t len) {
  return len == PAD_LEN && data[0] == PAD_REPORT_ID;
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
    if (!this->said_unreadable_) {
      this->said_unreadable_ = true;
      ESP_LOGI(TAG, "this device is sending buttons, but 'keys:' is not set on "
                    "portall_bt, so nothing is decoded and nothing reaches the "
                    "page");
    }
    return;
  }
  uint16_t at = 0;
  if (!looks_like_keyboard(data, len, &at)) {
    if (looks_like_shield_pad(data, len)) {
      this->feed_pad_report(data, len);
      return;
    }
    this->say_unreadable_report_(data, len);
    return;
  }

  uint8_t now[6] = {};
  const uint16_t count = (uint16_t) (len - at) < 6 ? (uint16_t) (len - at) : 6;
  memcpy(now, data + at, count);

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
  memcpy(this->held_, now, sizeof(this->held_));
}

void PortallBT::feed_pad_report(const uint8_t *data, uint16_t len) {
  /* The hat, which is what drives a grid of tiles.
   *
   * HID's Hat Switch runs clockwise from north, so the four this panel can
   * use are the even values and the odd ones are diagonals. A diagonal sends
   * NOTHING: a grid of tiles has no diagonal, and picking one of the two
   * axes for the caller would be exactly the invention this file exists to
   * keep out. A cleaner press is the answer, and it is the one the user's own
   * notes reached independently -- they listed four directions, not eight. */
  const uint8_t hat = (uint8_t) ((data[PAD_HAT_BYTE] >> 4) & 0x0F);
  if (hat != this->pad_hat_) {
    this->pad_hat_ = hat;
    uint16_t usage = 0;
    switch (hat) {
      case 0: usage = KEY_UP; break;
      case 2: usage = KEY_RIGHT; break;
      case 4: usage = KEY_DOWN; break;
      case 6: usage = KEY_LEFT; break;
      default: break;  // a diagonal, or 8 for the hat coming back to rest
    }
    if (usage != 0) {
      ESP_LOGI(TAG, "gamepad: %s", usage_name(usage));
      this->key_sink_(PAGE_KEYBOARD, usage);
    }
  }

  /* The buttons, edge-triggered against the last report for the reason the
     keyboard path already documents: a thumb held down is in every report. */
  const uint8_t buttons = data[PAD_BUTTON_BYTE];
  const uint8_t pressed = (uint8_t) (buttons & ~this->pad_buttons_);
  this->pad_buttons_ = buttons;

  if ((pressed & PAD_A) != 0) {
    ESP_LOGI(TAG, "gamepad: A -- ok");
    this->key_sink_(PAGE_KEYBOARD, KEY_ENTER);
  }
  if ((pressed & PAD_B) != 0) {
    ESP_LOGI(TAG, "gamepad: B -- back");
    this->key_sink_(PAGE_KEYBOARD, KEY_ESCAPE);
  }

  /* EVERY OTHER BUTTON NAMES ITSELF, ONCE, AND THAT IS THE POINT OF IT.
   *
   * X, Y and whatever else this byte carries have no agreed meaning in a page
   * -- but the button somebody actually wants next is the gamepad's HOME, and
   * nothing here knows which bit that is. Their notes cover A, B, X and Y and
   * stop there, so guessing would be the recipe-dressed-as-a-guess this file
   * has paid for before.
   *
   * So an unmapped bit says which bit it is, the first time it is pressed and
   * never again. One press of Home in a log is then the whole of what is
   * needed to map it -- against a round trip that would otherwise start with
   * "please turn show_reports on". */
  const uint8_t unnamed = (uint8_t) (pressed & ~(PAD_A | PAD_B) & ~this->pad_said_);
  if (unnamed != 0) {
    this->pad_said_ = (uint8_t) (this->pad_said_ | unnamed);
    for (uint8_t bit = 0; bit < 8; bit++) {
      if ((unnamed & (1 << bit)) == 0)
        continue;
      ESP_LOGI(TAG,
               "gamepad: button bit %u of byte %u is pressed and is not mapped "
               "-- say which button that is and it can be",
               (unsigned) bit, (unsigned) PAD_BUTTON_BYTE);
    }
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
  if (this->said_unreadable_)
    return;
  this->said_unreadable_ = true;
  char hex[3 * 8 + 1];
  size_t at = 0;
  const uint16_t show = len < 8 ? len : 8;
  for (uint16_t i = 0; i < show && at + 3 < sizeof(hex); i++)
    at += (size_t) snprintf(hex + at, sizeof(hex) - at, "%02x ", data[i]);
  hex[at] = '\0';
  ESP_LOGI(TAG,
           "this device's reports are %u bytes and start %s-- not a keyboard "
           "and not a layout this knows, so its buttons reach on_hid_report "
           "and go no further. Turn show_reports on and send the bytes.",
           (unsigned) len, hex);
}

}  // namespace portall_bt
}  // namespace esphome
