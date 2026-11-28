#pragma once
#include "esphome/core/component.h"
#include <string>

namespace esphome::portall_speedtest {
class SpeedTest : public Component {
 public:
  void set_port(uint16_t port) { port_ = port; }
  void set_key(const std::string &key) { key_ = key; }
  void loop() override;
  float get_setup_priority() const override { return setup_priority::LATE; }
  void dump_config() override;
 protected:
  static void task(void *arg);
  void serve();
  bool started_{false};
  uint16_t port_{5201};
  std::string key_;
};
}  // namespace esphome::portall_speedtest
