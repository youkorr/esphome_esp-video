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


# The shape the Supervisor writes /data/options.json in: grouped, with a
# panel's exceptions under advanced:. Two panels of the two shapes reported.
OPTIONS = {
    "panels": [
        # Capitals and stray spaces: the same tile to whoever typed it.
        {"name": "salon", "host": "127.0.0.2", "url": "launcher",
         "links": "Home Assistant, Jellyfin,  youtube , Proxmox",
         "width": 1280, "height": 800, "rotate": "0",
         "touch": {"rotate": "0"}, "advanced": {"home_assistant": True}},
        # A name that is no link, which must be said out loud rather than
        # simply never appear.
        {"name": "cuisine", "host": "127.0.0.3", "url": "launcher",
         "links": "Home Assistant, Recettes, YouTube, Proxmox, Netflx",
         "width": 1024, "height": 600, "rotate": "0",
         "touch": {"rotate": "0"}, "advanced": {"columns": 3}},
        # Chose nothing, so shows everything: every configuration written
        # before the field existed.
        {"name": "bureau", "host": "127.0.0.4", "url": "launcher",
         "links": " ", "width": 800, "height": 1280, "rotate": "0",
         "touch": {"rotate": "0"}},
    ],
    "links": [
        {"name": "Home Assistant", "url": "http://x/ha", "icon": "home"},
        {"name": "Jellyfin", "url": "http://x/jf", "icon": "jellyfin"},
        {"name": "Recettes", "url": "http://x/re", "icon": "cuisine"},
        {"name": "YouTube", "url": "http://x/yt", "icon": "youtube"},
        {"name": "Proxmox", "url": "http://x/px", "icon": "proxmox"},
        # Chosen by no panel that chose, so only on the one that did not.
        {"name": "Chambre", "url": "http://x/ch", "icon": "bed"},
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
    chose = {p["name"]: p.get("links", "") for p in OPTIONS["panels"]}
    names = lambda who: [l["name"] for l in launcher.links_for(links, chose[who])]
    check("a panel shows the links it names and no others",
          names("salon") == ["Home Assistant", "Jellyfin", "YouTube", "Proxmox"])
    check("a name is matched without case or surrounding spaces",
          "YouTube" in names("salon"))
    check("the two panels choose independently",
          "Jellyfin" in names("salon") and "Jellyfin" not in names("cuisine")
          and "Recettes" in names("cuisine") and "Recettes" not in names("salon"))
    check("the house's order is kept, not the order typed",
          [l["name"] for l in launcher.links_for(links, "Proxmox, Jellyfin")]
          == ["Jellyfin", "Proxmox"])
    check("a panel that chose nothing shows every link",
          len(names("bureau")) == len(links))
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
    check("a panel naming a link that is not there is said out loud",
          "[cuisine]" in log and "netflx" in log and "Recettes" in log)
    check("and the panel's own count is said at startup",
          "[salon] launcher: 4 link(s)" in log
          and "[bureau] launcher: 6 link(s)" in log)
    by_name = {p["name"]: p for p in panels}
    check("each panel is handed its own address",
          by_name["salon"]["url"] != by_name["cuisine"]["url"]
          and "cuisine" in by_name["cuisine"]["url"])
    check("and neither its column count nor its links reach a sender",
          "--columns" not in run.command_for(by_name["cuisine"])
          and "--links" not in run.command_for(by_name["cuisine"]))

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
        check("a link neither of them chose is on neither",
              "Chambre" not in salon["names"] + cuisine["names"])
        bureau = open_as(by_name["bureau"])
        check("the panel that chose nothing shows every link",
              len(bureau["names"]) == len(links) and "Chambre" in bureau["names"])

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
