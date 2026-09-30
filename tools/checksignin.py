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
heard = {"typed": "", "webdriver": None, "cookies": [], "visits": 0}


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
                    "fetch('/probe?w='+navigator.webdriver);"
                    "q.addEventListener('input',()=>fetch('/typed?v='+"
                    "encodeURIComponent(q.value)));</script>" % COLOUR)
            extra = [("Set-Cookie", "kept=yes; Max-Age=86400; Path=/"),
                     ("Set-Cookie", "session=yes; Path=/")]
        elif path == "/probe":
            heard["webdriver"] = q.get("w", [""])[0]
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
from ha_send import BROWSER_ARGS
from playwright.sync_api import sync_playwright
profile, site, browser = sys.argv[1], sys.argv[2], sys.argv[4]
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
    if "--signal" in sys.argv:
        # The first version: SIGTERM alone, which lost the cookie.
        signin.Session.quit_browser = lambda self: False
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
        tab = phone.new_page(viewport={"width": 420, "height": 860})
        tab.goto(page_url)
        print("The page on the telephone:")
        check("each screen is offered", tab.locator(
            "button[data-panel='salon']").count() == 1)
        check("a screen with no profile cannot be chosen",
              tab.locator("button[disabled]").count() == 1)

        check("the page carries Home Assistant's ingress path for noVNC",
              'data-prefix="api/hassio_ingress/abc"'
              in sign.page("/api/hassio_ingress/abc"))

        print("Choosing the screen:")
        tab.click("button[data-panel='salon']")
        tab.wait_for_selector("#live:not([hidden])", timeout=30000)
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
        check("it carries no debugging port and no automation flag",
              "remote-debugging" not in flat and "enable-automation" not in flat,
              flat)
        end = time.monotonic() + 20
        while time.monotonic() < end and heard["webdriver"] is None:
            time.sleep(0.2)
        check("and the page it shows sees no automation",
              heard["webdriver"] == "false", str(heard["webdriver"]))

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

        print("Done:")
        visits = heard["visits"]
        tab.click("#done")
        end = time.monotonic() + 45
        while time.monotonic() < end and heard["visits"] == visits:
            time.sleep(0.2)
        check("the session is over", sign.session is None)
        check("the panel's sender starts again by itself",
              heard["visits"] == visits + 1)
        last = heard["cookies"][-1] if heard["cookies"] else ""
        check("and the DRIVEN browser presents what the plain one was given",
              "kept=yes" in last, repr(last))
        print(f"    (a session cookie across it: "
              f"{'kept' if 'session=yes' in last else 'not kept'} -- Google's "
              f"sign-in cookies have an expiry, so this one is only noted)")
        phone.close()

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


if __name__ == "__main__":
    sys.exit(main())
