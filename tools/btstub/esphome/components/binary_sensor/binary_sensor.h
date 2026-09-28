// Stand-in for ESPHome's binary_sensor base, the calls the platform makes:
// publish_state and publish_initial_state (binary_sensor.h, 2026.10.0-dev).
#pragma once
#include <vector>
#include "esphome/core/component.h"
namespace esphome {
namespace binary_sensor {
class BinarySensor {
 public:
  void publish_state(bool s) { this->state = s; this->published.push_back(s); }
  void publish_initial_state(bool s) { this->state = s; }
  bool state{false};
  std::vector<bool> published;
};
}  // namespace binary_sensor
}  // namespace esphome
#define LOG_BINARY_SENSOR(prefix, type, obj) (void) (obj)
