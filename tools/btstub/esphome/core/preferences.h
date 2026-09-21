#pragma once
// Stand-in for ESPHome's preferences.
//
// This is where the memory of WHICH bonded address plays which part lives --
// not the pairing itself, which Bluedroid keeps in NVS by itself. The two
// calls used are load() and save(); make_preference is a template on the real
// one too, and getting its shape wrong here would be exactly the class of
// fault this whole tool exists for.
//
// IT REALLY STORES, because the thing most worth testing here is what happens
// to a panel that ALREADY HAS a record -- an upgrade, which is the one moment
// a household can silently lose what it paired. A stub whose load() always
// said "nothing saved" could only ever exercise a fresh board.
//
// And it is keyed by the hash AND the SIZE, which is what the real one does.
// That is not a detail: it is the whole reason `Remembered` may not grow a
// field, and a stub that ignored it would let a change through here that
// would make every paired panel forget its speaker.
#include <cstdint>
#include <cstring>
#include <map>
#include <vector>

namespace esphome {

class ESPPreferences;

class ESPPreferenceObject {
 public:
  ESPPreferenceObject() = default;
  ESPPreferenceObject(ESPPreferences *owner, uint64_t key) : owner_(owner), key_(key) {}
  template<typename T> bool load(T *dest);
  template<typename T> bool save(const T *src);

 protected:
  ESPPreferences *owner_{nullptr};
  uint64_t key_{0};
};

class ESPPreferences {
 public:
  template<typename T> ESPPreferenceObject make_preference(uint32_t type, bool in_flash = false) {
    (void) in_flash;
    return ESPPreferenceObject(this, ((uint64_t) type << 32) | (uint64_t) sizeof(T));
  }
  bool sync() { return true; }
  /// Start from an empty flash, which is what a test wants between cases.
  void wipe() { this->store.clear(); }
  std::map<uint64_t, std::vector<uint8_t>> store;
};

template<typename T> bool ESPPreferenceObject::load(T *dest) {
  if (this->owner_ == nullptr)
    return false;
  auto it = this->owner_->store.find(this->key_);
  if (it == this->owner_->store.end() || it->second.size() != sizeof(T))
    return false;
  memcpy(dest, it->second.data(), sizeof(T));
  return true;
}

template<typename T> bool ESPPreferenceObject::save(const T *src) {
  if (this->owner_ == nullptr)
    return false;
  const uint8_t *bytes = (const uint8_t *) src;
  this->owner_->store[this->key_].assign(bytes, bytes + sizeof(T));
  return true;
}

extern ESPPreferences *global_preferences;

}  // namespace esphome
