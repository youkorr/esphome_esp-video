#!/usr/bin/env python3
"""A link's user agent is said by the whole page, and a refusal is logged.

WHY THIS EXISTS. Reported from a panel: a link carrying a Tizen television's
user agent, YouTube's television interface, now gets the ordinary site, and
casting to the panel stopped working. Measured here first, against the sender
before 4.39.2 and after: the document's request carried the Tizen string in
both, and every request the page made afterwards carried a desktop Chrome on
Linux in both -- with Chrome's client hints beside the Tizen string. So the
sender had not changed for that link; the page had always contradicted
itself, and whatever YouTube now checks is not visible from here.

The shipped ha_send.py against a fake panel and a local site shaped like that
junction (/tv serves a "television" page to a Tizen agent and redirects
everybody else), driven through the control channel's `open`:

  - the television page's document goes out as the television, with no
    Sec-CH-UA beside it;
  - so does a request its own script makes afterwards, to an address that
    asked for nothing;
  - its scripts read the television in navigator.userAgent and find no
    navigator.userAgentData;
  - a page that asked for nothing is left as it was, hints included, and so
    is a Chrome agent's hints;
  - a redirect away from an address that asked for an agent is logged, and so
    is a page that left one seconds after arriving.

--sender PATH runs another copy of ha_send.py (an older release). SHOW=1
prints the end of the sender's log and the last requests the site saw.

Needs Playwright. Takes $CHROMIUM for the browser.
"""
import http.server
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
TIZEN = ("Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/537.36 "
         "(KHTML, like Gecko) 85.0.4183.93/6.0 TV Safari/537.36")
CHROMEY = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like "
           "Gecko) Chrome/120.0.0.0 Safari/537.36 CrKey/1.56.500000")
fails = 0
seen = []      # (path, user-agent, sec-ch-ua or None)
reports = []   # what pages' own scripts read


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


TV_PAGE = ("<!doctype html><body>TV<script>"
           "fetch('/api?from=tv');"
           "fetch('/report?' + encodeURIComponent(JSON.stringify({"
           " ua: navigator.userAgent,"
           " data: navigator.userAgentData === undefined ? 'none' : 'some'})));"
           "</script></body>")


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ua = self.headers.get("user-agent", "")
        path = self.path
        seen.append((path, ua, self.headers.get("sec-ch-ua")))
        if path.startswith("/report?"):
            reports.append(json.loads(unquote(path.split("?", 1)[1])))
            body = ""
        elif path.startswith("/tv"):
            if "Tizen" not in ua:
                self.send_response(302)
                self.send_header("Location", "/plain")
                self.end_headers()
                return
            body = TV_PAGE
        elif path.startswith("/bounce"):
            self.send_response(302)
            self.send_header("Location", "/plain?bounced")
            self.end_headers()
            return
        elif path.startswith("/leave"):
            body = ("<!doctype html><script>setTimeout(() => "
                    "location.href = '/plain?left', 400)</script>")
        elif path.startswith("/cast"):
            body = "<!doctype html><script>fetch('/api?from=cast')</script>"
        else:
            body = ("<!doctype html><body>plain<script>"
                    "fetch('/api?from=plain')</script></body>")
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def wait_for(test, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.05)
    return False


def main():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright")
        return 0

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        try:
            while conn.recv(1 << 20):
                pass
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    work = tempfile.mkdtemp()
    command = [sys.executable, "-u", SENDER,
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--not-home-assistant", "--width", "800", "--height", "480",
               "--audio", "off", "--keyboard", "off", "--control",
               "--profile", os.path.join(work, "p"),
               "--page-agent", f"{site}/tv={TIZEN}",
               "--page-agent", f"{site}/bounce={TIZEN}",
               "--page-agent", f"{site}/leave={TIZEN}",
               "--page-agent", f"{site}/cast={CHROMEY}"]
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def said(text):
        return any(text in line for line in out)

    def open_(path):
        process.stdin.write(f"open {site}{path}\n")
        process.stdin.flush()

    def last(prefix):
        hits = [s for s in seen if s[0].startswith(prefix)]
        return hits[-1] if hits else None

    try:
        if not wait_for(lambda: last("/api?from=plain"), 40):
            check("the sender starts and shows its home page", False,
                  "".join(out[-15:]))
            return 1
        home = last("/home")
        api = last("/api?from=plain")
        check("a page that asked for nothing goes out as this browser",
              "Tizen" not in home[1] and "Tizen" not in api[1], home[1][:60])
        check("with this browser's client hints, untouched",
              bool(home[2]) and bool(api[2]))

        open_("/tv")
        wait_for(lambda: reports and last("/api?from=tv"))
        doc = last("/tv")
        check("the television page is asked for as the television",
              doc is not None and "Tizen" in doc[1])
        check("and with no Chrome client hints beside it",
              doc is not None and doc[2] is None, doc and doc[2])
        api = last("/api?from=tv")
        check("a request its script makes afterwards says the television "
              "too", api is not None and "Tizen" in api[1],
              api and api[1][:60])
        check("also with no Chrome client hints",
              api is not None and api[2] is None, api and api[2])
        report = reports[-1] if reports else {}
        check("its scripts read the television in navigator.userAgent",
              "Tizen" in report.get("ua", ""), report)
        check("and find no navigator.userAgentData",
              report.get("data") == "none", report)

        open_("/cast")
        wait_for(lambda: last("/api?from=cast"))
        cast = last("/api?from=cast")
        check("a Chrome agent keeps its client hints",
              cast is not None and "CrKey" in cast[1] and bool(cast[2]),
              cast)

        open_("/home")
        wait_for(lambda: len([s for s in seen
                              if s[0] == "/api?from=plain"]) >= 2)
        back = [s for s in seen if s[0] == "/api?from=plain"][-1]
        check("back on a page that asked for nothing, it is this browser "
              "again", "Tizen" not in back[1] and "CrKey" not in back[1])

        open_("/bounce")
        check("a redirect away from an address with an agent is logged",
              wait_for(lambda: said("with a redirect to")),
              "".join(o for o in out if "Agent" in o))
        open_("/leave")
        check("a page that left one seconds after arriving is logged",
              wait_for(lambda: said("went to") and said("/plain?left")),
              "".join(o for o in out if "Agent" in o))
    finally:
        if os.environ.get("SHOW"):
            print("".join(out[-40:]))
            print(seen[-8:])
        process.kill()
        server.shutdown()
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
