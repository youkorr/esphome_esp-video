#!/usr/bin/env python3
"""Opening a launcher link by voice, from the sentence to the sender's stdin.

WHY THIS EXISTS. Asked as *"allez go ont test"* after *"ouvre Jellyfin"* was
proposed the way every Python "Jarvis" does it. Four places have to agree and
none of them can be seen from the others: the sentences Home Assistant is
given, the automation that carries them, the event it fires, and the line the
add-on writes to the right panel's sender. So this drives each with the
SHIPPED code:

  1. the sentences, matched by hassil -- the library Home Assistant itself
     matches trigger sentences with -- including the ones that must NOT
     match, because a sentence trigger is asked BEFORE Home Assistant's own
     commands and "ouvre le volet du salon" must stay a cover's;
  2. the automation, written to a stand-in of Home Assistant's own
     configuration API, once and not again when nothing changed;
  3. the event, sent over a real websocket to the shipped VoiceLinks;
  4. run.py's routing, down a real Remote into the sender's own Control.

    python3 tools/checkvoicelinks.py

  5. the shipped ha_send.py itself, given `open <address>` on its stdin the
     way run.py gives it, against a fake panel: the browser must fetch the
     page and a picture of it must reach the panel.

The hassil half needs `pip install hassil==3.12.1` (Home Assistant's own pin)
and the response half jinja2; each says it was skipped when they are absent.
The sender half needs Playwright and a Chromium, named by $CHROMIUM when
Playwright's own is not installed.
"""

import io
import json
import os
import pathlib
import socket
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))
sys.path.insert(0, str(HERE / "tools"))

import run  # noqa: E402
import voicelinks  # noqa: E402
from checkvoice import GUID, Peer  # noqa: E402,F401
from ha_send import Control  # noqa: E402

TOKEN = "a.b.c"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + (f"  ({detail})" if detail and not ok else ""))


LINKS = [
    {"name": "Home Assistant", "url": "http://ha.local/lovelace/0",
     "token": TOKEN},
    {"name": "Jellyfin", "url": "http://jf.local/"},
    {"name": "YouTube", "url": "https://www.youtube.com/tv"},
    {"name": "Camera & <cuisine>", "url": "http://cam.local/"},
    {"name": "Orange TV", "url": "https://tv.orange.fr/"},
    {"name": "🎬", "url": "http://nothing-to-say.local/"},
]


# --- 1. the sentences ------------------------------------------------------

def sentences():
    print("The sentences, matched the way Home Assistant matches them:")
    try:
        from hassil import Intents, recognize_all
        from hassil.parse_expression import parse_sentence
        from hassil.util import (PUNCTUATION_END, PUNCTUATION_END_WORD,
                                 PUNCTUATION_START, PUNCTUATION_START_WORD)
    except ImportError:
        print("  --   skipped: no hassil here (pip install hassil==3.12.1)")
        return
    names, dropped = voicelinks.names_of(LINKS)
    check("a name with nothing to say is left out, and said", dropped == ["🎬"])
    auto = voicelinks.automation(names)
    ok = True
    for trigger in auto["triggers"]:
        for sentence in trigger["command"]:
            # conversation/trigger.py: has_no_punctuation, is_valid_sentence
            bare = sentence
            if (PUNCTUATION_START.search(bare) or PUNCTUATION_END.search(bare)
                    or PUNCTUATION_START_WORD.search(bare)
                    or PUNCTUATION_END_WORD.search(bare)):
                ok = False
            parse_sentence(sentence)
    check("every sentence passes the trigger's own validation", ok)
    intents = {f"t{i}": {"data": [{"sentences": t["command"]}]}
               for i, t in enumerate(auto["triggers"])}
    ids = {f"t{i}": t["id"] for i, t in enumerate(auto["triggers"])}
    compiled = Intents.from_dict({"language": "fr", "intents": intents})

    def heard(text):
        found = [ids[r.intent.name] for r in recognize_all(text, compiled)]
        return found[0] if found else None

    for text, want in (("ouvre Jellyfin", "link:Jellyfin"),
                       ("Lance youtube.", "link:YouTube"),
                       ("mets Orange TV", "link:Orange TV"),
                       ("ouvre Home Assistant", "link:Home Assistant"),
                       ("affiche camera cuisine", "link:Camera cuisine"),
                       ("open Jellyfin", "link:Jellyfin"),
                       ("retour à l'accueil", "home"),
                       ("page d'accueil", "home"),
                       ("go home", "home")):
        got = heard(text)
        check(f"\"{text}\" -> {want}", got == want, repr(got))
    for text in ("ouvre le volet du salon", "ouvre le portail",
                 "allume la lumière du salon", "ouvre Spotify"):
        got = heard(text)
        check(f"\"{text}\" is left to Home Assistant", got is None, repr(got))


def response():
    print("What the voice assistant answers:")
    try:
        import jinja2
    except ImportError:
        print("  --   skipped: no jinja2 here")
        return
    auto = voicelinks.automation(["Jellyfin"])
    template = jinja2.Environment().from_string(
        auto["actions"][1]["set_conversation_response"])
    fr = {"user_input": {"language": "fr"}}
    en = {"user_input": {"language": "en"}}
    check("J'ouvre Jellyfin",
          template.render(trigger={**fr, "id": "link:Jellyfin"})
          == "J'ouvre Jellyfin")
    check("Opening Jellyfin in English",
          template.render(trigger={**en, "id": "link:Jellyfin"})
          == "Opening Jellyfin")
    check("Retour à l'accueil",
          template.render(trigger={**fr, "id": "home"})
          == "Retour à l'accueil")


# --- 2 and 3. a stand-in Home Assistant -------------------------------------

class FakeHA:
    """Home Assistant's automation config API and websocket, on one port."""

    def __init__(self, refuse=False):
        self.stored = None
        self.requests = []
        self.refuse = refuse
        self.peers = []
        self.subscribed = threading.Event()
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
            threading.Thread(target=self._serve, args=(conn,),
                             daemon=True).start()

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
            self._websocket(conn, head)
            return
        body = rest
        want = int(headers.get("content-length") or 0)
        while len(body) < want:
            body += conn.recv(4096)
        self.requests.append((method, path))
        if headers.get("authorization") != f"Bearer {TOKEN}":
            return self._reply(conn, 401, {"message": "Unauthorized"})
        if not path.endswith(f"/api/config/automation/config/"
                             f"{voicelinks.AUTOMATION_ID}"):
            return self._reply(conn, 404, {"message": "Not found"})
        if method == "GET":
            if self.stored is None:
                return self._reply(conn, 404, {"message": "Resource not found"})
            return self._reply(conn, 200, {"id": voicelinks.AUTOMATION_ID,
                                           **self.stored})
        if method == "POST":
            if self.refuse:
                return self._reply(conn, 400,
                                   {"message": "Message malformed: nope"})
            self.stored = json.loads(body)
            return self._reply(conn, 200, {"result": "ok"})
        if method == "DELETE":
            self.stored = None
            return self._reply(conn, 200, {"result": "ok"})
        return self._reply(conn, 405, {})

    def _reply(self, conn, status, obj):
        data = json.dumps(obj).encode()
        conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Type: application/json"
                     f"\r\nContent-Length: {len(data)}\r\nConnection: close"
                     f"\r\n\r\n".encode() + data)
        conn.close()

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
                    peer.event_type = msg.get("event_type")
                    peer.send({"id": msg["id"], "type": "result",
                               "success": True, "result": None})
                    self.peers.append(peer)
                    self.subscribed.set()
        except (ConnectionError, OSError, ValueError, AssertionError):
            pass

    def fire(self, data):
        for peer in self.peers:
            if peer.event_type == voicelinks.EVENT:
                peer.send({"id": peer.sub, "type": "event", "event": {
                    "event_type": voicelinks.EVENT, "data": data}})

    def close(self):
        self.server.close()


def quietly():
    lines = []
    return lines, lines.append


def automation_api():
    print("The automation, kept in Home Assistant:")
    ha = FakeHA()
    said, say = quietly()
    links = voicelinks.VoiceLinks(ha.base, TOKEN, LINKS, None, say)
    check("the first start writes it", links.sync()
          and ("POST", f"/api/config/automation/config/"
                       f"{voicelinks.AUTOMATION_ID}") in ha.requests)
    stored = ha.stored or {}
    check("with one trigger per link that can be said, and home",
          [t["id"] for t in stored.get("triggers", [])]
          == ["link:Home Assistant", "link:Jellyfin", "link:YouTube",
              "link:Camera cuisine", "link:Orange TV", "home"])
    check("and says which links it knows", any("5 link(s)" in s for s in said))
    before = len(ha.requests)
    again = voicelinks.VoiceLinks(ha.base, TOKEN, LINKS, None, say)
    check("a start with the same links writes nothing",
          again.sync() and ha.requests[before:] ==
          [("GET", f"/api/config/automation/config/"
                   f"{voicelinks.AUTOMATION_ID}")])
    more = voicelinks.VoiceLinks(ha.base, TOKEN,
                                 LINKS + [{"name": "Netflix", "url": "x"}],
                                 None, say)
    check("a new link rewrites it", more.sync()
          and "link:Netflix" in [t["id"] for t in ha.stored["triggers"]])
    voicelinks.remove(ha.base, TOKEN, say)
    check("turning it off removes it", ha.stored is None
          and any("removed" in s for s in said))
    del said[:]
    voicelinks.remove(ha.base, TOKEN, say)
    check("and says nothing when there was nothing to remove", said == [])
    ha.close()

    refusing = FakeHA(refuse=True)
    said, say = quietly()
    bad = voicelinks.VoiceLinks(refusing.base, TOKEN, LINKS, None, say)
    check("a refusal is said, with Home Assistant's reason",
          bad.sync() is False
          and any("400" in s and "malformed" in s for s in said))
    refusing.close()


def settle(until, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        time.sleep(0.02)
    return until()


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()


def end_to_end():
    print("From the event to the sender, through run.py:")
    ha = FakeHA()
    links = [dict(link) for link in LINKS]
    links[0]["url"] = ha.base + "/lovelace/0"      # the dashboard's link
    kitchen_links = [{"name": "Jellyfin", "url": "http://kitchen-jf.local/"}]
    config = run.regroup({
        "links": links,
        "launcher": {"voice_links": True},
        "launchers": [{"panel": "cuisine", "links": kitchen_links,
                       "avatar_voice": "assist_satellite.cuisine"}],
    })
    check("the form's grouped key reaches the flat name",
          config.get("launcher_voice_links") is True)
    run._config = config
    panels = [{"name": "salon", "host": "1", "url": "x"},
              {"name": "cuisine", "host": "2", "url": "y",
               "launcher_links": kitchen_links}]
    processes = {"salon": FakeProcess(), "cuisine": FakeProcess()}
    remotes = {}
    for name, process in processes.items():
        remotes[name] = run.Remote(name)
        remotes[name].set_process(process)
    said = []
    real_say = run.say
    run.say = said.append
    env = os.environ.pop("SUPERVISOR_TOKEN", None)
    try:
        started = run.start_voice_links(panels, remotes)
        check("it starts", started is not None)
        check("and subscribes to the event", ha.subscribed.wait(5))

        def lines(name):
            return processes[name].stdin.getvalue().splitlines()

        ha.fire({"link": "link:Jellyfin",
                 "satellite": "assist_satellite.cuisine"})
        check("the kitchen heard it, so the kitchen opens ITS Jellyfin",
              settle(lambda: lines("cuisine") ==
                     ["open http://kitchen-jf.local/"]), lines("cuisine"))
        check("and the living room is left alone", lines("salon") == [])
        ha.fire({"link": "link:Orange TV",
                 "satellite": "assist_satellite.cuisine"})
        check("a link only the house has is still opened",
              settle(lambda: lines("cuisine")[-1:] ==
                     ["open https://tv.orange.fr/"]), lines("cuisine"))
        ha.fire({"link": "home", "satellite": "assist_satellite.cuisine"})
        check("home goes home",
              settle(lambda: lines("cuisine")[-1:] == ["home"]))
        ha.fire({"link": "Jellyfin", "panel": "salon"})
        check("a plain name and a panel, from a button or an automation",
              settle(lambda: lines("salon") == ["open http://jf.local/"]),
              lines("salon"))
        ha.fire({"link": "link:Jellyfin", "satellite": "None"})
        check("with several panels and no idea which heard it, nothing opens"
              " and it is said",
              settle(lambda: any("several panels" in s for s in said))
              and lines("salon") == ["open http://jf.local/"])
        ha.fire({"link": "link:Spotify", "satellite":
                 "assist_satellite.cuisine"})
        check("a name that is no link is said",
              settle(lambda: any('no link is called "Spotify"' in s
                                 for s in said)))
        check("no line of it carries the token",
              not any(TOKEN in s for s in said))
    finally:
        run.say = real_say
        if env is not None:
            os.environ["SUPERVISOR_TOKEN"] = env
        ha.close()

    print("And the sender's side:")
    control = Control(io.StringIO("open http://jf.local/\nopen\nhome\n"))
    control._thread.join(timeout=2)
    got = control.drain()
    check("the sender's Control hands the loop an address to open",
          got == [("open", "http://jf.local/"), ("home", True)], repr(got))
    check("a line carrying a newline is never sent",
          run.Remote("x").send("open", "http://a\nhome") is False)
    argv = run.command_for({"host": "1", "url": "x", "control": True})
    check("a panel told to listen gets --control", "--control" in argv)
    argv = run.command_for({"host": "1", "url": "x"})
    check("and one that is not does not", "--control" not in argv)


def one_panel():
    print("One panel needs no names at all:")
    panel = {"name": "salon"}
    got, why = run.voice_target([panel], {}, "", "")
    check("whatever heard it, the only panel is the one", got is panel)
    got, why = run.voice_target([panel], {}, "", "garage")
    check("a panel named that does not exist is said, not guessed",
          got is None and "garage" in why)


def sender():
    print("The sender opens it, in its real browser:")
    import http.server
    import subprocess
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  --   skipped: no Playwright here")
        return
    asked = []

    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            colour = "#123456" if self.path.startswith("/b") else "#654321"
            body = (f"<!doctype html><body style='margin:0;background:"
                    f"{colour}'>{self.path}").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    pages = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=pages.serve_forever, daemon=True).start()
    page = f"http://127.0.0.1:{pages.server_address[1]}"

    # The panel reads the stream the way the board does -- a 16-byte header,
    # then payload_total bytes -- and keeps the colour at the centre of every
    # whole-panel picture. Bytes alone prove nothing: the corner mark fading
    # five seconds after a page arrives is a picture too, on the old page.
    import struct
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    received = [0]
    colours = []

    def look(width, height, payload):
        try:
            from PIL import Image
            picture = Image.open(io.BytesIO(payload)).convert("RGB")
            colours.append(picture.getpixel((width // 2, height // 2)))
        except Exception:  # noqa: BLE001 - a rectangle we cannot read
            pass
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        stream = b""
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                received[0] += len(data)
                stream += data
                while len(stream) >= _HEADER.size:
                    _, kind, _, x, y, w, h, packed = _HEADER.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    payload = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG and (w, h) == (320, 240):
                        look(w, h, payload)
        except (OSError, struct.error):
            pass
    threading.Thread(target=accept, daemon=True).start()

    command = [sys.executable, "-u",
               str(HERE / "components" / "portall" / "ha_send.py"),
               "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]),
               "--url", f"{page}/a.html", "--no-token",
               "--not-home-assistant", "--width", "320", "--height", "240",
               "--audio", "off", "--keyboard", "off", "--control"]
    if os.environ.get("CHROMIUM"):
        command += ["--browser", os.environ["CHROMIUM"]]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    try:
        started = settle(lambda: any(line.startswith("Ready")
                                     for line in out), 40)
        check("the sender starts on its first page", started, "".join(out))
        settle(lambda: colours, 10)
        process.stdin.write(f"open {page}/b.html\n")
        process.stdin.flush()
        check("the browser fetches the link it was told to open",
              settle(lambda: "/b.html" in asked, 15), repr(asked))
        check("and says so in the log",
              settle(lambda: any("Voice: opened" in line for line in out),
                     10), "".join(out[-5:]))
        # (0x12, 0x34, 0x56) is the new page's ground; JPEG moves it a little.
        def new_page():
            return any(abs(c[0] - 0x12) < 12 and abs(c[1] - 0x34) < 12
                       and abs(c[2] - 0x56) < 12 for c in colours)
        check("and a picture of the new page reaches the panel",
              settle(new_page, 10), repr(colours[-3:]))
    finally:
        process.kill()
        pages.shutdown()
        listener.close()


def main():
    sentences()
    response()
    automation_api()
    end_to_end()
    one_panel()
    sender()
    failed = results.count(False)
    print(f"\n{len(results) - failed} ok, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
