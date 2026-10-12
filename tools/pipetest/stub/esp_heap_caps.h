#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include <cstddef>
#ifdef __cplusplus
extern "C" {
#endif
#define MALLOC_CAP_SPIRAM 1
size_t heap_caps_get_free_size(unsigned);
void *heap_caps_aligned_alloc(size_t, size_t, unsigned);
void heap_caps_free(void *);
#ifdef __cplusplus
}
#endif
