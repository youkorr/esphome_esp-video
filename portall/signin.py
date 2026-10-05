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

The browser is an APP window (--app) at the telephone's own size and pixel
density, because an ordinary Chrome window is never narrower than 500 points:
on a telephone 390 points wide noVNC then shrank it to three quarters, and the
household had to zoom to read it. An app window has no address bar, so the
page carries one, with back, reload and home; another address is the same
browser closed and opened again on it.

A screen already signed into Google says so and opens nothing: its cookie jar
is read for Google's session cookie, and "Se deconnecter" removes Google's
and YouTube's cookies from the profile while its browser is stopped.

Two rules this file lives under, both the add-on's own:

- An accessory must never cost the picture. A missing Xvfb, x11vnc, websockify
  or noVNC turns the page into a sentence saying which, and the panels do not
  notice. Only the panel being signed in is stopped, and only for as long as
  the session lasts -- SESSION_LIMIT_S at most, and UNWATCHED_S once nobody
  has the page open.
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
import sqlite3
import subprocess
import tempfile
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

# A session whose page nobody has open ends after this. The panel's picture
# stops for as long as a session lasts, so a telephone put away without Done
# froze the screen for the whole of SESSION_LIMIT_S -- reported as a panel
# that "bloque tout" until the add-on was restarted. The page says it is
# there every ALIVE_S; a page closed, or an app sent to the background (its
# timers stop), says nothing. Three minutes is room for Google's second step
# in the Google app and back, which the session is deliberately kept for.
UNWATCHED_S = 3 * 60
ALIVE_S = 20

# What the browser opens on. Google, because that is the one that refuses the
# driven browser -- the address bar is right there for any other.
START_URL = "https://accounts.google.com/"

TOOLS = ("Xvfb", "x11vnc", "websockify", "xdotool")

# The page a session opens on when it is not to sign into Google: nothing at
# all, so the address the telephone types is the first page shown.
BLANK_URL = "about:blank"

# Signed into Google means Google's own session cookie, on google.<country>,
# not run out. Chrome keeps time in microseconds since 1601.
GOOGLE_SESSION = ("SID", "__Secure-1PSID", "__Secure-3PSID")
GOOGLE_HOST = re.compile(r"(^|\.)google\.[a-z]{2,3}(\.[a-z]{2})?$")
YOUTUBE_HOST = re.compile(r"(^|\.)youtube\.com$")
CHROME_EPOCH_S = 11644473600


def cookie_jar(profile):
    """The profile's cookie file, where this Chrome keeps it, or None."""
    if not profile:
        return None
    for parts in (("Default", "Network", "Cookies"), ("Default", "Cookies")):
        path = os.path.join(profile, *parts)
        if os.path.isfile(path):
            return path
    return None


def google_signed_in(profile):
    """True or False, or None when there is no jar to read yet.

    Read from a COPY: the panel's own browser keeps the jar open while it
    runs, and copying a file asks nothing of it. The values are encrypted and
    not needed -- the name, the host and the expiry are plain.
    """
    jar = cookie_jar(profile)
    if jar is None:
        return None
    with tempfile.TemporaryDirectory() as work:
        copy = os.path.join(work, "Cookies")
        try:
            shutil.copyfile(jar, copy)
            for tail in ("-journal", "-wal"):
                if os.path.exists(jar + tail):
                    shutil.copyfile(jar + tail, copy + tail)
            db = sqlite3.connect(copy)
            try:
                rows = db.execute(
                    "SELECT host_key, expires_utc FROM cookies WHERE name IN "
                    "(?, ?, ?)", GOOGLE_SESSION).fetchall()
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return None
    now = (time.time() + CHROME_EPOCH_S) * 1e6
    return any(GOOGLE_HOST.search(host or "") and (not until or until > now)
               for host, until in rows)


def forget_google(profile):
    """Remove Google's and YouTube's cookies from a profile nobody has open.

    Returns how many went. Only ever with the panel's browser stopped: Chrome
    writes its jar back from memory, and would put them back.
    """
    jar = cookie_jar(profile)
    if jar is None:
        return 0
    db = sqlite3.connect(jar)
    try:
        hosts = [h for (h,) in db.execute("SELECT DISTINCT host_key FROM cookies")
                 if GOOGLE_HOST.search(h or "") or YOUTUBE_HOST.search(h or "")]
        gone = 0
        for host in hosts:
            gone += db.execute("DELETE FROM cookies WHERE host_key = ?",
                               (host,)).rowcount
        db.commit()
    finally:
        db.close()
    return gone


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
                 url=START_URL, scale=1.0):
        self.name = name
        self.profile = profile
        self.browser = browser
        # The window in points, as the telephone lays its own page out, and
        # the telephone's pixels per point: the screen is drawn at that many
        # pixels, so noVNC shows it one pixel for one and nothing is shrunk.
        self.width = width
        self.height = height
        self.scale = scale
        self.locale = locale
        self.url = url
        self.started = time.monotonic()
        # When the page last said it was open (see UNWATCHED_S).
        self.seen_at = self.started
        self.web_port = None
        self._processes = []
        self._browser = None
        self._env = None
        # While go() closes the browser to open it again, the watcher must
        # not read the gap as somebody having closed it.
        self.reopening = False
        self.display = None

    def browser_command(self, url=None):
        """The plain browser's command line -- and what it must NOT carry.

        Nothing here drives the browser, which is the whole point, so no
        --remote-debugging-*, no --enable-automation and no Playwright.
        --password-store=basic is what the driven browser uses too (Playwright
        passes it by default): cookies are encrypted with a key that depends
        on it, and a mismatch would copy the session across and decrypt it to
        nothing. --no-sandbox because a container runs as root, where Chrome
        will not start without it -- Playwright passes that one too.

        --app because an ordinary window is never narrower than 500 points,
        measured: given 390 it opens at 500 and runs off a screen drawn for a
        telephone. An app window takes the size it is given.
        """
        argv = [self.browser, f"--user-data-dir={os.path.abspath(self.profile)}",
                "--password-store=basic", "--no-sandbox",
                "--no-first-run", "--no-default-browser-check",
                "--disable-dev-shm-usage", "--disable-gpu",
                f"--force-device-scale-factor={self.scale:g}",
                "--window-position=0,0",
                f"--window-size={self.width},{self.height}"]
        if self.locale:
            argv.append(f"--lang={self.locale}")
        argv.append(f"--app={url or self.url}")
        return argv

    def screen_size(self):
        """The virtual screen in pixels: the window at the telephone's
        density, and never smaller than the window itself."""
        return (max(self.width, round(self.width * self.scale)),
                max(self.height, round(self.height * self.scale)))

    def start(self):
        display = self.display = free_display()
        if display is None:
            raise RuntimeError("no free X display number")
        env = self._env = dict(os.environ, DISPLAY=f":{display}")
        vnc_port, self.web_port = free_port(), free_port()
        wide, high = self.screen_size()
        self._spawn(["Xvfb", f":{display}", "-screen", "0",
                     f"{wide}x{high}x24", "-nolisten", "tcp"])
        if not _wait_for(lambda: os.path.exists(f"/tmp/.X11-unix/X{display}"),
                         10):
            raise RuntimeError("the virtual screen did not start")
        self._browser = self._spawn(self.browser_command(), env=env)
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

        An app window has no bar of its own, so the page carries these as
        buttons. Home is the page the session started on.
        """
        if what == "back":
            return self.keys(("key", "alt+Left"))
        if what == "reload":
            return self.keys(("key", "F5"))
        if what == "home":
            return self.go(self.url)
        return False

    def go(self, url):
        """Another address. An app window has no address bar, so the browser
        is closed the way a person closes it -- its cookies written out --
        and opened again on the address: a second or two."""
        if self._env is None:
            return False
        self.reopening = True
        try:
            if not self.quit_browser():
                return False
            wait_profile_free(self.profile, 5)
            self._browser = self._spawn(self.browser_command(url),
                                        env=self._env)
            return True
        finally:
            self.reopening = False

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

    def begin(self, name, width, height, scale=1.0, url=None):
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
                              max(280, min(1600, width)),
                              max(300, min(2000, height)),
                              panel.get("locale") or "", url or self.url,
                              max(1.0, min(3.0, scale)))
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
            if not session.browser_running() and not session.reopening:
                self.finish("the browser was closed")
                return
            if time.monotonic() - session.started > SESSION_LIMIT_S:
                self.finish(f"{SESSION_LIMIT_S // 60} minutes went by")
                return
            if time.monotonic() - session.seen_at > UNWATCHED_S:
                self.finish(f"nobody had the page open for "
                            f"{UNWATCHED_S // 60} minutes")
                return
            time.sleep(1)

    # -- Google ----------------------------------------------------------------

    def google(self, name):
        """True, False or None (no profile, or nothing to read yet)."""
        return google_signed_in((self.panels.get(name) or {}).get("profile"))

    def sign_out(self, name):
        """Sign a screen out of Google: its browser stopped, Google's and
        YouTube's cookies removed from its profile, the screen started again.
        """
        with self._lock:
            if self.session is not None:
                return "busy"
            profile = (self.panels.get(name) or {}).get("profile")
            if not profile:
                return "no such screen"
            self.hold(name)
            try:
                if not wait_profile_free(profile):
                    return "profile busy"
                gone = forget_google(profile)
            except (OSError, sqlite3.Error) as err:
                self.say(f"[{name}] could not sign out of Google ({err})")
                return f"could not: {err}"
            finally:
                self.release(name)
        self.say(f"[{name}] signed out of Google ({gone} cookies removed)")
        return "ok"

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
                try:
                    scale = float(form.get("s", ["1"])[0])
                except ValueError:
                    scale = 1.0
                want = form.get("url", [""])[0]
                answer = self.begin(form.get("panel", [""])[0],
                                    number("w", 1024), number("h", 800),
                                    scale, address(want) if want else None)
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": answer}))
            elif method == "POST" and path == "/alive":
                session = self.session
                if session is not None:
                    session.seen_at = time.monotonic()
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": "ok" if session else "no"}))
            elif method == "POST" and path == "/nav":
                session = self.session
                ok = session is not None and session.navigate(
                    form.get("what", [""])[0])
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": "ok" if ok else "no"}))
            elif method == "POST" and path == "/go":
                session = self.session
                want = address(form.get("url", [""])[0])
                ok = session is not None and bool(want) and session.go(want)
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": "ok" if ok else "no"}))
            elif method == "POST" and path == "/signout":
                answer = self.sign_out(form.get("panel", [""])[0])
                self._reply(conn, 200, "application/json",
                            json.dumps({"answer": answer}))
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
                rows.append(f'<li class="screen"><div class="who"><b>{label}'
                            f'</b><small data-k="noprofile"></small></div></li>')
                continue
            # Signed in: the screen says so and opens nothing. Only a screen
            # that is not offers Google's sign-in page.
            state = "on" if self.google(name) is True else "off"
            main = (f'<button class="ghost" data-out="{label}" '
                    f'data-k="signout"></button>' if state == "on" else
                    f'<button class="go" data-panel="{label}" '
                    f'data-k="signin"></button>')
            rows.append(
                f'<li class="screen"><div class="who"><b>{label}</b>'
                f'<small class="state {state}" data-k="{state}"></small></div>'
                f'{main}<button class="link" data-panel="{label}" '
                f'data-url="{BLANK_URL}" data-k="other"></button></li>')
        missing = tools_missing()
        return PAGE % {
            "rows": "".join(rows),
            "missing": html.escape(", ".join(missing)),
            "active": html.escape(session.name) if session else "",
            "prefix": html.escape(prefix.strip("/")),
            "message": html.escape(self.message),
            "alive_ms": ALIVE_S * 1000,
        }


def address(text):
    """What somebody typed into the page's address field, as an address.

    http and https only -- a page this one opens must not be handed a file:
    or a javascript: -- and a bare name is taken to be a site, the way a
    browser's own bar takes it: https for a name with a dot in it, http for
    an address on the house's network (an IP, a port, a name with no dot),
    which is where Jellyfin and the like answer and seldom in https.
    """
    text = str(text or "").strip()
    if not text or text == BLANK_URL:
        return text
    if re.match(r"^https?://", text, re.I):
        return text
    if re.match(r"^(javascript|file|data|chrome|about|view-source|blob):",
                text, re.I):
        return ""
    host = re.split(r"[/?#]", text, 1)[0]
    local = (re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", host)
             or ":" in host or "." not in host)
    return ("http://" if local else "https://") + text


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portall - sign in</title>
<style>
 :root { --ground: #f4f6fa; --ink: #161b26; --faint: #5b6475; --card: #fff;
         --edge: #d8dde6; --accent: #2f6fdf; --ok: #15803d; }
 @media (prefers-color-scheme: dark) {
   :root { --ground: #0b0e14; --ink: #e8ecf4; --faint: #99a3b5;
           --card: #161b26; --edge: #2a3140; --accent: #5b8ff0;
           --ok: #4ade80; } }
 * { box-sizing: border-box; }
 html, body { margin: 0; height: 100%%; }
 body { background: var(--ground); color: var(--ink);
        font: 16px/1.45 system-ui, sans-serif; padding: 16px; }
 main { max-width: 40rem; margin: 0 auto; }
 h1 { font-size: 1.3rem; margin: 0 0 .4rem; }
 p { color: var(--faint); margin: .3rem 0 1rem; }
 ul { list-style: none; padding: 0; display: grid; gap: .7rem; }
 button { font: inherit; padding: .8rem 1rem; border-radius: 12px;
          border: 1px solid var(--edge); background: var(--card);
          color: var(--ink); cursor: pointer; }
 button:disabled { opacity: .55; }
 small { display: block; color: var(--faint); }
 .screen { display: grid; gap: .5rem; padding: .9rem 1rem; border-radius: 14px;
           border: 1px solid var(--edge); background: var(--card); }
 .who b { font-size: 1.05rem; }
 .state.on { color: var(--ok); font-weight: 600; }
 .screen .go { background: var(--accent); color: #fff; border: 0;
               font-weight: 600; }
 .screen .link { border: 0; background: none; color: var(--accent);
                 padding: .2rem 0; text-align: left; font-size: .9rem; }
 .bar { display: flex; gap: .5rem; align-items: center; }
 .bar button { padding: .7rem .9rem; }
 .bar .nav { font-size: 1.15rem; line-height: 1; min-width: 3rem;
             text-align: center; }
 .bar .gap { flex: 1; }
 #done { background: var(--accent); color: #fff; border: 0; font-weight: 600; }
 #live { display: flex; flex-direction: column; gap: .5rem;
         height: calc(100vh - 32px); height: calc(100dvh - 32px); }
 #live[hidden] { display: none; }
 .addr { display: flex; gap: .5rem; }
 .addr input { flex: 1; min-width: 0; font: inherit; padding: .7rem .8rem;
               border-radius: 12px; border: 1px solid var(--edge);
               background: var(--card); color: var(--ink); }
 #live > p { margin: 0; font-size: .85rem; }
 iframe { flex: 1; min-height: 0; width: 100%%; border: 1px solid var(--edge);
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
  <form class="addr" id="addr"><input id="url" type="text" inputmode="url"
   autocomplete="off" autocapitalize="off" spellcheck="false"
   enterkeyhint="go"><button data-k="go"></button></form>
  <iframe id="screen" allow="clipboard-read; clipboard-write"></iframe>
  <p data-k="live"></p>
 </div>
</main>
<script>
(function () {
  var fr = (navigator.language || '').toLowerCase().indexOf('fr') === 0;
  var T = fr ? {
    title: "Comptes des \\u00e9crans",
    intro: "Chaque \\u00e9cran garde sa connexion. Pour se connecter, un vrai Chrome s'ouvre ici \\u00e0 la taille de ce t\\u00e9l\\u00e9phone : tapez avec son clavier, puis appuyez sur Termin\\u00e9. L'\\u00e9cran s'arr\\u00eate pendant ce temps et revient connect\\u00e9.",
    on: "\\u25cf Connect\\u00e9 \\u00e0 Google",
    off: "Pas connect\\u00e9 \\u00e0 Google",
    signin: "Se connecter \\u00e0 Google",
    signout: "Se d\\u00e9connecter de Google",
    sure: "Appuyez encore pour vous d\\u00e9connecter",
    working: "Un instant\\u2026",
    other: "Se connecter \\u00e0 un autre site\\u2026",
    live: "Le clavier : bouton \\u2328 dans le menu \\u00e0 gauche.",
    url: "Adresse d'un site",
    go: "Aller",
    done: "Termin\\u00e9",
    back: "Page pr\\u00e9c\\u00e9dente",
    reload: "Actualiser",
    home: "Page de d\\u00e9part",
    noprofile: "keep_profile est d\\u00e9sactiv\\u00e9 pour cet \\u00e9cran : il n'a pas de profil o\\u00f9 garder une connexion.",
    missing: "Il manque \\u00e0 l'add-on : ",
    busy: "Une connexion est d\\u00e9j\\u00e0 en cours.",
    failed: "Impossible : "
  } : {
    title: "Screens' accounts",
    intro: "Each screen keeps its own sign-in. To sign in, a real Chrome opens here at this phone's size: type with its keyboard, then press Done. The screen stops meanwhile and comes back signed in.",
    on: "\\u25cf Signed into Google",
    off: "Not signed into Google",
    signin: "Sign into Google",
    signout: "Sign out of Google",
    sure: "Tap again to sign out",
    working: "One moment\\u2026",
    other: "Sign into another site\\u2026",
    live: "The keyboard: the \\u2328 button in the menu on the left.",
    url: "A site's address",
    go: "Go",
    done: "Done",
    back: "Back",
    reload: "Reload",
    home: "Start page",
    noprofile: "keep_profile is off for this screen: it has no profile to keep a sign-in in.",
    missing: "The add-on is missing: ",
    busy: "A sign-in is already running.",
    failed: "Could not: "
  };
  var m = document.getElementById('m');
  document.querySelectorAll('[data-k]').forEach(function (e) {
    e.textContent = T[e.getAttribute('data-k')] || '';
  });
  document.querySelectorAll('[data-t]').forEach(function (e) {
    e.title = T[e.getAttribute('data-t')] || '';
    e.setAttribute('aria-label', e.title);
  });
  document.getElementById('url').placeholder = T.url;
  var warn = document.getElementById('warn');
  if (m.dataset.missing) warn.textContent = T.missing + m.dataset.missing;
  else if (m.dataset.message) warn.textContent = T.failed + m.dataset.message;

  // Relative to wherever Home Assistant has put this page: its ingress path
  // is a token in the URL, and noVNC builds its websocket address from a
  // path of its own, which has to carry that token too.
  var base = location.pathname.replace(/[^\\/]*$/, '');
  var wsPath = (m.dataset.prefix ? m.dataset.prefix + '/' : base.replace(/^\\//, ''))
               + 'vnc/websockify';
  var pick = document.getElementById('pick'), live = document.getElementById('live');
  var screen = document.getElementById('screen');
  // While the screen is shown here, say so -- a page closed or put in the
  // background stops saying it, and the session then ends by itself rather
  // than leaving the panel's picture stopped (UNWATCHED_S).
  var alive = null;
  function open() {
    pick.hidden = true; live.hidden = false;
    if (!alive) alive = setInterval(function () { post('alive', ''); },
                                    %(alive_ms)d);
  }
  document.addEventListener('visibilitychange', function () {
    if (alive && !document.hidden) post('alive', '');
  });
  function connect() {
    screen.src = 'vnc/vnc.html?autoconnect=1&resize=scale&reconnect=1'
      + '&show_dot=1&path=' + encodeURIComponent(wsPath);
  }
  if (m.dataset.active) { open(); connect(); }

  function post(what, body) {
    return fetch(what, {method: 'POST', body: body,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
      .then(function (r) { return r.json(); });
  }
  // The browser is opened at the size of the frame it will be shown in,
  // in points, and at this phone's pixels per point -- so noVNC shows it
  // one pixel for one, the size this phone draws its own pages.
  document.querySelectorAll('button[data-panel]').forEach(function (b) {
    b.addEventListener('click', function () {
      open();
      var r = screen.getBoundingClientRect();
      var s = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
      b.disabled = true;
      post('start', 'panel=' + encodeURIComponent(b.dataset.panel)
           + '&w=' + Math.floor(r.width - 2) + '&h=' + Math.floor(r.height - 2)
           + '&s=' + s.toFixed(2)
           + (b.dataset.url ? '&url=' + encodeURIComponent(b.dataset.url) : ''))
        .then(function (a) {
          if (a.answer === 'ok') { connect(); return; }
          live.hidden = true; pick.hidden = false;
          b.disabled = false;
          warn.textContent = a.answer === 'busy' ? T.busy
            : a.answer === 'keep_profile' ? T.noprofile : T.failed + a.answer;
        });
    });
  });
  document.querySelectorAll('button[data-out]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!b.dataset.sure) { b.dataset.sure = '1'; b.textContent = T.sure; return; }
      b.disabled = true;
      b.textContent = T.working;
      post('signout', 'panel=' + encodeURIComponent(b.dataset.out))
        .then(function (a) {
          if (a.answer !== 'ok') warn.textContent = T.failed + a.answer;
          location.reload();
        });
    });
  });
  document.querySelectorAll('button[data-nav]').forEach(function (b) {
    b.addEventListener('click', function () {
      post('nav', 'what=' + b.dataset.nav);
    });
  });
  document.getElementById('addr').addEventListener('submit', function (e) {
    e.preventDefault();
    var field = document.getElementById('url');
    if (!field.value.trim()) return;
    post('go', 'url=' + encodeURIComponent(field.value));
    field.blur();
  });
  document.getElementById('done').addEventListener('click', function () {
    post('stop', '').then(function () { location.reload(); });
  });
})();
</script></body></html>
"""
