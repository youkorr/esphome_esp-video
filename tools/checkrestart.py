#!/usr/bin/env python3
"""Does Screencast.restart() survive a browser that refuses a second start?

WHY THIS EXISTS. 4.31.3 made restart() start the screencast again without
stopping it, measured on Playwright's own Chromium, which allows that. A
household's panel runs Google Chrome, which answers "Screencast is already
active" -- and the add-on crashed every time the corner brought a panel home.
There is no Chrome here, so the two browsers are stood in for by a session
that records what it is sent and refuses the way each one does:

  - Chromium: a start on a running screencast is accepted;
  - Chrome: it is refused until a stop has been sent.

    python3 tools/checkrestart.py
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "components" / "portall"))

import ha_send  # noqa: E402

fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class Session:
    def __init__(self, refuses):
        self.refuses = refuses
        self.running = False
        self.sent = []

    def on(self, *_):
        pass

    def send(self, method, params=None):
        self.sent.append(method)
        if method == "Page.startScreencast":
            if self.running and self.refuses:
                raise RuntimeError("CDPSession.send: Protocol error "
                                   "(Page.startScreencast): Screencast is "
                                   "already active")
            self.running = True
        elif method == "Page.stopScreencast":
            self.running = False


class Context:
    def __init__(self, session):
        self.session = session

    def new_cdp_session(self, page):
        return self.session


class Page:
    def __init__(self, session):
        self.context = Context(session)


def capture(refuses):
    session = Session(refuses)
    return ha_send.Screencast(Page(session), 800, 480, 80), session


def main():
    print("A browser that accepts a start on a running screencast (Chromium):")
    cast, session = capture(refuses=False)
    session.sent.clear()
    cast.restart()
    check("restart() does not raise", True)
    check("and sends one start and no stop",
          session.sent == ["Page.startScreencast"], repr(session.sent))

    print("A browser that refuses it (Google Chrome):")
    cast, session = capture(refuses=True)
    session.sent.clear()
    try:
        cast.restart()
        raised = None
    except Exception as err:  # noqa: BLE001
        raised = err
    check("restart() does not raise -- the reported crash", raised is None,
          repr(raised))
    check("and the screencast is running afterwards", session.running)
    session.sent.clear()
    cast.restart()
    check("and the next restart stops first without asking again",
          session.sent == ["Page.stopScreencast", "Page.startScreencast"],
          repr(session.sent))

    print("Any other refusal is not swallowed:")
    cast, session = capture(refuses=False)

    def broken(method, params=None):
        raise RuntimeError("Target closed")
    session.send = broken
    try:
        cast.restart()
        raised = False
    except RuntimeError:
        raised = True
    check("a closed page still raises for the caller to handle", raised)

    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
