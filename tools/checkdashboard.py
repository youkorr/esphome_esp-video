#!/usr/bin/env python3
"""The dashboard's nine apps fit the panel, and its local settings work.

Serves the real launcher at three landscape panel sizes and in portrait,
then drives the appearance dialog and checks its same-origin JSON endpoint.
The preference file lives in a temporary directory; no installed settings
are read or changed. Needs Playwright and Chromium:

    python3 tools/checkdashboard.py --browser /usr/bin/chromium

CHROMIUM may also select the browser. Servers live only for this process.
"""

import argparse
import json
import os
import pathlib
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portall"))

import appearance  # noqa: E402
import launcher  # noqa: E402
from playwright.sync_api import TimeoutError as BrowserTimeoutError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


LINKS = [
    {"name": name, "url": f"http://example.invalid/{index}", "icon": icon}
    for index, (name, icon) in enumerate([
        ("Home Assistant", "home-assistant"), ("Jellyfin", "jellyfin"),
        ("YouTube", "youtube"), ("Netflix", "netflix"),
        ("Orange TV", "orange-tv"), ("Google", "google"),
        ("Fichiers", "fichiers"), ("Photos", "photos"),
        ("AList", "alist"),
    ])
]
DEFAULTS = {"theme": "dark", "columns": 3}
WEATHER = {"condition": "sunny", "temperature": 21, "unit": "°C",
           "text": "21°C"}

# A range over an entire word has a line box for each line it occupies.
# Looking at the text itself catches "Jellyfi|n" even when the tile fits.
CUT_WORDS = r"""() => {
  const cut = [];
  for (const el of document.querySelectorAll('.name, .desc')) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      for (const match of node.textContent.matchAll(/\S+/g)) {
        const range = document.createRange();
        range.setStart(node, match.index);
        range.setEnd(node, match.index + match[0].length);
        const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
        if (lines.size > 1) cut.push(match[0]);
      }
    }
  }
  return cut;
}"""

GEOMETRY = """() => {
  const root = document.documentElement;
  const bounds = e => {
    const r = e.getBoundingClientRect();
    return {left: r.left, top: r.top, right: r.right, bottom: r.bottom,
            width: r.width, height: r.height};
  };
  const rows = {};
  const tiles = [...document.querySelectorAll('a.tile')].map(tile => {
    const box = bounds(tile);
    const name = tile.querySelector('.name').textContent;
    const inside = tile.querySelector('.in');
    const row = Math.round(box.top);
    rows[row] = (rows[row] || 0) + 1;
    return {...box, name, spill: inside.scrollHeight > tile.clientHeight + 1 ||
                               inside.scrollWidth > tile.clientWidth + 1};
  });
  const header = document.querySelector('header');
  const settings = document.getElementById('appearance-open');
  return {
    width: root.clientWidth, height: root.clientHeight,
    scrollWidth: Math.max(root.scrollWidth, document.body.scrollWidth),
    scrollHeight: Math.max(root.scrollHeight, document.body.scrollHeight),
    tiles, across: Math.max(0, ...Object.values(rows)),
    header: header && bounds(header), settings: settings && bounds(settings),
    avatar: document.getElementById('av') && bounds(document.getElementById('av')),
    scheme: getComputedStyle(root).colorScheme,
  };
}"""


class Checks:
    def __init__(self):
        self.failures = []

    def check(self, label, passed, detail=""):
        print(("  ok     " if passed else "  FAILED ") + label
              + (f" ({detail})" if not passed and detail else ""), flush=True)
        if not passed:
            self.failures.append(label)


def start(preferences=None, layout="dashboard"):
    address = launcher.start(
        LINKS, port=0, layout=layout, theme="dark", columns=3,
        avatar=True, clock=True, weather=lambda: dict(WEATHER),
        appearance_file=preferences)
    if not address:
        raise RuntimeError("launcher did not start")
    return address


def request(address, payload=None, *, raw=None, origin=None,
            content_type="application/json"):
    url = urllib.parse.urljoin(address, appearance.PATH)
    headers = {}
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else None)
    if data is not None:
        headers["Content-Type"] = content_type
        if origin is not None:
            headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read().decode("utf-8", "replace")


def load(page, address):
    page.goto(address, wait_until="domcontentloaded")
    page.wait_for_selector("a.tile")
    page.evaluate("document.fonts.ready")


def panel_checks(browser, address, checks):
    for width, height in ((1280, 800), (1280, 720), (1024, 600), (800, 1280)):
        portrait = height > width
        label = f"{width}x{height}"
        page = browser.new_page(viewport={"width": width, "height": height},
                                locale="fr-FR")
        try:
            load(page, address)
            geometry = page.evaluate(GEOMETRY)
            tiles = geometry["tiles"]
            off = [tile["name"] for tile in tiles
                   if tile["left"] < -1 or tile["right"] > width + 1
                   or (not portrait and (tile["top"] < -1
                                         or tile["bottom"] > height + 1))]
            checks.check(f"{label}: all nine apps fit the panel",
                         len(tiles) == 9 and not off, str(off))
            checks.check(f"{label}: no horizontal page overflow",
                         geometry["scrollWidth"] <= geometry["width"] + 1,
                         str(geometry))
            if not portrait:
                checks.check(f"{label}: three columns and no vertical scrolling",
                             geometry["across"] == 3
                             and geometry["scrollHeight"] <= height + 1,
                             f"columns={geometry['across']}, height={geometry['scrollHeight']}")
            cut = page.evaluate(CUT_WORDS)
            spills = [tile["name"] for tile in tiles if tile["spill"]]
            checks.check(f"{label}: names stay whole and inside their tiles",
                         not cut and not spills, f"split={cut}, spill={spills}")
            checks.check(f"{label}: dark dashboard with one real assistant",
                         page.locator("body.dashboard").count() == 1
                         and geometry["scheme"] == "dark"
                         and page.locator("#av").count() == 1
                         and page.locator(".dashboard-assistant #av").count() == 1)
            sidebar = page.locator("header")
            checks.check(f"{label}: clock, date, weather and settings are visible",
                         all(sidebar.locator(selector).is_visible()
                             and sidebar.locator(selector).inner_text().strip()
                             for selector in ("#t", "#d", ".wx", "#appearance-open")))
            if not portrait:
                rail, button, avatar = (geometry[key] for key in
                                        ("header", "settings", "avatar"))
                checks.check(f"{label}: assistant in sidebar, settings at bottom left",
                             button is not None and avatar is not None
                             and button["left"] >= -1
                             and button["right"] <= rail["right"] + 1
                             and button["bottom"] <= height + 1
                             and button["bottom"] >= height - 60
                             and avatar["left"] >= rail["left"] - 1
                             and avatar["right"] <= rail["right"] + 1
                             and avatar["top"] >= -1
                             and avatar["bottom"] <= button["top"] + 1,
                             f"settings={button}, avatar={avatar}")
        finally:
            page.close()


def endpoint_checks(address, checks):
    origin = address.rstrip("/")
    checks.check("appearance GET returns the configured defaults",
                 request(address) == (200, DEFAULTS))
    changed = {"theme": "light", "columns": 2}
    checks.check("same-origin JSON POST saves theme and columns",
                 request(address, changed, origin=origin) == (200, changed)
                 and request(address) == (200, changed))
    invalid = [
        ({"theme": "dark", "unknown": "value"}, 400), ({"theme": "pink"}, 400),
        ({"columns": -1}, 400), ({"columns": 7}, 400),
        ({"columns": True}, 400), ({"columns": "3"}, 400),
        ({"columns": 1.5}, 400), (["dark", 3], 400),
    ]
    wrong = []
    for payload, expected in invalid:
        status, _ = request(address, payload, origin=origin)
        if status != expected or request(address) != (200, changed):
            wrong.append((payload, status))
    checks.check("unknown settings and invalid values cannot change saved preferences",
                 not wrong, str(wrong))
    rejected = [
        (dict(payload={"theme": "dark"}, origin="http://elsewhere.invalid"), 403),
        (dict(payload={"theme": "dark"}), 403),
        (dict(payload={"theme": "dark"}, origin=origin,
              content_type="text/plain"), 415),
        (dict(raw=b"{bad json", origin=origin), 400),
    ]
    wrong = []
    for arguments, expected in rejected:
        status, _ = request(address, **arguments)
        if status != expected or request(address) != (200, changed):
            wrong.append((arguments, status))
    checks.check("cross-origin, missing-origin and malformed posts leave preferences intact",
                 not wrong, str(wrong))
    checks.check("same-origin reset returns the add-on defaults",
                 request(address, {"reset": True}, origin=origin) == (200, DEFAULTS))


def dialog_checks(browser, address, preference_file, checks):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="fr-FR")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        load(page, address)
        button = page.locator("#appearance-open")
        button.focus()
        button.click()
        page.wait_for_function("document.getElementById('appearance-dialog').open")
        checks.check("settings opens a labelled modal and focuses the theme chooser",
                     page.locator("#appearance-dialog[aria-labelledby]").count() == 1
                     and page.evaluate("document.activeElement.id") == "appearance-theme"
                     and page.locator("#appearance-theme").input_value() == "dark"
                     and page.locator("#appearance-columns").input_value() == "3")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.getElementById('appearance-dialog').open")
        checks.check("Escape closes settings and restores focus to its button",
                     page.evaluate("document.activeElement.id") == "appearance-open")
        button.click()
        page.wait_for_function("document.getElementById('appearance-dialog').open")
        page.locator("#appearance-theme").select_option("light")
        page.locator("#appearance-columns").select_option("2")
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=5000):
                page.locator("#appearance-apply").click()
        except BrowserTimeoutError:
            checks.check("applying settings reloads the dashboard", False,
                         page.locator("#appearance-error").inner_text())
            return
        page.wait_for_selector("a.tile")
        geometry = page.evaluate(GEOMETRY)
        saved = {"theme": "light", "columns": 2}
        checks.check("applying settings reloads with the chosen theme and columns",
                     geometry["scheme"] == "light" and geometry["across"] == 2
                     and request(address) == (200, saved))
        checks.check("appearance choices are persisted in the launcher's own file",
                     json.loads(preference_file.read_text()) == saved)
        restarted = start(preference_file)
        checks.check("a restarted launcher restores the saved choices",
                     request(restarted) == (200, saved))
        page.goto(restarted, wait_until="domcontentloaded")
        page.locator("#appearance-open").click()
        page.wait_for_function("document.getElementById('appearance-dialog').open")
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=5000):
                page.locator("#appearance-reset").click()
        except BrowserTimeoutError:
            checks.check("dialog reset reloads the dashboard", False,
                         page.locator("#appearance-error").inner_text())
            return
        page.wait_for_selector("a.tile")
        geometry = page.evaluate(GEOMETRY)
        checks.check("dialog reset restores dark, three columns and clears overrides",
                     geometry["scheme"] == "dark" and geometry["across"] == 3
                     and request(restarted) == (200, DEFAULTS)
                     and json.loads(preference_file.read_text()) == {})
        checks.check("dashboard and appearance dialog run without JavaScript errors",
                     not errors, str(errors))
    finally:
        page.close()


def other_pages(browser, dashboard, checks):
    for label, address in (("classic", start(layout="classic")),
                           ("screen saver", urllib.parse.urljoin(dashboard, launcher.SAVER_PATH))):
        page = browser.new_page()
        try:
            page.goto(address, wait_until="domcontentloaded")
            checks.check(f"{label} has no dashboard settings or dashboard layout",
                         page.locator("#appearance-open, #appearance-dialog, body.dashboard").count() == 0)
        finally:
            page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default=os.environ.get("CHROMIUM") or None,
                        help="Chromium executable (or set CHROMIUM)")
    arguments = parser.parse_args()
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="portall-dashboard-") as directory:
        preference_file = pathlib.Path(directory, "appearance.json")
        address = start(preference_file)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=arguments.browser)
            try:
                panel_checks(browser, address, checks)
                endpoint_checks(address, checks)
                dialog_checks(browser, address, preference_file, checks)
                other_pages(browser, address, checks)
            finally:
                browser.close()
    print("all passed" if not checks.failures else f"{len(checks.failures)} failed")
    return bool(checks.failures)


if __name__ == "__main__":
    sys.exit(main())
