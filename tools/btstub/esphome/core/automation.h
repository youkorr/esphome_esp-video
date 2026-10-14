#pragma once
// Stand-in for ESPHome's automation base classes.
//
// `virtual void play(const Ts &...x) = 0` is copied EXACTLY, including the
// const reference, because that signature is the whole point: taking the pack
// by value compiles as a perfectly legal NON-override that is never called.
// portall shipped one once and nothing caught it -- `esphome config` never
// compiles C++. With this stub and `override` on the action, g++ does.
#include "esphome/core/helpers.h"
#include <functional>
#include <vector>
namespace esphome {

template<typename... Ts> class Action {
 public:
  virtual ~Action() = default;
  virtual void play(const Ts &...x) = 0;
};

template<typename... Ts> class Trigger {
 public:
  /// What a test watches: ESPHome's own Trigger runs the YAML's automation
  /// here, and a test that wants to know what reached the YAML sets this.
  /// Nothing in a component ever does.
  std::function<void(Ts...)> observer;
  void trigger(Ts... x) {
    if (this->observer)
      this->observer(x...);
  }
};

}  // namespace esphome
