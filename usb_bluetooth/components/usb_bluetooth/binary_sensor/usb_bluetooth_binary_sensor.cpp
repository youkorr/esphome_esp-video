#include "usb_bluetooth_binary_sensor.h"

#ifdef USE_BINARY_SENSOR

#include <cinttypes>

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome {
namespace usb_bluetooth {

static const char *const TAG = "usb_bluetooth.key";

void UsbBluetoothKey::setup() {
  this->publish_initial_state(false);
  if (this->parent_ == nullptr)
    return;
  this->parent_->add_key_listener([this](uint16_t page, uint16_t usage) {
    const uint32_t key = (uint32_t) page << 16 | usage;
    for (uint32_t mine : this->usages_) {
      if (mine == key) {
        this->press_();
        return;
      }
    }
  });
  if (this->home_)
    this->parent_->add_home_listener([this]() { this->press_(); });
}

void UsbBluetoothKey::press_() {
  /* A second press inside the hold restarts it rather than being lost: the
     sensor goes OFF and ON again, which is two presses to LVGL as it was to
     the thumb. */
  if (this->pressed_at_ != 0)
    this->publish_state(false);
  this->pressed_at_ = millis();
  if (this->pressed_at_ == 0)
    this->pressed_at_ = 1;
  this->publish_state(true);
}

void UsbBluetoothKey::loop() {
  if (this->pressed_at_ != 0 && millis() - this->pressed_at_ >= this->hold_ms_) {
    this->pressed_at_ = 0;
    this->publish_state(false);
  }
}

void UsbBluetoothKey::dump_config() {
  LOG_BINARY_SENSOR("", "Bluetooth key", this);
  for (uint32_t key : this->usages_)
    ESP_LOGCONFIG(TAG, "  Key: page %#04x, usage %#04x", (unsigned) (key >> 16), (unsigned) (key & 0xFFFF));
  if (this->home_)
    ESP_LOGCONFIG(TAG, "  And a remote's Menu or a gamepad's Home");
  ESP_LOGCONFIG(TAG, "  Held for %" PRIu32 " ms", this->hold_ms_);
}

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_BINARY_SENSOR
