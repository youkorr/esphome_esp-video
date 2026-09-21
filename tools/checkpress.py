#!/usr/bin/env python3
"""Does a tile LOOK pressed, for long enough that a frame can contain it?

WHY THIS EXISTS. Reported from a panel: the link tiles have no press effect
"comme un button lvgl". The `:active` rule was already in the stylesheet and
was already being applied -- and it lasted a MEASURED median of 2.3 ms. The
sender holds a contact back until the finger lifts, so that a drag can become
a wheel instead of a click, and then dispatches mousedown and mouseup
together. The panel sees the page as JPEG rectangles at `fps`, and a frame at
25 is 40 ms, so the pressed state occupied 6% of one frame interval. It was
not missing; it was unphotographable.

So this measures the two things that were wrong, and the one that must not
break:

  - HOW LONG the pressed look is held, against a frame interval
  - WHETHER IT IS VISIBLE, read off the pixels rather than off the markup,
    because this repository has already shipped an on-screen keyboard that
    was drawn correctly and invisible on a real page
  - THAT THE TAP STILL REACHES THE LINK. A listener that swallowed it would
    turn a slow tile into a dead one, which is far worse than no effect.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.

    python3 tools/checkpress.py --browser /opt/pw-browsers/chromium
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portall"))
import launcher
from playwright.sync_api import sync_playwright

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

LINKS = [{"name": "Home Assistant", "url": "http://127.0.0.1:8099/?a", "icon": "home"},
         {"name": "Jellyfin", "url": "http://127.0.0.1:8099/?b", "icon": "jellyfin"},
         {"name": "YouTube", "url": "http://127.0.0.1:8099/?c", "icon": "youtube"}]

server = launcher.start(LINKS)
assert server is not None
fails = 0


def ok(what, passed):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what)
    if not passed:
        fails += 1


# The pressed look is held by a timeout, so the page can be asked how long it
# lasted without sampling it from outside and racing it.
# Hold the link back for the cases that only want the LOOK. A navigation
# destroys the observer below and races the next goto, which is a fault in the
# ruler rather than in the page -- the last case takes the hold off and
# requires the tap to navigate for real.
STAY = """() => document.addEventListener('click', e => e.preventDefault(), true)"""

WATCH = """() => { window.__held = new Promise(done => {
  const t = document.querySelector('a.tile');
  let on = 0;
  const obs = new MutationObserver(() => {
    if (t.classList.contains('press')) { if (!on) on = performance.now(); }
    else if (on) { obs.disconnect(); done(performance.now() - on); }
  });
  obs.observe(t, {attributes: true, attributeFilter: ['class']});
  setTimeout(() => { obs.disconnect(); done(-1); }, 3000);
}); }"""


def pixel(page, box, dx=0.5, dy=0.5):
    """One pixel from inside the tile, off a real capture of the page."""
    shot = page.screenshot(clip={"x": box["x"], "y": box["y"],
                                 "width": box["width"], "height": box["height"]})
    import struct
    import zlib
    # A PNG, decoded far enough to read one pixel -- no Pillow in this
    # container and none is needed for a single sample.
    pos, w, h, idat = 8, 0, 0, b""
    while pos < len(shot):
        ln = struct.unpack(">I", shot[pos:pos + 4])[0]
        kind = shot[pos + 4:pos + 8]
        body = shot[pos + 8:pos + 8 + ln]
        if kind == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
            depth, colour = body[8], body[9]
            if depth != 8 or colour not in (2, 6):
                raise SystemExit(f"unexpected PNG: depth {depth}, colour {colour}")
            bpp = 3 if colour == 2 else 4
        elif kind == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * bpp
    x, y = int(w * dx), int(h * dy)
    # Undo the per-row filters up to the row wanted.
    prev = bytearray(stride)
    at = 0
    for row in range(y + 1):
        f = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        prev = line
        at += 1 + stride
    return tuple(prev[x * bpp:x * bpp + 3])


with sync_playwright() as pw:
    b = (pw.chromium.launch(executable_path=BROWSER) if BROWSER
         else pw.chromium.launch())
    page = b.new_page(viewport={"width": 800, "height": 1280})
    page.goto("http://127.0.0.1:8099/")
    page.wait_for_selector("a.tile")
    box = page.query_selector("a.tile").bounding_box()
    at = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    print("how long the pressed look is held")
    page.evaluate(STAY)
    page.evaluate(WATCH)
    page.mouse.move(*at)
    page.mouse.down()
    page.mouse.up()
    ms = page.evaluate("window.__held")
    ok(f"it is held at all (measured {ms:.0f} ms)", ms > 0)
    # The rate a panel is really at during a press: a contact lifts the limit
    # to urgent_fps for two seconds, 30 by default. A capped link may be at 10.
    ok(f"and outlasts a frame at --fps 30 ({1000/30:.0f} ms)", ms > 1000 / 30)
    ok(f"and one at --fps 10 ({1000/10:.0f} ms), which a capped link runs at",
       ms > 1000 / 10)

    print("\nand whether it is visible, off the pixels rather than the markup")
    page.goto("http://127.0.0.1:8099/")
    page.wait_for_selector("a.tile")
    rest = pixel(page, box, 0.5, 0.12)
    page.evaluate("document.querySelector('a.tile').classList.add('press')")
    page.wait_for_timeout(60)
    down = pixel(page, box, 0.5, 0.12)
    apart = sum(abs(a - c) for a, c in zip(rest, down))
    print(f"    at rest {rest}, pressed {down}")
    ok(f"the tile really changes colour (distance {apart})", apart >= 24)

    # The scale is what a finger reads first, so it has to be a real move
    # rather than the 1.5% that was there.
    page.evaluate("document.querySelector('a.tile').classList.remove('press')")
    page.wait_for_timeout(30)
    before = page.query_selector("a.tile").bounding_box()
    page.evaluate("document.querySelector('a.tile').classList.add('press')")
    page.wait_for_timeout(60)
    after = page.query_selector("a.tile").bounding_box()
    shrink = before["width"] - after["width"]
    ok(f"and visibly shrinks ({shrink:.0f} px, where 1.5% was {before['width']*0.015:.0f})",
       shrink >= before["width"] * 0.025)

    print("\nand a remote, which produces no pointer event at all")
    page.goto("http://127.0.0.1:8099/")
    page.wait_for_selector("a.tile")
    page.evaluate(STAY)
    page.evaluate(WATCH)
    page.evaluate("document.querySelector('a.tile').focus()")
    page.keyboard.down("Enter")
    page.keyboard.up("Enter")
    ms = page.evaluate("window.__held")
    ok(f"OK on a chosen tile flashes it too (measured {ms:.0f} ms)", ms > 0)

    # LAST, because it really leaves the page: with nothing holding the click
    # back, the tap has to follow the link exactly as it did before any of
    # this existed.
    print("\nand the tap must still reach the link")
    page.goto("http://127.0.0.1:8099/")
    page.wait_for_selector("a.tile")
    page.mouse.move(*at)
    page.mouse.down()
    page.mouse.up()
    went = True
    try:
        page.wait_for_url("**/?a", timeout=3000)
    except Exception:
        went = False
    ok("a tap still navigates, so the effect did not eat the click", went)
    b.close()

print()
if fails:
    print(f"{fails} check(s) failed")
    sys.exit(1)
print("ok")
