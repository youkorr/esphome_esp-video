/* The speaker, as a USB Audio Class device.
 *
 * The host sees a sound card and sends it audio; it lands here and goes to an
 * ESPHome speaker. USB Audio is a standard class, so this needs nothing
 * installed on the other end -- unlike the picture, which has no class to
 * belong to.
 *
 * Espressif's usb_device_uac component owns the streaming: it drives the
 * isochronous endpoint, answers the class requests and hands over whole buffers
 * through the callbacks below. What it does not own is the TinyUSB audio
 * function description, which is in this component's tusb_config.h.
 *
 * The speaker this plays into is deliberately any ESPHome speaker, which is
 * what lets it be a mixer input alongside a media player and a voice assistant
 * rather than fighting them for the same I2S bus.
 */

#include "portall.h"

#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include "esphome/components/audio/audio.h"

extern "C" {
#include "tusb.h"
}

#ifdef USE_SPEAKER

#if CFG_TUD_AUDIO
extern "C" {
#include "usb_device_uac.h"
}
#endif

namespace esphome {
namespace portall {

static const char *const TAG = "portall.audio";

// How much audio to gather before handing it to the speaker. Far more than the
// 125 microseconds a High-Speed host sends at, far less than anyone hears as
// delay -- and small next to whatever buffer the speaker keeps, because a block
// that fills most of it cannot survive the slightest jitter.
static constexpr uint32_t AUDIO_BLOCK_MS = 10;

#if CFG_TUD_AUDIO
namespace {

/* usb_device_uac's callbacks carry a context pointer, but the volume the host
 * sets arrives before anything else and is wanted by the number entity too. */
Portall *audio_owner(void *ctx) { return static_cast<Portall *>(ctx); }

esp_err_t uac_output(uint8_t *buf, size_t len, void *ctx) {
  audio_owner(ctx)->on_audio_samples(buf, len);
  return ESP_OK;
}

void uac_set_mute(uint32_t mute, void *ctx) { audio_owner(ctx)->on_usb_audio_mute(mute != 0); }

void uac_set_volume(uint32_t volume, void *ctx) { audio_owner(ctx)->on_usb_audio_volume((float) volume / 100.0f); }

}  // namespace
#endif  // CFG_TUD_AUDIO

void Portall::setup_speaker_() {
  /* The blocking buffer, and telling the speaker what is coming.
   *
   * Both halves of the audio path need this and neither owns it. USB hands
   * over six samples at a time at High Speed -- twelve bytes, eight thousand
   * times a second -- and an ESPHome speaker will not take writes that small
   * at that rate; refusing them is what tore the stream into a crackle. The
   * network hands over whatever fitted in a payload. Either way the samples
   * are gathered here into blocks the speaker can use.
   */
  if (this->speaker_ == nullptr || this->audio_block_ != nullptr)
    return;
  this->audio_block_size_ = (size_t) (PORTALL_AUDIO_RATE / 1000) * AUDIO_BLOCK_MS *
                            (PORTALL_AUDIO_BITS / 8) * PORTALL_AUDIO_CHANNELS;
  this->audio_block_ = new uint8_t[this->audio_block_size_];

  // Tell the speaker what is coming before a byte of it does. Without this it
  // keeps ESPHome's historical default of 16 kHz mono, and a mixer asked to
  // combine that with a 48 kHz source refuses the stream outright -- which is
  // both the "Incompatible audio streams" error and the noise that comes out
  // when the samples are read at the wrong rate.
  this->speaker_->set_audio_stream_info(
      audio::AudioStreamInfo(PORTALL_AUDIO_BITS, PORTALL_AUDIO_CHANNELS, PORTALL_AUDIO_RATE));
  ESP_LOGCONFIG(TAG, "Speaker: %d Hz, %d bit, %d channel, %u byte blocks", PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS,
                PORTALL_AUDIO_CHANNELS, (unsigned) this->audio_block_size_);
}

#if CFG_TUD_AUDIO
void Portall::setup_uac_() {
  uac_device_config_t config = {};
  // The display already brought TinyUSB and the PHY up; this is one function of
  // that device, not a device of its own.
  config.skip_tinyusb_init = true;
  config.output_cb = uac_output;
  config.input_cb = nullptr;  // Playback only; the microphone is a later job.
  config.set_mute_cb = uac_set_mute;
  config.set_volume_cb = uac_set_volume;
  config.cb_ctx = this;
  config.spk_itf_num = ITF_NUM_AUDIO_STREAMING_SPK;
  config.mic_itf_num = -1;

  esp_err_t err = uac_device_init(&config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "uac_device_init() failed: %s", esp_err_to_name(err));
    this->mark_failed(LOG_STR("USB audio unavailable"));
    return;
  }
  ESP_LOGCONFIG(TAG, "Speaker reported to the USB host: %d Hz, %d bit, %d channel", CONFIG_UAC_SAMPLE_RATE,
                CONFIG_UAC_BIT_RESOLUTION, CONFIG_UAC_SPEAKER_CHANNEL_NUM);
}
#endif  // CFG_TUD_AUDIO

void Portall::on_audio_samples(const uint8_t *data, size_t length) {
  if (this->speaker_ == nullptr || length == 0)
    return;
  // Before the mute and volume checks: the host is sending, whatever this board
  // then decides to do with it.
  this->last_audio_ms_ = millis();
  this->last_packet_len_ = length;
  if (this->audio_muted_ || this->audio_volume_ <= 0.0f)
    return;

  if (!this->speaker_->is_running())
    this->speaker_->start();

  while (length > 0) {
    const size_t room = this->audio_block_size_ - this->audio_block_used_;
    const size_t take = length < room ? length : room;
    memcpy(this->audio_block_ + this->audio_block_used_, data, take);
    this->audio_block_used_ += take;
    data += take;
    length -= take;
    if (this->audio_block_used_ < this->audio_block_size_)
      break;
    this->flush_audio_block_();
  }

  if (!this->logged_first_audio_) {
    this->logged_first_audio_ = true;
    ESP_LOGI(TAG, "First audio: %u byte packets at %d Hz, %d bit, %d channel, played %u at a time",
             (unsigned) this->last_packet_len_, PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS, PORTALL_AUDIO_CHANNELS,
             (unsigned) this->audio_block_size_);
  }
}

void Portall::flush_audio_block_() {
  // The two-argument form: the one taking a timeout is compiled conditionally,
  // and this one is the interface every speaker implements. It reports how much
  // it took; audio it would not take is audio it was not ready for, and the
  // host is already sending the next block.
  const size_t length = this->audio_block_used_;
  const size_t written = this->speaker_->play(this->audio_block_, length);
  if (written > 0)
    this->audio_ever_accepted_ = true;
  if (written == length) {
    this->audio_block_used_ = 0;
    return;
  }

  // Whatever the speaker would not take stays where it is and goes out at the
  // front of the next block. Throwing it away instead cuts the wave mid-sample,
  // and a cut like that is a click -- which is what a stream that starts clean
  // and turns gritty is made of, one truncated block at a time.
  const size_t carried = length - written;
  memmove(this->audio_block_, this->audio_block_ + written, carried);
  this->audio_block_used_ = carried;

  this->audio_underruns_++;
  if (this->audio_underruns_ == 1 || this->audio_underruns_ % 500 == 0) {
    ESP_LOGW(TAG, "The speaker took %u of %u bytes, %u carried over (%u times so far)", (unsigned) written,
             (unsigned) length, (unsigned) carried, (unsigned) this->audio_underruns_);
  }

  // Carrying over only works while the speaker eventually catches up. If a
  // whole block comes back untouched there is no room to gather the next one
  // and the audio is genuinely arriving faster than it can leave; start again
  // rather than growing a delay that never drains.
  if (written == 0 && carried >= this->audio_block_size_) {
    this->audio_block_used_ = 0;
    this->audio_resyncs_++;
    if (this->audio_resyncs_ == 1 || this->audio_resyncs_ % 100 == 0) {
      if (this->audio_ever_accepted_) {
        // It has worked before, so this is the speaker falling behind: the
        // panel is busy, or something else is holding the bus.
        ESP_LOGW(TAG, "Dropped a block: the speaker is not draining (%u times so far)",
                 (unsigned) this->audio_resyncs_);
      } else {
        // It has NEVER taken a byte, which is a different fault entirely and
        // was reported as this same line repeating a hundred times a second.
        //
        // An ESPHome mixer refuses a source whose sample rate is not the one
        // it is already running at: MixerSpeaker::start() returns
        // ESP_ERR_INVALID_ARG, the source speaker is marked "Incompatible
        // audio streams" and never gets a ring buffer, so every play() after
        // that returns zero for as long as the board is up. portall sends
        // 48000 Hz because that is what a browser produces, and these panels
        // run their I2S at 44100 -- so a speaker_id: pointing straight at a
        // mixer input, or at the raw I2S speaker under one, can never work.
        //
        // The fix is a resampler between the two, which is what
        // yaml/guition-10-home-assistant.yaml wires up.
        ESP_LOGE(TAG,
                 "The speaker has not taken a single byte in %u blocks. It is refusing this stream rather than "
                 "falling behind: portall sends %d Hz, %d bit, %d channel, and an ESPHome mixer refuses a source "
                 "whose rate is not the one it already runs at. Point speaker_id: at a resampler whose "
                 "output_speaker: is the mixer input, not at the mixer input or the I2S speaker itself -- see "
                 "yaml/guition-10-home-assistant.yaml. Look for \"Incompatible audio streams\" on the speaker "
                 "component above.",
                 (unsigned) this->audio_resyncs_, PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS, PORTALL_AUDIO_CHANNELS);
      }
    }
  }
}

void Portall::set_audio_volume(float volume) {
  // Clamped rather than refused. The mistake this will actually see is a
  // slider that runs 0 to 100 handed over without dividing, and a panel that
  // shouts once is better than one that fails to boot -- but it says so, or
  // the sound would simply be at full and nobody would know why.
  if (volume < 0.0f || volume > 1.0f) {
    ESP_LOGW(TAG, "A volume of %.2f is outside 0 to 1 and has been clamped. A slider that runs to 100 wants "
                  "!lambda 'return x / 100.0;'",
             volume);
    volume = volume < 0.0f ? 0.0f : 1.0f;
  }
  this->audio_volume_ = volume;
  if (this->speaker_ != nullptr)
    this->speaker_->set_volume(volume);
  ESP_LOGD(TAG, "Volume set to %.0f%%", volume * 100.0f);
}

void Portall::on_usb_audio_mute(bool muted) {
  this->audio_muted_ = muted;
  if (this->speaker_ != nullptr)
    this->speaker_->set_mute_state(muted);
  ESP_LOGD(TAG, "Host %s the sound", muted ? "muted" : "unmuted");
}

}  // namespace portall
}  // namespace esphome

#endif  // USE_SPEAKER
