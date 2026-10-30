#include "lvgl_camera_display.h"
#include "esphome/core/log.h"
#include "esphome/core/application.h"
#include <cstring>
#include "esp_cache.h"
#include "esp_memory_utils.h"  // esp_ptr_external_ram()
#include "esp_heap_caps.h"
#include "driver/ppa.h"
#include "direct_geometry.h"
// Conditionally include detection components only if they exist
#ifdef USE_FACE_DETECTION
#include "esphome/components/face_detection/face_detection.h"
#endif
#ifdef USE_YOLO11_DETECTION
#include "esphome/components/yolo11_detection/yolo11_detection.h"
#endif
#ifdef USE_PEDESTRIAN_DETECTION
#include "esphome/components/pedestrian_detection/pedestrian_detection.h"
#endif

namespace esphome {
namespace lvgl_camera_display {

static const char *const TAG = "lvgl_camera_display";

void LVGLCameraDisplay::setup() {
  ESP_LOGCONFIG(TAG, "Configuration LVGL Camera Display...");
  ESP_LOGI(TAG, "Display is DISABLED by default - enable via switch in Home Assistant");

  if (this->camera_ == nullptr) {
    ESP_LOGE(TAG, "Camera non configuree");
    this->mark_failed();
    return;
  }

  // Verifier que la camera est operationnelle
  if (!this->camera_->is_pipeline_ready()) {
    ESP_LOGE(TAG, "Camera non operationnelle - pipeline non demarre");
    ESP_LOGE(TAG, "   Le composant mipi_dsi_cam a echoue a s'initialiser");
    ESP_LOGE(TAG, "   Verifiez les logs de mipi_dsi_cam pour plus de details");
    this->mark_failed();
    return;
  }

  ESP_LOGI(TAG, "LVGL Camera Display initialise (not started yet)");
  ESP_LOGI(TAG, "   Camera: Operationnelle");
  ESP_LOGI(TAG, "   Update interval: %u ms (~%d FPS) via LVGL timer",
           (unsigned) this->update_interval_, (int) (1000 / this->update_interval_));
  ESP_LOGI(TAG, "Turn on the 'LVGL Camera Display' switch to start");
}

void LVGLCameraDisplay::loop() {
  // Start timer when enabled
  if (this->enabled_ && this->lvgl_timer_ == nullptr) {
    ESP_LOGI(TAG, "Starting LVGL Camera Display...");
    this->lvgl_timer_ = lv_timer_create(lvgl_timer_callback_, this->update_interval_, this);
    if (this->lvgl_timer_ == nullptr) {
      ESP_LOGE(TAG, "Failed to create LVGL timer");
    } else {
      ESP_LOGI(TAG, "LVGL Camera Display started");
    }
  }

  // Stop timer when disabled
  if (!this->enabled_ && this->lvgl_timer_ != nullptr) {
    ESP_LOGI(TAG, "Stopping LVGL Camera Display...");
    lv_timer_del(this->lvgl_timer_);
    this->lvgl_timer_ = nullptr;
    ESP_LOGI(TAG, "LVGL Camera Display stopped");
  }
}

// Callback du timer LVGL (appele periodiquement par LVGL)
void LVGLCameraDisplay::lvgl_timer_callback_(lv_timer_t *timer) {
  LVGLCameraDisplay *display = static_cast<LVGLCameraDisplay *>(lv_timer_get_user_data(timer));
  if (display != nullptr) {
    display->update_camera_frame_();
  }
}

// Mise a jour de la frame camera (appelee par le timer LVGL)
void LVGLCameraDisplay::update_camera_frame_() {
  // Si la camera est en streaming, capturer ET mettre a jour le canvas
  if (!this->camera_->is_streaming()) {
    return;
  }

  // Temps de debut de cette frame
  uint32_t frame_start = millis();

  // Mesurer l'intervalle entre frames (temps total depuis la derniere frame)
  static uint32_t last_frame_start = 0;
  uint32_t frame_interval = 0;
  if (last_frame_start > 0) {
    frame_interval = frame_start - last_frame_start;
  }
  last_frame_start = frame_start;

  // Statistiques de frames manquees
  static uint32_t attempts = 0;
  static uint32_t skipped = 0;

  uint32_t t1 = millis();
  bool frame_captured = this->camera_->capture_frame();
  uint32_t t2 = millis();

  attempts++;
  if (!frame_captured) {
    skipped++;
    return;
  }

  this->update_canvas_();
  uint32_t t3 = millis();
  this->frame_count_++;

  // Temps de fin - mesure le temps CPU utilise pour cette frame
  uint32_t frame_end = millis();
  uint32_t frame_cpu_time = frame_end - frame_start;

  // Accumuler les temps pour statistiques
  static uint32_t last_stats_time = 0;
  static uint32_t total_capture_ms = 0;
  static uint32_t total_canvas_ms = 0;
  static uint32_t total_cpu_time_ms = 0;
  static uint32_t total_frame_interval_ms = 0;
  static uint32_t frame_interval_count = 0;

  total_capture_ms += (t2 - t1);
  total_canvas_ms += (t3 - t2);
  total_cpu_time_ms += frame_cpu_time;
  if (frame_interval > 0) {
    total_frame_interval_ms += frame_interval;
    frame_interval_count++;
  }

  // Logger performance toutes les 100 frames
  if (this->frame_count_ % 100 == 0) {
    uint32_t now_time = millis();

    if (last_stats_time > 0 && frame_interval_count > 0) {
      float elapsed = (now_time - last_stats_time) / 1000.0f;  // secondes
      float fps = 100.0f / elapsed;
      float avg_capture = total_capture_ms / 100.0f;
      float avg_canvas = total_canvas_ms / 100.0f;
      float avg_cpu_time = total_cpu_time_ms / 100.0f;
      float avg_frame_interval = total_frame_interval_ms / (float)frame_interval_count;
      float skip_rate = (skipped * 100.0f) / attempts;

      // CPU % = temps CPU utilise / temps total disponible * 100
      float cpu_percent = (avg_cpu_time / avg_frame_interval) * 100.0f;

      // Temps "perdu" = intervalle - temps CPU (temps ou LVGL fait autre chose)
      float lvgl_overhead = avg_frame_interval - avg_cpu_time;

      ESP_LOGI(TAG, "=== BENCHMARK (100 frames) ===");
      ESP_LOGI(TAG, "  FPS: %.1f | CPU: %.1f%%", fps, cpu_percent);
      ESP_LOGI(TAG, "  Frame interval: %.1fms (target: %ums)", avg_frame_interval, (unsigned) this->update_interval_);
      ESP_LOGI(TAG, "  CPU time: %.1fms (capture: %.1fms + canvas: %.1fms)", avg_cpu_time, avg_capture, avg_canvas);
      ESP_LOGI(TAG, "  LVGL overhead: %.1fms | Skip: %.1f%%", lvgl_overhead, skip_rate);
      if (this->direct_display_ != nullptr) {
        if (this->direct_frames_ > 0) {
          ESP_LOGI(TAG, "  Direct: %u of 100 frames, PPA %.1fms + display %.1fms each", (unsigned) this->direct_frames_,
                   this->direct_ppa_us_ / 1000.0f / this->direct_frames_,
                   this->direct_draw_us_ / 1000.0f / this->direct_frames_);
        } else {
          ESP_LOGI(TAG, "  Direct: no frame drawn directly (canvas not on screen, or the PPA refused - see above)");
        }
        this->direct_frames_ = 0;
        this->direct_ppa_us_ = 0;
        this->direct_draw_us_ = 0;
      }

      // Mettre a jour les stats pour l'affichage UI
      this->stats_fps_ = fps;
      this->stats_cpu_percent_ = cpu_percent;
      this->stats_frame_time_ = avg_cpu_time;
      this->stats_lvgl_overhead_ = lvgl_overhead;

      // Mettre a jour le label de stats si configure
      this->update_stats_label_();
    }
    last_stats_time = now_time;
    total_capture_ms = 0;
    total_canvas_ms = 0;
    total_cpu_time_ms = 0;
    total_frame_interval_ms = 0;
    frame_interval_count = 0;
    attempts = 0;
    skipped = 0;
  }
}

void LVGLCameraDisplay::update_stats_label_() {
  if (this->stats_label_ == nullptr) {
    return;
  }

  // Format: "FPS: 12.5 | CPU: 8.5%"
  char buf[64];
  snprintf(buf, sizeof(buf), "FPS: %.1f | CPU: %.1f%%",
           this->stats_fps_, this->stats_cpu_percent_);
  lv_label_set_text(this->stats_label_, buf);
}

void LVGLCameraDisplay::set_stats_label(lv_obj_t *label) {
  this->stats_label_ = label;
  if (label != nullptr) {
    lv_label_set_text(label, "FPS: -- | CPU: --%");
    ESP_LOGI(TAG, "Stats label configured: %p", label);
  }
}

void LVGLCameraDisplay::dump_config() {
  ESP_LOGCONFIG(TAG, "LVGL Camera Display:");
  ESP_LOGCONFIG(TAG, "  Update interval: %u ms", (unsigned) this->update_interval_);
  ESP_LOGCONFIG(TAG, "  FPS cible: ~%d", (int) (1000 / this->update_interval_));
  ESP_LOGCONFIG(TAG, "  Canvas configure: %s", this->canvas_obj_ ? "OUI" : "NON");
  if (this->direct_display_ != nullptr)
    ESP_LOGCONFIG(TAG, "  Direct mode: on, lvgl rotation %d, display %d bits", this->direct_rotation_,
                  this->direct_depth_);
}

void LVGLCameraDisplay::update_canvas_() {
  if (this->camera_ == nullptr) {
    return;
  }

  if (this->canvas_obj_ == nullptr) {
    if (!this->canvas_warning_shown_) {
      ESP_LOGW(TAG, "Canvas/Image null - pas encore configure?");
      this->canvas_warning_shown_ = true;
    }
    return;
  }

  // Liberer l'ancien buffer affiche (si present)
  if (this->displayed_buffer_ != nullptr) {
    this->camera_->release_buffer(this->displayed_buffer_);
    this->displayed_buffer_ = nullptr;
  }

  // Acquerir le nouveau buffer depuis le pool
  esp_cam_sensor::SimpleBufferElement *buffer = this->camera_->acquire_buffer();
  if (buffer == nullptr) {
    // Pas de buffer disponible - garder l'affichage precedent
    return;
  }

  uint8_t* img_data = this->camera_->get_buffer_data(buffer);
  uint16_t width = this->camera_->get_image_width();
  uint16_t height = this->camera_->get_image_height();

  if (img_data == nullptr) {
    return;
  }

  // ESP32-P4: Invalidate CPU cache before reading PSRAM buffer filled by DMA.
  // Camera DMA writes to PSRAM but CPU cache may hold stale data for this address.
  // Only PSRAM (external RAM) sits behind the data cache; internal SRAM is
  // DMA-coherent and esp_cache_msync() rejects it with
  // "esp_cache_msync(113): invalid addr or null pointer". Guard accordingly so
  // the sync is a no-op (not an error) when the frame lives in internal SRAM.
  uint32_t frame_size = width * height * 2;  // RGB565
  if (esp_ptr_external_ram(img_data)) {
    esp_cache_msync(img_data, frame_size,
                    ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_TYPE_DATA);
  }

  // Optional: draw detection results if configured. Track whether anything
  // actually wrote into the frame so we can skip the (expensive, full-frame)
  // cache write-back below when no overlay was drawn.
  bool overlay_drawn = false;
#ifdef USE_FACE_DETECTION
  if (this->face_detection_ != nullptr) {
    this->face_detection_->draw_on_frame(img_data, width, height);
    overlay_drawn = true;
  }
#endif
#ifdef USE_YOLO11_DETECTION
  if (this->yolo11_detection_ != nullptr) {
    this->yolo11_detection_->draw_on_frame(img_data, width, height);
    overlay_drawn = true;
  }
#endif
#ifdef USE_PEDESTRIAN_DETECTION
  if (this->pedestrian_detection_ != nullptr) {
    this->pedestrian_detection_->draw_on_frame(img_data, width, height);
    overlay_drawn = true;
  }
#endif

  // ESP32-P4: Flush CPU cache to PSRAM after detection drawing.
  // Without this, PPA/DMA reads stale data from PSRAM (see LVGL PR #9162).
  // Only needed when an overlay actually modified the buffer with the CPU;
  // otherwise the CPU wrote nothing and flushing the whole frame is wasted
  // work (a full-frame msync every frame).
  if (overlay_drawn && esp_ptr_external_ram(img_data)) {
    uint32_t buf_size_bytes = width * height * 2;
    esp_cache_msync(img_data, buf_size_bytes,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA);
  }

  // Detect widget type on first update
  if (this->first_update_) {
    this->is_canvas_ = lv_obj_check_type(this->canvas_obj_, &lv_canvas_class);
    ESP_LOGI(TAG, "Premier update - Widget type: %s", this->is_canvas_ ? "CANVAS" : "IMAGE");
    ESP_LOGI(TAG, "   Dimensions: %ux%u", width, height);
    ESP_LOGI(TAG, "   Buffer: %p (index=%u)", img_data, (unsigned) this->camera_->get_buffer_index(buffer));
  }

  // LVGL 9.4 ZERO-COPY MODE
  // Camera buffer stride = width * 2 (RGB565, no padding between rows)
  uint32_t stride = width * 2;
  uint32_t buf_size = width * height * 2;

  const bool direct = this->direct_display_ != nullptr && !this->direct_failed_ && this->canvas_on_screen_();
  if (!direct)
    this->direct_seeded_ = false;

  if (!this->draw_buf_initialized_) {
    // Initialize the draw buffer structure to point to camera data
    lv_draw_buf_init(&this->camera_draw_buf_, width, height,
                     LV_COLOR_FORMAT_RGB565, stride, img_data, buf_size);

    // Mark as modifiable so LVGL knows we'll update the data pointer
    lv_draw_buf_set_flag(&this->camera_draw_buf_, LV_IMAGE_FLAGS_MODIFIABLE);

    this->draw_buf_initialized_ = true;

    ESP_LOGI(TAG, "Zero-copy draw_buf initialized: %ux%u, stride=%u, size=%u, data=%p",
             width, height, (unsigned) stride, (unsigned) buf_size, img_data);
  } else {
    // Just update the data pointer - no memcpy needed!
    this->camera_draw_buf_.data = img_data;
  }

  if (direct && this->direct_seeded_) {
    // LVGL keeps pointing at the newest frame, so a redraw of something that
    // overlaps the canvas shows the right picture, but it is NOT told the
    // canvas changed: the frame goes to the display below, turned once by
    // the PPA, and LVGL neither redraws nor turns that area.
    if (this->draw_direct_(img_data, width, height)) {
      this->first_update_ = false;
      this->displayed_buffer_ = buffer;
      return;
    }
    // The PPA refused: draw_direct_ said why. This frame and every later
    // one go through LVGL as before.
  }
  if (direct)
    this->direct_seeded_ = true;

  // LVGL 9.4: Use the correct API depending on widget type
  if (this->is_canvas_) {
    // For canvas widgets: lv_canvas_set_draw_buf() properly updates
    // the canvas's internal draw_buf field AND calls lv_image_set_src()
    lv_canvas_set_draw_buf(this->canvas_obj_, &this->camera_draw_buf_);
  } else {
    // For image widgets: lv_image_set_src() is sufficient
    lv_image_set_src(this->canvas_obj_, &this->camera_draw_buf_);
  }

  // Force invalidation - LVGL 9.4 may skip redraw when same pointer is reused
  lv_obj_invalidate(this->canvas_obj_);

  this->first_update_ = false;

  // Tracker ce buffer pour le liberer au prochain update
  this->displayed_buffer_ = buffer;
}

bool LVGLCameraDisplay::canvas_on_screen_() {
  // Drawn behind LVGL's back, a frame must only go out while the canvas is
  // really there: not on another page, not hidden, not scrolled away.
  if (!lv_obj_is_visible(this->canvas_obj_))
    return false;
  lv_display_t *disp = lv_obj_get_display(this->canvas_obj_);
  return lv_obj_get_screen(this->canvas_obj_) == lv_display_get_screen_active(disp);
}

bool LVGLCameraDisplay::draw_direct_(const uint8_t *frame, uint16_t width, uint16_t height) {
  lv_display_t *disp = lv_obj_get_display(this->canvas_obj_);
  const int32_t ui_w = lv_display_get_horizontal_resolution(disp);
  const int32_t ui_h = lv_display_get_vertical_resolution(disp);
  const int rot = this->direct_rotation_;
  const bool swap = rot == 90 || rot == 270;
  const int32_t native_w = swap ? ui_h : ui_w;
  const int32_t native_h = swap ? ui_w : ui_h;
  if (this->direct_display_->get_native_width() != native_w ||
      this->direct_display_->get_native_height() != native_h) {
    ESP_LOGE(TAG, "Direct mode off: LVGL is %dx%d at rotation %d, so the panel should be %dx%d, and the display is "
                  "%dx%d. Is a rotation set on the display: block as well as on lvgl:?",
             (int) ui_w, (int) ui_h, rot, (int) native_w, (int) native_h, this->direct_display_->get_native_width(),
             this->direct_display_->get_native_height());
    this->direct_failed_ = true;
    return false;
  }

  lv_area_t at;
  lv_obj_get_coords(this->canvas_obj_, &at);
  const DirectPlan p = plan_direct(rot, ui_w, ui_h, at.x1, at.y1, at.x2, at.y2, width, height);
  if (!p.visible)
    return true;

  const display::ColorBitness bitness =
      this->direct_depth_ == 24 ? display::COLOR_BITNESS_888 : display::COLOR_BITNESS_565;
  const uint32_t frame_stride = (uint32_t) width * 2;

  // Nothing to turn, convert or cut sideways: the rows go straight out.
  if (rot == 0 && this->direct_depth_ == 16 && p.src_x == 0 && p.w == width) {
    const uint32_t t0 = micros();
    this->direct_display_->draw_pixels_at(p.px, p.py, p.pw, p.ph, frame + (size_t) p.src_y * frame_stride,
                                          display::COLOR_ORDER_RGB, bitness, false);
    this->direct_draw_us_ += micros() - t0;
    this->direct_frames_++;
    return true;
  }

  if (this->ppa_client_ == nullptr) {
    ppa_client_config_t cfg = {};
    cfg.oper_type = PPA_OPERATION_SRM;
    cfg.max_pending_trans_num = 1;
    ppa_client_handle_t client = nullptr;
    esp_err_t err = ppa_register_client(&cfg, &client);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "Direct mode off: no PPA client (%s); frames go through LVGL", esp_err_to_name(err));
      this->direct_failed_ = true;
      return false;
    }
    this->ppa_client_ = client;
  }

  // The PPA writes whole cache lines: its output buffer and that buffer's
  // size must both be aligned to one. 128 covers both line sizes a P4 can be
  // built with.
  constexpr size_t LINE = 128;
  const size_t bpp = this->direct_depth_ == 24 ? 3 : 2;
  const size_t need = ((size_t) p.pw * p.ph * bpp + LINE - 1) & ~(LINE - 1);
  if (need > this->direct_buf_len_) {
    heap_caps_free(this->direct_buf_);
    this->direct_buf_ = (uint8_t *) heap_caps_aligned_alloc(LINE, need, MALLOC_CAP_SPIRAM);
    this->direct_buf_len_ = this->direct_buf_ != nullptr ? need : 0;
    if (this->direct_buf_ == nullptr) {
      ESP_LOGE(TAG, "Direct mode off: no %u bytes of PSRAM for the turned frame", (unsigned) need);
      this->direct_failed_ = true;
      return false;
    }
    ESP_LOGI(TAG, "Direct mode: %dx%d of the frame from %d,%d, turned %d and drawn at %d,%d (%dx%d) on the panel",
             (int) p.w, (int) p.h, (int) p.src_x, (int) p.src_y, rot, (int) p.px, (int) p.py, (int) p.pw,
             (int) p.ph);
  }

  ppa_srm_oper_config_t srm = {};
  srm.in.buffer = frame;
  srm.in.pic_w = width;
  srm.in.pic_h = height;
  srm.in.block_w = p.w;
  srm.in.block_h = p.h;
  srm.in.block_offset_x = p.src_x;
  srm.in.block_offset_y = p.src_y;
  srm.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
  srm.out.buffer = this->direct_buf_;
  srm.out.buffer_size = this->direct_buf_len_;
  srm.out.pic_w = p.pw;
  srm.out.pic_h = p.ph;
  srm.out.block_offset_x = 0;
  srm.out.block_offset_y = 0;
  srm.out.srm_cm = this->direct_depth_ == 24 ? PPA_SRM_COLOR_MODE_RGB888 : PPA_SRM_COLOR_MODE_RGB565;
  switch (ppa_degrees(rot)) {
    case 90:
      srm.rotation_angle = PPA_SRM_ROTATION_ANGLE_90;
      break;
    case 180:
      srm.rotation_angle = PPA_SRM_ROTATION_ANGLE_180;
      break;
    case 270:
      srm.rotation_angle = PPA_SRM_ROTATION_ANGLE_270;
      break;
    default:
      srm.rotation_angle = PPA_SRM_ROTATION_ANGLE_0;
      break;
  }
  srm.scale_x = 1.0f;  // zero-initialised would mean scale 0
  srm.scale_y = 1.0f;
  srm.alpha_update_mode = PPA_ALPHA_NO_CHANGE;
  srm.mode = PPA_TRANS_MODE_BLOCKING;

  const uint32_t t0 = micros();
  const esp_err_t err = ppa_do_scale_rotate_mirror((ppa_client_handle_t) this->ppa_client_, &srm);
  const uint32_t t1 = micros();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Direct mode off: the PPA refused the frame (%s); frames go through LVGL", esp_err_to_name(err));
    this->direct_failed_ = true;
    return false;
  }
  // The PPA wrote PSRAM behind the cache: drop any stale lines so nothing
  // reads the old picture back through it.
  if (esp_ptr_external_ram(this->direct_buf_))
    esp_cache_msync(this->direct_buf_, this->direct_buf_len_, ESP_CACHE_MSYNC_FLAG_DIR_M2C);
  this->direct_display_->draw_pixels_at(p.px, p.py, p.pw, p.ph, this->direct_buf_, display::COLOR_ORDER_RGB, bitness,
                                        false);
  const uint32_t t2 = micros();
  this->direct_ppa_us_ += t1 - t0;
  this->direct_draw_us_ += t2 - t1;
  this->direct_frames_++;
  return true;
}

void LVGLCameraDisplay::configure_canvas(lv_obj_t *canvas) {
  this->canvas_obj_ = canvas;
  ESP_LOGI(TAG, "Canvas configure: %p", canvas);

  if (canvas != nullptr) {
    lv_coord_t w = lv_obj_get_width(canvas);
    lv_coord_t h = lv_obj_get_height(canvas);
    ESP_LOGI(TAG, "   Taille canvas: %dx%d", (int) w, (int) h);
  }
}

}  // namespace lvgl_camera_display
}  // namespace esphome
