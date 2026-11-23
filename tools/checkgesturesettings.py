#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""The way home, chosen in the launcher's Reglages dialog.

WHY THIS EXISTS. Asked as "est-ce le meilleur endroit ... de mettre dans
reglage", once a screen could go home by any of four edges. The choice is the
SCREEN's -- it is its Advanced home_gesture in the add-on's form -- and the
dialog is the launcher's, so this checks the join between the two, which is
where this project's faults of that shape have always lived:

  - the dialog shows the screen's own choice when it opens;
  - Apply writes it into every screen the launcher is shown on, in their
    Advanced, and nowhere in the launcher's own options;
  - each of those screens' senders is told `gesture <value>` at once, on its
    control channel, and the screen's settings carry it into its next start;
  - another field saved alone tells no sender anything;
  - a value that is not a way home is refused and nothing is written;
  - Reset takes the choice away (the corner again) and says so to the senders;
  - a screen with a launcher of its own changes only itself;
  - and in a real browser, the Navigation tab offers the six ways and Apply
    saves the one pressed.

Run against a stand-in Supervisor, the real run.py, appearance_store.py and
launcher.py, and a fake sender that only records what it is sent. Needs
Playwright for the last part (skipped without it); takes $CHROMIUM.
"""
import copy
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import appearance  # noqa: E402
import run as runner  # noqa: E402

fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class Supervisor(http.server.BaseHTTPRequestHandler):
    options = {}
    writes = 0
    lock = threading.RLock()

    def log_message(self, *args):
        pass

    def do_GET(self):
        with self.lock:
            data = json.dumps({"result": "ok",
                               "data": {"options": self.options}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            type(self).options = copy.deepcopy(data["options"])
            type(self).writes += 1
        self.send_response(200)
        self.send_header("Content-Length", "15")
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')


class Lines:
    """A sender's stdin that only remembers what it was given."""

    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass


class FakeSender:
    def __init__(self):
        self.stdin = Lines()


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if payload is None else {"Content-Type": "application/json",
                                          "Origin": url.rstrip("/")}
    req = urllib.request.Request(url.rstrip("/") + appearance.PATH,
                                 data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as answer:
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as answer:
        return answer.code, {}


def advanced(name):
    for entry in Supervisor.options["panels"]:
        if entry["name"] == name:
            return entry.get("advanced") or {}
    return {}


def boot(options):
    """Start the add-on's launchers the way main() does, with fake senders."""
    Supervisor.options = copy.deepcopy(options)
    # Read the way the add-on reads them: run.load_panels(), from a file.
    stored = Path(os.environ["PORTALL_CHECK_DIR"]) / "options.json"
    stored.write_text(json.dumps(options))
    os.environ["UDISP_CONFIG"] = str(stored)
    panels = runner.load_panels()
    config = runner._config
    runner._SCREENS.clear()
    runner._SCREENS.update({runner.screen_key(p).lower(): p for p in panels})
    senders = {}
    for panel in panels:
        key = runner.screen_key(panel)
        remote = runner.Remote(key)
        senders[key] = FakeSender()
        remote.set_process(senders[key])
        runner._SCREEN_REMOTES[key.lower()] = remote
    where, own = runner.start_launchers(config, panels)
    runner.route_to_launcher(panels, where, own)
    return panels, senders, where, own


def told(sender):
    return [line for line in sender.stdin.lines if line.startswith("gesture")]


def house(options):
    panels, senders, where, _ = boot(options)
    status, look = request(where)
    check("the dialog opens on the screen's own way home",
          status == 200 and look.get("home_gesture") == "right_edge", str(look))
    status, look = request(where, {"columns": 3})
    check("another field saved alone tells no sender anything",
          status == 200 and not any(told(s) for s in senders.values()),
          str({k: told(s) for k, s in senders.items()}))
    status, look = request(where, {"home_gesture": "bottom_edge"})
    check("Apply is accepted", status == 200 and
          look.get("home_gesture") == "bottom_edge", str((status, look)))
    check("it is stored in every screen this launcher is shown on",
          advanced("Salon").get("home_gesture") == "bottom_edge"
          and advanced("Cuisine").get("home_gesture") == "bottom_edge",
          str([e.get("advanced") for e in Supervisor.options["panels"]]))
    check("and nowhere in the launcher's own options",
          "home_gesture" not in json.dumps(Supervisor.options.get("launcher")))
    check("each sender is told at once",
          told(senders["Salon"]) == ["gesture bottom_edge\n"]
          and told(senders["Cuisine"]) == ["gesture bottom_edge\n"],
          str({k: told(s) for k, s in senders.items()}))
    salon = next(p for p in panels if p["name"] == "Salon")
    line = runner.command_for(salon)
    at = line.index("--home-gesture") if "--home-gesture" in line else -1
    check("and starts with it next time",
          at >= 0 and line[at + 1] == "bottom_edge")
    before = copy.deepcopy(Supervisor.options)
    status, _ = request(where, {"home_gesture": "sideways"})
    check("a value that is not a way home is refused, nothing written",
          status >= 400 and Supervisor.options == before, str(status))
    status, look = request(where, {"reset": True})
    check("Reset takes the choice away",
          status == 200 and look.get("home_gesture") == "corner"
          and "home_gesture" not in advanced("Salon")
          and "home_gesture" not in advanced("Cuisine"), str(look))
    check("and tells the senders the corner",
          told(senders["Salon"])[-1:] == ["gesture corner\n"], str(told(senders["Salon"])))
    return where


def own_launcher(options):
    options = copy.deepcopy(options)
    options["launchers"] = [{"panel": "Salon", "links": [
        {"name": "Mine", "url": "https://mine.test/"}]}]
    panels, senders, where, own = boot(options)
    salon_at = own["salon"][0]
    status, look = request(salon_at, {"home_gesture": "left_edge"})
    check("a screen's own launcher changes only that screen",
          status == 200 and advanced("Salon").get("home_gesture") == "left_edge"
          and "home_gesture" not in advanced("Cuisine")
          and told(senders["Salon"]) == ["gesture left_edge\n"]
          and not told(senders["Cuisine"]),
          str(([e.get("advanced") for e in Supervisor.options["panels"]],
               {k: told(s) for k, s in senders.items()})))


def in_a_browser(where):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  skipped: the dialog in a browser needs Playwright")
        return
    request(where, {"home_gesture": "right_edge"})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None)
        page = browser.new_page(viewport={"width": 800, "height": 1280},
                                locale="fr-FR")
        page.goto(where)
        page.click("#appearance-open")
        page.wait_for_selector("#appearance-dialog[open]")
        check("the dialog has a Navigation tab",
              page.locator("#appearance-tab-navigation").count() == 1)
        page.click("#appearance-tab-navigation")
        control = page.locator("#appearance-home_gesture")
        if control.get_attribute("role") == "combobox":
            control.click()
            choices = page.locator("#appearance-home_gesture-options [data-value]")
        else:
            choices = page.locator("#appearance-home_gesture button[data-value]")
        values = choices.evaluate_all("e => e.map(x => x.dataset.value)")
        check("offering the six ways home",
              sorted(values) == sorted(appearance.VALS["home_gesture"]),
              str(values))
        choices.filter(has_text="depuis le bas").first.click()
        page.click("#appearance-apply")
        page.wait_for_timeout(1500)
        check("and Apply saves the one pressed",
              advanced("Salon").get("home_gesture") == "bottom_edge",
              str(advanced("Salon")))
        browser.close()


def main():
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Supervisor)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    os.environ["SUPERVISOR_TOKEN"] = "fixture-only"
    with tempfile.TemporaryDirectory(prefix="portall-gesture-") as folder:
        temp = Path(folder)
        os.environ["PORTALL_CHECK_DIR"] = folder
        runner.AVATAR_DIR = str(temp / "avatar")
        runner.WALLPAPER_DIR = str(temp / "wallpaper")
        runner.DOWNLOADS = str(temp / "downloads")
        runner.launcher.PORT = 0
        runner.SUPERVISOR_API = f"http://127.0.0.1:{service.server_port}"
        runner.follow_voice = lambda *args, **kwargs: None

        class NoWeather:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                return None
        runner.Weather = NoWeather
        options = {
            "links": [{"name": "Exemple", "url": "https://example.test/"}],
            "launcher": {"layout": "classic", "theme": "dark", "columns": 2},
            "panels": [
                {"name": "Salon", "host": "10.0.0.2", "url": "launcher",
                 "advanced": {"home_gesture": "right_edge"}},
                {"name": "Cuisine", "host": "10.0.0.3", "url": "launcher",
                 "advanced": {}},
            ],
            "launchers": [], "defaults": {"locale": "fr-FR"},
        }
        print("The house's launcher, on two screens")
        where = house(options)
        print("A screen with a launcher of its own")
        own_launcher(options)
        print("The dialog in a browser")
        _, _, where, _ = boot(options)
        in_a_browser(where)
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
