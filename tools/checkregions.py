#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
"""Keep independent moving regions small, without losing changed pixels.

    python3 tools/checkregions.py [--sender PATH]
    python3 tools/checkregions.py --browser /usr/bin/google-chrome

The old vertical merge only looked at the last rectangle. Two moving columns
therefore became dozens of strips, and their fixed decode cost made the
sender replace the whole panel. Fragmented changes within a small window
also chose the whole panel without considering that window's bounding box.
"""
import argparse
import functools
import http.server
import importlib.util
import io
import os
import pathlib
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent


REGIONS_PAGE = """<!doctype html><style>
html,body{margin:0;background:#111;width:100%;height:100%}
video{position:absolute;top:128px;width:128px;height:1024px;object-fit:fill}
#v0{left:64px}#v1{left:576px}</style>
<video id=v0 autoplay muted playsinline></video><video id=v1 autoplay muted playsinline></video>
<script>
const c=document.createElement('canvas');c.width=128;c.height=1024;const ctx=c.getContext('2d');
const tex=document.createElement('canvas');tex.width=128;tex.height=1024;const tx=tex.getContext('2d');
const p=tx.createImageData(128,1024);let seed=17;
for(let y=0;y<1024;y++)for(let x=0;x<128;x++){
  const i=(y*128+x)*4;seed=(1664525*seed+1013904223)>>>0;
  p.data[i]=(x*2+y)%256;p.data[i+1]=(x+y*2)%256;p.data[i+2]=(seed>>>25)+70;p.data[i+3]=255;
}tx.putImageData(p,0,0);
let offset=0;function draw(){offset=(offset+7)%1024;ctx.drawImage(tex,0,-offset);ctx.drawImage(tex,0,1024-offset);}
draw();const stream=c.captureStream(50);for(const v of [v0,v1]){v.srcObject=stream;v.play();}setInterval(draw,20);
</script>"""


def browser_check(sender, browser, check):
    """The actual sender and wire headers, with two local video windows."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory() as folder:
        folder = pathlib.Path(folder)
        (folder / "index.html").write_text(REGIONS_PAGE)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
            functools.partial(Quiet, directory=str(folder)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        header = struct.Struct("<HBBHHHHI")
        ready = threading.Event()
        arrivals, updates, decoded = [], [], []

        def receive():
            connection, _ = listener.accept()
            data, last = bytearray(), None
            with connection:
                while True:
                    try:
                        chunk = connection.recv(65536)
                    except OSError:
                        return
                    if not chunk:
                        return
                    data.extend(chunk)
                    while len(data) >= header.size:
                        _, kind, _, x, y, w, h, packed = header.unpack_from(data)
                        length = packed >> 10
                        if len(data) < header.size + length:
                            break
                        if kind == 3:
                            payload = bytes(data[header.size:header.size + length])
                            try:
                                with Image.open(io.BytesIO(payload)) as picture:
                                    picture.load()
                                    decoded.append((picture.size == (w, h), (w, h) == (800, 1280)))
                            except (OSError, ValueError):
                                decoded.append((False, False))
                            now, frame = time.monotonic(), packed & 1023
                            if frame != last:
                                arrivals.append(now)
                                last = frame
                                ready.set()
                            updates.append((now, w * h, length))
                        del data[:header.size + length]

        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        command = [sys.executable, "-u", str(sender), "--host", "127.0.0.1",
                   "--port", str(listener.getsockname()[1]),
                   "--url", f"http://127.0.0.1:{server.server_address[1]}/index.html",
                   "--no-token", "--not-home-assistant", "--width", "800", "--height", "1280",
                   "--fps", "50", "--quality", "80", "--audio", "off", "--keyboard", "off",
                   "--no-refine", "--browser", browser]
        try:
            with (folder / "sender.log").open("w") as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    if not ready.wait(30):
                        raise RuntimeError("No picture from the sender: " + (folder / "sender.log").read_text()[-2000:])
                    time.sleep(4)
                    start = time.monotonic()
                    time.sleep(6)
                    end = time.monotonic()
                finally:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                    receiver.join(timeout=5)
                    if receiver.is_alive():
                        raise RuntimeError("Picture receiver did not finish after the sender exited")
            count = sum(start <= t < end for t in arrivals)
            pixels = sum(area for t, area, _ in updates if start <= t < end)
            size = sum(size for t, _, size in updates if start <= t < end)
            per_picture = pixels / max(count, 1)
            check("real video windows keep delivering pictures", count >= 10,
                  f"{count / (end - start):.1f} pictures/s")
            check("the sender's full-frame and region JPEGs decode to their wire dimensions",
                  bool(decoded) and all(valid for valid, _ in decoded) and any(full for _, full in decoded),
                  f"{len(decoded)} JPEGs, {sum(full for _, full in decoded)} full-frame")
            check("real sender avoids unnecessary whole-screen decodes", per_picture < 800 * 1280 * .8,
                  f"{per_picture:.0f} pixels/picture; {size / (1024 * (end - start)):.1f} KiB/s")
        finally:
            listener.close()
            server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sender", type=pathlib.Path,
                        default=ROOT / "components" / "portall" / "ha_send.py")
    parser.add_argument("--browser", help="also test the real sender with local video windows")
    args = parser.parse_args()
    sys.path.insert(0, str(args.sender.resolve().parent))
    spec = importlib.util.spec_from_file_location("sender_under_test", args.sender)
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    failures = 0

    def check(label, good, detail=""):
        nonlocal failures
        print(("  ok     " if good else "  ECHEC  ") + label + (f" ({detail})" if detail else ""))
        failures += not good

    def old_choice(rects, width, height, cost):
        # The original main loop's decision, for --sender pointing at 4.51.1.
        if sum(w * h for _, _, w, h in rects) / (width * height) + cost * len(rects) > 1:
            return [(0, 0, width, height)]
        return rects

    choose = getattr(sender, "choose_rectangles", old_choice)
    width, height = 800, 1280
    cost = sender.rect_cost_fraction(width, height)
    mask = np.zeros((20, 13), dtype=bool)
    mask[2:18, 1:3] = mask[2:18, 9:11] = True
    rects = sender.tile_rectangles(mask, width, height)
    check("two moving columns merge independently", rects == [(64, 128, 128, 1024), (576, 128, 128, 1024)],
          f"{len(rects)} rectangles")
    selected = choose(rects, width, height, cost)
    pixels = sum(w * h for _, _, w, h in selected)
    check("two columns do not redraw the whole panel", pixels == 262144,
          f"{pixels} of {width * height} pixels")

    mask[:] = False
    mask[5:13, 3:9] = np.indices((8, 6)).sum(axis=0) % 2 == 0
    selected = choose(sender.tile_rectangles(mask, width, height), width, height, cost)
    check("fragmented motion redraws its window, not the whole panel",
          selected == [(192, 320, 384, 512)], str(selected))
    sparse = [(64, 128, 64, 64), (640, 1024, 64, 64)]
    check("already-cheap sparse updates stay sparse", choose(sparse, width, height, cost) == sparse)
    check("an unchanged page stays silent", choose([], width, height, cost) == [])
    check("a full-screen change still sends a full screen",
          choose([(0, 0, width, height)], width, height, cost) == [(0, 0, width, height)])

    mask[:] = False
    mask[0, 1] = mask[2, 1] = True
    check("rows separated by an unchanged row are not joined",
          sender.tile_rectangles(mask, width, height) == [(64, 0, 64, 64), (64, 128, 64, 64)])

    rng = np.random.default_rng(4512)
    cases = 0
    valid = True
    for h, w in [(1280, 800), (600, 1024), (1277, 799), (65, 67), (31, 33)]:
        previous = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        for _ in range(30):
            current = previous.copy()
            for _ in range(10):
                y, x = int(rng.integers(h)), int(rng.integers(w))
                current[y:min(y + 70, h), x:min(x + 40, w)] ^= 255
            rects = choose(sender.changed_rectangles(previous, current), w, h,
                           sender.rect_cost_fraction(w, h))
            restored = previous.copy()
            for x, y, rw, rh in rects:
                valid &= 0 <= x < x + rw <= w and 0 <= y < y + rh <= h
                valid &= rw >= min(w, sender.MIN_RECT) and rh >= min(h, sender.MIN_RECT)
                restored[y:y + rh, x:x + rw] = current[y:y + rh, x:x + rw]
            valid &= np.array_equal(restored, current)
            cases += 1
    check("every changed pixel is delivered within bounds, including panel edges", valid,
          f"{cases} cases")

    if args.browser:
        browser_check(args.sender.resolve(), args.browser, check)
    print("all good" if not failures else f"{failures} failure(s)")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
