// Stand-in for ESP-IDF v5.5.5 components/usb/include/usb/usb_helpers.h
// (Apache-2.0, Espressif): the one parser portall_bt's own USB layer uses.
#pragma once
#include <stdint.h>
#include "usb/usb_types_ch9.h"
#ifdef __cplusplus
extern "C" {
#endif
const usb_standard_desc_t *usb_parse_next_descriptor(const usb_standard_desc_t *cur_desc, uint16_t wTotalLength,
                                                     int *offset);
#ifdef __cplusplus
}
#endif
