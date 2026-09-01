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
import subprocess
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
    docker_text = dockerfile.read_text()
    faults = []

    # What lands in the image, whether copied from the build context or
    # fetched over the network.
    shipped = set()
    for line in docker_text.splitlines():
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
    # Both of them: DOCS.md is the Documentation tab and README.md is the INFO
    # tab, which is the half that was got wrong twice -- a README is not only
    # for GitHub here, it is the first thing anybody sees after installing.
    for name, tab in (("DOCS.md", "Documentation"), ("README.md", "Info")):
        page = folder / name
        if not page.exists():
            continue
        text = page.read_text()
        pictures = [
            src for src in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
            if not src.startswith(("http://", "https://", "data:"))
        ]
        links = [
            src for src in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", text)
            if not src.startswith(("http://", "https://", "#", "mailto:"))
        ]
        if pictures:
            faults.append(
                f"{name} points at {', '.join(pictures)} for a picture, and a "
                f"relative path never resolves on the {tab} tab -- use a full "
                f"https:// address or drop the picture"
            )
        if links:
            faults.append(
                f"{name} links to {', '.join(links)}, which goes nowhere on the "
                f"{tab} tab: the file is rendered with no base address. Name "
                f"the tab in words instead"
            )

    config = folder / "config.yaml"
    if config.exists():
        version = str(yaml.safe_load(config.read_text()).get("version"))
        # docker_text and not text: a loop above reads DOCS.md and README.md
        # into a variable called text, and for several releases this line
        # searched a README for ARG BUNDLE, found nothing, and passed in
        # silence -- so the one check meant to stop a stale image never ran.
        bundle = re.search(r"^ARG BUNDLE=(\S+)", docker_text, re.M)
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


def check_reaches_sender(folder):
    """Does every option in the form actually reach ha_send.py?

    The fault this exists for, found the same day it was written: `locale` was
    added to config.yaml, to its schema and to run.py's SHARED_KEYS, and still
    never reached a panel -- command_for() emits from its OWN list of keys, and
    that list had not been touched. Nothing failed. The form offered a setting
    that did nothing at all, which is worse than not offering it.

    It is the launcher.py fault again in a different pair of lists, so it is
    checked the same way: by running the thing rather than by reading it.
    """
    folder = pathlib.Path(folder)
    doc = yaml.safe_load((folder / "config.yaml").read_text())
    schema = doc.get("schema") or {}
    sys.path.insert(0, str(folder))
    try:
        import run  # noqa: PLC0415 - the point is to import the shipped file
    except Exception as err:  # noqa: BLE001 - any failure is the answer
        print(f"  ECHEC  {folder}/run.py could not be imported: {err}")
        return 1

    # The ones that are the add-on's own business rather than the sender's.
    ITS_OWN = {
        "links", "panels", "token",
        # Not a flag of its own: it decides whether there is a --profile at
        # all, and what directory it names.
        "keep_profile",
        # Acted on by the add-on before the sender starts, by copying a folder
        # into that profile. The sender never hears about it.
        "import_profile",
        "launcher_title", "launcher_subtitle", "launcher_theme",
        "launcher_color", "launcher_background", "launcher_background_blur",
        "launcher_background_dim", "launcher_columns",
    }
    panel = {"host": "1.2.3.4", "width": 800, "height": 1280,
             "rotate": "0", "touch_rotate": "0"}
    faults = []
    for key, spec in schema.items():
        if key in ITS_OWN or not isinstance(spec, str):
            continue
        flag = "--" + key.replace("_", "-")
        # A value a form could really carry, of roughly the right kind.
        probe = True if spec.startswith("bool") else "7" if spec.startswith(
            ("int", "float", "port")) else "probe"
        argv = run.command_for({**panel, key: probe})
        if flag not in argv:
            faults.append(
                f"{key} is in the form but command_for() never emits {flag}, "
                f"so setting it does nothing"
            )
    if faults:
        print(f"  ECHEC  {folder}/run.py")
        for fault in faults:
            print(f"         {fault}")
        return 1
    print(f"  ok     {folder}/run.py  (every option reaches the sender)")
    return 0


def check_version_moved(folder):
    """Has anything the image ships changed since the version last moved?

    The fault this exists for reached a user, who asked the only question that
    could have found it: "il ya pas de mise a de addon?" -- there was no
    update offered, because the version had been bumped and then more work had
    been done on top of it. Home Assistant offers an update when the version
    in config.yaml is newer than the installed one, so work that lands after a
    bump is invisible until the NEXT bump. Nothing compared the two: the check
    above only holds config.yaml and ARG BUNDLE against each other, and they
    agreed perfectly while shipping nothing.

    Asked of git, because that is the only thing that knows when the version
    last changed. Outside a repository it says so and passes -- a check that
    cannot run is not a failure.
    """
    folder = pathlib.Path(folder)
    doc = yaml.safe_load((folder / "config.yaml").read_text())
    version = str(doc.get("version", ""))
    root = folder.parent

    def git(*args):
        try:
            out = subprocess.run(("git",) + args, cwd=root, text=True,
                                 capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    if git("rev-parse", "--git-dir") is None:
        print(f"  --     {folder}/config.yaml  (not a git checkout, version "
              f"{version} not checked against what changed)")
        return 0
    # Walked rather than searched. `git log -S<string>` matches the commit that
    # REMOVED a string as readily as the one that added it, so asking for the
    # current version found the NEXT bump when there was one and reported that
    # nothing had changed since -- a check that passed on exactly the state it
    # was written to catch.
    rel = f"{folder.name}/config.yaml"
    bump = None
    for commit in (git("log", "--format=%H", "--", rel) or "").splitlines():
        blob = git("show", f"{commit}:{rel}")
        if blob is None:
            break
        found = re.search(r'^version:\s*"?([^"\s]+)"?', blob, re.M)
        if found and found.group(1) == version:
            bump = commit          # keep walking back for the FIRST one
        else:
            break                  # the version changed here, so stop
    if not bump:
        print(f"  --     {folder}/config.yaml  (version {version} is not "
              f"committed yet, so there is nothing to compare)")
        return 0

    # What the image actually carries: everything COPY'd, the senders ADD'd by
    # URL, and config.yaml itself.
    shipped = [str(folder), "components/portall/ha_send.py",
               "components/portall/udisp_send.py"]
    after = git("log", "--format=%h %s", f"{bump}..HEAD", "--", *shipped)
    changed = [l for l in (after or "").splitlines() if l.strip()]
    # The bump commit itself usually carries a CHANGELOG entry and nothing
    # else needs to follow it; what matters is work landing AFTER it.
    if changed:
        print(f"  ECHEC  {folder}/config.yaml")
        print(f"         version is still {version}, but {len(changed)} "
              f"commit(s) have changed what the image ships since it moved:")
        for line in changed[:6]:
            print(f"           {line}")
        print(f"         Home Assistant offers an update on the version alone, "
              f"so none of this reaches a panel until it is bumped.")
        return 1
    print(f"  ok     {folder}/config.yaml  (version {version} is newer than "
          f"everything the image ships)")
    return 0


if __name__ == "__main__":
    files = sys.argv[1:] or ["portall/config.yaml"]
    bad = sum(check(f) for f in files)
    bad += sum(check_image(pathlib.Path(f).parent) for f in files)
    bad += sum(check_reaches_sender(pathlib.Path(f).parent) for f in files)
    bad += sum(check_version_moved(pathlib.Path(f).parent) for f in files)
    sys.exit(bad)
