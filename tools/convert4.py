#!/usr/bin/env python3
"""Turn a 3.x add-on configuration into the 4.0.0 shape.

The Supervisor drops a key the schema no longer knows BEFORE run.py ever sees
it, so no code inside the add-on can carry a 3.x configuration across. That is
what makes 4.0.0 a major version, and it is why this exists: paste what the
add-on's own "Edit in YAML" shows you, and paste the answer back.

    python3 tools/convert4.py < ancien.yaml > nouveau.yaml

No dependency beyond PyYAML, which the machine you run it on may already have;
otherwise run it wherever you have Python and copy the result.
"""
import sys

# The same map run.py reads in the other direction. One definition of where a
# setting lives would be better still, but this file has to run outside the
# add-on, on a laptop, with nothing of the add-on on it.
FLAT_TO_GROUP = {
    "launcher_theme": ("launcher", "theme"),
    "launcher_columns": ("launcher", "columns"),
    "launcher_align": ("launcher", "align"),
    "launcher_clock": ("launcher", "clock", "show"),
    "launcher_clock_size": ("launcher", "clock", "size"),
    "launcher_clock_color": ("launcher", "clock", "color"),
    "launcher_date_size": ("launcher", "date", "size"),
    "launcher_date_color": ("launcher", "date", "color"),
    "launcher_weather": ("launcher", "weather", "entity"),
    "launcher_weather_size": ("launcher", "weather", "size"),
    "launcher_background": ("launcher", "background", "source"),
    "launcher_background_motion": ("launcher", "background", "motion"),
    "launcher_background_blur": ("launcher", "background", "blur"),
    "launcher_background_dim": ("launcher", "background", "dim"),
    "launcher_slideshow": ("launcher", "slideshow", "enabled"),
    "launcher_slideshow_urls": ("launcher", "slideshow", "urls"),
    "launcher_slideshow_seconds": ("launcher", "slideshow", "seconds"),
    "launcher_slideshow_fade": ("launcher", "slideshow", "fade"),
    "launcher_slideshow_rescan": ("launcher", "slideshow", "rescan"),
    "port": ("defaults", "port"),
    "fps": ("defaults", "fps"),
    "quality": ("defaults", "quality"),
    "keyboard": ("defaults", "keyboard"),
    "keep_profile": ("defaults", "keep_profile"),
    "locale": ("defaults", "locale"),
    "stats": ("debug", "stats"),
    "show_media": ("debug", "show_media"),
    "show_touches": ("debug", "show_touches"),
}
# What a panel keeps at its own level. Everything else it carries moves into
# advanced:, except the three touch keys, which become touch:.
PANEL_PLAIN = ("name", "host", "url", "width", "height", "rotate")
PANEL_TOUCH = {"touch_rotate": "rotate", "touch_mirror_x": "mirror_x",
               "touch_mirror_y": "mirror_y"}
# Settings that no longer exist, each with the reason in its own words --
# named rather than dropped in silence, because a setting that disappears
# without one reads as a bug.
RETIRED = {
    "home_corner": "the corner is 14% of each axis and is no longer a setting",
    "home_hold": "the hold is one second and is no longer a setting",
    "home_taps": ("counting taps cost the corner -- the first of two cannot be "
                  "acted on until the window for the second has passed, so a "
                  "tap there was either late or swallowed, and a page may have "
                  "its own control under it. The way home is the hold and the "
                  "sideways swipe"),
}


def put(into, path, value):
    for step in path[:-1]:
        into = into.setdefault(step, {})
    into[path[-1]] = value


def convert(old):
    new, said = {}, []
    for key, value in old.items():
        if key == "panels":
            new["panels"] = [panel(p, said) for p in value]
        elif key == "links":
            new["links"] = value
        elif key == "debug" and isinstance(value, dict):
            # The 3.8.0 pilot already carried these; keep whichever is on.
            for name, on in value.items():
                if on:
                    put(new, ("debug", name), True)
        elif key in RETIRED:
            said.append(f"{key} no longer exists -- {RETIRED[key]}")
        elif key in FLAT_TO_GROUP:
            put(new, FLAT_TO_GROUP[key], value)
        else:
            said.append(f"{key} was not recognised and has been left at the "
                        f"top level -- check it against the documentation")
            new[key] = value
    return new, said


def panel(old, said):
    if not isinstance(old, dict):
        return old
    out, touch, advanced = {}, {}, {}
    for key, value in old.items():
        if key in PANEL_PLAIN:
            out[key] = value
        elif key in PANEL_TOUCH:
            touch[PANEL_TOUCH[key]] = value
        elif key in RETIRED:
            # These two were always panel settings, so this is the branch that
            # really fires and the one in convert() never does. Dropping them
            # here without a word is exactly what RETIRED exists to prevent: a
            # setting that vanishes silently reads as the converter losing it.
            said.append(f"{key} no longer exists, so it has been dropped from "
                        f"the panel {old.get('name', 'without a name')!r} -- "
                        f"{RETIRED[key]}")
        else:
            advanced[key] = value
    if touch:
        out["touch"] = touch
    if advanced:
        out["advanced"] = advanced
    return out


def main():
    try:
        import yaml
    except ImportError:
        sys.exit("This needs PyYAML: python3 -m pip install pyyaml")
    old = yaml.safe_load(sys.stdin.read())
    if not isinstance(old, dict):
        sys.exit("That does not look like an add-on configuration: paste what "
                 "the add-on's own Edit in YAML shows, braces and all.")
    new, said = convert(old)
    for line in said:
        print(f"# {line}", file=sys.stderr)
    yaml.safe_dump(new, sys.stdout, sort_keys=False, allow_unicode=True,
                   default_flow_style=False)


if __name__ == "__main__":
    main()
