#!/usr/bin/env python3
"""The page's sound at the rate the panel asks for, checked at both ends.

A panel whose speaker feeds a mixer at 44100 used to need a resampler on the
panel to take the browser's 48000 down to it -- and a wake word listening
beside that resampler starved it into dropped blocks. `sample_rate:` on
portall moves the conversion to the add-on: the board asks for its rate on the
return channel (b"A", kilohertz, 50 Hz steps), the sender captures at it, and
each block says what it is in its header's height field.

Two ends, two languages, one field and one message between them -- so all of
it is run rather than read:

  * the wire: a 48000 header is byte for byte what it always was, and a
    sender from BEFORE this skips the new message without losing the touch
    that follows it -- the tolerance the whole design leans on;
  * the board: the SHIPPED audio.cpp compiled against tools/audiotest/rate.cpp;
  * the sender, end to end when PulseAudio and a Chromium are here: a page
    playing 440 Hz, a fake panel that asks for 44100, and the samples that
    come back measured -- if they were captured at 48000 and only LABELLED
    44100, the tone would read 479 Hz.

    python3 tools/checksamplerate.py [--browser PATH] [--sender PATH]

What it cannot check: that ESP-IDF's build accepts the C++, and that a real
mixer takes the stream. The board half is a stand-in for the rest of the class.
"""

import collections
import functools
import http.server
import math
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))
sys.path.insert(0, str(ROOT / "tools"))

import udisp_send  # noqa: E402
import checkstereo  # noqa: E402

# The last commit before the rate could be asked for: its parser is what an
# add-on that has not been updated runs.
BEFORE = "17599e6"
HEADER = struct.Struct("<HBBHHHHI")
LETTERS = {ord(c) for c in "TSHKA"}

faults = checkstereo.faults
check = checkstereo.check


def option(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


BROWSER = option("--browser", os.environ.get("CHROMIUM", ""))
# --sender runs the end-to-end half against another copy -- an older one, to
# see it fail.
SENDER = option("--sender", str(ROOT / "components" / "portall" / "ha_send.py"))


def rates_offered():
    """sample_rate:'s choices, read off the component rather than restated."""
    text = (ROOT / "components" / "portall" / "__init__.py").read_text()
    found = re.search(r"SAMPLE_RATES = \(([^)]*)\)", text)
    return [int(x) for x in found.group(1).split(",") if x.strip()]


def board_message(rate):
    """The three bytes network.cpp sends, by its own arithmetic."""
    return bytes([ord("A"), rate // 1000, (rate % 1000) // 50])


def wire_cases():
    before = struct.pack("<HBBHHHHI", 0, 0x10, 0, 0, 0, 0, 0, 1920 << 10)
    check("a 48000 header is byte for byte what it always was",
          udisp_send.build_audio_header(1920) == before)
    fields = HEADER.unpack(udisp_send.build_audio_header(1764, 1, 44100))
    check("a 44100 header carries 44100 in its height", fields[6] == 44100)
    check("and its width and length are untouched",
          fields[5] == 0 and fields[7] >> 10 == 1764)
    for rate in rates_offered():
        message = board_message(rate)
        parsed, rest = udisp_send.parse_messages(message)
        check(f"{rate} crosses as {list(message[1:])} and comes back {rate}",
              parsed == [("rate", rate)] and rest == b"")
        check(f"and neither byte of it is a letter a parser looks for",
              not (set(message[1:]) & LETTERS))
    parsed, rest = udisp_send.parse_messages(b"A,")
    check("a message cut short waits for the rest of it",
          parsed == [] and rest == b"A,")

    # An add-on that has not been updated, reading the same bytes.
    old = subprocess.run(
        ["git", "show", f"{BEFORE}:components/portall/udisp_send.py"],
        cwd=ROOT, capture_output=True, text=True)
    if old.returncode != 0:
        check("the parser from before could be read out of git", False)
        return
    module = types.ModuleType("udisp_send_before")
    exec(compile(old.stdout, "udisp_send_before.py", "exec"), module.__dict__)
    touch = b"T\x01\x00\x10\x00\x20\x00"
    parsed, _ = module.parse_messages(board_message(44100) + touch)
    check("a sender from before skips it and still reads the touch after it",
          parsed == [("touch", [(0, 16, 32)])])


def sender_cases():
    from ha_send import PageAudio, PanelWriter  # noqa: PLC0415

    audio = PageAudio("salon")
    check("a capture starts at 48000", audio.rate == 48000
          and audio.block == 1920)
    check("told 44100 it switches", audio.set_rate(44100))
    check("and its 20 ms blocks are 882 frames, 1764 bytes",
          audio.block == 1764)
    check("told the same again it does nothing", not audio.set_rate(44100))
    check("a stereo capture at 44100 takes 3528-byte blocks",
          PageAudio("salon", 2, 44100).block == 3528)

    class Sink:
        def write(self, data):
            pass

    writer = PanelWriter(Sink())
    writer.audio_rate = 48000
    held = threading.Event()
    with writer._wake:
        writer.offer_audio(b"\x01\x00" * 960)
        writer.audio_rate = 44100
        writer.offer_audio(b"\x01\x00" * 882)
        queued = list(writer._audio)
        writer._audio.clear()
        held.set()
    heights = [HEADER.unpack(block[:16])[6] for block in queued]
    check("a block keeps the rate it was handed over at, "
          "whatever the capture does next", heights == [0, 44100])


def fake_panel(ask, heard):
    """A panel that asks for `ask` and keeps the PCM that comes back."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        conn.sendall(board_message(ask))
        buffer = b""
        want = 0
        pending = None
        try:
            while True:
                chunk = conn.recv(1 << 16)
                if not chunk:
                    return
                buffer += chunk
                while True:
                    if want == 0:
                        if len(buffer) < 16:
                            break
                        fields = HEADER.unpack(buffer[:16])
                        buffer = buffer[16:]
                        want = fields[7] >> 10
                        pending = (fields[1], fields[6], bytearray())
                        if want == 0:
                            continue
                    take = min(want, len(buffer))
                    pending[2].extend(buffer[:take])
                    buffer = buffer[take:]
                    want -= take
                    if want:
                        break
                    if pending[0] == 0x10:
                        heard.append((pending[1], bytes(pending[2])))
        except OSError:
            return
    threading.Thread(target=accept, daemon=True).start()
    return listener.getsockname()[1]


PAGE = """<!doctype html><body style="background:#123">
<script>
const ctx = new AudioContext();
const osc = ctx.createOscillator();
const gain = ctx.createGain();
osc.frequency.value = 440; gain.gain.value = 0.3;
osc.connect(gain).connect(ctx.destination); osc.start();
</script>"""


def goertzel(samples, rate, freq):
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def end_to_end():
    if not (shutil.which("parec") and shutil.which("pactl")):
        print("  --     end to end skipped: no PulseAudio here")
        return
    if subprocess.run(["pactl", "info"], capture_output=True).returncode:
        print("  --     end to end skipped: no PulseAudio server running")
        return
    folder = tempfile.mkdtemp()
    (pathlib.Path(folder) / "index.html").write_text(PAGE)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=folder))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    heard = []
    port = fake_panel(44100, heard)
    command = [sys.executable, "-u", str(SENDER), "--host", "127.0.0.1",
               "--port", str(port),
               "--url", f"http://127.0.0.1:{server.server_address[1]}/",
               "--no-token", "--not-home-assistant",
               "--width", "320", "--height", "240", "--keyboard", "off"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    time.sleep(14)
    process.terminate()
    out = process.communicate(timeout=10)[0]
    server.shutdown()

    by_rate = collections.Counter(rate for rate, _ in heard)
    check(f"the sender switches when the panel asks ({dict(by_rate)})",
          by_rate.get(44100, 0) >= 100)
    check("and says so in its log", "at 44100 Hz" in out)
    late = [i for i, (rate, _) in enumerate(heard) if rate == 0]
    check("48000 goes out only before the switch, never after it",
          not late or max(late) < min(
              (i for i, (rate, _) in enumerate(heard) if rate == 44100),
              default=0))
    blocks = [data for rate, data in heard if rate == 44100]
    check("every 44100 block is 20 ms of mono: 1764 bytes",
          blocks and all(len(b) == 1764 for b in blocks))
    samples = []
    for data in blocks[-50:]:
        samples.extend(struct.unpack(f"<{len(data) // 2}h", data))
    if not samples:
        check("the tone could be measured", False)
        return
    at_440 = goertzel(samples, 44100, 440)
    at_479 = goertzel(samples, 44100, 440 * 48000 / 44100)
    check(f"the samples really are 44100: the tone reads 440 Hz, not 479 "
          f"({at_440 / max(at_479, 1):.0f} times the energy)",
          at_440 > 20 * at_479)


def main():
    print("The page's sound at the panel's own rate:")
    wire_cases()
    sender_cases()
    checkstereo.board_cases("rate.cpp")
    end_to_end()
    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
