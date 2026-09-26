#!/usr/bin/env python3
"""One arrow, one move -- and never on a page that navigates itself.

WHY THIS EXISTS. A panel reported that up, down, left and right inside a link
need several presses each, "compared to YouTube which is very fluid". The
report is exact and the cause was already written down in CLAUDE.md as the one
thing `--enable-spatial-navigation` does not fix: Chromium SCROLLS a row into
view before it will focus it, so a row fully off screen costs two presses of
scrolling before the third moves anything. YouTube never shows it because its
television interface moves its own focus.

Nothing but a real browser can see this, so this measures PRESSES PER MOVE on
a media-site grid -- rows of links, no key handler, taller than the panel --
and then spends most of its cases on the half that could do harm: standing
down wherever the page navigates for itself.

`--without` runs the same fixtures with the script left out, which is how the
reported fault is reproduced against the shipped file rather than a reverted
copy. It reads 3 presses per row, exactly as the panel did.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM; without either
it uses whatever Playwright has installed.
"""

import functools
import http.server
import os
import pathlib
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))

import launcher  # noqa: E402
from ha_send import BROWSER_ARGS, SPATNAV_JS, NavNotes  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")
WITHOUT = "--without" in sys.argv

faults = []


def check(what, ok):
    print(("  ok     " if ok else "  ECHEC  ") + what)
    if not ok:
        faults.append(what)


def serve(body):
    """One page on a port of its own. Not a file:// URL: a panel never sees one."""
    folder = tempfile.mkdtemp()
    (pathlib.Path(folder) / "index.html").write_text(
        "<!doctype html><meta charset=utf-8>" + body)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=folder)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/" % server.server_address[1]


TILE = ('<style>body{margin:0}a{display:block;width:180px;height:110px;'
        'background:#222;border:3px solid #222;box-sizing:border-box}'
        'a:focus{border-color:#4af}.row{display:flex;gap:12px}'
        'h2{margin:18px 12px 8px}</style>')
GRID = TILE + "".join(
    '<h2>%d</h2><div class=row>%s</div>' % (
        r, "".join('<a href="#" id="t%d_%d">%d-%d</a>' % (r, c, r, c)
                   for c in range(6)))
    for r in range(8))

SMALL = '<style>a{display:block;width:120px;height:60px}</style>' + "".join(
    '<a href="#" id="a%d">%d</a>' % (i, i) for i in range(6))

# A page that handles the arrow AND swallows it, deliberately moving the
# OPPOSITE way so that which of the two ran is readable rather than inferred.
POLITE = SMALL + """<script>
addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { document.getElementById('a0').focus();
                               e.preventDefault(); }
}, true);
document.getElementById('a3').focus();
</script>"""

# And one that moves focus without swallowing, which is the case the
# after-the-fact look exists for.
RUDE = SMALL + """<script>
addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') document.getElementById('a5').focus();
}, true);
document.getElementById('a0').focus();
</script>"""

# One that stops the key on its way DOWN, so nothing after it -- this script
# included -- ever hears of it. No other trace is left of that case.
STOPPER = SMALL + """<script>
document.addEventListener('keydown', e => {
  if (e.key.startsWith('Arrow')) e.stopPropagation();
}, true);
document.getElementById('a0').focus();
</script>"""

# A "roving tabindex" row: one member reachable, the others tabindex -1 and
# meant to be reached by the page's own arrow handler -- which this one
# does not have. Nothing is found to the right, and the line has to say why.
ROVING = ('<style>div{display:inline-block;width:120px;height:60px}</style>'
          '<div id="r0" tabindex="0">r0</div>' + "".join(
              '<div id="r%d" tabindex="-1">r%d</div>' % (i, i)
              for i in range(1, 5)) +
          "<script>document.getElementById('r0').focus()</script>")

# One that moves the focus AFTER this script has, from a listener that runs
# later in the same key -- the case the stand-down exists for.
LATE = SMALL + """<script>
addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') document.getElementById('a5').focus();
}, false);
document.getElementById('a0').focus();
</script>"""

# A grid whose own handler moves one row down WITHOUT swallowing the key,
# on the way down. Without a guard the fallback then moved a second row.
GREEDY = GRID + """<script>
addEventListener('keydown', e => {
  if (e.key !== 'ArrowDown') return;
  const m = /t(\\d+)_(\\d+)/.exec(document.activeElement.id);
  if (m) document.getElementById('t' + (+m[1] + 1) + '_' + m[2]).focus();
}, true);
document.getElementById('t0_0').focus();
</script>"""

FIELD = '<input id="f" value="bonjour"><a href="#" id="a1">a1</a>'
TALL = '<a href="#" id="only">only</a><div style="height:3000px"></div>'

LINKS = [{"name": "Home Assistant", "url": "http://x/1", "icon": "home"},
         {"name": "Jellyfin", "url": "http://x/2", "icon": "jellyfin"},
         {"name": "YouTube", "url": "http://x/3", "icon": "youtube"},
         {"name": "Proxmox", "url": "http://x/4", "icon": "proxmox"},
         {"name": "Unraid", "url": "http://x/5", "icon": "unraid"},
         {"name": "Camera", "url": "http://x/6", "icon": "camera"}]


def main():
    print("Arrow navigation inside a link"
          + (" -- WITHOUT the script" if WITHOUT else "") + ":")
    with sync_playwright() as pw:
        browser = (pw.chromium.launch(args=BROWSER_ARGS,
                                      executable_path=BROWSER)
                   if BROWSER else pw.chromium.launch(args=BROWSER_ARGS))

        said = []  # what the shipped NavNotes printed, as (host, kind)

        class Recording(NavNotes):
            def __call__(self, kind, host, detail):
                before = len(self.seen)
                super().__call__(kind, host, detail)
                if len(self.seen) > before:
                    said.append((kind, str(detail)))

        notes = Recording()

        def open_page(url, width=800, height=400):
            page = browser.new_page(viewport={"width": width, "height": height})
            if not WITHOUT:
                page.expose_function("__udispNavNote", notes)
                page.add_init_script(SPATNAV_JS)
            page.goto(url)
            return page

        def told(kind):
            """Whether the line for this kind was printed, and its detail."""
            for k, detail in reversed(said):
                if k == kind:
                    return detail
            return None

        def who(page):
            return page.evaluate(
                "() => document.activeElement && document.activeElement.id")

        def presses_to_move(page, key, tries=6):
            """How many presses it really costs to move once."""
            before = who(page)
            for n in range(1, tries + 1):
                page.keyboard.press(key)
                page.wait_for_timeout(120)
                if who(page) != before:
                    return n
            return tries + 1

        # -- the measurement the report is about ------------------------
        server, url = serve(GRID)
        page = open_page(url)
        page.evaluate("() => document.getElementById('t0_0').focus()")
        costs = [presses_to_move(page, "ArrowDown") for _ in range(6)]
        print(f"         presses per row, walking down: {costs}")
        check("one press moves one row, even once the page has to scroll",
              costs == [1] * 6)
        check("and the page still scrolls to keep it in view",
              page.evaluate("() => window.scrollY") > 0)
        sideways = [presses_to_move(page, "ArrowRight") for _ in range(3)]
        check("sideways, where nothing scrolls, costs one as it always did",
              sideways == [1, 1, 1])
        page.close()
        server.shutdown()

        # -- the half that could do harm --------------------------------
        server, url = serve(POLITE)
        page = open_page(url)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(150)
        check("a page that swallows the arrow keeps it, and its own move wins",
              who(page) == "a0")
        page.close()
        server.shutdown()

        server, url = serve(RUDE)
        page = open_page(url)
        for _ in range(3):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
        check("a page that moves focus WITHOUT swallowing is left to it",
              who(page) == "a5")
        page.close()
        server.shutdown()

        server, url = serve(FIELD)
        page = open_page(url)
        page.evaluate("() => document.getElementById('f').focus()")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(150)
        check("arrows in a text field belong to the caret", who(page) == "f")
        page.close()
        server.shutdown()

        server, url = serve(TALL)
        page = open_page(url)
        page.evaluate("() => document.getElementById('only').focus()")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(200)
        check("with nothing in that direction, the browser may still scroll",
              page.evaluate("() => window.scrollY") > 0)
        page.close()
        server.shutdown()

        # -- saying why an arrow did nothing -----------------------------
        # Each of these is a different site's reason, and every one of them
        # looks the same from the glass. Each page is served on its own port,
        # so each counts as its own site for the once-per-site rule.
        if not WITHOUT:
            check("the first move on the grid was said once, naming the tile",
                  (told("moved") or "").startswith("a#t"))
            check("a page that takes the arrow is named as doing so",
                  told("handled") is not None)
            server, url = serve(LATE)
            page = open_page(url)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            check("a page that moves focus behind this script is named, "
                  "with where it put it", "a#a5" in (told("stood") or ""))
            page.close()
            server.shutdown()

            server, url = serve(GREEDY)
            page = open_page(url)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            check("a page that moves one row itself is not moved a second "
                  "row by this script", who(page) == "t1_0")
            page.close()
            server.shutdown()

            server, url = serve(STOPPER)
            page = open_page(url)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            check("a page that stops the key before this script is named",
                  told("stopped") is not None)
            check("this script did nothing there -- the browser's own "
                  "spatial navigation is what moved it", who(page) == "a1")
            page.close()
            server.shutdown()

            server, url = serve(ROVING)
            page = open_page(url)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            detail = told("none") or ""
            check("nothing to the right is said, with the four the page "
                  "keeps to itself", "right from div#r0" in detail
                  and "4 element(s)" in detail)
            page.close()
            server.shutdown()

            before = len(said)
            server, url = serve(STOPPER)
            page = open_page(url)
            for _ in range(5):
                page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            page.close()
            server.shutdown()
            check("five stopped keys on one site make one line",
                  len(said) - before == 1)

        # -- and the page this project ships ----------------------------
        # The launcher swallows every arrow itself, which is a fix this
        # repository already had reported back to it. So it is the strongest
        # case available here: our own page must be untouched.
        site = launcher.start(LINKS)
        if site is None:
            print("  --     the launcher would not bind, so it was not checked")
        else:
            page = open_page("http://127.0.0.1:8099/", 800, 420)
            page.wait_for_selector("a.tile")
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            first = page.evaluate("() => document.activeElement.textContent")
            check("the launcher's first arrow still chooses its first tile",
                  "Home Assistant" in first)
            page.evaluate("() => { document.querySelectorAll('a.tile')[0]"
                          ".focus(); window.scrollTo(0, 80); }")
            page.wait_for_timeout(80)
            before = page.evaluate("() => window.scrollY")
            page.keyboard.press("ArrowUp")
            page.wait_for_timeout(150)
            check("and up on its top row still scrolls nothing",
                  page.evaluate("() => window.scrollY") == before)
            page.evaluate("() => document.querySelectorAll('a.tile')[0].focus()")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(150)
            check("and one arrow still moves exactly one tile",
                  "Jellyfin" in page.evaluate(
                      "() => document.activeElement.textContent"))
            page.close()
        browser.close()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
