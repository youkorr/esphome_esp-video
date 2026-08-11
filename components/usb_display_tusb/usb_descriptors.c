/* Device, configuration and string descriptors for the extended screen.
 *
 * One vendor interface with a bulk IN/OUT pair. The PC application finds the
 * device by VID/PID and claims that interface; there is no class driver
 * involved on either side.
 */

#include <string.h>
#include "tusb.h"
#include "usb_descriptors.h"

#define USB_VID CONFIG_USB_DISPLAY_VID
#define USB_PID CONFIG_USB_DISPLAY_PID

enum {
  STR_INDEX_LANGUAGE = 0,
  STR_INDEX_MANUFACTURER,
  STR_INDEX_PRODUCT,
  STR_INDEX_SERIAL,
  STR_INDEX_VENDOR,
};

//--------------------------------------------------------------------+
// Device descriptor
//--------------------------------------------------------------------+
static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,

    .bDeviceClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceSubClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceProtocol = TUSB_CLASS_UNSPECIFIED,

    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0100,

    .iManufacturer = STR_INDEX_MANUFACTURER,
    .iProduct = STR_INDEX_PRODUCT,
    .iSerialNumber = STR_INDEX_SERIAL,

    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *) &desc_device; }

//--------------------------------------------------------------------+
// Configuration descriptor
//--------------------------------------------------------------------+
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_VENDOR_DESC_LEN)

static uint8_t const desc_configuration[] = {
    /* Bus-powered is a lie on a board with its own supply, but the host budget
     * check is what a 500 mA claim risks failing, so ask for the minimum. */
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, TUSB_DESC_CONFIG_ATT_SELF_POWERED, 100),
    TUD_VENDOR_DESCRIPTOR(ITF_NUM_VENDOR, STR_INDEX_VENDOR, EPNUM_VENDOR, 0x80 | EPNUM_VENDOR, CFG_TUD_VENDOR_EPSIZE),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
  (void) index;
  return desc_configuration;
}

#if TUD_OPT_HIGH_SPEED
/* A High-Speed device must answer the qualifier and other-speed requests, or
 * the host reports it as "not working properly" before any data flows. */
static tusb_desc_device_qualifier_t const desc_device_qualifier = {
    .bLength = sizeof(tusb_desc_device_qualifier_t),
    .bDescriptorType = TUSB_DESC_DEVICE_QUALIFIER,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceSubClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceProtocol = TUSB_CLASS_UNSPECIFIED,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .bNumConfigurations = 0x01,
    .bReserved = 0x00,
};

uint8_t const *tud_descriptor_device_qualifier_cb(void) { return (uint8_t const *) &desc_device_qualifier; }

uint8_t const *tud_descriptor_other_speed_configuration_cb(uint8_t index) {
  (void) index;
  return desc_configuration;
}
#endif

//--------------------------------------------------------------------+
// String descriptors
//--------------------------------------------------------------------+
static char const *string_desc_arr[] = {
    (const char[]){0x09, 0x04},       // 0: English (0x0409)
    CONFIG_USB_DISPLAY_MANUFACTURER,  // 1
    CONFIG_USB_DISPLAY_PRODUCT,       // 2
    CONFIG_USB_DISPLAY_SERIAL,        // 3
    "Extended Screen",                // 4: the vendor interface
};

static uint16_t _desc_str[32];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
  (void) langid;
  uint8_t chr_count;

  if (index == STR_INDEX_LANGUAGE) {
    memcpy(&_desc_str[1], string_desc_arr[0], 2);
    chr_count = 1;
  } else {
    if (index >= sizeof(string_desc_arr) / sizeof(string_desc_arr[0]))
      return NULL;

    const char *str = string_desc_arr[index];
    chr_count = (uint8_t) strlen(str);
    if (chr_count > 31)
      chr_count = 31;
    for (uint8_t i = 0; i < chr_count; i++)
      _desc_str[1 + i] = str[i];
  }

  _desc_str[0] = (uint16_t) ((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
  return _desc_str;
}
