#pragma once
// Stand-in, copied field for field from ESP-IDF v5.5.4
// components/bt/host/bluedroid/api/include/api/esp_bt_defs.h -- only what
// portall_bt names.
#include <cstdint>

#define ESP_BD_ADDR_LEN 6
typedef uint8_t esp_bd_addr_t[ESP_BD_ADDR_LEN];

typedef enum {
  ESP_BT_STATUS_SUCCESS = 0,
  ESP_BT_STATUS_FAIL,
} esp_bt_status_t;
