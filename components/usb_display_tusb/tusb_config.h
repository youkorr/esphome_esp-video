/* TinyUSB configuration for the USB extended-screen device.
 *
 * Vendor class only: USB has no standard "display" class, so the PC side is an
 * application talking to a vendor interface (see Espressif's usb_extend_screen
 * example and its Windows driver). Touch (HID) and audio (UAC) are not wired
 * up here.
 */
#pragma once

#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_TUSB_OS OPT_OS_FREERTOS
/* ESP-IDF wants the "freertos/" prefix on its includes. */
#if TU_CHECK_MCU(OPT_MCU_ESP32S2, OPT_MCU_ESP32S3, OPT_MCU_ESP32P4)
#define CFG_TUSB_OS_INC_PATH freertos/
#endif

#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG 0
#endif

#define CFG_TUD_ENABLED 1

#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif
#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#endif

#define CFG_TUD_ENDPOINT0_SIZE 64

/* 512 at High Speed, 64 at Full Speed -- the maximum a bulk endpoint may
 * declare at each speed. */
#if CONFIG_USB_DISPLAY_HIGH_SPEED
#define UDISP_EP_SIZE 512
#else
#define UDISP_EP_SIZE 64
#endif

#define CFG_TUD_VENDOR 1
#define CFG_TUD_VENDOR_EPSIZE UDISP_EP_SIZE
/* Ten packets of slack: the PC pushes a whole frame in a burst and the receive
 * callback drains it into the frame queue. */
#define CFG_TUD_VENDOR_RX_BUFSIZE (UDISP_EP_SIZE * 10)
#define CFG_TUD_VENDOR_TX_BUFSIZE UDISP_EP_SIZE

#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_AUDIO 0
#define CFG_TUD_VIDEO 0

#ifdef __cplusplus
}
#endif
