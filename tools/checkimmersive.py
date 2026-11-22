#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A video playing inside a link takes the whole screen -- seen from the panel.

WHY THIS EXISTS. Asked with a photograph of a panel on YouTube under the bar:
when a video plays on Jellyfin, Netflix, Orange TV or YouTube, the panel goes
full screen after a few seconds, without the bar -- except on a link used for
browsing, such as Google, which keeps it (`keep_bar: true` on that link,
--page-keep-bar to the sender).

The shipped ha_send.py runs against a fake panel that reassembles every
rectangle into a picture, and real contacts go up its return channel. The
pages each have their own colour, and every video is a canvas stream of one
colour with a small square moving in a corner, so it plays for as long as
the page lets it:

  - /film, a video filling its page with its sound on (Jellyfin, a
    television interface): the bar goes after a few seconds, comes back when
    a tap pauses the video, and stays while a finger was just there;
  - /desk, a player inside a page with the site's own controls drawn over it
    (YouTube's ordinary site): the player is put in full screen, overlays and
    all, and given back when the video pauses; the next video, at a new
    address in the same document, keeps it; and when the site's own button
    leaves full screen, that is respected while the video goes on playing;
  - /preview, a muted video (a hover preview, a camera tile): never;
  - /search, opened as a link that keeps its bar, from which a tap goes to
    /film: the bar stays the whole time.

--without runs the same sender with --page-keep-bar http, which keeps the bar
everywhere -- the panel as it was: every case about the bar going must then
fail. --picture FILE saves what the panel shows while /desk is in full screen.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
WITHOUT = "--without" in sys.argv
PICTURE = (sys.argv[sys.argv.index("--picture") + 1]
           if "--picture" in sys.argv else None)
W, H = 800, 480

HOME = (0x80, 0x20, 0x20)
VIDEO = (0x20, 0xA0, 0x60)
DESK = (0x70, 0x70, 0x70)
OSD = (0xD0, 0x20, 0x20)
SEARCH = (0xB0, 0xA0, 0x20)
STRIP = (24, 27, 34)
fails = 0
pushed = []

# One colour and a square moving down its left edge, so the stream keeps
# producing frames and the video keeps playing.
STREAM = (
    "const c=document.createElement('canvas');c.width=320;c.height=180;"
    "const g=c.getContext('2d');let n=0;"
    "setInterval(()=>{g.fillStyle='rgb%s';g.fillRect(0,0,320,180);"
    "g.fillStyle='#fff';g.fillRect(2,(n++*7)%%160,12,12);},50);"
    "const v=document.querySelector('video');v.srcObject=c.captureStream(20);"
    "v.play();" % (VIDEO,))

PAGES = {
    "home": f"<body style='margin:0;height:100vh;background:rgb{HOME}'>",
    # Click toggles the video, the way a site's player does.
    "film": ("<body style='margin:0;background:#000;overflow:hidden'>"
             "<video style='display:block;width:100vw;height:100vh;"
             "object-fit:fill'></video><script>" + STREAM +
             "addEventListener('click',()=>v.paused?v.play():v.pause());"
             "</script>"),
    "preview": ("<body style='margin:0;background:#000;overflow:hidden'>"
                "<video muted style='display:block;width:100vw;height:100vh;"
                "object-fit:fill'></video><script>" + STREAM + "</script>"),
    "desk": (f"<body style='margin:0;height:100vh;background:rgb{DESK}'>"
             "<div id=player style='position:absolute;left:40px;top:40px;"
             "width:480px;height:270px;background:#000'>"
             "<video style='width:100%;height:100%;object-fit:fill'></video>"
             f"<div id=osd style='position:absolute;left:0;bottom:0;"
             f"width:100%;height:24px;background:rgb{OSD}'></div></div>"
             "<script>" + STREAM +
             "v.addEventListener('click',()=>v.paused?v.play():v.pause());"
             # The site's own button: leaves full screen, the video plays on.
             "document.getElementById('osd').addEventListener('click',e=>{"
             "e.stopPropagation();document.fullscreenElement&&"
             "document.exitFullscreen();});"
             # The next video, the way YouTube moves to it: a new address in
             # the same document, the player and its full screen kept.
             "navigator.mediaSession.setActionHandler('nexttrack',()=>{"
             "history.pushState({},'','/desk?v=2');"
             "fetch('/pushed',{keepalive:true});});</script>"),
    "search": (f"<body style='margin:0;height:100vh;background:rgb{SEARCH}'>"
               "<script>addEventListener('click',()=>"
               "location.href='/film');</script>"),
}


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=18):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.strip("/").split("?")[0]
        if name == "pushed":
            pushed.append(1)
        body = ("<!doctype html>" + PAGES.get(name, PAGES["home"])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def addon():
    """From the form's links to the sender's line, through run.py's own code.

    The house's links and a screen's own launcher's alike: a screen with its
    own launcher takes its bar from its own links only, as it does its
    quality and its frame limit.
    """
    sys.path.insert(0, str(HERE / "portall"))
    import run
    house = [
        {"name": "Google", "url": "https://www.google.com/", "keep_bar": True},
        {"name": "YouTube", "url": "https://www.youtube.com/tv"},
        {"name": "Jellyfin", "url": "http://jellyfin:8096/", "keep_bar": False},
    ]
    own = [{"name": "Bing", "url": "https://www.bing.com/", "keep_bar": True},
           {"name": "Netflix", "url": "https://www.netflix.com/"}]
    shape = {"host": "1.2.3.4", "url": "http://x/", "width": 800,
             "height": 480}
    panels = [{"name": "salon", **shape},
              {"name": "cuisine", **shape, "launcher_links": own}]
    run.give_page_settings(panels, {"links": house})
    salon, cuisine = (run.command_for(panel) for panel in panels)

    def kept(argv):
        return [argv[i + 1] for i, word in enumerate(argv)
                if word == "--page-keep-bar"]
    print("The add-on:")
    check("a house link with keep_bar on keeps its bar",
          kept(salon) == ["https://www.google.com/"], str(kept(salon)))
    check("a screen with its own launcher keeps only its own links' bars",
          kept(cuisine) == ["https://www.bing.com/"], str(kept(cuisine)))


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    addon()
    from udisp_send import _HEADER, UDISP_TYPE_JPG

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"

    canvas = Image.new("RGB", (W, H))
    lock = threading.Lock()
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
                        pic = Image.open(io.BytesIO(payload)).convert("RGB")
                    except Exception:  # noqa: BLE001
                        continue
                    with lock:
                        canvas.paste(pic, (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    # Top right: the bar's ground when it is drawn, the page's otherwise, and
    # never the corner mark or a video's moving square.
    ground = (W - 3, 3)
    top = (W // 2, 3)
    middle = (W // 2, H // 2)

    def colour(at):
        with lock:
            return canvas.getpixel(at)

    def until(test, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.05)
        return False

    def bar_drawn():
        return near(colour(ground), STRIP)

    def holds(test, seconds):
        """True for the whole of `seconds`."""
        return not until(lambda: not test(), seconds)

    def tap(at):
        x, y = at
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    command = [sys.executable, "-u", str(SENDER_DIR / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control", "--page-keep-bar", f"{site}/search"]
    if BROWSER:
        command += ["--browser", BROWSER]
    if WITHOUT:
        command += ["--page-keep-bar", "http"]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def ask(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def said(words):
        return any(words in line for line in list(out))

    try:
        check("the panel's own page arrives",
              until(lambda: near(colour(middle), HOME), 40))

        print("A video filling its page, with its sound on:")
        ask(f"open {site}/film")
        check("it arrives under the bar",
              until(lambda: near(colour(middle), VIDEO) and bar_drawn(), 10),
              f"{colour(middle)} {colour(ground)}")
        check("the bar is still there two seconds in", holds(bar_drawn, 2.0))
        check("then it goes, and the video has the whole screen",
              until(lambda: near(colour(ground), VIDEO)
                    and near(colour(top), VIDEO), 4),
              f"{colour(ground)} {colour(top)}")
        check("and it stays gone while the video plays",
              holds(lambda: near(colour(ground), VIDEO), 3.0),
              str(colour(ground)))
        tap(middle)
        check("a tap pauses it and the bar comes back",
              until(bar_drawn, 4), str(colour(ground)))
        tap(middle)
        check("playing again, the bar stays while the finger was just there",
              holds(bar_drawn, 2.0), str(colour(ground)))
        check("and goes once it has been left alone",
              until(lambda: near(colour(ground), VIDEO), 5),
              str(colour(ground)))

        print("A player inside a page, with the site's own controls:")
        ask(f"open {site}/desk")
        check("it arrives under the bar, the page around the player",
              until(lambda: bar_drawn()
                    and near(colour((W - 40, H - 40)), DESK), 10),
              f"{colour(ground)} {colour((W - 40, H - 40))}")
        check("then the player is put in full screen",
              until(lambda: near(colour(ground), VIDEO)
                    and near(colour((W - 40, H // 2)), VIDEO), 6),
              f"{colour(ground)} {colour((W - 40, H // 2))}")
        check("with the site's controls still drawn over it",
              near(colour((W // 2, H - 6)), OSD), str(colour((W // 2, H - 6))))
        if PICTURE:
            with lock:
                canvas.save(PICTURE)
        tap(middle)
        check("a tap pauses it: full screen and the bar are given back",
              until(lambda: bar_drawn()
                    and near(colour((W - 40, H - 40)), DESK), 5),
              f"{colour(ground)} {colour((W - 40, H - 40))}")
        tap((40 + 240, 40 + 135))
        check("played again, it goes back to full screen",
              until(lambda: near(colour((W - 40, H // 2)), VIDEO), 8),
              str(colour((W - 40, H // 2))))
        ask("media nexttrack")
        check("the next video comes at a new address in the same page",
              until(lambda: bool(pushed), 5))
        check("and it stays in full screen, the bar still away",
              holds(lambda: near(colour((W - 40, H // 2)), VIDEO)
                    and not bar_drawn(), 3.0),
              f"{colour((W - 40, H // 2))} {colour(ground)}")
        tap((W // 2, H - 6))
        check("the site's own button leaves full screen, and the bar is back",
              until(lambda: bar_drawn()
                    and near(colour((W - 40, H - 40)), DESK), 5),
              f"{colour(ground)} {colour((W - 40, H - 40))}")
        check("and that is respected while the video plays on",
              holds(bar_drawn, 6.0), str(colour(ground)))

        print("A muted video:")
        ask(f"open {site}/preview")
        check("it arrives", until(lambda: near(colour(middle), VIDEO), 10))
        check("and keeps the bar", holds(bar_drawn, 6.0),
              str(colour(ground)))

        print("A link that keeps its bar, browsed from:")
        ask(f"open {site}/search")
        check("it arrives", until(lambda: near(colour(middle), SEARCH), 10))
        tap(middle)
        check("a video found from it plays",
              until(lambda: near(colour(middle), VIDEO), 10))
        check("and the bar stays above it", holds(bar_drawn, 6.0),
              str(colour(ground)))
        check("the log says why", said("keeps its bar (keep_bar)"))

        crashed = [line.strip() for line in out if "Traceback" in line]
        check("the sender did not crash", not crashed, str(crashed))
        lines = [line.strip() for line in out if line.startswith("Video:")]
        print("    " + "\n    ".join(lines[:12]))
    finally:
        process.kill()
        listener.close()
        server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
