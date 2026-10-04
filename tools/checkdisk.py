#!/usr/bin/env python3
"""The add-on says in its log how much room it takes on the server's disk.

WHY THIS EXISTS. Asked as "mesure la capacite d'espace que prend portall sur
mon server home assistant". Home Assistant shows a disk, not an add-on, so
run.disk_report() walks the container itself. Checked on a made-up tree whose
sizes are known, laid out like the add-on's container:

  - each part of the image lands on its own line (Chrome, Playwright's
    Chromium, Python), and what is left is "the rest of the system";
  - /data is counted apart, each screen's profile and downloads on its own
    line;
  - Home Assistant's folders mounted in (/config, /share, /media) are the
    household's and are never counted;
  - a file hard-linked twice is counted once, and the sizes are what is on
    the disk (blocks), so a sparse file costs what it really costs.
"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
fails = 0
MB = 1_000_000


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def put(root, path, size):
    full = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(os.urandom(size))
    return full


def main():
    import run
    run.IMAGE_PARTS = tuple(
        (name, tuple(p for p in places if p != "/ms-playwright"))
        if name != "Chromium (Playwright)" else
        (name, ("/root/.cache/ms-playwright",))
        for name, places in run.IMAGE_PARTS)
    root = tempfile.mkdtemp()
    put(root, "/opt/google/chrome/chrome", 3 * MB)
    put(root, "/root/.cache/ms-playwright/chromium-1/chrome", 2 * MB)
    put(root, "/usr/local/lib/python3.12/x.so", 1 * MB)
    linked = put(root, "/usr/lib/libbig.so", 1 * MB)
    os.link(linked, os.path.join(root, "usr/lib/libbig-again.so"))
    put(root, "/data/profiles/salon/Default/Cache/a", 4 * MB)
    put(root, "/data/downloads/salon/film.webm", 2 * MB)
    put(root, "/data/avatar/salon.json", 10)
    put(root, "/config/home-assistant_v2.db", 9 * MB)
    put(root, "/media/photos/big.jpg", 9 * MB)
    sparse = os.path.join(root, "usr/lib/sparse.img")
    with open(sparse, "wb") as handle:
        handle.truncate(50 * MB)

    lines = run.disk_report(root)
    print("    " + "\n    ".join(lines))
    text = "\n".join(lines)

    def mb(label):
        for line in lines:
            if line.strip().startswith(label):
                value = line.rsplit(":", 1)[1].strip()
                number, unit = value.split()
                return float(number) * (1000 if unit == "GB" else 1)
        return None

    check("Google Chrome on its own line", mb("Google Chrome") == 3)
    check("Playwright's Chromium on its own line",
          mb("Chromium (Playwright)") == 2)
    check("Python and its libraries on its own line",
          mb("Python and its libraries") == 1)
    check("a hard link counted once, and a sparse file at what it costs",
          mb("the rest of the system") == 1, str(mb("the rest of the system")))
    check("the screen's profile counted apart",
          mb("data, browser profile of salon") == 4)
    check("its downloads too", mb("data, downloads of salon") == 2)
    check("Home Assistant's own folders are not counted",
          "home-assistant" not in text and mb("data, everything else") == 0)
    check("the total adds up",
          "takes 13 MB on the server -- 7 MB for the add-on itself, 6 MB for "
          "its data" in lines[0], lines[0])
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
