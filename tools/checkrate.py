#!/usr/bin/env python3
"""Does --max-rate hold what a panel is sent under a byte rate?

WHY THIS EXISTS. A panel watching YouTube at quality 50 and 30 pictures a
second was perfect up to about 2.7 MB/s -- 0% panel wait, worst gap 42 ms --
and past 2.9 the ESP32-C6's SDIO link threw reads away and every window had
340-400 ms holes in it. A fixed quality makes the rate follow the scene, so
the busy scenes were the ones that crossed. RateControl sends those scenes a
little softer instead.

Two halves, both against the SHIPPED code:

  * RateControl on its own, fed picture sizes, so the arithmetic can be seen
    without a browser: a heavy stream brings the quality down, a light one
    lets it climb back, it never goes above what was asked for or below its
    floor, a picture after a long still gap is not waved through, and a rate
    of 0 changes nothing.
  * the real ha_send.py against a fake panel that takes everything at once,
    with a page that is expensive to encode, read off its own --stats: with
    no limit it sends well over the rate, with the limit it stays near it at
    the same number of pictures a second, and --stats says which qualities
    were used. And a page that does not move is never touched.

Needs Playwright and a Chromium for the second half. Takes --browser or
$CHROMIUM.
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
SENDER = ROOT / "components" / "portall" / "ha_send.py"
sys.path.insert(0, str(SENDER.parent))


def option(name, default):
    for n, arg in enumerate(sys.argv):
        if arg == name and n + 1 < len(sys.argv):
            return sys.argv[n + 1]
    return default


BROWSER = option("--browser", os.environ.get("CHROMIUM", ""))
SECONDS = 24
fails = 0


def ok(what, passed, detail=""):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what
          + (f"  ({detail})" if detail else ""))
    if not passed:
        fails += 1


# ----------------------------------------------------------------- arithmetic
from ha_send import RateControl  # noqa: E402

FRAME = 1 / 30

rc = RateControl(2400)
q0 = rc.quality(80)
ok("the first picture goes out at the quality asked for", q0 == 80, q0)
# Pictures twice the budget, a steady stream at 30 a second.
budget = 2400 * 1024 * FRAME
for _ in range(10):
    q = rc.quality(80)
    rc.note(budget * 2, FRAME, FRAME, 80)
ok("a stream twice over the rate brings the quality down", rc.current < 50,
   rc.current)
low = rc.current
for _ in range(200):
    rc.quality(80)
    rc.note(budget * 3, FRAME, FRAME, 80)
ok("and never below its floor", rc.current == RateControl.MIN_QUALITY,
   rc.current)
for _ in range(10):
    rc.quality(80)
    rc.note(budget * 0.3, FRAME, FRAME, 80)
ok("a light stream lets it climb back, a step at a time",
   rc.current == RateControl.MIN_QUALITY + 10, rc.current)
for _ in range(500):
    rc.quality(80)
    rc.note(budget * 0.3, FRAME, FRAME, 80)
ok("but never above what the page asked for", rc.current == 80, rc.current)
rc.quality(40)
ok("a page that asks for less is given less at once", rc.quality(40) == 40)

rc = RateControl(2400)
rc.quality(80)
# One whole panel after ten seconds of a still page: 250 KiB is many times a
# single frame's share, and nothing against the quarter second it may count.
rc.note(250 * 1024, 10.0, FRAME, 80)
ok("a whole panel after a still page is not judged as one frame's share",
   rc.current == 80, rc.current)
# But the span is bounded, so a huge one after a long gap still counts: 1 MiB
# over a quarter second is 4 MiB/s, not 100 KiB/s over ten seconds.
rc.note(1024 * 1024, 10.0, FRAME, 80)
ok("and a huge one after a long gap is still weighed", rc.current < 80,
   rc.current)

rc = RateControl(0)
for _ in range(50):
    q = rc.quality(80)
    rc.note(10 ** 7, FRAME, FRAME, 80)
ok("a rate of 0 changes nothing", rc.quality(80) == 80)
low, high = RateControl(2400).take_range()
ok("a controller never asked reports no range", low is None and high is None)

# The budget: what the panel reported. Quality at its floor, every picture
# still 95 KiB where 30 a second at 2400 KiB/s leaves 80.
rc = RateControl(2400)
now, sent, released = 0.0, 0, 0
for turn in range(3000):          # ten seconds of 1/300 s loop turns
    now = turn / 300
    if now - released * FRAME >= 0 and rc.allows(now):
        rc.quality(80)
        rc.note(95 * 1024, FRAME, FRAME, 80, now=now)
        sent += 95 * 1024
        released += 1
# Anything faster than one per FRAME is refused above, so this is the most it
# could have sent without the budget: 30 a second.
rate_sent = sent / 1024 / now
ok("with the quality at its floor, the budget holds the rate",
   rate_sent <= 2400 * 1.02, f"{rate_sent:.0f} KiB/s")
ok("by sending fewer pictures, not none",
   22 <= released / now <= 26, f"{released / now:.1f} pictures/s")
ok("and says how many it made wait", rc.take_held() > 0)
ok("and the count starts again once read", rc.take_held() == 0)

rc = RateControl(2400)
rc.quality(80)
ok("a first picture is never held", rc.allows(0.0))
rc.note(250 * 1024, 10.0, FRAME, 80, now=0.0)
ok("a whole panel after a still page does not stop the one after it for long",
   rc.allows(0.1), rc._credit(0.1))

rc = RateControl(2400)
rc.quality(60)
rc.note(400 * 1024, FRAME, FRAME, 60, now=0.0)
# 400 KiB against a full budget of 360 leaves 40 KiB of debt, which the rate
# refills in about 16 ms.
ok("a picture far over the budget makes the next one wait",
   not rc.allows(0.005))
ok("and only as long as the debt takes to refill", rc.allows(0.02))
before = rc.current
rc.note(95 * 1024, 0.2, FRAME, 60, now=0.2)
ok("and a picture that waited is still judged on its weight, so the quality "
   "goes down first", rc.current < before, f"{before} -> {rc.current}")

rc = RateControl(0)
rc.note(10 ** 8, FRAME, FRAME, 80, now=0.0)
ok("a rate of 0 never holds anything", rc.allows(0.0) and rc.take_held() == 0)

# ------------------------------------------------------------ the real sender
# Hundreds of soft coloured discs moving every frame over a gradient: the
# kind of picture JPEG pays for -- water, leaves, a crowd -- and a whole panel
# every frame, like a video.
BUSY = """<!doctype html><meta charset=utf-8><style>
html,body{margin:0;height:100%;overflow:hidden;
 background:linear-gradient(135deg,#123 0%,#a52 50%,#2a6 100%)}
canvas{display:block}
</style><body><canvas id=c></canvas><script>
const c = document.getElementById('c'), g = c.getContext('2d');
c.width = innerWidth; c.height = innerHeight;
const dots = [];
for (let i = 0; i < 900; i++) dots.push({x: Math.random()*c.width,
  y: Math.random()*c.height, r: 4 + Math.random()*22,
  h: Math.random()*360, v: 1 + Math.random()*5});
(function step() {
  g.clearRect(0, 0, c.width, c.height);
  for (const d of dots) {
    d.y += d.v; if (d.y > c.height + 30) d.y = -30;
    g.fillStyle = `hsla(${d.h},80%,55%,.55)`;
    g.beginPath(); g.arc(d.x, d.y, d.r, 0, 7); g.fill();
  }
  requestAnimationFrame(step);
})();
</script>"""
STILL = """<!doctype html><meta charset=utf-8><body
 style="margin:0;background:#111;color:#eee;font:40px sans-serif">
<p style="padding:40px">A dashboard that does not move.</p>"""


def serve(pages):
    folder = tempfile.mkdtemp()
    for name, text in pages.items():
        (pathlib.Path(folder) / name).write_text(text)

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


def run(url, *extra):
    command = [sys.executable, "-u", str(SENDER), "--host", "127.0.0.1",
               "--port", str(fake_panel()), "--url", url,
               "--no-token", "--not-home-assistant",
               "--width", "800", "--height", "1280", "--fps", "30",
               "--quality", "80", "--audio", "off", "--keyboard", "off",
               "--stats", *extra]
    if BROWSER:
        command += ["--browser", BROWSER]
    try:
        out = subprocess.run(command, capture_output=True, text=True,
                             timeout=SECONDS).stdout
    except subprocess.TimeoutExpired as done:
        out = done.stdout.decode() if isinstance(done.stdout, bytes) else (
            done.stdout or "")
    lines = [l for l in out.splitlines() if " pictures/s" in l][1:]
    kib = [float(m) for m in re.findall(r"([\d.]+) KiB/s", "\n".join(lines))]
    fps = [float(m) for m in re.findall(r"^([\d.]+) pictures/s",
                                        "\n".join(lines), re.M)]
    quality = re.findall(r"quality (\d+(?:-\d+)?)", "\n".join(lines))
    return out, kib, fps, quality


def mean(values):
    return sum(values) / len(values) if values else 0.0


port = serve({"busy.html": BUSY, "still.html": STILL})
busy = f"http://127.0.0.1:{port}/busy.html"

_, free_kib, free_fps, free_q = run(busy)
ok("the busy page with no limit sends well over 1500 KiB/s",
   mean(free_kib) > 1500 * 1.3, f"{mean(free_kib):.0f} KiB/s, "
   f"{mean(free_fps):.1f} pictures/s")
ok("and says nothing about quality", not free_q, free_q)

out, kib, fps, quality = run(busy, "--max-rate", "1500")
ok("with --max-rate 1500 it stays near 1500 KiB/s",
   kib and mean(kib) <= 1500 * 1.12,
   f"{mean(kib):.0f} KiB/s, windows {', '.join(f'{k:.0f}' for k in kib)}")
ok("at the same number of pictures a second",
   fps and mean(fps) >= 0.9 * mean(free_fps),
   f"{mean(fps):.1f} against {mean(free_fps):.1f}")
ok("and --stats says which qualities it used", bool(quality), quality)
ok("the log says the limit is on", "Rate: at most 1500 KiB/s" in out)

# The panel's report: a scene too heavy for the rate even at the lowest
# quality. 4.21.0 sent about 1330 KiB/s here, at quality 25.
out, kib, fps, quality = run(busy, "--max-rate", "900")
ok("with the quality at its floor, --max-rate 900 still holds 900",
   kib and max(kib) <= 900 * 1.08,
   f"windows {', '.join(f'{k:.0f}' for k in kib)}, "
   f"{mean(fps):.1f} pictures/s, quality {quality}")
ok("and --stats says pictures were held",
   bool(re.search(r"\d+ held", out)))

_, kib, fps, quality = run(busy, "--max-rate", "1500",
                           "--page-rate", f"http://127.0.0.1:{port}/busy=0")
ok("a link given 0 turns the limit off for itself",
   mean(kib) > 1500 * 1.3 and not quality, f"{mean(kib):.0f} KiB/s")

_, kib, fps, quality = run(f"http://127.0.0.1:{port}/still.html",
                           "--max-rate", "1500")
ok("a page that does not move is never touched", not quality, quality)

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
