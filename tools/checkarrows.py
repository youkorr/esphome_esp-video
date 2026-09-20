#!/usr/bin/env python3
"""Do the launcher's arrow keys stay inside the tile grid?

WHY THIS EXISTS. A panel reported "up cree des probleme", and the Bluetooth
log showed `gamepad: up` decoded perfectly -- so the fault was on the page.
The handler swallowed an arrow only when it FOUND a tile in that direction, so
an arrow at the EDGE of the grid fell through to the browser, which scrolls:
the launcher jumped away from the tile that had just been chosen and the focus
ring ended up off-screen. Up is where it shows first, because a launcher opens
on its top row and up is the one direction that row has nothing in.

Nothing but a real browser can see this. `esphome config` never reaches the
add-on, and reading the markup says nothing about what a key does -- so this
measures `window.scrollY` in Chromium, against the real launcher served by its
own server.

AND THE RULER MATTERS MORE THAN THE CODE HERE. The first version pressed up
with the page already at the top, where a browser cannot scroll up either: it
passed against the broken launcher and proved nothing. An unswallowed arrow
needs somewhere to go, so the page is scrolled first -- which is the state a
panel is in after somebody has moved around the grid.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM; without either
it uses whatever Playwright has installed.

    python3 tools/checkarrows.py --browser /opt/pw-browsers/chromium
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

LINKS = [{"name": "Home Assistant", "url": "http://x/1", "icon": "home"},
         {"name": "Jellyfin", "url": "http://x/2", "icon": "jellyfin"},
         {"name": "YouTube", "url": "http://x/3", "icon": "youtube"},
         {"name": "Proxmox", "url": "http://x/4", "icon": "proxmox"},
         {"name": "Unraid", "url": "http://x/5", "icon": "unraid"},
         {"name": "Camera", "url": "http://x/6", "icon": "camera"}]

server = launcher.start(LINKS)
assert server is not None
fails = 0
def ok(what, passed):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what)
    if not passed:
        fails += 1

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=BROWSER) if BROWSER \
        else pw.chromium.launch()
    # A panel's own shape, and short enough that the page really scrolls.
    page = b.new_page(viewport={"width": 800, "height": 420})
    page.goto("http://127.0.0.1:8099/")
    page.wait_for_selector("a.tile")
    scrollable = page.evaluate("document.body.scrollHeight > window.innerHeight")
    ok("the page is taller than the panel, so scrolling is possible",
       scrollable)

    # First arrow chooses. Down picks the first tile, up picks the last.
    page.keyboard.press("ArrowDown")
    first = page.evaluate("document.activeElement.textContent")
    page.evaluate("document.activeElement.blur(); window.scrollTo(0,0)")
    page.keyboard.press("ArrowUp")
    last = page.evaluate("document.activeElement.textContent")
    ok("down from a cold page chooses the first tile", "Home Assistant" in first)
    ok("and up chooses the last", "Camera" in last)

    # THE FAULT, and the ruler had to be fixed first. The first version of
    # this case pressed up with the page already at the top, where a browser
    # cannot scroll up either -- so it passed against the broken code and
    # proved nothing. The page has to be SCROLLED for an unswallowed up to
    # have somewhere to go, which is exactly the state a panel is in after
    # somebody has moved around the grid.
    page.evaluate("document.querySelectorAll('a.tile')[0].focus();"
                  "window.scrollTo(0, 80)")
    page.wait_for_timeout(80)
    before = page.evaluate("window.scrollY")
    ok("the page really is scrolled, so up has somewhere to go", before > 0)
    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(150)
    after = page.evaluate("window.scrollY")
    still = page.evaluate("document.activeElement.textContent")
    ok("up on the top row does not scroll the page", before == after)
    ok("and leaves the focus where it was", "Home Assistant" in still)

    # The other three edges, for the same reason.
    tiles = page.evaluate("document.querySelectorAll('a.tile').length")
    page.evaluate("window.scrollTo(0, 0);"
                  "document.querySelectorAll('a.tile')[%d].focus()" % (tiles - 1))
    page.evaluate("window.scrollTo(0, 0)")
    before = page.evaluate("window.scrollY")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(120)
    ok("down on the bottom row does not scroll either",
       page.evaluate("window.scrollY") == before)

    # And moving still works, which is what a half-fix would break.
    page.evaluate("document.querySelectorAll('a.tile')[0].focus()")
    page.keyboard.press("ArrowRight")
    ok("right still moves", "Jellyfin" in page.evaluate("document.activeElement.textContent"))
    page.keyboard.press("ArrowDown")
    moved = page.evaluate("document.activeElement.textContent")
    ok("and down still moves", "Home Assistant" not in moved and "Jellyfin" not in moved)

    # A letter key must still reach the page.
    page.evaluate("window.scrollTo(0, 0)")
    page.keyboard.press("PageDown")
    page.wait_for_timeout(120)
    ok("a key this does not handle still reaches the browser",
       page.evaluate("window.scrollY") > 0)
    b.close()
# start() returns the address; the thread is a daemon and goes with us.
print("FAILURES" if fails else "all ok")
sys.exit(1 if fails else 0)
