#!/usr/bin/env python3
"""Back, reload and home on the glass, inside a link -- seen from the panel.

WHY THIS EXISTS. Asked three times, the last with a Google link on screen:
a browser has a way back, a reload and a home, and a panel had none. The bar
is drawn by the sender (NavBar) and pressed by arithmetic in the sender, so
the only check that can fail the way a panel would is one that reads the
PIXELS that come down the socket and sends real contacts up it.

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle into a picture. Pages, each its own colour: the panel's own (home,
a launcher here), A, which goes to B when its left quarter is tapped -- a
page moving on inside a link -- B, and C, which moves within itself by
history.pushState when tapped, the way most sites move today.

  - the bar is not drawn on the panel's own page;
  - it is drawn when a link opens, in a strip of its own above the page,
    and stays -- the page is never under it;
  - back, reload and home each do their job, and the page under the bar is
    never clicked by any of them;
  - and they stay INSIDE the link, as a browser's do: back from the link's
    first page stays on it, home is the link's first page, and neither ever
    lands on the launcher (reported: both did). A page that moved within
    itself goes back within itself, and a link asked for from inside another
    one starts where it was opened.

--without runs the same sender with --no-nav-bar, which is the panel as it
was: every case about the bar must then fail. --picture FILE saves what the
panel shows while the bar is up.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
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

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
WITHOUT = "--without" in sys.argv
PICTURE = (sys.argv[sys.argv.index("--picture") + 1]
           if "--picture" in sys.argv else None)
W, H = 800, 480

HOME = (0x80, 0x20, 0x20)
A = (0x20, 0x60, 0xA0)
B = (0x30, 0x90, 0x40)
C = (0xB0, 0xA0, 0x20)
D = (0x70, 0x30, 0x90)
counts = {"a": 0, "b": 0, "c": 0, "clicked": 0}
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=14):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.strip("/").split("?")[0]
        if name == "clicked":
            counts["clicked"] += 1
            body = b"ok"
        else:
            colour = {"home": HOME, "a": A, "b": B, "c": C}.get(name, HOME)
            if name in counts:
                counts[name] += 1
            pushed = D if name == "c" else colour
            body = ("<!doctype html><body style='margin:0;height:100vh'>"
                    "<script>"
                    f"const base='rgb{colour}', pushed='rgb{pushed}';"
                    f"const push={'true' if name == 'c' else 'false'};"
                    "const next=new URLSearchParams(location.search)"
                    ".get('next');"
                    "function paint(){document.body.style.background="
                    "location.hash=='#2'?pushed:base;}"
                    "addEventListener('popstate',paint);paint();"
                    "addEventListener('click',e=>{fetch('/clicked',{keepalive:true});"
                    "if(push){history.pushState({},'',location.pathname"
                    "+location.search+'#2');paint();}"
                    "else if(next&&e.clientX<innerWidth/4)"
                    "location.href=next;});"
                    "</script>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


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

    # The same geometry the sender computes, from the same class.
    bar = ha_send.NavBar(W, H)
    strip = bar.height
    spots = {b["name"]: (int(b["x"] + b["w"] / 2), strip // 2)
             for b in bar.buttons}
    ground = (W - 3, 3)
    STRIP = (24, 27, 34)
    middle = (W // 2, H * 2 // 3)
    left = (W // 8, H * 2 // 3)

    def colour(at):
        with lock:
            return canvas.getpixel(at)

    def wait(want, at=middle, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if near(colour(at), want):
                return True
            time.sleep(0.05)
        return False

    def bar_drawn(page_colour=None, seconds=3):
        """The strip's own ground is drawn across the top of the panel."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if near(colour(ground), STRIP):
                return True
            time.sleep(0.05)
        return False

    def tap(at):
        x, y = at
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    command = [sys.executable, "-u", str(SENDER_DIR / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--no-token", "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control"]
    if BROWSER:
        command += ["--browser", BROWSER]
    if WITHOUT:
        command.append("--no-nav-bar")
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
        print("The panel's own page:")
        check("it arrives", wait(HOME, seconds=40))
        time.sleep(1.0)
        check("no bar is drawn on it",
              # Not where the back button would be: that is under the corner
              # mark, drawn faint on the page for a few seconds on arrival.
              all(near(colour(p), HOME)
                  for p in (ground, spots["reload"], spots["home"])),
              str({k: colour(p) for k, p in spots.items()}))

        print("A link opens:")
        ask(f"open {site}/a")
        check("page A arrives", wait(A))
        check("the bar is drawn above it", bar_drawn(A),
              str(colour(ground)))
        if PICTURE:
            time.sleep(0.3)
            with lock:
                canvas.save(PICTURE)
        check("and the page starts below it, not under it",
              near(colour((W // 2, strip + 3)), A),
              str(colour((W // 2, strip + 3))))
        time.sleep(6)
        check("it is still there a while later", bar_drawn(A))
        tap(middle)
        # The click comes when the finger is let go, a moment after the bar
        # is drawn; a navigation asked for before it would swallow it.
        time.sleep(0.5)

        taps = 1  # the touch in the middle of A, just above

        def press(name, page_colour):
            bar_drawn(page_colour)
            tap(spots[name])

        def stays(page_colour, name, before):
            """Still that page after a while, and not asked for again."""
            time.sleep(1.5)
            return near(colour(middle), page_colour) and \
                counts[name] == before

        print("Inside a link:")
        ask(f"open {site}/a?next=/b")
        wait(A)
        time.sleep(0.5)
        before = counts["a"]
        press("back", A)
        check("back on the link's first page stays on it, not the launcher",
              stays(A, "a", before), str(colour(middle)))
        tap(left)
        taps += 1
        check("a link on the page moves on to B", wait(B))
        before = counts["b"]
        press("reload", B)
        end = time.monotonic() + 8
        while time.monotonic() < end and counts["b"] == before:
            time.sleep(0.05)
        check("reload asks for B again", counts["b"] > before,
              f"{before} -> {counts['b']}")
        check("and B is still what the panel shows", wait(B))
        press("back", B)
        check("back returns to A", wait(A))
        before = counts["a"]
        press("back", A)
        check("and back again stays on A, the link's first page",
              stays(A, "a", before), str(colour(middle)))
        tap(left)
        taps += 1
        wait(B)
        before = counts["a"]
        press("home", B)
        check("home goes to the link's first page, not the launcher",
              wait(A) and not near(colour(middle), HOME),
              str(colour(middle)))
        check("and loads it afresh, as a browser's home does",
              counts["a"] > before, f"{before} -> {counts['a']}")

        print("A page that moves within itself:")
        ask(f"open {site}/c")
        check("C arrives, asked for from inside A", wait(C))
        tap(middle)
        taps += 1
        check("C moves within itself", wait(D))
        press("back", D)
        check("back goes back within C, not to the launcher", wait(C)
              and not near(colour(middle), HOME), str(colour(middle)))
        before = counts["c"]
        press("back", C)
        check("back on C stays on it: C started there, not at A",
              stays(C, "c", before), str(colour(middle)))
        before = counts["c"]
        press("home", C)
        end = time.monotonic() + 8
        while time.monotonic() < end and counts["c"] == before:
            time.sleep(0.05)
        check("home on the link's first page loads it again",
              counts["c"] > before and wait(C), f"{before} -> {counts['c']}")
        # Held, the top-left of the glass still goes home: it is where
        # somebody holds to, and inside a link it is now the back button.
        bar_drawn(C)
        x, y = spots["back"]
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(1.2)
        panel[0].sendall(b"T\x00")
        check("holding the back button goes to the panel's own page",
              wait(HOME), str(colour(middle)))
        check("and the bar goes with it", not bar_drawn(seconds=1.5),
              str(colour(ground)))
        time.sleep(0.5)
        check("no button press ever reached the page under the bar",
              counts["clicked"] == taps,
              f"{counts['clicked']} clicks for {taps} taps on the page")
        crashed = [l.strip() for l in out if "Traceback" in l]
        check("the sender did not crash", not crashed, str(crashed))
        said = [l.strip() for l in out if l.startswith("Bar:")
                or "home button" in l]
        print("    " + "\n    ".join(said[:6]))
    finally:
        process.kill()
        listener.close()
        server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
