#!/usr/bin/env python3
"""Compile components/portall_bt with a plain g++ and stand-in headers.

There is no ESP-IDF toolchain where this repository is worked on, and
`esphome config` validates YAML and codegen and never compiles a line of C++.
That gap is where this project's most expensive mistakes have lived: a helper
that had been renamed, a play() that was not an override, an anchored edit
that deleted a function. Every one of them reached a user's board as a build
error, which is the worst place to find one.

This does not fix that -- only a real toolchain compiles for real -- but it
closes the cheap half. `tools/btstub/` holds stand-ins for the handful of
headers portall_bt includes: ESPHome's Component and log macros, FreeRTOS's
two task calls, and CherryUSB's host API. The CherryUSB stub is copied field
for field from core/usbh_core.h, common/usb_def.h and osal/idf/usb_config.h
of the version the component pins, so a member that does not exist, a
constant that was never defined and a typo in a name are all caught here in a
second.

What it CANNOT catch, and the list matters more than the tool: anything about
the real headers that the stand-in gets wrong, the linker, the IDF build, and
every fault that only appears on hardware.

    python3 tools/checkbt.py
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "components" / "portall_bt").glob("*.cpp"))


def main() -> int:
    if not SOURCES:
        print("  no sources under components/portall_bt")
        return 1

    failed = False
    for source in SOURCES:
        command = [
            "g++",
            "-std=gnu++17",
            "-fsyntax-only",
            "-Wall",
            "-Wextra",
            "-Wno-unused-parameter",
            "-DUSE_ESP32",
            f"-I{ROOT / 'tools' / 'btstub'}",
            f"-I{source.parent}",
            str(source),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and not result.stderr.strip():
            print(f"  ok     {source.relative_to(ROOT)}")
        else:
            failed = True
            print(f"  ECHEC  {source.relative_to(ROOT)}")
            for line in result.stderr.strip().splitlines():
                print(f"         {line}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
