#pragma once
/* Stand-in for components/portall/portall.h, for compiling audio.cpp alone.
 *
 * The real header drags in the JPEG decoder, the PPA and TinyUSB, none of which
 * exist on a workstation. audio.cpp touches only the members below, so they
 * are declared here with the real types and defaults; the PORTALL_AUDIO_*
 * constants are NOT restated -- tools/checkstereo.py copies them out of the
 * real header each run, so the two cannot drift. */
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "esphome/core/hal.h"
#include "esphome/components/speaker/speaker.h"
#include "audio_constants.h"
namespace esphome { namespace portall {
class Portall {
 public:
  void set_speaker(speaker::Speaker *s) { speaker_ = s; }
  void on_audio_samples(const uint8_t *data, size_t length, uint8_t channels = PORTALL_AUDIO_CHANNELS,
                        uint32_t rate = PORTALL_AUDIO_RATE);
  void set_sample_rate(uint32_t rate) { sample_rate_ = rate; }
  void set_audio_volume(float volume);
  void on_usb_audio_mute(bool muted);
  void setup_speaker_();
  void flush_audio_block_();
  speaker::Speaker *speaker_{nullptr};
  float audio_volume_{1.0f};
  bool audio_muted_{false};
  bool logged_first_audio_{false};
  uint8_t *audio_block_{nullptr};
  size_t audio_block_size_{0};
  size_t audio_block_used_{0};
  size_t last_packet_len_{0};
  uint32_t audio_resyncs_{0};
  bool audio_ever_accepted_{false};
  uint32_t audio_underruns_{0};
  uint32_t audio_refusals_{0};
  uint32_t audio_hold_until_ms_{0};
  uint32_t audio_hold_ms_{0};
  uint8_t audio_channels_{PORTALL_AUDIO_CHANNELS};
  uint32_t last_audio_ms_{0};
  uint32_t sample_rate_{PORTALL_AUDIO_RATE};
  uint32_t rate_mismatch_since_ms_{0};
  bool logged_rate_mismatch_{false};
};
}}
