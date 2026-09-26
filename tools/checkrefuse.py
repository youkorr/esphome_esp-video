#!/usr/bin/env python3
"""The panel's sound against a speaker that will not stay started.

A mixer source its mixer refuses -- a sample rate other than the one the mixer
already runs at -- starts itself from every play() and is back to stopped
inside one turn of ESPHome's loop, making a ring buffer and throwing it away
each time. Fed fifty blocks a second that rebooted a panel. This compiles the
SHIPPED audio.cpp against tools/audiotest/refuse.cpp, which counts the starts.

    python3 tools/checkrefuse.py            the working tree
    python3 tools/checkrefuse.py --ref REV  audio.cpp as it was at REV
"""
import subprocess
import sys

import checkstereo


def main():
    source = None
    if "--ref" in sys.argv:
        rev = sys.argv[sys.argv.index("--ref") + 1]
        source = subprocess.run(
            ["git", "show", f"{rev}:components/portall/audio.cpp"],
            cwd=checkstereo.ROOT, capture_output=True, text=True,
            check=True).stdout
    print("A speaker that will not stay started:")
    checkstereo.board_cases("refuse.cpp", source)
    if checkstereo.faults:
        print(f"\n{len(checkstereo.faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
