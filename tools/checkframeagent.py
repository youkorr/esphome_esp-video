#!/usr/bin/env python3
"""Does the browser say the same thing about itself in every frame?

A page on 127.0.0.1 frames one on 127.0.0.2. That is a different site, so the
frame gets its own process, which is the shape of YouTube's player inside
Google Photos. The frame starts a worker that fetches something. The check
compares what the page, the frame and the worker say, both in their own
scripts and in the headers the server receives.

The browser is started through the sender's own _launch(), present_browser()
and webdriver script, as main() does. --old does what the sender did before
launch_agent() and reproduces the fault: HeadlessChrome inside the frame.

    python3 tools/checkframeagent.py [--old] [--browser PATH]
"""
import argparse
import http.server
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "components", "portall"))
import ha_send  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SAY = ("navigator.userAgent + ' | ' + JSON.stringify("
       "(navigator.userAgentData || {}).brands || null)"
       " + ' | ' + navigator.webdriver")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", action="store_true")
    parser.add_argument("--browser", default=None)
    args = parser.parse_args()

    heard = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            heard.append((self.headers.get("Host").split(":")[0], self.path,
                          self.headers.get("User-Agent") or "",
                          self.headers.get("sec-ch-ua")))
            if self.path == "/":
                body = (f"<script>window.said={SAY}</script>"
                        f"<iframe src='http://127.0.0.2:{port}/frame'>"
                        "</iframe>")
                kind = "text/html"
            elif self.path == "/frame":
                body = (f"<script>window.said={SAY};"
                        "new Worker('/w.js')</script>")
                kind = "text/html"
            elif self.path == "/w.js":
                body = "fetch('/from-worker')"
                kind = "text/javascript"
            else:
                body, kind = "ok", "text/plain"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    failures = []

    def case(name, ok, detail=""):
        print(("ok      " if ok else "ECHEC   ") + name
              + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with sync_playwright() as playwright:
        executable = args.browser or ha_send.pick_browser("auto")
        launched = None if args.old else {}
        context = ha_send._launch(
            playwright, executable, tempfile.mkdtemp(),
            {"width": 800, "height": 600},
            list(ha_send.BROWSER_ARGS) + ["--headless"], agent=launched)
        page = context.pages[0] if context.pages else context.new_page()
        agent = ha_send.present_browser(
            context.new_cdp_session(page), page, False, "",
            (launched or {}).get("used"))
        if agent is not None:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});")
        page.goto(f"http://127.0.0.1:{port}/")
        page.wait_for_timeout(1500)
        top = page.evaluate("window.said")
        frames = [f for f in page.frames if "/frame" in f.url]
        inner = frames[0].evaluate("window.said") if frames else None
        context.close()

    case("the frame was loaded", inner is not None)
    case("no script anywhere reads Headless",
         "Headless" not in top and "Headless" not in (inner or ""),
         f"page {top.split(' | ')[0]}; frame {(inner or '').split(' | ')[0]}")
    case("the frame's script says what the page's says", top == inner,
         f"page {top}; frame {inner}")
    agents = {h[2] for h in heard}
    case("every request, the worker's included, carries one user agent",
         len(agents) == 1 and "Headless" not in next(iter(agents)),
         " / ".join(sorted(agents)))
    hints = {h[3] for h in heard if h[3] is not None}
    case("every request that carries hints carries the same ones",
         len(hints) <= 1 and not any("Headless" in h for h in hints),
         " / ".join(sorted(hints)))
    case("navigator.webdriver is hidden in the frame too",
         (inner or "").endswith("| undefined"), inner)
    server.shutdown()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
