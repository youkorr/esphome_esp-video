#!/usr/bin/env python3
"""A panel drawing in 16 bits shows a dark gradient as bands; one in 24 does not.

WHY THIS EXISTS. Reported with a photograph of the launcher: horizontal lines
across every button and across the wallpaper, after the finished picture
(4.45) had changed nothing. They are not the JPEG. The board decoded into
RGB565 and the display was drawn in 16 bits, so red and blue have 32 levels,
and a button's shading or a blurred, dimmed photograph -- a dozen levels from
top to bottom -- is drawn as a few flat bands.

Two answers, both checked here:

  - 24 bits. A display with `color_depth: 24` is read by portall at codegen,
    the board decodes RGB888 and draws it as it stands, and tells the sender
    ('C' 24 on the return channel) so nothing is dithered.
  - 16 bits. The sender dithers the finished picture -- the only one sent at
    a quality where a dither survives the JPEG -- and says so once.

It checks the component's reading of the display, its refusal of canvas mode
at 24, the message on the wire (and that a sender from before it skips it),
and the shipped sender against a fake panel at quality 95 on a page of
buttons: the finished picture is dithered for a panel that says nothing,
measurably less banded once put through RGB565, and not dithered for a panel
that says 24 or with --no-dither.

--sender PATH runs another copy of ha_send.py. Needs Playwright and Pillow;
CHROMIUM names the browser.
"""
import http.server
import io
import os
import pathlib
import subprocess
import sys
import threading
import time
import types

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))
import checksharp as cs  # noqa: E402  -- its fake panel and its run()

cs.SENDER = (sys.argv[sys.argv.index("--sender") + 1]
             if "--sender" in sys.argv else cs.SENDER)
W, H = cs.W, cs.H
fails = 0

# Buttons shaded the launcher's way over a dark blue that darkens downwards:
# the two things the photograph showed banded.
PAGE = """<!doctype html><html><body style="margin:0;height:100vh;
background:linear-gradient(#1c2c6c,#0a1236)">
<div style="position:absolute;left:60px;top:60px;width:240px;height:280px;
border-radius:16px;background:#202a4a;background-image:linear-gradient(
rgba(255,255,255,.08),rgba(0,0,0,.14))"></div>
<div style="position:absolute;left:340px;top:60px;width:240px;height:280px;
border-radius:16px;background:#2a2f40;background-image:linear-gradient(
rgba(255,255,255,.08),rgba(0,0,0,.14))"></div>
</body></html>"""
# Inside the first button, away from its corners and the corner mark.
SMOOTH = (80, 90, 280, 320)


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


def to565(a):
    """What a 16-bit panel keeps of an RGB888 picture."""
    a = a.astype(np.int32)
    r, g, b = a[..., 0] >> 3, a[..., 1] >> 2, a[..., 2] >> 3
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)], -1).astype(float)


def blur(a, sigma=3.0):
    """What the eye integrates across a room: a Gaussian, per channel."""
    radius = int(3 * sigma)
    k = np.exp(-np.arange(-radius, radius + 1) ** 2 / (2 * sigma ** 2))
    k /= k.sum()
    out = a.astype(float)
    for axis in (0, 1):
        out = np.apply_along_axis(
            lambda v: np.convolve(np.pad(v, radius, mode="edge"), k, "valid"),
            axis, out)
    return out


def region(a):
    x0, y0, x1, y1 = SMOOTH
    return a[y0:y1, x0:x1]


def banding(shown, ref):
    """How far the panel's picture ripples about the page once averaged:
    the bands. The mean is left out -- that is a bias, not a band."""
    err = blur(region(shown)) - blur(region(ref).astype(float))
    return float((err - err.mean((0, 1))).std())


def grain(shown, ref):
    """The fine pattern a dither leaves: what is left after a 4x4 average."""
    d = region(shown).astype(float) - region(ref).astype(float)
    h, w = (d.shape[0] // 4) * 4, (d.shape[1] // 4) * 4
    d = d[:h, :w]
    box = d.reshape(h // 4, 4, w // 4, 4, 3).mean((1, 3))
    return float((d - np.repeat(np.repeat(box, 4, 0), 4, 1)).std())


def component_half():
    """portall's __init__.py: how it reads the display it draws on."""
    source = (HERE / "components" / "portall" / "__init__.py").read_text()
    # Only the two functions are needed; executed alone, with the names they
    # read, so this half runs without esphome installed.
    start = source.index("def display_depth(")
    end = source.index("def _final_validate(")
    ns = {"CONF_ID": "id", "CONF_CANVAS": "canvas",
          "CONF_DISPLAY_ID": "display_id",
          "cv": types.SimpleNamespace(Invalid=ValueError),
          "fv": None}
    exec(source[start:end], ns)
    depth = ns["display_depth"]
    Id = types.SimpleNamespace

    def cfg(*blocks):
        return {"display": list(blocks)}

    screen = Id(id="main_screen")
    check("a display with color_depth: 24 is drawn in 24",
          depth(cfg({"id": Id(id="main_screen"), "color_depth": "24"}),
                screen) == 24)
    check("24bit is 24 too",
          depth(cfg({"id": Id(id="main_screen"), "color_depth": "24bit"}),
                screen) == 24)
    check("16 and a display with no such option are 16",
          depth(cfg({"id": Id(id="main_screen"), "color_depth": "16"}),
                screen) == 16
          and depth(cfg({"id": Id(id="main_screen")}), screen) == 16)
    check("another display's depth is not this one's",
          depth(cfg({"id": Id(id="other"), "color_depth": "24"},
                    {"id": Id(id="main_screen")}), screen) == 16)
    full = cfg({"id": Id(id="main_screen"), "color_depth": "24"})
    ns["fv"] = types.SimpleNamespace(
        full_config=types.SimpleNamespace(get=lambda: full))
    refused = False
    try:
        ns["_depth_fits"]({"display_id": screen, "canvas": "page_canvas"})
    except ValueError:
        refused = True
    check("canvas mode is refused at 24", refused)
    check("and plain mode is not",
          ns["_depth_fits"]({"display_id": screen}) is not None)


def wire_half():
    sys.path.insert(0, str(HERE / "components" / "portall"))
    import udisp_send
    got, _ = udisp_send.parse_messages(b"C\x18T\x00")
    check("'C' 24 reads as 24, and the touch after it is still read",
          ("depth", 24) in got and ("touch", []) in got, got)
    got, _ = udisp_send.parse_messages(b"C\x10")
    check("'C' 16 reads as 16", got == [("depth", 16)], got)
    # A sender from before it, from git.
    old = subprocess.run(
        ["git", "-C", str(HERE), "show",
         "49a525b:components/portall/udisp_send.py"],
        capture_output=True, text=True)
    if old.returncode == 0:
        ns = {"__name__": "old_udisp"}
        exec(compile(old.stdout, "old_udisp_send.py", "exec"), ns)
        got, _ = ns["parse_messages"](b"C\x18T\x00")
        check("a sender from before it skips it and reads the touch",
              got == [("touch", [])], got)


class TellingPanel(cs.Panel):
    """A fake panel that says its colour depth the moment it accepts."""

    def __init__(self, depth):
        self.depth = depth
        super().__init__()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.conn = conn
            if self.depth:
                conn.sendall(bytes([ord("C"), self.depth]))
            self._read(conn)


def finished(panel_cls_depth, url, *extra):
    """The finished picture a sender at quality 95 sends, and its log."""
    original = cs.Panel
    cs.Panel = (lambda: TellingPanel(panel_cls_depth))
    try:
        panel, process, out = cs.run(url, *extra)
    finally:
        cs.Panel = original
    # run() hard-codes --quality 80; the later --quality wins.
    try:
        ok = cs.wait_for(lambda: any(cs.whole(g) and g[5]
                                     for g in panel.since(0)), 40)
        time.sleep(0.4)
        return (panel.picture() if ok else None), out
    finally:
        process.kill()


def sender_half():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    ref = cs.reference(url)
    plain_565 = banding(to565(ref), ref)

    shown, out = finished(0, url, "--quality", "95")
    check("a panel that says nothing gets a finished picture", shown is not None,
          "".join(out[-10:]))
    if shown is None:
        return
    said = [o for o in out if o.startswith("Sharp: this panel draws in 16")]
    check("and it is dithered, said once", len(said) == 1, said)
    dithered = banding(to565(shown), ref)
    check("through RGB565 it is less banded than the page itself would be "
          "(by a third or more)", dithered <= plain_565 * 0.67,
          f"{plain_565:.2f} -> {dithered:.2f}")
    print(f"         bands on a 16-bit panel: {plain_565:.2f} undithered, "
          f"{dithered:.2f} dithered")
    check("and it carries the dither's grain", grain(shown, ref) > 1.0,
          f"{grain(shown, ref):.2f}")

    shown, out = finished(24, url, "--quality", "95")
    check("a panel that says 24 gets a finished picture", shown is not None)
    if shown is not None:
        check("not dithered", grain(shown, ref) < 0.8,
              f"{grain(shown, ref):.2f}")
        check("and the sender says what the panel draws in",
              any("Panel: draws in 24 bits" in o for o in out))
        check("on a 24-bit panel the gradient is the page's",
              banding(shown.astype(float), ref) < plain_565 * 0.5,
              f"{banding(shown.astype(float), ref):.2f}")

    shown, out = finished(0, url, "--quality", "95", "--no-dither")
    if shown is not None:
        check("--no-dither: not dithered", grain(shown, ref) < 0.8,
              f"{grain(shown, ref):.2f}")

    shown, out = finished(0, url)
    if shown is not None:
        check("below quality 90 nothing is dithered, where it would not "
              "survive the JPEG", grain(shown, ref) < 0.8,
              f"{grain(shown, ref):.2f}")


def main():
    print("The component:")
    component_half()
    print("The wire:")
    wire_half()
    print("The sender, on a fake panel:")
    sender_half()
    print("all good" if not fails else f"{fails} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
