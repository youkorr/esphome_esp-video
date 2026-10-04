#!/usr/bin/env python3
"""The screen's Wi-Fi and Bluetooth on its launcher, while they are connected.

WHY THIS EXISTS. Asked as "afficher dans la page d'accueil launcher l'icone du
wifi et du bluetooth quand ils sont connectes". A panel names its ESPHome
device (esphome_device), run.PanelStatus reads that device's entities from
Home Assistant, and the launcher shows two icons. Checked here:

  - the reading itself (PanelStatus.judge): on the network while any entity
    has a state, off when every one is "unavailable"; bars from a dBm sensor;
    Bluetooth from a portall_bt text sensor reading "... connected", and not
    from "paired, away", "none" or "disconnected"; another device's entities
    never counted;
  - the whole way: a stand-in Home Assistant that checks the bearer token,
    the real PanelStatus polling it, the real launcher served with it, a real
    browser on the panel's own address -- the icons appear, change their bars
    and go away as the stand-in's states change;
  - the screen found with NOTHING set, by its address: a panel's host is
    the host Home Assistant's ESPHome integration connects to (read off the
    config entry's diagnostics), its entities asked of the template API --
    so an entity renamed away from the device's prefix still counts. 4.37.0
    needed esphome_device, which the form never shows, and looked for no
    screen that had not set it -- so it showed nothing;
  - the icons sit in a row of their own at the top right, above the clock,
    and small;
  - run.route_to_launcher gives every launcher panel ?panel=<name> while the
    icons are on, and none when they are off.

Needs Playwright. Takes $CHROMIUM for the browser.
"""
import copy
import http.server
import json
import os
import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
BROWSER = os.environ.get("CHROMIUM", "")
TOKEN = "stand-in"
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def entity(eid, state, **attributes):
    return {"entity_id": eid, "state": state, "attributes": attributes}


HOUSE = [
    entity("sensor.salon_p4_bluetooth_speaker",
           "UGREEN-90748 (46:E8:1C:8A:88:DD) connected"),
    entity("switch.salon_p4_bluetooth", "on"),
    entity("sensor.salon_p4_wifi_signal", "-70", unit_of_measurement="dBm",
           device_class="signal_strength"),
    entity("sensor.cuisine_p4_bluetooth_speaker", "none"),
    entity("weather.home", "sunny"),
]


def main():
    import run
    P = run.PanelStatus

    print("The name, as Home Assistant spells it in an entity id:")
    for given, want in (("ha-guit-10-p4", "ha_guit_10_p4"),
                        ("HA-GUIT-10-P4", "ha_guit_10_p4"),
                        ("Écran salon", "ecran_salon")):
        check(f"{given} -> {want}", P.slug(given) == want, P.slug(given))
    print("The reading:")
    got = P.judge(HOUSE, "salon_p4")
    check("on the network, two bars at -70 dBm, Bluetooth connected",
          got == {"wifi": {"on": True, "bars": 2}, "bluetooth": True}, str(got))
    off = [dict(e, state="unavailable") for e in HOUSE]
    got = P.judge(off, "salon_p4")
    check("every entity unavailable: off the network, and no Bluetooth",
          got["wifi"]["on"] is False and got["bluetooth"] is False, str(got))
    for words in ("UGREEN (46:E8:1C:8A:88:DD) paired, away", "none",
                  "disconnected", "46:E8:1C:8A:88:DD (Bluetooth off)"):
        house = copy.deepcopy(HOUSE)
        house[0]["state"] = words
        check(f"no Bluetooth for \"{words}\"",
              P.judge(house, "salon_p4")["bluetooth"] is False)
    got = P.judge(HOUSE, "cuisine_p4")
    check("another device: its own state, not the salon's",
          got == {"wifi": {"on": True, "bars": None}, "bluetooth": False},
          str(got))
    check("a device with no entity at all is unknown",
          P.judge(HOUSE, "grenier") is None)
    for rssi, bars in (("-50", 4), ("-60", 3), ("-72", 2), ("-85", 1)):
        house = copy.deepcopy(HOUSE)
        house[2]["state"] = rssi
        check(f"{rssi} dBm is {bars} bar(s)",
              P.judge(house, "salon_p4")["wifi"]["bars"] == bars)

    print("Matching a screen to its ESPHome device:")
    known = [{"id": "e1", "title": "HA-GUIT-10-P4", "host": "192.168.1.50",
              "name": "ha-guit-10-p4"},
             {"id": "e2", "title": "Cuisine", "host": "cuisine-p4.local",
              "name": "cuisine-p4"},
             {"id": "e3", "title": "Not loaded", "host": "", "name": ""}]
    for want, got in (
            ({"host": "192.168.1.50"}, "e1"),
            ({"host": "cuisine-p4.local"}, "e2"),
            ({"host": "cuisine-p4"}, "e2"),
            ({"device": "HA-GUIT-10-P4", "host": "10.0.0.9"}, "e1"),
            ({"device": "Not loaded", "host": ""}, "e3"),
            ({"host": "192.168.1.77"}, None)):
        found = P.match(want, known)
        check(f"{want} -> {got}", (found or {}).get("id") == got,
              str(found))

    print("The whole way, in a browser:")
    states = {"now": copy.deepcopy(HOUSE)
              + [entity("sensor.renamed_by_hand", "-60",
                        unit_of_measurement="dBm")]}
    # Which entity belongs to which config entry, the way Home Assistant's
    # entity registry knows it: e1 is the salon's board, including the
    # sensor somebody renamed away from the device's prefix.
    owner = {"e1": {"sensor.salon_p4_bluetooth_speaker",
                    "switch.salon_p4_bluetooth", "sensor.salon_p4_wifi_signal",
                    "sensor.renamed_by_hand"},
             "e2": {"sensor.cuisine_p4_bluetooth_speaker"}}
    entries = [{"entry_id": "e1", "title": "Salon P4", "domain": "esphome"},
               {"entry_id": "e2", "title": "Cuisine", "domain": "esphome"},
               {"entry_id": "e9", "title": "Broken", "domain": "esphome"}]
    hosts = {"e1": ("192.168.1.50", "salon-p4"),
             "e2": ("192.168.1.51", "cuisine-p4")}

    class HA(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, kind="application/json", code=200):
            body = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._send(b"", code=401)
                return False
            return True

        def do_GET(self):
            if not self._allowed():
                return
            if self.path == "/api/states":
                self._send(json.dumps(states["now"]))
            elif self.path == "/api/config/config_entries/entry?domain=esphome":
                self._send(json.dumps(entries))
            elif self.path.startswith("/api/diagnostics/config_entry/"):
                ident = self.path.rsplit("/", 1)[1]
                if ident not in hosts:
                    self._send(b"", code=500)
                    return
                host, name = hosts[ident]
                self._send(json.dumps({"home_assistant": {}, "data": {
                    "config": {"data": {"host": host, "port": 6053,
                                        "device_name": name}}}}))
            else:
                self._send(b"", code=404)

        def do_POST(self):
            if not self._allowed():
                return
            body = json.loads(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)))
            if self.path != "/api/template":
                self._send(b"", code=404)
                return
            import re as _re
            ident = _re.search(r"== '([^']+)'", body["template"]).group(1)
            self._send("\n".join(sorted(owner.get(ident, ()))) + "\n",
                       "text/plain")

    ha = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()
    P.EVERY_S = 0.5
    url = f"http://127.0.0.1:{ha.server_address[1]}"
    # Nothing set but the screen's address, as on every panel there is.
    reading = P([{"name": "salon", "url": "launcher", "host": "192.168.1.50"},
                 {"name": "grenier", "url": "launcher",
                  "host": "192.168.1.99"},
                 {"name": "tele", "url": "http://x.invalid/",
                  "host": "192.168.1.51"}], url, TOKEN).start()
    end = time.monotonic() + 10
    while time.monotonic() < end and not (reading and reading("salon")):
        time.sleep(0.1)
    check("found by its address alone, and read", callable(reading)
          and (reading("salon") or {}).get("bluetooth") is True,
          str(reading and reading("salon")))
    check("a sensor renamed away from the device's prefix still counts",
          (reading("salon") or {}).get("wifi", {}).get("bars") == 3,
          str(reading("salon")))
    check("a screen no device has the address of shows nothing",
          reading("grenier") is None)
    check("a panel not on the launcher is not looked for",
          reading("tele") is None)
    states["now"] = copy.deepcopy(HOUSE)

    import launcher
    where = launcher.start([{"name": "x", "url": "https://x.invalid/"}],
                           port=launcher.ANY_PORT, clock=True, status=reading)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=BROWSER or None)
        page = browser.new_page(viewport={"width": 1024, "height": 600})
        page.goto(where + "?panel=salon")
        page.wait_for_timeout(600)

        def shown():
            return page.evaluate(
                "() => [!document.getElementById('net').hidden, "
                "document.getElementById('net').getAttribute('data-bars'), "
                "!document.getElementById('bt').hidden]")

        page.wait_for_timeout(600)
        check("both icons are shown, the Wi-Fi with two bars",
              shown() == [True, "2", True], str(shown()))
        place = page.evaluate(
            "() => { const n = document.getElementById('bt')"
            ".getBoundingClientRect(), t = document.getElementById('t')"
            ".getBoundingClientRect(); return [n.right, n.top, n.bottom, "
            "t.top, n.height]; }")
        check("at the top right, in a row above the clock",
              place[0] > 1024 * 0.88 and place[1] < 600 * 0.12
              and place[2] <= place[3] + 1, str(place))
        check("and small: no taller than a line of the date",
              place[4] <= 24, str(place))
        box = page.evaluate(
            "() => { const r = document.getElementById('bt')"
            ".getBoundingClientRect(); return [r.width, r.height]; }")
        check("and drawn at a size that can be seen", box[0] >= 12
              and box[1] >= 12, str(box))
        if "--picture" in sys.argv:
            page.screenshot(path=sys.argv[sys.argv.index("--picture") + 1],
                            clip={"x": 0, "y": 0, "width": 1024, "height": 140})
        states["now"][0]["state"] = "UGREEN (46:E8:1C:8A:88:DD) paired, away"
        states["now"][2]["state"] = "-50"
        page.wait_for_timeout(11500)
        check("Bluetooth disconnected: its icon goes, the Wi-Fi gains bars",
              shown() == [True, "4", False], str(shown()))
        states["now"] = [dict(e, state="unavailable") for e in HOUSE]
        page.wait_for_timeout(11500)
        check("the screen off the network: no icon at all",
              shown()[0] is False and shown()[2] is False, str(shown()))
        page.goto(where)
        page.wait_for_timeout(600)
        check("a page that names no panel shows nothing",
              shown()[0] is False and shown()[2] is False, str(shown()))
        browser.close()
    plain = launcher.start([{"name": "x", "url": "https://x.invalid/"}],
                           port=launcher.ANY_PORT, clock=True)
    import urllib.request
    check("a launcher with no device named carries no icons at all",
          'id="net"' not in urllib.request.urlopen(plain).read().decode())

    print("The address each panel is given:")
    run._config = {"links": [{"name": "x", "url": "https://x.invalid/"}]}
    run.STATUS = reading
    panels = [{"name": "salon", "url": "launcher"},
              {"name": "", "host": "192.168.1.60", "url": "launcher"}]
    run.route_to_launcher(panels, "http://127.0.0.1:8099/")
    check("every launcher panel is told its own name while the icons are on",
          panels[0]["url"] == "http://127.0.0.1:8099/?panel=salon",
          panels[0]["url"])
    check("one with no name by its host, the key the reading uses",
          panels[1]["url"] == "http://127.0.0.1:8099/?panel=192.168.1.60",
          panels[1]["url"])
    run.STATUS = None
    panels = [{"name": "salon", "url": "launcher"}]
    run.route_to_launcher(panels, "http://127.0.0.1:8099/")
    check("with the icons off the address is what it always was",
          panels[0]["url"] == "http://127.0.0.1:8099/", panels[0]["url"])
    ha.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
