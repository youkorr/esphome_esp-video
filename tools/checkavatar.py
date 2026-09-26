#!/usr/bin/env python3
"""The launcher's avatar: where it sits, how a finger moves it, what it costs.

WHY THIS EXISTS. Asked for as a face on the home page "de la taille d'un
button", in the bottom right corner, "que je peux le deplacer ou je veux".
Every one of those is something only a browser can say, and the move is the
part that could not work the obvious way: the sender never gives a page a
mouse drag. It puts the pointer where the finger lands and turns the drag
into WHEEL events, so the face has to be moved by wheels -- and a wheel that
lands anywhere else must still scroll the page.

This drives the add-on's own path -- the grouped form through run.regroup()
and run.start_launcher() -- then replays a finger the way the sender does:
mouse.move to the landing point, mouse.wheel with minus the finger's travel.

It also counts what the face costs a still launcher, because a still
launcher costing nothing is the property this project advertises loudest.

Needs Playwright and a Chromium. Takes $CHROMIUM.
"""

import base64
import datetime
import functools
import http.server
import io
import json
import os
import pathlib
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))

import ha_send  # noqa: E402
import launcher  # noqa: E402
import run  # noqa: E402
from PIL import Image, ImageChops  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

LINKS = [{"name": n, "url": "http://x/%d" % i, "icon": ic}
         for i, (n, ic) in enumerate([("Jellyfin", "jellyfin"),
                                      ("YouTube", "youtube"),
                                      ("Reolink", "reolink")])]
faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        faults.append(what)


def serve(avatar, label=""):
    config = run.regroup({"links": LINKS, "launcher": {"avatar": avatar,
                                                       "tiles": "buttons"}})
    return run.start_launcher(config, port=launcher.ANY_PORT, label=label)


def box(page):
    return page.evaluate("""() => {
        const b = document.getElementById('av');
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return [r.left, r.top, r.width, r.height, b.dataset.mood];
    }""")


def weathers():
    """What the weather puts on the face, from Home Assistant's own words."""
    cases = [
        ({"condition": "rainy", "temperature": 12, "unit": "°C"}, "rain"),
        ({"condition": "pouring", "temperature": 30, "unit": "°C"}, "rain"),
        ({"condition": "snowy", "temperature": -2, "unit": "°C"}, "snow"),
        ({"condition": "sunny", "temperature": 28, "unit": "°C"}, "hot"),
        ({"condition": "sunny", "temperature": 77, "unit": "°F"}, "hot"),
        ({"condition": "partlycloudy", "temperature": 26, "unit": "°C"}, "hot"),
        ({"condition": "cloudy", "temperature": 30, "unit": "°C"}, ""),
        ({"condition": "sunny", "temperature": 20, "unit": "°C"}, ""),
        ({"condition": "cloudy", "temperature": 3, "unit": "°C"}, "cold"),
        ({"condition": "clear-night", "temperature": 40, "unit": "°F"}, "cold"),
        ({"condition": "cloudy", "temperature": None, "unit": ""}, ""),
        (None, ""),
    ]
    wrong = [(s, want, launcher.avatar_weather(s)) for s, want in cases
             if launcher.avatar_weather(s) != want]
    check("the weather dresses it: rain, snow, hot and sunny, cold, or nothing",
          not wrong, str(wrong))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def pages():
    """Two little pages to open, each a colour, on a server of their own."""
    root = tempfile.mkdtemp()
    for name, colour in (("a", "#b91c1c"), ("b", "#15803d")):
        pathlib.Path(root, name).mkdir()
        pathlib.Path(root, name, "index.html").write_text(
            f"<body style='background:{colour}'>{name}</body>")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def character(browser):
    """The five bodies, and what the face does besides being moved."""
    weathers()
    for shape in launcher.AVATAR_SHAPES:
        body = launcher.render(LINKS, avatar=True, avatar_shape=shape)
        check(f"{shape}: drawn in its own body",
              f'data-shape="{shape}"' in body and "{" not in
              launcher.avatar_html(shape))
    check("a shape nobody knows is the mochi, never a failure",
          'data-shape="mochi"' in launcher.render(LINKS, avatar=True,
                                                  avatar_shape="dragon"))
    config = run.regroup({"links": LINKS, "launcher": {"avatar": True}})
    check("the form's own default is the mochi",
          'data-shape="mochi"' in launcher.render(
              LINKS, avatar=True, avatar_shape=config.get(
                  "launcher_avatar_shape") or launcher.DEFAULT_AVATAR))

    site = pages()
    links = [{"name": "Rouge", "url": site + "/a/", "icon": "tv"},
             {"name": "Vert", "url": site + "/b/", "icon": "tv"}]
    reading = {"condition": "rainy", "temperature": 12, "unit": "°C",
               "text": "12°C"}
    weather = lambda: dict(reading)  # noqa: E731

    class Silent:
        """A voice assistant nobody talks to: the page's question is held."""
        def wait(self, since):
            threading.Event().wait(60)
            return since, "neutral"

    def launch(shape, hour, reading_now=None):
        if reading_now is not None:
            reading.clear()
            reading.update(reading_now)
        address = launcher.start(links, avatar=True, avatar_shape=shape,
                                 port=launcher.ANY_PORT, weather=weather,
                                 voice=Silent())
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.clock.install(time=datetime.datetime(2026, 9, 27, hour, 0))
        page.goto(address)
        page.wait_for_selector("#av")
        return page, address

    def shown(page, selector):
        return page.evaluate(f"""() => {{
            const e = document.querySelector('#av {selector}');
            return !!e && getComputedStyle(e).display !== 'none'; }}""")

    def data(page, key):
        return page.evaluate(f"document.getElementById('av').dataset.{key}")

    page, address = launch("mochi", 12)
    check("served on a rainy day it starts with its cloud",
          data(page, "wx") == "rain" and shown(page, ".acc.rain")
          and not shown(page, ".acc.hot"))
    reading.update(condition="sunny", temperature=31, text="31°C")
    page.clock.fast_forward("02:05")
    page.wait_for_timeout(300)
    check("and dresses again when the reading changes, without a reload",
          data(page, "wx") == "hot" and shown(page, ".acc.hot")
          and not shown(page, ".acc.rain"), str(data(page, "wx")))

    # Only what is painted takes a touch: the box's corner is the page's.
    corner = page.evaluate("""() => {
        const r = document.getElementById('av').getBoundingClientRect();
        const e = document.elementFromPoint(r.left + 3, r.top + 3);
        return !!e && !!e.closest('#av'); }""")
    check("a touch in the empty corner of its box is not a touch on it",
          not corner)

    page.evaluate("window.portallAvatar.stand('surprised')")
    check("it listens: the waves are out",
          shown(page, ".acc.waves") and data(page, "voice") == "surprised")
    page.evaluate("window.portallAvatar.stand('thinking')")
    check("it thinks: a bubble", shown(page, ".acc.bubble")
          and not shown(page, ".acc.waves"))
    page.evaluate("window.portallAvatar.stand('neutral')")
    check("and when the voice assistant is idle, all of it goes",
          not shown(page, ".acc.bubble") and data(page, "mood") == "neutral")

    # It looks at the tile a remote chooses, and where a finger lands.
    page.evaluate("document.querySelector('a.tile').focus()")
    look = page.evaluate("""() => [getComputedStyle(document.getElementById('av'))
        .getPropertyValue('--lx'), getComputedStyle(document.getElementById('av'))
        .getPropertyValue('--ly')]""")
    check("a tile chosen with a remote: it looks at it",
          look[0].strip() not in ("", "0px") and float(look[0].strip()[:-2]) < 0,
          str(look))
    page.mouse.move(1270, 10)
    page.mouse.down()
    page.mouse.up()
    look = page.evaluate("""() => getComputedStyle(document.getElementById('av'))
        .getPropertyValue('--ly')""")
    check("a finger landing above it: it looks up",
          float(look.strip()[:-2]) < 0, look)

    # Asked by voice: it looks, the tile goes down, then the page follows.
    target = links[1]["url"]
    check("a link asked for by voice is opened by the face",
          ha_send.open_link(page, target) and page.url == address)
    page.wait_for_timeout(400)
    pressed = page.evaluate("""u => [...document.querySelectorAll('a.tile')]
        .filter(t => t.classList.contains('press')).map(t => t.href)""", target)
    check("it smiles at it and the tile goes down first",
          data(page, "mood") == "happy" and pressed == [target], str(pressed))
    page.clock.fast_forward(1000)
    page.wait_for_url(target, timeout=5000)
    check("then the page follows the link itself", page.url == target)
    check("an address no tile carries is opened the ordinary way",
          ha_send.open_link(page, links[0]["url"]) and page.url == links[0]["url"])
    page.close()

    page, _ = launch("robot", 23, {"condition": "cloudy", "temperature": 15,
                                   "unit": "°C", "text": "15°C"})
    check("at night it dozes, with its z's", data(page, "mood") == "sleepy"
          and shown(page, ".acc.zzz") and data(page, "wx") == "")
    page.evaluate("window.portallAvatar.stand('surprised')")
    lit = page.evaluate("""() => getComputedStyle(
        document.querySelector('#av .bulb')).fill""")
    check("the robot listens with its antenna, not with waves",
          not shown(page, ".acc.waves") and lit == "rgb(56, 189, 248)", lit)
    page.evaluate("window.portallAvatar.stand('neutral')")
    check("and goes back to dozing, since it is still night",
          data(page, "mood") == "sleepy")
    page.close()


def main():
    run.AVATAR_DIR = tempfile.mkdtemp()
    browser_path = os.environ.get("CHROMIUM") or None
    print("The avatar on the launcher:")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        page.goto(serve(False))
        page.wait_for_selector("a.tile")
        check("off, which is the default, draws no face", box(page) is None)

        address = serve(True, label=" [salon]")
        page.goto(address)
        page.wait_for_selector("#av")
        x, y, w, h, mood = box(page)
        button = page.evaluate("""() => document.querySelector('a.tile')
            .getBoundingClientRect().width""")
        _, _, vw, vh = map(float, launcher.AVATAR_VIEW.split())
        check("as wide as a button, as tall as its drawing", abs(w - button) < 2
              and abs(w / h - vw / vh) < 0.02,
              f"{w:.0f}x{h:.0f}, a button {button:.0f}")
        check("in the bottom right corner", 1280 - (x + w) < 30 and
              800 - (y + h) < 30, f"at {x:.0f},{y:.0f}")

        cx, cy = x + w / 2, y + h / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.wait_for_timeout(80)
        page.mouse.up()
        page.wait_for_timeout(200)
        check("a tap makes it smile, and opens nothing",
              box(page)[4] == "happy" and page.url == address)

        # A finger landing on it and travelling 600 left and 300 up, sent the
        # way the sender sends it: the pointer at the landing, then wheels.
        page.mouse.move(cx, cy)
        for _ in range(15):
            page.mouse.wheel(40, 20)
            page.wait_for_timeout(30)
        page.wait_for_timeout(900)
        nx, ny = box(page)[:2]
        check("dragged with a finger, it follows the finger", abs(nx - (x - 600))
              < 2 and abs(ny - (y - 300)) < 2, f"{x:.0f},{y:.0f} -> "
              f"{nx:.0f},{ny:.0f}")
        check("and the page under it did not scroll",
              page.evaluate("scrollY") == 0)

        for _ in range(40):
            page.mouse.wheel(-200, -200)
        page.wait_for_timeout(700)
        ex, ey = box(page)[:2]
        check("it cannot be pushed off the panel", 1280 - (ex + w) < 1 and
              800 - (ey + h) < 1 and ex >= 0, f"{ex:.0f},{ey:.0f}")

        page.mouse.move(cx - 300, cy - 200)
        for _ in range(15):
            page.mouse.wheel(40, 20)
        page.wait_for_timeout(700)
        check("a drag starting anywhere else does not move it",
              box(page)[:2] == [ex, ey])

        saved = pathlib.Path(run.AVATAR_DIR, "salon.json")
        check("where it was put is kept by the add-on, under the panel's name",
              saved.exists() and json.loads(saved.read_text())["x"] == 1.0,
              saved.read_text() if saved.exists() else "no file")

        # Put it somewhere in the middle, then start the launcher again, the
        # way a restart does, on a fresh port.
        page.mouse.move(ex + w / 2, ey + h / 2)
        for _ in range(10):
            page.mouse.wheel(50, 30)
            page.wait_for_timeout(30)
        page.wait_for_timeout(900)
        before = box(page)[:2]
        page.goto(serve(True, label=" [salon]"))
        page.wait_for_selector("#av")
        after = box(page)[:2]
        check("after a restart it is where it was left", abs(before[0] -
              after[0]) < 2 and abs(before[1] - after[1]) < 2,
              f"{before} -> {after}")

        # What it costs a launcher nobody touches.
        cdp = page.context.new_cdp_session(page)
        frames = []

        def got(event):
            frames.append(event["data"])
            cdp.send("Page.screencastFrameAck",
                     {"sessionId": event["sessionId"]})

        # At noon, awake: a dozing face does not glance about, so measured at
        # night this would count a cheaper face than the one people see.
        page.clock.install(time=datetime.datetime(2026, 9, 27, 12, 0))
        page.goto(serve(True, label=" [cost]"))
        page.wait_for_selector("#av")
        page.wait_for_timeout(1000)
        cdp.on("Page.screencastFrame", got)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 80})
        page.wait_for_timeout(15000)
        cdp.send("Page.stopScreencast")
        changed, before = 0, None
        for data in frames:
            picture = Image.open(io.BytesIO(base64.b64decode(data))).convert("L")
            if before is not None and ImageChops.difference(
                    picture, before).getbbox():
                changed += 1
            before = picture
        check("still, it costs a few pictures of its eyes, not a stream",
              changed <= 14, f"{changed} changed pictures in 15 s")
        character(browser)
        browser.close()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
