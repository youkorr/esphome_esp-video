#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include "FreeRTOS.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct QueueStub *QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned len, unsigned size);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);
void vQueueDelete(QueueHandle_t q);
#ifdef __cplusplus
}
#endif
