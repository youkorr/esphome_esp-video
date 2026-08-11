#pragma once

#include "esphome/core/component.h"
#include "esphome/components/display/display.h"

#include <cstdint>

extern "C" {
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "driver/jpeg_decode.h"
}

namespace esphome {
namespace usb_display {

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
class USBDisplay : public Component {
 public:
  void setup() override;
  void dump_config() override;
  // After the display, whose panel this draws into.
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_display(display::Display *display) { this->display_ = display; }
  void set_resolution(uint16_t width, uint16_t height) {
    this->width_ = width;
    this->height_ = height;
  }
  void set_frame_buffers(uint8_t count) { this->frame_buffer_count_ = count; }
  void set_max_frame_bytes(size_t bytes) { this->max_frame_bytes_ = bytes; }

  // Called from TinyUSB's receive callback; see the .cpp for why this is
  // reachable from C.
  void on_vendor_rx(uint8_t itf);

 protected:
  /// One frame in flight: a compressed frame being filled by USB, or a full one
  /// waiting to be decoded.
  struct Frame {
    uint8_t *data{nullptr};
    size_t capacity{0};
    size_t received{0};
    size_t total{0};
    uint16_t width{0};
    uint16_t height{0};
  };

  // Decodes whatever the USB side has finished and draws it. Runs on its own
  // task: at the frame rates this is built for, ESPHome's loop is nowhere near
  // often enough, and a JPEG decode plus a full-panel blit is far too much to
  // put in front of every other component.
  static void decode_task(void *param);
  void run_decode_task();

  bool allocate_frames_();
  Frame *take_empty_();
  void queue_filled_(Frame *frame);
  bool append_(Frame *frame, const uint8_t *data, size_t len);

  display::Display *display_{nullptr};
  uint16_t width_{1024};
  uint16_t height_{600};
  uint8_t frame_buffer_count_{4};
  size_t max_frame_bytes_{128 * 1024};

  Frame *frames_{nullptr};
  QueueHandle_t empty_queue_{nullptr};
  QueueHandle_t filled_queue_{nullptr};
  // The frame the receive callback is currently filling, or null between frames.
  Frame *current_{nullptr};
  // Set when a frame arrived with no buffer free: its payload is counted out
  // and thrown away rather than being mistaken for a header.
  size_t skipping_{0};

  jpeg_decoder_handle_t jpeg_{nullptr};
  // Decoded RGB565, handed to the display. Sized for width * height * 2.
  uint8_t *rgb_buffer_{nullptr};
  size_t rgb_buffer_len_{0};

  // Reported on an interval, like the camera components.
  uint32_t frames_drawn_{0};
  uint32_t frames_dropped_{0};
  uint32_t draw_us_{0};
  uint32_t stats_since_ms_{0};
  bool logged_first_frame_{false};
};

}  // namespace usb_display
}  // namespace esphome
