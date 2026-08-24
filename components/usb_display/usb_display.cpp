#include "usb_display.h"

#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"

#include <cmath>
#include <cstring>

extern "C" {
#include "sdkconfig.h"
#include "freertos/task.h"
#include "esp_private/usb_phy.h"
#include "tusb.h"
#include "usb_descriptors.h"
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display";

static constexpr uint32_t STATS_INTERVAL_MS = 5000;
// Long enough for a host to enumerate, load a driver and start it, short enough
// to be in the same screenful of log as the startup lines.
static constexpr uint32_t UNCLAIMED_WARNING_MS = 10000;
// How far the frame rate has to move before it is worth another line.
static constexpr float STATS_FPS_EPSILON = 1.0f;
// Silence longer than this is the host having stopped, not a gap between
// buffers. Half a second is far more than any scheduling hiccup and far less
// than a listener would notice.
static constexpr uint32_t AUDIO_IDLE_MS = 500;
// How long a transport that can be made to wait will wait for a free frame
// buffer. Long enough to ride out a burst -- a whole page arrives as several
// rectangles at once and the decoder is momentarily behind -- and short enough
// that a decoder which has genuinely stopped does not hold the socket shut.
static constexpr uint32_t FRAME_WAIT_MS = 250;

// TinyUSB's callbacks are plain C with no context argument, so the one instance
// has to be reachable from file scope. A second usb_display would need a second
// USB device controller, which the ESP32-P4 does not have; the Python side
// enforces a single instance.
static USBDisplay *g_usb_display = nullptr;

extern "C" void tud_vendor_rx_cb(uint8_t itf, uint8_t const *buffer, uint16_t bufsize) {
  (void) buffer;
  (void) bufsize;
  if (g_usb_display != nullptr)
    g_usb_display->on_vendor_rx(itf);
}

// The device end of enumeration. Whether these fire at all is the difference
// between "the host never saw us" and "the host configured us and the sender
// is the problem", which nothing else here can tell apart.
extern "C" void tud_mount_cb(void) {
  // The speed the bus actually negotiated, which is not necessarily the one the
  // descriptors were built for: the endpoint sizes in tusb_config.h are fixed at
  // compile time, and a High-Speed bulk endpoint on a Full-Speed bus enumerates
  // fine and then never transfers anything. Say it out loud rather than leaving
  // a silent endpoint to be diagnosed from its absence.
  if (g_usb_display != nullptr)
    g_usb_display->set_configured();

  const tusb_speed_t speed = tud_speed_get();
  const char *speed_name = (speed == TUSB_SPEED_HIGH)   ? "High Speed"
                           : (speed == TUSB_SPEED_FULL) ? "Full Speed"
                                                        : "Low Speed";
  ESP_LOGI(TAG, "USB: configured by the host at %s", speed_name);

#if CONFIG_USB_DISPLAY_HIGH_SPEED
  const bool as_built = speed == TUSB_SPEED_HIGH;
#else
  const bool as_built = speed == TUSB_SPEED_FULL;
#endif
  if (!as_built) {
    ESP_LOGE(TAG,
             "USB: the bus is %s but this firmware was built with %u byte endpoints; "
             "no data will arrive. Check usb_speed: in the configuration.",
             speed_name, (unsigned) CFG_TUD_VENDOR_EPSIZE);
  }
}
extern "C" void tud_umount_cb(void) { ESP_LOGW(TAG, "USB: unconfigured"); }
extern "C" void tud_suspend_cb(bool remote_wakeup_en) {
  (void) remote_wakeup_en;
  ESP_LOGW(TAG, "USB: suspended by the host");
}
extern "C" void tud_resume_cb(void) { ESP_LOGI(TAG, "USB: resumed"); }

namespace {

void tusb_device_task(void *param) {
  (void) param;
  while (true)
    tud_task();
}

}  // namespace

void USBDisplay::setup() {
  if (this->display_ == nullptr) {
    this->mark_failed(LOG_STR("No display"));
    return;
  }
  g_usb_display = this;

  jpeg_decode_engine_cfg_t engine_cfg = {};
  engine_cfg.intr_priority = 1;
  engine_cfg.timeout_ms = 50;
  esp_err_t err = jpeg_new_decoder_engine(&engine_cfg, &this->jpeg_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "jpeg_new_decoder_engine() failed: %s", esp_err_to_name(err));
    this->mark_failed(LOG_STR("JPEG decoder unavailable"));
    return;
  }

  // The decoder works in 16x16 minimum coded units and writes a whole number of
  // them, so it decodes a 1024x600 frame into 1024x608 and refuses a buffer
  // sized for 1024x600. Round both axes up; the real pixels stay at the top
  // left of that area, so drawing skips the padding rather than showing it.
  // Nothing configured means the host draws at the panel's own size.
  if (this->render_width_ == 0 || this->render_height_ == 0) {
    this->render_width_ = this->width_;
    this->render_height_ = this->height_;
  }
  this->scaling_ = this->render_width_ != this->width_ || this->render_height_ != this->height_;

  // The decoder writes what the host sent, which is the render size and not
  // the panel's.
  this->padded_width_ = (this->render_width_ + 15) & ~15;
  this->padded_height_ = (this->render_height_ + 15) & ~15;

  // The driver's own allocator: the decode output is written by DMA, so the
  // buffer has to sit on a cache line and occupy a whole number of them. It
  // reports back how much it really took, which is what the size check wants.
  jpeg_decode_memory_alloc_cfg_t out_cfg = {};
  out_cfg.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
  const size_t out_wanted = (size_t) this->padded_width_ * this->padded_height_ * 2;
  this->rgb_buffer_ = (uint8_t *) jpeg_alloc_decoder_mem(out_wanted, &out_cfg, &this->rgb_buffer_len_);
  if (this->rgb_buffer_ == nullptr) {
    ESP_LOGE(TAG, "Could not allocate the %u byte RGB565 buffer", (unsigned) out_wanted);
    this->mark_failed(LOG_STR("RGB buffer allocation failed"));
    return;
  }

  // A quarter turn swaps what ends up on the panel; a half turn does not.
  const bool quarter_turn = this->rotation_ == 90 || this->rotation_ == 270;
  this->out_width_ = quarter_turn ? this->height_ : this->width_;
  this->out_height_ = quarter_turn ? this->width_ : this->height_;

  // The accelerator is needed for either job, and does both in one pass.
  if ((this->rotation_ != 0 || this->scaling_) && !this->allocate_rotation_()) {
    this->mark_failed(LOG_STR("Rotation setup failed"));
    return;
  }

  if (!this->allocate_frames_()) {
    this->mark_failed(LOG_STR("Frame buffer allocation failed"));
    return;
  }

#if CFG_TUD_MSC
  // Before tusb_init(): the host can ask for a sector as soon as it has
  // enumerated the drive.
  this->setup_sender_drive_();
#endif
#ifdef USE_TOUCHSCREEN
  if (this->touchscreen_ != nullptr)
    this->setup_touch_();
#endif
#if CFG_TUD_AUDIO
  this->setup_audio_();
#endif
  this->setup_network_();

  // Device mode on the PHY that matches the speed the descriptors were built
  // for. TinyUSB does not set the PHY up itself.
  //
  // The ESP32-P4 has two internal Full-Speed PHYs and one High-Speed one, and
  // they are separate targets: USB_PHY_TARGET_INT is a Full-Speed PHY,
  // USB_PHY_TARGET_UTMI is the High-Speed one. Picking INT while tusb_config.h
  // declares a 512-byte bulk endpoint does not fail loudly -- the device
  // enumerates and the host configures it, because endpoint 0 is legal at
  // either speed -- but 512 is not a legal bulk packet size at Full Speed, so
  // the OUT pipe never carries a byte and the receive callback never fires.
  usb_phy_handle_t phy = nullptr;
  usb_phy_config_t phy_conf = {};
  phy_conf.controller = USB_PHY_CTRL_OTG;
  phy_conf.otg_mode = USB_OTG_MODE_DEVICE;
#if CONFIG_USB_DISPLAY_HIGH_SPEED
  phy_conf.target = USB_PHY_TARGET_UTMI;
#else
  phy_conf.target = USB_PHY_TARGET_INT;
#endif
  if (usb_new_phy(&phy_conf, &phy) != ESP_OK) {
    this->mark_failed(LOG_STR("USB PHY setup failed"));
    return;
  }
  if (!tusb_init()) {
    this->mark_failed(LOG_STR("tusb_init failed"));
    return;
  }

  // Priority 5 for the USB task, 4 for the decoder: USB has to keep draining
  // the endpoint or the host stalls, and a decode that runs late only costs a
  // frame. Core 1 keeps both off the core ESPHome's loop runs on.
  xTaskCreatePinnedToCore(tusb_device_task, "usbd", 4096, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(USBDisplay::decode_task, "udisp", 4096, this, 4, nullptr, 1);

  this->stats_since_ms_ = millis();
  this->started_ms_ = millis();
  ESP_LOGI(TAG, "USB extended screen ready: %ux%u, %u frame buffers of %u bytes", (unsigned) this->width_,
           (unsigned) this->height_, (unsigned) this->frame_buffer_count_, (unsigned) this->max_frame_bytes_);
}

bool USBDisplay::allocate_rotation_() {
  ppa_client_config_t ppa_config = {};
  ppa_config.oper_type = PPA_OPERATION_SRM;
  ppa_config.max_pending_trans_num = 1;
  esp_err_t err = ppa_register_client(&ppa_config, &this->ppa_client_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ppa_register_client() failed: %s", esp_err_to_name(err));
    this->ppa_client_ = nullptr;
    return false;
  }

  // The accelerator writes this by DMA, so it wants a whole number of cache
  // lines starting on one.
  this->rot_buffer_len_ = ((size_t) this->out_width_ * this->out_height_ * 2 + 63) & ~(size_t) 63;
  this->rot_buffer_ = (uint8_t *) heap_caps_aligned_alloc(64, this->rot_buffer_len_, MALLOC_CAP_SPIRAM);
  if (this->rot_buffer_ == nullptr) {
    ESP_LOGE(TAG, "Could not allocate the %u byte rotation buffer", (unsigned) this->rot_buffer_len_);
    return false;
  }
  return true;
}

void USBDisplay::place_(const Frame &frame, uint16_t &x, uint16_t &y, uint16_t &w, uint16_t &h) const {
  // The rectangle arrives in the host's coordinates, which are the render size
  // and not always the panel's. Bring it into panel coordinates first, because
  // the turn below is described against the panel.
  //
  // The division is exact by construction, not by luck: the configuration is
  // refused unless a tile-aligned coordinate lands on a whole panel pixel, so
  // there is no rounding here to accumulate into a seam. In 32 bits because
  // 1280 x 800 overflows 16 before the divide.
  const uint16_t fx = (uint16_t) ((uint32_t) frame.x * this->width_ / this->render_width_);
  const uint16_t fy = (uint16_t) ((uint32_t) frame.y * this->height_ / this->render_height_);
  const uint16_t fw = (uint16_t) ((uint32_t) frame.width * this->width_ / this->render_width_);
  const uint16_t fh = (uint16_t) ((uint32_t) frame.height * this->height_ / this->render_height_);

  // Turning the panel moves a rectangle as well as its contents. Everything
  // here is clockwise, the way a mounting is described.
  switch (this->rotation_) {
    case 90:
      x = (uint16_t) (this->height_ - fy - fh);
      y = fx;
      w = fh;
      h = fw;
      break;
    case 180:
      x = (uint16_t) (this->width_ - fx - fw);
      y = (uint16_t) (this->height_ - fy - fh);
      w = fw;
      h = fh;
      break;
    case 270:
      x = fy;
      y = (uint16_t) (this->width_ - fx - fw);
      w = fh;
      h = fw;
      break;
    default:
      x = fx;
      y = fy;
      w = fw;
      h = fh;
      break;
  }
}

bool USBDisplay::rotate_(const Frame &frame, uint16_t padded_width, uint16_t padded_height) {
  ppa_srm_rotation_angle_t angle;
  // The accelerator turns counter-clockwise; the configuration is clockwise,
  // the way a panel's mounting is described.
  switch (this->rotation_) {
    case 90:
      angle = PPA_SRM_ROTATION_ANGLE_270;
      break;
    case 180:
      angle = PPA_SRM_ROTATION_ANGLE_180;
      break;
    case 270:
      angle = PPA_SRM_ROTATION_ANGLE_90;
      break;
    default:
      // No turn at all is a legitimate reason to be here: the accelerator is
      // also what scales, and a panel drawn at its own size but rendered
      // smaller needs this pass without needing a rotation.
      if (!this->scaling_)
        return false;
      angle = PPA_SRM_ROTATION_ANGLE_0;
      break;
  }

  const bool quarter_turn = this->rotation_ == 90 || this->rotation_ == 270;
  // What comes out is the rectangle at the panel's scale, which is what will
  // be drawn; place_ works the destination out the same way.
  const uint16_t scaled_w = (uint16_t) ((uint32_t) frame.width * this->width_ / this->render_width_);
  const uint16_t scaled_h = (uint16_t) ((uint32_t) frame.height * this->height_ / this->render_height_);
  const uint16_t out_w = quarter_turn ? scaled_h : scaled_w;
  const uint16_t out_h = quarter_turn ? scaled_w : scaled_h;

  ppa_srm_oper_config_t srm = {};
  // The decoded rectangle sits at the top left of a buffer rounded up to whole
  // 16x16 units, so read it as a block out of the larger picture rather than
  // rotating the padding along with it.
  srm.in.buffer = this->rgb_buffer_;
  srm.in.pic_w = padded_width;
  srm.in.pic_h = padded_height;
  srm.in.block_w = frame.width;
  srm.in.block_h = frame.height;
  srm.in.block_offset_x = 0;
  srm.in.block_offset_y = 0;
  srm.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  srm.out.buffer = this->rot_buffer_;
  srm.out.buffer_size = this->rot_buffer_len_;
  srm.out.pic_w = out_w;
  srm.out.pic_h = out_h;
  srm.out.block_offset_x = 0;
  srm.out.block_offset_y = 0;
  srm.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  srm.rotation_angle = angle;
  srm.scale_x = (float) this->width_ / (float) this->render_width_;
  srm.scale_y = (float) this->height_ / (float) this->render_height_;
  srm.mode = PPA_TRANS_MODE_BLOCKING;

  esp_err_t err = ppa_do_scale_rotate_mirror(this->ppa_client_, &srm);
  if (err != ESP_OK) {
    if (!this->logged_rotate_error_) {
      this->logged_rotate_error_ = true;
      ESP_LOGE(TAG, "PPA rotation failed: %s (%ux%u out of %ux%u, into %ux%u, %u byte buffer)", esp_err_to_name(err),
               (unsigned) frame.width, (unsigned) frame.height, (unsigned) padded_width, (unsigned) padded_height,
               (unsigned) out_w, (unsigned) out_h, (unsigned) this->rot_buffer_len_);
    }
    return false;
  }
  return true;
}

bool USBDisplay::allocate_frames_() {
  this->frames_ = new Frame[this->frame_buffer_count_];
  this->empty_queue_ = xQueueCreate(this->frame_buffer_count_, sizeof(Frame *));
  this->filled_queue_ = xQueueCreate(this->frame_buffer_count_, sizeof(Frame *));
  if (this->empty_queue_ == nullptr || this->filled_queue_ == nullptr)
    return false;

  // Same allocator for the compressed side: the decoder reads the bit stream by
  // DMA too, so these have the same cache alignment requirement as the output.
  jpeg_decode_memory_alloc_cfg_t in_cfg = {};
  in_cfg.buffer_direction = JPEG_DEC_ALLOC_INPUT_BUFFER;

  for (uint8_t i = 0; i < this->frame_buffer_count_; i++) {
    Frame *frame = &this->frames_[i];
    size_t allocated = 0;
    frame->data = (uint8_t *) jpeg_alloc_decoder_mem(this->max_frame_bytes_, &in_cfg, &allocated);
    if (frame->data == nullptr) {
      ESP_LOGE(TAG, "Could not allocate frame buffer %u of %u bytes", i, (unsigned) this->max_frame_bytes_);
      return false;
    }
    frame->capacity = this->max_frame_bytes_;
    Frame *ptr = frame;
    xQueueSend(this->empty_queue_, &ptr, 0);
  }
  return true;
}

USBDisplay::Frame *USBDisplay::take_empty_(uint32_t wait_ms) {
  Frame *frame = nullptr;
  if (xQueueReceive(this->empty_queue_, &frame, pdMS_TO_TICKS(wait_ms)) != pdTRUE)
    return nullptr;
  frame->received = 0;
  return frame;
}

void USBDisplay::queue_filled_(Frame *frame) {
  if (xQueueSend(this->filled_queue_, &frame, 0) != pdTRUE)
    xQueueSend(this->empty_queue_, &frame, 0);
}

bool USBDisplay::append_(Frame *frame, const uint8_t *data, size_t len) {
  if (len > 0 && frame->received + len <= frame->capacity) {
    memcpy(frame->data + frame->received, data, len);
    frame->received += len;
  } else if (len > 0) {
    // Overlong frame: keep counting so the stream stays in sync, but the
    // payload is no longer usable.
    frame->received += len;
  }
  return frame->received >= frame->total;
}

// ===========================================================================
// USB receive -- runs on the TinyUSB task
// ===========================================================================
// One chunk of the host's stream, whatever carried it here. The USB endpoint
// and any other transport hand bytes to this and nothing below cares which.
//
// This is a byte stream, not a sequence of messages, and the difference is the
// whole reason this function is a loop. A USB transfer usually did arrive with
// one frame in it, which let an earlier version read the header off the front
// of a chunk and hand the rest to the frame. TCP makes no such promise: a read
// can hold half a header, or the tail of one frame followed by the head of the
// next. Assume otherwise and the parser loses its place -- and there is no
// marker in this protocol to find it again with, so it stays lost. The visible
// result is a decoder complaining about impossible geometry, and a panel that
// freezes until something restarts the connection.
void USBDisplay::feed_(const uint8_t *data, size_t len, bool may_wait) {
  if (len == 0)
    return;
  // Two transports can be active at once; a frame half assembled from one must
  // not have the other's bytes appended to it.
  LockGuard lock(this->feed_lock_);

  if (!this->logged_first_bytes_) {
    this->logged_first_bytes_ = true;
    ESP_LOGI(TAG, "First %u bytes from the host", (unsigned) len);
  }

  while (len > 0) {
    // Counting out a frame that cannot be kept. Only its own bytes: whatever
    // follows is the next header and belongs to the next pass.
    if (this->skipping_ > 0) {
      const size_t take = this->skipping_ < len ? this->skipping_ : len;
      this->skipping_ -= take;
      data += take;
      len -= take;
      continue;
    }

    // Filling a frame, again only up to what it asked for.
    if (this->current_ != nullptr) {
      const size_t want = this->current_->total - this->current_->received;
      const size_t take = want < len ? want : len;
      if (this->append_(this->current_, data, take)) {
        this->queue_filled_(this->current_);
        this->current_ = nullptr;
      }
      data += take;
      len -= take;
      continue;
    }

    // Gathering a header, which may well be split across two reads.
    const size_t need = sizeof(udisp_frame_header_t) - this->header_len_;
    const size_t take = need < len ? need : len;
    memcpy(this->header_buf_ + this->header_len_, data, take);
    this->header_len_ += take;
    data += take;
    len -= take;
    if (this->header_len_ < sizeof(udisp_frame_header_t))
      return;  // the rest of it is in the next read
    this->header_len_ = 0;

    auto *header = (udisp_frame_header_t *) this->header_buf_;
    if (header->type == UDISP_TYPE_END) {
      // Not a frame: the host marking the end of what it was sending. Count its
      // payload out like any other and say nothing -- it is the protocol
      // working, not a rejection.
      this->skipping_ = header->payload_total;
      continue;
    }
    // Any rectangle that fits on the panel, not just the whole panel: a sender
    // that redraws only what changed sends small ones, and that is most of
    // where its speed comes from.
    const bool usable = header->type == UDISP_TYPE_JPG && header->width > 0 && header->height > 0 &&
                        header->x + header->width <= this->render_width_ &&
                        header->y + header->height <= this->render_height_ && header->payload_total > 0 &&
                        header->payload_total <= this->max_frame_bytes_;
    if (!usable) {
      // Silently dropping these is how a sender configured for the wrong size
      // looks exactly like a sender that is not running at all.
      if (!this->logged_bad_header_) {
        this->logged_bad_header_ = true;
        ESP_LOGW(TAG,
                 "Ignoring frames: host sends type=%u %ux%u at %u,%u of %u bytes, which is not a JPEG rectangle "
                 "inside %ux%u of at most %u bytes",
                 (unsigned) header->type, (unsigned) header->width, (unsigned) header->height, (unsigned) header->x,
                 (unsigned) header->y, (unsigned) header->payload_total, (unsigned) this->render_width_,
                 (unsigned) this->render_height_, (unsigned) this->max_frame_bytes_);
      }
      // Best effort: if the length is plausible this stays in step, and if it
      // is not there was nothing to stay in step with.
      this->skipping_ = header->payload_total;
      continue;
    }

    // Wait for a buffer where waiting is possible. Not reading the socket for a
    // moment is all the flow control this needs -- the sender blocks, catches
    // up and carries on -- and it costs nothing, where throwing the rectangle
    // away costs the pixels under it until the next full redraw. A whole page
    // arrives as a burst of rectangles and the decoder is briefly behind by
    // definition; that is the moment this matters most.
    Frame *frame = this->take_empty_(may_wait ? FRAME_WAIT_MS : 0);
    if (frame == nullptr) {
      // Nothing free, and nothing to be done about it: count this frame out so
      // the next header is recognised rather than being read out of the middle
      // of a payload.
      this->frames_dropped_++;
      this->dropped_no_buffer_++;
      this->skipping_ = header->payload_total;
      continue;
    }
    frame->x = header->x;
    frame->y = header->y;
    frame->width = header->width;
    frame->height = header->height;
    frame->id = header->frame_id;
    frame->total = header->payload_total;
    frame->paced = may_wait;
    this->current_ = frame;
  }
}

void USBDisplay::on_vendor_rx(uint8_t itf) {
  static uint8_t rx_buf[CFG_TUD_VENDOR_EPSIZE];

  while (tud_vendor_n_available(itf)) {
    int read = tud_vendor_n_read(itf, rx_buf, sizeof(rx_buf));
    if (read <= 0)
      break;
    this->feed_(rx_buf, (size_t) read);
  }
}

// ===========================================================================
// Decode and draw -- its own task, never ESPHome's loop
// ===========================================================================
void USBDisplay::decode_task(void *param) { static_cast<USBDisplay *>(param)->run_decode_task(); }

void USBDisplay::run_decode_task() {
  jpeg_decode_cfg_t decode_cfg = {};
  decode_cfg.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
  decode_cfg.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_BGR;

  while (true) {
    Frame *frame = nullptr;
    if (xQueueReceive(this->filled_queue_, &frame, portMAX_DELAY) != pdTRUE)
      continue;

    // A host that sends faster than this board can draw is not doing anything
    // wrong -- it has no way to know -- but decoding and rotating a frame that
    // is already stale spends the memory bandwidth the audio needs. Turn it
    // away before any of that work happens, rather than after.
    //
    // Only a whole-panel frame, though, and only from a transport that cannot
    // be asked to wait.
    //
    // A whole-panel frame is a complete picture and the next one replaces it
    // entirely, so dropping one costs a moment of smoothness and nothing else.
    // A smaller rectangle is the opposite: it is the only place its pixels will
    // ever be sent, because a sender that redraws what changed does not repeat
    // what did not. Drop one of those and the area under it keeps the previous
    // page until some later full redraw -- two screens on top of each other,
    // and a wait to come back.
    //
    // And over a socket even a whole-panel frame is worth keeping, because the
    // sender there believes what it sent was drawn and will not send it again:
    // it moves on to the next difference. Turning one away leaves the panel a
    // picture behind with nothing to correct it. Not reading the socket for a
    // moment says "slow down" without losing anything, which is what the wait
    // for a free buffer already does.
    const bool whole_panel = frame->width >= this->render_width_ && frame->height >= this->render_height_;
    //
    // The unit being limited is the picture, not the rectangle. A sender that
    // redraws only what changed splits one picture across several rectangles,
    // all carrying the same identifier; deciding on each of them separately
    // would draw the first and discard the rest, leaving half an update on the
    // panel. So once a picture is admitted the rest of it follows, and once one
    // is turned away the rest of it goes with it.
    bool too_soon = whole_panel && !frame->paced && this->min_frame_interval_ms_ != 0 &&
                    millis() - this->last_draw_ms_ < this->min_frame_interval_ms_;
    if (frame->id == this->drawing_frame_id_) {
      too_soon = false;
    } else if (frame->id == this->gated_frame_id_) {
      too_soon = true;
    }
    if (too_soon) {
      this->gated_frame_id_ = frame->id;
      this->frames_dropped_++;
      this->dropped_too_soon_++;
      xQueueSend(this->empty_queue_, &frame, 0);
      continue;
    }
    this->drawing_frame_id_ = frame->id;

    // The decoder rounds each rectangle up to whole 16x16 units, so the stride
    // follows the rectangle's own width, not the panel's. The buffer is sized
    // for a full-panel rectangle, which is the largest one that can arrive.
    const uint16_t padded_w = (frame->width + 15) & ~15;
    const uint16_t padded_h = (frame->height + 15) & ~15;

    uint32_t out_size = 0;
    esp_err_t err = jpeg_decoder_process(this->jpeg_, &decode_cfg, frame->data, frame->received, this->rgb_buffer_,
                                         this->rgb_buffer_len_, &out_size);
    if (err == ESP_OK) {
      uint32_t start = micros();
      // The decoded rows are padded_w wide even when fewer pixels are wanted;
      // x_pad tells the display to skip the difference at the end of each line.
      // When the width is already a multiple of 16 there is no padding, and
      // this stays on mipi_dsi's single-transfer path instead of one DMA per
      // line. The rotated buffer never has any.
      uint16_t dst_x, dst_y, dst_w, dst_h;
      this->place_(*frame, dst_x, dst_y, dst_w, dst_h);

      const uint8_t *pixels = this->rgb_buffer_;
      int x_pad = padded_w - frame->width;
      if (this->ppa_client_ != nullptr) {
        // Drawing the unrotated buffer instead would be worse than dropping the
        // frame: after a quarter turn the axes are swapped, so it would go to
        // the panel at the wrong shape and in the wrong place.
        if (!this->rotate_(*frame, padded_w, padded_h)) {
          this->frames_dropped_++;
          this->dropped_rotate_++;
          xQueueSend(this->empty_queue_, &frame, 0);
          continue;
        }
        pixels = this->rot_buffer_;
        x_pad = 0;
      }
      this->display_->draw_pixels_at(dst_x, dst_y, dst_w, dst_h, pixels, display::COLOR_ORDER_RGB,
                                     display::COLOR_BITNESS_565, false, 0, 0, x_pad);
      this->draw_us_ += micros() - start;
      this->frames_drawn_++;
      this->last_draw_ms_ = millis();
      this->last_frame_w_ = frame->width;
      this->last_frame_h_ = frame->height;
      if (!this->logged_first_frame_) {
        this->logged_first_frame_ = true;
        ESP_LOGI(TAG, "First frame from the host: %u bytes compressed, %ux%u at %u,%u, drawn as %ux%u at %u,%u",
                 (unsigned) frame->received, (unsigned) frame->width, (unsigned) frame->height, (unsigned) frame->x,
                 (unsigned) frame->y, (unsigned) dst_w, (unsigned) dst_h, (unsigned) dst_x, (unsigned) dst_y);
      }
    } else {
      this->frames_dropped_++;
      this->dropped_decode_++;
      // Every frame failing looks exactly like no frame arriving, because the
      // statistics below only run once something has been drawn. Say it once.
      if (!this->logged_decode_error_) {
        this->logged_decode_error_ = true;
        ESP_LOGE(TAG, "JPEG decode failed: %s (%u bytes in, %ux%u into a %u byte buffer)", esp_err_to_name(err),
                 (unsigned) frame->received, (unsigned) frame->width, (unsigned) frame->height,
                 (unsigned) this->rgb_buffer_len_);
      }
    }

    xQueueSend(this->empty_queue_, &frame, 0);

    uint32_t elapsed = millis() - this->stats_since_ms_;
    if (elapsed >= STATS_INTERVAL_MS && this->frames_drawn_ > 0) {
      const float fps = this->frames_drawn_ * 1000.0f / elapsed;
      // A steady stream says the same thing every five seconds forever, which
      // buries anything worth reading. Report the first measurement, then only
      // when the rate has actually moved -- or when frames start or stop being
      // lost, which is a change of state rather than a running commentary. A
      // host sending faster than this can draw loses frames continuously and
      // perfectly normally; saying so every five seconds is noise.
      const bool dropping = this->frames_dropped_ > 0;
      if (!this->logged_stats_ || dropping != this->was_dropping_ ||
          fabsf(fps - this->last_fps_) >= STATS_FPS_EPSILON) {
        ESP_LOGD(TAG, "%ux%u @ %.1f fps, %u us/draw, %u dropped (%u no buffer, %u too soon, %u decode, %u rotate)",
                 (unsigned) this->last_frame_w_, (unsigned) this->last_frame_h_, fps,
                 (unsigned) (this->draw_us_ / this->frames_drawn_), (unsigned) this->frames_dropped_,
                 (unsigned) this->dropped_no_buffer_, (unsigned) this->dropped_too_soon_, (unsigned) this->dropped_decode_,
                 (unsigned) this->dropped_rotate_);
        this->logged_stats_ = true;
        this->last_fps_ = fps;
      }
      this->was_dropping_ = dropping;
      this->stats_since_ms_ = millis();
      this->frames_drawn_ = 0;
      this->frames_dropped_ = 0;
      this->dropped_no_buffer_ = 0;
      this->dropped_too_soon_ = 0;
      this->dropped_decode_ = 0;
      this->dropped_rotate_ = 0;
      this->draw_us_ = 0;
    }
  }
}

void USBDisplay::reset_stream_() {
  LockGuard lock(this->feed_lock_);
  if (this->current_ != nullptr) {
    xQueueSend(this->empty_queue_, &this->current_, 0);
    this->current_ = nullptr;
  }
  this->skipping_ = 0;
  // Half a header is worth even less than half a payload: keeping it would put
  // the previous connection's bytes in front of the next one's first frame.
  this->header_len_ = 0;
}

void USBDisplay::loop() {
#if CFG_TUD_HID
  this->retry_release_();
#endif

#if CFG_TUD_AUDIO
  // The host does not announce that it has stopped; it simply stops sending.
  // Silence for longer than a few buffers is the only signal there is.
  const bool active = this->last_audio_ms_ != 0 && millis() - this->last_audio_ms_ < AUDIO_IDLE_MS;
  if (active != this->audio_active_) {
    this->audio_active_ = active;
    Trigger<> *trigger = active ? this->audio_start_trigger_ : this->audio_stop_trigger_;
    if (trigger != nullptr)
      trigger->trigger();
  }
#endif

  // A host that has no driver for a device reads its descriptors and stops
  // there: it never selects a configuration, so tud_mount_cb never runs and the
  // only evidence is a line that is missing from the log. Say it instead --
  // "nothing claimed this device" is a completely different problem from
  // "claimed, but no picture arrives", and they look identical otherwise.
  if (!this->configured_ && !this->logged_unclaimed_ && millis() - this->started_ms_ > UNCLAIMED_WARNING_MS) {
    this->logged_unclaimed_ = true;
    ESP_LOGW(TAG,
             "USB: the host has not configured this device. It read the descriptors and stopped, which is what a host "
             "does when no driver claims the device -- check that one is bound to %04X:%04X.",
             (unsigned) CONFIG_USB_DISPLAY_VID, (unsigned) CONFIG_USB_DISPLAY_PID);
  }
}

void USBDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "USB Extended Display:");
  ESP_LOGCONFIG(TAG, "  Resolution: %ux%u", (unsigned) this->width_, (unsigned) this->height_);
  if (this->scaling_) {
    ESP_LOGCONFIG(TAG, "  Host draws %ux%u, scaled up by the pixel-processing accelerator", (unsigned) this->render_width_,
                  (unsigned) this->render_height_);
  }
  // Which identifiers the board actually enumerates as, and how many
  // interfaces. Both decide which driver a host binds, and neither could be
  // read back off a log before.
  ESP_LOGCONFIG(TAG, "  USB device: %04X:%04X \"%s\" \"%s\", %s, %s", (unsigned) CONFIG_USB_DISPLAY_VID,
                (unsigned) CONFIG_USB_DISPLAY_PID, CONFIG_USB_DISPLAY_MANUFACTURER, CONFIG_USB_DISPLAY_PRODUCT,
                CONFIG_USB_DISPLAY_HIGH_SPEED ? "High Speed" : "Full Speed",
                CFG_TUD_MSC ? "display + sender drive" : "display only");
  ESP_LOGCONFIG(TAG, "  Advertised to the host: %s", CONFIG_USB_DISPLAY_VENDOR_STRING);
  // Whether the network listener is up belongs here, not only in a line
  // written during setup: a log opened over the API starts after that and
  // never shows it.
  if (this->port_ != 0) {
    ESP_LOGCONFIG(TAG, "  Also listening on TCP port %u", (unsigned) this->port_);
#ifdef USE_TOUCHSCREEN
    if (this->touchscreen_ != nullptr)
      ESP_LOGCONFIG(TAG, "    Touches sent back to the connected sender");
#endif
  } else {
    ESP_LOGCONFIG(TAG, "  Network: off (set port: to accept frames over Wi-Fi)");
  }
  ESP_LOGCONFIG(TAG, "  Frame buffers: %u x %u bytes", (unsigned) this->frame_buffer_count_,
                (unsigned) this->max_frame_bytes_);
  ESP_LOGCONFIG(TAG, "  Decoded buffer: %u bytes (%ux%u, rounded up to whole 16x16 units)",
                (unsigned) this->rgb_buffer_len_, (unsigned) this->padded_width_, (unsigned) this->padded_height_);
  if (this->rotation_ != 0) {
    ESP_LOGCONFIG(TAG, "  Rotation: %u degrees, drawn as %ux%u (pixel-processing accelerator)",
                  (unsigned) this->rotation_, (unsigned) this->out_width_, (unsigned) this->out_height_);
  }
  // The sender has to be told the same geometry the board rejects everything
  // else for, so print the command rather than leaving it to be matched by
  // hand against the configuration above. Rotation is deliberately absent: the
  // board does that now, and asking the sender for it as well would undo it.
#if CFG_TUD_MSC
  ESP_LOGCONFIG(TAG, "  Host sender: UDISP.PY, on the drive this board presents");
  ESP_LOGCONFIG(TAG, "    python UDISP.PY --width %u --height %u", (unsigned) this->width_, (unsigned) this->height_);
#else
  ESP_LOGCONFIG(TAG, "  Host sender: python udisp_send.py --width %u --height %u", (unsigned) this->render_width_,
                (unsigned) this->render_height_);
#endif
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace usb_display
}  // namespace esphome
