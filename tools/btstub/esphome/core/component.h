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
// Only what the text_sensor platform needs: something to derive from that
// carries an update() the tool can see compiled.
class PollingComponent : public Component {
 public:
  PollingComponent() = default;
  explicit PollingComponent(uint32_t interval) : interval_(interval) {}
  virtual void update() {}
  void set_update_interval(uint32_t interval) { this->interval_ = interval; }
  uint32_t get_update_interval() const { return this->interval_; }

 protected:
  uint32_t interval_{0};
};
}
