"""Open a launcher link by voice: "ouvre Jellyfin", "lance YouTube".

It is the idea of every Python "Jarvis" -- hear a sentence, open a page --
with the parts already in this house. The panel's voice assistant is the ear,
Home Assistant understands the sentence, and the add-on opens the link in the
panel's browser, which is the one browser here that can show anything.

HOW HOME ASSISTANT IS TOLD. An automation with one sentence trigger per link,
written by this add-on through Home Assistant's own configuration API -- the
route its automation editor uses (homeassistant/components/config/view.py,
/api/config/automation/config/<id>) -- and rewritten only when the links
change. Home Assistant reloads it by itself (the view's post-write hook).

Not a wildcard, and that is the whole reason for writing it here rather than
handing somebody a sentence to paste. Sentence triggers are matched BEFORE
Home Assistant's own commands (DefaultAgent._async_handle_message asks
async_recognize_sentence_trigger first), so "ouvre {anything}" would take
"ouvre le volet du salon" away from the covers. Naming the links exactly
leaves every other sentence alone -- and the names change, so something has
to keep the list in step, which is this.

HOW THE ADD-ON HEARS IT. The automation fires a `portall_open` event with the
link's name and the voice assistant that heard it; this subscribes to that
event over Home Assistant's websocket, the one the avatar already uses. The
event is also a plain entry point: a dashboard button or any automation can
fire `portall_open` with `link:` (a name, or `home`) and `panel:`.

An accessory must never cost the picture: every failure here is said once and
retried, and no panel waits on any of it.
"""

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request

from voice import Closed, Socket, websocket_address

AUTOMATION_ID = "portall_open_links"
EVENT = "portall_open"
HOME = "home"
LINK = "link:"

# The words somebody says in front of a link's name. French first, because
# that is who this was built with, and English beside it. No hyphens: a
# sentence trigger refuses punctuation (conversation/trigger.py,
# has_no_punctuation), and a dash is punctuation to it.
#
# Both forms of address, because a speech engine writes what it hears and a
# household does not all say "tu" to its panel: "Ouvrez Home Assistant" missed
# every link. And "moi" after any of them -- "ouvre-moi Jellyfin" reaches the
# trigger as "ouvre moi Jellyfin", the hyphen gone.
VERBS = ("(ouvre|ouvrez|ouvrir|lance|lancez|lancer|affiche|affichez|afficher|"
         "montre|montrez|mets|mettez|va sur|allez sur|"
         "open|launch|show|start|go to) [moi]")
# Optional words before the name, so "lance la télé" still names "télé".
# "l'" too: a speech-to-text engine writes "l'Home Assistant" as readily as a
# person says it, and it is the one article that leaves no space behind it.
ARTICLES = "[le |la |les |l']"
# What people put between the verb and the name without meaning anything by
# it -- "ouvre la page de Home Assistant", "lance l'appli Jellyfin". Optional,
# and the name still has to follow, so none of these can take a sentence
# about something else.
FILLERS = ("[(la page|l'application|l'appli|l'interface|le site|l'écran) "
           "[de |du |d']]")
# And after it: politeness costs a match otherwise.
POLITE = "[s'il te plaît|s'il vous plaît|stp|please]"

# Whatever the speech engine wrote BEFORE the command, which is most often
# the wake word itself. Reported from a panel as
#     Speech recognised as: "Ok, Naboo, ouvre la page Home Assistant."
# -- the phrase is in the audio the satellite streams, Home Assistant hands
# the text over as it is (nothing in assist_pipeline strips it), and a
# sentence has to match from its first word, so every link missed and the
# conversation agent answered instead. The spelling is the engine's (Naboo,
# Nabou, Nabu, Jarvis...), so it cannot be listed; a list reference in a
# sentence trigger is a WILDCARD (default_agent._rebuild_trigger_intents), and
# an optional one takes anything or nothing.
#
# Only in front of a VERB. Before a bare "accueil" or "home" it would turn
# "allume la lumière de l'accueil" into going home; before a verb it still
# needs the verb and then a link's exact name, which is what keeps "ok nabu,
# ouvre le volet du salon" with the covers.
LEAD = "[{before}] "

HOME_SENTENCES = [
    LEAD + "(retour|reviens|retourne|revenir) [a|à] [la page d'|l']accueil",
    LEAD + "(ouvre|ouvrez|affiche|affichez|montre|montrez) [moi] "
    "[la page d'|l']accueil",
    "[la] page d'accueil",
    "accueil",
    LEAD + "(go|back) [to] home",
    "home [screen]",
]


def speakable(name):
    """A link's name as a sentence may carry it: letters, digits, apostrophes.

    Everything else becomes a space. A sentence trigger refuses punctuation,
    and ( ) [ ] { } < > | are template syntax -- "Camera & <cuisine>" is a
    name a form allows, and would otherwise be an automation Home Assistant
    refuses as a whole, taking every other link with it.
    """
    kept = "".join(c if (c.isalnum() or c in " '") else " "
                   for c in str(name or ""))
    return " ".join(kept.split())


def spoken_forms(name, others=()):
    """The name as a sentence template: as written, and run together.

    "Home Assistant" is also written "HomeAssistant" -- by people and by
    speech-to-text engines -- and the two are the same link. A one-word name
    has nothing to run together and stays as it is. `others` are the link's
    voice: words, each taken the same two ways.
    """
    forms = []
    for said in (name, *others):
        for form in (said, said.replace(" ", "")):
            if form not in forms:
                forms.append(form)
    return f"({'|'.join(forms)})" if len(forms) > 1 else forms[0]


def voice_of(link):
    """What else a link may be called aloud: its voice:, comma-separated.

    A setting on the LINK, because it is a fact about the name: "Jellyfin" is
    an English word said in a French sentence, and the speech engine writes
    "gelée fine". No grammar can guess that, and a household can read it off
    its own log and write it down.
    """
    if not isinstance(link, dict):
        return []
    out = []
    for part in str(link.get("voice") or "").split(","):
        said = speakable(part)
        if key(said):
            out.append(said)
    return out


def key(name):
    """What two spellings of one name have in common, to find a link by it.

    Case, accents, spaces and punctuation go, so "you tube", "YouTube" and
    "Youtube!" are the same link -- a speech-to-text engine picks any of them.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(c for c in text.lower() if c.isalnum())


def automation(names, voices=None):
    """The automation this add-on keeps in Home Assistant, for these names.

    `voices` maps a name to the other words it may be said as; the trigger's
    id stays the name, so what the add-on is told is the link, whichever of
    its words was heard.
    """
    voices = voices or {}
    triggers = []
    for name in names:
        forms = spoken_forms(name, voices.get(name, ()))
        triggers.append({
            "trigger": "conversation",
            "command": [f"{LEAD}{VERBS} {FILLERS}{ARTICLES}{forms} "
                        f"{POLITE}"],
            "id": LINK + name,
        })
    triggers.append({"trigger": "conversation", "command": HOME_SENTENCES,
                     "id": HOME})
    response = (
        "{% set fr = ((trigger.user_input or {}).language or '')[:2] == 'fr' %}"
        "{% if trigger.id == 'home' %}"
        "{{ \"Retour à l'accueil\" if fr else 'Back to the home page' }}"
        "{% else %}"
        "{{ (\"J'ouvre \" if fr else 'Opening ') ~ trigger.id[5:] }}"
        "{% endif %}")
    return {
        "alias": "Portall : ouvrir un lien à la voix",
        "description": (
            "Written by the Portall add-on from its links, and rewritten "
            "whenever they change -- edits made here are replaced. Turn off "
            "voice_links in the add-on to remove it."),
        "mode": "queued",
        "triggers": triggers,
        "conditions": [],
        "actions": [
            {"event": EVENT,
             "event_data": {"link": "{{ trigger.id }}",
                            "satellite": "{{ trigger.satellite_id }}"}},
            {"set_conversation_response": response},
        ],
    }


def names_of(links):
    """Every distinct name a sentence can carry, in the order they came.

    A link whose name has nothing sayable in it -- an emoji -- is named by the
    first of its voice: words instead, and dropped only when it has none.
    """
    seen, out, dropped = set(), [], []
    for link in links:
        if not isinstance(link, dict):
            continue
        spoken = speakable(link.get("name"))
        if not key(spoken):
            spoken = (voice_of(link) or [""])[0]
        if not key(spoken):
            if link.get("name"):
                dropped.append(str(link.get("name")))
            continue
        if key(spoken) in seen:
            continue
        seen.add(key(spoken))
        out.append(spoken)
    return out, dropped


def voices_of(links, names):
    """{name: [other words]} for the links that have some, and the clashes.

    A word already a link's name, or already another link's word, is left
    out and returned: said aloud it would open whichever trigger Home
    Assistant tried first, which is a coin toss from the glass.
    """
    taken = {key(name) for name in names}
    out, clashes = {}, []
    for link in links:
        if not isinstance(link, dict):
            continue
        name = speakable(link.get("name"))
        if not key(name):
            name = (voice_of(link) or [""])[0]
        if name not in names:
            continue
        for said in voice_of(link):
            if said.lower() == name.lower():
                continue
            # The link's own name spelt apart ("jelly fin") is not a clash:
            # it is the same link, written the way the engine writes it.
            if key(said) in taken and key(said) != key(name):
                clashes.append((str(link.get("name") or name), said))
                continue
            taken.add(key(said))
            out.setdefault(name, []).append(said)
    return out, clashes


def find_link(links, name):
    """The link a name means, or None: by its name, then by a voice: word.

    The second is for the link whose name is an emoji, which the automation
    knows by its first voice: word.
    """
    wanted = key(name)
    if not wanted:
        return None
    for link in links:
        if isinstance(link, dict) and key(link.get("name")) == wanted:
            return link
    for link in links:
        if any(key(said) == wanted for said in voice_of(link)):
            return link
    return None


def wanted(name):
    """What an event asked for: ("home", None), ("link", name) or None."""
    text = str(name or "").strip()
    if not text:
        return None
    if text.lower() == HOME:
        return (HOME, None)
    if text.startswith(LINK):
        text = text[len(LINK):]
    return ("link", text) if text else None


class VoiceLinks:
    """Keeps the automation in step and hands each request to a panel."""

    RETRY_FIRST_S = 2.0
    RETRY_MAX_S = 60.0

    def __init__(self, base, token, links, act, say):
        # `base` is Home Assistant's REST address -- the Supervisor's
        # http://supervisor/core, or a house's own origin in a hand run.
        self.base = str(base or "").rstrip("/")
        self.url = websocket_address(base) if base else ""
        self.token = str(token or "")
        self.names, self.dropped = names_of(links)
        self.voices, self.clashes = voices_of(links, self.names)
        self.act = act
        self._say = say
        self._said = set()

    def say(self, key_, text):
        if key_ in self._said:
            return
        self._said.add(key_)
        self._say(f"Voice links: {text}")

    # -- the automation ------------------------------------------------------

    def _request(self, method, body=None):
        where = f"{self.base}/api/config/automation/config/{AUTOMATION_ID}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            where, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as answer:
                return answer.status, json.loads(answer.read().decode() or "{}")
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read().decode() or "{}")
            except ValueError:
                detail = {}
            return err.code, detail

    def sync(self):
        """Write the automation if it is missing or differs. True when it holds.

        Asked first rather than written blindly: a write rewrites the house's
        automations.yaml and reloads the automation, and doing that at every
        start of the add-on for nothing is churn somebody would notice.
        """
        want = automation(self.names, self.voices)
        status, have = self._request("GET")
        if status == 200:
            have = {k: v for k, v in have.items() if k != "id"}
            if have == want:
                return True
        elif status != 404:
            self.say(f"read:{status}",
                     f"Home Assistant answered {status} when asked for the "
                     f"automation ({have.get('message', '')}); links cannot "
                     f"be opened by voice until it can be written.")
            return False
        status, answer = self._request("POST", want)
        if status != 200:
            self.say(f"write:{status}",
                     f"Home Assistant refused the automation ({status}: "
                     f"{answer.get('message', '')}). Links cannot be opened "
                     f"by voice.")
            return False
        self._say(f"Voice links: the automation \"{want['alias']}\" now "
                  f"knows {len(self.names)} link(s): "
                  + ", ".join(
                      name + (f" (or {', '.join(self.voices[name])})"
                              if self.voices.get(name) else "")
                      for name in self.names))
        return True

    # -- the event -----------------------------------------------------------

    def handle(self, data):
        asked = wanted((data or {}).get("link"))
        if asked is None:
            self._say(f"Voice links: an event {EVENT} named no link "
                      f"({json.dumps(data)[:120]}) -- ignored")
            return
        satellite = str((data or {}).get("satellite") or "").strip()
        if satellite in ("None", "none"):
            satellite = ""
        panel = str((data or {}).get("panel") or "").strip()
        self.act(asked[0], asked[1], satellite, panel)

    def start(self):
        if not self.base or not self.token:
            self.say("route", "no way to reach Home Assistant, so links "
                     "cannot be opened by voice. Under Home Assistant this "
                     "cannot happen.")
            return self
        for name in self.dropped:
            self.say(f"drop:{name}", f"the link \"{name}\" has nothing in "
                     f"its name that can be said, so it cannot be opened by "
                     f"voice. Give it a name made of words, or words to say "
                     f"in its voice: setting.")
        for name, said in self.clashes:
            self.say(f"clash:{name}:{said}", f"\"{said}\" in the voice: of "
                     f"\"{name}\" is already the name or a word of another "
                     f"link, so it is left out -- said aloud it could open "
                     f"either one.")
        threading.Thread(target=self._run, daemon=True,
                         name="portall-voice-links").start()
        return self

    def _run(self):
        wait = self.RETRY_FIRST_S
        synced = False
        while True:
            began = time.monotonic()
            try:
                if not synced:
                    synced = self.sync()
                self._session()
            except (OSError, Closed, ValueError) as err:
                self.say(f"lost:{type(err).__name__}",
                         f"lost Home Assistant ({err}); trying again.")
            if time.monotonic() - began > 60:
                wait = self.RETRY_FIRST_S
            time.sleep(wait)
            wait = min(wait * 2, self.RETRY_MAX_S)

    def _session(self):
        link = Socket(self.url)
        try:
            first = link.recv()
            if first.get("type") != "auth_required":
                raise Closed(f"said {first.get('type')!r} before asking for "
                             f"a token")
            link.send({"type": "auth", "access_token": self.token})
            if link.recv().get("type") != "auth_ok":
                self.say("refused", "Home Assistant refused this add-on's "
                         "token, so links cannot be opened by voice.")
                return
            link.send({"id": 1, "type": "subscribe_events",
                       "event_type": EVENT})
            # Home Assistant and the Supervisor's proxy both ping an idle
            # connection, and a ping is answered inside recv(), so a read this
            # long means the connection is gone.
            link.settimeout(180)
            while True:
                message = link.recv()
                if message.get("id") != 1:
                    continue
                if message.get("type") == "result":
                    if not message.get("success", False):
                        self.say("subscribe", "Home Assistant would not let "
                                 "this add-on listen for its event.")
                        return
                    self.say("listening", f"listening for \"ouvre <link>\" "
                             f"({len(self.names)} link(s))")
                    continue
                event = message.get("event") or {}
                try:
                    self.handle(event.get("data"))
                except Exception as err:  # noqa: BLE001 - never the picture
                    self._say(f"Voice links: could not act on {EVENT} "
                              f"({err})")
        finally:
            link.close()


def remove(base, token, say):
    """Take the automation away when voice_links is off. Quiet if absent."""
    if not base or not token:
        return
    helper = VoiceLinks(base, token, [], None, say)
    try:
        status, _ = helper._request("GET")
        if status == 200:
            status, answer = helper._request("DELETE")
            if status == 200:
                say("Voice links: off, so the automation \"Portall : ouvrir "
                    "un lien à la voix\" was removed from Home Assistant")
    except OSError:
        pass
