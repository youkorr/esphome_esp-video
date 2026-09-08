#!/usr/bin/env python3
"""Send a region of this computer's screen to an ESPHome wired_portall board.

The board speaks Espressif's udisp protocol over a USB vendor interface: a
16-byte header followed by a JPEG of the whole region. Espressif ship a Windows
driver for this; the point of the script is to not need it -- pyusb works on
Linux, macOS and Windows alike.

    ./udisp_send.py --width 1024 --height 600

It waits for the board rather than failing when it is not plugged in, and goes
back to waiting if it is unplugged or reflashed, so it can be left running. To
have it start at every login on Windows, add --install-startup to the options
you want; --uninstall-startup takes it back out.

Requirements:

    pip install mss pillow numpy zeroconf    # over the network
    pip install pyusb mss pillow numpy libusb-package   # over a cable

On Windows this same file is also shipped frozen, as portall.exe, which is
what the panel's own waiting screen asks for: one download, double-clicked,
with no Python and no pip. It is built by .github/workflows/portall-exe.yml on
a Windows runner, because PyInstaller cannot cross-compile. Everything below
applies to it unchanged -- portall.exe --install-startup is the same command
with the same effect.

There is also a script beside this one, windows/setup.ps1, that does the whole
of the PC side in one line for somebody who would rather have the Python.

Over the network, nothing has to be typed at all: the board advertises its
address and the shape of its panel from its own ESPHome configuration, so

    ./udisp_send.py --discover

finds it and takes the rest from what it said. See
yaml/ws-wired-portall.yaml for the mdns: block that does the advertising.

libusb-package is what supplies the libusb library pyusb needs. On Linux and
macOS the system one is used if it is already installed, so it is optional
there; on Windows it is the easy way out of hunting for a DLL.

Access to the device:

  Linux   a udev rule, or run as root. Without it pyusb cannot claim the
          interface. Example, as /etc/udev/rules.d/99-udisp.rules:
              SUBSYSTEM=="usb", ATTR{idVendor}=="303a", ATTR{idProduct}=="4001", MODE="0666"
  macOS    libusb is enough (brew install libusb); nothing claims a vendor
          interface, so it is free to take.
  Windows  the interface needs the WinUSB driver. The board advertises
          Microsoft OS 2.0 descriptors, so Windows 8 and later bind WinUSB by
          themselves the first time it is plugged in. Zadig is only the
          fallback for when that did not happen -- and it binds the same
          WinUSB, so a board already set up with it keeps working.
"""

import argparse
import io
import os
import struct
import sys
import threading
import time

# Payload types from Espressif's udisp protocol. Only JPEG is implemented on
# the board side; the others exist in their header and are listed for context.
UDISP_TYPE_RGB565 = 0
UDISP_TYPE_RGB888 = 1
UDISP_TYPE_YUV420 = 2
UDISP_TYPE_JPG = 3
# WiredPortall's own addition to Espressif's set: a block of PCM for the panel's
# speaker, so the sound of the page arrives with the picture of it. Home
# Assistant's own audio does not come this way -- the board has a media_player
# of its own and always did.
UDISP_TYPE_PCM = 0x10
AUDIO_RATE = 48000
AUDIO_BITS = 16
AUDIO_CHANNELS = 1
# Not a picture: the host marking the end of what it was sending. The board
# counts its payload out and says nothing, which makes an empty one a heartbeat.
UDISP_TYPE_END = 0xFF

# crc16, type, cmd, x, y, width, height, then a packed word holding a 10-bit
# frame id and a 22-bit payload length. Little-endian, 16 bytes, and it is
# Espressif's layout -- the firmware parses it byte for byte.
_HEADER = struct.Struct("<HBBHHHHI")

DEFAULT_VID = 0x303A
DEFAULT_PID = 0x4001


def build_header(width, height, payload_len, frame_id, x=0, y=0):
    """One rectangle's header.

    x and y default to the top left because this sender redraws the whole panel
    every time. They exist because the protocol has always carried them: a
    sender that knows what changed sends only that rectangle, and every
    rectangle of one picture shares its frame_id so the board admits or drops
    them together.
    """
    if payload_len >= 1 << 22:
        raise ValueError(
            f"payload of {payload_len} bytes does not fit the 22-bit length field"
        )
    packed = (frame_id & 0x3FF) | ((payload_len & 0x3FFFFF) << 10)
    # crc16 and cmd are unused by the board; it validates on the geometry and
    # the length instead.
    return _HEADER.pack(0, UDISP_TYPE_JPG, 0, x, y, width, height, packed)


def build_audio_header(payload_len):
    """One block of sound's header.

    The same sixteen bytes as a rectangle, and deliberately so: one definition
    of the wire format is worth more than a tidier one for each kind of thing
    on it. A rectangle's geometry means nothing here, so x, y, width and height
    go out as zero and the board reads only the length -- and the frame id goes
    out as zero too, because sound is not admitted or dropped in pictures.

    What follows is signed 16-bit little-endian samples at AUDIO_RATE, one
    channel. Mono because these panels have one speaker, and because it halves
    what the network carries: 96 KiB/s beside a picture that can want two
    megabytes.
    """
    if payload_len >= 1 << 22:
        raise ValueError(
            f"payload of {payload_len} bytes does not fit the 22-bit length field"
        )
    packed = (payload_len & 0x3FFFFF) << 10
    return _HEADER.pack(0, UDISP_TYPE_PCM, 0, 0, 0, 0, 0, packed)


def build_heartbeat():
    """Sixteen bytes that say "still here" and nothing else.

    A sender that only transmits what changed is silent while nothing changes,
    and a board cannot tell that apart from a sender that has died. This is the
    protocol's end-of-frame marker carrying no payload: the board counts out
    zero bytes and carries on, so it costs one header and means only that the
    connection is alive.
    """
    return _HEADER.pack(0, UDISP_TYPE_END, 0, 0, 0, 0, 0, 0)


def _bundled_backend():
    """The libusb that libusb-package ships, if it is installed.

    pyusb is only a wrapper: it needs a libusb shared library, and finds one on
    Linux and macOS through the system package manager. Windows has no such
    thing, so rather than sending people to copy a DLL by hand, pick up the one
    libusb-package bundles when it is available.
    """
    try:
        import libusb_package
    except ImportError:
        return None
    for name in ("get_libusb1_backend", "get_libusb1_backend_"):
        getter = getattr(libusb_package, name, None)
        if getter is not None:
            try:
                return getter()
            except Exception:  # noqa: BLE001 - any failure just means "no bundled backend"
                return None
    return None


def find_endpoint(vid, pid):
    import usb.core
    import usb.util

    backend = _bundled_backend()
    try:
        device = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
    except usb.core.NoBackendError as err:
        raise SystemExit(
            "pyusb found no libusb backend.\n"
            "  Windows  pip install libusb-package   (it bundles the DLL)\n"
            "  Linux    install libusb-1.0-0 from your package manager\n"
            "  macOS    brew install libusb"
        ) from err

    if device is None:
        return None, None

    # Linux binds nothing to a vendor interface, but be explicit rather than
    # failing on a busy interface somewhere else.
    try:
        if device.is_kernel_driver_active(0):
            device.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass

    device.set_configuration()
    interface = device.get_active_configuration()[(0, 0)]
    endpoint = usb.util.find_descriptor(
        interface,
        custom_match=lambda e: (
            usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        ),
    )
    if endpoint is None:
        raise SystemExit("The device has no bulk OUT endpoint on its first interface")
    return device, endpoint


def parse_messages(buffer):
    """Pull whole messages out of a byte buffer from the board.

    Two kinds travel this way, and they are told apart by their first byte:

      b"T", a contact count, then five bytes per contact -- an identifier and a
      little-endian x and y, in the panel's own pixels, already corrected by the
      touch screen's own transform. A count of zero is a release.

      b"S", then 1 if the panel is awake and 0 if it has gone to sleep. A
      sleeping panel is showing nothing to nobody, so there is no sense
      rendering or sending for it.

    Returns ("touch", contacts) and ("awake", bool) pairs, and whatever tail is
    still short of a whole message so the caller can hand it back next time.
    """
    messages = []
    at = 0
    while True:
        if len(buffer) - at < 2:
            break
        kind = buffer[at]
        if kind == ord("S"):
            messages.append(("awake", bool(buffer[at + 1])))
            at += 2
            continue
        if kind != ord("T"):
            # Not a message boundary: skip a byte rather than reading a length
            # out of the middle of something.
            at += 1
            continue
        count = buffer[at + 1]
        end = at + 2 + count * 5
        if len(buffer) < end:
            break
        contacts = []
        for i in range(count):
            base = at + 2 + i * 5
            contacts.append(
                (
                    buffer[base],
                    buffer[base + 1] | (buffer[base + 2] << 8),
                    buffer[base + 3] | (buffer[base + 4] << 8),
                )
            )
        messages.append(("touch", contacts))
        at = end
    return messages, buffer[at:]


class _TcpEndpoint:
    """A socket dressed as the endpoint object the send loop already uses.

    It also drains the board's return channel. This sender does nothing with
    the touches -- it mirrors a desktop that already has its own pointer, and
    over the cable the same contacts arrive as HID, which every operating
    system understands by itself. Draining still matters: a socket nobody reads
    fills its window and eventually costs the board a send. read_touches() is
    where a sender that renders its own page picks them up instead.
    """

    def __init__(self, sock):
        self._sock = sock
        self._tail = b""

    def write(self, data):
        self._sock.sendall(data)

    def read_messages(self):
        """Whatever the board has said since the last call. Never blocks.

        select() rather than MSG_DONTWAIT, which Windows does not have.
        """
        import select

        while True:
            try:
                readable, _, _ = select.select([self._sock], [], [], 0)
            except OSError:
                break
            if not readable:
                break
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            self._tail += chunk
        messages, self._tail = parse_messages(self._tail)
        return messages

    def read_touches(self):
        """Only the contacts, for a sender with no use for the rest."""
        return [body for kind, body in self.read_messages() if kind == "touch"]

    def close(self):
        self._sock.close()


# Only what changed is sent, and these are what decide that. Every number here
# was measured on a panel by the Home Assistant sender this is ported from --
# ha_send.py -- rather than picked.
TILE = 64
# A rectangle costs the board a fixed amount on top of its pixels: its header,
# its own JPEG tables, one more DMA transfer set up. 1.5 ms, against a
# whole-panel decode of 8.5 ms for the 0.6144 megapixels of a 1024x600 panel.
RECT_FIXED_MS = 1.5
PANEL_DECODE_MS_PER_MPX = 8.5 / 0.6144
# No rectangle narrower or shorter than this. The P4's JPEG decoder is a DMA
# engine working in 16x16 units and a sliver stalls it -- a 32x128 strip comes
# back as ESP_ERR_TIMEOUT rather than as pixels. Slivers are the panel's own
# edge wherever its size is not a multiple of the tile.
MIN_RECT = 64
# However little changes, redraw everything this often. A rectangle lost to a
# busy board or a hiccup would otherwise stay wrong forever, because nothing
# marks that area as changed again.
FULL_REDRAW_SECONDS = 30.0
# A sender that transmits only what changed is SILENT while nothing changes,
# and silence is indistinguishable from having died. The board's patience is
# 30 seconds; this is what proves life inside it.
HEARTBEAT_S = 3.0


def rect_cost_fraction(width, height):
    """What one rectangle costs, as a fraction of redrawing the whole panel.

    A ratio, and only the numerator is fixed -- a whole-panel decode grows with
    the pixels -- so it cannot be one constant for every panel. 0.176 at
    1024x600, 0.106 at 800x1280.
    """
    return RECT_FIXED_MS / (PANEL_DECODE_MS_PER_MPX * width * height / 1e6)


def changed_rectangles(previous, current, tile=TILE):
    """Where the two pictures differ, as few rectangles as reasonable.

    Tiles that differ are found first, merged along each row, then rows that
    ended up with the same run merged down the columns -- a window that moved,
    a menu that opened. One rectangle costs the board a header, a JPEG's own
    tables and a decode, so a handful of large ones beats a crowd of small ones
    even carrying a few unchanged pixels along.

    Returns (x, y, w, h) tuples in pixels.
    """
    import numpy as np  # noqa: F401 - kept local so --help needs no numpy

    height, width = current.shape[:2]
    tiles_x = (width + tile - 1) // tile
    tiles_y = (height + tile - 1) // tile

    # One vectorised comparison over the whole picture, and the colour axis is
    # deliberately left alone: reducing it away first with np.any(axis=-1)
    # reads every byte again along the one axis that is not contiguous, and
    # measured fifteen times slower for the same answer.
    differing = previous != current

    rectangles = []
    for ty in range(tiles_y):
        top = ty * tile
        bottom = min(top + tile, height)
        row = differing[top:bottom]
        run_start = None
        for tx in range(tiles_x):
            left = tx * tile
            right = min(left + tile, width)
            differs = bool(row[:, left:right].any())
            if differs and run_start is None:
                run_start = left
            elif not differs and run_start is not None:
                rectangles.append((run_start, top, left - run_start, bottom - top))
                run_start = None
        if run_start is not None:
            rectangles.append((run_start, top, width - run_start, bottom - top))

    # Stack rows covering the same columns that touch. Rows come in order, so
    # the candidate is always the one just added.
    merged = []
    for x, y, w, h in rectangles:
        if merged:
            mx, my, mw, mh = merged[-1]
            if mx == x and mw == w and my + mh == y:
                merged[-1] = (mx, my, mw, mh + h)
                continue
        merged.append((x, y, w, h))

    # Widen anything the decoder would choke on, backwards so it stays inside
    # the panel. A panel smaller than the minimum keeps whatever it has.
    grown = []
    for x, y, w, h in merged:
        if w < MIN_RECT and width >= MIN_RECT:
            x, w = min(x, width - MIN_RECT), MIN_RECT
        if h < MIN_RECT and height >= MIN_RECT:
            y, h = min(y, height - MIN_RECT), MIN_RECT
        grown.append((x, y, w, h))
    return grown


# The pointer, drawn by hand, because no screen capture carries it.
#
# Windows composites the cursor over the desktop rather than into it: mss,
# BitBlt and the Desktop Duplication API alike hand over a picture with no
# pointer in it. On a panel that is only being watched nobody minds. On a
# second screen somebody is working on, not seeing where you are pointing
# makes it useless.
#
# A drawn arrow rather than the real cursor bitmap: fetching that means
# GetCursorInfo, GetIconInfo and DrawIconEx onto a device context, and then
# carrying the result into a PIL image. This is seven points and works the
# same on every machine, at the cost of not showing the I-beam or the busy
# ring.
CURSOR = ((0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12))


def cursor_position():
    """Where the pointer is, in desktop coordinates, or None.

    Windows only, and never fatal: a machine that will not say is a machine
    that gets no pointer drawn, not one that stops sending its screen.
    """
    try:
        import ctypes

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = Point()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return point.x, point.y
    except Exception:  # noqa: BLE001 - no cursor is not a reason to stop
        return None


def draw_cursor(image, monitor, position, scale=1.0):
    """Put the pointer into the picture, if it is on this screen.

    Drawn before the diff sees the picture, so moving the mouse marks the
    tiles it left and the tiles it arrived on, and both are sent -- which is
    what makes it rub out cleanly rather than leave a trail.
    """
    if position is None:
        return
    x = position[0] - monitor["left"]
    y = position[1] - monitor["top"]
    if not (0 <= x < monitor["width"] and 0 <= y < monitor["height"]):
        return  # the pointer is on one of the other screens
    from PIL import ImageDraw

    points = [(x + px * scale, y + py * scale) for px, py in CURSOR]
    draw = ImageDraw.Draw(image)
    # White with a black outline, so it is visible over anything. A single
    # colour disappears over half the desktops there are.
    draw.polygon(points, fill=(255, 255, 255), outline=(0, 0, 0))


def pick_monitor(monitors, wanted, panel_w, panel_h):
    """Which screen to send, given what the panel said it is.

    mss numbers monitors from 1, with 0 meaning all of them joined. A number
    is obeyed. "auto" looks for one whose size is EXACTLY the panel's, which
    is the whole point: a virtual display created for this panel is set to the
    panel's resolution, so it identifies itself without anybody counting
    screens -- and a screen that is the right shape is never the laptop's own.

    Falls back to the primary, because a wrong screen is better than none, and
    says which it took.
    """
    if str(wanted).lower() != "auto":
        return monitors[int(wanted)]
    clones = duplicated(monitors)
    for index, monitor in enumerate(monitors[1:], start=1):
        if monitor["width"] == panel_w and monitor["height"] == panel_h:
            print(f"Capturing monitor {index}, which is {panel_w}x{panel_h} "
                  f"-- the panel's own size")
            # The right size and still a copy. Windows is CLONING it, which no
            # amount of driver or resolution fixes, and from the glass it is
            # identical to having no second screen -- which is how it survived
            # a working setup and a restart.
            if index in clones:
                print()
                print("But Windows is DUPLICATING that screen, so it shows the")
                print("same desktop as another one. That is why the panel is a")
                print("mirror even though the screen is now the right size.")
                print()
                print("    Press Windows+P and choose Extend (Etendre).")
                print()
                print("    Or run:  portall.exe --extend")
                print()
            return monitor
    # Say what there WAS, not only what there was not. Without the list this
    # was a dead end from the other side of a chat window: a virtual display
    # that exists but is the wrong size, one Windows has not been told to
    # extend onto, and one that is simply not there all read the same.
    print(f"No monitor is {panel_w}x{panel_h}. What Windows is showing:")
    describe_monitors(monitors)
    # Say what this IS, not only what is missing. Reported from a panel as
    # "ce n'est pas un ecran secondaire juste il recopie l'ecran principal" --
    # with this message on the screen above it, saying the true thing in words
    # that did not answer the question being asked.
    print()
    print("So the panel is showing a COPY of your main screen, and it will go on")
    print("showing a copy. portall does not make screens -- Windows does, and it")
    print("only makes one when there is a display adapter behind it.")
    print()
    # Name the command. This message described the problem and the CLASS of
    # solution for two releases while --setup, which does the whole thing,
    # was never mentioned in the one place anybody reads. Reported three times
    # as the panel still being a mirror, each time from a run showing this.
    print("    THIS IS THE COMMAND THAT FIXES IT, run it once:")
    print()
    print("        portall.exe --setup")
    print()
    print("It asks Windows for the administrator window itself, installs the")
    print(f"driver, sets the new screen to {panel_w}x{panel_h}, and starts with")
    print("Windows from then on. Afterwards this panel is a desktop you can drag")
    print("a window onto, and it stays one.")
    print()
    big = f"{monitors[1]['width']}x{monitors[1]['height']}"
    print(f"It costs frames as well, not only the second desktop: all of {big}")
    print(f"has to be grabbed and then squeezed down to {panel_w}x{panel_h} for every")
    print("picture. A screen that is already the panel's size needs neither.")
    print("Sending the primary one, scaled, meanwhile.")
    return monitors[1]


def duplicated(monitors):
    """Screens Windows is CLONING, by index. Empty when every one is its own.

    Two monitors sharing an origin is what duplicating looks like from here:
    both adapters are handed the same desktop, so they report the same
    rectangle. It is the whole of a report that survived a driver install and
    a restart -- "au redemarrage il affiche un miroir de mon ecran primaire" --
    because a virtual display in clone mode IS a copy of the primary, and
    every check this program had said the screen was there and the right size.

    Windows' own projection mode decides it and nothing about the driver does,
    which is why the setup could be entirely correct and the panel still show
    a mirror.
    """
    seen = {}
    clones = set()
    for index, monitor in enumerate(monitors):
        if index == 0:                            # the union of them all
            continue
        where = (monitor["left"], monitor["top"])
        if where in seen:
            clones.add(seen[where])
            clones.add(index)
        seen[where] = index
    return sorted(clones)


def extend_displays():
    """Ask Windows to give the second screen a desktop of its own.

    DisplaySwitch.exe is Windows' own, is what Win+P drives, and needs no
    administrator. Called at the end of the setup because a virtual display
    that arrives in clone mode is a screen that exists, is the right size,
    passes every check here, and shows a copy.
    """
    code, out = _run(["DisplaySwitch.exe", "/extend"])
    return code == 0, out.strip()


def describe_monitors(monitors, panels=()):
    """Every screen, as Windows reports it. monitors[0] is all of them joined.

    A screen that is exactly some panel's size is named, because that is the
    one that will be taken and saying so is the whole point of the listing.
    Screens sharing an origin are called out as duplicated: from a panel that
    is indistinguishable from having no second screen at all.
    """
    clones = duplicated(monitors)
    for index, monitor in enumerate(monitors):
        where = ("all of them joined" if index == 0
                 else f"at {monitor['left']},{monitor['top']}")
        note = "   <- primary" if index == 1 else ""
        if index in clones:
            note += "   <- DUPLICATED, showing the same desktop as another"
        for panel in panels:
            if (index and monitor["width"] == panel["width"]
                    and monitor["height"] == panel["height"]):
                note = f"   <- this one goes to {panel['name']}"
        print(f"    {index}: {monitor['width']}x{monitor['height']}, {where}{note}")


class PanelWriter:
    """Keeps the socket's blocking write off the capture loop.

    ``sendall`` does not return until the board has taken the bytes, and while
    it waits nothing else happens at all: no screen is grabbed, no picture is
    made, no pointer is noticed. That is harmless while the link keeps up and
    it is the whole of the stutter when it does not -- a radio that goes away
    for a couple of hundred milliseconds leaves whatever was in flight to
    drain afterwards, and the loop stops for as long as that takes. On the
    Home Assistant sender this was measured turning a single turn of the loop
    into three seconds.

    Writing from a thread turns a stall into the right kind of loss. The loop
    never waits; a link that cannot keep up costs PICTURES, and a picture is
    exactly the thing that is safe to lose, because the one after it replaces
    it entirely.

    One picture is held and no more, and it is all-or-nothing. A rectangle is
    never resent, so half a picture would leave that part of the panel wrong
    until the thirty-second redraw.
    """

    def __init__(self, endpoint):
        self._endpoint = endpoint
        self._wake = threading.Condition()
        self._slot = None
        self._error = None
        self._stop = False
        self.blocked = 0.0
        # When the write in progress began, or None between writes. Without
        # it the whole of a long write is credited to the window in which it
        # FINISHES, and a stall longer than a window prints a percentage of
        # time that cannot exist.
        self._writing_since = None
        self._thread = threading.Thread(
            target=self._run, name="panel-writer", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            with self._wake:
                while self._slot is None and not self._stop:
                    self._wake.wait()
                if self._stop:
                    return
                blobs = self._slot
                self._writing_since = time.monotonic()
            try:
                for blob in blobs:
                    self._endpoint.write(blob)
            except OSError as err:
                # Handed to the loop, which owns reconnecting.
                with self._wake:
                    self._error = err
                    self._slot = None
                    self._wake.notify_all()
                return
            with self._wake:
                if self._writing_since is not None:
                    self.blocked += time.monotonic() - self._writing_since
                    self._writing_since = None
                self._slot = None
                self._wake.notify_all()

    def ready(self):
        """Whether the panel has caught up enough to be given another."""
        with self._wake:
            if self._error is not None:
                raise self._error
            return self._slot is None

    def offer(self, blobs):
        """Hand over a whole picture. Only call this after ready()."""
        with self._wake:
            if self._error is not None:
                raise self._error
            self._slot = blobs
            self._wake.notify()

    def take_blocked(self):
        """Seconds spent writing since this was last asked.

        Including the write still going on: credited as it accrues rather than
        when it ends, so a stall longer than a window is spread across the
        windows it spans instead of arriving all at once.
        """
        with self._wake:
            spent, self.blocked = self.blocked, 0.0
            if self._writing_since is not None:
                now = time.monotonic()
                spent += now - self._writing_since
                self._writing_since = now
        return spent

    def close(self):
        with self._wake:
            self._stop = True
            self._wake.notify_all()
        # Never wait on a write that may itself be stuck: the socket is about
        # to be closed under it, and the thread is a daemon.
        self._thread.join(timeout=0.5)


SERVICE_TYPE = "_portall._tcp.local."


def discover(seconds=3.0):
    """Panels that said what they are, over mDNS.

    The board advertises this from its ESPHome configuration -- the address,
    the port, and the shape of the panel -- so none of it has to be typed
    here. That is the whole point: the YAML already knows, and a number typed
    twice is a number that will disagree eventually.

    Returns a list of dicts, or an empty list. Never raises: a machine with no
    mDNS on it is a machine that gives an address by hand, not one that fails.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        print("Discovery needs zeroconf: pip install zeroconf")
        return []
    import socket as _socket
    import time as _time

    found = {}

    class Listener(ServiceListener):
        def _look(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if info is None:
                return
            # A panel may answer on several addresses; the first that is a
            # plain IPv4 one is what a socket wants.
            addresses = [
                _socket.inet_ntop(_socket.AF_INET, packed)
                for packed in info.addresses
                if len(packed) == 4
            ]
            if not addresses:
                return
            txt = {
                key.decode(errors="replace"): (value or b"").decode(errors="replace")
                for key, value in (info.properties or {}).items()
            }

            def number(key):
                try:
                    return int(txt.get(key, ""))
                except ValueError:
                    return None

            found[name] = {
                "name": name.split(".")[0],
                "host": addresses[0],
                "port": info.port,
                "width": number("width"),
                "height": number("height"),
                # What the BOARD does, not what the sender should do. It turns
                # the picture in hardware, so rotating here as well would turn
                # it twice.
                "rotation": number("rotation"),
                "format": txt.get("format", ""),
            }

        add_service = _look
        update_service = _look

        def remove_service(self, zc, type_, name):
            found.pop(name, None)

    zeroconf = Zeroconf()
    try:
        ServiceBrowser(zeroconf, SERVICE_TYPE, Listener())
        _time.sleep(seconds)
    finally:
        zeroconf.close()
    return sorted(found.values(), key=lambda panel: panel["name"])


def connect_tcp(host, port):
    """Wait for the board to answer, the same way the USB path waits for it."""
    import socket

    announced = False
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"Connected to {host}:{port}")
            return _TcpEndpoint(sock)
        except OSError as err:
            if not announced:
                announced = True
                print(f"Waiting for {host}:{port} ({err})")
            time.sleep(1.0)


def wait_for_endpoint(vid, pid):
    """Block until the board is there, however long that takes.

    Exiting when the board is absent means the sender has to be started after
    the board, by hand, every time -- and started again after every unplug.
    Waiting instead is what lets this run unattended from login.
    """
    announced = False
    while True:
        device, endpoint = find_endpoint(vid, pid)
        if device is not None:
            return device, endpoint
        if not announced:
            announced = True
            print(
                f"Waiting for {vid:04x}:{pid:04x}.\n"
                "  - Is the board on its OTG port, running a wired_portall firmware?\n"
                "  - On Windows the display interface needs WinUSB. The board asks\n"
                "    for it itself, but Windows caches that answer per device\n"
                "    revision and never asks twice, so a board that enumerated\n"
                "    before it grew those descriptors stays without a driver even\n"
                "    though its drive mounts. Clear the cache, as administrator:\n"
                f"      Remove-Item -Recurse -Force 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\usbflags\\{vid:04X}{pid:04X}0201'\n"
                "    then unplug and plug it back in. Zadig, pointed at the\n"
                "    interface rather than the device, does the same by hand."
            )
        time.sleep(1.0)


STARTUP_SCRIPT_NAME = "esphome_udisp_send.vbs"


def _startup_path():
    """Where Windows looks for things to run at login, or None elsewhere."""
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(
        appdata,
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
        STARTUP_SCRIPT_NAME,
    )


def _require_startup_path():
    path = _startup_path()
    if path is None:
        raise SystemExit(
            "Starting at login is only wired up for Windows here. Elsewhere, run "
            "the same command line from a systemd user unit (Linux) or a launchd "
            "agent (macOS)."
        )
    return path


def frozen():
    """Whether this is running as portall.exe rather than as a .py.

    PyInstaller sets sys.frozen and puts the real program in sys.executable;
    __file__ then points inside a bundle that is unpacked to a temporary
    directory and deleted afterwards. Every path decision below has to ask
    this, and getting it wrong produces a login task pointing at a folder that
    no longer exists -- which works exactly once.
    """
    return getattr(sys, "frozen", False)


def own_path():
    """The file to copy, to hash, and to run again: the exe, or this script."""
    return os.path.abspath(sys.executable if frozen() else __file__)


LOG_NAME = "udisp_send.log"
LOG_MAX_BYTES = 1024 * 1024


def _log_to_file_when_hidden(forced=False):
    """Write to a file when there is nowhere else to write.

    The login task runs under pythonw.exe, which has no console: sys.stdout is
    None there, and print() silently does nothing. That is fine until it is
    not -- a sender that fails to start leaves no trace at all, and from the
    outside that is indistinguishable from a panel that is off, a network that
    is down and a file that was never installed.

    A frozen build breaks that test rather than passing it. portall.exe is
    built with a console so that somebody who double-clicks it sees it working,
    and the login script then starts it with that console HIDDEN -- so
    sys.stdout is a perfectly good handle to a window nobody can see, and every
    line goes nowhere while looking like it went somewhere. `forced` is how the
    login script says which of the two this is; the flag is written into the
    command line at install time rather than guessed at here.

    Returns the path it is writing to, or None when there is a console and
    this is somebody watching it.
    """
    if sys.stdout is not None and not forced:
        return None
    where = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "esphome-udisp", LOG_NAME)
    try:
        os.makedirs(os.path.dirname(where), exist_ok=True)
        # Started again means started over. A log that only grows is one
        # nobody reads, and what matters is this run.
        if os.path.exists(where) and os.path.getsize(where) > LOG_MAX_BYTES:
            os.remove(where)
        handle = open(where, "a", encoding="utf-8", buffering=1)
    except OSError:
        # Never fatal. A sender that will not start because it could not open
        # its log is worse than one that says nothing.
        return None
    handle.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    sys.stdout = handle
    sys.stderr = handle
    return where


def _install_copy():
    """Put a copy of this script somewhere that will still be there at login.

    This is normally run straight off the board's own drive, and that drive only
    exists while the board is plugged in -- and not always under the same
    letter. A login task pointing at it would work until it did not, in a way
    that would look like the board being broken. Copy it to the user's own
    directory and point at the copy.
    """
    target_dir = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "esphome-udisp"
    )
    os.makedirs(target_dir, exist_ok=True)
    source = own_path()
    target = os.path.join(target_dir, os.path.basename(source))

    if os.path.normcase(source) != os.path.normcase(target):
        with open(source, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
    return target


def install_startup(args):
    """Run this sender at every login, without a console window.

    A one-line VBScript in the Startup folder rather than a shortcut or a
    registry key: it is the only one of the three that can start a program with
    its window hidden, and it is a text file the user can read and delete.
    """
    path = _require_startup_path()

    copied = _install_copy()
    if frozen():
        # The executable IS the program. Handing it to an interpreter would
        # be asking Python to run a Windows binary.
        parts = [copied]
    else:
        # pythonw.exe is the interpreter without a console; fall back to the
        # one running this if the installation has no windowed build.
        interpreter = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(interpreter):
            interpreter = sys.executable
        parts = [interpreter, copied]
    # A hidden window is not the same as no window, and only this run knows
    # which it is about to become. Said out loud on the command line rather
    # than sniffed at startup.
    parts += ["--log-file"]
    parts += [
        "--monitor",
        str(args.monitor),
        "--fps",
        str(args.fps),
        "--quality",
        str(args.quality),
        "--rotate",
        str(args.rotate),
    ]
    # The transport the run was told to use, so a login task keeps it. Without
    # this a network sender came back at the next login looking for USB.
    if args.discover:
        # Written as --discover rather than as the address that was found.
        # A panel's address comes from DHCP and will change; its NAME will
        # not, and the shape of the panel is in its ESPHome configuration
        # rather than in this file. Freezing either here is how a login task
        # comes back one day pointed at a washing machine.
        parts += ["--discover"]
        if args.panel:
            parts += ["--panel", args.panel]
    elif args.host:
        parts += ["--host", args.host, "--port", str(args.port),
                  "--width", str(args.width), "--height", str(args.height)]
    else:
        parts += ["--vid", hex(args.vid), "--pid", hex(args.pid),
                  "--width", str(args.width), "--height", str(args.height)]
    # Quote every part for the shell, then double the quotes again because the
    # whole command is about to become a VBScript string literal.
    command = " ".join(f'"{part}"' for part in parts).replace('"', '""')

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "' Sends this screen to an ESPHome wired_portall board at login.\r\n"
            "' Delete this file, or run udisp_send.py --uninstall-startup, to stop.\r\n"
            f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n'
        )
    print(f"Installed: {path}")
    print(f"Running:   {copied}")
    print(f"Log:       {os.path.join(os.path.dirname(copied), LOG_NAME)}")
    print("It runs with no console, so that log is where it says what it is")
    print("doing and why it stopped.")
    print("It starts at the next login, and waits for the board rather than")
    print("failing when it is not plugged in yet.")
    return 0


# Where the Virtual Display Driver keeps the file that says what shapes its
# monitor can be. Its own README names the first; the others are where
# installs have been seen to land.
VDD_SETTINGS_PATHS = (
    r"C:\VirtualDisplayDriver\vdd_settings.xml",
    r"C:\IddSampleDriver\vdd_settings.xml",
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "VirtualDisplayDriver", "vdd_settings.xml"),
)

VDD_WINGET_ID = "VirtualDrivers.Virtual-Display-Driver"


def _run(command, check=False):
    """Run something and hand back (code, output). Never raises on a bad exit."""
    import subprocess

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=600)
    except FileNotFoundError:
        return 127, f"{command[0]} is not on this machine"
    except Exception as err:                      # noqa: BLE001 - reported, not raised
        return 1, str(err)
    out = (done.stdout or "") + (done.stderr or "")
    if check and done.returncode != 0:
        raise SystemExit(out.strip() or f"{command[0]} failed")
    return done.returncode, out


# What an indirect display driver calls itself, whichever one is installed.
# Espressif's is in here beside the Virtual Display Driver because a board fed
# over a cable is the other route to the same thing, and somebody who has that
# one installed should not be told they have nothing.
DRIVER_NAMES = "Virtual Display|IddSample|Idd Device|usb_graphic|xfz1986"


def virtual_display_present():
    """Whether Windows has an indirect display driver, and what state it is in.

    Asked of Windows rather than of the filesystem: a folder left behind by an
    uninstall would answer yes, and then the setup would configure a driver
    that is not there and report success.

    NOT restricted to -Class Display, which the first version was. These
    enumerate under more than one class depending on the driver and the
    version, so a class filter is a way to answer "nothing installed" about a
    driver that is sitting right there. The Status comes back too: a driver
    present and in Error is a different problem from one absent, and the two
    were previously the same empty string.
    """
    code, out = _run([
        "powershell", "-NoProfile", "-Command",
        "Get-PnpDevice -ErrorAction SilentlyContinue "
        f"| Where-Object {{ $_.FriendlyName -match '{DRIVER_NAMES}' }} "
        "| ForEach-Object { \"$($_.Status)  $($_.Class)  $($_.FriendlyName)\" }",
    ])
    return code == 0 and out.strip() != "", out.strip()


def screens_windows_has():
    """Every screen Windows is showing, or None when that cannot be asked."""
    try:
        import mss
    except ImportError:
        return None
    try:
        screenshotter = getattr(mss, "MSS", None) or mss.mss
        with screenshotter() as sct:
            return [dict(m) for m in sct.monitors]
    except Exception:                             # noqa: BLE001 - diagnostic only
        return None


def check_windows(args):
    """Say where the PC side stands, in one command, without changing anything.

    Written because "it behaves like a mirror and Windows does not know the
    panel" has three quite different causes that look identical from a panel:
    no driver, a driver at the wrong size, and a driver Windows has disabled.
    Each needs a different next step and none of them is visible from the
    picture on the glass.
    """
    if sys.platform != "win32":
        raise SystemExit("--check asks Windows about its displays, so it only "
                         "means something on Windows.")

    print("Virtual display driver")
    present, what = virtual_display_present()
    if present:
        for line in what.splitlines():
            print(f"    {line}")
        print("    (a line beginning OK is working; Error or Unknown is not)")
    else:
        print("    NONE INSTALLED.")
        print("    This is why the panel is a copy of the main screen and why")
        print("    Windows forgets it: the second desktop is the DRIVER's, not")
        print("    this program's. Run --setup in an administrator window.")

    print()
    print("Its settings file")
    path = vdd_settings_path()
    print(f"    {path}" if path else "    not found in any of the usual places")

    print()
    print("Screens Windows is showing")
    monitors = screens_windows_has()
    if monitors is None:
        print("    could not be asked")
    else:
        describe_monitors(monitors)

    wanted = None
    if args.width and args.height:
        wanted = (args.width, args.height)
    else:
        panels = discover(seconds=2.0)
        if panels:
            wanted = (panels[0]["width"], panels[0]["height"])
            print(f"\nThe panel says it is {wanted[0]}x{wanted[1]}")

    print()
    if wanted and monitors:
        match = [i for i, m in enumerate(monitors)
                 if i and m["width"] == wanted[0] and m["height"] == wanted[1]]
        if match:
            print(f"Monitor {match[0]} is exactly the panel's size, so this is a")
            print("second desktop rather than a copy. That is what it should say.")
        else:
            print(f"NO screen is {wanted[0]}x{wanted[1]}, so the panel can only be")
            print("shown a copy of another one. Either the driver is missing, or")
            print("it is installed at a different size -- the two lines above say")
            print("which. Restarting Windows once is what makes a size change take.")
    else:
        print("Without the panel's size nothing above can be judged against it;")
        print("give --width and --height, or leave the panel on and try again.")
    return 0


def vdd_settings_path():
    for path in VDD_SETTINGS_PATHS:
        if os.path.exists(path):
            return path
    return None


def set_panel_resolution(path, width, height, hz=60):
    """Make the virtual monitor exactly the panel's size, and only that.

    EDITED, never regenerated. This file belongs to another project and carries
    elements this one has never heard of; writing a fresh one from a README
    would mean inventing a schema, which is how a working install becomes a
    driver that will not start. Everything not named here is left untouched,
    and the original is kept beside it.

    Only one resolution, because that is what makes the panel findable: --monitor
    auto takes the screen whose size is EXACTLY the panel's, and a driver
    offering twenty shapes is a screen that could be any of them. It is also the
    answer to a panel that reported several monitors -- the shipped file declares
    a list, and each entry is another mode Windows may pick.

    Returns the list of changes made, in words, or raises SystemExit.
    """
    import shutil
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(path)
    except ET.ParseError as err:
        raise SystemExit(f"{path} is not readable as XML ({err}). Left alone.")
    root = tree.getroot()
    changed = []

    # Every list of resolutions, wherever it sits: the file has been seen with
    # one at the top and with one per monitor, and this has to work on both.
    lists = [el for el in root.iter() if el.tag.lower() == "resolutions"]
    if not lists:
        raise SystemExit(
            f"{path} has no <resolutions> in it, so this is not the file this "
            "was written for. Left alone -- set the resolution in the Virtual "
            "Display Driver's own app instead."
        )
    wanted = (str(width), str(height), str(hz))
    for holder in lists:
        entries = list(holder)
        # Say nothing when there is nothing to say. Run twice, the second run
        # reported the same change as the first and rewrote the file to do it
        # -- which reads as a setting that will not stick.
        if len(entries) == 1:
            have = tuple(
                (entries[0].findtext(tag) or "").strip()
                for tag in ("width", "height", "refresh_rate")
            )
            if have == wanted:
                continue
        for child in entries:
            holder.remove(child)
        entry = ET.SubElement(holder, "resolution")
        ET.SubElement(entry, "width").text = str(width)
        ET.SubElement(entry, "height").text = str(height)
        ET.SubElement(entry, "refresh_rate").text = str(hz)
        changed.append(
            f"{len(entries)} resolutions -> one, {width}x{height} at {hz} Hz")

    # A count of monitors, if this version has one. Named conservatively: only
    # an element that already holds a small number is touched, so a tag that
    # merely happens to contain the word is not overwritten with a 1.
    for el in root.iter():
        if "count" not in el.tag.lower():
            continue
        text = (el.text or "").strip()
        if text.isdigit() and 0 < int(text) < 100 and text != "1":
            el.text = "1"
            changed.append(f"<{el.tag}> {text} -> 1, so there is one screen")

    if not changed:
        return ["already exactly one screen at the panel's size"]

    backup = path + ".before-portall"
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
        changed.append(f"the original is kept at {backup}")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return changed


def am_admin():
    """Whether this is already the administrator window the driver install needs."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                             # noqa: BLE001 - answered, not raised
        return False


def relaunch_as_admin():
    """Ask Windows for the elevated window rather than asking a person for one.

    Installing a driver needs administrator rights, and "open an administrator
    PowerShell, then type this" is a step that was reported three times as
    simply not happening -- which is fair, because somebody who downloaded one
    file expects to run that file. ShellExecute with the runas verb puts the
    ordinary consent prompt up instead, so a double-click reaches the same
    place.

    --pause goes with it because the new console closes the moment the work
    ends, and a window that vanishes takes its error message with it.

    Returns True when a second, elevated copy has been started.
    """
    try:
        import ctypes
    except ImportError:
        return False

    me = own_path()
    if frozen():
        program, arguments = me, "--setup --pause"
    else:
        program, arguments = sys.executable, f'"{me}" --setup --pause'
    print("This needs administrator rights to install a driver.")
    print("Windows will ask; the work happens in the window that opens.")
    try:
        # Above 32 is success, per ShellExecute's own convention.
        started = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", program, arguments, None, 1)
    except Exception as err:                      # noqa: BLE001 - reported
        print(f"Could not ask for it ({err}).")
        return False
    if int(started) <= 32:
        print("That was refused or cancelled, so nothing has been changed.")
        print("Right-click the Start menu, choose Terminal (Administrator),")
        print("and run portall.exe --setup there instead.")
        return False
    return True


def offer_setup(args, monitor):
    """Ask, instead of printing a command for somebody to go and type.

    Four rounds of this ended with "comment je le fais avec le terminal ?",
    which is the right question and the sign that the answer was wrong. Naming
    the command was already the second attempt; the first only named the class
    of driver. A person who downloaded one file and double-clicked it should
    not have to learn where PowerShell is to make their screen work.

    Three guards, and the middle one is the important one:

    - Windows only, since it installs a Windows driver.
    - **Never when nobody is there.** A run started at login has a console it
      cannot be seen through, and a prompt there would wait for a keypress for
      ever, with the panel dark and no way to tell why. --log-file is what that
      run is marked with, and an absent or redirected stdin says the same.
    - And only when the screen really is the wrong size, so a panel that is
      already a second desktop is never asked anything.

    Returns True when setup was started and this run should stop.
    """
    if sys.platform != "win32" or args.log_file:
        return False
    if monitor["width"] == args.width and monitor["height"] == args.height:
        return False
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False

    print()
    try:
        answer = input("Set that up now? Windows will ask permission. [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer.strip().lower() in ("n", "no", "non"):
        print("Carrying on with a copy of the main screen.")
        return False

    print()
    setup_windows(args)
    print()
    print("When that window has finished, restart Windows once and start this")
    print("again. Until then the panel can only be shown a copy.")
    return True


def setup_windows(args):
    """The whole PC side in one command, and never again.

    This exists because the alternative is a page of instructions: install a
    driver from another project, find its XML, work out which resolution to
    put in it, then run this by hand at every login. Every one of those steps
    was reported as a place to get stuck, and the last of them is why quitting
    this program takes the second screen away.
    """
    if sys.platform != "win32":
        raise SystemExit(
            "--setup installs a Windows display driver, so it only means "
            "something on Windows."
        )

    if not am_admin():
        return 0 if relaunch_as_admin() else 1

    print("Looking for the panel, to take its size from what it advertises")
    panels = discover()
    if not panels:
        raise SystemExit(
            "No panel answered. It has to be on and on this network for its "
            "size to be read; --panel names one, or give --width and --height "
            "by hand."
        )
    panel = panels[0]
    width, height = panel["width"], panel["height"]
    print(f"  {panel['name']}: {width}x{height}")

    present, what = virtual_display_present()
    if present:
        print(f"Virtual display driver: already installed ({what})")
    else:
        print(f"Virtual display driver: not installed, asking winget for it")
        code, out = _run([
            "winget", "install", "--id", VDD_WINGET_ID, "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ])
        if code != 0:
            raise SystemExit(
                f"winget could not install it:\n{out.strip()}\n\n"
                "This step needs an administrator window, and winget itself on "
                "older builds of Windows. The driver can also be installed by "
                "hand from the Virtual Display Driver releases page; run this "
                "again afterwards and it will do the rest."
            )
        print("  installed")

    path = vdd_settings_path()
    if path is None:
        print("Its settings file is not where it usually is, so the resolution")
        print(f"has to be set to {width}x{height} in the driver's own app.")
    else:
        print(f"Setting the virtual screen to the panel's own size, in {path}")
        for line in set_panel_resolution(path, width, height):
            print(f"  {line}")

    print()
    install_startup(args)

    # Ask Windows whether any of that took, rather than saying Done. The first
    # version printed success unconditionally, which is the silent no-op this
    # project keeps recording -- and it recorded it again: reported back as
    # "il se comporte comme un miroir ... il n'est pas reconnu par windows",
    # from a run that had just told them everything was finished.
    # A virtual display can arrive CLONING the primary, which is a screen that
    # exists, is the right size, passes every check below, and shows a copy.
    print()
    print("Asking Windows to extend onto it rather than duplicate")
    fine, said = extend_displays()
    print("  done" if fine else f"  could not: {said or 'DisplaySwitch refused'}")
    print("  (Windows+P, then Extend, is the same thing by hand)")

    print()
    print("--- did it work? ---")
    print()
    args.width, args.height = width, height
    check_windows(args)
    print()
    present, _ = virtual_display_present()
    if not present:
        print("The driver is still not installed, so nothing above will happen.")
        print("That is the step to chase; the rest is done and harmless.")
        return 1
    print("Restart Windows once if a size changed: a driver reads that file when")
    print("it starts, so a running one is still the size it was.")
    return 0


def uninstall_startup():
    path = _require_startup_path()
    if not os.path.exists(path):
        print(f"Nothing installed at {path}")
        return 0
    os.remove(path)
    print(f"Removed: {path}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Not required=True, so --uninstall-startup does not have to be handed a
    # geometry it will not use.
    parser.add_argument(
        "--width",
        type=int,
        help="must match the width: of the wired_portall component",
    )
    parser.add_argument(
        "--height",
        type=int,
        help="must match the height: of the wired_portall component",
    )
    parser.add_argument(
        "--monitor",
        default="auto",
        help="which monitor to capture: a number (1 = primary), or auto. "
        "auto takes the one whose size is exactly the panel's, which is what "
        "a virtual display set to the panel's resolution will be -- so a "
        "second screen created for this is found without being counted. "
        "Failing that, the primary one",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="rotate the image clockwise before sending, for a panel that is not "
        "mounted the right way up. 90 and 270 swap the aspect ratio, so --width "
        "and --height (and the component) have to be the rotated size",
    )
    parser.add_argument(
        "--fps", type=float, default=30.0, help="frames per second to aim for"
    )
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality, 1..95")
    parser.add_argument(
        "--host",
        help="send over the network to a board listening on this address, "
        "instead of over USB. Use with the component's port: option",
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--show-touches",
        action="store_true",
        help="print the contacts the board sends back over the network. Nothing "
        "here acts on them -- this is how to see that the return channel works",
    )
    parser.add_argument("--vid", type=lambda v: int(v, 0), default=DEFAULT_VID)
    parser.add_argument("--pid", type=lambda v: int(v, 0), default=DEFAULT_PID)
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="run this sender at every login with the options given here, then "
        "exit. Windows only",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print this file's own fingerprint and stop, so a copy served "
        "from a cache can be told from the current one",
    )
    parser.add_argument(
        "--no-cursor",
        dest="cursor",
        action="store_false",
        help="leave the mouse pointer out. No screen capture carries it, so "
        "it is drawn on; a panel that is only being watched does not need it",
    )
    parser.add_argument(
        "--cursor-size",
        type=float,
        default=1.0,
        help="how large to draw the pointer, as a multiple. Worth raising on "
        "a panel being read across a room",
    )
    parser.add_argument(
        "--list-monitors",
        action="store_true",
        help="print the screens Windows is showing, with their sizes, and "
        "stop. This is what to run when a virtual display has been created "
        "and the panel is showing the wrong one",
    )
    parser.add_argument(
        "--usb",
        action="store_true",
        help="send over the cable instead of the network. This copy is for a "
        "panel fed over Wi-Fi, so the network is what it does when nothing "
        "says otherwise",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="find panels on the network by mDNS and use one, instead of "
        "being given --host, --width and --height. The board advertises its "
        "address and its shape from its ESPHome configuration, so nothing "
        "here has to be typed. With several panels found, they are listed "
        "and one is chosen with --panel",
    )
    parser.add_argument(
        "--panel",
        default=None,
        help="which discovered panel to use, by name, when --discover finds "
        "more than one",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="undo --install-startup and exit",
    )
    parser.add_argument(
        "--extend",
        action="store_true",
        help="tell Windows to extend onto the second screen instead of "
        "duplicating the first, and stop. This is what Windows+P does, and it "
        "is the fix when the panel is the right size and still shows a copy. "
        "Needs no administrator",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="wait for Enter before finishing. --setup puts this on the "
        "elevated window it opens, because a console that closes the instant "
        "the work ends takes its error message with it",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="say where the PC side stands -- whether a virtual display driver "
        "is installed, what size it is, and whether any screen matches the "
        "panel -- and change nothing. Needs no administrator",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="do the whole PC side once: install the virtual display driver, "
        "set it to the panel's own size, and start at every login. Needs an "
        "administrator window. After it, nothing has to be run by hand again",
    )
    parser.add_argument(
        "--log-file",
        action="store_true",
        help="write everything to a file beside the installed copy instead of "
        "to the console. --install-startup puts this on the command line it "
        "writes, because a run started at login has a console nobody can see",
    )
    args = parser.parse_args()

    # Before anything that might print, so a hidden run has somewhere to say
    # what went wrong.
    _log_to_file_when_hidden(forced=args.log_file)

    if args.version:
        # The file's own fingerprint, not a number somebody has to remember to
        # bump. GitHub's raw view is behind a cache that will happily serve a
        # copy from an hour ago -- measured, byte for byte -- and from a chat
        # window an old file and a broken one look identical.
        import hashlib

        me = own_path()
        with open(me, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:12]
        print(f"{os.path.basename(me)} {digest}, {os.path.getsize(me)} bytes")
        return 0

    if args.extend:
        if sys.platform != "win32":
            raise SystemExit("--extend drives Windows' own DisplaySwitch, so "
                             "it only means something on Windows.")
        fine, said = extend_displays()
        print("Windows is extending onto the second screen now."
              if fine else f"DisplaySwitch would not: {said}")
        return 0 if fine else 1

    if args.check:
        return check_windows(args)

    if args.setup:
        try:
            return setup_windows(args)
        finally:
            # In a finally, so a SystemExit keeps the window open too: the
            # elevated console closes on exit, and the runs worth reading are
            # exactly the ones that failed.
            if args.pause:
                try:
                    input("\nPress Enter to close this window.")
                except (EOFError, KeyboardInterrupt):
                    pass

    if args.uninstall_startup:
        return uninstall_startup()

    if args.list_monitors:
        try:
            import mss
        except ImportError as err:
            raise SystemExit(f"{err}. pip install mss") from err
        # Ask the panels what shape they are, so this can say which screen
        # will be taken rather than leaving that to be worked out. Printing
        # advice unconditionally is what made a list that was already correct
        # look as though something was still wrong.
        panels = discover(2.0)
        screenshotter = getattr(mss, "MSS", None) or mss.mss
        with screenshotter() as sct:
            print("The screens Windows is showing:")
            describe_monitors(sct.monitors, panels)
            matched = [
                panel for panel in panels
                for monitor in sct.monitors[1:]
                if monitor["width"] == panel["width"]
                and monitor["height"] == panel["height"]
            ]
        for panel in panels:
            if panel in matched:
                print(f"\n{panel['name']} is {panel['width']}x{panel['height']} "
                      f"and a screen of that size is here, so it will be found "
                      f"with no arguments at all.")
            else:
                print(f"\n{panel['name']} is {panel['width']}x{panel['height']} "
                      f"and no screen is that size. A virtual display set to "
                      f"exactly that will be picked up by itself; Windows also "
                      f"has to be set to Extend onto it rather than Duplicate.")
        if not panels:
            print("\nNo panel answered, so this cannot say which screen would "
                  "be taken. A virtual display made for a panel should be "
                  "exactly the panel's size.")
        return 0

    # Run with nothing at all and it finds the panel by itself. The address,
    # the size and the rotation are in the board's own ESPHome configuration,
    # so there is nothing here worth asking a person to type.
    if not args.discover and not args.host and not args.usb:
        args.discover = True

    if args.discover:
        panels = discover()
        if not panels:
            parser.error(
                "no panel answered on the network. Check the board has port: "
                "set and the mdns: services block from ws-wired-portall.yaml, "
                "and that this machine is on the same network -- mDNS does "
                "not cross a router."
            )
        for panel in panels:
            print(f"  {panel['name']}  {panel['host']}:{panel['port']}  "
                  f"{panel['width']}x{panel['height']}"
                  + (f", the board turns it {panel['rotation']} degrees"
                     if panel["rotation"] else ""))
        chosen = panels[0]
        if args.panel:
            named = [p for p in panels if p["name"] == args.panel]
            if not named:
                parser.error(f"no panel called {args.panel!r} was found")
            chosen = named[0]
        elif len(panels) > 1:
            parser.error("several panels answered -- say which with --panel")
        # Only what was not given by hand. Somebody who typed a size meant it.
        if not args.host:
            args.host, args.port = chosen["host"], chosen["port"]
        if args.width is None:
            args.width = chosen["width"]
        if args.height is None:
            args.height = chosen["height"]
        # Deliberately NOT --rotate. The board turns the picture in hardware,
        # so rotating it here as well would turn it twice; the number is
        # advertised so this can say what is happening, not so it can act.
        print(f"Using {chosen['name']} at {args.host}:{args.port}, "
              f"{args.width}x{args.height}")

    if args.width is None or args.height is None:
        parser.error("--width and --height are required, or use --discover")
    if args.install_startup:
        return install_startup(args)

    try:
        import mss
        import numpy as np
        from PIL import Image
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies: pip install mss pillow numpy"
            + ("" if args.host or args.discover else " pyusb")
        ) from err

    # Pillow's ROTATE_n turn counter-clockwise, and moved into an enum in 9.1
    # while staying reachable from the module for compatibility. Transposing is
    # a memory shuffle where rotate() goes through the resampling machinery, so
    # take the cheap one.
    transposes = getattr(Image, "Transpose", Image)
    transpose = {
        0: None,
        90: transposes.ROTATE_270,
        180: transposes.ROTATE_180,
        270: transposes.ROTATE_90,
    }[args.rotate]

    # NOT for a panel fed over the network. pyusb is what talks to a board on
    # a cable, and a machine sending over Wi-Fi has no reason to install it --
    # this used to die here with "No module named 'usb'" on a path that never
    # touches USB, which is the first thing anybody trying the network sender
    # runs into.
    if not args.host:
        import usb.core
        import usb.util

        # The classes that mean "the board went away". Captured here rather
        # than named in the except clause, because over the network usb is
        # never imported and naming it there would be a NameError on the first
        # hiccup -- which is exactly when it must not be.
        lost = (usb.core.USBError, OSError)
    else:
        lost = (OSError,)

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_id = 0
    # Worked out from the panel rather than assumed: it is a ratio, and a
    # whole-panel decode grows with the pixels while a rectangle's overhead
    # does not.
    rect_cost = rect_cost_fraction(args.width, args.height)


    # mss.mss() is a deprecated alias for mss.MSS(), which older versions do not
    # have.
    screenshotter = getattr(mss, "MSS", None) or mss.mss

    try:
        with screenshotter() as sct:
            monitor = pick_monitor(sct.monitors, args.monitor,
                                   args.width, args.height)
            # Offer to fix it here rather than leaving instructions. This is
            # the one moment the fault is certain and somebody is watching.
            if offer_setup(args, monitor):
                return 0
            # The arrow is drawn at the size of the SCREEN being captured, and
            # that screen is often larger than the panel -- on a mirrored
            # 1920x1080 shown at 1024x600 the pointer would arrive shrunk by
            # half. Drawn larger by exactly what the picture is about to lose.
            cursor_scale = args.cursor_size * max(
                1.0, monitor["width"] / float(args.width))
            # Outer loop: one pass per connection. Unplugging the board, or
            # reflashing it, ends the inner loop and comes back here to wait for
            # it rather than ending the program.
            while True:
                if args.host:
                    device, endpoint = None, connect_tcp(args.host, args.port)
                else:
                    device, endpoint = wait_for_endpoint(args.vid, args.pid)
                # Say where it is really going: over the network the USB
                # identifiers are not merely useless, they name a device that
                # is not in this at all.
                where = (f"{args.host}:{args.port}" if args.host
                         else f"{args.vid:04x}:{args.pid:04x}")
                print(
                    f"Sending {args.width}x{args.height} at up to {args.fps:g} fps to "
                    f"{where}"
                    + (f", rotated {args.rotate} degrees here" if args.rotate else "")
                )

                writer = PanelWriter(endpoint)
                frames = 0
                skipped = 0
                worst_turn = 0.0
                rectangles_sent = 0
                wholes = 0
                total_bytes = 0
                stats_at = time.monotonic()
                # Per connection, not per run: a board that came back has
                # forgotten everything, so the first picture after it must be
                # a whole one.
                previous = None
                last_full = 0.0
                last_sent = time.monotonic()
                try:
                    while True:
                        started = time.monotonic()

                        # Ask before doing any work at all. A panel that has
                        # not taken the last picture will not take this one
                        # either, and grabbing, diffing and encoding for it
                        # would be a whole turn spent on something to throw
                        # away. What is lost is a PICTURE, which is the right
                        # thing to lose: the next one replaces it entirely.
                        if not writer.ready():
                            skipped += 1
                            remaining = interval - (time.monotonic() - started)
                            if remaining > 0:
                                time.sleep(remaining)
                            continue

                        shot = sct.grab(monitor)
                        image = Image.frombytes("RGB", shot.size, shot.rgb)
                        # Before the rotation and the scaling, so the arrow is
                        # turned and shrunk with everything else and lands
                        # where the pointer really is.
                        if args.cursor:
                            draw_cursor(image, monitor, cursor_position(),
                                        cursor_scale)
                        # Rotate before scaling, so a quarter turn is fitted to
                        # the panel's shape rather than to the desktop's.
                        if transpose is not None:
                            image = image.transpose(transpose)
                        # The board draws the frame as it arrives and rejects any
                        # other size, so scaling happens here.
                        if image.size != (args.width, args.height):
                            image = image.resize(
                                (args.width, args.height), Image.BILINEAR
                            )

                        # What changed, and nothing else. This is the whole
                        # difference between a panel that costs the link
                        # everything and one that costs nothing while a desktop
                        # sits still -- which is what a desktop mostly does.
                        current = np.asarray(image)
                        if previous is None or started - last_full >= FULL_REDRAW_SECONDS:
                            rectangles = [(0, 0, args.width, args.height)]
                            last_full = started
                            wholes += 1
                        else:
                            rectangles = changed_rectangles(previous, current)
                            covered = sum(w * h for _, _, w, h in rectangles)
                            # Give up on pieces only when the whole panel is
                            # actually cheaper. It is the COUNT that decides,
                            # not the area: twenty scattered rectangles cost
                            # the board more than one decode of everything,
                            # while one large rectangle beats it on both bytes
                            # and time.
                            if rectangles and (
                                covered / (args.width * args.height)
                                + rect_cost * len(rectangles) > 1.0
                            ):
                                rectangles = [(0, 0, args.width, args.height)]
                                last_full = started
                                wholes += 1

                        blobs = []
                        for x, y, w, h in rectangles:
                            buffer = io.BytesIO()
                            image.crop((x, y, x + w, y + h)).save(
                                buffer, format="JPEG", quality=args.quality
                            )
                            payload = buffer.getvalue()
                            blobs.append(
                                build_header(w, h, len(payload), frame_id, x, y)
                                + payload
                            )
                            rectangles_sent += 1
                            total_bytes += len(payload)
                        if blobs:
                            # The whole picture in one handover, so the writer
                            # cannot be interrupted halfway through it.
                            writer.offer(blobs)

                        if rectangles:
                            previous = current
                            frames += 1
                            # One identifier for the whole picture, so the
                            # board admits or drops its rectangles together and
                            # never shows half an update.
                            frame_id = (frame_id + 1) & 0x3FF
                            last_sent = started
                        elif started - last_sent >= HEARTBEAT_S:
                            # Nothing changed, so nothing was sent -- and a
                            # silent sender is indistinguishable from a dead
                            # one to a board counting down its timeout.
                            writer.offer([build_heartbeat()])
                            last_sent = started

                        now = time.monotonic()
                        if now - stats_at >= 5.0:
                            elapsed = now - stats_at
                            # `whole` is the field to read. The rectangle count
                            # cannot say on its own: a whole panel is one
                            # rectangle and so is a window that grew, and the
                            # two differ by a hundred kilobytes. Nothing here
                            # divides by a count -- a desktop that did not
                            # change sends nothing, and this line has to print
                            # for that case rather than raise on it.
                            # `panel wait` is the writer thread's time, not
                            # the loop's, so it can sit near 100% without a
                            # stutter -- when it does, `skipped` is what the
                            # link is costing. `worst turn` is the longest
                            # single pass of the loop, which says whether a
                            # pause was the socket or this machine.
                            waited = writer.take_blocked()
                            print(
                                f"{frames / elapsed:.1f} pictures/s, "
                                f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                                f"{wholes} whole, "
                                f"{total_bytes / elapsed / 1024:.1f} KiB/s, "
                                f"panel wait {min(100, waited / elapsed * 100):.0f}%, "
                                f"{skipped} skipped, "
                                f"worst turn {worst_turn * 1000:.0f} ms"
                            )
                            frames = 0
                            skipped = 0
                            worst_turn = 0.0
                            rectangles_sent = 0
                            wholes = 0
                            total_bytes = 0
                            stats_at = now

                        worst_turn = max(worst_turn, time.monotonic() - started)
                        remaining = interval - (time.monotonic() - started)
                        if remaining > 0:
                            time.sleep(remaining)
                except lost as err:
                    print(f"Lost the board ({err}), waiting for it to come back")
                finally:
                    writer.close()
                    if device is not None:
                        usb.util.dispose_resources(device)
                    else:
                        endpoint.close()
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
