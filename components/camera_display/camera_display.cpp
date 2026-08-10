#include "camera_display.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include "esp_heap_caps.h"
#include "esp_private/esp_cache_private.h"  // esp_cache_get_alignment()

namespace esphome {
namespace camera_display {

static const char *const TAG = "camera_display";

void CameraDisplay::setup() {
  if (this->camera_ == nullptr || this->display_ == nullptr) {
    this->mark_failed();
    return;
  }
  this->stats_since_ms_ = millis();
}

void CameraDisplay::loop() {
  if (!this->enabled_ || this->is_failed())
    return;

  // Nothing on screen is otherwise indistinguishable from nothing running, so
  // say which stage is not producing -- once per stage, not once per loop.
  if (!this->camera_->is_streaming()) {
    this->log_stage_once_(STAGE_STREAMING, "camera is not streaming yet");
    return;
  }
  if (!this->camera_->capture_frame()) {
    this->log_stage_once_(STAGE_CAPTURE, "camera is streaming but capture_frame() returns false");
    return;
  }

  esp_cam_sensor::SimpleBufferElement *buffer = this->camera_->acquire_buffer();
  if (buffer == nullptr) {
    this->log_stage_once_(STAGE_ACQUIRE, "frame captured but acquire_buffer() returned nothing");
    return;
  }

  uint8_t *data = this->camera_->get_buffer_data(buffer);
  uint16_t width = this->camera_->get_image_width();
  uint16_t height = this->camera_->get_image_height();

  // The frame size comes from the camera, so a position that fits on one sensor
  // format runs off the panel on another. esp_lcd_panel_draw_bitmap() is given
  // the rectangle unchecked, so catch it here rather than in the driver.
  // Rotate on the way out, if asked. The camera driver is left alone: this is
  // where Espressif's video_lcd_display example puts the PPA too.
  if (this->rotation_ != 0 && data != nullptr) {
    if (this->ppa_ == nullptr && !this->setup_ppa_(width, height)) {
      this->camera_->release_buffer(buffer);
      this->mark_failed();
      return;
    }
    data = const_cast<uint8_t *>(this->transform_(data, width, height));
    width = this->out_width_;
    height = this->out_height_;
  }

  if (data != nullptr && width != 0 && height != 0 && !this->bounds_checked_) {
    this->bounds_checked_ = true;
    int dw = this->display_->get_width();
    int dh = this->display_->get_height();
    if (this->x_ + width > dw || this->y_ + height > dh) {
      ESP_LOGE(TAG, "%ux%u at %d,%d does not fit a %dx%d display; set the size on the camera, not here", width, height,
               this->x_, this->y_, dw, dh);
      this->camera_->release_buffer(buffer);
      this->mark_failed();
      return;
    }
  }

  if (data != nullptr && width != 0 && height != 0) {
    if (!this->drew_once_) {
      this->drew_once_ = true;
      ESP_LOGI(TAG, "First frame: %ux%u at %d,%d on a %dx%d display", width, height, this->x_, this->y_,
               this->display_->get_width(), this->display_->get_height());
    }
    uint32_t start = micros();
    // The packed overload passes x_offset/y_offset/x_pad = 0, which is what
    // keeps mipi_dsi on its single-transfer path instead of one DMA per line.
    // Endianness is ignored there: the fast path assumes the source already
    // matches the panel, which RGB565 from the ISP does.
    this->display_->draw_pixels_at(this->x_, this->y_, width, height, data, display::COLOR_ORDER_RGB,
                                   display::COLOR_BITNESS_565, false);
    this->stats_draw_us_ += micros() - start;
    this->stats_frames_++;
  }

  this->camera_->release_buffer(buffer);

  uint32_t elapsed = millis() - this->stats_since_ms_;
  if (elapsed >= STATS_INTERVAL_MS && this->stats_frames_ > 0) {
    ESP_LOGD(TAG, "%ux%u @ %.1f fps, %u us/draw", (unsigned) width, (unsigned) height,
             this->stats_frames_ * 1000.0f / elapsed, (unsigned) (this->stats_draw_us_ / this->stats_frames_));
    this->stats_since_ms_ = millis();
    this->stats_frames_ = 0;
    this->stats_draw_us_ = 0;
  }
}

bool CameraDisplay::setup_ppa_(uint16_t src_width, uint16_t src_height) {
  const bool swaps = this->rotation_ == 90 || this->rotation_ == 270;
  this->out_width_ = swaps ? src_height : src_width;
  this->out_height_ = swaps ? src_width : src_height;

  ppa_client_config_t cfg = {};
  cfg.oper_type = PPA_OPERATION_SRM;
  cfg.max_pending_trans_num = 1;
  esp_err_t err = ppa_register_client(&cfg, &this->ppa_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "ppa_register_client failed: %s", esp_err_to_name(err));
    return false;
  }

  size_t align = 64;
  esp_cache_get_alignment(MALLOC_CAP_SPIRAM, &align);
  if (align < 64)
    align = 64;
  this->ppa_out_size_ = (size_t) this->out_width_ * this->out_height_ * 2;
  this->ppa_out_size_ = (this->ppa_out_size_ + align - 1) / align * align;
  this->ppa_out_ = (uint8_t *) heap_caps_aligned_alloc(align, this->ppa_out_size_, MALLOC_CAP_SPIRAM);
  if (this->ppa_out_ == nullptr) {
    ESP_LOGE(TAG, "no room for a %u byte PPA output buffer", (unsigned) this->ppa_out_size_);
    return false;
  }
  ESP_LOGI(TAG, "PPA rotation %d deg: %ux%u -> %ux%u", this->rotation_, src_width, src_height, this->out_width_,
           this->out_height_);
  return true;
}

const uint8_t *CameraDisplay::transform_(const uint8_t *src, uint16_t src_width, uint16_t src_height) {
  ppa_srm_oper_config_t cfg = {};
  cfg.in.buffer = src;
  cfg.in.pic_w = src_width;
  cfg.in.pic_h = src_height;
  cfg.in.block_w = src_width;
  cfg.in.block_h = src_height;
  cfg.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  cfg.out.buffer = this->ppa_out_;
  cfg.out.buffer_size = this->ppa_out_size_;
  cfg.out.pic_w = this->out_width_;
  cfg.out.pic_h = this->out_height_;
  cfg.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  // The PPA turns counter-clockwise, so a clockwise request maps to its
  // complement -- the same inversion ESPHome makes in lvgl_esphome.cpp.
  switch (this->rotation_) {
    case 90:
      cfg.rotation_angle = PPA_SRM_ROTATION_ANGLE_270;
      break;
    case 180:
      cfg.rotation_angle = PPA_SRM_ROTATION_ANGLE_180;
      break;
    case 270:
      cfg.rotation_angle = PPA_SRM_ROTATION_ANGLE_90;
      break;
    default:
      cfg.rotation_angle = PPA_SRM_ROTATION_ANGLE_0;
      break;
  }
  cfg.scale_x = 1.0f;
  cfg.scale_y = 1.0f;
  cfg.mode = PPA_TRANS_MODE_BLOCKING;

  esp_err_t err = ppa_do_scale_rotate_mirror(this->ppa_, &cfg);
  if (err != ESP_OK) {
    this->log_stage_once_(STAGE_PPA, esp_err_to_name(err));
    return nullptr;
  }
  return this->ppa_out_;
}

void CameraDisplay::log_stage_once_(uint8_t stage, const char *reason) {
  if (this->logged_stage_ == stage)
    return;
  this->logged_stage_ = stage;
  ESP_LOGW(TAG, "No frame drawn: %s", reason);
}

void CameraDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "Camera Display:");
  ESP_LOGCONFIG(TAG, "  Position: %d,%d", this->x_, this->y_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace camera_display
}  // namespace esphome
