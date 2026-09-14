#pragma once
#include <cstdio>
#define ESP_LOGE(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGW(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGI(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGD(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGCONFIG(tag, ...) printf(__VA_ARGS__)

// ESP-IDF stand-ins used by the Bluedroid probe.
typedef int esp_err_t;
#define ESP_OK 0
