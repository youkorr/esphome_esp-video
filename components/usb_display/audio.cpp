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

#include "usb_display.h"

#include "esphome/core/log.h"
#include "esphome/core/hal.h"

extern "C" {
#include "tusb.h"
}

#if CFG_TUD_AUDIO

extern "C" {
#include "usb_device_uac.h"
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.audio";

namespace {

/* usb_device_uac's callbacks carry a context pointer, but the volume the host
 * sets arrives before anything else and is wanted by the number entity too. */
USBDisplay *audio_owner(void *ctx) { return static_cast<USBDisplay *>(ctx); }

esp_err_t uac_output(uint8_t *buf, size_t len, void *ctx) {
  audio_owner(ctx)->on_usb_audio(buf, len);
  return ESP_OK;
}

void uac_set_mute(uint32_t mute, void *ctx) { audio_owner(ctx)->on_usb_audio_mute(mute != 0); }

void uac_set_volume(uint32_t volume, void *ctx) { audio_owner(ctx)->on_usb_audio_volume((float) volume / 100.0f); }

}  // namespace

void USBDisplay::setup_audio_() {
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
  ESP_LOGCONFIG(TAG, "Speaker reported to the host: %d Hz, %d bit, %d channel", CONFIG_UAC_SAMPLE_RATE,
                CONFIG_UAC_BIT_RESOLUTION, CONFIG_UAC_SPEAKER_CHANNEL_NUM);
}

void USBDisplay::on_usb_audio(const uint8_t *data, size_t length) {
  if (this->speaker_ == nullptr || length == 0)
    return;
  // Before the mute and volume checks: the host is sending, whatever this board
  // then decides to do with it.
  this->last_audio_ms_ = millis();
  if (this->audio_muted_ || this->audio_volume_ <= 0.0f)
    return;

  if (!this->speaker_->is_running())
    this->speaker_->start();

  // The two-argument form: the one taking a timeout is compiled conditionally,
  // and this one is the interface every speaker implements.
  this->speaker_->play(data, length);

  if (!this->logged_first_audio_) {
    this->logged_first_audio_ = true;
    ESP_LOGI(TAG, "First audio from the host: %u bytes", (unsigned) length);
  }
}

void USBDisplay::on_usb_audio_volume(float volume) {
  this->audio_volume_ = volume;
  if (this->speaker_ != nullptr)
    this->speaker_->set_volume(volume);
  ESP_LOGD(TAG, "Host set the volume to %.0f%%", volume * 100.0f);
}

void USBDisplay::on_usb_audio_mute(bool muted) {
  this->audio_muted_ = muted;
  if (this->speaker_ != nullptr)
    this->speaker_->set_mute_state(muted);
  ESP_LOGD(TAG, "Host %s the sound", muted ? "muted" : "unmuted");
}

}  // namespace usb_display
}  // namespace esphome

#endif  // CFG_TUD_AUDIO
