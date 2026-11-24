#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py). */
#include <cstdint>
typedef int BaseType_t;
typedef uint32_t TickType_t;
#define pdMS_TO_TICKS(ms) ((TickType_t) (ms))
