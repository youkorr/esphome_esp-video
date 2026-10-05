#!/usr/bin/env python3
"""A page emptying its own player is not a media error.

Reported from a panel on Google News, nine lines in three seconds:

    Media: error format not supported: MEDIA_ELEMENT_ERROR: Empty src
           attribute -- <podcast-player-content> t=0.0 ready=0 net=3 paused

`src = ''` is how a page stops a player, and the browser answers it with
code 4 -- the same code as a file it cannot decode -- and the message
"Empty src attribute". Printed as "format not supported" it reads as a codec
problem that is not there.

The shipped MEDIA_INIT script in the shipped Chromium, reporting through the
same binding the sender installs:

  - a player whose source is emptied, three ways, says nothing;
  - a file the browser cannot decode is still reported, with its reason.

    python3 tools/checkmediaerror.py [--sender PATH]

--sender PATH reads the script out of another copy of ha_send.py; against
the previous release the first case fails, which is the report.
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = (sys.argv[sys.argv.index("--sender") + 1] if "--sender" in sys.argv
          else os.path.join(HERE, "..", "components", "portall", "ha_send.py"))


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(SENDER)))
    sys.path.insert(1, os.path.join(HERE, "..", "components", "portall"))
    spec = importlib.util.spec_from_file_location("sender_under_test", SENDER)
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    from playwright.sync_api import sync_playwright

    fails = 0

    def check(what, ok, detail=""):
        nonlocal fails
        print(("  ok     " if ok else "  ECHEC  ") + what
              + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            fails += 1

    said = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None)
        context = browser.new_context()
        context.expose_function("__udispMediaError",
                                lambda what: said.append(what))
        context.add_init_script(sender.media_init_js(0))
        page = context.new_page()
        page.goto("data:text/html,<audio id=a></audio><video id=v></video>"
                  "<audio id=b></audio>")
        # One at a time and apart: the script says at most one line every
        # 400 ms, so steps taken together would hide all but the first.
        for step in ("document.getElementById('a').setAttribute('src', '')",
                     "(v => { v.setAttribute('src', ''); v.load(); })"
                     "(document.getElementById('v'))",
                     "document.getElementById('b').src = ''"):
            page.evaluate(step)
            page.wait_for_timeout(700)
        check("a player whose source is emptied says nothing",
              not any("Empty src" in line for line in said), str(said))
        said.clear()
        page.wait_for_timeout(500)
        # Bytes that are no media at all: the browser cannot open them.
        page.evaluate("""() => {
            const a = document.getElementById('a');
            a.src = 'data:audio/mpeg;base64,' + btoa('not a sound at all');
        }""")
        page.wait_for_timeout(2000)
        check("a file the browser cannot decode is still reported",
              any(line.startswith("error ") for line in said), str(said))
        browser.close()
    print("\nAll good." if not fails else f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
