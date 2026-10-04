#!/usr/bin/env python3
"""A sender that stops unasked comes back on the page it was showing.

WHY THIS EXISTS. From a panel's log: Google Chrome closed the page the moment
a download began, the sender's next call to it raised, the sender exited,
the add-on started it again -- and the panel was back on its launcher, the
site somebody was on gone. --resume keeps the page shown in a file and the
next run goes back to it, if that run starts soon after: a crash is recovered
from, a stop asked for is not.

The shipped sender runs against a fake panel, three times over one file:

  - killed without warning on a link's page, the next run shows that page;
  - stopped with SIGTERM, as the add-on stops a panel, the next run shows the
    panel's own page;
  - a file left from long ago is not obeyed.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER = HERE / "components" / "portall" / "ha_send.py"
sys.path.insert(0, str(SENDER.parent))
BROWSER = os.environ.get("CHROMIUM", "")
W, H = 800, 480
HOME = (0x80, 0x20, 0x20)
A = (0x20, 0x60, 0xA0)
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(c, want):
    return all(abs(a - b) < 14 for a, b in zip(c, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        colour = A if self.path.startswith("/a") else HOME
        body = (f"<!doctype html><body style='margin:0;height:100vh;"
                f"background:rgb{colour}'>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(site, resume, seconds_to_settle=3.0):
    """One sender and one fake panel; returns (process, colour(), log)."""
    from PIL import Image
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    canvas = Image.new("RGB", (W, H))
    lock = threading.Lock()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    up = []

    def accept():
        conn, _ = listener.accept()
        up.append(conn)
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
    command = [sys.executable, "-u", str(SENDER), "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url",
               f"{site}/home", "--no-token", "--not-home-assistant",
               "--width", str(W), "--height", str(H), "--audio", "off",
               "--keyboard", "off", "--control", "--resume", resume]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    end = time.monotonic() + 40
    while time.monotonic() < end and not up:
        time.sleep(0.1)
    time.sleep(seconds_to_settle)

    def colour():
        with lock:
            return canvas.getpixel((W // 2, H * 2 // 3))
    return process, colour, out


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    resume = os.path.join(tempfile.mkdtemp(), "salon.url")

    print("A run that ends unasked:")
    first, colour, _ = run(site, resume)
    first.stdin.write(f"open {site}/a\n")
    first.stdin.flush()
    time.sleep(2)
    check("the link's page is shown", near(colour(), A), str(colour()))
    check("and kept in the file", os.path.exists(resume)
          and open(resume).read().startswith(f"{site}/a"))
    first.kill()                     # no warning, as when the browser dies
    first.wait()
    second, colour, out = run(site, resume)
    check("the next run goes back to it", near(colour(), A), str(colour()))
    check("and says so", any(l.startswith("Resume:") for l in out),
          str([l.strip() for l in out][-5:]))

    print("A stop asked for:")
    second.send_signal(signal.SIGTERM)
    second.wait(20)
    check("leaves nothing to go back to", not os.path.exists(resume))
    third, colour, _ = run(site, resume)
    check("so the next run is on the panel's own page",
          near(colour(), HOME), str(colour()))
    third.send_signal(signal.SIGTERM)
    third.wait(20)

    print("An old file:")
    with open(resume, "w") as handle:
        handle.write(f"{site}/a")
    old = time.time() - 600
    os.utime(resume, (old, old))
    fourth, colour, _ = run(site, resume)
    check("is not obeyed", near(colour(), HOME), str(colour()))
    fourth.kill()
    server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
