#!/usr/bin/env python3
"""A link that opens a new window is shown on the panel -- seen on the glass.

WHY THIS EXISTS. Reported from Google News: an article tapped, its link
focused (`Keyboard: ... focus is A`), and nothing else. Its links open in a
new tab, and a panel shows one page: the tab loaded behind it, unseen.

The shipped ha_send.py against a fake panel that reassembles the rectangles,
on a list page of rows: a `target="_blank"` link, a `window.open()`, a
window opened blank and sent somewhere 300 ms later, and a download opened in
a new window:

  - a tap on the target=_blank link shows the article on the panel;
  - the bar's back returns to the list;
  - a tap on the window.open() row shows its page too, and so does a
    window opened blank and sent somewhere afterwards;
  - a download opened in a new window is kept once, and the list stays.

--sender PATH runs another copy of ha_send.py (an older release), which is
how the report is reproduced: the panel stays on the list.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
W, H = 800, 480

HOME = (0x80, 0x20, 0x20)
LIST = (0x20, 0x60, 0xA0)
ARTICLE = (0x30, 0x90, 0x40)
OPENED = (0xA0, 0x80, 0x20)
LATER = (0x90, 0x30, 0x90)
fails = 0
asked = []


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=14):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


def plain(colour):
    return (f"<!doctype html><body style='margin:0;height:100vh;"
            f"background:rgb{colour}'></body>")


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.strip("/").split("?")[0]
        asked.append(name)
        kind = "text/html"
        if name == "list":
            # The way Google News lays out an article: an <a> that opens a
            # new tab. Below it, a script that opens one itself.
            body = (f"<!doctype html><body style='margin:0;"
                    f"background:rgb{LIST}'>"
                    "<a href='/article' target='_blank' rel='noopener' "
                    "style='display:block;height:30vh'></a>"
                    "<div onclick=\"window.open('/opened')\" "
                    "style='height:30vh'></div>"
                    "<div onclick=\"const w = window.open(''); "
                    "setTimeout(() => w.location = '/later', 300)\" "
                    "style='height:20vh'></div>"
                    "<a href='/file.bin' target='_blank' "
                    "style='display:block;height:20vh'></a></body>")
        elif name == "file.bin":
            body, kind = "0123456789" * 100, "application/octet-stream"
        else:
            body = plain({"home": HOME, "article": ARTICLE,
                          "opened": OPENED, "later": LATER}.get(name, HOME))
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        if name == "file.bin":
            self.send_header("Content-Disposition",
                             "attachment; filename=file.bin")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    try:
        from PIL import Image
        from playwright.sync_api import sync_playwright  # noqa: F401
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

    strip = ha_send.NavBar(W, H).height
    back = next((int(b["x"] + b["w"] / 2), strip // 2)
                for b in ha_send.NavBar(W, H).buttons if b["name"] == "back")
    view = H - strip
    middle = (W // 2, strip + view // 2)

    def colour(at=middle):
        with lock:
            return canvas.getpixel(at)

    def wait(want, at=middle, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if near(colour(at), want):
                return True
            time.sleep(0.05)
        return False

    def tap(at):
        x, y = at
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    work = tempfile.mkdtemp()
    command = [sys.executable, "-u", SENDER,
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control", "--profile", os.path.join(work, "p"),
               "--downloads", os.path.join(work, "d")]
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
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
        check("the panel's own page arrives", wait(HOME, seconds=40))
        ask(f"open {site}/list")
        check("the list opens", wait(LIST))
        time.sleep(0.6)
        tap((W // 2, strip + int(view * 0.15)))
        check("a target=_blank link shows its page on the panel",
              wait(ARTICLE), str(colour()))
        time.sleep(0.6)
        tap(back)
        check("the bar's back returns to the list", wait(LIST),
              str(colour()))
        time.sleep(0.6)
        tap((W // 2, strip + int(view * 0.45)))
        check("a window.open() shows its page on the panel",
              wait(OPENED), str(colour()))
        time.sleep(0.6)
        tap(back)
        wait(LIST)
        time.sleep(0.6)
        tap((W // 2, strip + int(view * 0.7)))
        check("a window opened blank and sent somewhere after shows it",
              wait(LATER), str(colour()))
        time.sleep(0.6)
        tap(back)
        wait(LIST)
        time.sleep(0.6)
        tap((W // 2, strip + int(view * 0.9)))
        end = time.monotonic() + 8
        kept = []
        while time.monotonic() < end and not kept:
            kept = [n for n in os.listdir(os.path.join(work, "d"))
                    if not n.endswith(".part")] \
                if os.path.isdir(os.path.join(work, "d")) else []
            time.sleep(0.1)
        time.sleep(1.5)
        kept = [n for n in os.listdir(os.path.join(work, "d"))
                if not n.endswith(".part")] \
            if os.path.isdir(os.path.join(work, "d")) else []
        check("a download opened in a new window is kept once",
              kept == ["file.bin"], str(kept))
        check("and the list stays on the panel", wait(LIST, seconds=2),
              str(colour()))
        check("and the sender is still running", process.poll() is None,
              "".join(out[-5:]))
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(work, ignore_errors=True)
        if fails:
            print("".join(line for line in out
                          if re.search(r"window|Window|Download|Tile|Bar",
                                       line)))
    print("\nAll good." if not fails else f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
