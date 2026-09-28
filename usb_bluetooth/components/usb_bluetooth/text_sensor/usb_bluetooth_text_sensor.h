#pragma once

#include "esphome/core/component.h"
#include "esphome/core/defines.h"

#ifdef USE_TEXT_SENSOR

#include "esphome/components/text_sensor/text_sensor.h"
#include "../usb_bluetooth.h"

namespace esphome {
namespace usb_bluetooth {

/* What this board has paired, in Home Assistant rather than in a log.
 *
 * THERE ARE EXACTLY TWO, and that is structural rather than a shortcut.
 * `Remembered` holds one address for a speaker and one for an input device --
 * six bytes each -- because a connection BY ADDRESS is what lets this
 * component reconnect without an inquiry, and an inquiry is what takes the
 * panel's Wi-Fi down. So there is no list to page through: there is a
 * speaker slot and an input slot, and this reports them.
 *
 * It POLLS rather than being pushed to. The two things it reports change on
 * somebody else's task -- Bluedroid's -- and a text sensor published from
 * there would be a cross-task publish for a line that nobody reads faster
 * than they can look at a screen. Ten seconds is below noticing and costs a
 * string comparison.
 */
class UsbBluetoothTextSensor : public text_sensor::TextSensor, public PollingComponent {
 public:
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_parent(UsbBluetooth *parent) { this->parent_ = parent; }
  /// true reports the speaker slot, false the input one.
  void set_speaker(bool speaker) { this->speaker_ = speaker; }
  /// 1 .. MAX_INPUTS reports that one input slot; 0, the list of them all.
  void set_slot(uint8_t slot) { this->slot_ = slot; }

  void update() override;

 protected:
  UsbBluetooth *parent_{nullptr};
  bool speaker_{true};
  uint8_t slot_{0};
};

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_TEXT_SENSOR
