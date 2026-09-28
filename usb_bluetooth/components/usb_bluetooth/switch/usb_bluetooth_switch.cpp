#include "usb_bluetooth_switch.h"

#ifdef USE_SWITCH

#include "esphome/core/log.h"

namespace esphome {
namespace usb_bluetooth {

static const char *const TAG = "usb_bluetooth.switch";

void UsbBluetoothSwitch::setup() {
  /* The stored position wins, because this is a SETTING rather than a reading:
   * somebody who switched Bluetooth off before going away does not want it
   * back on because the power blinked. That is the same reasoning the volume
   * slider is built on, and ESPHome's own restore does the work. */
  const optional<bool> restored = this->get_initial_state_with_restore_mode();
  const bool on = restored.value_or(true);
  if (this->parent_ != nullptr)
    this->parent_->set_bt_enabled(on);
  this->publish_state(on);
}

void UsbBluetoothSwitch::dump_config() {
  LOG_SWITCH("", "Portall Bluetooth", this);
  ESP_LOGCONFIG(TAG, "  Off hangs up and stops paging; the dongle stays enumerated");
}

void UsbBluetoothSwitch::write_state(bool state) {
  if (this->parent_ != nullptr)
    this->parent_->set_bt_enabled(state);
  this->publish_state(state);
}

}  // namespace usb_bluetooth
}  // namespace esphome

#endif  // USE_SWITCH
