#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""How late is a picture when the panel takes them slower than they are made?

WHY THIS EXISTS. Reported as the panel coming back from an add-on update
"avec beaucoup de latence en vidéo et navigation". Whatever slows a panel
down, what turned it into SECONDS of lag was this machine's kernel: with
nothing bounding it, Linux grows a socket's send buffer to megabytes, and
every picture in it is that much older by the time the board decodes it.

The shipped ha_send.py runs against a fake panel with a board-sized receive
window (512000) and a token bucket for its drain, on a page that paints the
time as sixteen blocks and changes every frame, so every picture is a whole
panel. The panel reads the time back out of each picture.

  - taking 1200 KiB/s, slower than the page is made: a picture is not
    seconds old, and the link carries as much as it did;
  - taking everything: thirty pictures a second, as fresh as before.

--ref REV runs the sender of that commit (git show), the reproduction:
3.3 s of lag before UNSENT_LIMIT. Needs Playwright and Pillow; takes $CHROMIUM.
"""
import http.server
import io
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
BROWSER = os.environ.get("CHROMIUM", "")
REF = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else None
W, H = 800, 480
fails = 0

PAGE = b"""<!doctype html><body style='margin:0;background:#000;overflow:hidden'>
<canvas id=c width=800 height=480></canvas><script>
const c=document.getElementById('c'),g=c.getContext('2d');
function paint(){
  const img=g.createImageData(800,480),d=img.data;
  for(let i=0;i<d.length;i+=4){const v=Math.random()*255|0;
    d[i]=v;d[i+1]=(v*7)&255;d[i+2]=(v*3)&255;d[i+3]=255;}
  g.putImageData(img,0,0);
  const t=Date.now()&0xffff;
  for(let b=0;b<16;b++){g.fillStyle=(t>>b)&1?'#fff':'#000';
    g.fillRect(b*50,200,50,60);}
  requestAnimationFrame(paint);
}
paint();
</script>"""


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


def run(sender_dir, rate, seconds, window=512000, stall=0.0):
    """(pictures a second, KiB/s, median age ms) with the panel draining
    `rate` KiB/s, or everything when it is 0."""
    sys.path.insert(0, str(sender_dir))
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    from PIL import Image

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    listener = socket.socket()
    # Linux doubles SO_RCVBUF; this approximates the receiver buffer, not
    # an exact advertised TCP window (which also depends on kernel policy).
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, window // 2)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    ages, got, start = [], [0], [None]

    def panel():
        conn, _ = listener.accept()
        stream, budget, last = b"", 0.0, time.monotonic()
        next_stall = last + 5
        while True:
            if stall and time.monotonic() >= next_stall:
                time.sleep(stall)
                next_stall = time.monotonic() + 5
                # Do not bank a burst of credits during the injected pause.
                last = time.monotonic()
                budget = 0.0
            if rate > 0:
                now = time.monotonic()
                budget = min(budget + (now - last) * rate * 1024, 65536)
                last = now
                if budget < 4096:
                    time.sleep(0.002)
                    continue
                want = int(min(budget, 16384))
            else:
                want = 1 << 20
            try:
                data = conn.recv(want)
            except OSError:
                return
            if not data:
                return
            budget -= len(data)
            got[0] += len(data)
            stream += data
            while len(stream) >= _HEADER.size:
                _, kind, _, x, y, w, h, packed = _HEADER.unpack_from(stream)
                total = packed >> 10
                if len(stream) < _HEADER.size + total:
                    break
                payload = stream[_HEADER.size:_HEADER.size + total]
                stream = stream[_HEADER.size + total:]
                if kind != UDISP_TYPE_JPG or not y <= 230 < y + h:
                    continue
                pic = Image.open(io.BytesIO(payload)).convert("L")
                t = 0
                for b in range(16):
                    px = b * 50 + 25 - x
                    if not 0 <= px < w:
                        break
                    if pic.getpixel((px, 230 - y)) > 128:
                        t |= 1 << b
                else:
                    now = int(time.time() * 1000) & 0xffff
                    if start[0] is None:
                        start[0] = time.monotonic()
                    ages.append((time.monotonic() - start[0],
                                 (now - t) & 0xffff))

    threading.Thread(target=panel, daemon=True).start()
    command = [sys.executable, "-u", str(sender_dir / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url",
               f"http://127.0.0.1:{server.server_address[1]}/",
               "--not-home-assistant", "--width", str(W), "--height", str(H),
               "--audio", "off", "--keyboard", "off", "--fps", "30",
               "--quality", "80", "--no-refine"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    threading.Thread(target=lambda: process.stdout.read(),
                     daemon=True).start()
    try:
        begun = time.monotonic()
        while start[0] is None and time.monotonic() - begun < 60:
            time.sleep(0.1)
        time.sleep(seconds)
    finally:
        os.killpg(process.pid, 9)
        listener.close()
        server.shutdown()
        sys.path.remove(str(sender_dir))
        for name in ("udisp_send",):
            sys.modules.pop(name, None)
    if len(ages) < 2:
        return 0.0, 0.0, None
    span = max(ages[-1][0] - ages[0][0], 1)
    late = [age for at, age in ages if at > seconds / 2] or [0]
    return len(ages) / span, got[0] / 1024 / span, statistics.median(late)


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    sender = HERE / "components" / "portall"
    temp = None
    if REF:
        temp = tempfile.TemporaryDirectory()
        sender = pathlib.Path(temp.name)
        for name in ("ha_send.py", "udisp_send.py"):
            (sender / name).write_bytes(subprocess.run(
                ["git", "-C", str(HERE), "show",
                 f"{REF}:components/portall/{name}"],
                capture_output=True, check=True).stdout)
    print(f"The sender of {REF or 'the working tree'}:")
    if "--guition" in sys.argv:
        fps, kib, age = run(sender, 3200, 30, window=65536, stall=0.250)
        print(f"    simulated 64 KiB receive buffer, 3200 KiB/s, "
              f"250 ms read pause every 5 s: {fps:.1f} pictures/s, "
              f"{kib:.0f} KiB/s, median age {age} ms")
        return 0 if age is not None else 1
    rate, kib, age = run(sender, 1200, 20)
    print(f"    taking 1200 KiB/s: {rate:.1f} pictures/s, {kib:.0f} KiB/s,"
          f" median age {age} ms")
    check("a panel slower than the page is not shown it seconds late",
          age is not None and age < 1500, f"{age} ms")
    check("and the link carries as much as before", kib > 1200 * 0.9,
          f"{kib:.0f} KiB/s")
    rate, kib, age = run(sender, 0, 15)
    print(f"    taking everything: {rate:.1f} pictures/s, {kib:.0f} KiB/s,"
          f" median age {age} ms")
    check("a panel that keeps up gets thirty a second", rate > 27,
          f"{rate:.1f}")
    check("as fresh as ever", age is not None and age < 150, f"{age} ms")
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
