#include "usb_display.h"

#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"

#include <cstring>

extern "C" {
#include "freertos/task.h"
#include "esp_private/usb_phy.h"
#include "tusb.h"
#include "usb_descriptors.h"
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display";

static constexpr uint32_t STATS_INTERVAL_MS = 5000;

// TinyUSB's callbacks are plain C with no context argument, so the one instance
// has to be reachable from file scope. A second usb_display would need a second
// USB device controller, which the ESP32-P4 does not have; the Python side
// enforces a single instance.
static USBDisplay *g_usb_display = nullptr;

extern "C" void tud_vendor_rx_cb(uint8_t itf, uint8_t const *buffer, uint16_t bufsize) {
  (void) buffer;
  (void) bufsize;
  if (g_usb_display != nullptr)
    g_usb_display->on_vendor_rx(itf);
}

namespace {

void tusb_device_task(void *param) {
  (void) param;
  while (true)
    tud_task();
}

}  // namespace

void USBDisplay::setup() {
  if (this->display_ == nullptr) {
    this->mark_failed(LOG_STR("No display"));
    return;
  }
  g_usb_display = this;

  this->rgb_buffer_len_ = (size_t) this->width_ * this->height_ * 2;
  this->rgb_buffer_ = (uint8_t *) heap_caps_aligned_alloc(64, this->rgb_buffer_len_, MALLOC_CAP_SPIRAM);
  if (this->rgb_buffer_ == nullptr) {
    ESP_LOGE(TAG, "Could not allocate the %u byte RGB565 buffer", (unsigned) this->rgb_buffer_len_);
    this->mark_failed(LOG_STR("RGB buffer allocation failed"));
    return;
  }

  if (!this->allocate_frames_()) {
    this->mark_failed(LOG_STR("Frame buffer allocation failed"));
    return;
  }

  jpeg_decode_engine_cfg_t engine_cfg = {};
  engine_cfg.intr_priority = 1;
  engine_cfg.timeout_ms = 50;
  esp_err_t err = jpeg_new_decoder_engine(&engine_cfg, &this->jpeg_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "jpeg_new_decoder_engine() failed: %s", esp_err_to_name(err));
    this->mark_failed(LOG_STR("JPEG decoder unavailable"));
    return;
  }

  // Device mode on the internal PHY, exactly as Espressif's usb_extend_screen
  // example does it. TinyUSB does not set the PHY up itself.
  usb_phy_handle_t phy = nullptr;
  usb_phy_config_t phy_conf = {};
  phy_conf.controller = USB_PHY_CTRL_OTG;
  phy_conf.otg_mode = USB_OTG_MODE_DEVICE;
  phy_conf.target = USB_PHY_TARGET_INT;
  if (usb_new_phy(&phy_conf, &phy) != ESP_OK) {
    this->mark_failed(LOG_STR("USB PHY setup failed"));
    return;
  }
  if (!tusb_init()) {
    this->mark_failed(LOG_STR("tusb_init failed"));
    return;
  }

  // Priority 5 for the USB task, 4 for the decoder: USB has to keep draining
  // the endpoint or the host stalls, and a decode that runs late only costs a
  // frame. Core 1 keeps both off the core ESPHome's loop runs on.
  xTaskCreatePinnedToCore(tusb_device_task, "usbd", 4096, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(USBDisplay::decode_task, "udisp", 4096, this, 4, nullptr, 1);

  this->stats_since_ms_ = millis();
  ESP_LOGI(TAG, "USB extended screen ready: %ux%u, %u frame buffers of %u bytes", (unsigned) this->width_,
           (unsigned) this->height_, (unsigned) this->frame_buffer_count_, (unsigned) this->max_frame_bytes_);
}

bool USBDisplay::allocate_frames_() {
  this->frames_ = new Frame[this->frame_buffer_count_];
  this->empty_queue_ = xQueueCreate(this->frame_buffer_count_, sizeof(Frame *));
  this->filled_queue_ = xQueueCreate(this->frame_buffer_count_, sizeof(Frame *));
  if (this->empty_queue_ == nullptr || this->filled_queue_ == nullptr)
    return false;

  for (uint8_t i = 0; i < this->frame_buffer_count_; i++) {
    Frame *frame = &this->frames_[i];
    frame->data = (uint8_t *) heap_caps_aligned_alloc(64, this->max_frame_bytes_, MALLOC_CAP_SPIRAM);
    if (frame->data == nullptr) {
      ESP_LOGE(TAG, "Could not allocate frame buffer %u of %u bytes", i, (unsigned) this->max_frame_bytes_);
      return false;
    }
    frame->capacity = this->max_frame_bytes_;
    Frame *ptr = frame;
    xQueueSend(this->empty_queue_, &ptr, 0);
  }
  return true;
}

USBDisplay::Frame *USBDisplay::take_empty_() {
  Frame *frame = nullptr;
  if (xQueueReceive(this->empty_queue_, &frame, 0) != pdTRUE)
    return nullptr;
  frame->received = 0;
  return frame;
}

void USBDisplay::queue_filled_(Frame *frame) {
  if (xQueueSend(this->filled_queue_, &frame, 0) != pdTRUE)
    xQueueSend(this->empty_queue_, &frame, 0);
}

bool USBDisplay::append_(Frame *frame, const uint8_t *data, size_t len) {
  if (len > 0 && frame->received + len <= frame->capacity) {
    memcpy(frame->data + frame->received, data, len);
    frame->received += len;
  } else if (len > 0) {
    // Overlong frame: keep counting so the stream stays in sync, but the
    // payload is no longer usable.
    frame->received += len;
  }
  return frame->received >= frame->total;
}

// ===========================================================================
// USB receive -- runs on the TinyUSB task
// ===========================================================================
void USBDisplay::on_vendor_rx(uint8_t itf) {
  static uint8_t rx_buf[CFG_TUD_VENDOR_EPSIZE];

  while (tud_vendor_n_available(itf)) {
    int read = tud_vendor_n_read(itf, rx_buf, sizeof(rx_buf));
    if (read <= 0)
      break;

    if (this->skipping_ > 0) {
      this->skipping_ = (this->skipping_ > (size_t) read) ? this->skipping_ - read : 0;
      continue;
    }

    if (this->current_ != nullptr) {
      if (this->append_(this->current_, rx_buf, read)) {
        this->queue_filled_(this->current_);
        this->current_ = nullptr;
      }
      continue;
    }

    // Start of a frame: the first packet carries the header.
    if ((size_t) read < sizeof(udisp_frame_header_t))
      continue;
    auto *header = (udisp_frame_header_t *) rx_buf;
    if (header->type != UDISP_TYPE_JPG)
      continue;  // only the JPEG payload type is handled here
    if (header->x != 0 || header->y != 0 || header->width != this->width_ || header->height != this->height_)
      continue;

    const uint8_t *payload = rx_buf + sizeof(udisp_frame_header_t);
    const size_t payload_len = read - sizeof(udisp_frame_header_t);

    Frame *frame = this->take_empty_();
    if (frame == nullptr) {
      // Nothing free: count this frame out so the next header is recognised
      // rather than being read out of the middle of a payload.
      this->frames_dropped_++;
      this->skipping_ = (header->payload_total > payload_len) ? header->payload_total - payload_len : 0;
      continue;
    }
    frame->width = header->width;
    frame->height = header->height;
    frame->total = header->payload_total;
    if (this->append_(frame, payload, payload_len)) {
      this->queue_filled_(frame);
    } else {
      this->current_ = frame;
    }
  }
}

// ===========================================================================
// Decode and draw -- its own task, never ESPHome's loop
// ===========================================================================
void USBDisplay::decode_task(void *param) { static_cast<USBDisplay *>(param)->run_decode_task(); }

void USBDisplay::run_decode_task() {
  jpeg_decode_cfg_t decode_cfg = {};
  decode_cfg.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
  decode_cfg.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_BGR;

  while (true) {
    Frame *frame = nullptr;
    if (xQueueReceive(this->filled_queue_, &frame, portMAX_DELAY) != pdTRUE)
      continue;

    uint32_t out_size = 0;
    esp_err_t err = jpeg_decoder_process(this->jpeg_, &decode_cfg, frame->data, frame->received, this->rgb_buffer_,
                                         this->rgb_buffer_len_, &out_size);
    if (err == ESP_OK) {
      uint32_t start = micros();
      // The packed overload: no offsets and no padding is what keeps mipi_dsi
      // on its single-transfer path instead of one DMA per line.
      this->display_->draw_pixels_at(0, 0, frame->width, frame->height, this->rgb_buffer_, display::COLOR_ORDER_RGB,
                                     display::COLOR_BITNESS_565, false);
      this->draw_us_ += micros() - start;
      this->frames_drawn_++;
      if (!this->logged_first_frame_) {
        this->logged_first_frame_ = true;
        ESP_LOGI(TAG, "First frame from the host: %u bytes compressed, %ux%u decoded", (unsigned) frame->received,
                 (unsigned) frame->width, (unsigned) frame->height);
      }
    } else {
      this->frames_dropped_++;
    }

    xQueueSend(this->empty_queue_, &frame, 0);

    uint32_t elapsed = millis() - this->stats_since_ms_;
    if (elapsed >= STATS_INTERVAL_MS && this->frames_drawn_ > 0) {
      ESP_LOGD(TAG, "%ux%u @ %.1f fps, %u us/draw, %u dropped", (unsigned) this->width_, (unsigned) this->height_,
               this->frames_drawn_ * 1000.0f / elapsed, (unsigned) (this->draw_us_ / this->frames_drawn_),
               (unsigned) this->frames_dropped_);
      this->stats_since_ms_ = millis();
      this->frames_drawn_ = 0;
      this->frames_dropped_ = 0;
      this->draw_us_ = 0;
    }
  }
}

void USBDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "USB Extended Display:");
  ESP_LOGCONFIG(TAG, "  Resolution: %ux%u", (unsigned) this->width_, (unsigned) this->height_);
  ESP_LOGCONFIG(TAG, "  Frame buffers: %u x %u bytes", (unsigned) this->frame_buffer_count_,
                (unsigned) this->max_frame_bytes_);
  ESP_LOGCONFIG(TAG, "  Decoded buffer: %u bytes", (unsigned) this->rgb_buffer_len_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace usb_display
}  // namespace esphome
