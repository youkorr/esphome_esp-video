#!/usr/bin/env python3
"""Can a panel be signed into a site from a telephone, and does it stay?

WHY THIS EXISTS. Google refuses to sign in a browser that is being driven,
and a panel's browser always is. portall/signin.py opens a PLAIN browser on
the panel's own profile and shows it on a telephone through noVNC, so the
signing in happens in a browser nobody drives. What has to be true for that to
be worth anything, each one a case below:

  - the panel's own sender is stopped first and its profile is let go;
  - the browser shown carries no automation at all -- no debugging port, no
    --enable-automation, navigator.webdriver false;
  - the telephone sees it: noVNC connects THROUGH the add-on's page and draws
    the browser's picture;
  - the telephone types into it: keys pressed on noVNC reach a field;
  - Done closes it, the sender starts again, and the DRIVEN browser -- the
    sender's, with its own arguments -- presents the cookie the plain one was
    given. That last one is the whole point.

The sender is stood in for by a script that opens the profile the way the real
one does (Playwright, a persistent context, ha_send.BROWSER_ARGS) and reports
the cookies a page receives. Needs Playwright, Pillow, Xvfb, x11vnc,
websockify, xdotool and noVNC. Takes $CHROMIUM for the browser.

--signal stops the plain browser with SIGTERM alone, as the first version
did: the cookie it was given does not reach the disk (Chrome writes cookies
every thirty seconds) and the last case fails. That is the reproduction.
"""

import http.server
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))

fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


COLOUR = (200, 40, 120)
heard = {"typed": "", "webdriver": None, "cookies": [], "visits": 0,
         "shown": []}


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = urllib.parse.parse_qs(query)
        extra = []
        if path == "/set":
            body = ("<!doctype html><body style='margin:0;background:rgb(%d,%d,%d)'>"
                    "<input id=q autofocus style='font-size:40px;width:90%%;"
                    "margin:40px'><script>"
                    "fetch('/probe?w='+navigator.webdriver+'&iw='+innerWidth"
                    "+'&dpr='+devicePixelRatio);"
                    "addEventListener('pageshow',()=>fetch('/shown?p=set'));"
                    "q.addEventListener('input',()=>fetch('/typed?v='+"
                    "encodeURIComponent(q.value)));</script>" % COLOUR)
            # A new value on every visit, so the last one is what has to
            # come back: proof that the browser wrote its cookies out when it
            # was closed, rather than on its thirty-second timer.
            heard["sets"] = heard.get("sets", 0) + 1
            extra = [("Set-Cookie", "kept=yes; Max-Age=86400; Path=/"),
                     ("Set-Cookie", "last=%d; Max-Age=86400; Path=/"
                      % heard["sets"]),
                     ("Set-Cookie", "session=yes; Path=/")]
        elif path == "/hop":
            # A link to /other, focused, so a key opens it: a page with
            # something behind it to go back to, inside one browser. Opened
            # by a person's key and not by a script, because Chrome's back
            # skips a page that moved on by itself.
            body = ("<!doctype html><body><a id=a href=/other autofocus>"
                    "other</a><script>addEventListener('pageshow',"
                    "()=>fetch('/shown?p=hop'));</script>")
        elif path == "/other":
            body = ("<!doctype html><body><script>addEventListener('pageshow',"
                    "()=>fetch('/shown?p=other'));</script>")
        elif path == "/shown":
            heard["shown"].append(q.get("p", [""])[0])
            body = "ok"
        elif path == "/probe":
            heard["webdriver"] = q.get("w", [""])[0]
            heard["inner"] = (q.get("iw", [""])[0], q.get("dpr", [""])[0])
            body = "ok"
        elif path == "/typed":
            heard["typed"] = q.get("v", [""])[0]
            body = "ok"
        elif path == "/check":
            heard["visits"] += 1
            heard["cookies"].append(self.headers.get("Cookie") or "")
            body = "ok"
        else:
            body = "?"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


FAKE_SENDER = r'''
import sys, time
sys.path.insert(0, sys.argv[3])
from ha_send import BROWSER_ARGS, forget_tabs
from playwright.sync_api import sync_playwright
profile, site, browser = sys.argv[1], sys.argv[2], sys.argv[4]
# As the real sender does before every start: --restore-last-session would
# otherwise bring back the tabs of the run before.
forget_tabs(profile)
with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(profile, args=BROWSER_ARGS,
                                                headless=True,
                                                executable_path=browser)
    ctx.pages[0].goto(site + "/check")
    print("fake sender up", flush=True)
    while True:
        time.sleep(0.5)
'''


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    import run
    import signin
    quits = []
    if "--signal" in sys.argv:
        # The first version: SIGTERM alone, which lost the cookie.
        signin.Session.quit_browser = lambda self: quits.append(False) or False
    else:
        real_quit = signin.Session.quit_browser

        def recorded(self):
            began = time.monotonic()
            quits.append((real_quit(self), time.monotonic() - began))
            return quits[-1][0]
        signin.Session.quit_browser = recorded
    missing = signin.tools_missing()
    if missing:
        print("skipped: needs " + ", ".join(missing))
        return 0
    from playwright.sync_api import sync_playwright

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"

    work = tempfile.mkdtemp()
    profile = os.path.join(work, "salon")
    fake = os.path.join(work, "fake_sender.py")
    pathlib.Path(fake).write_text(FAKE_SENDER)
    # The same browser the plain one will be, as it is on a real box: a
    # profile last written by a newer version is refused by an older one.
    browser = os.environ.get("CHROMIUM") or signin.playwrights_chromium()
    run.command_for = lambda panel: [
        sys.executable, "-u", fake, profile, site,
        str(HERE / "components" / "portall"), browser]
    run.say = lambda text: print("    | " + text)

    print("The panel's sender, before anything:")
    stop = threading.Event()
    thread = threading.Thread(target=run.serve,
                              args=({"name": "salon"}, "salon", stop),
                              daemon=True)
    thread.start()
    end = time.monotonic() + 30
    while time.monotonic() < end and not heard["visits"]:
        time.sleep(0.2)
    check("the stand-in sender opened the profile and a page",
          heard["visits"] == 1 and signin.processes_using(profile))

    page_url = None
    plain_argv = []
    screens = {"salon": {"profile": profile, "browser": browser,
                         "locale": "fr"},
               "cuisine": {"profile": None, "browser": browser, "locale": ""}}
    sign = signin.SignIn(screens, run.hold_panel, run.release_panel, run.say,
                         peers=("127.0.0.1",), url=site + "/set")
    port = sign.serve(0, host="127.0.0.1")
    page_url = f"http://127.0.0.1:{port}/"

    with sync_playwright() as pw:
        phone = pw.chromium.launch(executable_path=browser)
        # A telephone: 420 points wide at three pixels a point.
        tab = phone.new_page(viewport={"width": 420, "height": 860},
                             device_scale_factor=3)
        tab.goto(page_url)
        print("The page on the telephone:")
        check("each screen is offered", tab.locator(
            "button.go[data-panel='salon']").count() == 1)
        check("a screen with no profile cannot be chosen",
              tab.locator("button[data-panel='cuisine']").count() == 0)
        check("its downloads are not on this page any more",
              tab.locator("#files").count() == 0)
        check("a running browser's cookie jar can be read: not signed in",
              signin.google_signed_in(profile) is False,
              str(signin.cookie_jar(profile)))

        check("the page carries Home Assistant's ingress path for noVNC",
              'data-prefix="api/hassio_ingress/abc"'
              in sign.page("/api/hassio_ingress/abc"))

        print("Choosing the screen:")
        tab.click("button.go[data-panel='salon']")
        tab.wait_for_selector("#live:not([hidden])", timeout=30000)
        # The frame is shown first, so it can be measured; the session is
        # there once the start has been answered.
        end = time.monotonic() + 30
        while time.monotonic() < end and sign.session is None:
            time.sleep(0.2)
        time.sleep(0.5)
        check("the panel's own sender is stopped",
              run._current.get("salon") is None)
        session = sign.session
        check("a session is running on that profile",
              session is not None and session.browser_running())
        pids = signin.processes_using(profile)
        for pid in pids:
            plain_argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            break
        flat = b" ".join(plain_argv).decode(errors="replace")
        check("only the plain browser has the profile open", len(pids) == 1,
              " / ".join(open(f"/proc/{p}/cmdline", "rb").read()
                         .replace(b"\0", b" ")[:160].decode(errors="replace")
                         for p in pids))
        check("it is an app window at the telephone's own density",
              "--app=" in flat and "--force-device-scale-factor=3" in flat,
              flat)
        check("drawn on a screen of that many pixels",
              session.screen_size() == (round(session.width * 3),
                                        round(session.height * 3)),
              f"{session.screen_size()} for {session.width}x{session.height}")
        check("it carries no debugging port and no automation flag",
              "remote-debugging" not in flat and "enable-automation" not in flat,
              flat)
        end = time.monotonic() + 20
        while time.monotonic() < end and heard["webdriver"] is None:
            time.sleep(0.2)
        check("and the page it shows sees no automation",
              heard["webdriver"] == "false", str(heard["webdriver"]))
        frame_w = tab.evaluate("document.getElementById('screen')"
                               ".getBoundingClientRect().width")
        inner = heard.get("inner") or ("0", "0")
        check("the page is laid out as wide as the phone shows it, not 500",
              abs(float(inner[0] or 0) - (frame_w - 2)) <= 2
              and float(inner[1] or 0) == 3,
              f"{inner} in a frame {frame_w:.0f} wide")

        check("a second screen cannot start while one is signing in",
              sign.begin("salon", 800, 600) == "busy")

        print("What the telephone sees and types:")
        frame = tab.frame_locator("#screen")
        canvas = frame.locator("canvas").first
        canvas.wait_for(timeout=30000)
        colour = None
        end = time.monotonic() + 20
        while time.monotonic() < end:
            # The share of the picture in the page's own colour, rather than
            # one pixel: the browser draws its own bars, and a bubble of its
            # own may sit anywhere over the page.
            shot = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
            small = shot.resize((64, 64))
            pixels = [small.getpixel((x, y)) for x in range(64)
                      for y in range(64)]
            share = sum(all(abs(a - b) < 20 for a, b in zip(px, COLOUR))
                        for px in pixels) / len(pixels)
            colour = f"{share:.0%} of the picture"
            if share > 0.3:
                break
            time.sleep(0.5)
        check("noVNC draws the plain browser through the add-on's page",
              share > 0.3, colour)
        canvas.click(position={"x": 5, "y": 5})
        tab.keyboard.type("bonjour", delay=60)
        end = time.monotonic() + 10
        while time.monotonic() < end and heard["typed"] != "bonjour":
            time.sleep(0.2)
        check("keys typed on the telephone reach the field",
              heard["typed"] == "bonjour", repr(heard["typed"]))

        print("Back, reload and home, from the telephone's own buttons:")

        def after(action, want):
            """Run it, then the page the plain browser showed next."""
            before = len(heard["shown"])
            action()
            end = time.monotonic() + 15
            while time.monotonic() < end and len(heard["shown"]) == before:
                time.sleep(0.2)
            got = heard["shown"][before:]
            return got[-1] if got else None, got

        host = site.split("://", 1)[1]

        def typed(text):
            tab.fill("#url", text)
            tab.press("#url", "Enter")
        seen, got = after(lambda: typed(host + "/hop"), "hop")
        check("an address typed on the telephone opens in the browser",
              seen == "hop", str(got))
        time.sleep(0.5)
        seen, got = after(lambda: session.keys(("key", "Return")), "other")
        check("and its page goes on from there", seen == "other", str(got))
        check("and the session went on through it", sign.session is session
              and session.browser_running())
        seen, got = after(lambda: tab.click("button[data-nav='back']"), "hop")
        check("the back button returns to the page before", seen == "hop",
              str(got))
        seen, got = after(lambda: tab.click("button[data-nav='reload']"),
                          "hop")
        check("the reload button loads the page again", seen == "hop",
              str(got))
        seen, got = after(lambda: tab.click("button[data-nav='home']"), "set")
        check("the home button opens the page it started on", seen == "set",
              str(got))
        check("each has a label a screen reader and a long press can read",
              tab.locator("button[data-nav][aria-label='Page pr\u00e9c\u00e9dente']"
                          ).count() + tab.locator(
                  "button[data-nav][aria-label='Back']").count() == 1)

        print("Done:")
        visits = heard["visits"]
        written = heard.get("sets", 0)
        set_at = time.monotonic()
        tab.click("#done")
        end = time.monotonic() + 45
        while time.monotonic() < end and heard["visits"] == visits:
            time.sleep(0.2)
        check("the session is over", sign.session is None)
        check("the panel's sender starts again by itself",
              heard["visits"] == visits + 1, f"{visits} -> {heard['visits']}")
        last = heard["cookies"][-1] if heard["cookies"] else ""
        check("and the DRIVEN browser presents what the plain one was given",
              "kept=yes" in last, repr(last))
        check("Done closed the browser itself, not a signal after a wait",
              bool(quits) and quits[0] is not False and quits[0][0]
              and quits[0][1] < 5, str(quits))
        written = heard.get("sets", 0)
        check(f"even the cookie written last (visit {written}) -- so it was "
              f"written out when the browser closed",
              f"last={written}" in last, repr(last))
        print(f"    (a session cookie across it: "
              f"{'kept' if 'session=yes' in last else 'not kept'} -- Google's "
              f"sign-in cookies have an expiry, so this one is only noted)")
        phone.close()

    google_cases(signin, run)

    print("Nothing but the Supervisor may ask:")
    import socket
    guarded = signin.SignIn(screens, run.hold_panel, run.release_panel,
                            run.say, peers=("172.30.32.2",))
    guarded_port = guarded.serve(0, host="127.0.0.1")
    with socket.create_connection(("127.0.0.1", guarded_port), timeout=5) as c:
        c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        try:
            answer = c.recv(100)
        except ConnectionResetError:
            answer = b""
    check("a request from anywhere else gets no answer at all", answer == b"",
          repr(answer))

    stop.set()
    with run._running_lock:
        for process in run._running:
            process.kill()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


def google_cases(signin, run):
    """Signed into Google: said on the page, nothing opened, and undone.

    A cookie jar written the way Chrome's is (the columns that matter), with
    Google's session cookie on .google.com, one on .google.fr that has run
    out, YouTube's, and a site's own that must survive signing out.
    """
    import sqlite3
    print("Signed into Google:")
    work = tempfile.mkdtemp()
    profile = os.path.join(work, "salon")
    os.makedirs(os.path.join(profile, "Default", "Network"))
    jar = os.path.join(profile, "Default", "Network", "Cookies")
    db = sqlite3.connect(jar)
    db.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT,"
               " expires_utc INTEGER)")
    future = int((time.time() + signin.CHROME_EPOCH_S + 86400 * 365) * 1e6)
    past = int((time.time() + signin.CHROME_EPOCH_S - 86400) * 1e6)
    db.executemany("INSERT INTO cookies VALUES (?, ?, '', ?)", [
        (".google.com", "SID", future), (".google.fr", "SID", past),
        (".youtube.com", "LOGIN_INFO", future), ("jellyfin.local", "x", 0)])
    db.commit()
    db.close()
    check("a jar with Google's session cookie reads as signed in",
          signin.google_signed_in(profile) is True)
    held = []
    page = signin.SignIn({"salon": {"profile": profile, "browser": "x"}},
                         lambda n: held.append(("hold", n)),
                         lambda n: held.append(("release", n)),
                         lambda text: None, peers=("127.0.0.1",))
    html_page = page.page()
    check("the page says so, and offers to sign out rather than in",
          'class="state on"' in html_page and 'data-out="salon"' in html_page
          and 'class="go" data-panel="salon"' not in html_page)
    check("signing out answers ok", page.sign_out("salon") == "ok")
    check("with the screen stopped while it did, and started again",
          held == [("hold", "salon"), ("release", "salon")], str(held))
    db = sqlite3.connect(jar)
    left = sorted(h for (h,) in db.execute("SELECT host_key FROM cookies"))
    db.close()
    check("Google's and YouTube's cookies are gone, a site's own is not",
          left == ["jellyfin.local"], str(left))
    check("and the page offers to sign in again",
          'class="go" data-panel="salon"' in page.page()
          and signin.google_signed_in(profile) is False)
    check("google.co.uk is Google, notgoogle.com is not",
          not signin.GOOGLE_HOST.search("notgoogle.com")
          and signin.GOOGLE_HOST.search("accounts.google.co.uk"))


if __name__ == "__main__":
    sys.exit(main())
