#!/usr/bin/env python3
"""A sign-in nobody is watching gives the panel back by itself.

The panel's picture stops for as long as a sign-in session lasts. A telephone
put away without Done used to leave it stopped until SESSION_LIMIT_S, twenty
minutes -- reported as a panel that froze until the add-on was restarted.

The real SignIn and its watcher, with a stand-in for the browser session so
no Xvfb or Chrome is needed:

- the real page, opened in a browser on a sped-up clock, says it is there;
- a page that keeps saying so keeps its session;
- a page that stops ends it, and the panel is released.

    python3 tools/checkunwatched.py [--signin PATH]

--signin runs another copy of signin.py (an older release, say), which is how
the fault is reproduced: there the session outlives the silence.
"""
import argparse
import importlib.util
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    spec = importlib.util.spec_from_file_location("signin_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSession:
    """What SignIn's watcher asks of a session, and no browser."""

    def __init__(self, name):
        self.name = name
        self.started = time.monotonic()
        self.seen_at = self.started
        self.reopening = False
        self.web_port = None
        self.stopped = False

    def browser_running(self):
        return not self.stopped

    def stop(self):
        self.stopped = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signin",
                        default=os.path.join(HERE, "..", "portall",
                                             "signin.py"))
    args = parser.parse_args()
    signin = load(args.signin)

    failures = []

    def case(name, ok, detail=""):
        print(("  ok    " if ok else "  ECHEC ") + name
              + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    # Seconds rather than minutes, so the run takes seconds.
    signin.UNWATCHED_S = 2
    signin.SESSION_LIMIT_S = 600
    released, said = [], []
    sign = signin.SignIn({"salon": {"profile": "/nonexistent", "browser": "x"}},
                         lambda name: None, released.append, said.append,
                         peers=("127.0.0.1",))
    port = sign.serve(0, host="127.0.0.1")

    def start_session():
        session = FakeSession("salon")
        sign.session = session
        threading.Thread(target=sign._watch, args=(session,),
                         daemon=True).start()
        return session

    print("The page says it is there:")
    session = start_session()
    from playwright.sync_api import sync_playwright
    browser_path = os.environ.get("CHROMIUM") or None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path)
        page = browser.new_page(viewport={"width": 390, "height": 760})
        page.clock.install()
        page.goto(f"http://127.0.0.1:{port}/")
        case("the page opens on the running session",
             page.locator("#live").is_visible())
        # Five turns of the page's own timer across six seconds of the
        # watcher's: the session must outlive UNWATCHED_S twice over.
        alive_ms = getattr(signin, "ALIVE_S", 20) * 1000
        for _ in range(6):
            page.clock.run_for(alive_ms)
            time.sleep(1)
        case("a page that is open keeps its session",
             sign.session is session and not session.stopped,
             f"released {released}")
        page.close()
        browser.close()

    print("Nobody watching:")
    deadline = time.monotonic() + 6
    while sign.session is session and time.monotonic() < deadline:
        time.sleep(0.2)
    case("a page closed ends the session", sign.session is None,
         "still running after 6 s with nothing said for 2")
    case("and its browser is closed the polite way", session.stopped)
    case("and the panel is given back", released == ["salon"], released)
    case("and the log says why",
         any("nobody had the page open" in line for line in said), said)

    print("Kept alive by hand, then let go:")
    released.clear()
    session = start_session()
    import urllib.request
    for _ in range(4):
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{port}/alive", data=b"", method="POST"),
                timeout=5).read()
        except OSError:
            pass  # an older signin.py has no /alive
        time.sleep(1)
    case("four seconds of /alive outlive a two-second limit",
         sign.session is session)
    time.sleep(3.5)
    case("and three seconds of silence end it", sign.session is None)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
