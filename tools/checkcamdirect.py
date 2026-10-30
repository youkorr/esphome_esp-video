#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""lvgl_camera_display's direct mode: does the frame land where LVGL put it?

Builds tools/camdirecttest/main.cpp with g++ against the SHIPPED
direct_geometry.h and runs it: the three boards' geometries (Tab5 at lvgl
270, Guition at 90, Waveshare at 180) and forty random ones, each compared
pixel for pixel with the UI drawn as lv_image draws it and turned by
ESPHome's own lvgl flush loops.

Then the check checks itself: copies of the header with the PPA's angle not
inverted, and with the 90-degree placement measured off the wrong side, must
each fail. A check that cannot fail on a broken copy proves nothing.

Needs only g++. Not compiled for an ESP32-P4 and never run on a board.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = ROOT / "components/lvgl_camera_display/direct_geometry.h"
TEST = ROOT / "tools/camdirecttest/main.cpp"

MUTANTS = {
    "the PPA's angle not inverted": ("case 90:\n      return 270;", "case 90:\n      return 90;"),
    "90 placed off the wrong side": ("p.px = ui_h - vy1 - p.h;", "p.px = vy1;"),
    "the frame not centred": ("(cw - frame_w) / 2", "0 * (cw - frame_w)"),
}


def run(header_dir, quiet):
    with tempfile.TemporaryDirectory() as tmp:
        exe = pathlib.Path(tmp) / "camdirect"
        subprocess.run(
            ["g++", "-O2", "-std=c++17", "-Wall", "-Wextra", "-Werror", f"-I{header_dir}", str(TEST), "-o", str(exe)],
            check=True,
        )
        res = subprocess.run([str(exe)], capture_output=True, text=True)
        if not quiet:
            print("".join(line + "\n" for line in res.stdout.splitlines() if not line.startswith("ok   random")), end="")
        return res.returncode


def main():
    if shutil.which("g++") is None:
        sys.exit("needs g++")
    failed = run(HEADER.parent, quiet=False) != 0
    text = HEADER.read_text()
    for name, (old, new) in MUTANTS.items():
        assert text.count(old) == 1, f"mutant '{name}' no longer applies"
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / HEADER.name).write_text(text.replace(old, new))
            if run(tmp, quiet=True) == 0:
                print(f"FAIL mutant not caught: {name}")
                failed = True
            else:
                print(f"ok   mutant caught: {name}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
