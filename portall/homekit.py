"""A HomeKit television per panel, so a telephone is the remote.

The ask this answers, in the household's own words: pair it, unpair it, and
have it work. Not "write a universal media_player, expose it to HomeKit in
accessory mode, and add an automation" -- which is the route Home Assistant
documents, is three things to configure, and is a mechanism somebody has to
operate rather than a thing that exists.

Why it lives HERE, in the add-on, and not on the board: the widget in an
iPhone's Control Centre -- the one built for an Apple TV -- drives any
HomeKit accessory of the **Television** category, and the key it sends has to
reach whatever renders the page. That is this add-on. The board never sees a
byte of this, no Bluetooth is involved, and nothing is paired to the panel.

Two facts from Home Assistant's own HomeKit code decided the shape, read
rather than assumed:

  * A Television accessory may NOT be bridged -- their documentation says
    `mode` must be `accessory` with a single entity. So this is one accessory,
    one driver, one port and one pairing code PER PANEL, rather than one
    bridge carrying all of them.
  * A Television with NO input sources at all is a shipped configuration:
    `RemoteInputSelectAccessory` returns before adding a single InputSource
    when the media player cannot select one. A panel has no inputs, so this
    accessory is a remote and nothing else, and that is not a stunt.

An accessory must never cost the picture. Every failure here is caught, said
once, and the panels carry on rendering -- the same rule the on-screen
keyboard, the launcher and the token all live under.
"""

import json
import os
import threading


# Names, never numbers. pyhap carries HomeKit's own ValidValues for RemoteKey,
# so the integers are read from the specification's table at run time instead
# of being transcribed here -- a hand-copied pair of constants is the failure
# this repository keeps paying for.
#
# The split between Back and Exit is the television one this project already
# made for a Bluetooth remote: Back goes back WITHIN the page, Exit leaves it
# for the panel's own url. Whichever button of the widget sends which, the
# household ends up with one of each, and the log names both the first time
# they arrive so it can be read rather than guessed at.
ACTIONS = {
    "ArrowUp": ("key", "ArrowUp"),
    "ArrowDown": ("key", "ArrowDown"),
    "ArrowLeft": ("key", "ArrowLeft"),
    "ArrowRight": ("key", "ArrowRight"),
    "Select": ("key", "Enter"),
    # Back goes HOME, and Escape sits on the ⓘ button. That is the opposite
    # of the split this project made for a Bluetooth remote, and the reason
    # is that the two devices do not have the same buttons.
    #
    # The iOS Control Centre remote gives a HomeKit television exactly five:
    # the pad, Select, Back, Play/Pause and ⓘ. There is NO button that sends
    # Exit -- so mapping the way home onto Exit put it somewhere nobody could
    # press, which is this project's most-recorded fault in a fresh costume:
    # a fix the reader cannot reach from where they are standing. Reported
    # from a panel as being unable to leave a link.
    #
    # Of the two, Back is the one somebody reaches for to leave a page, and
    # on a panel leaving the page IS the launcher. Escape can afford the
    # obscure button because it does nothing at all on nearly every page one
    # of these shows -- Home Assistant has no use for it, the launcher has
    # none, and Jellyfin only listens in its TV layout.
    "Back": ("home", True),
    "Information": ("key", "Escape"),
    # Kept although this widget never sends it: another HomeKit controller
    # may, and a television's Exit means the same thing as its Back here.
    "Exit": ("home", True),
    # Worth attempting rather than leaving out: a panel showing a film is what
    # these are for. The sender presses them through the browser's keyboard,
    # which says so in one line if a name means nothing to it -- so the cost
    # of being wrong here is a log line, not a silence.
    "PlayPause": ("key", "MediaPlayPause"),
    "NextTrack": ("key", "MediaTrackNext"),
    "PreviousTrack": ("key", "MediaTrackPrevious"),
}

# The first port this uses. Home Assistant's own HomeKit bridge defaults to
# 21063 and gives each accessory-mode entity one of its own above that, and
# this add-on may be on the same host network -- so start well clear of it.
FIRST_PORT = 21180


def valid_values(pyhap_module=None):
    """HomeKit's RemoteKey table, out of pyhap rather than out of memory."""
    if pyhap_module is None:
        import pyhap as pyhap_module  # noqa: PLC0415
    path = os.path.join(
        os.path.dirname(pyhap_module.__file__), "resources", "characteristics.json"
    )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["RemoteKey"]["ValidValues"]


def actions_by_number(pyhap_module=None):
    """{RemoteKey integer: (kind, body)} for the keys this maps."""
    table = valid_values(pyhap_module)
    out = {}
    for name, what in ACTIONS.items():
        number = table.get(name)
        if number is None:
            # pyhap and this table disagreeing is a thing to say, not to
            # swallow: it means HomeKit renamed a key under us.
            continue
        out[number] = what
    return out


def slug(name):
    """A file name from a panel's name. Never empty, never a path."""
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in str(name).strip()]
    out = "".join(keep).strip("-") or "panel"
    return out[:48]


def read_pin(path, make_pin):
    """The pairing code for one panel, the same one across restarts.

    pyhap does NOT persist the pincode -- its encoder writes the MAC, the
    keys, the paired clients and the config version, and nothing else -- so a
    code left to it is a fresh one on every start. That matters for exactly
    the minutes it has to: somebody reading the code off the log, restarting
    the add-on for an unrelated reason, and then typing a code that is no
    longer the one being asked for.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            pin = handle.read().strip()
        if pin:
            return pin.encode()
    except OSError:
        pass
    pin = make_pin()
    if isinstance(pin, bytes):
        pin = pin.decode()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(pin)
    except OSError:
        # A code that cannot be written is a code that changes next restart,
        # which is a nuisance and not a failure. Pair now and it still works.
        pass
    return pin.encode()


class Television:
    """One panel's HomeKit accessory, driven from its own thread."""

    def __init__(self, name, send, say, port, persist_dir, make_driver=None,
                 make_accessory=None):
        self.name = name
        self.port = port
        self._send = send
        self._say = say
        self._persist_dir = persist_dir
        self._make_driver = make_driver or _driver
        self._make_accessory = make_accessory or _accessory
        self._driver = None
        self._thread = None
        self._seen = set()
        self._active = True

    # -- what the widget presses ------------------------------------------

    def remote_key(self, value, table=None):
        """A press from the Control Centre remote. Never raises."""
        if table is None:
            table = actions_by_number()
        what = table.get(value)
        if what is None:
            if value not in self._seen:
                self._seen.add(value)
                self._say(f"[{self.name}] HomeKit remote key {value} is not "
                          f"one this maps to anything")
            return False
        kind, body = what
        if value not in self._seen:
            self._seen.add(value)
            # Once per key, so the first press of each button says which of
            # them the widget actually sends -- the one thing about that
            # widget this project could not find written down.
            self._say(f"[{self.name}] HomeKit remote: "
                      f"{'home' if kind == 'home' else body}")
        try:
            self._send(kind, body)
        except Exception as problem:  # noqa: BLE001
            self._say(f"[{self.name}] HomeKit remote key would not reach the "
                      f"panel ({problem})")
            return False
        return True

    def set_active(self, value):
        """HomeKit's on/off. Recorded so the widget shows a live television.

        It deliberately does nothing else: the backlight and portall.sleep are
        the BOARD's, in its own YAML, and a remote reaching in to turn a panel
        off would be this add-on taking over a decision that is not its own.
        """
        self._active = bool(value)

    # -- lifetime ----------------------------------------------------------

    def start(self):
        pin_path = os.path.join(self._persist_dir, slug(self.name) + ".pin")
        state_path = os.path.join(self._persist_dir, slug(self.name) + ".state")
        try:
            self._driver = self._make_driver(self.port, state_path, pin_path)
            accessory = self._make_accessory(self._driver, self)
            self._driver.add_accessory(accessory)
        except Exception as problem:  # noqa: BLE001
            self._say(f"[{self.name}] no HomeKit remote: {problem}")
            self._driver = None
            return False
        code = self._driver.state.pincode.decode()
        self._say(f"[{self.name}] HomeKit remote \"{self.name}\" is waiting to "
                  f"be paired on port {self.port} -- open the Home app on the "
                  f"iPhone, Add Accessory, More options, and enter {code}")
        try:
            accessory.setup_message()
        except Exception:  # noqa: BLE001
            # The QR code is a convenience; the digits above are the thing.
            pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        try:
            self._driver.start()
        except Exception as problem:  # noqa: BLE001
            self._say(f"[{self.name}] the HomeKit remote stopped: {problem}")

    def stop(self):
        if self._driver is None:
            return
        try:
            self._driver.stop()
        except Exception:  # noqa: BLE001
            pass


def _driver(port, state_path, pin_path):
    from pyhap.accessory_driver import AccessoryDriver  # noqa: PLC0415
    from pyhap.state import State  # noqa: PLC0415

    pin = read_pin(pin_path, lambda: State().pincode)
    return AccessoryDriver(port=port, persist_file=state_path, pincode=pin)


def _accessory(driver, television):
    from pyhap.accessory import Accessory  # noqa: PLC0415
    from pyhap.const import CATEGORY_TELEVISION  # noqa: PLC0415

    table = actions_by_number()

    class PanelRemote(Accessory):
        category = CATEGORY_TELEVISION

        def __init__(self):
            super().__init__(driver, television.name)
            self.set_info_service(
                manufacturer="Portall",
                model="Panel",
                serial_number=slug(television.name),
            )
            # add_preload_service brings the Television service's required
            # characteristics with it -- Active, ActiveIdentifier,
            # ConfiguredName and SleepDiscoveryMode -- and RemoteKey is the
            # optional one this exists for.
            service = self.add_preload_service("Television", chars=["RemoteKey"])
            service.configure_char("RemoteKey", setter_callback=self._pressed)
            service.configure_char("ConfiguredName", value=television.name)
            service.configure_char("SleepDiscoveryMode", value=True)
            service.configure_char("Active", value=1,
                                   setter_callback=television.set_active)
            self.set_primary_service(service)
            # ActiveIdentifier is left unconfigured on purpose: Home
            # Assistant's own accessory does exactly this when there are no
            # input sources, and a panel has none.

        def _pressed(self, value):
            television.remote_key(value, table)

    return PanelRemote()


def start(remotes, persist_dir, say, first_port=FIRST_PORT):
    """One accessory per panel. Returns the ones that came up.

    `remotes` is a list of (name, send) -- send(kind, body) is what puts the
    press on that panel's own sender.
    """
    try:
        import pyhap  # noqa: F401,PLC0415
    except ImportError:
        say("No HomeKit remote: this build has no HAP-python in it. "
            "The panels are unaffected.")
        return []
    started = []
    for index, (name, send) in enumerate(remotes):
        television = Television(name, send, say, first_port + index, persist_dir)
        if television.start():
            started.append(television)
    return started
