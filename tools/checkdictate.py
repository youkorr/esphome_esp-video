#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Dictation: words said to the voice assistant, typed into the panel's page.

WHY THIS EXISTS. A panel has no keyboard to hand and the on-screen one is
slow for anything longer than a word. "Écris recette de crêpes" and "cherche
Les Simpson" go through Home Assistant's speech-to-text, a sentence trigger
in the automation the add-on writes, an event, run.py's routing, the
sender's control channel, and finally a field in the page -- which may be in
a frame, or not focused at all. Each of those is driven here with the
SHIPPED code:

  1. the sentences, through hassil 3.12.1, Home Assistant's own pin -- the
     words captured, and the sentences that must stay Home Assistant's;
  2. the reply and the event's text, through jinja2;
  3. the event over a real websocket to the shipped VoiceLinks, run.py's
     routing, and a real Remote -- and the words kept out of every log line;
  4. the shipped ha_send.py in a real browser: a focused field, a field in a
     frame, a search field nobody focused, a search replacing what a field
     held, and a page with nothing to type into.

    python3 tools/checkdictate.py

Needs hassil==3.12.1 and jinja2 for 1 and 2, Playwright and a Chromium for 4;
each half says when it is skipped.
"""
import http.server
import io
import os
import pathlib
import socket
import subprocess
import sys
import threading
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))

import checkvoicelinks as cv  # noqa: E402
from checkvoicelinks import FakeHA, FakeProcess, LINKS, check, settle  # noqa: E402,E501
import run  # noqa: E402
import voicelinks  # noqa: E402
from ha_send import Control  # noqa: E402


def sentences():
    print("The sentences, matched the way Home Assistant matches them:")
    try:
        from hassil import Intents, WildcardSlotList, recognize_all
        from hassil.parse_expression import parse_sentence
    except ImportError:
        print("  --   skipped: no hassil here (pip install hassil==3.12.1)")
        return
    names, _ = voicelinks.names_of(LINKS)
    voices, _ = voicelinks.voices_of(LINKS, names)
    auto = voicelinks.automation(names, voices)
    for trigger in auto["triggers"]:
        for sentence in trigger["command"]:
            parse_sentence(sentence)
    intents = {f"t{i}": {"data": [{"sentences": t["command"]}]}
               for i, t in enumerate(auto["triggers"])}
    ids = {f"t{i}": t["id"] for i, t in enumerate(auto["triggers"])}
    compiled = Intents.from_dict({"language": "fr", "intents": intents})
    # As default_agent._rebuild_trigger_intents does: every list a trigger
    # sentence names is a wildcard.
    for name in ("before", "text"):
        compiled.slot_lists[name] = WildcardSlotList(name)

    def heard(text):
        for result in recognize_all(text, compiled):
            words = result.entities.get("text")
            return ids[result.intent.name], (
                words.text.strip() if words is not None else None)
        return None, None

    for text, kind, words in (
            ("écris recette de crêpes", "type", "recette de crêpes"),
            ("Ok Nabu, écris bonjour à tous", "type", "bonjour à tous"),
            ("tape 1234", "type", "1234"),
            ("Écrivez-moi le code de la porte", "type",
             "le code de la porte"),
            ("type hello world", "type", "hello world"),
            ("cherche Les Simpson", "search", "Les Simpson"),
            ("Ok Nabu, recherche la météo de demain", "search",
             "la météo de demain"),
            ("search for cats", "search", "cats")):
        got_kind, got_words = heard(text)
        check(f"\"{text}\" -> {kind}: {words}",
              got_kind == kind and got_words == words,
              repr((got_kind, got_words)))
    got_kind, _ = heard("ouvre Jellyfin")
    check("\"ouvre Jellyfin\" is still a link", got_kind == "link:Jellyfin",
          repr(got_kind))
    for text in ("écris", "allume la lumière du salon",
                 "ouvre le volet du salon", "quelle heure est il",
                 "mets la musique dans la cuisine"):
        got_kind, _ = heard(text)
        check(f"\"{text}\" is left to Home Assistant", got_kind is None,
              repr(got_kind))


def templates():
    print("What the voice assistant answers, and what the event carries:")
    try:
        import jinja2
    except ImportError:
        print("  --   skipped: no jinja2 here")
        return
    auto = voicelinks.automation(["Jellyfin"])
    env = jinja2.Environment()
    answer = env.from_string(auto["actions"][1]["set_conversation_response"])
    text = env.from_string(auto["actions"][0]["event_data"]["text"])
    fr = {"user_input": {"language": "fr"}}
    en = {"user_input": {"language": "en"}}
    check("J'écris", answer.render(trigger={**fr, "id": "type"}) == "J'écris")
    check("Je cherche",
          answer.render(trigger={**fr, "id": "search"}) == "Je cherche")
    check("Typing, in English",
          answer.render(trigger={**en, "id": "type"}) == "Typing")
    check("a link still answers J'ouvre",
          answer.render(trigger={**fr, "id": "link:Jellyfin"})
          == "J'ouvre Jellyfin")
    check("the event carries the words heard",
          text.render(trigger={"slots": {"text": "les simpson"}})
          == "les simpson")
    check("and nothing, without a word of warning, for a link",
          text.render(trigger={"slots": {}}) == ""
          and text.render(trigger={}) == "")


def routing():
    print("From the event to the sender, through run.py:")
    ha = FakeHA()
    kitchen = [{"name": "Jellyfin", "url": "http://kitchen-jf.local/"}]
    links = [dict(link) for link in LINKS]
    # The dashboard's link is how the add-on finds Home Assistant.
    links[0]["url"] = ha.base + "/lovelace/0"
    run._config = run.regroup({
        "links": links,
        "launcher": {"voice_links": True},
        "launchers": [{"panel": "cuisine", "links": kitchen,
                       "avatar_voice": "assist_satellite.cuisine"}],
    })
    panels = [{"name": "salon", "host": "1", "url": "x"},
              {"name": "cuisine", "host": "2", "url": "y",
               "launcher_links": kitchen}]
    processes = {"salon": FakeProcess(), "cuisine": FakeProcess()}
    remotes = {}
    for name, process in processes.items():
        remotes[name] = run.Remote(name)
        remotes[name].set_process(process)
    said = []
    real_say = run.say
    run.say = said.append
    env = os.environ.pop("SUPERVISOR_TOKEN", None)

    def lines(name):
        return processes[name].stdin.getvalue().splitlines()
    try:
        run.start_voice_links(panels, remotes)
        check("it subscribes to the event", ha.subscribed.wait(5))
        ha.fire({"link": "type", "text": "recette de crêpes",
                 "satellite": "assist_satellite.cuisine"})
        check("the kitchen heard it, so the kitchen types it",
              settle(lambda: lines("cuisine") == ["type recette de crêpes"]),
              lines("cuisine"))
        ha.fire({"link": "search", "text": "les  simpson\nencore",
                 "panel": "salon"})
        check("a search goes to the panel named, on one line",
              settle(lambda: lines("salon") == ["search les simpson encore"]),
              lines("salon"))
        ha.fire({"link": "type", "text": 42, "panel": "salon"})
        check("a number a template made of the words is typed as one",
              settle(lambda: lines("salon")[-1:] == ["type 42"]),
              lines("salon"))
        ha.fire({"link": "type", "text": "  ", "panel": "salon"})
        check("no words is said, not sent",
              settle(lambda: any("carried no text" in s for s in said))
              and len(lines("salon")) == 2)
        check("and no line of the log carries the words",
              not any("crêpes" in s or "simpson" in s for s in said),
              repr(said))
    finally:
        run.say = real_say
        if env is not None:
            os.environ["SUPERVISOR_TOKEN"] = env
        ha.close()

    control = Control(io.StringIO("type bonjour à tous\nsearch cats\n"
                                  "type\n"))
    control._thread.join(timeout=2)
    got = control.drain()
    check("the sender's Control hands the loop both, and drops an empty one",
          got == [("type", "bonjour à tous"), ("search", "cats")], repr(got))


PAGES = {
    # A field that takes focus by itself, and reports what it holds.
    "/field": """<input id=f autofocus oninput="fetch('/got?f='
      + encodeURIComponent(this.value))">""",
    # The same, inside a frame: the top frame's active element is the IFRAME.
    "/frame": """<iframe style="width:300px;height:80px" srcdoc="<input
      autofocus oninput=&quot;fetch('/got?frame='
      + encodeURIComponent(this.value))&quot;>"></iframe>""",
    # Nothing focused, and a search field to be found.
    "/search": """<p>results</p><form action="/found"><input type=search
      name=q placeholder="Rechercher"></form>""",
    # A search field already holding words, focused.
    "/again": """<form action="/found"><input name=q value="old words"
      autofocus></form>""",
    # Nothing to type into at all.
    "/nothing": "<p>a picture and nothing else</p>",
}


def sender():
    print("The sender types it, in its real browser:")
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("  --   skipped: no Playwright here")
        return
    got = []

    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path in ("/got", "/found"):
                got.append((path, urllib.parse.parse_qs(query)))
            body = ("<!doctype html><meta charset=utf-8><body>"
                    + PAGES.get(path, "")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    pages = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=pages.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{pages.server_address[1]}"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def drain():
        conn, _ = listener.accept()
        try:
            while conn.recv(1 << 20):
                pass
        except OSError:
            pass
    threading.Thread(target=drain, daemon=True).start()
    command = [sys.executable, "-u",
               str(HERE / "components" / "portall" / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/field",
               "--no-token", "--not-home-assistant", "--width", "480",
               "--height", "320", "--audio", "off", "--keyboard", "off",
               "--control"]
    if os.environ.get("CHROMIUM"):
        command += ["--browser", os.environ["CHROMIUM"]]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def ask(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def last(key):
        for path, query in reversed(got):
            if key in query:
                return query[key][0]
        return None

    def go(path):
        ask(f"open {site}{path}")
        settle(lambda: any(f"Voice: opened {site}{path}" in line
                           for line in out), 15)
        settle(lambda: False, 0.8)
    try:
        check("the sender starts",
              settle(lambda: any(line.startswith("Ready") for line in out),
                     40), "".join(out[-5:]))
        settle(lambda: False, 1.0)
        ask("type bonjour à tous, ça va ?")
        check("a focused field gets the words, accents and all",
              settle(lambda: last("f") == "bonjour à tous, ça va ?", 8),
              repr(last("f")))
        go("/frame")
        ask("type dans le cadre")
        check("a field inside a frame gets them",
              settle(lambda: last("frame") == "dans le cadre", 8),
              repr(last("frame")))
        go("/search")
        ask("search Les Simpson")
        check("with nothing focused, the page's search field is found, "
              "filled and sent",
              settle(lambda: last("q") == "Les Simpson", 8), repr(got[-2:]))
        go("/again")
        ask("search new words")
        check("a search replaces what the field held",
              settle(lambda: last("q") == "new words", 8), repr(got[-2:]))
        go("/nothing")
        ask("type nowhere")
        check("a page with nothing to type into says so",
              settle(lambda: any("nothing on this page is waiting for text"
                                 in line for line in out), 8),
              "".join(out[-4:]))
        check("and the words are never printed",
              not any("Simpson" in line or "bonjour" in line
                      or "nowhere" in line for line in out),
              "".join(line for line in out if "Voice" in line))
    finally:
        process.kill()
        pages.shutdown()
        listener.close()


def main():
    sentences()
    templates()
    routing()
    sender()
    failed = cv.results.count(False)
    print(f"\n{len(cv.results) - failed} ok, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
