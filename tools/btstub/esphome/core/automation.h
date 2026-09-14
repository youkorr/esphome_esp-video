#pragma once
// Stand-in for ESPHome's automation base classes.
//
// `virtual void play(const Ts &...x) = 0` is copied EXACTLY, including the
// const reference, because that signature is the whole point: taking the pack
// by value compiles as a perfectly legal NON-override that is never called.
// portall shipped one once and nothing caught it -- `esphome config` never
// compiles C++. With this stub and `override` on the action, g++ does.
#include "esphome/core/helpers.h"
#include <vector>
namespace esphome {

template<typename... Ts> class Action {
 public:
  virtual ~Action() = default;
  virtual void play(const Ts &...x) = 0;
};

template<typename... Ts> class Trigger {
 public:
  void trigger(Ts... x) { (void) sizeof...(x); }
};

}  // namespace esphome
