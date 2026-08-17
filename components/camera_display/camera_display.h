#pragma once

#include "esphome/core/component.h"
#include "esphome/components/display/display.h"
#include "esphome/components/esp_cam_sensor/esp_cam_sensor_camera.h"

#include "driver/ppa.h"

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
/// this component only moves it: x/y place the top-left corner on the panel,
/// and the size is whatever the camera produces. There is deliberately no
/// width/height here -- set the output size on the camera.
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
  /// 0, 90, 180 or 270. Anything but 0 runs the frame through the PPA on the
  /// way to the panel, which is where Espressif's own example puts it too --
  /// the camera driver stays untouched.
  void set_rotation(int degrees) { this->rotation_ = degrees; }

 protected:
  esp_cam_sensor::MipiDSICamComponent *camera_{nullptr};
  display::Display *display_{nullptr};
  int x_{0};
  int y_{0};
  bool enabled_{true};
  int rotation_{0};

  // PPA transform, only allocated when rotation_ != 0. Without it the V4L2
  // buffer goes to the panel untouched.
  ppa_client_handle_t ppa_{nullptr};
  uint8_t *ppa_out_{nullptr};
  size_t ppa_out_size_{0};
  uint16_t out_width_{0};
  uint16_t out_height_{0};
  bool setup_ppa_(uint16_t src_width, uint16_t src_height);
  const uint8_t *transform_(const uint8_t *src, uint16_t src_width, uint16_t src_height);
  // The frame size only becomes known once the first frame arrives.
  bool bounds_checked_{false};
  bool drew_once_{false};

  // Which stage last reported that it had no frame, so the reason is logged on
  // each transition rather than on every loop iteration.
  enum : uint8_t { STAGE_NONE = 0, STAGE_STREAMING, STAGE_CAPTURE, STAGE_ACQUIRE, STAGE_PPA };
  uint8_t logged_stage_{STAGE_NONE};
  void log_stage_once_(uint8_t stage, const char *reason);

  // Throughput, reported on an interval so the direct path can be compared with
  // the LVGL canvas one on the same hardware.
  static constexpr uint32_t STATS_INTERVAL_MS = 5000;
  uint32_t stats_since_ms_{0};
  uint32_t stats_frames_{0};
  uint32_t stats_draw_us_{0};
};

}  // namespace camera_display
}  // namespace esphome
