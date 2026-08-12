/* TinyUSB configuration for the USB extended-screen device.
 *
 * The picture goes over a vendor interface, because USB has no "display" class
 * for it to belong to: the other end is an application or Espressif's Windows
 * driver, with no class driver involved. Everything else this board offers the
 * host -- the touch screen, the speaker, the drive carrying the sender -- is a
 * standard class the host already has a driver for, and each is switched on
 * from the component's configuration.
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

/* The speaker, as a USB Audio Class device. Another standard class, so the host
 * needs nothing installed for it either.
 *
 * Espressif's usb_device_uac component supplies the descriptors and the
 * callbacks but not this: the TinyUSB audio function has to be described here,
 * and it is the part a host accepts or rejects the device on. One asynchronous
 * isochronous OUT endpoint with its feedback endpoint, one alternate setting,
 * 16-bit at the configured rate.
 */
#if CONFIG_USB_DISPLAY_AUDIO
#include "uac_config.h"

#define CFG_TUD_AUDIO 1
#define CFG_TUD_AUDIO_FUNC_1_DESC_LEN TUD_AUDIO_DESC_LEN
/* One streaming interface: playback only. The microphone would be a second. */
#define CFG_TUD_AUDIO_FUNC_1_N_AS_INT 1
#define CFG_TUD_AUDIO_FUNC_1_CTRL_BUF_SZ 64

#define CFG_TUD_AUDIO_ENABLE_EP_OUT 1
#define CFG_TUD_AUDIO_FUNC_1_N_CHANNELS_RX CONFIG_UAC_SPEAKER_CHANNEL_NUM
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_N_BYTES_PER_SAMPLE_RX CONFIG_UAC_BYTES_PER_SAMPLE
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_RESOLUTION_RX CONFIG_UAC_BIT_RESOLUTION
#define CFG_TUD_AUDIO_FUNC_1_MAX_SAMPLE_RATE CONFIG_UAC_SAMPLE_RATE

/* One frame of audio, plus one: at full speed the host sends a packet every
 * millisecond and may send one sample more than nominal to keep its own clock
 * in step, and a packet that does not fit is a packet lost. */
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_EP_SZ_OUT                                                                        \
  ((CONFIG_UAC_SAMPLE_RATE / 1000 + 1) * CONFIG_UAC_BYTES_PER_SAMPLE * CONFIG_UAC_SPEAKER_CHANNEL_NUM)
#define CFG_TUD_AUDIO_FUNC_1_EP_OUT_SZ_MAX CFG_TUD_AUDIO_FUNC_1_FORMAT_1_EP_SZ_OUT
/* Room for the interval the component reads at, so a late read does not lose
 * audio the host has already handed over. */
#define CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ (CFG_TUD_AUDIO_FUNC_1_EP_OUT_SZ_MAX * (CONFIG_UAC_SPK_INTERVAL_MS + 2))

/* The OUT endpoint is asynchronous, which is what lets the board run off its
 * own audio clock rather than the host's -- and that is exactly what obliges it
 * to tell the host, on a feedback endpoint, how fast that clock actually runs. */
#define CFG_TUD_AUDIO_ENABLE_FEEDBACK_EP 1
#define CFG_TUD_AUDIO_ENABLE_FEEDBACK_FORMAT_CORRECTION 1

/* One alternate setting carrying one format. */
#define CFG_TUD_AUDIO_FUNC_1_N_FORMATS 1

/* No microphone yet, so no IN endpoint -- but usb_device_uac.c sizes its
 * microphone buffer and builds its resolution table from these whatever the
 * channel count, so they have to exist. Small, because nothing ever fills
 * them. */
#define CFG_TUD_AUDIO_ENABLE_EP_IN 0
#define CFG_TUD_AUDIO_FUNC_1_N_CHANNELS_TX 1
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_N_BYTES_PER_SAMPLE_TX CONFIG_UAC_BYTES_PER_SAMPLE
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_RESOLUTION_TX CONFIG_UAC_BIT_RESOLUTION
#define CFG_TUD_AUDIO_FUNC_1_FORMAT_1_EP_SZ_IN 64
#define CFG_TUD_AUDIO_FUNC_1_EP_IN_SZ_MAX 64
#define CFG_TUD_AUDIO_FUNC_1_EP_IN_SW_BUF_SZ 64

#define CFG_TUD_AUDIO_ENABLE_ENCODING 0
#define CFG_TUD_AUDIO_ENABLE_DECODING 0
#else
#define CFG_TUD_AUDIO 0
#endif

#define CFG_TUD_CDC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VIDEO 0

#ifdef __cplusplus
}
#endif
