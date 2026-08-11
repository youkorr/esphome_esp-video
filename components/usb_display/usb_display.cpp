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
// How far the frame rate has to move before it is worth another line.
static constexpr float STATS_FPS_EPSILON = 1.0f;

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
  this->padded_width_ = (this->width_ + 15) & ~15;
  this->padded_height_ = (this->height_ + 15) & ~15;

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

  if (this->rotation_ != 0 && !this->allocate_rotation_()) {
    this->mark_failed(LOG_STR("Rotation setup failed"));
    return;
  }

  if (!this->allocate_frames_()) {
    this->mark_failed(LOG_STR("Frame buffer allocation failed"));
    return;
  }

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

bool USBDisplay::rotate_() {
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
      return false;
  }

  ppa_srm_oper_config_t srm = {};
  // The decoded frame sits at the top left of a buffer rounded up to whole
  // 16x16 units, so read it as a block out of the larger picture rather than
  // rotating the padding along with it.
  srm.in.buffer = this->rgb_buffer_;
  srm.in.pic_w = this->padded_width_;
  srm.in.pic_h = this->padded_height_;
  srm.in.block_w = this->width_;
  srm.in.block_h = this->height_;
  srm.in.block_offset_x = 0;
  srm.in.block_offset_y = 0;
  srm.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  srm.out.buffer = this->rot_buffer_;
  srm.out.buffer_size = this->rot_buffer_len_;
  srm.out.pic_w = this->out_width_;
  srm.out.pic_h = this->out_height_;
  srm.out.block_offset_x = 0;
  srm.out.block_offset_y = 0;
  srm.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  srm.rotation_angle = angle;
  srm.scale_x = 1.0f;
  srm.scale_y = 1.0f;
  srm.mode = PPA_TRANS_MODE_BLOCKING;

  esp_err_t err = ppa_do_scale_rotate_mirror(this->ppa_client_, &srm);
  if (err != ESP_OK) {
    if (!this->logged_rotate_error_) {
      this->logged_rotate_error_ = true;
      ESP_LOGE(TAG, "PPA rotation failed: %s (%ux%u out of %ux%u, into %ux%u, %u byte buffer)", esp_err_to_name(err),
               (unsigned) this->width_, (unsigned) this->height_, (unsigned) this->padded_width_,
               (unsigned) this->padded_height_, (unsigned) this->out_width_, (unsigned) this->out_height_,
               (unsigned) this->rot_buffer_len_);
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

USBDisplay::Frame *USBDisplay::take_empty_() {
  Frame *frame = nullptr;
  if (xQueueReceive(this->empty_queue_, &frame, 0) != pdTRUE)
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
void USBDisplay::on_vendor_rx(uint8_t itf) {
  static uint8_t rx_buf[CFG_TUD_VENDOR_EPSIZE];

  while (tud_vendor_n_available(itf)) {
    int read = tud_vendor_n_read(itf, rx_buf, sizeof(rx_buf));
    if (read <= 0)
      break;

    if (!this->logged_first_bytes_) {
      this->logged_first_bytes_ = true;
      ESP_LOGI(TAG, "First %d bytes from the host", read);
    }

    if (this->skipping_ > 0) {
      this->skipping_ = (this->skipping_ > (size_t) read) ? this->skipping_ - read : 0;
      continue;
    }

    if (this->current_ != nullptr) {
      if (this->append_(this->current_, rx_buf, read)) {
        this->queue_filled_(this->current_);
        this->current_ = nullptr;
      }
      continue;
    }

    // Start of a frame: the first packet carries the header.
    if ((size_t) read < sizeof(udisp_frame_header_t))
      continue;
    auto *header = (udisp_frame_header_t *) rx_buf;
    const bool usable = header->type == UDISP_TYPE_JPG && header->x == 0 && header->y == 0 &&
                        header->width == this->width_ && header->height == this->height_;
    if (!usable) {
      // Silently dropping these is how a sender configured for the wrong size
      // looks exactly like a sender that is not running at all.
      if (!this->logged_bad_header_) {
        this->logged_bad_header_ = true;
        ESP_LOGW(TAG, "Ignoring frames: host sends type=%u %ux%u at %u,%u, this display wants type=%u %ux%u at 0,0",
                 header->type, header->width, header->height, header->x, header->y, UDISP_TYPE_JPG,
                 (unsigned) this->width_, (unsigned) this->height_);
      }
      continue;
    }

    const uint8_t *payload = rx_buf + sizeof(udisp_frame_header_t);
    const size_t payload_len = read - sizeof(udisp_frame_header_t);

    Frame *frame = this->take_empty_();
    if (frame == nullptr) {
      // Nothing free: count this frame out so the next header is recognised
      // rather than being read out of the middle of a payload.
      this->frames_dropped_++;
      this->skipping_ = (header->payload_total > payload_len) ? header->payload_total - payload_len : 0;
      continue;
    }
    frame->width = header->width;
    frame->height = header->height;
    frame->total = header->payload_total;
    if (this->append_(frame, payload, payload_len)) {
      this->queue_filled_(frame);
    } else {
      this->current_ = frame;
    }
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

    uint32_t out_size = 0;
    esp_err_t err = jpeg_decoder_process(this->jpeg_, &decode_cfg, frame->data, frame->received, this->rgb_buffer_,
                                         this->rgb_buffer_len_, &out_size);
    if (err == ESP_OK) {
      uint32_t start = micros();
      // The decoded rows are padded_width_ wide even when fewer pixels are
      // wanted; x_pad tells the display to skip the difference at the end of
      // each line. When the width is already a multiple of 16 there is no
      // padding, and this stays on mipi_dsi's single-transfer path instead of
      // one DMA per line. The rotated buffer never has any.
      const uint8_t *pixels = this->rgb_buffer_;
      int x_pad = this->padded_width_ - this->width_;
      if (this->ppa_client_ != nullptr) {
        // Drawing the unrotated buffer instead would be worse than dropping the
        // frame: after a quarter turn out_width_ and out_height_ are swapped, so
        // it would go to the panel at the wrong shape.
        if (!this->rotate_()) {
          this->frames_dropped_++;
          xQueueSend(this->empty_queue_, &frame, 0);
          continue;
        }
        pixels = this->rot_buffer_;
        x_pad = 0;
      }
      this->display_->draw_pixels_at(0, 0, this->out_width_, this->out_height_, pixels, display::COLOR_ORDER_RGB,
                                     display::COLOR_BITNESS_565, false, 0, 0, x_pad);
      this->draw_us_ += micros() - start;
      this->frames_drawn_++;
      if (!this->logged_first_frame_) {
        this->logged_first_frame_ = true;
        ESP_LOGI(TAG, "First frame from the host: %u bytes compressed, %ux%u decoded, drawn as %ux%u",
                 (unsigned) frame->received, (unsigned) frame->width, (unsigned) frame->height,
                 (unsigned) this->out_width_, (unsigned) this->out_height_);
      }
    } else {
      this->frames_dropped_++;
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
      // when it has actually moved -- or whenever a frame was lost, which is
      // always worth a line.
      if (!this->logged_stats_ || this->frames_dropped_ > 0 || fabsf(fps - this->last_fps_) >= STATS_FPS_EPSILON) {
        ESP_LOGD(TAG, "%ux%u @ %.1f fps, %u us/draw, %u dropped", (unsigned) this->out_width_,
                 (unsigned) this->out_height_, fps, (unsigned) (this->draw_us_ / this->frames_drawn_),
                 (unsigned) this->frames_dropped_);
        this->logged_stats_ = true;
        this->last_fps_ = fps;
      }
      this->stats_since_ms_ = millis();
      this->frames_drawn_ = 0;
      this->frames_dropped_ = 0;
      this->draw_us_ = 0;
    }
  }
}

void USBDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "USB Extended Display:");
  ESP_LOGCONFIG(TAG, "  Resolution: %ux%u", (unsigned) this->width_, (unsigned) this->height_);
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
  ESP_LOGCONFIG(TAG, "  Host sender: python udisp_send.py --width %u --height %u", (unsigned) this->width_,
                (unsigned) this->height_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace usb_display
}  // namespace esphome
