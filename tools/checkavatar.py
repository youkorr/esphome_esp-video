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
    """What the face does besides being moved."""
    weathers()
    body = launcher.render(LINKS, avatar=True)
    check("one face, drawn whole", 'id="av"' in body and "{" not in
          launcher.avatar_html() and 'class="pear"' in body)
    # A shape saved by an older add-on is a key the form no longer has: the
    # Supervisor drops it with a warning, and nothing here may ask for it.
    config = run.regroup({"links": LINKS, "launcher": {
        "avatar": True, "avatar_shape": "mochi"}})
    check("a shape saved by an older add-on is ignored, never a failure",
          "launcher_avatar_shape" not in config
          and "avatar_shape" not in run.LAUNCHER_OWN)
    # Every expression is the same commands in the same order, or the
    # browser cannot ease from one to the next and snaps instead.
    import re
    shapes = {re.sub(r"[-0-9.]+", "#", launcher.eye_path(side, name))
              for name in launcher.EXPRESSIONS for side in "lr"}
    check("every eye in every expression can ease into every other",
          len(shapes) == 1, str(len(shapes)))

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

    def launch(hour, reading_now=None, locale=None):
        if reading_now is not None:
            reading.clear()
            reading.update(reading_now)
        address = launcher.start(links, avatar=True,
                                 port=launcher.ANY_PORT, weather=weather,
                                 voice=Silent())
        extra = {"locale": locale} if locale else {}
        page = browser.new_page(viewport={"width": 1280, "height": 800},
                                **extra)
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

    page, address = launch(12)
    check("served on a rainy day it starts with its cloud",
          data(page, "wx") == "rain" and shown(page, ".acc.rain")
          and not shown(page, ".acc.hot"))
    reading.update(condition="sunny", temperature=31, text="31°C")
    page.clock.fast_forward("02:05")
    page.wait_for_timeout(300)
    check("and dresses again when the reading changes, without a reload",
          data(page, "wx") == "hot" and shown(page, ".acc.sweat")
          and not shown(page, ".acc.rain"), str(data(page, "wx")))

    # Only what is painted takes a touch: the box's corner is the page's.
    corner = page.evaluate("""() => {
        const r = document.getElementById('av').getBoundingClientRect();
        const e = document.elementFromPoint(r.left + 3, r.top + 3);
        return !!e && !!e.closest('#av'); }""")
    check("a touch in the empty corner of its box is not a touch on it",
          not corner)

    page.evaluate("window.portallAvatar.stand('thinking')")
    check("it thinks: dots", shown(page, ".acc.dots")
          and data(page, "voice") == "thinking")
    page.evaluate("window.portallAvatar.stand('neutral')")
    check("and when the voice assistant is idle, they go",
          not shown(page, ".acc.dots") and data(page, "mood") == "neutral")

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
    width = lambda side: page.evaluate(  # noqa: E731
        f"document.querySelector('#av .pb.{side}').getBoundingClientRect()"
        ".width")
    check("looking up is only looking: both eyes stay their size",
          abs(width("l") - width("r")) < 0.5)
    page.clock.fast_forward(1600)
    page.wait_for_timeout(100)
    size = width("l")
    middle = page.evaluate("""() => { const r = document.getElementById('av')
        .getBoundingClientRect(); return r.top + r.height / 2; }""")
    page.mouse.move(10, middle)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(100)
    check("a finger landing to its left: the left eye grows, the right shrinks",
          width("l") > size * 1.1 and width("r") < size * 0.9,
          f"{size:.1f} -> {width('l'):.1f} / {width('r'):.1f}")
    page.clock.fast_forward(1600)
    page.wait_for_timeout(100)
    check("and a second and a half later they are their size again",
          abs(width("l") - size) < 0.5 and abs(width("r") - size) < 0.5)

    # The panel woke: a smile and a word, in the corner the cloud uses.
    page.evaluate("window.portallAvatar.weather('rain')")
    check("woken, it says hello, and the cloud makes room for it",
          ha_send.greet_avatar(page) and shown(page, ".acc.hello")
          and data(page, "mood") == "happy" and not shown(page, ".acc.rain"))
    said = page.evaluate("document.querySelector('#av .acc.hello text')"
                         ".textContent")
    check("in the panel's language: Hello in English", said == "Hello", said)
    page.clock.fast_forward(3100)
    page.wait_for_timeout(100)
    check("and three seconds later it is back to the weather",
          not shown(page, ".acc.hello") and shown(page, ".acc.rain")
          and data(page, "mood") == "neutral")
    page.evaluate("window.portallAvatar.weather('hot')")

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

    page, _ = launch(23, {"condition": "cloudy", "temperature": 15,
                          "unit": "°C", "text": "15°C"})
    check("at night it dozes, with its z's", data(page, "mood") == "sleepy"
          and shown(page, ".acc.zzz") and data(page, "wx") == "")
    page.evaluate("window.portallAvatar.stand('surprised')")
    page.evaluate("window.portallAvatar.stand('neutral')")
    page.clock.fast_forward(2100)
    page.wait_for_timeout(50)
    check("and goes back to dozing, since it is still night",
          data(page, "mood") == "sleepy")
    page.close()

    page, _ = launch(20, locale="fr-FR")
    check("a French panel woken in the evening says Bonsoir",
          ha_send.greet_avatar(page) and page.evaluate(
              "document.querySelector('#av .acc.hello text').textContent")
          == "Bonsoir")
    page.close()
    page, _ = launch(9, locale="fr-FR")
    check("and Bonjour in the morning", ha_send.greet_avatar(page)
          and page.evaluate("document.querySelector('#av .acc.hello text')"
                            ".textContent") == "Bonjour")
    page.goto("about:blank")
    check("a page with no face greets nobody, and nothing breaks",
          ha_send.greet_avatar(page) is False)
    page.close()


def face(browser):
    """The face: its expressions, and what sets each one off."""
    print("The face:")
    links = [{"name": "Rouge", "url": "http://127.0.0.1:9/a/", "icon": "tv"}]
    address = launcher.start(links, avatar=True, port=launcher.ANY_PORT)
    page = browser.new_page(viewport={"width": 1280, "height": 800},
                            locale="fr-FR")
    page.clock.install(time=datetime.datetime(2026, 9, 27, 12, 0))
    page.goto(address)
    page.wait_for_selector("#av")

    def px():
        return page.evaluate("document.getElementById('av').dataset.px")

    def shown(selector):
        return page.evaluate(f"""() => {{
            const e = document.querySelector('#av {selector}');
            return !!e && getComputedStyle(e).display !== 'none'; }}""")

    def eye(side):
        """The eye's box, and the height of its top edge at the outer and
        the inner corner -- read off the path the browser is drawing."""
        return page.evaluate(f"""() => {{
            const e = document.querySelector('#av .pb.{side} .pr');
            const b = e.getBBox();
            const n = getComputedStyle(e).d.match(/-?[0-9.]+/g).map(Number);
            // M is the top edge's left end and L its right end.
            const left = n[1], right = n[3];
            return {{w: b.width, h: b.height, y: b.y,
                     outer: '{side}' === 'l' ? left : right,
                     inner: '{side}' === 'l' ? right : left,
                     d: getComputedStyle(e).d,
                     fill: getComputedStyle(e).fill}}; }}""")

    def mood(m, voice="neutral"):
        page.evaluate(f"""() => {{ const b = document.getElementById('av');
            b.dataset.voice = '{voice}'; b.dataset.mood = '{m}'; }}""")
        page.clock.fast_forward(200)
        page.wait_for_timeout(250)

    rest = eye("l")
    check("at rest: two glowing boxes with a flat top",
          px() == "neutral" and abs(rest["outer"] - rest["inner"]) < 0.5
          and "pxeye" in rest["fill"], str(rest))
    check("its head wears headphones", page.evaluate(
        "document.querySelectorAll('#av .pear').length") == 2)
    mood("happy")
    check("content: its eyes become two arcs",
          px() == "happy" and eye("l")["h"] < rest["h"] * 0.8)
    mood("surprised")
    check("surprised: bigger eyes", eye("l")["h"] > rest["h"] + 4)
    mood("angry")
    left, right = eye("l"), eye("r")
    check("cross: each eye comes down at the nose, and turns red",
          left["inner"] > left["outer"] + 3 and right["inner"] > right["outer"] + 3
          and "pxred" in left["fill"], f"{left['inner']} {left['outer']}")
    mood("sad")
    left = eye("l")
    check("sad: down at the outer corners, and a tear",
          left["outer"] > left["inner"] + 3 and shown(".px-tear"))
    mood("tired")
    check("tired: half shut", eye("l")["h"] < rest["h"] * 0.6)
    for name, extra in (("laugh", ".px-laugh"), ("love", ".px-love"),
                        ("dizzy", ".px-dizzy")):
        mood(name)
        check(f"{name}: drawn instead of its eyes",
              px() == name and shown(extra) and not shown(".look"))
    mood("wink")
    check("wink: one eye open, the other an arc",
          shown(".look") and eye("r")["h"] < eye("l")["h"] * 0.8)
    mood("neutral")

    # The voice assistant, through the attribute the page already follows.
    page.evaluate("window.portallAvatar.stand('surprised')")
    page.wait_for_timeout(50)
    ear = lambda: page.evaluate(  # noqa: E731
        "getComputedStyle(document.querySelector('#av .pear')).fill")
    check("the voice assistant listens: its headphones light up, no mouth",
          px() == "listen" and ear() == "rgb(53, 227, 255)"
          and not shown(".px-talk"))
    page.evaluate("window.portallAvatar.stand('thinking')")
    page.wait_for_timeout(50)
    check("it thinks: eyes up and to the side, and dots",
          px() == "think" and shown(".acc.dots"))
    page.evaluate("window.portallAvatar.stand('happy')")
    page.wait_for_timeout(50)
    heights = lambda: page.evaluate(  # noqa: E731
        "document.querySelector('#av .px-talk').getAttribute('ry')")
    check("its headphones go dark when it stops listening",
          ear() != "rgb(53, 227, 255)")
    first = heights()
    page.clock.fast_forward(260)
    page.wait_for_timeout(50)
    check("it answers: a mouth that opens and closes while it speaks",
          px() == "speak" and shown(".px-talk") and heights() != first)
    page.evaluate("window.portallAvatar.stand('neutral')")
    page.wait_for_timeout(50)
    still = heights()
    page.clock.fast_forward(600)
    page.wait_for_timeout(50)
    check("and it is gone and still when it has finished",
          heights() == still and not shown(".px-talk") and px() == "neutral")
    page.evaluate("window.portallAvatar.stand('surprised')")
    page.evaluate("window.portallAvatar.stand('neutral')")
    page.wait_for_timeout(50)
    check("listened and heard nothing it could use: suspicious",
          px() == "suspicious")
    page.clock.fast_forward(2100)
    page.wait_for_timeout(50)
    check("for two seconds", px() == "neutral")

    # A finger on it.
    r = page.evaluate("""() => { const r = document.querySelector('#av .pb.l')
        .getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""")
    seen = set()
    for _ in range(12):
        page.mouse.click(*r)
        seen.add(page.evaluate("document.getElementById('av').dataset.mood"))
        page.clock.fast_forward(3100)
    check("one tap at a time: a smile, a wink or a heart, and not always the same",
          seen <= {"happy", "wink", "love"} and len(seen) >= 2, str(seen))
    for _ in range(3):
        page.mouse.click(*r)
    check("three taps in a row: it laughs",
          page.evaluate("document.getElementById('av').dataset.mood") == "laugh")
    page.clock.fast_forward(3100)
    for _ in range(5):
        page.mouse.click(*r)
    check("five: it gets cross",
          page.evaluate("document.getElementById('av').dataset.mood") == "angry")
    page.clock.fast_forward(3100)

    # Dragged: slowly it is only moved, fast it ends up dizzy.
    page.mouse.move(*r)
    for _ in range(4):
        page.mouse.wheel(10, 0)
        page.clock.fast_forward(300)
    page.clock.fast_forward(800)
    page.wait_for_timeout(50)
    check("dragged slowly it is not dizzy",
          page.evaluate("document.getElementById('av').dataset.mood") != "dizzy")
    page.clock.fast_forward(3000)
    r = page.evaluate("""() => { const r = document.querySelector('#av .pb.l')
        .getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""")
    page.mouse.move(*r)
    for _ in range(6):
        page.mouse.wheel(120, 60)
    page.clock.fast_forward(800)
    page.wait_for_timeout(50)
    check("dragged fast it is dizzy", px() == "dizzy")
    page.clock.fast_forward(2500)

    # Weather, night, and the greeting.
    page.evaluate("window.portallAvatar.weather('hot')")
    page.wait_for_timeout(250)
    check("hot: a drop of sweat and heavy lids",
          shown(".acc.sweat") and px() == "hot")
    page.evaluate("window.portallAvatar.weather('cold')")
    page.wait_for_timeout(250)
    check("cold: its eyes turn pale, and a snowflake",
          "pxcold" in eye("l")["fill"] and shown(".acc.flake"))
    page.evaluate("window.portallAvatar.weather('')")
    check("woken, it says Bonjour", __import__("ha_send").greet_avatar(page)
          and shown(".acc.hello") and page.evaluate(
              "document.querySelector('#av .acc.hello text').textContent")
          == "Bonjour")
    page.close()

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.clock.install(time=datetime.datetime(2026, 9, 27, 23, 0))
    page.goto(address)
    page.wait_for_selector("#av")
    check("at night: tired eyes and its z's",
          page.evaluate("document.getElementById('av').dataset.px") == "tired"
          and page.evaluate("getComputedStyle(document.querySelector('#av .acc.zzz')).display")
          != "none")
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
        check("a tap makes it smile, wink or love, and opens nothing",
              box(page)[4] in ("happy", "wink", "love")
              and page.url == address, str(box(page)[4]))

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
        face(browser)
        browser.close()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
