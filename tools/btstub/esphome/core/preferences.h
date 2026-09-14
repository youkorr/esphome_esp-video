#pragma once
// Stand-in for ESPHome's preferences.
//
// This is where the memory of WHICH bonded address plays which part lives --
// not the pairing itself, which Bluedroid keeps in NVS by itself. The two
// calls used are load() and save(); make_preference is a template on the real
// one too, and getting its shape wrong here would be exactly the class of
// fault this whole tool exists for.
#include <cstdint>
#include <cstring>
namespace esphome {

class ESPPreferenceObject {
 public:
  template<typename T> bool load(T *src) {
    (void) src;
    return false;
  }
  template<typename T> bool save(const T *src) {
    (void) src;
    return true;
  }
};

class ESPPreferences {
 public:
  template<typename T> ESPPreferenceObject make_preference(uint32_t type, bool in_flash = false) {
    (void) type;
    (void) in_flash;
    return {};
  }
  bool sync() { return true; }
};

extern ESPPreferences *global_preferences;

}  // namespace esphome
