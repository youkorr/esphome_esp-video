/* The few pieces of ESPHome's core that portall.cpp reaches, for a
 * workstation build (tools/checkpipeline.py). ESPHome's own component.cpp
 * drags in the whole Application and its scheduler, none of which a panel
 * that is set up once and then left to its two tasks ever touches. The
 * Mutex is ESPHome's own host one (components/host/helpers.cpp). */
#include "esphome/core/component.h"
#include "esphome/core/color.h"
#include "esphome/core/helpers.h"
#include "esphome/core/time.h"

#include <cstdio>
#include <mutex>

namespace esphome {

void Component::setup() {}
void Component::loop() {}
void Component::dump_config() {}
float Component::get_setup_priority() const { return 0.0f; }
bool Component::can_proceed() { return true; }
void Component::call_setup() { this->setup(); }
void Component::mark_failed() { printf("  (the component marked itself failed)\n"); }
void Component::status_set_error(const LogString *) {}
PollingComponent::PollingComponent(uint32_t update_interval) : update_interval_(update_interval) {}
void PollingComponent::call_setup() { this->setup(); }
uint32_t PollingComponent::get_update_interval() const { return this->update_interval_; }
void PollingComponent::stop_poller() {}

Mutex::Mutex() { handle_ = new std::mutex(); }
Mutex::~Mutex() { delete static_cast<std::mutex *>(handle_); }
void Mutex::lock() { static_cast<std::mutex *>(handle_)->lock(); }
bool Mutex::try_lock() { return static_cast<std::mutex *>(handle_)->try_lock(); }
void Mutex::unlock() { static_cast<std::mutex *>(handle_)->unlock(); }

Color Color::fade_to_black(uint8_t) { return *this; }
Color Color::fade_to_white(uint8_t) { return *this; }
size_t ESPTime::strftime(char *buffer, size_t len, const char *) {
  if (len > 0)
    buffer[0] = 0;
  return 0;
}

}  // namespace esphome
