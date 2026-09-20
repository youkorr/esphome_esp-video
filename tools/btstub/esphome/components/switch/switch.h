// Stand-in for ESPHome's switch base, copied field for field from 2026.8.2 --
// only what portall_bt's switch platform touches. It says what these are
// called and what they take; it does not say an ESP-IDF build will accept
// them. See CLAUDE.md on what these stubs are and are not.
#pragma once

#include <string>
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

namespace esphome {
namespace switch_ {

class Switch {
 public:
  void publish_state(bool state) { this->state = state; }
  bool state{false};
  optional<bool> get_initial_state_with_restore_mode() { return optional<bool>(); }

 protected:
  virtual void write_state(bool state) = 0;
};

}  // namespace switch_
}  // namespace esphome

#define LOG_SWITCH(prefix, type, obj) (void) (obj)
