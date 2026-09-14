#pragma once
#include <cstdio>
#define ESP_LOGE(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGW(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGI(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGD(tag, ...) printf(__VA_ARGS__)
#define ESP_LOGCONFIG(tag, ...) printf(__VA_ARGS__)
