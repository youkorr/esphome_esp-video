#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include "FreeRTOS.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef void *TaskHandle_t;
BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name, uint32_t stack, void *arg, unsigned prio, TaskHandle_t *out, int core);
void vTaskDelay(TickType_t ticks);
#ifdef __cplusplus
}
#endif
