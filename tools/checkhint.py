#!/usr/bin/env python3
"""Is the corner mark drawn on the page a link opens, and not only at start?

WHY THIS EXISTS. Reported from a panel: the white corner that says "hold here
to go home" was there after a reboot and then gone for good -- "invisible
until you reboot". It was shown for five seconds when the sender started and
when the corner brought the panel home, and at no other time: opening a link
is a navigation nothing told the mark about, so inside a link, which is the
one place the way out is needed, it was never drawn.

This drives the SHIPPED HomeHint and HOME_HINT_JS in a real browser, the way
the send loop does: draw the mark, follow a link, ask take_arrival(), draw
again, and read whether the NEW document carries it. A same-document change
of address (a dashboard switching view) must not count as an arrival.

Pass a path to another copy of ha_send.py to run the same cases against it;
that is how the fault is reproduced against the version that shipped.

Needs Playwright and a Chromium. Takes $CHROMIUM.
"""

import functools
import http.server
import importlib.util
import os
import pathlib
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = next((a for a in sys.argv[1:] if a.endswith(".py")),
              str(HERE / "components" / "portall" / "ha_send.py"))
sys.path.insert(0, str(HERE / "components" / "portall"))
spec = importlib.util.spec_from_file_location("sender", SOURCE)
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)

from playwright.sync_api import sync_playwright  # noqa: E402

faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        faults.append(what)


def serve():
    folder = pathlib.Path(tempfile.mkdtemp())
    (folder / "index.html").write_text(
        '<!doctype html><body style="background:#fff">'
        '<a id="go" href="link.html">Jellyfin</a>'
        '<button id="view" onclick="history.pushState(null, null, location.pathname + String.fromCharCode(35) + 2)">'
        'view</button>')
    (folder / "link.html").write_text(
        '<!doctype html><body style="background:#fff"><p>the link</p>')
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/" % server.server_address[1]


SHOWN = """() => {
    const d = document.getElementById('__portall_home');
    return !!d && d.style.display !== 'none' && d.matches(':popover-open');
}"""


def turn(page, hint):
    """One turn of the send loop's mark logic, inside the hint window."""
    arrived = hasattr(hint, "take_arrival") and hint.take_arrival()
    hint.set(0.0)
    return arrived


def main():
    print("The corner mark, page after page (" + os.path.basename(SOURCE) + "):")
    server, url = serve()
    browser_path = os.environ.get("CHROMIUM") or None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_init_script(sender.HOME_HINT_JS)
        page = context.new_page()
        page.goto(url)
        hint = sender.HomeHint(page, 1280, 800)
        turn(page, hint)
        check("drawn on the page the sender starts on", page.evaluate(SHOWN))

        page.click("#go")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(100)
        arrived = turn(page, hint)
        check("opening a link counts as a page arriving", arrived)
        check("and the mark is drawn on the page the link opened",
              page.evaluate(SHOWN))

        page.go_back()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(100)
        turn(page, hint)
        check("and again on the page it came back to", page.evaluate(SHOWN))

        page.click("#view")
        page.wait_for_timeout(100)
        again = hasattr(hint, "take_arrival") and hint.take_arrival()
        check("a view changing inside the same page is not an arrival",
              not again)

        hint.set(None)
        check("taken away when its time is up", not page.evaluate(SHOWN))
        browser.close()
    server.shutdown()

    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
