#!/usr/bin/env python3
"""Keep one ha_send.py running per panel, for as long as this container lives.

A panel is a long-lived thing: it should come up when the machine does, come
back when the network blinks, and never need a terminal. ha_send.py already
survives a panel going away, but not the machine it runs on going away -- which
is the whole point of moving it off a desktop and onto the box that is on
anyway.

Configuration comes from whichever of these exists, in order:

  /data/options.json      a Home Assistant add-on writes this
  $UDISP_CONFIG           a path to the same JSON, for docker or systemd
  the environment         HOST, URL, TOKEN and friends, for one panel

The add-on form takes a list, because one household has more than one panel and
an add-on cannot be installed twice. Each gets its own process, its own restart
count and its own prefix in the log, so a panel that is misbehaving is obvious
without turning the others off.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

SENDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha_send.py")

# The senders currently running, so stopping this process stops them too. A
# sender holds a browser open; left behind it would keep a panel fed by a
# container that is supposed to be gone, and outlive a systemd unit that has
# been told to stop.
_running = []
_running_lock = threading.Lock()
# Several panels write to one log. Without this their lines interleave.
_print_lock = threading.Lock()

# Long enough not to hammer a Home Assistant that is still starting, short
# enough that a panel is back before anyone walks over to it.
RESTART_DELAY_S = 5
# A run shorter than this did not fail on its own: it failed at startup, and
# retrying at the same speed would spin. Back off instead.
SHORT_RUN_S = 20
MAX_RESTART_DELAY_S = 120


def say(text):
    with _print_lock:
        print(text, flush=True)


# Settings a panel may leave out and inherit from the top of the file. The
# token is the reason this exists -- it is the same for every panel in a house
# and long enough that repeating it per panel is only a way to get one of them
# wrong -- and the rest follow because there is no sense in a rule that applies
# to one key.
SHARED_KEYS = (
    "token",
    "url",
    "port",
    "fps",
    "quality",
    "capture_quality",
    "urgent_fps",
    "urgent_window",
    "keyboard",
    "blank_after",
    "keep_profile",
    "browser",
    "browser_args",
    "rect_cost",
    "freeze_animations",
    "stats",
)


def load_panels():
    """Every panel to serve, as dictionaries of ha_send.py's options."""
    for path in ("/data/options.json", os.environ.get("UDISP_CONFIG")):
        if path and os.path.exists(path):
            with open(path) as handle:
                config = json.load(handle)
            panels = config.get("panels")
            if panels:
                shared = {
                    key: config[key]
                    for key in SHARED_KEYS
                    if config.get(key) not in (None, "")
                }
                # A panel's own value wins -- but only if it is a value. An
                # add-on's form has no empty state to speak of, so a field
                # nobody filled in arrives as "", and a plain merge would let
                # that blank the shared one. The token is where this bites:
                # every panel would silently lose it.
                merged = [
                    {
                        **shared,
                        **{
                            key: value
                            for key, value in panel.items()
                            if key not in SHARED_KEYS or value not in (None, "")
                        },
                    }
                    for panel in panels
                ]
                # A panel that is not showing Home Assistant must not be given
                # the house's token -- and the token is shared, so without this
                # it would inherit one, have it written into that site's
                # storage, and then wait thirty seconds for a dashboard that
                # was never coming. Dropping the token is the whole switch: the
                # sender already treats a panel with none as an ordinary page.
                for panel in merged:
                    if str(panel.pop("home_assistant", True)).lower() in (
                        "false",
                        "no",
                        "0",
                    ):
                        panel.pop("token", None)
                return merged
            # A file holding a single panel is a reasonable thing to write.
            if config.get("host"):
                return [config]
            return []

    if os.environ.get("HOST"):
        return [
            {
                key: os.environ[key.upper()]
                for key in (
                    "host",
                    "port",
                    "url",
                    "token",
                    "width",
                    "height",
                    "rotate",
                    "touch_rotate",
                    "fps",
                    "quality",
                    "capture_quality",
                    "rect_cost",
                )
                if os.environ.get(key.upper())
            }
        ]
    return []


# Where a panel's browser profile lives. /data is the add-on's own persistent
# volume, so what somebody signs into survives a restart and an update.
PROFILES = "/data/profiles"


def profile_for(panel):
    """This panel's profile directory, or None if it is not to keep one.

    One per panel and never shared: Chromium locks a profile directory, and a
    second browser pointed at the same one refuses to start at all. The name
    comes from the panel's own, reduced to something a filesystem is happy
    with, and falls back to its address when it has none.
    """
    if str(panel.get("keep_profile", True)).lower() in ("false", "no", "0"):
        return None
    who = str(panel.get("name") or panel.get("host") or "panel")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in who)
    return os.path.join(PROFILES, safe or "panel")


def command_for(panel):
    """One panel's options, as the command line ha_send.py expects."""
    argv = [sys.executable, "-u", SENDER]
    profile = profile_for(panel)
    if profile:
        argv += ["--profile", profile]
    for key in (
        "host",
        "port",
        "url",
        "token",
        "width",
        "height",
        "rotate",
        "touch_rotate",
        "render_width",
        "render_height",
        "fps",
        "quality",
        "capture_quality",
        "urgent_fps",
        "urgent_window",
        "keyboard",
        "blank_after",
        "rect_cost",
        "browser",
    ):
        value = panel.get(key)
        # Zero means "not set" for the render size, because that is what an
        # add-on's number field offers when somebody wants to turn it off:
        # there is no way to leave it empty.
        if key in ("render_width", "render_height") and value in (0, "0"):
            continue
        if value not in (None, ""):
            argv += [f"--{key.replace('_', '-')}", str(value)]
    # One line, because an add-on form has no repeatable field -- but split
    # the way a shell would, not on whitespace. The flag people actually need
    # here has spaces inside it (--host-resolver-rules="MAP * 1.1.1.1"), and
    # splitting on whitespace tore it into three flags that mean nothing.
    for flag in shlex.split(str(panel.get("browser_args") or "")):
        argv += ["--browser-arg", flag]
    for key in (
        "touch_mirror_x",
        "touch_mirror_y",
        "no_touch",
        "freeze_animations",
        "stats",
    ):
        # Accept the string forms a hand-written JSON file may carry.
        value = panel.get(key)
        if value is True or str(value).lower() in ("true", "yes", "1"):
            argv.append(f"--{key.replace('_', '-')}")
    return argv


def serve(panel, name, stop):
    """Run one panel's sender, restarting it until asked to stop."""
    delay = RESTART_DELAY_S
    while not stop.is_set():
        started = time.monotonic()
        say(f"[{name}] starting")
        try:
            process = subprocess.Popen(
                command_for(panel),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as err:
            say(f"[{name}] could not start: {err}")
            stop.wait(delay)
            continue

        with _running_lock:
            _running.append(process)
        # Prefix every line, so one log can carry several panels and still be
        # read.
        for line in process.stdout:
            say(f"[{name}] {line.rstrip()}")
        process.wait()
        with _running_lock:
            if process in _running:
                _running.remove(process)

        if stop.is_set():
            return
        ran_for = time.monotonic() - started
        if ran_for >= SHORT_RUN_S:
            delay = RESTART_DELAY_S  # it worked for a while; this was a blip
        say(f"[{name}] exited with {process.returncode} after {ran_for:.0f}s, "
            f"restarting in {delay}s")
        stop.wait(delay)
        # Widen only after having waited, so the first retry is prompt and it
        # is a repeated failure that earns the longer pause.
        if ran_for < SHORT_RUN_S:
            delay = min(delay * 2, MAX_RESTART_DELAY_S)


def main():
    panels = load_panels()
    if not panels:
        say("No panels configured. Set them in the add-on options, or point "
            "$UDISP_CONFIG at a JSON file, or set HOST, URL and TOKEN.")
        return 1

    missing = [p for p in panels if not p.get("host") or not p.get("url")]
    if missing:
        say("Every panel needs at least host and url")
        return 1

    stop = threading.Event()

    def shut_down(signum, frame):
        say("Stopping")
        stop.set()
        # Ask the senders to go first. Without this each thread stays blocked
        # reading a browser's output until that browser decides to end, which
        # is not a thing it plans to do.
        with _running_lock:
            for process in _running:
                try:
                    process.terminate()
                except OSError:
                    pass

    signal.signal(signal.SIGTERM, shut_down)
    signal.signal(signal.SIGINT, shut_down)

    threads = []
    for index, panel in enumerate(panels, start=1):
        name = panel.get("name") or panel.get("host") or f"panel {index}"
        thread = threading.Thread(target=serve, args=(panel, name, stop), daemon=True)
        thread.start()
        threads.append(thread)
    say(f"Serving {len(threads)} panel(s)")

    # The container lives as long as the panels do.
    while not stop.is_set():
        stop.wait(1)
    for thread in threads:
        thread.join(timeout=5)
    # Anything still up had its chance to leave politely.
    with _running_lock:
        for process in _running:
            try:
                process.kill()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
