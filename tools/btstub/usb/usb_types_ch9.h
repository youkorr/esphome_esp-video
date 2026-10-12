// Stand-in for ESP-IDF v5.5.5 components/usb/include/usb/usb_types_ch9.h
// (Apache-2.0, Espressif): the declarations portall_bt's own USB layer uses,
// copied as they are there, so that a member that does not exist or a typo in
// a name is caught on a workstation. Not part of any firmware.
#pragma once
#include <stdint.h>

#define USB_DESC_ATTR __attribute__((packed))

#define USB_B_DESCRIPTOR_TYPE_DEVICE 0x01
#define USB_B_DESCRIPTOR_TYPE_CONFIGURATION 0x02
#define USB_B_DESCRIPTOR_TYPE_STRING 0x03
#define USB_B_DESCRIPTOR_TYPE_INTERFACE 0x04
#define USB_B_DESCRIPTOR_TYPE_ENDPOINT 0x05

#define USB_SETUP_PACKET_SIZE 8
typedef union {
  struct {
    uint8_t bmRequestType;
    uint8_t bRequest;
    uint16_t wValue;
    uint16_t wIndex;
    uint16_t wLength;
  } USB_DESC_ATTR;
  uint8_t val[USB_SETUP_PACKET_SIZE];
} usb_setup_packet_t;

#define USB_B_REQUEST_SET_INTERFACE 0x0B

#define USB_STANDARD_DESC_SIZE 2
typedef union {
  struct {
    uint8_t bLength;
    uint8_t bDescriptorType;
  } USB_DESC_ATTR;
  uint8_t val[USB_STANDARD_DESC_SIZE];
} usb_standard_desc_t;

#define USB_DEVICE_DESC_SIZE 18
typedef union {
  struct {
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
  } USB_DESC_ATTR;
  uint8_t val[USB_DEVICE_DESC_SIZE];
} usb_device_desc_t;

#define USB_CONFIG_DESC_SIZE 9
typedef union {
  struct {
    uint8_t bLength;
    uint8_t bDescriptorType;
    uint16_t wTotalLength;
    uint8_t bNumInterfaces;
    uint8_t bConfigurationValue;
    uint8_t iConfiguration;
    uint8_t bmAttributes;
    uint8_t bMaxPower;
  } USB_DESC_ATTR;
  uint8_t val[USB_CONFIG_DESC_SIZE];
} usb_config_desc_t;

#define USB_INTF_DESC_SIZE 9
typedef union {
  struct {
    uint8_t bLength;
    uint8_t bDescriptorType;
    uint8_t bInterfaceNumber;
    uint8_t bAlternateSetting;
    uint8_t bNumEndpoints;
    uint8_t bInterfaceClass;
    uint8_t bInterfaceSubClass;
    uint8_t bInterfaceProtocol;
    uint8_t iInterface;
  } USB_DESC_ATTR;
  uint8_t val[USB_INTF_DESC_SIZE];
} usb_intf_desc_t;

#define USB_EP_DESC_SIZE 7
typedef union {
  struct {
    uint8_t bLength;
    uint8_t bDescriptorType;
    uint8_t bEndpointAddress;
    uint8_t bmAttributes;
    uint16_t wMaxPacketSize;
    uint8_t bInterval;
  } USB_DESC_ATTR;
  uint8_t val[USB_EP_DESC_SIZE];
} usb_ep_desc_t;

#define USB_STR_DESC_SIZE 2
typedef union {
  struct {
    uint8_t bLength;
    uint8_t bDescriptorType;
    uint16_t wData[];
  } USB_DESC_ATTR;
  uint8_t val[USB_STR_DESC_SIZE];
} usb_str_desc_t;
