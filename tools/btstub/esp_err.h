// Stand-in for ESP-IDF's esp_err.h -- only what portall_bt names.
#pragma once
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
