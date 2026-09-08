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
    for index, monitor in enumerate(monitors[1:], start=1):
        if monitor["width"] == panel_w and monitor["height"] == panel_h:
            print(f"Capturing monitor {index}, which is {panel_w}x{panel_h} "
                  f"-- the panel's own size")
            return monitor
    # Say what there WAS, not only what there was not. Without the list this
    # was a dead end from the other side of a chat window: a virtual display
    # that exists but is the wrong size, one Windows has not been told to
    # extend onto, and one that is simply not there all read the same.
    print(f"No monitor is {panel_w}x{panel_h}. What Windows is showing:")
    describe_monitors(monitors)
    print("Set the virtual display to exactly "
          f"{panel_w}x{panel_h} and it will be picked up by itself. "
          "Sending the primary one, scaled, meanwhile.")
    return monitors[1]


def describe_monitors(monitors):
    """Every screen, as Windows reports it. monitors[0] is all of them joined."""
    for index, monitor in enumerate(monitors):
        where = "all of them joined" if index == 0 else f"at {monitor['left']},{monitor['top']}"
        print(f"    {index}: {monitor['width']}x{monitor['height']}, {where}"
              + ("   <- primary" if index == 1 else ""))


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
    target = os.path.join(target_dir, "udisp_send.py")

    source = os.path.abspath(__file__)
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

    # pythonw.exe is the interpreter without a console; fall back to the one
    # running this if the installation has no windowed build.
    interpreter = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(interpreter):
        interpreter = sys.executable

    copied = _install_copy()
    parts = [
        interpreter,
        copied,
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
    print("It starts at the next login, and waits for the board rather than")
    print("failing when it is not plugged in yet.")
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
    args = parser.parse_args()

    if args.version:
        # The file's own fingerprint, not a number somebody has to remember to
        # bump. GitHub's raw view is behind a cache that will happily serve a
        # copy from an hour ago -- measured, byte for byte -- and from a chat
        # window an old file and a broken one look identical.
        import hashlib

        with open(os.path.abspath(__file__), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:12]
        print(f"udisp_send.py {digest}, {os.path.getsize(os.path.abspath(__file__))} bytes")
        return 0

    if args.uninstall_startup:
        return uninstall_startup()

    if args.list_monitors:
        try:
            import mss
        except ImportError as err:
            raise SystemExit(f"{err}. pip install mss") from err
        screenshotter = getattr(mss, "MSS", None) or mss.mss
        with screenshotter() as sct:
            print("The screens Windows is showing:")
            describe_monitors(sct.monitors)
        print("\nA virtual display made for a panel should be exactly the "
              "panel's size, and Windows has to be set to Extend onto it "
              "rather than Duplicate.")
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

                frames = 0
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

                        shot = sct.grab(monitor)
                        image = Image.frombytes("RGB", shot.size, shot.rgb)
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

                        for x, y, w, h in rectangles:
                            buffer = io.BytesIO()
                            image.crop((x, y, x + w, y + h)).save(
                                buffer, format="JPEG", quality=args.quality
                            )
                            payload = buffer.getvalue()
                            endpoint.write(
                                build_header(w, h, len(payload), frame_id, x, y)
                                + payload
                            )
                            rectangles_sent += 1
                            total_bytes += len(payload)

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
                            endpoint.write(build_heartbeat())
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
                            print(
                                f"{frames / elapsed:.1f} pictures/s, "
                                f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                                f"{wholes} whole, "
                                f"{total_bytes / elapsed / 1024:.1f} KiB/s"
                            )
                            frames = 0
                            rectangles_sent = 0
                            wholes = 0
                            total_bytes = 0
                            stats_at = now

                        remaining = interval - (time.monotonic() - started)
                        if remaining > 0:
                            time.sleep(remaining)
                except lost as err:
                    print(f"Lost the board ({err}), waiting for it to come back")
                finally:
                    if device is not None:
                        usb.util.dispose_resources(device)
                    else:
                        endpoint.close()
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
