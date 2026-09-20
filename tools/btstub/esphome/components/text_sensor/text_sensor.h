// Stand-in for ESPHome's text_sensor base -- see the note in switch.h.
#pragma once

#include <string>
#include "esphome/core/component.h"

namespace esphome {
namespace text_sensor {

class TextSensor {
 public:
  void publish_state(const std::string &state) { this->state = state; }
  bool has_state() const { return this->has_state_; }
  std::string state;

 protected:
  bool has_state_{false};
};

}  // namespace text_sensor
}  // namespace esphome

#define LOG_TEXT_SENSOR(prefix, type, obj) (void) (obj)
