#!/usr/bin/env python3
"""The slideshow as a screen saver -- on the add-on's page, and on the glass.

WHY THIS EXISTS. Asked as the slideshow working "comme un ecran de veille
tout en laissant l'heure, date, meteo ... surtout sans les link", stopping
when the screen goes dark and back to normal at a touch -- and, asked how it
should start, "regarde le comportement d'un pc, d'une tablette". A PC's lock
screen and a tablet's photo frame come up after a while without a touch, over
whatever is showing, and a touch goes back to it. This checks every half of
that through the shipped code:

  - the launcher: with a delay the page of links keeps its first picture
    still, /saver has no tile, cycles the pictures, and shows the clock, the
    date, the weather and the face only where asked for;
  - run.py: a launcher panel is handed --saver and --saver-after, and a
    delay of 0 keeps the old behaviour and hands nothing;
  - the sender, against a fake panel that reassembles the rectangles: the
    saver comes up over a site after the delay, a touch brings the site back
    exactly as it was and clicks nothing, the screen going dark stops the
    saver's page, and a page playing a sound keeps it away.

--sender PATH runs another copy of ha_send.py. Needs Playwright and Pillow.
"""
import http.server
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
W, H = 800, 480
SITE = (0x20, 0x60, 0xA0)
PICTURES = [(0xD0, 0x30, 0x30), (0x30, 0xC0, 0x40), (0xE0, 0xC0, 0x20)]
fails = 0
hits = []


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=40):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        hits.append(self.path)
        if self.path.startswith("/click"):
            self.send_response(204)
            self.end_headers()
            return
        sound = ""
        if self.path.startswith("/music"):
            # A second of a tone, looping, with its sound on.
            sound = ("<audio src='/tone.wav' autoplay loop></audio>")
        if self.path.startswith("/tone.wav"):
            import math
            import struct
            rate = 8000
            frames = b"".join(struct.pack("<h", int(8000 * math.sin(i / 3)))
                              for i in range(rate))
            data = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
                    + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
                    + b"data" + struct.pack("<I", len(frames)) + frames)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        body = (f"<!doctype html><body style='margin:0;height:100vh;"
                f"background:rgb{SITE}' onclick=\"fetch('/click')\">"
                f"{sound}</body>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def pictures():
    from PIL import Image
    folder = tempfile.mkdtemp()
    for i, colour in enumerate(PICTURES):
        Image.new("RGB", (64, 64), colour).save(f"{folder}/{i}.png")
    return folder


def launcher_half(browser, folder):
    import launcher
    links = [{"name": "Site", "url": "http://example.invalid/", "icon": "home"}]
    where = launcher.start(links, background=folder, slideshow=True,
                           every=1, fade=0, port=launcher.ANY_PORT,
                           saver_after=2, saver_date=False,
                           saver_weather=False, saver_avatar=False,
                           avatar=True)
    base = where.split("?")[0].rstrip("/")
    page = browser.new_page(viewport={"width": W, "height": H})
    page.goto(base + "/")
    page.wait_for_timeout(300)
    check("the page of links keeps its tiles",
          page.locator("a.tile").count() == 1)
    first = page.evaluate("getComputedStyle(document.getElementById('wa'))"
                          ".backgroundImage")
    page.wait_for_timeout(2300)
    check("and with a delay its picture stays still behind them",
          page.evaluate("getComputedStyle(document.getElementById('wa'))"
                        ".backgroundImage") == first
          and page.evaluate("getComputedStyle(document.getElementById('wb'))"
                            ".opacity") == "0")
    page.goto(base + "/saver")
    page.wait_for_timeout(300)
    check("the saver has no tile", page.locator("a.tile").count() == 0)
    check("and shows the time",
          page.evaluate("getComputedStyle(document.getElementById('t'))"
                        ".display") != "none")
    check("and not the date it was told to leave out",
          page.evaluate("(() => { const d = document.getElementById('d');"
                        " return !d || getComputedStyle(d).display == 'none';"
                        " })()"))
    check("nor the face it was told to leave out",
          page.locator(".avatar, #avatar").count() == 0)
    seen = set()
    for _ in range(14):
        seen.add(page.evaluate(
            "[...document.querySelectorAll('.wall')].map(w => "
            "getComputedStyle(w).opacity + getComputedStyle(w)"
            ".backgroundImage).join('|')"))
        page.wait_for_timeout(250)
    check("and changes its picture by itself", len(seen) >= 2, seen)
    page.close()

    # Reported as "tu as mis clock en huge trop grand, date, meteo en bas de
    # l'ecran sauf avatar ... il faut que l'utilisateur doit tout choisir":
    # each one where it is put, as big and in the colour it is given.
    layout = {"clock_position": "bottom_right", "clock_size": "small",
              "clock_color": "amber", "date_position": "top",
              "date_size": "huge", "weather_position": "bottom_left",
              "weather_color": "sky", "avatar_position": "top_left",
              "avatar_size": "large", "shade": "strong"}
    placed = launcher.start(links, background=folder, slideshow=True,
                            every=1, fade=0, port=launcher.ANY_PORT,
                            saver_after=2, avatar=True, saver_layout=layout,
                            weather=lambda: {"condition": "sunny",
                                             "text": "21°C"})
    page = browser.new_page(viewport={"width": W, "height": H})
    page.goto(placed.split("?")[0].rstrip("/") + "/saver")
    page.wait_for_timeout(400)
    box = lambda sel: page.evaluate(
        f"(() => {{ const r = document.querySelector({sel!r})"
        ".getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom,"
        " r.width, r.height]; })()")
    t, d, wx, av = box("#t"), box("#d"), box(".wx"), box("#av")
    check("the time goes where it is put (bottom right)",
          t[2] > W * .8 and t[3] > H * .8, t)
    check("the date where it is put (top, centred)",
          d[1] < H * .25 and abs((d[0] + d[2]) / 2 - W / 2) < W * .05, d)
    check("the weather where it is put (bottom left)",
          wx[4] > 20 and wx[0] < W * .15 and wx[3] > H * .8, wx)
    check("the face where it is put (top left)",
          av[0] < W * .15 and av[1] < H * .15, av)
    style = lambda sel, prop: page.evaluate(
        f"getComputedStyle(document.querySelector({sel!r})).{prop}")
    # small is clamp(22px, 5vw, 40px): 40 px at 800 wide; huge date 28.8.
    check("the time at the size it is given",
          style("#t", "fontSize") == "40px", style("#t", "fontSize"))
    check("the date at the size it is given",
          style("#d", "fontSize") == "28.8px", style("#d", "fontSize"))
    check("the face at the size it is given (36vmin)",
          abs(av[4] - 0.36 * min(W, H)) < 2, av)
    check("the time in the colour it is given",
          style("#t", "color") == "rgb(245, 158, 11)", style("#t", "color"))
    check("the temperature in the colour it is given",
          style(".wx .out", "color") == "rgb(14, 165, 233)")
    check("the date in white when nothing is said",
          style("#d", "color") == "rgb(255, 255, 255)")
    check("a strong veil under each corner used, and nowhere else",
          style(".veil", "backgroundImage").count("radial-gradient") == 4
          and "0.55" in style(".veil", "backgroundImage"))
    page.close()
    # Nothing said: the time and the date top left, the weather top right,
    # the face bottom right, the time at the page of links' medium -- not
    # the lock screen's huge one it used to be.
    plain = launcher.start(links, background=folder, slideshow=True, every=1,
                           fade=0, port=launcher.ANY_PORT, saver_after=2,
                           avatar=True, saver_layout={"clock_position": "nope"},
                           weather=lambda: {"condition": "sunny",
                                            "text": "21°C"})
    page = browser.new_page(viewport={"width": W, "height": H})
    page.goto(plain.split("?")[0].rstrip("/") + "/saver")
    page.wait_for_timeout(400)
    t, d, wx, av = box("#t"), box("#d"), box(".wx"), box("#av")
    check("by default the time and the date top left, one above the other",
          t[0] < W * .1 and t[1] < H * .2 and d[1] >= t[3] - 2, (t, d))
    check("the weather top right",
          wx[4] > 20 and wx[2] > W * .85 and wx[1] < H * .2, wx)
    check("the face bottom right", av[2] > W * .85 and av[3] > H * .85, av)
    check("the time at medium (clamp(34px, 8vw, 68px) is 64px here)",
          style("#t", "fontSize") == "64px", style("#t", "fontSize"))
    check("and a word nobody expected is the default, never a failure",
          t[0] < W * .1)
    page.close()

    # Reported as "cela remplace mon fond ecran": a wallpaper of its own
    # and the slideshow's pictures BY ADDRESS. The addresses won over the
    # wallpaper on the page of links; they are the saver's alone now.
    wall = data_png((0x10, 0x10, 0x90))
    slides = [data_png(c) for c in PICTURES]
    both = launcher.start(links, background=wall, slideshow=True, urls=slides,
                          every=1, fade=0, port=launcher.ANY_PORT,
                          saver_after=2)
    base = both.split("?")[0].rstrip("/")
    page = browser.new_page(viewport={"width": W, "height": H})
    page.goto(base + "/")
    page.wait_for_timeout(300)
    shown = page.evaluate("getComputedStyle(document.getElementById('wa'))"
                          ".backgroundImage")
    check("a wallpaper of its own stays behind the links",
          wall in shown and not any(s in shown for s in slides), shown[:80])
    page.goto(base + "/saver")
    page.wait_for_timeout(300)
    shown = page.evaluate("getComputedStyle(document.getElementById('wa'))"
                          ".backgroundImage")
    check("and the slideshow's addresses go to the saver",
          any(s in shown for s in slides) and wall not in shown, shown[:80])
    page.close()
    alone = launcher.start(links, slideshow=True, urls=slides, every=1,
                           fade=0, port=launcher.ANY_PORT, saver_after=2)
    page = browser.new_page(viewport={"width": W, "height": H})
    page.goto(alone.split("?")[0])
    page.wait_for_timeout(300)
    check("with no wallpaper of its own the page keeps the first, still",
          slides[0] in page.evaluate(
              "getComputedStyle(document.getElementById('wa'))"
              ".backgroundImage"))
    page.close()

    # With nought, the old behaviour: no saver, the pictures behind the
    # links.
    still = launcher.start(links, background=folder, slideshow=True,
                           every=1, fade=0, port=launcher.ANY_PORT,
                           saver_after=0)
    html = urllib_get(still.split("?")[0])
    check("a delay of 0 cycles the pictures behind the links as before",
          "setInterval(show" in html)


def data_png(colour):
    import base64
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def urllib_get(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as answer:
        return answer.read().decode()


def addon_half():
    import run
    run._config = run.regroup({
        "links": [{"name": "Site", "url": "http://x/"}],
        "launcher": {"slideshow": {"enabled": True, "after": 3}},
    })
    panels = [{"name": "salon", "host": "1.2.3.4", "url": "launcher"}]
    run.STATUS = None
    run.route_to_launcher(panels, "http://127.0.0.1:8099/")
    line = run.command_for(dict(panels[0]))
    check("a launcher panel is handed the saver's address",
          "--saver" in line
          and line[line.index("--saver") + 1] == "http://127.0.0.1:8099/saver",
          line)
    check("and the delay in seconds",
          "--saver-after" in line
          and line[line.index("--saver-after") + 1] == "180")
    run._config = run.regroup({
        "links": [{"name": "Site", "url": "http://x/"}],
        "launcher": {"slideshow": {"enabled": True, "after": 0}},
    })
    panels = [{"name": "salon", "host": "1.2.3.4", "url": "launcher"}]
    run.route_to_launcher(panels, "http://127.0.0.1:8099/")
    check("a delay of 0 hands nothing",
          "--saver" not in run.command_for(dict(panels[0])))

    # The places, sizes and colours reach the page from the form -- the
    # house's grouped keys and a launchers: entry's flat ones alike.
    import launcher
    seen = []
    real = launcher.start
    launcher.start = lambda *a, **k: seen.append(k) or "http://x/"
    try:
        house = run.regroup({
            "links": [{"name": "Site", "url": "http://x/"}],
            "launcher": {"slideshow": {"enabled": True,
                                       "clock_position": "bottom",
                                       "clock_size": "small",
                                       "shade": "none"}},
        })
        run.start_launcher(house, port=launcher.ANY_PORT)
        own = run.launcher_config(house, {
            "panel": "salon", "links": [{"name": "Site", "url": "http://x/"}],
            "slideshow_avatar_position": "top", "slideshow_clock_size": "huge"})
        run.start_launcher(own, port=launcher.ANY_PORT)
    finally:
        launcher.start = real
    check("the house's places reach the page",
          seen and seen[0].get("saver_layout") == {
              "clock_position": "bottom", "clock_size": "small",
              "shade": "none"}, seen and seen[0].get("saver_layout"))
    got = launcher.saver_look(seen[1].get("saver_layout")) if len(seen) > 1 \
        else {}
    check("and a screen's own launcher lays its own over them",
          got.get("avatar_position") == "top"
          and got.get("clock_size") == "huge"
          and got.get("clock_position") == "bottom", got)


def sender_half(folder):
    from PIL import Image
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import launcher

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    saver_home = launcher.start(
        [{"name": "Site", "url": site + "/"}], background=folder,
        slideshow=True, every=1, fade=0, port=launcher.ANY_PORT,
        saver_after=1, saver_weather=False)
    saver_url = saver_home.split("?")[0].rstrip("/") + "/saver"

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
                    payload = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind != UDISP_TYPE_JPG:
                        continue
                    try:
                        pic = Image.open(io.BytesIO(payload)).convert("RGB")
                    except Exception:  # noqa: BLE001
                        continue
                    with lock:
                        canvas.paste(pic, (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    # Top right: away from the saver's clock, low left, and from the
    # corner mark, top left.
    spot = (W - 40, 40)

    def colour():
        with lock:
            return canvas.getpixel(spot)

    def wait(test, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test(colour()):
                return True
            time.sleep(0.05)
        return False

    def is_picture(c):
        return any(near(c, p) for p in PICTURES)

    def tap():
        x, y = W // 2, H // 2
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    work = tempfile.mkdtemp()
    command = [sys.executable, "-u", SENDER,
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", site + "/site",
               "--not-home-assistant", "--width", str(W), "--height", str(H),
               "--audio", "off", "--keyboard", "off", "--no-nav-bar",
               "--profile", os.path.join(work, "p"),
               "--saver", saver_url, "--saver-after", "2"]
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    try:
        ok = wait(lambda c: near(c, SITE), 40)
        check("the site is on the panel", ok, "".join(out[-12:]))
        if not ok:
            return
        check("after the delay with no touch, the saver comes up over it",
              wait(is_picture, 6), colour())
        seen = set()
        end = time.monotonic() + 3.5
        while time.monotonic() < end:
            c = colour()
            for i, p in enumerate(PICTURES):
                if near(c, p):
                    seen.add(i)
            time.sleep(0.1)
        check("and its pictures change on the panel", len(seen) >= 2, seen)
        clicks = sum(1 for h in hits if h.startswith("/click"))
        tap()
        check("a touch brings the site back", wait(lambda c: near(c, SITE), 4),
              colour())
        time.sleep(0.6)
        check("and that touch pressed nothing on it",
              sum(1 for h in hits if h.startswith("/click")) == clicks)
        tap()
        time.sleep(0.6)
        check("the next touch reaches the page as usual",
              sum(1 for h in hits if h.startswith("/click")) == clicks + 1)
        check("the saver comes back after the delay again",
              wait(is_picture, 6), colour())
        before = sum(1 for h in hits if "wallpaper" in h)
        panel[0].sendall(b"S\x00")
        time.sleep(1.0)
        settled = sum(1 for h in hits if "wallpaper" in h)
        time.sleep(3.0)
        check("the screen going dark stops the saver's page",
              any("Screen saver: off (the screen went dark)" in o for o in out)
              and sum(1 for h in hits if "wallpaper" in h) == settled,
              (before, settled, sum(1 for h in hits if "wallpaper" in h)))
        panel[0].sendall(b"S\x01")
        check("waking shows the site, not the saver",
              wait(lambda c: near(c, SITE), 4)
              and not wait(is_picture, 1.2), colour())

        # A page playing a sound keeps it away.
        tap()
        time.sleep(0.4)
    finally:
        process.kill()

    command[command.index("--url") + 1] = site + "/music"
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out2 = []
    threading.Thread(target=lambda: out2.extend(process.stdout),
                     daemon=True).start()
    panel.clear()
    threading.Thread(target=accept, daemon=True).start()
    try:
        wait(lambda c: near(c, SITE), 40)
        time.sleep(5)
        check("a page playing a sound keeps the saver away",
              not any("Screen saver: on" in o for o in out2)
              and any("not now, something is playing" in o for o in out2),
              "".join(out2[-25:]))
    finally:
        process.kill()
        server.shutdown()


def main():
    try:
        from PIL import Image  # noqa: F401
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    folder = pictures()
    print("The launcher:")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=BROWSER or p.chromium.executable_path)
        launcher_half(browser, folder)
        browser.close()
    print("The add-on:")
    addon_half()
    print("The sender, on a fake panel:")
    sender_half(folder)
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
