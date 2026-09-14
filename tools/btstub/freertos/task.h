#pragma once
#include <cstdint>
typedef void *TaskHandle_t;
int xTaskCreate(void (*fn)(void *), const char *name, unsigned stack, void *arg, unsigned prio, TaskHandle_t *out);
void vTaskDelete(TaskHandle_t h);
