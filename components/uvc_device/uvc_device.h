#pragma once

#include "esphome/core/component.h"

#include <cstdint>

extern "C" {
#include "usb_device_uvc.h"
}

namespace esphome {
namespace uvc_device {

/**
 * @brief Turns the ESP32-P4 into a USB webcam.
 *
 * The MIPI-CSI sensor feeds the hardware JPEG encoder, and usb_device_uvc
 * presents the encoded frames to whatever is plugged into the USB port. This is
 * Espressif's esp_video `uvc` example, restructured as an ESPHome component.
 *
 * Nothing runs in loop(). usb_device_uvc drives the pipeline from its own task
 * through the four callbacks below, which is what keeps the frame rate off
 * ESPHome's main loop.
 */
class UVCDevice : public Component {
 public:
  void setup() override;
  void dump_config() override;

  // After esp_video (DATA): esp_video_init() has to have created the devices.
  float get_setup_priority() const override { return setup_priority::DATA - 10.0f; }

  void set_resolution(uint32_t width, uint32_t height) {
    this->width_ = width;
    this->height_ = height;
  }
  void set_framerate(uint32_t rate) { this->framerate_ = rate; }
  void set_jpeg_quality(int quality) { this->jpeg_quality_ = quality; }

 protected:
  // usb_device_uvc callbacks. Static thunks; `ctx` is the component.
  static esp_err_t start_cb(uvc_format_t format, int width, int height, int rate, void *ctx);
  static uvc_fb_t *fb_get_cb(void *ctx);
  static void fb_return_cb(uvc_fb_t *fb, void *ctx);
  static void stop_cb(void *ctx);

  // The host opened the stream: configure both devices and STREAMON.
  esp_err_t on_start_(int width, int height);
  // The host wants the next frame: capture -> encode -> hand it over.
  uvc_fb_t *on_fb_get_();
  // The host is done with the frame we handed it.
  void on_fb_return_();
  // The host closed the stream.
  void on_stop_();

  void teardown_();

  // Two capture buffers so the sensor can fill one while the other encodes.
  static constexpr int CAPTURE_BUFFER_COUNT = 3;

  uint32_t width_{1280};
  uint32_t height_{720};
  uint32_t framerate_{15};
  int jpeg_quality_{80};

  // MIPI-CSI capture device (/dev/video0), producing raw frames.
  int capture_fd_{-1};
  uint8_t *capture_buffer_[CAPTURE_BUFFER_COUNT]{};
  size_t capture_buffer_len_[CAPTURE_BUFFER_COUNT]{};
  // Pixel format the sensor and the encoder agreed on, picked in on_start_().
  uint32_t capture_format_{0};

  // Hardware JPEG encoder (/dev/video10), memory-to-memory.
  int encoder_fd_{-1};
  uint8_t *encoder_buffer_{nullptr};
  size_t encoder_buffer_len_{0};

  // Handed to usb_device_uvc, which memcpy()s out of it, so it outlives a frame.
  uint8_t *uvc_buffer_{nullptr};
  size_t uvc_buffer_size_{0};

  bool streaming_{false};
  uvc_fb_t fb_{};

  // Throughput of what this board hands to the host, reported on an interval.
  uint32_t frames_{0};
  uint32_t bytes_{0};
  uint32_t stats_since_ms_{0};
};

}  // namespace uvc_device
}  // namespace esphome
