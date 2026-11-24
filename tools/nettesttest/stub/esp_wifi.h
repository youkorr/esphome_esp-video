#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py):
   the one call facts.cpp makes, as ESP-IDF 6.1.1 declares it. */
typedef int esp_err_t;
#define ESP_OK 0
typedef enum {
  WIFI_PS_NONE,
  WIFI_PS_MIN_MODEM,
  WIFI_PS_MAX_MODEM,
} wifi_ps_type_t;
extern "C" esp_err_t esp_wifi_get_ps(wifi_ps_type_t *type);
