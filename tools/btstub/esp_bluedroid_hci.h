// Stand-in for ESP-IDF components/bt/host/bluedroid/api/include/api/esp_bluedroid_hci.h
//
// This is the whole specification of the glue portall_bt writes: three
// function pointers a host-only Bluedroid calls instead of a controller.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct esp_bluedroid_hci_driver_callbacks {
  void (*notify_host_send_available)(void);
  int (*notify_host_recv)(uint8_t *data, uint16_t len);
} esp_bluedroid_hci_driver_callbacks_t;

typedef struct esp_bluedroid_hci_driver_operations {
  void (*send)(uint8_t *data, uint16_t len);
  bool (*check_send_available)(void);
  esp_err_t (*register_host_callback)(const esp_bluedroid_hci_driver_callbacks_t *callback);
} esp_bluedroid_hci_driver_operations_t;

esp_err_t esp_bluedroid_attach_hci_driver(const esp_bluedroid_hci_driver_operations_t *ops);
esp_err_t esp_bluedroid_detach_hci_driver(void);
