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
}

void PortallBTSpeaker::start() {
  this->half_sample_ = 0;
  this->have_half_ = false;
  this->state_ = speaker::STATE_RUNNING;
}

void PortallBTSpeaker::stop() {
  this->have_half_ = false;
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
    this->have_half_ = false;
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

  if (this->audio_stream_info_.get_channels() >= 2) {
    // Already two channels: the bytes are the wire format as they stand.
    this->parent_->feed_audio(data, (uint32_t) length);
    return length;
  }

  this->play_mono_(data, length);
  return length;
}

void PortallBTSpeaker::play_mono_(const uint8_t *data, size_t length) {
  // A small buffer rather than a call per sample: feed_audio copies byte by
  // byte into a ring, so handing it 256 bytes at a time instead of 4 is the
  // difference between one loop and two.
  uint8_t out[256];
  size_t used = 0;

  // Finish the sample the last call ended in the middle of, if there was one.
  if (this->have_half_ && length > 0) {
    this->have_half_ = false;
    const uint8_t low = this->half_sample_;
    const uint8_t high = data[0];
    data += 1;
    length -= 1;
    out[used++] = low;
    out[used++] = high;
    out[used++] = low;
    out[used++] = high;
  }

  while (length >= 2) {
    if (used + 4 > sizeof(out)) {
      this->parent_->feed_audio(out, (uint32_t) used);
      used = 0;
    }
    out[used++] = data[0];
    out[used++] = data[1];
    out[used++] = data[0];
    out[used++] = data[1];
    data += 2;
    length -= 2;
  }

  if (used > 0)
    this->parent_->feed_audio(out, (uint32_t) used);

  // Carry the odd byte. See the header: half a sample kept as a whole one
  // would put the two channels out of step for the rest of the stream.
  if (length == 1) {
    this->half_sample_ = data[0];
    this->have_half_ = true;
  }
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_SPEAKER
