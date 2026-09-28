#pragma once

#include "esphome/core/defines.h"
#ifdef USE_BINARY_SENSOR

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/core/component.h"

#include <vector>

#include "../usb_bluetooth.h"

namespace esphome {
namespace usb_bluetooth {

/* One key of every input device, as a binary sensor. See __init__.py for why
 * it stays ON for `hold:` rather than for as long as the device says: LVGL
 * reads a keypad on its own timer and would miss a press shorter than that. */
class UsbBluetoothKey : public binary_sensor::BinarySensor, public Component {
 public:
  void set_parent(UsbBluetooth *parent) { this->parent_ = parent; }
  void add_usage(uint16_t page, uint16_t usage) { this->usages_.push_back((uint32_t) page << 16 | usage); }
  void set_home(bool home) { this->home_ = home; }
  void set_hold(uint32_t ms) { this->hold_ms_ = ms; }

  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 protected:
  void press_();

  UsbBluetooth *parent_{nullptr};
  // Page in the high half, usage in the low, one entry per key named.
  std::vector<uint32_t> usages_{};
  bool home_{false};
  uint32_t hold_ms_{100};
  // When the current press began, 0 when released. millis() is never 0 by
  // the time a device has paired, so 0 needs no separate flag.
  uint32_t pressed_at_{0};
};

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_BINARY_SENSOR
