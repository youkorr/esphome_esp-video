#pragma once

#include "esphome/components/number/number.h"
#include "../portall.h"

#if CFG_TUD_AUDIO

namespace esphome {
namespace portall {

/// The playback volume of the sound coming from the host.
///
/// The host has its own volume control and sets it over USB, so this is the
/// same value from the other side: moving either moves the sound. A number
/// rather than a media player's volume because the sound is not something this
/// board is playing -- it belongs to the computer, and this only decides how
/// loudly it comes out here.
class USBVolumeNumber : public number::Number, public Component {
 public:
  void set_parent(Portall *parent) { this->parent_ = parent; }
  void setup() override { this->publish_state(this->parent_->get_audio_volume() * 100.0f); }
  /// Follows the host as well as Home Assistant.
  ///
  /// Turning the volume down on the computer is a USB request, and it arrives
  /// on the USB task -- which is no place to publish a state from, because
  /// that walks the API connections and belongs to the loop. So the value is
  /// noticed here instead, where publishing is safe. Without this the entity
  /// showed whatever it was last told by Home Assistant, while the sound
  /// followed the computer: two numbers for one volume.
  void loop() override;
  void dump_config() override;

 protected:
  void control(float value) override {
    this->parent_->on_usb_audio_volume(value / 100.0f);
    this->publish_state(value);
  }

  Portall *parent_{nullptr};
};

}  // namespace portall
}  // namespace esphome

#endif  // CFG_TUD_AUDIO
