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

COLUMNS = 4


def main():
    names = sorted(launcher.ICONS)
    rows = (len(names) + COLUMNS - 1) // COLUMNS
    print("| " + " | ".join(["nom | icône"] * COLUMNS) + " |")
    print("|" + "---|" * (COLUMNS * 2))
    for row in range(rows):
        cells = []
        for column in range(COLUMNS):
            index = column * rows + row
            if index < len(names):
                name = names[index]
                cells.append(f"`{name}` | {launcher.icon_for(name)}")
            else:
                cells.append(" | ")
        print("| " + " | ".join(cells) + " |")
    print()
    print(f"{len(names)} names, {len(set(launcher.ICONS.values()))} distinct icons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
