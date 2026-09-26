#!/usr/bin/env python3
"""Can the tile a remote is on be SEEN, and does focus_color reach the page?

WHY THIS EXISTS. Reported from a panel: "sur un fond clair ont ne voit pas ce
que je selectionne" -- the ring around the tile a remote or a gamepad is on is
the accent colour, and the accent is a middle shade chosen to sit on a card,
not to stand out from one. On a light theme it nearly vanishes, and on a
panel driven by a remote it is the only thing saying where OK will land.

`focus_color` is a name from the clock's colour list. This checks it the way
a household sets it: the grouped form the Supervisor writes, through
run.py's regroup(), launcher_config() for a panel's own launcher, and
start_launcher(), then a real browser pressing an arrow and reading the
COMPUTED ring off the focused tile -- in both shapes, cards and buttons.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.
"""

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))

import launcher  # noqa: E402
import run  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

LINKS = [{"name": "Home Assistant", "url": "http://x/1", "icon": "home"},
         {"name": "Jellyfin", "url": "http://x/2", "icon": "jellyfin"},
         {"name": "YouTube", "url": "http://x/3", "icon": "youtube"}]

faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        faults.append(what)


def rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def serve(options, entry=None):
    """One launcher built the way the add-on builds it, from the form."""
    config = run.regroup({"links": LINKS, "launcher": options})
    if entry is not None:
        config = run.launcher_config(config, dict(entry, links=LINKS))
    return run.start_launcher(config, port=launcher.ANY_PORT)


def ring(page, address):
    page.goto(address)
    page.wait_for_selector("a.tile")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)
    return page.evaluate("""() => {
        const s = getComputedStyle(document.activeElement);
        return [document.activeElement.className, s.borderTopColor,
                s.boxShadow];
    }""")


def main():
    print("The ring around the chosen tile:")
    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path=BROWSER) if BROWSER
                   else pw.chromium.launch())
        page = browser.new_page(viewport={"width": 1024, "height": 600})

        cls, border, shadow = ring(page, serve({"theme": "light"}))
        accent = rgb(launcher.PALETTES[launcher.DEFAULT_PALETTE])
        check("left alone it is the theme's accent, as it always was",
              "tile" in cls and border == accent and accent in shadow,
              border)

        cls, border, shadow = ring(page, serve({"tiles": "buttons"}))
        check("a chosen BUTTON carries the ring, not only a thin border "
              "(its drop shadow used to replace it)", accent in shadow, shadow)

        for shape in ("cards", "buttons"):
            cls, border, shadow = ring(page, serve(
                {"theme": "light", "tiles": shape, "focus_color": "black"}))
            check(f"focus_color black on a light {shape} page is black, "
                  "border and ring", border == "rgb(0, 0, 0)"
                  and "rgb(0, 0, 0)" in shadow, f"{border} / {shadow}")

        cls, border, shadow = ring(page, serve(
            {"theme": "light", "focus_color": "black"},
            {"focus_color": "yellow"}))
        yellow = rgb(launcher.PALETTES["yellow"])
        check("a panel's own launcher sets its own, over the house's",
              border == yellow, border)

        cls, border, shadow = ring(page, serve(
            {"theme": "light", "focus_color": "black"}, {"theme": "dark"}))
        check("and one that does not set it keeps the house's",
              border == "rgb(0, 0, 0)", border)

        cls, border, shadow = ring(page, serve(
            {"focus_color": "not-a-colour"}))
        check("a name that is not in the list falls back to the theme",
              border == accent, border)

        browser.close()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
