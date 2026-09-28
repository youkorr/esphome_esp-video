#include "usb_bluetooth_text_sensor.h"

#ifdef USE_TEXT_SENSOR

#include "esphome/core/log.h"

namespace esphome {
namespace usb_bluetooth {

static const char *const TAG = "usb_bluetooth.text_sensor";

void UsbBluetoothTextSensor::dump_config() {
  LOG_TEXT_SENSOR("", "USB Bluetooth device", this);
  ESP_LOGCONFIG(TAG, "  Reporting the %s slot", this->speaker_ ? "speaker" : "input device");
}

void UsbBluetoothTextSensor::update() {
  if (this->parent_ == nullptr)
    return;
  const std::string now = this->parent_->describe_role(this->speaker_);
  // Only on a change: an unchanged state is still a message on the API
  // connection, and this one is read by somebody glancing at a card.
  if (!this->has_state() || this->state != now)
    this->publish_state(now);
}

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_TEXT_SENSOR
