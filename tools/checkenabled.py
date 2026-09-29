#!/usr/bin/env python3
"""Does a screen switched off in the add-on really stop being served?

WHY THIS EXISTS. Asked for as a switch per screen "qui permet de faire soit
une maintenance ... cela evite qu'il fasse une recherche de ecran alors que je
l'ai deconnecter": a screen being reflashed or unplugged had a sender trying
to reach it for ever.

This runs the add-on's own main() on an options file in the form the
Supervisor writes, with the pieces that would start real processes replaced
by recorders: which panels get a sender, which profiles the sweep is told
about, and what the add-on does when every panel is off. And command_for(),
so the switch never reaches the sender as a flag it does not know.

    python3 tools/checkenabled.py
"""

import json
import os
import pathlib
import signal
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))

import run  # noqa: E402

faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail else ""))
    if not ok:
        faults.append(what)


def panel(name, **extra):
    return {"name": name, "host": f"10.0.0.{len(name)}", "url": "http://x/",
            "width": 1024, "height": 600, "rotate": "0",
            "touch": {"rotate": "0"}, "advanced": {}, **extra}


def run_main(panels):
    """main() on these panels; returns (served names, swept names, code)."""
    served, swept = [], []
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "options.json"
        path.write_text(json.dumps({"panels": panels, "links": []}))
        os.environ["UDISP_CONFIG"] = str(path)

        def fake_serve(p, name, stop, remote=None):
            served.append(name)
            if len(served) == expected[0]:
                os.kill(os.getpid(), signal.SIGTERM)

        run.serve = fake_serve
        run.sweep_profiles = lambda ps: swept.extend(p["name"] for p in ps)
        run.start_launchers = lambda config, ps: (None, {})
        run.start_voice_links = lambda ps, remotes: None
        run.start_pulseaudio = lambda: None
        expected = [sum(1 for p in panels if run.in_use(p))]
        if expected[0] == 0:
            # Nothing will ever be served, so stop it from outside, the way
            # the Supervisor does.
            threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        code = run.main()
    return served, swept, code


def main():
    print("A screen switched off:")
    served, swept, code = run_main([
        panel("salon"),                       # saved before the switch existed
        panel("cuisine", enabled=False),      # switched off
        panel("chambre", enabled=True),
        panel("bureau", enabled=""),          # a blank field is not "off"
    ])
    check("a switched-off screen gets no sender", "cuisine" not in served,
          ", ".join(served))
    check("every other screen still does, a missing or blank switch meaning on",
          sorted(served) == ["bureau", "chambre", "salon"], ", ".join(sorted(served)))
    check("its profile is still counted as one to keep",
          "cuisine" in swept, ", ".join(swept))
    check("and the add-on carries on", code == 0, str(code))

    print("Every screen switched off:")
    served, swept, code = run_main([panel("salon", enabled=False),
                                    panel("cuisine", enabled="false")])
    check("nothing is served", served == [], ", ".join(served))
    check("the add-on waits rather than stopping, and leaves cleanly when told",
          code == 0, str(code))

    print("The sender's command line:")
    line = run.command_for({**panel("salon", enabled=True), "port": 5000})
    check("carries no --enabled, which the sender does not know",
          not any("enabled" in part for part in line), " ".join(line[:6]))

    print("ok" if not faults else f"{len(faults)} ECHEC")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
