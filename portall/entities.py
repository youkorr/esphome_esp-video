# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Tiles that act on Home Assistant, and the states they show.

A link whose url is an entity id -- `light.salon`, `scene.cinema`,
`sensor.salon_temperature` -- is not a page to open. It is a tile that does
something when it is pressed and says how things stand: a light lit in
colour, a shutter half open, the temperature in figures. The household writes
nothing new for it: the entity goes where the address would, the way the
word `files` already does for the Files page.

Two halves, and the page holds neither credential:

- THE STATES are followed by the add-on over Home Assistant's websocket,
  exactly as the avatar follows a voice assistant (voice.py, whose client this
  reuses): one connection per launcher that has such tiles, silent while
  nothing changes. The page asks 127.0.0.1 and is answered when something
  does -- so a still launcher still costs nothing, and a light switched on
  from a wall costs the one rectangle of its tile.
- A PRESS is a POST to the launcher, which calls the service through the same
  route the weather is read by -- the Supervisor's own credential under Home
  Assistant. A page's storage is shared with every site a panel visits, which
  is why a token never goes into one.

What a press does is decided here and only here, by domain, and deliberately
not for everything Home Assistant has: a lock or an alarm is SHOWN and never
acted on. A tap on a wall panel -- or a child's hand on it -- must not open
the front door.

An accessory must never cost the picture: every failure here is said once,
and a tile whose state cannot be read just says so.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request

import voice

ENTITY = re.compile(r"^([a-z_]+)\.([a-z0-9_]+)$")

# What a press does, by domain. `toggle` where the domain has one, because a
# tile has one button and a second press should undo the first.
ACTIONS = {
    "light": "toggle",
    "switch": "toggle",
    "fan": "toggle",
    "input_boolean": "toggle",
    "automation": "toggle",
    "cover": "toggle",
    "scene": "turn_on",
    "script": "turn_on",
    "button": "press",
    "input_button": "press",
}

# Shown and never acted on. A lock and an alarm on purpose (see above); a
# valve because it is water; the rest because one button cannot say what a
# press on them would mean.
SHOWN = frozenset({
    "sensor", "binary_sensor", "lock", "alarm_control_panel", "climate",
    "person", "device_tracker", "input_number", "number", "input_select",
    "select", "counter", "media_player", "vacuum", "timer", "sun",
    "water_heater", "humidifier", "valve", "weather",
})

# The icon a tile gets when the link names none, by domain -- names from the
# launcher's own list, so they are drawn the way any other tile's are.
ICONS = {
    "light": "light", "switch": "plug", "input_boolean": "plug",
    "fan": "fan", "cover": "shutter", "scene": "film", "script": "film",
    "button": "power", "input_button": "power", "automation": "robot",
    "binary_sensor": "motion", "lock": "lock", "alarm_control_panel": "alarm",
    "climate": "thermometer", "water_heater": "water", "humidifier": "water",
    "valve": "water", "person": "home", "device_tracker": "home",
    "media_player": "music", "vacuum": "aspirateur", "timer": "timer",
    "sun": "sun", "weather": "sun",
}

# Of an entity's attributes, only what a tile draws. The rest -- a camera's
# access token, a media player's whole queue -- has no business in a page.
KEPT = ("unit_of_measurement", "device_class", "brightness",
        "current_position", "current_temperature", "hvac_action")

# How long a page's question is held open, as for the face.
HOLD_S = voice.HOLD_S


def entity_of(url):
    """The entity id a link's url names, or None for an ordinary address.

    Only a domain this file knows: `youtube.com` has the shape of an entity
    id and must stay a web address.
    """
    text = str(url or "").strip()
    match = ENTITY.match(text)
    if not match or (match.group(1) not in ACTIONS
                     and match.group(1) not in SHOWN):
        return None
    return text


def acts(entity):
    """Whether pressing this entity's tile does anything."""
    return str(entity or "").split(".")[0] in ACTIONS


def icon_name(entity):
    """The default icon name for an entity's tile."""
    return ICONS.get(str(entity or "").split(".")[0], "")


def is_tile(link):
    """Whether a configured link is an entity tile rather than a page."""
    return isinstance(link, dict) and entity_of(link.get("url")) is not None


class EntityStates:
    """Follows some entities and tells waiting pages when one of them moves."""

    RETRY_FIRST_S = 2.0
    RETRY_MAX_S = 60.0

    def __init__(self, base, token, entities, label=""):
        self.url = voice.websocket_address(base) if base else ""
        self.rest = (str(base).rstrip("/") + "/api") if base else ""
        self.token = str(token or "")
        self.entities = sorted({e for e in entities if entity_of(e)})
        self.label = label
        self.states = {}
        self.version = 0
        self._changed = threading.Condition()
        self._said = set()

    def say(self, key, text):
        """Once per distinct thing, so a retry loop cannot fill the log."""
        if key in self._said:
            return
        self._said.add(key)
        print(f"Tiles{self.label}: {text}", flush=True)

    def _bump(self):
        self.version += 1
        self._changed.notify_all()

    def _put(self, entity, state, attributes):
        kept = {k: attributes[k] for k in KEPT if k in attributes}
        with self._changed:
            now = {"s": state, "a": kept}
            if self.states.get(entity) != now:
                self.states[entity] = now
                self._bump()

    def _change(self, entity, plus, minus):
        with self._changed:
            was = self.states.get(entity) or {"s": None, "a": {}}
            now = {"s": was["s"], "a": dict(was["a"])}
            if "s" in plus:
                now["s"] = plus["s"]
            for k, v in (plus.get("a") or {}).items():
                if k in KEPT:
                    now["a"][k] = v
            for k in (minus.get("a") or []):
                now["a"].pop(k, None)
            if now != was:
                self.states[entity] = now
                self._bump()

    def snapshot(self):
        with self._changed:
            return {e: dict(v) for e, v in self.states.items()}

    def wait(self, since, timeout=HOLD_S):
        """(version, states) once the version differs from `since`, or at timeout."""
        with self._changed:
            if since == self.version:
                self._changed.wait_for(lambda: self.version != since, timeout)
            return self.version, {e: dict(v) for e, v in self.states.items()}

    def start(self):
        if not self.entities:
            return self
        if not self.url or not self.token:
            self.say("route", "no way to reach Home Assistant, so the tiles "
                     "for " + ", ".join(self.entities) + " show nothing and "
                     "do nothing. Under Home Assistant this cannot happen; "
                     "run by hand, it needs a link that carries the "
                     "dashboard's address and a token.")
            return self
        threading.Thread(target=self._run, daemon=True,
                         name="portall-tiles").start()
        return self

    def _run(self):
        wait = self.RETRY_FIRST_S
        while True:
            began = time.monotonic()
            try:
                wait = self._session() or wait
            except (OSError, voice.Closed, ValueError) as err:
                self.say(f"lost:{type(err).__name__}",
                         f"lost Home Assistant ({err}); trying again. The "
                         f"tiles keep what they last showed meanwhile.")
            if time.monotonic() - began > 60:
                wait = self.RETRY_FIRST_S
            time.sleep(wait)
            wait = min(wait * 2, self.RETRY_MAX_S)

    def _session(self):
        """One connection, for as long as it lasts. Returns a delay, or None."""
        link = voice.Socket(self.url)
        try:
            first = link.recv()
            if first.get("type") != "auth_required":
                raise voice.Closed(f"said {first.get('type')!r} before asking "
                                   f"for a token")
            link.send({"type": "auth", "access_token": self.token})
            if link.recv().get("type") != "auth_ok":
                self.say("refused", "Home Assistant refused this add-on's "
                         "token, so the tiles show nothing and do nothing.")
                return self.RETRY_MAX_S
            link.send({"id": 1, "type": "subscribe_entities",
                       "entity_ids": self.entities})
            link.settimeout(120)
            first_batch = True
            while True:
                message = link.recv()
                if message.get("id") != 1:
                    continue
                if message.get("type") == "result":
                    if not message.get("success", False):
                        self.say("subscribe", "Home Assistant would not "
                                 "follow the tiles' entities: "
                                 + str(message.get("error")))
                        return self.RETRY_MAX_S
                    continue
                event = message.get("event") or {}
                added = event.get("a")
                for entity, state in (added or {}).items():
                    self._put(entity, state.get("s"), state.get("a") or {})
                if added is not None and first_batch:
                    first_batch = False
                    # Subscribing to an entity that does not exist is not an
                    # error to Home Assistant: it is simply never in the
                    # first batch. So the tile would wait for ever, and say
                    # nothing -- the silence this project keeps paying for.
                    missing = [e for e in self.entities if e not in added]
                    for entity in missing:
                        self._put(entity, "missing", {})
                    if missing:
                        self.say("missing:" + ",".join(missing),
                                 "Home Assistant has no "
                                 + ", ".join(missing) + ". Check the "
                                 "entity id in Settings > Devices & "
                                 "services > Entities; a tile shows it as "
                                 "not found until it exists.")
                for entity, change in (event.get("c") or {}).items():
                    self._change(entity, change.get("+") or {},
                                 change.get("-") or {})
                for entity in (event.get("r") or []):
                    self._put(entity, "missing", {})
        finally:
            link.close()

    def act(self, entity):
        """Call the service a press on this tile means. (ok, reason)."""
        domain = str(entity or "").split(".")[0]
        service = ACTIONS.get(domain)
        if service is None or entity not in self.entities:
            return False, "this tile does nothing when pressed"
        if not self.rest or not self.token:
            return False, "no way to reach Home Assistant"
        request = urllib.request.Request(
            f"{self.rest}/services/{domain}/{service}",
            data=json.dumps({"entity_id": entity}).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                answer.read()
            return True, ""
        except urllib.error.HTTPError as err:
            why = f"Home Assistant answered {err.code}"
        except Exception as err:  # noqa: BLE001 - any failure is reported
            why = f"Home Assistant could not be reached ({err})"
        self.say(f"act:{entity}:{why}",
                 f"pressing {entity} ({domain}.{service}) failed: {why}")
        return False, why
