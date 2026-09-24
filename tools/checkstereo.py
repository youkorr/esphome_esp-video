#!/usr/bin/env python3
"""Stereo for the page's sound, checked at both ends of the wire.

The page's sound was mono end to end, and stereo is ONE setting in the add-on:
the sender captures two channels and says so in the PCM header's width field,
and the board re-tells its speaker the shape when it changes. Two ends, two
languages, one field between them -- the shape every fault this repository
keeps recording has lived in. So both ends are run here, not read:

  * the sender half in Python: the header, the capture's block size, the
    volume scaling on interleaved samples, and the add-on's command line;
  * the board half by compiling the SHIPPED audio.cpp with g++ against a
    stand-in for the rest of the Portall class, and driving it through a
    recording speaker (tools/audiotest/).

What it cannot check: that the ESP-IDF build accepts the C++ (no toolchain
here), and that a speaker, a mixer and a resampler behind portall carry two
channels -- that is the YAML's, and it is num_channels: 2 on the last speaker.
"""

import pathlib
import re
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))
sys.path.insert(0, str(ROOT / "portall"))

import udisp_send  # noqa: E402
from ha_send import PageAudio, scale_pcm  # noqa: E402

faults = []


def check(what, ok):
    print(("  ok     " if ok else "  ECHEC  ") + what)
    if not ok:
        faults.append(what)


def wire_cases():
    # What every sender before stereo sent, packed here by hand rather than
    # through the function under test.
    before = struct.pack("<HBBHHHHI", 0, 0x10, 0, 0, 0, 0, 0, 1920 << 10)
    check("a mono header is byte for byte what it always was",
          udisp_send.build_audio_header(1920) == before)
    fields = struct.unpack("<HBBHHHHI", udisp_send.build_audio_header(3840, 2))
    check("a stereo header carries 2 in its width field",
          fields[5] == 2 and fields[1] == 0x10)
    check("and its length is the payload's, as ever",
          fields[7] >> 10 == 3840)
    try:
        udisp_send.build_audio_header(100, 3)
        refused = False
    except ValueError:
        refused = True
    check("three channels are refused rather than sent", refused)


def sender_cases():
    check("a mono capture takes 20 ms blocks of 1920 bytes",
          PageAudio("salon").block == 1920)
    check("a stereo one takes 20 ms blocks of 3840 bytes",
          PageAudio("salon", 2).block == 3840)
    left_right = struct.pack("<4h", 1000, -2000, 400, -800)
    check("the volume scales both channels alike",
          struct.unpack("<4h", scale_pcm(left_right, 0.5))
          == (500, -1000, 200, -400))


def addon_cases():
    import run  # noqa: PLC0415

    base = {"name": "salon", "host": "10.0.0.2", "url": "http://x",
            "width": 800, "height": 1280}
    check("stereo: true on a panel puts --stereo on its sender",
          "--stereo" in run.command_for(dict(base, stereo=True)))
    check("and a panel that did not ask stays mono",
          "--stereo" not in run.command_for(dict(base)))


def board_cases():
    """The shipped audio.cpp, compiled and switched between shapes."""
    header = (ROOT / "components" / "portall" / "portall.h").read_text()
    constants = "\n".join(line for line in header.splitlines()
                          if re.match(r"#define PORTALL_AUDIO_", line))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "audio_constants.h").write_text("#pragma once\n" + constants + "\n")
        (tmp / "portall.h").write_text(
            (ROOT / "tools" / "audiotest" / "portall.h").read_text())
        # A copy, beside the stand-in header, so its own #include "portall.h"
        # finds the stand-in rather than the real one next to the original.
        (tmp / "audio.cpp").write_text(
            (ROOT / "components" / "portall" / "audio.cpp").read_text())
        flags = ["-std=gnu++20", "-Wall", "-DUSE_SPEAKER", f"-I{tmp}",
                 f"-I{ROOT / 'tools' / 'btstub'}"]
        built = subprocess.run(
            ["g++", *flags, str(tmp / "audio.cpp"),
             str(ROOT / "tools" / "audiotest" / "switch.cpp"),
             "-o", str(tmp / "switch")],
            capture_output=True, text=True)
        if built.returncode != 0:
            check("audio.cpp compiles against the stand-in", False)
            print(built.stderr[-2000:])
            return
        ran = subprocess.run([str(tmp / "switch")], capture_output=True,
                             text=True)
        # The component's own log lines carry no newline under the stand-in,
        # so each result starts on a line of its own and only those are read.
        for line in ran.stdout.splitlines():
            if line.startswith("  ok"):
                check("board: " + line[len("  ok"):].strip(), True)
            elif line.startswith("  ECHEC"):
                check("board: " + line[len("  ECHEC"):].strip(), False)
        if ran.returncode != 0 and not faults:
            check("the board harness ran to the end", False)


def main():
    print("Stereo:")
    wire_cases()
    sender_cases()
    addon_cases()
    board_cases()
    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
