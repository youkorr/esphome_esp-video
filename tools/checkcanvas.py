#!/usr/bin/env python3
"""Compile portall's LVGL canvas mode against a real LVGL, and run it.

The canvas mode (`canvas:` on portall) is the one part of the component that
calls into somebody else's C library, and nothing else here can see it:
`esphome config` never compiles C++, and there is no ESP-IDF toolchain. This
builds LVGL from source on the workstation with a minimal configuration,
lifts `canvas_tick_()` and `copy_to_canvas_()` out of portall.cpp as they
are, and draws with them on a display that has no panel behind it -- reading
back, from the flush callback, what would have reached the screen.

It paid for itself on its first run: `lv_area_join()` is in LVGL 9's PRIVATE
headers, so the first version of the mode would have failed to compile on
the board.

Build against the LVGL ESPHome pins (`LVGL_VERSION` in its lvgl component),
not whatever is newest:

    git clone --depth 1 -b v9.5.0 https://github.com/lvgl/lvgl /tmp/lvgl
    python3 tools/checkcanvas.py --lvgl /tmp/lvgl

It also checks the touch half: canvas_turn_() in network.cpp, which turns a
contact from the glass into LVGL's coordinates when `lvgl: rotation:` turns
the screen, against ESPHome's own rotation loops run on a test picture.

And the flow control: canvas_wait_() holding a new picture until LVGL's own
REFR_READY says the last one was drawn, with a vTaskDelay that runs LVGL's
refresh in its place.

What it does not cover: the decode task's real concurrency with LVGL's
render, and anything about a real panel.
"""

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = ROOT / "tools" / "canvastest"
SOURCE = ROOT / "components" / "portall" / "portall.cpp"

# Backends for other hardware, and the operating-system shims, need headers
# this machine does not have; nothing the canvas path touches is in them.
SKIP = re.compile(
    r"/drivers/|/libs/(?!bin_decoder/)|"
    r"/draw/(nxp|renesas|vg_lite|sdl|opengles|dma2d|espressif|eve|nema_gfx)/|"
    r"/osal/(pthread|freertos|cmsis|rtthread|windows|sdl2|mqx|linux)"
)


def shipped():
    text = SOURCE.read_text()
    start = "#ifdef USE_LVGL\nvoid Portall::canvas_tick_()"
    a = text.index(start)
    b = text.index("#endif  // USE_LVGL", a)
    # The wait's bound is a constant at the top of the file; carried across
    # rather than restated, so the harness cannot disagree with it.
    # Every constant of the canvas block is carried across the same way.
    block = text[text.index("static constexpr uint32_t CANVAS_WAIT_MS"):]
    block = block[:block.index("#endif")]
    consts = "".join(line + "\n" for line in block.splitlines() if line.startswith("static constexpr"))
    return consts + text[a + len("#ifdef USE_LVGL\n"):b]


def turn():
    text = (ROOT / "components" / "portall" / "network.cpp").read_text()
    a = text.index("static void canvas_turn_(")
    b = text.index("\n}\n", a) + 3
    return text[a:b]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lvgl", default=os.environ.get("LVGL"),
                        help="an LVGL source tree (or $LVGL)")
    parser.add_argument("--cache", default=None,
                        help="where to keep LVGL's objects between runs")
    args = parser.parse_args()
    if not args.lvgl:
        sys.exit("give --lvgl PATH (or $LVGL): see the top of this file")
    lvgl = pathlib.Path(args.lvgl).resolve()
    cache = pathlib.Path(args.cache or tempfile.gettempdir()) / "portall-canvas-lvgl"
    cache.mkdir(parents=True, exist_ok=True)
    flags = ["-DLV_CONF_INCLUDE_SIMPLE", f"-I{HERE}", f"-I{lvgl}"]

    sources = [p for p in sorted((lvgl / "src").rglob("*.c")) if not SKIP.search(str(p))]
    stamp = hashlib.sha1((str(lvgl) + (HERE / "lv_conf.h").read_text()).encode()).hexdigest()[:10]

    def build(path):
        out = cache / f"{stamp}-{hashlib.sha1(str(path).encode()).hexdigest()[:12]}.o"
        if not out.exists():
            subprocess.run(["gcc", "-c", "-O1", "-w", *flags, str(path), "-o", str(out)], check=True)
        return out

    print(f"building LVGL from {lvgl} ({len(sources)} files)")
    with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        objects = list(pool.map(build, sources))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "shipped.inc").write_text(shipped())
        # The harness's video-mode cases place pixels on a turned panel and
        # read them back through the shipped touch conversion, which is itself
        # checked against ESPHome's rotation loops below.
        (tmp / "turn.inc").write_text(turn())
        binary = tmp / "harness"
        subprocess.run(["g++", "-std=c++17", "-O0", "-Wall", "-Wno-unused-function", *flags,
                        f"-I{tmp}", str(HERE / "harness.cpp"), *map(str, objects), "-o", str(binary),
                        "-lm"], check=True)
        failed = subprocess.run([str(binary)]).returncode
        # The touch half: the shipped canvas_turn_() against ESPHome's own
        # rotation loops. Needs no LVGL.
        (tmp / "turn.inc").write_text(turn())
        turner = tmp / "turn"
        subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", f"-I{tmp}", str(HERE / "turn.cpp"),
                        "-o", str(turner)], check=True)
        return subprocess.run([str(turner)]).returncode or failed


if __name__ == "__main__":
    sys.exit(main())
