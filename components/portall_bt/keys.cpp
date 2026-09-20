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
 * A GAMEPAD is not here and cannot be: its report layout differs per device
 * and is only knowable from its own report descriptor, which is a separate
 * piece of work.
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
  if (!this->key_sink_)
    return;
  uint16_t at = 0;
  if (!looks_like_keyboard(data, len, &at))
    return;

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

}  // namespace portall_bt
}  // namespace esphome
