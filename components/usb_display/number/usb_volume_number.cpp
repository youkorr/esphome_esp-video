#include "usb_volume_number.h"

#include "esphome/core/log.h"

#if CFG_TUD_AUDIO

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.number";

void USBVolumeNumber::dump_config() { LOG_NUMBER(TAG, "USB Display Volume", this); }

}  // namespace usb_display
}  // namespace esphome

#endif
