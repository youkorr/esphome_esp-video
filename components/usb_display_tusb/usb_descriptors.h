#pragma once

#include "tusb.h"
#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The vendor interface stays first: the Microsoft OS 2.0 descriptors name it by
 * number when they ask Windows to bind WinUSB to it, and a PC application finds
 * the bulk endpoints by walking interface 0. Anything added later goes after
 * it. */
enum {
  ITF_NUM_VENDOR = 0,
#if CFG_TUD_HID
  /* Second, the way Espressif order theirs: their display driver expects the
   * touch interface here when it binds the composite product identifier. */
  ITF_NUM_HID,
#endif
#if CFG_TUD_AUDIO
  /* Audio is a function of two interfaces: a control one and a streaming one.
   * Same order as Espressif's, for the same reason as the touch. */
  ITF_NUM_AUDIO_CONTROL,
  ITF_NUM_AUDIO_STREAMING_SPK,
#endif
#if CFG_TUD_MSC
  ITF_NUM_MSC,
#endif
  ITF_NUM_TOTAL,
};

enum {
  EPNUM_DEFAULT = 0,
  EPNUM_VENDOR,
#if CFG_TUD_HID
  EPNUM_HID,
#endif
#if CFG_TUD_AUDIO
  EPNUM_AUDIO_OUT,
  EPNUM_AUDIO_FB,
#endif
#if CFG_TUD_MSC
  EPNUM_MSC,
#endif
};

#if CFG_TUD_HID
enum {
  REPORT_ID_TOUCH = 1,
  REPORT_ID_MAX_COUNT,
};

/* Five contacts, which is what the report descriptor below declares and what
 * the host asks for through the maximum-count feature report. */
#define UDISP_TOUCH_MAX_POINTS 5

/* One contact, in the layout the report descriptor declares. Espressif's, byte
 * for byte, the same way the frame header is: their Windows driver is the other
 * end of it. */
typedef struct {
  uint8_t press_down;
  uint8_t index;
  uint16_t x;
  uint16_t y;
  uint16_t width;
  uint16_t height;
} __attribute__((packed)) udisp_touch_contact_t;

typedef struct {
  udisp_touch_contact_t contacts[UDISP_TOUCH_MAX_POINTS];
  uint8_t count;
} __attribute__((packed)) udisp_touch_report_t;

/* Report descriptor for a five-contact touch screen, from Espressif's
 * usb_extend_screen example (MIT, Jerzy Kasenbreg and Koji KITAYAMA). Copied
 * rather than written because the host end is their driver: the shape of this
 * is what it expects to find.
 *
 * The two arguments are the logical and physical maxima for X and Y, so they go
 * in horizontal then vertical -- the same order as, and confusingly the reverse
 * of the names of, the height and width they pass. */
#define FINGER_USAGE(width, height)                                                                                    \
  HID_USAGE(0x42), HID_COLLECTION(HID_COLLECTION_LOGICAL), HID_USAGE(0x42), HID_LOGICAL_MIN(0x00),                     \
      HID_LOGICAL_MAX(0x01), HID_REPORT_SIZE(1), HID_REPORT_COUNT(1),                                                  \
      HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE), HID_REPORT_COUNT(7),                                          \
      HID_INPUT(HID_CONSTANT | HID_ARRAY | HID_ABSOLUTE), HID_REPORT_SIZE(8), HID_USAGE(0x51), HID_REPORT_COUNT(1),    \
      HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE), HID_USAGE_PAGE(HID_USAGE_PAGE_DESKTOP),                       \
      HID_LOGICAL_MAX_N(width, 2), HID_REPORT_SIZE(16), HID_UNIT_EXPONENT(0x0e), HID_UNIT(0x13), HID_USAGE(0x30),      \
      HID_PHYSICAL_MIN(0), HID_PHYSICAL_MAX_N(width, 2), HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),            \
      HID_LOGICAL_MAX_N(height, 2), HID_PHYSICAL_MAX_N(height, 2), HID_USAGE(0x31),                                    \
      HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE), HID_USAGE_PAGE(HID_USAGE_PAGE_DIGITIZER), HID_USAGE(0x48),    \
      HID_USAGE(0x49), HID_REPORT_COUNT(2), HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE), HID_COLLECTION_END,

#define TUD_HID_REPORT_DESC_TOUCH_SCREEN(report_id, width, height)                                                     \
  HID_USAGE_PAGE(HID_USAGE_PAGE_DIGITIZER), HID_USAGE(0x04), HID_COLLECTION(HID_COLLECTION_APPLICATION),               \
      HID_REPORT_ID(report_id) FINGER_USAGE(width, height) FINGER_USAGE(width, height) FINGER_USAGE(width, height)     \
          FINGER_USAGE(width, height) FINGER_USAGE(width, height) HID_USAGE(0x54),                                     \
      HID_LOGICAL_MAX(127), HID_REPORT_COUNT(1), HID_REPORT_SIZE(8),                                                   \
      HID_INPUT(HID_DATA | HID_VARIABLE | HID_ABSOLUTE), HID_REPORT_ID(report_id + 1) HID_USAGE(0x55),                 \
      HID_REPORT_COUNT(1), HID_LOGICAL_MAX(0x10), HID_FEATURE(HID_DATA | HID_VARIABLE | HID_ABSOLUTE),                 \
      HID_COLLECTION_END
#endif  // CFG_TUD_HID

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
