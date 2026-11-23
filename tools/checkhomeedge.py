#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""The way home from a link, chosen per screen: the corner, the right edge, or
both -- seen from the panel.

WHY THIS EXISTS. Asked for as a choice once the corner had been lived with:
"soit le toucher du haut ... ou un slide de droite". `home_gesture` on a
screen's Advanced (--home-gesture to the sender) is corner, right_edge or
both -- and since 4.58.0 left_edge, top_edge and bottom_edge too, asked for as
"il manquerait left_edge et haut et bas", and changeable while the sender
runs through a `gesture` line on its control channel (the launcher's
Reglages).

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle into a picture, and real contacts go up its return channel. A link
opens a tall page that reports its clicks and its scrolling, and the panel's
own page has a colour of its own, so "home" is read off the glass:

  - right_edge: a swipe left from the right edge goes home without scrolling
    the page; a tap there and a drag up it still reach the page; holding the
    corner is an ordinary press; the edge's mark is drawn on arrival and the
    corner's is not;
  - corner (what every screen had): the same swipe stays on the link, and no
    edge mark is drawn;
  - both: either goes home;
  - left_edge: a swipe right from the left edge goes home, a tap there and a
    drag up it reach the page;
  - top_edge: a swipe down that starts on the bar's strip goes home and
    presses no button of it;
  - bottom_edge: a swipe up from the bottom goes home, a tap there and an
    ordinary drag up the middle reach the page;
  - and a sender started on the corner, told `gesture bottom_edge` on its
    control channel, goes home by the bottom from the next touch on.

--sender FILE runs another copy of ha_send.py (the one before this, say):
every case about the edge must then fail. Needs Playwright and Pillow; takes
$CHROMIUM for the browser.
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
HOME = (0x80, 0x20, 0x20)
PAGE = (0x20, 0x60, 0xA0)
fails = 0
events = []

PAGES = {
    "home": f"<body style='margin:0;height:100vh;background:rgb{HOME}'>",
    # Tall, so a drag has something to scroll; and it says what it was given.
    "a": (f"<body style='margin:0;height:400vh;background:rgb{PAGE}'>"
          "<script>"
          "addEventListener('click',e=>fetch('/ev?click='+e.clientX+','"
          "+e.clientY,{keepalive:true}));"
          "let t=null;addEventListener('scroll',()=>{clearTimeout(t);"
          "t=setTimeout(()=>fetch('/ev?scroll='+scrollY,{keepalive:true}),"
          "120);});</script>"),
}


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=18):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        name = path.strip("/")
        if name == "ev":
            events.append(urllib.parse.parse_qsl(query)[0])
        body = ("<!doctype html>" + PAGES.get(name, "")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run(gesture, site, cases):
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
            time.sleep(0.03)
        return False

    def contact(x, y):
        panel[0].sendall(b"T\x01\x00" + int(x).to_bytes(2, "little")
                         + int(y).to_bytes(2, "little"))

    def lift():
        panel[0].sendall(b"T\x00")

    def stroke(start, end, steps=14, hold=0.0):
        contact(*start)
        time.sleep(0.03 + hold)
        for i in range(1, steps + 1):
            contact(start[0] + (end[0] - start[0]) * i / steps,
                    start[1] + (end[1] - start[1]) * i / steps)
            time.sleep(0.02)
        time.sleep(0.05)
        lift()

    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url",
               f"{site}/home", "--no-token", "--not-home-assistant",
               "--width", str(W), "--height", str(H), "--audio", "off",
               "--keyboard", "off", "--control", "--fps", "25",
               "--home-gesture", gesture]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    middle = (W // 2, (strip + H) // 2)
    edge_mark = {"right_edge": (W - 6, (strip + H) // 2),
                 "left_edge": (6, (strip + H) // 2),
                 "top_edge": (W // 2, strip + 6),
                 "bottom_edge": (W // 2, H - 6)}.get(gesture,
                                                     (W - 6, (strip + H) // 2))
    corner_mark = (6, strip + 6)
    tools = {
        "strip": strip, "colour": colour, "until": until, "stroke": stroke,
        "contact": contact, "lift": lift, "middle": middle,
        "edge_mark": edge_mark, "corner_mark": corner_mark,
        "process": process, "out": out,
    }

    def open_link(tag):
        events.clear()
        process.stdin.write(f"open {site}/a?{tag}\n")
        process.stdin.flush()
        arrived = until(lambda: near(colour(middle), PAGE), 10)
        # The marks are drawn on arrival and fade after a second and a half;
        # read them now, then wait them out so a later sample is the page.
        # A mark is drawn for a second and a half, so it is counted only when
        # seen on three samples in a row: one dark frame right after a page
        # arrives -- seen at the right edge, a few times in twenty runs,
        # with either Playwright -- is not a mark.
        marks = {"edge": False, "corner": False, "seen": []}
        runs = {"edge": 0, "corner": 0}
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            for name, at in (("edge", edge_mark), ("corner", corner_mark)):
                seen = colour(at)
                if not near(seen, PAGE, 30):
                    runs[name] += 1
                    marks["seen"].append((name, seen))
                    if runs[name] >= 3:
                        marks[name] = True
                else:
                    runs[name] = 0
            time.sleep(0.03)
        time.sleep(1.2)
        return arrived, marks
    tools["open_link"] = open_link
    try:
        print(f"home_gesture: {gesture}")
        check("the panel's own page arrives",
              until(lambda: bool(panel) and near(colour(middle), HOME), 40))
        if not panel:
            return
        cases(tools)
        crashed = [line.strip() for line in out if "Traceback" in line]
        check("the sender did not crash", not crashed, str(crashed[:1]))
    finally:
        process.kill()
        listener.close()


def right_edge(t):
    arrived, marks = t["open_link"]("1")
    check("a link opens", arrived)
    check("the edge's mark is drawn when it arrives", marks["edge"])
    check("and the corner's is not", not marks["corner"])
    t["stroke"]((W - 8, 300), (W - 8, 300), steps=1)
    time.sleep(0.6)
    check("a tap on the right edge reaches the page",
          any(k == "click" for k, _ in events), str(events))
    events.clear()
    t["stroke"]((W - 8, 420), (W - 8, 160))
    time.sleep(0.8)
    check("a drag up the edge scrolls the page",
          any(k == "scroll" for k, _ in events), str(events))
    check("and stays on it", t["until"](
        lambda: near(t["colour"](t["middle"]), PAGE), 1))
    events.clear()
    t["stroke"]((W - 8, 300), (W - 260, 304))
    check("a swipe left from the right edge goes home",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5),
          str(t["colour"](t["middle"])))
    check("without scrolling or clicking the page it left",
          not events, str(events))

    t["open_link"]("2")
    events.clear()
    t["stroke"]((8, t["strip"] + 8), (8, t["strip"] + 8), steps=1, hold=1.2)
    time.sleep(1.0)
    check("holding the corner is an ordinary press now",
          near(t["colour"](t["middle"]), PAGE)
          and any(k == "click" for k, _ in events),
          f"{t['colour'](t['middle'])} {events}")


def corner(t):
    arrived, marks = t["open_link"]("3")
    check("a link opens", arrived)
    check("no edge mark is drawn", not marks["edge"], str(marks["seen"][:6]))
    t["stroke"]((W - 8, 300), (W - 260, 304))
    check("a swipe left from the right edge stays on the link",
          not t["until"](lambda: near(t["colour"](t["middle"]), HOME), 2.5))


def both(t):
    arrived, marks = t["open_link"]("4")
    check("a link opens", arrived)
    check("both marks are drawn", marks["edge"] and marks["corner"],
          str(marks))
    t["stroke"]((W - 8, 300), (W - 260, 304))
    check("the edge goes home",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5))
    t["open_link"]("5")
    t["stroke"]((8, t["strip"] + 8), (8, t["strip"] + 8), steps=1, hold=1.2)
    check("and so does the corner",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5))


def left_edge(t):
    arrived, marks = t["open_link"]("6")
    check("a link opens", arrived)
    check("the left edge's mark is drawn when it arrives", marks["edge"],
          str(marks["seen"][:6]))
    t["stroke"]((8, 300), (8, 300), steps=1)
    time.sleep(0.6)
    check("a tap on the left edge reaches the page",
          any(k == "click" for k, _ in events), str(events))
    events.clear()
    t["stroke"]((8, 420), (8, 160))
    time.sleep(0.8)
    check("a drag up the left edge scrolls the page",
          any(k == "scroll" for k, _ in events), str(events))
    events.clear()
    t["stroke"]((8, 300), (260, 304))
    check("a swipe right from the left edge goes home",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5),
          str(t["colour"](t["middle"])))
    check("without scrolling or clicking the page it left",
          not events, str(events))


def top_edge(t):
    arrived, marks = t["open_link"]("7")
    check("a link opens", arrived)
    check("the top edge's mark is drawn when it arrives", marks["edge"],
          str(marks["seen"][:6]))
    events.clear()
    # From the middle of the bar's strip down into the page.
    t["stroke"]((W // 2, t["strip"] // 2), (W // 2 + 4, t["strip"] // 2 + 200))
    check("a swipe down from the top, through the bar, goes home",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5),
          str(t["colour"](t["middle"])))
    check("without reaching the page it left", not events, str(events))
    t["open_link"]("8")
    events.clear()
    # Up rather than down: a page that has just opened is at its top, and a
    # drag down there has nothing to scroll -- the first version of this case
    # failed on exactly that, with the code right.
    t["stroke"]((W // 2, 420), (W // 2, 160))
    time.sleep(0.8)
    check("an ordinary drag in the page is still a scroll",
          any(k == "scroll" for k, _ in events)
          and near(t["colour"](t["middle"]), PAGE), str(events))


def bottom_edge(t):
    arrived, marks = t["open_link"]("9")
    check("a link opens", arrived)
    check("the bottom edge's mark is drawn when it arrives", marks["edge"],
          str(marks["seen"][:6]))
    check("and the corner's is not", not marks["corner"])
    t["stroke"]((W // 2, H - 8), (W // 2, H - 8), steps=1)
    time.sleep(0.6)
    check("a tap on the bottom edge reaches the page",
          any(k == "click" for k, _ in events), str(events))
    events.clear()
    t["stroke"]((W // 2, 400), (W // 2, 160))
    time.sleep(0.8)
    check("a drag up the middle of the page is still a scroll",
          any(k == "scroll" for k, _ in events)
          and near(t["colour"](t["middle"]), PAGE), str(events))
    events.clear()
    t["stroke"]((W // 2, H - 8), (W // 2 + 4, H - 260))
    check("a swipe up from the bottom goes home",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5),
          str(t["colour"](t["middle"])))
    check("without scrolling or clicking the page it left",
          not events, str(events))


def changed_live(t):
    t["open_link"]("10")
    t["stroke"]((W // 2, H - 8), (W // 2 + 4, H - 260))
    check("on the corner, a swipe up from the bottom stays on the link",
          not t["until"](lambda: near(t["colour"](t["middle"]), HOME), 2.5))
    t["process"].stdin.write("gesture bottom_edge\n")
    t["process"].stdin.flush()
    said = t["until"](lambda: any("swipe up from the bottom" in line
                                  for line in t["out"]), 5)
    check("told `gesture bottom_edge`, the sender says so", said,
          str([line for line in t["out"] if "Home" in line][-3:]))
    t["open_link"]("11")
    t["stroke"]((W // 2, H - 8), (W // 2 + 4, H - 260))
    check("and goes home by the bottom from then on",
          t["until"](lambda: near(t["colour"](t["middle"]), HOME), 5))
    t["open_link"]("12")
    t["stroke"]((8, t["strip"] + 8), (8, t["strip"] + 8), steps=1, hold=1.2)
    check("with the corner no longer a way home",
          not t["until"](lambda: near(t["colour"](t["middle"]), HOME), 2.0))


def main():
    try:
        import playwright  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        run("right_edge", site, right_edge)
        run("corner", site, corner)
        run("both", site, both)
        run("left_edge", site, left_edge)
        run("top_edge", site, top_edge)
        run("bottom_edge", site, bottom_edge)
        run("corner", site, changed_live)
    finally:
        server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
