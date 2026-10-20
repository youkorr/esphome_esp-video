#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A camera on the screens when somebody is at the door, end to end.

WHY THIS EXISTS. Asked for by a household with a camera on the porch, for
others with a doorbell: when a sensor in Home Assistant says somebody is
there, the chosen screens show the camera live over whatever they show, and
go back to exactly that afterwards or at a touch. Five places have to agree
and none of them can see the others: the sensor's state over Home Assistant's
websocket, the add-on's choice of screens, the line it writes to a sender,
the sender's own page and screencast, and -- for a screen that was dark --
the light the add-on turns on and, if nobody touched it, off again. So this
drives each with the SHIPPED code:

  1. alerts.py on its own: what an entry means, what counts as somebody
     being there, the two minutes before an alert is shown again;
  2. alerts.Alerts against a stand-in Home Assistant over a real websocket;
  3. run.py: the command line, the routing to screens, the camera's address
     with its access token, and the light;
  4. the shipped ha_send.py against a fake panel that reassembles what it is
     sent, with the camera served as Home Assistant serves one -- an MJPEG
     stream behind an access token -- driven through run.start_alerts and the
     stand-in's websocket: the camera reaches the glass, the page comes back
     after its time and at a touch (which presses nothing), a dark screen is
     lit for it and put back in the dark, and the camera's token reaches no
     log.

    python3 tools/checkalerts.py [--sender PATH]

The sender half needs Playwright, a Chromium (named by $CHROMIUM when
Playwright's own is not installed) and Pillow. About a minute and a half.
"""

import http.server
import io
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))
sys.path.insert(0, str(HERE / "tools"))

import alerts  # noqa: E402
import run  # noqa: E402
from checkvoice import Peer  # noqa: E402


def option(name, default):
    for n, arg in enumerate(sys.argv):
        if arg == name and n + 1 < len(sys.argv):
            return sys.argv[n + 1]
    return default


SENDER = option("--sender", str(HERE / "components" / "portall"
                                / "ha_send.py"))
TOKEN = "a.b.c"
ACCESS = "f00dcafe" * 8
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + (f"  ({detail})" if detail and not ok else ""))


def settle(until, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        time.sleep(0.05)
    return bool(until())


def quietly():
    lines = []
    return lines, lines.append


# --- 1. alerts.py on its own ------------------------------------------------

def units():
    print("What an entry means:")
    lines, say = quietly()
    parsed = alerts.parse([
        {"sensor": "binary_sensor.porch_person", "camera": "camera.porch"},
        {"sensor": "event.doorbell", "camera": "camera.porch",
         "seconds": 900, "panels": "Salon, nowhere", "wake": True},
        {"sensor": "binary_sensor.x"},
        {"sensor": "binary_sensor.x", "camera": "image.porch"},
        {"sensor": "binary_sensor.x", "camera": "camera.y",
         "panels": "nowhere"},
        {"sensor": "binary_sensor.x", "camera": "camera.y", "seconds": 1,
         "wake": "false"},
    ], ["salon", "cuisine"], say)
    check("a sensor and a camera are all it takes", parsed[0] == {
        "sensor": "binary_sensor.porch_person", "camera": "camera.porch",
        "seconds": 30, "panels": None, "wake": False}, repr(parsed[0]))
    check("its screens are matched by name, the time is held to 5 minutes",
          parsed[1]["panels"] == ["salon"] and parsed[1]["seconds"] == 300
          and parsed[1]["wake"] is True, repr(parsed[1]))
    check("a screen that does not exist is named in the log",
          any("nowhere" in line and "salon, cuisine" in line
              for line in lines), repr(lines))
    check("an entry with no camera, or one that is not a camera, is left out",
          len(parsed) == 3 and any("needs both" in line for line in lines)
          and any("image.porch is not a camera" in line for line in lines),
          repr(lines))
    check("five seconds at least, and the word false is not true",
          parsed[2]["seconds"] == 5 and parsed[2]["wake"] is False,
          repr(parsed[2]))
    check("every screen when none is named",
          alerts.targets(parsed[0], ["salon", "cuisine"])
          == ["salon", "cuisine"])

    print("What counts as somebody being there:")
    b = "binary_sensor.porch"
    check("a sensor turning on", alerts.fires(b, "off", "on"))
    check("not one coming back on the network already on",
          not alerts.fires(b, "on", "on"))
    check("one never seen with a real state, turning on",
          alerts.fires(b, None, "on"))
    check("not one going unavailable", not alerts.fires(b, "off",
                                                        "unavailable"))
    e = "event.doorbell"
    check("a doorbell's new event",
          alerts.fires(e, "2026-10-20T08:00:00", "2026-10-20T09:00:00"))
    check("its very first", alerts.fires(e, None, "2026-10-20T09:00:00"))
    check("not the same event again after it was unavailable",
          not alerts.fires(e, "2026-10-20T09:00:00", "2026-10-20T09:00:00"))

    print("Not twice inside two minutes:")
    now = [1000.0]
    fired = []
    lines, say = quietly()
    watch = alerts.Alerts("http://x", TOKEN, [
        {"sensor": b, "camera": "camera.porch", "seconds": 30,
         "panels": None, "wake": False}], fired.append, say,
        clock=lambda: now[0])
    watch.changed(b, "on", initial=True)
    check("a sensor already on when the add-on starts does not fire", not fired)
    watch.changed(b, "off")
    watch.changed(b, "on")
    check("it fires when it turns on", len(fired) == 1)
    now[0] += 60
    watch.changed(b, "off")
    watch.changed(b, "on")
    check("not again a minute later, and says so",
          len(fired) == 1 and any("not shown twice" in line
                                  for line in lines), repr(lines))
    now[0] += 61
    watch.changed(b, "unavailable")
    watch.changed(b, "on")
    check("not from unavailable back to the on it was", len(fired) == 1)
    watch.changed(b, "off")
    watch.changed(b, "on")
    check("again once two minutes have passed", len(fired) == 2)

    print("The words the sender and the add-on share:")
    try:
        import ha_send
    except ImportError as err:
        check("the sender can be read", False, str(err))
        return
    check("an alert that ran out with nobody touching the screen",
          ha_send.ALERT_UNTOUCHED == alerts.UNTOUCHED)
    check("the alert closed from the screen",
          ha_send.ALERT_TOUCHED == alerts.TOUCHED)
    body = ha_send._alert_body(json.dumps({
        "url": "http://ha/api/camera_proxy_stream/camera.p?token=x",
        "name": "Porche\nd'entrée", "seconds": 9999}))
    check("an alert line is read, its time held and its name one line",
          body == {"url": "http://ha/api/camera_proxy_stream/camera.p?token=x",
                   "name": "Porche d'entrée", "seconds": 300}, repr(body))
    check("one that is not JSON, or not an address, is refused",
          ha_send._alert_body("{not json") is None
          and ha_send._alert_body('{"url": "javascript:x"}') is None)


# --- 2 and 3. a stand-in Home Assistant --------------------------------------

def jpeg(colour, n=0, size=(320, 240)):
    """One picture of the camera: its colour, and a square that moves along
    the top from one picture to the next, as anything filmed does -- so the
    page changes in part, the way a camera does, and not as a whole."""
    from PIL import Image, ImageDraw
    out = io.BytesIO()
    picture = Image.new("RGB", size, colour)
    x = (n * 24) % (size[0] - 40)
    ImageDraw.Draw(picture).rectangle((x, 16, x + 40, 56), fill=(250, 250, 250))
    picture.save(out, "JPEG", quality=90)
    return out.getvalue()


class FakeHA:
    """Home Assistant's websocket, states, services, ESPHome devices and a
    camera's MJPEG stream, on one port."""

    CAMERA = (230, 30, 200)

    def __init__(self, host="127.0.0.1"):
        self.states = {"binary_sensor.porch_person": "off",
                       "light.salon_backlight": "on"}
        self.posted = []
        self.peers = []
        self.streams = 0
        self.stream_paths = []
        self.on_light = None
        self.host = host
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
        if path.startswith("/api/camera_proxy_stream/"):
            self._stream(conn, path)
            return
        body = rest
        want = int(headers.get("content-length") or 0)
        while len(body) < want:
            body += conn.recv(4096)
        if headers.get("authorization") != f"Bearer {TOKEN}":
            return self._reply(conn, 401, {"message": "Unauthorized"})
        split = urllib.parse.urlsplit(path)
        if method == "GET" and split.path.startswith("/api/states/"):
            eid = split.path[len("/api/states/"):]
            if eid == "camera.porch":
                return self._reply(conn, 200, {
                    "entity_id": eid, "state": "idle", "attributes": {
                        "access_token": ACCESS, "friendly_name": "Porche"}})
            if eid in self.states:
                return self._reply(conn, 200, {"entity_id": eid,
                                               "state": self.states[eid]})
            return self._reply(conn, 404, {"message": "Entity not found."})
        if method == "POST" and split.path.startswith("/api/services/light/"):
            what = split.path.rsplit("/", 1)[1]
            eid = json.loads(body or b"{}").get("entity_id")
            self.posted.append((what, eid))
            self.states[eid] = "on" if what == "turn_on" else "off"
            if self.on_light is not None:
                self.on_light(self.states[eid] == "on")
            return self._reply(conn, 200, [])
        if split.path == "/api/config/config_entries/entry":
            return self._reply(conn, 200, [{"entry_id": "e1",
                                            "title": "Salon"}])
        if split.path == "/api/diagnostics/config_entry/e1":
            return self._reply(conn, 200, {"data": {"config": {"data": {
                "host": self.host, "device_name": "salon"}}}})
        if split.path == "/api/template":
            data = b"light.salon_backlight\nsensor.salon_wifi_signal\n"
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                         b"Content-Length: " + str(len(data)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + data)
            conn.close()
            return
        return self._reply(conn, 404, {"message": "Not found"})

    def _stream(self, conn, path):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        self.stream_paths.append(path)
        if query.get("token") != [ACCESS]:
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n"
                         b"Connection: close\r\n\r\n")
            conn.close()
            return
        self.streams += 1
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/"
                         b"x-mixed-replace;boundary=frameboundary\r\n"
                         b"Connection: close\r\n\r\n")
            n = 0
            while True:
                frame = jpeg(self.CAMERA, n)
                part = (b"--frameboundary\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode()
                        + b"\r\n\r\n" + frame + b"\r\n")
                # The first twice, as Home Assistant does for Chrome.
                conn.sendall(part + (part if n == 0 else b""))
                n += 1
                time.sleep(0.25)
        except OSError:
            pass
        finally:
            self.streams -= 1
            conn.close()

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
                if msg.get("type") == "subscribe_entities":
                    peer.sub = msg["id"]
                    peer.wanted = msg.get("entity_ids") or []
                    peer.send({"id": msg["id"], "type": "result",
                               "success": True, "result": None})
                    peer.send({"id": msg["id"], "type": "event", "event": {
                        "a": {eid: {"s": self.states[eid], "a": {}}
                              for eid in peer.wanted if eid in self.states}}})
                    self.peers.append(peer)
                    self.subscribed.set()
        except (ConnectionError, OSError, ValueError, AssertionError):
            pass

    def set_state(self, eid, state):
        self.states[eid] = state
        for peer in list(self.peers):
            if eid in peer.wanted:
                try:
                    peer.send({"id": peer.sub, "type": "event", "event": {
                        "c": {eid: {"+": {"s": state}}}}})
                except OSError:
                    self.peers.remove(peer)

    def close(self):
        self.server.close()


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()


ALERT = {"sensor": "binary_sensor.porch_person", "camera": "camera.porch",
         "seconds": 5, "panels": None, "wake": False}


def websocket():
    print("The sensor, followed over Home Assistant's websocket:")
    ha = FakeHA()
    fired = []
    lines, say = quietly()
    try:
        watch = alerts.Alerts(ha.base, TOKEN, [ALERT], fired.append, say)
        watch.start()
        check("it subscribes to the sensor", ha.subscribed.wait(5))
        check("and says what it follows",
              settle(lambda: any("following binary_sensor.porch_person"
                                 in line for line in lines)), repr(lines))
        ha.set_state("binary_sensor.porch_person", "on")
        check("somebody on the porch fires the alert",
              settle(lambda: fired), repr(lines))
        ha.set_state("binary_sensor.porch_person", "off")
        ha.set_state("binary_sensor.porch_person", "on")
        check("and the same again at once does not",
              settle(lambda: any("not shown twice" in line
                                 for line in lines)) and len(fired) == 1,
              repr(lines))
    finally:
        ha.close()

    print("A sensor that does not exist is named:")
    ha = FakeHA()
    lines, say = quietly()
    try:
        alerts.Alerts(ha.base, TOKEN, [dict(ALERT, sensor="binary_sensor.no")],
                      fired.append, say).start()
        check("in the log, once",
              settle(lambda: any("binary_sensor.no does not exist" in line
                                 for line in lines)), repr(lines))
    finally:
        ha.close()


def routing():
    print("The add-on: which screens, what line, which light:")
    ha = FakeHA()
    said = []
    real_say = run.say
    run.say = said.append
    # Through regroup(), as the options the Supervisor writes arrive.
    run._config = run.regroup({
        "links": [{"name": "Home Assistant", "url": f"{ha.base}/lovelace/0",
                   "token": TOKEN}],
        "alerts": [dict(ALERT, panels="salon"),
                   dict(ALERT, sensor="binary_sensor.door",
                        panels="cuisine", wake=True)]})
    try:
        panels = [{"name": "salon", "host": "127.0.0.1", "url": "launcher"},
                  {"name": "cuisine", "host": "127.0.0.1", "url": "launcher"},
                  {"name": "bureau", "host": "10.0.0.9", "url": "launcher"}]
        names = ["salon", "cuisine", "bureau"]
        planned = run.plan_alerts(panels, names)
        check("the screens an alert can appear on are marked",
              panels[0].get("alerts") and panels[1].get("alerts")
              and not panels[2].get("alerts"))
        line = run.command_for(dict(panels[0], url="http://x/"))
        check("and their sender keeps a page ready and listens",
              "--alerts" in line and "--control" in line, repr(line))
        check("one that shows none does not",
              "--alerts" not in run.command_for(dict(panels[2],
                                                     url="http://x/")))
        check("what each alert will do is said at the start",
              any("binary_sensor.door shows camera.porch for 5s on cuisine; "
                  "a dark screen is lit for it" in line for line in said),
              repr(said))

        remotes = {name: run.Remote(name) for name in names}
        processes = {name: FakeProcess() for name in names}
        for name in names:
            remotes[name].set_process(processes[name])
        run._watchers.clear()
        run.start_alerts(panels, names, planned, remotes)
        check("it follows both sensors", ha.subscribed.wait(5))
        settle(lambda: len(ha.peers) and len(ha.peers[0].wanted) == 2)
        ha.set_state("binary_sensor.porch_person", "on")
        check("an alert line reaches the screen it is for",
              settle(lambda: processes["salon"].stdin.getvalue()), repr(said))
        sent = processes["salon"].stdin.getvalue()
        body = json.loads(sent.partition(" ")[2])
        check("and only that one",
              not processes["cuisine"].stdin.getvalue()
              and not processes["bureau"].stdin.getvalue())
        check("with the camera's live stream, from the dashboard's address",
              body["url"].startswith(f"{ha.base}/api/camera_proxy_stream/"
                                     f"camera.porch?token="), body["url"])
        check("its own access token, never the house's",
              f"token={ACCESS}" in body["url"] and TOKEN not in body["url"])
        check("named as Home Assistant names the camera",
              body["name"] == "Porche" and body["seconds"] == 5)
        check("and the token is in no line of the add-on's log",
              not any(ACCESS in line for line in said), repr(said))

        ha.states["binary_sensor.door"] = "off"
        ha.states["light.salon_backlight"] = "off"
        ha.set_state("binary_sensor.door", "on")
        check("a dark screen is lit for an alert that asks",
              settle(lambda: ("turn_on", "light.salon_backlight")
                     in ha.posted, 5), repr(ha.posted))
        check("found by the screen's address, the way the Wi-Fi icon is",
              any("lighting the screen (light.salon_backlight)" in line
                  for line in said), repr(said[-4:]))
        watcher = run._watchers.get("cuisine")
        check("its sender's log is watched", watcher is not None)
        if watcher is not None:
            watcher(alerts.TOUCHED + " (a touch)\n")
            watcher(alerts.UNTOUCHED + "\n")
            time.sleep(0.5)
            check("touched, it is left lit: somebody is using it",
                  ("turn_off", "light.salon_backlight") not in ha.posted,
                  repr(ha.posted))
            ha.states["light.salon_backlight"] = "off"
            for watch in _WATCHERS:
                watch.fired_at.clear()
            ha.set_state("binary_sensor.door", "off")
            ha.set_state("binary_sensor.door", "on")
            settle(lambda: ha.posted.count(("turn_on",
                                            "light.salon_backlight")) == 2, 5)
            watcher(alerts.UNTOUCHED + "\n")
            check("untouched, the screen it lit goes back in the dark",
                  settle(lambda: ("turn_off", "light.salon_backlight")
                         in ha.posted, 5), repr(ha.posted))
            ha.states["light.salon_backlight"] = "on"
            for watch in _WATCHERS:
                watch.fired_at.clear()
            ha.set_state("binary_sensor.door", "off")
            ha.set_state("binary_sensor.door", "on")
            time.sleep(1.0)
            watcher(alerts.UNTOUCHED + "\n")
            time.sleep(0.5)
            check("and one that was already lit is left lit",
                  ha.posted.count(("turn_off", "light.salon_backlight")) == 1
                  and ha.posted.count(("turn_on",
                                       "light.salon_backlight")) == 2,
                  repr(ha.posted))
        check("a screen that shows none of them is not watched",
              "salon" not in run._watchers and "bureau" not in run._watchers)
    finally:
        run.say = real_say
        ha.close()


# --- 4. the sender itself ----------------------------------------------------

PAGE = (40, 90, 160)


def sender():
    print("The sender, its page, a fake panel and the stand-in:")
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
                body = ("<!doctype html><body style='margin:0;height:100vh;"
                        "background:rgb%s' onclick=\"fetch('/clicked')\">"
                        % (PAGE,)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    pages = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=pages.serve_forever, daemon=True).start()

    width, height = 320, 240
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
        except (OSError, struct.error):
            pass
    threading.Thread(target=accept, daemon=True).start()

    def centre():
        with lock:
            return canvas.getpixel((width // 2, height // 2))

    def near(colour, want, slack=40):
        return all(abs(a - b) <= slack for a, b in zip(colour, want))

    def camera():
        return near(centre(), FakeHA.CAMERA)

    def page():
        return near(centre(), PAGE)

    def corner():
        # Inside the strip a link carries along the top, at its right end.
        with lock:
            return canvas.getpixel((width - 10, 30))

    def strip():
        return near(corner(), (24, 27, 34), 12)

    ha = FakeHA()
    # The camera as a household has it: Home Assistant on the house's
    # network, which Chromium calls a PRIVATE address. Played here by telling
    # it so for the stand-in's port -- loopback would be allowed where the
    # real address is refused, and that refusal is the fault this caught.
    command = [sys.executable, "-u", SENDER, "--host", "127.0.0.1",
               "--port", str(listener.getsockname()[1]),
               "--url", f"http://127.0.0.1:{pages.server_address[1]}/",
               "--no-token", "--not-home-assistant", "--width", str(width),
               "--height", str(height), "--audio", "off", "--keyboard", "off",
               "--control", "--alerts",
               "--browser-arg=--ip-address-space-overrides="
               f"127.0.0.1:{ha.port}=private"]
    if os.environ.get("CHROMIUM"):
        command += ["--browser", os.environ["CHROMIUM"]]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    said = []
    real_say = run.say
    run.say = said.append

    def read():
        for line in process.stdout:
            out.append(line)
            watcher = run._watchers.get("salon")
            if watcher is not None:
                watcher(line)
    threading.Thread(target=read, daemon=True).start()

    def logged(text, since=0):
        return any(text in line for line in out[since:])

    def tap(x, y, hold=0.08):
        panel[0].sendall(b"T\x01\x00" + int(x).to_bytes(2, "little")
                         + int(y).to_bytes(2, "little"))
        time.sleep(hold)
        panel[0].sendall(b"T\x00")

    try:
        check("the sender starts", settle(lambda: logged("Ready"), 40),
              "".join(out[-5:]))
        check("its page reaches the panel", settle(page, 10), repr(centre()))
        # Inside a link, where the screen carries its strip along the top:
        # where somebody is when the doorbell rings, and the one place the
        # strip is pasted onto a picture.
        process.stdin.write(f"open http://127.0.0.1:"
                            f"{pages.server_address[1]}/inside\n")
        process.stdin.flush()
        check("a link opens, with its strip",
              settle(lambda: page() and strip(), 10),
              repr((centre(), corner())))

        remote = run.Remote("salon")
        remote.set_process(process)
        run._config = {"links": [{"name": "Home Assistant",
                                  "url": f"{ha.base}/lovelace/0",
                                  "token": TOKEN}],
                       "alerts": [dict(ALERT, wake=True)]}
        panels = [{"name": "salon", "host": "127.0.0.1", "url": "launcher"}]
        run._watchers.clear()
        planned = run.plan_alerts(panels, ["salon"])
        run.start_alerts(panels, ["salon"], planned, {"salon": remote})
        ha.subscribed.wait(5)

        # Somebody on the porch.
        ha.set_state("binary_sensor.porch_person", "on")
        check("the camera reaches the glass", settle(camera, 10),
              repr(centre()))
        check("over the strip as well: the whole screen is the camera's",
              not strip() and corner()[0] > 100, repr(corner()))
        check("read live from Home Assistant, with its access token",
              ha.streams == 1 and any(f"token={ACCESS}" in p
                                      for p in ha.stream_paths),
              repr(ha.stream_paths))
        check("and the sender says so",
              logged("Alert: Porche on the screen for"), "".join(out[-5:]))
        check("after its time the page is back by itself, strip and all",
              settle(lambda: page() and strip(), 12),
              repr((centre(), corner())))
        check("said as an alert that ran out with nobody touching it",
              logged(alerts.UNTOUCHED))
        check("a screen that was already lit is left lit",
              not any(what == "turn_off" for what, _ in ha.posted),
              repr(ha.posted))
        check("and the camera's stream is closed with it",
              settle(lambda: ha.streams == 0, 5), repr(ha.streams))

        # A touch closes it, and presses nothing.
        process.stdin.write("alert " + json.dumps({
            "url": f"{ha.base}/api/camera_proxy_stream/camera.porch?"
                   f"token={ACCESS}", "name": "Porche", "seconds": 60})
            + "\n")
        process.stdin.flush()
        check("a second alert brings the camera back", settle(camera, 10),
              repr((centre(), ha.streams, len(ha.stream_paths))))
        mark = len(out)
        before = len(clicks)
        tap(width // 2, height // 2)
        check("a touch takes the screen back to the page", settle(page, 5),
              repr(centre()))
        check("said as closed from the screen",
              logged(alerts.TOUCHED + " (a touch)", mark), "".join(out[mark:]))
        time.sleep(1.0)
        check("and the page under it was not pressed", len(clicks) == before,
              repr(clicks))
        tap(width // 2, height // 2)
        check("the next touch presses it as usual",
              settle(lambda: len(clicks) > before, 5), repr(clicks))

        # A dark screen, lit for the alert, and put back in the dark. The
        # board's answer to its light -- portall.wake / portall.sleep in its
        # YAML, arriving as 'S' -- comes after Home Assistant has reached it
        # over the network: half a second here, and then at once, because
        # which of the alert and the wake reaches the sender first must not
        # matter.
        for delay, how in ((0.5, "lit a moment later"),
                           (0.0, "lit before the alert reaches the sender")):
            def light(on, delay=delay):
                threading.Timer(delay, panel[0].sendall,
                                (b"S\x01" if on else b"S\x00",)).start()
            ha.on_light = light
            ha.states["light.salon_backlight"] = "off"
            asleep = len(out)
            panel[0].sendall(b"S\x00")
            settle(lambda: logged("Panel asleep", asleep), 5)
            mark = len(out)
            posted = len(ha.posted)
            ha.set_state("binary_sensor.porch_person", "off")
            # The add-on does not show an alert twice inside two minutes,
            # which is checked above; here its clock is put back by hand.
            for watch in _WATCHERS:
                watch.fired_at.clear()
            ha.set_state("binary_sensor.porch_person", "on")
            if delay:
                check("the alert waits on a dark screen",
                      settle(lambda: logged("the screen is dark; shown if it "
                                            "lights up", mark), 5),
                      "".join(out[mark:]))
            check(f"the add-on lights it ({how})",
                  settle(lambda: ("turn_on", "light.salon_backlight")
                         in ha.posted[posted:], 5), repr(ha.posted))
            check("and the camera comes up on it", settle(camera, 10),
                  repr(centre()))
            check("after its time, nobody having touched it, it says so",
                  settle(lambda: logged(alerts.UNTOUCHED, mark), 12),
                  "".join(out[mark:]))
            check("and the add-on puts the screen back in the dark",
                  settle(lambda: ("turn_off", "light.salon_backlight")
                         in ha.posted[posted:], 5), repr(ha.posted))
            settle(lambda: logged("Panel asleep", mark), 5)

        # Dark, and nobody lights it.
        ha.on_light = None
        mark = len(out)
        process.stdin.write("alert " + json.dumps({
            "url": f"{ha.base}/api/camera_proxy_stream/camera.porch?"
                   f"token={ACCESS}", "name": "Porche", "seconds": 5})
            + "\n")
        process.stdin.flush()
        settle(lambda: logged("the screen is dark", mark), 5)
        time.sleep(6)
        panel[0].sendall(b"S\x01")
        check("an alert whose time passed while the screen stayed dark is "
              "not shown when it lights",
              settle(lambda: logged("not shown, the screen stayed dark",
                                    mark), 5) and settle(page, 5),
              "".join(out[mark:]))

        process.stdin.write("alert {not json\n")
        process.stdin.flush()
        check("a line that is not an alert is said and ignored",
              settle(lambda: logged("an alert that is not one"), 5))
        check("the camera's token is in no line of the sender's log",
              not any(ACCESS in line for line in out))
        check("nor of the add-on's", not any(ACCESS in line for line in said))
    finally:
        run.say = real_say
        process.kill()
        pages.shutdown()
        listener.close()
        ha.close()


# Every Alerts the add-on starts, kept, so a check can move its clock.
_WATCHERS = []


def main():
    real = alerts.Alerts.start

    def keep(self):
        _WATCHERS.append(self)
        return real(self)
    alerts.Alerts.start = keep
    os.environ.pop("SUPERVISOR_TOKEN", None)
    units()
    websocket()
    routing()
    sender()
    bad = results.count(False)
    print("all good" if not bad else f"{bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
