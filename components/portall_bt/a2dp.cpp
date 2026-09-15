/* A2DP source: the panel sends its sound to a Bluetooth speaker.
 *
 * This is the half of the Bluetooth work the ESP32-C6 can never do. A2DP is
 * Bluetooth CLASSIC, the C6 is BLE only, and the dongle is what closes that
 * gap -- so a car receiver, a pair of headphones or a garden speaker becomes
 * an output this panel has.
 *
 * WHAT THE HEADER SAYS, AND WHAT IT TOOK TO READ IT PROPERLY. There are two
 * A2DP source APIs in ESP-IDF 5.5.5 and picking the wrong one is a build
 * failure on somebody else's board:
 *
 *   esp_a2d_source_register_data_callback()   raw PCM, Bluedroid encodes
 *   esp_a2d_source_audio_data_send()          the application encodes SBC
 *
 * `CONFIG_BT_A2DP_USE_EXTERNAL_CODEC` chooses between them and defaults to n,
 * so the first one is what a default build uses. It is what this component
 * uses, and it costs nothing: Bluedroid carries its own SBC encoder, already
 * compiled, and paces the stream itself by PULLING.
 *
 * The second is where Espressif are going -- their Kconfig says "The internal
 * codec in A2DP will be removed in the future" -- and taking it now would mean
 * an SBC encoder in this component. Bluedroid's own is behind a PRIVATE
 * include directory, and `espressif/esp_audio_codec` ships its per-codec
 * headers only in the registry package. So the migration waits until one of
 * those can actually be read, and the deprecated path works today.
 *
 * THE FORMAT IS NOT A CHOICE. `btc_a2dp_source.c` says it in a comment:
 * "for now hardcode 44.1 khz 16 bit stereo PCM format". So the callback below
 * hands over 44100 Hz, signed 16-bit little-endian, TWO channels interleaved,
 * whatever the rest of this board runs at. portall's own page audio is 48 kHz
 * MONO, so feeding this from there needs a resample and a channel duplication
 * -- ESPHome has both as speaker components, and that is the next step rather
 * than this one.
 *
 * THE CALLBACK IS THE CLOCK, which is the whole reason this path is cheap.
 * Bluedroid asks for exactly as many bytes as it is about to encode, when it
 * is about to encode them, from its own task. Nothing here has to know what
 * time it is. The other API would have made this component responsible for
 * real time, and getting that wrong is a stutter nobody can diagnose.
 *
 * `len == -1` is a FLUSH rather than a read -- their documentation says so and
 * the return value is ignored for it. Treating it as a length would mean
 * reading an enormous buffer.
 */

#include "portall_bt.h"

#ifdef USE_ESP32

#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include <cmath>
#include <cstring>

#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
#include "esp_a2dp_api.h"
#include "esp_avrc_api.h"
#include "esp_bt_defs.h"
#include "esp_gap_bt_api.h"
#endif

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt";

// 44100 Hz, 16-bit, two channels: four bytes a frame, and every number below
// follows from that rather than being chosen.
static constexpr uint32_t A2DP_RATE = 44100;
static constexpr uint32_t A2DP_BYTES_PER_FRAME = 4;

#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)

static PortallBT *g_a2dp = nullptr;

// One tenth of a second of sound in hand. Enough to ride a scheduling hiccup
// on the producer's side, and small enough that what comes out is what is
// being played now rather than a backlog -- the same reasoning the sender's
// own half-second audio deque is built on, one order of magnitude tighter
// because this queue has no network under it.
static constexpr uint32_t PCM_RING = 17640;  // 0.1 s at 44100 x 4 bytes
static uint8_t g_pcm[PCM_RING];
static volatile uint32_t g_pcm_head = 0;
static volatile uint32_t g_pcm_tail = 0;

// Where the test tone has got to, in frames. Kept across calls because a sine
// restarted every callback is a click every callback.
static uint32_t g_tone_at = 0;

static uint32_t pcm_available() {
  const uint32_t head = g_pcm_head;
  const uint32_t tail = g_pcm_tail;
  return head >= tail ? head - tail : PCM_RING - tail + head;
}

/* Bluedroid asking for its next block of PCM, on its own A2DP task.
 *
 * It must always be filled. A2DP is a stream with a clock at the other end: a
 * short read is not "nothing to play just now", it is a gap in a stream the
 * sink is decoding at a fixed rate, which is a click. So a shortfall is filled
 * with silence and counted, rather than shortening the answer. */
static int32_t a2dp_pcm_cb(uint8_t *buf, int32_t len) {
  if (buf == nullptr || len < 0) {
    // -1 is a flush, not a length. Their own documentation says the return
    // value is ignored here; treating it as a size would read 2 GB.
    return 0;
  }
  if (g_a2dp != nullptr)
    return g_a2dp->fill_pcm(buf, (uint32_t) len);
  memset(buf, 0, (size_t) len);
  return len;
}

static void a2dp_cb(esp_a2d_cb_event_t event, esp_a2d_cb_param_t *param) {
  if (g_a2dp == nullptr)
    return;
  switch (event) {
    case ESP_A2D_PROF_STATE_EVT:
      if (param->a2d_prof_stat.init_state == ESP_A2D_INIT_SUCCESS)
        g_a2dp->on_a2dp_ready();
      break;

    case ESP_A2D_CONNECTION_STATE_EVT:
      if (param->conn_stat.state == ESP_A2D_CONNECTION_STATE_CONNECTED) {
        g_a2dp->on_a2dp_open(param->conn_stat.remote_bda);
      } else if (param->conn_stat.state == ESP_A2D_CONNECTION_STATE_DISCONNECTED) {
        g_a2dp->on_a2dp_closed(param->conn_stat.disc_rsn == ESP_A2D_DISC_RSN_ABNORMAL);
      }
      break;

    case ESP_A2D_MEDIA_CTRL_ACK_EVT:
      // The handshake is two steps and the second only makes sense after the
      // first: ask whether the source side is ready, and only then start.
      // Sending START straight after connecting is refused by some sinks.
      if (param->media_ctrl_stat.cmd == ESP_A2D_MEDIA_CTRL_CHECK_SRC_RDY &&
          param->media_ctrl_stat.status == ESP_A2D_MEDIA_CTRL_ACK_SUCCESS) {
        esp_a2d_media_ctrl(ESP_A2D_MEDIA_CTRL_START);
      } else if (param->media_ctrl_stat.cmd == ESP_A2D_MEDIA_CTRL_START &&
                 param->media_ctrl_stat.status != ESP_A2D_MEDIA_CTRL_ACK_SUCCESS) {
        ESP_LOGW(TAG, "the speaker refused to start the stream (status %d)",
                 (int) param->media_ctrl_stat.status);
      }
      break;

    case ESP_A2D_AUDIO_STATE_EVT:
      g_a2dp->on_a2dp_audio(param->audio_stat.state == ESP_A2D_AUDIO_STATE_STARTED);
      break;

    default:
      break;
  }
}

/* AVRCP, and the panel's TARGET role is the one that matters.
 *
 * A speaker that has just connected asks for a second L2CAP channel, and a
 * panel that has not registered AVRCP refuses it. From a real car receiver's
 * first connection:
 *
 *     W BT_L2CAP: L2CAP - rcvd conn req for unknown PSM: 23
 *
 * 23 is 0x17, AVCTP, which is the channel AVRCP runs over -- so that line is
 * the device asking for its own buttons and being told no. On a car kit that
 * is the steering wheel, the volume knob and the track buttons; on a pair of
 * headphones it is the one on the earcup.
 *
 * The panel is the TARGET because the panel is the player: the target is the
 * end that RECEIVES play, pause and next. The other end, the controller, is
 * the thing with the buttons on it. Getting that the wrong way round would
 * register the role that sends commands to something that has no player.
 *
 * The supported command set is copied from the ALLOWED one rather than listed
 * here, which is how Espressif's own examples do it and is the only version
 * that cannot go stale: the stack says what it can carry, and this says yes to
 * all of it. A hand-written list would quietly stop supporting whatever was
 * added to the specification after it was typed.
 */
static const char *key_name(uint8_t code) {
  switch (code) {
    case ESP_AVRC_PT_CMD_PLAY: return "play";
    case ESP_AVRC_PT_CMD_PAUSE: return "pause";
    case ESP_AVRC_PT_CMD_STOP: return "stop";
    case ESP_AVRC_PT_CMD_FORWARD: return "next";
    case ESP_AVRC_PT_CMD_BACKWARD: return "previous";
    case ESP_AVRC_PT_CMD_FAST_FORWARD: return "fast forward";
    case ESP_AVRC_PT_CMD_REWIND: return "rewind";
    case ESP_AVRC_PT_CMD_VOL_UP: return "volume up";
    case ESP_AVRC_PT_CMD_VOL_DOWN: return "volume down";
    case ESP_AVRC_PT_CMD_MUTE: return "mute";
    case ESP_AVRC_PT_CMD_POWER: return "power";
    default: return "";
  }
}

static void avrcp_tg_cb(esp_avrc_tg_cb_event_t event, esp_avrc_tg_cb_param_t *param) {
  if (g_a2dp == nullptr)
    return;
  switch (event) {
    case ESP_AVRC_TG_CONNECTION_STATE_EVT:
      ESP_LOGI(TAG, "the speaker's buttons are %s",
               param->conn_stat.connected ? "connected" : "disconnected");
      break;

    case ESP_AVRC_TG_PASSTHROUGH_CMD_EVT:
      g_a2dp->on_media_key(param->psth_cmd.key_code,
                           param->psth_cmd.key_state == ESP_AVRC_PT_CMD_STATE_PRESSED);
      break;

    case ESP_AVRC_TG_SET_ABSOLUTE_VOLUME_CMD_EVT:
      // AVRCP carries volume as 0..127, not 0..100 and not 0..255.
      g_a2dp->on_media_volume((float) param->set_abs_vol.volume / 127.0f);
      break;

    default:
      break;
  }
}

static void start_avrcp() {
  esp_avrc_tg_register_callback(avrcp_tg_cb);
  const esp_err_t err = esp_avrc_tg_init();
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "AVRCP would not start (%d); the speaker's buttons will do nothing", (int) err);
    return;
  }
  esp_avrc_psth_bit_mask_t allowed = {};
  if (esp_avrc_tg_get_psth_cmd_filter(ESP_AVRC_PSTH_FILTER_ALLOWED_CMD, &allowed) == ESP_OK)
    esp_avrc_tg_set_psth_cmd_filter(ESP_AVRC_PSTH_FILTER_SUPPORTED_CMD, &allowed);
}

#endif  // CONFIG_BT_BLUEDROID_ENABLED && CONFIG_BT_A2DP_ENABLE

// ---------------------------------------------------------------------------

void PortallBT::on_media_key(uint8_t code, bool pressed) {
  const uint8_t next = (uint8_t) ((this->key_head_ + 1) % MEDIA_KEYS);
  if (next == this->key_tail_)
    this->key_tail_ = (uint8_t) ((this->key_tail_ + 1) % MEDIA_KEYS);
  this->keys_[this->key_head_] = MediaKey{code, pressed};
  this->key_head_ = next;
}

void PortallBT::on_media_volume(float fraction) {
  this->media_volume_ = fraction;
  this->media_volume_fresh_ = true;
}

void PortallBT::drain_media_() {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  while (this->key_tail_ != this->key_head_) {
    const MediaKey key = this->keys_[this->key_tail_];
    this->key_tail_ = (uint8_t) ((this->key_tail_ + 1) % MEDIA_KEYS);
    const char *name = key_name(key.code);
    ESP_LOGI(TAG, "button %02x%s%s %s", key.code, name[0] != '\0' ? " " : "", name,
             key.pressed ? "pressed" : "released");
    for (auto *trigger : this->media_key_triggers_)
      trigger->trigger(key.code, key.pressed);
  }
  if (this->media_volume_fresh_) {
    this->media_volume_fresh_ = false;
    ESP_LOGI(TAG, "the speaker asked for volume %.0f%%", this->media_volume_ * 100.0f);
    for (auto *trigger : this->media_volume_triggers_)
      trigger->trigger(this->media_volume_);
  }
#endif
}

void PortallBT::start_a2dp_() {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  if (!this->a2dp_)
    return;
  g_a2dp = this;

  // BEFORE the A2DP calls, and that is not a preference: esp_a2d_source_init's
  // own documentation says "If you want to use AVRC together, you should
  // initiate AVRC first." The same shape as attaching the HCI driver before
  // esp_bluedroid_init -- an ordering written down in a header, which this
  // component has already been caught getting wrong by not reading one.
  start_avrcp();

  esp_a2d_register_callback(a2dp_cb);
  // The deprecated entry point, deliberately. See this file's header: it is
  // what a default build supports, and it makes Bluedroid the clock.
  const esp_err_t reg = esp_a2d_source_register_data_callback(a2dp_pcm_cb);
  if (reg != ESP_OK) {
    ESP_LOGE(TAG, "A2DP would not take the PCM callback (%d)", (int) reg);
    return;
  }
  const esp_err_t err = esp_a2d_source_init();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "A2DP source would not start (%d)", (int) err);
    return;
  }
  ESP_LOGI(TAG, "A2DP source starting -- 44100 Hz, 16-bit, stereo (Bluedroid encodes)");
#elif defined(CONFIG_BT_BLUEDROID_ENABLED)
  if (this->a2dp_)
    ESP_LOGE(TAG, "audio: true was asked for and CONFIG_BT_A2DP_ENABLE is not set");
#endif
}

uint32_t PortallBT::fill_pcm(uint8_t *buf, uint32_t len) {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  uint32_t written = 0;

  // Whatever has been fed in, first.
  while (written < len && g_pcm_tail != g_pcm_head) {
    buf[written++] = g_pcm[g_pcm_tail];
    g_pcm_tail = (g_pcm_tail + 1) % PCM_RING;
  }

  if (written < len && this->test_tone_hz_ != 0) {
    // A sine, in frames rather than bytes, so the two channels cannot drift
    // apart and the phase carries across calls.
    const uint32_t frames = (len - written) / A2DP_BYTES_PER_FRAME;
    for (uint32_t i = 0; i < frames; i++) {
      const float t = (float) (g_tone_at + i) / (float) A2DP_RATE;
      const int16_t sample = (int16_t) (8000.0f * sinf(6.2831853f * (float) this->test_tone_hz_ * t));
      for (uint8_t channel = 0; channel < 2; channel++) {
        buf[written++] = (uint8_t) (sample & 0xFF);
        buf[written++] = (uint8_t) ((sample >> 8) & 0xFF);
      }
    }
    g_tone_at += frames;
  }

  if (written < len) {
    // Silence rather than a short answer: see this file's header. Counted so
    // a starved stream says so instead of merely sounding wrong.
    memset(buf + written, 0, len - written);
    this->pcm_starved_ += len - written;
    written = len;
  }
  return written;
#else
  memset(buf, 0, len);
  (void) this;
  return len;
#endif
}

void PortallBT::feed_audio(const uint8_t *data, uint32_t len) {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  for (uint32_t i = 0; i < len; i++) {
    const uint32_t next = (g_pcm_head + 1) % PCM_RING;
    if (next == g_pcm_tail) {
      // Full. Drop the OLDEST, because sound whose moment has passed is worth
      // less than the sound being played now -- the same choice the sender's
      // own audio deque makes, for the same reason.
      g_pcm_tail = (g_pcm_tail + 1) % PCM_RING;
      this->pcm_dropped_++;
    }
    g_pcm[g_pcm_head] = data[i];
    g_pcm_head = next;
  }
#else
  (void) data;
  (void) len;
#endif
}

uint32_t PortallBT::pcm_queued() const {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  return pcm_available();
#else
  return 0;
#endif
}

void PortallBT::on_a2dp_ready() {
  this->a2dp_up_ = true;
  // Only now is there a profile to connect with, so this is where a
  // remembered speaker is first asked for. Same rule as the HID side.
  this->reconnect_due_ms_ = millis();
  if (this->reconnect_backoff_ms_ == 0)
    this->reconnect_backoff_ms_ = 2000;
}

void PortallBT::on_a2dp_open(const uint8_t *addr) {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  ESP_LOGI(TAG, "speaker %02X:%02X:%02X:%02X:%02X:%02X is connected", addr[0], addr[1], addr[2], addr[3],
           addr[4], addr[5]);
  this->a2dp_open_ = true;
  memcpy(this->open_sink_, addr, 6);
  this->remember_sink_(addr);
  // Ask before starting. The reply comes back as ESP_A2D_MEDIA_CTRL_ACK_EVT.
  esp_a2d_media_ctrl(ESP_A2D_MEDIA_CTRL_CHECK_SRC_RDY);
#else
  (void) addr;
#endif
}

void PortallBT::on_a2dp_closed(bool abnormal) {
  if (this->a2dp_open_) {
    ESP_LOGI(TAG, "speaker disconnected%s; it will be asked for again by address, with no scan",
             abnormal ? " (signal lost)" : "");
  }
  this->a2dp_open_ = false;
  memset(this->open_sink_, 0, 6);
  this->a2dp_playing_ = false;
  this->reconnect_backoff_ms_ = 2000;
  this->reconnect_due_ms_ = millis() + 2000;
}

void PortallBT::on_a2dp_audio(bool started) {
  if (started == this->a2dp_playing_)
    return;
  this->a2dp_playing_ = started;
  ESP_LOGI(TAG, "audio stream %s", started ? "started" : "suspended");
  if (!started && this->pcm_starved_ != 0) {
    ESP_LOGW(TAG, "  %u bytes of silence were sent for want of anything to play",
             (unsigned) this->pcm_starved_);
    this->pcm_starved_ = 0;
  }
}

void PortallBT::remember_sink_(const uint8_t *addr) {
#if defined(CONFIG_BT_BLUEDROID_ENABLED)
  if (this->remembered_.has_sink && memcmp(this->remembered_.sink, addr, 6) == 0)
    return;
  memcpy(this->remembered_.sink, addr, 6);
  this->remembered_.has_sink = true;
  this->remembered_pref_.save(&this->remembered_);
  ESP_LOGI(TAG, "remembering %02X:%02X:%02X:%02X:%02X:%02X as this panel's speaker", addr[0], addr[1],
           addr[2], addr[3], addr[4], addr[5]);
#else
  (void) addr;
#endif
}

void PortallBT::a2dp_reconnect_() {
#if defined(CONFIG_BT_BLUEDROID_ENABLED) && defined(CONFIG_BT_A2DP_ENABLE)
  if (!this->a2dp_ || !this->a2dp_up_ || this->a2dp_open_ || !this->remembered_.has_sink)
    return;
  ESP_LOGD(TAG, "asking the speaker to connect (no scan, by address)");
  esp_a2d_source_connect(this->remembered_.sink);
#endif
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_ESP32
