"""Sign a panel's browser into a site, from a telephone.

WHY THIS EXISTS. Google refuses to sign in a browser it can tell is being
driven, and the panel's browser is driven by definition: the picture is taken
out of it through its debugging protocol. The refusal is at the SIGNING IN
only -- afterwards the session is a cookie, and a cookie written by an
ordinary browser is sent by the driven one opening the same profile (measured,
and recorded in CLAUDE.md under import_profile). import_profile did that by
copying a folder somebody had prepared on another machine with an obscure
flag, which was reported as "trop compliquer pour les utilisateur".

So the ordinary browser is started HERE, on the panel's own profile, and shown
on the telephone: an add-on tab in Home Assistant (ingress) holds noVNC, which
draws a virtual screen (Xvfb) with a plain Chrome in it -- no debugging
protocol, no automation of any kind, and an address bar. Somebody signs in
with the telephone's own keyboard, presses Done, and the panel comes back
signed in.

Two rules this file lives under, both the add-on's own:

- An accessory must never cost the picture. A missing Xvfb, x11vnc, websockify
  or noVNC turns the page into a sentence saying which, and the panels do not
  notice. Only the panel being signed in is stopped, and only for as long as
  the session lasts -- SESSION_LIMIT_S at most.
- One browser per profile. Chromium locks a profile, so the panel's own
  browser is stopped first and the profile is checked to be FREE -- by asking
  /proc which processes name it -- before the plain one is started on it.
"""

import glob
import html
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse

# The browsers the sender prefers, in the sender's own order, so the plain
# browser opening a profile is the very one that wrote it: Chrome refuses a
# profile last written by a newer version of itself.
try:
    from ha_send import SYSTEM_BROWSERS
except Exception:  # noqa: BLE001 - the list is short and must not cost the page
    SYSTEM_BROWSERS = ("/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
                       "/usr/bin/chromium", "/usr/bin/chromium-browser")

# Ingress requests reach an add-on from the Supervisor, and from nowhere else.
# This add-on is on the host network (for HomeKit), so the port is open on the
# house's LAN as well: anything that is not the Supervisor is refused, which
# is what Home Assistant's own documentation asks of an ingress server.
INGRESS_PEERS = ("172.30.32.2",)

NOVNC = "/usr/share/novnc"

# Long enough to sign into two or three sites with a telephone keyboard, short
# enough that a panel left in the middle of it comes back by itself.
SESSION_LIMIT_S = 20 * 60

# What the browser opens on. Google, because that is the one that refuses the
# driven browser -- the address bar is right there for any other.
START_URL = "https://accounts.google.com/"

TOOLS = ("Xvfb", "x11vnc", "websockify", "xdotool")


def tools_missing():
    """What this machine lacks for a session, by name. Empty is ready."""
    missing = [name for name in TOOLS if shutil.which(name) is None]
    if not os.path.exists(os.path.join(NOVNC, "vnc.html")):
        missing.append("novnc")
    return missing


def playwrights_chromium():
    """Playwright's own full Chromium, the sender's last choice, or None."""
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
             os.path.expanduser("~/.cache/ms-playwright"), "/ms-playwright"]
    found = []
    for root in roots:
        if root:
            found += glob.glob(os.path.join(root, "chromium-*", "chrome-linux*",
                                            "chrome"))

    def build(path):
        number = re.search(r"chromium-(\d+)", path)
        return int(number.group(1)) if number else 0
    return max(found, key=build) if found else None


def pick_browser(choice=""):
    """The browser the sender would pick for this panel's `browser:`.

    The same rule as the sender's own: a path names one exactly, "off" is
    Playwright's own, and anything else prefers a system browser.
    """
    choice = str(choice or "").strip()
    if choice and choice.lower() not in ("auto", "off"):
        return choice if os.path.exists(choice) else None
    if choice.lower() != "off":
        for path in SYSTEM_BROWSERS:
            if os.path.exists(path):
                return path
    return playwrights_chromium()


def processes_using(profile):
    """The pids whose command line opens this profile directory."""
    want = "--user-data-dir=" + os.path.abspath(profile)
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue
        if any(a.decode(errors="replace") == want for a in argv):
            pids.append(int(entry))
    return pids


def wait_profile_free(profile, timeout=15.0):
    """True once no process has the profile open; kills what is left late.

    A browser told to go normally does, and releases its lock on the way out.
    One that does not is killed rather than waited on for ever: the panel is
    already dark while this waits.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not processes_using(profile):
            return True
        time.sleep(0.2)
    for pid in processes_using(profile):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)
    return not processes_using(profile)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def free_display():
    for number in range(90, 140):
        if not (os.path.exists(f"/tmp/.X11-unix/X{number}")
                or os.path.exists(f"/tmp/.X{number}-lock")):
            return number
    return None


def _wait_for(check, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class Session:
    """One plain browser on one profile, and the screen it is shown on."""

    def __init__(self, name, profile, browser, width, height, locale="",
                 url=START_URL):
        self.name = name
        self.profile = profile
        self.browser = browser
        self.width = width
        self.height = height
        self.locale = locale
        self.url = url
        self.started = time.monotonic()
        self.web_port = None
        self._processes = []
        self._browser = None
        self.display = None

    def browser_command(self, display):
        """The plain browser's command line -- and what it must NOT carry.

        Nothing here drives the browser, which is the whole point, so no
        --remote-debugging-*, no --enable-automation and no Playwright.
        --password-store=basic is what the driven browser uses too (Playwright
        passes it by default): cookies are encrypted with a key that depends
        on it, and a mismatch would copy the session across and decrypt it to
        nothing. --no-sandbox because a container runs as root, where Chrome
        will not start without it -- Playwright passes that one too.
        """
        argv = [self.browser, f"--user-data-dir={os.path.abspath(self.profile)}",
                "--password-store=basic", "--no-sandbox",
                "--no-first-run", "--no-default-browser-check",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--window-position=0,0",
                f"--window-size={self.width},{self.height}"]
        if self.locale:
            argv.append(f"--lang={self.locale}")
        argv.append(self.url)
        return argv

    def start(self):
        display = self.display = free_display()
        if display is None:
            raise RuntimeError("no free X display number")
        env = dict(os.environ, DISPLAY=f":{display}")
        vnc_port, self.web_port = free_port(), free_port()
        self._spawn(["Xvfb", f":{display}", "-screen", "0",
                     f"{self.width}x{self.height}x24", "-nolisten", "tcp"])
        if not _wait_for(lambda: os.path.exists(f"/tmp/.X11-unix/X{display}"),
                         10):
            raise RuntimeError("the virtual screen did not start")
        self._browser = self._spawn(self.browser_command(display), env=env)
        self._spawn(["x11vnc", "-display", f":{display}", "-rfbport",
                     str(vnc_port), "-localhost", "-nopw", "-forever",
                     "-shared", "-quiet"], env=env)
        if not _wait_for(lambda: _listening(vnc_port), 10):
            raise RuntimeError("the screen could not be shared")
        self._spawn(["websockify", "--web", NOVNC, f"127.0.0.1:{self.web_port}",
                     f"127.0.0.1:{vnc_port}"])
        if not _wait_for(lambda: _listening(self.web_port), 10):
            raise RuntimeError("the screen could not be served")

    def _spawn(self, argv, env=None):
        process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self._processes.append(process)
        return process

    def browser_running(self):
        return self._browser is not None and self._browser.poll() is None

    def keys(self, *steps):
        """Type into the browser's own window, the way a person does.

        xdotool through the X server rather than an event sent to the window:
        Chrome ignores synthetic events, and a key reaching the focused window
        is exactly what a keyboard does. Each step is ("key", "alt+Left") or
        ("type", "some text"), and each is its own xdotool call: `type` takes
        every word after it as text, so nothing can be chained behind it.

        Three things a first version got wrong, and none of them showed:
        `search --pid` with no pattern takes the NEXT word as the pattern, so
        the chain never ran; `windowactivate` needs a window manager and this
        screen has none, where `windowfocus` does not; and Chrome has windows
        nobody sees, so only a visible one is focused.
        """
        if not self.browser_running():
            return False
        env = dict(os.environ, DISPLAY=f":{self.display}")
        head = ["xdotool", "search", "--sync", "--onlyvisible", "--limit", "1",
                "--pid", str(self._browser.pid), ".", "windowfocus", "--sync"]
        for kind, value in steps:
            tail = (["key", "--clearmodifiers", value] if kind == "key"
                    else ["type", "--delay", "15", value])
            try:
                done = subprocess.run(head + tail, env=env, timeout=10,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return False
            if done.returncode != 0:
                return False
        return True

    def navigate(self, what):
        """Back, reload, or the page it was opened on.

        Chrome's own bar has the first two, drawn for a desktop and scaled
        down to a telephone, where they are a few pixels wide -- so the
        page carries them as buttons of its own. Home is the start page
        rather than Chrome's homepage, which is a new tab and not set.
        """
        if what == "back":
            return self.keys(("key", "alt+Left"))
        if what == "reload":
            return self.keys(("key", "F5"))
        if what == "home":
            return self.go(self.url)
        return False

    def go(self, url):
        """The address bar, typed into: focus it, the address, Enter."""
        return self.keys(("key", "ctrl+l"), ("type", url), ("key", "Return"))

    def quit_browser(self):
        """Close the browser the way a person does, and say whether it went.

        Each window closed with its own Ctrl+Shift+W, which runs the full
        shutdown when the last one goes -- including writing the cookies,
        which Chrome otherwise does on a thirty-second timer. NOT its Quit:
        on Linux Ctrl+Shift+Q only shows "hold to quit" and quits nothing.
        A sign-in can leave a second window (a pop-up), hence the loop.
        """
        if self._browser is None or self._browser.poll() is not None:
            return True
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if not self.keys(("key", "ctrl+shift+w")):
                break
            try:
                self._browser.wait(2)
                return True
            except subprocess.TimeoutExpired:
                continue
        try:
            self._browser.wait(1)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stop(self):
        """The browser first and politely, so it writes its cookies out."""
        if not self.quit_browser():
            self._browser.terminate()
            try:
                self._browser.wait(15)
            except subprocess.TimeoutExpired:
                self._browser.kill()
        for process in reversed(self._processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
        wait_profile_free(self.profile, 5)


class SignIn:
    """The ingress page, and the one session it may have at a time.

    `panels` is {name: {"profile", "browser", "locale"}}. `hold(name)` stops
    that panel's sender and returns once it has gone; `release(name)` lets it
    start again. Both are run.py's -- this file does not know how a sender is
    supervised.
    """

    def __init__(self, panels, hold, release, say, peers=INGRESS_PEERS,
                 url=START_URL):
        self.panels = panels
        self.hold = hold
        self.release = release
        self.say = say
        self.peers = tuple(peers)
        self.url = url
        self.session = None
        self.message = ""
        self._lock = threading.Lock()
        self.server = None

    # -- the session ---------------------------------------------------------

    def begin(self, name, width, height):
        with self._lock:
            if self.session is not None:
                return "busy"
            missing = tools_missing()
            if missing:
                self.message = "missing: " + ", ".join(missing)
                return self.message
            panel = self.panels.get(name)
            if panel is None:
                return "no such screen"
            if not panel.get("profile"):
                self.message = "keep_profile"
                return self.message
            browser = pick_browser(panel.get("browser"))
            if browser is None:
                self.message = "no browser"
                return self.message
            self.say(f"[{name}] signing in from a telephone: this screen's "
                     "picture stops until Done is pressed")
            self.hold(name)
            if not wait_profile_free(panel["profile"]):
                self.say(f"[{name}] its browser would not let go of the "
                         "profile -- signing in abandoned")
                self.release(name)
                return "profile busy"
            os.makedirs(panel["profile"], exist_ok=True)
            session = Session(name, panel["profile"], browser,
                              max(500, min(1600, width)),
                              max(600, min(1400, height)),
                              panel.get("locale") or "", self.url)
            try:
                session.start()
            except Exception as err:  # noqa: BLE001 - the panel comes back
                session.stop()
                self.release(name)
                self.message = f"could not start: {err}"
                self.say(f"[{name}] signing in could not start ({err})")
                return self.message
            self.session = session
            self.message = ""
            threading.Thread(target=self._watch, args=(session,), daemon=True,
                             name="signin-watch").start()
            return "ok"

    def finish(self, why="Done"):
        with self._lock:
            session, self.session = self.session, None
        if session is None:
            return
        session.stop()
        self.release(session.name)
        self.say(f"[{session.name}] signing in finished ({why}); the screen "
                 "starts again with what was signed into")

    def _watch(self, session):
        """Ends a session nobody finished: a closed browser, or too long."""
        while self.session is session:
            if not session.browser_running():
                self.finish("the browser was closed")
                return
            if time.monotonic() - session.started > SESSION_LIMIT_S:
                self.finish(f"{SESSION_LIMIT_S // 60} minutes went by")
                return
            time.sleep(1)

    # -- the server ----------------------------------------------------------

    def serve(self, port, host="0.0.0.0"):
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                owner._handle(self.request, self.client_address[0])

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True,
                         name="signin").start()
        return self.server.server_address[1]

    def _handle(self, conn, peer):
        if peer not in self.peers:
            conn.close()
            return
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _version = lines[0].split(" ", 2)
        except ValueError:
            conn.close()
            return
        headers = {}
        for line in lines[1:]:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        path, _, query = target.partition("?")

        if path.startswith("/vnc/"):
            self._proxy(conn, lines, path[len("/vnc"):], query, rest)
            return
        try:
            length = int(headers.get("content-length") or 0)
            while len(rest) < length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                rest += chunk
            form = urllib.parse.parse_qs(rest[:length].decode(errors="replace"))
            if method == "POST" and path == "/start":
                def number(key, default):
                    try:
                        return int(float(form.get(key, [default])[0]))
                    except ValueError:
                        return default
                answer = self.begin(form.get("panel", [""])[0],
                                    number("w", 1024), number("h", 800))
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": answer}))
            elif method == "POST" and path == "/nav":
                session = self.session
                ok = session is not None and session.navigate(
                    form.get("what", [""])[0])
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": "ok" if ok else "no"}))
            elif method == "POST" and path == "/stop":
                threading.Thread(target=self.finish, daemon=True).start()
                self._reply(conn, 200, "application/json", '{"answer": "ok"}')
            elif method == "GET" and path == "/state":
                session = self.session
                self._reply(conn, 200, "application/json", json.dumps({
                    "panel": session.name if session else None,
                    "message": self.message}))
            elif method == "GET" and path in ("/", ""):
                prefix = headers.get("x-ingress-path", "")
                self._reply(conn, 200, "text/html; charset=utf-8",
                            self.page(prefix))
            else:
                self._reply(conn, 404, "text/plain", "not found")
        finally:
            conn.close()

    @staticmethod
    def _reply(conn, status, kind, body):
        body = body.encode() if isinstance(body, str) else body
        reason = {200: "OK", 404: "Not Found"}.get(status, "OK")
        conn.sendall((f"HTTP/1.1 {status} {reason}\r\nContent-Type: {kind}\r\n"
                      f"Content-Length: {len(body)}\r\nCache-Control: no-store"
                      "\r\nConnection: close\r\n\r\n").encode() + body)

    def _proxy(self, conn, lines, path, query, rest):
        """noVNC's files and its websocket, handed through to websockify.

        Byte for byte after the request line, so a websocket upgrade needs
        nothing special: once the request is across, both directions are just
        piped until either side closes.
        """
        session = self.session
        if session is None or session.web_port is None:
            self._reply(conn, 404, "text/plain", "no session")
            conn.close()
            return
        try:
            upstream = socket.create_connection(("127.0.0.1", session.web_port),
                                                timeout=5)
        except OSError:
            self._reply(conn, 404, "text/plain", "no session")
            conn.close()
            return
        upstream.settimeout(None)
        method, _target, version = lines[0].split(" ", 2)
        first = f"{method} {path}{'?' + query if query else ''} {version}"
        # One request per connection, unless it is the websocket. A browser
        # keeps a connection open and sends its NEXT request down it, which
        # would reach websockify still carrying /vnc and come back 404 --
        # seen as half of noVNC's files missing and nothing ever drawn.
        headers = lines[1:]
        if not any(h.lower().startswith("upgrade:") for h in headers):
            headers = [h for h in headers
                       if not h.lower().startswith("connection:")]
            headers.append("Connection: close")
        upstream.sendall(("\r\n".join([first] + headers) + "\r\n\r\n")
                         .encode("latin-1") + rest)

        def pipe(source, sink):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    sink.sendall(data)
            except OSError:
                pass
            for end in (source, sink):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        other = threading.Thread(target=pipe, args=(upstream, conn),
                                 daemon=True)
        other.start()
        pipe(conn, upstream)
        other.join(5)
        upstream.close()
        conn.close()

    def page(self, prefix=""):
        session = self.session
        rows = []
        for name, panel in self.panels.items():
            label = html.escape(name)
            if not panel.get("profile"):
                rows.append(f'<li><button disabled>{label}</button>'
                            f'<small data-k="noprofile"></small></li>')
            else:
                rows.append(f'<li><button data-panel="{label}">{label}'
                            f'</button></li>')
        missing = tools_missing()
        return PAGE % {
            "rows": "".join(rows),
            "missing": html.escape(", ".join(missing)),
            "active": html.escape(session.name) if session else "",
            "prefix": html.escape(prefix.strip("/")),
            "message": html.escape(self.message),
        }


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portall - sign in</title>
<style>
 :root { --ground: #f4f6fa; --ink: #161b26; --faint: #5b6475; --card: #fff;
         --edge: #d8dde6; --accent: #2f6fdf; }
 @media (prefers-color-scheme: dark) {
   :root { --ground: #0b0e14; --ink: #e8ecf4; --faint: #99a3b5;
           --card: #161b26; --edge: #2a3140; --accent: #5b8ff0; } }
 * { box-sizing: border-box; }
 html, body { margin: 0; height: 100%%; }
 body { background: var(--ground); color: var(--ink);
        font: 16px/1.45 system-ui, sans-serif; padding: 16px; }
 main { max-width: 40rem; margin: 0 auto; }
 h1 { font-size: 1.3rem; margin: 0 0 .4rem; }
 p { color: var(--faint); margin: .3rem 0 1rem; }
 ul { list-style: none; padding: 0; display: grid; gap: .6rem; }
 button { font: inherit; width: 100%%; padding: .9rem 1rem; border-radius: 12px;
          border: 1px solid var(--edge); background: var(--card);
          color: var(--ink); text-align: left; cursor: pointer; }
 button[data-panel]::after { content: " \\2192"; color: var(--accent); }
 button:disabled { opacity: .55; }
 small { display: block; color: var(--faint); margin: .2rem .2rem 0; }
 .bar { display: flex; gap: .5rem; align-items: center; margin-bottom: .6rem; }
 .bar button { width: auto; padding: .7rem .9rem; }
 .bar .nav { font-size: 1.15rem; line-height: 1; min-width: 3rem;
             text-align: center; }
 .bar .gap { flex: 1; }
 #done { background: var(--accent); color: #fff; border: 0; font-weight: 600; }
 #live > p { margin: 0 0 .5rem; font-size: .9rem; }
 iframe { width: 100%%; height: calc(100vh - 150px); border: 1px solid var(--edge);
          border-radius: 12px; background: #000; }
 .warn { color: #c2410c; }
</style></head>
<body><main id="m"
 data-active="%(active)s" data-prefix="%(prefix)s"
 data-missing="%(missing)s" data-message="%(message)s">
 <div id="pick">
  <h1 data-k="title"></h1>
  <p data-k="intro"></p>
  <p class="warn" id="warn"></p>
  <ul>%(rows)s</ul>
 </div>
 <div id="live" hidden>
  <div class="bar">
   <button class="nav" data-nav="back" data-t="back">&#8592;</button>
   <button class="nav" data-nav="reload" data-t="reload">&#10227;</button>
   <button class="nav" data-nav="home" data-t="home">&#8962;</button>
   <span class="gap"></span>
   <button id="done" data-k="done"></button>
  </div>
  <p data-k="live"></p>
  <iframe id="screen" allow="clipboard-read; clipboard-write"></iframe>
 </div>
</main>
<script>
(function () {
  var fr = (navigator.language || '').toLowerCase().indexOf('fr') === 0;
  var T = fr ? {
    title: "Se connecter depuis un \\u00e9cran",
    intro: "Choisissez l'\\u00e9cran. Un vrai Chrome s'ouvre ici sur son profil, avec une barre d'adresse : connectez-vous \\u00e0 Google ou \\u00e0 n'importe quel site avec le clavier de ce t\\u00e9l\\u00e9phone, puis appuyez sur Termin\\u00e9. L'\\u00e9cran s'arr\\u00eate pendant ce temps et revient connect\\u00e9.",
    live: "Connect\\u00e9 au profil de l'\\u00e9cran. Le clavier : bouton \\u2328 dans le menu \\u00e0 gauche.",
    done: "Termin\\u00e9",
    back: "Page pr\\u00e9c\\u00e9dente",
    reload: "Actualiser",
    home: "Accueil (page de connexion Google)",
    noprofile: "keep_profile est d\\u00e9sactiv\\u00e9 pour cet \\u00e9cran : il n'a pas de profil o\\u00f9 garder une connexion.",
    missing: "Il manque \\u00e0 l'add-on : ",
    busy: "Une connexion est d\\u00e9j\\u00e0 en cours.",
    failed: "Impossible de d\\u00e9marrer : "
  } : {
    title: "Sign in from a screen",
    intro: "Pick the screen. A real Chrome opens here on its profile, with an address bar: sign into Google or any site with this phone's keyboard, then press Done. The screen stops meanwhile and comes back signed in.",
    live: "On the screen's profile. The keyboard: the \\u2328 button in the menu on the left.",
    done: "Done",
    back: "Back",
    reload: "Reload",
    home: "Home (Google's sign-in page)",
    noprofile: "keep_profile is off for this screen: it has no profile to keep a sign-in in.",
    missing: "The add-on is missing: ",
    busy: "A sign-in is already running.",
    failed: "Could not start: "
  };
  var m = document.getElementById('m');
  document.querySelectorAll('[data-k]').forEach(function (e) {
    e.textContent = T[e.getAttribute('data-k')] || '';
  });
  document.querySelectorAll('[data-t]').forEach(function (e) {
    e.title = T[e.getAttribute('data-t')] || '';
    e.setAttribute('aria-label', e.title);
  });
  var warn = document.getElementById('warn');
  if (m.dataset.missing) warn.textContent = T.missing + m.dataset.missing;
  else if (m.dataset.message) warn.textContent = T.failed + m.dataset.message;

  // Relative to wherever Home Assistant has put this page: its ingress path
  // is a token in the URL, and noVNC builds its websocket address from a
  // path of its own, which has to carry that token too.
  var base = location.pathname.replace(/[^\\/]*$/, '');
  var wsPath = (m.dataset.prefix ? m.dataset.prefix + '/' : base.replace(/^\\//, ''))
               + 'vnc/websockify';
  function show() {
    document.getElementById('pick').hidden = true;
    document.getElementById('live').hidden = false;
    document.getElementById('screen').src = 'vnc/vnc.html?autoconnect=1'
      + '&resize=scale&reconnect=1&show_dot=1&path=' + encodeURIComponent(wsPath);
  }
  if (m.dataset.active) show();

  function post(what, body) {
    return fetch(what, {method: 'POST', body: body,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
      .then(function (r) { return r.json(); });
  }
  document.querySelectorAll('button[data-panel]').forEach(function (b) {
    b.addEventListener('click', function () {
      var w = Math.round(window.innerWidth * Math.min(window.devicePixelRatio || 1, 1.5));
      var h = Math.round((window.innerHeight - 150) * Math.min(window.devicePixelRatio || 1, 1.5));
      b.disabled = true;
      post('start', 'panel=' + encodeURIComponent(b.dataset.panel)
           + '&w=' + w + '&h=' + h).then(function (r) {
        if (r.answer === 'ok') { show(); return; }
        b.disabled = false;
        warn.textContent = r.answer === 'busy' ? T.busy
          : r.answer === 'keep_profile' ? T.noprofile : T.failed + r.answer;
      });
    });
  });
  document.querySelectorAll('button[data-nav]').forEach(function (b) {
    b.addEventListener('click', function () {
      post('nav', 'what=' + b.dataset.nav);
    });
  });
  document.getElementById('done').addEventListener('click', function () {
    post('stop', '').then(function () { location.reload(); });
  });
})();
</script></body></html>
"""
