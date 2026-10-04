#!/usr/bin/env python3
"""Does a login a site keeps with SameSite=Strict survive a tap on its tile?

WHY THIS EXISTS. Reported from a panel: Unraid asked for its password again
each time its tile was pressed, "stay signed in" ticked and nothing
restarted. Unraid's session cookie is SameSite=Strict, and a browser does
not send a Strict cookie on a navigation that starts on ANOTHER site -- which
a tile on the launcher, served from 127.0.0.1, always is. The add-on now
opens a tapped tile itself (FOLLOW_JS in launcher.py, Follow and go_to in
ha_send.py), the way an address typed into the bar is opened.

Here the launcher is the shipped one, served by launcher.start(), and the
"site" is another host (localhost against 127.0.0.1) that sets a Strict
cookie at /login and says whether it came back:

  1. in a browser, with the shipped Follow bound the way the sender binds it
     and go_to doing the navigation -- a real click on the real tile;
  2. the same page with nothing bound: an ordinary link, which is how the
     launcher behaves in any browser, and is the report reproduced;
  3. a page on another origin calling __udispFollow is not listened to;
  4. the shipped ha_send.py, end to end: signed in, then the link asked for
     by voice on a launcher with a face, which presses the tile -- the whole
     of the loop's new path, read off the site's own request log.

    python3 tools/checkfollow.py [--browser PATH]
"""
import http.server
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "components" / "portall"))
sys.path.insert(0, str(HERE / "portall"))

BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")

fails = 0


def check(what, passed, detail=""):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what
          + (f"  ({detail})" if detail and not passed else ""))
    if not passed:
        fails += 1


def settle(until, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        time.sleep(0.1)
    return bool(until())


class Site(http.server.BaseHTTPRequestHandler):
    """A server that signs in the way Unraid does, and remembers what came."""
    heard = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        if self.path == "/login":
            self.send_header("Set-Cookie",
                             "unraid_x=1; Path=/; HttpOnly; SameSite=Strict")
        elif self.path != "/favicon.ico":
            Site.heard.append(
                (self.path, "unraid_x=1" in (self.headers.get("Cookie") or "")))
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>site</title>signed in")


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def cookie_for(path):
    for got, sent in reversed(Site.heard):
        if got == path:
            return sent
    return None


def in_a_browser(site, launch):
    import ha_send
    from playwright.sync_api import sync_playwright
    print("In the browser, the tile against the site's Strict login:")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSER or None)

        context = browser.new_context()
        follow = ha_send.Follow(launch)
        context.expose_binding("__udispFollow", follow)
        page = context.new_page()
        page.goto(f"{site}/login")
        page.goto(launch)
        page.click("a.tile")
        taken = follow.drain()
        check("a tap on the tile is handed to the sender, not followed",
              taken == [("follow", f"{site}/tile")] and page.url == launch,
              repr(taken))
        if taken:
            ha_send.go_to(page, taken[0][1])
        check("and the site receives its login with it -- Unraid stays "
              "signed in", cookie_for("/tile") is True, repr(Site.heard))

        # A page somewhere else that knows the binding's name.
        page.goto(f"{site}/other")
        page.evaluate(f"window.__udispFollow('{site}/sneaky')")
        time.sleep(0.2)
        check("a page on another origin cannot ask the sender to navigate",
              follow.drain() == [])
        context.close()

        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{site}/login")
        page.goto(launch)
        page.click("a.tile")
        page.wait_for_url(f"{site}/tile")
        check("with no sender it is an ordinary link, and the login is "
              "left behind -- the report", cookie_for("/tile") is False,
              repr(Site.heard[-1:]))
        browser.close()


def end_to_end(site, launch):
    print("The shipped sender, told by voice, on a launcher with a face:")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def drain():
        conn, _ = listener.accept()
        try:
            while conn.recv(1 << 20):
                pass
        except OSError:
            pass
    threading.Thread(target=drain, daemon=True).start()
    command = [sys.executable, "-u",
               str(HERE / "components" / "portall" / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", launch,
               "--no-token", "--not-home-assistant", "--width", "800",
               "--height", "480", "--audio", "off", "--keyboard", "off",
               "--control"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def say(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()
    try:
        check("the sender starts on the launcher",
              settle(lambda: any(l.startswith("Ready") for l in out), 40),
              "".join(out[-5:]))
        say(f"open {site}/login")
        settle(lambda: any("/login" in l and "opened" in l for l in out), 15)
        say("home")
        settle(lambda: any(l.startswith("Home: back") for l in out), 15)
        time.sleep(1.5)
        Site.heard.clear()
        say(f"open {site}/tile")
        check("the face presses the tile and the sender opens it",
              settle(lambda: any(l.startswith("Tile: opened") for l in out),
                     15), "".join(out[-6:]))
        check("and the site receives its login with it",
              settle(lambda: cookie_for("/tile") is not None, 10)
              and cookie_for("/tile") is True, repr(Site.heard))
    finally:
        process.kill()
        listener.close()


def main():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("skipped: no Playwright here")
        return 0
    import launcher
    site_server = serve(Site)
    # localhost against 127.0.0.1: a different SITE, as Unraid's address is
    # to the launcher's.
    site = f"http://localhost:{site_server.server_address[1]}"
    links = [{"name": "Unraid", "url": f"{site}/tile", "icon": "unraid"}]
    launch = launcher.start(links, avatar=True,
                            port=launcher.ANY_PORT)
    in_a_browser(site, launch)
    end_to_end(site, launch)
    site_server.shutdown()
    print(f"\n{fails} problem(s)." if fails else "\nAll good.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
