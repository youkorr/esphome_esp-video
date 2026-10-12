#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include <cstdint>
typedef uint32_t TickType_t;
typedef int BaseType_t;
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY 0xffffffffu
#define pdMS_TO_TICKS(ms) (ms)
typedef struct { int owner; int count; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED {0, 0}
#define portENTER_CRITICAL(m) (void) (m)
#define portEXIT_CRITICAL(m) (void) (m)
