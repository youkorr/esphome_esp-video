# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Notifications on the screens, and a screen's page sent to a telephone.

NOTIFICATIONS. Any automation fires the event `portall_notify` with a
`message` (and a `title`, `seconds`, `panel` if it wants), and every screen,
or the ones it names, shows those words over whatever it shows for a few
seconds: "Le lave-linge a terminé", "La porte du garage est ouverte". The
sender writes them onto the picture (ha_send.Toasts), so a film goes on, sound
and all, and no page can hide them. This follows the event over Home
Assistant's websocket, the way the voice links follow theirs.

A TELEPHONE. "Envoie ça sur mon téléphone": the screen hands the add-on the
address of the page it shows, and this sends it as a notification of Home
Assistant's own phone app, which opens it when touched. A page of Home
Assistant itself is sent as its PATH, which the app opens inside itself --
the full address is usually one only the house's network can reach. Read in
Home Assistant's own documentation for the app's notifications: `url` for
iOS, `clickAction` for Android, a path starting with / for a view of its own.

An accessory must never cost the picture: every failure here is said once and
retried, and no screen waits on any of it.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from voice import Closed, Socket, websocket_address
except ImportError:  # run from somewhere else, by a check
    from .voice import Closed, Socket, websocket_address

EVENT = "portall_notify"

SECONDS = 10
SECONDS_MIN = 3
SECONDS_MAX = 120


def ask(base, token, path, body=None, timeout=15, text=False):
    """One request to Home Assistant's REST API: its JSON, or its text."""
    request = urllib.request.Request(
        f"{base}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="GET" if body is None else "POST")
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        raw = answer.read().decode()
    return raw if text else json.loads(raw or "null")


# -- notifications ----------------------------------------------------------

def notice(data):
    """What an event asks to show, as the sender's notify line wants it.

    None when there is nothing to show. Its words are never printed by the
    add-on: an automation may put anything in them.
    """
    data = data if isinstance(data, dict) else {}
    title = " ".join(str(data.get("title") or "").split())[:80]
    message = " ".join(str(data.get("message") or "").split())[:300]
    if not title and not message:
        return None
    try:
        seconds = float(data.get("seconds") or SECONDS)
    except (TypeError, ValueError):
        seconds = SECONDS
    return {"title": title, "message": message,
            "seconds": max(SECONDS_MIN, min(SECONDS_MAX, seconds))}


def screens(data, names):
    """(the screens an event is for, the names it gave that are none).

    `panel:` (or `panels:`) as one name, a comma-separated string or a list;
    none means every screen.
    """
    data = data if isinstance(data, dict) else {}
    asked = data.get("panel") or data.get("panels") or ""
    if isinstance(asked, (list, tuple)):
        wanted = [str(a).strip() for a in asked if str(a).strip()]
    else:
        wanted = [a.strip() for a in str(asked).split(",") if a.strip()]
    if not wanted:
        return list(names), []
    known = {str(n).strip().lower(): str(n) for n in names}
    found, unknown = [], []
    for name in wanted:
        hit = known.get(name.lower())
        if hit is None:
            unknown.append(name)
        elif hit not in found:
            found.append(hit)
    return found, unknown


class EventFollower:
    """Calls handle(data) for every event of one type Home Assistant fires."""

    RETRY_FIRST_S = 2.0
    RETRY_MAX_S = 60.0

    def __init__(self, base, token, event, handle, say, label):
        self.url = websocket_address(base) if base else ""
        self.token = str(token or "")
        self.event = event
        self.handle = handle
        self._say = say
        self.label = label
        self._said = set()

    def say(self, key, text):
        if key in self._said:
            return
        self._said.add(key)
        self._say(f"{self.label}: {text}")

    def start(self):
        if not self.url or not self.token:
            self.say("route", "no way to reach Home Assistant, so its event "
                     f"{self.event} cannot be heard. Under Home Assistant "
                     f"this cannot happen.")
            return None
        threading.Thread(target=self._run, daemon=True,
                         name=f"portall-{self.event}").start()
        return self

    def _run(self):
        wait = self.RETRY_FIRST_S
        while True:
            began = time.monotonic()
            try:
                self._session()
            except (OSError, Closed, ValueError) as err:
                self.say(f"lost:{type(err).__name__}",
                         f"lost Home Assistant ({err}); trying again.")
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
                         f"token, so {self.event} cannot be heard.")
                time.sleep(self.RETRY_MAX_S)
                return
            link.send({"id": 1, "type": "subscribe_events",
                       "event_type": self.event})
            # Pinged while idle by Home Assistant and the Supervisor's proxy,
            # answered inside recv(), so a read this long is a lost link.
            link.settimeout(180)
            while True:
                message = link.recv()
                if message.get("id") != 1:
                    continue
                if message.get("type") == "result":
                    if not message.get("success", False):
                        self.say("subscribe", "Home Assistant would not let "
                                 f"this add-on listen for {self.event}.")
                        time.sleep(self.RETRY_MAX_S)
                        return
                    self.say("listening", f"listening for {self.event}")
                    continue
                event = message.get("event") or {}
                try:
                    self.handle(event.get("data") or {})
                except Exception as err:  # noqa: BLE001 - never the picture
                    self._say(f"{self.label}: could not act on {self.event} "
                              f"({err})")
        finally:
            link.close()


# -- a page sent to a telephone ---------------------------------------------

def phone_services(base, token, setting):
    """The phone app's notify services to send to: (names, why none).

    The setting names them -- "notify.mobile_app_iphone_de_lea", or without
    "notify." -- comma-separated. Empty sends to every telephone that has
    Home Assistant's app, which is what "mon téléphone" means in a house with
    one, and the log names them so a house with several can pick.
    """
    wanted = []
    for part in str(setting or "").split(","):
        name = part.strip()
        if name.startswith("notify."):
            name = name[len("notify."):]
        if name and name not in wanted:
            wanted.append(name)
    if wanted:
        return wanted, None
    try:
        domains = ask(base, token, "/api/services")
    except Exception as err:  # noqa: BLE001 - said by the caller
        return [], f"Home Assistant could not be asked for them ({err})"
    found = []
    for domain in domains or []:
        if domain.get("domain") != "notify":
            continue
        for name in sorted(domain.get("services") or {}):
            if name.startswith("mobile_app_"):
                found.append(name)
    if not found:
        return [], ("no telephone has Home Assistant's app -- install it and "
                    "sign in, and Home Assistant gives it a notify service")
    return found, None


def phone_address(url, origin):
    """What the phone is handed to open: Home Assistant's own pages as a path.

    The address the screen uses for Home Assistant is the house's -- often
    http://homeassistant:8123 or a local address the telephone cannot reach
    away from home. The app opens a path inside itself, wherever it is.
    """
    if origin:
        split = urllib.parse.urlsplit(url)
        here = f"{split.scheme}://{split.netloc}"
        if here.lower() == origin.rstrip("/").lower():
            path = split.path or "/"
            if split.query:
                path += "?" + split.query
            if split.fragment:
                path += "#" + split.fragment
            return path
    return url


def phone_message(shared, screen, origin, french):
    """The notification sent: what the phone shows, and what it opens."""
    url = str(shared.get("url") or "")
    host = urllib.parse.urlsplit(url).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    target = phone_address(url, origin)
    title = str(shared.get("title") or "").strip() or host or url
    if target.startswith("/"):
        where = "Home Assistant"
    else:
        where = host
    if french:
        message = (f"{where} · envoyé depuis l'écran {screen}. "
                   f"Touchez pour l'ouvrir.")
    else:
        message = f"{where} · sent from the {screen} screen. Tap to open it."
    return {"title": title[:120], "message": message,
            "data": {"url": target, "clickAction": target}}


def send_to_phones(base, token, services, payload):
    """Send one notification to each service. Returns (sent, failed)."""
    sent, failed = [], []
    for name in services:
        try:
            ask(base, token, f"/api/services/notify/{name}", payload)
            sent.append(name)
        except urllib.error.HTTPError as err:
            failed.append((name, f"Home Assistant answered {err.code}"))
        except Exception as err:  # noqa: BLE001
            failed.append((name, str(err)))
    return sent, failed
