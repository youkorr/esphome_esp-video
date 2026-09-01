#!/usr/bin/env python3
"""Draw the add-on's icon and logo.

Home Assistant's store shows `icon.png` beside the add-on in a list and
`logo.png` on its page. Both are drawn here rather than kept as binaries
nobody can edit: the shape is a handful of numbers, and a picture in a
repository that cannot be regenerated is a picture nobody dares change.

The mark is a doorway inside a screen. That is the whole of what this project
does -- a page arrives through a portal and lands on a panel -- and it stays
legible at the 32 pixels a store list actually shows.

    python3 tools/makeicon.py
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent.parent / "portall"
# Drawn four times over and shrunk, which is the cheapest antialiasing there
# is and the only one that keeps a thin arch clean at this size.
SCALE = 4
GROUND = (14, 18, 27)
ACCENT = (56, 189, 248)
INK = (232, 236, 244)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def mark(size, ground=True):
    """The doorway-in-a-screen, on a square of the given size."""
    s = size * SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if ground:
        draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22),
                               fill=GROUND)
    # The screen: a landscape panel, because that is what these are.
    pad, top = s * 0.16, s * 0.22
    screen = [pad, top, s - pad, s - top]
    draw.rounded_rectangle(screen, radius=int(s * 0.06), outline=ACCENT,
                           width=max(1, int(s * 0.045)))
    # The doorway inside it: a rounded arch, open at the floor of the screen.
    stroke = max(1, int(s * 0.045))
    width = (screen[2] - screen[0]) * 0.42
    left = (s - width) / 2
    arch_top = top + (screen[3] - screen[1]) * 0.22
    # Standing on the INSIDE of the frame, not across it. Drawn over the
    # bottom rail it cut the screen in half, which reads as a broken box
    # rather than as a doorway in one.
    floor = screen[3] - stroke / 2
    draw.rounded_rectangle([left, arch_top, left + width, floor],
                           radius=int(width / 2), fill=INK)
    # Squared off where it meets the floor, so it reads as a doorway rather
    # than a pill.
    draw.rectangle([left, floor - width / 2, left + width, floor], fill=INK)
    return image.resize((size, size), Image.LANCZOS)


def main():
    icon = mark(256)
    icon.save(HERE / "icon.png")

    # The logo carries its own ground rather than sitting on the store's.
    # A pale wordmark on transparent is invisible on a light card and a dark
    # one is invisible on a dark card; the store has both, and which one a
    # person sees is their theme rather than anything this can know.
    width, height = 500, 200
    s = SCALE // 2
    logo = Image.new("RGBA", (width * s, height * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.rounded_rectangle([0, 0, width * s - 1, height * s - 1],
                           radius=int(height * s * 0.18), fill=GROUND)
    badge = mark(int(height * s * 0.68), ground=False)
    logo.paste(badge, (int(height * s * 0.16), int(height * s * 0.16)), badge)
    try:
        font = ImageFont.truetype(FONT, int(height * s * 0.34))
    except OSError:
        print("no DejaVu Sans Bold here; the wordmark would be a bitmap font")
        return 1
    text_x = int(height * s * 0.16) + badge.width + int(height * s * 0.18)
    box = draw.textbbox((0, 0), "portall", font=font)
    draw.text((text_x, (height * s - (box[3] - box[1])) / 2 - box[1]),
              "portall", font=font, fill=INK)
    logo.resize((width, height), Image.LANCZOS).save(HERE / "logo.png")

    for name in ("icon.png", "logo.png"):
        path = HERE / name
        print(f"  {path.relative_to(HERE.parent)}  "
              f"{Image.open(path).size[0]}x{Image.open(path).size[1]}  "
              f"{path.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
