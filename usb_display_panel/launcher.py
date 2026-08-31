"""The page a panel comes home to, built from the add-on's own settings.

A panel pointed at a page of links is a launcher, and the point of putting the
page here rather than asking for a URL is that there is then nothing else to
install and nothing else to keep running: the add-on already has the list, and
it already has a process that outlives every panel.

The shape is taken from Homepage (gethomepage.dev), which is what was asked
for, and its vocabulary is kept so that somebody who knows one knows the other:
a **theme** of dark or light, a **color** named after Tailwind's palettes, a
**background** picture with a blur and a dim over it, groups of links under
their own headers, and cards that carry an icon, a name and a description.

What is deliberately not taken from it is the density. Homepage is read at a
desk with a mouse; this is read across a room and pressed with a thumb, so the
cards are large, there is no hover state, and nothing is small enough to need
aiming at.

Everything is inline and only the wallpaper is ever fetched. The container has
no promise of reaching the internet, a font or an icon pack that fails to load
leaves holes where the labels should be, and a panel is exactly where nobody
can open the console to find out why. Icons are whatever character you put in
the field -- an emoji, a letter -- because a character always draws. A
wallpaper that will not load leaves the plain colour behind it, which is why it
is allowed to be fetched at all.
"""
import html
import http.server
import mimetypes
import os
import threading

# Inside the add-on's own container, which is where the senders run too, so
# nothing of this is reachable from the network.
PORT = 8099
ADDRESS = f"http://127.0.0.1:{PORT}/"
# What somebody writes in a panel's url: to be sent here.
KEYWORD = "launcher"
# Where the wallpaper is served from when it is a file on disk rather than an
# address. One path, so the page can name it before the file has been read.
WALLPAPER_PATH = "/wallpaper"

# Homepage names its palettes after Tailwind's, so these do too. Only the
# middle shade is given: the surfaces, the borders and the text are mixed from
# it in the page itself, which is both shorter than twenty hex values apiece
# and impossible to get inconsistent. These are Tailwind's 500s to the eye
# rather than to the digit -- they are decoration, and nothing depends on the
# exact number.
PALETTES = {
    "slate": "#64748b",
    "gray": "#6b7280",
    "zinc": "#71717a",
    "neutral": "#737373",
    "stone": "#78716c",
    "red": "#ef4444",
    "amber": "#f59e0b",
    "yellow": "#eab308",
    "lime": "#84cc16",
    "green": "#22c55e",
    "emerald": "#10b981",
    "teal": "#14b8a6",
    "cyan": "#06b6d4",
    "sky": "#0ea5e9",
    "blue": "#3b82f6",
    "indigo": "#6366f1",
    "violet": "#8b5cf6",
    "purple": "#a855f7",
    "fuchsia": "#d946ef",
    "pink": "#ec4899",
    "rose": "#f43f5e",
}
DEFAULT_PALETTE = "slate"

# Homepage's own words for how much to blur what is behind the cards.
BLURS = {"off": "0px", "sm": "4px", "md": "10px", "xl": "24px"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
 :root {
   color-scheme: %(scheme)s;
   --accent: %(accent)s;
   /* Every other colour is mixed from the accent and the theme's own end of
      the scale, so a palette is one value to set and cannot fall out of step
      with itself. color-mix has been in Chromium since 111 and the add-on
      already refuses to be quiet about a browser older than 114. */
   --ground: %(ground_fallback)s;
   --ground: color-mix(in srgb, var(--accent) 8%%, %(ground_end)s);
   --card: %(card_fallback)s;
   --card: color-mix(in srgb, var(--accent) 14%%, %(card_end)s);
   --edge: color-mix(in srgb, var(--accent) 30%%, %(card_end)s);
   --ink: %(ink)s;
   --faint: color-mix(in srgb, var(--ink) 55%%, var(--ground));
 }
 * { box-sizing: border-box; }
 html, body { margin: 0; min-height: 100%%; }
 body {
   background: var(--ground); color: var(--ink);
   font: 500 16px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif;
   display: flex; flex-direction: column; min-height: 100vh;
 }
 /* The picture is a layer of its own rather than the body's background,
    because a blur belongs to it alone: put on the body it would take the
    text with it, and the dim would have to be fought back out of the cards. */
 .wall {
   position: fixed; inset: 0; z-index: -1;
   background: var(--ground) center/cover no-repeat;
   background-image: %(wall)s;
   filter: blur(%(blur)s) brightness(%(brightness)s);
   transform: scale(1.06);   /* so a blur does not show the page's edge */
 }
 header { padding: 4vh 5vw 1vh; }
 h1 { margin: 0; font-size: clamp(22px, 4.5vw, 40px); font-weight: 650;
      letter-spacing: -0.01em; }
 header p { margin: .4em 0 0; color: var(--faint);
            font-size: clamp(13px, 2.2vw, 18px); }
 main { flex: 1; padding: 1vh 5vw 5vh; }
 section { margin-top: 3vh; }
 /* Homepage's "underlined" header, which is the one that still reads as a
    heading when there is a photograph behind it. */
 h2 {
   margin: 0 0 1.6vh; padding-bottom: .5em;
   font-size: clamp(15px, 2.4vw, 21px); font-weight: 600;
   letter-spacing: .08em; text-transform: uppercase; color: var(--faint);
   border-bottom: 2px solid var(--edge);
 }
 .group { display: grid; gap: 2.4vmin; grid-template-columns: %(columns)s; }
 a.tile {
   display: flex; align-items: center; gap: 3vmin;
   min-height: 13vh; padding: 2.6vmin 3.4vmin; border-radius: 3vmin;
   background: %(tile_bg)s; border: 1px solid var(--edge);
   color: inherit; text-decoration: none;
   %(tile_blur)s
   /* A finger has no hover, and a tap that lights nothing looks ignored. */
   -webkit-tap-highlight-color: transparent;
 }
 a.tile:active { border-color: var(--accent); transform: scale(.985); }
 .icon {
   flex: none; width: clamp(44px, 9vw, 74px); height: clamp(44px, 9vw, 74px);
   display: grid; place-items: center; border-radius: 28%%;
   background: color-mix(in srgb, var(--accent) 28%%, transparent);
   font-size: clamp(24px, 5vw, 40px); line-height: 1;
   /* The field asks for a character and somebody will type a word into it,
      because nothing stops them. Left alone that word runs straight across
      the name beside it. Clipped, and set smaller below when it is long, so
      the worst case is a shortened label rather than two overlapping ones. */
   overflow: hidden;
 }
 .icon.long { font-size: clamp(12px, 2vw, 17px); font-weight: 700;
              letter-spacing: -.02em; }
 /* Blocks, not spans. They are written as spans because an <a> may not
    contain a <div>, and a span left inline puts the description on the same
    line as the name with nothing between them. */
 .text { display: block; min-width: 0; }
 .name { display: block; font-size: clamp(16px, 2.7vw, 23px); font-weight: 600;
         overflow-wrap: anywhere; }
 .desc { display: block; margin-top: .25em; color: var(--faint);
         font-size: clamp(12px, 2vw, 17px); overflow-wrap: anywhere; }
 .empty { color: var(--faint); font-size: clamp(14px, 2.4vw, 20px); }
</style></head>
<body>
<div class="wall"></div>
<header><h1>%(title)s</h1>%(subtitle)s</header>
<main>%(groups)s</main>
</body></html>
"""

TILE = ('<a class="tile" href="%(url)s">'
        '<span class="icon%(icon_long)s">%(icon)s</span>'
        '<span class="text"><span class="name">%(name)s</span>%(desc)s</span>'
        '</a>')

EMPTY = ('<p class="empty">No links yet. Add them under <b>links</b> in this '
         "add-on's configuration, then restart it.</p>")


def _wallpaper(background):
    """What the page should name as its picture, and what to serve for it.

    Returns (css, path_to_serve). An address is used as it stands -- the panel
    fetches it itself, and Home Assistant's own /local is the obvious place to
    keep one. A path is served from here instead, because the page is on
    127.0.0.1 and a file:// URL in a page is refused by every browser there is.
    """
    background = str(background or "").strip()
    if not background:
        return "none", None
    if background.startswith(("http://", "https://", "data:")):
        return f'url("{html.escape(background, quote=True)}")', None
    if os.path.isfile(background):
        return f'url("{WALLPAPER_PATH}")', background
    # Said once, at startup, rather than left as a blank wall nobody can
    # explain from the panel.
    print(f"Launcher: no wallpaper at {background} -- the plain colour is "
          f"being used. Put the file under /config, /share or /media, or give "
          f"an address the panel can reach.", flush=True)
    return "none", None


def render(links, title="Panel", subtitle="", theme="dark",
           color=DEFAULT_PALETTE, background="", blur="off", dim=40,
           columns=0):
    """The page, as one string.

    Every value is escaped. These come from a configuration file a person
    edits, so a name containing an ampersand or a stray angle bracket is a
    typo rather than an attack -- and a typo that silently breaks the page a
    panel comes home to is still the worst kind of bug to chase.
    """
    dark = str(theme).lower() != "light"
    accent = PALETTES.get(str(color).lower(), PALETTES[DEFAULT_PALETTE])
    wall_css, _ = _wallpaper(background)
    has_wall = wall_css != "none"

    # Grouped in the order the groups first appear, so the list in the form is
    # the order on the panel and nobody has to think about sorting.
    groups = {}
    for link in links:
        if not link.get("url"):
            continue
        groups.setdefault(str(link.get("group") or "").strip(), []).append(link)

    body = []
    for name, entries in groups.items():
        tiles = "".join(
            TILE % {
                "url": html.escape(str(entry.get("url", "")), quote=True),
                "icon": html.escape(str(entry.get("icon") or "\N{BULLET}")),
                # Two characters still read at the full size -- "HA" is a
                # perfectly good icon. Beyond that it is a word, and a word
                # has to be set smaller to stay inside its square.
                "icon_long": (
                    " long"
                    if len(str(entry.get("icon") or "")) > 2 else ""
                ),
                "name": html.escape(str(entry.get("name") or entry["url"])),
                "desc": (
                    '<span class="desc">%s</span>'
                    % html.escape(str(entry["description"]))
                    if str(entry.get("description") or "").strip() else ""
                ),
            }
            for entry in entries
        )
        heading = f"<h2>{html.escape(name)}</h2>" if name else ""
        body.append(f'<section>{heading}<div class="group">{tiles}</div></section>')

    # Nought means let the panel decide, which is what a launcher shown on
    # three different shapes of screen wants: a fixed count either wastes a
    # wide one or crushes a narrow one.
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        columns = 0
    grid = (f"repeat({columns}, 1fr)" if columns > 0
            else "repeat(auto-fit, minmax(min(42vw, 300px), 1fr))")

    try:
        dim = max(0, min(100, int(dim)))
    except (TypeError, ValueError):
        dim = 40

    return PAGE % {
        "title": html.escape(title),
        "subtitle": (f"<p>{html.escape(subtitle)}</p>"
                     if str(subtitle).strip() else ""),
        "groups": "".join(body) or EMPTY,
        "columns": grid,
        "scheme": "dark" if dark else "light",
        "accent": accent,
        # The end of the scale everything is mixed towards, and a plain value
        # first for anything that cannot mix.
        "ground_end": "#0b0e14" if dark else "#f4f6fa",
        "ground_fallback": "#0b0e14" if dark else "#f4f6fa",
        "card_end": "#161b26" if dark else "#ffffff",
        "card_fallback": "#161b26" if dark else "#ffffff",
        "ink": "#e8ecf4" if dark else "#161b26",
        "wall": wall_css,
        # Both belong to the picture and only to the picture. Left applied
        # with no wallpaper set, the dim darkened the plain colour instead --
        # a light theme came out mud grey, which is nobody's idea of light.
        "blur": BLURS.get(str(blur).lower(), "0px") if has_wall else "0px",
        # A photograph is nearly always too bright to read white text over, so
        # the dim is what makes a wallpaper usable rather than a decoration
        # somebody turns off again.
        "brightness": f"{max(0, 100 - dim)}%" if has_wall else "100%",
        # Cards float over a picture and sit solid on a plain colour. This is
        # Homepage's cardBlur without an option for it: over a photograph it
        # is what makes the text readable, and over a flat colour it does
        # nothing at all, so there is nothing to decide.
        "tile_bg": ("color-mix(in srgb, var(--card) 72%, transparent)"
                    if has_wall else "var(--card)"),
        "tile_blur": ("backdrop-filter: blur(12px) saturate(140%); "
                      "-webkit-backdrop-filter: blur(12px) saturate(140%);"
                      if has_wall else ""),
    }


def start(links, title="Panel", subtitle="", theme="dark",
          color=DEFAULT_PALETTE, background="", blur="off", dim=40,
          columns=0):
    """Serve the page for as long as the add-on runs. Returns its address.

    One server for every panel: they all come home to the same list, and a
    second copy of it would only be a second thing to keep in step.
    """
    body = render(links, title, subtitle, theme, color, background, blur,
                  dim, columns).encode()
    _, wallpaper = _wallpaper(background)
    picture, kind = None, "application/octet-stream"
    if wallpaper:
        try:
            with open(wallpaper, "rb") as handle:
                picture = handle.read()
            kind = mimetypes.guess_type(wallpaper)[0] or kind
            print(f"Launcher: wallpaper {wallpaper} "
                  f"({len(picture) // 1024} KiB)", flush=True)
        except OSError as err:
            # Read once at startup rather than per request: a panel coming
            # home should not wait on a disk, and a file that has gone away
            # should not turn into a broken page later on.
            print(f"Launcher: could not read {wallpaper} ({err})", flush=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # the add-on log is for panels, not for page requests

        def _reply(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            # Rebuilt on every restart, and a panel that came home to a stale
            # copy would show links that were edited away.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.split("?")[0] == WALLPAPER_PATH:
                if picture is None:
                    self.send_error(404)
                    return
                # The one thing here worth caching: it does not change while
                # the add-on runs, and a panel coming home should not fetch a
                # megabyte again.
                self._reply(picture, kind)
                return
            self._reply(body, "text/html; charset=utf-8")

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as err:
        # An accessory must never cost the panels. Something else on the port,
        # or no permission to bind: say so and let every panel that wanted
        # this be told, rather than taking the add-on down with it.
        print(f"Launcher: could not listen on {PORT} ({err})", flush=True)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="launcher",
                     daemon=True).start()
    return ADDRESS
