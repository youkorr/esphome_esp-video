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

# Same rule, same reason: a panel that wants no remote must not be stopped by
# the absence of one.
try:
    import homekit
except ImportError:  # the image was built without it
    homekit = None

# Its own folder under the add-on's persistent volume, so a pairing survives
# a restart and an update.
HOMEKIT_DIR = "/data/homekit"

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
    # Stamped, because the whole of a timing question is WHEN two lines
    # happened relative to each other, and the supervisor's own view does not
    # stamp an add-on's output. To the tenth, which is the resolution anything
    # a finger does is argued at.
    now = time.time()
    stamp = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 10)}"
    with _print_lock:
        print(f"{stamp} {text}", flush=True)


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
    "max_rate",
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
    "show_touches",
    "homekit",
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


def page_rate_from(config):
    """--page-rate arguments for every link that asked for one.

    The byte rate belongs on the link for the reason the frame limit does: a
    film is where it bites, and a dashboard never comes near it. Zero is a
    value here -- it turns the limit off for that link -- so it is kept.
    """
    out = []
    for link in config.get("links") or []:
        url, rate = link.get("url"), link.get("max_rate")
        if given(url) and given(rate):
            out.append(f"{str(url).strip()}={float(rate):g}")
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

    The page must not read it: an add-on's credential in a page's storage is
    on every site a panel visits, which is a leak this project has already had
    to close once. So the reading is fetched here, in the background, and
    served to the page from 127.0.0.1.

    An accessory must never cost the picture: every failure leaves the last
    reading in place, or none at all, and says so once rather than each time.
    """

    EVERY_S = 600

    # An add-on is handed its own credential by the Supervisor and can reach
    # Home Assistant's REST API through it, with nothing configured: the
    # Supervisor proxies /core/api/... to Core and checks the bearer token
    # against the add-on that was given it. Confirmed in the Supervisor's own
    # source rather than remembered -- ENV_TOKEN = "SUPERVISOR_TOKEN" in
    # docker/const.py, the route GET /core/api/{path} in api/__init__.py, and
    # api/proxy.py refusing it unless access_homeassistant_api, which is the
    # `homeassistant_api: true` this add-on's config.yaml now declares.
    #
    # This is what the weather should always have used. Reading it through the
    # house's LONG-LIVED token meant the reading depended on where somebody
    # had put that token -- and after 3.0.0 moved tokens onto the links, a
    # household that filled in the panel's token instead got no weather and a
    # message naming the wrong half. The Supervisor route has no such
    # question: there is nothing to fill in and nothing to get wrong.
    SUPERVISOR = "http://supervisor/core"

    def __init__(self, url, token, entity):
        self.entity = str(entity or "").strip()
        self.state = None
        self._said = False

        supervisor = os.environ.get("SUPERVISOR_TOKEN")
        if supervisor:
            self.url, self.token, self.own = self.SUPERVISOR, supervisor, True
            return

        # Not under the Supervisor: a hand run, or docker-compose. Then the
        # house's own address and token are all there is, and they come from
        # the link that names the dashboard.
        self.own = False

        # The ORIGIN, not the address as it stands. That `url:` is nearly
        # always a dashboard -- http://homeassistant:8123/lovelace/0 -- and
        # the first version of this appended /api/states/... straight to it,
        # asking for
        #   http://homeassistant:8123/lovelace/0/api/states/weather.home
        # which is a 404 every time. Reported as the weather simply not
        # appearing while the clock beside it worked.
        from urllib.parse import urlsplit

        split = urlsplit(str(url or ""))
        self.url = (f"{split.scheme}://{split.netloc}"
                    if split.scheme and split.netloc else "")
        self.token = str(token or "")

    def wanted(self):
        return bool(self.entity and self.url and self.token)

    def why_not(self):
        """Why an entity that was asked for cannot be read, or None.

        Silence is right when nobody asked for weather. It is wrong when
        somebody did and it never appears: that is the shape of fault this
        project keeps having to find twice.

        Under the Supervisor this can only ever be None, which is the point of
        the route above: there is no setting left to get wrong.
        """
        if not self.entity or self.wanted():
            return None
        return ("this is not running as a Home Assistant add-on, so it has no "
                "credential of its own, and no link carries both a Home "
                "Assistant address and a token: to read it with")

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


# What a launchers: entry may set, each the house launcher's own flat name
# without its prefix -- theme is launcher_theme, clock_size is
# launcher_clock_size -- so an entry is the house launcher with that panel's
# own values laid over it, and there is no second table to keep in step.
LAUNCHER_OWN = (
    "theme", "columns", "align", "tiles", "focus_color",
    "clock", "clock_size", "clock_color", "date_size", "date_color",
    "weather", "weather_size",
    "background", "background_motion", "background_blur", "background_dim",
    "slideshow", "slideshow_urls", "slideshow_seconds", "slideshow_fade",
    "slideshow_rescan",
)


def own_launchers(config, panels):
    """Each panel's own launchers: entry, keyed by the panel's name.

    A launcher belongs to ONE panel, independent of every other: its links,
    its columns, its clock, its wallpaper, all of it. Asked for as "panels
    salon has its own links, buttons, columns, every option, and panel 2 its
    own, independent, not the first panel's" -- after a shared list with a
    per-panel choice of names was reported as still not being that.

    It is a list of its own rather than a group inside a panel's entry for a
    reason in the Supervisor, not in taste: a list or a group nested inside a
    panel is REQUIRED there (supervisor/apps/options.py,
    _check_missing_options, only treats a plain "...?" as optional), so every
    configuration already saved would stop the add-on on this update. A new
    top-level list takes its default -- empty -- from this add-on's own
    options instead.

    An entry naming no panel, or a second entry for the same one, is said out
    loud: from the glass either is a launcher that never appears.
    """
    names = {str(p.get("name", "")).strip().lower(): str(p.get("name", ""))
             for p in panels}
    out = {}
    for entry in config.get("launchers") or []:
        if not isinstance(entry, dict):
            continue
        who = str(entry.get("panel", "")).strip().lower()
        if who not in names:
            say(f"Launcher: launchers: has an entry for panel "
                f"\"{entry.get('panel', '')}\", which is no panel here -- the "
                f"panels are {', '.join(sorted(names.values())) or 'none'}. "
                f"The name has to be the panel's own name:.")
            continue
        if who in out:
            say(f"[{names[who]}] launchers: has two entries for this panel; "
                f"the first is the one shown.")
            continue
        out[who] = entry
    return out


def launcher_config(config, entry):
    """The house's settings with one panel's own launcher laid over them.

    Only what the entry sets replaces the house's, so an entry that says
    nothing but its links still has the house's clock and wallpaper. An empty
    list counts as not set, which is what a form leaves behind.
    """
    merged = dict(config)
    for key in LAUNCHER_OWN:
        value = entry.get(key)
        if value is None or value == [] or not given(value):
            continue
        merged["launcher_" + key] = value
    merged["links"] = entry.get("links") or []
    return merged


def start_launcher(config, port=None, house_links=(), label=""):
    """Serve one page of links, if there are any, and say where it is.

    Returns the address, or None when there are no links -- in which case a
    panel asking for the launcher is told plainly rather than being pointed at
    an empty page it cannot get out of. `house_links` are consulted for the
    weather's address when this launcher's own links carry none.
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
        tiles=str(config.get("launcher_tiles") or "cards"),
        focus_color=str(config.get("launcher_focus_color")
                        or launcher.FOLLOW_THEME),
        motion=truthy(config.get("launcher_background_motion", False)),
        slideshow=truthy(config.get("launcher_slideshow", False)),
        every=config.get("launcher_slideshow_seconds", 30),
        fade=config.get("launcher_slideshow_fade", 1),
        rescan=config.get("launcher_slideshow_rescan", 60),
        urls=config.get("launcher_slideshow_urls") or [],
        port=launcher.PORT if port is None else port,
        weather=Weather(
            # The dashboard's own link is what has the address and the
            # token now, and it is the only thing here that ever had a use
            # for either.
            *home_assistant_link({"links": list(links) + list(house_links)}),
            config.get("launcher_weather"),
        ).start(),
    )
    if where is not None:
        say(f"Launcher{label}: {len(links)} link(s) at {where}")
    return where


def start_launchers(config, panels):
    """The house's launcher, and one of its own for each panel that has one.

    Returns the house's address (None when nothing needs it, or it has no
    links) and, per panel name, its own address and its launchers: entry.
    Only panels whose url is "launcher" are served; an entry for a panel that
    shows a page of its own is said, not silently ignored.
    """
    entries = own_launchers(config, panels)
    house = config.get("links") or []
    own = {}
    wants_house = False
    for panel in panels:
        name = str(panel.get("name", "")).strip()
        entry = entries.get(name.lower())
        on_launcher = (str(panel.get("url", "")).strip().lower()
                       == LAUNCHER_KEYWORD)
        if entry is None:
            wants_house = wants_house or on_launcher
            continue
        if not on_launcher:
            say(f"[{name}] has an entry under launchers:, but its url is not "
                f"\"{LAUNCHER_KEYWORD}\", so it is not shown. Put "
                f"\"{LAUNCHER_KEYWORD}\" in this panel's url to use it.")
            continue
        if not (entry.get("links") or []):
            say(f"[{name}] its entry under launchers: has no links, so its "
                f"launcher would be empty; it shows the house's instead.")
            wants_house = True
            continue
        where = start_launcher(
            launcher_config(config, entry),
            port=launcher.ANY_PORT if launcher else None,
            house_links=house, label=f" [{name}]")
        if where is None:
            wants_house = True
        else:
            own[name.lower()] = (where, entry)
    shared = start_launcher(config) if wants_house else None
    return shared, own


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


# The form is grouped; nothing below it is, and nothing below it needs to be.
# One table says where each grouped key used to live, and one function puts it
# back there -- so regrouping the form cost this function rather than every
# reader of every setting. The names on the right are what the rest of this
# file, launcher.py and ha_send.py all still speak.
_GROUPED = {
    "launcher": {
        "theme": "launcher_theme",
        "columns": "launcher_columns",
        "align": "launcher_align",
        "tiles": "launcher_tiles",
        "focus_color": "launcher_focus_color",
        "clock": {"show": "launcher_clock", "size": "launcher_clock_size",
                  "color": "launcher_clock_color"},
        "date": {"size": "launcher_date_size", "color": "launcher_date_color"},
        "weather": {"entity": "launcher_weather",
                    "size": "launcher_weather_size"},
        "background": {"source": "launcher_background",
                       "motion": "launcher_background_motion",
                       "blur": "launcher_background_blur",
                       "dim": "launcher_background_dim"},
        # enabled rather than on: an `on` key reads as the boolean true in
        # YAML 1.1, so the key would vanish from the form it names.
        "slideshow": {"enabled": "launcher_slideshow",
                      "urls": "launcher_slideshow_urls",
                      "seconds": "launcher_slideshow_seconds",
                      "fade": "launcher_slideshow_fade",
                      "rescan": "launcher_slideshow_rescan"},
    },
    # These two carry the same names on both sides: they are grouped for the
    # eye, not renamed.
    "defaults": {k: k for k in ("port", "fps", "quality", "max_rate",
                                "keyboard",
                                "keep_profile", "locale", "homekit")},
    "debug": {k: k for k in ("stats", "show_media", "show_touches")},
}
# A panel's own two groups. Everything in advanced: keeps its own name, so it
# needs no table -- only touch: renames.
_PANEL_GROUPED = {
    "touch": {"rotate": "touch_rotate", "mirror_x": "touch_mirror_x",
              "mirror_y": "touch_mirror_y"},
}


def _spread(source, plan, into):
    """Copy one group's values out to the flat names, recursing on subgroups."""
    if not isinstance(source, dict):
        return
    for name, target in plan.items():
        value = source.get(name)
        if isinstance(target, dict):
            _spread(value, target, into)
        elif value is not None:
            # Plain assignment rather than setdefault: the flat keys are gone
            # from the schema in 4.0.0, so nothing can already hold one unless
            # somebody wrote it by hand -- and then the group they also wrote
            # is the more deliberate of the two.
            into[name if target is None else target] = value


def regroup(config):
    """Turn the grouped form into the flat keys everything else reads.

    A hand-written options file in the old flat shape passes through untouched,
    which is what keeps panels.example.json and UDISP_CONFIG working.
    """
    for group, plan in _GROUPED.items():
        _spread(config.get(group), plan, config)
    for panel in config.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        for group, plan in _PANEL_GROUPED.items():
            _spread(panel.pop(group, None), plan, panel)
        advanced = panel.pop("advanced", None)
        if isinstance(advanced, dict):
            for key, value in advanced.items():
                if value is not None:
                    panel[key] = value
    return config


def load_panels():
    """Every panel to serve, as dictionaries of ha_send.py's options."""
    global _config
    for path in ("/data/options.json", os.environ.get("UDISP_CONFIG")):
        if path and os.path.exists(path):
            with open(path) as handle:
                config = json.load(handle)
            _config = regroup(config)
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


def profile_name(panel):
    """The directory name this panel's profile has, kept or not.

    Separate from profile_for because the sweep below has to ask a different
    question: not "does this panel want a profile" but "whose is this folder".
    A panel with keep_profile off still OWNS the name -- turning the option off
    should not make the sweep treat what is there as somebody else's and
    delete it.
    """
    who = str(panel.get("name") or panel.get("host") or "panel")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in who)
    return safe or "panel"


def profile_for(panel):
    """This panel's profile directory, or None if it is not to keep one.

    One per panel and never shared: Chromium locks a profile directory, and a
    second browser pointed at the same one refuses to start at all. The name
    comes from the panel's own, reduced to something a filesystem is happy
    with, and falls back to its address when it has none.
    """
    if str(panel.get("keep_profile", True)).lower() in ("false", "no", "0"):
        return None
    return os.path.join(PROFILES, profile_name(panel))


def folder_size(path):
    """How many bytes are under here. Unreadable files count as nothing."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def sweep_profiles(panels):
    """Remove the profiles of panels that are no longer configured.

    A profile is a browser's whole home -- cookies, local storage, and several
    hundred megabytes of cache it fills on its own -- and nothing ever removed
    one. A panel renamed or taken out of the list left its folder behind for
    good, so /data grew by a browser's worth every time somebody tried a name
    and changed their mind. Reported from a real add-on at 2.1 GB, of which
    two thirds belonged to panels that did not exist any more.

    The list is the only thing that knows, which is why this lives here and
    not in the sender. Three guards, and the first is the one that matters:

    - Nothing is swept when no panels are configured. main() refuses to run in
      that state anyway, but a configuration that failed to load must never
      be read as "this house has no panels, delete everything".
    - Only direct children of PROFILES, and only directories. A file sitting
      there is somebody's, not ours.
    - A panel owns its name whether or not it keeps a profile, so switching
      keep_profile off does not hand the folder to the sweep.
    """
    if not panels:
        return
    if not os.path.isdir(PROFILES):
        return
    keep = {profile_name(panel) for panel in panels}
    for name in sorted(os.listdir(PROFILES)):
        if name in keep:
            continue
        path = os.path.join(PROFILES, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        freed = folder_size(path)
        try:
            shutil.rmtree(path)
        except OSError as err:
            say(f"could not remove the profile of \"{name}\", which is not a "
                f"panel any more ({err})")
            continue
        say(f"removed the browser profile of \"{name}\", which is not a panel "
            f"any more -- {freed / 1e6:.0f} MB")

    # And say what the ones being kept cost, because otherwise nobody finds
    # out until /data is full. A browser's cache is bounded now, so a figure
    # that keeps climbing past a few hundred megabytes is a bug rather than a
    # panel that has been running a long time -- and this is the line that
    # makes that visible without a shell inside the container. It is a walk of
    # the tree at startup, which is stat calls rather than reads.
    #
    # A panel with keep_profile off is the case the guard above creates and
    # nothing else would report: it still owns its folder, so the sweep leaves
    # it alone, and it no longer opens it, so the folder sits there for ever
    # with nobody reading it. That is right -- switching the option back on
    # should find what was there -- but it must not be silent, or somebody who
    # turned the option off to save space watches a gigabyte not move and has
    # no way to learn why.
    wants = {panel_name: profile_for(panel) is not None
             for panel in panels
             for panel_name in (profile_name(panel),)}
    for name in sorted(keep):
        path = os.path.join(PROFILES, name)
        if not os.path.isdir(path):
            continue
        size = folder_size(path) / 1e6
        if wants.get(name, True):
            say(f"the browser profile of \"{name}\" is {size:.0f} MB")
        else:
            say(f"\"{name}\" has keep_profile off, so it starts a fresh "
                f"browser every time and the {size:.0f} MB already under "
                f"{PROFILES}/{name} is kept but never opened. Turn "
                f"keep_profile back on to use it again, or remove that panel "
                f"from the list once to have this sweep take it away.")


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
        "max_rate",
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
    if panel.get("on_launcher"):
        argv.append("--not-home-assistant")
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
    # And a byte rate per link, for the film that needs one.
    for link in panel.get("page_rate") or []:
        argv += ["--page-rate", link]
    # And the token of the Home Assistant a link opens. Not a panel setting:
    # a token belongs to an origin, and the link is what names one.
    for link in panel.get("page_token") or []:
        argv += ["--page-token", link]
    for key in (
        "touch_mirror_x",
        "touch_mirror_y",
        "no_touch",
        "stereo",
        "freeze_animations",
        "stats",
        "show_media",
        "show_touches",
    ):
        # Accept the string forms a hand-written JSON file may carry.
        value = panel.get(key)
        if value is True or str(value).lower() in ("true", "yes", "1"):
            argv.append(f"--{key.replace('_', '-')}")
    # Not named after the option, because it is wider than the option: it
    # opens the sender's stdin to whatever started it. The HomeKit accessory
    # is the only thing that uses it today.
    if str(panel.get("homekit", "")).strip().lower() in ("true", "yes", "1"):
        argv.append("--control")
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


class Remote:
    """A way in to one panel's sender that survives the sender restarting.

    The HomeKit accessory is started once and lives for as long as the
    add-on; a sender is a child process that dies and is started again on a
    backoff. So the accessory holds one of these rather than a pipe, and this
    carries whichever process is current -- otherwise the first crash would
    leave a paired remote writing into a closed pipe for ever, which from the
    sofa is a remote that simply stopped.

    A press for a panel whose sender is down is DROPPED, deliberately, and
    said once. Queueing it would replay a button somewhere in the next
    minute, at a moment nobody asked for.
    """

    def __init__(self, name):
        self.name = name
        self._process = None
        self._lock = threading.Lock()
        self._complained = False
        # The last volume the telephone set, re-sent to every sender that
        # starts. A key is a moment and is dropped when nobody is there; a
        # volume is a SETTING, and a sender that restarted at full volume
        # after somebody had turned it down would be the panel shouting.
        self._volume = None

    def set_process(self, process):
        with self._lock:
            self._process = process
            if process is not None:
                self._complained = False
                if self._volume is not None and process.stdin is not None:
                    try:
                        process.stdin.write(self._volume)
                        process.stdin.flush()
                    except (OSError, ValueError):
                        pass

    def send(self, kind, body):
        if kind == "home":
            line = "home\n"
        elif kind == "volume":
            line = f"volume {body}\n"
            self._volume = line
        else:
            line = f"key {body}\n"
        with self._lock:
            process = self._process
            if process is None or process.stdin is None:
                if not self._complained:
                    self._complained = True
                    say(f"[{self.name}] a remote pressed a key while this "
                        f"panel's sender was not running -- dropped")
                return False
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (OSError, ValueError):
                # The child went away between the check and the write. Not
                # worth a line of its own: the restart says so already.
                return False
        return True


def serve(panel, name, stop, remote=None):
    """Run one panel's sender, restarting it until asked to stop."""
    seed_profile(panel, name)
    delay = RESTART_DELAY_S
    while not stop.is_set():
        started = time.monotonic()
        say(f"[{name}] starting")
        try:
            process = subprocess.Popen(
                command_for(panel),
                stdin=subprocess.PIPE if remote is not None else None,
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
        if remote is not None:
            remote.set_process(process)
        # Prefix every line, so one log can carry several panels and still be
        # read.
        for line in process.stdout:
            say(f"[{name}] {line.rstrip()}")
        process.wait()
        if remote is not None:
            remote.set_process(None)
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


def route_to_launcher(panels, where, own=None):
    """Send every panel whose url is "launcher" to its launcher.

    Its own, from `own` (name -> (address, launchers: entry)), when it has
    one; the house's at `where` otherwise. A function rather than a stretch
    of main() so that the address each panel is really handed can be checked
    without starting any sender.
    """
    own = own or {}
    for panel in panels:
        if str(panel.get("url", "")).strip().lower() != LAUNCHER_KEYWORD:
            continue
        name = str(panel.get("name", "")).strip()
        address, entry = own.get(name.lower(), (None, None))
        if address is None and where is None:
            # Say which of the two it is. Telling somebody who filled the
            # list in that it is empty sends them to look at the one thing
            # that is right.
            why = (
                "no links are configured; add some under links, or give this "
                "panel its own under launchers"
                if not (_config.get("links") or [])
                else "the launcher could not be served, for the reason above"
            )
            say(f"[{name or 'panel'}] url is \"{LAUNCHER_KEYWORD}\" "
                f"but {why}, or point this panel at a page of its own")
            panel["url"] = ""
            continue
        mine = (entry.get("links") or []) if entry else \
            (_config.get("links") or [])
        # The token belongs to Home Assistant, and the page this panel now
        # opens is the launcher. Without saying so, the sender would install
        # the token for the launcher's own address -- and the frontend ignores
        # a record whose hassUrl is not its own, so the dashboard behind a
        # tile would ask to log in with the token sitting unused in its
        # storage. The Home Assistant LINK is the house's dashboard address
        # now, which is where the token lives too, so the two cannot disagree.
        # A panel's own links are asked first, then the house's.
        home, _ = home_assistant_link(
            {"links": list(mine) + list(_config.get("links") or [])})
        if given(panel.get("token")) and not given(panel.get("token_url")):
            if given(home):
                panel["token_url"] = home
            else:
                say(f"[{name or 'panel'}] this panel starts on the launcher "
                    f"and has a token of its own, but no link carries a Home "
                    f"Assistant address to attach it to. Give the dashboard's "
                    f"link a token, or a tile opening it will ask to log in.")
        if address is not None:
            panel["url"] = address
            panel["launcher_links"] = list(mine)
            say(f"[{name}] launcher: its own, {len(mine)} link(s)")
        else:
            panel["url"] = where
            # Said on every panel sharing the house's launcher, because that
            # is what was reported as each panel not having its own: the way
            # to give it one is an entry under launchers:, and the form never
            # shows an empty list's shape.
            say(f"[{name or 'panel'}] launcher: the house's, {len(mine)} "
                f"link(s). To give this panel its own links and settings, add "
                f"an entry for it under launchers: -- panel: "
                f"{name or '<name>'} with its own links:")
        # Not a form field: the supervisor never sees this, it is what the
        # rewrite above already knows. A page of links does not paint in
        # stages, and the sender otherwise spends three seconds waiting for a
        # staging it will never see -- every time the corner brings the panel
        # home.
        panel["on_launcher"] = True


def give_page_settings(panels, config):
    """Each panel's quality, frame limit, user agent and tokens per page.

    Read from the links that panel opens: its own launcher's when it has one,
    so a panel with its own launcher is independent in this too. Tokens are
    the exception and are asked of the house's links as well, after the
    panel's own: a token is a credential for an address rather than a look,
    and a Home Assistant tile without one asks to log in. A panel that opted
    out of Home Assistant carries none, which is all home_assistant: false
    ever meant.
    """
    house = config.get("links") or []
    for panel in panels:
        mine = panel.get("launcher_links")
        links = {"links": mine if mine is not None else house}
        for key, values in (("page_quality", page_quality_from(links)),
                            ("page_agent", page_agent_from(links)),
                            ("page_fps", page_fps_from(links)),
                            ("page_rate", page_rate_from(links))):
            if values:
                panel.setdefault(key, values)
        if str(panel.get("home_assistant", True)).strip().lower() \
                in ("false", "no", "0"):
            continue
        keys = page_tokens_from(
            {"links": list(mine or []) + list(house)} if mine is not None
            else links)
        if keys:
            panel.setdefault("page_token", keys)


def main():
    panels = load_panels()
    if not panels:
        say("No panels configured. Set them in the add-on options, or point "
            "$UDISP_CONFIG at a JSON file, or set HOST, URL and TOKEN.")
        return 1

    # Before anything is started, and after the list is known to be a real
    # one: a browser holding a profile open is not a folder to be removing.
    sweep_profiles(panels)

    # Before the check below, because a panel asking for the launcher has no
    # url of its own until this has given it one.
    where, own = start_launchers(_config, panels)
    route_to_launcher(panels, where, own)

    give_page_settings(panels, _config)

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
    remotes = []
    for index, panel in enumerate(panels, start=1):
        name = panel.get("name") or panel.get("host") or f"panel {index}"
        wants = str(panel.get("homekit", "")).strip().lower() in ("true", "yes", "1")
        remote = Remote(name) if wants else None
        if remote is not None:
            remotes.append((name, remote))
        thread = threading.Thread(target=serve, args=(panel, name, stop, remote),
                                  daemon=True)
        thread.start()
        threads.append(thread)
    say(f"Serving {len(threads)} panel(s)")

    # After the senders, so a press cannot arrive before there is anything to
    # hand it to -- and because the accessory is the accessory here: the
    # panels are the point and this must never be what delays them.
    televisions = []
    if remotes:
        if homekit is None:
            say("homekit is on but this build has no homekit.py in it. "
                "The panels are unaffected.")
        else:
            televisions = homekit.start(
                [(name, remote.send) for name, remote in remotes],
                HOMEKIT_DIR, say)

    # The container lives as long as the panels do.
    while not stop.is_set():
        stop.wait(1)
    for television in televisions:
        television.stop()
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
