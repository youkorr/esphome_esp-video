#!/usr/bin/env python3
"""Run portall_bt's C++ checks against the stand-alone copy in usb_bluetooth/.

usb_bluetooth/components/usb_bluetooth is portall_bt renamed, with the link to
portall taken out and a binary_sensor platform added, so that somebody can use
it with nothing but ESPHome and LVGL. A copy drifts the moment either side is
edited and nothing compares them -- so this runs the SAME checks on it:
tools/checkbt.py's syntax passes and its whole tools/bttest suite, renamed on
the fly, plus tools/usbbttest/, which is what only the copy has.

The rename is the one the copy was made with: PortallBT -> UsbBluetooth,
portall_bt -> usb_bluetooth, and a single key sink -> listeners.

    python3 tools/checkusbbt.py
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
COPY = ROOT / "usb_bluetooth" / "components" / "usb_bluetooth"


def rename(text):
    for old, new in (
        ("PORTALL_BT", "USB_BLUETOOTH"),
        ("PortallBT", "UsbBluetooth"),
        ("portall_bt", "usb_bluetooth"),
        ("set_key_sink", "add_key_listener"),
        ("set_home_sink", "add_home_listener"),
    ):
        text = text.replace(old, new)
    return text


def checker(tests):
    """tools/checkbt.py, pointed at the copy and at the renamed tests."""
    text = (ROOT / "tools" / "checkbt.py").read_text()
    for old, new in (
        ('ROOT / "components" / "portall_bt"', 'ROOT / "usb_bluetooth" / "components" / "usb_bluetooth"'),
        ("ROOT / 'components' / 'portall_bt'", "ROOT / 'usb_bluetooth' / 'components' / 'usb_bluetooth'"),
        ('ROOT / "tools" / "bttest"', f'pathlib.Path({str(tests)!r})'),
        ("test.relative_to(ROOT)", "test.name"),
        ("CONFIGURATIONS = [\n", 'CONFIGURATIONS = [\n    ("binary_sensor: - platform: usb_bluetooth", '
         '["-DCONFIG_BT_BLUEDROID_ENABLED=1", "-DCONFIG_BT_A2DP_ENABLE=1", '
         '"-DCONFIG_BT_HID_HOST_ENABLED=1", "-DUSE_BINARY_SENSOR"]),\n'),
    ):
        # Every replacement must land: one that silently matched nothing would
        # run checkbt against portall_bt and call the COPY clean.
        if old not in text:
            raise SystemExit(f"tools/checkbt.py no longer contains {old!r}; update this tool")
        text = text.replace(old, new)
    text = text.replace("portall_bt", "usb_bluetooth")
    return text.replace(
        "ROOT = pathlib.Path(__file__).resolve().parent.parent",
        f"ROOT = pathlib.Path({str(ROOT)!r})",
    )


def main() -> int:
    if not COPY.is_dir():
        print(f"  {COPY} is not there")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        tests = tmp / "tests"
        tests.mkdir()
        for source in (ROOT / "tools" / "bttest").iterdir():
            if source.suffix in (".cpp", ".h"):
                (tests / source.name).write_text(rename(source.read_text()))
        for source in (ROOT / "tools" / "usbbttest").iterdir():
            if source.suffix in (".cpp", ".h"):
                shutil.copyfile(source, tests / source.name)
        script = tmp / "check.py"
        script.write_text(checker(tests))
        return subprocess.run([sys.executable, str(script)]).returncode


if __name__ == "__main__":
    sys.exit(main())
