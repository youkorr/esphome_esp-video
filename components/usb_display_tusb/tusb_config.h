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

/* Which root hub port the device stack runs on, and at what speed.
 *
 * The ESP32-P4's High-Speed controller is root hub port 1; port 0 is the
 * Full-Speed one. Getting this wrong is not a runtime problem: tusb_init()
 * with no arguments expands to a static assertion that fails outright with
 * "CFG_TUSB_RHPORT0_MODE/CFG_TUSB_RHPORT1_MODE must be defined".
 *
 * Same split as Espressif's usb_extend_screen example.
 */
#if CONFIG_USB_DISPLAY_HIGH_SPEED
#define CFG_TUSB_RHPORT1_MODE (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)
#else
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif

#define CFG_TUSB_OS OPT_OS_FREERTOS

/* TinyUSB's FreeRTOS layer picks its critical-section form from this. Without
 * it, it emits the vanilla FreeRTOS taskENTER_CRITICAL() with no argument,
 * while ESP-IDF's multicore RISC-V port wants a mux:
 *
 *   error: too few arguments to function 'vPortEnterCriticalMultiCore'
 *
 * ESP-IDF defines ESP_PLATFORM globally, but not everywhere TinyUSB's headers
 * are reached from, so Espressif's own example pins it here too. */
#ifndef ESP_PLATFORM
#define ESP_PLATFORM 1
#endif

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

/* The read-only drive that carries the PC sender. Mass storage is a class that
 * Windows, Linux and macOS all ship a driver for, so it costs the user nothing
 * -- unlike the display itself, which has no class to belong to. */
#if CONFIG_USB_DISPLAY_SENDER_DRIVE
#define CFG_TUD_MSC 1
#define CFG_TUD_MSC_EP_BUFSIZE UDISP_EP_SIZE
#else
#define CFG_TUD_MSC 0
#endif

/* The touch screen. HID is another class every operating system has a driver
 * for, so this needs no installing either. */
#if CONFIG_USB_DISPLAY_TOUCH
#define CFG_TUD_HID 1
#define CFG_TUD_HID_EP_BUFSIZE UDISP_EP_SIZE
#else
#define CFG_TUD_HID 0
#endif

#define CFG_TUD_CDC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_AUDIO 0
#define CFG_TUD_VIDEO 0

#ifdef __cplusplus
}
#endif
