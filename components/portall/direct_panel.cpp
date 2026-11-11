// SPDX-License-Identifier: MIT
// Copyright (c) 2026 youkorr
#include "portall.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "freertos/task.h"

namespace esphome::portall {
static const char *const TAG = "portall";

void Portall::setup_panel_buffers_() {
  if (!this->panel_buffers_requested_)
    return;
#if PORTALL_HAS_DIRECT_PANEL
  const auto &panel = mipi_dsi::g_direct_panel;
  if (panel.display != this->display_ || panel.acquire == nullptr || panel.present == nullptr ||
      panel.cancel == nullptr || panel.width != this->width_ || panel.height != this->height_ ||
      panel.bytes_per_pixel != 2 || this->bytes_per_pixel_ != 2 || this->width_ % 16 || this->height_ % 16 ||
      panel.buffer_size < this->rgb_buffer_len_ || this->rotation_ != 0 || this->scaling_) {
    ESP_LOGW(TAG, "panel_buffers: no matching, aligned three-buffer panel; keeping the normal copy path");
    return;
  }
  this->direct_panel_ = &panel;
  this->panel_buffers_active_ = true;
  ESP_LOGI(TAG, "panel_buffers: whole JPEGs decode into the panel's three buffers; rectangles use normal blits");
#else
  ESP_LOGW(TAG, "panel_buffers: the display driver has no synchronized direct-buffer API; keeping normal copies");
#endif
}

uint8_t *Portall::acquire_panel_frame_(const Frame &frame, size_t &room) {
#if PORTALL_HAS_DIRECT_PANEL
  if (!this->panel_buffers_active_ || frame.x != 0 || frame.y != 0 || frame.width != this->width_ ||
      frame.height != this->height_)
    return nullptr;
  const uint32_t start = micros();
  while (this->draws_finished_.load(std::memory_order_acquire) !=
         this->draws_queued_.load(std::memory_order_relaxed))
    vTaskDelay(1);
  uint8_t *buffer = this->direct_panel_->acquire(this->direct_panel_->instance, 100);
  this->panel_wait_us_ += micros() - start;
  if (buffer != nullptr)
    room = this->direct_panel_->buffer_size;
  return buffer;
#else
  (void) frame;
  (void) room;
  return nullptr;
#endif
}

void Portall::cancel_panel_frame_(uint8_t *buffer) {
#if PORTALL_HAS_DIRECT_PANEL
  this->direct_panel_->cancel(this->direct_panel_->instance, buffer);
#else
  (void) buffer;
#endif
}

void Portall::present_panel_frame_(const Frame &frame, uint8_t *buffer) {
#if PORTALL_HAS_DIRECT_PANEL
  const uint32_t start = micros();
  if (!this->direct_panel_->present(this->direct_panel_->instance, buffer)) {
    this->cancel_panel_frame_(buffer);
    this->frames_dropped_++;
    this->dropped_no_buffer_++;
    ESP_LOGW(TAG, "panel_buffers: the display refused a decoded frame");
    return;
  }
  this->draw_us_ += micros() - start;
  this->panel_frames_++;
  this->note_drawn_(frame, 0, 0, frame.width, frame.height);
#else
  (void) frame;
  (void) buffer;
#endif
}
}  // namespace esphome::portall
