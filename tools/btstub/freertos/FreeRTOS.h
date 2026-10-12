#pragma once
#include <cstdint>
#define pdPASS 1
#define pdTRUE 1
#define pdFALSE 0
// uint32_t, as on the ESP32 port (portmacro.h: typedef uint32_t TickType_t).
typedef uint32_t TickType_t;
typedef int BaseType_t;
#define portMAX_DELAY ((TickType_t) 0xffffffffUL)
