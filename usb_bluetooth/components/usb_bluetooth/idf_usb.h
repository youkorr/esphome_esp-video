// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// usb_bluetooth, from Portall: https://github.com/youkorr/esphome_esp-video
#pragma once

/* usb_bluetooth's own USB host layer, on ESP-IDF's USB Host Library.
 *
 * Used instead of CherryUSB when `transport: idf` is set, which defines
 * HCI_USB_IDF. CherryUSB stays the default and nothing here is compiled
 * without that define.
 *
 * WHY IT SPEAKS CHERRYUSB'S NAMES. usb_bluetooth.cpp reaches its USB host
 * through nine calls and a handful of structures, and that code runs on
 * four boards today: the probe, the Realtek download, the inquiry, Bluedroid's
 * transport and both reader tasks. Giving this layer the same nine names and
 * the same structures means none of it had to change to run on top of this one.
 * The proven code is shared rather than copied. What is CherryUSB's here is the
 * shape of a call, never a line of its code.
 *
 * WHAT IS UNDERNEATH. ESP-IDF's own USB Host Library (components/usb in
 * v5.5.5, the version ESPHome builds with). It is part of ESP-IDF, so it adds
 * no dependency: no download, no patch, and no version of somebody else's to
 * pin. It does not come with ESPHome's `usb_host` component either; this
 * installs the library itself, the way esphome/esphome#16944 does.
 *
 * The four things that differ from CherryUSB, and what this layer does about
 * each:
 *
 *   - The library NEVER times a transfer out. `timeout_ms` in usb_transfer_t
 *     says "currently not supported yet". So the wait is ours: a semaphore and
 *     a timeout. An IN transfer that has not finished is left IN FLIGHT, and
 *     the next read on that endpoint goes on waiting for it, so a packet that
 *     arrives just after a timeout is delivered rather than lost. An OUT that
 *     does not finish is cancelled (halt, flush, clear), which is what a
 *     CherryUSB timeout amounts to.
 *   - Callbacks arrive on the client's own task, from
 *     usb_host_client_handle_events(). A task here does nothing but call it,
 *     so a blocked reader can never be what stops its own answer arriving.
 *   - An endpoint that ends a transfer in error is HALTED by the library and
 *     stays so until usb_host_endpoint_clear(). The next transfer on it clears
 *     it first.
 *   - Claiming an interface sends nothing to the device. usbh_set_interface()
 *     claims, then sends SET_INTERFACE itself, which is what CherryUSB's does.
 *
 * Not carried, as with CherryUSB: isochronous transfers (SCO), and a dongle
 * behind a hub. NOTHING HERE HAS RUN ON A BOARD. tools/checkidfusb.py runs it
 * on a workstation, against a stand-in for the library and a simulated
 * dongle, with usb_bluetooth's own probe on top. */

#include <cstddef>
#include <cstdint>

#include "usb/usb_host.h"

// How many interfaces, and endpoints per interface, are kept. A dongle has
// four interfaces at most, and its HCI interface has three endpoints.
#define CONFIG_USBHOST_MAX_INTERFACES 8
#define CONFIG_USBHOST_MAX_ENDPOINTS 4

// CherryUSB's buffers must be uncached and 64-byte aligned because its DMA
// reads them. These are ordinary memory here: every transfer has its own DMA
// buffer from usb_host_transfer_alloc(), and this layer copies in and out of it.
#define USB_NOCACHE_RAM_SECTION
#define USB_MEM_ALIGNX

// Not register addresses, as they are in CherryUSB, but tokens for which
// controller to use. usbh_initialize() turns them into the library's
// peripheral_map. On a P4 bit 1 is the full-speed controller:
// USB_DWC_LL_GET_HW(num) is `((num) == 1) ? &USB_DWC_FS : &USB_DWC_HS` in
// hal/esp32p4/include/hal/usb_dwc_ll.h. 0 is the default, the high-speed one.
#define ESP_USB_HS0_BASE 0u
#define ESP_USB_FS0_BASE 1u

// CherryUSB's error numbers (common/usb_errno.h), returned negative, so the
// log lines that already decode them stay true: -10 a NAK, -14 a timeout,
// -3 a dongle that is no longer there.
#define USB_ERR_NOMEM 1
#define USB_ERR_INVAL 2
#define USB_ERR_NODEV 3
#define USB_ERR_NOTCONN 4
#define USB_ERR_NOTSUPP 5
#define USB_ERR_BUSY 6
#define USB_ERR_RANGE 7
#define USB_ERR_STALL 8
#define USB_ERR_BABBLE 9
#define USB_ERR_NAK 10
#define USB_ERR_DT 11
#define USB_ERR_IO 12
#define USB_ERR_SHUTDOWN 13
#define USB_ERR_TIMEOUT 14

#define USB_REQUEST_DIR_OUT (0U << 7)
#define USB_REQUEST_DIR_IN (1U << 7)
#define USB_REQUEST_STANDARD (0U << 5)
#define USB_REQUEST_CLASS (1U << 5)
#define USB_REQUEST_RECIPIENT_DEVICE (0U << 0)
#define USB_REQUEST_RECIPIENT_INTERFACE (1U << 0)

#define USB_GET_MAXPACKETSIZE(x) ((x) & 0x7ff)
#define USB_GET_ENDPOINT_TYPE(x) ((x) & 0x03)

// USB_SPEED_LOW, _FULL and _HIGH are ESP-IDF's own (usb_speed_t), and
// usbh_hubport::speed holds the library's value, so they are not redefined.

enum {
  USBH_EVENT_DEVICE_CONNECTED = 4,
  USBH_EVENT_DEVICE_DISCONNECTED,
  USBH_EVENT_DEVICE_CONFIGURED,
};

struct usb_setup_packet {
  uint8_t bmRequestType;
  uint8_t bRequest;
  uint16_t wValue;
  uint16_t wIndex;
  uint16_t wLength;
} __attribute__((packed));

struct usb_device_descriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint16_t bcdUSB;
  uint8_t bDeviceClass;
  uint8_t bDeviceSubClass;
  uint8_t bDeviceProtocol;
  uint8_t bMaxPacketSize0;
  uint16_t idVendor;
  uint16_t idProduct;
  uint16_t bcdDevice;
  uint8_t iManufacturer;
  uint8_t iProduct;
  uint8_t iSerialNumber;
  uint8_t bNumConfigurations;
} __attribute__((packed));

struct usb_configuration_descriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint16_t wTotalLength;
  uint8_t bNumInterfaces;
  uint8_t bConfigurationValue;
  uint8_t iConfiguration;
  uint8_t bmAttributes;
  uint8_t bMaxPower;
} __attribute__((packed));

struct usb_interface_descriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint8_t bInterfaceNumber;
  uint8_t bAlternateSetting;
  uint8_t bNumEndpoints;
  uint8_t bInterfaceClass;
  uint8_t bInterfaceSubClass;
  uint8_t bInterfaceProtocol;
  uint8_t iInterface;
} __attribute__((packed));

struct usb_endpoint_descriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint8_t bEndpointAddress;
  uint8_t bmAttributes;
  uint16_t wMaxPacketSize;
  uint8_t bInterval;
} __attribute__((packed));

struct usbh_endpoint {
  struct usb_endpoint_descriptor ep_desc;
};

// Only alternate setting 0 is kept: it is the only one usb_bluetooth reads, and
// a dongle's six SCO settings are why CherryUSB needed patching at all.
struct usbh_interface_altsetting {
  struct usb_interface_descriptor intf_desc;
  struct usbh_endpoint ep[CONFIG_USBHOST_MAX_ENDPOINTS];
};

struct usbh_interface {
  struct usbh_interface_altsetting altsetting[1];
  uint8_t altsetting_num;
};

struct usbh_configuration {
  struct usb_configuration_descriptor config_desc;
  struct usbh_interface intf[CONFIG_USBHOST_MAX_INTERFACES];
};

struct usbh_hubport {
  bool connected;
  uint8_t port;
  uint8_t dev_addr;
  uint8_t speed;  // usb_speed_t, ESP-IDF's values
  struct usb_device_descriptor device_desc;
  struct usbh_configuration config;
  const char *iManufacturer;
  const char *iProduct;
  const char *iSerialNumber;
  struct usb_setup_packet *setup;
};

typedef void (*usbh_complete_callback_t)(void *arg, int nbytes);

// An aggregate on purpose: usb_bluetooth.cpp resets one with `usbh_urb{}`. It
// carries no state of its own between transfers -- the library keeps each
// endpoint's DATA0/DATA1 toggle itself, so the trap CherryUSB's urbs set (a
// fresh urb restarting at DATA0) cannot happen here.
struct usbh_urb {
  struct usbh_hubport *hport;
  struct usb_endpoint_descriptor *ep;
  struct usb_setup_packet *setup;
  uint8_t *transfer_buffer;
  uint32_t transfer_buffer_length;
  int actual_length;
  uint32_t timeout;
  usbh_complete_callback_t complete;
  void *arg;
};

typedef void (*usbh_event_handler_t)(uint8_t busid, uint8_t hub_index, uint8_t hub_port, uint8_t intf,
                                     uint8_t event);

static inline void usbh_int_urb_fill(struct usbh_urb *urb, struct usbh_hubport *hport,
                                     struct usb_endpoint_descriptor *ep, uint8_t *buf, uint32_t len,
                                     uint32_t timeout, usbh_complete_callback_t complete, void *arg) {
  urb->hport = hport;
  urb->ep = ep;
  urb->setup = nullptr;
  urb->transfer_buffer = buf;
  urb->transfer_buffer_length = len;
  urb->actual_length = 0;
  urb->timeout = timeout;
  urb->complete = complete;
  urb->arg = arg;
}

static inline void usbh_bulk_urb_fill(struct usbh_urb *urb, struct usbh_hubport *hport,
                                      struct usb_endpoint_descriptor *ep, uint8_t *buf, uint32_t len,
                                      uint32_t timeout, usbh_complete_callback_t complete, void *arg) {
  usbh_int_urb_fill(urb, hport, ep, buf, len, timeout, complete, arg);
}

/* The nine calls. Same meaning as CherryUSB's, returns >= 0 on success and a
 * negated USB_ERR_* on failure:
 *
 *   usbh_initialize       installs the library on the chosen controller and
 *                         starts its two tasks; `handler` hears
 *                         USBH_EVENT_DEVICE_CONFIGURED once a device has been
 *                         opened and its descriptors read
 *   usbh_find_hubport     the device, or nullptr once it has gone
 *   usbh_set_interface    claims the interface and sends SET_INTERFACE; 8 on
 *                         success, the bytes of a setup packet, as CherryUSB
 *   usbh_control_transfer the setup packet, then `buffer` as the data stage;
 *                         the bytes moved, setup included
 *   usbh_submit_urb       one bulk or interrupt transfer, blocking up to
 *                         urb->timeout milliseconds (0 waits for ever); the
 *                         bytes read land in urb->actual_length */
int usbh_initialize(uint8_t busid, uintptr_t reg_base, usbh_event_handler_t handler);
struct usbh_hubport *usbh_find_hubport(uint8_t busid, uint8_t hub_index, uint8_t hub_port);
int usbh_set_interface(struct usbh_hubport *hport, uint8_t intf, uint8_t altsetting);
int usbh_control_transfer(struct usbh_hubport *hport, struct usb_setup_packet *setup, uint8_t *buffer);
int usbh_submit_urb(struct usbh_urb *urb);
