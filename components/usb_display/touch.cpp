/* The touch screen, sent back to whoever is driving the picture.
 *
 * There are two ends it can go to, and they are independent. Over the cable it
 * is an HID digitizer, which needs nothing installed on the other end: HID is a
 * class every operating system already has a driver for, and a five-contact
 * digitizer is something Windows, Linux and macOS all understand out of the
 * box. The report layout is Espressif's, so their Windows display driver --
 * which pairs the touch with the monitor it created -- finds what it expects.
 * Over the network it goes back up the same socket the frames arrive on, which
 * is what makes a page rendered on another machine pressable here.
 *
 * A board may have either, both or neither, so this file is compiled whenever
 * a touch screen exists at all and each destination is guarded on its own.
 *
 * Coordinates are passed through untouched. ESPHome applies the touchscreen's
 * own transform: before a listener sees a point, so a panel mounted upside down
 * is already corrected there, in the same place LVGL gets it from.
 */

#include "usb_display.h"

#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include <cstring>

extern "C" {
#include "tusb.h"
#include "usb_descriptors.h"
}

#ifdef USE_TOUCHSCREEN

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.touch";

void USBDisplay::setup_touch_() {
  this->touchscreen_->register_listener(this);
#if CFG_TUD_HID
  ESP_LOGCONFIG(TAG, "Touch screen reported to the host, up to %d contacts", UDISP_TOUCH_MAX_POINTS);
#else
  ESP_LOGCONFIG(TAG, "Touch screen reported to the network sender, up to %d contacts", UDISP_NET_TOUCH_MAX);
#endif
}

void USBDisplay::update(const touchscreen::TouchPoints_t &points) {
#if CFG_TUD_HID
  udisp_touch_report_t report = {};

  uint8_t count = 0;
  for (const auto &point : points) {
    if (count >= UDISP_TOUCH_MAX_POINTS)
      break;
    auto &contact = report.contacts[count];
    contact.press_down = 1;
    contact.index = point.id;
    contact.x = point.x;
    contact.y = point.y;
    // The host wants a contact area. The GT911 reports a pressure rather than a
    // shape, which is what Espressif put here too.
    contact.width = (uint16_t) point.z_raw;
    contact.height = (uint16_t) point.z_raw;
    count++;
  }
  report.count = count;

  this->send_touch_report_(report);
  // A release is a report with no contacts, and it is the one that must not be
  // lost: drop a press and the next poll replaces it, drop the release and the
  // host believes a finger is still down.
  this->release_pending_ = count > 0;
#endif
  // The same contacts go back to whoever is sending the picture over the
  // network, which is what makes a dashboard rendered elsewhere pressable here.
  this->queue_touch_(points);
}

void USBDisplay::release() {
  const touchscreen::TouchPoints_t none;
  this->queue_touch_(none);
#if CFG_TUD_HID
  udisp_touch_report_t report = {};
  this->release_pending_ = !this->send_touch_report_(report);
#endif
}

#if CFG_TUD_HID
bool USBDisplay::send_touch_report_(const udisp_touch_report_t &report) {
  if (!tud_hid_ready())
    return false;
  return tud_hid_report(REPORT_ID_TOUCH, &report, sizeof(report));
}

void USBDisplay::retry_release_() {
  if (!this->release_pending_)
    return;
  udisp_touch_report_t report = {};
  if (this->send_touch_report_(report))
    this->release_pending_ = false;
}
#endif  // CFG_TUD_HID

}  // namespace usb_display
}  // namespace esphome

#endif  // USE_TOUCHSCREEN

#if CFG_TUD_HID
// ===========================================================================
// The HID callbacks TinyUSB calls, which are plain C
// ===========================================================================
extern "C" uint16_t tud_hid_get_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type,
                                          uint8_t *buffer, uint16_t reqlen) {
  (void) itf;
  (void) report_type;
  // The host asks how many contacts this screen can report before it will treat
  // it as a multi-touch device at all.
  if (report_id == REPORT_ID_MAX_COUNT && reqlen >= 1) {
    buffer[0] = UDISP_TOUCH_MAX_POINTS;
    return 1;
  }
  return 0;
}

extern "C" void tud_hid_set_report_cb(uint8_t itf, uint8_t report_id, hid_report_type_t report_type,
                                      uint8_t const *buffer, uint16_t bufsize) {
  (void) itf;
  (void) report_id;
  (void) report_type;
  (void) buffer;
  (void) bufsize;
  // Nothing to receive: a touch screen has no lamps to set.
}

#endif  // CFG_TUD_HID
