#!/usr/bin/env python3
"""Put a web page on an ESPHome usb_display panel, over Wi-Fi.

The panel is not running a browser. A machine that is on anyway -- the Home
Assistant server itself, or anything beside it -- renders the page in a browser
with no window, and sends the picture to the panel. Touches come back over the
same socket and are replayed into that browser, so the panel behaves like the
screen of the machine doing the rendering.

A Home Assistant dashboard is what this was written for and what --token is
for: a browser with no keyboard cannot get past a login screen, so the token is
written into storage the way the frontend writes it after one. Nothing else
about this is particular to Home Assistant. Leave --token out and any page at
all is rendered, diffed and sent by the same code.

    ./ha_send.py --calibrate --host 192.168.1.9 --width 1024 --height 600
    ./ha_send.py --host 192.168.1.9 --width 1024 --height 600 \
        --url http://homeassistant.local:8123/lovelace/0 --token <long-lived> \
        --touch-rotate 0
    ./ha_send.py --host 192.168.1.9 --width 1024 --height 600 \
        --url https://example.com/ --touch-rotate 0

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
# The two measurements it is made of, kept apart so it can be worked out for
# a panel rather than assumed. A rectangle adds a fixed 1.5 ms on the board --
# its header, its own JPEG tables, one more DMA transfer set up -- and a
# whole-panel decode took 8.5 ms on the 1024x600 it was measured on, which is
# 0.6144 megapixels.
RECT_FIXED_MS = 1.5
PANEL_DECODE_MS_PER_MPX = 8.5 / 0.6144


def rect_cost_fraction(width, height):
    """What one rectangle costs, as a fraction of redrawing the whole panel.

    This has to be computed and cannot be a constant, because it is a ratio and
    only the numerator is fixed: a whole-panel decode grows with the pixels. At
    1024x600 it comes to 0.176, which is the 0.18 that was measured there and
    hard-coded, so nothing changes on that panel. At 800x1280 -- two thirds
    again as many pixels -- it comes to 0.106.

    Leaving 0.18 on a larger panel is not a small error. The rule gives up on
    rectangles when coverage + fraction x count exceeds one, so at 0.18 six
    rectangles alone exceed it: the whole panel goes out however little of it
    changed. Measured on an 800x1280 dashboard with a camera on it, 17 pictures
    out of 18 in a five second window were whole panels of 132 KiB. At 0.106
    the same rule holds out to nine rectangles and weighs the area again.
    """
    return RECT_FIXED_MS / (PANEL_DECODE_MS_PER_MPX * width * height / 1e6)
# No rectangle narrower or shorter than this.
#
# The P4's JPEG decoder is a DMA engine working in 16x16 units, and a sliver
# stalls it: a 32x128 strip comes back as ESP_ERR_TIMEOUT rather than as
# pixels. Slivers are not rare -- they are the panel's own edge, wherever its
# size is not a multiple of the tile: 800 is twelve tiles of 64 and a
# remainder of 32, so the rightmost column of every picture was one. Nothing
# was ever drawn there, which is why the edge went stale and stayed stale.
#
# Growing such a rectangle backwards costs a few pixels sent twice and fixes
# it outright.
MIN_RECT = 64
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
# How long the frame limit is lifted for after a press, and how far.
#
# One free frame is enough to watch a button go down. It is not enough for what
# a press usually starts. Changing dashboard repaints the whole page over about
# a second, and at four frames a second that arrives as four pictures, which
# looks like a slideshow -- reported from a real panel as "it drags between the
# dashboards", and the frame limit was the cause. The limit exists to stop a
# page that never settles spending the link on frames nobody asked for, and for
# a second after a press exactly the opposite is true.
#
# A window that raises the rate rather than one that removes the limit -- but
# not for the reason it first seemed. The link has room: the board's radio was
# measured above 25 Mbit/s serving a camera, and the inbound side, which is the
# one that matters here, is bounded by the 28800-byte receive window over the
# round trip, so 23 Mbit/s at a 10 ms RTT and more on a quiet network. A
# transition peaked at 6.2.
#
# What actually binds during a transition is this machine. Nearly every picture
# then is a whole panel, and decoding, diffing and re-encoding one at 800x1280
# takes around 40 ms: the loop falls from 62 Hz to 43 and the sender tops out
# near seven pictures a second whatever it is allowed. Fifteen therefore does
# not bind there at all. What it stops is the other case -- a small cheap
# change being sent sixty times a second because a finger touched the panel.
# Defaults, overridable: a panel reported that a standing 10 was slow against
# 30 even with the window, and it was right -- fifteen for one second is less
# than the machine can do and less than the thing being watched needs. A
# transition, a scroll settling, a card opening: all of them run longer than a
# second, and the sender was measured serving 21 pictures a second on that
# panel. So the window now goes to what the browser and the machine will give,
# for as long as the movement lasts, and the standing limit is left free to be
# low -- which is the whole point of having two numbers instead of one.
URGENT_WINDOW_S = 2.0
URGENT_FPS = 30.0
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

    # Widen anything the decoder would choke on, backwards so it stays inside
    # the panel. A panel smaller than the minimum keeps whatever it has.
    grown = []
    for x, y, w, h in merged:
        if w < MIN_RECT and width >= MIN_RECT:
            x, w = min(x, width - MIN_RECT), MIN_RECT
        if h < MIN_RECT and height >= MIN_RECT:
            y, h = min(y, height - MIN_RECT), MIN_RECT
        grown.append((x, y, w, h))
    return grown


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

    def __init__(self, page, width, height, quality):
        self._session = page.context.new_cdp_session(page)
        self._width = width
        self._height = height
        self._quality = quality
        self._latest = None
        self._unacked = []
        # How many pictures the browser has actually made. The count that
        # matters is this one against the count sent: the gap between them is
        # work paid for and thrown away, and it is invisible from every other
        # number this prints.
        self.produced = 0
        self._session.on("Page.screencastFrame", self._on_frame)
        self._running = True
        self._start()

    def _start(self):
        # JPEG, and this is where most of the server's CPU went before.
        #
        # PNG was chosen on the belief that a JPEG would make every tile differ
        # from the last by a pixel or two of ringing, so nothing would ever look
        # unchanged. That is not what happens. JPEG is a block transform: an 8x8
        # block whose pixels went in identical comes out identical, because the
        # coefficients are the same numbers. Ringing is deterministic, not
        # noise. Measured on a dashboard-shaped 1024x600 picture with one clock
        # digit changed, at every quality from 60 to 95 and with and without
        # chroma subsampling: exactly the one tile holding the digit differed,
        # never another. Subsampling widens the unit to a 16x16 MCU, and the
        # tile is a multiple of 16, so the spread stays inside the tile that
        # already changed.
        #
        # What PNG did cost was real. Decoding it here is the half that was
        # measured in this process: 7.2 ms a frame against 2.2 for JPEG, plus
        # 1.8 more because a PNG arrives needing a convert() that a JPEG does
        # not. The browser's own encode is the larger half and is inferred
        # rather than measured -- the same 1024x600 picture costs libpng 22.5 ms
        # and libjpeg 1.6 through Pillow, and Chromium uses those same two
        # libraries on the same pixels. Call the whole of it twenty-odd
        # milliseconds of a core per frame, spent on a distinction the panel
        # cannot show.
        options = {
            "maxWidth": self._width,
            "maxHeight": self._height,
            "everyNthFrame": 1,
        }
        if self._quality:
            options["format"] = "jpeg"
            options["quality"] = self._quality
        else:
            options["format"] = "png"
        self._session.send("Page.startScreencast", options)

    def _on_frame(self, params):
        self._latest = base64.b64decode(params["data"])
        self._unacked.append(params["sessionId"])
        self.produced += 1

    def take(self):
        """The newest frame since the last call, or None if nothing moved."""
        frame, self._latest = self._latest, None
        return frame

    def request(self, discard=False):
        """Let the browser produce another frame.

        The acknowledgement is not a formality: it is the flow control. Chromium
        keeps about three frames in flight and then waits to be told they
        arrived, so acknowledging on every turn of the loop -- a hundred and
        twenty times a second -- asks it to paint and encode at its own full
        rate, and the frame limit then throws the surplus away once it has
        already been paid for. Measured on an animated page at 800x1280: 59.8
        frames a second produced when acknowledging freely, 28.5 when
        acknowledging at ten a second and 12.0 at four. The pictures that stop
        being made are exactly the ones that were being discarded.

        discard drops whatever is already in hand along with the
        acknowledgement. That is for the moment a press has just been replayed:
        a frame waiting at that instant was painted before the finger landed
        and shows nothing of it, so letting it through would spend the free
        pass on the wrong picture -- the mistake that once made a press feel
        205 ms slow instead of 105. Nothing is lost by dropping it, because a
        picture that is not sent does not advance what the next difference is
        measured against.
        """
        if discard:
            self._latest = None
        for session_id in self._unacked:
            try:
                self._session.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception:  # noqa: BLE001 - a closed page is handled by the caller
                pass
        self._unacked.clear()

    def freeze_animations(self):
        """Hold every animation on the page still.

        A dashboard with a pulsing icon or a spinner never stops changing, so
        the screencast never goes quiet and the whole pipeline runs for a
        picture nobody is watching. Measured on an animated page: 55.8 frames a
        second, against 0.2 once frozen.

        This goes through the protocol's animation domain rather than through
        CSS, because CSS cannot reach it. Home Assistant builds its cards from
        custom elements, and a rule added to the document does not cross into a
        shadow root -- measured: 60.2 frames a second with the stylesheet in
        place, which is to say no effect whatever. prefers-reduced-motion is no
        better, being only a request the page is free to ignore. The animation
        domain acts on the engine that drives them and does not care where the
        keyframes were declared.

        What it does not reach is anything that is not an animation. A card
        that draws itself on a canvas from requestAnimationFrame, a camera
        tile, a video: none of them go through that engine and none of them
        stop. Measured on one page carrying all three kinds at once -- a
        keyframe animation in a shadow root, a canvas loop and a setInterval
        rewriting text -- 57.3 frames a second before and 59.2 after, because
        the canvas alone is enough to keep the page moving. Each on its own,
        frozen: 0.2 for the animation, 59.7 for the canvas, unchanged for the
        interval. This is worth trying and worth measuring, not worth
        assuming.
        """
        self._session.send("Animation.enable")
        self._session.send("Animation.setPlaybackRate", {"playbackRate": 0})

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
        help="a Home Assistant long-lived access token, or $HA_TOKEN. Only "
        "Home Assistant needs one: leave it out and any other page is "
        "rendered and sent the same way, with nothing written into its "
        "storage and no dashboard waited for",
    )
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument(
        "--render-width",
        type=int,
        help="draw at this width instead of the panel's, and let the board's "
        "pixel-processing accelerator scale it up. Every pixel is paid for "
        "four times here -- painted, encoded, compared with the last one, "
        "encoded again -- and on the board the scaling is free, being a DMA "
        "engine that is otherwise idle. Must match the component's "
        "render_width, which refuses a size that would not land on whole "
        "panel pixels",
    )
    parser.add_argument(
        "--render-height",
        type=int,
        help="the other half of --render-width. Give both or neither",
    )
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
        "--capture-quality",
        type=int,
        default=90,
        help="quality of the picture the browser hands over, 1..100, or 0 for "
        "lossless PNG. This is not --quality: that one is what the panel "
        "receives, and this one is what it is made from, so keeping it above "
        "--quality leaves the second encode something to work with. PNG is "
        "the honest answer and costs the server about eight times as much "
        "CPU for a picture the panel cannot tell apart",
    )
    parser.add_argument(
        "--urgent-fps",
        type=float,
        default=URGENT_FPS,
        help="the limit that applies for a moment after a contact, instead of "
        "--fps. This is what lets --fps be low: at rest a dashboard costs what "
        "it costs, and the moment somebody touches it the brake comes off. "
        "Raise it if a low --fps still feels slow under the finger; it cannot "
        "make the sender go faster than the machine it runs on",
    )
    parser.add_argument(
        "--urgent-window",
        type=float,
        default=URGENT_WINDOW_S,
        help="how many seconds a contact keeps --urgent-fps in force. A "
        "dashboard transition repaints for about a second and a scroll settles "
        "over longer; each new contact starts the count again",
    )
    parser.add_argument(
        "--rect-cost",
        type=float,
        help="what one rectangle costs the board, as a fraction of redrawing "
        "the whole panel. Worked out from the panel's size when not given -- "
        "0.18 at 1024x600, 0.11 at 800x1280 -- because it is a ratio to a "
        "whole-panel decode and that grows with the pixels. Raise it to prefer "
        "whole panels, lower it to prefer rectangles",
    )
    parser.add_argument(
        "--freeze-animations",
        action="store_true",
        help="hold the page's animations still. A pulsing icon or a spinner "
        "keeps the whole pipeline running for a picture nobody is watching. "
        "Only animations: a camera, a video and a card that draws itself on a "
        "canvas do not go through the animation engine and do not stop -- one "
        "of those on the page is enough for this to change nothing at all. "
        "Try it with --stats and keep it only if the idle rate drops",
    )
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

    # Chromium rejects the whole startScreencast call for a quality outside
    # this, and the failure surfaces as a page that simply never sends a frame.
    if not 0 <= args.capture_quality <= 100:
        parser.error("--capture-quality must be between 0 and 100")

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
        # A token is only what gets past Home Assistant's login screen, and
        # nothing else needs one. Any other page -- a train board, a photo
        # frame, a weather site, something somebody built -- is rendered,
        # diffed and sent by exactly the same code, so leaving --token out is
        # how you ask for one of those. It is not a mistake to refuse.
        #
        # A token of the wrong shape is worth refusing here rather than in the
        # browser. Home Assistant answers a bad one by redirecting to its login
        # page, and all this sender can see of that is a page that is not a
        # dashboard -- so it says to check --url, the scheme and the port, and
        # sends you looking in the one place the fault is not.
        #
        # Two things about the shape are certain and cost nothing to check. It
        # is a JWT, so it has three parts separated by dots. It is signed with
        # HS256, whose signature is 32 bytes, which is 43 base64url characters
        # and never any other number. The way it gets broken is being copied
        # out of the dialog by hand and pasted into a console, which truncates
        # it -- silently, and by a different amount each time.
        if args.token:
            args.token = args.token.strip()
            parts = args.token.split(".")
            if len(parts) != 3:
                parser.error(
                    f"the token is not a JWT: it has {len(parts)} "
                    f"dot-separated parts and a Home Assistant token has "
                    f"three. Copy it again."
                )
            if len(parts[2]) != 43:
                parser.error(
                    f"the token's signature is {len(parts[2])} characters and "
                    f"every Home Assistant token's is 43, so this one was cut "
                    f"or joined while being copied. Consoles do that to a long "
                    f"paste. Copy it in Home Assistant with the dialog's copy "
                    f"button and take it from the clipboard without retyping "
                    f"it: in PowerShell, "
                    f"$env:HA_TOKEN = (Get-Clipboard -Raw).Trim()"
                )

    try:
        from PIL import Image
    except ImportError as err:
        raise SystemExit(f"{err}. Install it: pip install pillow") from err

    # The browser renders at the panel's shape. A quarter turn means the page is
    # taller than it is wide, and the panel is the other way round.
    # What is drawn and sent, which is the panel's size unless the board has
    # been told the host will draw smaller. --width and --height stay the
    # panel's throughout, because that is what a contact is reported in.
    # Zero is how a form with no empty state says "not set", so it means the
    # same as leaving them out.
    if args.render_width == 0:
        args.render_width = None
    if args.render_height == 0:
        args.render_height = None
    if (args.render_width is None) != (args.render_height is None):
        parser.error("--render-width and --render-height go together")
    send_w = args.render_width if args.render_width is not None else args.width
    send_h = args.render_height if args.render_height is not None else args.height
    if send_w > args.width or send_h > args.height:
        parser.error(
            f"--render-width/--render-height {send_w}x{send_h} is larger than "
            f"the panel's {args.width}x{args.height}; the board scales up, not down"
        )

    page_w, page_h = send_w, send_h
    if args.rotate in (90, 270):
        page_w, page_h = send_h, send_w

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
                endpoint, page_w, page_h, send_w, send_h, transpose,
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
    rect_cost = (
        # From the PANEL, not from what is drawn. The rule protects the board,
        # and the board's cost for a whole picture barely shrinks when the host
        # draws smaller: the decode does, but the accelerator's pass and the
        # write to the display are still panel-sized. Judging by the render
        # size makes the fraction larger, saturates it sooner, and sends whole
        # pictures more often -- measured on one page, 49.9 KiB/s that way
        # against 34.5 this way, which is the opposite of the point.
        rect_cost_fraction(args.width, args.height)
        if args.rect_cost is None
        else args.rect_cost
    )
    # A press lifts the limit to this for a moment, so that what the press
    # started -- most often a whole new dashboard -- does not arrive as a
    # slideshow. Never slower than the standing limit.
    urgent_interval = min(interval, 1.0 / args.urgent_fps) if args.urgent_fps > 0 else 0.0
    if args.stats:
        print(
            f"One rectangle costs {rect_cost:.3f} of a whole panel at "
            f"{send_w}x{send_h}, so anything from "
            f"{int(1.0 / rect_cost) + 1} of them is sent whole"
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=BROWSER_ARGS)
        context = browser.new_context(
            viewport={"width": page_w, "height": page_h}, device_scale_factor=1
        )
        if args.token:
            install_token(context, args.url, args.token)
        page = context.new_page()
        print(
            f"Opening {args.url} at {page_w}x{page_h}"
            + ("" if args.token else " (no token, so this is not treated as "
                                     "Home Assistant)")
        )
        # Not networkidle: the frontend holds a websocket open for as long as it
        # runs, and waiting for the network to go quiet would wait forever.
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as err:  # noqa: BLE001 - re-raised after the diagnosis
            explain_unreachable(args.url, err)
            raise
        if args.token:
            # Only worth waiting for when Home Assistant is what was asked for.
            # On any other page the element never appears and the wait is
            # thirty seconds of nothing.
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
                if args.token
                else "Warning: this page went to a Home Assistant login screen. "
                "It needs a token: pass --token, or set HA_TOKEN."
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
        capture = Screencast(page, page_w, page_h, args.capture_quality)
        if args.freeze_animations:
            capture.freeze_animations()
        frame_id = 0
        # The newest rendering, kept across connections: a panel that comes back
        # needs a whole picture, and the browser will not send another frame
        # until something on the page moves.
        image = None
        current = None
        # How often each tile has been in a rectangle, for --show-changes.
        heat = np.zeros(
            ((send_h + TILE - 1) // TILE, (send_w + TILE - 1) // TILE),
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
            fulls = 0
            last_send = 0.0
            last_sent = time.monotonic()
            # Assumed awake until the panel says otherwise; it announces its
            # state as soon as a sender connects, so this is only the first
            # instant.
            awake = True
            # Until when a press has the frame limit raised for it. Everything
            # painted before the press is dropped when it lands, so there is no
            # question of the window covering a picture that predates it.
            urgent_until = 0.0
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
                    # Inside the window that a press opens, the faster limit.
                    # Nothing needs to be said about which frame the press
                    # belongs to any more: the press threw away everything that
                    # had been painted before it, so every frame that arrives
                    # from here is one that answers it.
                    limit = urgent_interval if started < urgent_until else interval
                    # Hold the newest frame back until the send rate allows it.
                    # Nothing is lost by waiting: take() only ever returns the
                    # latest, so a frame held here is replaced rather than
                    # queued.
                    shot = None
                    if pending is not None and started - last_send >= limit:
                        shot, pending = pending, None
                        last_send = started

                    # Ask for another picture only once there is somewhere to
                    # put it. Holding the acknowledgement back is what stops
                    # the browser painting frames this loop would only throw
                    # away -- see Screencast.request. A pump early, so the next
                    # one is ready when the interval is up rather than being
                    # started then.
                    if pending is None and started - last_send >= limit - PUMP_MS / 1000.0:
                        capture.request()

                    if shot is not None:
                        image = Image.open(io.BytesIO(shot))
                        # A JPEG frame already arrives as RGB, and convert()
                        # to the mode a picture is already in still copies the
                        # whole of it -- 1.8 ms a frame for nothing.
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        if transpose is not None:
                            image = image.transpose(transpose)
                        if image.size != (send_w, send_h):
                            image = image.resize(
                                (send_w, send_h), Image.BILINEAR
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
                            rectangles = [(0, 0, send_w, send_h)]
                            last_full = started
                            fulls += 1
                        else:
                            rectangles = changed_rectangles(previous, current)
                            covered = sum(w * h for _, _, w, h in rectangles)
                            # Redraw everything only when doing so is actually
                            # cheaper than the pieces.
                            panel = send_w * send_h
                            if covered / panel + rect_cost * len(
                                rectangles
                            ) > 1.0:
                                rectangles = [(0, 0, send_w, send_h)]
                                last_full = started
                                fulls += 1

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
                            urgent_until = time.monotonic() + args.urgent_window
                            # Nothing painted before the finger landed shows
                            # anything of it, in hand or still in the browser.
                            pending = None
                            capture.request(discard=True)
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
                            # What the browser made, against what was worth
                            # sending. Every frame counted here was painted and
                            # encoded whether or not it was used, so a number
                            # far above the one before it is the cost of
                            # pictures nobody will ever see.
                            f"{capture.produced / elapsed:.1f} made/s, "
                            f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                            # How many of those pictures gave up on rectangles
                            # and sent the whole panel. A dashboard costing
                            # hundreds of kilobytes a second is usually doing
                            # this, and the rectangle count alone does not say
                            # so -- a whole panel is one rectangle, and so is a
                            # card that grew.
                            f"{fulls} whole, "
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
                        capture.produced = fulls = 0
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
