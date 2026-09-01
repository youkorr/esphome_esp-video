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
# The launcher imports its logos the way it will inside the add-on's image,
# where both sit in the same directory.
sys.path.insert(0, str(HERE / "portall"))
spec = importlib.util.spec_from_file_location(
    "launcher", HERE / "portall" / "launcher.py")
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
    print()
    print("### Les logos de services")
    print()
    print("| service | les noms qui y mènent | service | les noms qui y mènent |")
    print("|---|---|---|---|")
    entries = [(slug, words.split()) for slug, words, _hex, _d
               in sorted(launcher.logos.LOGO_LIST)]
    half = (len(entries) + 1) // 2
    for row in range(half):
        cells = []
        for column in range(2):
            index = column * half + row
            if index < len(entries):
                slug, names = entries[index]
                cells.append(slug + " | " + " ".join(f"`{n}`" for n in names))
            else:
                cells.append(" | ")
        print("| " + " | ".join(cells) + " |")
    print()
    print(f"{len(launcher.logos.LOGO_LIST)} service logos, drawn from the "
          f"add-on itself and never fetched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
