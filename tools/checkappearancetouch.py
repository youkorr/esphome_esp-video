#!/usr/bin/env python3
"""Exercise local appearance settings through the panel's real touch injector.

Native select_option() can pass while a panel tap lands under Chromium's
native popup. This regression uses ha_send._launch, TouchMap, Injector and
Screencast, including the sender's on-screen keyboard for background text.
All servers choose ephemeral ports and preferences live in a temporary folder.

    python3 tools/checkappearancetouch.py --browser /usr/bin/chromium

Screenshots, screencast frames and observed DOM events are retained under /tmp.
"""

import argparse
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portall"))
sys.path.insert(0, str(ROOT / "components" / "portall"))

import appearance  # noqa: E402
import ha_send  # noqa: E402
import launcher  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from checkdashboard import Checks, DEFAULTS, GEOMETRY, load, request, start  # noqa: E402


EVENTS = """(() => {
  window.__appearanceTouchEvents = [];
  for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click', 'input', 'change']) {
    document.addEventListener(type, event => {
      const e = event.target;
      window.__appearanceTouchEvents.push({type, id: e.id, tag: e.tagName,
        text: (e.textContent || '').trim().slice(0, 90), value: e.value,
        x: event.clientX, y: event.clientY});
    }, true);
  }
})();"""


def palette(name):
    value = launcher.PALETTES[name].lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


class Wallpaper(http.server.BaseHTTPRequestHandler):
    """A real local image, with no dependency on an Internet host."""

    hits = 0

    def log_message(self, *_args):
        pass

    def do_GET(self):
        type(self).hits += 1
        body = (b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
                b'<rect width="64" height="64" fill="#2563eb"/>'
                b'<circle cx="32" cy="32" r="20" fill="#f59e0b"/></svg>')
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Panel:
    def __init__(self, playwright, executable, width, height, output):
        self.width, self.height = width, height
        self.output = output
        self.context = ha_send._launch(
            playwright, executable, None, {"width": width, "height": height},
            ha_send.BROWSER_ARGS, locale="fr-FR", touch=True)
        self.context.add_init_script(ha_send.KEYBOARD_INIT_JS)
        self.context.add_init_script(EVENTS)
        self.page = self.context.new_page()
        self.errors, self.posts = [], []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("request", lambda req: self.posts.append(req.post_data_json)
                     if req.method == "POST" and req.url.endswith(appearance.PATH) else None)
        self.keyboard = ha_send.Keyboard(self.page, width, height, "qwerty")
        self.injector = ha_send.Injector(
            self.page, ha_send.TouchMap(width, height, width, height, 0, False, False),
            self.keyboard, page_w=width, page_h=height)
        self.capture = None
        self.observations = []

    def close(self):
        if self.capture:
            self.capture.stop()
        (self.output / "observations.json").write_text(
            json.dumps({"stages": self.observations, "posts": self.posts,
                        "errors": self.errors}, ensure_ascii=False, indent=2))
        browser = self.context.browser
        self.context.close()
        if browser:
            browser.close()

    def goto(self, address):
        load(self.page, address)
        self.keyboard.forget()
        if self.capture is None:
            self.capture = ha_send.Screencast(self.page, self.width, self.height, 0)

    def snapshot(self, tag):
        frame = None
        for _ in range(5):
            self.page.wait_for_timeout(35)
            self.capture.request(eager=True)
            frame = self.capture.take() or frame
        if frame:
            (self.output / f"{tag}-screencast.png").write_bytes(frame)
        self.page.screenshot(path=str(self.output / f"{tag}.png"))
        self.observations.append({"stage": tag, "url": self.page.url,
                                  "events": self.page.evaluate(
                                      "window.__appearanceTouchEvents.splice(0)")})

    def tap_at(self, x, y):
        self.injector.handle([[(0, int(x), int(y))], []])
        self.page.wait_for_timeout(105)
        self.injector.release_press()
        self.page.wait_for_timeout(55)

    def scroll(self, delta, navigation=False):
        # This is also the sender's scrolling path: Injector._flush_wheel.
        selector = ".appearance-navigation" if navigation else "#appearance-panels"
        box = self.page.locator(selector).bounding_box()
        self.page.mouse.move(box["x"] + box["width"] / 2,
                             box["y"] + box["height"] / 2)
        self.page.mouse.wheel(0, delta)
        self.page.wait_for_timeout(85)

    def reveal(self, locator):
        """Bring a control into view by wheel, never scrollIntoView()."""
        section = locator.evaluate("e => e.closest('[role=tabpanel]')?.dataset.section")
        if section:
            tab = self.page.locator(f"#appearance-tab-{section}")
            if tab.get_attribute("aria-selected") != "true":
                self.tap(tab)
        navigation = locator.evaluate("e => !!e.closest('.appearance-navigation')")
        container = self.page.locator(".appearance-navigation" if navigation
                                     else "#appearance-panels" if section
                                     else "#appearance-dialog")
        previous = None
        for _ in range(65):
            box = locator.bounding_box()
            if not box:
                raise AssertionError(f"control is not rendered: {locator}")
            dialog = container.bounding_box()
            lower = min(self.height - 12, dialog["y"] + dialog["height"])
            upper = max(12, dialog["y"])
            hit = self.page.evaluate("""([x, y, e]) => {
              const h = document.elementFromPoint(x, y);
              return !!h && (h === e || e.contains(h));
            }""", [box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2, locator.element_handle()])
            if hit and box["y"] >= dialog["y"] and box["y"] + box["height"] <= dialog["y"] + dialog["height"]:
                return box
            center = box["y"] + box["height"] / 2
            delta = min(300, max(70, abs(center - (upper + lower) / 2)))
            direction = -1 if center < upper else 1
            location = round(box["y"], 1)
            if location == previous:
                direction = -1 if center < self.height / 2 else 1
            previous = location
            self.scroll(direction * delta, navigation=navigation)
        raise AssertionError(f"cannot reach control by scrolling: {locator}")

    def tap(self, selector):
        locator = self.page.locator(selector) if isinstance(selector, str) else selector
        box = self.reveal(locator) if self.page.locator("#appearance-dialog[open]").count() else locator.bounding_box()
        if not box:
            raise AssertionError(f"missing touch target: {selector}")
        self.tap_at(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    def open(self):
        self.tap("#appearance-open")
        self.page.wait_for_function("document.getElementById('appearance-dialog').open")

    def option(self, key, value):
        """Choose an ordinary DOM button, so the panel can see and hit it."""
        encoded = str(value).lower() if isinstance(value, bool) else str(value)
        control = self.page.locator(f"#appearance-{key}")
        self.reveal(control)
        role = control.get_attribute("role")
        if role == "spinbutton":
            self.number(key, value)
        elif role == "switch":
            if control.get_attribute("aria-checked") != encoded:
                self.tap(control)
        elif role == "combobox":
            self.tap(control)
            self.tap(f'#appearance-{key}-options [data-value="{encoded}"]')
        else:
            self.tap(f'#appearance-{key} button[data-value="{encoded}"]')

    def value(self, key):
        return self.page.locator(f"#appearance-{key}").evaluate("e => e.value")

    def number(self, key, wanted):
        for _ in range(105):
            current = int(self.value(key))
            if current == wanted:
                return
            card = self.page.locator(f'.appearance-field[data-key="{key}"]')
            label = "Augmenter" if current < wanted else "Diminuer"
            self.tap(card.locator(f'button[aria-label^="{label}"]'))
        raise AssertionError(f"{key} stepper cannot reach {wanted}")

    def type_background(self, value, checks):
        self.tap("#appearance-background")
        self.keyboard.sync()
        probe = self.page.evaluate("window.__udispKb.probe()")
        checks.check("on-screen keyboard appears above the appearance dialog",
                     self.keyboard.visible and probe.get("open"), str(probe))
        if not self.keyboard.visible:
            raise AssertionError("background input did not bring up the panel keyboard")
        self.snapshot("keyboard-above-appearance-dialog")
        for char in value:
            key = next((k for k in self.keyboard.keys
                        if k["action"] == "char" and self.keyboard._label(k) == char), None)
            if key is None:
                layer = next(k for k in self.keyboard.keys if k["action"] == "layer")
                self.tap_at(layer["x"] + layer["w"] / 2, layer["y"] + layer["h"] / 2)
                key = next((k for k in self.keyboard.keys
                            if k["action"] == "char" and self.keyboard._label(k) == char), None)
            if key is None:
                raise AssertionError(f"on-screen keyboard cannot type {char!r}")
            self.tap_at(key["x"] + key["w"] / 2, key["y"] + key["h"] / 2)
        hide = next(k for k in self.keyboard.keys if k["action"] == "hide")
        self.tap_at(hide["x"] + hide["w"] / 2, hide["y"] + hide["h"] / 2)
        checks.check("background URL is entered through on-screen keys",
                     self.value("background") == value, self.value("background"))

    def apply(self, selector="#appearance-apply"):
        with self.page.expect_navigation(wait_until="domcontentloaded", timeout=7000):
            self.tap(selector)
        self.page.wait_for_selector("a.tile")
        self.keyboard.forget()


def form_geometry(panel, checks, label):
    page = panel.page
    metadata = page.locator("#appearance-fields").text_content()
    checks.check(f"{label}: controls use the complete metadata contract",
                 json.loads(metadata) == appearance.FIELDS and len(appearance.FIELDS) == 22)
    checks.check(f"{label}: appearance uses visible DOM controls without native selects",
                 page.locator("#appearance-dialog select").count() == 0)
    problems, seen = [], set()
    for field in appearance.FIELDS:
        locator = page.locator(f"#appearance-{field['key']}")
        panel.reveal(locator)
        card = page.locator(f'.appearance-field[data-key="{field["key"]}"]')
        if locator.get_attribute("role") == "combobox":
            panel.tap(locator)
        controls = card.locator("button, input")
        for control in controls.all():
            box = panel.reveal(control)
            seen.add(control.evaluate("e => e.id || e.outerHTML"))
            if box["width"] < 44 or box["height"] < 44:
                problems.append((field["key"], "target below 44px", box))
            if box["x"] < 0 or box["x"] + box["width"] > panel.width + 1:
                problems.append((field["key"], "horizontal clipping", box))
        if locator.get_attribute("role") == "combobox":
            panel.tap(locator)
    selectors = ["#appearance-reset", "#appearance-cancel", "#appearance-apply", "#appearance-close"]
    selectors += ["#" + tab.get_attribute("id")
                  for tab in page.locator("#appearance-tabs button").all()]
    for selector in selectors:
        box = panel.reveal(page.locator(selector))
        if min(box["width"], box["height"]) < 44:
            problems.append((selector, "target below 44px", box))
    overflow = page.evaluate("""() => ({page: document.documentElement.scrollWidth,
      dialog: document.querySelector('#appearance-dialog').scrollWidth,
      width: document.querySelector('#appearance-dialog').clientWidth})""")
    checks.check(f"{label}: every control is reachable by wheel and has a 44px hit target",
                 not problems and len(seen) >= 22, str(problems))
    checks.check(f"{label}: settings have no horizontal overflow",
                 overflow["page"] <= panel.width and overflow["dialog"] <= overflow["width"] + 1,
                 str(overflow))


def effective(panel, expected, checks, label):
    page = panel.page
    actual = page.evaluate("""() => {
      const style = s => { const e = document.querySelector(s); if (!e) return null;
        const c = getComputedStyle(e); return {color:c.color,size:c.fontSize,
          filter:c.filter,image:c.backgroundImage}; };
      return {scheme:getComputedStyle(document.documentElement).colorScheme,
        time:style('.time'), date:style('.date'), weather:style('#temp'), wall:style('.wall'),
        avatar:document.querySelectorAll('#av').length};
    }""")
    columns = page.evaluate(GEOMETRY)["across"]
    checks.check(f"{label}: theme and columns affect the rendered dashboard",
                 actual["scheme"] == "light" and columns == 2, str(actual))
    checks.check(f"{label}: clock, date and weather sizes and colors reach computed styles",
                 actual["time"]["size"] == "96px" and actual["time"]["color"] == palette("amber")
                 and actual["date"]["size"] == "16px" and actual["date"]["color"] == palette("sky")
                 and actual["weather"]["size"] == "34px" and actual["weather"]["color"] == palette("rose"),
                 str(actual))
    checks.check(f"{label}: avatar visibility affects the page",
                 actual["avatar"] == int(expected["avatar"]), str(actual))
    checks.check(f"{label}: real background image, blur and dim affect the wall layer",
                 actual["wall"]["image"] != "none"
                 and actual["wall"]["filter"] == "blur(10px) brightness(0.4)"
                 and Wallpaper.hits > 0, str(actual["wall"]))


def exercise(playwright, address, preference_file, checks, executable, width, height, wallpaper, output):
    label = f"{width}x{height} touch"
    output.mkdir(parents=True, exist_ok=True)
    panel = Panel(playwright, executable, width, height, output)
    page = panel.page
    try:
        panel.goto(address)
        panel.open()
        checks.check(f"{label}: Réglages opens the named link-page appearance dialog",
                     page.locator("#appearance-dialog").get_attribute("aria-labelledby") == "appearance-title"
                     and page.locator("#appearance-title").inner_text() == "Apparence de la page de liens")
        panel.snapshot("01-settings-open")
        form_geometry(panel, checks, label)
        baseline = request(address)[1]
        before = len(panel.posts)
        panel.option("theme", "light")
        panel.option("columns", 2)
        checks.check(f"{label}: injected theme and column taps update the visible choices",
                     panel.value("theme") == "light" and str(panel.value("columns")) == "2")
        panel.tap("#appearance-cancel")
        page.wait_for_function("!document.getElementById('appearance-dialog').open")
        checks.check(f"{label}: Annuler discards the draft without POST",
                     len(panel.posts) == before and request(address) == (200, baseline))
        panel.open()
        panel.option("theme", "light")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.getElementById('appearance-dialog').open")
        checks.check(f"{label}: Escape discards the draft without POST and restores focus",
                     len(panel.posts) == before and request(address) == (200, baseline)
                     and page.evaluate("document.activeElement.id") == "appearance-open")
        panel.open()
        changes = {"theme": "light", "columns": 2, "clock_size": "large",
                   "clock_color": "amber", "date_size": "small", "date_color": "sky",
                   "weather_size": "large", "weather_color": "rose", "avatar": False,
                   "background": wallpaper, "background_blur": "md", "background_dim": 60}
        for key, value in changes.items():
            if key == "background":
                panel.type_background(value, checks)
            elif key == "background_dim":
                panel.number(key, value)
            else:
                panel.option(key, value)
        panel.snapshot("02-selected-through-injector")
        page.route("**" + appearance.PATH, lambda route: route.fulfill(
            status=500, content_type="text/plain", body="deliberate test save failure")
                   if route.request.method == "POST" else route.continue_())
        panel.tap("#appearance-apply")
        page.wait_for_selector("#appearance-error:not([hidden])")
        checks.check(f"{label}: a save error keeps the dialog and every selected choice",
                     page.locator("#appearance-dialog[open]").count() == 1
                     and all(str(panel.value(key)).lower() == str(value).lower()
                             for key, value in changes.items())
                     and request(address) == (200, baseline))
        panel.snapshot("03-save-error-retains-choices")
        page.unroute("**" + appearance.PATH)
        panel.apply()
        expected = {**baseline, **changes}
        checks.check(f"{label}: Appliquer reloads and persists the complete effective snapshot",
                     request(address) == (200, expected)
                     and {**baseline, **json.loads(preference_file.read_text())} == expected
                     and panel.posts[-1] == changes)
        effective(panel, expected, checks, label)
        panel.snapshot("04-applied-dashboard")
        restarted = start(preference_file)
        panel.goto(restarted)
        checks.check(f"{label}: restart restores all selected appearance fields",
                     request(restarted) == (200, expected))
        effective(panel, expected, checks, label + " after restart")
        panel.open()
        panel.option("avatar", True)
        panel.apply()
        checks.check(f"{label}: avatar can be enabled again through touch",
                     page.locator("#av").count() == 1 and request(restarted)[1]["avatar"] is True)
        panel.open()
        panel.option("layout", "classic")
        panel.apply()
        checks.check(f"{label}: classic layout keeps Réglages reachable",
                     page.locator("body.dashboard").count() == 0
                     and page.locator("#appearance-open").is_visible())
        panel.open()
        panel.option("layout", "dashboard")
        panel.apply()
        checks.check(f"{label}: classic can return to dashboard through touch settings",
                     page.locator("body.dashboard").count() == 1)
        panel.open()
        panel.apply("#appearance-reset")
        checks.check(f"{label}: reset reloads configured defaults and clears overrides",
                     request(restarted) == (200, DEFAULTS)
                     and json.loads(preference_file.read_text()) == {}
                     and page.evaluate(GEOMETRY)["across"] == 3)
        checks.check(f"{label}: no JavaScript error on the real sender path",
                     not panel.errors, str(panel.errors))
        panel.snapshot("05-reset-dashboard")
    except Exception as error:
        checks.check(f"{label}: touch workflow completes", False, repr(error))
        try:
            panel.snapshot("failure")
        except Exception:
            pass
        raise
    finally:
        panel.close()


def run(playwright, address, preference_file, checks, executable=None, output=None):
    output = pathlib.Path(output or tempfile.mkdtemp(prefix="portall-appearance-touch-"))
    print(f"appearance touch artifacts: {output}", flush=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Wallpaper)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wallpaper = f"http://127.0.0.1:{server.server_address[1]}/wall.svg"
    try:
        for index, (width, height) in enumerate(((1024, 600), (1280, 800))):
            # A launcher already running retains its own in-memory snapshot;
            # restarting the other test instance must not update it remotely.
            current_address = start(preference_file) if index else address
            exercise(playwright, current_address, preference_file, checks, executable,
                     width, height, wallpaper, output / f"{width}x{height}")
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default=os.environ.get("CHROMIUM") or None)
    parser.add_argument("--output", help="artifact directory (default: a new /tmp folder)")
    arguments = parser.parse_args()
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="portall-appearance-preferences-") as directory:
        preference_file = pathlib.Path(directory, "appearance.json")
        address = start(preference_file)
        with sync_playwright() as playwright:
            run(playwright, address, preference_file, checks,
                executable=arguments.browser, output=arguments.output)
    print("all passed" if not checks.failures else f"{len(checks.failures)} failed")
    return bool(checks.failures)


if __name__ == "__main__":
    sys.exit(main())
