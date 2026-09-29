#!/usr/bin/env python3
"""Does the weather's colour reach the TEMPERATURE on the page?

WHY THIS EXISTS. Reported from a panel: "la couleur de la temperature meteo
est fixe il faudrait les meme couleur que date". The clock and the date each
had a colour from a list; the weather had none, on the argument that it is an
emoji -- true of the sky, and not of the number beside it, which is text drawn
in the theme's faint ink whatever the household chose for everything else.

This builds the launcher the way the add-on does -- the grouped form the
Supervisor writes, run.py's regroup(), launcher_config() for a panel's own
launcher, start_launcher() -- and reads the COMPUTED colour of the
temperature in a real browser: the house's colour, a panel's own overriding
it, the theme's when nothing is chosen, and the date left alone.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM.
"""

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))

import launcher  # noqa: E402
import run  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

LINKS = [{"name": "Home Assistant", "url": "http://x/1", "icon": "home"}]

# There is no Home Assistant here, so the reading is a fixed one: everything
# after it -- the page, the sheet, the spans -- is the shipped path.
READING = {"condition": "sunny", "text": "21°C", "temperature": 21, "unit": "°C"}
run.Weather.start = lambda self: (lambda: READING) if self.entity else None

faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        faults.append(what)


def rgb(name):
    h = launcher.PALETTES[name].lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def serve(options, entry=None):
    """One launcher built the way the add-on builds it, from the form."""
    config = run.regroup({"links": LINKS, "launcher": options})
    if entry is not None:
        config = run.launcher_config(config, dict(entry, links=LINKS))
    return run.start_launcher(config, port=launcher.ANY_PORT)


def colours(page, address):
    page.goto(address)
    page.wait_for_selector("#temp", state="attached")
    return page.evaluate("""() => [
        getComputedStyle(document.getElementById('temp')).color,
        getComputedStyle(document.querySelector('.date')).color]""")


def main():
    print("The temperature's colour:")
    weather = {"entity": "weather.home"}
    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path=BROWSER) if BROWSER
                   else pw.chromium.launch())
        page = browser.new_page(viewport={"width": 1024, "height": 600})

        plain_temp, plain_date = colours(page, serve({"weather": weather}))
        check("with nothing chosen it is not a palette colour",
              plain_temp not in (rgb("rose"), rgb("sky")), plain_temp)

        temp, date = colours(page, serve(
            {"weather": dict(weather, color="rose")}))
        check("the house's colour reaches the temperature",
              temp == rgb("rose"), temp)
        check("and leaves the date as it was", date == plain_date, date)

        temp, _ = colours(page, serve(
            {"weather": dict(weather, color="rose")},
            {"panel": "cuisine", "weather_color": "sky"}))
        check("a panel's own launcher overrides it", temp == rgb("sky"), temp)

        temp, _ = colours(page, serve(
            {"weather": dict(weather, color="theme")}))
        check("theme is the theme's ink", temp == plain_temp, temp)

        temp, date = colours(page, serve(
            {"weather": dict(weather, color="amber"),
             "date": {"color": "sky"}}))
        check("beside a date colour, each keeps its own",
              temp == rgb("amber") and date == rgb("sky"), f"{temp} / {date}")
        browser.close()
    print("ok" if not faults else f"{len(faults)} ECHEC")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
