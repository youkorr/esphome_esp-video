#!/usr/bin/env python3
"""An action must live under the same guards as the method it drives.

THE FAULT THIS WAS WRITTEN FOR, found while adding the remote buttons and
present for several releases: `KeyAction` and `HomeAction` sat inside
`#ifdef USE_SPEAKER`. They drive `send_key()` and `ask_home()`, which are
declared unguarded -- so on a board with `keys: true` and no `speaker:` the
methods exist, the classes do not, and the build fails on somebody else's
panel with "KeyAction is not a member of portall".

Nothing here could see it. `esphome config` validates YAML and never compiles
C++; `checkguards.py` reads TinyUSB symbols and nothing else; and every
example this repository ships happens to have a speaker, which is why it went
unnoticed. It arrived by an anchored edit that put two classes inside a guard
meant for a third -- the same replacement hazard CLAUDE.md records four times,
and it took the SetVolumeAction comment away from SetVolumeAction with it.

THE RULE IS DERIVED, NOT LISTED. A hand-written table of which class may sit
under which macro is the pair-of-constants failure this repository keeps
paying for. So the guards are read off the header both times: the set above
each class, and the set above each Portall method that class calls through
`this->parent_->`. They must be EQUAL.

  - class more guarded than its method: the method exists and nothing can
    reach it. That is the fault above.
  - class less guarded than its method: the class exists and will not
    compile, which is the same fault mirrored.

ONE GUARD IS LEGITIMATE and is derived too: a class deriving from
`button::Button` genuinely needs whatever guards the include of
`esphome/components/button/button.h`, because without it there is no base to
derive from. So the allowed set is the methods' guards plus the guards on the
include of each base class's own component -- both read off the same header,
neither written down here.

Run with no arguments to check the headers that carry actions and entities.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT = [
    "components/portall/portall.h",
    "components/portall_bt/portall_bt.h",
]

# `#ifdef USE_SPEAKER`, `#if defined(USE_BUTTON)`, `#ifdef CONFIG_...` -- the
# name is what matters, so anything that looks like a macro on the line counts.
MACRO = re.compile(r"\b(USE_[A-Z0-9_]+|CONFIG_[A-Z0-9_]+|CFG_[A-Z0-9_]+)\b")
CLASS = re.compile(r"\bclass\s+(\w+)\s+(?:final\s+)?:(.*)$")
# `public button::Button` -- the namespace is the component whose header has to
# be included, and that include carries the guard the class really needs.
BASE = re.compile(r"\bpublic\s+(\w+)::")
INCLUDE = re.compile(r'#include\s+"esphome/components/(\w+)/')
CALL = re.compile(r"(?:this->)?parent_->(\w+)\s*\(")
# A member declaration, which is where a method's own guards are read from.
MEMBER = re.compile(r"^\s*(?:virtual\s+|static\s+|inline\s+)*[\w:<>,\s*&]+?\b(\w+)\s*\(")


def guards_by_line(path):
    """The set of macros guarding each line, and the class each line is in."""
    stack = []
    out = []
    for line in open(path, encoding="utf-8"):
        stripped = line.strip()
        if stripped.startswith("#if"):
            stack.append(frozenset(MACRO.findall(stripped)))
            out.append(frozenset().union(*stack) if stack else frozenset())
            continue
        if stripped.startswith(("#elif", "#else")):
            if stack:
                stack[-1] = frozenset(MACRO.findall(stripped))
            out.append(frozenset().union(*stack) if stack else frozenset())
            continue
        if stripped.startswith("#endif"):
            if stack:
                stack.pop()
            out.append(frozenset().union(*stack) if stack else frozenset())
            continue
        out.append(frozenset().union(*stack) if stack else frozenset())
    return out


def check(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    guards = guards_by_line(path)

    # Which guards each component's own header is included under.
    includes = {}
    for number, line in enumerate(lines):
        hit = INCLUDE.search(line)
        if hit:
            includes[hit.group(1)] = guards[number]

    # Every method Portall declares, with what guards it in the header. A name
    # seen at several guard depths keeps the LOOSEST, which is the one a caller
    # can rely on.
    methods = {}
    for number, line in enumerate(lines):
        hit = MEMBER.match(line)
        if hit and not line.lstrip().startswith(("//", "*", "/*", "#")):
            name = hit.group(1)
            here = guards[number]
            if name not in methods or len(here) < len(methods[name]):
                methods[name] = here

    problems = 0
    current = None
    depth_guards = frozenset()
    for number, line in enumerate(lines):
        hit = CLASS.search(line)
        if hit:
            current = hit.group(1)
            depth_guards = guards[number]
            for base in BASE.findall(hit.group(2)):
                depth_guards = depth_guards - includes.get(base, frozenset())
        for called in CALL.findall(line):
            if current is None or called not in methods:
                continue
            want = methods[called]
            if depth_guards != want:
                print(
                    f"{path}:{number + 1}: {current} is guarded by "
                    f"{sorted(depth_guards) or ['nothing']} and calls {called}(), "
                    f"which is guarded by {sorted(want) or ['nothing']}"
                )
                problems += 1
    return problems


def main(argv):
    paths = argv[1:] or [str(ROOT / p) for p in DEFAULT]
    problems = sum(check(p) for p in paths)
    if problems:
        print(f"\n{problems} problem(s): this compiles here and not on a board.")
        return 1
    print(f"CLEAN -- {len(paths)} file(s), every action matches what it drives")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
