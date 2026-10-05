#!/usr/bin/env python3
"""A finger flicked up the glass carries the page on, as on a tablet.

WHY THIS EXISTS. Asked as "est il possible que le slide up et down avec le
doigt soit comme une tablette ou un smartphone": a drag was replayed as a
mouse wheel, so the page stopped dead the moment the finger lifted.
`--glide` carries it on after the lift and slows it down.

The shipped ha_send.py against a fake panel sending real contacts up its
return channel, on a tall page that reports its scroll position and its
clicks to the server:

  - a quick flick moves the page further after the lift than before it;
  - a finger that stops before lifting does not glide;
  - a tap is still a click, and does not scroll;
  - a finger landing on a gliding page stops it, and clicks nothing;
  - after a navigation to another site, a flick still glides;
  - and without the option, a flick stops dead at the lift (the report).

    python3 tools/checkglide.py [--sender PATH]

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
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
fails = 0
seen = {"y": {}, "clicks": 0}

PAGE = ("<!doctype html><body style='margin:0'>"
        + "".join(f"<div style='height:100px;background:hsl({i * 37 % 360},"
                  f"60%,45%)'></div>" for i in range(400))
        + "<script>"
        "addEventListener('scroll', () => fetch('/pos?y=' + scrollY));"
        "addEventListener('click', () => fetch('/click'));"
        "</script></body>")


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
        host = (self.headers.get("Host") or "").split(":")[0]
        found = re.match(r"/pos\?y=([\d.]+)", self.path)
        if found:
            seen["y"][host] = float(found.group(1))
        elif self.path == "/click":
            seen["clicks"] += 1
        data = PAGE.encode() if self.path == "/page" else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def run(flag):
    from udisp_send import _HEADER
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    panel = []
    pictures = [0]
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
                    total = _HEADER.unpack_from(stream)[7] >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    if total:
                        pictures[0] += 1
                    stream = stream[_HEADER.size + total:]
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    work = tempfile.mkdtemp()
    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]),
               "--url", f"http://127.0.0.1:{port}/page",
               "--not-home-assistant", "--width", str(W), "--height", str(H),
               "--audio", "off", "--keyboard", "off", "--control",
               "--profile", os.path.join(work, "p")] + flag
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def contact(x, y):
        panel[0].sendall(b"T\x01\x00" + int(x).to_bytes(2, "little")
                         + int(y).to_bytes(2, "little"))

    def lift():
        panel[0].sendall(b"T\x00")

    def flick(x, y0, y1, steps=10, gap=0.015, rest=0.0):
        contact(x, y0)
        time.sleep(gap)
        for i in range(1, steps + 1):
            contact(x, y0 + (y1 - y0) * i / steps)
            time.sleep(gap)
        time.sleep(rest)
        lift()

    def settle(host, seconds=1.5):
        time.sleep(seconds)
        return seen["y"].get(host, 0.0)

    result = {}
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and (not panel or pictures[0] < 2):
            time.sleep(0.1)
        time.sleep(1.5)
        here = "127.0.0.1"
        flick(W // 2, 400, 100)
        time.sleep(0.12)
        at_lift = seen["y"].get(here, 0.0)
        result["flick"] = (at_lift, settle(here))
        start = seen["y"].get(here, 0.0)
        # The finger travels the same way, then rests before it lifts.
        flick(W // 2, 400, 100, rest=0.4)
        time.sleep(0.05)
        rested = seen["y"].get(here, 0.0)
        result["rested"] = (rested - start, settle(here) - start)
        before, clicks = seen["y"].get(here, 0.0), seen["clicks"]
        contact(W // 2, 240)
        time.sleep(0.1)
        lift()
        time.sleep(0.8)
        result["tap"] = (seen["clicks"] - clicks,
                         seen["y"].get(here, 0.0) - before)
        clicks = seen["clicks"]
        flick(W // 2, 400, 100)
        time.sleep(0.2)
        contact(W // 2, 240)
        time.sleep(0.1)
        lift()
        time.sleep(0.15)
        caught = seen["y"].get(here, 0.0)
        result["caught"] = (seen["clicks"] - clicks, caught,
                            settle(here, 1.2) - caught)
        process.stdin.write(f"open http://localhost:{port}/page\n")
        process.stdin.flush()
        time.sleep(3)
        flick(W // 2, 400, 100)
        time.sleep(0.12)
        result["other site"] = (seen["y"].get("localhost", 0.0),
                                settle("localhost"))
        result["running"] = process.poll() is None
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    return result, out


def main():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright")
        return 0
    print("With --glide:")
    got, out = run(["--glide"])
    lift, later = got["flick"]
    check("a flick glides on after the finger lifts", later > lift + 100,
          f"{lift:.0f} px at the lift, {later:.0f} px after")
    print(f"         ({lift:.0f} px at the lift, {later:.0f} px after)")
    rested, after = got["rested"]
    check("a finger that stops before lifting does not glide",
          after - rested < 30, f"{rested:.0f} then {after:.0f}")
    clicks, moved = got["tap"]
    check("a tap is still a click, and scrolls nothing",
          clicks == 1 and abs(moved) < 1, f"{clicks} click(s), {moved:.0f} px")
    clicks, _, moved = got["caught"]
    check("a finger landing on a gliding page stops it, clicking nothing",
          clicks == 0 and moved < 20, f"{clicks} click(s), "
          f"{moved:.0f} px after it")
    lift, later = got["other site"]
    check("on another site after a navigation, a flick still glides",
          later > lift + 100, f"{lift:.0f} then {later:.0f}")
    check("the sender is still running", got["running"], "".join(out[-5:]))

    print("Without it, as before:")
    got, out = run([])
    lift, later = got["flick"]
    check("a flick stops where the finger lifted (the report)",
          abs(later - lift) < 20 and lift > 200,
          f"{lift:.0f} px at the lift, {later:.0f} px after")
    clicks, moved = got["tap"]
    check("and a tap is a click", clicks == 1 and abs(moved) < 1,
          f"{clicks} click(s), {moved:.0f} px")
    print("\nAll good." if not fails else f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
