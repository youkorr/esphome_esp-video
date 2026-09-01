#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/defines.h"
#include "esphome/components/display/display.h"
#ifdef USE_TOUCHSCREEN
#include "esphome/components/touchscreen/touchscreen.h"
#endif
#ifdef USE_SPEAKER
#include "esphome/components/speaker/speaker.h"
#endif

#include <cstdint>

/* What a PCM payload carries, and what the USB audio class is configured for:
 * the two must agree, because they share one speaker and one block buffer.
 * Mono because these panels have one speaker and it halves what the network
 * carries -- 48 kHz of 16-bit mono is 96 KiB/s beside a picture that can want
 * two megabytes. */
#define PORTALL_AUDIO_RATE 48000
#define PORTALL_AUDIO_BITS 16
#define PORTALL_AUDIO_CHANNELS 1

extern "C" {
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "driver/jpeg_decode.h"
#include "driver/ppa.h"
#include "tusb.h"
#include "usb_descriptors.h"
}

namespace esphome {
namespace portall {

/// Contacts carried back to the network sender in one message. The same five a
/// digitizer reports over HID, so neither path is the narrower one.
static constexpr uint8_t UDISP_NET_TOUCH_MAX = 5;

/**
 * @brief Turns the ESP32-P4 into a second monitor for a PC over USB.
 *
 * USB has no standard display class, so this is Espressif's udisp protocol: a
 * vendor interface over which a PC application pushes JPEG-encoded frames of a
 * screen region. Each frame is decoded by the P4's hardware JPEG decoder and
 * drawn to an ESPHome display.
 *
 * The PC side is not optional and is not part of this component -- see the
 * windows_driver directory of Espressif's usb_extend_screen example.
 */
class Portall : public Component
#ifdef USE_TOUCHSCREEN
    ,
                   public touchscreen::TouchListener
#endif
{
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  // After the display, whose panel this draws into.
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_display(display::Display *display) { this->display_ = display; }
  void set_resolution(uint16_t width, uint16_t height) {
    this->width_ = width;
    this->height_ = height;
  }
  /// The size the host actually draws on, when that is smaller than the panel.
  ///
  /// Rendering at the panel's size and sending it is not the only option. The
  /// machine at the other end pays for every pixel four times over -- painting
  /// it, encoding it, comparing it with the last one, encoding it again -- and
  /// the accelerator on this board can scale a picture up for nothing, being a
  /// DMA engine that is otherwise idle. So the host may draw smaller and say
  /// so, and what arrives is stretched to the panel on the way to it.
  void set_render_resolution(uint16_t width, uint16_t height) {
    this->render_width_ = width;
    this->render_height_ = height;
  }
  void set_frame_buffers(uint8_t count) { this->frame_buffer_count_ = count; }
  void set_max_frame_bytes(size_t bytes) { this->max_frame_bytes_ = bytes; }
  /// Also enforced here, not only advertised: a host that ignores the figure
  /// otherwise spends this board's whole budget on frames it cannot draw.
  void set_max_fps(uint8_t fps) { this->min_frame_interval_ms_ = fps > 0 ? 1000u / fps : 0; }
  /// Clockwise, in degrees; 0, 90, 180 or 270. Done by the P4's pixel-processing
  /// accelerator, so it costs no CPU.
  void set_rotation(uint16_t degrees) { this->rotation_ = degrees; }
  // How much the accelerator moves per burst. Measured in this author's LVGL
  // work on the same silicon: a 64-byte burst leaves more external-memory
  // bandwidth for the MIPI-DSI controller's own fetch of the framebuffer,
  // which is the thing this competes with; a 128-byte burst gets the
  // accelerator through its own work faster. Which one wins depends on how
  // much of the panel is being redrawn, so it is measured rather than
  // assumed -- the stats line reports the time spent in the accelerator.
  void set_ppa_burst(uint16_t bytes) { this->ppa_burst_ = bytes; }
  /// TCP port to accept frames on, in addition to the USB interface. Zero
  /// leaves the board USB-only.
  void set_port(uint16_t port) { this->port_ = port; }
  /// The PC-side sender, compiled in so the board can hand it over on a
  /// read-only drive instead of sending the user to find it.
  void set_sender_script(const uint8_t *data, size_t length) {
    this->sender_script_ = data;
    this->sender_script_len_ = length;
  }

#ifdef USE_SPEAKER
  void set_speaker(speaker::Speaker *speaker) { this->speaker_ = speaker; }
  /* One buffer of PCM for the speaker, from whichever way it arrived: the USB
   * audio class, or a UDISP_TYPE_PCM payload off the socket. The two differ
   * only in how the bytes got here -- the blocking, the volume and the
   * underrun handling below are the same work either way, and were written
   * once for USB before there was another way in. */
  void on_audio_samples(const uint8_t *data, size_t length);
  /* Both ways in share one volume. on_usb_audio_volume is the USB class's
   * callback and set_audio_volume is what everything else calls -- the number
   * entity, the action, and anything added later. */
  void set_audio_volume(float volume);
  void on_usb_audio_volume(float volume) { this->set_audio_volume(volume); }
  void on_usb_audio_mute(bool muted);
  float get_audio_volume() const { return this->audio_volume_; }
#endif

#ifdef USE_TOUCHSCREEN
  void set_touchscreen(touchscreen::Touchscreen *touchscreen) { this->touchscreen_ = touchscreen; }
  // Every poll of the touch screen, with all contacts currently down. Touches
  // go two ways at once where both are available: to the host as HID, which is
  // what makes a USB-attached panel a real digitizer, and back up the network
  // socket to whoever is sending the picture, which is what makes a dashboard
  // rendered elsewhere pressable here.
  void update(const touchscreen::TouchPoints_t &points) override;
  void release() override;
#endif

  /// Fired from the loop when the host starts and stops sending sound. What a
  /// board has to do about that is its own business -- switch an amplifier on,
  /// stand a wake word down off a shared I2S bus -- and none of it belongs
  /// here.
  void set_audio_start_trigger(Trigger<> *trigger) { this->audio_start_trigger_ = trigger; }
  void set_audio_stop_trigger(Trigger<> *trigger) { this->audio_stop_trigger_ = trigger; }

  // Called from TinyUSB's receive callback; see the .cpp for why this is
  // reachable from C.
  void on_vendor_rx(uint8_t itf);
  /// One chunk of the host's stream, whatever carried it here.
  ///
  /// may_wait says whether this transport can be made to slow down. A socket
  /// can: stop reading it and the sender blocks, which costs a moment and
  /// loses nothing. A USB endpoint cannot -- the host writes whether or not
  /// anyone is listening -- so there the only answer to a full queue is to
  /// throw the frame away.
  void feed_(const uint8_t *data, size_t len, bool may_wait = false);
  /// Set when the host selects a configuration, which it only does once a
  /// driver has claimed the device.
  void set_configured() { this->configured_ = true; }

  /// Tell whoever is sending the picture whether anyone can see it.
  ///
  /// Turning the backlight off saves the most power on its own, but the
  /// sender goes on encoding and the board goes on decoding for a screen
  /// nobody is looking at. Saying so lets the sender stop, and stops the
  /// traffic with it. The connection stays up either way -- it is the
  /// pictures that pause, not the link.
  void set_awake(bool awake);
  bool is_awake() const { return !this->asleep_; }

 protected:
  /// One frame in flight: a compressed frame being filled by USB, or a full one
  /// waiting to be decoded.
  struct Frame {
    uint8_t *data{nullptr};
    size_t capacity{0};
    size_t received{0};
    size_t total{0};
    // Where on the panel this rectangle goes. A sender that redraws everything
    // sends the whole panel at 0,0; an indirect display driver sends only what
    // changed, which is most of the reason it is faster.
    uint16_t x{0};
    uint16_t y{0};
    uint16_t width{0};
    uint16_t height{0};
    // Which picture this rectangle belongs to. A sender that redraws only what
    // changed sends several rectangles carrying the same identifier, and they
    // have to be admitted or turned away together.
    uint16_t id{0};
    // Whether the transport this arrived on can be told to slow down. One that
    // can is never worth dropping a frame from: waiting says the same thing
    // and keeps the picture.
    bool paced{false};
  };

  // Decodes whatever the USB side has finished and draws it. Runs on its own
  // task: at the frame rates this is built for, ESPHome's loop is nowhere near
  // often enough, and a JPEG decode plus a full-panel blit is far too much to
  // put in front of every other component.
  static void decode_task(void *param);
  void run_decode_task();

  // Turns and scales the decoded frame into rot_buffer_ with the P4's
  // pixel-processing accelerator. Nothing here touches the CPU: it is a DMA
  // engine that reads one buffer and writes the other, and it does the turn
  // and the scale in the same pass. The name predates the scaling.
  bool rotate_(const Frame &frame, uint16_t padded_width, uint16_t padded_height);
  bool allocate_rotation_();
  /// Where a rectangle sent for the unrotated panel lands once the panel is
  /// turned. Also swaps the size on a quarter turn.
  void place_(const Frame &frame, uint16_t &x, uint16_t &y, uint16_t &w, uint16_t &h) const;
  // Lays the sender out as a FAT12 volume for the mass-storage interface to
  // serve. Defined in sender_drive.cpp, and compiled away with it.
  void setup_sender_drive_();
  // Defined in network.cpp: listens for a sender and feeds it to feed_().
  void setup_network_();
#ifdef USE_TOUCHSCREEN
  /// Queue one contact set for the network sender, if one is connected.
  void queue_touch_(const touchscreen::TouchPoints_t &points);
#endif
  /// Everything waiting to go back up the socket: contacts, and whether the
  /// panel is awake.
  void send_queued_messages_(int client);
  static void network_task(void *param);
  void run_network_task();
  /// Forget a half-received frame, so the next sender's first header is not
  /// read out of the middle of the last one's payload.
  void reset_stream_();
#ifdef USE_SPEAKER
  // Defined in audio.cpp. The first two are the shared half and are compiled
  // whenever there is a speaker; setup_uac_ is the USB half and is not.
  void setup_speaker_();
  void flush_audio_block_();
#if CFG_TUD_AUDIO
  void setup_uac_();
#endif
#endif
#ifdef USE_TOUCHSCREEN
  // Defined in touch.cpp, and compiled away with it.
  void setup_touch_();
#endif
#if CFG_TUD_HID
  bool send_touch_report_(const udisp_touch_report_t &report);
  void retry_release_();
#endif

  bool allocate_frames_();
  Frame *take_empty_(uint32_t wait_ms = 0);
  void queue_filled_(Frame *frame);
  bool append_(Frame *frame, const uint8_t *data, size_t len);

  display::Display *display_{nullptr};
  uint16_t width_{1024};
  uint16_t height_{600};
  // What the host draws on. Zero until setup(), which fills it in from the
  // panel's own size when nothing else was configured.
  uint16_t render_width_{0};
  uint16_t render_height_{0};
  bool scaling_{false};
  uint8_t frame_buffer_count_{4};
  size_t max_frame_bytes_{128 * 1024};
  uint32_t min_frame_interval_ms_{0};
  uint32_t last_draw_ms_{0};
  // The picture the rate limiter last let through and the one it last turned
  // away, so a picture split into several rectangles is not half drawn. The
  // protocol's identifier is ten bits, so 0xFFFF is a value no frame has.
  uint16_t drawing_frame_id_{0xFFFF};
  uint16_t gated_frame_id_{0xFFFF};
  uint16_t port_{0};
#ifdef USE_TOUCHSCREEN
  // Touches travel back to whoever is sending the picture, so a dashboard
  // rendered elsewhere can be pressed here. The touchscreen reports on
  // ESPHome's loop and the socket is written from the network task, so they
  // meet in a queue rather than touching the same descriptor.
  QueueHandle_t touch_queue_{nullptr};
  /// Board to sender: 'T', a contact count, then that many id/x/y triples.
  struct TouchEvent {
    uint8_t count;
    uint8_t id[UDISP_NET_TOUCH_MAX];
    uint16_t x[UDISP_NET_TOUCH_MAX];
    uint16_t y[UDISP_NET_TOUCH_MAX];
  };
  // The last set queued, so a finger resting still is not sent fifty times a
  // second saying the same thing.
  TouchEvent last_touch_{};
  bool last_touch_valid_{false};
#endif
  // feed_() is reachable from the USB task and the network one at once.
  Mutex feed_lock_;
  // Whether the panel is showing anything to anybody, and whether the sender
  // has been told. Written from the loop, read from the network task.
  bool asleep_{false};
  volatile bool status_pending_{false};

  Frame *frames_{nullptr};
  QueueHandle_t empty_queue_{nullptr};
  QueueHandle_t filled_queue_{nullptr};
  // The frame the receive callback is currently filling, or null between frames.
  Frame *current_{nullptr};
  // Set when a frame arrived with no buffer free: its payload is counted out
  // and thrown away rather than being mistaken for a header.
  size_t skipping_{0};
  // A header being gathered. It arrives whole in a USB transfer and in pieces
  // over TCP, where a read boundary falls wherever it likes.
  uint8_t header_buf_[sizeof(udisp_frame_header_t)]{};
  size_t header_len_{0};

  jpeg_decoder_handle_t jpeg_{nullptr};
  // Decoded RGB565, handed to the display. The decoder writes whole 16x16
  // minimum coded units, so this is sized for the resolution rounded up to a
  // multiple of 16 on both axes, not for the resolution itself.
  uint8_t *rgb_buffer_{nullptr};
  size_t rgb_buffer_len_{0};
  uint16_t padded_width_{0};
  uint16_t padded_height_{0};

  // Rotation, for a panel that is not mounted the way the host sends its
  // frames. Null client means none was asked for and nothing is allocated.
  uint16_t rotation_{0};
  uint16_t ppa_burst_{64};
  ppa_client_handle_t ppa_client_{nullptr};
  uint8_t *rot_buffer_{nullptr};
  size_t rot_buffer_len_{0};
  // What ends up on the panel: the frame size, with the axes swapped by a
  // quarter turn.
  uint16_t out_width_{0};
  uint16_t out_height_{0};

  Trigger<> *audio_start_trigger_{nullptr};
  Trigger<> *audio_stop_trigger_{nullptr};
  // Written from the audio task, read from the loop: the triggers run
  // automations, which belong to the loop and nowhere else.
  volatile uint32_t last_audio_ms_{0};
  bool audio_active_{false};

  const uint8_t *sender_script_{nullptr};
  size_t sender_script_len_{0};

#ifdef USE_SPEAKER
  speaker::Speaker *speaker_{nullptr};
  float audio_volume_{1.0f};
  bool audio_muted_{false};
  bool logged_first_audio_{false};
  // The host's packets are gathered here before the speaker sees them; see
  // audio.cpp for why they arrive far too small to hand over one at a time.
  uint8_t *audio_block_{nullptr};
  size_t audio_block_size_{0};
  size_t audio_block_used_{0};
  size_t last_packet_len_{0};
  uint32_t audio_resyncs_{0};
  // Whether the speaker has EVER taken a byte. It is the whole difference
  // between a speaker that is behind and one that is refusing the stream, and
  // the two need opposite things done about them.
  bool audio_ever_accepted_{false};
  // Buffers the speaker would not take whole. A few are normal at the start of
  // a stream; a steady stream of them is the board not keeping up.
  uint32_t audio_underruns_{0};
  // How much of a UDISP_TYPE_PCM payload is still to come. The parser's fourth
  // state, and the only one that hands its bytes straight on rather than
  // gathering them: samples are a stream, so a payload split across two reads
  // is two writes to the speaker and nothing else.
  size_t audio_want_{0};
#endif

#ifdef USE_TOUCHSCREEN
  touchscreen::Touchscreen *touchscreen_{nullptr};
#endif
#if CFG_TUD_HID
  // A release the host has not been told about yet. Losing a press costs one
  // poll; losing a release leaves a finger down forever.
  bool release_pending_{false};
#endif

  // Reported on an interval, like the camera components.
  uint32_t frames_drawn_{0};
  uint32_t frames_dropped_{0};
  // Why they were dropped. A single total says something is wrong without
  // saying what, and the three causes have nothing to do with each other: no
  // free buffer is the decoder falling behind, a decode failure is the frame
  // itself, a rotation failure is the accelerator.
  uint32_t dropped_no_buffer_{0};
  uint32_t dropped_too_soon_{0};
  uint32_t dropped_decode_{0};
  uint32_t dropped_rotate_{0};
  // The last rectangle drawn, which is not the panel once a driver sends only
  // what changed.
  uint16_t last_frame_w_{0};
  uint16_t last_frame_h_{0};
  uint32_t draw_us_{0};
  // Of draw_us_, the part spent inside the accelerator itself.
  uint32_t ppa_us_{0};
  uint32_t stats_since_ms_{0};
  // The last frame rate reported, so a steady stream is not restated every five
  // seconds for as long as it runs.
  float last_fps_{0.0f};
  bool logged_stats_{false};
  bool was_dropping_{false};
  bool logged_first_frame_{false};
  // One-shots so a wrong-sized sender and a silent bus are distinguishable
  // without turning the log into a per-packet trace.
  bool logged_first_bytes_{false};
  bool logged_bad_header_{false};
  bool logged_decode_error_{false};
  bool logged_rotate_error_{false};
  // A device nobody has claimed is never configured, and the only trace of that
  // is a callback that does not happen -- so watch for it not happening.
  bool configured_{false};
  bool logged_unclaimed_{false};
  uint32_t started_ms_{0};
};

/// Actions, so the panel's own automations can say when nobody is looking.
///
/// Two classes rather than one with a flag: that is how ESPHome spells a pair
/// of opposites, and it keeps the YAML reading as "portall.sleep" without
/// an argument to get the wrong way round.
template<typename... Ts> class SleepAction final : public Action<Ts...>, public Parented<Portall> {
 public:
  void play(const Ts &...) override { this->parent_->set_awake(false); }
};

template<typename... Ts> class WakeAction final : public Action<Ts...>, public Parented<Portall> {
 public:
  void play(const Ts &...) override { this->parent_->set_awake(true); }
};

#ifdef USE_SPEAKER
/* portall.set_volume, so a template number can own the volume.
 *
 * The component's own `number: platform: portall` follows whatever the sound
 * is currently at, which is right for a volume the host also controls and
 * wrong for the thing people actually want at boot: a slider with
 * restore_value and an initial_value, remembered across restarts. That is how
 * ESPHome does a setting, and a template number can only do it if it has
 * something to call.
 *
 * The value is a fraction, 0 to 1, like every other volume in ESPHome -- and
 * a slider is nearly always 0 to 100, so `!lambda 'return x / 100.0;'` is the
 * line this is written for. Anything outside the range is clamped rather than
 * refused: a volume is not worth failing a boot over, and set_audio_volume
 * says so once when it happens.
 */
template<typename... Ts> class SetVolumeAction final : public Action<Ts...>, public Parented<Portall> {
 public:
  TEMPLATABLE_VALUE(float, volume)

  void play(const Ts &...x) override { this->parent_->set_audio_volume(this->volume_.value(x...)); }
};
#endif

}  // namespace portall
}  // namespace esphome
