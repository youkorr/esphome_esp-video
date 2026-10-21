#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Notifications on the screens, a page sent to a telephone, good morning.

WHY THIS EXISTS. Three things asked for together, and each one crosses the
same four places none of which can see the others: Home Assistant (an event,
the phone app's notify services, the clock, the weather, the calendars, a
voice assistant's announce), the add-on's run.py, the line it writes to a
sender, and the sender's own picture and log. So this drives each with the
SHIPPED code:

  1. the pieces on their own: what an event asks to show and where, what a
     telephone is handed, the sentence "envoie ça sur mon téléphone" through
     hassil, the greeting's words, ha_send's Toasts on its own;
  2. run.py against a stand-in Home Assistant over a real websocket and its
     REST API: portall_notify reaching the screens it names, a page handed
     over by a sender reaching the phone services, the morning greeting said
     on the voice assistant and shown, once a day, only in the morning;
  3. the shipped ha_send.py against a fake panel that reassembles what it is
     sent: a notification written over a page that is not painting, gone
     after its time, closed by a tap that presses nothing, waiting while the
     screen is dark; and the page handed over on "share" in a line the
     add-on keeps out of its log.

    python3 tools/checknotices.py [--sender PATH]

Part 1 needs hassil==3.12.1 and jinja2 for the sentence and its answer; part 3
Playwright, a Chromium ($CHROMIUM when Playwright's own is not installed) and
Pillow. Each says it was skipped when they are absent.
"""

import http.server
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))
sys.path.insert(0, str(HERE / "tools"))

import ha_send  # noqa: E402
import morning  # noqa: E402
import notices  # noqa: E402
import run  # noqa: E402
import voicelinks  # noqa: E402
from checkvoice import Peer  # noqa: E402


def option(name, default):
    for n, arg in enumerate(sys.argv):
        if arg == name and n + 1 < len(sys.argv):
            return sys.argv[n + 1]
    return default


SENDER = option("--sender", str(HERE / "components" / "portall"
                                / "ha_send.py"))
TOKEN = "a.b.c"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + (f"  ({detail})" if detail and not ok else ""))


def settle(until, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        time.sleep(0.05)
    return until()


def quietly():
    lines = []
    return lines, lines.append


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()

    def lines(self):
        return self.stdin.getvalue().splitlines()


# --- 1. the pieces on their own ---------------------------------------------

def units():
    print("The pieces on their own:")
    check("the add-on's line marker is the sender's",
          run.ADDON_ONLY == ha_send.ADDON_ONLY
          and ha_send.SHARE_LINE.startswith(run.ADDON_ONLY))
    body = notices.notice({"title": " Lessive ", "message": "Le lave-linge\n"
                           "a terminé", "seconds": 999})
    check("an event's words are one line each, its time held",
          body == {"title": "Lessive", "message": "Le lave-linge a terminé",
                   "seconds": notices.SECONDS_MAX}, repr(body))
    check("an event with nothing to say shows nothing",
          notices.notice({"seconds": 5}) is None)
    names = ["salon", "Cuisine"]
    check("no panel: named is every screen",
          notices.screens({}, names) == (names, []))
    check("names, as a string or a list, any case",
          notices.screens({"panel": "cuisine, nowhere"}, names)
          == (["Cuisine"], ["nowhere"])
          and notices.screens({"panels": ["SALON"]}, names) == (["salon"], []))
    sent = ha_send._notice_body(json.dumps(body))
    check("the sender reads what the add-on writes", sent == body, repr(sent))
    check("and refuses a line with nothing in it",
          ha_send._notice_body('{"seconds": 3}') is None
          and ha_send._notice_body("{nope") is None)
    check("an emoji in front is the icon, the words the rest",
          ha_send._emoji_lead("\U0001F9FA Lessive") == ("\U0001F9FA",
                                                        "Lessive")
          and ha_send._emoji_lead("☀️ Bonjour")[0]
          == "☀️"
          and ha_send._emoji_lead("Bonjour") == ("", "Bonjour"))

    origin = "http://192.168.1.10:8123"
    check("a page of Home Assistant goes to the phone as its path",
          notices.phone_address(origin + "/lovelace/0?edit=1#x", origin)
          == "/lovelace/0?edit=1#x")
    check("any other page as its address",
          notices.phone_address("https://www.youtube.com/watch?v=1", origin)
          == "https://www.youtube.com/watch?v=1")
    message = notices.phone_message(
        {"url": "https://www.youtube.com/watch?v=1", "title": "Crêpes"},
        "salon", origin, True)
    check("what the phone shows: the page's title, the site, the screen",
          message["title"] == "Crêpes"
          and message["message"].startswith("youtube.com · envoyé depuis "
                                            "l'écran salon")
          and message["data"] == {"url": "https://www.youtube.com/watch?v=1",
                                  "clickAction":
                                      "https://www.youtube.com/watch?v=1"},
          repr(message))
    check("in English for an English screen", notices.phone_message(
        {"url": origin + "/lovelace/0", "title": ""}, "kitchen", origin,
        False)["message"].startswith("Home Assistant · sent from the "
                                     "kitchen screen"))

    check("a gesture given away is dropped up to its release",
          ha_send.drop_until_release([[(0, 1, 1)], [(0, 2, 2)], [],
                                      [(0, 5, 5)]]) == ([[(0, 5, 5)]], False)
          and ha_send.drop_until_release([[(0, 1, 1)]]) == ([], True))

    sentences()
    greeting()
    toasts()


def sentences():
    try:
        from hassil import Intents, WildcardSlotList, recognize_all
        from hassil.parse_expression import parse_sentence
    except ImportError:
        print("  --   the sentence skipped: no hassil here")
        return
    auto = voicelinks.automation(["Jellyfin", "Home Assistant"])
    for trigger in auto["triggers"]:
        for sentence in trigger["command"]:
            parse_sentence(sentence)
    intents = {f"t{i}": {"data": [{"sentences": t["command"]}]}
               for i, t in enumerate(auto["triggers"])}
    ids = {f"t{i}": t["id"] for i, t in enumerate(auto["triggers"])}
    compiled = Intents.from_dict({"language": "fr", "intents": intents})
    for name in ("before", "text"):
        compiled.slot_lists[name] = WildcardSlotList(name)

    def heard(text):
        for result in recognize_all(text, compiled):
            return ids[result.intent.name]
        return None
    for text in ("envoie ça sur mon téléphone",
                 "Ok Nabu, envoie-moi ça sur mon portable.",
                 "Envoie la page sur mon iPhone",
                 "partage cette page sur mon téléphone s'il te plaît",
                 "envoyez ça sur l'iphone", "send this to my phone",
                 "share the page to my iPhone"):
        check(f"\"{text}\" -> send", heard(text) == "send",
              repr(heard(text)))
    for text in ("envoie un message à Léa", "envoie ça à Léa",
                 "envoie la musique dans la cuisine", "allume la lumière"):
        check(f"\"{text}\" is left to Home Assistant", heard(text) is None,
              repr(heard(text)))
    check("\"ouvre Jellyfin\" is still a link",
          heard("ouvre Jellyfin") == "link:Jellyfin")
    check("an event portall_open with link: send asks for it, phone and all",
          voicelinks.wanted("send") == ("send", None))
    try:
        import jinja2
    except ImportError:
        print("  --   the answer skipped: no jinja2 here")
        return
    template = jinja2.Environment().from_string(
        auto["actions"][1]["set_conversation_response"])
    check("the voice assistant answers \"Je l'envoie sur ton téléphone\"",
          template.render(trigger={"id": "send", "user_input":
                                   {"language": "fr"}})
          == "Je l'envoie sur ton téléphone")


def greeting():
    weather = {"condition": "sunny", "high": 21, "low": 12,
               "temperature": 15, "unit": "°C"}
    events = [{"summary": "Anniversaire de Léa", "time": None},
              {"summary": "Dentiste", "time": "10:30"},
              {"summary": "Piscine", "time": "18:00"}]
    title, message, spoken = morning.greeting(True, weather, events, True)
    check("the greeting, said",
          spoken == "Bonjour ! Aujourd'hui, ensoleillé, de 12 à 21 degrés. "
                    "À l'agenda : Anniversaire de Léa ; à 10 h 30, Dentiste "
                    "; à 18 h, Piscine.", spoken)
    check("and shown, the sky as its icon",
          title == "☀️ Bonjour !"
          and message == "Ensoleillé, 12 à 21 °C · Anniversaire de Léa · "
                         "10 h 30 Dentiste · 18 h Piscine", repr((title,
                                                                 message)))
    _, _, spoken = morning.greeting(False, {"condition": "rainy",
                                            "temperature": 9, "unit": "°C"},
                                    [], True)
    check("in English, with today's temperature and an empty calendar",
          spoken == "Good morning! Today, rainy, 9 degrees. Nothing on the "
                    "calendar today.", spoken)
    _, message, spoken = morning.greeting(True, None, [], False)
    check("with nothing to tell, a plain hello", spoken == "Bonjour !"
          and message == "")
    check("the morning is 5 to 12",
          [morning.is_morning(h) for h in (4, 5, 11, 12)]
          == [False, True, True, False])
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "morning.json")
        first = morning.greeted_today("salon", "2026-10-21", path)
        again = morning.greeted_today("salon", "2026-10-21", path)
        other = morning.greeted_today("cuisine", "2026-10-21", path)
        tomorrow = morning.greeted_today("salon", "2026-10-22", path)
    check("once a day per screen, remembered on disk",
          (first, again, other, tomorrow) == (False, True, False, False))


def toasts():
    from PIL import Image
    t = ha_send.Toasts(640, 400)
    body = {"title": "\U0001F9FA Lessive", "message": "Le lave-linge a "
            "terminé, ça sèche.", "seconds": 5}
    t.add(dict(body), 0.0)
    check("dark, it waits and shows nothing", (t.tick(1.0, False), t.look())
          == (None, ()))
    t.tick(2.0, True)
    check("lit, it shows", len(t.look()) == 1)
    picture = t.draw(Image.new("RGB", (640, 400), (200, 200, 200)), 30)
    x0, y0, x1, y1, _ = t.rects[0]
    check("drawn below the strip, on its own dark ground",
          y0 > 30 and picture.getpixel(((x0 + x1) // 2, y1 - 3))
          == (30, 34, 44), repr((t.rects, picture.getpixel(
              ((x0 + x1) // 2, y1 - 3)))))
    t.tick(6.9, True)
    check("for its time", len(t.look()) == 1)
    t.tick(7.1, True)
    check("and not a moment more", t.look() == ())
    for n in range(4):
        t.add({"title": f"n{n}", "message": "", "seconds": 5}, 10.0)
    t.tick(10.0, True)
    check("three at once at most, the newest kept",
          [i["title"] for i in t.items] == ["n3", "n2", "n1"])
    t.draw(Image.new("RGB", (640, 400)), 0)
    x0, y0, x1, y1, ident = t.rects[1]
    check("a tap on one closes that one alone",
          t.dismiss_at((x0 + x1) / 2, (y0 + y1) / 2)
          and [i["title"] for i in t.items] == ["n3", "n1"])
    check("a tap beside them closes nothing",
          not t.dismiss_at(5, 395) and len(t.items) == 2)
    t2 = ha_send.Toasts(640, 400)
    t2.add({"title": "x", "message": "", "seconds": 5}, 0.0)
    t2.tick(ha_send.NOTICE_WAIT_S + 1, False)
    check("one that waited too long in the dark is dropped", not t2.items)


# --- 2. run.py against a stand-in Home Assistant -----------------------------

class FakeHA:
    """Home Assistant's websocket events and the REST routes these use."""

    def __init__(self):
        self.peers = []
        self.posted = []
        self.hour = 7
        self.day = "2026-10-21"
        self.features = 1
        self.subscribed = threading.Event()
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(16)
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
            threading.Thread(target=self._serve, args=(conn,),
                             daemon=True).start()

    def _reply(self, conn, status, obj, text=False):
        data = (obj if text else json.dumps(obj)).encode()
        conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Type: "
                     f"{'text/plain' if text else 'application/json'}\r\n"
                     f"Content-Length: {len(data)}\r\nConnection: close"
                     f"\r\n\r\n".encode() + data)
        conn.close()

    def _serve(self, conn):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                return
            head += chunk
        text, _, rest = head.partition(b"\r\n\r\n")
        lines = text.decode().split("\r\n")
        method, path = lines[0].split()[:2]
        headers = {k.lower(): v.strip() for k, _, v in
                   (line.partition(":") for line in lines[1:])}
        if path.endswith("/api/websocket"):
            return self._websocket(conn, head)
        body = rest
        want = int(headers.get("content-length") or 0)
        while len(body) < want:
            body += conn.recv(4096)
        if headers.get("authorization") != f"Bearer {TOKEN}":
            return self._reply(conn, 401, {"message": "Unauthorized"})
        split = urllib.parse.urlsplit(path)
        data = json.loads(body or b"null")
        if method == "POST" and split.path.startswith("/api/services/"):
            self.posted.append((split.path[len("/api/services/"):], data))
            if split.path.endswith("/weather/get_forecasts"):
                if "return_response" not in split.query:
                    return self._reply(conn, 400, {"message": "no"})
                return self._reply(conn, 200, {
                    "changed_states": [], "service_response": {
                        "weather.maison": {"forecast": [
                            {"condition": "sunny", "temperature": 21.4,
                             "templow": 11.6}]}}})
            return self._reply(conn, 200, [])
        if split.path == "/api/services":
            return self._reply(conn, 200, [
                {"domain": "light", "services": {"turn_on": {}}},
                {"domain": "notify", "services": {
                    "mobile_app_iphone_lea": {}, "mobile_app_pixel": {},
                    "persistent_notification": {}}}])
        if split.path == "/api/template":
            return self._reply(
                conn, 200, f"{self.day} {self.hour:02d}|{self.day}T"
                f"{self.hour:02d}:05:00+02:00|{self.day}T23:59:59+02:00",
                text=True)
        if split.path == "/api/states/weather.maison":
            return self._reply(conn, 200, {
                "entity_id": "weather.maison", "state": "cloudy",
                "attributes": {"temperature": 14, "temperature_unit": "°C"}})
        if split.path == "/api/states":
            return self._reply(conn, 200, [
                {"entity_id": "light.x", "state": "on", "attributes": {}},
                {"entity_id": "assist_satellite.salon", "state": "idle",
                 "attributes": {"supported_features": self.features}}])
        if split.path == "/api/calendars":
            return self._reply(conn, 200, [
                {"entity_id": "calendar.famille", "name": "Famille"}])
        if split.path == "/api/calendars/calendar.famille":
            query = urllib.parse.parse_qs(split.query)
            self.posted.append(("calendar", query))
            return self._reply(conn, 200, [
                {"summary": "Dentiste", "start": {"dateTime":
                 f"{self.day}T10:30:00+02:00"}, "end": {}},
                {"summary": "Anniversaire de Léa",
                 "start": {"date": self.day}, "end": {}}])
        return self._reply(conn, 404, {"message": "Not found"})

    def _websocket(self, conn, head):
        class Replay:
            def __init__(self, sock, first):
                self.sock, self.first = sock, first

            def recv(self, n):
                if self.first:
                    out, self.first = self.first[:n], self.first[n:]
                    return out
                return self.sock.recv(n)

            def __getattr__(self, name):
                return getattr(self.sock, name)
        try:
            peer = Peer(Replay(conn, head))
            peer.reader = conn.makefile("rb")
            peer.send({"type": "auth_required"})
            if peer.recv().get("access_token") != TOKEN:
                peer.send({"type": "auth_invalid"})
                return
            peer.send({"type": "auth_ok"})
            while True:
                msg = peer.recv()
                if msg.get("type") == "subscribe_events":
                    peer.sub = msg["id"]
                    peer.event = msg.get("event_type")
                    peer.send({"id": msg["id"], "type": "result",
                               "success": True, "result": None})
                    self.peers.append(peer)
                    self.subscribed.set()
        except (ConnectionError, OSError, ValueError, AssertionError):
            pass

    def fire(self, event, data):
        for peer in list(self.peers):
            if peer.event == event:
                try:
                    peer.send({"id": peer.sub, "type": "event", "event": {
                        "event_type": event, "data": data}})
                except OSError:
                    pass

    def close(self):
        self.server.close()


def addon():
    print("run.py, with a stand-in Home Assistant:")
    ha = FakeHA()
    said, say = quietly()
    real_say = run.say
    run.say = say
    os.environ.pop("SUPERVISOR_TOKEN", None)
    folder = tempfile.mkdtemp()
    real_greeted = morning.GREETED_FILE
    morning.GREETED_FILE = os.path.join(folder, "morning.json")
    try:
        run._watchers.clear()
        run._config = run.regroup({
            "links": [{"name": "Home Assistant",
                       "url": ha.base + "/lovelace/0", "token": TOKEN}],
            "launcher": {"weather": {"entity": "weather.maison"},
                         "morning": {"enabled": True}},
            "launchers": [{"panel": "cuisine", "morning": False}],
            "defaults": {"phone": ""},
        })
        panels = [{"name": "salon", "host": "1.2.3.4", "url": "launcher",
                   "locale": "fr-FR"},
                  {"name": "cuisine", "host": "1.2.3.5", "url": "launcher",
                   "locale": "en-US"}]
        names = ["salon", "cuisine"]
        remotes, procs = {}, {}
        for name in names:
            procs[name] = FakeProcess()
            remotes[name] = run.Remote(name)
            remotes[name].set_process(procs[name])

        # -- notifications
        run.start_notices(panels, names, remotes)
        check("it listens for portall_notify", ha.subscribed.wait(5))
        ha.fire("portall_notify", {"message": "Le lave-linge a terminé",
                                   "title": "Lessive", "panel": "cuisine"})
        check("a notification reaches the screen it names",
              settle(lambda: any(line.startswith("notify ")
                                 for line in procs["cuisine"].lines())))
        line = next((li for li in procs["cuisine"].lines()
                     if li.startswith("notify ")), "notify {}")
        check("as the sender reads it",
              ha_send._notice_body(line[len("notify "):])
              == {"title": "Lessive", "message": "Le lave-linge a terminé",
                  "seconds": 10.0}, line)
        check("and no other", not procs["salon"].lines())
        ha.fire("portall_notify", {"message": "Porte du garage ouverte"})
        check("one naming no screen goes to every screen",
              settle(lambda: any(li.startswith("notify ")
                                 for li in procs["salon"].lines())))
        check("its words are in no line of the add-on's log",
              not any("lave-linge" in s or "garage" in s for s in said),
              repr(said[-3:]))
        ha.fire("portall_notify", {"message": "x", "panel": "grenier"})
        check("a screen that is none is said",
              settle(lambda: any('names the screen "grenier"' in s
                                 for s in said)))

        # -- a page sent to a telephone
        run.start_sharing(panels, names)
        run.ask_to_share("salon", panels[0], remotes["salon"])
        check("asked for, the screen is asked for its page",
              "share" in procs["salon"].lines())
        shared = {"url": "https://www.youtube.com/watch?v=abc",
                  "title": "Recette de crêpes"}
        run.hand_line("salon", run.ADDON_ONLY + "share "
                      + json.dumps(shared) + "\n")

        def phoned():
            return [(svc, body) for svc, body in ha.posted
                    if svc.startswith("notify/")]
        check("handed over, it goes to every phone with the app",
              settle(lambda: sorted(s for s, _ in phoned())
                     == ["notify/mobile_app_iphone_lea",
                         "notify/mobile_app_pixel"]), repr(phoned()))
        body = phoned()[0][1] if phoned() else {}
        check("with the page to open and what it is",
              body.get("title") == "Recette de crêpes"
              and body.get("data", {}).get("url") == shared["url"]
              and "envoyé depuis l'écran salon" in body.get("message", ""),
              repr(body))
        check("the log names the site and the phones, never the address",
              settle(lambda: any("share: youtube.com sent to" in s
                                 for s in said))
              and not any("watch?v=abc" in s for s in said), repr(said[-2:]))
        del ha.posted[:]
        run._config["phone"] = "notify.mobile_app_pixel"
        run.hand_line("cuisine", run.ADDON_ONLY + "share " + json.dumps(
            {"url": ha.base + "/lovelace/0", "title": "Maison"}) + "\n")
        check("the setting names one phone, and only it is sent to",
              settle(lambda: [s for s, _ in phoned()]
                     == ["notify/mobile_app_pixel"]), repr(phoned()))
        body = phoned()[0][1] if phoned() else {}
        check("Home Assistant's own page goes as a path, in English here",
              body.get("data", {}).get("url") == "/lovelace/0"
              and body.get("message", "").startswith("Home Assistant · sent"),
              repr(body))
        del ha.posted[:]
        run._share_asked["salon"] = "mobile_app_iphone_lea"
        run.hand_line("salon", run.ADDON_ONLY + "share "
                      + json.dumps(shared) + "\n")
        check("a phone the request named wins over the setting",
              settle(lambda: [s for s, _ in phoned()]
                     == ["notify/mobile_app_iphone_lea"]), repr(phoned()))

        # -- the line itself, through serve(): kept out of the log
        seen = []
        run.watch_lines("serve-probe", seen.append)
        stop = threading.Event()
        real_command = run.command_for
        run.command_for = lambda panel: [sys.executable, "-c", (
            "print('\\x1eshare {\\\"url\\\": \\\"https://x.example/secret\\\"}');"
            "print('hello from the sender')")]
        try:
            thread = threading.Thread(target=run.serve, args=(
                {"name": "serve-probe"}, "serve-probe", stop), daemon=True)
            thread.start()
            settle(lambda: len(seen) >= 2, 5)
            stop.set()
        finally:
            run.command_for = real_command
        check("serve() prints a sender's ordinary lines",
              any("[serve-probe] hello from the sender" in s for s in said))
        check("and keeps a line for the add-on alone out of the log, "
              "handing it on",
              not any("x.example" in s for s in said)
              and any(line.startswith(run.ADDON_ONLY) for line in seen),
              repr(seen))

        # -- good morning
        del ha.posted[:]
        run.start_morning(panels, names, remotes)
        check("asked of the screens whose launcher says so, not the others",
              any("Morning: a good morning on salon when" in s
                  for s in said), repr([s for s in said if "Morning" in s]))
        before = len(procs["salon"].lines())
        run.hand_line("salon", "Panel awake\n")
        time.sleep(0.5)
        check("a screen that was never dark is not greeted",
              not any(s.startswith("assist_satellite") for s, _ in ha.posted))
        run.hand_line("salon", "Panel asleep\n")
        run.hand_line("salon", "Panel awake\n")

        def announced():
            return [b for s, b in ha.posted
                    if s == "assist_satellite/announce"]
        check("waking in the morning, it is said on the voice assistant",
              settle(lambda: announced()), repr(ha.posted))
        spoken = (announced() or [{}])[0]
        check("the day's weather and calendar, without a chime",
              spoken.get("entity_id") == "assist_satellite.salon"
              and spoken.get("preannounce") is False
              and spoken.get("message") == "Bonjour ! Aujourd'hui, "
              "ensoleillé, de 12 à 21 degrés. À l'agenda : Anniversaire de "
              "Léa ; à 10 h 30, Dentiste.", repr(spoken))
        shown = [li for li in procs["salon"].lines()[before:]
                 if li.startswith("notify ")]
        body = ha_send._notice_body(shown[0][len("notify "):]) \
            if shown else {}
        check("and shown on the screen",
              body.get("title") == "☀️ Bonjour !"
              and "10 h 30 Dentiste" in body.get("message", ""), repr(shown))
        calendar = [q for s, q in ha.posted if s == "calendar"]
        check("the calendar asked for the rest of today, by HA's clock",
              calendar and calendar[0].get("start") == [
                  "2026-10-21T07:05:00+02:00"]
              and calendar[0].get("end") == ["2026-10-21T23:59:59+02:00"],
              repr(calendar))
        count = len(announced())
        run.hand_line("salon", "Panel asleep\n")
        run.hand_line("salon", "Panel awake\n")
        time.sleep(0.6)
        check("once a day: a second wake says nothing",
              len(announced()) == count)
        ha.day, ha.hour = "2026-10-22", 15
        run.hand_line("salon", "Panel asleep\n")
        run.hand_line("salon", "Panel awake\n")
        time.sleep(0.6)
        check("the afternoon is not the morning", len(announced()) == count)
        ha.hour, ha.features = 8, 0
        run.hand_line("salon", "Panel asleep\n")
        run.hand_line("salon", "Panel awake\n")
        check("a voice assistant that cannot announce: shown, and said why",
              settle(lambda: any("shown, not said -- assist_satellite.salon "
                                 "cannot make announcements" in s
                                 for s in said)), repr(said[-2:]))
        check("the cuisine's own launcher turned it off: never greeted",
              not run._watchers.get("cuisine")
              or all("morning" not in s for s in said if "[cuisine]" in s))
    finally:
        run.say = real_say
        morning.GREETED_FILE = real_greeted
        ha.close()


# --- 3. the shipped sender against a fake panel ------------------------------

PAGE = (40, 120, 90)
GROUND = (30, 34, 44)


def sender():
    print("The sender, a fake panel, and a still page:")
    try:
        import playwright  # noqa: F401
        from PIL import Image
    except ImportError:
        print("  --   skipped: no Playwright or Pillow here")
        return
    from udisp_send import _HEADER, UDISP_TYPE_JPG

    clicks = []

    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/clicked"):
                clicks.append(self.path)
                body = b""
            else:
                body = ("<!doctype html><title>Recette de crêpes</title>"
                        "<body style='margin:0;height:100vh;background:rgb%s'"
                        " onclick=\"fetch('/clicked')\">" % (PAGE,)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    # 127.0.0.2: a page a telephone could open, which 127.0.0.1 -- the
    # launcher's own address -- is not.
    pages = http.server.ThreadingHTTPServer(("127.0.0.2", 0), Pages)
    threading.Thread(target=pages.serve_forever, daemon=True).start()
    address = f"http://127.0.0.2:{pages.server_address[1]}/"

    width, height = 480, 320
    canvas = Image.new("RGB", (width, height))
    lock = threading.Lock()
    panel = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        panel.append(conn)
        stream = b""
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                stream += data
                while len(stream) >= _HEADER.size:
                    _, kind, _, x, y, _, _, packed = _HEADER.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    blob = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG:
                        with lock:
                            canvas.paste(Image.open(io.BytesIO(blob))
                                         .convert("RGB"), (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    def at(x, y):
        with lock:
            return canvas.getpixel((x, y))

    def near(colour, want, slack=24):
        return all(abs(a - b) <= slack for a, b in zip(colour, want))

    # Where a notification's ground shows: its bottom edge, under the words.
    spot = (width // 2, 0)

    def toast_shown():
        return near(at(*spot), GROUND)

    def page_shown():
        return near(at(width // 2, height - 20), PAGE) \
            and near(at(*spot), PAGE)

    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]), "--url", address,
               "--no-token", "--not-home-assistant", "--width", str(width),
               "--height", str(height), "--audio", "off", "--keyboard", "off",
               "--control"]
    if os.environ.get("CHROMIUM"):
        command += ["--browser", os.environ["CHROMIUM"]]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []

    def read():
        for line in process.stdout:
            out.append(line)
    threading.Thread(target=read, daemon=True).start()

    def logged(text, since=0):
        return any(text in line for line in out[since:])

    def send(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def tap(x, y, hold=0.08):
        panel[0].sendall(b"T\x01\x00" + int(x).to_bytes(2, "little")
                         + int(y).to_bytes(2, "little"))
        time.sleep(hold)
        panel[0].sendall(b"T\x00")

    try:
        check("the sender starts", settle(lambda: logged("Ready"), 40),
              "".join(out[-5:]))
        check("its page reaches the panel", settle(page_shown, 10),
              repr(at(width // 2, height - 20)))
        # Long enough for the page to be truly still -- the corner's mark
        # gone, the finished picture sent -- so nothing but the notification
        # can make a picture.
        time.sleep(4.0)
        sent_at = time.monotonic()
        send("notify " + json.dumps({"title": "Lessive",
                                     "message": "Le lave-linge a terminé",
                                     "seconds": 4}))
        check("said, with no words", settle(lambda: logged(
            "Notice: on the screen for 4s"), 5)
            and not any("lave-linge" in line for line in out))
        # Where the box is: the sender's own geometry, asked of a Toasts of
        # the same size, so the ruler cannot drift from the drawing.
        probe = ha_send.Toasts(width, height)
        probe.add({"title": "Lessive", "message": "Le lave-linge a terminé",
                   "seconds": 4}, 0)
        probe.tick(0, True)
        probe.draw(Image.new("RGB", (width, height)), 0)
        x0, y0, x1, y1, _ = probe.rects[0]
        spot = ((x0 + x1) // 2, y1 - 3)
        shown = settle(toast_shown, 3)
        took = time.monotonic() - sent_at
        check(f"written over a page that paints nothing new, at once "
              f"({took * 1000:.0f} ms)", shown and took < 1.0,
              repr(at(*spot)))
        check("and the page around it untouched",
              near(at(width // 2, height - 20), PAGE))
        check("gone after its time", settle(page_shown, 6), repr(at(*spot)))

        # The same shape as the first, so its box is where the ruler is.
        send("notify " + json.dumps({"title": "Garage",
                                     "message": "La porte est ouverte",
                                     "seconds": 60}))
        check("another one", settle(toast_shown, 5), repr(at(*spot)))
        before = len(clicks)
        mark = len(out)
        tap(spot[0], spot[1])
        check("a tap on it closes it", settle(page_shown, 5)
              and logged("Notice: closed from the screen", mark),
              "".join(out[mark:]))
        time.sleep(1.0)
        check("and presses nothing underneath", len(clicks) == before,
              repr(clicks))
        tap(width // 2, height - 20)
        check("the next tap presses the page as usual",
              settle(lambda: len(clicks) > before, 5), repr(clicks))

        panel[0].sendall(b"S\x00")
        settle(lambda: logged("Panel asleep"), 5)
        mark = len(out)
        send("notify " + json.dumps({"title": "Nuit",
                                     "message": "Pendant la nuit",
                                     "seconds": 30}))
        check("dark, it waits", settle(lambda: logged(
            "Notice: the screen is dark; shown when it lights up", mark), 5),
            "".join(out[mark:]))
        panel[0].sendall(b"S\x01")
        check("lit, it shows", settle(toast_shown, 6), repr(at(*spot)))

        mark = len(out)
        send("share")
        check("asked for its page, it hands it over on a line of its own",
              settle(lambda: any(line.startswith(ha_send.SHARE_LINE)
                                 for line in out[mark:]), 5),
              "".join(out[mark:]))
        line = next((li for li in out[mark:]
                     if li.startswith(ha_send.SHARE_LINE)), "")
        try:
            shared = json.loads(line[len(ha_send.SHARE_LINE):])
        except ValueError:
            shared = {}
        check("the address and the title the page gives itself",
              shared == {"url": address, "title": "Recette de crêpes"},
              repr(shared))
        check("which is the only line with the address in it",
              sum(address in li for li in out[mark:]) == 1)
    finally:
        process.kill()
        pages.shutdown()
        listener.close()


def main():
    units()
    addon()
    sender()
    bad = results.count(False)
    print("all good" if not bad else f"{bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
