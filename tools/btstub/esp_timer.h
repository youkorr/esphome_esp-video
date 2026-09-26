#pragma once
// Stand-in for esp_timer.h: the one call portall_bt's speaker makes, the
// microsecond clock ESPHome speakers stamp their played-frame reports with.
#include <cstdint>
int64_t esp_timer_get_time(void);
