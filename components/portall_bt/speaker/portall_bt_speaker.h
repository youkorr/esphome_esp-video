#pragma once

#include "esphome/core/component.h"
#include "esphome/core/defines.h"

#ifdef USE_SPEAKER

#include "esphome/components/speaker/speaker.h"
#include "../portall_bt.h"

namespace esphome {
namespace portall_bt {

/* An ESPHome speaker whose output is a Bluetooth speaker.
 *
 * Everything below the play() call already existed: PortallBT::feed_audio()
 * fills the ring that Bluedroid's encoder PULLS from, on its own task, at its
 * own rate. What was missing was a door for a YAML to push sound through, so
 * the only thing that could be heard from a paired car receiver was the test
 * tone this component generates for itself.
 *
 * THE FORMAT IS FIXED AT BOTH ENDS AND THEY DISAGREE, which is the whole
 * design problem. portall's page audio is 48000 Hz, 16-bit, MONO -- 48000
 * because that is what a browser produces, mono because these panels have one
 * speaker and it halves what the network carries. A2DP is 44100 Hz, 16-bit,
 * STEREO -- not a preference either, a hardcoded constant in Espressif's
 * btc_a2dp_source.c with a comment saying as much.
 *
 * So two conversions are needed and they are split deliberately:
 *
 *   48000 -> 44100   ESPHome's resampler speaker, in the YAML
 *   mono  -> stereo  here
 *
 * The rate is real signal processing and ESPHome already has it; writing a
 * second resampler in this component would be the reinvention this repository
 * keeps warning itself about. The channel duplication is not signal
 * processing at all -- it is each 16-bit sample written twice, exact, with no
 * filter, no state and nothing to drift -- and doing it here is what keeps a
 * household's YAML to ONE extra block instead of two.
 *
 * It has to be here rather than anywhere else, because ESPHome's own parts
 * will not do it: AudioResampler::start() returns ESP_ERR_NOT_SUPPORTED the
 * moment the input and output channel counts differ, and the mixer, which
 * does convert channels, wants every source already at the output's sample
 * rate. Read rather than assumed, in 2026.8.2.
 */
class PortallBTSpeaker : public Component, public speaker::Speaker {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_parent(PortallBT *parent) { this->parent_ = parent; }

  size_t play(const uint8_t *data, size_t length) override;
  void start() override;
  void stop() override;
  bool has_buffered_data() const override;

 protected:
  /// Push `length` bytes of 16-bit mono at the ring, each sample twice.
  void play_mono_(const uint8_t *data, size_t length);

  PortallBT *parent_{nullptr};
  /* A sample is two bytes and a caller is not obliged to hand over a whole
   * one. Nothing in this project has ever split one -- portall flushes in
   * 1920-byte blocks and the resampler emits whole frames -- but half a
   * sample carried into the next call would swap the two channels for the
   * rest of the stream, and that is a fault nobody could diagnose from a car.
   * One byte of carry costs nothing and removes the question. */
  uint8_t half_sample_{0};
  bool have_half_{false};
  /* Said once rather than every block. A panel with no speaker paired feeds
   * this fifty times a second, and a warning at that rate is a log nobody can
   * read -- but total silence about sound going nowhere is exactly the fault
   * this file's neighbours keep recording. */
  bool said_nowhere_{false};
  /* The stream's own numbers are only known once something plays, so they are
   * checked there rather than in dump_config(), where audio_stream_info_ is
   * still whatever ESPHome starts it at. */
  bool said_format_{false};
};

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_SPEAKER
