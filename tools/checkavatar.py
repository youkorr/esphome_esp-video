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
import io
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))

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
        check("the size of a button", abs(w - button) < 2 and
              abs(w / h - 1.5) < 0.02, f"{w:.0f}x{h:.0f}, a button {button:.0f}")
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
        browser.close()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
