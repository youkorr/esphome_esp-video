#pragma once
// Stand-in: only what portall_bt calls. The real one is thousands of lines.
#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>
namespace esphome {
uint32_t fnv1_hash(const char *str);
template<typename T> class Parented {
 public:
  Parented() = default;
  T *get_parent() const { return parent_; }
  void set_parent(T *parent) { parent_ = parent; }
 protected:
  T *parent_{nullptr};
};
/* Enough CallbackManager for a speaker's audio output callback. The real one
 * (core/helpers.h, 2026.8.2) is a trivially-copyable container of function
 * pointers; what matters to the code under test is only that add() keeps a
 * callable and operator() calls every one of them, in order. */
template<typename... X> class CallbackManager;
template<typename... Ts> class CallbackManager<void(Ts...)> {
 public:
  template<typename F> void add(F &&callback) { this->callbacks_.emplace_back(std::forward<F>(callback)); }
  void call(Ts... args) {
    for (auto &callback : this->callbacks_)
      callback(args...);
  }
  void operator()(Ts... args) { this->call(args...); }

 private:
  std::vector<std::function<void(Ts...)>> callbacks_;
};
// Enough optional<> for the switch platform's restore path.
template<typename T> class optional {
 public:
  optional() = default;
  optional(T value) : value_(value), set_(true) {}
  bool has_value() const { return set_; }
  T value_or(T fallback) const { return set_ ? value_ : fallback; }

 private:
  T value_{};
  bool set_{false};
};
}  // namespace esphome
