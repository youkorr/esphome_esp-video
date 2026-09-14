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
}  // namespace esphome
