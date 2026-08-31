#include "usb_volume_number.h"

#include "esphome/core/log.h"

#include <cmath>

#if CFG_TUD_AUDIO

namespace esphome {
namespace portall {

static const char *const TAG = "usb_display.number";

// Half a step. The number moves in whole percent, so a smaller difference is
// the same setting with a different rounding error, and republishing it would
// put traffic on the API for nothing.
static constexpr float VOLUME_EPSILON = 0.5f;

void USBVolumeNumber::loop() {
  const float volume = this->parent_->get_audio_volume() * 100.0f;
  if (fabsf(volume - this->state) >= VOLUME_EPSILON)
    this->publish_state(volume);
}

void USBVolumeNumber::dump_config() { LOG_NUMBER(TAG, "USB Display Volume", this); }

}  // namespace portall
}  // namespace esphome

#endif
