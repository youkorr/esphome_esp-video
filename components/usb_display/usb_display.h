#pragma once

#include "esphome/core/component.h"
#include "esphome/core/defines.h"
#include "esphome/components/display/display.h"
#ifdef USE_TOUCHSCREEN
#include "esphome/components/touchscreen/touchscreen.h"
#endif

#include <cstdint>

extern "C" {
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "driver/jpeg_decode.h"
#include "driver/ppa.h"
#include "tusb.h"
#include "usb_descriptors.h"
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
class USBDisplay : public Component
#if CFG_TUD_HID
    ,
                   public touchscreen::TouchListener
#endif
{
 public:
  void setup() override;
  void loop() override;
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
  /// Clockwise, in degrees; 0, 90, 180 or 270. Done by the P4's pixel-processing
  /// accelerator, so it costs no CPU.
  void set_rotation(uint16_t degrees) { this->rotation_ = degrees; }
  /// The PC-side sender, compiled in so the board can hand it over on a
  /// read-only drive instead of sending the user to find it.
  void set_sender_script(const uint8_t *data, size_t length) {
    this->sender_script_ = data;
    this->sender_script_len_ = length;
  }

#if CFG_TUD_HID
  void set_touchscreen(touchscreen::Touchscreen *touchscreen) { this->touchscreen_ = touchscreen; }
  // Every poll of the touch screen, with all contacts currently down.
  void update(const touchscreen::TouchPoints_t &points) override;
  void release() override;
#endif

  // Called from TinyUSB's receive callback; see the .cpp for why this is
  // reachable from C.
  void on_vendor_rx(uint8_t itf);
  /// Set when the host selects a configuration, which it only does once a
  /// driver has claimed the device.
  void set_configured() { this->configured_ = true; }

 protected:
  /// One frame in flight: a compressed frame being filled by USB, or a full one
  /// waiting to be decoded.
  struct Frame {
    uint8_t *data{nullptr};
    size_t capacity{0};
    size_t received{0};
    size_t total{0};
    // Where on the panel this rectangle goes. A sender that redraws everything
    // sends the whole panel at 0,0; an indirect display driver sends only what
    // changed, which is most of the reason it is faster.
    uint16_t x{0};
    uint16_t y{0};
    uint16_t width{0};
    uint16_t height{0};
  };

  // Decodes whatever the USB side has finished and draws it. Runs on its own
  // task: at the frame rates this is built for, ESPHome's loop is nowhere near
  // often enough, and a JPEG decode plus a full-panel blit is far too much to
  // put in front of every other component.
  static void decode_task(void *param);
  void run_decode_task();

  // Turns the decoded frame into rot_buffer_ with the P4's pixel-processing
  // accelerator. Nothing here touches the CPU: it is a DMA engine that reads
  // one buffer and writes the other.
  bool rotate_(const Frame &frame, uint16_t padded_width, uint16_t padded_height);
  bool allocate_rotation_();
  /// Where a rectangle sent for the unrotated panel lands once the panel is
  /// turned. Also swaps the size on a quarter turn.
  void place_(const Frame &frame, uint16_t &x, uint16_t &y, uint16_t &w, uint16_t &h) const;
  // Lays the sender out as a FAT12 volume for the mass-storage interface to
  // serve. Defined in sender_drive.cpp, and compiled away with it.
  void setup_sender_drive_();
#if CFG_TUD_HID
  // Defined in touch.cpp, and compiled away with it.
  void setup_touch_();
  bool send_touch_report_(const udisp_touch_report_t &report);
  void retry_release_();
#endif

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
  // Decoded RGB565, handed to the display. The decoder writes whole 16x16
  // minimum coded units, so this is sized for the resolution rounded up to a
  // multiple of 16 on both axes, not for the resolution itself.
  uint8_t *rgb_buffer_{nullptr};
  size_t rgb_buffer_len_{0};
  uint16_t padded_width_{0};
  uint16_t padded_height_{0};

  // Rotation, for a panel that is not mounted the way the host sends its
  // frames. Null client means none was asked for and nothing is allocated.
  uint16_t rotation_{0};
  ppa_client_handle_t ppa_client_{nullptr};
  uint8_t *rot_buffer_{nullptr};
  size_t rot_buffer_len_{0};
  // What ends up on the panel: the frame size, with the axes swapped by a
  // quarter turn.
  uint16_t out_width_{0};
  uint16_t out_height_{0};

  const uint8_t *sender_script_{nullptr};
  size_t sender_script_len_{0};

#if CFG_TUD_HID
  touchscreen::Touchscreen *touchscreen_{nullptr};
  // A release the host has not been told about yet. Losing a press costs one
  // poll; losing a release leaves a finger down forever.
  bool release_pending_{false};
#endif

  // Reported on an interval, like the camera components.
  uint32_t frames_drawn_{0};
  uint32_t frames_dropped_{0};
  // Why they were dropped. A single total says something is wrong without
  // saying what, and the three causes have nothing to do with each other: no
  // free buffer is the decoder falling behind, a decode failure is the frame
  // itself, a rotation failure is the accelerator.
  uint32_t dropped_no_buffer_{0};
  uint32_t dropped_decode_{0};
  uint32_t dropped_rotate_{0};
  // The last rectangle drawn, which is not the panel once a driver sends only
  // what changed.
  uint16_t last_frame_w_{0};
  uint16_t last_frame_h_{0};
  uint32_t draw_us_{0};
  uint32_t stats_since_ms_{0};
  // The last frame rate reported, so a steady stream is not restated every five
  // seconds for as long as it runs.
  float last_fps_{0.0f};
  bool logged_stats_{false};
  bool was_dropping_{false};
  bool logged_first_frame_{false};
  // One-shots so a wrong-sized sender and a silent bus are distinguishable
  // without turning the log into a per-packet trace.
  bool logged_first_bytes_{false};
  bool logged_bad_header_{false};
  bool logged_decode_error_{false};
  bool logged_rotate_error_{false};
  // A device nobody has claimed is never configured, and the only trace of that
  // is a callback that does not happen -- so watch for it not happening.
  bool configured_{false};
  bool logged_unclaimed_{false};
  uint32_t started_ms_{0};
};

}  // namespace usb_display
}  // namespace esphome
