#!/usr/bin/env python3
"""Does --fps N deliver N pictures a second when nothing else is short?

WHY THIS EXISTS. A panel's own --stats during a swipe read 13 to 17 pictures a
second with `panel wait` at 4 to 18%: the panel was waiting for the server,
not the other way round. Run against a fake panel that takes everything at
once, the shipped sender gave 23.6 pictures a second at --fps 30. The limit
restarted from the turn that NOTICED a picture, and the loop only looks every
fifteen milliseconds or so, so every interval was rounded up to the next turn:
33 ms became about 42.

This runs the real ha_send.py -- the working tree's, or any other copy given
with --sender -- against a page that scrolls itself and a fake panel that
never makes it wait, and reads its own --stats lines. A fault in the pacing
cannot hide behind the link here, because there is no link to blame.

Reproduce the old behaviour by pointing it at the previous release:

    git show HEAD~1:components/portall/ha_send.py > /tmp/old/ha_send.py
    cp components/portall/udisp_send.py /tmp/old/
    python3 tools/checkpacing.py --sender /tmp/old/ha_send.py

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.
"""
import functools
import http.server
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent


def option(name, default):
    for n, arg in enumerate(sys.argv):
        if arg == name and n + 1 < len(sys.argv):
            return sys.argv[n + 1]
    return default


SENDER = option("--sender", str(ROOT / "components" / "portall" / "ha_send.py"))
BROWSER = option("--browser", os.environ.get("CHROMIUM", ""))
SECONDS = 22

# Cards with shadows and gradients, scrolled a little on every frame, so every
# picture is a whole panel -- the shape of a swipe down a long list.
PAGE = """<!doctype html><meta charset=utf-8><style>
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


def serve_page():
    folder = tempfile.mkdtemp()
    (pathlib.Path(folder) / "index.html").write_text(PAGE)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=folder)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def fake_panel():
    """A panel that takes every byte the instant it arrives."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        while True:
            conn, _ = listener.accept()

            def drain(conn=conn):
                try:
                    while conn.recv(1 << 20):
                        pass
                except OSError:
                    pass
            threading.Thread(target=drain, daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    return listener.getsockname()[1]


def rate(fps, page_port):
    panel_port = fake_panel()
    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(panel_port),
               "--url", f"http://127.0.0.1:{page_port}/",
               "--no-token", "--not-home-assistant",
               "--width", "800", "--height", "1280", "--fps", str(fps),
               "--quality", "80", "--audio", "off", "--keyboard", "off",
               "--stats"]
    if BROWSER:
        command += ["--browser", BROWSER]
    try:
        out = subprocess.run(command, capture_output=True, text=True,
                             timeout=SECONDS).stdout
    except subprocess.TimeoutExpired as done:
        out = done.stdout.decode() if isinstance(done.stdout, bytes) else (
            done.stdout or "")
    rates = [float(m) for m in re.findall(r"^([\d.]+) pictures/s", out, re.M)]
    # The first window holds the page loading.
    return rates[1:]


def main():
    page_port = serve_page()
    faults = []
    print(f"sender: {SENDER}")
    for fps in (30, 15):
        rates = rate(fps, page_port)
        if not rates:
            print(f"  ECHEC  --fps {fps}: no --stats line came back")
            faults.append(fps)
            continue
        mean = sum(rates) / len(rates)
        # Nine tenths: a turn still lands a little after its moment, but the
        # schedule keeps the average, where the old rule lost a quarter.
        ok = mean >= 0.9 * fps
        print(f"  {'ok    ' if ok else 'ECHEC '} --fps {fps}: {mean:.1f} "
              f"pictures a second reach a panel that never makes it wait "
              f"({', '.join(f'{r:.1f}' for r in rates)})")
        if not ok:
            faults.append(fps)
    if faults:
        sys.exit(1)
    print("ok")


main()
