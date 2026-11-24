#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py). */
#include "FreeRTOS.h"
typedef void *TaskHandle_t;
extern "C" BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name, uint32_t stack, void *arg,
                                              unsigned prio, TaskHandle_t *out, int core);
extern "C" void vTaskDelay(TickType_t ticks);
extern "C" void vTaskDelete(TaskHandle_t task);
