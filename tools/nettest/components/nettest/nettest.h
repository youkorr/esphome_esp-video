// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// A network test bench for an ESP32-P4 and its ESP32-C6, nothing else on the
// board: three plain socket servers (see servers.h) that tools/nettest/
// nettest.py talks to from a computer, and in every answer what this build
// was compiled with (facts.h), so two builds cannot be confused.
#pragma once

#include "esphome/core/component.h"

namespace esphome::nettest {

class NetTest : public Component {
 public:
  void set_port(uint16_t port) { this->port_ = port; }
  void set_label(const char *label);

  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

 protected:
  uint16_t port_{5201};
  uint32_t last_heap_log_{0};
  uint32_t last_rssi_{0};
};

}  // namespace esphome::nettest
