#!/usr/bin/env python3
"""Run components/usb_display_tusb/CMakeLists.txt against a stand-in ESP-IDF.

This exists because a build error reached a user's board from this one file,
and nothing in this repository had ever executed a line of CMake. The fault was
the shape this project keeps recording: the file asked whether the OPTION said
a USB device was wanted, when the question that decides it is whether TinyUSB
is in the BUILD. Those disagree whenever a build directory carries TinyUSB
from an earlier run -- `managed_components/` is discovered from disk -- and the
disagreement arrives as

    tusb_option.h (in "espressif__tinyusb") includes tusb_config.h, provided by
    usb_display_tusb component(s). However, usb_display_tusb component(s) is
    not in the requirements list of "espressif__tinyusb".

which names two components the reader never configured.

What this CAN check: which branch the file takes, and whether it reaches for
TinyUSB, for each of the four states. The IDF functions are stubs that record
what they were called with, and the file is processed with add_subdirectory
exactly as ESP-IDF processes a component.

What it CANNOT check: that ESP-IDF's real build agrees. The stubs say what
these functions are named and what this file does with them, not that an IDF
build accepts the result.

    python3 tools/checkcmake.py
"""

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "components" / "usb_display_tusb" / "CMakeLists.txt"

# The stand-in. Every IDF function this file calls, recording what it was
# handed into a file the test reads back -- so a branch that was taken is
# proved by what it DID, not by grepping the source for an `if`.
HARNESS = """
cmake_minimum_required(VERSION 3.16)
project(usb_display_tusb_check NONE)

set(IDF_VERSION_MAJOR 5)

function(idf_component_register)
  file(APPEND "${RECORD}" "registered ${ARGN}\\n")
endfunction()

function(idf_build_get_property var prop)
  if(prop STREQUAL "BUILD_COMPONENTS")
    set(${var} "${BUILD_COMPONENTS}" PARENT_SCOPE)
  else()
    set(${var} "" PARENT_SCOPE)
  endif()
endfunction()

function(idf_component_get_property var component prop)
  # The real one is a hard error for a component that is not in the build, and
  # that is the whole point of the branch above: reaching for TinyUSB when it
  # is absent must never happen.
  if(NOT "${component}" IN_LIST BUILD_COMPONENTS)
    message(FATAL_ERROR "asked for ${prop} of ${component}, which is not in the build")
  endif()
  file(APPEND "${RECORD}" "reached-for ${component}\\n")
  set(${var} "${component}_lib" PARENT_SCOPE)
endfunction()

function(target_include_directories)
  file(APPEND "${RECORD}" "include-dirs ${ARGN}\\n")
endfunction()

function(target_sources)
  file(APPEND "${RECORD}" "sources ${ARGN}\\n")
endfunction()

add_subdirectory(component)
"""


def run(label, build_components, device, source):
    """Configure the component once and report what it did."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "component").mkdir()
        (tmp / "component" / "CMakeLists.txt").write_text(source)
        # The descriptors the real file adds to TinyUSB; add_subdirectory does
        # not need them to exist, but a missing one would be a different fault.
        (tmp / "component" / "usb_descriptors.c").write_text("")
        (tmp / "CMakeLists.txt").write_text(HARNESS)
        record = tmp / "record.txt"
        record.write_text("")

        command = [
            "cmake",
            "-S", str(tmp),
            "-B", str(tmp / "build"),
            f"-DRECORD={record}",
            f"-DBUILD_COMPONENTS={';'.join(build_components)}",
        ]
        if device is not None:
            command.append(f"-DCONFIG_USB_DISPLAY_DEVICE={'y' if device else ''}")
        result = subprocess.run(command, capture_output=True, text=True)
        return result, record.read_text()


def main() -> int:
    source = COMPONENT.read_text()
    # The file as it was before the fix, rebuilt from the shipped one: the
    # check has to fail on the broken version or it is not a check.
    broken = source.replace(
        'idf_build_get_property(build_components BUILD_COMPONENTS)\n'
        'if(NOT "espressif__tinyusb" IN_LIST build_components)',
        "if(NOT CONFIG_USB_DISPLAY_DEVICE)",
    )
    if broken == source:
        print("  ECHEC  could not rebuild the old version to test against")
        return 1

    failures = 0

    def check(what, ok, detail=""):
        nonlocal failures
        print(f"  {'ok   ' if ok else 'ECHEC'}  {what}")
        if not ok:
            failures += 1
            for line in detail.strip().splitlines():
                print(f"         {line}")

    with_tusb = ["esp_driver_jpeg", "espressif__tinyusb"]
    without = ["esp_driver_jpeg"]

    # 1. The ordinary panel: portall with usb: true.
    result, did = run("device", with_tusb, True, source)
    check(
        "usb: true -- the descriptors go into TinyUSB",
        result.returncode == 0 and "reached-for espressif__tinyusb" in did,
        result.stderr,
    )

    # 2. A Wi-Fi-fed panel hosting a dongle: no TinyUSB in the build at all.
    result, did = run("no device", without, False, source)
    check(
        "usb: false -- TinyUSB is never reached for",
        result.returncode == 0 and "reached-for" not in did and "registered" in did,
        result.stderr,
    )

    # 3. THE FAULT A BOARD REPORTED. TinyUSB is in the build and the option is
    #    off, which is a build directory carrying it from an earlier run.
    result, did = run("stale", with_tusb, False, source)
    check(
        "TinyUSB left over from an earlier build still gets its tusb_config.h",
        result.returncode == 0 and "reached-for espressif__tinyusb" in did,
        result.stderr,
    )
    check(
        "and the leftover is named in the build output",
        "leftover from an earlier build" in result.stdout,
        result.stdout,
    )

    # 3b. The same state against the OLD file: it must fail the way the board
    #     did, or this check proves nothing.
    result, did = run("stale, old code", with_tusb, False, broken)
    check(
        "the old version leaves that TinyUSB without one, as reported",
        result.returncode == 0 and "reached-for" not in did,
        result.stderr,
    )

    # 4. The reverse, which the old file turned into a hard CMake error: the
    #    option on with no TinyUSB to reach for.
    result, did = run("option on, no library", without, True, source)
    check(
        "usb: true with no TinyUSB in the build returns instead of erroring",
        result.returncode == 0 and "reached-for" not in did,
        result.stderr,
    )
    result, did = run("option on, no library, old code", without, True, broken)
    check(
        "the old version made that a hard CMake error",
        result.returncode != 0 and "not in the build" in result.stderr,
        result.stdout + result.stderr,
    )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
