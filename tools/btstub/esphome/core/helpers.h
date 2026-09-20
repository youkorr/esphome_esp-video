#pragma once
// Stand-in: only what portall_bt calls. The real one is thousands of lines.
#include <cstdint>
#include <string>
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
