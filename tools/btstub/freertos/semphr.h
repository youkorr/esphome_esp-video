#pragma once
// Stand-in for FreeRTOS's semphr.h -- the binary semaphore and the mutex
// portall_bt's own USB layer waits on.
#include "freertos/FreeRTOS.h"
typedef struct SemaphoreStandIn *SemaphoreHandle_t;
SemaphoreHandle_t xSemaphoreCreateBinary(void);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreGive(SemaphoreHandle_t sem);
BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks);
