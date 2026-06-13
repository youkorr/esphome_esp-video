#include "esp_video_camera.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include "esp_heap_caps.h"

#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <errno.h>

extern "C" {
#include "esp_video_device.h"
#include "linux/videodev2.h"
}

// Certains SDK ne définissent pas ce contrôle JPEG : fallback.
#ifndef V4L2_CID_JPEG_COMPRESSION_QUALITY
#define V4L2_CID_JPEG_COMPRESSION_QUALITY (V4L2_CID_JPEG_CLASS_BASE + 1)
#endif

namespace esphome {
namespace esp_video_camera {

static const char *const TAG = "esp_video_camera";

// ===========================================================================
// ESPVideoCameraImage
// ===========================================================================
ESPVideoCameraImage::ESPVideoCameraImage(uint8_t *data, size_t length, uint8_t requesters)
    : data_(data), length_(length), requesters_(requesters) {}

ESPVideoCameraImage::~ESPVideoCameraImage() {
  if (this->data_ != nullptr) {
    heap_caps_free(this->data_);
    this->data_ = nullptr;
  }
}

bool ESPVideoCameraImage::was_requested_by(camera::CameraRequester requester) const {
  return (this->requesters_ & (1 << requester)) != 0;
}

// ===========================================================================
// ESPVideoCameraImageReader
// ===========================================================================
void ESPVideoCameraImageReader::set_image(std::shared_ptr<camera::CameraImage> image) {
  this->image_ = std::move(image);
  this->offset_ = 0;
}

size_t ESPVideoCameraImageReader::available() const {
  if (this->image_ == nullptr)
    return 0;
  return this->image_->get_data_length() - this->offset_;
}

uint8_t *ESPVideoCameraImageReader::peek_data_buffer() {
  if (this->image_ == nullptr)
    return nullptr;
  return this->image_->get_data_buffer() + this->offset_;
}

void ESPVideoCameraImageReader::consume_data(size_t consumed) { this->offset_ += consumed; }

void ESPVideoCameraImageReader::return_image() {
  this->image_.reset();
  this->offset_ = 0;
}

// ===========================================================================
// ESPVideoCamera
// ===========================================================================
void ESPVideoCamera::setup() {
  // Résoudre l'alias de device vers un chemin /dev/videoN concret.
  const std::string &d = this->device_;
  if (d == "jpeg" || d.empty()) {
    this->resolved_device_ = ESP_VIDEO_JPEG_DEVICE_NAME;  // /dev/video10
    this->is_hw_jpeg_ = true;
  } else if (d == "uvc") {
    this->resolved_device_ = ESP_VIDEO_USB_UVC_NAME_PREFIX "0";  // /dev/video40
  } else if (d.rfind("uvc", 0) == 0 && d.size() == 4) {
    this->resolved_device_ = std::string(ESP_VIDEO_USB_UVC_NAME_PREFIX) + d.substr(3);  // uvc0..uvc9
  } else if (d == "csi") {
    this->resolved_device_ = ESP_VIDEO_MIPI_CSI_DEVICE_NAME;  // /dev/video0
  } else if (d.rfind("/dev/", 0) == 0) {
    this->resolved_device_ = d;
    this->is_hw_jpeg_ = (d == ESP_VIDEO_JPEG_DEVICE_NAME);
  } else {
    ESP_LOGW(TAG, "Device '%s' inconnu, fallback sur l'encodeur JPEG matériel", d.c_str());
    this->resolved_device_ = ESP_VIDEO_JPEG_DEVICE_NAME;
    this->is_hw_jpeg_ = true;
  }

  // Vérifier que le device existe (esp_video doit être initialisé en amont).
  int test_fd = open(this->resolved_device_.c_str(), O_RDWR | O_NONBLOCK);
  if (test_fd < 0) {
    ESP_LOGE(TAG, "Device V4L2 '%s' indisponible (errno=%d: %s)", this->resolved_device_.c_str(), errno,
             strerror(errno));
    if (this->is_hw_jpeg_)
      ESP_LOGE(TAG, "  Activez 'enable_jpeg: true' dans le composant esp_video.");
    else
      ESP_LOGE(TAG, "  Pour l'UVC, activez 'enable_uvc: true' et branchez une caméra USB MJPEG.");
    this->mark_failed();
    return;
  }
  close(test_fd);

  ESP_LOGI(TAG, "Caméra Home Assistant prête sur %s (source: %s)", this->resolved_device_.c_str(),
           this->device_.c_str());
}

void ESPVideoCamera::loop() {
  if (!this->streaming_)
    return;

  // Récupérer une frame si disponible (non bloquant).
  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_MMAP;

  if (ioctl(this->fd_, VIDIOC_DQBUF, &buf) < 0) {
    if (errno == EAGAIN)
      return;  // Pas encore de frame
    ESP_LOGW(TAG, "VIDIOC_DQBUF a échoué: %s", strerror(errno));
    return;
  }

  bool throttled = false;
  uint32_t now = millis();
  if (this->min_interval_ms_ > 0 && (now - this->last_frame_ms_) < this->min_interval_ms_)
    throttled = true;  // Trop tôt : on recycle le buffer sans diffuser.

  if (!throttled && buf.index < (uint32_t) this->num_buffers_ && buf.bytesused > 0) {
    this->last_frame_ms_ = now;

    // Copier les octets JPEG dans un buffer possédé par l'image (de préférence
    // en PSRAM) pour pouvoir re-mettre en file le buffer V4L2 immédiatement.
    size_t len = buf.bytesused;
    uint8_t *copy = (uint8_t *) heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (copy == nullptr)
      copy = (uint8_t *) heap_caps_malloc(len, MALLOC_CAP_8BIT);

    if (copy != nullptr) {
      memcpy(copy, this->buffers_[buf.index].start, len);
      this->current_image_ =
          std::make_shared<ESPVideoCameraImage>(copy, len, this->single_requesters_ | this->stream_requesters_);
      for (auto *listener : this->listeners_)
        listener->on_camera_image(this->current_image_);
    } else {
      ESP_LOGW(TAG, "Allocation %u octets échouée (frame ignorée)", (unsigned) len);
    }

    // Les requêtes d'image unique sont satisfaites par cette frame.
    this->single_requesters_ = 0;
  }

  // Re-mettre le buffer en file pour la capture suivante.
  if (ioctl(this->fd_, VIDIOC_QBUF, &buf) < 0)
    ESP_LOGW(TAG, "VIDIOC_QBUF a échoué: %s", strerror(errno));

  // Plus aucun consommateur : on arrête la capture.
  if (this->stream_requesters_ == 0 && this->single_requesters_ == 0)
    this->stop_capture_();
}

camera::CameraImageReader *ESPVideoCamera::create_image_reader() { return new ESPVideoCameraImageReader(); }

void ESPVideoCamera::request_image(camera::CameraRequester requester) {
  this->single_requesters_ |= (1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::start_stream(camera::CameraRequester requester) {
  for (auto *listener : this->listeners_)
    listener->on_stream_start();
  this->stream_requesters_ |= (1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::stop_stream(camera::CameraRequester requester) {
  for (auto *listener : this->listeners_)
    listener->on_stream_stop();
  this->stream_requesters_ &= ~(1U << requester);
  this->update_capture_state_();
}

void ESPVideoCamera::update_capture_state_() {
  bool wanted = (this->stream_requesters_ != 0) || (this->single_requesters_ != 0);
  if (wanted && !this->streaming_)
    this->start_capture_();
  // L'arrêt est géré dans loop() après livraison de la dernière frame.
}

bool ESPVideoCamera::parse_resolution_(const std::string &res, uint32_t &width, uint32_t &height) {
  if (res.empty() || res == "auto")
    return false;  // Pas de résolution imposée
  if (res == "QVGA") { width = 320; height = 240; return true; }
  if (res == "VGA" || res == "480P") { width = 640; height = 480; return true; }
  if (res == "720P") { width = 1280; height = 720; return true; }
  if (res == "1080P") { width = 1920; height = 1080; return true; }
  unsigned int w = 0, h = 0;
  if (sscanf(res.c_str(), "%ux%u", &w, &h) == 2 && w > 0 && h > 0) {
    width = w;
    height = h;
    return true;
  }
  return false;
}

void ESPVideoCamera::configure_format_() {
  uint32_t width = 0, height = 0;
  bool force_res = parse_resolution_(this->resolution_, width, height);

  // Lire le format courant, ajuster, puis ré-appliquer (best-effort).
  // - UVC : on demande explicitement du MJPEG (+ résolution si fournie).
  // - JPEG matériel : on n'impose la résolution que si elle est demandée ;
  //   sinon on garde la résolution native du capteur auto-détecté.
  if (!this->is_hw_jpeg_ || force_res) {
    struct v4l2_format fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(this->fd_, VIDIOC_G_FMT, &fmt) == 0) {
      if (!this->is_hw_jpeg_)
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
      if (force_res) {
        fmt.fmt.pix.width = width;
        fmt.fmt.pix.height = height;
      }
      fmt.fmt.pix.field = V4L2_FIELD_NONE;
      if (ioctl(this->fd_, VIDIOC_S_FMT, &fmt) < 0)
        ESP_LOGW(TAG, "VIDIOC_S_FMT (résolution best-effort) a échoué: %s", strerror(errno));
    }
  }

  // Qualité JPEG (encodeur matériel uniquement).
  if (this->is_hw_jpeg_) {
    struct v4l2_control ctrl;
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.id = V4L2_CID_JPEG_COMPRESSION_QUALITY;
    ctrl.value = this->jpeg_quality_;
    ioctl(this->fd_, VIDIOC_S_CTRL, &ctrl);  // best-effort
  }
}

bool ESPVideoCamera::start_capture_() {
  if (this->streaming_)
    return true;
  if (this->is_failed())
    return false;

  this->fd_ = open(this->resolved_device_.c_str(), O_RDWR | O_NONBLOCK);
  if (this->fd_ < 0) {
    ESP_LOGE(TAG, "open(%s) a échoué: %s", this->resolved_device_.c_str(), strerror(errno));
    return false;
  }

  // Format (résolution / MJPEG) et qualité JPEG.
  this->configure_format_();

  // Allouer des buffers en MMAP.
  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = MAX_BUFFERS;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->fd_, VIDIOC_REQBUFS, &req) < 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS a échoué: %s", strerror(errno));
    this->stop_capture_();
    return false;
  }

  this->num_buffers_ = 0;
  for (unsigned int i = 0; i < req.count && i < MAX_BUFFERS; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;

    if (ioctl(this->fd_, VIDIOC_QUERYBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QUERYBUF[%u] a échoué: %s", i, strerror(errno));
      this->stop_capture_();
      return false;
    }

    this->buffers_[i].length = buf.length;
    this->buffers_[i].start =
        mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, this->fd_, buf.m.offset);
    if (this->buffers_[i].start == MAP_FAILED) {
      ESP_LOGE(TAG, "mmap[%u] a échoué: %s", i, strerror(errno));
      this->buffers_[i].start = nullptr;
      this->stop_capture_();
      return false;
    }
    this->num_buffers_++;

    if (ioctl(this->fd_, VIDIOC_QBUF, &buf) < 0) {
      ESP_LOGE(TAG, "VIDIOC_QBUF[%u] a échoué: %s", i, strerror(errno));
      this->stop_capture_();
      return false;
    }
  }

  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->fd_, VIDIOC_STREAMON, &type) < 0) {
    ESP_LOGE(TAG, "VIDIOC_STREAMON a échoué: %s", strerror(errno));
    this->stop_capture_();
    return false;
  }

  this->streaming_ = true;
  this->last_frame_ms_ = 0;
  ESP_LOGD(TAG, "Capture démarrée sur %s", this->resolved_device_.c_str());
  return true;
}

void ESPVideoCamera::stop_capture_() {
  if (this->fd_ >= 0) {
    if (this->streaming_) {
      int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
      ioctl(this->fd_, VIDIOC_STREAMOFF, &type);
    }
    for (int i = 0; i < this->num_buffers_; i++) {
      if (this->buffers_[i].start != nullptr) {
        munmap(this->buffers_[i].start, this->buffers_[i].length);
        this->buffers_[i].start = nullptr;
      }
    }
    close(this->fd_);
    this->fd_ = -1;
  }
  this->num_buffers_ = 0;
  this->streaming_ = false;
  ESP_LOGD(TAG, "Capture arrêtée");
}

void ESPVideoCamera::dump_config() {
  ESP_LOGCONFIG(TAG, "ESP-Video Camera (Home Assistant):");
  ESP_LOGCONFIG(TAG, "  Name: %s", this->get_name().c_str());
  ESP_LOGCONFIG(TAG, "  Source: %s (%s)", this->device_.c_str(), this->resolved_device_.c_str());
  ESP_LOGCONFIG(TAG, "  Resolution: %s", this->resolution_.c_str());
  if (this->is_hw_jpeg_)
    ESP_LOGCONFIG(TAG, "  JPEG quality: %d", this->jpeg_quality_);
  ESP_LOGCONFIG(TAG, "  Max framerate: %.1f fps", this->max_framerate_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  État: ÉCHEC (device indisponible)");
}

}  // namespace esp_video_camera
}  // namespace esphome
