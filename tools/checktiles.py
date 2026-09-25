#!/usr/bin/env python3
"""Does a launcher tile keep its words whole at every column count?

Reported from a panel with two photographs: at 1280x800 with `columns: 5`
the names read "Jellyfi n", "Reoli nk", "YouT ube", and at 6 they came out
one letter a line down the side of the tile. The icon sits BESIDE the name,
so a narrow tile leaves the name a sliver, and `overflow-wrap: anywhere`
then breaks it wherever it likes.

This serves the real launcher, opens it at a panel's own size, and asks
the browser where each word of each name was laid out: a word whose letters
do not all sit on one line has been cut. It also asks that no tile runs off
the side of the panel, which a fix that simply forbade breaking would cause.

    python3 tools/checktiles.py [--browser /opt/pw-browsers/chromium]
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

# The panel's own eight, in its own order and with its own descriptions.
LINKS = [
    {"name": "Home Assistant", "url": "http://x/1", "icon": "home-assistant"},
    {"name": "Jellyfin", "url": "http://x/2", "icon": "jellyfin",
     "description": "Films et series"},
    {"name": "Reolink", "url": "http://x/3", "icon": "camera",
     "description": "Camera"},
    {"name": "Immich", "url": "http://x/4", "icon": "immich",
     "description": "Photos"},
    {"name": "YouTube", "url": "http://x/5", "icon": "youtube",
     "description": "TV"},
    {"name": "netflix", "url": "http://x/6", "icon": "netflix",
     "description": "Film et Serie"},
    {"name": "Spotify", "url": "http://x/7", "icon": "spotify",
     "description": "Musique"},
    {"name": "Orange TV", "url": "http://x/8", "icon": "tv",
     "description": "TV"},
]

# For every word of every name and description: does it sit on one line?
# A Range over the word gives one client rect per line box it touches.
CUT_WORDS = """() => {
  const cut = [];
  for (const el of document.querySelectorAll('.name, .desc')) {
    const node = el.firstChild;
    if (!node || node.nodeType !== 3) continue;
    const text = node.textContent;
    const re = /\\S+/g;
    let m;
    while ((m = re.exec(text))) {
      const r = document.createRange();
      r.setStart(node, m.index);
      r.setEnd(node, m.index + m[0].length);
      const tops = new Set([...r.getClientRects()].map(x => Math.round(x.top)));
      if (tops.size > 1) cut.push(m[0]);
    }
  }
  return cut;
}"""

OVERFLOW = """() => {
  const w = document.documentElement.clientWidth;
  return [...document.querySelectorAll('a.tile')]
    .filter(t => t.getBoundingClientRect().right > w + 1)
    .map(t => t.querySelector('.name').textContent);
}"""

ACROSS = """() => {
  const rows = {};
  for (const t of document.querySelectorAll('a.tile')) {
    const k = Math.round(t.getBoundingClientRect().top);
    rows[k] = (rows[k] || 0) + 1;
  }
  return Math.max(...Object.values(rows));
}"""

fails = 0


def ok(what, passed, detail=""):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what
          + (f"  ({detail})" if detail and not passed else ""))
    if not passed:
        fails += 1


CASES = [  # panel size, columns asked for
    ((1280, 800), 5), ((1280, 800), 6), ((1280, 800), 4),
    ((1280, 800), 0),
    ((1024, 600), 0), ((1024, 600), 1), ((1024, 600), 2),
    ((1024, 600), 3), ((1024, 600), 4), ((1024, 600), 5),
    ((1024, 600), 6),
    ((800, 1280), 0), ((800, 1280), 3), ((800, 1280), 4),
]

# A button is a fixed shape, so what it holds can outgrow it where a card
# would simply grow taller: the icon and the name must fit inside the button.
SPILLS = """() => [...document.querySelectorAll('a.tile')]
  .filter(t => t.querySelector('.in').scrollHeight > t.clientHeight + 1)
  .map(t => t.querySelector('.name').textContent)"""

servers = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=BROWSER) if BROWSER \
        else pw.chromium.launch()
    for tiles in ("cards", "buttons"):
        for (w, h), columns in CASES:
            if (tiles, columns) not in servers:
                servers[tiles, columns] = launcher.start(
                    LINKS, columns=columns, tiles=tiles,
                    port=launcher.ANY_PORT)
            page = b.new_page(viewport={"width": w, "height": h})
            page.goto(servers[tiles, columns])
            page.wait_for_selector("a.tile")
            page.evaluate("document.fonts.ready")
            across = page.evaluate(ACROSS)
            cut = page.evaluate(CUT_WORDS)
            off = page.evaluate(OVERFLOW)
            label = (f"{tiles} {w}x{h}, columns {columns or 'auto'} "
                     f"({across} across)")
            ok(f"{label}: no word is cut", not cut, ", ".join(cut))
            ok(f"{label}: no tile runs off the panel", not off, ", ".join(off))
            if tiles == "buttons":
                spill = page.evaluate(SPILLS)
                ok(f"{label}: everything fits inside its button", not spill,
                   ", ".join(spill))
            if columns:
                ok(f"{label}: the columns asked for are the columns shown",
                   across == min(columns, len(LINKS)), f"{across}")
            page.close()
    b.close()

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
