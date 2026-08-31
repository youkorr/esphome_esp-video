#!/usr/bin/env python3
"""Print the launcher's icon names as a Markdown table.

The list lives in launcher.py and the README shows it, so one of them has to
be generated from the other or they drift -- and a documented name that does
not work is worse than no list at all.

    python3 tools/iconlist.py > /tmp/table.md
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "launcher", HERE / "usb_display_panel" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

COLUMNS = 2


def main():
    rows = [(launcher.icon_for(words.split()[0]), words.split())
            for _glyph, words in launcher.ICON_NAMES]
    half = (len(rows) + COLUMNS - 1) // COLUMNS
    print("| " + " | ".join(["icône | les noms qui y mènent"] * COLUMNS) + " |")
    print("|" + "---|" * (COLUMNS * 2))
    for row in range(half):
        cells = []
        for column in range(COLUMNS):
            index = column * half + row
            if index < len(rows):
                glyph, names = rows[index]
                cells.append(glyph + " | " + " ".join(f"`{n}`" for n in names))
            else:
                cells.append(" | ")
        print("| " + " | ".join(cells) + " |")
    print()
    print(f"{len(launcher.ICONS)} names onto "
          f"{len(set(launcher.ICONS.values()))} icons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
