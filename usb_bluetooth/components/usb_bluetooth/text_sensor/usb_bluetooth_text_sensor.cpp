#include "usb_bluetooth_text_sensor.h"

#ifdef USE_TEXT_SENSOR

#include "esphome/core/log.h"

namespace esphome {
namespace usb_bluetooth {

static const char *const TAG = "usb_bluetooth.text_sensor";

void UsbBluetoothTextSensor::dump_config() {
  LOG_TEXT_SENSOR("", "USB Bluetooth device", this);
  if (this->speaker_ && this->slot_ != 0)
    ESP_LOGCONFIG(TAG, "  Reporting speaker slot %u", (unsigned) this->slot_);
  else if (this->speaker_)
    ESP_LOGCONFIG(TAG, "  Reporting the speaker");
  else if (this->slot_ != 0)
    ESP_LOGCONFIG(TAG, "  Reporting input slot %u", (unsigned) this->slot_);
  else
    ESP_LOGCONFIG(TAG, "  Reporting every input device");
}

void UsbBluetoothTextSensor::update() {
  if (this->parent_ == nullptr)
    return;
  std::string now;
  if (this->slot_ == 0)
    now = this->parent_->describe_role(this->speaker_);
  else if (this->speaker_)
    now = this->parent_->describe_speaker(this->slot_ - 1);
  else
    now = this->parent_->describe_input(this->slot_ - 1);
  // Only on a change: an unchanged state is still a message on the API
  // connection, and this one is read by somebody glancing at a card.
  if (!this->has_state() || this->state != now)
    this->publish_state(now);
}

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_TEXT_SENSOR
