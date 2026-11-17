#!/usr/bin/env python3
"""Exercise Portall's optional direct decode with the real driver's ownership state.

  python tools/checkdirectpanel.py --driver /path/to/lvgl_9.5 --esphome /path/to/esphome

Compiles the shipped Portall files and mipi_dsi_direct.h, with a fake JPEG
engine and a scanning panel on real threads. This tests ownership and pixel
preservation, not hardware speed. The driver itself also needs a P4 build.
"""
import argparse
import pathlib
import shutil
import subprocess
import tempfile

import checkpipeline as base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=pathlib.Path, required=True)
    parser.add_argument("--esphome")
    args = parser.parse_args()
    esphome = base.find_esphome(args.esphome)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        es = base.stage(esphome, tmp / "es")
        inc = es / "esphome/components/mipi_dsi"
        inc.mkdir()
        header = args.driver / "components/mipi_dsi/mipi_dsi_direct.h"
        shutil.copy(header, inc)
        sources = [base.ROOT / "tools/directpaneltest/harness.cpp", base.HERE / "rtos.cpp", base.HERE / "core.cpp",
                   *(base.PORTALL / name for name in ("portall.cpp", "pipeline.cpp", "direct_panel.cpp")),
                   es / "esphome/core/color.cpp", es / "esphome/components/display/display.cpp",
                   es / "esphome/components/display/rect.cpp"]
        binary = tmp / "directtest"
        command = ["g++", "-std=gnu++20", "-O1", "-pthread", "-DPORTALL_TEST_DIRECT", "-Wall", "-Wno-unused",
                   f"-I{base.HERE / 'stub'}", f"-I{es}", f"-I{base.PORTALL}",
                   f"-I{base.ROOT / 'components/usb_display_tusb'}", *map(str, sources), "-o", str(binary)]
        subprocess.run(command, check=True)
        for extra in ([], ["--disabled"]):
            if subprocess.run([str(binary), *extra], timeout=30).returncode:
                return 1

        # These must fail the real test, rather than merely look plausible:
        # a buffer recycled after one ISR, and a swap before earlier DMA blits.
        original = header.read_text()
        assert original.count("std::memory_order_acquire) + 2;") == 1
        (inc / header.name).write_text(original.replace("std::memory_order_acquire) + 2;",
                                                       "std::memory_order_acquire) + 1;"))
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        caught = result.returncode != 0 and "ECHEC  one completion cannot retire a buffer" in result.stdout
        print("  " + ("ok     " if caught else "ECHEC  ") + "the test catches recycling a scan-out buffer one ISR too early", flush=True)
        if not caught:
            return 1
        (inc / header.name).write_text(original)

        direct = (base.PORTALL / "direct_panel.cpp").read_text()
        barrier = """  while (this->draws_finished_.load(std::memory_order_acquire) !=
         this->draws_queued_.load(std::memory_order_relaxed))
    vTaskDelay(1);
"""
        assert direct.count(barrier) == 1
        mutant = tmp / "direct_panel.cpp"
        mutant.write_text(direct.replace(barrier, ""))
        command[command.index(str(base.PORTALL / "direct_panel.cpp"))] = str(mutant)
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        caught = result.returncode != 0 and "ECHEC  whole frames and rectangles reach the display in order" in result.stdout
        print("  " + ("ok     " if caught else "ECHEC  ") + "the test catches a swap before the preceding rectangles finish", flush=True)
        return 0 if caught else 1


if __name__ == "__main__":
    raise SystemExit(main())
