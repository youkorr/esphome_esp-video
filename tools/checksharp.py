#!/usr/bin/env python3
"""The finished picture: a still screen is sent once more, sharp.

WHY THIS EXISTS. Reported as icons, buttons, the wallpaper and the screen
saver's photographs never being sharp, with the quality already raised to 95
in the hope it was the connection. It was not: every picture went through two
JPEGs that both halve the colour's resolution (Chromium's screencast does so
at any quality), and the byte rate lowered the quality during motion and left
the last picture there. See REFINE_AFTER_S in ha_send.py.

This runs the shipped sender against a fake panel that keeps every picture it
is sent, on a page of coloured text and a coloured icon over blue, and checks:

  - once the page is still, a whole picture arrives in full colour (4:4:4);
  - the panel is then measurably closer to the page than before it;
  - a still screen is sent nothing more after it, whatever the browser does;
  - a change goes out as before, and is finished again once it stops;
  - a panel that reconnects is sent the finished picture, not the soft one;
  - --no-refine sends none, and the add-on's sharp: false is --no-refine;
  - a cap the picture cannot meet in full colour is met lower and said once.

--sender PATH runs another copy of ha_send.py. Needs Playwright and Pillow.
"""
import http.server
import io
import math
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
W, H = 640, 400
# The part of the page that is text and icon, away from the corner mark.
BOX = (120, 80, 620, 380)
fails = 0

PAGE = """<!doctype html><html><body style="margin:0;height:100vh;
background:#1e3c8c;font:600 20px/1.4 sans-serif" onclick="
document.getElementById('n').textContent='Changed'">
<div style="position:absolute;left:130px;top:90px">
<svg width="90" height="90" viewBox="0 0 90 90">
<circle cx="45" cy="45" r="40" fill="#e53935"/>
<rect x="25" y="25" width="40" height="40" fill="#fdd835"/>
<circle cx="45" cy="45" r="10" fill="#43a047"/></svg>
<div id="n" style="color:#fff">Jellyfin</div>
<div style="color:#ff5252">YouTube 21 C</div>
<div style="color:#ffeb3b">Home Assistant</div>
<div style="color:#69f0ae;font-size:14px">mercredi 7 octobre 2026</div>
</div></body></html>"""


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def reference(url):
    """The page as the browser draws it, without loss."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"executable_path": BROWSER} if BROWSER else {}
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.goto(url)
        pg.wait_for_timeout(300)
        shot = pg.screenshot()
        b.close()
    return np.asarray(Image.open(io.BytesIO(shot)).convert("RGB"))


def psnr(a, b):
    x0, y0, x1, y1 = BOX
    d = (a[y0:y1, x0:x1].astype(float) - b[y0:y1, x0:x1].astype(float))
    return 10 * math.log10(255 ** 2 / max(1e-9, (d ** 2).mean()))


class Panel:
    """A fake panel: keeps the picture and every JPEG it is sent."""

    def __init__(self):
        from udisp_send import _HEADER, UDISP_TYPE_JPG
        self.header, self.jpg = _HEADER, UDISP_TYPE_JPG
        self.canvas = Image.new("RGB", (W, H))
        self.lock = threading.Lock()
        self.got = []        # (time, x, y, w, h, full colour?, bytes)
        self.conn = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.conn = conn
            self._read(conn)

    def _read(self, conn):
        stream = b""
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                stream += data
                while len(stream) >= self.header.size:
                    _, kind, _, x, y, w, h, packed = \
                        self.header.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < self.header.size + total:
                        break
                    payload = stream[self.header.size:self.header.size + total]
                    stream = stream[self.header.size + total:]
                    if kind != self.jpg:
                        continue
                    pic = Image.open(io.BytesIO(payload))
                    full = pic.layer[0][1:3] == (1, 1)
                    with self.lock:
                        self.canvas.paste(pic.convert("RGB"), (x, y))
                        self.got.append((time.monotonic(), x, y, w, h, full,
                                         len(payload)))
        except OSError:
            pass

    def picture(self):
        with self.lock:
            return np.asarray(self.canvas.copy())

    def since(self, t):
        with self.lock:
            return [g for g in self.got if g[0] >= t]

    def tap(self):
        x, y = 300, 200
        self.conn.sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                          + y.to_bytes(2, "little"))
        time.sleep(0.08)
        self.conn.sendall(b"T\x00")


def run(url, *extra, width=W):
    panel = Panel()
    work = tempfile.mkdtemp()
    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(panel.port), "--url", url,
               "--not-home-assistant", "--width", str(width), "--height", str(H),
               "--audio", "off", "--keyboard", "off", "--no-nav-bar",
               "--profile", os.path.join(work, "p"), "--quality", "80",
               "--stats", *extra]
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    return panel, process, out


def wait_for(test, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.05)
    return False


def whole(g):
    return g[3] == W and g[4] == H


def addon_half():
    import run
    base = {"name": "salon", "host": "1.2.3.4", "url": "http://x/"}
    check("the add-on sends nothing extra by default",
          "--no-refine" not in run.command_for(dict(base)))
    check("sharp: false is --no-refine",
          "--no-refine" in run.command_for(dict(base, sharp=False)))
    check("sharp: true is the default, nothing extra",
          "--no-refine" not in run.command_for(dict(base, sharp=True)))


def sender_half():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    ref = reference(url)

    panel, process, out = run(url)
    try:
        ok = wait_for(lambda: any(whole(g) for g in panel.since(0)), 40)
        check("the page reaches the panel", ok, "".join(out[-12:]))
        if not ok:
            return
        first = next(g for g in panel.since(0) if whole(g))
        soft = None
        # The first whole picture is the soft one; read the panel right
        # after it, before anything else lands.
        if not first[5]:
            soft = psnr(panel.picture(), ref)
        check("the first picture is the moving one, colour halved",
              not first[5])
        ok = wait_for(lambda: any(whole(g) and g[5]
                                  for g in panel.since(0)), 5)
        check("once still, a whole picture arrives in full colour", ok)
        sharp_at = next((g[0] for g in panel.since(0) if whole(g) and g[5]),
                        None)
        time.sleep(0.3)
        sharp = psnr(panel.picture(), ref)
        check("and the panel is then measurably closer to the page "
              "(+3 dB or more on the text and the icon)",
              soft is not None and sharp >= soft + 3,
              f"{soft} -> {sharp:.1f} dB")
        print(f"         text and icon: {soft:.1f} dB before, "
              f"{sharp:.1f} dB after" if soft is not None else "")
        # The corner mark fades 1.5 s after arrival, which is a change and
        # is finished again; from there the page is still.
        time.sleep(2.5)
        quiet = time.monotonic()
        time.sleep(3.0)
        later = panel.since(quiet)
        check("a still screen is sent nothing more", not later,
              [(g[3], g[4], g[5]) for g in later])

        # A change: it goes out as rectangles, then is finished again.
        mark = time.monotonic()
        panel.tap()
        ok = wait_for(lambda: any(not g[5] for g in panel.since(mark)), 5)
        check("a change goes out as before, colour halved", ok)
        ok = wait_for(lambda: any(g[5] for g in panel.since(mark)), 5)
        check("and is finished again once it stops", ok)
        sharp = [g for g in panel.since(mark) if g[5]]
        # Only where it changed: everything else on the glass is already
        # sharp, and sending it all again after every pause was the slowdown
        # reported after 4.46 -- a whole panel on the link and the board each
        # time a hand stopped.
        check("and only where it changed, not the whole panel",
              sharp and not any(whole(g) for g in sharp),
              [(g[3], g[4], g[6]) for g in sharp])
        check("and not while a hand may still be at the panel "
              "(1.5 s after the tap)",
              sharp and sharp[0][0] - mark >= 1.4,
              sharp and round(sharp[0][0] - mark, 2))

        # A panel that comes back is owed a whole picture: the finished one.
        time.sleep(1.0)
        mark = time.monotonic()
        panel.conn.close()
        ok = wait_for(lambda: any(whole(g) for g in panel.since(mark)), 15)
        firsts = [g for g in panel.since(mark) if whole(g)]
        check("a panel that reconnects is sent the finished picture",
              ok and firsts and firsts[0][5],
              [(g[5], g[6]) for g in firsts[:2]])
        time.sleep(5.5)
        check("and --stats counts them",
              any(", " in o and " sharp" in o for o in out),
              [o for o in out if "pictures/s" in o][-2:])
    finally:
        process.kill()

    # Turned off.
    panel, process, out = run(url, "--no-refine")
    try:
        wait_for(lambda: any(whole(g) for g in panel.since(0)), 40)
        time.sleep(4.0)
        check("--no-refine sends no full-colour picture",
              panel.since(0) and not any(g[5] for g in panel.since(0)))
    finally:
        process.kill()

    # A cap the full-colour picture cannot meet.
    panel, process, out = run(url, "--refine-bytes", "9000")
    try:
        wait_for(lambda: any(whole(g) for g in panel.since(0)), 40)
        time.sleep(3.0)
        big = [g for g in panel.since(0) if whole(g)][1:]
        said = [o for o in out if o.startswith("Sharp:")]
        # Whether anything fits 9000 bytes depends on how this browser draws
        # the text; either a picture under the cap or none at all is right,
        # and the line below says which.
        check("a cap is never exceeded by the finished picture",
              all(g[6] <= 9000 for g in big),
              str([(g[3], g[4], g[5], g[6]) for g in panel.since(0)]))
        check("and what it cost is said once",
              len(said) == 1, said)
    finally:
        process.kill()


def odd_width(url):
    """600 wide is what a Waveshare 7B drawn portrait sends. Its decoder lays a
    4:4:4 picture out on rows of 600 and the board steps 608 at a time, so the
    finished picture must keep the colour halved there."""
    panel, process, out = run(url, width=600)
    try:
        wait_for(lambda: any(g[3] == 600 for g in panel.since(0)), 40)
        time.sleep(3.0)
        got = [g for g in panel.since(0) if g[3] == 600 and g[4] == H]
        said = [o for o in out if o.startswith("Sharp: 600 pixels wide")]
        check("600 wide: a finished picture still goes out", len(got) >= 2,
              [(g[5], g[6]) for g in got])
        check("but never in full colour", not any(g[5] for g in got))
        check("and why is said once", len(said) == 1, said)
    finally:
        process.kill()


def main():
    print("The add-on:")
    addon_half()
    print("The sender, on a fake panel:")
    sender_half()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    odd_width(f"http://127.0.0.1:{server.server_address[1]}/")
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
