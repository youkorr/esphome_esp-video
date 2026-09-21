#!/usr/bin/env python3
"""Run the real CODEGEN on this repository's example firmware.

`esphome config` validates YAML and then stops. Every `to_code` in this
repository -- the one that resolves an entity's parent, the one that emits a
lambda into main.cpp -- runs afterwards and is never reached, so a whole class
of fault gets past it and lands on somebody's board:

  * `Couldn't find ID 'panel'`, which is what a household got from copying an
    example's `keys: panel` into a `portall:` block named something else
  * `EsphomeError: Circular dependency detected!`, which is what a first
    attempt at resolving that id automatically produced
  * an entity whose `set_parent()` was never emitted at all, which `config`
    prints NOTHING about: an auto-generated `portall_bt_id` does not appear in
    its output, so the one line proving the wiring is invisible there.

This loads each file the way `tools/checkyaml.py` does -- throwaway secrets, a
local `external_components` so the working tree is what is checked -- and then
calls `generate_cpp_contents()`. Anything that raises is the fault; `--show`
prints the statements that wire components to each other, which is how the
Bluetooth entities were proved rather than assumed.

It has to run INSIDE the esphome being checked, so give it that venv's python:

    /path/to/venv/bin/python tools/checkcodegen.py yaml/tab5-portall-bluetooth.yaml
    /path/to/venv/bin/python tools/checkcodegen.py --show yaml/*.yaml

Its blind spots are checkyaml's: micro_wake_word downloads its model while
validating, and no line of C++ is compiled here by anything.
"""

import pathlib
import re
import sys
import shutil
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from checkyaml import GIT_SOURCE, github_reachable, secrets_for  # noqa: E402

# Not an example this repository offers -- it is a household's own
# configuration, kept here because it is the file two faults were reported
# against. Its `esphome: name:` is not one esphome accepts, which is theirs to
# fix and not something a sweep should keep reporting.
THEIRS = {"GUITION_ PORTAL.yaml"}

# The statements worth reading when a wiring question is being asked: one
# component handed to another, and the sinks this repository emits into
# main.cpp. Deliberately not everything -- a generated main.cpp is thousands
# of lines and the interesting ones are the joins.
INTERESTING = re.compile(
    r"->set_parent\(|->set_\w*(speaker|sink|id)\(|set_key_sink|set_home_sink", re.I
)



def carry_siblings(source, text, into):
    """Copy the files a YAML names beside itself into the staging directory.

    A config may point at a file next to it -- `cv.file_` resolves a relative
    path against the YAML's OWN directory -- and staging the YAML somewhere
    else leaves those behind. A Realtek firmware blob is what found this: the
    file validated perfectly for a panel and this checker called it broken,
    which is the worst way round for a check to be wrong.

    Deliberately by NAME rather than by parsing the config: the names have to
    be known before the config can be read, since it is the reading that
    fails without them.
    """
    for item in source.iterdir():
        if item.is_file() and item.suffix not in (".yaml", ".yml") and item.name in text:
            shutil.copy(item, into / item.name)


def check(path, show):
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
        carry_siblings(pathlib.Path(path).parent, text, tmp)

        # Imported here rather than at the top: esphome is a large import and
        # this tool is also run to print its own help.
        import esphome.config as config_module
        from esphome.__main__ import generate_cpp_contents
        from esphome.core import CORE

        CORE.reset()
        CORE.config_path = target
        try:
            config = config_module.read_config({})
        except Exception:  # noqa: BLE001 - any failure is the same answer
            return _refused(path, staged + traceback.format_exc())
        if config is None:
            # read_config PRINTS its reasons and returns None, so there is
            # nothing to inspect: the staged text is what says whether this
            # file is one of the two that cannot validate from a sandbox.
            return _refused(path, staged)
        CORE.config = config
        try:
            generate_cpp_contents(config)
        except Exception as err:  # noqa: BLE001
            print(f"  ECHEC  {path}  (codegen: {err})")
            traceback.print_exc()
            return 1
        lines = [str(s) for s in CORE.main_statements]

    print(f"  ok     {path}  ({len(lines)} statements)")
    if show:
        for line in lines:
            for one in line.splitlines():
                if INTERESTING.search(one):
                    print("         " + one.strip())
    return 0


def _refused(path, why):
    """A file that would not validate -- unless the reason is this sandbox.

    micro_wake_word resolves a bare model name to a github.com URL and
    downloads it WHILE VALIDATING, so a container with no route there reports
    a perfectly good file as broken. checkyaml.py carries the same special
    case and its own comment says why: a check that quietly stopped
    recognising its own blind spot is worse than not having one.
    """
    if "micro_wake_word" in why and not github_reachable():
        print(f"  ?      {path}")
        print("         micro_wake_word downloads its model from github.com "
              "while validating, and there is no route to it from here.")
        return 0
    print(f"  ECHEC  {path}  (would not validate)")
    if why:
        for line in why.strip().splitlines()[-6:]:
            print("         " + line)
    return 1


def main():
    args = [a for a in sys.argv[1:] if a != "--show"]
    show = "--show" in sys.argv[1:]
    paths = args or sorted(
        str(p) for p in (ROOT / "yaml").glob("*.yaml") if p.name not in THEIRS
    )
    if not paths:
        print("  nothing to check")
        return 1
    return 1 if sum(check(p, show) for p in paths) else 0


if __name__ == "__main__":
    sys.exit(main())
