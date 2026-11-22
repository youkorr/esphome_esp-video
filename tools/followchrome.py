#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Keep the add-on's Google Chrome current, through updates Home Assistant
offers rather than behind anybody's back.

WHY THIS EXISTS. Asked as "est-ce que chrome est a jour ? ... est-il possible
que le projet portall se mette automatiquement a jour a condition qu'il le
propose". It was not: the image's browser layer is cached, so every release
from 4.51.1 to 4.55.0 carried Chrome 157.0.8103.50 while Google moved on to a
security fix and then to 158. The image workflow now passes Google's current
version as CHROME_STABLE, which rebuilds that layer when it changes, and
writes it on the image as the label io.portall.chrome. This script is what
the daily job (.github/workflows/portall-chrome.yml) runs:

  current             Google's current stable version for Linux.
  label IMAGE         The io.portall.chrome label of a published image, from
                      `docker buildx imagetools inspect`; empty if unreadable.
  bump CHROME         The next patch version: config.yaml, Dockerfile's ARG
                      BUNDLE, CLAUDE.md's "Currently" and a changelog entry.
                      Prints the new version.

--root DIR runs `bump` on another checkout (the check runs it on a copy).
"""
import json
import pathlib
import re
import subprocess
import sys
import textwrap
import urllib.request

GOOGLE = ("https://versionhistory.googleapis.com/v1/chrome/platforms/linux/"
          "channels/stable/versions/all/releases?filter=endtime=none")
LABEL = "io.portall.chrome"
VERSION_LINE = re.compile(r'^version: *"?(\d+)\.(\d+)\.(\d+)"? *$', re.M)


def current():
    """The version Google is serving to every Linux machine now."""
    with urllib.request.urlopen(GOOGLE, timeout=30) as answer:
        releases = json.load(answer).get("releases", [])
    # Fully rolled out, so it is the one apt hands over; the highest if two.
    served = [r["version"] for r in releases if r.get("fraction", 0) >= 1
              and re.fullmatch(r"\d+(\.\d+){3}", r.get("version", ""))]
    if not served:
        raise SystemExit("Google listed no fully rolled out stable version")
    return max(served, key=lambda v: tuple(int(p) for p in v.split(".")))


def label_of(config):
    """The label from `imagetools inspect --format '{{json .Image}}'`, which
    is an image's config, or a map of them by platform for an index."""
    if not isinstance(config, dict):
        return ""
    if "config" in config:
        return ((config.get("config") or {}).get("Labels") or {}).get(LABEL, "")
    for value in config.values():
        found = label_of(value)
        if found:
            return found
    return ""


def label(image):
    try:
        text = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", image,
             "--format", "{{json .Image}}"],
            check=True, capture_output=True, text=True, timeout=120).stdout
        return label_of(json.loads(text))
    except Exception as err:  # noqa: BLE001 - said, and read as unknown
        print(f"could not read {image}: {err}", file=sys.stderr)
        return ""


def edit(path, pattern, replacement):
    text = path.read_text()
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if count != 1:
        raise SystemExit(f"{path}: nothing matched {pattern!r}")
    path.write_text(new)


def bump(root, chrome):
    config = root / "portall" / "config.yaml"
    found = VERSION_LINE.search(config.read_text())
    if not found:
        raise SystemExit("no version line in config.yaml")
    old = ".".join(found.groups())
    major, minor, patch = (int(p) for p in found.groups())
    new = f"{major}.{minor}.{patch + 1}"
    edit(config, r'^version: *"?[\d.]+"? *$', f'version: "{new}"')
    edit(root / "portall" / "Dockerfile", r"^ARG BUNDLE=[\d.]+$",
         f"ARG BUNDLE={new}")
    notes = root / "CLAUDE.md"
    if notes.exists():
        edit(notes, r"Currently \*\*[\d.]+\*\*\.", f"Currently **{new}**.")
    entry = textwrap.fill(
        f"**Google Chrome {chrome}.** Published by itself when Google "
        f"released it, so the screens' browser carries Google's security "
        f"fixes; nothing else changes. On a Raspberry Pi the distribution's "
        f"Chromium is refreshed at the same time.",
        width=78, initial_indent="- ", subsequent_indent="  ")
    edit(root / "portall" / "CHANGELOG.md", r"^# Changelog\n\n",
         f"# Changelog\n\n## {new}\n\n{entry}\n"
         f"- No panel firmware update is required.\n\n")
    print(f"{old} -> {new}", file=sys.stderr)
    return new


def main(argv):
    root = pathlib.Path(__file__).resolve().parent.parent
    if "--root" in argv:
        at = argv.index("--root")
        root = pathlib.Path(argv[at + 1])
        argv = argv[:at] + argv[at + 2:]
    if argv[:1] == ["current"]:
        print(current())
    elif argv[:1] == ["label"] and len(argv) == 2:
        print(label(argv[1]))
    elif argv[:1] == ["bump"] and len(argv) == 2:
        print(bump(root, argv[1]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
