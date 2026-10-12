#!/usr/bin/env python3
"""Run portall's decode and draw, overlapped, on a workstation.

`decode_buffers: 2` gives the board a second decode buffer and a task of its
own for the draw, so the JPEG engine decodes picture N+1 while the display is
still copying N (components/portall/pipeline.cpp). Two tasks handing buffers to
each other is exactly the kind of thing that reads right and tears a picture,
and there is no ESP-IDF toolchain here -- so this builds the SHIPPED
portall.cpp and pipeline.cpp with g++, against ESPHome's own headers and
stand-ins for the JPEG engine, the accelerator and the display that take a set
time each, and runs them on real threads (tools/pipetest/).

    python3 tools/checkpipeline.py                    find esphome by importing it
    python3 tools/checkpipeline.py --esphome DIR      DIR holds the esphome package
    python3 tools/checkpipeline.py --lvgl /tmp/lvgl   also compile canvas mode

What it says: every picture drawn once and in order, rectangles and failed
decodes mixed in; no decode buffer written while the display or the
accelerator reads it; the two engines really busy at the same time with two
buffers and never with one; the pictures a second that buys; and the board's
stats line reporting the decode time and the decoder's wait.

The only change it makes to portall.cpp is the stats interval, one second
instead of five, so the stats case does not take ten. And it checks itself:
a copy of pipeline.cpp that gives a buffer back BEFORE drawing it must fail
the torn-buffer case, or the case proves nothing.

What it does not say: anything about the P4 -- its memory bandwidth, how the
2D-DMA shares its channels, or what the board's own line reads. One flash.
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = ROOT / "tools" / "pipetest"
PORTALL = ROOT / "components" / "portall"

DEFINES = """#pragma once
// A workstation build of portall: ESPHome's host HAL, and nothing else
// switched on unless asked for on the command line.
#define USE_HOST
#define ESPHOME_LOG_LEVEL 5
#define ESPHOME_COMPONENT_COUNT 4
#define ESPHOME_AREA_COUNT 0
#define ESPHOME_DEVICE_COUNT 0
"""


def find_esphome(given):
    if given:
        return pathlib.Path(given)
    if os.environ.get("ESPHOME_SRC"):
        return pathlib.Path(os.environ["ESPHOME_SRC"])
    try:
        import esphome  # noqa: F401
        return pathlib.Path(esphome.__file__).resolve().parent.parent
    except ImportError:
        return None


def stage(esphome_root, into):
    """ESPHome's headers, with a defines.h of our own.

    Copied rather than pointed at, because core/color.h includes "defines.h"
    by a relative name -- so an include path cannot put a different one in
    front of it, and the installed one switches on LVGL and much else.
    """
    src = esphome_root / "esphome"
    dst = into / "esphome"
    shutil.copytree(src / "core", dst / "core")
    for comp in ("display", "host"):
        shutil.copytree(src / "components" / comp, dst / "components" / comp)
    (dst / "components" / "lvgl").mkdir(parents=True)
    shutil.copy(src / "components" / "lvgl" / "lvgl_proxy.h", dst / "components" / "lvgl")
    (dst / "core" / "defines.h").write_text(DEFINES)
    return into


def patched_portall(into):
    text = (PORTALL / "portall.cpp").read_text()
    old = "static constexpr uint32_t STATS_INTERVAL_MS = 5000;"
    assert text.count(old) == 1, "STATS_INTERVAL_MS moved; update this tool"
    (into / "portall.cpp").write_text(text.replace(old, "static constexpr uint32_t STATS_INTERVAL_MS = 1000;"))
    return into / "portall.cpp"


def broken_pipeline(into):
    """pipeline.cpp with the buffer given back before it is drawn."""
    text = (PORTALL / "pipeline.cpp").read_text()
    draw = "    this->draw_decoded_(drawing.frame, drawing.pixels, drawing.padded_width, drawing.padded_height);\n"
    give = "    xQueueSend(this->spare_queue_, &drawing.pixels, portMAX_DELAY);\n"
    assert text.count(draw) == 1 and text.count(give) == 1, "run_draw_task changed; update this tool"
    a = text.index(draw)
    b = text.index(give)
    assert a < b
    text = text.replace(give, "").replace(draw, give + draw)
    (into / "pipeline.cpp").write_text(text)
    return into / "pipeline.cpp"


def build(esphome_dir, portall_cpp, pipeline_cpp, out):
    inc = [f"-I{HERE / 'stub'}", f"-I{esphome_dir}", f"-I{PORTALL}",
           f"-I{ROOT / 'components' / 'usb_display_tusb'}"]
    sources = [HERE / "harness.cpp", HERE / "rtos.cpp", HERE / "core.cpp", portall_cpp, pipeline_cpp,
               esphome_dir / "esphome" / "components" / "display" / "display.cpp",
               esphome_dir / "esphome" / "components" / "display" / "rect.cpp"]
    cmd = ["g++", "-std=gnu++20", "-O1", "-pthread", "-Wall", "-Wno-unused", *inc, *map(str, sources), "-o",
           str(out)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        print(done.stderr[-4000:])
        return False
    return True


def syntax(esphome_dir, lvgl):
    """Both files, with and without canvas mode, which this change rearranged."""
    good = True
    for label, extra in (("plain", []), ("canvas", ["-DUSE_LVGL", "-DLV_CONF_SKIP", f"-I{lvgl}"] if lvgl else None)):
        if extra is None:
            print(f"  ?      {label}: no LVGL given (--lvgl), canvas mode not compiled")
            continue
        for name in ("portall.cpp", "pipeline.cpp"):
            cmd = ["g++", "-std=gnu++20", "-fsyntax-only", "-Wall", "-Wextra", f"-I{HERE / 'stub'}",
                   f"-I{esphome_dir}", f"-I{PORTALL}", f"-I{ROOT / 'components' / 'usb_display_tusb'}", *extra,
                   str(PORTALL / name)]
            done = subprocess.run(cmd, capture_output=True, text=True)
            ours = [l for l in done.stderr.splitlines()
                    if ("error" in l or "warning" in l) and str(PORTALL) in l]
            if done.returncode != 0 or ours:
                good = False
                print(f"  ECHEC  {label}: {name}")
                for line in (ours or done.stderr.splitlines())[-8:]:
                    print("         " + line)
            else:
                print(f"  ok     {label}: {name} compiles, no warning of ours")
    return good


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--esphome", help="directory holding the esphome package (ESPHOME_SRC)")
    parser.add_argument("--lvgl", default=os.environ.get("LVGL"), help="an LVGL 9.5.0 tree, for canvas mode")
    args = parser.parse_args()

    root = find_esphome(args.esphome)
    if root is None or not (root / "esphome" / "core" / "component.h").exists():
        print("No esphome to build against: pip install esphome, or give --esphome DIR")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        es = stage(root, tmp / "es")
        print("Compiling the shipped files:")
        good = syntax(es, args.lvgl)

        print("\nRunning them:")
        (tmp / "real").mkdir()
        binary = tmp / "pipetest"
        if not build(es, patched_portall(tmp / "real"), PORTALL / "pipeline.cpp", binary):
            print("  ECHEC  the harness did not build")
            return 1
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=300)
        print(run.stdout.rstrip())
        good = good and run.returncode == 0

        print("\nThe torn-buffer case against a pipeline that gives a buffer back before drawing it:")
        (tmp / "broken").mkdir()
        broken = tmp / "broken" / "pipetest"
        if not build(es, patched_portall(tmp / "broken"), broken_pipeline(tmp / "broken"), broken):
            print("  ECHEC  the broken copy did not build")
            return 1
        run = subprocess.run([str(broken), "--quick"], capture_output=True, text=True, timeout=300)
        caught = "ECHEC  no buffer is written while the display reads it, with the display the slower" in run.stdout
        print(f"  {'ok    ' if caught else 'ECHEC '} it fails, so the case can fail")
        good = good and caught

    print("\nAll good." if good else "\nProblems above.")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
