#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Does the browser paint only the pictures a panel will be sent, and does a
page that stops moving still end on its last picture?

WHY THIS EXISTS. Chromium's screencast keeps three frames in flight and waits
for acknowledgements before capturing more. The sender gave every slot back
on each ask, so the browser painted and encoded up to three pictures for each
one that could go out: against a panel taking 800 KiB/s, 22.7 made a second
for 10.1 sent, and 45.4 for 23.1 against one that takes everything -- the
host's CPU spent on pictures thrown away. Asking for one picture at a time
while nobody touches the panel makes the two numbers equal.

The browser also does not remember a change it skipped while every slot was
out. On a page that then stops moving, that change never arrives at all: the
old sender left a panel at --fps 10 with the finished picture off on a
mid-animation picture in three runs of six. The second half checks that a
page's last change always reaches the panel.

    python3 tools/checkmade.py [--sender PATH] [--runs N]

--sender runs another copy of ha_send.py (copy udisp_send.py beside it).
Needs Playwright, a Chromium and Pillow. Takes about three minutes.
"""
import http.server
import io
import os
import pathlib
import signal
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))


def option(name, default):
    for n, arg in enumerate(sys.argv):
        if arg == name and n + 1 < len(sys.argv):
            return sys.argv[n + 1]
    return default


SENDER = option("--sender", str(SENDER_DIR / "ha_send.py"))
RUNS = int(option("--runs", "6"))
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails += 1


# Cards scrolled a little on every frame, so every picture is a whole panel.
SCROLLING = b"""<!doctype html><meta charset=utf-8><style>
body{margin:0;background:#111418;color:#e8e8e8;font:16px sans-serif}
.card{margin:14px;padding:18px;border-radius:14px;
 background:linear-gradient(135deg,#1d2430,#2a3444);
 box-shadow:0 10px 40px rgba(0,0,0,.6)}
</style><body><script>
for (let i = 0; i < 400; i++) {
  const d = document.createElement('div'); d.className = 'card';
  d.textContent = 'sensor.room_' + i + ' ' + (i * 7 % 40) + ' C';
  document.body.appendChild(d);
}
let y = 0;
(function step() {
  y += 14; if (y > document.body.scrollHeight - 1400) y = 0;
  window.scrollTo(0, y); requestAnimationFrame(step);
})();
</script>"""

# A colour every frame for five seconds, then green and still for good. The
# end is a little random so it lands at every point of the sender's cycle.
STOPPING = b"""<!doctype html><body style="margin:0;background:#000"><script>
let n = 0; const t0 = performance.now();
(function step() {
  if (performance.now() - t0 > 5000 + Math.random() * 200) {
    document.body.style.background = 'rgb(0,200,0)'; return; }
  n++; document.body.style.background =
    'rgb(' + (40 + n * 37 % 150) + ',0,' + (n * 53 % 150) + ')';
  requestAnimationFrame(step);
})();
</script>"""


def serve(body):
    class Site(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def panel(kib_s, canvas=None, lock=None):
    """A fake panel taking kib_s KiB a second (0: all of it), and painting
    what it is sent onto `canvas` when given one."""
    from PIL import Image
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    listener = socket.socket()
    # Small, so the drain rate reaches the sender rather than a buffer.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        stream, due = b"", time.monotonic()
        while True:
            try:
                data = conn.recv(16384)
            except OSError:
                return
            if not data:
                return
            if kib_s:
                due += len(data) / (kib_s * 1024)
                wait = due - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                else:
                    due = time.monotonic()
            if canvas is None:
                continue
            stream += data
            while len(stream) >= _HEADER.size:
                _, kind, _, x, y, _, _, packed = _HEADER.unpack_from(stream)
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
    return listener.getsockname()[1]


def sender(port, url, width, height, *extra):
    return [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
            "--port", str(port), "--url", url, "--no-token",
            "--not-home-assistant", "--width", str(width),
            "--height", str(height), "--audio", "off", "--keyboard", "off",
            *extra]


def start(command, **kw):
    # Its own process group, so the browser goes with it: a browser left
    # running from one half slows the next half's start.
    return subprocess.Popen(command, start_new_session=True, **kw)


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    process.wait()


def made_against_sent():
    print("Nobody touching, a panel taking 800 KiB/s, --fps 30:")
    server = serve(SCROLLING)
    port = panel(800)
    command = sender(port, f"http://127.0.0.1:{server.server_address[1]}/",
                     800, 1280, "--fps", "30", "--quality", "80", "--stats",
                     "--no-refine")
    process = start(command, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True)
    lines = []
    threading.Thread(target=lambda: lines.extend(process.stdout),
                     daemon=True).start()
    time.sleep(36)
    stop(process)
    server.shutdown()
    out = "".join(lines)
    # The first two windows hold the page loading.
    lines = [line for line in out.splitlines() if "pictures/s" in line][2:]
    sent = [float(m) for line in lines
            for m in re.findall(r"^([\d.]+) pictures/s", line)]
    made = [float(m) for line in lines
            for m in re.findall(r"([\d.]+) made/s", line)]
    if not sent or not made:
        check("the sender reports what it made and sent", False,
              "no --stats line came back")
        return
    sent, made = statistics.mean(sent), statistics.mean(made)
    check("the browser makes no more pictures than are sent",
          sent > 0 and made <= 1.25 * sent,
          f"{made:.1f} made a second for {sent:.1f} sent")


def last_picture():
    from PIL import Image
    print(f"A page that stops moving, --fps 10, finished picture off, "
          f"{RUNS} runs:")
    width, height = 480, 320
    ended = []
    for _ in range(RUNS):
        server = serve(STOPPING)
        canvas, lock = Image.new("RGB", (width, height)), threading.Lock()
        port = panel(0, canvas, lock)
        process = start(
            sender(port, f"http://127.0.0.1:{server.server_address[1]}/",
                   width, height, "--fps", "10", "--no-refine"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Timed from the panel's first picture, which is about when the page
        # loaded: five seconds of motion, and room after it.
        end = time.monotonic() + 40
        while time.monotonic() < end:
            with lock:
                if canvas.getpixel((width // 2, height // 2)) != (0, 0, 0):
                    break
            time.sleep(0.05)
        time.sleep(8)
        with lock:
            colour = canvas.getpixel((width // 2, height // 2))
        stop(process)
        server.shutdown()
        ended.append(colour[1] > 150 and colour[0] < 60)
        if not ended[-1]:
            print(f"         run {len(ended)} ended on {colour}")
    check("the panel ends on the page's last picture every time",
          all(ended), f"{sum(ended)} of {len(ended)}")


def main():
    print(f"sender: {SENDER}")
    if "--only-last" not in sys.argv:
        made_against_sent()
    last_picture()
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
