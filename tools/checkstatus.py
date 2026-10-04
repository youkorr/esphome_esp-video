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
  - run.route_to_launcher gives a panel naming its device ?panel=<name>, and
    one that does not keeps the address it always had.

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

    print("The whole way, in a browser:")
    states = {"now": copy.deepcopy(HOUSE)}

    class HA(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}" \
                    or self.path != "/api/states":
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps(states["now"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ha = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()
    P.EVERY_S = 0.5
    reading = P([{"name": "salon", "esphome_device": "salon-p4"}],
                f"http://127.0.0.1:{ha.server_address[1]}", TOKEN).start()
    check("started, and read", callable(reading)
          and reading("salon")["bluetooth"] is True)

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

        check("both icons are shown, the Wi-Fi with two bars",
              shown() == [True, "2", True], str(shown()))
        box = page.evaluate(
            "() => { const r = document.getElementById('bt')"
            ".getBoundingClientRect(); return [r.width, r.height]; }")
        check("and drawn at a size that can be seen", box[0] >= 14
              and box[1] >= 14, str(box))
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
    panels = [{"name": "salon", "url": "launcher", "esphome_device": "salon-p4"},
              {"name": "cuisine", "url": "launcher"}]
    run.route_to_launcher(panels, "http://127.0.0.1:8099/")
    check("a panel naming its device is told its own name",
          panels[0]["url"] == "http://127.0.0.1:8099/?panel=salon",
          panels[0]["url"])
    check("one that does not keeps the address it always had",
          panels[1]["url"] == "http://127.0.0.1:8099/", panels[1]["url"])
    ha.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
