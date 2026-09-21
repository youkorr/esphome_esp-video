#!/usr/bin/env python3
"""Does the add-on remove the profiles of panels that are gone, and only those?

A profile is a browser's whole home and several hundred megabytes of cache it
fills by itself, so sweeping the wrong one costs somebody every site they had
signed into from that panel. The guards are the point of this check, not the
removal: the case that must never fire is an empty panel list, because a
configuration that failed to load looks exactly like a house with no panels.

Runs run.py's own sweep_profiles against real directories in a temporary
place. No browser, no add-on, no Home Assistant.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "portall"))

import run  # noqa: E402

FAILED = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        FAILED.append(name)


def make(root, *names):
    """A profile directory with something in it, so a size can be reported."""
    for name in names:
        path = os.path.join(root, name, "Default", "Cache")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "block"), "wb") as handle:
            handle.write(b"x" * 4096)


def sweep(root, panels):
    run.PROFILES = root
    run.sweep_profiles(panels)
    return sorted(os.listdir(root))


def main():
    root = tempfile.mkdtemp(prefix="portall-profiles-")
    try:
        # The reported case: one panel configured, three folders on disk.
        make(root, "salon", "salon2", "Tab5")
        left = sweep(root, [{"name": "salon"}])
        check("the panel that is configured keeps its profile", left == ["salon"])

        # The guard that matters. A configuration that failed to load gives an
        # empty list, and reading that as "no panels, remove everything" is
        # the one mistake here that cannot be undone.
        make(root, "salon2", "Tab5")
        left = sweep(root, [])
        check("no panels configured sweeps nothing at all",
              left == ["Tab5", "salon", "salon2"])

        # keep_profile off is a panel saying it does not want a profile kept,
        # not a panel disowning the folder it already has.
        left = sweep(root, [{"name": "salon", "keep_profile": False},
                            {"name": "Tab5"}, {"name": "salon2"}])
        check("keep_profile off does not orphan that panel's own folder",
              left == ["Tab5", "salon", "salon2"])

        # A panel with no name is filed under its address, which is what
        # profile_for does, so the sweep has to agree with it.
        shutil.rmtree(root)
        os.makedirs(root)
        make(root, "192-168-1-40", "old")
        left = sweep(root, [{"host": "192.168.1.40"}])
        check("a panel with no name keeps the folder named for its address",
              left == ["192-168-1-40"])
        check("and that is the folder the sender is pointed at",
              run.profile_for({"host": "192.168.1.40"})
              == os.path.join(root, "192-168-1-40"))

        # Only directories, and never through a link: /data is somebody's own
        # volume and a file sitting beside the profiles is not ours to remove.
        shutil.rmtree(root)
        os.makedirs(root)
        make(root, "salon", "gone")
        elsewhere = tempfile.mkdtemp(prefix="portall-elsewhere-")
        with open(os.path.join(elsewhere, "keepme"), "w") as handle:
            handle.write("not ours")
        with open(os.path.join(root, "notes.txt"), "w") as handle:
            handle.write("somebody's")
        os.symlink(elsewhere, os.path.join(root, "link"))
        left = sweep(root, [{"name": "salon"}])
        check("a file beside the profiles is left alone", "notes.txt" in left)
        check("a link is not followed and not removed", "link" in left)
        check("what it came from is untouched",
              os.path.exists(os.path.join(elsewhere, "keepme")))
        check("and the orphan is still gone", "gone" not in left)
        shutil.rmtree(elsewhere)

        # keep_profile off: the folder stays (the guard above) and nothing
        # opens it again, which is the one state that would otherwise be
        # invisible -- a gigabyte that never moves and no line saying why.
        shutil.rmtree(root)
        os.makedirs(root)
        make(root, "salon")
        import io
        import contextlib
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            sweep(root, [{"name": "salon", "keep_profile": False}])
        check("a profile kept but no longer opened says so",
              "keep_profile off" in said.getvalue()
              and "never opened" in said.getvalue())
        check("and it is still there", os.path.isdir(os.path.join(root, "salon")))
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            sweep(root, [{"name": "salon"}])
        check("while a panel that does keep one gets the plain line",
              "keep_profile off" not in said.getvalue()
              and "browser profile of \"salon\" is" in said.getvalue())

        # Nothing at all there yet is the first start of a new add-on.
        shutil.rmtree(root)
        run.PROFILES = root
        run.sweep_profiles([{"name": "salon"}])
        check("no profiles directory yet is not an error",
              not os.path.exists(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if FAILED:
        print(f"\n{len(FAILED)} problem(s)")
        return 1
    print("\nok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
