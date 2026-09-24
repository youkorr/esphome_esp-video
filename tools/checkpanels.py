#!/usr/bin/env python3
"""Each panel its own launcher: its own links, and its own number across.

WHY THIS EXISTS. A house with two panels reported both halves at once: every
panel showed every link -- "il reprend tous les links du premier panels" --
and the four columns that suited a 1280x800 were wrong for a 1024x600. The
second half is visible rather than a matter of taste: at four across on
1024x600 the tiles are 220 px wide and the names break INSIDE words,
"Jellyfi|n", "YouTu|be", "Proxm|ox".

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


# The shape the Supervisor writes /data/options.json in: grouped, with a
# panel's exceptions under advanced:. Two panels of the two shapes reported.
OPTIONS = {
    "panels": [
        {"name": "salon", "host": "127.0.0.2", "url": "launcher",
         "width": 1280, "height": 800, "rotate": "0",
         "touch": {"rotate": "0"}, "advanced": {"home_assistant": True}},
        {"name": "cuisine", "host": "127.0.0.3", "url": "launcher",
         "width": 1024, "height": 600, "rotate": "0",
         "touch": {"rotate": "0"}, "advanced": {"columns": 3}},
    ],
    "links": [
        {"name": "Home Assistant", "url": "http://x/ha", "icon": "home"},
        {"name": "Jellyfin", "url": "http://x/jf", "icon": "jellyfin",
         "panels": "salon"},
        # Capitals and a stray space: the same room to whoever typed it.
        {"name": "Recettes", "url": "http://x/re", "icon": "cuisine",
         "panels": " Cuisine "},
        {"name": "YouTube", "url": "http://x/yt", "icon": "youtube",
         "panels": "salon, cuisine"},
        {"name": "Proxmox", "url": "http://x/px", "icon": "proxmox",
         "panels": ""},
        # A typo, which must be said out loud rather than hide the tile.
        {"name": "Chambre", "url": "http://x/ch", "icon": "bed",
         "panels": "chambre"},
    ],
    "launcher": {"columns": 4},
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
  return {names: t.map(name),
          across: Math.max(0, ...Object.values(rows)),
          first: t.length ? Math.round(t[0].getBoundingClientRect().width) : 0};
}"""


def main():
    import launcher
    import run
    from playwright.sync_api import sync_playwright

    print("Each panel its own launcher:")

    # -- the choosing, on its own ------------------------------------------
    links = OPTIONS["links"]
    names = lambda who: [l["name"] for l in launcher.links_for(links, who)]
    check("a link with no panels: is on every panel",
          "Home Assistant" in names("salon") and "Home Assistant" in names("cuisine"))
    check("an EMPTY panels: is the same as none",
          "Proxmox" in names("salon") and "Proxmox" in names("cuisine"))
    check("a link for one panel is on that one and not the other",
          "Jellyfin" in names("salon") and "Jellyfin" not in names("cuisine"))
    check("a name is matched without case or surrounding spaces",
          "Recettes" in names("cuisine") and "Recettes" not in names("salon"))
    check("two names separated by a comma reach both panels",
          "YouTube" in names("salon") and "YouTube" in names("cuisine"))
    check("a page asked for with no panel shows every link",
          len(launcher.links_for(links, "")) == len(links))
    check("the two panels are sent to different addresses",
          launcher.address_for("salon") != launcher.address_for("cuisine"))
    check("a name with a space in it survives the address",
          "salle+de+bain" in launcher.address_for("salle de bain")
          or "salle%20de%20bain" in launcher.address_for("salle de bain"))

    # -- the add-on's own path, from an options file -----------------------
    folder = tempfile.mkdtemp()
    path = pathlib.Path(folder) / "options.json"
    path.write_text(json.dumps(OPTIONS))
    os.environ["UDISP_CONFIG"] = str(path)
    panels = run.load_panels()
    said = io.StringIO()
    with redirect_stdout(said):
        where = run.start_launcher(run._config, panels)
        run.route_to_launcher(panels, where)
    log = said.getvalue()
    check("the launcher starts", where is not None)
    check("a link naming no panel here is said out loud, with its name",
          "chambre" in log and "Chambre" in log)
    by_name = {p["name"]: p for p in panels}
    check("each panel is handed its own address",
          by_name["salon"]["url"] != by_name["cuisine"]["url"]
          and "cuisine" in by_name["cuisine"]["url"])
    check("and a panel's column count reaches no sender",
          "--columns" not in run.command_for(by_name["cuisine"]))

    with sync_playwright() as pw:
        browser = (pw.chromium.launch(executable_path=BROWSER)
                   if BROWSER else pw.chromium.launch())

        def open_as(panel, url=None):
            page = browser.new_page(viewport={"width": panel["width"],
                                              "height": panel["height"]})
            page.goto(url or panel["url"])
            got = page.evaluate(TILES_JS)
            got["split"] = page.evaluate(WORDS_SPLIT_JS)
            page.close()
            return got

        salon = open_as(by_name["salon"])
        print(f"         salon   1280x800: {salon['across']} across, "
              f"{salon['names']}")
        check("salon shows its own links and not the kitchen's",
              "Jellyfin" in salon["names"]
              and "Recettes" not in salon["names"])
        check("salon takes the launcher's four across",
              salon["across"] == 4)

        cuisine = open_as(by_name["cuisine"])
        print(f"         cuisine 1024x600: {cuisine['across']} across, "
              f"tiles {cuisine['first']} px, {cuisine['names']}")
        check("the kitchen shows its own links and not the living room's",
              "Recettes" in cuisine["names"]
              and "Jellyfin" not in cuisine["names"])
        check("the kitchen takes its own three across", cuisine["across"] == 3)
        check("the typo'd link is on neither panel",
              "Chambre" not in salon["names"] + cuisine["names"])

        # The reported look, kept rather than remembered: the SAME panel at
        # the launcher's four across breaks names inside words. Every link
        # is shown here, because that is what the kitchen was given before.
        long_names = dict(by_name["cuisine"])
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
        check("the premise: four across on 1024x600 breaks words in half",
              bool(split_at_four))
        check("and three across breaks none", not split_at_three)
        check("the kitchen's own page breaks none", not cuisine["split"])
        browser.close()

    print()
    if faults:
        print(f"{len(faults)} failure(s)")
        return 1
    print("every case passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
