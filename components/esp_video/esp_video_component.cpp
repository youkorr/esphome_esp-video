#include "esp_video_component.h"
#include "i2c_helper.h"
#include "esphome/core/log.h"
#include "esp_heap_caps.h"
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>

// Headers ESP-Video
extern "C" {
#include "esp_video_init.h"
#include "esp_video_device.h"
#include "driver/ledc.h"  // For XCLK generation via LEDC (like M5Stack does)
#include "freertos/FreeRTOS.h"  // For vTaskDelay
#include "freertos/task.h"      // For pdMS_TO_TICKS and xTaskCreatePinnedToCore
#include "freertos/semphr.h"    // For semaphores (core synchronization)

// Forward declaration for ISP pipeline check
#ifdef ESP_VIDEO_ISP_ENABLED
bool esp_video_isp_pipeline_is_initialized(void);
#endif
}

namespace esphome {
namespace esp_video {

static const char *const TAG = "esp_video";

// Structure for passing parameters to esp_video_init task on core 0
struct esp_video_init_params_t {
  esp_video_init_config_t *video_config;
  esp_err_t result;
  SemaphoreHandle_t done_semaphore;
};

/**
 * @brief Task that runs esp_video_init on core 0
 *
 * CRITICAL: ESP32-P4 hardware peripherals (ISP, MIPI-CSI, camera) must be
 * initialized on core 0. If ESPHome runs on core 1 and calls esp_video_init()
 * from setup(), the camera drivers may not initialize correctly.
 *
 * This task ensures esp_video_init() runs on core 0 regardless of which core
 * ESPHome is running on.
 */
static void esp_video_init_task_core0(void *param) {
  esp_video_init_params_t *params = (esp_video_init_params_t *)param;

  ESP_LOGI(TAG, "📍 esp_video_init() running on core %d", xPortGetCoreID());

  // Call esp_video_init on core 0
  params->result = esp_video_init(params->video_config);

  // Signal completion
  xSemaphoreGive(params->done_semaphore);

  // Delete this task
  vTaskDelete(NULL);
}

/**
 * @brief Initialize camera XCLK using LEDC (like M5Stack Tab5 does)
 *
 * CRITICAL: For MIPI-CSI sensors on ESP32-P4, esp_video_init() does NOT initialize XCLK!
 * XCLK initialization only happens for DVP sensors in esp_video_init.c.
 *
 * For MIPI-CSI, we must initialize XCLK BEFORE calling esp_video_init(), otherwise
 * the sensor will not respond on I2C during detection (PID=0x0).
 *
 * This matches M5Stack's approach in bsp_cam_osc_init() which uses LEDC to generate
 * 24 MHz clock on GPIO 36.
 *
 * @param gpio_num GPIO pin for XCLK output
 * @param freq_hz XCLK frequency in Hz (typically 24000000 for MIPI-CSI sensors)
 * @return ESP_OK on success, error code otherwise
 */
static esp_err_t init_xclk_ledc(gpio_num_t gpio_num, uint32_t freq_hz) {
  ESP_LOGI(TAG, "🔧 Initializing XCLK via LEDC on GPIO%d @ %u Hz", gpio_num, freq_hz);

  // Configure LEDC timer for XCLK generation (matching M5Stack's implementation)
  ledc_timer_config_t timer_conf = {};
  timer_conf.speed_mode = LEDC_LOW_SPEED_MODE;
  timer_conf.timer_num = LEDC_TIMER_0;
  timer_conf.duty_resolution = LEDC_TIMER_1_BIT;  // 1-bit resolution for 50% duty cycle
  timer_conf.freq_hz = freq_hz;
  timer_conf.clk_cfg = LEDC_AUTO_CLK;

  esp_err_t ret = ledc_timer_config(&timer_conf);
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "LEDC timer config failed: %s", esp_err_to_name(ret));
    return ret;
  }

  // Configure LEDC channel to output XCLK on the specified GPIO
  ledc_channel_config_t ch_conf = {};
  ch_conf.speed_mode = LEDC_LOW_SPEED_MODE;
  ch_conf.channel = LEDC_CHANNEL_0;
  ch_conf.timer_sel = LEDC_TIMER_0;
  ch_conf.intr_type = LEDC_INTR_DISABLE;
  ch_conf.gpio_num = gpio_num;
  ch_conf.duty = 1;  // 50% duty cycle (1 out of 2^1 = 2)
  ch_conf.hpoint = 0;

  ret = ledc_channel_config(&ch_conf);
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "LEDC channel config failed: %s", esp_err_to_name(ret));
    return ret;
  }

  ESP_LOGI(TAG, "XCLK initialized successfully via LEDC");
  ESP_LOGI(TAG, "   GPIO%d now outputs %u Hz clock signal", gpio_num, freq_hz);
  ESP_LOGI(TAG, "   Sensor can now respond on I2C during detection");

  return ESP_OK;
}

void ESPVideoComponent::setup() {
  // ESP_LOGI(TAG, "========================================");
  // ESP_LOGI(TAG, "  ESP-Video Component Initialization");
  // ESP_LOGI(TAG, "========================================");

#ifdef ESP_VIDEO_VERSION
  // ESP_LOGI(TAG, "Version: %s (XCLK Support Enabled)", ESP_VIDEO_VERSION);
#else
  // ESP_LOGI(TAG, "Version: 2025-11-08 (XCLK Support Enabled)");
#endif

  // Afficher les fonctionnalités activées
  // ESP_LOGI(TAG, "Fonctionnalités activées:");

#ifdef ESP_VIDEO_JPEG_ENABLED
  // ESP_LOGI(TAG, "  Encodeur JPEG matériel");
#else
  // ESP_LOGI(TAG, "  Encodeur JPEG désactivé");
#endif

#ifdef ESP_VIDEO_ISP_ENABLED
  // ESP_LOGI(TAG, "  Image Signal Processor (ISP)");
#else
  // ESP_LOGI(TAG, "  ISP désactivé");
#endif

#ifdef CONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE
  // ESP_LOGI(TAG, "  Support MIPI-CSI");
#else
  // ESP_LOGW(TAG, "  Support MIPI-CSI désactivé");
#endif

  // Vérification de la mémoire disponible
  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  size_t min_heap = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);

  // ESP_LOGI(TAG, "Mémoire:");
  // ESP_LOGI(TAG, "  Libre actuellement: %u octets", (unsigned)free_heap);
  // ESP_LOGI(TAG, "  Minimum atteint: %u octets", (unsigned)min_heap);

  // Recommandation mémoire
  if (free_heap < 512 * 1024) {
    ESP_LOGW(TAG, "Mémoire faible! Recommandé: > 512 KB");
    // ESP_LOGW(TAG, "    Considérez réduire la résolution ou la qualité");
  }

  // Initialiser ESP-Video
  // ESP_LOGI(TAG, "----------------------------------------");
  // ESP_LOGI(TAG, "Initialisation ESP-Video...");

#ifdef CONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE
  // Vérifier que le bus I2C ESPHome est fourni
  if (this->i2c_bus_ == nullptr) {
    ESP_LOGE(TAG, "Bus I2C non fourni! Vérifiez la configuration i2c_id");
    this->mark_failed();
    return;
  }

  // Extraire le handle I2C ESP-IDF depuis le bus ESPHome
  // ESP_LOGI(TAG, "Configuration esp_video:");
  // ESP_LOGI(TAG, "  init_sccb: false (utilise le bus I2C ESPHome)");
  // ESP_LOGI(TAG, "  Setup priority: DATA (après I2C BUS:1000)");

  i2c_master_bus_handle_t i2c_handle = get_i2c_bus_handle(this->i2c_bus_);
  if (i2c_handle == nullptr) {
    ESP_LOGE(TAG, "Impossible d'extraire le handle I2C ESP-IDF");
    this->mark_failed();
    return;
  }

  // ESP_LOGI(TAG, "  Handle I2C ESP-IDF récupéré: %p", i2c_handle);

  // Initialize XCLK via LEDC if enabled (for non-M5Stack boards)
  // M5Stack Tab5 BSP already initializes XCLK, so this should be disabled for M5Stack
  if (this->enable_xclk_init_ && this->xclk_pin_ != (gpio_num_t)-1) {
    ESP_LOGI(TAG, "🔧 Initializing XCLK for non-M5Stack board (GPIO%d @ %u Hz)",
             this->xclk_pin_, this->xclk_freq_);

    esp_err_t xclk_ret = init_xclk_ledc(this->xclk_pin_, this->xclk_freq_);
    if (xclk_ret != ESP_OK) {
      ESP_LOGE(TAG, "XCLK initialization failed: %s", esp_err_to_name(xclk_ret));
      ESP_LOGE(TAG, "   Sensor detection will fail (Chip ID = 0x0000)");
      this->mark_failed();
      return;
    }

    // Wait for sensor to stabilize with XCLK
    vTaskDelay(pdMS_TO_TICKS(50));  // 50ms delay
  } else if (!this->enable_xclk_init_) {
    ESP_LOGI(TAG, "XCLK init disabled - assuming BSP or hardware provides XCLK");
  }

  // ESP_LOGI(TAG, "");
  // ESP_LOGI(TAG, "========================================");
  // ESP_LOGI(TAG, "  Calling esp_video_init()");
  // ESP_LOGI(TAG, "========================================");

  esp_video_init_csi_config_t csi_config = {};

  // Ne PAS initialiser SCCB - utiliser le bus I2C ESPHome existant
  csi_config.sccb_config.init_sccb = false;

  // Utiliser i2c_handle (union) car init_sccb = false
  csi_config.sccb_config.i2c_handle = i2c_handle;
  csi_config.sccb_config.freq = 400000;  // Fréquence I2C

  csi_config.reset_pin = (gpio_num_t)-1;  // Pas de pin de reset
  csi_config.pwdn_pin = (gpio_num_t)-1;   // Pas de pin de power-down

  // NOTE: xclk_pin and xclk_freq are NOT used by esp_video_init() for MIPI-CSI!
  // XCLK initialization only happens for DVP sensors in esp_video_init.c.
  // For MIPI-CSI, XCLK must be initialized BEFORE calling esp_video_init(),
  // which we did above using init_xclk_ledc().
  // Setting these fields here for documentation/completeness only:
  csi_config.xclk_pin = this->xclk_pin_;      // IGNORED for MIPI-CSI
  csi_config.xclk_freq = this->xclk_freq_;    // IGNORED for MIPI-CSI

  esp_video_init_config_t video_config = {};
  video_config.csi = &csi_config;

#if CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE
  // USB-UVC host: when enabled (enable_uvc: true), also bring up the USB host
  // stack + UVC host driver so an external USB camera shows up as a /dev/videoN
  // device. Allocated on the stack; esp_video_init copies what it needs.
  esp_video_init_usb_uvc_config_t uvc_config = {};
  if (this->enable_uvc_) {
    uvc_config.uvc.uvc_dev_num = 1;           // one USB camera
    uvc_config.uvc.task_stack = 4096;
    uvc_config.uvc.task_priority = 5;
    uvc_config.uvc.task_affinity = -1;        // no core affinity
    uvc_config.usb.init_usb_host_lib = true;  // esp_video owns the USB host lib
    uvc_config.usb.task_stack = 4096;
    uvc_config.usb.task_priority = 5;
    uvc_config.usb.task_affinity = -1;
    video_config.usb_uvc = &uvc_config;
    ESP_LOGI(TAG, "USB-UVC host enabled: external USB cameras will appear as /dev/videoN");
  }
#endif

  // CRITICAL: ESP32-P4 camera hardware must be initialized on core 0
  // If ESPHome runs on core 1, we must create a task on core 0 to call esp_video_init()
  // ESP_LOGI(TAG, "Current core: %d", xPortGetCoreID());
  // ESP_LOGI(TAG, "📍 Forcing esp_video_init() to run on core 0 (hardware requirement)");

  // Create semaphore for task synchronization
  SemaphoreHandle_t done_sem = xSemaphoreCreateBinary();
  if (done_sem == NULL) {
    ESP_LOGE(TAG, "Failed to create semaphore");
    this->mark_failed();
    return;
  }

  // Prepare parameters for core 0 task
  esp_video_init_params_t params = {};
  params.video_config = &video_config;
  params.done_semaphore = done_sem;

  // Create task on core 0
  TaskHandle_t task_handle = NULL;
  BaseType_t task_created = xTaskCreatePinnedToCore(
      esp_video_init_task_core0,  // Task function
      "esp_video_init",            // Task name
      8192,                        // Stack size
      &params,                     // Parameters
      5,                           // Priority
      &task_handle,                // Task handle
      0                            // Core 0 (PRO_CPU)
  );

  if (task_created != pdPASS) {
    ESP_LOGE(TAG, "Failed to create esp_video_init task on core 0");
    vSemaphoreDelete(done_sem);
    this->mark_failed();
    return;
  }

  // ESP_LOGI(TAG, "⏳ Waiting for esp_video_init() to complete on core 0...");

  // Wait for task to complete (max 10 seconds)
  if (xSemaphoreTake(done_sem, pdMS_TO_TICKS(10000)) != pdTRUE) {
    ESP_LOGE(TAG, "esp_video_init() timed out after 10 seconds");
    vSemaphoreDelete(done_sem);
    this->mark_failed();
    return;
  }

  // Clean up semaphore
  vSemaphoreDelete(done_sem);

  // Check result
  esp_err_t ret = params.result;
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "Échec esp_video_init() sur core 0: %d (%s)", ret, esp_err_to_name(ret));
    this->mark_failed();
    return;
  }

  // ESP_LOGI(TAG, "esp_video_init() réussi sur core 0 - Devices vidéo prêts!");

  // Vérifier quels devices vidéo ont été créés
  // NOTE: stat() ne fonctionne pas avec les devices VFS ESP-IDF, utilisons open() à la place
  // ESP_LOGW(TAG, "Vérification des devices vidéo créés (via open test):");

  int fd = open("/dev/video0", O_RDWR);
  if (fd >= 0) {
    ESP_LOGW(TAG, "   /dev/video0 existe et accessible (CSI video device - capteur detecte!)");
    ESP_LOGW(TAG, "      File descriptor: %d", fd);
    close(fd);
  } else {
    ESP_LOGW(TAG, "   /dev/video0 N'EXISTE PAS ou non accessible (errno=%d: %s)", errno, strerror(errno));
    ESP_LOGW(TAG, "      Cela signifie que la detection du capteur a echoue dans esp_video_init()");
  }

  fd = open("/dev/video10", O_RDWR);
  if (fd >= 0) {
    ESP_LOGW(TAG, "   /dev/video10 existe (JPEG encoder)");
    close(fd);
  }

  fd = open("/dev/video20", O_RDWR);
  if (fd >= 0) {
    ESP_LOGW(TAG, "   /dev/video20 existe (ISP device)");
    close(fd);
  }

  // NOTE: Sensor detection is already done by esp_video_init() above.
  // The automatic detection loop correctly identifies SC202CS, OV5647, OV02C10, etc.
  // Manual I2C chip ID verification is not needed and was causing confusing log messages
  // for users with different sensors (was always checking SC202CS registers).
  // If you need to debug sensor detection, enable verbose logging in esp_video_init.c

  // Vérifier si l'ISP pipeline est initialisé
#ifdef ESP_VIDEO_ISP_ENABLED
  bool isp_initialized = esp_video_isp_pipeline_is_initialized();
  // ESP_LOGI(TAG, "ISP Pipeline status: %s", isp_initialized ? "INITIALIZED" : "NOT INITIALIZED");

  if (!isp_initialized) {
    ESP_LOGW(TAG, "ISP Pipeline NOT initialized despite enable_isp: true");
    ESP_LOGW(TAG, "   This means IPA algorithms (AWB, sharpen, etc) are NOT active");
    ESP_LOGW(TAG, "   Image quality will be degraded (blanc->vert, pas net, etc)");
  } else {
    ESP_LOGI(TAG, "ISP Pipeline active - IPA algorithms running");
  }
#else
  // ESP_LOGW(TAG, "ISP not enabled in configuration");
#endif
#else
  // ESP_LOGW(TAG, "MIPI-CSI désactivé - esp_video_init() non appelé");
#endif

  this->initialized_ = true;

  // ESP_LOGI(TAG, "========================================");
  ESP_LOGI(TAG, "esp-video: ok");
  // ESP_LOGI(TAG, "Les devices /dev/video* sont disponibles");
  // ESP_LOGI(TAG, "========================================");
}

void ESPVideoComponent::loop() {
  // Rien à faire dans la boucle principale
}

void ESPVideoComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "ESP-Video Component:");

#ifdef ESP_VIDEO_VERSION
  ESP_LOGCONFIG(TAG, "  Version: %s", ESP_VIDEO_VERSION);
#endif

  ESP_LOGCONFIG(TAG, "  État: %s", this->initialized_ ? "Prêt" : "Non initialisé");
  ESP_LOGCONFIG(TAG, "  I2C: Bus ESPHome partagé (%p)", this->i2c_bus_);

  ESP_LOGCONFIG(TAG, "  Encodeurs:");
#ifdef ESP_VIDEO_JPEG_ENABLED
  ESP_LOGCONFIG(TAG, "    - JPEG (matériel)");
#endif

#ifdef ESP_VIDEO_ISP_ENABLED
  ESP_LOGCONFIG(TAG, "  ISP: Activé");
#endif

  ESP_LOGCONFIG(TAG, "  Interfaces:");
#ifdef CONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE
  ESP_LOGCONFIG(TAG, "    - MIPI-CSI");
#endif

  // Afficher l'utilisation mémoire actuelle
  size_t free_heap = heap_caps_get_free_size(MALLOC_CAP_8BIT);
  ESP_LOGCONFIG(TAG, "  Mémoire libre: %u octets", (unsigned)free_heap);
}

}  // namespace esp_video
}  // namespace esphome
