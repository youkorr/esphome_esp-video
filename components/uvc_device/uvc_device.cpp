#include "uvc_device.h"

#include "esphome/core/log.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

extern "C" {
#include "esp_video_device.h"
#include "linux/videodev2.h"
}

namespace esphome {
namespace uvc_device {

static const char *const TAG = "uvc_device";

// How often to report the rate frames are actually leaving at.
static constexpr uint32_t STATS_INTERVAL_MS = 10000;

// The JPEG encoder takes one of these on its input; which one depends on what
// the sensor and the ISP are producing. Order is preference.
static const uint32_t JPEG_INPUT_FORMATS[] = {
    V4L2_PIX_FMT_RGB565,
    V4L2_PIX_FMT_UYVY,
    V4L2_PIX_FMT_RGB24,
    V4L2_PIX_FMT_GREY,
};

void UVCDevice::setup() {
  // esp_video_init() has already run (the esp_video component owns it), so both
  // devices exist by now.
  this->capture_fd_ = open(ESP_VIDEO_MIPI_CSI_DEVICE_NAME, O_RDONLY);
  if (this->capture_fd_ < 0) {
    ESP_LOGE(TAG,
             "No MIPI-CSI sensor on %s (%s). The sensor has to be detected before this component can present it "
             "over USB.",
             ESP_VIDEO_MIPI_CSI_DEVICE_NAME, strerror(errno));
    this->mark_failed(LOG_STR("No MIPI-CSI sensor"));
    return;
  }

  this->encoder_fd_ = open(ESP_VIDEO_JPEG_DEVICE_NAME, O_RDONLY);
  if (this->encoder_fd_ < 0) {
    ESP_LOGE(TAG, "No hardware JPEG encoder on %s (%s)", ESP_VIDEO_JPEG_DEVICE_NAME, strerror(errno));
    this->mark_failed(LOG_STR("No hardware JPEG encoder"));
    return;
  }

  struct v4l2_ext_control control[1];
  memset(control, 0, sizeof(control));
  control[0].id = V4L2_CID_JPEG_COMPRESSION_QUALITY;
  control[0].value = this->jpeg_quality_;
  struct v4l2_ext_controls controls;
  memset(&controls, 0, sizeof(controls));
  controls.ctrl_class = V4L2_CID_JPEG_CLASS;
  controls.count = 1;
  controls.controls = control;
  if (ioctl(this->encoder_fd_, VIDIOC_S_EXT_CTRLS, &controls) != 0)
    ESP_LOGW(TAG, "Could not set the JPEG quality to %d (%s)", this->jpeg_quality_, strerror(errno));

  // Worst case for one frame, the same bound Espressif's example uses. A real
  // JPEG is an order of magnitude smaller, but the buffer is allocated once and
  // a frame that overran it would be dropped by usb_device_uvc.
  this->uvc_buffer_size_ = (size_t) this->width_ * this->height_;
  this->uvc_buffer_ = (uint8_t *) heap_caps_malloc(this->uvc_buffer_size_, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (this->uvc_buffer_ == nullptr)
    this->uvc_buffer_ = (uint8_t *) heap_caps_malloc(this->uvc_buffer_size_, MALLOC_CAP_8BIT);
  if (this->uvc_buffer_ == nullptr) {
    ESP_LOGE(TAG, "Could not allocate the %u byte USB transfer buffer", (unsigned) this->uvc_buffer_size_);
    this->mark_failed(LOG_STR("USB transfer buffer allocation failed"));
    return;
  }

  uvc_device_config_t config = {};
  config.uvc_buffer = this->uvc_buffer_;
  config.uvc_buffer_size = this->uvc_buffer_size_;
  config.start_cb = UVCDevice::start_cb;
  config.fb_get_cb = UVCDevice::fb_get_cb;
  config.fb_return_cb = UVCDevice::fb_return_cb;
  config.stop_cb = UVCDevice::stop_cb;
  config.cb_ctx = this;

  esp_err_t err = uvc_device_config(0, &config);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "uvc_device_config() failed: %s", esp_err_to_name(err));
    this->mark_failed(LOG_STR("uvc_device_config failed"));
    return;
  }
  err = uvc_device_init();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "uvc_device_init() failed: %s", esp_err_to_name(err));
    this->mark_failed(LOG_STR("uvc_device_init failed"));
    return;
  }

  ESP_LOGI(TAG, "USB webcam ready: MJPEG %ux%u @ %u fps", (unsigned) this->width_, (unsigned) this->height_,
           (unsigned) this->framerate_);
}

// ===========================================================================
// usb_device_uvc callbacks -- all of these run on its task, not on loop()
// ===========================================================================
esp_err_t UVCDevice::start_cb(uvc_format_t format, int width, int height, int rate, void *ctx) {
  if (format != UVC_FORMAT_JPEG) {
    ESP_LOGE(TAG, "The host asked for a format this component does not produce");
    return ESP_ERR_NOT_SUPPORTED;
  }
  ESP_LOGI(TAG, "Host opened the stream: %dx%d @ %d fps", width, height, rate);
  return static_cast<UVCDevice *>(ctx)->on_start_(width, height);
}

uvc_fb_t *UVCDevice::fb_get_cb(void *ctx) { return static_cast<UVCDevice *>(ctx)->on_fb_get_(); }

void UVCDevice::fb_return_cb(uvc_fb_t *fb, void *ctx) { static_cast<UVCDevice *>(ctx)->on_fb_return_(); }

void UVCDevice::stop_cb(void *ctx) { static_cast<UVCDevice *>(ctx)->on_stop_(); }

esp_err_t UVCDevice::on_start_(int width, int height) {
  // Which raw format the encoder can be fed depends on the sensor and the ISP,
  // so ask the capture device what it offers and take the first one the encoder
  // accepts, rather than assuming RGB565.
  this->capture_format_ = 0;
  for (int index = 0;; index++) {
    struct v4l2_fmtdesc fmtdesc;
    memset(&fmtdesc, 0, sizeof(fmtdesc));
    fmtdesc.index = index;
    fmtdesc.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(this->capture_fd_, VIDIOC_ENUM_FMT, &fmtdesc) != 0)
      break;
    for (uint32_t candidate : JPEG_INPUT_FORMATS) {
      if (candidate == fmtdesc.pixelformat) {
        this->capture_format_ = candidate;
        break;
      }
    }
    if (this->capture_format_ != 0)
      break;
  }
  if (this->capture_format_ == 0) {
    ESP_LOGE(TAG, "The sensor produces nothing the JPEG encoder can take");
    return ESP_ERR_NOT_SUPPORTED;
  }

  // Read what the sensor is actually running at before setting anything. Its
  // size is fixed when the firmware is built, and S_FMT with any other size is
  // rejected with a bare EINVAL that names neither the size asked for nor the
  // one that would work -- which is no help at all to whoever has to fix it.
  struct v4l2_format format;
  memset(&format, 0, sizeof(format));
  format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->capture_fd_, VIDIOC_G_FMT, &format) != 0) {
    ESP_LOGE(TAG, "VIDIOC_G_FMT on the sensor failed: %s", strerror(errno));
    return ESP_FAIL;
  }
  const unsigned sensor_width = format.fmt.pix.width;
  const unsigned sensor_height = format.fmt.pix.height;
  if ((int) sensor_width != width || (int) sensor_height != height) {
    ESP_LOGE(TAG,
             "The sensor is running at %ux%u but the USB descriptor announces %dx%d, so the host would decode a "
             "frame of the wrong shape. Either set resolution: %ux%u here, or build the sensor for %dx%d.",
             sensor_width, sensor_height, width, height, sensor_width, sensor_height, width, height);
    return ESP_ERR_INVALID_SIZE;
  }

  // Only the pixel format is negotiable; keep the size the sensor came up with.
  format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  format.fmt.pix.pixelformat = this->capture_format_;
  if (ioctl(this->capture_fd_, VIDIOC_S_FMT, &format) != 0) {
    ESP_LOGE(TAG, "VIDIOC_S_FMT on the sensor failed: %s", strerror(errno));
    return ESP_FAIL;
  }

  struct v4l2_requestbuffers req;
  memset(&req, 0, sizeof(req));
  req.count = CAPTURE_BUFFER_COUNT;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->capture_fd_, VIDIOC_REQBUFS, &req) != 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS on the sensor failed: %s", strerror(errno));
    return ESP_FAIL;
  }

  for (int i = 0; i < CAPTURE_BUFFER_COUNT; i++) {
    struct v4l2_buffer buf;
    memset(&buf, 0, sizeof(buf));
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = i;
    if (ioctl(this->capture_fd_, VIDIOC_QUERYBUF, &buf) != 0) {
      ESP_LOGE(TAG, "VIDIOC_QUERYBUF[%d] failed: %s", i, strerror(errno));
      this->teardown_();
      this->teardown_();
      return ESP_FAIL;
    }
    this->capture_buffer_[i] =
        (uint8_t *) mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, this->capture_fd_, buf.m.offset);
    if (this->capture_buffer_[i] == MAP_FAILED) {
      this->capture_buffer_[i] = nullptr;
      ESP_LOGE(TAG, "mmap[%d] failed: %s", i, strerror(errno));
      this->teardown_();
      this->teardown_();
      return ESP_FAIL;
    }
    this->capture_buffer_len_[i] = buf.length;
    if (ioctl(this->capture_fd_, VIDIOC_QBUF, &buf) != 0) {
      ESP_LOGE(TAG, "VIDIOC_QBUF[%d] failed: %s", i, strerror(errno));
      this->teardown_();
      this->teardown_();
      return ESP_FAIL;
    }
  }

  // Encoder input: the sensor's buffers are handed over as USERPTR, so nothing
  // is copied between capture and encode.
  memset(&format, 0, sizeof(format));
  format.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
  format.fmt.pix.width = width;
  format.fmt.pix.height = height;
  format.fmt.pix.pixelformat = this->capture_format_;
  if (ioctl(this->encoder_fd_, VIDIOC_S_FMT, &format) != 0) {
    ESP_LOGE(TAG, "VIDIOC_S_FMT on the encoder input failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  memset(&req, 0, sizeof(req));
  req.count = 1;
  req.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
  req.memory = V4L2_MEMORY_USERPTR;
  if (ioctl(this->encoder_fd_, VIDIOC_REQBUFS, &req) != 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS on the encoder input failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }

  // Encoder output: one MMAP buffer holding the finished JPEG.
  memset(&format, 0, sizeof(format));
  format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  format.fmt.pix.width = width;
  format.fmt.pix.height = height;
  format.fmt.pix.pixelformat = V4L2_PIX_FMT_JPEG;
  if (ioctl(this->encoder_fd_, VIDIOC_S_FMT, &format) != 0) {
    ESP_LOGE(TAG, "VIDIOC_S_FMT on the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  memset(&req, 0, sizeof(req));
  req.count = 1;
  req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  req.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->encoder_fd_, VIDIOC_REQBUFS, &req) != 0) {
    ESP_LOGE(TAG, "VIDIOC_REQBUFS on the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }

  struct v4l2_buffer buf;
  memset(&buf, 0, sizeof(buf));
  buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  buf.memory = V4L2_MEMORY_MMAP;
  buf.index = 0;
  if (ioctl(this->encoder_fd_, VIDIOC_QUERYBUF, &buf) != 0) {
    ESP_LOGE(TAG, "VIDIOC_QUERYBUF on the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  this->encoder_buffer_ =
      (uint8_t *) mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, this->encoder_fd_, buf.m.offset);
  if (this->encoder_buffer_ == MAP_FAILED) {
    this->encoder_buffer_ = nullptr;
    ESP_LOGE(TAG, "mmap of the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  this->encoder_buffer_len_ = buf.length;
  if (ioctl(this->encoder_fd_, VIDIOC_QBUF, &buf) != 0) {
    ESP_LOGE(TAG, "VIDIOC_QBUF on the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }

  int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->encoder_fd_, VIDIOC_STREAMON, &type) != 0) {
    ESP_LOGE(TAG, "STREAMON on the encoder output failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
  if (ioctl(this->encoder_fd_, VIDIOC_STREAMON, &type) != 0) {
    ESP_LOGE(TAG, "STREAMON on the encoder input failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }
  type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (ioctl(this->capture_fd_, VIDIOC_STREAMON, &type) != 0) {
    ESP_LOGE(TAG, "STREAMON on the sensor failed: %s", strerror(errno));
    this->teardown_();
    return ESP_FAIL;
  }

  this->frames_ = 0;
  this->bytes_ = 0;
  this->stats_since_ms_ = (uint32_t) (esp_timer_get_time() / 1000);
  this->streaming_ = true;
  return ESP_OK;
}

uvc_fb_t *UVCDevice::on_fb_get_() {
  // usb_device_uvc keeps asking for frames even after start_cb failed, so
  // without this every request would drive a doomed ioctl and log a warning for
  // it, burying the one line that says why the stream never started.
  if (!this->streaming_)
    return nullptr;

  // Dequeue one raw frame, hand it to the encoder as USERPTR, take the JPEG
  // back. The encoder releases its input as part of completing its output, so
  // the input has to be reclaimed after the output, not before -- waiting the
  // other way round deadlocks.
  struct v4l2_buffer cap_buf;
  memset(&cap_buf, 0, sizeof(cap_buf));
  cap_buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  cap_buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->capture_fd_, VIDIOC_DQBUF, &cap_buf) != 0) {
    ESP_LOGW(TAG, "Sensor DQBUF failed: %s", strerror(errno));
    return nullptr;
  }

  struct v4l2_buffer out_buf;
  memset(&out_buf, 0, sizeof(out_buf));
  out_buf.index = 0;
  out_buf.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
  out_buf.memory = V4L2_MEMORY_USERPTR;
  out_buf.m.userptr = (uintptr_t) this->capture_buffer_[cap_buf.index];
  out_buf.length = this->capture_buffer_len_[cap_buf.index];
  out_buf.bytesused = cap_buf.bytesused;
  if (ioctl(this->encoder_fd_, VIDIOC_QBUF, &out_buf) != 0) {
    ESP_LOGW(TAG, "Encoder input QBUF failed: %s", strerror(errno));
    ioctl(this->capture_fd_, VIDIOC_QBUF, &cap_buf);
    return nullptr;
  }

  struct v4l2_buffer enc_buf;
  memset(&enc_buf, 0, sizeof(enc_buf));
  enc_buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  enc_buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->encoder_fd_, VIDIOC_DQBUF, &enc_buf) != 0) {
    ESP_LOGW(TAG, "Encoder output DQBUF failed: %s", strerror(errno));
    ioctl(this->capture_fd_, VIDIOC_QBUF, &cap_buf);
    return nullptr;
  }

  ioctl(this->capture_fd_, VIDIOC_QBUF, &cap_buf);
  ioctl(this->encoder_fd_, VIDIOC_DQBUF, &out_buf);

  // Whether frames leave this board at all is the one thing the host cannot
  // tell us, and it is what separates "the device is not producing" from "the
  // transport is dropping everything".
  this->frames_++;
  this->bytes_ += enc_buf.bytesused;
  if (this->frames_ == 1)
    ESP_LOGI(TAG, "First frame handed to the host: %u bytes", (unsigned) enc_buf.bytesused);
  uint32_t now_ms = (uint32_t) (esp_timer_get_time() / 1000);
  uint32_t elapsed = now_ms - this->stats_since_ms_;
  if (elapsed >= STATS_INTERVAL_MS) {
    ESP_LOGI(TAG, "%ux%u @ %.1f fps, %u B/frame", (unsigned) this->width_, (unsigned) this->height_,
             this->frames_ * 1000.0f / elapsed, (unsigned) (this->bytes_ / this->frames_));
    this->stats_since_ms_ = now_ms;
    this->frames_ = 0;
    this->bytes_ = 0;
  }

  int64_t us = esp_timer_get_time();
  this->fb_.buf = this->encoder_buffer_;
  this->fb_.len = enc_buf.bytesused;
  this->fb_.width = this->width_;
  this->fb_.height = this->height_;
  this->fb_.format = UVC_FORMAT_JPEG;
  this->fb_.timestamp.tv_sec = us / 1000000L;
  this->fb_.timestamp.tv_usec = us % 1000000L;
  return &this->fb_;
}

void UVCDevice::on_fb_return_() {
  struct v4l2_buffer enc_buf;
  memset(&enc_buf, 0, sizeof(enc_buf));
  enc_buf.index = 0;
  enc_buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  enc_buf.memory = V4L2_MEMORY_MMAP;
  if (ioctl(this->encoder_fd_, VIDIOC_QBUF, &enc_buf) != 0)
    ESP_LOGW(TAG, "Encoder output QBUF failed: %s", strerror(errno));
}

void UVCDevice::on_stop_() {
  ESP_LOGI(TAG, "Host closed the stream");
  this->teardown_();
}

// Also called from the failure paths of on_start_(), which can already have
// mapped buffers by the time it gives up, so this has to be safe to call when
// nothing was ever streaming.
void UVCDevice::teardown_() {
  if (this->streaming_) {
    this->streaming_ = false;
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(this->capture_fd_, VIDIOC_STREAMOFF, &type);
    type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
    ioctl(this->encoder_fd_, VIDIOC_STREAMOFF, &type);
    type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(this->encoder_fd_, VIDIOC_STREAMOFF, &type);
  }

  // The next on_start_() maps a fresh set, so release these rather than leaking
  // one mapping per host connect.
  for (int i = 0; i < CAPTURE_BUFFER_COUNT; i++) {
    if (this->capture_buffer_[i] != nullptr) {
      munmap(this->capture_buffer_[i], this->capture_buffer_len_[i]);
      this->capture_buffer_[i] = nullptr;
      this->capture_buffer_len_[i] = 0;
    }
  }
  if (this->encoder_buffer_ != nullptr) {
    munmap(this->encoder_buffer_, this->encoder_buffer_len_);
    this->encoder_buffer_ = nullptr;
    this->encoder_buffer_len_ = 0;
  }
}

void UVCDevice::dump_config() {
  ESP_LOGCONFIG(TAG, "USB Video Class device:");
  ESP_LOGCONFIG(TAG, "  Format: MJPEG %ux%u @ %u fps", (unsigned) this->width_, (unsigned) this->height_,
                (unsigned) this->framerate_);
  ESP_LOGCONFIG(TAG, "  JPEG quality: %d", this->jpeg_quality_);
  ESP_LOGCONFIG(TAG, "  USB transfer buffer: %u bytes", (unsigned) this->uvc_buffer_size_);
  if (this->is_failed())
    ESP_LOGCONFIG(TAG, "  State: FAILED");
}

}  // namespace uvc_device
}  // namespace esphome
