#!/usr/bin/env python3
"""Back, reload and home on the glass, inside a link -- seen from the panel.

WHY THIS EXISTS. Asked three times, the last with a Google link on screen:
a browser has a way back, a reload and a home, and a panel had none. The bar
is drawn by the sender (NavBar) and pressed by arithmetic in the sender, so
the only check that can fail the way a panel would is one that reads the
PIXELS that come down the socket and sends real contacts up it.

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle into a picture. Three pages: the panel's own (home), A and B, each
its own colour, and A and B count every click they receive.

  - the bar is not drawn on the panel's own page;
  - it is drawn when a link opens, and gone five seconds later;
  - a touch anywhere brings it back;
  - back, reload and home each do their job, and the page under the bar is
    never clicked by any of them;
  - back from the first page a link opened goes to the page before it.

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
counts = {"a": 0, "b": 0, "clicked": 0}
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
            colour = {"home": HOME, "a": A, "b": B}.get(name, HOME)
            if name in counts:
                counts[name] += 1
            body = (b"<!doctype html><body style='margin:0;height:100vh;"
                    b"background:rgb(%d,%d,%d)'><script>"
                    b"addEventListener('click',()=>fetch('/clicked'));"
                    b"</script>" % colour)
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
    bar = ha_send.NavBar(None, W, H, ha_send.HOME_CORNER_FRACTION * W)
    spots = {b["name"]: (int(b["x"] + b["w"] / 2), int(b["y"] + b["h"] / 2))
             for b in bar.buttons}
    middle = (W // 2, H * 2 // 3)

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

    def bar_drawn(page_colour, seconds=3):
        """The bar's middle button is not the page's own colour."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not near(colour(spots["reload"]), page_colour, 30):
                return True
            time.sleep(0.05)
        return False

    def bar_gone(page_colour, seconds=8):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if all(near(colour(p), page_colour) for p in spots.values()):
                return True
            time.sleep(0.1)
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
              all(near(colour(p), HOME) for p in spots.values()),
              str({k: colour(p) for k, p in spots.items()}))

        print("A link opens:")
        ask(f"open {site}/a")
        check("page A arrives", wait(A))
        check("the bar is drawn over it", bar_drawn(A),
              str(colour(spots["reload"])))
        if PICTURE:
            time.sleep(0.3)
            with lock:
                canvas.save(PICTURE)
        check("and is gone a few seconds later", bar_gone(A))
        tap(middle)
        check("a touch anywhere brings it back", bar_drawn(A))

        print("Its buttons:")
        ask(f"open {site}/b")
        check("page B arrives", wait(B))
        bar_drawn(B)
        clicked = counts["clicked"]
        tap(spots["back"])
        check("back returns to A", wait(A))
        before = counts["a"]
        bar_drawn(A)
        tap(spots["reload"])
        end = time.monotonic() + 8
        while time.monotonic() < end and counts["a"] == before:
            time.sleep(0.05)
        check("reload asks for A again", counts["a"] > before,
              f"{before} -> {counts['a']}")
        check("and A is still what the panel shows", wait(A))
        bar_drawn(A)
        tap(spots["back"])
        check("back from the first page a link opened goes home", wait(HOME))
        ask(f"open {site}/b")
        wait(B)
        bar_drawn(B)
        tap(spots["home"])
        check("home goes to the panel's own page", wait(HOME))
        time.sleep(0.5)
        check("no button press ever reached the page under the bar",
              counts["clicked"] == clicked,
              f"{counts['clicked'] - clicked} clicks")
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
