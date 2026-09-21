#!/usr/bin/env python3
"""Can a frame contain a page's OWN pressed state, on any site?

WHY THIS EXISTS. Reported from a panel: "jellyfin fait pareil que netflix le
button ne dispose aucun effect". The grouping is the clue -- those two were
named and Home Assistant was not -- and it is a property of the SITE rather
than of the launcher.

`Injector` holds a contact back until the finger lifts, so that a gesture that
travelled can become a wheel instead of a click, and then dispatched mousedown
and mouseup together. `:active` is tied to the button really being down, so on
this path it lasted about as long as one task. A site that ANIMATES its own
feedback -- Material's ripple, which Home Assistant uses -- runs on after the
button is up and was always visible; a site that styles `:active` and nothing
else had nothing left to show by the time anything was painted.

So this measures the two shapes side by side, through a real screencast, which
is the only picture a panel ever gets:

  - an :active-only button, the Jellyfin and Netflix shape
  - an animated one, the Home Assistant shape, which must keep working
  - the old behaviour, reached with press_hold 0, which must FAIL

It drives the SHIPPED Injector rather than calling page.mouse itself, because
the fault was in how the Injector dispatches and a test that dispatched for
itself would prove only that Chromium works.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.

    python3 tools/checkpresshold.py --browser /opt/pw-browsers/chromium
"""
import base64
import io
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))
import ha_send
from PIL import Image
from playwright.sync_api import sync_playwright

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

W, H = 400, 300
REST = (51, 51, 51)
# The colour a press puts there, and the one it has to differ from.
DOWN = (229, 9, 20)

# The base colour is in the STYLESHEET, not inline: an inline style beats every
# selector in the sheet, so a fixture written the obvious way never applies its
# own :active and measures nothing. That fault is already in CLAUDE.md under
# the on-screen keyboard's hide(), and it cost a run here too -- the tell was a
# case reading "0 of 0 frames", which for a button flashing red is impossible.
ACTIVE = """<html><body style="margin:0;background:#111">
<button id=b style="position:absolute;left:0;top:0;width:400px;height:300px;
 border:0">press</button>
<style>#b{background:#333} #b:active{background:#e50914}</style>
</body></html>"""

RIPPLE = """<html><body style="margin:0;background:#111">
<button id=b style="position:absolute;left:0;top:0;width:400px;height:300px;
 border:0">press</button>
<style>#b{background:#333}
@keyframes r{from{background:#e50914}to{background:#333}}
#b.on{animation:r .4s ease-out}</style>
<script>b.addEventListener('pointerdown',()=>{b.classList.remove('on');
 void b.offsetWidth; b.classList.add('on')})</script></body></html>"""

fails = 0


def ok(what, passed):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what)
    if not passed:
        fails += 1


class Straight:
    """The panel's coordinates are the page's, so the gesture is the subject."""

    def to_page(self, x, y):
        return float(x), float(y)


def pressed_frames(browser, html, press_hold):
    """Tap the middle through the shipped Injector; count what a panel would see.

    Returns (frames showing the press, frames in total).
    """
    page = browser.new_page(viewport={"width": W, "height": H})
    page.set_content(html)
    cdp = page.context.new_cdp_session(page)
    frames = []

    def got(ev):
        frames.append(ev["data"])
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:  # noqa: BLE001 - the page went away mid-capture
            pass

    cdp.on("Page.screencastFrame", got)
    cdp.send("Page.startScreencast",
             {"format": "jpeg", "quality": 90, "everyNthFrame": 1})
    # Let the page settle and throw away what it painted getting there, so the
    # count below is only about the press.
    page.wait_for_timeout(600)
    frames.clear()

    inj = ha_send.Injector(page, Straight(), None, W, H,
                           press_hold=press_hold)
    # Middle of the button, which is nowhere near the home corner.
    at = (1, W // 2, H // 2)
    inj.handle([[at]])   # the finger lands
    inj.handle([[]])     # and lifts, which is where the press is dispatched
    # The loop is what lets go of it, so this is the loop.
    until = time.monotonic() + 1.0
    while time.monotonic() < until:
        inj.tick(time.monotonic())
        page.wait_for_timeout(8)
    cdp.send("Page.stopScreencast")

    hits = 0
    for data in frames:
        im = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        # A QUARTER down, not the middle, because the middle of a button is
        # where its LABEL is -- the same fault CLAUDE.md records for
        # checkpress.py, which sampled a tile's name and read the same white
        # glyph in both states.
        px = im.getpixel((im.width // 2, im.height // 4))
        # Judged as "not at rest" rather than "equal to DOWN": an animation
        # passes through every colour between the two, so only its first frame
        # is the pressed colour and the rest of it is just as much a press.
        if sum(abs(a - c) for a, c in zip(px, REST)) > 60:
            hits += 1
    page.close()
    return hits, len(frames)


with sync_playwright() as pw:
    b = (pw.chromium.launch(executable_path=BROWSER) if BROWSER
         else pw.chromium.launch())

    print(f"a page that styles :active and nothing else "
          f"(the Jellyfin and Netflix shape)")
    hits, total = pressed_frames(b, ACTIVE, ha_send.PRESS_HOLD_S)
    ok(f"a frame contains the press ({hits} of {total})", hits >= 1)

    # The fault itself, kept rather than remembered. --press-hold 0 IS the old
    # behaviour, so this runs the shipped code against the shipped option.
    was, total_was = pressed_frames(b, ACTIVE, 0.0)
    ok(f"and with --press-hold 0, which is how it shipped, none does "
       f"({was} of {total_was})", was == 0)

    print("\nand one that animates its own feedback (the Home Assistant shape)")
    hits, total = pressed_frames(b, RIPPLE, ha_send.PRESS_HOLD_S)
    ok(f"a frame contains the press ({hits} of {total})", hits >= 1)
    # This is the control: it was never broken, and it must not become so.
    was, total_was = pressed_frames(b, RIPPLE, 0.0)
    ok(f"and it always could, which is why it was never reported "
       f"({was} of {total_was})", was >= 1)

    print("\nand the hold must not cost the click")
    page = b.new_page(viewport={"width": W, "height": H})
    page.set_content(ACTIVE + "<script>window.__hit=0;"
                     "b.addEventListener('click',()=>window.__hit++)</script>")
    inj = ha_send.Injector(page, Straight(), None, W, H,
                           press_hold=ha_send.PRESS_HOLD_S)
    inj.handle([[(1, W // 2, H // 2)]])
    inj.handle([[]])
    ok("the page has not been clicked before the button is let go",
       page.evaluate("window.__hit") == 0)
    until = time.monotonic() + 0.6
    while time.monotonic() < until:
        inj.tick(time.monotonic())
        page.wait_for_timeout(8)
    ok("and exactly once after it", page.evaluate("window.__hit") == 1)

    print("\nand a second tap must not land on a button still held")
    page2 = b.new_page(viewport={"width": W, "height": H})
    page2.set_content(ACTIVE + "<script>window.__down=0;window.__up=0;"
                      "b.addEventListener('mousedown',()=>window.__down++);"
                      "b.addEventListener('mouseup',()=>window.__up++)</script>")
    inj = ha_send.Injector(page2, Straight(), None, W, H,
                           press_hold=ha_send.PRESS_HOLD_S)
    for _ in range(3):
        inj.handle([[(1, W // 2, H // 2)]])
        inj.handle([[]])   # no tick in between: the hold is still running
    until = time.monotonic() + 0.6
    while time.monotonic() < until:
        inj.tick(time.monotonic())
        page2.wait_for_timeout(8)
    downs, ups = page2.evaluate("window.__down"), page2.evaluate("window.__up")
    ok(f"three taps are three downs and three ups ({downs}, {ups})",
       downs == 3 and ups == 3)
    b.close()

print()
if fails:
    print(f"{fails} check(s) failed")
    sys.exit(1)
print("ok")
