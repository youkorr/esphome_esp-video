#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Run portall_bt's own USB layer against a stand-in for ESP-IDF's library.

components/portall_bt/idf_usb.cpp is what `transport: idf` compiles instead of
CherryUSB. Nothing here can build it for a board, so this builds it with g++
against tools/btstub/usb/ -- the library's own declarations, copied from
ESP-IDF v5.5.5 -- and links it to tools/idfusbtest/fakehost.cpp, which plays
the library AND a dongle behind it: real threads, callbacks delivered from the
client task the way the library delivers them, transfers that never time out
by themselves, endpoints that halt on an error and stay halted until cleared,
a flush that hands transfers back CANCELED, an unplug that hands them back
NO_DEVICE. On top of it runs portall_bt's SHIPPED probe, the code that drives
four boards today through CherryUSB, and its Bluedroid transport.

Then it checks itself: four copies of idf_usb.cpp, each with one of the
layer's own rules taken out, must each fail something. A layer whose checks
pass whether or not the rule is there has not been checked.

What it CANNOT say: whether the real library behaves as the stand-in does, and
anything about a board. The stand-in is the library's documentation and
source read at v5.5.5, not the library.

    python3 tools/checkidfusb.py            every scenario, then the mutants
    python3 tools/checkidfusb.py --show     and print the component's log
    python3 tools/checkidfusb.py --copy     the same against usb_bluetooth/,
                                            renamed the way checkusbbt.py does
"""

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "components" / "portall_bt"
COPY = ROOT / "usb_bluetooth" / "components" / "usb_bluetooth"
HERE = ROOT / "tools" / "idfusbtest"
TESTS = ROOT / "tools" / "bttest"

SCENARIOS = [
    "broadcom", "realtek", "slow", "error", "bluedroid", "layer", "rejoin", "other", "watchdog", "restart",
    "hub",
]

def rename(text):
    """The rename usb_bluetooth/ was made with -- tools/checkusbbt.py's."""
    for old, new in (
        ("PORTALL_BT", "USB_BLUETOOTH"),
        ("PortallBT", "UsbBluetooth"),
        ("portall_bt", "usb_bluetooth"),
        ("set_key_sink", "add_key_listener"),
        ("set_home_sink", "add_home_listener"),
    ):
        text = text.replace(old, new)
    return text


FLAGS = [
    "-std=gnu++17",
    "-pthread",
    "-Wall",
    "-Wextra",
    "-Wno-unused-parameter",
    "-DUSE_ESP32",
    "-DHCI_USB_IDF",
    "-DBT_HARNESS_REAL_RTOS",
    "-DBT_HARNESS_OWNS_HOST",
    "-DCONFIG_BT_BLUEDROID_ENABLED=1",
    "-DCONFIG_BT_A2DP_ENABLE=1",
    "-DCONFIG_BT_HID_HOST_ENABLED=1",
    "-DUSE_SPEAKER",
    f"-I{ROOT / 'tools' / 'btstub'}",
    f"-I{COMPONENT}",
    f"-I{ROOT / 'tools' / 'bttest'}",
    f"-I{HERE}",
]

# Everything but the layer itself, built once. The harness includes
# portall_bt.cpp; the rest are its other translation units, named rather than
# globbed for the reason checkbt.py gives.
COMMON = [
    HERE / "harness.cpp",
    HERE / "fakehost.cpp",
    HERE / "rtos.cpp",
    COMPONENT / "hid.cpp",
    COMPONENT / "a2dp.cpp",
    COMPONENT / "keys.cpp",
    COMPONENT / "hid_descriptor.cpp",
    COMPONENT / "hci_ledger.cpp",
    COMPONENT / "speaker" / "portall_bt_speaker.cpp",
]

# The rules, one taken out at a time: the layer's own, and the ones that keep
# Bluedroid alive across a dongle that goes away. Each (file, old, new) must
# match exactly once in the shipped file, or this stops rather than checking
# the shipped file a second time and calling it a mutant.
MUTANTS = {
    "a read that timed out submits again instead of collecting its transfer": [(
        "idf_usb.cpp",
        "  if (!pipe->pending) {\n    // The library wants an IN transfer to be whole packets.",
        "  if (true) {\n    // The library wants an IN transfer to be whole packets.",
    )],
    "a halted endpoint is never cleared": [(
        "idf_usb.cpp",
        "  if (pipe->halted) {\n    usb_host_endpoint_clear(g_host.dev, pipe->address);\n"
        "    pipe->halted = false;\n  }\n  pipe->xfer->num_bytes = num_bytes;",
        "  pipe->xfer->num_bytes = num_bytes;",
    )],
    "a completion left by the last dongle is not drained": [(
        "idf_usb.cpp",
        "  while (xSemaphoreTake(pipe->done, 0) == pdTRUE) {\n  }\n",
        "",
    )],
    "a read that times out cancels its transfer": [(
        "idf_usb.cpp",
        "  if (!wait_done(pipe, ms))\n    return g_host.gone ? -USB_ERR_NODEV : -USB_ERR_TIMEOUT;\n"
        "  const int err = reap(pipe);",
        "  if (!wait_done(pipe, ms)) {\n    if (g_host.gone)\n      return -USB_ERR_NODEV;\n"
        "    cancel(pipe);\n    return -USB_ERR_TIMEOUT;\n  }\n  const int err = reap(pipe);",
    )],
    "a dongle that goes is not answered for": [(
        "portall_bt.cpp",
        "    answered = g_ledger.lost(outbox_put, nullptr);\n",
        "    answered = 0;\n",
    )],
    "a command sent while it is away goes unanswered": [(
        "portall_bt.cpp",
        "      g_ledger.answer_for(body, body_len, outbox_put, nullptr);\n",
        "",
    )],
    "the dongle that comes back is not told its settings again": [(
        "portall_bt.cpp",
        "    const size_t count = g_ledger.kept();\n",
        "    const size_t count = 0;\n",
    )],
    "a dongle that stops answering one command is waited on for ever": [(
        "portall_bt.cpp",
        "    late = g_ledger.overdue(now_ms(), ANSWER_LIMIT_MS, outbox_put, nullptr, &first);\n",
        "    late = 0;\n",
    )],
    "a dongle that has stopped answering is never restarted": [(
        "portall_bt.cpp",
        "    if (this->strikes_ >= RESTART_STRIKES) {\n",
        "    if (false) {\n",
    )],
    "the root port is switched off and never on again": [(
        "idf_usb.cpp",
        "      const esp_err_t err = usb_host_lib_set_root_port_power(true);\n",
        "      const esp_err_t err = ESP_FAIL;\n",
    )],
    "a setting given again keeps its first value": [(
        "hci_ledger.cpp",
        "    if (this->arena_[at + 2] == len && memcmp(&this->arena_[at + 3], params, len) == 0)\n"
        "      return;  // said again, the same: nothing changed\n",
        "    return;\n",
    )],
    "the links are not closed when it goes": [(
        "hci_ledger.cpp",
        "    emit(ev, sizeof(ev), arg);\n    emitted++;\n  }\n  this->link_count_ = 0;",
        "    (void) ev;\n  }\n  this->link_count_ = 0;",
    )],
}


def compile_object(source: pathlib.Path, out: pathlib.Path) -> str:
    result = subprocess.run(
        ["g++", *FLAGS, "-c", "-o", str(out), str(source)], capture_output=True, text=True
    )
    if result.returncode != 0:
        return result.stderr
    # A warning in the component is a fault worth seeing. One in the harness or
    # a stand-in is not shipped: linkstubs.h carries helpers other tests use.
    ours = [line for line in result.stderr.splitlines() if "warning" in line and str(COMPONENT) in line]
    return "\n".join(ours) if ours else ""


def link(objects, out: pathlib.Path) -> str:
    result = subprocess.run(
        ["g++", "-pthread", "-o", str(out), *map(str, objects)], capture_output=True, text=True
    )
    return result.stderr if result.returncode != 0 else ""


def run_scenarios(binary: pathlib.Path, show: bool, quiet: bool) -> int:
    failures = 0
    env = dict(os.environ)
    if show:
        env["IDFUSB_SHOW_LOG"] = "1"
    for name in SCENARIOS:
        try:
            run = subprocess.run(
                [str(binary), name], capture_output=True, text=True, timeout=120, env=env
            )
            out, code = run.stdout, run.returncode
        except subprocess.TimeoutExpired as expired:
            out = (expired.stdout or b"").decode(errors="replace") if isinstance(expired.stdout, bytes) else (expired.stdout or "")
            out += "  ECHEC  the scenario did not finish in 120 s\n"
            code = 1
        if code < 0:
            out += f"  ECHEC  the scenario died (signal {-code})\n"
            code = 1
        failures += code
        if not quiet:
            print(f"  {name}")
            for line in out.rstrip().splitlines():
                print(f"  {line}" if line.startswith("  ") else f"    {line}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--show", action="store_true", help="print the component's own log too")
    parser.add_argument("--no-mutants", action="store_true", help="skip the self-check")
    parser.add_argument("--copy", action="store_true", help="check usb_bluetooth/ instead")
    args = parser.parse_args()

    global COMPONENT, FLAGS, COMMON
    with tempfile.TemporaryDirectory(prefix="idfusb") as tmp:
        tmp = pathlib.Path(tmp)
        if args.copy:
            # The same harness and stubs, renamed; the fake library is the
            # library and needs nothing. The component's own sources are the
            # copy's, read where they are.
            tests = tmp / "tests"
            tests.mkdir()
            for source in [*TESTS.glob("*.h"), *HERE.glob("*.h"), HERE / "harness.cpp"]:
                (tests / source.name).write_text(rename(source.read_text()))
            FLAGS = [
                flag.replace(str(COMPONENT), str(COPY)).replace(str(TESTS), str(tests)).replace(
                    f"-I{HERE}", f"-I{tests}"
                )
                for flag in FLAGS
            ]
            COMMON = [tests / "harness.cpp", HERE / "fakehost.cpp", HERE / "rtos.cpp"] + [
                COPY / rename(str(path.relative_to(COMPONENT))) for path in COMMON[3:]
            ]
            COMPONENT = COPY
        shipped = COMPONENT / "idf_usb.cpp"
        text = shipped.read_text()

        objects = []
        by_source = {}
        for source in COMMON:
            obj = tmp / (source.stem + ".o")
            err = compile_object(source, obj)
            if not obj.exists():
                print(f"  ECHEC  {source.name} would not build")
                for line in err.strip().splitlines():
                    print(f"         {line}")
                return 1
            if err:
                print(f"  ECHEC  {source.name} built with warnings")
                for line in err.strip().splitlines():
                    print(f"         {line}")
                return 1
            objects.append(obj)
            by_source[source.name] = obj

        layer = tmp / "idf_usb.o"
        err = compile_object(shipped, layer)
        if not layer.exists() or err:
            print(f"  ECHEC  {shipped.relative_to(ROOT)} would not build cleanly")
            for line in err.strip().splitlines():
                print(f"         {line}")
            return 1
        binary = tmp / "harness"
        err = link([*objects, layer], binary)
        if err:
            print("  ECHEC  the harness would not link")
            for line in err.strip().splitlines():
                print(f"         {line}")
            return 1

        print(f"The shipped layer ({shipped.relative_to(ROOT)}):")
        failures = run_scenarios(binary, args.show, quiet=False)
        if failures:
            print(f"\n{failures} check(s) failed.")
            return 1
        if args.no_mutants:
            return 0

        print("\nThe check against itself -- each copy below must fail something:")
        caught_all = True
        harness_name = COMMON[0].name
        for label, edits in MUTANTS.items():
            texts = {}
            for file, old, new in edits:
                if args.copy:
                    file, old, new = rename(file), rename(old), rename(new)
                base = texts.get(file, (COMPONENT / file).read_text())
                count = base.count(old)
                if count != 1:
                    print(f"  ECHEC  '{label}': the edit matches {count} times in {file}")
                    return 1
                texts[file] = base.replace(old, new)
            parts = dict(by_source)
            layer_obj = layer
            mutdir = tmp / "mutsrc"
            mutdir.mkdir(exist_ok=True)
            for old_file in mutdir.iterdir():
                old_file.unlink()
            failed = None
            for file, mutated in texts.items():
                obj = tmp / ("mutant_" + pathlib.Path(file).stem + ".o")
                obj.unlink(missing_ok=True)
                if file.endswith("idf_usb.cpp") or file.endswith("hci_ledger.cpp"):
                    # Beside the original, so its quoted includes resolve the same way.
                    copy = tmp / ("mutant_" + file)
                    copy.write_text(mutated)
                    command = ["g++", *FLAGS, f"-iquote{COMPONENT}", "-c", "-o", str(obj), str(copy)]
                else:
                    # The component's main file is #included by the harness, so
                    # the harness is rebuilt with the mutant first on the path.
                    (mutdir / file).write_text(mutated)
                    command = ["g++", f"-I{mutdir}", *FLAGS, "-c", "-o", str(obj), str(COMMON[0])]
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode != 0:
                    failed = result.stderr
                    break
                if file.endswith("idf_usb.cpp"):
                    layer_obj = obj
                elif file.endswith("hci_ledger.cpp"):
                    parts["hci_ledger.cpp"] = obj
                else:
                    parts[harness_name] = obj
            if failed is not None:
                print(f"  ECHEC  '{label}': the mutant would not build")
                for line in failed.strip().splitlines()[:10]:
                    print(f"         {line}")
                return 1
            mutant = tmp / "mutant"
            err = link([*parts.values(), layer_obj], mutant)
            if err:
                print(f"  ECHEC  '{label}': the mutant would not link")
                return 1
            caught = run_scenarios(mutant, False, quiet=True)
            if caught:
                print(f"  ok     {label}: {caught} check(s) fail")
            else:
                caught_all = False
                print(f"  ECHEC  {label}: every check still passes")
        if not caught_all:
            return 1
    print("\nAll scenarios pass, and every rule taken out is caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
