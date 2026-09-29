#!/usr/bin/env python3
"""How long from asking for a link to seeing it on the panel?

WHY THIS EXISTS. Reported from panels other people run: opening a link takes
long enough that the add-on is thought to be broken. The time a site takes to
load is the site's; what this measures is what the SENDER adds on top of it,
which is the part this project can change.

A site is served here the way most real ones are built: an HTML shell that
paints at once (a coloured ground), and a script that takes a while to arrive
and then paints the page itself. The shipped ha_send.py is started against a
fake panel that reads the stream the way the board does, decodes every whole
picture and notes WHEN the shell's colour and the page's colour first reach
it. The link is asked for on the control channel -- the same go_to() a tapped
tile uses.

    python3 tools/checklinkspeed.py [--ref REV] [--script-delay 1.5]

--ref runs the ha_send.py of a git revision instead of the working tree, so a
change can be measured against the code it replaces.
"""
import http.server
import io
import os
import pathlib
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

REF = None
SENDER = None
TAP = False
TRACE = False
DELAY = 1.5
PATH = "page"
args = sys.argv[1:]
while args:
    flag = args.pop(0)
    if flag == "--ref":
        REF = args.pop(0)
    elif flag == "--sender":
        SENDER = pathlib.Path(args.pop(0))
    elif flag == "--trace":
        TRACE = True
    elif flag == "--tap":
        TAP = True
    elif flag == "--styled":
        PATH = "styled"
    elif flag == "--script-delay":
        DELAY = float(args.pop(0))
BROWSER = os.environ.get("CHROMIUM", "")

SHELL = (0x20, 0x40, 0x60)
PAGE = (0x60, 0xA0, 0x40)
HOME = (0x80, 0x20, 0x20)


def near(colour, want):
    return all(abs(a - b) < 14 for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/app.css"):
            time.sleep(DELAY)
            body = b"body{background:rgb(%d,%d,%d)!important}" % PAGE
            kind = "text/css"
        elif self.path.startswith("/styled"):
            body = (b"<!doctype html><html><head>"
                    b"<link rel=stylesheet href=/app.css></head>"
                    b"<body style='margin:0'>loading</body></html>")
            kind = "text/html"
        elif self.path.startswith("/app.js"):
            time.sleep(DELAY)
            body = (b"document.body.style.background='rgb(%d,%d,%d)';"
                    % PAGE)
            kind = "text/javascript"
        elif self.path.startswith("/home"):
            body = (b"<!doctype html><body style='margin:0;"
                    b"background:rgb(%d,%d,%d)'>home" % HOME)
            kind = "text/html"
        else:
            body = (b"<!doctype html><html><head>"
                    b"<script type=module src=/app.js></script></head>"
                    b"<body style='margin:0;background:rgb(%d,%d,%d)'>"
                    b"loading</body></html>" % SHELL)
            kind = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def sender_path():
    if SENDER is not None:
        return SENDER
    if REF is None:
        return SENDER_DIR / "ha_send.py"
    where = pathlib.Path(tempfile.mkdtemp())
    for name in ("ha_send.py", "udisp_send.py"):
        text = subprocess.run(
            ["git", "-C", str(HERE), "show",
             f"{REF}:components/portall/{name}"],
            capture_output=True, text=True, check=True).stdout
        (where / name).write_text(text)
    return where / "ha_send.py"


HOME_URL = None
TILE_AT = (0, 0)


def tap_setup(site):
    """A launcher whose one tile opens the slow page, and where that tile is."""
    global HOME_URL, TILE_AT
    sys.path.insert(0, str(HERE / "portall"))
    import launcher
    from playwright.sync_api import sync_playwright
    HOME_URL = launcher.start(
        [{"name": "Slow", "url": f"{site}/{PATH}", "icon": "tv"}],
        port=launcher.ANY_PORT)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=BROWSER or None)
        page = browser.new_page(viewport={"width": 400, "height": 240})
        page.goto(HOME_URL)
        box = page.locator("a.tile").bounding_box()
        TILE_AT = (int(box["x"] + box["width"] / 2),
                   int(box["y"] + box["height"] / 2))
        browser.close()


def run_once(site):
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    from PIL import Image
    seen = []           # (monotonic, colour) of every whole picture
    panel = []          # the connection, so a finger can be sent up it
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
                    if kind != UDISP_TYPE_JPG or x > 200 or y > 100 \
                            or x + w < 200 or y + h < 100:
                        continue
                    try:
                        pic = Image.open(io.BytesIO(payload)).convert("RGB")
                        seen.append((time.monotonic(),
                                     pic.getpixel((200 - x, 100 - y))))
                    except Exception:  # noqa: BLE001
                        pass
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    command = [sys.executable, "-u", str(sender_path()),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", HOME_URL or f"{site}/home",
               "--no-token", "--not-home-assistant", "--width", "400",
               "--height", "240", "--audio", "off", "--keyboard", "off",
               "--control"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and not (
                any(near(c, HOME) for _, c in seen)
                or (TAP and seen)):
            time.sleep(0.05)
        time.sleep(1.5)
        if TAP:
            # A finger on the tile, the way the board reports one: a
            # contact, then a release. The sender acts on the release, so
            # that is when this starts counting.
            x, y = TILE_AT
            panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                             + y.to_bytes(2, "little"))
            time.sleep(0.06)
            asked = time.monotonic()
            panel[0].sendall(b"T\x00")
        else:
            asked = time.monotonic()
            process.stdin.write(f"open {site}/{PATH}?{time.time()}\n")
            process.stdin.flush()
        end = asked + DELAY + 10
        while time.monotonic() < end and not any(
                t > asked and near(c, PAGE) for t, c in seen):
            time.sleep(0.02)
        shell = next((t - asked for t, c in seen
                      if t > asked and near(c, SHELL)), None)
        blank = next((t - asked for t, c in seen
                      if t > asked and min(c) > 235), None)
        page = next((t - asked for t, c in seen
                     if t > asked and near(c, PAGE)), None)
        opened = [line.strip() for line in out if "opened" in line]
        if TRACE:
            marks = []
            for line in out:
                if line.startswith("TRACE "):
                    tag, at = line.split()[1:3]
                    if float(at) > asked:
                        marks.append(f"{tag} {(float(at) - asked) * 1000:.0f}")
            got = [f"panel {(t - asked) * 1000:.0f} {c}" for t, c in seen
                   if t > asked][:3]
            print("    " + ", ".join(marks[:14] + got))
        return shell, page, blank, opened
    finally:
        process.kill()
        listener.close()


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
    what = "stylesheet" if PATH == "styled" else "script"
    if TAP:
        tap_setup(site)
        print(f"A finger on the launcher's tile at {TILE_AT}, counted from "
              f"the moment it lifts:")
    print(f"{'working tree' if REF is None else REF}, a page whose {what} "
          f"takes {DELAY:.1f}s to arrive:")
    for _ in range(3):
        shell, page, blank, opened = run_once(site)
        fmt = lambda v: "never" if v is None else f"{v * 1000:.0f} ms"
        print(f"  its shell on the panel after {fmt(shell)}, the whole page "
              f"after {fmt(page)}, a white screen {fmt(blank)}")
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
