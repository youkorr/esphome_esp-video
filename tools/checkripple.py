#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Android's press on Portall's own buttons, seen from the panel.

WHY THIS EXISTS. Reported as "toutes les touches de portall ... il ne dispose
pas d'effet, est-ce normal", and asked for as "que les touches fonctionnent
comme un android". The page is told of a finger only once it LIFTS -- until
then nobody knows whether it is a tap or a scroll -- so nothing could show a
press while the finger was down, which is when a telephone shows it. Now the
sender tells Portall's own pages where the finger landed and they draw a
circle growing from it inside the button (TOUCH_FX_JS).

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle it receives into a picture, and real contacts go up its return
channel. A page with one dark button is the panel's home; the same page on
another origin stands for a site. A point inside the button, away from its
label, is read off the glass:

  - with --press-ripple, a finger held on the button lights it within a third
    of a second, and it stays lit while the finger is down;
  - lifting it fades the circle, and the button is clicked;
  - a drag that starts on the button takes the circle away and clicks
    nothing;
  - on a site the button is left alone -- it has its own look;
  - and without --press-ripple (a hand run, the sender as it was), a held
    finger changes nothing on the glass, which is the report.

And on the add-on's real launcher, in a browser: a tile and a button of the
Reglages dialog take the circle where the finger landed, inside their own
corners, without moving by a pixel, and give it back when it is over.

--sender FILE runs another copy of ha_send.py. Needs Playwright and Pillow;
takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
W, H = 800, 480
GROUND = (18, 22, 30)
BUTTON = (32, 40, 51)
# The button, in page coordinates, and a point inside it away from its label.
BOX = (100, 100, 400, 200)
SAMPLE = (140, 130)
fails = 0
events = []

PAGE = (f"<!doctype html><body style='margin:0;height:100vh;"
        f"background:rgb{GROUND}'>"
        f"<button id=b style='position:absolute;left:{BOX[0]}px;"
        f"top:{BOX[1]}px;width:{BOX[2]}px;height:{BOX[3]}px;"
        f"background:rgb{BUTTON};color:#fff;border:0;border-radius:24px;"
        f"font:24px sans-serif'>OK</button>"
        "<script>document.getElementById('b').addEventListener('click',"
        "()=>fetch('/ev?click=1',{keepalive:true}));</script>")


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
        path, _, query = self.path.partition("?")
        if path == "/ev":
            events.append(urllib.parse.parse_qsl(query)[0])
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def lit(colour):
    """Brighter than the button at rest by more than noise."""
    return sum(colour) - sum(BUTTON) > 45


def run(label, port, extra, cases):
    from PIL import Image
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import ha_send
    strip = ha_send.NavBar(W, H).height
    canvas = Image.new("RGB", (W, H))
    lock = threading.Lock()
    panel = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        panel.append(conn)
        stream = b""
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                stream += data
                while len(stream) >= _HEADER.size:
                    _, kind, _, x, y, w, h, packed = _HEADER.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    blob = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG:
                        with lock:
                            canvas.paste(Image.open(io.BytesIO(blob))
                                         .convert("RGB"), (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    def colour(at):
        with lock:
            return canvas.getpixel(at)

    def until(test, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.02)
        return False

    def contact(x, y):
        panel[0].sendall(b"T\x01\x00" + int(x).to_bytes(2, "little")
                         + int(y).to_bytes(2, "little"))

    def lift():
        panel[0].sendall(b"T\x00")

    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url",
               f"http://127.0.0.1:{port}/home", "--no-token",
               "--not-home-assistant", "--width", str(W), "--height", str(H),
               "--audio", "off", "--keyboard", "off", "--control",
               "--fps", "25", "--no-refine"] + extra
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    tools = {"colour": colour, "until": until, "contact": contact,
             "lift": lift, "strip": strip, "process": process, "port": port}
    try:
        print(label)
        middle = (BOX[0] + BOX[2] // 2, BOX[1] + BOX[3] // 2)
        check("the panel's own page arrives",
              until(lambda: bool(panel) and colour(middle) != (0, 0, 0)
                    and not lit(colour(SAMPLE)), 40),
              str(colour(SAMPLE)) if panel else "no panel")
        if not panel:
            return
        time.sleep(2.5)      # the corner mark fades
        cases(tools)
        crashed = [line.strip() for line in out if "Traceback" in line]
        check("the sender did not crash", not crashed, str(crashed[:1]))
    finally:
        process.kill()
        listener.close()


def held_press(t, y_off=0):
    """A finger held on the button's middle; what the glass shows."""
    x, y = BOX[0] + BOX[2] // 2, BOX[1] + BOX[3] // 2 + y_off
    sample = (SAMPLE[0], SAMPLE[1] + y_off)
    events.clear()
    landed = time.monotonic()
    t["contact"](x, y)
    lit_soon = t["until"](lambda: lit(t["colour"](sample)), 0.34)
    t["took"] = time.monotonic() - landed
    time.sleep(0.4)
    still = lit(t["colour"](sample))
    seen = t["colour"](sample)
    t["lift"]()
    return lit_soon, still, seen, sample


def with_ripple(t):
    lit_soon, still, seen, sample = held_press(t)
    check("a finger held on a button lights it within a third of a second",
          lit_soon, f"{seen}, {t['took']:.2f}s")
    print(f"         (lit {t['took'] * 1000:.0f} ms after the finger landed)")
    check("and it stays lit while the finger is down", still, str(seen))
    check("lifting fades it", t["until"](
        lambda: not lit(t["colour"](sample)), 1.5), str(t["colour"](sample)))
    time.sleep(0.4)
    check("and the button is clicked",
          any(k == "click" for k, _ in events), str(events))
    events.clear()
    x, y = BOX[0] + BOX[2] // 2, BOX[1] + BOX[3] // 2
    t["contact"](x, y)
    t["until"](lambda: lit(t["colour"](SAMPLE)), 0.6)
    for step in range(1, 8):
        t["contact"](x + step * 12, y)
        time.sleep(0.02)
    gone = t["until"](lambda: not lit(t["colour"](SAMPLE)), 1.0)
    t["lift"]()
    time.sleep(0.5)
    check("a drag that starts on the button takes the circle away", gone,
          str(t["colour"](SAMPLE)))
    check("and clicks nothing", not any(k == "click" for k, _ in events),
          str(events))
    # The same page on another origin is a site, with its own look.
    t["process"].stdin.write(f"open http://127.0.0.2:{t['port']}/site\n")
    t["process"].stdin.flush()
    # Below the home page's button, inside the site's, which the bar's
    # strip has moved down: that point is the button only once it arrived.
    below = (SAMPLE[0], BOX[1] + BOX[3] + t["strip"] - 6)
    arrived = t["until"](lambda: abs(sum(t["colour"](below))
                                     - sum(BUTTON)) < 20, 8)
    time.sleep(2.5)
    lit_soon, still, seen, _ = held_press(t, y_off=t["strip"])
    check("the site arrives", arrived, str(t["colour"](below)))
    check("on a site the button is left alone", not lit_soon and not still,
          str(seen))


def without_ripple(t):
    lit_soon, still, seen, _ = held_press(t)
    check("without it, a held finger changes nothing on the glass "
          "(the report)", not lit_soon and not still, str(seen))


def on_the_launcher():
    import tempfile
    from playwright.sync_api import sync_playwright
    import ha_send
    sys.path.insert(0, str(HERE / "portall"))
    import run as runner
    print("the add-on's launcher")
    with tempfile.TemporaryDirectory() as folder:
        runner.AVATAR_DIR = str(pathlib.Path(folder) / "avatar")
        runner.WALLPAPER_DIR = str(pathlib.Path(folder) / "wallpaper")
        runner.DOWNLOADS = str(pathlib.Path(folder) / "downloads")
        runner.follow_voice = lambda *args, **kwargs: None

        class NoWeather:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                return None
        runner.Weather = NoWeather
        config = runner.regroup({
            "links": [{"name": "Jellyfin", "url": "https://jellyfin.test/"},
                      {"name": "YouTube", "url": "https://youtube.test/"}],
            "launcher": {"layout": "classic", "theme": "dark"},
            "panels": [], "launchers": [], "defaults": {}})
        where = runner.start_launcher(config, port=0)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=BROWSER or None)
            context = browser.new_context(viewport={"width": 800,
                                                    "height": 1280})
            context.add_init_script(ha_send.touch_fx_js(
                {ha_send.origin_of(where)}))
            page = context.new_page()
            page.goto(where)
            tile = page.locator("a.tile").first
            before = tile.bounding_box()
            x, y = before["x"] + before["width"] * .7, before["y"] + 20
            took = page.evaluate("a => window.__portallTouch.down(a[0], a[1])",
                                 [x, y])
            page.wait_for_timeout(320)
            inside = page.evaluate("""() => {
              const r = document.querySelector('a.tile .__portall_rip');
              if (!r) return null;
              const box = r.getBoundingClientRect(),
                    tile = r.parentElement.getBoundingClientRect();
              const dot = r.firstElementChild.getBoundingClientRect();
              // Inside the tile's border, which is where `inset: 0` puts it.
              const edge = parseFloat(getComputedStyle(r.parentElement)
                                      .borderLeftWidth) || 0;
              return {fits: Math.abs(box.left - tile.left - edge) < 1
                        && Math.abs(box.width + 2 * edge - tile.width) < 1,
                      at: [box.left, box.width, tile.left, tile.width, edge],
                      grown: dot.width >= tile.width,
                      radius: getComputedStyle(r).borderRadius};
            }""")
            check("a tile takes the circle from where the finger landed",
                  took and inside and inside["fits"] and inside["grown"],
                  str(inside))
            check("inside its own rounded corners",
                  bool(inside) and inside["radius"] not in ("", "0px"),
                  str(inside))
            check("without moving by a pixel", tile.bounding_box() == before,
                  f"{before} {tile.bounding_box()}")
            page.evaluate("window.__portallTouch.up()")
            page.wait_for_timeout(700)
            check("and gives it back when it is over",
                  page.locator(".__portall_rip").count() == 0)
            page.click("#appearance-open")
            page.wait_for_selector("#appearance-dialog[open]")
            tab = page.locator("#appearance-tab-tiles")
            box = tab.bounding_box()
            took = page.evaluate("a => window.__portallTouch.down(a[0], a[1])",
                                 [box["x"] + 10, box["y"] + box["height"] / 2])
            page.wait_for_timeout(120)
            check("a button of the Reglages dialog takes it too",
                  took and page.locator(
                      "#appearance-tab-tiles .__portall_rip").count() == 1)
            page.evaluate("window.__portallTouch.cancel()")
            browser.close()


def main():
    try:
        import playwright  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        run("--press-ripple", port, ["--press-ripple"], with_ripple)
        run("without it", port, [], without_ripple)
        on_the_launcher()
    finally:
        server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
