# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Good morning: the day's weather and calendar, when a screen first wakes.

The first time a screen lights up in the morning, it says what the day holds
-- "Bonjour ! Aujourd'hui, ensoleillé, de 12 à 21 degrés. À l'agenda : à
10 h 30, Dentiste." -- on its own speaker, through the voice assistant that
lives on it, and shows the same as a notification. Once a day per screen,
between MORNING_FROM and MORNING_UNTIL, and only when it WAKES -- after it was
dark -- so a screen somebody is already using is never interrupted.

Everything comes from Home Assistant, read by the add-on:

- the CLOCK is Home Assistant's own (its template `now()`), so the morning
  is the house's morning whatever time zone this container keeps;
- the WEATHER is the screen's launcher weather entity: today's forecast
  through `weather.get_forecasts` (daily), its state when there is none;
- the CALENDAR is the calendars named in the settings, or every calendar
  Home Assistant has, through its REST view /api/calendars/<entity>, for
  what is left of the day;
- the VOICE is `assist_satellite.announce` on the screen's voice assistant --
  the one its avatar follows -- which says a text with the voice that answers
  its questions, and puts the satellite in "responding", which is what moves
  the avatar's mouth. A satellite that cannot announce, or none, still gets
  the notification.

Read in Home Assistant's own source (2026.2): assist_satellite/services.yaml
and entity.py for announce, weather/__init__.py for get_forecasts and its
?return_response route in api/__init__.py, calendar/__init__.py for the view.
"""

import json
import os
import urllib.parse

try:
    from notices import ask
except ImportError:  # run from somewhere else, by a check
    from .notices import ask

try:
    from launcher import SKY
except Exception:  # noqa: BLE001 - an icon is a nicety
    SKY = {}

# The hours, by Home Assistant's clock, a wake counts as the morning's.
MORNING_FROM = 5
MORNING_UNTIL = 12

# Where each screen's last greeting is kept, so a restart of the add-on in
# the morning does not greet a second time.
GREETED_FILE = "/data/morning.json"

# Home Assistant's weather conditions, said aloud. Its own list
# (weather/const.py ATTR_CONDITION_*), every one.
CONDITIONS = {
    "clear-night": ("nuit claire", "a clear night"),
    "cloudy": ("nuageux", "cloudy"),
    "exceptional": ("un temps exceptionnel", "exceptional weather"),
    "fog": ("du brouillard", "foggy"),
    "hail": ("de la grêle", "hail"),
    "lightning": ("de l'orage", "thunderstorms"),
    "lightning-rainy": ("orageux et pluvieux", "thunderstorms and rain"),
    "partlycloudy": ("partiellement nuageux", "partly cloudy"),
    "pouring": ("de fortes pluies", "heavy rain"),
    "rainy": ("pluvieux", "rainy"),
    "snowy": ("de la neige", "snow"),
    "snowy-rainy": ("neige et pluie", "sleet"),
    "sunny": ("ensoleillé", "sunny"),
    "windy": ("venteux", "windy"),
    "windy-variant": ("venteux et nuageux", "windy and cloudy"),
}

# Asked of Home Assistant's template engine: its own idea of today and now,
# and the end of today, all three in the house's time zone.
CLOCK = ("{{ now().strftime('%Y-%m-%d %H') }}|{{ now().isoformat() }}|"
         "{{ now().replace(hour=23, minute=59, second=59, microsecond=0)"
         ".isoformat() }}")


def is_morning(hour):
    return MORNING_FROM <= hour < MORNING_UNTIL


def local_now(base, token):
    """(today, hour, now, end of today), by Home Assistant's clock."""
    text = ask(base, token, "/api/template", {"template": CLOCK}, text=True)
    stamp, now, end = text.strip().split("|")
    day, hour = stamp.split()
    return day, int(hour), now, end


def _number(value):
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def read_weather(base, token, entity):
    """Today's weather: {condition, high, low, temperature, unit}, or None."""
    if not entity:
        return None
    state = ask(base, token, f"/api/states/{entity}")
    attributes = (state or {}).get("attributes") or {}
    out = {"condition": (state or {}).get("state"),
           "temperature": _number(attributes.get("temperature")),
           "unit": attributes.get("temperature_unit") or "",
           "high": None, "low": None}
    try:
        answer = ask(base, token,
                     "/api/services/weather/get_forecasts?return_response",
                     {"entity_id": entity, "type": "daily"})
        days = (((answer or {}).get("service_response") or {})
                .get(entity) or {}).get("forecast") or []
        if days:
            today = days[0]
            out["high"] = _number(today.get("temperature"))
            out["low"] = _number(today.get("templow"))
            out["condition"] = today.get("condition") or out["condition"]
    except Exception:  # noqa: BLE001 - its state is enough
        pass
    return out


def calendars_of(base, token, setting):
    """The calendars to read: those named, or every one Home Assistant has."""
    named = [part.strip() for part in str(setting or "").split(",")
             if part.strip()]
    if named:
        return named
    return [entry.get("entity_id") for entry in
            ask(base, token, "/api/calendars") or []
            if entry.get("entity_id")]


def read_events(base, token, calendars, start, end):
    """The rest of today's events: [{summary, time or None}], all day first."""
    query = urllib.parse.urlencode({"start": start, "end": end})
    events, seen = [], set()
    for calendar in calendars:
        try:
            found = ask(base, token, f"/api/calendars/{calendar}?{query}")
        except Exception:  # noqa: BLE001 - the other calendars still count
            continue
        for event in found or []:
            summary = " ".join(str(event.get("summary") or "").split())
            if not summary:
                continue
            begins = event.get("start") or {}
            when = begins.get("dateTime")
            moment = when[11:16] if isinstance(when, str) and \
                len(when) >= 16 else None
            key = (summary.lower(), moment)
            if key in seen:
                continue
            seen.add(key)
            events.append({"summary": summary[:60], "time": moment})
    events.sort(key=lambda e: (e["time"] is not None, e["time"] or ""))
    return events[:3]


def _clock(moment, french):
    hour, minute = moment.split(":")
    if french:
        return f"{int(hour)} h {minute}" if minute != "00" else \
            f"{int(hour)} h"
    return moment


def greeting(french, weather, events, looked_at_calendar):
    """(title, message, what is said aloud) for the day."""
    hello = "Bonjour !" if french else "Good morning!"
    said = [hello]
    shown = []
    icon = ""
    if weather:
        condition = str(weather.get("condition") or "").lower()
        words = CONDITIONS.get(condition)
        word = (words[0] if french else words[1]) if words else ""
        icon = SKY.get(condition, "")
        high, low = weather.get("high"), weather.get("low")
        temperature = weather.get("temperature")
        unit = weather.get("unit") or ("°C" if french else "°")
        if high is not None and low is not None:
            heard = (f"de {low} à {high} degrés" if french
                     else f"from {low} to {high} degrees")
            seen = (f"{low} à {high} {unit}" if french
                    else f"{low} to {high} {unit}")
        elif temperature is not None:
            heard = f"{temperature} degrés" if french \
                else f"{temperature} degrees"
            seen = f"{temperature} {unit}"
        else:
            heard = seen = ""
        if word or heard:
            said.append(("Aujourd'hui, " if french else "Today, ")
                        + ", ".join(p for p in (word, heard) if p) + ".")
            shown.append(", ".join(p for p in (word.capitalize(), seen) if p))
    if events:
        items_said, items_shown = [], []
        for event in events:
            if event["time"]:
                when = _clock(event["time"], french)
                items_said.append((f"à {when}, " if french
                                   else f"at {when}, ") + event["summary"])
                items_shown.append(f"{when} {event['summary']}")
            else:
                items_said.append(event["summary"])
                items_shown.append(event["summary"])
        said.append(("À l'agenda : " if french else "On the calendar: ")
                    + " ; ".join(items_said) + ".")
        shown.extend(items_shown)
    elif looked_at_calendar:
        said.append("Rien à l'agenda aujourd'hui." if french
                    else "Nothing on the calendar today.")
        shown.append("Rien à l'agenda" if french else "Nothing on the "
                     "calendar")
    title = f"{icon} {hello}".strip()
    return title, " · ".join(shown), " ".join(said)


def satellite_for(base, token, wanted):
    """The voice assistant to say it with: (entity, why not)."""
    asked = str(wanted or "").strip()
    if asked.lower() in ("off", "none", "false", "no"):
        return None, "this screen's avatar follows no voice assistant (off)"
    states = ask(base, token, "/api/states") or []
    satellites = {s.get("entity_id"): s for s in states
                  if str(s.get("entity_id", "")).startswith(
                      "assist_satellite.")}
    if asked:
        state = satellites.get(asked)
        if state is None:
            return None, f"{asked} is not a voice assistant Home Assistant has"
    elif len(satellites) == 1:
        asked, state = next(iter(satellites.items()))
    elif not satellites:
        return None, "Home Assistant has no voice assistant"
    else:
        return None, ("Home Assistant has several voice assistants -- put "
                      "this screen's in its avatar_voice")
    features = (state.get("attributes") or {}).get("supported_features") or 0
    try:
        features = int(features)
    except (TypeError, ValueError):
        features = 0
    if not features & 1:
        return None, f"{asked} cannot make announcements"
    return asked, None


def announce(base, token, satellite, text):
    """Say it on the screen, with no chime before: it is a greeting."""
    ask(base, token, "/api/services/assist_satellite/announce",
        {"entity_id": satellite, "message": text, "preannounce": False},
        timeout=60)


def greeted_today(name, day, path=None):
    """True if `name` was greeted on `day`; otherwise notes that it is now."""
    path = path or GREETED_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            seen = json.load(handle)
    except (OSError, ValueError):
        seen = {}
    if not isinstance(seen, dict):
        seen = {}
    if seen.get(name) == day:
        return True
    seen[name] = day
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(seen, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    return False
