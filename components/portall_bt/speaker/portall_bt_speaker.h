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

  void loop() override;

  /* Takes only what fits, and says how much -- which is what every ESPHome
   * speaker does and what this one did not. It used to accept everything:
   * right for the page's sound, which arrives in real time from the network,
   * and wrong for an announcement, which a FLAC decoder produces as fast as
   * it can. The ring kept a tenth of a second of it and the rest was thrown
   * away, so the answer reached the car in fragments and sounded too fast.
   * Now the caller keeps what did not fit and offers it again. */
  size_t play(const uint8_t *data, size_t length) override;
#ifdef USE_ESP32
  size_t play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) override;
#endif
  void start() override;
  void stop() override;
  bool has_buffered_data() const override;

  /* Volume and mute are done HERE, in software, on the samples on their way
   * past -- and the base class invites exactly that: "Individual speaker
   * components can override and implement in software if an audio dac isn't
   * available."
   *
   * Without these two the slider moved and nothing happened, which is what a
   * panel reported. The chain explains why. `portall.set_volume` does not
   * scale anything itself: portall's on_audio_samples() only GATES on the
   * value -- a volume of zero is silence -- and hands the number to
   * speaker_->set_volume(). Every block between here and there passes it on
   * faithfully (a mixer source gives it to the mixer's output speaker, a
   * resampler to its own output speaker), so it arrives here intact. And here
   * it met speaker::Speaker::set_volume(), which stores the value and applies
   * it to an audio_dac_ -- a codec on an I2S bus. A Bluetooth speaker has no
   * codec on this board, so there was nothing at the end of the chain to
   * obey it.
   *
   * That is also why it works on the panel's own loudspeaker in the same
   * YAML: an i2s_audio speaker has an ES8311 or ES8388 behind it and the
   * volume is set in the codec's own register. */
  void set_volume(float volume) override;
  void set_mute_state(bool mute_state) override;

 protected:
  /* One path for both channel counts. `in_frame` is how many bytes of the
   * caller's stream make one A2DP frame -- 2 when it is mono and each sample
   * goes out twice, 4 when it is already stereo -- and it is the unit the
   * carry above is kept in. */
  void play_frames_(const uint8_t *data, size_t length, uint8_t in_frame);
  /// play() with a wait, counted in ticks, for room in the ring.
  size_t play_waiting_(const uint8_t *data, size_t length, uint32_t ticks_to_wait);
  /* Frames this speaker threw away itself -- nothing paired to send them to --
   * reported in loop() with the ones Bluedroid took, for the same reason: a
   * caller counting frames it handed on must see every one of them come
   * back, played or not, or it never finishes. Written on the caller's task,
   * read on the loop. */
  std::atomic<uint32_t> dropped_frames_{0};
  /// Write one A2DP frame, with the gain applied, and return the bytes used.
  size_t emit_frame_(uint8_t *out, const uint8_t *in, uint8_t in_frame) const;
  /// A 16-bit sample with volume and mute applied.
  int16_t scaled_(uint8_t low, uint8_t high) const;

  PortallBT *parent_{nullptr};
  /* A caller is not obliged to hand over a whole unit, and what this leaves
   * behind is never thrown away.
   *
   * The unit differs by path -- two bytes of a mono sample, four of a stereo
   * frame -- so the carry holds up to three. Nothing in this project has ever
   * split one (portall flushes 1920-byte blocks and the resampler emits whole
   * frames), but a part-unit dropped or kept as a whole one puts the two
   * channels out of step for the REST OF THE STREAM, and past the ring that
   * is not a click but hiss. Three bytes of carry cost nothing and remove the
   * question, and everything handed to feed_audio is a whole number of
   * frames because of them. */
  uint8_t carry_[4]{};
  uint8_t carry_len_{0};
  /* Said once rather than every block. A panel with no speaker paired feeds
   * this fifty times a second, and a warning at that rate is a log nobody can
   * read -- but total silence about sound going nowhere is exactly the fault
   * this file's neighbours keep recording. */
  bool said_nowhere_{false};
  /* The stream's own numbers are only known once something plays, so they are
   * checked there rather than in dump_config(), where audio_stream_info_ is
   * still whatever ESPHome starts it at. */
  bool said_format_{false};
  /* Q15 rather than a float, because this multiplies every sample on the
   * audio task's critical path: 32768 is unity and the shift back is free.
   * Mute is kept as its own flag rather than a gain of zero, so unmuting
   * returns to the volume that was set rather than to silence. */
  int32_t gain_q15_{32768};
  bool muted_{false};
};

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_SPEAKER
