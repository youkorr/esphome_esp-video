#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Coming home to the launcher shows its wallpaper from the first picture.

WHY THIS EXISTS. Filmed on a Waveshare 7B: back from a link, the launcher
arrived on its plain ground, and its wallpaper only in the picture after --
the wallpaper "disappears and comes back" every time. A page paints as soon
as it can and a background picture is not something it waits for, so a
first paint that beats the picture's load and decode is a launcher with no
wallpaper, and the screencast hands that picture over like any other.

The shipped launcher and the shipped sender, against a fake panel that puts
the rectangles together the way the board does: a launcher with a large,
detailed wallpaper, one tile, a link on another server. The panel is read at
a spot only the wallpaper covers, from the moment home is asked for: every
picture that reaches it after the link must already be the wallpaper.

    python3 tools/checkhomewall.py [--sender PATH] [--runs N]

--sender runs another copy of ha_send.py (beside its own udisp_send.py), so
the fault can be measured against the code it replaces.
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
sys.path.insert(0, str(HERE / "portall"))

SENDER = SENDER_DIR / "ha_send.py"
RUNS = 4
args = sys.argv[1:]
while args:
    flag = args.pop(0)
    if flag == "--sender":
        SENDER = pathlib.Path(args.pop(0))
    elif flag == "--runs":
        RUNS = int(args.pop(0))
BROWSER = os.environ.get("CHROMIUM", "")

W, H = 1024, 600
SPOT = (980, 560)        # a corner only the wallpaper covers
LINK = (0x30, 0xA0, 0x50)
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, tol=24):
    return all(abs(a - b) < tol for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = (b"<!doctype html><html><body style='margin:0;"
                b"background:rgb(%d,%d,%d)'>a link</body></html>" % LINK)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_wallpaper(path):
    """A 4K picture as detailed as a photograph, so its decode costs what a
    real one does: noise over a strong magenta, which no theme's ground is."""
    import numpy
    from PIL import Image
    rng = numpy.random.default_rng(7)
    pixels = rng.integers(-40, 40, (2160, 3840, 3), dtype=numpy.int16)
    pixels += numpy.array([200, 40, 170], dtype=numpy.int16)
    Image.fromarray(pixels.clip(0, 255).astype(numpy.uint8)).save(
        path, quality=92)
    return os.path.getsize(path)


def run_once(home_url, link_url, wake=False):
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    from PIL import Image
    canvas = Image.new("RGB", (W, H))
    seen = []                 # (monotonic, colour at SPOT) after each rectangle
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
                        canvas.paste(Image.open(io.BytesIO(payload))
                                     .convert("RGB"), (x, y))
                    except Exception:  # noqa: BLE001
                        continue
                    if x <= SPOT[0] < x + w and y <= SPOT[1] < y + h:
                        seen.append((time.monotonic(),
                                     canvas.getpixel(SPOT)))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    command = [sys.executable, "-u", str(SENDER), "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url", home_url,
               "--no-token", "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control"] + (["--blank-after", "1"] if wake else [])
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def settled(after, quiet=1.0, limit=20):
        """The colour at SPOT once nothing has changed there for `quiet`."""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            fresh = [c for t, c in seen if t > after]
            if fresh and time.monotonic() - max(
                    t for t, _ in seen) > quiet:
                return fresh[-1]
            time.sleep(0.05)
        return None

    try:
        wall = settled(0, limit=40)
        result = {"wall": wall}
        if wall is None:
            return result, out
        if wake:
            # Dark long enough for the page to be parked on about:blank, then
            # awake: the launcher is loaded again, and must come back whole.
            panel[0].sendall(b"S\x00")
            time.sleep(3.0)
            woke = time.monotonic()
            panel[0].sendall(b"S\x01")
            settled(woke, quiet=1.5, limit=10)
            after = [(t, c) for t, c in seen if t > woke]
            result["home"] = [c for _, c in after]
            result["took"] = after[0][0] - woke if after else None
            result["parked"] = any("Loading the page again" in line
                                   for line in out)
            return result, out
        asked = time.monotonic()
        process.stdin.write(f"open {link_url}\n")
        process.stdin.flush()
        end = asked + 10
        while time.monotonic() < end and not any(
                t > asked and near(c, LINK) for t, c in seen):
            time.sleep(0.02)
        result["link"] = any(t > asked and near(c, LINK) for t, c in seen)
        time.sleep(1.0)
        home = time.monotonic()
        process.stdin.write("home\n")
        process.stdin.flush()
        settled(home, quiet=1.5, limit=10)
        after = [(t, c) for t, c in seen if t > home]
        # What the panel showed at SPOT, in order, the link's own colour
        # aside: the link is what is left on screen until home arrives.
        result["home"] = [c for _, c in after if not near(c, LINK)]
        result["took"] = next((t - home for t, c in after
                               if not near(c, LINK)), None)
        result["wall_at"] = next((t - home for t, c in after
                                  if result["wall"] and near(c, result["wall"])),
                                 None)
        return result, out
    finally:
        process.kill()
        listener.close()


def main():
    try:
        import numpy  # noqa: F401
        import playwright  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright, numpy and Pillow")
        return 0
    import launcher
    where = tempfile.mkdtemp()
    picture = os.path.join(where, "wall.jpg")
    size = make_wallpaper(picture)
    # On ANOTHER SITE, as every real link is: 127.0.0.2 is not 127.0.0.1 to
    # a browser, so coming home is a new renderer process with nothing of
    # the launcher's in memory -- the case a same-site link would hide.
    server = http.server.ThreadingHTTPServer(("127.0.0.2", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    link_url = f"http://127.0.0.2:{server.server_address[1]}/page"
    home_url = launcher.start(
        [{"name": "Link", "url": link_url, "icon": "tv"}],
        background=picture, dim=0, port=launcher.ANY_PORT)
    plain_url = launcher.start(
        [{"name": "Link", "url": link_url, "icon": "tv"}],
        port=launcher.ANY_PORT)
    print(f"{SENDER}: a {size / 1e6:.1f} MB wallpaper, home from a link "
          f"{RUNS} times:")
    for run in range(RUNS):
        result, out = run_once(home_url, link_url)
        wall = result.get("wall")
        if wall is None or not result.get("link"):
            check(f"run {run + 1}: the launcher and the link reach the panel",
                  False, "".join(out[-8:]))
            continue
        home = result["home"]
        bare = [c for c in home if not near(c, wall)]
        took = result["took"]
        print(f"    run {run + 1}: wallpaper {wall}, after home "
              f"{len(home)} picture(s) {home[:4]}, the first after "
              f"{'never' if took is None else f'{took * 1000:.0f} ms'}, "
              f"the wallpaper after "
              f"{'never' if result['wall_at'] is None else str(round(result['wall_at'] * 1000)) + ' ms'}")
        check(f"run {run + 1}: home reaches the panel", bool(home))
        check(f"run {run + 1}: every picture of home shows the wallpaper",
              home and not bare, f"{len(bare)} without it: {bare[:3]}")
        # The hold is bounded: a picture slower than that is shown anyway.
        check(f"run {run + 1}: and within the wait's bound",
              took is not None and took < 1.5, took)
        crashed = [line.strip() for line in out if "Traceback" in line]
        check(f"run {run + 1}: the sender did not crash", not crashed,
              crashed[-1] if crashed else "")

    print("Woken after the page was parked, which loads the launcher again:")
    for run in range(2):
        result, out = run_once(home_url, link_url, wake=True)
        wall = result.get("wall")
        home = result.get("home") or []
        bare = [c for c in home if wall is None or not near(c, wall)]
        print(f"    run {run + 1}: after waking {len(home)} picture(s) "
              f"{home[:4]}")
        check(f"run {run + 1}: the page was parked and loaded again",
              result.get("parked"), "".join(out[-6:]))
        check(f"run {run + 1}: every picture after waking shows the "
              f"wallpaper", home and not bare, f"{len(bare)} without it")

    print("A launcher with no wallpaper, which must not be held at all:")
    for run in range(2):
        result, out = run_once(plain_url, link_url)
        took = result.get("took")
        print(f"    run {run + 1}: the first picture of home after "
              f"{'never' if took is None else f'{took * 1000:.0f} ms'}")
        check(f"run {run + 1}: home reaches the panel promptly",
              took is not None and took < 0.5, took)
        waited = [line.strip() for line in out
                  if "wallpaper was not on screen" in line]
        check(f"run {run + 1}: nothing was waited for", not waited,
              waited[:1])
    server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
