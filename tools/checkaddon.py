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

It also checks the two things about the image that a Python test cannot see:
that every local module run.py imports is actually copied into it, and that
the version in config.yaml matches the Dockerfile's ARG BUNDLE.

    python3 tools/checkaddon.py portall/config.yaml
"""
import ast
import pathlib
import re
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


def check_image(folder):
    """What the image will contain, against what run.py needs from it.

    The fault this exists for: launcher.py was added beside run.py and
    imported at the top of it, and the Dockerfile was never told to copy it.
    Everything validated -- the YAML, the Python, the launcher's own tests --
    and the add-on died on ModuleNotFoundError before serving a panel, because
    nothing anywhere compares one file's imports against another file's COPY
    lines.

    The version pair is here for the same reason: Docker caches a layer on its
    command string alone, so an add-on whose ARG BUNDLE did not move is an
    add-on that ships the previous build's senders and browser.
    """
    folder = pathlib.Path(folder)
    dockerfile = folder / "Dockerfile"
    entry = folder / "run.py"
    if not dockerfile.exists() or not entry.exists():
        return 0
    text = dockerfile.read_text()
    faults = []

    # What lands in the image, whether copied from the build context or
    # fetched over the network.
    shipped = set()
    for line in text.splitlines():
        words = line.split()
        if words and words[0].upper() in ("COPY", "ADD") and len(words) >= 3:
            shipped.update(pathlib.PurePosixPath(w).name for w in words[1:-1])

    # What run.py needs, and what THOSE need in turn. Only modules that exist
    # as files beside it: anything else is a package pip installed and not
    # this Dockerfile's business. Following the chain matters -- run.py
    # imports the launcher, and the launcher imports the logos, and the file
    # two steps out is exactly the one nobody thinks to add a COPY for.
    local, pending = set(), [entry]
    while pending:
        tree = ast.parse(pending.pop().read_text())
        needed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                needed.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                needed.add(node.module.split(".")[0])
        for name in needed:
            if (folder / f"{name}.py").exists() and name not in local:
                local.add(name)
                pending.append(folder / f"{name}.py")
    missing = sorted(n for n in local if f"{n}.py" not in shipped)
    if missing:
        faults.append(
            f"the add-on needs {', '.join(missing)}, and the Dockerfile never "
            f"copies {', '.join(n + '.py' for n in missing)} -- the add-on "
            f"will die on ModuleNotFoundError before serving a panel"
        )

    # What the Supervisor shows, and what it will not show. The Documentation
    # tab reads DOCS.md; a README.md is for whoever is reading the repository
    # on GitHub and is never displayed in Home Assistant -- which is how this
    # add-on shipped with an empty Documentation tab and a perfectly good
    # README nobody could see from the panel they had just installed.
    for name, why in (("DOCS.md", "the Documentation tab is empty without it"),
                      ("CHANGELOG.md", "the Changelog tab is empty without it"),
                      ("icon.png", "the store shows a blank square without it"),
                      ("logo.png", "the add-on's page has no header without it")):
        if not (folder / name).exists():
            faults.append(f"no {name} -- {why}")

    # A relative image in DOCS.md is a broken image. The Supervisor hands that
    # file to the frontend as text and renders it there, with no base address
    # to resolve a path against, so ![Portall](logo.png) -- which GitHub draws
    # perfectly -- arrives on the Documentation tab as a broken-picture icon.
    # Shipped exactly that way and reported from the store.
    docs = folder / "DOCS.md"
    if docs.exists():
        relative = [
            src for src in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", docs.read_text())
            if not src.startswith(("http://", "https://", "data:"))
        ]
        if relative:
            faults.append(
                f"DOCS.md points at {', '.join(relative)} for a picture, and a "
                f"relative path never resolves on the Documentation tab -- use "
                f"a full https:// address or leave the picture to README.md"
            )

    config = folder / "config.yaml"
    if config.exists():
        version = str(yaml.safe_load(config.read_text()).get("version"))
        bundle = re.search(r"^ARG BUNDLE=(\S+)", text, re.M)
        if bundle and bundle.group(1) != version:
            faults.append(
                f"config.yaml is {version} and ARG BUNDLE is "
                f"{bundle.group(1)} -- Docker will reuse the cached layers and "
                f"ship the previous build's senders and browser"
            )

    if faults:
        print(f"  ECHEC  {dockerfile}")
        for fault in faults:
            print(f"         {fault}")
        return 1
    print(f"  ok     {dockerfile}  ({len(shipped)} files in the image)")
    return 0


if __name__ == "__main__":
    files = sys.argv[1:] or ["portall/config.yaml"]
    bad = sum(check(f) for f in files)
    bad += sum(check_image(pathlib.Path(f).parent) for f in files)
    sys.exit(bad)
