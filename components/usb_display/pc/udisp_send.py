#!/usr/bin/env python3
"""Send a region of this computer's screen to an ESPHome usb_display board.

The board speaks Espressif's udisp protocol over a USB vendor interface: a
16-byte header followed by a JPEG of the whole region. Espressif ship a Windows
driver for this; the point of the script is to not need it -- pyusb works on
Linux, macOS and Windows alike.

    ./udisp_send.py --width 1024 --height 600

Requirements:

    pip install pyusb mss pillow

Access to the device:

  Linux   a udev rule, or run as root. Without it pyusb cannot claim the
          interface. Example, as /etc/udev/rules.d/99-udisp.rules:
              SUBSYSTEM=="usb", ATTR{idVendor}=="303a", ATTR{idProduct}=="4001", MODE="0666"
  macOS    libusb is enough (brew install libusb); nothing claims a vendor
          interface, so it is free to take.
  Windows  the interface needs the WinUSB driver bound to it, which is what
          Zadig does in a couple of clicks. Windows will not hand a vendor
          interface to libusb otherwise.
"""

import argparse
import io
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


def find_endpoint(vid, pid):
    import usb.core
    import usb.util

    try:
        device = usb.core.find(idVendor=vid, idProduct=pid)
    except usb.core.NoBackendError as err:
        # pyusb is only a wrapper; without libusb it cannot see any device at
        # all, and its own message says nothing about how to fix that.
        raise SystemExit(
            "pyusb found no libusb backend.\n"
            "  Windows  install libusb: pip install libusb1, or drop libusb-1.0.dll "
            "next to python.exe\n"
            "  Linux    install libusb-1.0-0\n"
            "  macOS    brew install libusb"
        ) from err

    if device is None:
        raise SystemExit(
            f"No device with {vid:04x}:{pid:04x}.\n"
            "  - Is the board on its OTG port, running a usb_display firmware?\n"
            "  - On Windows the interface needs WinUSB bound to it with Zadig; until "
            "then libusb cannot see the device even though Windows shows it."
        )

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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--width",
        type=int,
        required=True,
        help="must match the width: of the usb_display component",
    )
    parser.add_argument(
        "--height",
        type=int,
        required=True,
        help="must match the height: of the usb_display component",
    )
    parser.add_argument(
        "--monitor", type=int, default=1, help="which monitor to capture (1 = primary)"
    )
    parser.add_argument(
        "--fps", type=float, default=30.0, help="frames per second to aim for"
    )
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality, 1..95")
    parser.add_argument("--vid", type=lambda v: int(v, 0), default=DEFAULT_VID)
    parser.add_argument("--pid", type=lambda v: int(v, 0), default=DEFAULT_PID)
    args = parser.parse_args()

    try:
        import mss
        from PIL import Image
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies: pip install pyusb mss pillow"
        ) from err

    device, endpoint = find_endpoint(args.vid, args.pid)
    print(
        f"Sending {args.width}x{args.height} at up to {args.fps:g} fps to {args.vid:04x}:{args.pid:04x}"
    )

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_id = 0
    frames = 0
    total_bytes = 0
    stats_at = time.monotonic()

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[args.monitor]
            while True:
                started = time.monotonic()

                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                # The board draws the frame as it arrives and rejects any other
                # size, so scaling happens here.
                if image.size != (args.width, args.height):
                    image = image.resize((args.width, args.height), Image.BILINEAR)

                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=args.quality)
                payload = buffer.getvalue()

                endpoint.write(
                    build_header(args.width, args.height, len(payload), frame_id)
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
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        import usb.util

        usb.util.dispose_resources(device)
    return 0


if __name__ == "__main__":
    sys.exit(main())
