#pragma once

#include "esphome/core/component.h"
#include "esphome/components/camera/camera.h"

#include <memory>
#include <string>
#include <vector>

namespace esphome {
namespace esp_video_camera {

/**
 * @brief Une frame JPEG/MJPEG possédée (copie en PSRAM) partagée avec l'API.
 *
 * La donnée est encodée JPEG (exigence de l'API caméra Home Assistant). Elle
 * est copiée depuis le buffer V4L2 mappé pour que celui-ci puisse être
 * immédiatement re-mis en file (VIDIOC_QBUF) sans attendre que l'API ait fini
 * de transmettre l'image sur le réseau.
 */
class ESPVideoCameraImage : public camera::CameraImage {
 public:
  ESPVideoCameraImage(uint8_t *data, size_t length, uint8_t requesters);
  ~ESPVideoCameraImage() override;

  uint8_t *get_data_buffer() override { return this->data_; }
  size_t get_data_length() override { return this->length_; }
  bool was_requested_by(camera::CameraRequester requester) const override;

 protected:
  uint8_t *data_{nullptr};
  size_t length_{0};
  uint8_t requesters_{0};
};

/**
 * @brief Lecteur d'image utilisé par l'API pour streamer les octets JPEG.
 */
class ESPVideoCameraImageReader : public camera::CameraImageReader {
 public:
  void set_image(std::shared_ptr<camera::CameraImage> image) override;
  size_t available() const override;
  uint8_t *peek_data_buffer() override;
  void consume_data(size_t consumed) override;
  void return_image() override;

 protected:
  std::shared_ptr<camera::CameraImage> image_;
  size_t offset_{0};
};

/**
 * @brief Caméra Home Assistant adossée au pipeline esp_video (V4L2).
 *
 * Capture des frames JPEG/MJPEG depuis un device V4L2 fourni par esp_video :
 *   - "jpeg" : encodeur JPEG matériel (/dev/video10) — fonctionne avec TOUS les
 *     capteurs MIPI-CSI auto-détectés (SC202CS, OV5647, OV02C10, SC2336...).
 *   - "uvc"  : caméra USB-UVC externe (/dev/video40) qui émet du MJPEG.
 *   - "/dev/videoN" : chemin V4L2 explicite.
 *
 * Le device n'est ouvert et le streaming V4L2 démarré qu'à la demande (quand
 * Home Assistant ouvre le flux ou demande une image), puis arrêté dès qu'il
 * n'y a plus de consommateur.
 */
class ESPVideoCamera : public camera::Camera {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  // camera::Camera ------------------------------------------------------------
  void add_listener(camera::CameraListener *listener) override { this->listeners_.push_back(listener); }
  camera::CameraImageReader *create_image_reader() override;
  void request_image(camera::CameraRequester requester) override;
  void start_stream(camera::CameraRequester requester) override;
  void stop_stream(camera::CameraRequester requester) override;

  // Configuration -------------------------------------------------------------
  void set_device(const std::string &device) { this->device_ = device; }
  void set_resolution(const std::string &resolution) { this->resolution_ = resolution; }
  void set_jpeg_quality(int quality) { this->jpeg_quality_ = quality; }
  void set_max_framerate(float fps) {
    this->max_framerate_ = fps;
    this->min_interval_ms_ = (fps > 0.0f) ? (uint32_t) (1000.0f / fps) : 0;
  }

 protected:
  bool start_capture_();
  void stop_capture_();
  void update_capture_state_();
  void configure_format_();
  static bool parse_resolution_(const std::string &res, uint32_t &width, uint32_t &height);

  // Configuration
  std::string device_{"jpeg"};
  std::string resolved_device_;
  bool is_hw_jpeg_{false};
  std::string resolution_{"auto"};
  int jpeg_quality_{10};
  float max_framerate_{10.0f};
  uint32_t min_interval_ms_{100};
  uint32_t last_frame_ms_{0};

  // Consommateurs (masques de bits indexés par camera::CameraRequester)
  std::vector<camera::CameraListener *> listeners_;
  std::shared_ptr<ESPVideoCameraImage> current_image_;
  uint8_t stream_requesters_{0};
  uint8_t single_requesters_{0};

  // État V4L2
  int fd_{-1};
  bool streaming_{false};
  static constexpr int MAX_BUFFERS = 3;
  struct MappedBuffer {
    void *start{nullptr};
    size_t length{0};
  };
  MappedBuffer buffers_[MAX_BUFFERS];
  int num_buffers_{0};
};

}  // namespace esp_video_camera
}  // namespace esphome
