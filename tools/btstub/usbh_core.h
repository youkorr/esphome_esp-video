#pragma once
#include <cstdint>
#include <cstddef>
/* A faithful-enough stand-in for CherryUSB v1.6.1, copied field for field from
   core/usbh_core.h, common/usb_def.h and osal/idf/usb_config.h so that a plain
   g++ can find the typos this project keeps paying for. It is a harness, not
   an implementation. */
#define CONFIG_USBHOST_MAX_INTERFACES 8
#define CONFIG_USBHOST_MAX_INTF_ALTSETTINGS 8
#define CONFIG_USBHOST_MAX_ENDPOINTS 4
#define CONFIG_USBHOST_DEV_NAMELEN 16
#define USB_NOCACHE_RAM_SECTION
#define USB_MEM_ALIGNX
#define ESP_USB_HS0_BASE 0x50000000
#define ESP_USB_FS0_BASE 0x50040000
#define USB_SPEED_LOW 1
#define USB_SPEED_FULL 2
#define USB_SPEED_HIGH 3
#define USB_REQUEST_DIR_OUT (0U << 7)
#define USB_REQUEST_CLASS (1U << 5)
#define USB_REQUEST_RECIPIENT_DEVICE (0U << 0)
#define USB_ENDPOINT_TYPE_MASK (3 << 0)
#define USB_GET_ENDPOINT_TYPE(x) ((x & USB_ENDPOINT_TYPE_MASK) >> 0)
#define USBH_GET_URB_INTERVAL(a, b) (a)
enum { USBH_EVENT_DEVICE_CONNECTED = 4, USBH_EVENT_DEVICE_DISCONNECTED, USBH_EVENT_DEVICE_CONFIGURED };
struct usb_setup_packet { uint8_t bmRequestType; uint8_t bRequest; uint16_t wValue; uint16_t wIndex; uint16_t wLength; };
struct usb_device_descriptor { uint8_t bLength; uint8_t bDescriptorType; uint16_t bcdUSB; uint8_t bDeviceClass; uint8_t bDeviceSubClass; uint8_t bDeviceProtocol; uint8_t bMaxPacketSize0; uint16_t idVendor; uint16_t idProduct; uint16_t bcdDevice; };
struct usb_configuration_descriptor { uint8_t bLength; uint8_t bDescriptorType; uint16_t wTotalLength; uint8_t bNumInterfaces; };
struct usb_interface_descriptor { uint8_t bLength; uint8_t bDescriptorType; uint8_t bInterfaceNumber; uint8_t bAlternateSetting; uint8_t bNumEndpoints; uint8_t bInterfaceClass; uint8_t bInterfaceSubClass; uint8_t bInterfaceProtocol; uint8_t iInterface; };
struct usb_endpoint_descriptor { uint8_t bLength; uint8_t bDescriptorType; uint8_t bEndpointAddress; uint8_t bmAttributes; uint16_t wMaxPacketSize; uint8_t bInterval; };
struct usbh_endpoint { struct usb_endpoint_descriptor ep_desc; };
struct usbh_interface_altsetting { struct usb_interface_descriptor intf_desc; struct usbh_endpoint ep[CONFIG_USBHOST_MAX_ENDPOINTS]; };
struct usbh_interface { char devname[CONFIG_USBHOST_DEV_NAMELEN]; void *class_driver; void *priv; struct usbh_interface_altsetting altsetting[CONFIG_USBHOST_MAX_INTF_ALTSETTINGS]; uint8_t altsetting_num; };
struct usbh_configuration { struct usb_configuration_descriptor config_desc; struct usbh_interface intf[CONFIG_USBHOST_MAX_INTERFACES]; };
struct usbh_hubport;
typedef void (*usbh_complete_callback_t)(void *arg, int nbytes);
struct usbh_urb { struct usbh_hubport *hport; struct usb_endpoint_descriptor *ep; struct usb_setup_packet *setup; uint8_t *transfer_buffer; uint32_t transfer_buffer_length; int actual_length; uint32_t timeout; usbh_complete_callback_t complete; void *arg; uint32_t interval; };
struct usbh_hubport { bool connected; uint8_t port; uint8_t dev_addr; uint8_t speed; struct usb_device_descriptor device_desc; struct usbh_configuration config; const char *iManufacturer; const char *iProduct; const char *iSerialNumber; struct usb_setup_packet *setup; };
typedef void (*usbh_event_handler_t)(uint8_t busid, uint8_t hub_index, uint8_t hub_port, uint8_t intf, uint8_t event);
static inline void usbh_int_urb_fill(struct usbh_urb *urb, struct usbh_hubport *hport, struct usb_endpoint_descriptor *ep, uint8_t *buf, uint32_t len, uint32_t timeout, usbh_complete_callback_t complete, void *arg) { urb->hport = hport; urb->ep = ep; urb->setup = nullptr; urb->transfer_buffer = buf; urb->transfer_buffer_length = len; urb->timeout = timeout; urb->complete = complete; urb->arg = arg; }
int usbh_submit_urb(struct usbh_urb *urb);
int usbh_control_transfer(struct usbh_hubport *hport, struct usb_setup_packet *setup, uint8_t *buffer);
int usbh_initialize(uint8_t busid, uintptr_t reg_base, usbh_event_handler_t handler);
struct usbh_hubport *usbh_find_hubport(uint8_t busid, uint8_t hub_index, uint8_t hub_port);
