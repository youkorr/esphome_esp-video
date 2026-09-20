/* Bluetooth Classic HID host, and the memory that makes it usable.
 *
 * A gamepad and a remote control are the SAME feature, which is worth saying
 * once: both are Classic HID devices, both arrive here as input reports over
 * ACL, and the only difference is which buttons somebody presses. So this file
 * is the answer to a panel that wants a gamepad AND the answer to one that
 * wants arrow keys for a television interface, and neither has to wait for the
 * other.
 *
 * WHAT IS AND IS NOT DONE. This carries reports; it does not interpret them. A
 * HID report descriptor says which bit is which button and every device's is
 * different -- a guess at one, written with no device to try it against, would
 * be a mapping nobody could trust. So `on_hid_report` hands the bytes to the
 * YAML, `show_reports:` prints them, and the mapping is written afterwards with
 * a real device's own log in hand. That is the shape this project keeps
 * arriving at: report the boring thing first, decide afterwards.
 *
 * THE MEMORY, which was asked for in as many words, is in two halves and only
 * one of them is ours.
 *
 * Bluedroid keeps the LINK KEYS -- the cryptography of a pairing -- in NVS by
 * itself. Nothing here writes them, and they survive a restart untouched. What
 * it does not keep is which of the bonded addresses is a gamepad and which is
 * a speaker: `esp_bt_gap_get_bond_device_list` returns addresses and nothing
 * else. So the second half is one small record of our own, in ESPHome's
 * preferences, saying which address plays which part. Losing it costs a
 * reconnection; losing Bluedroid's costs the pairing.
 *
 * AND THE THIRD HALF, which is neither store: somebody has to ASK. Bluedroid
 * does not reconnect a bonded device on its own. On startup this reads its own
 * record and calls esp_bt_hid_host_connect, then again on a backoff while the
 * device is away -- and that is what makes the whole thing quiet, because a
 * connection by address needs NO INQUIRY. An inquiry sweeps the whole 2.4 GHz
 * band at full power and was measured, twice, taking this board's Wi-Fi down
 * for exactly as long as it ran. Remembering the device and not killing the
 * picture are therefore the same piece of work, which is the nicest thing
 * about this design and the reason `pair()` is an action somebody invokes
 * rather than anything that happens by itself.
 *
 * ONE CONFIGURATION TRAP, and it is Espressif's default rather than ours:
 * BT_HID_REMOVE_DEVICE_BONDING_ENABLED defaults to y, which throws the bonding
 * away when a device asks for a "virtual cable unplug". That is what the HID
 * specification asks for and it is also a device unpairing itself, which from
 * the sofa is a gamepad that has forgotten the panel. The schema turns it off,
 * and says so.
 */

#include "portall_bt.h"

#ifdef USE_ESP32

#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"

#include <cstring>

#ifdef CONFIG_BT_BLUEDROID_ENABLED
#include "esp_bt_defs.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#ifdef CONFIG_BT_HID_HOST_ENABLED
#include "esp_hidh_api.h"
#endif
#ifdef CONFIG_BT_A2DP_ENABLE
#include "esp_a2dp_api.h"
#endif
#endif

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt";

// How long to leave between attempts at a device that is not answering, and
// the ceiling it grows to. Two seconds is quick enough that switching a gamepad
// on feels immediate; a minute is slow enough that a gamepad left in a drawer
// costs nothing at all.
//
// OUTSIDE the Bluedroid guard on purpose: on_hid_ready and on_hid_closed are
// public and unguarded -- they are the C callbacks' way in -- so anything they
// touch has to exist in every configuration. Putting these behind the guard
// compiled two ways out of three and failed the third, which is exactly what
// tools/checkbt.py's `host_stack: none` pass is for.
static constexpr uint32_t RECONNECT_FIRST_MS = 2000;
static constexpr uint32_t RECONNECT_MAX_MS = 60000;

static uint32_t now_ms_() { return millis(); }

static void say_addr(char *out, const uint8_t *addr) {
  snprintf(out, 18, "%02X:%02X:%02X:%02X:%02X:%02X", addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
}

static bool addr_set(const uint8_t *addr) {
  for (uint8_t i = 0; i < 6; i++) {
    if (addr[i] != 0)
      return true;
  }
  return false;
}

#ifdef CONFIG_BT_BLUEDROID_ENABLED

// The one instance, for callbacks that are plain C with no context argument.
// A second dongle would need a second USB host controller and a second
// Bluedroid, neither of which exists.
static PortallBT *g_bt = nullptr;

// ---------------------------------------------------------------------------
// GAP: pairing, and what comes back from an inquiry
// ---------------------------------------------------------------------------

static void gap_cb(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param) {
  if (g_bt == nullptr)
    return;
  char addr[18];

  switch (event) {
    case ESP_BT_GAP_DISC_RES_EVT: {
      // Only ever reached during pair(), because nothing else here inquires.
      uint32_t cod = 0;
      const char *name = nullptr;
      for (int i = 0; i < param->disc_res.num_prop; i++) {
        const esp_bt_gap_dev_prop_t &prop = param->disc_res.prop[i];
        if (prop.type == ESP_BT_GAP_DEV_PROP_COD && prop.len >= (int) sizeof(uint32_t))
          cod = *(uint32_t *) prop.val;
        else if (prop.type == ESP_BT_GAP_DEV_PROP_BDNAME)
          name = (const char *) prop.val;
      }
      say_addr(addr, param->disc_res.bda);
      const uint32_t major = esp_bt_gap_get_cod_major_dev(cod);
      // Bits 2-7 of the Class of Device, whose meaning depends on the major
      // class above. Within AUDIO/VIDEO, 0x12 is Gaming/Toy -- a gamepad
      // with a headphone jack, not a speaker.
      const uint32_t minor = (cod >> 2) & 0x3F;
      g_bt->note_heard();
      ESP_LOGI(TAG, "  heard %s  class %06X  major %u minor %u%s%s", addr, (unsigned) cod,
               (unsigned) major, (unsigned) minor, name != nullptr ? "  " : "",
               name != nullptr ? name : "");
      // PERIPHERAL is tested FIRST. A gamepad, a keyboard, a mouse or a
      // remote all say PERIPHERAL, and that answer is unambiguous -- so it
      // takes priority over the AUDIO/VIDEO check below. Putting audio first
      // is what let a controller with a headphone jack (NVIDIA Shield, among
      // others) be mistaken for a speaker: it reports AUDIO/VIDEO because of
      // the jack, and `wants_speaker()` was tested before the minor class
      // could say otherwise.
      if (major == ESP_BT_COD_MAJOR_DEV_PERIPHERAL && g_bt->wants_input()) {
        ESP_LOGI(TAG, "  that is an input device -- stopping the scan and pairing with it");
        esp_bt_gap_cancel_discovery();
#ifdef CONFIG_BT_HID_HOST_ENABLED
        esp_bt_hid_host_connect(param->disc_res.bda);
#endif
      } else if (major == ESP_BT_COD_MAJOR_DEV_AV && minor == 0x12 && g_bt->wants_input()) {
        // Gaming/Toy within Audio/Video: a gamepad that reports audio
        // capabilities because it carries a headphone jack or a microphone.
        // The NVIDIA Shield controller is one. Pairing it via A2DP makes the
        // panel stream audio to a thumbstick and ignore its buttons, which is
        // the fault this block exists to prevent.
        ESP_LOGI(TAG, "  that is a gaming device (A/V class, minor 0x12) -- pairing as input");
        esp_bt_gap_cancel_discovery();
#ifdef CONFIG_BT_HID_HOST_ENABLED
        esp_bt_hid_host_connect(param->disc_res.bda);
#endif
      } else if (major == ESP_BT_COD_MAJOR_DEV_AV && g_bt->wants_speaker()) {
        ESP_LOGI(TAG, "  that is a speaker -- stopping the scan and pairing with it");
        esp_bt_gap_cancel_discovery();
#ifdef CONFIG_BT_A2DP_ENABLE
        esp_a2d_source_connect(param->disc_res.bda);
#endif
      } else if (major == ESP_BT_COD_MAJOR_DEV_PERIPHERAL || major == ESP_BT_COD_MAJOR_DEV_AV) {
        /* Heard, recognised, and passed over -- which without this line is
         * indistinguishable from not being heard at all.
         *
         * `audio:` and `hid:` both default to FALSE, and they are what
         * wants_speaker() and wants_input() return. So a panel built with one
         * of them on skips every device of the other kind IN SILENCE: the
         * device appears on the line above, nothing connects, and the scan
         * ends saying it heard something. That silence is the other half of
         * the mis-detection above -- with `hid:` off, a controller is skipped
         * whatever its class says, and the speaker beside it is taken. */
        const bool input_kind =
            major == ESP_BT_COD_MAJOR_DEV_PERIPHERAL || minor == 0x12;
        ESP_LOGW(TAG, "  that is %s, and this panel has `%s: true` off -- skipping it",
                 input_kind ? "an input device (gamepad, keyboard, mouse, remote)" : "a speaker",
                 input_kind ? "hid" : "audio");
      }
      break;
    }

    case ESP_BT_GAP_DISC_STATE_CHANGED_EVT:
      if (param->disc_st_chg.state == ESP_BT_GAP_DISCOVERY_STOPPED) {
        ESP_LOGI(TAG, "scan finished, %u device(s) heard; the Wi-Fi should come back now",
                 (unsigned) g_bt->heard());
        // A count of zero used to read exactly like a scan that never started,
        // and the two need different next steps: nothing heard is a device not
        // in pairing mode, or one still connected somewhere else.
        if (g_bt->heard() == 0)
          ESP_LOGW(TAG, "  nothing answered. Put the device in PAIRING mode -- a speaker already "
                        "connected to a telephone or a car will not answer a scan.");
        g_bt->resume_reconnect();
        g_bt->say_pairing_later();
      }
      break;

    case ESP_BT_GAP_AUTH_CMPL_EVT:
      say_addr(addr, param->auth_cmpl.bda);
      if (param->auth_cmpl.stat == ESP_BT_STATUS_SUCCESS) {
        ESP_LOGI(TAG, "paired with %s \"%s\" -- Bluedroid has the link key in NVS now", addr,
                 (const char *) param->auth_cmpl.device_name);
      } else {
        ESP_LOGW(TAG, "pairing with %s failed (status %d)", addr, (int) param->auth_cmpl.stat);
      }
      break;

    case ESP_BT_GAP_PIN_REQ_EVT: {
      // A device old enough to want a PIN rather than Secure Simple Pairing.
      // 0000 is what every gamepad and remote that asks for one uses, and a
      // panel has no keyboard to type another with.
      say_addr(addr, param->pin_req.bda);
      ESP_LOGI(TAG, "%s asked for a PIN; answering 0000", addr);
      esp_bt_pin_code_t pin = {'0', '0', '0', '0'};
      esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pin);
      break;
    }

    case ESP_BT_GAP_CFM_REQ_EVT:
      // Secure Simple Pairing wants a yes. With no screen and no buttons this
      // panel declares NoInputNoOutput, so the comparison is not shown to
      // anybody and accepting is the whole of the protocol's "just works".
      esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
      break;

    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// HID host
// ---------------------------------------------------------------------------

#ifdef CONFIG_BT_HID_HOST_ENABLED

static void hid_cb(esp_hidh_cb_event_t event, esp_hidh_cb_param_t *param) {
  if (g_bt == nullptr)
    return;
  switch (event) {
    case ESP_HIDH_INIT_EVT:
      ESP_LOGI(TAG, "HID host ready");
      g_bt->on_hid_ready();
      break;
    case ESP_HIDH_OPEN_EVT:
      if (param->open.status == ESP_HIDH_OK) {
        g_bt->on_hid_open(param->open.bd_addr);
      } else {
        g_bt->on_hid_closed();
      }
      break;
    case ESP_HIDH_CLOSE_EVT:
      g_bt->on_hid_closed();
      break;
    case ESP_HIDH_DATA_IND_EVT:
      g_bt->on_hid_report(param->data_ind.data, param->data_ind.len);
      break;
    /* THE DEVICE SAYING WHERE ITS OWN BUTTONS ARE.
     *
     * Bluedroid reads the report descriptor out of the device's SDP record
     * and hands it over whole, with the vendor and product ids beside it. It
     * was going unread for the whole life of this component, while keys.cpp
     * carried byte offsets somebody had measured off one controller -- and a
     * panel proved two of those three wrong. This event is the answer to
     * "handle every device", and it was already arriving. */
    case ESP_HIDH_GET_DSCP_EVT:
      g_bt->on_hid_descriptor(param->dscp.dsc_list, param->dscp.dl_len,
                              param->dscp.vendor_id, param->dscp.product_id);
      break;
    default:
      break;
  }
}

#endif  // CONFIG_BT_HID_HOST_ENABLED
#endif  // CONFIG_BT_BLUEDROID_ENABLED

// ---------------------------------------------------------------------------
// The component's own half
// ---------------------------------------------------------------------------

void PortallBT::load_remembered_() {
  this->remembered_pref_ = global_preferences->make_preference<Remembered>(fnv1_hash("portall_bt_devices"));
  if (!this->remembered_pref_.load(&this->remembered_)) {
    this->remembered_ = Remembered{};
  }
}

void PortallBT::remember_hid_(const uint8_t *addr) {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (this->remembered_.has_hid && memcmp(this->remembered_.hid, addr, 6) == 0)
    return;  // Already ours; writing it again would spend a flash erase.
  memcpy(this->remembered_.hid, addr, 6);
  this->remembered_.has_hid = true;
  this->remembered_pref_.save(&this->remembered_);
  char text[18];
  say_addr(text, addr);
  ESP_LOGI(TAG, "remembering %s as this panel's input device", text);
#else
  (void) addr;
#endif
}

void PortallBT::start_profiles_() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (this->profiles_up_)
    return;
  g_bt = this;
  this->load_remembered_();

  esp_bt_gap_register_callback(gap_cb);
  esp_bt_gap_set_device_name(this->device_name_);

  // NoInputNoOutput, because that is the truth: this panel has no keypad to
  // type a passkey on and nowhere to show one. Claiming otherwise makes a
  // remote device ask a question nobody can answer.
  esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
  esp_bt_gap_set_security_param(ESP_BT_SP_IOCAP_MODE, &iocap, sizeof(iocap));

  // CONNECTABLE and NOT discoverable, and both halves are deliberate. A gamepad
  // that has been paired pages the host itself when somebody presses its
  // button, so the panel has to be reachable -- that is the connectable half,
  // and it is free. Discoverable is the half that costs: it puts this board in
  // every phone's Bluetooth list for ever, for the sake of a pairing that
  // happens once. pair() is where that is turned on, for as long as it runs.
  esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_NON_DISCOVERABLE);

  const int bonded = esp_bt_gap_get_bond_device_num();
  ESP_LOGI(TAG, "Bluedroid remembers %d paired device(s) from NVS", bonded);
  if (bonded > 0) {
    esp_bd_addr_t list[8];
    int count = bonded < 8 ? bonded : 8;
    if (esp_bt_gap_get_bond_device_list(&count, list) == ESP_OK) {
      char text[18];
      for (int i = 0; i < count; i++) {
        say_addr(text, list[i]);
        const bool ours = this->remembered_.has_hid && memcmp(this->remembered_.hid, list[i], 6) == 0;
        ESP_LOGI(TAG, "  %s%s", text, ours ? "  (this panel's input device)" : "");
      }
    }
  }

#ifdef CONFIG_BT_HID_HOST_ENABLED
  if (this->hid_host_) {
    esp_bt_hid_host_register_callback(hid_cb);
    const esp_err_t err = esp_bt_hid_host_init();
    if (err != ESP_OK)
      ESP_LOGW(TAG, "HID host would not start (%d); the stack is up but nothing will pair", (int) err);
  }
#else
  if (this->hid_host_)
    ESP_LOGE(TAG, "hid: true was asked for and CONFIG_BT_HID_HOST_ENABLED is not set");
#endif

  this->start_a2dp_();

  this->profiles_up_ = true;
#endif
}

void PortallBT::on_hid_ready() {
  // Only now is there anything to connect WITH. Asking before the profile has
  // initialised is the same mistake as reading an endpoint before the reader
  // task exists, which this component already paid for once.
  this->reconnect_due_ms_ = now_ms_();
  this->reconnect_backoff_ms_ = RECONNECT_FIRST_MS;
}

void PortallBT::on_hid_open(const uint8_t *addr) {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  char text[18];
  say_addr(text, addr);
  ESP_LOGI(TAG, "input device %s is connected", text);
  this->hid_open_ = true;
  memcpy(this->open_hid_, addr, 6);
  this->reconnect_backoff_ms_ = RECONNECT_FIRST_MS;
  this->remember_hid_(addr);
#else
  (void) addr;
#endif
}

void PortallBT::on_hid_closed() {
  if (this->hid_open_)
    ESP_LOGI(TAG, "input device disconnected; it will be asked for again by address, with no scan");
  this->hid_open_ = false;
  memset(this->open_hid_, 0, 6);
  // Straight back to the short interval: a device that has just been switched
  // off is the one most likely to be switched on again in a moment.
  this->reconnect_backoff_ms_ = RECONNECT_FIRST_MS;
  this->reconnect_due_ms_ = now_ms_() + RECONNECT_FIRST_MS;
}

void PortallBT::on_hid_report(const uint8_t *data, uint16_t len) {
  const uint8_t next = (uint8_t) ((this->report_head_ + 1) % REPORTS);
  if (next == this->report_tail_) {
    // Full. Drop the OLDEST -- see the queue's own comment in the header: the
    // last report of a press is the release, and a release that never arrives
    // leaves a button held down for ever.
    this->report_tail_ = (uint8_t) ((this->report_tail_ + 1) % REPORTS);
    this->reports_lost_++;
  }
  HidReport &slot = this->reports_[this->report_head_];
  slot.len = (uint8_t) (len > sizeof(slot.data) ? sizeof(slot.data) : len);
  memcpy(slot.data, data, slot.len);
  this->report_head_ = next;
}

void PortallBT::on_hid_descriptor(const uint8_t *desc, uint16_t len, uint16_t vendor,
                                  uint16_t product) {
  /* Copied here and parsed in loop(), because this runs on Bluedroid's task
     and the parsed map is read on ESPHome's. Nothing is logged from here for
     the same reason. */
  if (desc == nullptr || len == 0)
    return;
  this->pending_desc_len_ = len > MAX_DESC ? MAX_DESC : len;
  memcpy(this->pending_desc_, desc, this->pending_desc_len_);
  this->pending_desc_vendor_ = vendor;
  this->pending_desc_product_ = product;
  this->desc_pending_ = true;
}

void PortallBT::drain_reports_() {
  if (this->desc_pending_) {
    this->desc_pending_ = false;
    this->feed_hid_descriptor(this->pending_desc_, this->pending_desc_len_,
                              this->pending_desc_vendor_, this->pending_desc_product_);
  }
  while (this->report_tail_ != this->report_head_) {
    const HidReport &slot = this->reports_[this->report_tail_];
    std::vector<uint8_t> bytes(slot.data, slot.data + slot.len);
    if (this->show_reports_) {
      /* What every mapping has to be written against, and there is no way to
       * guess it: a report descriptor differs per device, so the bytes are
       * the specification.
       *
       * ONLY WHEN THEY CHANGE. A controller at rest repeats one report about
       * a hundred times a second, so printing every one buries the handful of
       * lines somebody is hunting for under thousands that say nothing -- and
       * this option exists precisely for somebody who is hunting. A change IS
       * the press, so the filter and the question are the same thing. */
      const bool same = slot.len == this->last_shown_len_ &&
                        memcmp(slot.data, this->last_shown_, slot.len) == 0;
      if (same) {
        if (this->shown_repeats_ < 0xFFFF)
          this->shown_repeats_++;
      } else {
        char hex[3 * sizeof(slot.data) + 1];
        size_t at = 0;
        for (uint8_t i = 0; i < slot.len && at + 3 < sizeof(hex); i++)
          at += (size_t) snprintf(hex + at, sizeof(hex) - at, "%02x ", slot.data[i]);
        hex[at] = '\0';

        /* And WHICH bytes moved, because that is the answer rather than the
           raw material for it: comparing two 33-byte lines by eye is what
           this is here to save. A press usually moves one byte, and that byte
           with its bits is the mapping. */
        char moved[96];
        size_t at2 = 0;
        moved[0] = '\0';
        if (this->last_shown_len_ == slot.len) {
          for (uint8_t i = 0; i < slot.len && at2 + 24 < sizeof(moved); i++) {
            if (slot.data[i] == this->last_shown_[i])
              continue;
            at2 += (size_t) snprintf(moved + at2, sizeof(moved) - at2,
                                     "%sbyte %u %02x->%02x", at2 == 0 ? "" : ", ",
                                     (unsigned) i, this->last_shown_[i], slot.data[i]);
          }
        }

        if (this->shown_repeats_ != 0) {
          ESP_LOGI(TAG, "  (the one before repeated %u times)",
                   (unsigned) this->shown_repeats_);
          this->shown_repeats_ = 0;
        }
        ESP_LOGI(TAG, "report, %u bytes: %s%s%s", (unsigned) slot.len, hex,
                 moved[0] != '\0' ? " -- changed: " : "", moved);
        memcpy(this->last_shown_, slot.data, slot.len);
        this->last_shown_len_ = slot.len;
      }
    }
    for (auto *trigger : this->hid_report_triggers_)
      trigger->trigger(bytes);
    /* A keyboard or a television remote speaking HID. keys.cpp decides
       whether these bytes are a boot-protocol keyboard report at all, and
       says out loud what it made of them; a gamepad is not covered and does
       not pretend to be. */
    this->feed_hid_keys(slot.data, slot.len);
    this->report_tail_ = (uint8_t) ((this->report_tail_ + 1) % REPORTS);
  }
  if (this->reports_lost_ != 0) {
    ESP_LOGW(TAG, "%u input report(s) dropped -- the loop is not keeping up with the device",
             (unsigned) this->reports_lost_);
    this->reports_lost_ = 0;
  }
}

void PortallBT::reconnect_tick_() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (!this->profiles_up_ || this->bt_off_)
    return;
  const uint32_t at = now_ms_();
  if ((int32_t) (at - this->reconnect_due_ms_) < 0 || this->reconnect_backoff_ms_ == 0)
    return;

  // Both profiles ride one clock, because both are the same act: a connection
  // BY ADDRESS, which runs no inquiry. Whichever of them is away gets asked.
  this->a2dp_reconnect_();
  this->hid_reconnect_();

  this->reconnect_backoff_ms_ = this->reconnect_backoff_ms_ * 2 > RECONNECT_MAX_MS
                                    ? RECONNECT_MAX_MS
                                    : this->reconnect_backoff_ms_ * 2;
  this->reconnect_due_ms_ = at + this->reconnect_backoff_ms_;
#endif
}

void PortallBT::hid_reconnect_() {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_HID_HOST_ENABLED)
  if (!this->hid_host_ || this->hid_open_ || !this->profiles_up_)
    return;
  if (!this->remembered_.has_hid || !addr_set(this->remembered_.hid))
    return;

  char text[18];
  say_addr(text, this->remembered_.hid);
  ESP_LOGD(TAG, "asking %s to connect (no scan, by address)", text);
  esp_bt_hid_host_connect(this->remembered_.hid);
#endif
}

void PortallBT::pair() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (!this->profiles_up_) {
    ESP_LOGW(TAG, "nothing to pair with yet -- no dongle has answered");
    return;
  }
  /* Refused rather than run, because a scan that pairs something and then
   * hangs it straight back up is worse than no scan: it costs the Wi-Fi for
   * ten seconds and leaves a bond nobody asked for. */
  if (this->bt_off_) {
    ESP_LOGW(TAG, "Bluetooth is switched off on this panel -- turn it on before pairing");
    return;
  }
  /* AN INQUIRY CANNOT FIND A DEVICE THAT IS ALREADY TALKING TO THIS PANEL,
   * and that is what "I can't pair any more" turns out to be.
   *
   * The first pairing works because nothing is remembered and nothing is
   * connected. Afterwards this component reconnects BY ADDRESS on a 2 s -> 60 s
   * clock, for ever, which is the whole design -- so by the time somebody
   * presses Pair again the speaker is connected to us, is therefore not
   * answering anybody's inquiry, and the scan hears silence. Reported from a
   * panel whose boot inquiry had heard that same speaker at -45 dBm six
   * seconds earlier, which is what rules out the radio and the distance.
   *
   * So pairing begins by getting out of the way: stop paging for the length of
   * the scan, and hang up what is already up. */
  this->reconnect_paused_ = true;
  this->reconnect_backoff_ms_ = 0;
  this->drop_links_();
  this->heard_ = 0;

  // Discoverable only while this runs, so the panel is not in every phone's
  // Bluetooth list for the rest of its life for the sake of one pairing.
  const esp_err_t mode = esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
  if (mode != ESP_OK)
    ESP_LOGW(TAG, "could not make this panel discoverable (%d); a device that needs to see it "
                  "will not", (int) mode);

  // Same conversion as the probe's own inquiry, and for the same reason: the
  // specification counts this in units of 1.28 seconds.
  uint8_t length = (uint8_t) ((this->pair_seconds_ * 100 + 127) / 128);
  if (length < 1)
    length = 1;
  if (length > 0x30)
    length = 0x30;

  ESP_LOGI(TAG, "scanning for about %u seconds -- put the device in pairing mode now",
           (unsigned) ((length * 128) / 100));
  ESP_LOGW(TAG, "  the Wi-Fi will drop while this runs. An inquiry sweeps the whole 2.4 GHz");
  ESP_LOGW(TAG, "  band, and the picture on this panel comes over that band. It comes back");
  ESP_LOGW(TAG, "  the moment the scan ends, and nothing after this ever scans again.");
  /* CHECKED, because the silent version of this is the fault this repository
   * records more often than any other -- and here it wore the worst costume of
   * all: the three encouraging lines above print, discovery never starts, and
   * the log then says NOTHING. Not even "scan finished", because that line
   * comes from an event the stack only sends if it began. From the outside a
   * refused scan and a scan that heard nothing are the same silence. */
  const esp_err_t started = esp_bt_gap_start_discovery(ESP_BT_INQ_MODE_GENERAL_INQUIRY, length, 0);
  if (started != ESP_OK) {
    ESP_LOGE(TAG, "the scan did not start (%d) -- nothing is being looked for. Press this again "
                  "in a few seconds; if it keeps refusing, restart the panel.", (int) started);
    this->resume_reconnect();
  }
#endif
}

void PortallBT::drop_links_() {
#if defined(CONFIG_BT_A2DP_ENABLE)
  char text[18];
  if (this->a2dp_open_ && addr_set(this->open_sink_)) {
    say_addr(text, this->open_sink_);
    ESP_LOGI(TAG, "  hanging up the speaker %s first -- a connected device answers no inquiry",
             text);
    esp_a2d_source_disconnect(this->open_sink_);
  }
#endif
#if defined(CONFIG_BT_HID_HOST_ENABLED)
  char hid_text[18];
  if (this->hid_open_ && addr_set(this->open_hid_)) {
    say_addr(hid_text, this->open_hid_);
    ESP_LOGI(TAG, "  hanging up the input device %s first", hid_text);
    esp_bt_hid_host_disconnect(this->open_hid_);
  }
#endif
}

void PortallBT::say_pairing_later() {
  // Defined here rather than inline in the header, and the reason is narrower
  // than it first looked: now_ms_() is a free `static` function in THIS file,
  // not a member, so the header cannot see it at all -- a member declared
  // later would have been fine. Written inline first, and checkbt.py named the
  // line in one pass across five configurations.
  this->pair_report_due_ms_ = now_ms_() + 3000;
}

void PortallBT::pair_report_tick_() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (this->pair_report_due_ms_ == 0)
    return;
  if ((int32_t) (now_ms_() - this->pair_report_due_ms_) < 0)
    return;
  this->pair_report_due_ms_ = 0;

  if (this->a2dp_open_ || this->hid_open_) {
    ESP_LOGI(TAG, "pairing finished: a device is connected. Nothing will scan again -- from now "
                  "on this panel reconnects by address.");
  } else if (this->heard_ == 0) {
    ESP_LOGW(TAG, "pairing finished and nothing answered the scan. Put the device in PAIRING "
                  "mode and press the button again; a speaker already connected to a telephone "
                  "or a car does not answer.");
  } else {
    ESP_LOGW(TAG, "pairing finished: %u device(s) heard, none of them connected. Only a speaker "
                  "or an input device is taken, and only if this panel was asked for that kind.",
             (unsigned) this->heard_);
  }
#endif
}

void PortallBT::resume_reconnect() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (!this->reconnect_paused_)
    return;
  this->reconnect_paused_ = false;
  // From the short end of the backoff: whatever was just paired should come
  // back at once, and a scan that found nothing should put a remembered device
  // back where it was.
  this->reconnect_backoff_ms_ = RECONNECT_FIRST_MS;
  this->reconnect_due_ms_ = now_ms_() + RECONNECT_FIRST_MS;
#endif
}

void PortallBT::set_bt_enabled(bool on) {
  if (this->bt_off_ != !on)
    return;  // already where it is being asked to go
  this->bt_off_ = !on;
  if (!on) {
    ESP_LOGI(TAG, "Bluetooth off: hanging up and no longer paging for paired devices");
    this->drop_links_();
#ifdef CONFIG_BT_BLUEDROID_ENABLED
    this->reconnect_backoff_ms_ = 0;
#endif
    return;
  }
  ESP_LOGI(TAG, "Bluetooth on: looking for the remembered device(s) again");
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  // From the short end, so a speaker in the room comes back at once rather
  // than after whatever the backoff had grown to before it was switched off.
  this->reconnect_backoff_ms_ = RECONNECT_FIRST_MS;
  this->reconnect_due_ms_ = now_ms_() + RECONNECT_FIRST_MS;
#endif
}

std::string PortallBT::describe_role(bool speaker) const {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  const bool has = speaker ? this->remembered_.has_sink : this->remembered_.has_hid;
  if (!has)
    return "none";
  char text[18];
  say_addr(text, speaker ? this->remembered_.sink : this->remembered_.hid);
  const bool open = speaker ? this->a2dp_open_ : this->hid_open_;
  std::string out(text);
  if (this->bt_off_)
    return out + " (Bluetooth off)";
  return out + (open ? " connected" : " paired, away");
#else
  (void) speaker;
  return "none";
#endif
}

void PortallBT::forget_one(bool speaker) {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (!this->profiles_up_) {
    ESP_LOGW(TAG, "nothing to forget yet -- no dongle has answered");
    return;
  }
  const bool has = speaker ? this->remembered_.has_sink : this->remembered_.has_hid;
  if (!has) {
    ESP_LOGW(TAG, "no %s is remembered, so there is nothing to forget",
             speaker ? "speaker" : "input device");
    return;
  }
  const uint8_t *addr = speaker ? this->remembered_.sink : this->remembered_.hid;
  char text[18];
  say_addr(text, addr);

  /* Hang up this one FIRST, for the reason forget() records: removing a bond
   * under a live ACL leaves the device connected with its key gone, so the
   * next scan cannot see it AND it can no longer come back on its own. */
  esp_bd_addr_t target;
  memcpy(target, addr, 6);
  if (speaker) {
#ifdef CONFIG_BT_A2DP_ENABLE
    if (this->a2dp_open_)
      esp_a2d_source_disconnect(target);
#endif
  } else {
#ifdef CONFIG_BT_HID_HOST_ENABLED
    if (this->hid_open_)
      esp_bt_hid_host_disconnect(target);
#endif
  }
  esp_bt_gap_remove_bond_device(target);

  if (speaker) {
    this->remembered_.has_sink = false;
    memset(this->remembered_.sink, 0, 6);
  } else {
    this->remembered_.has_hid = false;
    memset(this->remembered_.hid, 0, 6);
    this->hid_open_ = false;
    memset(this->open_hid_, 0, 6);
  }
  this->remembered_pref_.save(&this->remembered_);
  ESP_LOGI(TAG, "forgot the %s %s -- link key and role both", speaker ? "speaker" : "input device",
           text);
#else
  (void) speaker;
#endif
}

void PortallBT::forget() {
#ifdef CONFIG_BT_BLUEDROID_ENABLED
  if (!this->profiles_up_) {
    ESP_LOGW(TAG, "nothing to forget yet -- no dongle has answered");
    return;
  }
  /* Hang up FIRST. Removing a bond while the ACL is still up leaves a live
   * connection whose key the stack has just deleted -- and the device stays
   * connected, so the pairing run that follows scans for something that cannot
   * answer. That is the reported "even Forget does not help". */
  this->drop_links_();

  const int bonded = esp_bt_gap_get_bond_device_num();
  if (bonded > 0) {
    esp_bd_addr_t list[8];
    int count = bonded < 8 ? bonded : 8;
    if (esp_bt_gap_get_bond_device_list(&count, list) == ESP_OK) {
      for (int i = 0; i < count; i++)
        esp_bt_gap_remove_bond_device(list[i]);
    }
  }
  // Both stores, or the next start would ask an address Bluedroid no longer has
  // a key for, for ever, on a backoff.
  this->remembered_ = Remembered{};
  this->remembered_pref_.save(&this->remembered_);
  this->hid_open_ = false;
  memset(this->open_hid_, 0, 6);
  ESP_LOGI(TAG, "forgot %d paired device(s), link keys and roles both", bonded);
#endif
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_ESP32
