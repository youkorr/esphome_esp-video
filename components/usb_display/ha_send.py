#!/usr/bin/env python3
"""Put a Home Assistant dashboard on an ESPHome usb_display panel, over Wi-Fi.

The panel is not running Home Assistant and is not running a browser. A machine
that is on anyway -- the Home Assistant server itself, or anything beside it --
renders the dashboard in a browser with no window, and sends the picture to the
panel. Touches come back over the same socket and are replayed into that
browser, so the panel behaves like the screen of the machine doing the
rendering.

    ./ha_send.py --host 192.168.1.9 --width 1024 --height 600 \
        --url http://homeassistant.local:8123/lovelace/0 --token <long-lived>

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
import io
import json
import os
import sys
import time

# udisp_send.py is next to this file and owns the wire format. Importing it
# rather than restating the header keeps one definition of the protocol.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udisp_send import build_header, connect_tcp  # noqa: E402

# The panel is divided into tiles and each is compared with the last picture.
# Small tiles find changes precisely and cost many rectangles; large ones cost
# few rectangles and send unchanged pixels along with the changed ones. 64 is a
# compromise that keeps a clock's worth of change down to one or two tiles.
TILE = 64
# Past this fraction of the panel, sending one rectangle costs less than sending
# many: the headers, the JPEG tables repeated per rectangle and the board's
# per-rectangle work stop being worth the pixels saved.
FULL_REDRAW_FRACTION = 0.45
# However little changes, redraw everything this often. A dropped rectangle --
# the board was busy, the socket hiccuped -- would otherwise stay wrong on the
# panel forever, because nothing would ever mark that area as changed again.
FULL_REDRAW_SECONDS = 30.0


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
    differing = np.any(previous != current, axis=-1)

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


def rotate_point(x, y, width, height, degrees):
    """A point on the panel, back to where it is on the page.

    The image is turned before it is sent, so the panel's pixels are not the
    page's. This is the inverse of that turn: the panel says where the finger
    landed on the panel, and the browser needs where that is on the page.
    width and height are the page's, before rotation.
    """
    if degrees == 0:
        return x, y
    if degrees == 90:
        # The page was turned clockwise, so panel x runs down the page.
        return y, height - 1 - x
    if degrees == 180:
        return width - 1 - x, height - 1 - y
    if degrees == 270:
        return width - 1 - y, x
    return x, y


class Injector:
    """Replays the panel's contacts into the browser.

    One pointer, because a dashboard is a list of things to press and the second
    finger has nothing to do. Down and up are tracked rather than sent for every
    report: a finger held still still reports, and pressing again every poll
    would turn one tap into thirty.
    """

    def __init__(self, page, width, height, rotate):
        self._page = page
        self._width = width
        self._height = height
        self._rotate = rotate
        self._down = False

    def handle(self, reports):
        for contacts in reports:
            if not contacts:
                if self._down:
                    self._page.mouse.up()
                    self._down = False
                continue
            _, px, py = contacts[0]
            x, y = rotate_point(px, py, self._width, self._height, self._rotate)
            # Clamp rather than skip: a contact on the very last column is a
            # real press, and the browser refuses a point outside the viewport.
            x = max(0, min(self._width - 1, x))
            y = max(0, min(self._height - 1, y))
            self._page.mouse.move(x, y)
            if not self._down:
                self._page.mouse.down()
                self._down = True

    def release(self):
        if self._down:
            self._page.mouse.up()
            self._down = False


def main():
    parser = argparse.ArgumentParser(
        description="Render a Home Assistant dashboard onto a usb_display panel"
    )
    parser.add_argument(
        "--host", required=True, help="the panel's address (its port: option)"
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--url", required=True, help="the dashboard to render")
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
        "--fps", type=float, default=10.0, help="how often to look for changes"
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
    args = parser.parse_args()

    if not args.token:
        parser.error("--token is required (or set HA_TOKEN)")

    try:
        import numpy as np
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies:\n"
            "    pip install playwright pillow numpy\n"
            "    playwright install chromium"
        ) from err

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

    interval = 1.0 / args.fps if args.fps > 0 else 0.0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--hide-scrollbars", "--disable-gpu"]
        )
        context = browser.new_context(
            viewport={"width": page_w, "height": page_h}, device_scale_factor=1
        )
        install_token(context, args.url, args.token)
        page = context.new_page()
        print(f"Opening {args.url} at {page_w}x{page_h}")
        # Not networkidle: the frontend holds a websocket open for as long as it
        # runs, and waiting for the network to go quiet would wait forever.
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
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

        injector = None if args.no_touch else Injector(page, page_w, page_h, args.rotate)
        frame_id = 0

        # One pass per connection: losing the panel waits for it to come back
        # rather than ending, so this can be left running as a service.
        while True:
            endpoint = connect_tcp(args.host, args.port)
            previous = None
            last_full = 0.0
            rectangles_sent = 0
            bytes_sent = 0
            pictures = 0
            stats_at = time.monotonic()
            try:
                while True:
                    started = time.monotonic()

                    shot = page.screenshot(type="png")
                    image = Image.open(io.BytesIO(shot)).convert("RGB")
                    if transpose is not None:
                        image = image.transpose(transpose)
                    if image.size != (args.width, args.height):
                        image = image.resize(
                            (args.width, args.height), Image.BILINEAR
                        )
                    current = np.asarray(image)

                    force_full = (
                        previous is None
                        or started - last_full >= FULL_REDRAW_SECONDS
                    )
                    if force_full:
                        rectangles = [(0, 0, args.width, args.height)]
                        last_full = started
                    else:
                        rectangles = changed_rectangles(previous, current)
                        covered = sum(w * h for _, _, w, h in rectangles)
                        if covered == 0:
                            rectangles = []
                        elif covered > args.width * args.height * FULL_REDRAW_FRACTION:
                            rectangles = [(0, 0, args.width, args.height)]
                            last_full = started

                    for x, y, w, h in rectangles:
                        buffer = io.BytesIO()
                        image.crop((x, y, x + w, y + h)).save(
                            buffer, format="JPEG", quality=args.quality
                        )
                        payload = buffer.getvalue()
                        endpoint.write(
                            build_header(w, h, len(payload), frame_id, x, y) + payload
                        )
                        rectangles_sent += 1
                        bytes_sent += len(payload)

                    if rectangles:
                        previous = current
                        pictures += 1
                        # One identifier per picture, so the board's rate limit
                        # decides about the picture and not about each of its
                        # rectangles.
                        frame_id = (frame_id + 1) & 0x3FF

                    reports = endpoint.read_touches()
                    if args.show_touches:
                        for contacts in reports:
                            if contacts:
                                joined = ", ".join(
                                    f"#{i} at {x},{y}" for i, x, y in contacts
                                )
                                print(f"touch {joined}")
                            else:
                                print("touch released")
                    if injector is not None and reports:
                        injector.handle(reports)

                    now = time.monotonic()
                    if args.stats and now - stats_at >= 5.0:
                        elapsed = now - stats_at
                        print(
                            f"{pictures / elapsed:.1f} pictures/s, "
                            f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                            f"{bytes_sent / elapsed / 1024:.1f} KiB/s"
                        )
                        rectangles_sent = bytes_sent = pictures = 0
                        stats_at = now

                    remaining = interval - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
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
