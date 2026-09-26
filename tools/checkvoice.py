#!/usr/bin/env python3
"""The avatar follows the voice assistant: add-on, websocket, page.

Drives the SHIPPED portall/voice.py against a stand-in Home Assistant that
speaks the real websocket protocol -- the handshake, masked client frames,
pings, fragments, the auth exchange, get_states and subscribe_entities with
the compressed "a" / "c" / "r" events Home Assistant really sends
(websocket_api/messages.py, _state_diff_event) -- and then the SHIPPED
launcher in a real browser, reading the face's mood off the page.

    python3 tools/checkvoice.py

Needs Playwright and a Chromium for the browser half, like checkavatar.py;
CHROMIUM names the browser when Playwright's own is not installed.
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "portall"))

import voice  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TOKEN = "stand-in-token"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))


class Peer:
    """One accepted connection, from the server's side."""

    def __init__(self, conn):
        self.conn = conn
        self.reader = conn.makefile("rb")
        self.pongs = []
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(1024)
            if not chunk:
                raise ConnectionError("no handshake")
            request += chunk
        lines = request.decode().split("\r\n")
        headers = {k.lower(): v.strip() for k, _, v in
                   (line.partition(":") for line in lines[1:] if line)}
        self.path = lines[0].split()[1]
        accept = base64.b64encode(hashlib.sha1(
            (headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket"
                      "\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                      f"{accept}\r\n\r\n").encode())

    def frame(self, opcode, payload, fin=True):
        head = bytearray([(0x80 if fin else 0) | opcode])
        size = len(payload)
        if size < 126:
            head.append(size)
        elif size < 65536:
            head.append(126)
            head += struct.pack(">H", size)
        else:
            head.append(127)
            head += struct.pack(">Q", size)
        self.conn.sendall(bytes(head) + payload)

    def send(self, obj):
        self.frame(0x1, json.dumps(obj).encode())

    def recv(self):
        while True:
            first, second = self.reader.read(2)
            size = second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self.reader.read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self.reader.read(8))[0]
            if not second & 0x80:
                raise AssertionError("a client frame arrived unmasked")
            mask = self.reader.read(4)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(self.reader.read(size)))
            opcode = first & 0x0F
            if opcode == 0xA:
                self.pongs.append(data)
                continue
            if opcode == 0x8:
                raise ConnectionError("closed")
            return json.loads(data)


class FakeHA:
    """Home Assistant's websocket, as far as the avatar uses it."""

    def __init__(self, states, token=TOKEN):
        self.states = dict(states)  # entity_id -> state
        self.token = token
        self.peers = []
        self.subscribed = threading.Event()
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.connections = 0
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
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            peer = Peer(conn)
            peer.send({"type": "auth_required", "ha_version": "2026.9.0"})
            auth = peer.recv()
            if auth.get("access_token") != self.token:
                peer.send({"type": "auth_invalid", "message": "Invalid access"})
                conn.close()
                return
            peer.send({"type": "auth_ok", "ha_version": "2026.9.0"})
            while True:
                msg = peer.recv()
                if msg["type"] == "get_states":
                    peer.send({"id": msg["id"], "type": "result", "success": True,
                               "result": [{"entity_id": e, "state": s}
                                          for e, s in self.states.items()]})
                elif msg["type"] == "subscribe_entities":
                    wanted = msg.get("entity_ids") or []
                    if any("." not in e for e in wanted):
                        peer.send({"id": msg["id"], "type": "result",
                                   "success": False,
                                   "error": {"code": "invalid_format"}})
                        continue
                    peer.sub = msg["id"]
                    peer.wanted = wanted
                    peer.send({"id": msg["id"], "type": "result", "success": True,
                               "result": None})
                    peer.send({"id": msg["id"], "type": "event", "event": {"a": {
                        e: {"s": s, "a": {}, "c": "x", "lc": 1.0}
                        for e, s in self.states.items() if e in wanted}}})
                    self.peers.append(peer)
                    self.subscribed.set()
        except (ConnectionError, OSError, ValueError, AssertionError):
            pass

    def change(self, entity, state):
        """What Home Assistant sends when a state moves: a compressed diff."""
        self.states[entity] = state
        for peer in list(self.peers):
            if entity in peer.wanted:
                try:
                    peer.send({"id": peer.sub, "type": "event", "event": {
                        "c": {entity: {"+": {"s": state, "lc": time.time()}}}}})
                except OSError:
                    pass

    def drop(self):
        for peer in self.peers:
            try:
                peer.conn.shutdown(socket.SHUT_RDWR)
                peer.conn.close()
            except OSError:
                pass
        self.peers = []
        self.subscribed.clear()

    def close(self):
        self.drop()
        self.server.close()


def said_by(fn):
    """What a call prints, as one string."""
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        value = fn()
    return value, out.getvalue()


def settle(until, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        time.sleep(0.02)
    return False


def main():
    SAT = "assist_satellite.guition_assist_satellite"

    print("the websocket itself")
    ha = FakeHA({SAT: "idle"})
    link = voice.Socket(voice.websocket_address(ha.base))
    check("the address is /api/websocket beside the REST API",
          voice.websocket_address("http://supervisor/core")
          == "ws://supervisor/core/api/websocket"
          and voice.websocket_address("https://ha.example:8123/")
          == "wss://ha.example:8123/api/websocket")
    check("the handshake is accepted and auth_required arrives",
          link.recv().get("type") == "auth_required")
    link.send({"type": "auth", "access_token": TOKEN})
    check("a masked frame is read by the server", link.recv().get("type") == "auth_ok")
    peer = None
    settle(lambda: ha.connections >= 1)
    link.close()

    # Pings, fragments and a long message, straight at the client.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    box = {}

    def serve():
        conn, _ = srv.accept()
        p = Peer(conn)
        box["peer"] = p
        p.frame(0x9, b"are-you-there")
        body = json.dumps({"part": "one" * 3}).encode()
        p.frame(0x1, body[:5], fin=False)
        p.frame(0x0, body[5:], fin=True)
        p.send({"big": "x" * 70000})
        p.send({"mid": "y" * 300})
        box["pong"] = p.recv
    threading.Thread(target=serve, daemon=True).start()
    link = voice.Socket(f"ws://127.0.0.1:{srv.getsockname()[1]}/api/websocket")
    first = link.recv()
    check("a fragmented message is put back together",
          first == {"part": "oneoneone"}, repr(first))
    check("a message over 64 KiB is read whole", len(link.recv()["big"]) == 70000)
    check("a message over 125 bytes is read whole", len(link.recv()["mid"]) == 300)
    link.send({"after": True})
    got = box["peer"].recv()
    check("a ping is answered with its own bytes",
          box["peer"].pongs == [b"are-you-there"] and got == {"after": True},
          repr(box["peer"].pongs))
    link.close()
    srv.close()
    ha.close()

    print("following the satellite")
    ha = FakeHA({SAT: "idle", "light.kitchen": "on"})
    v = voice.Voice(ha.base, TOKEN, "", " [salon]")
    _, out = said_by(lambda: (v.start(), settle(ha.subscribed.is_set)))
    settle(lambda: v.entity == SAT)
    time.sleep(0.2)
    out += ""
    check("with one voice assistant in the house, it is followed", v.entity == SAT)
    version, mood = v.wait(-1)
    check("and the face starts neutral while it is idle", mood == "neutral")
    for state, want in (("listening", "surprised"), ("processing", "thinking"),
                        ("responding", "happy"), ("idle", "neutral")):
        began = time.monotonic()
        ha.change(SAT, state)
        version, mood = v.wait(version, timeout=3)
        took = time.monotonic() - began
        check(f"{state} -> {want}, in {took * 1000:.0f} ms", mood == want and took < 0.5,
              f"{mood}")
    began = time.monotonic()
    ha.change(SAT, "idle")  # no change of face: nobody is woken
    same = v.wait(version, timeout=0.4)
    check("a state that does not change the face wakes nobody",
          same == (version, "neutral") and time.monotonic() - began >= 0.35)
    ha.change(SAT, "listening")
    version, mood = v.wait(version, timeout=3)
    v.RETRY_FIRST_S = 0.2
    ha.drop()
    version, mood = v.wait(version, timeout=3)
    check("a lost connection puts the face back to neutral", mood == "neutral")
    check("and it comes back by itself", settle(ha.subscribed.is_set, 5))
    ha.change(SAT, "processing")
    version, mood = v.wait(version, timeout=3)
    check("and follows again after it", mood == "thinking")
    ha.close()

    print("what it says when it cannot")
    ha = FakeHA({SAT: "idle", "assist_satellite.cuisine": "idle"})
    v = voice.Voice(ha.base, TOKEN, "")
    _, out = said_by(lambda: (v.start(), time.sleep(0.6)))
    check("several voice assistants: none is followed, and they are named",
          v.entity is None and "assist_satellite.cuisine" in out
          and SAT in out and "avatar_voice" in out, out)
    v2 = voice.Voice(ha.base, TOKEN, "assist_satellite.cuisine")
    _, out = said_by(lambda: (v2.start(), time.sleep(0.6)))
    check("naming one of them follows it", v2.entity == "assist_satellite.cuisine"
          and "follows assist_satellite.cuisine" in out, out)
    v3 = voice.Voice(ha.base, TOKEN, "assist_satellite.salle_de_bain")
    _, out = said_by(lambda: (v3.start(), time.sleep(0.6)))
    check("a name that does not exist is said", "does not exist" in out, out)
    v4 = voice.Voice(ha.base, TOKEN, "guition")
    _, out = said_by(lambda: (v4.start(), time.sleep(0.6)))
    check("a name that is not an entity is said, with what one looks like",
          "assist_satellite.kitchen" in out, out)
    v5 = voice.Voice(ha.base, "wrong-token", "")
    _, out = said_by(lambda: (v5.start(), time.sleep(0.6)))
    check("a refused token is said once", out.count("refused") == 1, out)
    ha.close()
    ha = FakeHA({"light.kitchen": "on"})
    v6 = voice.Voice(ha.base, TOKEN, "")
    _, out = said_by(lambda: (v6.start(), time.sleep(0.6)))
    check("no voice assistant at all is said", "no voice assistant" in out, out)
    ha.close()
    v7 = voice.Voice("http://127.0.0.1:9", TOKEN, "")
    v7.RETRY_FIRST_S = 0.05
    v7.RETRY_MAX_S = 0.1
    _, out = said_by(lambda: (v7.start(), time.sleep(0.8)))
    check("an unreachable Home Assistant is said once, however often it is tried",
          out.count("lost Home Assistant") == 1, out)

    print("the add-on's own wiring")
    import run
    check("no face, nothing followed",
          run.follow_voice({"launcher_avatar": False}, []) is None)
    check("off follows nothing",
          run.follow_voice({"launcher_avatar": True,
                            "launcher_avatar_voice": "off"}, []) is None)
    os.environ["SUPERVISOR_TOKEN"] = "supervisor-token"
    real_start = voice.Voice.start
    voice.Voice.start = lambda self: self
    try:
        got = run.follow_voice({"launcher_avatar": True,
                                "launcher_avatar_voice": " assist_satellite.x "}, [])
    finally:
        voice.Voice.start = real_start
        del os.environ["SUPERVISOR_TOKEN"]
    check("under the Supervisor it reads through the add-on's own credential",
          got is not None and got.url == "ws://supervisor/core/api/websocket"
          and got.token == "supervisor-token" and got.asked == "assist_satellite.x",
          got and got.url)
    grouped = run.regroup({"launcher": {"avatar": True, "avatar_voice": "a.b"}}) \
        if hasattr(run, "regroup") else {"launcher_avatar_voice": "a.b"}
    check("the form's launcher: avatar_voice reaches it",
          grouped.get("launcher_avatar_voice") == "a.b", grouped)

    browser_half(SAT)
    failed = results.count(False)
    print(f"{len(results) - failed} ok, {failed} failed")
    sys.exit(1 if failed else 0)


def browser_half(SAT):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  --   no Playwright here, so the page itself is not checked")
        return
    import launcher

    print("the face on the page")
    ha = FakeHA({SAT: "idle"})
    v = voice.Voice(ha.base, TOKEN, "").start()
    settle(ha.subscribed.is_set)
    links = [{"name": "Home Assistant", "url": "http://127.0.0.1:1/", "icon": "ha"}]
    where = launcher.start(links, port=launcher.ANY_PORT, avatar=True,
                           avatar_file=None, voice=v)
    plain = launcher.start(links, port=launcher.ANY_PORT, avatar=True,
                           avatar_file=None)
    asked = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None)
        page = browser.new_page(viewport={"width": 1024, "height": 600})
        page.on("request", lambda r: asked.append(r.url)
                if "/voice" in r.url else None)
        # At noon: from 22 h to 7 h an idle face dozes, so this reads
        # "neutral" only in the daytime -- and a check that passes or fails
        # with the hour it is run at is not a check.
        import datetime
        page.clock.install(time=datetime.datetime(2026, 9, 27, 12, 0))
        page.goto(where)
        mood = lambda: page.evaluate("document.getElementById('av').dataset.mood")
        settle(lambda: mood() == "neutral")
        check("the page starts with the face neutral", mood() == "neutral", mood())
        for state, want in (("listening", "surprised"), ("processing", "thinking"),
                            ("responding", "happy"), ("idle", "neutral")):
            began = time.monotonic()
            ha.change(SAT, state)
            ok = settle(lambda: mood() == want, 3)
            check(f"the page shows {want} {(time.monotonic() - began) * 1000:.0f} ms "
                  f"after {state}", ok, mood())
        ha.change(SAT, "listening")
        settle(lambda: mood() == "surprised")
        box = page.locator("#av").bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        check("a tap while it listens smiles for a moment",
              settle(lambda: mood() == "happy", 1), mood())
        check("then goes back to listening, not to neutral",
              settle(lambda: mood() == "surprised", 4), mood())
        ha.change(SAT, "idle")
        settle(lambda: mood() == "neutral")
        before = len(asked)
        time.sleep(3)
        check("nothing is asked while nothing happens",
              len(asked) - before == 0, f"{len(asked) - before} questions")
        other = browser.new_page()
        other_asked = []
        other.on("request", lambda r: other_asked.append(r.url)
                 if "/voice" in r.url else None)
        other.goto(plain)
        time.sleep(1)
        check("a launcher with no voice assistant asks nothing",
              not other_asked and other.evaluate(
                  "!!window.portallAvatar && !!window.portallAvatar.stand"))
        browser.close()
    ha.close()


if __name__ == "__main__":
    main()
