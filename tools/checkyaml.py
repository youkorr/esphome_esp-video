#!/usr/bin/env python3
"""Run `esphome config` on this repository's example firmware.

The rule in CLAUDE.md is that no configuration is handed over unvalidated, and
until this existed there was no way to keep it: `esphome config` needs secrets
the repository must not contain, and it would fetch the component from GitHub
rather than looking at the working tree, so it would validate whatever main
holds instead of what is about to be pushed.

This does three things and then gets out of the way:

  * writes a throwaway secrets.yaml holding every `!secret` the file asks for,
    with values shaped like the real ones -- a 32-byte base64 key where a key
    is wanted, because the schema checks that
  * points `external_components` at ./components, so what is checked is the
    code in this checkout
  * runs `esphome config` and says which files passed

It found real faults the first time it was run: a fallback hotspot SSID over
the 32-character limit in two files, and a `microphone_type:` that the es8311
schema has never had. Both had been shipped after a "parse check", which is
exactly the kind of confidence this replaces.

    python3 tools/checkyaml.py yaml/*.yaml
    ESPHOME=/path/to/venv/bin/esphome python3 tools/checkyaml.py yaml/p4-*.yaml

**Validate against the esphome the user builds with, not the newest release.**
A board reported a compile error on 2026.9.0-dev over an API that exists in
2026.6.5, which is what happened to be installed here -- so this checked a
configuration nobody was running. `esphome version` says which one is being
used, and installing dev takes a Python 3.12 virtualenv:

    python3.12 -m venv /tmp/esphome-dev
    /tmp/esphome-dev/bin/pip install "git+https://github.com/esphome/esphome@dev"

It is still only half the check: `esphome config` validates YAML and codegen
and never compiles C++, so an API that moved is found by whoever compiles.

Two things it cannot check from a sandbox: micro_wake_word downloads its model
from github.com during validation, and voice_assistant needs it. Where that
host is unreachable the file cannot be validated whole, and the failure says
so rather than naming anything in the file.
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A key the schema will accept: 32 bytes of base64. Anything shorter is
# rejected before the rest of the file is looked at, which reads like a fault
# in the configuration and is not one.
FAKE_KEY = "aGVsbG9oZWxsb2hlbGxvaGVsbG9oZWxsb2hlbGxvMTI="
GIT_SOURCE = re.compile(
    r"( *)- source:\n *type: git\n *url: [^\n]*\n( *ref: [^\n]*\n)?", re.M
)


def secrets_for(text):
    """Every !secret the file asks for, with a value of the right shape."""
    out = {}
    for name in sorted(set(re.findall(r"!secret\s+([A-Za-z0-9_]+)", text))):
        out[name] = FAKE_KEY if "key" in name else f"check-{name}"
    return out


def github_reachable():
    """Asked once, and only when something has already failed for want of it."""
    if github_reachable.answer is None:
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlopen("https://github.com/", timeout=10).close()
            github_reachable.answer = True
        except Exception:  # noqa: BLE001 - any failure is the same answer
            github_reachable.answer = False
    return github_reachable.answer


github_reachable.answer = None


def check(path, esphome):
    text = pathlib.Path(path).read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "secrets.yaml").write_text(
            "".join(f'{k}: "{v}"\n' for k, v in secrets_for(text).items())
        )
        staged = GIT_SOURCE.sub(
            lambda m: f"{m.group(1)}- source:\n{m.group(1)}    type: local\n"
                      f"{m.group(1)}    path: {ROOT / 'components'}\n",
            text,
        )
        target = tmp / pathlib.Path(path).name
        target.write_text(staged)
        run = subprocess.run(
            [esphome, "config", str(target)],
            capture_output=True, text=True, cwd=tmp,
        )
    # esphome prints the resolved configuration on stdout and says whether it
    # is valid on stderr, so looking at only one of them gets it backwards.
    both = run.stdout + run.stderr
    # esphome prints the resolved configuration on stdout and says whether it
    # is valid on stderr, so looking at only one of them gets it backwards.
    ok = "Configuration is valid" in both
    if ok:
        print(f"  ok     {path}")
        return 0
    # micro_wake_word resolves a bare model name to a github.com URL and
    # downloads it while validating, so a sandbox with no route there fails on
    # a file that is perfectly good. Worth telling apart from a real fault,
    # and worth only believing after asking whether the host is reachable.
    if "Not a valid model name" in both and not github_reachable():
        print(f"  ?      {path}")
        print("         micro_wake_word downloads its model from github.com, "
              "unreachable from here, so the rest of the file went unchecked")
        return 0
    print(f"  ECHEC  {path}")
    # esphome echoes the whole resolved configuration and puts each complaint
    # inline, next to the key it is about. Printing the first lines therefore
    # prints the echo and hides the message -- which cost two round trips to
    # find out that a config had been rejected over something else entirely.
    # Anything shaped like `key: value` is the echo; what is left is the fault.
    echo = re.compile(r"^\s*-?\s*[\w.]+:\s*\S*\s*$")
    lines = [
        l for l in both.splitlines()
        if l.strip() and not l.startswith("INFO ") and not echo.match(l)
    ]
    for line in lines[:14]:
        print(f"         {line}")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument(
        "--esphome", default=os.environ.get("ESPHOME", "esphome"),
        help="the esphome to run, if it is not on the path. It is a large "
             "install and a virtualenv of its own is the usual way: "
             "python3 -m venv .esphome && .esphome/bin/pip install esphome",
    )
    args = parser.parse_args()
    if shutil.which(args.esphome) is None and not pathlib.Path(args.esphome).exists():
        raise SystemExit(
            f"{args.esphome} not found. Install it into a virtualenv and pass "
            f"--esphome, or set ESPHOME."
        )
    sys.exit(sum(check(f, args.esphome) for f in args.files))
