#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py):
   the two names nettest.cpp uses, as ESPHome declares them
   (esphome/components/wifi/wifi_component.h). */
#include <cstdint>
namespace esphome::wifi {
class WiFiComponent {
 public:
  int8_t wifi_rssi();
};
extern WiFiComponent *global_wifi_component;  // NOLINT
}  // namespace esphome::wifi
