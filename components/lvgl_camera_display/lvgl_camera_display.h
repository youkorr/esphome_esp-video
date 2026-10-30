#pragma once

#include "esphome/core/component.h"
#include "esphome/components/lvgl/lvgl_esphome.h"
#include "esphome/components/esp_cam_sensor/esp_cam_sensor_camera.h"
#include "esphome/components/display/display.h"

// Forward declarations
namespace esphome {
namespace face_detection {
class FaceDetectionComponent;
}
namespace yolo11_detection {
class YOLO11DetectionComponent;
}
namespace pedestrian_detection {
class PedestrianDetectionComponent;
}
}

namespace esphome {
namespace lvgl_camera_display {

class LVGLCameraDisplay : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_camera(esp_cam_sensor::MipiDSICamComponent *camera) { this->camera_ = camera; }
  void set_canvas_id(const std::string &canvas_id) { this->canvas_id_ = canvas_id; }
  void set_update_interval(uint32_t interval_ms) { this->update_interval_ = interval_ms; }
  void set_enabled(bool enabled) { this->enabled_ = enabled; }
  // Direct mode: each frame is turned by the PPA and written straight to the
  // display, where LVGL would have copied it into the canvas, redrawn the
  // canvas and turned the whole area in its flush. `rotation` is the lvgl:
  // block's and `depth` the display's color_depth, both read at codegen.
  void set_direct(display::Display *display, int rotation, int depth) {
    this->direct_display_ = display;
    this->direct_rotation_ = rotation;
    this->direct_depth_ = depth;
  }
#ifdef USE_FACE_DETECTION
  void set_face_detection(face_detection::FaceDetectionComponent *face_detect) { this->face_detection_ = face_detect; }
#endif
#ifdef USE_YOLO11_DETECTION
  void set_yolo11_detection(yolo11_detection::YOLO11DetectionComponent *yolo11_detect) { this->yolo11_detection_ = yolo11_detect; }
#endif
#ifdef USE_PEDESTRIAN_DETECTION
  void set_pedestrian_detection(pedestrian_detection::PedestrianDetectionComponent *ped_detect) { this->pedestrian_detection_ = ped_detect; }
#endif

  void configure_canvas(lv_obj_t *canvas);
  void set_stats_label(lv_obj_t *label);

  // Getters for stats (for external display)
  float get_fps() const { return this->stats_fps_; }
  float get_cpu_percent() const { return this->stats_cpu_percent_; }
  float get_frame_time() const { return this->stats_frame_time_; }

  float get_setup_priority() const override { return setup_priority::LATE; }

  // Static callback for LVGL timer
  static void lvgl_timer_callback_(lv_timer_t *timer);

 protected:
  esp_cam_sensor::MipiDSICamComponent *camera_{nullptr};
#ifdef USE_FACE_DETECTION
  face_detection::FaceDetectionComponent *face_detection_{nullptr};  // Optional
#endif
#ifdef USE_YOLO11_DETECTION
  yolo11_detection::YOLO11DetectionComponent *yolo11_detection_{nullptr};  // Optional
#endif
#ifdef USE_PEDESTRIAN_DETECTION
  pedestrian_detection::PedestrianDetectionComponent *pedestrian_detection_{nullptr};  // Optional
#endif
  lv_obj_t *canvas_obj_{nullptr};
  std::string canvas_id_{};

  uint32_t update_interval_{33};
  uint32_t last_update_{0};

  uint32_t frame_count_{0};
  bool first_update_{true};
  bool canvas_warning_shown_{false};
  bool enabled_{false};  // LVGL camera display enabled/disabled by switch

  uint32_t last_fps_time_{0};

  lv_timer_t *lvgl_timer_{nullptr};

  // Buffer pool tracking (pour release apres affichage)
  esp_cam_sensor::SimpleBufferElement *displayed_buffer_{nullptr};

  // LVGL 9.4 Zero-copy: draw buffer that points directly to camera buffer
  lv_draw_buf_t camera_draw_buf_{};
  bool draw_buf_initialized_{false};
  bool is_canvas_{false};  // true if widget is canvas (uses memcpy), false if image (uses zero-copy)

  // Benchmark stats for UI display
  lv_obj_t *stats_label_{nullptr};
  float stats_fps_{0.0f};
  float stats_cpu_percent_{0.0f};
  float stats_frame_time_{0.0f};
  float stats_lvgl_overhead_{0.0f};

  // Direct mode (see set_direct). Null when the YAML did not ask for it.
  display::Display *direct_display_{nullptr};
  int direct_rotation_{0};
  int direct_depth_{16};
  void *ppa_client_{nullptr};  // ppa_client_handle_t, kept out of this header
  uint8_t *direct_buf_{nullptr};
  size_t direct_buf_len_{0};
  bool direct_seeded_{false};   // LVGL has been given the canvas once since it became visible
  bool direct_failed_{false};   // said once, and the frame went through LVGL instead
  uint32_t direct_ppa_us_{0};   // summed over a benchmark window
  uint32_t direct_draw_us_{0};
  uint32_t direct_frames_{0};

  bool canvas_on_screen_();
  bool draw_direct_(const uint8_t *frame, uint16_t width, uint16_t height);

  void update_camera_frame_();
  void update_canvas_();
  void update_stats_label_();
};

}  // namespace lvgl_camera_display
}  // namespace esphome
