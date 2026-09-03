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
import shutil
import signal
import subprocess
import sys
import threading
import time

# An accessory must never cost the picture, and this one already has: the file
# was not copied into the add-on's image, so an import at the top of the file
# took the whole supervisor down before any panel was served. The panels do not
# need the launcher to run, so a missing one is reported and stepped over.
try:
    import launcher
except ImportError:  # the image was built without it
    launcher = None

# One literal, used only when there is no module to ask.
LAUNCHER_KEYWORD = getattr(launcher, "KEYWORD", "launcher")

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
    "locale",
    "user_agent",
    "rect_cost",
    "freeze_animations",
    "stats",
    "show_media",
)


def given(value):
    """Whether a form field was actually filled in.

    An add-on's form has no empty state, so a field nobody meant to set
    arrives as "" -- and a field somebody cleared by hand can arrive as a
    single space, which is not the same thing to Python and is exactly the
    same thing to the person who typed it. Both mean "not set", and treating
    the second as a value is how a panel ended up being handed a token made
    of one space.
    """
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def page_quality_from(config):
    """--page-quality arguments for every link that asked for one.

    The list belongs to the launcher and the setting belongs to the sender, so
    this is where the two meet. A link with no quality of its own is absent
    from the list and simply uses the panel's.
    """
    out = []
    for link in config.get("links") or []:
        url, quality = link.get("url"), link.get("quality")
        if given(url) and given(quality):
            out.append(f"{str(url).strip()}={int(quality)}")
    return out


def page_fps_from(config):
    """--page-fps arguments for every link that asked for one.

    The third setting to want to live on the link rather than the panel, and
    the one that matters most for video: what a board pays for on full motion
    is the NUMBER of whole panels a second, not their size, so a link showing a
    film wants a lower limit while the dashboard keeps its own.
    """
    out = []
    for link in config.get("links") or []:
        url, fps = link.get("url"), link.get("fps")
        if given(url) and given(fps):
            out.append(f"{str(url).strip()}={float(fps):g}")
    return out


def page_tokens_from(config):
    """--page-token arguments for every link that carries one.

    A token belongs to an ORIGIN, so it belongs to the link that names one --
    which is what a household said when they asked why Home Assistant's token
    sat at the top of a form beside a list of links. It is handed to every
    panel, the same way a quality or a user agent per link is: the links are
    the house's, and a panel that never opens the dashboard is unaffected.
    """
    out = []
    for link in config.get("links") or []:
        url, token = link.get("url"), link.get("token")
        if given(url) and given(token):
            out.append(f"{str(url).strip()}={str(token).strip()}")
    return out


def home_assistant_link(config):
    """The link that carries a token, which is the Home Assistant one.

    Nothing else in the list has any use for one, so this needs no separate
    setting saying which link is the dashboard: the token is the mark.
    """
    for link in config.get("links") or []:
        if given(link.get("url")) and given(link.get("token")):
            return str(link["url"]).strip(), str(link["token"]).strip()
    return None, None


def page_agent_from(config):
    """--page-agent arguments for every link that asked to be told something.

    The same meeting point as the quality above, and it exists for one case:
    YouTube's television interface is what a panel can be signed into with a
    code typed on a phone, and a panel is not a television anywhere else --
    least of all to Home Assistant. So it belongs to the LINK.
    """
    out = []
    for link in config.get("links") or []:
        url, agent = link.get("url"), link.get("user_agent")
        if given(url) and given(agent):
            out.append(f"{str(url).strip()}={str(agent).strip()}")
    return out


class Weather:
    """The house's own weather, read by the ADD-ON and never by the page.

    run.py already has the token and the address of Home Assistant; the
    launcher page has neither, and giving it either would put a long-lived
    token into the storage of every site a panel visits -- a leak this project
    has already had to close once. So the reading is fetched here, in the
    background, and served to the page from 127.0.0.1.

    An accessory must never cost the picture: every failure leaves the last
    reading in place, or none at all, and says so once rather than each time.
    """

    EVERY_S = 600

    def __init__(self, url, token, entity):
        # The ORIGIN, not the address as it stands. The shared `url:` is
        # nearly always a dashboard -- http://homeassistant:8123/lovelace/0 --
        # and the first version of this appended /api/states/... straight to
        # it, asking for
        #   http://homeassistant:8123/lovelace/0/api/states/weather.home
        # which is a 404 every time. Reported as the weather simply not
        # appearing while the clock beside it worked.
        from urllib.parse import urlsplit

        split = urlsplit(str(url or ""))
        self.url = (f"{split.scheme}://{split.netloc}"
                    if split.scheme and split.netloc else "")
        self.token = str(token or "")
        self.entity = str(entity or "").strip()
        self.state = None
        self._said = False

    def wanted(self):
        return bool(self.entity and self.url and self.token)

    def why_not(self):
        """Why an entity that was asked for cannot be read, or None.

        Silence is right when nobody asked for weather. It is wrong when
        somebody did and it never appears: that is the shape of fault this
        project keeps having to find twice.
        """
        if not self.entity:
            return None
        if not self.url:
            return "there is no url: to read it from"
        if not self.token:
            return "there is no token: to read it with"
        return None

    def read(self):
        import urllib.error
        import urllib.request

        where = f"{self.url}/api/states/{self.entity}"
        request = urllib.request.Request(
            where, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                data = json.loads(answer.read().decode())
        except Exception as err:  # noqa: BLE001 - any failure keeps the last
            if not self._said:
                self._said = True
                # The address as well as the error. Without it, "could not be
                # read" is the same line whether the entity is misspelt, the
                # token is wrong, or the address had a dashboard path glued to
                # it -- which is exactly the fault this once had.
                say(f"[weather] could not read {where} ({err}) -- "
                    f"the launcher will show no weather")
            return
        attributes = data.get("attributes") or {}
        temperature = attributes.get("temperature")
        unit = attributes.get("temperature_unit") or ""
        self.state = {
            "condition": data.get("state"),
            "text": (f"{round(float(temperature))}{unit}"
                     if temperature is not None else ""),
        }
        if self._said:
            self._said = False
            say(f"[weather] {self.entity} is readable again")

    def run(self):
        while True:
            self.read()
            time.sleep(self.EVERY_S)

    def start(self):
        """Read once now so the first page carries a value, then keep it fresh."""
        blocked = self.why_not()
        if blocked is not None:
            say(f"[weather] {self.entity} was asked for but {blocked}")
        if not self.wanted():
            return None
        self.read()
        threading.Thread(target=self.run, name="weather", daemon=True).start()
        return lambda: self.state


def truthy(value):
    """What a switch in the add-on's form means.

    A bool from the Supervisor, or the word somebody wrote by hand in YAML --
    both reach here, and "false" as a string is not true however Python feels
    about it.
    """
    return str(value).strip().lower() not in ("false", "no", "0", "off", "")


def start_launcher(config):
    """Serve the page of links, if there are any, and say where it is.

    Returns the address, or None when nobody configured any links -- in which
    case a panel asking for the launcher is told plainly rather than being
    pointed at an empty page it cannot get out of.
    """
    links = config.get("links") or []
    if not links:
        return None
    if launcher is None:
        say("Launcher: this build does not carry launcher.py, so the page of "
            "links cannot be served. Panels with a url of their own are "
            "unaffected.")
        return None
    where = launcher.start(
        links,
        theme=str(config.get("launcher_theme") or "dark"),
        background=str(config.get("launcher_background") or ""),
        blur=str(config.get("launcher_background_blur") or "off"),
        dim=config.get("launcher_background_dim", 40),
        columns=config.get("launcher_columns", 0),
        clock=truthy(config.get("launcher_clock", True)),
        clock_size=str(config.get("launcher_clock_size")
                       or launcher.DEFAULT_SIZE),
        clock_color=str(config.get("launcher_clock_color")
                        or launcher.FOLLOW_THEME),
        date_size=str(config.get("launcher_date_size")
                      or launcher.DEFAULT_SIZE),
        date_color=str(config.get("launcher_date_color")
                       or launcher.FOLLOW_THEME),
        weather_size=str(config.get("launcher_weather_size")
                         or launcher.DEFAULT_SIZE),
        align=str(config.get("launcher_align") or "left"),
        motion=truthy(config.get("launcher_background_motion", False)),
        slideshow=truthy(config.get("launcher_slideshow", False)),
        every=config.get("launcher_slideshow_seconds", 30),
        fade=config.get("launcher_slideshow_fade", 1),
        rescan=config.get("launcher_slideshow_rescan", 60),
        weather=Weather(
            # The dashboard's own link is what has the address and the
            # token now, and it is the only thing here that ever had a use
            # for either.
            *home_assistant_link(config),
            config.get("launcher_weather"),
        ).start(),
    )
    if where is not None:
        say(f"Launcher: {len(links)} link(s) at {where}")
    return where


def start_pulseaudio():
    """One sound server for the whole add-on, before any panel starts.

    Each sender then makes its own null sink inside it, named after its panel,
    so two panels never hear each other. Failing is not fatal: the senders say
    so and render the picture regardless.
    """
    try:
        done = subprocess.run(
            ["pulseaudio", "--start", "--exit-idle-time=-1", "--disallow-exit"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as err:
        say(f"No sound: pulseaudio would not run ({err})")
        return
    # It warns about running as root every time and starts anyway, which is
    # the normal case in a container and not worth printing.
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        say(f"No sound: pulseaudio exited {done.returncode}"
            f"{' -- ' + detail[-1] if detail else ''}")
        return
    say("Sound server started")


# The whole configuration as it was read, for the settings that are not a
# panel's -- the launcher's list of links, so far.
_config = {}


def load_panels():
    """Every panel to serve, as dictionaries of ha_send.py's options."""
    global _config
    for path in ("/data/options.json", os.environ.get("UDISP_CONFIG")):
        if path and os.path.exists(path):
            with open(path) as handle:
                config = json.load(handle)
            _config = config
            panels = config.get("panels")
            if panels:
                shared = {
                    key: config[key]
                    for key in SHARED_KEYS
                    if given(config.get(key))
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
                            if key not in SHARED_KEYS or given(value)
                        },
                    }
                    for panel in panels
                ]
                # A panel that is not showing Home Assistant must be given no
                # token at all -- neither one of its own nor any the links
                # carry. Dropping them is the whole switch: the sender already
                # treats a panel with none as an ordinary page.
                #
                # The key is READ rather than popped, because the links are
                # handed out later and that step has to ask the same question.
                for panel in merged:
                    if str(panel.get("home_assistant", True)).lower() in (
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
        "token_url",
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
        "locale",
        "user_agent",
    ):
        value = panel.get(key)
        # Zero means "not set" for the render size, because that is what an
        # add-on's number field offers when somebody wants to turn it off:
        # there is no way to leave it empty.
        if key in ("render_width", "render_height") and value in (0, "0"):
            continue
        if given(value):
            argv += [f"--{key.replace('_', '-')}", str(value)]
    # One line, because an add-on form has no repeatable field -- but split
    # the way a shell would, not on whitespace. The flag people actually need
    # here has spaces inside it (--host-resolver-rules="MAP * 1.1.1.1"), and
    # splitting on whitespace tore it into three flags that mean nothing.
    for flag in shlex.split(str(panel.get("browser_args") or "")):
        argv += ["--browser-arg", flag]
    # A quality per link, which is easier to reason about than one per panel:
    # the panel does not know whether it is showing a film, and the link does.
    for link in panel.get("page_quality") or []:
        argv += ["--page-quality", link]
    # And a user agent per link, for the same reason and in the same shape.
    for link in panel.get("page_agent") or []:
        argv += ["--page-agent", link]
    # And a frame limit per link, for the same reason again.
    for link in panel.get("page_fps") or []:
        argv += ["--page-fps", link]
    # And the token of the Home Assistant a link opens. Not a panel setting:
    # a token belongs to an origin, and the link is what names one.
    for link in panel.get("page_token") or []:
        argv += ["--page-token", link]
    for key in (
        "touch_mirror_x",
        "touch_mirror_y",
        "no_touch",
        "freeze_animations",
        "stats",
        "show_media",
    ):
        # Accept the string forms a hand-written JSON file may carry.
        value = panel.get(key)
        if value is True or str(value).lower() in ("true", "yes", "1"):
            argv.append(f"--{key.replace('_', '-')}")
    return argv


def seed_profile(panel, name):
    """Start a panel's browser profile from one signed in by hand, once.

    Google refuses to sign a browser in when it can tell it is being driven,
    and that refusal is the whole reason this exists. What it checks is the
    SIGNING IN; afterwards the session is a cookie like any other. So the
    signing in is done in an ordinary browser somewhere else -- a Raspberry Pi,
    a laptop, anything with a Chromium a person clicks on -- and the profile it
    leaves behind is handed over here.

    Measured: a cookie written by a plain chromium process, with no automation
    of any kind attached, is sent by the automated browser opening the same
    profile directory.

    Copied rather than used where it lies, for two reasons: /share and /config
    are mapped read-only and a browser must write to its profile, and a profile
    is the panel's from then on -- what it signs into later stays.

    Only ever into an EMPTY profile. Doing it on every start would throw away
    everything the panel has done since, which is the opposite of the point.
    """
    source = str(panel.get("import_profile") or "").strip()
    if not source:
        return
    target = profile_for(panel)
    if target is None:
        say(f"[{name}] import_profile needs keep_profile on -- without a "
            f"profile kept between restarts there is nowhere to put it")
        return
    if os.path.isdir(target) and os.listdir(target):
        return
    if not os.path.isdir(source):
        say(f"[{name}] import_profile: {source} is not there. It should be a "
            f"folder this add-on can read -- under /share, /config or /media.")
        return
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError as err:
        # An accessory must never cost the picture: a profile that would not
        # copy leaves the panel with a fresh one, which is what it had before.
        say(f"[{name}] import_profile: could not copy {source} ({err}) -- "
            f"carrying on with a fresh profile")
        return
    say(f"[{name}] started its browser profile from {source}")


def serve(panel, name, stop):
    """Run one panel's sender, restarting it until asked to stop."""
    seed_profile(panel, name)
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

    # Before the check below, because a panel asking for the launcher has no
    # url of its own until this has given it one.
    where = start_launcher(_config)
    for panel in panels:
        if str(panel.get("url", "")).strip().lower() != LAUNCHER_KEYWORD:
            continue
        if where is None:
            # Say which of the two it is. Telling somebody who filled the
            # list in that it is empty sends them to look at the one thing
            # that is right.
            why = (
                "no links are configured; add some under links"
                if not (_config.get("links") or [])
                else "the launcher could not be served, for the reason above"
            )
            say(f"[{panel.get('name', 'panel')}] url is \"{LAUNCHER_KEYWORD}\" "
                f"but {why}, or point this panel at a page of its own")
            panel["url"] = ""
        else:
            # The token belongs to Home Assistant, and the page this panel now
            # opens is the launcher. Without saying so, the sender would
            # install the token for the launcher's own address -- and the
            # frontend ignores a record whose hassUrl is not its own, so the
            # dashboard behind a tile would ask to log in with the token
            # sitting unused in its storage. The Home Assistant LINK is the
            # house's dashboard address now, which is where the token lives
            # too, so the two can no longer disagree.
            home, _ = home_assistant_link(_config)
            if given(panel.get("token")) and not given(panel.get("token_url")):
                if given(home):
                    panel["token_url"] = home
                else:
                    say(f"[{panel.get('name', 'panel')}] this panel starts on "
                        f"the launcher and has a token of its own, but no "
                        f"link carries a Home Assistant address to attach it "
                        f"to. Give the dashboard's link a token, or a tile "
                        f"opening it will ask to log in.")
            panel["url"] = where

    # Every panel gets the same list: the links are the house's, not one
    # screen's, and a panel that never opens a given page is unaffected by a
    # quality set for it.
    wanted = page_quality_from(_config)
    if wanted:
        for panel in panels:
            panel.setdefault("page_quality", wanted)
    agents = page_agent_from(_config)
    if agents:
        for panel in panels:
            panel.setdefault("page_agent", agents)
    limits = page_fps_from(_config)
    if limits:
        for panel in panels:
            panel.setdefault("page_fps", limits)
    # A panel that opted out of Home Assistant carries none of the house's
    # tokens, which is the whole of what home_assistant: false ever meant.
    keys = page_tokens_from(_config)
    if keys:
        for panel in panels:
            if str(panel.get("home_assistant", True)).strip().lower() \
                    not in ("false", "no", "0"):
                panel.setdefault("page_token", keys)

    missing = [p for p in panels if not p.get("host") or not p.get("url")]
    if missing:
        # Named, because the url used to be inheritable from the top of the
        # form and is not any more: a configuration that worked yesterday
        # arrives here today with nothing to show, and "every panel needs a
        # url" does not say that a setting moved.
        say("Every panel needs a host and a url of its own: "
            + ", ".join(str(p.get("name") or p.get("host") or "?")
                        for p in missing)
            + ". Put \"launcher\" there for the page of links, or the "
              "address of the page that panel shows. Home Assistant's own "
              "address is a link now rather than a setting at the top -- see "
              "\"Moving from 2.x\" in the documentation.")
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

    # Before any sender, not after. Each one makes its own null sink inside
    # this server as it starts, and a sender that got there first found no
    # server at all -- pactl then tries to spawn its own, which is both slow
    # and a second server nobody wanted.
    start_pulseaudio()
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
