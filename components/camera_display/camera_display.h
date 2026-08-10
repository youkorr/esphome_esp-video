#pragma once

#include "esphome/core/component.h"
#include "esphome/components/display/display.h"
#include "esphome/components/esp_cam_sensor/esp_cam_sensor_camera.h"

namespace esphome {
namespace camera_display {

/// Draws camera frames straight onto a display, without going through LVGL.
///
/// This is the path Espressif's own esp-iot-solution video_lcd_display example
/// takes: V4L2 -> PPA -> esp_lcd_panel_draw_bitmap(). ESPHome's equivalent of
/// that last call is Display::draw_pixels_at(), which on mipi_dsi forwards the
/// caller's pointer to esp_lcd_panel_draw_bitmap() unchanged when the bitness
/// matches and there is no offset or padding -- so the frame reaches the panel
/// with no CPU copy.
///
/// The frame is already scaled and rotated by esp_cam_sensor's PPA stage, so
/// this component only moves it.
class CameraDisplay : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_camera(esp_cam_sensor::MipiDSICamComponent *camera) { this->camera_ = camera; }
  void set_display(display::Display *disp) { this->display_ = disp; }
  void set_position(int x, int y) {
    this->x_ = x;
    this->y_ = y;
  }
  void set_enabled(bool enabled) { this->enabled_ = enabled; }

 protected:
  esp_cam_sensor::MipiDSICamComponent *camera_{nullptr};
  display::Display *display_{nullptr};
  int x_{0};
  int y_{0};
  bool enabled_{true};

  // Throughput, reported on an interval so the direct path can be compared with
  // the LVGL canvas one on the same hardware.
  static constexpr uint32_t STATS_INTERVAL_MS = 5000;
  uint32_t stats_since_ms_{0};
  uint32_t stats_frames_{0};
  uint32_t stats_draw_us_{0};
};

}  // namespace camera_display
}  // namespace esphome
