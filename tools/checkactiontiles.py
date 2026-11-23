#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Tiles that act: a link whose url is a Home Assistant entity.

WHY THIS EXISTS. Asked for as the first of a list of improvements to the page
of links, *"des tuiles qui agissent au lieu d'ouvrir une page"*. A tile whose
url is `light.salon` switches the light and shows whether it is on; a sensor's
tile shows its value; a lock's tile shows its state and does nothing at all.
What a press does, and what may be pressed, are the parts that can hurt a
household, so they are what this checks hardest:

  - which urls are entities (youtube.com is not), and which of them act;
  - the page: such a tile carries the entity and "#", ordinary tiles are what
    they were, and a page with no such tile carries no script for them;
  - the add-on keeps them out of the voice links (an "ouvre" bound to a
    light's name would take "allume le salon" away from Home Assistant);
  - and against a stand-in Home Assistant that speaks the real websocket and
    REST shapes, in a real browser in French: every state in words, a press
    calling the right service with the add-on's credential, the change coming
    back and lighting the tile, a change made elsewhere reaching it, a scene
    saying it ran, a failed call saying so, a lock and a foreign page refused,
    the sender never asked to follow such a tile, and no credential anywhere
    in the page.

Needs Playwright (the add-on's own browser) for the last part, which it skips
without it; takes $CHROMIUM.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import entities  # noqa: E402
import launcher  # noqa: E402
import run as runner  # noqa: E402

TOKEN = "fixture-token-not-a-secret"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class FakeHA:
    """Home Assistant's websocket and REST API on one port, as tiles use them."""

    def __init__(self, states):
        # entity -> {"s": state, "a": attributes}, the shape HA compresses to.
        self.states = {e: {"s": s, "a": dict(a)} for e, (s, a) in states.items()}
        self.calls = []
        self.failing = set()
        self.peers = []
        self.lock = threading.Lock()
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def _accept(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        reader = conn.makefile("rb")
        try:
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                line = reader.readline()
                if not line:
                    return
                head += line
            lines = head.decode().split("\r\n")
            method, path = lines[0].split()[:2]
            headers = {k.lower(): v.strip() for k, _, v in
                       (line.partition(":") for line in lines[1:] if line)}
            if headers.get("upgrade", "").lower() == "websocket":
                self._websocket(conn, reader, headers)
            else:
                body = reader.read(int(headers.get("content-length") or 0))
                self._rest(conn, method, path, headers, body)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _rest(self, conn, method, path, headers, body):
        status, answer = 404, b"[]"
        if headers.get("authorization") != f"Bearer {TOKEN}":
            status, answer = 401, b"401: Unauthorized"
        elif method == "POST" and path.startswith("/api/services/"):
            domain, service = path[len("/api/services/"):].split("/")
            entity = json.loads(body or b"{}").get("entity_id")
            with self.lock:
                self.calls.append((domain, service, entity))
            if entity in self.failing:
                status, answer = 500, b"500 Internal Server Error"
            else:
                status = 200
                self._apply(domain, service, entity)
        conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Length: {len(answer)}\r\n"
                     f"Content-Type: application/json\r\nConnection: close"
                     f"\r\n\r\n".encode() + answer)

    def _apply(self, domain, service, entity):
        was = self.states.get(entity)
        if was is None:
            return
        if domain == "light" and service == "toggle":
            if was["s"] == "on":
                self.change(entity, "off", {}, ["brightness"])
            else:
                self.change(entity, "on", {"brightness": 153})
        elif domain == "cover" and service == "toggle":
            self.change(entity, "closed" if was["s"] != "closed" else "open")
        elif domain == "switch" and service == "toggle":
            self.change(entity, "off" if was["s"] == "on" else "on")
        elif domain == "scene":
            self.change(entity, "2026-11-23T14:00:00+00:00")

    # --- the websocket ---------------------------------------------------

    def _websocket(self, conn, reader, headers):
        accept = base64.b64encode(hashlib.sha1(
            (headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket"
                      "\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                      f"{accept}\r\n\r\n").encode())
        peer = {"conn": conn, "reader": reader, "sub": None, "wanted": []}
        self._send(peer, {"type": "auth_required", "ha_version": "2026.11.0"})
        if self._recv(peer).get("access_token") != TOKEN:
            self._send(peer, {"type": "auth_invalid"})
            return
        self._send(peer, {"type": "auth_ok", "ha_version": "2026.11.0"})
        while True:
            msg = self._recv(peer)
            if msg.get("type") != "subscribe_entities":
                continue
            peer["sub"], peer["wanted"] = msg["id"], msg.get("entity_ids") or []
            self._send(peer, {"id": msg["id"], "type": "result",
                              "success": True, "result": None})
            with self.lock:
                first = {e: {"s": v["s"], "a": v["a"], "c": "x", "lc": 1.0}
                         for e, v in self.states.items() if e in peer["wanted"]}
                self.peers.append(peer)
            self._send(peer, {"id": msg["id"], "type": "event",
                              "event": {"a": first}})

    def _send(self, peer, obj):
        payload = json.dumps(obj).encode()
        head = bytearray([0x81])
        if len(payload) < 126:
            head.append(len(payload))
        else:
            head.append(126)
            head += struct.pack(">H", len(payload))
        peer["conn"].sendall(bytes(head) + payload)

    def _recv(self, peer):
        reader = peer["reader"]
        while True:
            first, second = reader.read(2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", reader.read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", reader.read(8))[0]
            mask = reader.read(4)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(reader.read(size)))
            if first & 0x0F == 0x8:
                raise ConnectionError("closed")
            if first & 0x0F == 0x1:
                return json.loads(data)

    def change(self, entity, state, plus_attrs=None, minus_attrs=None):
        """A state moving, sent the way Home Assistant compresses it."""
        with self.lock:
            now = self.states.setdefault(entity, {"s": None, "a": {}})
            now["s"] = state
            now["a"].update(plus_attrs or {})
            for key in minus_attrs or []:
                now["a"].pop(key, None)
            diff = {"+": {"s": state, "lc": time.time()}}
            if plus_attrs:
                diff["+"]["a"] = plus_attrs
            if minus_attrs:
                diff["-"] = {"a": list(minus_attrs)}
            for peer in list(self.peers):
                if entity in peer["wanted"]:
                    try:
                        self._send(peer, {"id": peer["sub"], "type": "event",
                                          "event": {"c": {entity: diff}}})
                    except OSError:
                        pass


STATES = {
    "light.salon": ("off", {"friendly_name": "Salon"}),
    "cover.volet": ("open", {"current_position": 40}),
    "sensor.temperature": ("21.46", {"unit_of_measurement": "°C",
                                     "device_class": "temperature"}),
    "binary_sensor.porte": ("on", {"device_class": "door"}),
    "lock.entree": ("locked", {}),
    "scene.cinema": ("2026-11-20T10:00:00+00:00", {}),
    "switch.panne": ("off", {}),
}

LINKS = [
    {"name": "Lumière", "url": "light.salon", "group": "Maison"},
    {"name": "Volet", "url": "cover.volet", "group": "Maison"},
    {"name": "Température", "url": "sensor.temperature", "group": "Maison"},
    {"name": "Porte", "url": "binary_sensor.porte", "group": "Maison"},
    {"name": "Entrée", "url": "lock.entree", "group": "Maison"},
    {"name": "Cinéma", "url": "scene.cinema", "group": "Maison"},
    {"name": "Prise", "url": "switch.panne", "group": "Maison"},
    {"name": "Absente", "url": "light.absente", "group": "Maison"},
    {"name": "YouTube", "url": "https://www.youtube.com/tv", "icon": "youtube"},
]


def pure():
    print("Which urls are entities, and which act")
    check("light.salon is an entity", entities.entity_of("light.salon") == "light.salon")
    check("youtube.com is an address, not an entity",
          entities.entity_of("youtube.com") is None)
    check("an http address is not an entity",
          entities.entity_of("http://homeassistant:8123/lovelace/0") is None)
    check("a light acts, a lock does not",
          entities.acts("light.salon") and not entities.acts("lock.entree"))
    check("a sensor is shown and does not act",
          entities.entity_of("sensor.x") == "sensor.x"
          and not entities.acts("sensor.x"))
    check("an alarm is never acted on",
          not entities.acts("alarm_control_panel.maison"))


def page():
    print("The page")
    body = launcher.render(LINKS, theme="dark")
    check("a light's tile carries its entity and no address",
          'data-entity="light.salon"' in body
          and 'class="tile act acts" href="#"' in body)
    check("a lock's tile is shown but cannot be pressed",
          'class="tile act" href="#" data-entity="lock.entree"' in body)
    check("an ordinary link is what it was",
          'class="tile" href="https://www.youtube.com/tv"' in body)
    check("a light with no icon gets the bulb", "\U0001F4A1" in body)
    check("the script that keeps them is on this page",
          launcher.ENTITIES_PATH in body and launcher.ACT_PATH in body)
    plain = launcher.render([LINKS[-1]], theme="dark")
    check("a page with no such tile carries nothing for them",
          launcher.ENTITIES_PATH not in plain and "a.tile.act" not in plain)


def voice_links():
    print("The voice links")
    seen = {}

    class Recorder:
        def __init__(self, base, token, links, act, say):
            seen["links"] = links

        def start(self):
            return self

    real, runner.voicelinks.VoiceLinks = runner.voicelinks.VoiceLinks, Recorder
    old = runner._config
    try:
        runner._config = {"links": LINKS, "launcher_voice_links": True,
                          "launchers": []}
        runner.start_voice_links([], {})
    finally:
        runner.voicelinks.VoiceLinks = real
        runner._config = old
    names = [link.get("name") for link in seen.get("links") or []]
    check("no tile that acts is given an \"ouvre\" sentence",
          names == ["YouTube"], str(names))


def live():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  skipped: the page in a browser needs Playwright")
        return
    print("Against a stand-in Home Assistant, in a real browser")
    fake = FakeHA(STATES)
    wanted = [entities.entity_of(link["url"]) for link in LINKS
              if entities.is_tile(link)]
    states = entities.EntityStates(fake.base, TOKEN, wanted, " [check]").start()
    where = launcher.start(LINKS, port=0, theme="dark", entity_states=states)
    check("the launcher starts", where is not None)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(states.snapshot()) < len(wanted):
        time.sleep(0.05)
    followed = states.snapshot()
    check("every tile's entity is followed, the missing one as missing",
          len(followed) == len(wanted)
          and followed.get("light.absente", {}).get("s") == "missing",
          str(sorted(followed)))

    followed_by_sender = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None)
        page = browser.new_page(viewport={"width": 1280, "height": 800},
                                locale="fr-FR")
        page.expose_function("__udispFollow",
                             lambda href: followed_by_sender.append(href))
        page.goto(where)

        def text(entity):
            return page.eval_on_selector(
                f'a.tile[data-entity="{entity}"] .state', "e => e.textContent")

        def classes(entity):
            return page.eval_on_selector(
                f'a.tile[data-entity="{entity}"]', "e => e.className")

        def wait_text(entity, want, seconds=4):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if text(entity) == want:
                    return True
                page.wait_for_timeout(50)
            return False

        check("a light that is off says so and is greyed",
              wait_text("light.salon", "Éteint") and " off" in classes("light.salon"),
              f"{text('light.salon')!r} {classes('light.salon')}")
        check("a half-open shutter is lit and says how far",
              text("cover.volet") == "Ouvert · 40 %" and " on" in classes("cover.volet"),
              repr(text("cover.volet")))
        check("a temperature is in figures, the French way",
              text("sensor.temperature") == "21,5 °C", repr(text("sensor.temperature")))
        check("a door sensor says open", text("binary_sensor.porte") == "Ouvert",
              repr(text("binary_sensor.porte")))
        check("a lock says locked", text("lock.entree") == "Verrouillé",
              repr(text("lock.entree")))
        check("an entity that does not exist says not found",
              text("light.absente") == "Introuvable"
              and " gone" in classes("light.absente"), repr(text("light.absente")))
        check("a scene has no state line",
              text("scene.cinema") == "", repr(text("scene.cinema")))

        before = len(fake.calls)
        began = time.monotonic()
        page.click('a.tile[data-entity="light.salon"]')
        lit = wait_text("light.salon", "Allumé · 60 %")
        took = (time.monotonic() - began) * 1000
        check("a press toggles the light with the add-on's credential",
              fake.calls[before:] == [("light", "toggle", "light.salon")],
              str(fake.calls[before:]))
        check(f"and the tile lights when Home Assistant says so ({took:.0f} ms)",
              lit and " on" in classes("light.salon"), repr(text("light.salon")))
        check("the page did not go anywhere",
              page.evaluate("location.hash") == "" and page.url.rstrip("/") == where.rstrip("/"),
              page.url)

        fake.change("light.salon", "off", {}, ["brightness"])
        check("a light switched elsewhere is shown off without a press",
              wait_text("light.salon", "Éteint"), repr(text("light.salon")))

        before = len(fake.calls)
        page.click('a.tile[data-entity="scene.cinema"]')
        check("a scene says it ran", wait_text("scene.cinema", "Lancé"),
              repr(text("scene.cinema")))
        check("through scene.turn_on",
              fake.calls[before:] == [("scene", "turn_on", "scene.cinema")],
              str(fake.calls[before:]))
        check("and the word goes again", wait_text("scene.cinema", "", 3),
              repr(text("scene.cinema")))

        fake.failing.add("switch.panne")
        page.click('a.tile[data-entity="switch.panne"]')
        check("a call Home Assistant refuses says it failed",
              wait_text("switch.panne", "Échec"), repr(text("switch.panne")))

        before = len(fake.calls)
        page.click('a.tile[data-entity="lock.entree"]')
        page.wait_for_timeout(400)
        refused = page.evaluate("""() => fetch('%s', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({entity: 'lock.entree'})}).then(r => r.status)"""
                                % launcher.ACT_PATH)
        check("a lock's tile calls nothing, and asking for it directly is refused",
              fake.calls[before:] == [] and refused == 403,
              f"{fake.calls[before:]} {refused}")
        stranger = page.evaluate("""() => fetch('%s', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({entity: 'light.cuisine'})}).then(r => r.status)"""
                                 % launcher.ACT_PATH)
        check("an entity that is not one of its tiles is refused", stranger == 403,
              str(stranger))

        page.click('a.tile[href^="https://www.youtube.com"]', no_wait_after=True)
        page.wait_for_timeout(300)
        check("the sender is never asked to follow a tile that acts",
              followed_by_sender == ["https://www.youtube.com/tv"],
              str(followed_by_sender))
        page.goto(where)
        check("no credential anywhere in the page", TOKEN not in page.content())
        browser.close()

    request = urllib.request.Request(
        where.rstrip("/") + launcher.ACT_PATH,
        data=json.dumps({"entity": "light.salon"}).encode(),
        headers={"Content-Type": "application/json", "Origin": "http://evil.test"})
    try:
        urllib.request.urlopen(request, timeout=5)
        foreign = 204
    except urllib.error.HTTPError as err:
        foreign = err.code
    check("a press from another site's page is refused", foreign == 403,
          str(foreign))


def main():
    pure()
    page()
    voice_links()
    live()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
