#!/usr/bin/env python3
"""Put a Home Assistant dashboard on an ESPHome usb_display panel, over Wi-Fi.

The panel is not running Home Assistant and is not running a browser. A machine
that is on anyway -- the Home Assistant server itself, or anything beside it --
renders the dashboard in a browser with no window, and sends the picture to the
panel. Touches come back over the same socket and are replayed into that
browser, so the panel behaves like the screen of the machine doing the
rendering.

    ./ha_send.py --calibrate --host 192.168.1.9 --width 1024 --height 600
    ./ha_send.py --host 192.168.1.9 --width 1024 --height 600 \
        --url http://homeassistant.local:8123/lovelace/0 --token <long-lived> \
        --touch-rotate 0

Calibrate first, once per board. There is no way to know from here which way a
panel reports its contacts: it depends on how the touch controller is wired and
on the transform: the touch screen was configured with, and no two boards
agree -- a GT911 on one mirrors both axes, the same part on another swaps them,
a GSL3680 on a third mirrors one. ESPHome hands a listener that board's own
display coordinates, which is what LVGL wants and is not necessarily the
orientation the picture is being shown in. So --calibrate draws three targets,
asks for a tap on each, and prints the options that make the two agree. It needs
no browser and no token, so it is the first thing to run on a new panel.

What makes this affordable is that it does not send the picture. It sends the
part of the picture that changed. A dashboard at rest is a clock moving once a
minute against a background that never moves at all: a few kilobytes a second,
where mirroring a desktop costs upwards of a megabyte a second. That is the
difference between a panel on a battery and a panel on a cable.

The board has always been able to draw a rectangle rather than a screen -- the
protocol header carries x, y, width and height, and Espressif's own Windows
driver uses it that way. Every rectangle of one picture is sent with the same
frame identifier so the board admits or drops them together, and never shows
half an update.

Requirements:

    pip install playwright pillow numpy
    playwright install chromium

The token is a long-lived access token from your Home Assistant profile page,
at the bottom under "Long-lived access tokens". It is written into the
browser's local storage the way the frontend writes it after a login, which is
what lets a browser with no keyboard get past the login screen.
"""

import argparse
import base64
import io
import json
import os
import sys
import time

# udisp_send.py is next to this file and owns the wire format. Importing it
# rather than restating the header keeps one definition of the protocol.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udisp_send import build_header, build_heartbeat, connect_tcp  # noqa: E402

# The panel is divided into tiles and each is compared with the last picture.
# Small tiles find changes precisely and cost many rectangles; large ones cost
# few rectangles and send unchanged pixels along with the changed ones. 64 is a
# compromise that keeps a clock's worth of change down to one or two tiles.
TILE = 64
# What one rectangle costs the board on top of its pixels, as a fraction of a
# whole-panel decode. Measured: the panel decodes in 8.5 ms and each rectangle
# adds roughly 1.5 ms of fixed work on top of its share of that.
#
# It is the count that decides whether to give up and send the panel, not the
# area. Twenty scattered rectangles covering 45% cost the board 34 ms against
# 8.5 for the panel, so the panel wins; one rectangle covering 64% -- which is
# what two camera cards look like once the tiles between them join up -- costs
# 233 KiB against 272 for the panel and decodes in 6.9 ms against 8.5, so it
# wins on both counts. Judging by area alone got that case exactly backwards
# and sent a whole panel, every frame, to update two thirds of it.
RECT_COST_FRACTION = 0.18
# However little changes, redraw everything this often. A dropped rectangle --
# the board was busy, the socket hiccuped -- would otherwise stay wrong on the
# panel forever, because nothing would ever mark that area as changed again.
FULL_REDRAW_SECONDS = 30.0
# How long to let the browser run between looks. Short, because this is
# what bounds how stale a change can be before it is even noticed; not
# zero, because each look is a round trip into the browser.
PUMP_MS = 8
# How often to say "still here" when there is nothing to send. A sender that
# only transmits what changed is silent while nothing changes, and silence is
# indistinguishable from having died; the board's patience has to be longer
# than this, and is.
HEARTBEAT_S = 3.0
# Whether a frame that follows an input skips the rate limit.
#
# The limit exists to stop a busy page spending the whole link on frames
# nobody asked for. A frame that follows a press is the opposite: somebody
# just did something and is waiting to see it happen, and holding it back for
# the rest of the interval is the difference between a panel that feels
# connected to your finger and one that does not. Self-limiting, because it
# only fires as often as there is input.
URGENT_AFTER_INPUT = True
# How the browser is started.
#
# The defaults are written for a browser somebody is looking at, and this one
# is not: nothing is ever in the foreground, no window is ever focused, and a
# page can sit for hours with no input. Chromium reads that as a tab nobody
# wants and starts economising -- throttling timers, suspending media, pausing
# a video that has been playing to no one. On a dashboard with two camera cards
# that shows up as one camera live and the other stopped, coming back only when
# something makes the browser pay attention to it again.
BROWSER_ARGS = [
    "--hide-scrollbars",
    "--disable-gpu",
    # A video nobody clicked on is still a video that should play here.
    "--autoplay-policy=no-user-gesture-required",
    # Do not stop a stream because the page it is on is not in front.
    "--disable-background-media-suspend",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    # There is no window manager here to ask, and the answer it guesses is
    # "covered", which costs the page its updates.
    "--disable-features=CalculateNativeWinOcclusion",
]
# How far a finger has to travel before the gesture is scrolling rather than
# a tap. Small enough that a deliberate drag is recognised at once, large
# enough that the wobble of a fingertip on a press is not.
DRAG_THRESHOLD = 12


def install_token(context, url, token):
    """Write the token where the frontend expects to find it after a login.

    Home Assistant's frontend keeps its session in local storage under
    hassTokens. A long-lived token cannot be refreshed, so the expiry is set far
    enough out that it is never reached; the frontend checks it before deciding
    to refresh.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    tokens = {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 315360000,
        "hassUrl": origin,
        "clientId": None,
        "expires": int(time.time() * 1000) + 315360000000,
        "refresh_token": "",
    }
    context.add_init_script(
        f"window.localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
    )


def explain_unreachable(url, error):
    """Say something useful about a page that would not open.

    Playwright's own message names the failure and not the cause, and the
    likeliest cause here is a name that means something where the address was
    written and nothing where the browser is running. A remote-access name --
    Tailscale, Nabu Casa, a dynamic DNS host -- resolves on the network it
    belongs to; a container beside Home Assistant is not on it and does not
    need to be, because Home Assistant is right there.
    """
    from urllib.parse import urlsplit

    text = str(error)
    host = urlsplit(url).hostname or url
    if "ERR_NAME_NOT_RESOLVED" in text:
        print(f"\n{host} does not resolve from where this is running.")
        if os.path.exists("/data/options.json"):
            print(
                "This is running as a Home Assistant add-on, on the same "
                "machine as Home Assistant, so it should ask for it directly:\n"
                "    url: http://homeassistant:8123/lovelace/0\n"
                "That name exists inside every add-on. A remote-access address "
                "-- Tailscale, Nabu Casa, dynamic DNS -- is for reaching the "
                "house from outside and does not resolve in here."
            )
        else:
            print(
                "Use an address that resolves on this machine: the local "
                "hostname, or Home Assistant's IP and port."
            )
    elif "ERR_CONNECTION_REFUSED" in text:
        print(f"\nNothing is listening at {host}. Check the port.")
    elif "ERR_CERT" in text or "SSL" in text:
        print(
            f"\nThe certificate for {host} was refused. Inside the house, "
            "http:// and the plain port avoids the question entirely."
        )
    else:
        return
    print()


def changed_rectangles(previous, current, tile=TILE):
    """Where the two pictures differ, as few rectangles as reasonable.

    Tiles that differ are found first, then merged along each row, and rows that
    ended up with the same run merged down the columns -- a card that grew, a
    dialog that opened. One rectangle costs a header, a JPEG's own tables and a
    decode on the board, so a handful of large ones beats a crowd of small ones
    even when they carry a few unchanged pixels along.

    Returns (x, y, w, h) tuples in pixels.
    """
    import numpy as np

    height, width = current.shape[:2]
    tiles_x = (width + tile - 1) // tile
    tiles_y = (height + tile - 1) // tile

    # One pass over the whole picture rather than a comparison per tile: the
    # difference is a single vectorised operation, and the per-tile question is
    # then just whether its block of the answer holds anything.
    #
    # The colour axis is deliberately left alone. Reducing it away first --
    # np.any(..., axis=-1) -- reads every byte again along the one axis that is
    # not contiguous, and costs fifteen times what the comparison itself does:
    # 9.3 ms against 0.6 ms for a 1024x600 frame. Asking a three-dimensional
    # slice whether it holds anything answers the same question for nothing.
    differing = previous != current

    rectangles = []
    for ty in range(tiles_y):
        top = ty * tile
        bottom = min(top + tile, height)
        row = differing[top:bottom]
        run_start = None
        for tx in range(tiles_x):
            left = tx * tile
            right = min(left + tile, width)
            differs = bool(row[:, left:right].any())
            if differs and run_start is None:
                run_start = left
            elif not differs and run_start is not None:
                rectangles.append((run_start, top, left - run_start, bottom - top))
                run_start = None
        if run_start is not None:
            rectangles.append((run_start, top, width - run_start, bottom - top))

    # Stack rows that cover the same columns and touch. The rows are produced in
    # order, so the candidate is always the one just added.
    merged = []
    for x, y, w, h in rectangles:
        if merged:
            mx, my, mw, mh = merged[-1]
            if mx == x and mw == w and my + mh == y:
                merged[-1] = (mx, my, mw, mh + h)
                continue
        merged.append((x, y, w, h))
    return merged


class TouchMap:
    """Where a contact on the panel is on the page.

    There is no single answer, because the coordinates a board reports depend on
    how its touch controller is wired and on the transform: it was configured
    with -- and every panel differs. A GT911 on one board reports with both axes
    mirrored, the same part on another reports with the axes swapped, a GSL3680
    on a third mirrors only one. ESPHome hands each of them to a listener in
    that board's own display coordinates, which is what LVGL wants and is not
    necessarily the orientation the picture is being shown in.

    So rather than assume, this holds the whole family of possibilities -- four
    turns, each with or without a mirror -- and calibrate() picks the one that
    matches by asking for a few taps. Eight candidates covers every way a panel
    can be mounted and every transform: that can be written for it.
    """

    def __init__(self, page_w, page_h, panel_w, panel_h, rotate, mirror_x, mirror_y):
        self.page_w = page_w
        self.page_h = page_h
        self.panel_w = panel_w
        self.panel_h = panel_h
        self.rotate = rotate
        self.mirror_x = mirror_x
        self.mirror_y = mirror_y

    def to_page(self, px, py):
        # Normalised first, scaled back at the end. A board can report its
        # contacts on a range that is not the panel's -- the Tab5 does, because
        # its touch screen is told to swap the axes while the coordinates are
        # still scaled to a portrait display -- and a mapping that only turns
        # and mirrors cannot express that. Working in fractions of the panel
        # makes the ranges somebody else's problem, and is exactly the identity
        # when the two agree, which is every panel where they do.
        u = px / max(1, self.panel_w - 1)
        v = py / max(1, self.panel_h - 1)
        if self.mirror_x:
            u = 1.0 - u
        if self.mirror_y:
            v = 1.0 - v
        if self.rotate == 90:
            u, v = v, 1.0 - u
        elif self.rotate == 180:
            u, v = 1.0 - u, 1.0 - v
        elif self.rotate == 270:
            u, v = 1.0 - v, u
        x = int(round(u * (self.page_w - 1)))
        y = int(round(v * (self.page_h - 1)))
        # Clamp rather than skip: a contact on the very last column is a real
        # press, and the browser refuses a point outside the viewport.
        return max(0, min(self.page_w - 1, x)), max(0, min(self.page_h - 1, y))

    def options(self):
        """The command line that reproduces this, for pasting into a service."""
        parts = [f"--touch-rotate {self.rotate}"]
        if self.mirror_x:
            parts.append("--touch-mirror-x")
        if self.mirror_y:
            parts.append("--touch-mirror-y")
        return " ".join(parts)

    @staticmethod
    def candidates(page_w, page_h, panel_w, panel_h):
        """Every way this panel's coordinates could relate to the page's.

        Eight: four turns, each with or without a mirror. Mirroring both axes
        is the same map as turning half way round, so mirror_y is left out of
        the search -- it stays accepted on the command line, because a mapping
        already written down should keep working.

        The shapes are not used to narrow this down. They used to be, on the
        reasoning that a quarter turn swaps the axes and so only turns of a
        matching shape are possible -- true of the picture, and not true of the
        coordinates a board reports, which can be on ranges of their own.
        """
        for rotate in (0, 90, 180, 270):
            for mirror_x in (False, True):
                yield TouchMap(
                    page_w, page_h, panel_w, panel_h, rotate, mirror_x, False
                )


def send_picture(endpoint, image, frame_id, transpose, panel_w, panel_h, quality):
    """One whole-panel picture, turned the way the frames are."""
    if transpose is not None:
        image = image.transpose(transpose)
    if image.size != (panel_w, panel_h):
        image = image.resize((panel_w, panel_h))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    payload = buffer.getvalue()
    endpoint.write(build_header(panel_w, panel_h, len(payload), frame_id) + payload)


def _target_picture(page_w, page_h, tx, ty, step, total):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (page_w, page_h), (16, 16, 20))
    draw = ImageDraw.Draw(image)
    radius = max(12, min(page_w, page_h) // 16)
    for r, colour in ((radius, (240, 240, 240)), (radius // 2, (220, 60, 60))):
        draw.ellipse((tx - r, ty - r, tx + r, ty + r), outline=colour, width=4)
    draw.line((tx - radius * 2, ty, tx + radius * 2, ty), fill=(240, 240, 240))
    draw.line((tx, ty - radius * 2, tx, ty + radius * 2), fill=(240, 240, 240))
    draw.text(
        (page_w // 2 - 90, page_h - 40),
        f"Touch the circle  ({step} of {total})",
        fill=(200, 200, 200),
    )
    return image


def calibrate(endpoint, page_w, page_h, panel_w, panel_h, transpose, quality):
    """Ask for a few taps and work out which way the contacts come in.

    Three targets, deliberately off-centre and not in a line: a point in the
    middle is fixed by half the candidates at once, and three points that are
    collinear cannot separate a mirror from a turn. Each target is drawn by this
    script and sent as a whole picture, so calibration works before the browser
    is involved at all.
    """
    samples = []
    frame_id = 0
    targets = [(0.25, 0.22), (0.78, 0.30), (0.30, 0.80)]
    for step, (fx, fy) in enumerate(targets, start=1):
        tx, ty = int(fx * page_w), int(fy * page_h)

        # Drain before the target is shown, never after. Anything already in
        # flight belongs to the previous target or to a finger that had not
        # lifted; anything that arrives once the circle is up is the answer, and
        # throwing that away would leave this waiting for a tap that was made.
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            endpoint.read_touches()
            time.sleep(0.05)

        send_picture(
            endpoint,
            _target_picture(page_w, page_h, tx, ty, step, len(targets)),
            frame_id,
            transpose,
            panel_w,
            panel_h,
            quality,
        )
        frame_id = (frame_id + 1) & 0x3FF

        print(f"  target {step} of {len(targets)}: touch the circle on the panel")
        contact = None
        while contact is None:
            for contacts in endpoint.read_touches():
                if contacts:
                    contact = contacts[0]
                    break
            time.sleep(0.02)
        _, px, py = contact
        print(f"    got {px},{py}")
        samples.append(((tx, ty), (px, py)))

        # Wait for the finger to leave, so the next target does not read it.
        lifted = False
        while not lifted:
            for contacts in endpoint.read_touches():
                if not contacts:
                    lifted = True
            time.sleep(0.02)

    scored = []
    for candidate in TouchMap.candidates(page_w, page_h, panel_w, panel_h):
        error = 0
        for (tx, ty), (px, py) in samples:
            x, y = candidate.to_page(px, py)
            error += abs(x - tx) + abs(y - ty)
        scored.append((error / len(samples), candidate))
    scored.sort(key=lambda pair: pair[0])

    best_error, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else float("inf")
    print(f"\nBest match: {best.options()}  (average miss {best_error:.0f} px)")
    if best_error > min(page_w, page_h) * 0.15:
        print(
            "That is a long way off. Check --width and --height match the "
            "component, and that the taps landed on the circles."
        )
    elif runner_up - best_error < best_error:
        print(
            "The next candidate is nearly as good, so this is a guess. Run the "
            "calibration again, taking care to hit the middle of each circle."
        )
    print("Add those options to the command line to keep this.\n")
    return best


class Screencast:
    """Frames pushed by the browser, rather than pulled out of it.

    Page.captureScreenshot forces a full paint, a compose and an encode every
    time it is called, whether or not anything moved -- and on a live dashboard
    it is slow enough to fall behind and eventually fail outright with "Unable to
    capture screenshot". Chromium's screencast is the other way round: it hands
    over a frame when the page actually changes and stays silent when it does
    not, which is exactly the question this sender is asking.

    Frames must be acknowledged or the stream stops after a handful. The
    acknowledgement is deliberately not sent from the event handler -- that runs
    inside Playwright's own dispatch -- but from the loop, right after pumping.
    """

    def __init__(self, page, width, height):
        self._session = page.context.new_cdp_session(page)
        self._width = width
        self._height = height
        self._latest = None
        self._unacked = []
        self._session.on("Page.screencastFrame", self._on_frame)
        self._running = True
        self._start()

    def _start(self):
        self._session.send(
            "Page.startScreencast",
            {
                # Lossless: a JPEG here would make every tile differ from the
                # last by a pixel or two of ringing, and nothing would ever look
                # unchanged.
                "format": "png",
                "maxWidth": self._width,
                "maxHeight": self._height,
                "everyNthFrame": 1,
            },
        )

    def _on_frame(self, params):
        self._latest = base64.b64decode(params["data"])
        self._unacked.append(params["sessionId"])

    def take(self):
        """The newest frame since the last call, or None if nothing moved."""
        for session_id in self._unacked:
            try:
                self._session.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception:  # noqa: BLE001 - a closed page is handled by the caller
                pass
        self._unacked.clear()
        frame, self._latest = self._latest, None
        return frame

    def pause(self):
        """Stop the browser producing frames at all."""
        if not self._running:
            return
        self._running = False
        self._latest = None
        self._unacked.clear()
        try:
            self._session.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001 - a closed page needs no stopping
            pass

    def resume(self):
        if self._running:
            return
        self._running = True
        self._start()

    def stop(self):
        self.pause()


class Injector:
    """Replays the panel's contacts into the browser.

    One pointer, because a dashboard is a list of things to press and the second
    finger has nothing to do.

    A finger does two things a mouse does not do with one button: it taps, and
    it drags to scroll. Which one it was is only known once it has moved, so the
    press is held back until the finger lifts. A gesture that stayed put becomes
    a click; one that travelled becomes scrolling, by the distance it
    travelled -- drag the content down and the page goes up, the way it does on
    anything with a touch screen. Sending the press immediately instead would
    turn every scroll into a click on whatever was under the finger when it
    landed.
    """

    def __init__(self, page, touch_map):
        self._page = page
        self._map = touch_map
        # Where the finger landed, and where it was last seen. None between
        # gestures.
        self._start = None
        self._last = None
        self._scrolling = False

    def handle(self, reports):
        # Every report, in order. Collapsing a run of them down to its last
        # position would be cheaper but wrong: the first position of a run is
        # where the finger landed and the rest is how far it travelled, and a
        # gesture is exactly the difference between the two. Nothing is spent on
        # this -- the browser is only called when the finger lands, moves or
        # lifts, and the board already stops repeating a finger that is holding
        # still.
        for contacts in reports:
            point = None if not contacts else contacts[0][1:]
            if point is None:
                self._finish()
                continue
            x, y = self._map.to_page(*point)
            if self._start is None:
                # The finger has just landed. Put the pointer there so a scroll
                # goes to whatever is under it -- a dashboard has panes that
                # scroll on their own -- but do not press yet.
                self._page.mouse.move(x, y)
                self._start = self._last = (x, y)
                self._scrolling = False
                continue
            if not self._scrolling and (
                abs(x - self._start[0]) + abs(y - self._start[1]) >= DRAG_THRESHOLD
            ):
                self._scrolling = True
            if self._scrolling:
                dx, dy = x - self._last[0], y - self._last[1]
                if dx or dy:
                    # Negated: dragging the content downwards means going up the
                    # page, which is a negative wheel.
                    self._page.mouse.wheel(-dx, -dy)
            self._last = (x, y)

    def _finish(self):
        """The finger left. A gesture that never travelled was a tap."""
        if self._start is not None and not self._scrolling:
            self._page.mouse.move(*self._last)
            self._page.mouse.down()
            self._page.mouse.up()
        self._start = self._last = None
        self._scrolling = False

    def release(self):
        # A lost connection is not a tap: drop the gesture rather than clicking
        # wherever the finger happened to be.
        self._start = self._last = None
        self._scrolling = False


def main():
    parser = argparse.ArgumentParser(
        description="Render a Home Assistant dashboard onto a usb_display panel"
    )
    parser.add_argument(
        "--host", required=True, help="the panel's address (its port: option)"
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--url", help="the dashboard to render. Not needed with --calibrate"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HA_TOKEN"),
        help="a Home Assistant long-lived access token, or $HA_TOKEN",
    )
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument(
        "--rotate",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="turn the picture clockwise before sending, for a panel that is "
        "not mounted the right way up. Touches are turned back the other way, "
        "so the two stay in agreement. Leave at 0 if the component's rotation: "
        "option is already doing this",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="find out which way this panel reports its contacts, by drawing "
        "three targets and asking for a tap on each. Prints the options to add. "
        "No browser and no token needed -- run it once per board",
    )
    parser.add_argument(
        "--touch-rotate",
        type=int,
        choices=(0, 90, 180, 270),
        help="how far to turn the contacts back, when that is not --rotate. "
        "Every panel reports differently -- it depends on how the controller is "
        "wired and on the transform: it was given -- so take this from "
        "--calibrate rather than guessing",
    )
    parser.add_argument(
        "--touch-mirror-x",
        action="store_true",
        help="the panel reports x the other way round. From --calibrate",
    )
    parser.add_argument(
        "--touch-mirror-y",
        action="store_true",
        help="the panel reports y the other way round. From --calibrate",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=10.0,
        help="upper bound on how often a change is acted on. The browser only "
        "hands over a frame when the page moves, so a still dashboard costs "
        "nothing whatever this is set to",
    )
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality, 1..95")
    parser.add_argument(
        "--no-touch", action="store_true", help="do not replay the panel's contacts"
    )
    parser.add_argument(
        "--show-touches", action="store_true", help="print the contacts as they arrive"
    )
    parser.add_argument(
        "--stats", action="store_true", help="print what is being sent every 5 seconds"
    )
    parser.add_argument(
        "--show-changes",
        action="store_true",
        help="with --stats, also name the busiest parts of the screen. Use it "
        "to find the card that never stops redrawing",
    )
    args = parser.parse_args()

    if not args.calibrate:
        if not args.url:
            parser.error("--url is required")
        # Checked here rather than left to the browser, which reports a
        # malformed address as a bare navigation failure with the reason
        # buried in a stack trace. The common way to get one is copying a
        # documented line whole -- label included -- into a field that wanted
        # only the value.
        if not args.url.startswith(("http://", "https://")):
            parser.error(
                f"--url must begin with http:// or https://, and this one is "
                f"{args.url!r}. If it starts with something like 'url: ', that "
                f"is the name of the setting and not part of the address: the "
                f"value alone is what goes in."
            )
        if not args.token:
            parser.error("--token is required (or set HA_TOKEN)")

    try:
        from PIL import Image
    except ImportError as err:
        raise SystemExit(f"{err}. Install it: pip install pillow") from err

    # The browser renders at the panel's shape. A quarter turn means the page is
    # taller than it is wide, and the panel is the other way round.
    page_w, page_h = args.width, args.height
    if args.rotate in (90, 270):
        page_w, page_h = args.height, args.width

    transposes = getattr(Image, "Transpose", Image)
    transpose = {
        0: None,
        90: transposes.ROTATE_270,
        180: transposes.ROTATE_180,
        270: transposes.ROTATE_90,
    }[args.rotate]

    # Calibration draws its own targets, so it needs neither a browser nor a
    # token -- which is what makes it the first thing to run on a new board.
    if args.calibrate:
        endpoint = connect_tcp(args.host, args.port)
        try:
            calibrate(
                endpoint, page_w, page_h, args.width, args.height, transpose,
                args.quality,
            )
        finally:
            endpoint.close()
        return

    try:
        import numpy as np
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies:\n"
            "    pip install playwright pillow numpy\n"
            "    playwright install chromium"
        ) from err

    interval = 1.0 / args.fps if args.fps > 0 else 0.0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=BROWSER_ARGS)
        context = browser.new_context(
            viewport={"width": page_w, "height": page_h}, device_scale_factor=1
        )
        install_token(context, args.url, args.token)
        page = context.new_page()
        print(f"Opening {args.url} at {page_w}x{page_h}")
        # Not networkidle: the frontend holds a websocket open for as long as it
        # runs, and waiting for the network to go quiet would wait forever.
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as err:  # noqa: BLE001 - re-raised after the diagnosis
            explain_unreachable(args.url, err)
            raise
        try:
            page.wait_for_selector("home-assistant", timeout=30000)
        except Exception:  # noqa: BLE001 - a page without it is still worth sending
            print("Warning: this does not look like a Home Assistant page")
        # The dashboard paints in stages -- shell, then cards, then their data --
        # and the first picture is the one full redraw everything else is a
        # difference from. Let it settle before taking it.
        page.wait_for_timeout(3000)
        if "/auth/authorize" in page.url:
            print(
                "Warning: Home Assistant is asking to log in, so the token was "
                "not accepted. Check --url points at the same address the token "
                "was created on, scheme and port included."
            )

        touch_map = TouchMap(
            page_w,
            page_h,
            args.width,
            args.height,
            args.rotate if args.touch_rotate is None else args.touch_rotate,
            args.touch_mirror_x,
            args.touch_mirror_y,
        )
        injector = None if args.no_touch else Injector(page, touch_map)
        capture = Screencast(page, page_w, page_h)
        frame_id = 0
        # The newest rendering, kept across connections: a panel that comes back
        # needs a whole picture, and the browser will not send another frame
        # until something on the page moves.
        image = None
        current = None
        # How often each tile has been in a rectangle, for --show-changes.
        heat = np.zeros(
            ((args.height + TILE - 1) // TILE, (args.width + TILE - 1) // TILE),
            dtype=np.int32,
        )

        # One pass per connection: losing the panel waits for it to come back
        # rather than ending, so this can be left running as a service.
        while True:
            endpoint = connect_tcp(args.host, args.port)
            previous = None
            last_full = 0.0
            rectangles_sent = 0
            bytes_sent = 0
            pictures = 0
            loops = 0
            pending = None
            last_send = 0.0
            last_sent = time.monotonic()
            # Assumed awake until the panel says otherwise; it announces its
            # state as soon as a sender connects, so this is only the first
            # instant.
            awake = True
            # Set when a press has just been replayed into the page, and
            # handed to the next frame the browser produces -- that is the one
            # showing its effect, and the one that should not wait its turn.
            urgent = False
            pending_urgent = False
            stats_at = time.monotonic()
            try:
                while True:
                    # Pump the browser's events -- this is the only place the
                    # screencast frames arrive -- on a short beat, not on the
                    # frame interval. Waiting a whole interval before looking
                    # means a change that landed a millisecond after the last
                    # look sits untouched for the rest of it, which is most of
                    # what "the animation is sluggish" is made of. The interval
                    # still caps how often anything is sent, below.
                    page.wait_for_timeout(PUMP_MS)
                    started = time.monotonic()

                    frame = capture.take()
                    if frame is not None:
                        pending = frame
                        # The free pass belongs to the first frame produced
                        # after the input, not to whichever one happened to be
                        # waiting when it arrived -- that one was rendered
                        # before the press and shows nothing of it.
                        pending_urgent, urgent = urgent, False
                    # Hold the newest frame back until the send rate allows it.
                    # Nothing is lost by waiting: take() only ever returns the
                    # latest, so a frame held here is replaced rather than
                    # queued.
                    shot = None
                    if pending is not None and (
                        started - last_send >= interval or pending_urgent
                    ):
                        shot, pending = pending, None
                        last_send = started
                        pending_urgent = False

                    if shot is not None:
                        image = Image.open(io.BytesIO(shot)).convert("RGB")
                        if transpose is not None:
                            image = image.transpose(transpose)
                        if image.size != (args.width, args.height):
                            image = image.resize(
                                (args.width, args.height), Image.BILINEAR
                            )
                        current = np.asarray(image)

                    stale = started - last_full >= FULL_REDRAW_SECONDS
                    # Nothing to send until the browser has produced something.
                    # After that, a new frame is a reason to send, and so is a
                    # panel that has just reconnected and knows nothing. A
                    # sleeping one is never a reason: only the heartbeat below
                    # goes out, which is enough to hold the connection open.
                    if awake and image is not None and (
                        shot is not None or previous is None or stale
                    ):
                        # Everything, when there is nothing to compare against
                        # yet, and once in a while regardless: a rectangle lost
                        # to a busy board or a hiccuping socket would otherwise
                        # stay wrong forever, because nothing would ever mark
                        # that area as changed again.
                        if previous is None or stale:
                            rectangles = [(0, 0, args.width, args.height)]
                            last_full = started
                        else:
                            rectangles = changed_rectangles(previous, current)
                            covered = sum(w * h for _, _, w, h in rectangles)
                            # Redraw everything only when doing so is actually
                            # cheaper than the pieces.
                            panel = args.width * args.height
                            if covered / panel + RECT_COST_FRACTION * len(
                                rectangles
                            ) > 1.0:
                                rectangles = [(0, 0, args.width, args.height)]
                                last_full = started

                        for x, y, w, h in rectangles:
                            buffer = io.BytesIO()
                            image.crop((x, y, x + w, y + h)).save(
                                buffer, format="JPEG", quality=args.quality
                            )
                            payload = buffer.getvalue()
                            endpoint.write(
                                build_header(w, h, len(payload), frame_id, x, y)
                                + payload
                            )
                            rectangles_sent += 1
                            bytes_sent += len(payload)

                        if args.show_changes:
                            for x, y, w, h in rectangles:
                                heat[y // TILE : (y + h) // TILE,
                                     x // TILE : (x + w) // TILE] += 1

                        if rectangles:
                            previous = current
                            pictures += 1
                            # One identifier per picture, so the board's rate
                            # limit decides about the picture and not about each
                            # of its rectangles.
                            frame_id = (frame_id + 1) & 0x3FF
                        last_sent = started
                    elif started - last_sent >= HEARTBEAT_S:
                        endpoint.write(build_heartbeat())
                        last_sent = started

                    reports = []
                    for kind, body in endpoint.read_messages():
                        if kind == "touch":
                            reports.append(body)
                            continue
                        # The panel went dark or came back. Rendering for a
                        # screen nobody can see costs the server, the network
                        # and the board alike, so stop at the source: the
                        # browser is told to stop producing frames at all.
                        if body != awake:
                            awake = body
                            print("Panel " + ("awake" if awake else "asleep"))
                            if awake:
                                capture.resume()
                                # It has been showing nothing; whatever it had
                                # is no longer what should be there.
                                previous = None
                                if injector is not None:
                                    injector.release()
                            else:
                                capture.pause()
                    if args.show_touches and reports:
                        # With the time on them, because "the panel reacts
                        # slowly" has two very different causes and this tells
                        # them apart: a stamp that appears the moment the finger
                        # lands means the board and the network are fine and the
                        # wait is the page reacting, and one that appears late
                        # means the report itself was late.
                        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() % 1 * 1000):03d}"
                        for contacts in reports:
                            if contacts:
                                joined = ", ".join(
                                    f"#{i} at {x},{y}" for i, x, y in contacts
                                )
                                print(f"[{stamp}] touch {joined}")
                            else:
                                print(f"[{stamp}] touch released")
                    if injector is not None and reports:
                        injector.handle(reports)
                        if URGENT_AFTER_INPUT:
                            urgent = True
                        if args.show_touches:
                            print(
                                f"[{time.strftime('%H:%M:%S')}"
                                f".{int(time.time() % 1 * 1000):03d}] injected"
                            )

                    loops += 1
                    now = time.monotonic()
                    if args.stats and now - stats_at >= 5.0:
                        elapsed = now - stats_at
                        print(
                            f"{pictures / elapsed:.1f} pictures/s, "
                            f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                            f"{bytes_sent / elapsed / 1024:.1f} KiB/s, "
                            # How often touches are looked at. Anything much
                            # below --fps means the loop is the bottleneck, and
                            # a press waits that long before it is even read.
                            f"loop {loops / elapsed:.1f} Hz"
                        )
                        if args.show_changes and heat.any():
                            # Where the traffic is coming from. A dashboard that
                            # costs hundreds of kilobytes a second has something
                            # on it that never stops moving -- a graph, a camera
                            # tile, a spinner -- and the only way to find it is
                            # to be told which part of the screen it is on.
                            order = np.argsort(heat, axis=None)[::-1][:5]
                            spots = []
                            for flat in order:
                                ty, tx = divmod(int(flat), heat.shape[1])
                                if heat[ty, tx] == 0:
                                    break
                                spots.append(
                                    f"{tx * TILE},{ty * TILE} x{int(heat[ty, tx])}"
                                )
                            print("  busiest areas: " + "; ".join(spots))
                            heat[:] = 0
                        rectangles_sent = bytes_sent = pictures = loops = 0
                        stats_at = now
            except OSError as err:
                print(f"Lost the panel ({err}), waiting for it to come back")
                if injector is not None:
                    injector.release()
            finally:
                endpoint.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
