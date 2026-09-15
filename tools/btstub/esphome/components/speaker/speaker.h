#pragma once
/* Stand-in for esphome/components/speaker/speaker.h.
 *
 * Copied field for field from ESPHome 2026.8.2, which is the version this
 * repository validates against and the one the user flashes. The signatures
 * are what matter and they are exact: play() is pure virtual and takes
 * (const uint8_t *, size_t), has_buffered_data() is const, and the State
 * enumerators are the real names. A stub that guesses one of those lets code
 * that cannot build pass the only C++ check this repository has -- which is
 * the fault ESP_HIDH_DATA_IND_EVT's invented report id nearly became.
 *
 * Left out: the TickType_t overload (behind USE_ESP32 in the real header and
 * not overridden here), the audio_dac hooks and the output callback, none of
 * which portall_bt's speaker touches. */
#include <cstddef>
#include <cstdint>
#include <vector>

#include "esphome/components/audio/audio.h"

namespace esphome {
namespace speaker {

enum State : uint8_t {
  STATE_STOPPED = 0,
  STATE_STARTING,
  STATE_RUNNING,
  STATE_STOPPING,
};

class Speaker {
 public:
  virtual size_t play(const uint8_t *data, size_t length) = 0;
  size_t play(const std::vector<uint8_t> &data) { return this->play(data.data(), data.size()); }

  virtual void start() = 0;
  virtual void stop() = 0;
  virtual void finish() { this->stop(); }

  virtual void set_pause_state(bool pause_state) {}
  virtual bool get_pause_state() const { return false; }

  virtual bool has_buffered_data() const = 0;

  bool is_running() const { return this->state_ == STATE_RUNNING; }
  bool is_stopped() const { return this->state_ == STATE_STOPPED; }

  virtual void set_volume(float volume) { this->volume_ = volume; }
  virtual float get_volume() { return this->volume_; }

  virtual void set_mute_state(bool mute_state) { this->mute_state_ = mute_state; }
  virtual bool get_mute_state() { return this->mute_state_; }

  void set_audio_stream_info(const audio::AudioStreamInfo &audio_stream_info) {
    this->audio_stream_info_ = audio_stream_info;
  }
  audio::AudioStreamInfo &get_audio_stream_info() { return this->audio_stream_info_; }

 protected:
  State state_{STATE_STOPPED};
  audio::AudioStreamInfo audio_stream_info_;
  float volume_{1.0f};
  bool mute_state_{false};
};

}  // namespace speaker
}  // namespace esphome
