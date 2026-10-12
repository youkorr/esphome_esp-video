#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include <cstddef>
#ifdef __cplusplus
extern "C" {
#endif
typedef int esp_err_t;
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 1
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 2
#define ESP_CACHE_MSYNC_FLAG_UNALIGNED 4
#define ESP_CACHE_MSYNC_FLAG_TYPE_DATA 8
esp_err_t esp_cache_msync(void *, size_t, int);
#ifdef __cplusplus
}
#endif
