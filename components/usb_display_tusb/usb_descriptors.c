/* Device, configuration and string descriptors for the extended screen.
 *
 * A vendor interface with a bulk IN/OUT pair carries the picture: the PC
 * application finds the device by VID/PID and claims that interface, with no
 * class driver involved on either side, because USB defines no display class
 * for one to belong to.
 *
 * Optionally a mass-storage interface follows, holding the program the PC has
 * to run. That one is a standard class every operating system already has a
 * driver for, which is the point of it.
 */

#include <string.h>
#include "tusb.h"
#include "usb_descriptors.h"
#if CFG_TUD_AUDIO
/* TUD_AUDIO_DESCRIPTOR and TUD_AUDIO_DEVICE_DESC_LEN, built by Espressif's
 * usb_device_uac component from the channel counts and sample rate. */
#include "uac_descriptors.h"
#endif

#define USB_VID CONFIG_USB_DISPLAY_VID
#define USB_PID CONFIG_USB_DISPLAY_PID

enum {
  STR_INDEX_LANGUAGE = 0,
  STR_INDEX_MANUFACTURER,
  STR_INDEX_PRODUCT,
  STR_INDEX_SERIAL,
  STR_INDEX_VENDOR,
  STR_INDEX_HID,
  STR_INDEX_AUDIO,
  STR_INDEX_MSC,
};

//--------------------------------------------------------------------+
// Device descriptor
//--------------------------------------------------------------------+
static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    /* 2.1, not 2.0: a host only asks for the BOS descriptor -- and with it the
     * Microsoft OS 2.0 descriptors below -- from a device that claims 2.1. */
    .bcdUSB = 0x0210,

    .bDeviceClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceSubClass = TUSB_CLASS_UNSPECIFIED,
    .bDeviceProtocol = TUSB_CLASS_UNSPECIFIED,

    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor = USB_VID,
    .idProduct = USB_PID,
    /* Windows caches whether a device answers Microsoft OS descriptors, under a
     * key made of the vendor, product and this revision, and never asks again
     * once it has an answer. A board that enumerated before those descriptors
     * existed -- or before it grew a second interface -- is remembered as not
     * having them, and the vendor interface is then left with no driver no
     * matter what the descriptors now say.
     *
     * Give each shape of the device its own revision, so a board that changes
     * shape gets a fresh answer instead of a stale one. */
#if CFG_TUD_MSC
    .bcdDevice = 0x0201,
#else
    .bcdDevice = 0x0101,
#endif

    .iManufacturer = STR_INDEX_MANUFACTURER,
    .iProduct = STR_INDEX_PRODUCT,
    .iSerialNumber = STR_INDEX_SERIAL,

    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *) &desc_device; }

//--------------------------------------------------------------------+
// Configuration descriptor
//--------------------------------------------------------------------+
#if CFG_TUD_HID
/* Horizontal then vertical: the two arguments are the maxima for X and Y. */
static uint8_t const desc_hid_report[] = {
    TUD_HID_REPORT_DESC_TOUCH_SCREEN(REPORT_ID_TOUCH, CONFIG_USB_DISPLAY_WIDTH, CONFIG_USB_DISPLAY_HEIGHT),
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
  (void) instance;
  return desc_hid_report;
}
#endif

#if CFG_TUD_AUDIO
#define AUDIO_DESC_LEN TUD_AUDIO_DEVICE_DESC_LEN
#else
#define AUDIO_DESC_LEN 0
#endif

#define CONFIG_TOTAL_LEN                                                              \
  (TUD_CONFIG_DESC_LEN + TUD_VENDOR_DESC_LEN + (CFG_TUD_HID ? TUD_HID_DESC_LEN : 0) + \
   AUDIO_DESC_LEN + (CFG_TUD_MSC ? TUD_MSC_DESC_LEN : 0))

static uint8_t const desc_configuration[] = {
    /* Bus-powered is a lie on a board with its own supply, but the host budget
     * check is what a 500 mA claim risks failing, so ask for the minimum. */
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, TUSB_DESC_CONFIG_ATT_SELF_POWERED, 100),
    TUD_VENDOR_DESCRIPTOR(ITF_NUM_VENDOR, STR_INDEX_VENDOR, EPNUM_VENDOR, 0x80 | EPNUM_VENDOR, CFG_TUD_VENDOR_EPSIZE),
#if CFG_TUD_HID
    /* 10 ms polling: the GT911 is read about that often, and asking the host to
     * come more frequently only spends bus time on unchanged reports. */
    TUD_HID_DESCRIPTOR(ITF_NUM_HID, STR_INDEX_HID, HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_report),
                       0x80 | EPNUM_HID, CFG_TUD_HID_EP_BUFSIZE, 10),
#endif
#if CFG_TUD_AUDIO
    /* No IN endpoint: playback only for now, so the microphone argument is
     * unused by the speaker-only form of this macro. */
    TUD_AUDIO_DESCRIPTOR(ITF_NUM_AUDIO_CONTROL, STR_INDEX_AUDIO, EPNUM_AUDIO_OUT, 0x00, 0x80 | EPNUM_AUDIO_FB),
#endif
#if CFG_TUD_MSC
    TUD_MSC_DESCRIPTOR(ITF_NUM_MSC, STR_INDEX_MSC, EPNUM_MSC, 0x80 | EPNUM_MSC, CFG_TUD_MSC_EP_BUFSIZE),
#endif
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
// Microsoft OS 2.0 descriptors -- what makes Windows bind WinUSB itself
//--------------------------------------------------------------------+
/* Without these, a vendor interface is a driverless device on Windows and
 * somebody has to bind WinUSB to it by hand with Zadig. With them, Windows 8
 * and later read the compatible ID straight off the device and load WinUSB on
 * their own, so libusb can open it with nothing installed.
 *
 * Layout and lengths are fixed by Microsoft's specification; the totals below
 * are checked against the section sizes at compile time.
 */

#define MS_OS_20_VENDOR_REQUEST 0x02

#define MS_OS_20_SET_HEADER_LEN 0x0A
#define MS_OS_20_CONFIG_SUBSET_LEN 0x08
#define MS_OS_20_FUNCTION_SUBSET_LEN 0x08
#define MS_OS_20_COMPATIBLE_ID_LEN 0x14
/* Registry property carrying DeviceInterfaceGUIDs: 10 bytes of header, a
 * 42-byte UTF-16 name and an 80-byte UTF-16 value. */
#define MS_OS_20_REGISTRY_LEN (0x0A + 0x2A + 0x50)

#define MS_OS_20_DESC_LEN \
  (MS_OS_20_SET_HEADER_LEN + MS_OS_20_CONFIG_SUBSET_LEN + MS_OS_20_FUNCTION_SUBSET_LEN + MS_OS_20_COMPATIBLE_ID_LEN + \
   MS_OS_20_REGISTRY_LEN)

#define BOS_TOTAL_LEN (TUD_BOS_DESC_LEN + TUD_BOS_MICROSOFT_OS_DESC_LEN)

static uint8_t const desc_bos[] = {
    TUD_BOS_DESCRIPTOR(BOS_TOTAL_LEN, 1),
    TUD_BOS_MS_OS_20_DESCRIPTOR(MS_OS_20_DESC_LEN, MS_OS_20_VENDOR_REQUEST),
};

uint8_t const *tud_descriptor_bos_cb(void) { return desc_bos; }

static uint8_t const desc_ms_os_20[] = {
    /* Set header: length, type, minimum Windows version (8.1), total length */
    U16_TO_U8S_LE(MS_OS_20_SET_HEADER_LEN),
    U16_TO_U8S_LE(MS_OS_20_SET_HEADER_DESCRIPTOR),
    U32_TO_U8S_LE(0x06030000),
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN),

    /* Configuration subset: length, type, configuration index, reserved, total length */
    U16_TO_U8S_LE(MS_OS_20_CONFIG_SUBSET_LEN),
    U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_CONFIGURATION),
    0,
    0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - MS_OS_20_SET_HEADER_LEN),

    /* Function subset: length, type, first interface, reserved, subset length */
    U16_TO_U8S_LE(MS_OS_20_FUNCTION_SUBSET_LEN),
    U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_FUNCTION),
    ITF_NUM_VENDOR,
    0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - MS_OS_20_SET_HEADER_LEN - MS_OS_20_CONFIG_SUBSET_LEN),

    /* Compatible ID: this is the line that says "load WinUSB" */
    U16_TO_U8S_LE(MS_OS_20_COMPATIBLE_ID_LEN),
    U16_TO_U8S_LE(MS_OS_20_FEATURE_COMPATBLE_ID),
    'W',
    'I',
    'N',
    'U',
    'S',
    'B',
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,

    /* Registry property: the device interface GUID applications open by. */
    U16_TO_U8S_LE(MS_OS_20_REGISTRY_LEN),
    U16_TO_U8S_LE(MS_OS_20_FEATURE_REG_PROPERTY),
    U16_TO_U8S_LE(0x0007), /* REG_MULTI_SZ */
    U16_TO_U8S_LE(0x002A), /* length of "DeviceInterfaceGUIDs" in UTF-16 */
    'D',
    0x00,
    'e',
    0x00,
    'v',
    0x00,
    'i',
    0x00,
    'c',
    0x00,
    'e',
    0x00,
    'I',
    0x00,
    'n',
    0x00,
    't',
    0x00,
    'e',
    0x00,
    'r',
    0x00,
    'f',
    0x00,
    'a',
    0x00,
    'c',
    0x00,
    'e',
    0x00,
    'G',
    0x00,
    'U',
    0x00,
    'I',
    0x00,
    'D',
    0x00,
    's',
    0x00,
    0x00,
    0x00,
    U16_TO_U8S_LE(0x0050),
    '{',
    0x00,
    '9',
    0x00,
    '7',
    0x00,
    '5',
    0x00,
    'F',
    0x00,
    '4',
    0x00,
    '4',
    0x00,
    'D',
    0x00,
    '9',
    0x00,
    '-',
    0x00,
    '0',
    0x00,
    'D',
    0x00,
    '0',
    0x00,
    '8',
    0x00,
    '-',
    0x00,
    '4',
    0x00,
    '3',
    0x00,
    'F',
    0x00,
    'D',
    0x00,
    '-',
    0x00,
    '8',
    0x00,
    'B',
    0x00,
    '3',
    0x00,
    'E',
    0x00,
    '-',
    0x00,
    '1',
    0x00,
    '2',
    0x00,
    '7',
    0x00,
    'C',
    0x00,
    'A',
    0x00,
    '8',
    0x00,
    'A',
    0x00,
    'F',
    0x00,
    'F',
    0x00,
    'F',
    0x00,
    '9',
    0x00,
    'D',
    0x00,
    '}',
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
};

TU_VERIFY_STATIC(sizeof(desc_ms_os_20) == MS_OS_20_DESC_LEN, "Microsoft OS 2.0 descriptor length mismatch");

/* Windows fetches the set above through a vendor request named in the BOS
 * platform capability. Answering it is the whole handshake. */
bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
  if (stage != CONTROL_STAGE_SETUP)
    return true;

  if (request->bmRequestType_bit.type == TUSB_REQ_TYPE_VENDOR && request->bRequest == MS_OS_20_VENDOR_REQUEST &&
      request->wIndex == 7) {
    return tud_control_xfer(rhport, request, (void *) (uintptr_t) desc_ms_os_20, sizeof(desc_ms_os_20));
  }
  return false;
}

//--------------------------------------------------------------------+
// String descriptors
//--------------------------------------------------------------------+
static char const *string_desc_arr[] = {
    (const char[]){0x09, 0x04},         // 0: English (0x0409)
    CONFIG_USB_DISPLAY_MANUFACTURER,    // 1
    CONFIG_USB_DISPLAY_PRODUCT,         // 2
    CONFIG_USB_DISPLAY_SERIAL,          // 3
    CONFIG_USB_DISPLAY_VENDOR_STRING,   // 4: the vendor interface
    "touch",                            // 5: the touch screen
    "esp uac",                          // 6: the audio function
    "Sender",                           // 7: the drive holding the PC program
};

/* Long enough for the vendor interface string, which is not a label: it is how
 * the host is told the geometry, and it runs to about forty characters. The
 * obvious 32 truncates it into nonsense. */
static uint16_t _desc_str[64];

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
    if (chr_count > 63)
      chr_count = 63;
    for (uint8_t i = 0; i < chr_count; i++)
      _desc_str[1 + i] = str[i];
  }

  _desc_str[0] = (uint16_t) ((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
  return _desc_str;
}
