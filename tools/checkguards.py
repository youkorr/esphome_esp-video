#!/usr/bin/env python3
"""Every TinyUSB symbol must sit under a guard that is false without a device.

`usb: false` on portall leaves TinyUSB out of the build entirely, so that a
panel fed over Wi-Fi can release the USB OTG peripheral and a USB HOST -- a
Bluetooth dongle, which is what this was written for -- may have it. The
ESP32-P4 has two of those peripherals and a board wires each of its sockets to
one; on the M5Stack Tab5 the USB-A host socket is on the same high-speed one
portall puts in device mode, so the two genuinely cannot share it.

What that means for the C++ is that a few dozen lines have to disappear when
one macro is off, and the fault this looks for is the one a reader cannot see:
a `tud_*` call left OUTSIDE every guard. It balances perfectly, it compiles in
every configuration anybody here can compile, and it fails at the LINK on
somebody else's board -- which is the worst place this project has ever found
an error, and has found two.

Counting `#if` against `#endif` does not find it. This walks the guard stack
and asks, of every line that names a TinyUSB symbol, whether anything above it
is a condition that goes false without a USB device.

IT WAS WRONG ITSELF FIRST, and the way is worth recording. `#else` was treated
as unprotected whatever it followed -- true after `#if CONFIG_USB_DISPLAY_DEVICE`
and exactly backwards after `#if !CONFIG_USB_DISPLAY_DEVICE`, which is how
on_vendor_rx was written at the time. It reported three faults that were not
there. The answer was not to teach this about negation: it was to stop writing
the negated form, which no reader could follow either. So a guard here is
positive, and this stays simple enough to be believed.

Reproduced against a copy with the guards stripped out before it was believed
at all -- which caught tud_vendor_rx_cb, tud_mount_cb, tusb_speed_t and the
rest, and is the only way this class of check is ever worth anything.

Run with no arguments to check everything that should be clean.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# What only exists when TinyUSB is in the build. Deliberately a list of shapes
# rather than a prefix: CFG_TUD_VENDOR_EPSIZE is a number in tusb_config.h and
# nothing at all without it, and an array sized by it is one of the two ways
# this has actually gone wrong.
USES = re.compile(
    r"\b(tud_[a-z_]+|tusb_init|tusb_speed_t|TUSB_SPEED_\w+|tusb_device_task"
    r"|usb_phy_handle_t|usb_phy_config_t|usb_new_phy|USB_PHY_\w+|USB_OTG_\w+"
    r"|CFG_TUD_VENDOR_EPSIZE|TUD_HID_\w+)\b"
)

# A condition that is false when no USB device is built. CFG_TUD_* counts
# because those come from tusb_config.h, which is not read at all without
# TinyUSB, so the preprocessor reads every one of them as 0.
SAFE = re.compile(r"CONFIG_USB_DISPLAY_DEVICE|CFG_TUD_[A-Z_]+")

DEFAULT = [
    "components/portall/portall.cpp",
    "components/portall/portall.h",
    "components/portall/audio.cpp",
    "components/portall/touch.cpp",
    "components/portall/network.cpp",
    "components/portall/sender_drive.cpp",
    "components/portall/number/usb_volume_number.cpp",
    "components/portall/number/usb_volume_number.h",
    "components/usb_display_tusb/usb_descriptors.h",
]


def check(path):
    problems = 0
    stack = []  # one entry per open #if: True when it protects what is inside
    for number, line in enumerate(open(path, encoding="utf-8"), 1):
        stripped = line.strip()
        if stripped.startswith("#if"):
            if stripped.lstrip("#if").lstrip().startswith("!") and SAFE.search(stripped):
                print(f"{path}:{number}: negated guard -- see this file's docstring")
                print(f"    {stripped[:90]}")
                problems += 1
            stack.append(bool(SAFE.search(stripped)))
            continue
        if stripped.startswith("#elif"):
            if stack:
                stack[-1] = bool(SAFE.search(stripped))
            continue
        if stripped.startswith("#else"):
            # The other arm of a USB guard is the no-USB arm, by construction.
            if stack:
                stack[-1] = False
            continue
        if stripped.startswith("#endif"):
            if stack:
                stack.pop()
            else:
                print(f"{path}:{number}: #endif with nothing open")
                problems += 1
            continue
        if stripped.startswith(("//", "*", "/*")):
            continue
        hit = USES.search(line)
        if hit and not any(stack):
            print(f"{path}:{number}: {hit.group(1)} is not under a USB guard")
            print(f"    {stripped[:90]}")
            problems += 1
    if stack:
        print(f"{path}: {len(stack)} guard(s) never closed")
        problems += 1
    return problems


def main(argv):
    paths = argv[1:] or [str(ROOT / p) for p in DEFAULT]
    problems = sum(check(p) for p in paths)
    if problems:
        print(f"\n{problems} problem(s): this would link on a board and not here.")
        return 1
    print(f"CLEAN -- {len(paths)} file(s), every TinyUSB symbol is under a guard")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
