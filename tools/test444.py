#!/usr/bin/env python3
"""Does this panel draw a full-colour (4:4:4) JPEG? Look at it and see.

The add-on's finished picture (4.45.0) is a JPEG whose colour is kept at full
resolution. Espressif's driver says the ESP32-P4's decoder takes one -- read
in ESP-IDF v5.5.5: jpeg_decode.c recognises sampling 0x11 as YUV444 and
allows it into RGB565 on every revision -- and a Guition 800x1280 showed the
green band with nothing wrong. A picture the board cannot decode is dropped,
and a dropped picture leaves the old one on the glass, so from the add-on
alone a refusal looks exactly like success. This makes the answer visible.

The card is the panel's own geometry (the portall: block's width and height),
so a screen used in landscape through the add-on's rotate: shows it in
portrait: the add-on turns the page, and this sends straight.

It sends the same test card twice in turn, a few seconds each:

  - 4:2:0 under a GREY band, which is what every panel has always drawn;
  - 4:4:4 under a GREEN band.

If the green band never appears, the board refused it, and its own log says
"JPEG decode failed". If it appears, look at the red and yellow text and the
coloured edges: they are what full colour changes.

Only one sender at a time reaches a panel, so switch this screen off in the
add-on first (its "Enabled" switch) and back on afterwards:

    pip install pillow
    python tools/test444.py --host 192.168.1.11 --width 800 --height 1280

--width and --height are the width and height of the portall: block in the
panel's own YAML. Ctrl+C stops it.
"""
import argparse
import io
import socket
import struct
import threading
import time

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("This needs Pillow: pip install pillow")

# The same sixteen bytes as a rectangle. Kept here rather than imported so this
# runs from a bare checkout; udisp_send.py holds the one definition, and if the
# wire format ever changes it changes there and is copied here.
HEADER = struct.Struct("<HBBHHHHI")
TYPE_JPG = 3
# The board's own default; a picture above its max_frame_bytes is dropped.
DEFAULT_MAX_FRAME_BYTES = 131072


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1
        return ImageFont.load_default()


def card(width, height, full):
    """The test card: what 4:2:0 smears, under a band naming which it is."""
    image = Image.new("RGB", (width, height), (30, 60, 140))
    draw = ImageDraw.Draw(image)
    unit = max(8, min(width, height) // 40)
    band = unit * 5
    draw.rectangle((0, 0, width, band), fill=(40, 170, 80) if full else (90, 90, 90))
    draw.text((unit, unit), "4:4:4  couleurs pleines" if full else "4:2:0  couleur divisee",
              fill=(255, 255, 255), font=font(unit * 3))
    y = band + unit * 2
    for text, colour in (("YouTube 21 C", (255, 82, 82)),
                         ("Home Assistant", (255, 235, 59)),
                         ("Jellyfin", (105, 240, 174)),
                         ("mercredi 7 octobre", (255, 64, 129))):
        draw.text((unit * 2, y), text, fill=colour, font=font(unit * 2))
        y += unit * 3
    # Coloured edges and one-pixel lines of red and yellow on blue: the things
    # a halved colour resolution blurs first -- the lines go grey.
    x0 = unit * 2
    size = unit * 6
    draw.ellipse((x0, y, x0 + size, y + size), fill=(229, 57, 53))
    draw.rectangle((x0 + size // 4, y + size // 4, x0 + 3 * size // 4, y + 3 * size // 4),
                   fill=(253, 216, 53))
    for i in range(12):
        x = x0 + size + unit * 2 + i * unit
        draw.line((x, y, x, y + size), fill=(255, 40, 40) if i % 2 else (255, 230, 0), width=1)
    return image


def encode(image, full, quality, limit):
    for q in range(quality, 9, -5):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=q, subsampling=0 if full else 2)
        data = buffer.getvalue()
        if len(data) <= limit:
            return data, q
    return data, q


def drain(conn):
    """The board says a few things back (its sample rate, awake/asleep);
    nothing here needs them, and reading them keeps its side moving."""
    try:
        while conn.recv(256):
            pass
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", required=True, help="the panel's address")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=4.0,
                        help="how long each picture stays (default 4)")
    parser.add_argument("--quality", type=int, default=90)
    parser.add_argument("--max-frame-bytes", type=int, default=DEFAULT_MAX_FRAME_BYTES,
                        help="the panel's max_frame_bytes (default 131072)")
    args = parser.parse_args()

    if args.width % 16:
        print(f"Note: {args.width} is not a multiple of 16. The board lays a "
              f"4:4:4 picture of this width out 8 pixels off per row, so the "
              f"green card will look slanted -- that is the board, not the "
              f"decoder, and the add-on does not send 4:4:4 at such a width.")

    # One quality for both, the highest at which the full-colour one fits, so
    # the colour is the only thing that differs between the two.
    limit = args.max_frame_bytes - 1024
    full_data, q = encode(card(args.width, args.height, True), True, args.quality, limit)
    half_data, _ = encode(card(args.width, args.height, False), False, q, limit)
    pictures = [("4:2:0 (grey band)", half_data), ("4:4:4 (green band)", full_data)]
    for name, data in pictures:
        print(f"{name}: {len(data)} bytes at quality {q}")

    print(f"Connecting to {args.host}:{args.port} ...")
    conn = socket.create_connection((args.host, args.port), timeout=10)
    conn.settimeout(None)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    threading.Thread(target=drain, args=(conn,), daemon=True).start()
    print("Connected. Watch the panel; Ctrl+C stops.")

    frame_id = 0
    try:
        while True:
            for name, data in pictures:
                frame_id = frame_id % 1023 + 1
                packed = frame_id | (len(data) << 10)
                conn.sendall(HEADER.pack(0, TYPE_JPG, 0, 0, 0, args.width,
                                         args.height, packed) + data)
                print(f"  sent {name}")
                time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    except OSError as err:
        print(f"The panel closed the connection ({err}). Is the screen "
              f"switched off in the add-on, so this is the only sender?")
    finally:
        conn.close()
    print("Done. Switch the screen back on in the add-on.")


if __name__ == "__main__":
    main()
