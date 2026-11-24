#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py).
   The call as esp-hosted 3.1.0 declares it (host/compat/include/esp_hosted.h),
   the fields as ESPHome's esp32_hosted update entity reads them. */
#include <cstdint>
typedef struct {
  uint32_t major1;
  uint32_t minor1;
  uint32_t patch1;
} esp_hosted_coprocessor_fwver_t;
extern "C" int esp_hosted_get_coprocessor_fwversion(esp_hosted_coprocessor_fwver_t *ver_info);
