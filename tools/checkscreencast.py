#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
"""Keep capturing when a frame arrives inside a synchronous CDP acknowledgement.

Playwright dispatches events while CDPSession.send() waits for its response.
The browser has already accepted the acknowledgement when its replacement
frame arrives. If the sender still counts the acknowledged frame, it learns
a window larger than the browser's real window and then withholds every ACK.
The picture stops; a touch temporarily frees all slots, while media and sound
have never stopped. The browser below delivers events at that legal boundary.

    python3 tools/checkscreencast.py [--sender PATH]
    python3 tools/checkscreencast.py --browser /usr/bin/google-chrome
"""
import argparse
import base64
import importlib.util
import pathlib
import sys
import time
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.now = 1.0

    def __call__(self):
        return self.now

    def tick(self):
        self.now += 0.04


class Session:
    def __init__(self, capacity, inside_ack):
        self.capacity = capacity
        self.inside_ack = inside_ack
        self.outstanding = 0
        self.sid = 0
        self.frames = 0
        self.acks = 0
        self.moving = True
        self.running = False
        self.handler = None

    def on(self, event, handler):
        assert event == "Page.screencastFrame"
        self.handler = handler

    def fill(self):
        while self.running and self.moving and self.outstanding < self.capacity:
            self.outstanding += 1
            self.frames += 1
            self.handler({"sessionId": self.sid,
                          "data": base64.b64encode(str(self.frames).encode()).decode()})

    def send(self, method, params=None):
        if method == "Page.startScreencast":
            self.sid += 1
            self.outstanding = 0
            self.running = True
            self.fill()
        elif method == "Page.stopScreencast":
            self.running = False
        elif method == "Page.screencastFrameAck":
            if params["sessionId"] == self.sid:
                assert self.outstanding > 0, "an ACK for a frame already acknowledged"
                self.outstanding -= 1
                self.acks += 1
                if self.inside_ack:
                    # The event arrives before the command's response.
                    self.fill()
        elif method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"still").decode()}
        return {}


class Page:
    def __init__(self, session):
        self.session = session
        self.context = self

    def new_cdp_session(self, page):
        return self.session


def browser_check(sender, executable, check):
    """Real video/compositor/events; hold the ACK reply open for 45 ms.

    This controls event timing, not frame counts or the browser's real window.
    A canvas supplies a local video track, so no account or media download is
    needed. The page changes no visible DOM while the video is playing.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, args=sender.BROWSER_ARGS)
        try:
            page = browser.new_page(viewport={"width": 480, "height": 320})
            page.set_content('<body style="margin:0"><video id="video" autoplay muted playsinline '
                             'style="width:480px;height:320px"></video>')
            page.evaluate("""async () => {
                const canvas = document.createElement('canvas');
                canvas.width = 480; canvas.height = 320;
                const ctx = canvas.getContext('2d'); let n = 0;
                function draw() {
                    ctx.fillStyle = `rgb(${n++ % 200},50,120)`;
                    ctx.fillRect(0,0,480,320);
                    requestAnimationFrame(draw);
                }
                draw(); video.srcObject = canvas.captureStream(30);
                await video.play();
            }""")
            raw = page.context.new_cdp_session(page)

            class SlowReply:
                def on(self, *args):
                    return raw.on(*args)

                def send(self, method, params=None):
                    reply = raw.send(method, params)
                    if method == "Page.screencastFrameAck":
                        # Pump real events before returning control to request().
                        page.wait_for_timeout(45)
                    return reply

            cast = sender.Screencast(Page(SlowReply()), 480, 320, 90)
            page.wait_for_timeout(150)
            start = last = time.monotonic()
            received = 0
            pictures = set()
            while time.monotonic() - start < 4:
                page.wait_for_timeout(8)
                now = time.monotonic()
                if now - last < 1 / 15:
                    continue
                shot = cast.take()
                if shot is not None:
                    received += 1
                    pictures.add(shot)
                cast.request()
                last = now
            decoded = page.evaluate("video.getVideoPlaybackQuality().totalVideoFrames")
            check("real browser: video advances without a touch", decoded >= 60,
                  f"{decoded} video frames")
            check("real browser: changing pictures keep arriving", received >= 30 and len(pictures) >= 20,
                  f"{received} pictures, {len(pictures)} different")
            check("real browser: reentrant events do not inflate the window", cast._in_flight_seen <= 3,
                  f"learnt {cast._in_flight_seen}")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sender", type=pathlib.Path,
                        default=ROOT / "components" / "portall" / "ha_send.py")
    parser.add_argument("--browser", help="also reproduce ACK event interleaving in this Chrome/Chromium")
    args = parser.parse_args()
    sys.path.insert(0, str(args.sender.resolve().parent))
    spec = importlib.util.spec_from_file_location("sender_under_test", args.sender)
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    failures = 0

    def check(label, good, detail=""):
        nonlocal failures
        print(("  ok     " if good else "  ECHEC  ") + label + (f" ({detail})" if detail else ""))
        failures += not good

    for capacity in (1, 2, 3):
        for inside in (False, True):
            clock = Clock()
            session = Session(capacity, inside)
            with patch.object(sender.time, "monotonic", clock):
                cast = sender.Screencast(Page(session), 480, 320, 90)
                for _ in range(30):
                    clock.tick()
                    cast.take()
                    cast.request()
                    session.fill()  # events after the response, if not delivered inside it
                label = f"window {capacity}, frames {'inside' if inside else 'after'} ACK response"
                check(label + ": frames continue without touching", cast.produced >= 30,
                      f"{cast.produced} frames")
                check(label + ": the learnt window stays real", cast._in_flight_seen == capacity,
                      f"learnt {cast._in_flight_seen}")
                check(label + ": ledger matches the browser", len(cast._unacked) == session.outstanding)

                # A touch opens eager capture, then ordinary pacing takes over.
                cast.request(discard=True)
                session.fill()
                before = cast.produced
                for _ in range(10):
                    clock.tick()
                    cast.take()
                    cast.request()
                    session.fill()
                check(label + ": releasing a touch does not freeze capture",
                      cast.produced >= before + 10)

                # A still dashboard must remain still, without a screenshot loop.
                session.moving = False
                before = cast.produced
                for _ in range(30):
                    clock.tick()
                    cast.take()
                    cast.request()
                check(label + ": an idle page makes no new frames", cast.produced == before)
                check(label + ": catch-up stays bounded", cast.recaptured <= 1,
                      f"{cast.recaptured} screenshots")

                cast.pause()
                clock.tick()
                before = cast.produced
                check(label + ": sleeping stops capture", not session.running)
                session.moving = True
                cast.resume()
                for _ in range(10):
                    clock.tick()
                    cast.take()
                    cast.request()
                    session.fill()
                check(label + ": waking resumes capture", cast.produced >= before + 10)

    if args.browser:
        browser_check(sender, args.browser, check)
    print("all good" if not failures else f"{failures} failure(s)")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
