#pragma once
#include <cstdint>
#include <cstddef>
namespace esphome {
namespace setup_priority { static const float LATE = 0.0f; }
class Component {
 public:
  virtual void setup() {}
  virtual void loop() {}
  virtual void dump_config() {}
  virtual float get_setup_priority() const { return 0.0f; }
};
}
