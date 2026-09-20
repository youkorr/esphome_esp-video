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
two task calls, CherryUSB's host API, and the four Bluedroid headers the host
stack needs -- including esp_bluedroid_hci.h, which is the whole
specification of the HCI glue. The CherryUSB stub is copied field
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
# **/*.cpp rather than *.cpp: the speaker platform lives in its own
# subdirectory, the way every ESPHome platform does, and a glob that
# stopped at the top level would have said CLEAN about a file it never
# opened -- which is the silent no-op this tool exists to close.
SOURCES = sorted((ROOT / "components" / "portall_bt").glob("**/*.cpp"))

# BOTH ways, and the second one is the point. Most of the host-stack code sits
# behind `#ifdef CONFIG_BT_BLUEDROID_ENABLED`, so a single pass without that
# symbol compiles the file and never looks at the half most likely to be
# wrong -- it is the newest, it calls the least familiar API, and it is the
# only part no user has ever built. A check that passes on code it did not
# read is the silent no-op this repository keeps recording.
CONFIGURATIONS = [
    ("host_stack: none", []),
    ("host_stack: bluedroid", ["-DCONFIG_BT_BLUEDROID_ENABLED=1"]),
    # And a third, for the same reason the second exists: the HID host is
    # behind CONFIG_BT_HID_HOST_ENABLED, so without this pass the newest file
    # in the component compiles down to nothing and the check says CLEAN.
    (
        "host_stack: bluedroid, hid: true",
        ["-DCONFIG_BT_BLUEDROID_ENABLED=1", "-DCONFIG_BT_HID_HOST_ENABLED=1"],
    ),
    # And a fourth, for the A2DP source, which is behind its own symbol again.
    # Every profile added here has to bring a configuration with it or the
    # newest file in the component compiles down to nothing and this says
    # CLEAN -- which is what the second and third passes were both added for.
    (
        "host_stack: bluedroid, audio: true",
        ["-DCONFIG_BT_BLUEDROID_ENABLED=1", "-DCONFIG_BT_A2DP_ENABLE=1"],
    ),
    (
        "everything at once",
        [
            "-DCONFIG_BT_BLUEDROID_ENABLED=1",
            "-DCONFIG_BT_HID_HOST_ENABLED=1",
            "-DCONFIG_BT_A2DP_ENABLE=1",
        ],
    ),
    # And a fifth for the speaker platform, which is behind USE_SPEAKER --
    # ESPHome's own define, written by codegen only when a speaker is in the
    # YAML. Without this pass speaker/portall_bt_speaker.cpp compiles to an
    # empty translation unit and this tool prints ok about nothing at all.
    (
        "speaker: - platform: portall_bt",
        [
            "-DCONFIG_BT_BLUEDROID_ENABLED=1",
            "-DCONFIG_BT_A2DP_ENABLE=1",
            "-DUSE_SPEAKER",
        ],
    ),
    # And one each for the two platforms added with the Bluetooth switch, for
    # exactly the reason the speaker pass exists: behind USE_SWITCH and
    # USE_TEXT_SENSOR they compile to empty translation units, and this tool
    # would print ok about a file it had never read a line of.
    (
        "switch: - platform: portall_bt",
        [
            "-DCONFIG_BT_BLUEDROID_ENABLED=1",
            "-DCONFIG_BT_A2DP_ENABLE=1",
            "-DCONFIG_BT_HID_HOST_ENABLED=1",
            "-DUSE_SWITCH",
        ],
    ),
    (
        "text_sensor: - platform: portall_bt",
        [
            "-DCONFIG_BT_BLUEDROID_ENABLED=1",
            "-DCONFIG_BT_A2DP_ENABLE=1",
            "-DCONFIG_BT_HID_HOST_ENABLED=1",
            "-DUSE_TEXT_SENSOR",
        ],
    ),
]


def main() -> int:
    if not SOURCES:
        print("  no sources under components/portall_bt")
        return 1

    failed = False
    for source in SOURCES:
        for label, extra in CONFIGURATIONS:
            command = [
                "g++",
                "-std=gnu++17",
                "-fsyntax-only",
                "-Wall",
                "-Wextra",
                "-Wno-unused-parameter",
                "-DUSE_ESP32",
                *extra,
                f"-I{ROOT / 'tools' / 'btstub'}",
                f"-I{source.parent}",
                str(source),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            name = f"{source.relative_to(ROOT)}  ({label})"
            if result.returncode == 0 and not result.stderr.strip():
                print(f"  ok     {name}")
            else:
                failed = True
                print(f"  ECHEC  {name}")
                for line in result.stderr.strip().splitlines():
                    print(f"         {line}")

    if not run_tests():
        failed = True

    return 1 if failed else 0


def run_tests() -> bool:
    """Compile and RUN tools/bttest/*.cpp, which include the component itself.

    A syntax check cannot see arithmetic, and the arithmetic is where this
    component's faults have lived: an alignment taken from the wrong port, a
    class-of-device offset taken from the wrong event, and a frame ended on a
    short packet that a frame of exactly one packet never produces. These link
    against the real source and exercise the real functions.
    """
    tests = sorted((ROOT / "tools" / "bttest").glob("*.cpp"))
    if not tests:
        return True

    ok = True
    for test in tests:
        binary = test.with_suffix(".bin")
        build = subprocess.run(
            [
                "g++",
                "-std=gnu++17",
                "-Wall",
                "-Wextra",
                "-Wno-unused-parameter",
                "-DUSE_ESP32",
                "-DCONFIG_BT_BLUEDROID_ENABLED=1",
                # The profiles are ON for the tests, unlike the syntax passes
                # above which deliberately try every combination: a test that
                # runs arithmetic needs the arithmetic compiled in, and the
                # speaker's mono-to-stereo is behind both of these.
                "-DCONFIG_BT_A2DP_ENABLE=1",
                "-DUSE_SPEAKER",
                f"-I{ROOT / 'tools' / 'btstub'}",
                f"-I{ROOT / 'components' / 'portall_bt'}",
                "-o",
                str(binary),
                str(test),
                # hid.cpp is a second translation unit of the same component
                # and loop() calls into it, so the test does not link without
                # it. Named rather than globbed: a test that silently picked up
                # whatever was in the folder would be a different check every
                # time somebody added a file.
                str(ROOT / "components" / "portall_bt" / "hid.cpp"),
                str(ROOT / "components" / "portall_bt" / "a2dp.cpp"),
                # keys.cpp turns an AVRCP command or a HID keyboard report
                # into a key. Both drains call into it, so nothing links
                # without it -- which is the LINKER doing the job this whole
                # arrangement exists for: the tests build against the real
                # sources so a declaration that stopped matching its
                # definition is caught here rather than on somebody's board.
                str(ROOT / "components" / "portall_bt" / "keys.cpp"),
                # hid_descriptor.cpp walks a device's own report descriptor,
                # which is what keys.cpp reads instead of the byte offsets it
                # used to carry. It is pure arithmetic behind no #ifdef at
                # all, which is precisely why it is worth linking and running
                # rather than only compiling.
                str(ROOT / "components" / "portall_bt" / "hid_descriptor.cpp"),
                str(ROOT / "components" / "portall_bt" / "speaker" / "portall_bt_speaker.cpp"),
            ],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            ok = False
            print(f"  ECHEC  {test.relative_to(ROOT)} would not build")
            for line in build.stderr.strip().splitlines():
                print(f"         {line}")
            continue

        run = subprocess.run([str(binary)], capture_output=True, text=True)
        binary.unlink(missing_ok=True)
        for line in run.stdout.strip().splitlines():
            print(line if line.startswith("  ") else f"  {line}")
        if run.returncode != 0:
            ok = False
    return ok


if __name__ == "__main__":
    sys.exit(main())
