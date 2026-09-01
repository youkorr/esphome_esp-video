#pragma once

#include "esphome/components/number/number.h"
#include "../portall.h"

#if CFG_TUD_AUDIO

namespace esphome {
namespace portall {

/// How loudly this board plays the sound it is sent.
///
/// It governs both ways in, and that is worth being clear about because the
/// class is still called USB: the volume is applied in on_audio_samples, which
/// is the one door PCM comes through whether it arrived over the USB audio
/// class or over the network from a page the add-on is rendering. At zero the
/// samples are dropped before the speaker sees them.
///
/// A number rather than a media player's volume because the sound is not
/// something this board chose to play -- it belongs to whatever is sending it,
/// and this only decides how loudly it comes out here.
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
