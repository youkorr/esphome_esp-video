#include "portall_bt_speaker.h"

#ifdef USE_SPEAKER

#include "esphome/core/log.h"

#include <cinttypes>
#include <cstring>

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt.speaker";

// What A2DP takes, and neither number is ours to choose: Espressif hardcode
// 44100 Hz, 16-bit, two channels in btc_a2dp_source.c.
static constexpr uint32_t A2DP_RATE = 44100;
static constexpr uint8_t A2DP_BITS = 16;

void PortallBTSpeaker::setup() {
  if (this->parent_ != nullptr && !this->parent_->wants_speaker()) {
    // Nothing downstream. Said here as well as in the config validator,
    // because the validator cannot see a parent that failed at runtime.
    ESP_LOGE(TAG, "The portall_bt: this speaker belongs to has no audio: true, so nothing will be heard");
  }
}

void PortallBTSpeaker::dump_config() {
  ESP_LOGCONFIG(TAG, "Bluetooth speaker:");
  ESP_LOGCONFIG(TAG, "  Sends %" PRIu32 " Hz, %u bit, %u channel to the paired device",
                this->audio_stream_info_.get_sample_rate(),
                (unsigned) this->audio_stream_info_.get_bits_per_sample(),
                (unsigned) this->audio_stream_info_.get_channels());
  if (this->audio_stream_info_.get_channels() == 1)
    ESP_LOGCONFIG(TAG, "  Each sample goes out twice: A2DP carries two channels and this panel produces one");
  ESP_LOGCONFIG(TAG, "  Volume is applied here, in software: there is no codec on this path to set it in");
}

void PortallBTSpeaker::start() {
  this->carry_len_ = 0;
  this->state_ = speaker::STATE_RUNNING;
}

void PortallBTSpeaker::stop() {
  this->carry_len_ = 0;
  this->state_ = speaker::STATE_STOPPED;
}

bool PortallBTSpeaker::has_buffered_data() const {
  return this->parent_ != nullptr && this->parent_->pcm_queued() > 0;
}

size_t PortallBTSpeaker::play(const uint8_t *data, size_t length) {
  if (this->parent_ == nullptr || data == nullptr || length == 0)
    return length;

  // A speaker that has not been started is not one to push sound at.
  if (this->state_ != speaker::STATE_RUNNING)
    this->start();

  /* Nothing paired, so there is nowhere for this to go -- and it is ACCEPTED
   * rather than refused, which is the part worth explaining.
   *
   * Returning 0 would be the honest-looking answer and it is the wrong one:
   * portall reads a speaker that never takes a byte as a stream being refused
   * and prints a long diagnosis about resamplers and mixers, which would be a
   * confident explanation of the wrong fault. Sound with no speaker at the
   * other end is not a stream that will not fit; it is sound whose moment has
   * passed, which this component already throws away for the same reason one
   * layer down. */
  if (!this->parent_->speaker_connected()) {
    if (!this->said_nowhere_) {
      this->said_nowhere_ = true;
      ESP_LOGI(TAG, "No Bluetooth speaker is connected, so this sound is being dropped. Pair one with the "
                    "portall_bt.pair action.");
    }
    this->carry_len_ = 0;
    return length;
  }
  this->said_nowhere_ = false;

  /* What is actually arriving, said once, because the two ends of this path
   * are fixed by other people and a mismatch is silent.
   *
   * Nothing here can resample, so a stream at any rate but 44100 reaches the
   * sink and is decoded at 44100 anyway -- which is not silence and not a
   * click, it is everything played at the wrong speed and the wrong pitch.
   * From a car that reads as the panel being broken rather than as a missing
   * resampler, so the log names the number it got and the one it needs. */
  if (!this->said_format_) {
    this->said_format_ = true;
    const uint32_t rate = this->audio_stream_info_.get_sample_rate();
    const uint8_t bits = this->audio_stream_info_.get_bits_per_sample();
    if (rate != A2DP_RATE || bits != A2DP_BITS) {
      ESP_LOGE(TAG,
               "This is being sent %" PRIu32 " Hz, %u bit sound and A2DP carries %" PRIu32 " Hz, %u bit. It will "
               "play at the wrong speed. Put a resampler between the source and this speaker -- "
               "speaker: - platform: resampler, output_speaker: this one.",
               rate, (unsigned) bits, A2DP_RATE, (unsigned) A2DP_BITS);
    } else {
      ESP_LOGI(TAG, "Playing %" PRIu32 " Hz, %u bit, %u channel over Bluetooth", rate, (unsigned) bits,
               (unsigned) this->audio_stream_info_.get_channels());
    }
  }

  this->play_frames_(data, length, this->audio_stream_info_.get_channels() >= 2 ? 4 : 2);
  return length;
}

void PortallBTSpeaker::set_volume(float volume) {
  // Keep the base class's bookkeeping -- get_volume() and any audio_dac a
  // future board might carry -- and add the part that actually does it.
  speaker::Speaker::set_volume(volume);
  volume = volume < 0.0f ? 0.0f : (volume > 1.0f ? 1.0f : volume);
  this->gain_q15_ = (int32_t) (volume * 32768.0f + 0.5f);
}

void PortallBTSpeaker::set_mute_state(bool mute_state) {
  speaker::Speaker::set_mute_state(mute_state);
  this->muted_ = mute_state;
}

int16_t PortallBTSpeaker::scaled_(uint8_t low, uint8_t high) const {
  if (this->muted_)
    return 0;
  const int16_t sample = (int16_t) ((uint16_t) low | ((uint16_t) high << 8));
  if (this->gain_q15_ >= 32768)
    return sample;
  return (int16_t) (((int32_t) sample * this->gain_q15_) >> 15);
}

void PortallBTSpeaker::play_frames_(const uint8_t *data, size_t length, uint8_t in_frame) {
  /* A staging buffer rather than a call per frame: feed_audio copies into a
   * ring, so handing it 256 bytes at a time instead of 4 is the difference
   * between one loop and many. 256 is a multiple of four, which is what keeps
   * every flush a whole number of A2DP frames. */
  uint8_t out[256];
  size_t used = 0;

  // Finish the unit the last call ended in the middle of, if there was one.
  if (this->carry_len_ > 0) {
    const uint8_t want = in_frame - this->carry_len_;
    if (length < want) {
      // Still not a whole one. Keep gathering; nothing goes out this call.
      memcpy(this->carry_ + this->carry_len_, data, length);
      this->carry_len_ += (uint8_t) length;
      return;
    }
    memcpy(this->carry_ + this->carry_len_, data, want);
    data += want;
    length -= want;
    this->carry_len_ = 0;
    used += this->emit_frame_(out, this->carry_, in_frame);
  }

  while (length >= in_frame) {
    if (used + 4 > sizeof(out)) {
      this->parent_->feed_audio(out, (uint32_t) used);
      used = 0;
    }
    used += this->emit_frame_(out + used, data, in_frame);
    data += in_frame;
    length -= in_frame;
  }

  if (used > 0)
    this->parent_->feed_audio(out, (uint32_t) used);

  // Whatever is left is less than one unit. See the header: dropping it, or
  // keeping it as a whole one, puts the two channels out of step for the rest
  // of the stream.
  if (length > 0) {
    memcpy(this->carry_, data, length);
    this->carry_len_ = (uint8_t) length;
  }
}

size_t PortallBTSpeaker::emit_frame_(uint8_t *out, const uint8_t *in, uint8_t in_frame) const {
  const int16_t left = this->scaled_(in[0], in[1]);
  // Mono duplicates; stereo takes the second sample as it comes.
  const int16_t right = in_frame >= 4 ? this->scaled_(in[2], in[3]) : left;
  out[0] = (uint8_t) (left & 0xFF);
  out[1] = (uint8_t) ((left >> 8) & 0xFF);
  out[2] = (uint8_t) (right & 0xFF);
  out[3] = (uint8_t) ((right >> 8) & 0xFF);
  return 4;
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_SPEAKER
