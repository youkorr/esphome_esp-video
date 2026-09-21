#pragma once
/* The report descriptors the input tests press buttons at.
 *
 * ONE COPY, for the reason linkstubs.h gives about its own list: two copies
 * of a fixture drift the moment somebody adjusts one of them, and a test that
 * is quietly measuring a different device from the one beside it proves
 * nothing at all. input.cpp drives one device through these; multi.cpp drives
 * two, which is the case they had to be shared for.
 *
 * Both are built to the specification rather than captured off a device --
 * the NVIDIA Shield's own descriptor has still never been seen here, which is
 * said out loud in CLAUDE.md and is the honest limit of every one of these.
 */
#include <cstdint>

/* A gamepad: four axes, a hat with a null state, sixteen buttons, and a
   consumer report of its own carrying AC Home and AC Back in the four-byte
   Usage form whose high half is the page. */
inline const uint8_t PAD_DESC[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x05,        // Usage (Game Pad)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x01,        //   Report ID (1)
    0x09, 0x01,        //   Usage (Pointer)
    0xA1, 0x00,        //   Collection (Physical)
    0x09, 0x30,        //     Usage (X)
    0x09, 0x31,        //     Usage (Y)
    0x09, 0x32,        //     Usage (Z)
    0x09, 0x35,        //     Usage (Rz)
    0x15, 0x00,        //     Logical Minimum (0)
    0x26, 0xFF, 0x00,  //     Logical Maximum (255)
    0x75, 0x08,        //     Report Size (8)
    0x95, 0x04,        //     Report Count (4)
    0x81, 0x02,        //     Input (Data,Var,Abs)
    0xC0,              //   End Collection
    0x09, 0x39,        //   Usage (Hat switch)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x07,        //   Logical Maximum (7)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x42,        //   Input (Data,Var,Abs,Null State)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x03,        //   Input (Const,Var,Abs)  -- padding, no field
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (Button 1)
    0x29, 0x10,        //   Usage Maximum (Button 16)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x10,        //   Report Count (16)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0xC0,              // End Collection
    0x05, 0x0C,        // Usage Page (Consumer)
    0x09, 0x01,        // Usage (Consumer Control)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x02,        //   Report ID (2)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x02,        //   Report Count (2)
    0x0A, 0x23, 0x02,  //   Usage (AC Home)     -- four-byte form, own page
    0x0A, 0x24, 0x02,  //   Usage (AC Back)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x95, 0x06,        //   Report Count (6)
    0x81, 0x03,        //   Input (Const,Var,Abs)
    0xC0,              // End Collection
};
/* A keyboard: eight modifier bits, the reserved byte, and six keycodes as an
   ARRAY field -- several instances of one field each holding a usage rather
   than a bit per key, which is how six keys fit in six bytes. */
inline const uint8_t KBD_DESC[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x06,        // Usage (Keyboard)
    0xA1, 0x01,        // Collection (Application)
    0x05, 0x07,        //   Usage Page (Keyboard)
    0x19, 0xE0,        //   Usage Minimum (LeftControl)
    0x29, 0xE7,        //   Usage Maximum (Right GUI)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x08,        //   Report Count (8)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x95, 0x01,        //   Report Count (1)
    0x75, 0x08,        //   Report Size (8)
    0x81, 0x03,        //   Input (Const,Var,Abs)  -- the reserved byte
    0x95, 0x06,        //   Report Count (6)
    0x75, 0x08,        //   Report Size (8)
    0x15, 0x00,        //   Logical Minimum (0)
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x19, 0x00,        //   Usage Minimum (0)
    0x2A, 0xFF, 0x00,  //   Usage Maximum (255)
    0x81, 0x00,        //   Input (Data,Array,Abs)
    0xC0,              // End Collection
};