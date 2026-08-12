#pragma once

#include "esphome/components/number/number.h"
#include "../usb_display.h"

#if CFG_TUD_AUDIO

namespace esphome {
namespace usb_display {

/// The playback volume of the sound coming from the host.
///
/// The host has its own volume control and sets it over USB, so this is the
/// same value from the other side: moving either moves the sound. A number
/// rather than a media player's volume because the sound is not something this
/// board is playing -- it belongs to the computer, and this only decides how
/// loudly it comes out here.
class USBVolumeNumber : public number::Number, public Component {
 public:
  void set_parent(USBDisplay *parent) { this->parent_ = parent; }
  void setup() override { this->publish_state(this->parent_->get_audio_volume() * 100.0f); }
  void dump_config() override;

 protected:
  void control(float value) override {
    this->parent_->on_usb_audio_volume(value / 100.0f);
    this->publish_state(value);
  }

  USBDisplay *parent_{nullptr};
};

}  // namespace usb_display
}  // namespace esphome

#endif  // CFG_TUD_AUDIO
