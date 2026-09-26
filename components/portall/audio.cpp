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
#if CONFIG_USB_DISPLAY_DEVICE
#include "tusb.h"
#endif
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
// One second of blocks: how long a speaker that has never taken anything is
// given before it is called a speaker that refuses the stream.
static constexpr uint32_t NEVER_ACCEPTED_BLOCKS = 1000 / AUDIO_BLOCK_MS;
// A speaker that refuses this many blocks in a row WITHOUT ever reaching
// running is one that will not stay started, and is left alone for a while.
// A healthy start takes one or two turns of ESPHome's loop, a few blocks.
static constexpr uint32_t REFUSED_BLOCKS = 200 / AUDIO_BLOCK_MS;
// How long it is left alone: doubling from the first to the last, and back to
// the first once it really plays.
static constexpr uint32_t REFUSED_HOLD_FIRST_MS = 2000;
static constexpr uint32_t REFUSED_HOLD_MAX_MS = 30000;

static size_t audio_block_bytes(unsigned channels) {
  return (size_t) (PORTALL_AUDIO_RATE / 1000) * AUDIO_BLOCK_MS * (PORTALL_AUDIO_BITS / 8) * channels;
}

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
  // Allocated for the most channels a stream may carry, once, so a change of
  // shape never allocates on the audio path; only the size in use moves.
  this->audio_block_ = new uint8_t[audio_block_bytes(PORTALL_AUDIO_MAX_CHANNELS)];
  this->audio_block_size_ = audio_block_bytes(this->audio_channels_);

  // Tell the speaker what is coming before a byte of it does. Without this it
  // keeps ESPHome's historical default of 16 kHz mono, and a mixer asked to
  // combine that with a 48 kHz source refuses the stream outright -- which is
  // both the "Incompatible audio streams" error and the noise that comes out
  // when the samples are read at the wrong rate.
  this->speaker_->set_audio_stream_info(
      audio::AudioStreamInfo(PORTALL_AUDIO_BITS, this->audio_channels_, PORTALL_AUDIO_RATE));
  ESP_LOGCONFIG(TAG, "Speaker: %d Hz, %d bit, %u channel, %u byte blocks (a sender may switch it to stereo)",
                PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS, (unsigned) this->audio_channels_,
                (unsigned) this->audio_block_size_);
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

void Portall::on_audio_samples(const uint8_t *data, size_t length, uint8_t channels) {
  if (this->speaker_ == nullptr || length == 0 || this->audio_block_ == nullptr)
    return;
  // Before the mute and volume checks: the host is sending, whatever this board
  // then decides to do with it.
  this->last_audio_ms_ = millis();
  this->last_packet_len_ = length;

  if (channels != this->audio_channels_) {
    // The sender changed between mono and stereo -- which happens when the
    // add-on's setting changes, not while anything plays. The speaker is told
    // the new shape before a byte of it arrives, exactly as at setup: fed two
    // channels while expecting one, it plays at half speed. A speaker still
    // running the old shape is stopped first, and what arrives while it
    // stops is dropped -- a few blocks, once, at the switch.
    if (!this->speaker_->is_stopped()) {
      this->speaker_->stop();
      return;
    }
    this->audio_channels_ = channels;
    this->audio_block_used_ = 0;
    this->audio_block_size_ = audio_block_bytes(channels);
    this->speaker_->set_audio_stream_info(audio::AudioStreamInfo(PORTALL_AUDIO_BITS, channels, PORTALL_AUDIO_RATE));
    ESP_LOGI(TAG, "The page's sound is %s now", channels >= 2 ? "stereo" : "mono");
  }
  if (this->audio_muted_ || this->audio_volume_ <= 0.0f)
    return;

  // Left alone after refusing to stay started; see flush_audio_block_().
  if (this->audio_hold_until_ms_ != 0) {
    if ((int32_t) (millis() - this->audio_hold_until_ms_) < 0)
      return;
    this->audio_hold_until_ms_ = 0;
  }

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
    ESP_LOGI(TAG, "First audio: %u byte packets at %d Hz, %d bit, %u channel, played %u at a time",
             (unsigned) this->last_packet_len_, PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS, (unsigned) this->audio_channels_,
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

  /* A speaker that will not STAY started is left alone, and the reason is a
   * panel that rebooted.
   *
   * Every ESPHome speaker starts itself from play() when it is stopped. A
   * mixer source that its mixer refuses -- a sample rate other than the one
   * the mixer is already running at, "Incompatible audio streams" -- goes
   * from starting straight back to stopped inside one turn of the loop,
   * creating its ring buffer and throwing it away on the way. Fed fifty
   * blocks a second, that is fifty ring buffers a second made on the loop and
   * written to from this task, and a panel logged exactly that for two
   * seconds before `assert failed: spinlock_acquire` took it down.
   *
   * The mixer takes its rate from the first source to play after boot, so
   * the order decides: an announcement at 44100 before the page at 48000
   * locks the page out until a restart, and the other way round silences the
   * answer. The fix is in the YAML; what belongs here is that sound must
   * never cost the panel. */
  if (written > 0 || this->speaker_->is_running()) {
    this->audio_refusals_ = 0;
    if (written > 0)
      this->audio_hold_ms_ = 0;
  } else if (++this->audio_refusals_ >= REFUSED_BLOCKS) {
    const bool first = this->audio_hold_ms_ == 0;
    this->audio_hold_ms_ = first ? REFUSED_HOLD_FIRST_MS
                                 : (this->audio_hold_ms_ * 2 > REFUSED_HOLD_MAX_MS ? REFUSED_HOLD_MAX_MS
                                                                                   : this->audio_hold_ms_ * 2);
    this->audio_hold_until_ms_ = millis() + this->audio_hold_ms_;
    if (this->audio_hold_until_ms_ == 0)
      this->audio_hold_until_ms_ = 1;
    this->audio_refusals_ = 0;
    this->audio_block_used_ = 0;
    if (first) {
      ESP_LOGW(TAG,
               "The speaker went back to stopped every time it was started (%u blocks in a row), so it is left "
               "alone for %u s at a time rather than started again fifty times a second, which is what rebooted "
               "a panel. The usual cause is a mixer: it runs at the rate of the first source to play after boot "
               "and refuses any other (look for \"Incompatible audio streams\"). portall sends %d Hz; give every "
               "source of that mixer one rate, with a resampler between portall and its mixer input as "
               "yaml/guition-voice-bluetooth.yaml does.",
               (unsigned) REFUSED_BLOCKS, (unsigned) (this->audio_hold_ms_ / 1000), PORTALL_AUDIO_RATE);
    } else {
      ESP_LOGW(TAG, "The speaker still will not stay started; trying again in %u s",
               (unsigned) (this->audio_hold_ms_ / 1000));
    }
    return;
  }
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
  // Not before the speaker has ever taken anything: until then the block
  // below says what is wrong, and says it once.
  if (this->audio_ever_accepted_ && (this->audio_underruns_ == 1 || this->audio_underruns_ % 500 == 0)) {
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
    // A speaker that has never taken anything is judged after a second of
    // refusing, not on the first block. That first block is handed over the
    // instant start() is called, and a mixer source starts from its own loop
    // afterwards, so it is refused every time on every panel -- which printed
    // the error below at the top of each stream that then played perfectly.
    const bool report = this->audio_ever_accepted_
                            ? (this->audio_resyncs_ == 1 || this->audio_resyncs_ % 100 == 0)
                            : (this->audio_resyncs_ == NEVER_ACCEPTED_BLOCKS || this->audio_resyncs_ % 500 == 0);
    if (report) {
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
        // audio streams" and every play() after that returns zero for as long
        // as the board is up. A source that falls straight back to stopped is
        // caught by the hold above before this is reached; this is what is
        // left of that case, a speaker that says it runs and takes nothing. portall sends
        // 48000 Hz because that is what a browser produces, and these panels
        // run their I2S at 44100 -- so a speaker_id: pointing straight at a
        // mixer input, or at the raw I2S speaker under one, can never work.
        //
        // The fix is for every source of one mixer to share a rate: run the
        // mixer at 48000 and put a resampler AFTER it, which is what
        // yaml/tab5-portall-bluetooth.yaml wires up, or put one between
        // portall and its mixer input.
        ESP_LOGE(TAG,
                 "The speaker has not taken a single byte in %u blocks. It is refusing this stream rather than "
                 "falling behind: portall sends %d Hz, %d bit, %u channel, and an ESPHome mixer refuses a source "
                 "whose rate is not the one it already runs at. Give every mixer source 48000 and put a "
                 "resampler after the mixer (see yaml/tab5-portall-bluetooth.yaml), or point speaker_id: at a "
                 "resampler in front of the mixer input. Look for \"Incompatible audio streams\" on the speaker "
                 "component above.",
                 (unsigned) this->audio_resyncs_, PORTALL_AUDIO_RATE, PORTALL_AUDIO_BITS,
                 (unsigned) this->audio_channels_);
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
