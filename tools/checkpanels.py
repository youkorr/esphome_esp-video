#!/usr/bin/env python3
"""Each panel its own launcher: its own links, and its own number across.

WHY THIS EXISTS. A house with two panels reported both halves at once: every
panel showed every link -- "il reprend tous les links du premier panels" --
and the four columns that suited a 1280x800 were wrong for a 1024x600. The
second half is visible rather than a matter of taste: at four across on
1024x600 the tiles are 220 px wide and the names break INSIDE words,
"Jellyfi|n", "YouTu|be", "Proxm|ox".

4.17.0 answered the first half with a panels: field on each LINK, and it
worked -- this file passed against it -- and was reported straight back as
"chaque panel ne dispose pas de ses propres links choisis independamment".
The choice was on the wrong side: somebody changing one screen goes to that
screen's entry. So a panel carries links: now, the names of what it shows.

So this drives the shipped code the way the add-on does -- the options file
read by run.load_panels(), the launcher started by run.start_launcher(), each
panel sent to its address by run.route_to_launcher() -- and then opens each
panel's own address at that panel's own size in a real browser, reading the
tiles off the page rather than trusting the function that chose them.

`--without` is not offered: the fault was an absence (one address for every
panel, one list, one number), and the reproduction below is run against the
shipped files of the commit before this one with `git worktree`, by hand.

Needs Playwright and a Chromium. Takes --browser or $CHROMIUM. Uses port 8099,
the launcher's own, so nothing else may be listening there.
"""

import io
import json
import os
import pathlib
import sys
import tempfile
from contextlib import redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

faults = []


def check(what, ok):
    print(("  ok     " if ok else "  ECHEC  ") + what)
    if not ok:
        faults.append(what)


# Not a real token: the right shape, and nothing it opens.
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ0ZXN0In0." + "a" * 43

# The shape the Supervisor writes /data/options.json in: grouped, with a
# panel's exceptions under advanced:. Two panels with launchers of their own,
# of the two shapes reported, and a third that has none and takes the house's.
OPTIONS = {
    "panels": [
        {"name": "salon", "host": "127.0.0.2", "url": "launcher",
         "width": 800, "height": 1280, "rotate": "90",
         "touch": {"rotate": "90"}, "advanced": {"home_assistant": True}},
        {"name": "cuisine", "host": "127.0.0.3", "url": "launcher",
         "width": 1024, "height": 600, "rotate": "180",
         "touch": {"rotate": "180"}},
        {"name": "bureau", "host": "127.0.0.4", "url": "launcher",
         "width": 800, "height": 1280, "rotate": "0",
         "touch": {"rotate": "0"}},
    ],
    # The house's: what a panel with no entry of its own shows.
    "links": [
        {"name": "Home Assistant", "url": "http://homeassistant:8123/lovelace/6",
         "icon": "home-assistant", "quality": 60, "token": FAKE_TOKEN},
        {"name": "Spotify", "url": "https://open.spotify.com/", "icon": "spotify"},
        {"name": "Proxmox", "url": "http://x/px", "icon": "proxmox"},
    ],
    "launcher": {"theme": "dark", "columns": 4,
                 "clock": {"show": True, "size": "large", "color": "white"}},
    "launchers": [
        # Its own links, and nothing else set: everything else is the house's.
        {"panel": "salon",
         "links": [
             {"name": "Jellyfin", "url": "http://192.168.1.2:8096/", "icon": "jellyfin",
              "quality": 40},
             {"name": "YouTube", "url": "https://www.youtube.com/tv", "icon": "youtube",
              "fps": 20},
             {"name": "netflix", "url": "https://www.netflix.com/fr/", "icon": "netflix"},
             {"name": "Orange TV", "url": "https://tv.orange.fr/", "icon": "tv"},
         ]},
        # Capitals in the panel's name, and a look of its own.
        {"panel": " Cuisine ", "columns": 3, "theme": "light",
         "clock_size": "small", "background": "http://x/cuisine-wall.jpg",
         "links": [
             {"name": "Recettes", "url": "http://x/re", "icon": "cuisine"},
             {"name": "Reolink", "url": "https://192.168.1.22/", "icon": "reolink",
              "quality": 30},
             {"name": "Immich", "url": "http://192.168.1.158:8080/", "icon": "immich"},
             {"name": "Home Assistant", "url": "http://homeassistant:8123/lovelace/2",
              "icon": "home-assistant"},
         ]},
        # A panel that does not exist, which must be said out loud.
        {"panel": "chambre",
         "links": [{"name": "Chambre", "url": "http://x/ch", "icon": "bed"}]},
    ],
}

WORDS_SPLIT_JS = """() => {
  // A word laid out on more than one line is a word broken in half, which is
  // what four across on 1024x600 did to every longer name.
  const out = [];
  for (const el of document.querySelectorAll('a.tile .name')) {
    const node = [...el.childNodes].find(n => n.nodeType === 3);
    if (!node) continue;
    const text = node.textContent;
    let at = 0;
    for (const word of text.split(/\\s+/)) {
      if (!word) continue;
      const start = text.indexOf(word, at); at = start + word.length;
      const r = document.createRange();
      r.setStart(node, start); r.setEnd(node, start + word.length);
      const tops = new Set([...r.getClientRects()].map(q => Math.round(q.top)));
      if (tops.size > 1) out.push(word);
    }
  }
  return out;
}"""

TILES_JS = """() => {
  const t = [...document.querySelectorAll('a.tile')];
  // The widest row, not tiles divided by rows: four tiles three across are
  // a row of three and a row of one, and dividing calls that two across.
  const rows = {};
  for (const e of t) {
    const top = Math.round(e.getBoundingClientRect().top);
    rows[top] = (rows[top] || 0) + 1;
  }
  const name = e => (e.querySelector('.name') || e).textContent.trim();
  const clock = document.querySelector('.time');
  // The INK rather than the ground: the ground is mixed with color-mix and
  // computes to color(srgb ...), while the ink is a plain rgb(), and a light
  // theme is the one whose ink is dark.
  const ink = getComputedStyle(document.body).color
    .match(/\\d+/g).slice(0, 3).map(Number);
  return {names: t.map(name),
          across: Math.max(0, ...Object.values(rows)),
          first: t.length ? Math.round(t[0].getBoundingClientRect().width) : 0,
          clock: clock ? parseFloat(getComputedStyle(clock).fontSize) : 0,
          light: ink.reduce((a, b) => a + b, 0) < 3 * 128};
}"""


def main():
    import launcher
    import run
    from playwright.sync_api import sync_playwright

    print("Each panel its own launcher:")

    folder = tempfile.mkdtemp()
    path = pathlib.Path(folder) / "options.json"
    path.write_text(json.dumps(OPTIONS))
    os.environ["UDISP_CONFIG"] = str(path)
    panels = run.load_panels()
    said = io.StringIO()
    with redirect_stdout(said):
        where, own = run.start_launchers(run._config, panels)
        run.route_to_launcher(panels, where, own)
        run.give_page_settings(panels, run._config)
    log = said.getvalue()
    by_name = {p["name"]: p for p in panels}
    urls = {name: p["url"] for name, p in by_name.items()}

    check("the house's launcher starts, for the panel that has no entry",
          where == launcher.ADDRESS)
    check("each panel is handed an address of its own",
          len(set(urls.values())) == 3)
    check("a panel with its own launcher is not on the house's",
          urls["salon"] != launcher.ADDRESS and urls["cuisine"] != launcher.ADDRESS)
    check("and the panel with none is", urls["bureau"] == launcher.ADDRESS)
    check("an entry naming no panel is said out loud, with the panels there are",
          "\"chambre\", which is no panel here" in log and "salon" in log)
    check("a panel on the house's launcher is told how to have its own",
          any("[bureau]" in l and "launchers:" in l for l in log.splitlines()))
    check("and a panel with its own is not",
          not any("[salon]" in l and "add an entry" in l for l in log.splitlines()))

    # What each panel's SENDER is handed, off the command line the add-on
    # really builds. Printed nowhere: it carries a token.
    line = {name: " ".join(run.command_for(p)) for name, p in by_name.items()}
    check("a panel's own link sets the quality on its own page",
          "http://192.168.1.2:8096/=40" in line["salon"])
    check("and the house's links set nothing on a panel with its own",
          "lovelace/6=60" not in line["salon"]
          and "lovelace/6=60" not in line["cuisine"])
    check("each panel's settings are its own, not the other's",
          "192.168.1.22/=30" in line["cuisine"]
          and "192.168.1.22/=30" not in line["salon"]
          and "8096/=40" not in line["cuisine"])
    check("the house's token still reaches a panel whose links carry none",
          "--page-token http://homeassistant:8123/lovelace/6=" in line["salon"])
    check("the panel with no entry keeps the house's settings",
          "lovelace/6=60" in line["bureau"])
    check("nothing of the launcher reaches a sender as a flag",
          not any(f in l for l in line.values()
                  for f in ("--columns", "--links", "--launcher")))

    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path=BROWSER)
                   if BROWSER else pw.chromium.launch())

        def open_as(panel):
            w, h = panel["width"], panel["height"]
            if str(panel.get("rotate")) in ("90", "270"):
                w, h = h, w
            page = browser.new_page(viewport={"width": w, "height": h})
            page.goto(panel["url"])
            got = page.evaluate(TILES_JS)
            got["split"] = page.evaluate(WORDS_SPLIT_JS)
            got["size"] = f"{w}x{h}"
            page.close()
            return got

        seen = {name: open_as(p) for name, p in by_name.items()}
        for name, got in seen.items():
            print(f"         {name:8} {got['size']}: {got['across']} across, "
                  f"clock {got['clock']:.0f}px, "
                  f"{'light' if got['light'] else 'dark'}, {got['names']}")
        salon, cuisine, bureau = seen["salon"], seen["cuisine"], seen["bureau"]
        check("salon shows exactly its own links",
              salon["names"] == ["Jellyfin", "YouTube", "netflix", "Orange TV"])
        check("the kitchen shows exactly its own links",
              cuisine["names"] == ["Recettes", "Reolink", "Immich",
                                   "Home Assistant"])
        check("the panel with no entry shows the house's",
              bureau["names"] == ["Home Assistant", "Spotify", "Proxmox"])
        check("salon inherits the house's four across", salon["across"] == 4)
        check("the kitchen takes its own three across", cuisine["across"] == 3)
        check("the kitchen takes its own theme, the others keep the house's",
              cuisine["light"] and not salon["light"] and not bureau["light"])
        check("the kitchen takes its own clock size, salon inherits the house's",
              0 < cuisine["clock"] < salon["clock"])
        import urllib.request
        body = {name: urllib.request.urlopen(url, timeout=5).read().decode()
                for name, url in urls.items()}
        check("the kitchen's own wallpaper is on its page and nobody else's",
              "cuisine-wall.jpg" in body["cuisine"]
              and "cuisine-wall.jpg" not in body["salon"] + body["bureau"])
        check("the kitchen's own page breaks no word in half",
              not cuisine["split"])

        # The reported look, kept rather than remembered: the SAME panel at
        # the launcher's four across USED to break names inside words, which
        # is why a panel was given columns of its own. A narrow tile now puts
        # its icon above its name instead (tools/checktiles.py), so four
        # across must break none either -- the per-panel setting stays, and
        # is no longer the only thing between a panel and a cut word.
        page = browser.new_page(viewport={"width": 1024, "height": 600})
        page.set_content(launcher.render(
            [{"name": n, "url": "http://x/", "icon": "home"} for n in
             ("Home Assistant", "Jellyfin", "YouTube", "Prime Video",
              "Netflix", "Orange TV", "Proxmox", "Unraid")], columns=4))
        split_at_four = page.evaluate(WORDS_SPLIT_JS)
        page.set_content(launcher.render(
            [{"name": n, "url": "http://x/", "icon": "home"} for n in
             ("Home Assistant", "Jellyfin", "YouTube", "Prime Video",
              "Netflix", "Orange TV", "Proxmox", "Unraid")], columns=3))
        split_at_three = page.evaluate(WORDS_SPLIT_JS)
        page.close()
        print(f"         1024x600, words broken in half: at four across "
              f"{split_at_four}, at three {split_at_three}")
        check("four across on 1024x600 breaks no word in half any more",
              not split_at_four)
        check("and three across breaks none", not split_at_three)
        browser.close()

    print()
    if faults:
        print(f"{len(faults)} failure(s)")
        return 1
    print("every case passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
