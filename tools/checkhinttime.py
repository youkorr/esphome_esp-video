#!/usr/bin/env python3
"""How long the corner mark stays on a page a link opened, seen on the panel.

WHY THIS EXISTS. Reported with a photograph of it still over a page's
heading: "cette angle de retour reste trop longtemps ... il faut qu'elle
s'efface rapidement". checkhint.py proves the mark is drawn and taken away;
only the pixels off the socket say for how long. The shipped sender runs
against a fake panel, a link opens a plain page, and the corner is watched
until it is the page's own colour again.

--ref REV measures the sender as it was at REV. Needs Playwright and Pillow.
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

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
REF = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else None
LIMIT_S = 2.5
W, H = 800, 480
PAGE = (0x20, 0x60, 0xA0)


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        colour = PAGE if self.path.startswith("/a") else (0x80, 0x20, 0x20)
        body = (f"<!doctype html><body style='margin:0;height:100vh;"
                f"background:rgb{colour}'>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def sender():
    if not REF:
        return SENDER_DIR / "ha_send.py"
    old = pathlib.Path(tempfile.mkdtemp()) / "ha_send.py"
    old.write_bytes(subprocess.check_output(
        ["git", "-C", str(HERE), "show",
         f"{REF}:components/portall/ha_send.py"]))
    (old.parent / "udisp_send.py").write_bytes(
        (SENDER_DIR / "udisp_send.py").read_bytes())
    return old


def main():
    from PIL import Image
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import ha_send
    spot = (8, ha_send.NavBar(W, H).height + 8)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
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
    threading.Thread(target=accept, daemon=True).start()
    command = [sys.executable, "-u", str(sender()), "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url",
               f"{site}/home", "--no-token", "--not-home-assistant",
               "--width", str(W), "--height", str(H), "--audio", "off",
               "--keyboard", "off", "--control"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, text=True)
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and not up:
            time.sleep(0.1)
        time.sleep(7)
        times = []
        for i in range(3):
            process.stdin.write(f"open {site}/a?{i}\n")
            process.stdin.flush()
            asked = time.monotonic()
            seen = gone = None
            while time.monotonic() - asked < 10:
                # Just below the bar's strip, inside the quarter disc.
                with lock:
                    c = canvas.getpixel(spot)
                marked = not all(abs(x - y) < 14 for x, y in zip(c, PAGE))
                if seen is None and marked:
                    seen = time.monotonic()
                if seen is not None and not marked:
                    gone = time.monotonic()
                    break
                time.sleep(0.02)
            times.append(None if seen is None or gone is None
                         else gone - seen)
            time.sleep(1)
    finally:
        process.kill()
        server.shutdown()
    print(f"the corner mark stayed on the page for: "
          + ", ".join("never seen" if t is None else f"{t:.1f}s"
                      for t in times))
    ok = all(t is not None and t <= LIMIT_S for t in times)
    print("ok" if ok else f"ECHEC: more than {LIMIT_S}s, or never drawn")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
