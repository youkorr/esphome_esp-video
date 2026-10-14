#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A transport button pauses, resumes and skips whatever plays on the page.

A car's steering wheel, a headset's button, a remote's media row, a Shield and
the iPhone's television remote all end at ha_send's act_on_media(), through
the shipped MEDIA_CONTROL_JS. Before it, the iPhone's play/pause went to the
page as the browser key "MediaPlayPause" -- which Playwright refuses as an
"Unknown key", so it did nothing on every panel -- and every other transport
button stopped at the board.

This runs the shipped script and function in the real browser, launched with
the sender's own BROWSER_ARGS, against pages served over HTTP so the script is
installed the way the sender installs it (an init script, before the page's
own):

  - a page with a sound playing and no Media Session: pause, resume, ten
    seconds forward for "next";
  - a page that registered Media Session handlers: the SITE's own handler is
    called, and "next" is the site's next rather than a seek;
  - a player in an iframe of another origin, with nothing in the top page;
  - a page with nothing playing, which says so;
  - and the measurement that made this necessary: Playwright refusing the
    browser key the HomeKit remote used to send.

    python3 tools/checkmedia.py
"""

import base64
import http.server
import io
import math
import pathlib
import struct
import sys
import threading
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))

from ha_send import BROWSER_ARGS, MEDIA_CONTROL_JS, Control, act_on_media  # noqa: E402
from udisp_send import parse_messages  # noqa: E402

faults = []


def check(what, ok):
    print(("  ok     " if ok else "  ECHEC  ") + what)
    if not ok:
        faults.append(what)


def tone_wav(seconds=40):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 6)))
                               for i in range(8000 * seconds)))
    return out.getvalue()


# The sound is a data: address rather than a file, because a seek needs the
# server to answer byte ranges and http.server does not; a data: source is
# all in memory and seekable, as a real site's media is.
TONE = "data:audio/wav;base64," + base64.b64encode(tone_wav()).decode()

PLAIN = """<!doctype html><html><body><audio id=a src="@TONE@"></audio>
<script>document.getElementById('a').play();</script></body></html>"""

SESSION = """<!doctype html><html><head><script>
window.calls = [];
navigator.mediaSession.setActionHandler('play', () => {
  window.calls.push('play'); document.getElementById('a').play(); });
navigator.mediaSession.setActionHandler('pause', () => {
  window.calls.push('pause'); document.getElementById('a').pause(); });
navigator.mediaSession.setActionHandler('nexttrack', () => window.calls.push('nexttrack'));
</script></head><body><audio id=a src="@TONE@"></audio>
<script>document.getElementById('a').play();</script></body></html>"""

NOTHING = "<!doctype html><html><body><p>nothing plays here</p></body></html>"

PLAIN = PLAIN.replace("@TONE@", TONE)
SESSION = SESSION.replace("@TONE@", TONE)


def serve(pages, host="127.0.0.1"):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in pages:
                body, kind = pages[self.path].encode(), "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def end_to_end(base):
    """The shipped sender, a fake panel, and a Play/Pause up its return channel.

    The browser cases above call act_on_media() directly. This is the JOIN:
    the board's b"K" message through parse_messages, the loop's dispatch and
    the log line -- where a NameError would take the sender down on the first
    media key a car ever sent.
    """
    import socket  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def panel():
        conn, _ = listener.accept()
        conn.settimeout(0.2)
        started = time.monotonic()
        said = [False, False]
        while time.monotonic() - started < 20:
            try:
                if not conn.recv(1 << 16):
                    break
            except socket.timeout:
                pass
            except OSError:
                break
            # Once the page has had time to start its sound: pause, then play.
            if not said[0] and time.monotonic() - started > 8:
                conn.sendall(b"K\x0c\x00\xcd\x00")
                said[0] = True
            if not said[1] and time.monotonic() - started > 11:
                conn.sendall(b"K\x0c\x00\xcd\x00")
                said[1] = True
        conn.close()

    threading.Thread(target=panel, daemon=True).start()
    command = [sys.executable, "-u", str(ROOT / "components" / "portall" / "ha_send.py"),
               "--host", "127.0.0.1", "--port", str(listener.getsockname()[1]),
               "--url", f"{base}/plain", "--no-token", "--not-home-assistant",
               "--width", "320", "--height", "240", "--keyboard", "off", "--audio", "off"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(15)
    process.terminate()
    out = process.communicate(timeout=20)[0]
    lines = [line for line in out.splitlines() if line.startswith("Media: play/pause")]
    check(f"the shipped sender pauses on the board's Play/Pause ({lines[:1]})",
          bool(lines) and "-> pause" in lines[0])
    check("and plays on the next one", len(lines) >= 2 and "-> play" in lines[1])
    check("and goes on running", "Traceback" not in out)


def paused(frame):
    return frame.evaluate("() => document.getElementById('a').paused")


def at(frame):
    return frame.evaluate("() => document.getElementById('a').currentTime")


def main():
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    print("The wire and the control channel")
    got, _ = parse_messages(b"K\x0c\x00\xcd\x00K\x0c\x00\xb5\x00")
    check("a Consumer Play/Pause from the board is a media action",
          got == [("media", "playpause"), ("media", "nexttrack")])
    control = Control(io.StringIO("media playpause\nmedia nonsense\n"))
    control._thread.join(timeout=2)
    check("the add-on's 'media playpause' line is one too, and an unknown word is not",
          control.drain() == [("media", "playpause")])

    pages = {"/plain": PLAIN, "/session": SESSION, "/nothing": NOTHING}
    server = serve(pages)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    # Another origin for the frame, as a player embedded from elsewhere is:
    # a second server on 127.0.0.2. Added once its port is known; the
    # handler reads the dict per request.
    elsewhere = serve(pages, "127.0.0.2")
    pages["/framed"] = (f"<!doctype html><html><body><p>top</p><iframe "
                        f"src=http://127.0.0.2:{elsewhere.server_address[1]}/plain>"
                        f"</iframe></body></html>")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS)
        context = browser.new_context()
        context.add_init_script(MEDIA_CONTROL_JS)
        page = context.new_page()

        print("\nA page playing a sound, with no Media Session of its own")
        page.goto(f"{base}/plain")
        page.wait_for_function("() => !document.getElementById('a').paused")
        line = act_on_media(page, "playpause")
        check("play/pause pauses it", paused(page))
        check(f"and says how ({line!r})", "play/pause -> pause" in line and "video or audio" in line)
        line = act_on_media(page, "playpause")
        page.wait_for_timeout(200)
        check("pressed again, it plays", not paused(page))
        before = at(page)
        line = act_on_media(page, "nexttrack")
        check("next, with no list to move along, is ten seconds forward",
              at(page) >= before + 9 and "ten seconds forward" in line)
        before = at(page)
        act_on_media(page, "previoustrack")
        check("previous is ten seconds back", at(page) <= before - 9)
        act_on_media(page, "stop")
        check("stop pauses it", paused(page))

        print("\nA page that registered Media Session handlers")
        page.goto(f"{base}/session")
        page.wait_for_function("() => !document.getElementById('a').paused")
        line = act_on_media(page, "playpause")
        check("play/pause calls the SITE's own pause",
              page.evaluate("window.calls") == ["pause"] and paused(page))
        check(f"and says so ({line!r})", "site's own player" in line)
        before = at(page)
        line = act_on_media(page, "nexttrack")
        check("next is the site's next, not a seek",
              page.evaluate("window.calls")[-1] == "nexttrack" and abs(at(page) - before) < 1)

        print("\nA player in an iframe of another origin")
        page.goto(f"{base}/framed", wait_until="load")
        inner = page.frame(url=lambda u: u.endswith("/plain"))
        inner.wait_for_function(
            "() => { const a = document.getElementById('a'); return a && !a.paused; }")
        line = act_on_media(page, "playpause")
        check("the top page has nothing; the frame's player is paused", paused(inner))

        print("\nA page with nothing playing")
        page.goto(f"{base}/nothing")
        line = act_on_media(page, "pause")
        check(f"pause says nothing plays ({line!r})", "nothing is playing" in line)
        line = act_on_media(page, "play")
        check(f"and play says nothing can ({line!r})", "nothing on this page can" in line)

        print("\nWhy the HomeKit remote's play/pause did nothing before")
        refused = False
        try:
            page.keyboard.press("MediaPlayPause")
        except Exception as problem:  # noqa: BLE001
            refused = "Unknown key" in str(problem)
        check("Playwright refuses the browser key MediaPlayPause outright", refused)

        browser.close()

    print("\nThe whole way: board message, sender loop, page")
    end_to_end(base)
    server.shutdown()
    elsewhere.shutdown()
    print("\nAll good." if not faults else f"\n{len(faults)} problem(s).")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
