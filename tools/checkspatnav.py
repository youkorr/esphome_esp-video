#!/usr/bin/env python3
"""Do the arrow keys reach a site that does not handle them itself?

WHY THIS EXISTS. A panel driving an NVIDIA Shield reported YouTube working and
Netflix, Orange TV and Jellyfin not -- "il ya juste parfois le button up et
down qui fonctionne mais difficillement". That is not a fault in the gamepad,
the dongle or the wire: a browser does not move focus between links with the
arrows, only Tab does, so an arrow on a site with no spatial navigation of its
own falls through to the browser's default, which is to SCROLL. Up and down
have somewhere to go and left and right have nothing, which is the report
word for word.

`--enable-spatial-navigation` is the fallback, and the two things worth
checking are opposite in sign:

  - it HELPS a page that ignores arrows      (Netflix, Orange TV, Jellyfin's
                                              default layout, most sites)
  - it must NOT override a page that does    (the launcher, YouTube /tv,
    handle them                               Jellyfin in TV layout)

The second is the one that could make this a bad trade, because the launcher
already swallows every arrow and is the one page that works today.

THE FLAG IS READ OFF THE SHIPPED LIST rather than written out here, so that
removing it from ha_send.py fails this check. A test carrying its own copy of
the thing it is checking can only ever agree with itself.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM; without either
it uses whatever Playwright has installed.

    python3 tools/checkspatnav.py --browser /opt/pw-browsers/chromium
"""
import http.server
import os
import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))
import ha_send
from playwright.sync_api import sync_playwright

FLAG = "--enable-spatial-navigation"

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

fails = 0


def ok(what, passed):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what)
    if not passed:
        fails += 1


# A media site's shape: rows of links, no key handler, taller than the panel.
# The height matters -- an arrow the page ignores needs somewhere to scroll TO,
# or this cannot tell "moved the focus" from "did nothing at all".
GRID = """<!doctype html><meta charset=utf-8><title>rows</title>
<style>body{margin:0;background:#111}
 .row{display:flex;gap:12px;padding:12px}
 a{display:block;width:200px;height:120px;background:#222;color:#eee}
 a:focus{outline:4px solid #6cf}</style><body>
""" + "".join(
    '<div class="row">'
    + "".join(f'<a href="#t{r}{c}" id="t{r}{c}">t{r},{c}</a>' for c in range(4))
    + "</div>" for r in range(8)) + "</body>"

# The launcher's shape, reduced to the one thing that matters: a handler that
# calls preventDefault(). It moves the opposite way ON PURPOSE, so which of the
# two ran is readable off the result rather than inferred from it.
HANDLED = """<!doctype html><meta charset=utf-8><title>handled</title>
<style>a{display:inline-block;width:150px;height:80px;background:#222}
 a:focus{outline:4px solid #6cf}</style><body>
<a href="#a" id="a">a</a><a href="#b" id="b">b</a><a href="#c" id="c">c</a>
<script>
window.addEventListener('keydown', function (e) {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  e.preventDefault();
  var ids = ['a','b','c'], i = ids.indexOf(document.activeElement.id);
  if (i < 0) i = 0;
  i += (e.key === 'ArrowRight') ? -1 : 1;      // backwards, deliberately
  if (i >= 0 && i < 3) document.getElementById(ids[i]).focus();
}, true);
</script></body>"""


def serve(body):
    data = body.encode()

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


def launch(pw, args):
    return (pw.chromium.launch(executable_path=BROWSER, args=args) if BROWSER
            else pw.chromium.launch(args=args))


def arrows(page, start, keys, scroll_to=200):
    """Press each key from the same tile and the same scroll position.

    Resetting between keys is not tidiness: measured end to end, each press
    would otherwise start from wherever the last one left the page.
    """
    moved = []
    for key in keys:
        page.evaluate(f"window.scrollTo(0,{scroll_to});"
                      f"document.getElementById('{start}').focus();")
        page.keyboard.press(key)
        page.wait_for_timeout(120)
        moved.append(page.evaluate("document.activeElement.id"))
    return moved


print("the flag is in the sender's own argument list")
ok(f"ha_send.BROWSER_ARGS carries {FLAG}", FLAG in ha_send.BROWSER_ARGS)
shipped = list(ha_send.BROWSER_ARGS)
without = [a for a in shipped if a != FLAG]

grid_port = serve(GRID)
handled_port = serve(HANDLED)

with sync_playwright() as pw:
    print("\na site with no arrow handling of its own, the reported case")
    b = launch(pw, shipped)
    page = b.new_page(viewport={"width": 800, "height": 420})
    page.goto(f"http://127.0.0.1:{grid_port}/")
    got = arrows(page, "t11", ["ArrowRight", "ArrowDown", "ArrowLeft"])
    ok("right moves to the next tile along", got[0] == "t12")
    ok("down moves to the row below", got[1] == "t21")
    ok("left moves back", got[2] == "t10")

    # Up from a row that is off screen: the first press reveals it, the second
    # takes it. Worth asserting rather than glossing -- it is what "difficult"
    # would look like if it were ever wrong, and a reader of the log deserves
    # to know one press can be a scroll.
    page.evaluate("window.scrollTo(0,200);document.getElementById('t11').focus();")
    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(120)
    first = page.evaluate("document.activeElement.id")
    page.keyboard.press("ArrowUp")
    page.wait_for_timeout(120)
    ok("up over an off-screen row scrolls, then takes it on the second press",
       first == "t11" and page.evaluate("document.activeElement.id") == "t01")
    ok("and up takes it on the FIRST press when that row is visible",
       arrows(page, "t11", ["ArrowUp"], scroll_to=0) == ["t01"])

    page.evaluate("document.getElementById('t21').focus();")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    ok("Enter still activates what is focused", page.url.endswith("#t21"))
    b.close()

    print("\nthe fault itself, kept: the same site with the flag taken out")
    b = launch(pw, without)
    page = b.new_page(viewport={"width": 800, "height": 420})
    page.goto(f"http://127.0.0.1:{grid_port}/")
    got = arrows(page, "t11", ["ArrowRight", "ArrowDown", "ArrowLeft"])
    ok("no arrow moves the focus at all, which is what a panel reported",
       got == ["t11", "t11", "t11"])
    b.close()

    print("\na page that handles its own arrows must keep winning")
    for label, args in (("with the flag", shipped), ("without it", without)):
        b = launch(pw, args)
        page = b.new_page(viewport={"width": 800, "height": 420})
        page.goto(f"http://127.0.0.1:{handled_port}/")
        page.evaluate("document.getElementById('b').focus();")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(120)
        where = page.evaluate("document.activeElement.id")
        ok(f"{label}, the page's own handler decides (b -> a, not c)",
           where == "a")
        b.close()

print()
if fails:
    print(f"{fails} check(s) failed")
    sys.exit(1)
print("ok")
