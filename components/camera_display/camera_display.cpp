#include "camera_display.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

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
  if (!this->camera_->is_streaming())
    return;
  if (!this->camera_->capture_frame())
    return;

  esp_cam_sensor::SimpleBufferElement *buffer = this->camera_->acquire_buffer();
  if (buffer == nullptr)
    return;

  uint8_t *data = this->camera_->get_buffer_data(buffer);
  uint16_t width = this->camera_->get_image_width();
  uint16_t height = this->camera_->get_image_height();

  if (data != nullptr && width != 0 && height != 0) {
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

void CameraDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "Camera Display:");
  ESP_LOGCONFIG(TAG, "  Position: %d,%d", this->x_, this->y_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace camera_display
}  // namespace esphome
