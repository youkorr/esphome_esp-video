#include "esp_cam_sensor_camera.h"
#include "esphome/core/hal.h"
#include "esp_err.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"

#include <string.h>
#include <vector>
#include <sys/stat.h>
#include <sys/types.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <errno.h>

// Headers C avec protection extern "C"
extern "C" {
#include "esp_cam_sensor.h"
#include "esp_cam_sensor_types.h"
#include "ov5647.h"
#include "esp_video_init.h"
#include "esp_video_device.h"
#include "esp_video_ioctl.h"
#include "esp_video_isp_ioctl.h"
#include "esp_ipa.h"
#include "esp_ipa_types.h"
#include "driver/ppa.h"  // Pixel-Processing Accelerator for hardware mirror/rotate
#include "linux/videodev2.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"  // Pour esp_timer_get_time() (profiling)
#include "esp_private/esp_cache_private.h"  // Pour esp_cache_get_alignment() (détection dynamique cache line)
}

// Décodage MJPEG matériel (ESP32-P4) pour les caméras UVC qui ne fournissent
// que du MJPEG. Optionnel : opt-in via -DUVC_ENABLE_MJPEG_DECODE pour garantir
// que la compilation par défaut (chemin YUYV) reste indépendante du driver JPEG.
#if defined(UVC_ENABLE_MJPEG_DECODE) && __has_include(<driver/jpeg_decode.h>)
extern "C" {
#include "driver/jpeg_decode.h"
}
#define UVC_HW_JPEG_DECODE 1
#else
#define UVC_HW_JPEG_DECODE 0
#endif

// Custom format configurations for all sensors
#include "ov5647_custom_formats.h"   // OV5647: VGA 640x480, 800x600, 800x640, 1024x600
#include "sc202cs_custom_formats.h"  // SC202CS: 800x600
#include "ov02c10_custom_formats.h"  // OV02C10: 1280x800, 800x600

// imlib est optionnel - désactivé pour l'instant car compilé par ESP-IDF après PlatformIO
// Pour activer : ajouter -DENABLE_IMLIB_DRAWING dans build_flags
#ifdef ENABLE_IMLIB_DRAWING
  extern "C" {
    #include "imlib.h"
  }
  #define IMLIB_AVAILABLE 1
#else
  #define IMLIB_AVAILABLE 0
#endif

namespace esphome {
namespace esp_cam_sensor {

static const char *const TAG = "esp_cam_sensor";

static constexpr uint32_t HEALTH_CHECK_INTERVAL_MS = 30000;
static constexpr size_t MAX_FRAME_SIZE = 512 * 1024;
static constexpr size_t MIN_FREE_HEAP = 100 * 1024;

static inline bool wants_jpeg_(const std::string &fmt) {
  return (fmt == "JPEG" || fmt == "MJPEG");
}

static inline bool wants_h264_(const std::string &fmt) {
  return (fmt == "H264");
}

static inline int safe_ioctl_(int fd, unsigned long req, void *arg, const char *req_name) {
  int r;
  do {
    r = ioctl(fd, req, arg);
  } while (r == -1 && errno == EINTR);
  if (r < 0) {
    ESP_LOGE(TAG, "ioctl(%s) a échoué: errno=%d (%s)", req_name, errno, strerror(errno));
  }
  return r;
}

static bool open_node_(const char *node, int *fd_out) {
  int fd = open(node, O_RDWR | O_NONBLOCK);
  if (fd < 0) {
    // Silencieux, sauf si réellement utilisé (erreur rapportée par appelant si nécessaire)
    return false;
  }
  *fd_out = fd;
  return true;
}

static void close_fd_(int &fd) {
  if (fd >= 0) {
    close(fd);
    fd = -1;
  }
}

static bool map_resolution_(const std::string &res, uint32_t &w, uint32_t &h) {
  std::string res_upper = res;
  std::transform(res_upper.begin(), res_upper.end(), res_upper.begin(), ::toupper);
  
  if (res_upper == "QVGA")   { w = 320;  h = 240;  return true; }
  if (res_upper == "VGA")    { w = 640;  h = 480;  return true; }
  if (res_upper == "480P")   { w = 640;  h = 480;  return true; }
  if (res_upper == "720P")   { w = 1280; h = 720;  return true; }
  if (res_upper == "1080P")  { w = 1920; h = 1080; return true; }

  unsigned int pw = 0, ph = 0;
  if (sscanf(res.c_str(), "%ux%u", &pw, &ph) == 2 && pw > 0 && ph > 0) {
    w = pw; h = ph; return true;
  }
  
  return false;
}

// Conversion d'un pixel YUV (BT.601, plage limitée 16-235) en RGB565.
// Coefficients en virgule fixe ×256. Utilisé pour les caméras UVC en YUYV.
static inline uint16_t yuv_to_rgb565_(int y, int u, int v) {
  int c = y - 16;
  int d = u - 128;
  int e = v - 128;
  int r = (298 * c + 409 * e + 128) >> 8;
  int g = (298 * c - 100 * d - 208 * e + 128) >> 8;
  int b = (298 * c + 516 * d + 128) >> 8;
  if (r < 0) r = 0; else if (r > 255) r = 255;
  if (g < 0) g = 0; else if (g > 255) g = 255;
  if (b < 0) b = 0; else if (b > 255) b = 255;
  return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

static uint32_t map_pixfmt_fourcc_(const std::string &fmt, const std::string &bayer_pattern = "BGGR") {
  if (fmt == "RGB565") return V4L2_PIX_FMT_RGB565;
  if (fmt == "YUYV")   return V4L2_PIX_FMT_YUYV;
  if (fmt == "UYVY")   return V4L2_PIX_FMT_UYVY;
  if (fmt == "NV12")   return V4L2_PIX_FMT_NV12;
  if (fmt == "MJPEG" || fmt == "JPEG") return V4L2_PIX_FMT_MJPEG;
  if (fmt == "RAW8") {
    // Utiliser le pattern Bayer configuré
    if (bayer_pattern == "RGGB") return V4L2_PIX_FMT_SRGGB8;
    if (bayer_pattern == "GRBG") return V4L2_PIX_FMT_SGRBG8;
    if (bayer_pattern == "GBRG") return V4L2_PIX_FMT_SGBRG8;
    if (bayer_pattern == "BGGR") return V4L2_PIX_FMT_SBGGR8;
    return V4L2_PIX_FMT_SBGGR8;  // Défaut: BGGR
  }
  return V4L2_PIX_FMT_YUYV;
}

static bool isp_apply_fmt_fps_(const std::string &res_s, const std::string &fmt_s, int fps) {
  int fd = -1;
  if (!open_node_(ESP_VIDEO_ISP1_DEVICE_NAME, &fd)) return false;

  uint32_t w = 0, h = 0;
  if (!map_resolution_(res_s, w, h)) {
    ESP_LOGW(TAG, "Résolution '%s' non reconnue, fallback 1280x720", res_s.c_str());
    w = 1280; h = 720;
  }
  const uint32_t fourcc = map_pixfmt_fourcc_(fmt_s);

  struct v4l2_format fmt;
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  fmt.fmt.pix.width = w;
  fmt.fmt.pix.height = h;
  fmt.fmt.pix.pixelformat = fourcc;
  fmt.fmt.pix.field = V4L2_FIELD_NONE;

  if (safe_ioctl_(fd, VIDIOC_S_FMT, &fmt, "VIDIOC_S_FMT") < 0) {
    close_fd_(fd);
    return false;
  }

  if (fps > 0) {
    struct v4l2_streamparm parm;
    memset(&parm, 0, sizeof(parm));
    parm.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    parm.parm.capture.timeperframe.numerator = 1;
    parm.parm.capture.timeperframe.denominator = fps;
    (void)safe_ioctl_(fd, VIDIOC_S_PARM, &parm, "VIDIOC_S_PARM");
  }

  close_fd_(fd);
  return true;
}

static bool jpeg_apply_quality_(int quality) {
  int fd = -1;
  if (!open_node_(ESP_VIDEO_JPEG_DEVICE_NAME, &fd)) return false;

#ifndef V4L2_CID_JPEG_COMPRESSION_QUALITY
#define V4L2_CID_JPEG_COMPRESSION_QUALITY (V4L2_CID_JPEG_CLASS_BASE+1)
#endif
  struct v4l2_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_JPEG_COMPRESSION_QUALITY;
  ctrl.value = quality;

  // The JPEG quality control is optional: several sensor/encoder paths (e.g.
  // OV5647) don't expose V4L2_CID_JPEG_COMPRESSION_QUALITY and return EINVAL
  // (or ENOTTY). That is harmless — the encoder just keeps its default
  // quality — so don't call safe_ioctl_() here (it would log an ESP_LOGE and
  // scare users). Do the ioctl directly and only warn on unexpected errors.
  int r;
  do {
    r = ioctl(fd, VIDIOC_S_CTRL, &ctrl);
  } while (r == -1 && errno == EINTR);
  if (r < 0) {
    if (errno == EINVAL || errno == ENOTTY) {
      ESP_LOGD(TAG, "JPEG quality control not supported by this encoder - using default");
    } else {
      ESP_LOGW(TAG, "Could not set JPEG quality: errno=%d (%s)", errno, strerror(errno));
    }
  }

  close_fd_(fd);
  return true;
}

static bool h264_apply_basic_params_(int /*fps*/) {
  int fd = -1;
  if (!open_node_(ESP_VIDEO_H264_DEVICE_NAME, &fd)) return false;
  close_fd_(fd);
  return true;
}

void MipiDSICamComponent::cleanup_pipeline_() {
  // Le pipeline est géré par le composant esp_video
  this->pipeline_started_ = false;
}

bool MipiDSICamComponent::check_pipeline_health_() {
  if (!this->pipeline_started_) {
    return false;
  }

  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  if (free_heap < MIN_FREE_HEAP) {
    ESP_LOGW(TAG, "⚠️ Mémoire faible: %u octets libres (min: %u)", 
             (unsigned)free_heap, (unsigned)MIN_FREE_HEAP);
    this->error_count_++;
    return false;
  }

  return true;
}

// ============================================================================
// PPA (Pixel-Processing Accelerator) Hardware Transform Functions
// ============================================================================

bool MipiDSICamComponent::init_ppa_() {
  // Check if user explicitly disabled PPA via YAML (use saved original value)
  if (this->ppa_user_override_ && !this->ppa_user_requested_) {
    ESP_LOGI(TAG, "⚠️ PPA explicitly DISABLED by user (ppa_enabled: false) - hardware rotation only");
    return true;
  }

  // Auto-detect: Enable PPA if crop offset, mirror, rotation, or resize is configured
  if (!this->ppa_user_override_) {
    if (!this->mirror_x_ && !this->mirror_y_ && this->rotation_ == 0 &&
        this->crop_offset_x_ == 0 && this->output_width_ == 0 && this->output_height_ == 0) {
      ESP_LOGI(TAG, "PPA not needed (no mirror/rotate/crop/resize configured)");
      this->ppa_enabled_ = false;
      return true;
    }
  }

  ppa_client_config_t ppa_config = {};
  ppa_config.oper_type = PPA_OPERATION_SRM;
  ppa_config.max_pending_trans_num = 16;

  esp_err_t ret = ppa_register_client(&ppa_config, (ppa_client_handle_t*)&this->ppa_client_handle_);
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "Failed to register PPA client: %s", esp_err_to_name(ret));
    return false;
  }

  this->ppa_enabled_ = true;

  // Log PPA configuration
  if (this->output_width_ > 0 && this->output_height_ > 0) {
    ESP_LOGI(TAG, "PPA hardware transform enabled (mirror_x=%d, mirror_y=%d, rotation=%d, crop_offset_x=%d, resize=%dx%d)",
             this->mirror_x_, this->mirror_y_, this->rotation_, this->crop_offset_x_,
             this->output_width_, this->output_height_);
  } else {
    ESP_LOGI(TAG, "PPA hardware transform enabled (mirror_x=%d, mirror_y=%d, rotation=%d, crop_offset_x=%d)",
             this->mirror_x_, this->mirror_y_, this->rotation_, this->crop_offset_x_);
  }

  return true;
}

bool MipiDSICamComponent::apply_ppa_transform_(uint8_t *src_buffer, uint8_t *dst_buffer) {
  if (!this->ppa_enabled_ || !this->ppa_client_handle_) {
    return true;  // Pas de transformation
  }

  // SIMPLIFIED PPA configuration to match M5Stack's working implementation
  // M5Stack only sets: buffer, pic_w, pic_h, scale_x, scale_y, mirror_x
  // They do NOT set block_w, block_h, block_offset_x (let PPA handle defaults)

  ppa_srm_oper_config_t srm_config = {};

  // Input dimensions (from sensor - works for ANY sensor and ANY resolution)
  // image_width_ and image_height_ are set from V4L2 format (line 1030-1031)
  int input_width = this->image_width_;
  int input_height = this->image_height_;

  // Output dimensions and scaling - GENERIC ALGORITHM for all sensors/resolutions
  // Based on ESP-GMF implementation (esp_gmf_video_ppa.c lines 225-240)
  //
  // TEST BOTH APPROACHES to fix "quadrupled image" issue:
  // Approach A: Swap dimensions for rotation (ESP-GMF style)
  // Approach B: Keep dimensions same for rotation
  //
  // Currently testing: Approach A (ESP-GMF)

  int output_width, output_height;
  float scale_x, scale_y;

  if (this->output_width_ > 0 && this->output_height_ > 0) {
    // User specified explicit output size
    output_width = this->output_width_;
    output_height = this->output_height_;

    // Calculate scale factors (ESP-GMF: swap for 90°/270°)
    if (this->rotation_ == 0 || this->rotation_ == 180) {
      scale_x = (float)output_width / (float)input_width;
      scale_y = (float)output_height / (float)input_height;
    } else {  // 90° or 270°
      scale_x = (float)output_height / (float)input_width;
      scale_y = (float)output_width / (float)input_height;
    }
  } else {
    // No explicit output - APPROACH A: Swap dimensions (ESP-GMF style)
    if (this->rotation_ == 90 || this->rotation_ == 270) {
      output_width = input_height;   // 640
      output_height = input_width;   // 480
    } else {
      output_width = input_width;
      output_height = input_height;
    }
    scale_x = 1.0f;
    scale_y = 1.0f;
  }

  // INPUT CONFIG (matching M5Stack implementation)
  srm_config.in.buffer = src_buffer;
  srm_config.in.pic_w = input_width;
  srm_config.in.pic_h = input_height;
  srm_config.in.block_w = input_width;        // Process entire width
  srm_config.in.block_h = input_height;       // Process entire height
  srm_config.in.block_offset_x = 0;           // Start at top-left
  srm_config.in.block_offset_y = 0;           // Start at top-left
  srm_config.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  // OUTPUT CONFIG (matching M5Stack implementation)
  srm_config.out.buffer = dst_buffer;
  srm_config.out.buffer_size = output_width * output_height * 2;  // RGB565 = 2 bytes/pixel
  srm_config.out.pic_w = output_width;
  srm_config.out.pic_h = output_height;
  srm_config.out.block_offset_x = 0;          // Write to top-left
  srm_config.out.block_offset_y = 0;          // Write to top-left
  srm_config.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;

  // Transformation configuration
  // CRITICAL: PPA angles are INVERTED from logical rotation (from LVGL lvgl_port_v9.c)
  //   Logical 90° → PPA_SRM_ROTATION_ANGLE_270
  //   Logical 270° → PPA_SRM_ROTATION_ANGLE_90
  srm_config.rotation_angle = PPA_SRM_ROTATION_ANGLE_0;
  if (this->rotation_ == 90) {
    srm_config.rotation_angle = PPA_SRM_ROTATION_ANGLE_270;  // INVERTED!
  } else if (this->rotation_ == 180) {
    srm_config.rotation_angle = PPA_SRM_ROTATION_ANGLE_180;
  } else if (this->rotation_ == 270) {
    srm_config.rotation_angle = PPA_SRM_ROTATION_ANGLE_90;   // INVERTED!
  }

  srm_config.scale_x = scale_x;
  srm_config.scale_y = scale_y;
  srm_config.mirror_x = this->mirror_x_;
  srm_config.mirror_y = this->mirror_y_;
  srm_config.rgb_swap = false;
  srm_config.byte_swap = false;
  srm_config.mode = PPA_TRANS_MODE_BLOCKING;

  // LOG PPA configuration only once (first frame)
  static bool ppa_config_logged = false;
  if (!ppa_config_logged) {
    ESP_LOGI(TAG, "PPA Config:");
    ESP_LOGI(TAG, "  Input:  %dx%d RGB565", input_width, input_height);
    ESP_LOGI(TAG, "  Output: %dx%d RGB565", output_width, output_height);
    ESP_LOGI(TAG, "  Scale:  x=%.3f y=%.3f", scale_x, scale_y);
    ESP_LOGI(TAG, "  Mirror: x=%d y=%d", this->mirror_x_, this->mirror_y_);
    ESP_LOGI(TAG, "  Rotate: %d°", this->rotation_);
    ppa_config_logged = true;
  }

  // Execute PPA transformation
  esp_err_t ret = ppa_do_scale_rotate_mirror(
      (ppa_client_handle_t)this->ppa_client_handle_,
      &srm_config
  );

  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "❌ PPA transform failed: %s", esp_err_to_name(ret));
    return false;
  }

  return true;
}

void MipiDSICamComponent::cleanup_ppa_() {
  if (this->ppa_client_handle_) {
    ppa_unregister_client((ppa_client_handle_t)this->ppa_client_handle_);
    this->ppa_client_handle_ = nullptr;
    this->ppa_enabled_ = false;
    ESP_LOGI(TAG, "PPA hardware transform cleanup");
  }
}

// ============================================================================

void MipiDSICamComponent::setup() {
  // Initialiser le spinlock pour le buffer pool (affectation directe de la macro)
  this->buffer_mutex_ = portMUX_INITIALIZER_UNLOCKED;

  // Vérifier mémoire disponible
  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  if (free_heap < MIN_FREE_HEAP * 2) {
    ESP_LOGW(TAG, "⚠️ Low memory: %u bytes (min: %u)", (unsigned)free_heap, (unsigned)(MIN_FREE_HEAP * 2));
  }

  // Vérifier que les devices nécessaires sont disponibles
  bool isp_available = false;
  bool jpeg_available = false;
  bool h264_available = false;

  // Tester si l'ISP est disponible
  int test_fd = -1;
  if (open_node_(ESP_VIDEO_ISP1_DEVICE_NAME, &test_fd)) {
    isp_available = true;
    close_fd_(test_fd);
  }

  // Tester si JPEG est disponible
  test_fd = -1;
  if (open_node_(ESP_VIDEO_JPEG_DEVICE_NAME, &test_fd)) {
    jpeg_available = true;
    close_fd_(test_fd);
  }

  // Tester si H264 est disponible
  test_fd = -1;
  if (open_node_(ESP_VIDEO_H264_DEVICE_NAME, &test_fd)) {
    h264_available = true;
    close_fd_(test_fd);
  }

  // Vérifier qu'au moins un device est disponible
  if (!isp_available && !jpeg_available && !h264_available) {
    ESP_LOGE(TAG, "ERROR: No video devices available");
    ESP_LOGE(TAG, "  Required: ISP(%s), JPEG(%s), or H264(%s)",
             ESP_VIDEO_ISP1_DEVICE_NAME, ESP_VIDEO_JPEG_DEVICE_NAME, ESP_VIDEO_H264_DEVICE_NAME);
    ESP_LOGE(TAG, "  Enable in esp_video: enable_isp/enable_jpeg/enable_h264: true");
    this->pipeline_started_ = false;
    this->mark_failed();
    return;
  }

  // Configurer l'encodeur JPEG si nécessaire
  if (wants_jpeg_(this->pixel_format_)) {
    if (!jpeg_available) {
      ESP_LOGE(TAG, "ERROR: JPEG format requested but JPEG encoder not available (enable_jpeg: true)");
      this->pipeline_started_ = false;
      this->mark_failed();
      return;
    }
    if (!jpeg_apply_quality_(this->jpeg_quality_)) {
      ESP_LOGW(TAG, "WARNING: JPEG quality not applied");
    }
  }

  // Configurer l'encodeur H264 si nécessaire
  if (wants_h264_(this->pixel_format_)) {
    if (!h264_available) {
      ESP_LOGE(TAG, "ERROR: H264 format requested but H264 encoder not available (enable_h264: true)");
      this->pipeline_started_ = false;
      this->mark_failed();
      return;
    }
    (void)h264_apply_basic_params_(this->framerate_);
  }

  this->pipeline_started_ = true;
  this->last_health_check_ = millis();

  // Initialiser PPA (Pixel-Processing Accelerator) si mirror/rotate configurés
  if (!this->init_ppa_()) {
    ESP_LOGW(TAG, "PPA initialization failed, mirror/rotate will not be available");
  }

  // Messages simples de succès
  ESP_LOGI(TAG, "esp-cam-sensor: ok (%s)", this->sensor_name_.c_str());
  if (isp_available) ESP_LOGI(TAG, "esp-video-isp: ok");
  if (jpeg_available) ESP_LOGI(TAG, "jpeg-encoder: ok");
  if (h264_available) ESP_LOGI(TAG, "h264-encoder: ok");
  ESP_LOGI(TAG, "Camera ready: %s @ %s (%d fps)",
           this->pixel_format_.c_str(), this->resolution_.c_str(), this->framerate_);
}

void MipiDSICamComponent::loop() {
  if (!this->pipeline_started_) {
    return;
  }

  uint32_t now = millis();
  
  if (now - this->last_health_check_ >= HEALTH_CHECK_INTERVAL_MS) {
    this->last_health_check_ = now;
    
    if (!this->check_pipeline_health_()) {
      ESP_LOGW(TAG, "Vérification de santé du pipeline a échoué (erreurs: %u)", 
               (unsigned)this->error_count_);
      
      if (this->error_count_ > 5) {
        ESP_LOGE(TAG, "Trop d'erreurs détectées, nettoyage du pipeline...");
        this->cleanup_pipeline_();
        this->mark_failed();
      }
    } else {
      if (this->error_count_ > 0) {
        this->error_count_--;
      }
    }
  }
}

void MipiDSICamComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "MIPI DSI Camera:");
  ESP_LOGCONFIG(TAG, "  Capteur: %s", this->sensor_name_.c_str());
  ESP_LOGCONFIG(TAG, "  Résolution: %s", this->resolution_.c_str());
  ESP_LOGCONFIG(TAG, "  Format: %s", this->pixel_format_.c_str());
  ESP_LOGCONFIG(TAG, "  FPS: %d", this->framerate_);
  ESP_LOGCONFIG(TAG, "  État: %s", this->pipeline_started_ ? "ACTIF" : "INACTIF");
  ESP_LOGCONFIG(TAG, "  Snapshots: %u", (unsigned)this->snapshot_count_);
}

bool MipiDSICamComponent::capture_snapshot_to_file(const std::string &path) {
  if (!this->pipeline_started_) {
    ESP_LOGE(TAG, "Pipeline non démarré, impossible de capturer");
    return false;
  }

  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  if (free_heap < MIN_FREE_HEAP + MAX_FRAME_SIZE) {
    ESP_LOGE(TAG, "Mémoire insuffisante pour capturer (%u octets libres)", (unsigned)free_heap);
    this->error_count_++;
    return false;
  }

  // Choisir le device de capture selon le format
  // IMPORTANT: Pour RGB565/YUYV/formats bruts, capturer depuis /dev/video0 (CSI)
  // L'ISP /dev/video20 est utilisé AUTOMATIQUEMENT dans le pipeline interne
  // Seulement JPEG/H264 utilisent leurs encodeurs dédiés
  const char *dev = wants_jpeg_(this->pixel_format_) ?
                    ESP_VIDEO_JPEG_DEVICE_NAME :       // /dev/video10 pour JPEG
                    wants_h264_(this->pixel_format_) ?
                    ESP_VIDEO_H264_DEVICE_NAME :       // /dev/video11 pour H264
                    ESP_VIDEO_MIPI_CSI_DEVICE_NAME;    // /dev/video0 pour RGB565/YUYV/etc

  ESP_LOGI(TAG, "📸 Capture V4L2 streaming: %s → %s", dev, path.c_str());

  // 1. Ouvrir le device
  int fd = open(dev, O_RDWR | O_NONBLOCK);
  if (fd < 0) {
    ESP_LOGE(TAG, "open(%s) a échoué: errno=%d (%s)", dev, errno, strerror(errno));
    this->error_count_++;
    return false;
  }

  // 2. Vérifier le format actuel
  struct v4l2_format fmt;
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;

  if (ioctl(fd, VIDIOC_G_FMT, &fmt) < 0) {
    ESP_LOGE(TAG, "VIDIOC_G_FMT a échoué: %s", strerror(errno));
    close(fd);
    this->error_count_++;
    return false;
  }

  ESP_LOGI(TAG, "Format actuel: %ux%u, fourcc=0x%08X, sizeimage=%u",
           (unsigned) fmt.fmt.pix.width, (unsigned) fmt.fmt.pix.height,
           (unsigned) fmt.fmt.pix.pixelformat, (unsigned) fmt.fmt.pix.sizeimage);

  // 3. Demander 2 buffers en mode MMAP
  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = 2;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;

  if (ioctl(fd, VIDIOC_REQBUFS, &req) < 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS a échoué: %s", strerror(errno));
    close(fd);
    this->error_count_++;
    return false;
  }

  ESP_LOGI(TAG, "%u buffers alloués", (unsigned) req.count);

  // 4. Mapper et queuer les buffers
  struct {
    void *start;
    size_t length;
  } buffers[2];

  for (unsigned int i = 0; i < req.count; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;

    // Obtenir les infos du buffer
    if (ioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QUERYBUF[%u] a échoué: %s", i, strerror(errno));
      close(fd);
      this->error_count_++;
      return false;
    }

    // Mapper le buffer en mémoire
    buffers[i].length = buf.length;
    buffers[i].start = mmap(NULL, buf.length, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fd, buf.m.offset);

    if (buffers[i].start == MAP_FAILED) {
      ESP_LOGE(TAG, "mmap[%u] a échoué: %s", i, strerror(errno));
      // Nettoyer les buffers déjà mappés
      for (unsigned int j = 0; j < i; j++) {
        munmap(buffers[j].start, buffers[j].length);
      }
      close(fd);
      this->error_count_++;
      return false;
    }

    ESP_LOGI(TAG, "Buffer[%u] mappé: %u octets @ %p", i, (unsigned) buf.length, buffers[i].start);

    // Mettre le buffer dans la queue
    if (ioctl(fd, VIDIOC_QBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QBUF[%u] a échoué: %s", i, strerror(errno));
      // Nettoyer tous les buffers mappés
      for (unsigned int j = 0; j <= i; j++) {
        munmap(buffers[j].start, buffers[j].length);
      }
      close(fd);
      this->error_count_++;
      return false;
    }
  }

  ESP_LOGI(TAG, "Tous les buffers sont dans la queue");

  // 5. DÉMARRER LE STREAMING ★★★
  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(fd, VIDIOC_STREAMON, &type) < 0) {
    ESP_LOGE(TAG, "VIDIOC_STREAMON a échoué: %s", strerror(errno));
    // Nettoyer tous les buffers
    for (unsigned int i = 0; i < req.count; i++) {
      munmap(buffers[i].start, buffers[i].length);
    }
    close(fd);
    this->error_count_++;
    return false;
  }

  ESP_LOGI(TAG, "STREAMING DÉMARRÉ - Le sensor stream maintenant !");
  ESP_LOGI(TAG, "   → CSI controller actif");
  ESP_LOGI(TAG, "   → ISP actif");
  

  // 6. Attendre et récupérer une frame
  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_MMAP;

  ESP_LOGI(TAG, "Attente d'une frame...");

  if (ioctl(fd, VIDIOC_DQBUF, &buf) < 0) {
    ESP_LOGE(TAG, "VIDIOC_DQBUF a échoué: %s", strerror(errno));
    ioctl(fd, VIDIOC_STREAMOFF, &type);
    for (unsigned int i = 0; i < req.count; i++) {
      munmap(buffers[i].start, buffers[i].length);
    }
    close(fd);
    this->error_count_++;
    return false;
  }

  ESP_LOGI(TAG, "Frame capturée: %u octets (buffer index=%u, sequence=%u)",
           (unsigned) buf.bytesused, (unsigned) buf.index, (unsigned) buf.sequence);

  // 7. Créer le répertoire si nécessaire
  std::string dir = path.substr(0, path.find_last_of('/'));
  if (!dir.empty()) {
    struct stat st;
    if (stat(dir.c_str(), &st) != 0) {
      mkdir(dir.c_str(), 0755);
    }
  }

  // 8. Sauvegarder la frame
  FILE *f = fopen(path.c_str(), "wb");
  if (!f) {
    ESP_LOGE(TAG, "fopen(%s) pour écriture a échoué: %s", path.c_str(), strerror(errno));
    ioctl(fd, VIDIOC_STREAMOFF, &type);
    for (unsigned int i = 0; i < req.count; i++) {
      munmap(buffers[i].start, buffers[i].length);
    }
    close(fd);
    this->error_count_++;
    return false;
  }

  size_t written = fwrite(buffers[buf.index].start, 1, buf.bytesused, f);
  fclose(f);

  if (written != buf.bytesused) {
    ESP_LOGW(TAG, "Écriture incomplète (%u / %u octets)",
             (unsigned)written, (unsigned) buf.bytesused);
  }

  // 9. Arrêter le streaming
  if (ioctl(fd, VIDIOC_STREAMOFF, &type) < 0) {
    ESP_LOGW(TAG, "VIDIOC_STREAMOFF a échoué: %s", strerror(errno));
  } else {
    ESP_LOGI(TAG, "Streaming arrêté");
  }

  // 10. Libérer les buffers mappés
  for (unsigned int i = 0; i < req.count; i++) {
    munmap(buffers[i].start, buffers[i].length);
  }

  close(fd);

  this->snapshot_count_++;
  ESP_LOGI(TAG, "Snapshot #%u enregistré: %s (%u octets)",
           (unsigned)this->snapshot_count_, path.c_str(), (unsigned)written);

  return (written == buf.bytesused);
}

// ============================================================================
// Streaming Vidéo Continu pour LVGL Display
// ============================================================================

bool MipiDSICamComponent::start_streaming() {
  if (this->streaming_active_) {
    ESP_LOGW(TAG, "Streaming déjà actif");
    return true;
  }

  // Caméra USB externe (UVC) : chemin de capture dédié (MMAP + conversion RGB565).
  // Indépendant du pipeline MIPI-CSI/ISP (une carte UVC-only n'a pas de capteur MIPI).
  if (this->is_uvc_source_()) {
    return this->start_streaming_uvc_();
  }

  if (!this->pipeline_started_) {
    ESP_LOGE(TAG, "Pipeline non démarré, impossible de streamer");
    return false;
  }

  // ESP_LOGI(TAG, "=== START STREAMING (Zero-Copy RGB565) ===");

  // Solution finale: Zero-copy pour 30+ FPS garanti
  // Utiliser les buffers V4L2 MMAP directement, sans copie PPA
  const char *dev = ESP_VIDEO_MIPI_CSI_DEVICE_NAME;  // /dev/video0

  // ESP_LOGI(TAG, "Device: %s (RGB565 zero-copy mode)", dev);
  // ESP_LOGW(TAG, "Zero-copy mode: léger risque de tearing (généralement imperceptible)");

  // 1. Ouvrir le device
  this->video_fd_ = open(dev, O_RDWR | O_NONBLOCK);
  if (this->video_fd_ < 0) {
    ESP_LOGE(TAG, "open(%s) failed: %s", dev, strerror(errno));
    return false;
  }

  // 2. Configurer le format RGB565
  uint32_t width, height;
  if (!map_resolution_(this->resolution_, width, height)) {
    ESP_LOGE(TAG, "Invalid resolution: %s", this->resolution_.c_str());
    close(this->video_fd_);
    this->video_fd_ = -1;
    return false;
  }

  // ============================================================================
  // Custom Format Support (OV5647, SC202CS) - Native resolutions via VIDIOC_S_SENSOR_FMT
  // ============================================================================
  bool custom_format_applied = false;

  // OV5647: switch the sensor to a matching native format via VIDIOC_S_SENSOR_FMT.
  // Native formats are owned by the driver (ov5647.c) and exposed through
  // ov5647_get_format_info(). Supported sizes: 800x640, 800x800, 800x1280 (RAW8 50fps),
  // 1280x960 (RAW10 45fps), 1920x1080 (RAW10 30fps). The ISP then converts RAW->RGB565.
  if (this->sensor_name_ == "ov5647") {
    size_t format_count = 0;
    const esp_cam_sensor_format_t *natives = ov5647_get_format_info(&format_count);
    const esp_cam_sensor_format_t *match = nullptr;
    for (size_t i = 0; i < format_count; i++) {
      if (natives[i].width == (uint16_t)width && natives[i].height == (uint16_t)height) {
        match = &natives[i];
        ESP_LOGI(TAG, "Using NATIVE OV5647 format[%u]: %s (%ufps)",
                 (unsigned)i, natives[i].name, natives[i].fps);
        break;
      }
    }
    if (match != nullptr) {
      if (ioctl(this->video_fd_, VIDIOC_S_SENSOR_FMT, match) != 0) {
        ESP_LOGE(TAG, "VIDIOC_S_SENSOR_FMT failed: %s", strerror(errno));
        ESP_LOGE(TAG, "Sensor stays on its default format, downstream ioctls will likely fail");
      } else {
        custom_format_applied = true;
        // ISP demosaic/bayer blocks were configured at boot for the sensor's
        // default format (Format[0] = RAW8). When switching to a different
        // bit depth or timing (e.g. RAW10 1280x960), the pipeline must be
        // torn down and re-initialised, otherwise colour is wrong (grayscale).
        esp_err_t reinit_ret = esp_video_reconfigure_isp_pipeline("OV5647");
        if (reinit_ret != ESP_OK) {
          ESP_LOGW(TAG, "ISP pipeline re-init failed: %s - colours may be off",
                   esp_err_to_name(reinit_ret));
        } else {
          // The ISP re-init opens/closes its own internal fd on /dev/video0,
          // which resets the V4L2 device's cur_format back to Format[0]
          // (800x1280). Our user fd then sees the stale state and the next
          // VIDIOC_S_FMT is rejected by csi_video as "width or height is
          // invalid". Re-apply VIDIOC_S_SENSOR_FMT on our fd to re-sync the
          // device with the sensor's actual format.
          if (ioctl(this->video_fd_, VIDIOC_S_SENSOR_FMT, match) != 0) {
            ESP_LOGW(TAG, "Re-apply VIDIOC_S_SENSOR_FMT after ISP re-init failed: %s",
                     strerror(errno));
          }
        }
      }
    } else {
      ESP_LOGE(TAG, "No native OV5647 format matches %ux%u. Supported sizes:", (unsigned) width, (unsigned) height);
      for (size_t i = 0; i < format_count; i++) {
        ESP_LOGE(TAG, "  - %ux%u @ %ufps (%s)",
                 natives[i].width, natives[i].height, natives[i].fps, natives[i].name);
      }
    }
  }
  // ============================================================================

  // ============================================================================
  // Custom Format Support (SC202CS @ 800x600)
  // ============================================================================
  if (this->sensor_name_ == "sc202cs") {
    const esp_cam_sensor_format_t *custom_format = nullptr;

    // SC202CS has native 800x600 format, but driver defaults to 720P (index 1)
    // We need to explicitly apply 800x600 format (index 0) via VIDIOC_S_SENSOR_FMT
    if (width == 800 && height == 600) {
      custom_format = &sc202cs_custom_format_800x600;
      ESP_LOGI(TAG, "Using SC202CS NATIVE format: 800x600 RAW8 @ 30fps");
    }

    // Apply custom format via VIDIOC_S_SENSOR_FMT
    if (custom_format != nullptr) {
      if (ioctl(this->video_fd_, VIDIOC_S_SENSOR_FMT, custom_format) != 0) {
        ESP_LOGE(TAG, "VIDIOC_S_SENSOR_FMT failed for SC202CS: %s", strerror(errno));
        ESP_LOGE(TAG, "   Falling back to driver default (likely 720P)");
      } else {
        ESP_LOGI(TAG, "SC202CS 800x600 format applied successfully!");
        ESP_LOGI(TAG, "   Sensor registers configured for 800x600 centered crop");
      }
    }
  }

    // ============================================================================
  // Custom Format Support (OV02C10 @ 640x480, 800x600, 640x368, 480x640, or 1920x1080)
  // ============================================================================
  if (this->sensor_name_ == "ov02c10") {
    const esp_cam_sensor_format_t *custom_format = nullptr;

    // Sélectionner le format custom selon la résolution
    // Note: 1288x728 is the native resolution (no custom format needed)
    if (width == 480 && height == 640) {
      // YAML: "480x640" → Portrait capture, LVGL handles rotation
      custom_format = &ov02c10_format_480x640_raw10_30fps_rot270;
      ESP_LOGI(TAG, "Using PORTRAIT format: 480x640 RAW10 @ 30fps (no sensor rotation, LVGL will rotate)");
    } else if (width == 640 && height == 480) {
      custom_format = &ov02c10_format_640x480_raw10_30fps;
      ESP_LOGW(TAG, "Using 640x480 RAW10 (VGA 4:3) - WARNING: 25%% horizontal crop (zoom 1.33x, only 75%% FOV visible)");
      ESP_LOGW(TAG, "Consider using 640x368 for 98%% FOV coverage instead - See OV02C10_640x480_ZOOM_FIX.md");
    } else if (width == 800 && height == 600) {
      custom_format = &ov02c10_format_800x600_raw10_30fps;
      ESP_LOGI(TAG, "Using CUSTOM format: 800x600 RAW10 @ 30fps (SVGA 4:3, based on working 640x480)");
    } else if (width == 960 && height == 540) {
      // DISABLED: 960x540 causes persistent watchdog timeout even with 1288x728 config
      ESP_LOGE(TAG, "❌ 960x540 is DISABLED - resolution incompatible with OV02C10");
      ESP_LOGE(TAG, "❌ Use 800x600 (SVGA) or 1288x728 (HD) instead");
      return false;
    } else if (width == 640 && height == 368) {
      custom_format = &ov02c10_format_640x368_raw10_30fps;
      ESP_LOGI(TAG, "Using CUSTOM format: 640x368 RAW10 @ 30fps (near 16:9, ~2%% crop, 16-byte aligned!)");
    } else if (width == 1920 && height == 1080) {
      custom_format = &ov02c10_format_1920x1080_raw10_30fps;
      ESP_LOGI(TAG, "Using NATIVE format: 1920x1080 RAW10 @ 30fps (1080P - Full Sensor)");
    } else if (width == 1288 && height == 728) {
      // 1288x728 is the driver's default format index. Historically we skipped
      // VIDIOC_S_SENSOR_FMT here ("native, no custom format needed") and relied
      // on the sensor being pre-programmed at probe time. That assumption no
      // longer holds: probe only sets cur_format (the pointer), it does not
      // write the 1288x728 register array to the sensor, so the sensor never
      // actually streams and VIDIOC_DQBUF stays EAGAIN forever (black screen).
      // Apply the format explicitly, exactly like every other OV02C10 mode.
      // It matches the boot-time ISP pipeline format, so no ISP re-init needed.
      custom_format = &ov02c10_format_1288x728_raw10_30fps;
      ESP_LOGI(TAG, "Using NATIVE format: 1288x728 RAW10 @ 30fps (HD - default index)");
    }

    // Appliquer le format custom via VIDIOC_S_SENSOR_FMT
    if (custom_format != nullptr) {
      if (ioctl(this->video_fd_, VIDIOC_S_SENSOR_FMT, custom_format) != 0) {
        ESP_LOGE(TAG, "VIDIOC_S_SENSOR_FMT failed: %s", strerror(errno));
        ESP_LOGE(TAG, "Custom format not supported, falling back to standard format");
      } else {
        ESP_LOGI(TAG, "Custom format applied successfully!");
        ESP_LOGI(TAG, "   Sensor registers configured for native %ux%u", (unsigned) width, (unsigned) height);
        // Update width/height to match format's actual output dimensions
        // (important for rotated formats where output != input)
        width = custom_format->width;
        height = custom_format->height;
        ESP_LOGI(TAG, "   Actual output dimensions after rotation: %ux%u", (unsigned) width, (unsigned) height);
      }
    }
  }
  // ============================================================================

  // NOTE: Custom formats are now applied above for all sensors (OV02C10, OV5647, SC202CS)
  // The driver will use these sensor register configurations

  // RGB565 natif du CSI (pas de conversion, pas de copie)
  // Note: Si custom format RAW10 appliqué, ISP convertira RAW10→RGB565
  uint32_t fourcc = V4L2_PIX_FMT_RGB565;

  // Énumérer les formats supportés par le capteur (ESP-IDF 5.4.2+ peut avoir des restrictions)
  ESP_LOGI(TAG, "Checking supported formats for %s...", this->sensor_name_.c_str());
  struct v4l2_fmtdesc fmtdesc;
  bool format_supported = false;
  for (int i = 0; i < 10; i++) {
    memset(&fmtdesc, 0, sizeof(fmtdesc));
    fmtdesc.index = i;
    fmtdesc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(this->video_fd_, VIDIOC_ENUM_FMT, &fmtdesc) < 0) {
      break;  // Pas d'autres formats
    }
    char fourcc_str[5];
    fourcc_str[0] = (fmtdesc.pixelformat >> 0) & 0xFF;
    fourcc_str[1] = (fmtdesc.pixelformat >> 8) & 0xFF;
    fourcc_str[2] = (fmtdesc.pixelformat >> 16) & 0xFF;
    fourcc_str[3] = (fmtdesc.pixelformat >> 24) & 0xFF;
    fourcc_str[4] = '\0';
    ESP_LOGI(TAG, "  Format[%d]: %s (%s)", i, fmtdesc.description, fourcc_str);
    if (fmtdesc.pixelformat == fourcc) {
      format_supported = true;
    }
  }

  if (!format_supported) {
    ESP_LOGW(TAG, "RGB565 may not be supported by sensor, trying anyway...");
  }

  // Énumérer les tailles de frame supportées pour RGB565
  ESP_LOGI(TAG, "Checking supported frame sizes for RGB565...");
  struct v4l2_frmsizeenum frmsize;
  bool size_found = false;
  for (int i = 0; i < 20; i++) {
    memset(&frmsize, 0, sizeof(frmsize));
    frmsize.index = i;
    frmsize.pixel_format = fourcc;
    if (ioctl(this->video_fd_, VIDIOC_ENUM_FRAMESIZES, &frmsize) < 0) {
      break;
    }
    if (frmsize.type == V4L2_FRMSIZE_TYPE_DISCRETE) {
      ESP_LOGI(TAG, "  Size[%d]: %ux%u", i, (unsigned) frmsize.discrete.width, (unsigned) frmsize.discrete.height);
      if (frmsize.discrete.width == width && frmsize.discrete.height == height) {
        size_found = true;
      }
    }
  }

  // Si RGB565 n'a aucune résolution, vérifier RAW8 (conversion ISP possible)
  if (!size_found) {
    ESP_LOGD(TAG, "No sizes found for RGB565 - checking native RAW8 formats...");
    for (int i = 0; i < 20; i++) {
      memset(&frmsize, 0, sizeof(frmsize));
      frmsize.index = i;
      frmsize.pixel_format = V4L2_PIX_FMT_SBGGR8;  // RAW8 BGGR (Format[0] des logs)
      if (ioctl(this->video_fd_, VIDIOC_ENUM_FRAMESIZES, &frmsize) < 0) {
        break;
      }
      if (frmsize.type == V4L2_FRMSIZE_TYPE_DISCRETE) {
        ESP_LOGI(TAG, "  RAW8 Size[%d]: %ux%u", i, (unsigned) frmsize.discrete.width, (unsigned) frmsize.discrete.height);
        if (frmsize.discrete.width == width && frmsize.discrete.height == height) {
          size_found = true;
          ESP_LOGI(TAG, "Found RAW8 %ux%u - ISP will convert to RGB565", (unsigned) width, (unsigned) height);
        }
      } else if (frmsize.type == V4L2_FRMSIZE_TYPE_STEPWISE) {
        ESP_LOGI(TAG, "  RAW8 Stepwise: %ux%u to %ux%u (step %ux%u)",
                 (unsigned) frmsize.stepwise.min_width, (unsigned) frmsize.stepwise.min_height,
                 (unsigned) frmsize.stepwise.max_width, (unsigned) frmsize.stepwise.max_height,
                 (unsigned) frmsize.stepwise.step_width, (unsigned) frmsize.stepwise.step_height);
      }
    }
  }

  // Si toujours pas trouvé, vérifier RAW10 (OV02C10 custom formats)
  if (!size_found) {
    ESP_LOGD(TAG, "No sizes found for RAW8 - checking native RAW10 formats...");
    for (int i = 0; i < 20; i++) {
      memset(&frmsize, 0, sizeof(frmsize));
      frmsize.index = i;
      frmsize.pixel_format = V4L2_PIX_FMT_SBGGR10;  // RAW10 BGGR (OV02C10 native)
      if (ioctl(this->video_fd_, VIDIOC_ENUM_FRAMESIZES, &frmsize) < 0) {
        break;
      }
      if (frmsize.type == V4L2_FRMSIZE_TYPE_DISCRETE) {
        ESP_LOGI(TAG, "  RAW10 Size[%d]: %ux%u", i, (unsigned) frmsize.discrete.width, (unsigned) frmsize.discrete.height);
        if (frmsize.discrete.width == width && frmsize.discrete.height == height) {
          size_found = true;
          ESP_LOGI(TAG, "Found RAW10 %ux%u - ISP will convert to RGB565", (unsigned) width, (unsigned) height);
        }
      } else if (frmsize.type == V4L2_FRMSIZE_TYPE_STEPWISE) {
        ESP_LOGI(TAG, "  RAW10 Stepwise: %ux%u to %ux%u (step %ux%u)",
                 (unsigned) frmsize.stepwise.min_width, (unsigned) frmsize.stepwise.min_height,
                 (unsigned) frmsize.stepwise.max_width, (unsigned) frmsize.stepwise.max_height,
                 (unsigned) frmsize.stepwise.step_width, (unsigned) frmsize.stepwise.step_height);
      }
    }
  }


  if (!size_found) {
    ESP_LOGD(TAG, "Requested size %ux%u not found in supported list", (unsigned) width, (unsigned) height);
    ESP_LOGD(TAG, "Trying to set anyway (driver may adjust)...");
  }

  struct v4l2_format fmt;
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  fmt.fmt.pix.width = width;
  fmt.fmt.pix.height = height;
  fmt.fmt.pix.pixelformat = fourcc;
  fmt.fmt.pix.field = V4L2_FIELD_NONE;

  // SET le format pour que le driver calcule sizeimage
  if (ioctl(this->video_fd_, VIDIOC_S_FMT, &fmt) < 0) {
    if (custom_format_applied) {
      ESP_LOGW(TAG, "VIDIOC_S_FMT failed but custom sensor format was applied");
      ESP_LOGW(TAG, "Trying to query the actual format from driver...");

      // Query what format the driver actually set
      memset(&fmt, 0, sizeof(fmt));
      fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
      if (ioctl(this->video_fd_, VIDIOC_G_FMT, &fmt) == 0) {
        ESP_LOGI(TAG, "Driver format: %ux%u, sizeimage=%u",
                 (unsigned) fmt.fmt.pix.width, (unsigned) fmt.fmt.pix.height, (unsigned) fmt.fmt.pix.sizeimage);
        // Use the custom format dimensions
        fmt.fmt.pix.width = width;
        fmt.fmt.pix.height = height;
        fmt.fmt.pix.sizeimage = width * height * 2; // RGB565 = 2 bytes per pixel
        ESP_LOGI(TAG, "Using custom format dimensions: %ux%u", (unsigned) width, (unsigned) height);
      } else {
        // Fallback: calculate sizeimage manually
        fmt.fmt.pix.width = width;
        fmt.fmt.pix.height = height;
        fmt.fmt.pix.sizeimage = width * height * 2;
        ESP_LOGW(TAG, "Could not query format, using calculated values");
      }
    } else {
      ESP_LOGE(TAG, "VIDIOC_S_FMT failed: %s", strerror(errno));
      ESP_LOGE(TAG, "Requested: %ux%u RGB565", (unsigned) width, (unsigned) height);
      ESP_LOGE(TAG, "This may indicate:");
      ESP_LOGE(TAG, "  1. Sensor %s doesn't support this resolution in RGB565", this->sensor_name_.c_str());
      ESP_LOGE(TAG, "  2. ESP-IDF 5.4.2+ has stricter format validation");
      ESP_LOGE(TAG, "  3. Try a different resolution (VGA/1080P) or pixel format");
      close(this->video_fd_);
      this->video_fd_ = -1;
      return false;
    }
  }

  // 3. Vérifier le format appliqué (le driver peut ajuster)
  if (ioctl(this->video_fd_, VIDIOC_G_FMT, &fmt) < 0) {
    ESP_LOGE(TAG, "VIDIOC_G_FMT failed: %s", strerror(errno));
    close(this->video_fd_);
    this->video_fd_ = -1;
    return false;
  }

  this->image_width_ = fmt.fmt.pix.width;
  this->image_height_ = fmt.fmt.pix.height;

  // Calculer la taille du buffer pour CAPTURE (RGB565 = 2 bytes/pixel)
  // NOTE: Buffers must be allocated at CAPTURE size, not PPA output size
  this->image_buffer_size_ = this->image_width_ * this->image_height_ * 2;

  // Log PPA resize if configured (but keep buffer size at capture dimensions)
  if (this->output_width_ > 0 && this->output_height_ > 0) {
    ESP_LOGI(TAG, "Format: %ux%u RGB565 → PPA resize → %ux%u, buffer size: %u bytes (%u KB)",
             this->image_width_, this->image_height_,
             this->output_width_, this->output_height_,
             this->image_buffer_size_, this->image_buffer_size_ / 1024);
  } else {
    ESP_LOGI(TAG, "Format: %ux%u RGB565, buffer size: %u bytes (%u KB)",
             this->image_width_, this->image_height_,
             this->image_buffer_size_, this->image_buffer_size_ / 1024);
  }

  // NOTE: SC202CS 800x600 is now a NATIVE format in the driver (sc202cs.c)
  // No re-application needed - timing registers are set by driver's native format

  // 3. Allouer NUM_BUFFERS buffers SPIRAM AVANT de les passer à V4L2 (mode USERPTR)
  // ★ CRITICAL: Utiliser V4L2_MEMORY_USERPTR pour éviter memcpy vers SPIRAM (comme Waveshare)
  // Détection dynamique de la cache line (64B ou 128B selon L2_CACHE_LINE_SIZE configuré)
  size_t cache_line_size = 64;
  esp_cache_get_alignment(MALLOC_CAP_SPIRAM, &cache_line_size);
  if (cache_line_size < 64) cache_line_size = 64;

  ESP_LOGI(TAG, "Allocating cache-aligned SPIRAM buffers for V4L2 USERPTR mode:");
  ESP_LOGI(TAG, "  Buffers: %d × %u bytes = %u KB total",
           NUM_BUFFERS, this->image_buffer_size_, (this->image_buffer_size_ * NUM_BUFFERS) / 1024);
  ESP_LOGI(TAG, "  Cache line size: %u bytes", cache_line_size);

  for (int i = 0; i < NUM_BUFFERS; i++) {
    this->simple_buffers_[i].data = (uint8_t*)heap_caps_aligned_alloc(
        cache_line_size,
        this->image_buffer_size_,
        MALLOC_CAP_SPIRAM);

    if (this->simple_buffers_[i].data == nullptr) {
      ESP_LOGE(TAG, "❌ Failed to allocate aligned buffer %d (size: %u bytes, align: %u)",
               i, this->image_buffer_size_, cache_line_size);
      ESP_LOGE(TAG, "   Free SPIRAM: %u bytes, Free internal: %u bytes",
               heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
               heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
      // Libérer les buffers déjà alloués
      for (int j = 0; j < i; j++) {
        heap_caps_free(this->simple_buffers_[j].data);
        this->simple_buffers_[j].data = nullptr;
      }
      close(this->video_fd_);
      this->video_fd_ = -1;
      return false;
    }
    this->simple_buffers_[i].allocated = false;
    this->simple_buffers_[i].index = i;
    ESP_LOGI(TAG, "  Buffer[%d]: %p (aligned to %u bytes)",
             i, this->simple_buffers_[i].data, cache_line_size);
  }
  this->current_buffer_index_ = -1;
  this->image_buffer_ = nullptr;

  // 4. Demander NUM_BUFFERS buffers V4L2 en mode USERPTR (au lieu de MMAP)
  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = NUM_BUFFERS;  // Double buffering (2 buffers)
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_USERPTR;  // ★ USERPTR au lieu de MMAP!

  if (ioctl(this->video_fd_, VIDIOC_REQBUFS, &req) < 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS (USERPTR mode) failed: %s", strerror(errno));
    // Libérer les buffers SPIRAM
    for (int i = 0; i < NUM_BUFFERS; i++) {
      heap_caps_free(this->simple_buffers_[i].data);
      this->simple_buffers_[i].data = nullptr;
    }
    close(this->video_fd_);
    this->video_fd_ = -1;
    return false;
  }

  ESP_LOGI(TAG, "V4L2 USERPTR mode: %u buffers requested", (unsigned) req.count);

  // 5. Queuer les buffers avec nos pointeurs SPIRAM
  for (int i = 0; i < NUM_BUFFERS; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_USERPTR;
    buf.index = i;
    buf.m.userptr = (unsigned long)this->simple_buffers_[i].data;  // ★ Notre buffer SPIRAM
    buf.length = this->image_buffer_size_;

    if (ioctl(this->video_fd_, VIDIOC_QBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QBUF[%d] (USERPTR) failed: %s", i, strerror(errno));
      // Libérer les buffers SPIRAM
      for (int j = 0; j < NUM_BUFFERS; j++) {
        heap_caps_free(this->simple_buffers_[j].data);
        this->simple_buffers_[j].data = nullptr;
      }
      close(this->video_fd_);
      this->video_fd_ = -1;
      return false;
    }
    ESP_LOGI(TAG, "  Buffer[%u] queued: userptr=%p, length=%u",
             i, (void*)buf.m.userptr, (unsigned) buf.length);
  }

  // 8. DÉMARRER LE STREAMING
  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->video_fd_, VIDIOC_STREAMON, &type) < 0) {
    ESP_LOGE(TAG, "VIDIOC_STREAMON failed: %s", strerror(errno));
    this->stop_streaming();
    return false;
  }

  this->streaming_active_ = true;
  this->frame_sequence_ = 0;

  // Réinitialiser PPA si nécessaire (au cas où stop_streaming() l'a désactivé)
  // Vérifier si rotation/mirror/crop sont configurés même si ppa_enabled_ est actuellement false
  if (!this->ppa_enabled_ && (this->mirror_x_ || this->mirror_y_ || this->rotation_ != 0 ||
                               this->crop_offset_x_ != 0 || this->output_width_ > 0 || this->output_height_ > 0)) {
    ESP_LOGI(TAG, "Reinitializing PPA (was disabled by previous stop_streaming)");
    if (!this->init_ppa_()) {
      ESP_LOGW(TAG, "PPA reinitialization failed");
    }
  }

  // Allouer buffer séparé pour PPA si mirror/rotate activés
  if (this->ppa_enabled_) {
    this->image_buffer_ = (uint8_t*)heap_caps_malloc(
        this->image_buffer_size_,
        MALLOC_CAP_DMA | MALLOC_CAP_SPIRAM
    );
    if (!this->image_buffer_) {
      ESP_LOGE(TAG, "Failed to allocate PPA image buffer (%u bytes)", this->image_buffer_size_);
      this->stop_streaming();
      return false;
    }
    ESP_LOGI(TAG, "PPA buffer allocated: %u bytes @ %p", this->image_buffer_size_, this->image_buffer_);
  }

  ESP_LOGI(TAG, "mipi_dsi_cam: streaming started");

  // ★ WORKAROUND: Wait for sensor to complete internal initialization
  // Some custom formats (especially 800x600) may not generate frames immediately
  // This prevents watchdog timeout during first frame capture
  ESP_LOGI(TAG, "Waiting 300ms for sensor initialization...");
  vTaskDelay(pdMS_TO_TICKS(300));
  ESP_LOGI(TAG, "Sensor should be ready for capture");

  this->isp_fd_ = open(ESP_VIDEO_ISP1_DEVICE_NAME, O_RDWR | O_NONBLOCK);
  if (this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Failed to open ISP device %s for V4L2 controls: %s",
             ESP_VIDEO_ISP1_DEVICE_NAME, strerror(errno));
    ESP_LOGW(TAG, "Brightness/Contrast/Saturation/AWB controls will not be available");
  } else {
    ESP_LOGI(TAG, "ISP device opened for V4L2 controls: %s", ESP_VIDEO_ISP1_DEVICE_NAME);
  }

  // Les buffers SPIRAM ont déjà été alloués et passés à V4L2 en mode USERPTR
  // V4L2 écrit maintenant directement dans nos buffers SPIRAM - pas de memcpy nécessaire!
  ESP_LOGI(TAG, "V4L2 USERPTR mode active - zero-copy to SPIRAM");

  // Auto-appliquer les gains RGB CCM si configurés dans YAML
  if (this->rgb_gains_enabled_) {
    if (this->set_rgb_gains(this->rgb_gains_red_, this->rgb_gains_green_, this->rgb_gains_blue_)) {
      // ESP_LOGI(TAG, "CCM RGB gains auto-applied: R=%.2f, G=%.2f, B=%.2f",
      //          this->rgb_gains_red_, this->rgb_gains_green_, this->rgb_gains_blue_);
    } else {
      ESP_LOGW(TAG, "Failed to auto-apply CCM RGB gains");
    }
  }

  // Auto-activer AWB (Auto White Balance) pour corriger blanc → jaune
  // IMPORTANT: AWB ne fonctionne PAS sur certains capteurs (Invalid argument)
  // OV5647, SC202CS, OV02C10 gèrent automatiquement la balance des blancs via leurs propres registres et IPA JSON
  if (this->sensor_name_ != "sc202cs" && this->sensor_name_ != "ov5647" && this->sensor_name_ != "ov02c10") {
    if (this->set_white_balance_mode(true)) {
      ESP_LOGI(TAG, "AWB (Auto White Balance) enabled");
    } else {
      ESP_LOGW(TAG, "Failed to enable AWB, trying manual white balance temperature");
      // Fallback: configurer température couleur manuelle (5500K = lumière du jour)
      this->set_white_balance_temp(5500);
    }
  } else {
    ESP_LOGI(TAG, "%s: Using sensor built-in AWB (V4L2 AWB not supported)", this->sensor_name_.c_str());
  }

  // NOTE: Brightness/Contrast/Saturation auto-application désactivée
  // Utilisez les contrôles YAML number avec initial_value pour ajuster:
  //   - Brightness: initial_value: 60 (testov5647)
  //   - Contrast: initial_value: 145 (testov5647)
  //   - Saturation: initial_value: 135 (testov5647)
  // Voir CAMERA_CONTROLS_YAML.md pour la configuration complète

  return true;
}

bool MipiDSICamComponent::capture_frame() {
  if (!this->streaming_active_) {
    return false;
  }

  // Caméra USB externe (UVC) : chemin de capture/conversion dédié
  if (this->is_uvc_source_()) {
    return this->capture_frame_uvc_();
  }

  // Feed watchdog at start of capture — ONLY if this task is subscribed to the
  // TWDT. capture_frame() is also driven by tasks that are not registered (e.g.
  // the webrtc video-TX task at 10 fps); calling reset() from those just floods
  // the log with "esp_task_wdt_reset(): task not found" every frame.
  if (esp_task_wdt_status(nullptr) == ESP_OK)
    esp_task_wdt_reset();

  static uint32_t profile_count = 0;
  static uint32_t total_dqbuf_us = 0;
  static uint32_t total_copy_us = 0;
  static uint32_t total_qbuf_us = 0;

  // 1. Dequeue un buffer rempli (USERPTR mode)
  uint32_t t1 = esp_timer_get_time();
  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_USERPTR;  // ★ USERPTR au lieu de MMAP

  if (ioctl(this->video_fd_, VIDIOC_DQBUF, &buf) < 0) {
    if (errno == EAGAIN) {
      // Pas de frame disponible (mode non-blocking)
      return false;
    }
    ESP_LOGE(TAG, "VIDIOC_DQBUF failed: %s", strerror(errno));
    return false;
  }
  uint32_t t2 = esp_timer_get_time();

  // 2. V4L2 a déjà écrit directement dans notre buffer SPIRAM!
  // Pas de memcpy nécessaire - le buffer est prêt à être utilisé
  int buffer_idx = buf.index;
  uint8_t *frame_data = this->simple_buffers_[buffer_idx].data;

  // 3. Apply PPA transformations if enabled (crop, mirror, rotate)
  uint32_t t3 = esp_timer_get_time();
  uint8_t *display_buffer = frame_data;  // By default, use captured frame directly

  if (this->ppa_enabled_ && this->image_buffer_) {
    // Transform FROM V4L2 buffer TO separate PPA buffer (no in-place transform!)
    if (this->apply_ppa_transform_(frame_data, this->image_buffer_)) {
      display_buffer = this->image_buffer_;  // Use PPA output for display
      // Log only once (first frame)
      static bool ppa_buffer_logged = false;
      if (!ppa_buffer_logged) {
        ESP_LOGI(TAG, "Using PPA output buffer at %p", display_buffer);
        ppa_buffer_logged = true;
      }
    } else {
      ESP_LOGE(TAG, "PPA transform failed, using original buffer");
    }
  }
  uint32_t t4 = esp_timer_get_time();

  // 4. Mettre à jour current_buffer_index_ (pour acquire_buffer)
  portENTER_CRITICAL(&this->buffer_mutex_);
  // Marquer l'ancien buffer comme disponible pour V4L2 si c'est un buffer différent
  // SAUF si PPA est actif (dans ce cas le V4L2 buffer reste en lecture seule)
  if (this->current_buffer_index_ >= 0 && this->current_buffer_index_ != buffer_idx) {
    if (!this->ppa_enabled_) {
      this->simple_buffers_[this->current_buffer_index_].allocated = false;
    }
  }
  // Marquer le nouveau buffer comme actuellement utilisé
  this->simple_buffers_[buffer_idx].allocated = true;
  this->current_buffer_index_ = buffer_idx;

  // CRITICAL: When PPA is enabled, update the buffer element to point to PPA output!
  // This ensures acquire_buffer() returns the transformed image, not the raw sensor data
  if (this->ppa_enabled_ && this->image_buffer_) {
    // Temporarily override the V4L2 buffer pointer with PPA output
    this->simple_buffers_[buffer_idx].data = this->image_buffer_;
  }

  // Legacy API pointer (not used when SimpleBufferElement is used)
  if (!this->ppa_enabled_) {
    this->image_buffer_ = display_buffer;
  }
  portEXIT_CRITICAL(&this->buffer_mutex_);

  this->frame_sequence_++;

  // Log uniquement la première frame
  if (this->frame_sequence_ == 1) {
    ESP_LOGI(TAG, "First frame captured (V4L2 USERPTR - zero-copy to SPIRAM):");
    ESP_LOGI(TAG, "   Buffer size: %u bytes (%ux%u × 2 = RGB565)",
             this->image_buffer_size_, this->image_width_, this->image_height_);
    ESP_LOGI(TAG, "   SPIRAM buffer: %p (index=%d)", frame_data, buffer_idx);
    ESP_LOGI(TAG, "   Timing: DQBUF=%uus, PPA=%uus",
             (unsigned)(t2-t1), (unsigned)(t4-t3));
    ESP_LOGI(TAG, "   First pixels (RGB565): %02X%02X %02X%02X %02X%02X",
             frame_data[0], frame_data[1],
             frame_data[2], frame_data[3],
             frame_data[4], frame_data[5]);
  }

  // Profiling détaillé toutes les 100 frames
  profile_count++;
  total_dqbuf_us += (t2 - t1);
  total_copy_us += (t4 - t3);  // PPA transformation time (no memcpy!)

  // 5. Re-queue le buffer pour V4L2 (V4L2 réutilisera notre buffer SPIRAM)
  uint32_t t5 = esp_timer_get_time();

  // IMPORTANT: Restore original V4L2 buffer pointer before queuing back
  // We may have overridden it with PPA buffer for display purposes
  if (this->ppa_enabled_ && this->simple_buffers_[buffer_idx].data != frame_data) {
    portENTER_CRITICAL(&this->buffer_mutex_);
    this->simple_buffers_[buffer_idx].data = frame_data;  // Restore V4L2 pointer
    portEXIT_CRITICAL(&this->buffer_mutex_);
  }

  buf.m.userptr = (unsigned long)frame_data;  // Repasser le pointeur SPIRAM
  buf.length = this->image_buffer_size_;
  if (ioctl(this->video_fd_, VIDIOC_QBUF, &buf) < 0) {
    ESP_LOGE(TAG, "VIDIOC_QBUF failed: %s", strerror(errno));
    return false;
  }
  uint32_t t6 = esp_timer_get_time();

  total_qbuf_us += (t6 - t5);

  if (profile_count == 100) {


    profile_count = 0;
    total_dqbuf_us = 0;
    total_copy_us = 0;
    total_qbuf_us = 0;
  }

  return true;
}

void MipiDSICamComponent::stop_streaming() {
  if (!this->streaming_active_) {
    return;
  }

  // ESP_LOGI(TAG, "=== STOP STREAMING ===");

  // 1. Arrêter le streaming V4L2
  if (this->video_fd_ >= 0) {
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(this->video_fd_, VIDIOC_STREAMOFF, &type) < 0) {
      ESP_LOGW(TAG, "VIDIOC_STREAMOFF failed: %s", strerror(errno));
    }
  }

  // 1b. UVC : libérer les buffers MMAP fournis par le driver UVC (le chemin
  // commun ci-dessous libère les buffers RGB565 de sortie, alloués en SPIRAM).
  if (this->is_uvc_source_()) {
    this->stop_streaming_uvc_();
  }

  // 2. Libérer les buffers SPIRAM (USERPTR mode - pas de munmap nécessaire)
  portENTER_CRITICAL(&this->buffer_mutex_);
  this->current_buffer_index_ = -1;
  portEXIT_CRITICAL(&this->buffer_mutex_);

  for (int i = 0; i < NUM_BUFFERS; i++) {
    if (this->simple_buffers_[i].data != nullptr) {
      heap_caps_free(this->simple_buffers_[i].data);
      this->simple_buffers_[i].data = nullptr;
      this->simple_buffers_[i].allocated = false;
    }
  }

  // Reset legacy pointer
  this->image_buffer_ = nullptr;

  // 4. Cleanup PPA si activé
  if (this->ppa_enabled_) {
    this->cleanup_ppa_();
  }

  // 5. Libérer la structure imlib si allouée (seulement si imlib activé)
#if IMLIB_AVAILABLE
  if (this->imlib_image_) {
    free(this->imlib_image_);
    this->imlib_image_ = nullptr;
    this->imlib_image_valid_ = false;
  }
#endif

  // 6. Fermer le device CSI
  if (this->video_fd_ >= 0) {
    close(this->video_fd_);
    this->video_fd_ = -1;
  }

  // 7. Fermer le device ISP (contrôles V4L2)
  if (this->isp_fd_ >= 0) {
    close(this->isp_fd_);
    this->isp_fd_ = -1;
  }

  this->streaming_active_ = false;
  this->image_width_ = 0;
  this->image_height_ = 0;
  this->image_buffer_size_ = 0;

  // ESP_LOGI(TAG, "Streaming stopped, resources freed");
}

// ============================================================================
// UVC (caméra USB externe) — chemin de capture dédié
//
// Le driver UVC d'Espressif énumère la caméra USB comme un nœud V4L2
// (/dev/video40 par défaut) et fournit ses propres frame buffers (MMAP). Le
// format livré est YUYV ou MJPEG, et NON le RGB565 du chemin MIPI-CSI : on
// convertit donc chaque frame en RGB565 dans le buffer pool d'affichage
// existant (simple_buffers_), pour réutiliser à l'identique acquire/release et
// get_image_* côté lvgl_camera_display.
// ============================================================================

bool MipiDSICamComponent::start_streaming_uvc_() {
  // Nœud V4L2 du driver UVC. Par défaut le premier device UVC (/dev/video40).
  // Surchargé par `device_path:` si renseigné dans le YAML.
  const char *dev = this->device_path_.empty() ? ESP_VIDEO_USB_UVC_NAME(0)
                                                : this->device_path_.c_str();
  ESP_LOGI(TAG, "UVC: ouverture du nœud %s", dev);

  this->video_fd_ = open(dev, O_RDWR | O_NONBLOCK);
  if (this->video_fd_ < 0) {
    ESP_LOGE(TAG, "UVC: open(%s) a échoué: %s", dev, strerror(errno));
    ESP_LOGE(TAG, "UVC: vérifiez qu'une caméra USB est branchée et que `enable_uvc: true` est activé sur esp_video");
    return false;
  }

  // Nettoyage local sûr en cas d'échec (streaming_active_ pas encore positionné,
  // donc stop_streaming() ne ferait rien).
  auto cleanup = [this]() {
    for (int i = 0; i < UVC_CAPTURE_BUFFERS; i++) {
      if (this->uvc_mmap_[i]) {
        munmap(this->uvc_mmap_[i], this->uvc_mmap_len_[i]);
        this->uvc_mmap_[i] = nullptr;
        this->uvc_mmap_len_[i] = 0;
      }
    }
    for (int i = 0; i < NUM_BUFFERS; i++) {
      if (this->simple_buffers_[i].data) {
        heap_caps_free(this->simple_buffers_[i].data);
        this->simple_buffers_[i].data = nullptr;
      }
    }
    this->uvc_buf_count_ = 0;
    close_fd_(this->video_fd_);
  };

  struct v4l2_capability cap;
  memset(&cap, 0, sizeof(cap));
  if (ioctl(this->video_fd_, VIDIOC_QUERYCAP, &cap) == 0) {
    ESP_LOGI(TAG, "UVC: driver='%s' card='%s'", cap.driver, cap.card);
  }

  // 1. Choisir le format de capture : YUYV de préférence (conversion logicielle
  //    directe, sans dépendance), sinon MJPEG (décodage matériel si activé).
  uint32_t chosen = 0;
  for (int i = 0;; i++) {
    struct v4l2_fmtdesc fmtdesc;
    memset(&fmtdesc, 0, sizeof(fmtdesc));
    fmtdesc.index = i;
    fmtdesc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(this->video_fd_, VIDIOC_ENUM_FMT, &fmtdesc) < 0) {
      break;
    }
    ESP_LOGI(TAG, "UVC: format[%d] = %.4s", i, (const char *) &fmtdesc.pixelformat);
    if (fmtdesc.pixelformat == V4L2_PIX_FMT_YUYV) {
      chosen = V4L2_PIX_FMT_YUYV;  // préféré
      break;
    }
    if (fmtdesc.pixelformat == V4L2_PIX_FMT_MJPEG && chosen == 0) {
      chosen = V4L2_PIX_FMT_MJPEG;  // repli, on continue de chercher YUYV
    }
  }
  if (chosen == 0) {
    ESP_LOGE(TAG, "UVC: aucun format supporté (ni YUYV ni MJPEG) exposé par la caméra");
    cleanup();
    return false;
  }
#if !UVC_HW_JPEG_DECODE
  if (chosen == V4L2_PIX_FMT_MJPEG) {
    ESP_LOGE(TAG, "UVC: la caméra ne fournit que du MJPEG mais le décodage matériel est désactivé");
    ESP_LOGE(TAG, "UVC: ajoutez -DUVC_ENABLE_MJPEG_DECODE dans build_flags, ou utilisez une caméra YUYV");
    cleanup();
    return false;
  }
#endif

  // 2. Format demandé (résolution issue du YAML, repli VGA)
  uint32_t width, height;
  if (!map_resolution_(this->resolution_, width, height)) {
    ESP_LOGW(TAG, "UVC: résolution '%s' non reconnue, repli sur VGA 640x480", this->resolution_.c_str());
    width = 640;
    height = 480;
  }

  struct v4l2_format fmt;
  memset(&fmt, 0, sizeof(fmt));
  fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  fmt.fmt.pix.width = width;
  fmt.fmt.pix.height = height;
  fmt.fmt.pix.pixelformat = chosen;
  fmt.fmt.pix.field = V4L2_FIELD_NONE;
  if (ioctl(this->video_fd_, VIDIOC_S_FMT, &fmt) < 0) {
    ESP_LOGE(TAG, "UVC: VIDIOC_S_FMT %ux%u %.4s a échoué: %s", (unsigned) width, (unsigned) height,
             (const char *) &chosen, strerror(errno));
    cleanup();
    return false;
  }
  // 3. Relire le format réellement appliqué (le driver/caméra peut l'ajuster)
  if (ioctl(this->video_fd_, VIDIOC_G_FMT, &fmt) < 0) {
    ESP_LOGE(TAG, "UVC: VIDIOC_G_FMT a échoué: %s", strerror(errno));
    cleanup();
    return false;
  }
  this->image_width_ = fmt.fmt.pix.width;
  this->image_height_ = fmt.fmt.pix.height;
  this->uvc_capture_fourcc_ = fmt.fmt.pix.pixelformat;
  // Sortie d'affichage : toujours RGB565 (2 octets/pixel) après conversion
  this->image_buffer_size_ = (size_t) this->image_width_ * this->image_height_ * 2;
  ESP_LOGI(TAG, "UVC: capture %ux%u %.4s → RGB565 (%u octets/buffer)", this->image_width_,
           this->image_height_, (const char *) &this->uvc_capture_fourcc_,
           (unsigned) this->image_buffer_size_);

  // 4. Allouer les buffers RGB565 de SORTIE (buffer pool d'affichage), comme le chemin MIPI
  size_t cache_line_size = 64;
  esp_cache_get_alignment(MALLOC_CAP_SPIRAM, &cache_line_size);
  if (cache_line_size < 64) {
    cache_line_size = 64;
  }
  for (int i = 0; i < NUM_BUFFERS; i++) {
    this->simple_buffers_[i].data = (uint8_t *) heap_caps_aligned_alloc(
        cache_line_size, this->image_buffer_size_, MALLOC_CAP_SPIRAM);
    if (this->simple_buffers_[i].data == nullptr) {
      ESP_LOGE(TAG, "UVC: échec allocation buffer RGB565 %d (%u octets)", i,
               (unsigned) this->image_buffer_size_);
      cleanup();
      return false;
    }
    this->simple_buffers_[i].allocated = false;
    this->simple_buffers_[i].index = i;
  }
  this->current_buffer_index_ = -1;
  this->image_buffer_ = nullptr;

  // 5. Demander les buffers de CAPTURE du driver UVC en MMAP (mémoire fournie par le driver)
  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = UVC_CAPTURE_BUFFERS;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->video_fd_, VIDIOC_REQBUFS, &req) < 0) {
    ESP_LOGE(TAG, "UVC: VIDIOC_REQBUFS (MMAP) a échoué: %s", strerror(errno));
    cleanup();
    return false;
  }
  // Le driver peut renvoyer un nombre différent ; on borne à notre capacité.
  this->uvc_buf_count_ = (int) req.count > UVC_CAPTURE_BUFFERS ? UVC_CAPTURE_BUFFERS : (int) req.count;

  for (int i = 0; i < this->uvc_buf_count_; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;
    if (ioctl(this->video_fd_, VIDIOC_QUERYBUF, &buf) < 0) {
      ESP_LOGE(TAG, "UVC: VIDIOC_QUERYBUF[%d] a échoué: %s", i, strerror(errno));
      cleanup();
      return false;
    }
    this->uvc_mmap_len_[i] = buf.length;
    this->uvc_mmap_[i] = (uint8_t *) mmap(NULL, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED,
                                          this->video_fd_, buf.m.offset);
    if (this->uvc_mmap_[i] == MAP_FAILED) {
      ESP_LOGE(TAG, "UVC: mmap[%d] a échoué: %s", i, strerror(errno));
      this->uvc_mmap_[i] = nullptr;
      cleanup();
      return false;
    }
    if (ioctl(this->video_fd_, VIDIOC_QBUF, &buf) < 0) {
      ESP_LOGE(TAG, "UVC: VIDIOC_QBUF[%d] a échoué: %s", i, strerror(errno));
      cleanup();
      return false;
    }
  }

  // 6. Démarrer le flux
  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->video_fd_, VIDIOC_STREAMON, &type) < 0) {
    ESP_LOGE(TAG, "UVC: VIDIOC_STREAMON a échoué: %s", strerror(errno));
    cleanup();
    return false;
  }

  this->streaming_active_ = true;
  this->frame_sequence_ = 0;
  ESP_LOGI(TAG, "UVC: streaming démarré");
  return true;
}

bool MipiDSICamComponent::capture_frame_uvc_() {
  // Only feed the TWDT if this task is subscribed (see capture_frame() note).
  if (esp_task_wdt_status(nullptr) == ESP_OK)
    esp_task_wdt_reset();

  // 1. Récupérer une frame de capture (MMAP, fournie par le driver UVC)
  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->video_fd_, VIDIOC_DQBUF, &buf) < 0) {
    if (errno == EAGAIN) {
      return false;  // pas de frame prête (mode non bloquant)
    }
    ESP_LOGE(TAG, "UVC: VIDIOC_DQBUF a échoué: %s", strerror(errno));
    return false;
  }

  const int cap_idx = buf.index;
  if (cap_idx < 0 || cap_idx >= UVC_CAPTURE_BUFFERS || this->uvc_mmap_[cap_idx] == nullptr) {
    ESP_LOGW(TAG, "UVC: index de buffer DQBUF invalide (%d)", cap_idx);
    ioctl(this->video_fd_, VIDIOC_QBUF, &buf);  // rendre le buffer
    return false;
  }
  const uint8_t *src = this->uvc_mmap_[cap_idx];
  const size_t src_len = buf.bytesused;

  // 2. Choisir un buffer RGB565 de sortie distinct de celui actuellement affiché
  //    (double buffering, comme le chemin MIPI — léger risque de tearing accepté)
  int out_idx;
  portENTER_CRITICAL(&this->buffer_mutex_);
  out_idx = (this->current_buffer_index_ == 0) ? 1 : 0;
  portEXIT_CRITICAL(&this->buffer_mutex_);

  // 3. Convertir la frame UVC en RGB565
  bool ok = this->convert_uvc_frame_(src, src_len, this->simple_buffers_[out_idx].data);

  // 4. Rendre le buffer de capture au driver UVC, quoi qu'il arrive
  if (ioctl(this->video_fd_, VIDIOC_QBUF, &buf) < 0) {
    ESP_LOGW(TAG, "UVC: VIDIOC_QBUF a échoué: %s", strerror(errno));
  }
  if (!ok) {
    return false;
  }

  // 5. Publier le buffer converti dans le pool d'affichage
  portENTER_CRITICAL(&this->buffer_mutex_);
  if (this->current_buffer_index_ >= 0 && this->current_buffer_index_ != out_idx) {
    this->simple_buffers_[this->current_buffer_index_].allocated = false;
  }
  this->simple_buffers_[out_idx].allocated = true;
  this->current_buffer_index_ = out_idx;
  this->image_buffer_ = this->simple_buffers_[out_idx].data;
  portEXIT_CRITICAL(&this->buffer_mutex_);

  this->frame_sequence_++;
  if (this->frame_sequence_ == 1) {
    ESP_LOGI(TAG, "UVC: première frame %ux%u convertie en RGB565", this->image_width_,
             this->image_height_);
  }
  return true;
}

bool MipiDSICamComponent::convert_uvc_frame_(const uint8_t *src, size_t src_len, uint8_t *dst) {
  const int w = this->image_width_;
  const int h = this->image_height_;

  if (this->uvc_capture_fourcc_ == V4L2_PIX_FMT_YUYV) {
    const size_t expected = (size_t) w * h * 2;  // 2 octets/pixel en YUYV
    if (src_len < expected) {
      ESP_LOGW(TAG, "UVC: frame YUYV tronquée (%u < %u octets)", (unsigned) src_len,
               (unsigned) expected);
      return false;
    }
    uint16_t *out = (uint16_t *) dst;
    const int npix = w * h;
    // YUYV : 4 octets (Y0 U Y1 V) = 2 pixels partageant la chrominance
    for (int i = 0; i < npix; i += 2) {
      const uint8_t *p = src + (size_t) i * 2;
      int y0 = p[0], u = p[1], y1 = p[2], v = p[3];
      out[i] = yuv_to_rgb565_(y0, u, v);
      out[i + 1] = yuv_to_rgb565_(y1, u, v);
    }
    return true;
  }

#if UVC_HW_JPEG_DECODE
  if (this->uvc_capture_fourcc_ == V4L2_PIX_FMT_MJPEG) {
    // Décodage MJPEG → RGB565 via le moteur JPEG matériel de l'ESP32-P4.
    // Opt-in / expérimental : selon la version d'ESP-IDF, le buffer de sortie
    // peut nécessiter un alignement particulier (jpeg_alloc_decoder_mem).
    if (this->uvc_jpeg_decoder_ == nullptr) {
      jpeg_decode_engine_cfg_t eng = {};
      eng.timeout_ms = 70;
      jpeg_decoder_handle_t hdl = nullptr;
      esp_err_t e = jpeg_new_decoder_engine(&eng, &hdl);
      if (e != ESP_OK) {
        ESP_LOGE(TAG, "UVC: jpeg_new_decoder_engine a échoué: %s", esp_err_to_name(e));
        return false;
      }
      this->uvc_jpeg_decoder_ = hdl;
    }
    jpeg_decode_cfg_t dec = {};
    dec.output_format = JPEG_DECODE_OUT_FORMAT_RGB565;
    dec.rgb_order = JPEG_DEC_RGB_ELEMENT_ORDER_RGB;
    uint32_t out_len = 0;
    esp_err_t e = jpeg_decoder_process((jpeg_decoder_handle_t) this->uvc_jpeg_decoder_, &dec, src,
                                       (uint32_t) src_len, dst, (uint32_t) this->image_buffer_size_,
                                       &out_len);
    if (e != ESP_OK) {
      ESP_LOGW(TAG, "UVC: jpeg_decoder_process a échoué: %s", esp_err_to_name(e));
      return false;
    }
    return true;
  }
#endif

  ESP_LOGW(TAG, "UVC: format de capture non supporté (0x%08x)",
           (unsigned) this->uvc_capture_fourcc_);
  return false;
}

void MipiDSICamComponent::stop_streaming_uvc_() {
  for (int i = 0; i < UVC_CAPTURE_BUFFERS; i++) {
    if (this->uvc_mmap_[i]) {
      munmap(this->uvc_mmap_[i], this->uvc_mmap_len_[i]);
      this->uvc_mmap_[i] = nullptr;
      this->uvc_mmap_len_[i] = 0;
    }
  }
  this->uvc_buf_count_ = 0;
#if UVC_HW_JPEG_DECODE
  if (this->uvc_jpeg_decoder_) {
    jpeg_del_decoder_engine((jpeg_decoder_handle_t) this->uvc_jpeg_decoder_);
    this->uvc_jpeg_decoder_ = nullptr;
  }
#endif
}


bool MipiDSICamComponent::set_exposure(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set exposure: ISP device not open");
    return false;
  }

  // V4L2_CID_EXPOSURE_ABSOLUTE control
  struct v4l2_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));

  if (value == 0) {
    // Réactiver AEC automatique
    ctrl.id = V4L2_CID_EXPOSURE_AUTO;
    ctrl.value = V4L2_EXPOSURE_AUTO;  // Auto exposure

    if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
      ESP_LOGE(TAG, "Failed to enable auto exposure: %s", strerror(errno));
      return false;
    }
    ESP_LOGI(TAG, "Auto exposure enabled (AEC active)");
  } else {
    // Désactiver AEC et définir exposition manuelle
    ctrl.id = V4L2_CID_EXPOSURE_AUTO;
    ctrl.value = V4L2_EXPOSURE_MANUAL;  // Manual exposure

    if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
      ESP_LOGW(TAG, "Failed to disable auto exposure: %s", strerror(errno));
      // Continue anyway, try to set exposure value
    }

    // Définir la valeur d'exposition
    ctrl.id = V4L2_CID_EXPOSURE_ABSOLUTE;
    ctrl.value = value;

    if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
      ESP_LOGE(TAG, "Failed to set exposure to %d: %s", value, strerror(errno));
      return false;
    }
    ESP_LOGI(TAG, "Manual exposure set to %d (AEC disabled)", value);
  }

  return true;
}


bool MipiDSICamComponent::set_gain(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set gain: ISP device not open");
    return false;
  }

  // V4L2_CID_GAIN control
  struct v4l2_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_GAIN;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set gain to %d: %s", value, strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Gain set to %d (%.1fx)", value, value / 1000.0f);
  return true;
}


bool MipiDSICamComponent::set_white_balance_mode(bool auto_mode) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set white balance mode: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_AUTO_WHITE_BALANCE;
  ctrl.value = auto_mode ? 1 : 0;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set white balance mode: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "White balance: %s", auto_mode ? "AUTO (AWB enabled)" : "MANUAL");
  return true;
}


bool MipiDSICamComponent::set_white_balance_temp(int kelvin) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set white balance temperature: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_WHITE_BALANCE_TEMPERATURE;
  ctrl.value = kelvin;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set white balance temperature to %dK: %s", kelvin, strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "White balance temperature set to %dK", kelvin);
  return true;
}


bool MipiDSICamComponent::set_ccm_matrix(float matrix[3][3]) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set CCM matrix: ISP device not open");
    return false;
  }

  // Créer structure CCM avec matrice fournie
  esp_video_isp_ccm_t ccm_config;
  memset(&ccm_config, 0, sizeof(ccm_config));
  ccm_config.enable = true;

  // Copier matrice (dimensions vérifiées par ISP_CCM_DIMENSION)
  for (int i = 0; i < 3; i++) {
    for (int j = 0; j < 3; j++) {
      ccm_config.matrix[i][j] = matrix[i][j];
    }
  }

  // Configurer via V4L2 ioctl avec CID personnalisé ESP32
  struct v4l2_ext_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_USER_ESP_ISP_CCM;
  ctrl.ptr = &ccm_config;
  ctrl.size = sizeof(ccm_config);

  struct v4l2_ext_controls ctrls;
  memset(&ctrls, 0, sizeof(ctrls));
  ctrls.count = 1;
  ctrls.controls = &ctrl;

  if (ioctl(this->isp_fd_, VIDIOC_S_EXT_CTRLS, &ctrls) < 0) {
    ESP_LOGE(TAG, "Failed to set CCM matrix: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "CCM matrix configured:");
  ESP_LOGI(TAG, "  [%.2f, %.2f, %.2f]", matrix[0][0], matrix[0][1], matrix[0][2]);
  ESP_LOGI(TAG, "  [%.2f, %.2f, %.2f]", matrix[1][0], matrix[1][1], matrix[1][2]);
  ESP_LOGI(TAG, "  [%.2f, %.2f, %.2f]", matrix[2][0], matrix[2][1], matrix[2][2]);
  return true;
}


bool MipiDSICamComponent::set_rgb_gains(float red, float green, float blue) {
  // Créer matrice diagonale
  float matrix[3][3] = {
    {red,  0.0f, 0.0f},
    {0.0f, green, 0.0f},
    {0.0f, 0.0f,  blue}
  };

  if (!set_ccm_matrix(matrix)) {
    return false;
  }

  ESP_LOGI(TAG, "RGB gains: R=%.2f, G=%.2f, B=%.2f", red, green, blue);
  return true;
}


bool MipiDSICamComponent::set_wb_gains(float red_gain, float blue_gain) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set WB gains: ISP device not open");
    return false;
  }

  // Créer structure WB
  esp_video_isp_wb_t wb_config;
  memset(&wb_config, 0, sizeof(wb_config));
  wb_config.enable = true;
  wb_config.red_gain = red_gain;
  wb_config.blue_gain = blue_gain;

  // Configurer via V4L2 ioctl avec CID personnalisé ESP32
  struct v4l2_ext_control ctrl;
  memset(&ctrl, 0, sizeof(ctrl));
  ctrl.id = V4L2_CID_USER_ESP_ISP_WB;
  ctrl.ptr = &wb_config;
  ctrl.size = sizeof(wb_config);

  struct v4l2_ext_controls ctrls;
  memset(&ctrls, 0, sizeof(ctrls));
  ctrls.count = 1;
  ctrls.controls = &ctrl;

  if (ioctl(this->isp_fd_, VIDIOC_S_EXT_CTRLS, &ctrls) < 0) {
    ESP_LOGE(TAG, "Failed to set WB gains: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "WB gains: Red=%.2f, Blue=%.2f (Green=1.0)", red_gain, blue_gain);
  return true;
}


bool MipiDSICamComponent::set_brightness(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set brightness: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  ctrl.id = V4L2_CID_BRIGHTNESS;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set brightness: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Brightness set to %d", value);
  return true;
}

/**
 * @brief Régler le contraste de l'image
 * @param value Valeur de contraste (0 à 255, défaut: 128)
 */
bool MipiDSICamComponent::set_contrast(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set contrast: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  ctrl.id = V4L2_CID_CONTRAST;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set contrast: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Contrast set to %d", value);
  return true;
}

/**
 * @brief Régler la saturation des couleurs
 * @param value Valeur de saturation (0 à 255, défaut: 128)
 */
bool MipiDSICamComponent::set_saturation(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set saturation: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  ctrl.id = V4L2_CID_SATURATION;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set saturation: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Saturation set to %d", value);
  return true;
}

/**
 * @brief Régler la teinte de l'image
 * @param value Valeur de teinte (-180 à 180, défaut: 0)
 */
bool MipiDSICamComponent::set_hue(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set hue: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  ctrl.id = V4L2_CID_HUE;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set hue: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Hue set to %d", value);
  return true;
}

/**
 * @brief Régler la netteté de l'image (filter)
 * @param value Valeur de netteté (0 à 255, défaut: 128)
 */
bool MipiDSICamComponent::set_sharpness(int value) {
  if (!this->streaming_active_ || this->isp_fd_ < 0) {
    ESP_LOGW(TAG, "Cannot set sharpness: ISP device not open");
    return false;
  }

  struct v4l2_control ctrl;
  ctrl.id = V4L2_CID_SHARPNESS;
  ctrl.value = value;

  if (ioctl(this->isp_fd_, VIDIOC_S_CTRL, &ctrl) < 0) {
    ESP_LOGE(TAG, "Failed to set sharpness: %s", strerror(errno));
    return false;
  }

  ESP_LOGI(TAG, "Sharpness set to %d", value);
  return true;
}

// ============================================================================
// imlib - Méthodes de dessin zero-copy sur buffer RGB565
// ============================================================================

#if IMLIB_AVAILABLE

image_t* MipiDSICamComponent::get_imlib_image() {
  if (!this->streaming_active_ || !this->image_buffer_ || this->image_buffer_size_ == 0) {
    ESP_LOGW(TAG, "Cannot get imlib image: no active frame buffer");
    this->imlib_image_valid_ = false;
    return nullptr;
  }

  // Allouer la structure imlib au premier appel
  if (!this->imlib_image_) {
    this->imlib_image_ = (image_t*)malloc(sizeof(image_t));
    if (!this->imlib_image_) {
      ESP_LOGE(TAG, "Failed to allocate imlib image structure");
      return nullptr;
    }
    memset(this->imlib_image_, 0, sizeof(image_t));
  }

  // Initialiser la structure imlib image_t pour pointer vers le buffer V4L2 (zero-copy)
  // Use getters to account for rotation
  this->imlib_image_->w = this->get_image_width();
  this->imlib_image_->h = this->get_image_height();
  this->imlib_image_->pixfmt = PIXFORMAT_RGB565;
  this->imlib_image_->pixels = this->image_buffer_;
  this->imlib_image_valid_ = true;

  return this->imlib_image_;
}

void MipiDSICamComponent::draw_string(int x, int y, const char *text, uint16_t color, float scale) {
  image_t *img = this->get_imlib_image();
  if (!img) return;

  imlib_draw_string(img, x, y, text, color, scale, 1, 1, 0, false, false, PIXFORMAT_RGB565, nullptr);
}

void MipiDSICamComponent::draw_line(int x0, int y0, int x1, int y1, uint16_t color, int thickness) {
  image_t *img = this->get_imlib_image();
  if (!img) return;

  imlib_draw_line(img, x0, y0, x1, y1, color, thickness);
}

void MipiDSICamComponent::draw_rectangle(int x, int y, int w, int h, uint16_t color, int thickness, bool fill) {
  image_t *img = this->get_imlib_image();
  if (!img) return;

  imlib_draw_rectangle(img, x, y, w, h, color, thickness, fill);
}

void MipiDSICamComponent::draw_circle(int cx, int cy, int radius, uint16_t color, int thickness, bool fill) {
  image_t *img = this->get_imlib_image();
  if (!img) return;

  imlib_draw_circle(img, cx, cy, radius, color, thickness, fill);
}

int MipiDSICamComponent::get_pixel(int x, int y) {
  image_t *img = this->get_imlib_image();
  if (!img) return 0;

  return imlib_get_pixel(img, x, y);
}

void MipiDSICamComponent::set_pixel(int x, int y, uint16_t color) {
  image_t *img = this->get_imlib_image();
  if (!img) return;

  imlib_set_pixel(img, x, y, color);
}

#else  // IMLIB_AVAILABLE == 0

// Stubs imlib (imlib désactivé) - retournent sans erreur
image_t* MipiDSICamComponent::get_imlib_image() {
  ESP_LOGW(TAG, "imlib drawing disabled (compile with -DENABLE_IMLIB_DRAWING to enable)");
  return nullptr;
}

void MipiDSICamComponent::draw_string(int x, int y, const char *text, uint16_t color, float scale) {
  // Stub - ne fait rien
}

void MipiDSICamComponent::draw_line(int x0, int y0, int x1, int y1, uint16_t color, int thickness) {
  // Stub - ne fait rien
}

void MipiDSICamComponent::draw_rectangle(int x, int y, int w, int h, uint16_t color, int thickness, bool fill) {
  // Stub - ne fait rien
}

void MipiDSICamComponent::draw_circle(int cx, int cy, int radius, uint16_t color, int thickness, bool fill) {
  // Stub - ne fait rien
}

int MipiDSICamComponent::get_pixel(int x, int y) {
  return 0;  // Stub - retourne noir
}

void MipiDSICamComponent::set_pixel(int x, int y, uint16_t color) {
  // Stub - ne fait rien
}

#endif  // IMLIB_AVAILABLE


SimpleBufferElement* MipiDSICamComponent::acquire_buffer() {
  if (!this->streaming_active_) {
    return nullptr;
  }

  SimpleBufferElement *buffer = nullptr;
  portENTER_CRITICAL(&this->buffer_mutex_);
  if (this->current_buffer_index_ >= 0) {
    buffer = &this->simple_buffers_[this->current_buffer_index_];
  }
  portEXIT_CRITICAL(&this->buffer_mutex_);

  return buffer;
}


void MipiDSICamComponent::release_buffer(SimpleBufferElement *element) {
  if (element == nullptr) {
    return;
  }

  // Ne PAS libérer current_buffer_index_ (il est encore utilisé pour capture)
  portENTER_CRITICAL(&this->buffer_mutex_);
  if (element->index != this->current_buffer_index_) {
    element->allocated = false;
  }
  portEXIT_CRITICAL(&this->buffer_mutex_);
}


uint8_t* MipiDSICamComponent::get_buffer_data(SimpleBufferElement *element) {
  if (element == nullptr) {
    return nullptr;
  }
  // CRITICAL: When PPA is enabled, return PPA output buffer instead of V4L2 input buffer!
  // V4L2 buffer (element->data) is 480x640, but PPA buffer (image_buffer_) is 640x480 after rotation
  if (this->ppa_enabled_ && this->image_buffer_) {
    return this->image_buffer_;  // PPA output buffer (rotated/transformed)
  }
  return element->data;  // V4L2 input buffer (no transformation)
}


uint32_t MipiDSICamComponent::get_buffer_index(SimpleBufferElement *element) {
  if (element == nullptr) {
    return 0;
  }
  return element->index;
}


bool MipiDSICamComponent::get_current_rgb_frame(SimpleBufferElement **buffer_out, uint8_t **data, int *width,
                                                 int *height) {
  if (buffer_out == nullptr || data == nullptr || width == nullptr || height == nullptr) {
    ESP_LOGE(TAG, "get_current_rgb_frame: nullptr parameter");
    return false;
  }

  if (!this->streaming_active_) {
    ESP_LOGW(TAG, "get_current_rgb_frame: not streaming");
    return false;
  }

  // Acquire current buffer
  SimpleBufferElement *buffer = this->acquire_buffer();
  if (buffer == nullptr) {
    ESP_LOGW(TAG, "get_current_rgb_frame: no buffer available");
    return false;
  }

  // Extract data and dimensions (use getters to account for rotation)
  *buffer_out = buffer;
  *data = buffer->data;
  *width = static_cast<int>(this->get_image_width());
  *height = static_cast<int>(this->get_image_height());

  return true;
}

}  // namespace esp_cam_sensor
}  // namespace esphome




