#pragma once

#include "tusb.h"
#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

enum {
  ITF_NUM_VENDOR = 0,
  ITF_NUM_TOTAL,
};

enum {
  EPNUM_DEFAULT = 0,
  EPNUM_VENDOR,
};

/* Packet header the PC application puts in front of every frame. Layout is
 * Espressif's udisp protocol, byte for byte -- their Windows driver is the
 * other end, so it is not ours to change. */
typedef struct {
  uint16_t crc16;
  uint8_t type;
  uint8_t cmd;
  uint16_t x;
  uint16_t y;
  uint16_t width;
  uint16_t height;
  uint32_t frame_id : 10;
  uint32_t payload_total : 22;
} __attribute__((packed)) udisp_frame_header_t;

#define UDISP_TYPE_RGB565 0
#define UDISP_TYPE_RGB888 1
#define UDISP_TYPE_YUV420 2
#define UDISP_TYPE_JPG 3
#define UDISP_TYPE_END 0xff

#ifdef __cplusplus
}
#endif
