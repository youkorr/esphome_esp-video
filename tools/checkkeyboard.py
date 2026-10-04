#!/usr/bin/env python3
"""The on-screen keyboard beside the back/reload/home bar -- seen from the panel.

WHY THIS EXISTS. Reported after the bar arrived: the keyboard "ne s'affiche
pas, ou parfois il s'affiche quand je suis sur une autre page web", and it and
the bar "ont du mal a cohabiter". Three faults answer that, each reproduced
here against the shipped sender before it was believed:

  - a page that moves WITHIN itself (history.pushState, and the bar's back
    over it) keeps its document, so the keys stay drawn there -- but the
    sender had forgotten them, so nothing ever took them down: a keyboard
    drawn on a page that has nothing to type into, and dead;
  - a page that navigates BY ITSELF while the keys are up (a search that
    loads its results, a redirect) is a new document with no keys drawn, but
    the sender still thought them up, and ate every tap along the bottom of
    the screen as a keystroke: a keyboard nobody can see;
  - a page that focuses a field by itself on arrival brought the keys up
    with nobody having touched anything. A telephone does not: its keyboard
    comes up for a field somebody tapped, or that a tap led to.

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle into a picture and sends real contacts up the return channel. What
is checked is the pixels and what the pages receive, never the sender's word.

--ref REV runs the same cases against ha_send.py as it was at REV, and --low
moves the field below where 4.34's bar was drawn, so the other two faults
show against it on their own.
Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
REF = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else None
W, H = 800, 480
# --low puts the field below where 4.34's bar was, so that against --ref the
# faults the bar was hiding can be seen on their own.
TOP = 200 if "--low" in sys.argv else 40

HOME = (0x80, 0x20, 0x20)
F = (0x20, 0x60, 0xA0)
T = (0x30, 0x90, 0x40)
AF = (0xB0, 0xA0, 0x20)
KEY = (0x2A, 0x2C, 0x34)
typed = {"v": ""}
clicks = []
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=14):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


FIELD = ("<input id=q style='position:absolute;left:200px;top:%dpx;"
         "width:400px;height:50px;font-size:24px' %%s>" % TOP)
BOTTOM = ("<button id=bottom style='position:absolute;left:0;top:300px;"
          "width:100%%;height:180px;opacity:.01' "
          "onclick=\"fetch('/clicked?where=%s',{keepalive:true})\"></button>")
REPORT = ("<script>addEventListener('input',e=>fetch('/typed?v='+"
          "encodeURIComponent(e.target.value)));</script>")
PAGES = {
    # A field, a button that moves the page within itself, and a button
    # under where the keys are drawn. Moving back blurs the field, the way a
    # single-page site re-rendering does.
    "f": (F, FIELD % "" + BOTTOM % "f"
          + "<button id=step style='position:absolute;left:10px;top:150px;"
            "width:90px;height:60px'>x</button>" + REPORT
          + "<script>step.onclick=()=>{history.pushState({},'','#2');"
            "document.activeElement.blur();};"
            "addEventListener('popstate',()=>document.activeElement.blur());"
            "</script>"),
    # A field that, once focused, makes the page go somewhere else by itself.
    "go": (F, FIELD % "" + REPORT
           + "<script>q.onfocus=()=>setTimeout(()=>location.href='/t',1200);"
             "</script>"),
    "t": (T, BOTTOM % "t"),
    # A field that takes focus by itself, nobody having touched anything.
    "af": (AF, FIELD % "autofocus" + REPORT),
    # A button whose field arrives a moment later, focused -- the shape of
    # Home Assistant's search dialog animating in.
    "late": (F, "<button id=late style='position:absolute;left:300px;"
                "top:150px;width:200px;height:60px'>x</button>" + REPORT
             + "<script>document.getElementById('late').onclick=()=>setTimeout(()=>{"
               "const i=document.createElement('input');"
               "i.style.cssText='position:absolute;left:200px;top:40px;"
               "width:400px;height:50px';document.body.appendChild(i);"
               "i.focus();},400);</script>"),
}


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        name = url.path.strip("/")
        query = urllib.parse.parse_qs(url.query)
        if name == "typed":
            typed["v"] = query.get("v", [""])[0]
            body = b"ok"
        elif name == "clicked":
            clicks.append(query.get("where", ["?"])[0])
            body = b"ok"
        else:
            colour, inner = PAGES.get(name, (HOME, ""))
            body = (f"<!doctype html><body style='margin:0;height:100vh;"
                    f"background:rgb{colour}'>{inner}").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def sender_file():
    if not REF:
        return SENDER_DIR / "ha_send.py"
    old = pathlib.Path(tempfile.mkdtemp()) / "ha_send.py"
    old.write_bytes(subprocess.check_output(
        ["git", "-C", str(HERE), "show",
         f"{REF}:components/portall/ha_send.py"]))
    for name in ("udisp_send.py",):
        (old.parent / name).write_bytes((SENDER_DIR / name).read_bytes())
    return old


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import ha_send

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"

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

    # The same geometry the sender computes, from the same classes. The keys
    # sit at the bottom of the panel whether or not a strip shortens the
    # page above them, so their place on the glass is the same either way.
    board = ha_send.Keyboard(None, W, H, "qwerty")
    key = {k["label"]: (int(k["x"] + k["w"] / 2), int(k["y"] + k["h"] / 2))
           for k in board.keys}
    # A point on a key's own ground: its centre is where its letter is.
    ground = {k["label"]: (int(k["x"] + 10), int(k["y"] + 8))
              for k in board.keys}
    if REF:
        # 4.34's bar, over the top of the page and the page not moved.
        strip = 0
        spots = {"back": (345, 38), "reload": (400, 38), "home": (455, 38)}
    else:
        bar = ha_send.NavBar(W, H)
        strip = bar.height
        spots = {b["name"]: (int(b["x"] + b["w"] / 2), strip // 2)
                 for b in bar.buttons}
    # The field is at the top of the page, under where 4.34's bar was --
    # the shape of Google's search box.
    field = (400, strip + TOP + 25)

    def colour(at):
        with lock:
            return canvas.getpixel(at)

    def until(test, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.05)
        return False

    def keys_up(seconds=4):
        return until(lambda: near(colour(ground["a"]), KEY), seconds)

    def keys_down(seconds=4):
        return until(lambda: not near(colour(ground["a"]), KEY), seconds)

    def page_is(want, seconds=10):
        return until(lambda: near(colour((720, 250)), want), seconds)

    def tap(at):
        x, y = at
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    def bar_up():
        if REF:
            return until(lambda: not near(colour(spots["reload"]),
                                          colour((720, 250)), 30), 3)
        return until(lambda: near(colour((W - 3, 3)), (24, 27, 34)), 3)

    command = [sys.executable, "-u", str(sender_file()),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--no-token", "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "qwerty",
               "--control"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def ask(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    try:
        print("Typing, with the bar beside it:")
        check("the panel's own page arrives", page_is(HOME, 40))
        ask(f"open {site}/f")
        check("a page with a field arrives", page_is(F))
        time.sleep(0.5)
        tap(field)
        check("a tap on the field brings the keys up", keys_up())
        tap(key["a"])
        check("a key types into the field",
              until(lambda: typed["v"] == "a", 3), repr(typed["v"]))
        if "--picture" in sys.argv:
            with lock:
                canvas.save(sys.argv[sys.argv.index("--picture") + 1])
        check("the bar is drawn with the keys up", bar_up()
              and near(colour(ground["a"]), KEY))
        time.sleep(6)
        tap(key["b"])
        check("a while later, the field still types",
              until(lambda: typed["v"] == "ab", 3), repr(typed["v"]))
        tap((50, strip + 180))
        check("moving within the page away from the field puts the keys "
              "away", keys_down())

        print("Back over a page that moved within itself:")
        tap(field)
        keys_up()
        bar_up()
        tap(spots["back"])
        check("the keys are not left drawn on it", keys_down(),
              str(colour(ground["a"])))
        clicks.clear()
        tap(key["a"])
        check("and the bottom of the screen is the page's again",
              until(lambda: "f" in clicks, 3), str(clicks))

        print("A page that goes somewhere else by itself:")
        ask(f"open {site}/go")
        page_is(F)
        time.sleep(0.5)
        tap(field)
        keys_up()
        check("it arrives on its own", page_is(T))
        time.sleep(0.8)
        check("with no keys drawn", not near(colour(ground["a"]), KEY),
              str(colour(ground["a"])))
        clicks.clear()
        tap(key["a"])
        check("and a tap where the keys were reaches the page, not a "
              "keyboard nobody can see", until(lambda: "t" in clicks, 3),
              str(clicks))

        print("A field nobody touched:")
        ask(f"open {site}/af")
        check("a page that focuses its own field arrives", page_is(AF))
        check("the keys do not come up by themselves",
              not keys_up(2.5), str(colour(ground["a"])))
        tap(field)
        check("a tap on it brings them up", keys_up())

        print("A field a tap leads to:")
        ask(f"open {site}/late")
        page_is(F)
        time.sleep(0.5)
        tap((400, strip + 180))
        check("a field that arrives after a tap brings the keys up",
              keys_up())

        if "-v" in sys.argv:
            print("\n".join("    " + l.rstrip() for l in out[-60:]))
        crashed = [l.strip() for l in out if "Traceback" in l]
        check("the sender did not crash", not crashed, str(crashed))
    finally:
        process.kill()
        listener.close()
        server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
