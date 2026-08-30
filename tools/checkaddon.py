#!/usr/bin/env python3
"""Check the add-on's config.yaml against itself before it reaches anybody.

The Supervisor validates a user's saved options against `schema:`, so an
option that appears under `options:` and not under `schema:` does not merely
go unused -- the add-on refuses to start, with "Unknown option". It was shipped
once, on `browser:`, and there is no way to notice it from a Python test or
from `esphome config`: it is a rule about one YAML file's two halves.

The reverse is worth saying too. A key in `schema:` with no default under
`options:` is legal, but a required one (no trailing `?`) that nobody can have
set is an add-on nobody can start either.

    python3 tools/checkaddon.py usb_display_panel/config.yaml
"""
import pathlib
import sys

import yaml


def check(path):
    doc = yaml.safe_load(pathlib.Path(path).read_text())
    faults = []
    options = doc.get("options") or {}
    schema = doc.get("schema") or {}

    missing = [k for k in options if k not in schema]
    if missing:
        faults.append(
            f"under options: but not under schema: {', '.join(sorted(missing))}"
            f" -- the Supervisor will refuse these as unknown options"
        )

    # A list of dictionaries -- panels -- is described by one specimen entry,
    # so the same rule applies one level down.
    for key, value in options.items():
        entries = value if isinstance(value, list) else []
        spec = schema.get(key)
        if not entries or not isinstance(spec, list) or not spec:
            continue
        allowed = set(spec[0])
        used = set().union(*(set(e) for e in entries if isinstance(e, dict)))
        unknown = used - allowed
        if unknown:
            faults.append(
                f"in the {key} example but not in its schema: "
                f"{', '.join(sorted(unknown))}"
            )

    required = [
        k for k, v in schema.items()
        if isinstance(v, str) and not v.endswith("?") and k not in options
    ]
    if required:
        faults.append(
            f"required by schema: with no default under options: "
            f"{', '.join(sorted(required))}"
        )

    if faults:
        print(f"  ECHEC  {path}")
        for fault in faults:
            print(f"         {fault}")
        return 1
    print(f"  ok     {path}  ({len(options)} options, version "
          f"{doc.get('version')})")
    return 0


if __name__ == "__main__":
    files = sys.argv[1:] or ["usb_display_panel/config.yaml"]
    sys.exit(sum(check(f) for f in files))
