#!/usr/bin/env python3
"""Run INSIDE the add-on's image: says what it carries, and fails when
something a panel needs is missing.

The workflow that publishes the image (.github/workflows/portall-image.yml)
runs this against the PUBLISHED image, pulled back from ghcr.io, every time it
runs -- so what is checked is what a Home Assistant box downloads, not a build
that never left the runner.

It exists because the Dockerfile lets most of its installs fail without
failing the build -- a panel that shows a dashboard is worth more than none --
which also means a build can succeed with the browser, the sound server or
the sign-in page's tools silently missing. Nothing else would say so before
a household's panel did.

    docker run --rm -v "$PWD/tools/checkimage.py:/checkimage.py:ro" \\
        ghcr.io/youkorr/amd64-portall:4.47.0 python /checkimage.py
"""

import os
import platform
import shutil
import subprocess
import sys

# /app in the image; elsewhere only to try this file out on a copy.
APP = os.environ.get("PORTALL_APP", "/app")
sys.path.insert(0, APP)

problems = []


def need(ok, what):
    print(f"  {'ok  ' if ok else 'MISSING'}  {what}")
    if not ok:
        problems.append(what)


def megabytes(path):
    """What a folder takes on the disk, or None when it is not there."""
    if not os.path.exists(path):
        return None
    out = subprocess.run(["du", "-sxm", path], capture_output=True, text=True)
    return int(out.stdout.split()[0]) if out.returncode == 0 else None


machine = platform.machine()
amd64 = machine in ("x86_64", "amd64")
print(f"Image for {machine}, version {open(os.path.join(APP, 'BUNDLE')).read().strip()}")

print("The add-on's own code:")
for module in ("ha_send", "udisp_send", "run", "launcher", "logos", "files",
               "homekit", "voice", "voicelinks", "signin"):
    try:
        __import__(module)
        need(True, f"{module}.py imports")
    except Exception as err:  # noqa: BLE001 -- the message is the finding
        need(False, f"{module}.py imports ({type(err).__name__}: {err})")

print("What it runs beside the browser:")
for tool, why in (("pulseaudio", "the page's sound"),
                  ("pactl", "the page's sound"),
                  ("parec", "the page's sound"),
                  ("Xvfb", "signing in from a telephone"),
                  ("x11vnc", "signing in from a telephone"),
                  ("websockify", "signing in from a telephone"),
                  ("xdotool", "signing in from a telephone")):
    need(shutil.which(tool) is not None, f"{tool} ({why})")
for module in ("numpy", "PIL", "pyhap"):
    try:
        __import__(module)
        need(True, f"Python {module}")
    except ImportError as err:
        need(False, f"Python {module} ({err})")

print("The launcher's icons:")
fonts = subprocess.run(["fc-list"], capture_output=True, text=True).stdout
need("Noto Color Emoji" in fonts, "a colour emoji font")

print("The browser:")
import ha_send  # noqa: E402 -- after the path above
from playwright.sync_api import sync_playwright  # noqa: E402

if amd64:
    # Every amd64 box gets Google Chrome: it is what carries H.264 and
    # Widevine, and the image no longer has Playwright's Chromium beside it.
    need(os.path.exists("/opt/google/chrome/chrome"), "Google Chrome")
candidates = [path for path in ha_send.SYSTEM_BROWSERS if os.path.exists(path)]
started = None
with sync_playwright() as playwright:
    # Last, Playwright's own FULL Chromium, named by path as the sender names
    # it: a bare launch() would look for the headless shell, which the image
    # does not install.
    for executable in candidates + [None]:
        try:
            browser = playwright.chromium.launch(
                executable_path=executable or playwright.chromium.executable_path,
                headless=True, args=["--no-sandbox"])
        except Exception as err:  # noqa: BLE001
            print(f"  {executable or 'Playwright own'} would not start: "
                  f"{str(err).splitlines()[0]}")
            continue
        page = browser.new_page()
        page.goto("data:text/html,<p>ok</p>")
        h264 = page.evaluate(
            "MediaSource.isTypeSupported('video/mp4; codecs=\"avc1.42E01E\"')")
        started = executable or "Playwright's own Chromium"
        print(f"  ok    starts: {started}, version {browser.version}, "
              f"H.264 {'yes' if h264 else 'no'}")
        if amd64:
            need(h264, "H.264 in the browser that starts")
        browser.close()
        break
need(started is not None, "a browser that starts")

print("What it takes on the disk:")
for label, path in (("Google Chrome", "/opt/google"),
                    ("Chromium (Debian)", "/usr/lib/chromium"),
                    ("Chromium (Playwright)", "/root/.cache/ms-playwright"),
                    ("Python and its libraries", "/usr/local"),
                    ("/usr", "/usr")):
    size = megabytes(path)
    if size is not None:
        print(f"  {label}: {size} MB")

if problems:
    print(f"\n{len(problems)} thing(s) missing: " + "; ".join(problems))
    sys.exit(1)
print("\nEverything a panel needs is in this image.")
