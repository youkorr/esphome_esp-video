#!/usr/bin/env python3
"""Every `portall.key` name reaches a real key in the browser.

There are two tables and they sit in two files, which is the failure mode this
repository keeps recording: a hand-copied pair of constants that drift apart
and say nothing when they do.

    components/portall/__init__.py   KEYS           name -> (page, usage)
    components/portall/udisp_send.py BROWSER_KEYS   (page, usage) -> "ArrowDown"

The board only ever sends what the first table resolved, and the sender only
ever presses what the second one holds. A name in one and not the other is a
button that crosses the link perfectly and does nothing at the far end -- and
`esphome config` cannot see it, because one side is Python the board never
runs and the other is Python the board never sees.

Both files are executed for real rather than parsed, so a table built by a
loop or a comprehension is read the way the program reads it.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def table_from(path, name, stubs=None):
    """Run the module far enough to read one dictionary out of it."""
    source = (ROOT / path).read_text()
    space = dict(stubs or {})
    # The component's __init__.py imports esphome, which is a large install and
    # not always here. Only the table is wanted, so the file is cut at the
    # first line after it rather than imported.
    start = source.index(f"{name} = {{")
    end = source.index("\n}\n", start) + 3
    exec(compile(source[start:end], str(path), "exec"), space)
    return space[name]


def main():
    keys = table_from("components/portall/__init__.py", "KEYS")
    browser = table_from("components/portall/udisp_send.py", "BROWSER_KEYS")

    problems = []
    for name, usage in sorted(keys.items()):
        where = browser.get(usage)
        if where is None:
            problems.append(
                f"portall.key: {name} resolves to {usage[0]:#04x}/{usage[1]:#04x}, "
                f"which the sender has no browser key for -- the button would "
                f"cross the link and do nothing")
        else:
            print(f"  ok     portall.key: {name:<10} -> "
                  f"{usage[0]:#04x}/{usage[1]:#04x} -> {where!r}")

    # The other direction is not a fault: the sender may understand a key no
    # action offers yet, and being tolerant of what a board sends is the rule
    # this project already keeps for 'H'. It is worth SAYING, though.
    spare = sorted(set(browser) - set(keys.values()))
    for usage in spare:
        print(f"  note   the sender knows {usage[0]:#04x}/{usage[1]:#04x} "
              f"({browser[usage]!r}) and no action sends it")

    # A usage of 0 is the C++ default, so a name that resolved to nothing would
    # look like a working action and send a key nobody can name.
    for name, usage in sorted(keys.items()):
        if usage[0] == 0 or usage[1] == 0:
            problems.append(f"portall.key: {name} has a zero in {usage}, which "
                            f"is KeyAction's own uninitialised value")

    for problem in problems:
        print(f"  ECHEC  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
