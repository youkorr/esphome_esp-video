#pragma once

#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "esphome/components/i2c/i2c.h"
#include "driver/gpio.h"  // For gpio_num_t

namespace esphome {
namespace esp_video {

/**
 * @brief Composant ESPHome pour ESP-Video d'Espressif
 *
 * Ce composant initialise ESP-Video en appelant esp_video_init()
 * avec init_sccb=false pour utiliser le bus I2C d'ESPHome.
 *
 * Avantage: Pas de conflit de bus, partage propre du bus I2C avec autres composants ESPHome.
 */
class ESPVideoComponent : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  float get_setup_priority() const override {
    // Priorité DATA: s'exécute APRÈS I2C (BUS = 1000)
    // pour que le bus I2C ESPHome soit déjà créé
    return setup_priority::DATA;
  }

  // Setter pour le bus I2C ESPHome
  void set_i2c_bus(i2c::I2CBus *bus) { this->i2c_bus_ = bus; }

  // Setters pour XCLK (requis pour la détection des capteurs MIPI-CSI)
  void set_xclk_pin(gpio_num_t pin) { this->xclk_pin_ = pin; }
  void set_xclk_freq(uint32_t freq) { this->xclk_freq_ = freq; }
  void set_enable_xclk_init(bool enable) { this->enable_xclk_init_ = enable; }
  void set_enable_uvc(bool enable) { this->enable_uvc_ = enable; }

 protected:
  bool initialized_{false};
  i2c::I2CBus *i2c_bus_{nullptr};
  gpio_num_t xclk_pin_{GPIO_NUM_36};     // Default XCLK pin for ESP32-P4
  uint32_t xclk_freq_{24000000};         // Default 24MHz for MIPI-CSI sensors
  bool enable_xclk_init_{false};         // Enable XCLK initialization via LEDC (for non-M5Stack boards)
  bool enable_uvc_{false};               // Start the USB-UVC host (external USB camera)
};

}  // namespace esp_video
}  // namespace esphome
