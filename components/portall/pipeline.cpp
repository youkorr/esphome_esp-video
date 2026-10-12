// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
#include "portall.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <cstdlib>

extern "C" {
#include "freertos/task.h"
}

namespace esphome {
namespace portall {

static const char *const TAG = "portall";

/* The decode of one picture, overlapped with the draw of the one before it.
 *
 * A picture goes through two engines and neither is the CPU: the JPEG
 * decoder writes it into a decode buffer, and the display's 2D-DMA copies it
 * from there into the frame buffer -- after the accelerator has turned it,
 * when the panel is turned. On one task those take turns, so each picture
 * costs the decode PLUS the draw, while the two engines sit idle half the
 * time each. The CPU and free PSRAM looking idle while a video plays is that,
 * read from the other side.
 *
 * Espressif's own usb_extend_screen, which this protocol comes from, does it
 * with CONFIG_EXAMPLE_LCD_BUF_COUNT decode buffers used in turn (main/
 * app_lcd.c), and claims 1024x600 at 60 pictures a second on a P4 that way.
 * Here it is the same idea through ESPHome's display, which needs no change to
 * ESPHome: a second decode buffer and a task of its own for the draw. The
 * decoder takes whichever buffer nothing is reading, decodes into it and
 * hands it on; the draw task draws it and gives it back. While the display is
 * copying picture N, the JPEG engine is already decoding N+1.
 *
 * Why it can overlap at all, read rather than assumed: the JPEG decoder takes
 * one TX and one RX channel of the 2D-DMA (jpeg_decode.c, v5.5.5), the
 * display's copy one of each (esp_lcd_panel_dpi.c, esp_async_fbcpy), the
 * accelerator one of each, and a P4 has three TX and two RX channels (four
 * and three from v3.0, soc_caps.h). The draw task uses the accelerator and
 * the copy one after the other, so at most two run at once, and the 2D-DMA
 * driver queues a transaction that finds no channel rather than failing.
 *
 * What it does NOT make free is external memory: the decoder writing, the
 * copy reading and writing and the display's own scanout all share PSRAM's
 * bandwidth. A panel that flickers with this and not without is the scanout
 * starved, and `decode_buffers: 1` is the way back.
 *
 * Order is kept: one queue, first in first out, so the rectangles of a picture
 * reach the display in the order they arrived and a later picture is never
 * drawn before an earlier one. Ownership is simple enough to state in one
 * line each: a decode buffer is either in spare_queue_ (nobody's), held by the
 * decoder, in draw_queue_, or held by the draw task -- never two at once.
 * There are two buffers and each queue holds two, so no send can ever find a
 * queue full. */

bool Portall::start_pipeline_() {
  if (this->decode_buffers_ < 2)
    return false;
#ifdef USE_LVGL
  // Canvas mode hands its pictures to LVGL's thread and paces itself on
  // LVGL's refresh, with a pair of canvas buffers of its own; nothing is drawn
  // by a task here in that mode.
  if (this->canvas_ != nullptr)
    return false;
#endif
  // The same allocator and the same size as the first: the decoder writes it
  // by DMA, so it has to start on a cache line and be a whole number of them.
  jpeg_decode_memory_alloc_cfg_t out_cfg = {};
  out_cfg.buffer_direction = JPEG_DEC_ALLOC_OUTPUT_BUFFER;
  size_t got = 0;
  this->rgb_spare_ = (uint8_t *) jpeg_alloc_decoder_mem(this->rgb_buffer_len_, &out_cfg, &got);
  if (this->rgb_spare_ == nullptr || got < this->rgb_buffer_len_) {
    // A panel that draws more slowly is better than one that does not draw:
    // the board carries on exactly as it did before this existed.
    ESP_LOGW(TAG, "No room for a second %u byte decode buffer; each picture will be decoded, then drawn",
             (unsigned) this->rgb_buffer_len_);
    free(this->rgb_spare_);
    this->rgb_spare_ = nullptr;
    return false;
  }
  this->spare_queue_ = xQueueCreate(2, sizeof(uint8_t *));
  this->draw_queue_ = xQueueCreate(2, sizeof(Drawing));
  if (this->spare_queue_ == nullptr || this->draw_queue_ == nullptr) {
    ESP_LOGW(TAG, "Could not create the queues between the decoder and the display; drawing on the decoder");
    if (this->spare_queue_ != nullptr)
      vQueueDelete(this->spare_queue_);
    if (this->draw_queue_ != nullptr)
      vQueueDelete(this->draw_queue_);
    this->spare_queue_ = nullptr;
    this->draw_queue_ = nullptr;
    free(this->rgb_spare_);
    this->rgb_spare_ = nullptr;
    return false;
  }
  uint8_t *first = this->rgb_buffer_;
  uint8_t *second = this->rgb_spare_;
  xQueueSend(this->spare_queue_, &first, 0);
  xQueueSend(this->spare_queue_, &second, 0);
  // Same priority and core as the decoder. Both spend nearly all their time
  // asleep while a DMA engine works, so they share core 1 without starving
  // each other -- and neither goes above micro_wake_word's 3, which is why the
  // decoder is 2 (see setup()).
  if (xTaskCreatePinnedToCore(Portall::draw_task, "udisp_draw", 4096, this, 2, nullptr, 1) != pdPASS) {
    ESP_LOGW(TAG, "Could not start the draw task; drawing on the decoder");
    return false;
  }
  return true;
}

void Portall::draw_task(void *param) { static_cast<Portall *>(param)->run_draw_task(); }

void Portall::run_draw_task() {
  Drawing drawing;
  while (true) {
    if (xQueueReceive(this->draw_queue_, &drawing, portMAX_DELAY) != pdTRUE)
      continue;
    this->draw_decoded_(drawing.frame, drawing.pixels, drawing.padded_width, drawing.padded_height);
    // Only now is the buffer free. The accelerator's pass is blocking, and
    // mipi_dsi's draw_pixels_at returns once the DPI driver says the copy is
    // done (its on_color_trans_done, given from the 2D-DMA's own completion),
    // so nothing reads it after this line.
    xQueueSend(this->spare_queue_, &drawing.pixels, portMAX_DELAY);
    this->report_stats_();
  }
}

uint8_t *Portall::take_decode_buffer_() {
  if (!this->pipelined_)
    return this->rgb_buffer_;
  uint8_t *buffer = nullptr;
  const uint32_t started = micros();
  // Both taken means one picture is being drawn and the next is waiting for
  // the display: the decoder has nowhere to write until the draw finishes.
  // That wait is the flow control, the same way a full frame queue holds the
  // socket shut -- and it is what the stats line reports, because it says the
  // display rather than the decoder is setting the pace.
  while (xQueueReceive(this->spare_queue_, &buffer, portMAX_DELAY) != pdTRUE) {
  }
  this->decode_waited_us_ += micros() - started;
  return buffer;
}

void Portall::give_back_(uint8_t *buffer) {
  if (this->pipelined_)
    xQueueSend(this->spare_queue_, &buffer, portMAX_DELAY);
}

void Portall::hand_over_(const Frame &frame, uint8_t *decoded, uint16_t padded_width, uint16_t padded_height) {
  if (!this->pipelined_) {
    this->draw_decoded_(frame, decoded, padded_width, padded_height);
    return;
  }
  Drawing drawing;
  drawing.frame = frame;
  // The compressed bytes are going back to the socket as soon as this
  // returns; nothing after the decode reads them, and a pointer that is not
  // there cannot be read by mistake.
  drawing.frame.data = nullptr;
  drawing.pixels = decoded;
  drawing.padded_width = padded_width;
  drawing.padded_height = padded_height;
  xQueueSend(this->draw_queue_, &drawing, portMAX_DELAY);
}

}  // namespace portall
}  // namespace esphome
