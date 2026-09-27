#!/usr/bin/env python3
"""Does a panel stay signed in to a site after its browser restarts?

WHY THIS EXISTS. Reported from a panel: Unraid and Reolink ask for the
password again, "rester connecte" ticked. Measured on the shipped browser
with a kept profile, what comes back after the browser is started again:

                             session cookie   expiring cookie   localStorage
    as it was                     lost              kept            kept

A session cookie is one with no expiry date, which Chromium keeps only while
it runs -- unless it is told to carry on the last session, which is what a
desktop Chrome set to "continue where you left off" does and why the same
login survives there. Unraid's is exactly that: a PHP session cookie
(`unraid_<md5 of the server name>`, `session_start()` with PHP's default
lifetime of 0). And the browser restarts far more often than it looks --
every add-on update, every Home Assistant restart, every crash of a sender.

The fix has a side the check has to cover too: carrying on the last session
also brings back the last session's TABS, which here are pages nobody sees
-- a restored YouTube goes on playing into the panel's sound, and the sender
takes the first tab, which was once a site's pop-up. So the check counts the
tabs a restart opens as well as the cookie it keeps.

Everything is the shipped code: BROWSER_ARGS, `_launch()` and
`forget_tabs()` out of ha_send.py, in a child process that is then killed
outright, which is how a sender ends when the add-on stops it.

    python3 tools/checksession.py [--browser PATH] [--without]

--without leaves out the flag and the tab clean-up, so the reported fault is
reproduced through the same code rather than a reverted copy. --keep-tabs
leaves out only the clean-up, which is the flag's own fault reproduced.
"""
import http.server
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "components" / "portall"))

FLAG = "--restore-last-session"
WITHOUT = "--without" in sys.argv
KEEP_TABS = "--keep-tabs" in sys.argv
BROWSER = ""
for n, arg in enumerate(sys.argv):
    if arg == "--browser" and n + 1 < len(sys.argv):
        BROWSER = sys.argv[n + 1]
BROWSER = BROWSER or os.environ.get("CHROMIUM", "")
# Chromium writes its cookie store every thirty seconds, so a login has to be
# older than that before killing the browser says anything about restarts
# rather than about that timer.
SETTLE_S = 35

fails = 0


def ok(what, passed, detail=""):
    global fails
    print(("  ok    " if passed else "  ECHEC ") + what
          + (f"  ({detail})" if detail else ""))
    if not passed:
        fails += 1


class Site(http.server.BaseHTTPRequestHandler):
    """Signs in the way Unraid does: a cookie with no expiry date."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        if self.path == "/login":
            self.send_header("Set-Cookie", "session=1; Path=/; HttpOnly")
            self.send_header("Set-Cookie", "remember=1; Path=/; Max-Age=86400")
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>site</title>signed in")


def child(profile, port, first):
    """One run of the browser, as the sender starts it. Never returns."""
    import ha_send
    from playwright.sync_api import sync_playwright
    args = list(ha_send.BROWSER_ARGS)
    if WITHOUT:
        args = [a for a in args if a != FLAG]
    elif not KEEP_TABS:
        ha_send.forget_tabs(profile)
    with sync_playwright() as playwright:
        context = ha_send._launch(
            playwright, BROWSER or None, profile,
            {"width": 400, "height": 300}, args)
        time.sleep(2)
        tabs = [page.url for page in context.pages]
        page = context.pages[0] if context.pages else context.new_page()
        if first:
            page.goto(f"http://127.0.0.1:{port}/login")
            # A site that opened a second tab of its own.
            context.new_page().goto(f"http://127.0.0.1:{port}/popup")
        else:
            page.goto(f"http://127.0.0.1:{port}/")
        # HttpOnly is what a real session cookie is, so the page cannot see
        # it: ask the browser.
        names = sorted(c["name"] for c in context.cookies())
        print("R " + json.dumps({"tabs": tabs, "cookies": names}), flush=True)
        time.sleep(3600)


def run_once(profile, port, first):
    process = subprocess.Popen(
        [sys.executable, __file__, "--child", profile, str(port),
         "1" if first else "0"] + sys.argv[1:],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    line = process.stdout.readline().strip()
    if first:
        time.sleep(SETTLE_S)
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    time.sleep(1)
    if not line.startswith("R "):
        return None
    return json.loads(line[2:])


def main():
    if "--child" in sys.argv:
        at = sys.argv.index("--child")
        child(sys.argv[at + 1], sys.argv[at + 2], sys.argv[at + 3] == "1")
        return 0
    import ha_send
    if not WITHOUT:
        ok("the shipped browser is told to carry on its last session",
           FLAG in ha_send.BROWSER_ARGS)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    profile = tempfile.mkdtemp()
    print("Signed in, then the browser killed and started again twice"
          + (" -- WITHOUT the fix" if WITHOUT else "") + ":")
    try:
        runs = [run_once(profile, port, first=(n == 0)) for n in range(3)]
    finally:
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    if None in runs:
        ok("every run of the browser started", False)
        return 1
    ok("signing in sets both cookies",
       runs[0]["cookies"] == ["remember", "session"], runs[0]["cookies"])
    for n, run in enumerate(runs[1:], start=1):
        ok(f"restart {n}: the expiring cookie is kept",
           "remember" in run["cookies"])
        ok(f"restart {n}: the session cookie is kept -- this is Unraid's login",
           "session" in run["cookies"], run["cookies"])
        ok(f"restart {n}: no old tab comes back, only the one it starts with",
           len(run["tabs"]) <= 1, run["tabs"])
    print(f"\n{fails} problem(s)." if fails else "\nAll good.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
