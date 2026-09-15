#pragma once
/* Stand-in for esphome/components/audio/audio.h.
 *
 * Copied field for field from ESP-IDF-era ESPHome 2026.8.2 -- the accessors
 * and the three-argument constructor, which is all portall_bt's speaker uses.
 * The conversion helpers are left out deliberately: a stub that carries more
 * than the component calls is a stub nobody can trust to be accurate. */
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace audio {

class AudioStreamInfo {
 public:
  AudioStreamInfo() : AudioStreamInfo(16, 1, 16000) {}
  AudioStreamInfo(uint8_t bits_per_sample, uint8_t channels, uint32_t sample_rate)
      : bits_per_sample_(bits_per_sample), channels_(channels), sample_rate_(sample_rate) {}

  uint8_t get_bits_per_sample() const { return this->bits_per_sample_; }
  uint8_t get_channels() const { return this->channels_; }
  uint32_t get_sample_rate() const { return this->sample_rate_; }

 protected:
  uint8_t bits_per_sample_;
  uint8_t channels_;
  uint32_t sample_rate_;
};

}  // namespace audio
}  // namespace esphome
