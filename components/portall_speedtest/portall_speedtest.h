#pragma once
#include "esphome/core/component.h"
#include <string>

namespace esphome::portall_speedtest {
class SpeedTest : public Component {
 public:
  void set_port(uint16_t port) { port_ = port; }
  void set_key(const std::string &key) { key_ = key; }
  void setup() override;
  void dump_config() override;
 protected:
  static void task(void *arg);
  void serve();
  uint16_t port_{5201};
  std::string key_;
};
}  // namespace esphome::portall_speedtest
