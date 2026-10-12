// Stand-in for ESP-IDF v5.5.5 components/usb/include/usb/usb_types_stack.h
// (Apache-2.0, Espressif), copied as it is there. See usb_types_ch9.h.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "usb/usb_types_ch9.h"

typedef enum {
  USB_SPEED_LOW = 0,
  USB_SPEED_FULL,
  USB_SPEED_HIGH,
} usb_speed_t;

typedef enum {
  USB_TRANSFER_TYPE_CTRL = 0,
  USB_TRANSFER_TYPE_ISOCHRONOUS,
  USB_TRANSFER_TYPE_BULK,
  USB_TRANSFER_TYPE_INTR,
} usb_transfer_type_t;

typedef struct usb_device_handle_s *usb_device_handle_t;
typedef bool (*usb_host_enum_filter_cb_t)(const usb_device_desc_t *dev_desc, uint8_t *bConfigurationValue);

typedef struct {
  usb_device_handle_t dev_hdl;
  uint8_t port_num;
} usb_parent_dev_info_t;

typedef struct {
  usb_parent_dev_info_t parent;
  usb_speed_t speed;
  uint8_t dev_addr;
  uint8_t bMaxPacketSize0;
  uint8_t bConfigurationValue;
  const usb_str_desc_t *str_desc_manufacturer;
  const usb_str_desc_t *str_desc_product;
  const usb_str_desc_t *str_desc_serial_num;
} usb_device_info_t;

typedef enum {
  USB_TRANSFER_STATUS_COMPLETED,
  USB_TRANSFER_STATUS_ERROR,
  USB_TRANSFER_STATUS_TIMED_OUT,
  USB_TRANSFER_STATUS_CANCELED,
  USB_TRANSFER_STATUS_STALL,
  USB_TRANSFER_STATUS_OVERFLOW,
  USB_TRANSFER_STATUS_SKIPPED,
  USB_TRANSFER_STATUS_NO_DEVICE,
} usb_transfer_status_t;

typedef struct {
  int num_bytes;
  int actual_num_bytes;
  usb_transfer_status_t status;
} usb_isoc_packet_desc_t;

typedef struct usb_transfer_s usb_transfer_t;
typedef void (*usb_transfer_cb_t)(usb_transfer_t *transfer);

struct usb_transfer_s {
  uint8_t *const data_buffer;
  const size_t data_buffer_size;
  int num_bytes;
  int actual_num_bytes;
  uint32_t flags;
  usb_device_handle_t device_handle;
  uint8_t bEndpointAddress;
  usb_transfer_status_t status;
  uint32_t timeout_ms;
  usb_transfer_cb_t callback;
  void *context;
  const int num_isoc_packets;
  usb_isoc_packet_desc_t isoc_packet_desc[];
};

#define USB_TRANSFER_FLAG_ZERO_PACK 0x01
