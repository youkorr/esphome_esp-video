#!/usr/bin/env python3
"""Do the links' three look settings reach the page, and do they do what
they say?

WHY THIS EXISTS. Asked for from a 1280x800 panel with a picture behind the
links, which the links covered: "une vraie transparence du bouton, tout en
laissant l'icone avec sa propre couleur", and, as options too, "la reduction
des boutons et du texte, et la couleur du texte". So three settings:

  tile_background  solid | transparent
  tile_size        tiny | small | medium
  tile_text_color  a name from the clock's colour list

Each is checked the way a household sets it -- the grouped form the
Supervisor writes, through run.py's regroup(), launcher_config() for a
panel's own launcher, and start_launcher() -- then read off a real browser:
computed styles, and for the transparency the PIXELS, because a ground that
computes as transparent can still be painted by something else.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.
"""

import io
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
          + (f"  ({detail})" if detail and not ok else ""))
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


LOOK = """() => {
  const t = document.querySelectorAll('a.tile');
  const s = getComputedStyle(t[0]);
  const box = [...t].map(e => { const r = e.getBoundingClientRect();
                                return [r.x, r.y, r.width, r.height]; });
  return {bg: s.backgroundColor, image: s.backgroundImage,
          border: s.borderTopColor, shadow: s.boxShadow,
          filter: s.backdropFilter,
          icon: getComputedStyle(t[0].querySelector('.icon')).backgroundColor,
          name: getComputedStyle(t[0].querySelector('.name')).color,
          box};
}"""


def look(page, address, press=None):
    page.goto(address)
    page.wait_for_selector("a.tile")
    page.evaluate("document.fonts.ready")
    if press:
        page.keyboard.press(press)
        page.wait_for_timeout(150)
    return page.evaluate(LOOK)


def pixel(page, x, y):
    from PIL import Image
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    return shot.getpixel((int(x), int(y)))


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("skipped: needs Pillow")
        return 0
    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path=BROWSER) if BROWSER
                   else pw.chromium.launch())
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        print("Left alone:")
        check("the defaults add no rule at all, so nothing changes for "
              "anybody who does not ask", launcher._tile_look() == [])
        for shape in ("cards", "buttons"):
            plain = look(page, serve({"tiles": shape}))
            check(f"a {shape} tile keeps its ground",
                  plain["bg"] != "rgba(0, 0, 0, 0)", plain["bg"])

        print("tile_background: transparent")
        for shape in ("cards", "buttons"):
            seen = look(page, serve({"tiles": shape,
                                     "tile_background": "transparent"}))
            check(f"{shape}: no ground, no gradient, no frame, no shadow, "
                  f"no blur",
                  seen["bg"] == "rgba(0, 0, 0, 0)" and seen["image"] == "none"
                  and seen["border"] == "rgba(0, 0, 0, 0)"
                  and seen["shadow"] == "none" and seen["filter"] == "none",
                  str(seen))
            check(f"{shape}: the icon has no tinted square behind it",
                  seen["icon"] == "rgba(0, 0, 0, 0)", seen["icon"])
            # The pixels: inside the tile, clear of its icon and name, is
            # the page's own ground -- and YouTube's mark is still red.
            x, y, w, h = seen["box"][2]
            inside = pixel(page, x + w - 6, y + h / 2)
            outside = pixel(page, x + w + 6, y + h / 2)
            check(f"{shape}: inside a tile is the page behind it",
                  max(abs(a - b) for a, b in zip(inside, outside)) <= 3,
                  f"{inside} against {outside}")
            icon = page.evaluate("""() => { const r = document
                .querySelectorAll('a.tile')[2].querySelector('.icon')
                .getBoundingClientRect(); return [r.x + r.width * .15,
                r.y + r.height / 2]; }""")
            # Near the mark's left edge: its middle is the play triangle,
            # which is cut out of the mark and shows the page through it.
            red = pixel(page, icon[0], icon[1])
            check(f"{shape}: the icon keeps its own colour (YouTube red)",
                  red[0] > 180 and red[1] < 70 and red[2] < 70, str(red))

            chosen = look(page, serve({"tiles": shape,
                                       "tile_background": "transparent"}),
                          press="ArrowDown")
            ring = rgb(launcher.PALETTES[launcher.DEFAULT_PALETTE])
            check(f"{shape}: the link a remote is on still shows its ring",
                  ring in page.evaluate(
                      "getComputedStyle(document.activeElement).boxShadow"))
            page.evaluate("document.querySelector('a.tile')"
                          ".classList.add('press')")
            check(f"{shape}: and a press still shows",
                  page.evaluate("getComputedStyle(document.querySelector("
                                "'a.tile')).backgroundColor")
                  != "rgba(0, 0, 0, 0)")

        print("tile_size:")
        for shape in ("buttons", "cards"):
            medium = look(page, serve({"tiles": shape, "columns": 3}))
            for size in ("small", "tiny"):
                seen = look(page, serve({"tiles": shape, "columns": 3,
                                         "tile_size": size}))
                factor = launcher.TILE_SIZES[size]
                h0, h1 = medium["box"][0][3], seen["box"][0][3]
                check(f"{shape} {size}: {factor:g} of the height",
                      abs(h1 - h0 * factor) <= 2, f"{h1:.0f} for {h0:.0f}")
                if shape == "buttons":
                    w0, w1 = medium["box"][0][2], seen["box"][0][2]
                    check(f"{shape} {size}: and of the width",
                          abs(w1 - w0 * factor) <= 2,
                          f"{w1:.0f} for {w0:.0f}")
                hit = page.evaluate("""() => { const r = document
                    .querySelectorAll('a.tile')[1].getBoundingClientRect();
                    return document.elementFromPoint(r.x + r.width / 2,
                        r.y + r.height / 2).closest('a').textContent.trim();
                    }""")
                check(f"{shape} {size}: a finger on the second link lands on "
                      f"it", hit == "Jellyfin", hit)

        print("tile_text_color:")
        seen = look(page, serve({"tile_text_color": "black"}))
        check("black reaches the names",
              seen["name"] == rgb(launcher.PALETTES["black"]), seen["name"])
        seen = look(page, serve({"tile_text_color": "yellow"},
                                {"panel": "salon",
                                 "tile_text_color": "black",
                                 "tile_background": "transparent",
                                 "tile_size": "small"}))
        check("a screen's own launchers: entry wins over the house's",
              seen["name"] == rgb(launcher.PALETTES["black"])
              and seen["bg"] == "rgba(0, 0, 0, 0)", str(seen["name"]))
        seen = look(page, serve({"tile_text_color": "not-a-colour"}))
        check("an unknown name follows the theme rather than failing",
              seen["name"] == "rgb(232, 236, 244)", seen["name"])

        browser.close()
    print("ok" if not faults else f"{len(faults)} ECHEC")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
