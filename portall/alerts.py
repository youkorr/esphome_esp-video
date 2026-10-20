# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A camera on the screens when somebody is at the door.

Asked for by a household with a Reolink camera on the porch and no doorbell,
for other households that have a doorbell with a camera: when a sensor says
somebody is there, the chosen screens show that camera live, over whatever
they were showing, and go back to exactly that afterwards.

Everything here is Home Assistant's own vocabulary, so it works for any
camera and any sensor Home Assistant has, whatever made them:

- the trigger is an entity's STATE. A binary_sensor (a person detected, a
  doorbell's "visitor") fires when it turns on. An event entity -- the newer
  way a doorbell press arrives, device_class doorbell -- holds the time of its
  last event, so it fires whenever that time changes.
- the picture is Home Assistant's camera proxy stream, the same route its
  own frontend falls back to for a live camera with no other stream. For a
  camera that only gives stills, Home Assistant makes it from snapshots, at
  most two a second (MIN_STREAM_INTERVAL in its camera component); one with
  a real MJPEG stream gives that stream. Read in Home Assistant 2026.2.

The sensors are followed over Home Assistant's websocket, so nothing is asked
while nothing happens. An alert is not repeated for COOLDOWN_S after it last
fired: a person standing on the porch keeps a person sensor on and off for
minutes, and every screen in the house jumping back to the camera each time
is not an alert any more.

An accessory must never cost the picture: every failure is said once and
retried, and the screens go on showing what they showed.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

try:
    from voice import Closed, Socket, websocket_address
except ImportError:  # run from somewhere else, by a check
    from .voice import Closed, Socket, websocket_address

# An alert that fired is not shown again for this long, however often its
# sensor goes on and off in between.
COOLDOWN_S = 120.0

SECONDS = 30
SECONDS_MIN = 5
SECONDS_MAX = 300

# The sender says this, at the start of a line, when an alert it showed ran
# its time out with nobody touching the screen. A screen the add-on lit for
# that alert goes back in the dark then -- the add-on knows it lit it, the
# sender knows nobody touched it, and neither knows both. The same words are
# in ha_send.py, and tools/checkalerts.py holds the two to each other.
UNTOUCHED = "Alert: over, nobody touched the screen"
# And this when somebody closed it from the screen -- a touch, a key: somebody
# is using it now, so it is left alone and its own timeout puts it in the dark.
TOUCHED = "Alert: closed from the screen"
# Every line about an alert ending starts with this. One that ended some
# other way -- the screen went dark, it never lit -- leaves nothing to do.
ENDED = "Alert: "

# A state that says nothing about the sensor itself.
NO_STATE = ("unavailable", "unknown", "", None)


def _names(text):
    return [part.strip() for part in str(text or "").split(",")
            if part.strip()]


def parse(entries, panel_names, say):
    """The alerts asked for, checked, as plain dictionaries.

    An entry without a sensor or a camera, or naming only screens that do
    not exist, is said once and left out -- the rest still work.
    """
    known = {str(n).strip().lower(): str(n).strip() for n in panel_names}
    out = []
    for number, entry in enumerate(entries or [], start=1):
        if not isinstance(entry, dict):
            continue
        sensor = str(entry.get("sensor") or "").strip()
        camera = str(entry.get("camera") or "").strip()
        label = f"alert {number}"
        if not sensor or not camera:
            say(f"Alerts: {label} needs both a sensor and a camera -- left "
                f"out")
            continue
        if "." not in sensor or "." not in camera:
            say(f"Alerts: {label}: {sensor or '?'} and {camera or '?'} must "
                f"be Home Assistant entities, like binary_sensor.porch_person "
                f"and camera.porch -- left out")
            continue
        if not camera.startswith("camera."):
            say(f"Alerts: {label}: {camera} is not a camera (a camera's name "
                f"begins with camera.) -- left out")
            continue
        try:
            seconds = int(float(entry.get("seconds") or SECONDS))
        except (TypeError, ValueError):
            seconds = SECONDS
        seconds = max(SECONDS_MIN, min(SECONDS_MAX, seconds))
        wanted = _names(entry.get("panels"))
        panels = []
        for name in wanted:
            found = known.get(name.lower())
            if found is None:
                say(f"Alerts: {label} names the screen \"{name}\", and the "
                    f"screens are " + (", ".join(known.values()) or "none"))
            elif found not in panels:
                panels.append(found)
        if wanted and not panels:
            say(f"Alerts: {label} names no screen that exists -- left out")
            continue
        wake = entry.get("wake")
        wake = wake is True or str(wake).strip().lower() in ("true", "yes",
                                                              "1", "on")
        out.append({"sensor": sensor, "camera": camera, "seconds": seconds,
                    "panels": panels or None, "wake": wake})
    return out


def targets(alert, panel_names):
    """The screens an alert is shown on: those it names, or every one."""
    return list(alert["panels"]) if alert["panels"] else list(panel_names)


def fires(entity_id, last, new):
    """Whether a change of state to `new` is somebody being there.

    `last` is the last state the sensor had that was a real one -- not
    unavailable or unknown -- so a sensor that drops off the network and
    comes back on does not ring the bell. The state each sensor has when the
    add-on starts is recorded without being asked about (Alerts.changed), so
    one already on then does not ring it either.
    """
    if new in NO_STATE:
        return False
    if entity_id.startswith("event."):
        # The state IS the time of the last event, so a different time is a
        # new event -- including the first one an entity ever has, when the
        # last real state is None.
        return new != last
    return new == "on" and last != "on"


def camera_address(origin, camera, access_token):
    """Where the panel's own browser reads the camera live.

    Through the access token Home Assistant gives every camera for exactly
    this -- its frontend's own pictures use it -- so no credential of the
    house goes into a page. It changes every five minutes and the last two
    are accepted, so it is asked for afresh for every alert; a stream
    already open keeps going.
    """
    query = urllib.parse.urlencode({"token": access_token})
    return (f"{origin.rstrip('/')}/api/camera_proxy_stream/"
            f"{urllib.parse.quote(camera)}?{query}")


def ask(base, token, path, body=None, timeout=15):
    """One request to Home Assistant's REST API; the answer as JSON."""
    request = urllib.request.Request(
        f"{base}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        text = answer.read().decode() or "null"
    return json.loads(text)


class Alerts:
    """Follows the alerts' sensors and calls fire(alert) when one fires."""

    RETRY_FIRST_S = 2.0
    RETRY_MAX_S = 60.0

    def __init__(self, base, token, alerts, fire, say, clock=time.monotonic):
        self.url = websocket_address(base) if base else ""
        self.token = str(token or "")
        self.alerts = list(alerts)
        self.fire = fire
        self.say_line = say
        self.clock = clock
        # The last real state of each sensor, kept across reconnections so a
        # Home Assistant that restarts does not ring every bell.
        self.last = {}
        self.fired_at = {}
        self._said = set()

    def say(self, key, text):
        if key in self._said:
            return
        self._said.add(key)
        self.say_line(f"Alerts: {text}")

    def sensors(self):
        return sorted({a["sensor"] for a in self.alerts})

    def changed(self, entity_id, state, initial=False):
        """A sensor's new state. Fires the alerts it belongs to, if it should."""
        last = self.last.get(entity_id)
        if not initial and fires(entity_id, last, state):
            now = self.clock()
            for number, alert in enumerate(self.alerts):
                if alert["sensor"] != entity_id:
                    continue
                since = now - self.fired_at.get(number, -1e18)
                if since < COOLDOWN_S:
                    self.say_line(f"Alerts: {entity_id} again, "
                                  f"{since:.0f}s after the last alert -- "
                                  f"not shown twice inside "
                                  f"{COOLDOWN_S:.0f}s")
                    continue
                self.fired_at[number] = now
                try:
                    self.fire(alert)
                except Exception as err:  # noqa: BLE001 - never the loop
                    self.say_line(f"Alerts: {alert['camera']} could not be "
                                  f"shown ({err})")
        if state not in NO_STATE:
            self.last[entity_id] = state

    def start(self):
        if not self.alerts:
            return None
        if not self.url or not self.token:
            self.say("route", "no way to reach Home Assistant, so no alert "
                     "can be shown. Under Home Assistant this cannot "
                     "happen; run by hand, it needs a link that carries the "
                     "dashboard's address and a token.")
            return None
        threading.Thread(target=self._run, daemon=True,
                         name="portall-alerts").start()
        return self

    def _run(self):
        wait = self.RETRY_FIRST_S
        while True:
            began = time.monotonic()
            try:
                self._session()
            except (OSError, Closed, ValueError) as err:
                self.say(f"lost:{type(err).__name__}",
                         f"lost Home Assistant ({err}); trying again. No "
                         f"alert can be shown meanwhile.")
            if time.monotonic() - began > 60:
                wait = self.RETRY_FIRST_S
            time.sleep(wait)
            wait = min(wait * 2, self.RETRY_MAX_S)

    def _session(self):
        link = Socket(self.url)
        try:
            first = link.recv()
            if first.get("type") != "auth_required":
                raise Closed(f"said {first.get('type')!r} before asking for "
                             f"a token")
            link.send({"type": "auth", "access_token": self.token})
            if link.recv().get("type") != "auth_ok":
                self.say("refused", "Home Assistant refused this add-on's "
                         "token, so no alert can be shown.")
                time.sleep(self.RETRY_MAX_S)
                return
            wanted = self.sensors()
            link.send({"id": 1, "type": "subscribe_entities",
                       "entity_ids": wanted})
            # Heartbeats keep arriving while nothing happens, so a read that
            # takes several minutes is a connection that is gone.
            link.settimeout(120)
            while True:
                message = link.recv()
                if message.get("id") != 1:
                    continue
                if message.get("type") == "result":
                    if not message.get("success", False):
                        self.say("subscribe", "Home Assistant would not "
                                 "follow " + ", ".join(wanted))
                        time.sleep(self.RETRY_MAX_S)
                        return
                    continue
                event = message.get("event") or {}
                added = event.get("a")
                if added is not None:
                    for eid in wanted:
                        if eid in added:
                            self.say(f"follow:{eid}", f"following {eid}")
                            self.changed(eid, added[eid].get("s"),
                                         initial=True)
                        else:
                            self.say(f"missing:{eid}",
                                     f"{eid} does not exist in Home "
                                     f"Assistant -- that alert will never "
                                     f"fire. Its name is under Settings > "
                                     f"Devices & services > Entities.")
                for eid, change in (event.get("c") or {}).items():
                    plus = change.get("+") or {}
                    if eid in wanted and "s" in plus:
                        self.changed(eid, plus["s"])
        finally:
            link.close()
