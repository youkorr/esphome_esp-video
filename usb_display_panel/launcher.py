"""The page a panel comes home to, built from the add-on's own settings.

A panel pointed at a page of links is a launcher, and the point of putting the
page here rather than asking for a URL is that there is then nothing else to
install and nothing else to keep running: the add-on already has the list, and
it already has a process that outlives every panel.

Everything is inline and nothing is fetched. The container has no promise of
reaching the internet, a font or an icon pack that fails to load leaves holes
where the labels should be, and a panel is exactly where nobody can open the
console to find out why. Icons are whatever character you put in the field --
an emoji, a letter -- because a character always draws.

Built for a finger: large targets, no hover, no small text, and the tiles size
themselves to whatever shape the panel is.
"""
import html
import http.server
import threading

# Inside the add-on's own container, which is where the senders run too, so
# nothing of this is reachable from the network.
PORT = 8099
ADDRESS = f"http://127.0.0.1:{PORT}/"
# What somebody writes in a panel's url: to be sent here.
KEYWORD = "launcher"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
 :root { color-scheme: dark; }
 * { box-sizing: border-box; }
 html, body { margin: 0; height: 100%%; }
 body {
   background: #10131a; color: #e8ecf4;
   font: 500 16px/1.3 system-ui, -apple-system, "Segoe UI", sans-serif;
   display: flex; flex-direction: column;
 }
 header { padding: 5vh 6vw 2vh; }
 h1 { margin: 0; font-size: clamp(22px, 4.5vw, 40px); font-weight: 600; }
 header p { margin: .4em 0 0; color: #8b95a8; font-size: clamp(13px, 2.2vw, 18px); }
 main {
   flex: 1; display: grid; gap: 3vmin; padding: 2vh 6vw 6vh;
   grid-template-columns: repeat(auto-fit, minmax(min(38vw, 260px), 1fr));
   align-content: start;
 }
 a.tile {
   display: flex; flex-direction: column; justify-content: center;
   align-items: center; gap: .5em;
   min-height: 18vh; padding: 3vmin; border-radius: 4vmin;
   background: #1b2130; border: 1px solid #2a3346;
   color: inherit; text-decoration: none;
   /* A finger has no hover, and a tap that lights nothing looks ignored. */
   -webkit-tap-highlight-color: transparent;
 }
 a.tile:active { background: #27324a; border-color: #3d4a66; }
 .icon { font-size: clamp(30px, 7vw, 56px); line-height: 1; }
 .name { font-size: clamp(15px, 2.6vw, 22px); text-align: center; }
 .empty { color: #8b95a8; padding: 0 6vw; font-size: clamp(14px, 2.4vw, 20px); }
</style></head>
<body>
<header><h1>%(title)s</h1><p>%(subtitle)s</p></header>
<main>%(tiles)s</main>
</body></html>
"""

TILE = ('<a class="tile" href="%(url)s">'
        '<span class="icon">%(icon)s</span>'
        '<span class="name">%(name)s</span></a>')

EMPTY = ('<p class="empty">No links yet. Add them under <b>links</b> in this '
         "add-on's configuration, then restart it.</p>")


def render(links, title="Panel", subtitle=""):
    """The page, as one string.

    Every value is escaped. These come from a configuration file a person
    edits, so a name containing an ampersand or a stray angle bracket is a
    typo rather than an attack -- and a typo that silently breaks the page a
    panel comes home to is still the worst kind of bug to chase.
    """
    tiles = "".join(
        TILE % {
            "url": html.escape(str(link.get("url", "")), quote=True),
            "icon": html.escape(str(link.get("icon") or "\N{BULLET}")),
            "name": html.escape(str(link.get("name") or link.get("url", ""))),
        }
        for link in links
        if link.get("url")
    )
    return PAGE % {
        "title": html.escape(title),
        "subtitle": html.escape(subtitle),
        "tiles": tiles or EMPTY,
    }


def start(links, title="Panel", subtitle=""):
    """Serve the page for as long as the add-on runs. Returns its address.

    One server for every panel: they all come home to the same list, and a
    second copy of it would only be a second thing to keep in step.
    """
    body = render(links, title, subtitle).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # the add-on log is for panels, not for page requests

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Rebuilt on every restart, and a panel that came home to a stale
            # copy would show links that were edited away.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

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
