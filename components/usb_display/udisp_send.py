#!/usr/bin/env python3
"""Send a region of this computer's screen to an ESPHome usb_display board.

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

    pip install pyusb mss pillow libusb-package

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

# crc16, type, cmd, x, y, width, height, then a packed word holding a 10-bit
# frame id and a 22-bit payload length. Little-endian, 16 bytes, and it is
# Espressif's layout -- the firmware parses it byte for byte.
_HEADER = struct.Struct("<HBBHHHHI")

DEFAULT_VID = 0x303A
DEFAULT_PID = 0x4001


def build_header(width, height, payload_len, frame_id):
    if payload_len >= 1 << 22:
        raise ValueError(
            f"payload of {payload_len} bytes does not fit the 22-bit length field"
        )
    packed = (frame_id & 0x3FF) | ((payload_len & 0x3FFFFF) << 10)
    # crc16 and cmd are unused by the board; it validates on the geometry and
    # the length instead.
    return _HEADER.pack(0, UDISP_TYPE_JPG, 0, 0, 0, width, height, packed)


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
                "  - Is the board on its OTG port, running a usb_display firmware?\n"
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
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--monitor",
        str(args.monitor),
        "--fps",
        str(args.fps),
        "--quality",
        str(args.quality),
        "--rotate",
        str(args.rotate),
        "--vid",
        hex(args.vid),
        "--pid",
        hex(args.pid),
    ]
    # Quote every part for the shell, then double the quotes again because the
    # whole command is about to become a VBScript string literal.
    command = " ".join(f'"{part}"' for part in parts).replace('"', '""')

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "' Sends this screen to an ESPHome usb_display board at login.\r\n"
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
        help="must match the width: of the usb_display component",
    )
    parser.add_argument(
        "--height",
        type=int,
        help="must match the height: of the usb_display component",
    )
    parser.add_argument(
        "--monitor", type=int, default=1, help="which monitor to capture (1 = primary)"
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
    parser.add_argument("--vid", type=lambda v: int(v, 0), default=DEFAULT_VID)
    parser.add_argument("--pid", type=lambda v: int(v, 0), default=DEFAULT_PID)
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="run this sender at every login with the options given here, then "
        "exit. Windows only",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="undo --install-startup and exit",
    )
    args = parser.parse_args()

    if args.uninstall_startup:
        return uninstall_startup()
    if args.width is None or args.height is None:
        parser.error("--width and --height are required")
    if args.install_startup:
        return install_startup(args)

    try:
        import mss
        from PIL import Image
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies: pip install pyusb mss pillow"
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

    import usb.core
    import usb.util

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_id = 0

    # mss.mss() is a deprecated alias for mss.MSS(), which older versions do not
    # have.
    screenshotter = getattr(mss, "MSS", None) or mss.mss

    try:
        with screenshotter() as sct:
            monitor = sct.monitors[args.monitor]
            # Outer loop: one pass per connection. Unplugging the board, or
            # reflashing it, ends the inner loop and comes back here to wait for
            # it rather than ending the program.
            while True:
                device, endpoint = wait_for_endpoint(args.vid, args.pid)
                print(
                    f"Sending {args.width}x{args.height} at up to {args.fps:g} fps to "
                    f"{args.vid:04x}:{args.pid:04x}"
                    + (f", rotated {args.rotate} degrees" if args.rotate else "")
                )

                frames = 0
                total_bytes = 0
                stats_at = time.monotonic()
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

                        buffer = io.BytesIO()
                        image.save(buffer, format="JPEG", quality=args.quality)
                        payload = buffer.getvalue()

                        endpoint.write(
                            build_header(
                                args.width, args.height, len(payload), frame_id
                            )
                            + payload
                        )
                        frame_id = (frame_id + 1) & 0x3FF

                        frames += 1
                        total_bytes += len(payload)
                        now = time.monotonic()
                        if now - stats_at >= 5.0:
                            elapsed = now - stats_at
                            print(
                                f"{frames / elapsed:.1f} fps, {total_bytes / frames / 1024:.0f} KiB/frame, "
                                f"{total_bytes / elapsed / 1024 / 1024:.1f} MiB/s"
                            )
                            frames = 0
                            total_bytes = 0
                            stats_at = now

                        remaining = interval - (time.monotonic() - started)
                        if remaining > 0:
                            time.sleep(remaining)
                except usb.core.USBError as err:
                    print(f"Lost the board ({err}), waiting for it to come back")
                finally:
                    usb.util.dispose_resources(device)
    except KeyboardInterrupt:
        print("\nStopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
