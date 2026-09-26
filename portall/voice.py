"""The panel's voice assistant, as Home Assistant sees it, for the avatar.

A panel running ESPHome's voice_assistant appears in Home Assistant as an
assist_satellite entity whose state is idle, listening, processing or
responding. That entity is the one thing about a voice assistant that does
not move with ESPHome's releases: the firmware's own triggers and options are
renamed and reshaped from one version to the next, the satellite's four states
are Home Assistant's, documented, and the same on every panel that has one.
So the avatar follows THAT, and needs nothing from the panel's firmware at all.

It is read by the ADD-ON, for the same reason the weather is: the page must
never hold a credential, since a page's storage is shared with every site a
panel visits. The page asks 127.0.0.1 and is answered when the state changes.

Home Assistant pushes changes over its websocket, which is what makes this
free while nothing happens: one connection per launcher that asked for it,
silent until somebody speaks. Python's standard library has no websocket
client, and the add-on installs nothing it does not need, so the part of
RFC 6455 a client uses is written out below -- text frames, fragments, and the
ping every thirty seconds that the Supervisor's proxy sends and closes the
connection without an answer to.

An accessory must never cost the picture: every failure here is said once and
retried, and the face keeps whatever expression it had.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit

DOMAIN = "assist_satellite"

# What each satellite state looks like on the face. The pairs are the ones
# the LVGL face this avatar was drawn from used for the same four moments
# (youkorr/esphome-lvgl-kawaii: listening -> surprised, speaking -> happy),
# with thinking where that project had its own "working hard".
MOODS = {
    "idle": "neutral",
    "listening": "surprised",
    "processing": "thinking",
    "responding": "happy",
}

# How long a page's question is held open before it is answered with what
# stands. Under the thirty seconds after which a browser or a proxy starts to
# wonder, and long enough that an idle panel asks three times a minute.
HOLD_S = 20.0

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Closed(Exception):
    """The far end hung up, or said something that is not a websocket."""


class Socket:
    """Just enough of an RFC 6455 client to talk to Home Assistant."""

    def __init__(self, url, timeout=15.0):
        split = urlsplit(url)
        secure = split.scheme == "wss"
        host = split.hostname or ""
        port = split.port or (443 if secure else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            raw = ssl.create_default_context().wrap_socket(
                raw, server_hostname=host)
        self.sock = raw
        self.reader = raw.makefile("rb")
        key = base64.b64encode(os.urandom(16)).decode()
        path = (split.path or "/") + (f"?{split.query}" if split.query else "")
        netloc = split.netloc.rsplit("@", 1)[-1]
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        status = self.reader.readline().decode("latin-1").strip()
        headers = {}
        while True:
            line = self.reader.readline().decode("latin-1")
            if line in ("\r\n", "\n", ""):
                break
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if " 101" not in status:
            raise Closed(f"answered {status or 'nothing'} instead of a websocket")
        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise Closed("answered with a websocket key that does not match")

    def settimeout(self, seconds):
        self.sock.settimeout(seconds)

    def _send(self, opcode, payload):
        head = bytearray([0x80 | opcode])
        size = len(payload)
        # A client masks every frame it sends; the specification requires it
        # and a server closes the connection on one that is not.
        if size < 126:
            head.append(0x80 | size)
        elif size < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", size)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", size)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def send(self, message):
        self._send(0x1, json.dumps(message).encode())

    def _exactly(self, count):
        data = self.reader.read(count)
        if data is None or len(data) != count:
            raise Closed("the connection ended in the middle of a message")
        return data

    def recv(self):
        """The next whole message, as JSON. Pings are answered on the way."""
        parts = []
        while True:
            first, second = self._exactly(2)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._exactly(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._exactly(8))[0]
            mask = self._exactly(4) if second & 0x80 else None
            payload = self._exactly(size)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping: answered with the same bytes
                self._send(0xA, payload)
                continue
            if opcode == 0xA:  # a pong nobody asked for
                continue
            if opcode == 0x8:
                raise Closed("Home Assistant closed the connection")
            parts.append(payload)
            if first & 0x80:  # FIN: the last fragment of this message
                return json.loads(b"".join(parts).decode())

    def close(self):
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def websocket_address(base):
    """Home Assistant's websocket, from the address its REST API is at.

    The Supervisor serves it at /core/api/websocket beside /core/api/..., and
    Home Assistant itself at /api/websocket, so it is the same suffix either
    way. An https address is a wss one.
    """
    split = urlsplit(str(base or ""))
    scheme = "wss" if split.scheme == "https" else "ws"
    return f"{scheme}://{split.netloc}{split.path.rstrip('/')}/api/websocket"


class Voice:
    """Follows one assist_satellite and tells waiting pages when it moves."""

    RETRY_FIRST_S = 2.0
    RETRY_MAX_S = 60.0
    # With no satellite to follow, looking again this rarely is enough: one
    # appears when somebody flashes a panel, not in the middle of a sentence.
    LOOK_AGAIN_S = 600.0

    def __init__(self, base, token, entity="", label=""):
        self.url = websocket_address(base) if base else ""
        self.token = str(token or "")
        self.asked = str(entity or "").strip()
        self.entity = None
        self.label = label
        self.mood = "neutral"
        self.version = 0
        self._changed = threading.Condition()
        self._said = set()

    def say(self, key, text):
        """Once per distinct thing, so a retry loop cannot fill the log."""
        if key in self._said:
            return
        self._said.add(key)
        print(f"Avatar{self.label}: {text}", flush=True)

    def _set(self, state):
        mood = MOODS.get(str(state or ""), "neutral")
        with self._changed:
            if mood != self.mood:
                self.mood = mood
                self.version += 1
                self._changed.notify_all()

    def wait(self, since, timeout=HOLD_S):
        """(version, mood) once the version differs from `since`, or at timeout.

        A page that has never asked passes -1 and is answered at once, so the
        face it draws first is the one that stands.
        """
        with self._changed:
            if since == self.version:
                self._changed.wait_for(lambda: self.version != since, timeout)
            return self.version, self.mood

    def start(self):
        if not self.url or not self.token:
            self.say("route", "no way to reach Home Assistant, so the face "
                     "cannot follow a voice assistant. Under Home Assistant "
                     "this cannot happen; run by hand, it needs a link that "
                     "carries the dashboard's address and a token.")
            return self
        threading.Thread(target=self._run, daemon=True,
                         name="portall-voice").start()
        return self

    def _run(self):
        wait = self.RETRY_FIRST_S
        while True:
            began = time.monotonic()
            try:
                wait = self._session() or wait
            except (OSError, Closed, ValueError) as err:
                self.say(f"lost:{type(err).__name__}",
                         f"lost Home Assistant ({err}); trying again. The "
                         f"face stays as it is meanwhile.")
            self._set("idle")
            # A connection that lived a while was not the thing failing, so
            # the next one is tried soon rather than after the grown delay.
            if time.monotonic() - began > 60:
                wait = self.RETRY_FIRST_S
            time.sleep(wait)
            wait = min(wait * 2, self.RETRY_MAX_S)

    def _session(self):
        """One connection, for as long as it lasts. Returns a delay, or None."""
        link = Socket(self.url)
        try:
            first = link.recv()
            if first.get("type") != "auth_required":
                raise Closed(f"said {first.get('type')!r} before asking for "
                             f"a token")
            link.send({"type": "auth", "access_token": self.token})
            answer = link.recv()
            if answer.get("type") != "auth_ok":
                self.say("refused", "Home Assistant refused this add-on's "
                         "token, so the face cannot follow a voice "
                         "assistant.")
                return self.RETRY_MAX_S

            entity = self.asked
            if not entity:
                link.send({"id": 1, "type": "get_states"})
                reply = link.recv()
                found = sorted(
                    s.get("entity_id", "") for s in (reply.get("result") or [])
                    if str(s.get("entity_id", "")).startswith(DOMAIN + "."))
                if not found:
                    self.say("none", "Home Assistant has no voice assistant "
                             "(no assist_satellite entity), so the face has "
                             "nothing to follow. Give a panel's firmware a "
                             "voice_assistant: and it will appear.")
                    return self.LOOK_AGAIN_S
                if len(found) > 1:
                    self.say("several", "Home Assistant has several voice "
                             "assistants -- " + ", ".join(found) + " -- so "
                             "the face follows none of them. Put the one "
                             "that belongs to this panel in avatar_voice.")
                    return self.LOOK_AGAIN_S
                entity = found[0]
            self.entity = entity

            link.send({"id": 2, "type": "subscribe_entities",
                       "entity_ids": [entity]})
            # Heartbeats keep arriving while nothing happens, so a read that
            # takes several minutes is a connection that is gone.
            link.settimeout(120)
            seen = False
            while True:
                message = link.recv()
                if message.get("id") != 2:
                    continue
                if message.get("type") == "result":
                    if not message.get("success", False):
                        self.say(f"refused:{entity}",
                                 f"Home Assistant will not follow "
                                 f"{entity!r}: a voice assistant's name "
                                 f"looks like assist_satellite.kitchen.")
                        return self.LOOK_AGAIN_S
                    continue
                event = message.get("event") or {}
                for eid, state in (event.get("a") or {}).items():
                    if eid == entity:
                        seen = True
                        self.say(f"follow:{entity}",
                                 f"follows {entity}"
                                 + ("" if self.asked else
                                    " (the only voice assistant in Home "
                                    "Assistant)"))
                        self._set(state.get("s"))
                for eid, change in (event.get("c") or {}).items():
                    if eid == entity and "s" in (change.get("+") or {}):
                        self._set(change["+"]["s"])
                if entity in (event.get("r") or []):
                    self._set("idle")
                if not seen and event.get("a") is not None:
                    self.say(f"missing:{entity}",
                             f"{entity} does not exist in Home Assistant. "
                             f"Its voice assistants are the entities whose "
                             f"name begins with assist_satellite.")
                    seen = True
        finally:
            link.close()
