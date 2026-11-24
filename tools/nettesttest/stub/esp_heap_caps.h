#pragma once
/* Stand-in for a workstation build of the nettest bench (tools/checknettest.py). */
#include <cstddef>
#define MALLOC_CAP_INTERNAL (1 << 11)
extern "C" size_t heap_caps_get_free_size(unsigned caps);
extern "C" size_t heap_caps_get_minimum_free_size(unsigned caps);
