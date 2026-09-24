#!/usr/bin/env python3
"""The HomeKit remote, driven the way an iPhone drives it.

What this exists to catch is the JOIN. Three shipped pieces sit between the
widget in somebody's Control Centre and a tile moving on the glass, and they
are in two files and two processes:

    pyhap's RemoteKey  ->  homekit.Television.remote_key
                       ->  run.Remote.send        (a line down a pipe)
                       ->  ha_send.Control        (the same pairs the loop
                                                   already acts on)

Every fault this repository keeps recording lives exactly there: two ends
that are each correct and a middle nobody ran. So the accessory is BUILT with
the real pyhap, the key is pressed through the real characteristic, and the
line that comes out is fed to the real Control.

Needs HAP-python:  pip install "HAP-python[QRCode]"
"""

import io
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))

import homekit  # noqa: E402
import run  # noqa: E402
from udisp_send import BROWSER_KEYS  # noqa: E402
from ha_send import Control, PageAudio  # noqa: E402


# What each HomeKit key is FOR, stated here rather than read out of the table
# being checked. The first version of this file took its expectation from
# homekit.ACTIONS itself, so folding Exit onto Escape -- the exact fault this
# project already made once, where EXIT and ROOT_MENU both meant Escape and no
# button went home -- passed every case. A test that restates the thing it
# checks proves only that it can copy.
#
# The split is the television one: Back goes back WITHIN the page, the TV
# button leaves it for the panel's own url.
# The keys the iOS Control Centre remote can actually SEND to a HomeKit
# television, which is not the same as the keys HomeKit defines. Five
# buttons: the pad, Select, Back, Play/Pause and the ⓘ. Stated here because
# it is the fact the mapping has to satisfy -- the first version put the way
# home on Exit, which is in HomeKit's table and on no button of this widget,
# so a panel could not leave a link.
WIDGET = {"ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Select",
          "Back", "PlayPause", "Information"}

WANT = {
    "ArrowUp": ("key", "ArrowUp"),
    "ArrowDown": ("key", "ArrowDown"),
    "ArrowLeft": ("key", "ArrowLeft"),
    "ArrowRight": ("key", "ArrowRight"),
    "Select": ("key", "Enter"),
    "Back": ("home", True),
    "Information": ("key", "Escape"),
    "Exit": ("home", True),
    "PlayPause": ("key", "MediaPlayPause"),
    "NextTrack": ("key", "MediaTrackNext"),
    "PreviousTrack": ("key", "MediaTrackPrevious"),
}

faults = []


def check(what, ok):
    print(("  ok     " if ok else "  ECHEC  ") + what)
    if not ok:
        faults.append(what)


class FakeProcess:
    """Something with a stdin, standing in for a sender."""

    def __init__(self):
        self.stdin = io.StringIO()
        self.closed = False

    def close(self):
        self.closed = True
        self.stdin.close()


def quiet(*_args, **_kwargs):
    pass


def spoken():
    lines = []
    return lines, lambda text: lines.append(text)


# -- the accessory, built with the real pyhap ----------------------------

def accessory_cases():
    try:
        import pyhap  # noqa: F401
    except ImportError:
        print("  --     HAP-python is not installed, so the accessory itself "
              "was not built. pip install \"HAP-python[QRCode]\"")
        return

    table = homekit.valid_values()
    folder = tempfile.mkdtemp()
    seen = []
    said, say = spoken()
    television = homekit.Television(
        "Salon", lambda kind, body: seen.append((kind, body)), say,
        homekit.FIRST_PORT, folder)
    driver = homekit._driver(homekit.FIRST_PORT,
                             os.path.join(folder, "salon.state"),
                             os.path.join(folder, "salon.pin"))
    accessory = homekit._accessory(driver, television)

    service = accessory.get_service("Television")
    char = service.get_characteristic("RemoteKey")

    # The category is what puts it in the remote list rather than in the
    # Home app as a nameless box.
    from pyhap.const import CATEGORY_TELEVISION
    check("the accessory is a Television, which is what the widget lists",
          accessory.category == CATEGORY_TELEVISION)

    # Home Assistant's own Television accessory adds no InputSource at all
    # when the player cannot select one, so this matches a shipped shape.
    present = sorted(c.display_name for c in service.characteristics)
    check("it carries RemoteKey and the four required characteristics",
          present == ["Active", "ActiveIdentifier", "ConfiguredName",
                      "RemoteKey", "SleepDiscoveryMode"])
    check("and no input sources, like Home Assistant's own when there are none",
          not any(s.display_name == "InputSource" for s in accessory.services))

    # Every key, pressed through the real characteristic, against what this
    # file says it is for.
    for name, want in WANT.items():
        seen.clear()
        char.client_update_value(table[name])
        check(f"HomeKit {name} reaches the panel as {want}", seen == [want])
    check("and it maps exactly those, no more",
          set(homekit.ACTIONS) == set(WANT))
    # THE case this round exists for. A panel's one indispensable remote
    # action is getting back to its own page, and it has to sit on a button
    # the widget owns.
    reachable = [name for name in WIDGET
                 if homekit.ACTIONS.get(name, (None,))[0] == "home"]
    check("a button the widget REALLY HAS goes home", reachable != [])
    check("and the pad and Select are not it",
          not ({"ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Select"}
               & set(reachable)))
    check("every key this maps is one HomeKit defines",
          set(homekit.ACTIONS) <= set(table))

    # And one it does not map. Rewind has nowhere sensible to go on a page.
    seen.clear()
    said.clear()
    char.client_update_value(table["Rewind"])
    check("an unmapped key sends nothing", seen == [])
    check("and says so once", len(said) == 1 and "Rewind" not in said[0])
    said.clear()
    char.client_update_value(table["Rewind"])
    check("and not twice", said == [])

    # Active is recorded and reaches no panel: a remote turning a screen off
    # is the BOARD's decision, in its own YAML.
    seen.clear()
    service.get_characteristic("Active").client_update_value(0)
    check("HomeKit's on/off touches no panel", seen == [])


def pin_cases():
    try:
        import pyhap  # noqa: F401
    except ImportError:
        return
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "salon.pin")
    from pyhap.state import State
    first = homekit.read_pin(path, lambda: State().pincode)
    second = homekit.read_pin(path, lambda: State().pincode)
    # pyhap's encoder persists the MAC, the keys and the paired clients and
    # NOT the pincode, so without this the code in the log is a different one
    # after every restart -- which is exactly the minutes somebody is reading
    # it off the screen and typing it in.
    check("the pairing code is the same after a restart", first == second)
    other = homekit.read_pin(os.path.join(folder, "cuisine.pin"),
                             lambda: State().pincode)
    check("and a second panel gets one of its own", other != first)


# -- the join with the sender --------------------------------------------

def name_cases():
    """The names this produces have to be names the sender can press.

    Derived rather than listed: a browser key name is either one the board's
    own table already uses, or a media key, which the browser's keyboard
    answers for itself and says so in one line if it cannot.
    """
    known = set(BROWSER_KEYS.values())
    stray = [body for kind, body in homekit.ACTIONS.values()
             if kind == "key" and body not in known
             and not body.startswith("Media")]
    check("every key name it sends is one the sender already knows",
          stray == [])
    check("and the arrows are among them",
          {("key", "ArrowUp"), ("key", "ArrowLeft")} <= set(homekit.ACTIONS.values()))
    check("the shipped table is what this file says it should be",
          homekit.ACTIONS == WANT)


def round_trip_cases():
    """From the accessory's own callback to the pairs the loop acts on.

    This is the whole point of the file: three shipped pieces, two files, one
    pipe, and the answer read out of the last one rather than asserted at
    each end.
    """
    if not _has_pyhap():
        return
    numbers = homekit.valid_values()
    table = homekit.actions_by_number()
    process = FakeProcess()
    remote = run.Remote("Salon")
    remote.set_process(process)
    television = homekit.Television("Salon", remote.send, quiet,
                                    homekit.FIRST_PORT, tempfile.mkdtemp())

    pressed = ("ArrowUp", "ArrowLeft", "Select", "Back", "Exit")
    for name in pressed:
        television.remote_key(numbers[name], table)
    want = [WANT[name] for name in pressed]

    control = Control(io.StringIO(process.stdin.getvalue()))
    control._thread.join(timeout=2)
    got = control.drain()
    check("what the accessory pressed is what the send loop is handed",
          got == want)
    check("and home crosses as its own kind, not as a key named home",
          ("home", True) in got and ("key", "home") not in got)


def _has_pyhap():
    try:
        import pyhap  # noqa: F401
    except ImportError:
        return False
    return True


def control_cases():
    control = Control(io.StringIO("key ArrowDown\nhome\nnonsense\n\nkey Enter\n"))
    control._thread.join(timeout=2)
    got = control.drain()
    check("stdin yields exactly the pairs the return channel yields",
          got == [("key", "ArrowDown"), ("home", True), ("key", "Enter")])
    check("and draining twice does not repeat them", control.drain() == [])


# -- the volume and mute, which the widget had nothing to send to --------

# What each press should leave the page at, stated here rather than read out
# of homekit.gain_for: ten steps, squared, so one press down from full is
# 0.9 squared.
AFTER_ONE_DOWN = 0.81
AFTER_TWO_DOWN = 0.64


def volume_accessory_cases():
    """The TelevisionSpeaker, built with the real pyhap and pressed through it."""
    if not _has_pyhap():
        return
    folder = tempfile.mkdtemp()
    seen = []
    television = homekit.Television(
        "Salon", lambda kind, body: seen.append((kind, body)), quiet,
        homekit.FIRST_PORT, folder)
    driver = homekit._driver(homekit.FIRST_PORT,
                             os.path.join(folder, "salon.state"),
                             os.path.join(folder, "salon.pin"))
    accessory = homekit._accessory(driver, television)
    tv = accessory.get_service("Television")
    speaker = accessory.get_service("TelevisionSpeaker")

    # THE case. With no speaker service the widget's volume and mute have
    # nothing to go to, which is the one control a panel reported dead.
    check("there is a TelevisionSpeaker, which is where volume and mute go",
          speaker is not None)
    if speaker is None:
        return
    check("and it is LINKED to the television, or the widget never finds it",
          speaker in tv.linked_services)
    present = sorted(c.display_name for c in speaker.characteristics)
    check("it carries what Home Assistant's own carries for step and mute",
          present == ["Active", "Mute", "Name", "VolumeControlType",
                      "VolumeSelector"])
    check("relative control, as Home Assistant's with no level offered",
          speaker.get_characteristic("VolumeControlType").value == 2)

    selector = speaker.get_characteristic("VolumeSelector")
    mute = speaker.get_characteristic("Mute")
    values = selector.properties["ValidValues"]
    up, down = values["Increment"], values["Decrement"]

    seen.clear()
    selector.client_update_value(down)
    check("the iPhone's volume-down lowers the page's sound",
          seen == [("volume", f"{AFTER_ONE_DOWN:.4f}")])
    seen.clear()
    selector.client_update_value(down)
    check("and a second press lowers it further",
          seen == [("volume", f"{AFTER_TWO_DOWN:.4f}")])
    seen.clear()
    mute.client_update_value(True)
    check("mute silences it", seen == [("volume", "0.0000")])
    seen.clear()
    mute.client_update_value(False)
    check("and unmute returns to the level it was at, not to full",
          seen == [("volume", f"{AFTER_TWO_DOWN:.4f}")])
    mute.client_update_value(True)
    seen.clear()
    selector.client_update_value(up)
    check("volume-up while muted unmutes, as a television does",
          seen == [("volume", f"{AFTER_ONE_DOWN:.4f}")] and not television.muted)
    for _ in range(20):
        selector.client_update_value(up)
    seen.clear()
    selector.client_update_value(up)
    check("and it never goes past the page's own level",
          seen == [("volume", "1.0000")])
    for _ in range(20):
        selector.client_update_value(down)
    check("nor below nothing", television.level == 0
          and homekit.gain_for(television.level, False) == 0.0)


def volume_round_trip_cases():
    """From the accessory to the gain the sender applies, across the pipe."""
    process = FakeProcess()
    remote = run.Remote("Salon")
    remote.set_process(process)
    television = homekit.Television("Salon", remote.send, quiet,
                                    homekit.FIRST_PORT, tempfile.mkdtemp())
    television.volume_step(1)
    television.remote_key(-1, {})  # an unknown key between, sending nothing
    control = Control(io.StringIO(process.stdin.getvalue()))
    control._thread.join(timeout=2)
    got = control.drain()
    check("a volume press reaches the send loop as a gain",
          got == [("volume", AFTER_ONE_DOWN)])

    # A sender that restarts must not come back at full volume.
    process.close()
    remote.set_process(None)
    second = FakeProcess()
    remote.set_process(second)
    check("a restarted sender is told the volume at once, with no press",
          second.stdin.getvalue() == f"volume {AFTER_ONE_DOWN:.4f}\n")

    control = Control(io.StringIO("volume 0.25\nvolume 7\nvolume loud\n"))
    control._thread.join(timeout=2)
    check("a gain outside 0..1 is refused, not applied",
          control.drain() == [("volume", 0.25)])


def gain_cases():
    """What PageAudio actually sends at a gain, sample by sample."""
    audio = PageAudio("salon")
    samples = [1000, -1000, 32767, -32768, 7, 0]
    block = b"".join(v.to_bytes(2, "little", signed=True) for v in samples)

    def sent(gain):
        audio.gain = gain
        audio._blocks.append(block)
        out = audio.take()
        if not out:
            return None
        data = out[0]
        return [int.from_bytes(data[i:i + 2], "little", signed=True)
                for i in range(0, len(data), 2)]

    check("at full volume the page's samples go out untouched",
          sent(1.0) == samples)
    check("at a quarter every sample is a quarter, negatives included",
          sent(0.25) == [250, -250, 8192, -8192, 2, 0])
    before = audio.silent
    check("muted, nothing is sent at all", sent(0.0) is None)
    check("and it is counted as silence, like a page playing nothing",
          audio.silent == before + 1)


# -- the restart, which is the fault a naive version would have ----------

def restart_cases():
    first = FakeProcess()
    remote = run.Remote("Salon")
    remote.set_process(first)
    remote.send("key", "ArrowUp")

    # The sender dies and is started again on the backoff, which is ordinary.
    first.close()
    remote.set_process(None)
    second = FakeProcess()
    remote.set_process(second)
    remote.send("key", "ArrowDown")

    check("a press after the sender restarted reaches the NEW one",
          second.stdin.getvalue() == "key ArrowDown\n")

    # The version this is not: a remote that grabbed the pipe once. Written
    # out rather than described, because the point is that it FAILS.
    naive_pipe = first.stdin
    try:
        naive_pipe.write("key ArrowDown\n")
        naive_ok = True
    except ValueError:
        naive_ok = False
    check("where holding the first pipe would have written into a dead one",
          not naive_ok)


def nothing_running_cases():
    said, say = spoken()
    remote = run.Remote("Salon")
    saved = run.say
    run.say = say
    try:
        ok = remote.send("key", "ArrowUp")
        again = remote.send("key", "ArrowUp")
    finally:
        run.say = saved
    check("a press with no sender running is dropped rather than raising",
          ok is False and again is False)
    check("and said once, not on every press", len(said) == 1)


# -- the add-on's own line ------------------------------------------------

def command_cases():
    """homekit: true has to put --control on the sender's line.

    This one is not generic: checkaddon's option sweep passes over `homekit`
    because it emits a flag of another name, so the thing it emits is checked
    here instead of nowhere.
    """
    def line(options):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        pathlib.Path(path).write_text(json.dumps(options))
        previous = os.environ.get("UDISP_CONFIG")
        os.environ["UDISP_CONFIG"] = path
        try:
            run._config = {}
            panels = run.load_panels()
            return run.command_for(panels[0]) if panels else []
        finally:
            os.unlink(path)
            if previous is None:
                os.environ.pop("UDISP_CONFIG", None)
            else:
                os.environ["UDISP_CONFIG"] = previous

    panel = {"name": "salon", "host": "1.2.3.4", "url": "http://x/",
             "width": 800, "height": 1280}
    on = line({"panels": [dict(panel)], "defaults": {"homekit": True}})
    off = line({"panels": [dict(panel)], "defaults": {"homekit": False}})
    check("homekit: true opens the sender's control channel",
          "--control" in on)
    check("and a panel that did not ask for one is untouched",
          "--control" not in off)


def main():
    print("The HomeKit remote:")
    accessory_cases()
    pin_cases()
    name_cases()
    control_cases()
    round_trip_cases()
    volume_accessory_cases()
    volume_round_trip_cases()
    gain_cases()
    restart_cases()
    nothing_running_cases()
    command_cases()
    if faults:
        print(f"\n{len(faults)} problem(s).")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
