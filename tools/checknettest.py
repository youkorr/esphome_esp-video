#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""The network test bench in tools/nettest/, checked as far as a workstation can.

WHY THIS EXISTS. esphome/esphome#23122 sends ESPHome's high-performance Wi-Fi
settings to the ESP32-C6 behind an ESP32-P4. Its first tester measured on a
link of about 3 Mbit/s -- a tenth of what Espressif publishes for the pair --
with a C6 reporting firmware 0.0.0, and saw TCP sending fall. A bench that
anybody can flash twice and measure from a computer on a cable is the
answer, and it must not be the thing that is wrong. So:

  1. servers.cpp and facts.cpp, the shipped files, built for this machine and
     measured by the shipped nettest.py over loopback: the numbers it prints
     are the board's own account, the facts line parses, the table groups by
     build, and a wrong address says so rather than hanging.
  2. nettest.cpp, servers.cpp and facts.cpp checked by g++ against ESPHome's
     own core headers, with and without Wi-Fi, as the board builds them.
  3. The two YAMLs, staged with a local copy of the component and, for the
     PR build, the pull request's wifi component: ESPHome's config and its
     CODEGEN run, and the sdkconfig each build would receive read back --
     with PSRAM guaranteed and not. That is what says the two builds differ
     in the Wi-Fi settings under test and in nothing else.

    python3 tools/checknettest.py
    python3 tools/checknettest.py --esphome-python VENV/bin/python \\
        --esphome-src ESPHOME_TREE --pr-components PR_TREE/esphome/components

Without --esphome-python the third part is skipped and says so. Not compiled
by ESP-IDF, and not flashed: no board and no toolchain here.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = ROOT / "tools" / "nettest"
COMPONENT = BENCH / "components" / "nettest"
HARNESS = ROOT / "tools" / "nettesttest"
fails = 0

# What the PR build should send to the C6, read off the pull request: with
# PSRAM guaranteed esp_wifi_remote offers only static TX buffers, so the
# dynamic TX count is not set then.
PR_OPTIONS = {
    "CONFIG_WIFI_RMT_STATIC_RX_BUFFER_NUM": 16,
    "CONFIG_WIFI_RMT_DYNAMIC_RX_BUFFER_NUM": 64,
    "CONFIG_WIFI_RMT_AMPDU_TX_ENABLED": True,
    "CONFIG_WIFI_RMT_TX_BA_WIN": 32,
    "CONFIG_WIFI_RMT_AMPDU_RX_ENABLED": True,
    "CONFIG_WIFI_RMT_RX_BA_WIN": 32,
}
PR_WITHOUT_PSRAM = {"CONFIG_WIFI_RMT_DYNAMIC_TX_BUFFER_NUM": 64}


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def free_port():
    """Three consecutive free ports."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        if base > 65000:
            continue
        try:
            held = []
            for offset in range(3):
                for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                    s = socket.socket(socket.AF_INET, kind)
                    s.bind(("127.0.0.1", base + offset))
                    held.append(s)
            for s in held:
                s.close()
            return base
        except OSError:
            for s in held:
                s.close()
    raise RuntimeError("no three free ports in a row")


# A PR build with PSRAM guaranteed, as far as the facts line can say it.
HOST_DEFINES = [
    "-DUSE_ESP32_HOSTED",
    "-DCONFIG_WIFI_RMT_STATIC_RX_BUFFER_NUM=16",
    "-DCONFIG_WIFI_RMT_DYNAMIC_RX_BUFFER_NUM=64",
    "-DCONFIG_WIFI_RMT_STATIC_TX_BUFFER=1",
    "-DCONFIG_WIFI_RMT_STATIC_TX_BUFFER_NUM=16",
    "-DCONFIG_WIFI_RMT_CACHE_TX_BUFFER_NUM=32",
    "-DCONFIG_WIFI_RMT_RX_BA_WIN=32",
    "-DCONFIG_WIFI_RMT_TX_BA_WIN=32",
    "-DCONFIG_LWIP_TCP_WND_DEFAULT=512000",
    "-DCONFIG_LWIP_TCP_SND_BUF_DEFAULT=65534",
    "-DCONFIG_LWIP_TCP_SACK_OUT=1",
    "-DCONFIG_SPIRAM_USE_MALLOC=1",
]


def part_host(tmp):
    print("1. the servers and the facts line on this machine, nettest.py "
          "against them:")
    binary = tmp / "nettest-host"
    build = ["g++", "-std=gnu++20", "-O2", "-Wall", "-Wextra", "-Werror",
             "-pthread", f"-I{HARNESS / 'stub'}", f"-I{COMPONENT}",
             *HOST_DEFINES, str(HARNESS / "host.cpp"),
             str(COMPONENT / "servers.cpp"), str(COMPONENT / "facts.cpp"),
             "-o", str(binary)]
    made = subprocess.run(build, capture_output=True, text=True)
    check("built with -Wall -Wextra -Werror", made.returncode == 0,
          made.stderr[-1500:])
    if made.returncode != 0:
        return
    port = free_port()
    server = subprocess.Popen([str(binary), str(port), "host"],
                              stdout=subprocess.PIPE, text=True)
    try:
        check("it starts", server.stdout.readline().strip() == "ready")
        out = tmp / "results.json"
        script = [sys.executable, str(BENCH / "nettest.py")]
        run = subprocess.run(script + ["127.0.0.1", "--port", str(port),
                                       "--seconds", "2", "--rounds", "2",
                                       "--note", "loopback",
                                       "--out", str(out)],
                             capture_output=True, text=True, timeout=120)
        print("    " + "\n    ".join(run.stdout.strip().splitlines()[:9]))
        check("a run ends well", run.returncode == 0, run.stderr[-800:])
        records = json.loads(out.read_text()) if out.exists() else []
        check("two rounds are saved", len(records) == 2, len(records))
        if not records:
            return
        first = records[0]
        facts = first["facts"]
        check("the board's build and C6 firmware are in every record",
              all(r["facts"].get("build") == "host"
                  and r["facts"].get("c6") == "2.12.13" for r in records),
              facts)
        check("and what it was compiled with",
              facts.get("rx") == "16/64" and facts.get("tx") == "static16"
              and facts.get("ba") == "rx32/tx32" and facts.get("wnd") == "512000"
              and facts.get("sack") == "1", facts)
        check("its heap, free and lowest, and its signal",
              facts.get("heap") == "123456/98765" and facts.get("rssi") == "-48",
              facts)
        check("how long it has been up and why it last started",
              facts.get("up", "").isdigit() and facts.get("reset") == "host",
              facts)
        check("and where malloc() goes", facts.get("malloc") == "psram", facts)
        rx = first["rx"]
        check("rx: the board received everything sent, and timed it",
              rx["got"] == rx["sent"] > 0 and rx["us"] > 0
              and rx["mbit"] and rx["mbit"] > 0, rx)
        check("and said the longest it waited between two reads",
              isinstance(rx.get("gap_ms"), int), rx)
        check("tx: something arrived for about the time asked",
              first["tx"]["got"] > 0 and 1.5 < first["tx"]["s"] < 3.0,
              first["tx"])
        check("burst: ten of 512 KB, each timed by the board",
              len(first["burst"]["mbit"]) == 10
              and first["burst"]["median"] > 0, first["burst"])
        check("ping: answered", first["ping"]["lost"] == 0
              and first["ping"]["median_ms"] is not None, first["ping"])
        check("the note is kept", first.get("note") == "loopback")

        # A second build's results in the same file, then the table.
        other = dict(records[0])
        other["facts"] = {**facts, "build": "other", "rx": "10/32"}
        out.write_text(json.dumps(records + [other]))
        table = subprocess.run(script + ["--table", "--out", str(out)],
                               capture_output=True, text=True, timeout=30)
        print("    " + "\n    ".join(table.stdout.strip().splitlines()[:5]))
        rows = [line for line in table.stdout.splitlines()
                if line.startswith("| 127.0.0.1 | host ")
                or line.startswith("| 127.0.0.1 | other ")]
        check("the table has one row per build", len(rows) == 2, table.stdout)
        check("and counts the runs of each",
              any(r.startswith("| 127.0.0.1 | host | 2.12.13 | psram | full | 2 ")
                  for r in rows),
              rows)
        check("and says what each build was compiled with",
              "- 127.0.0.1, host, C6 2.12.13: rx=16/64 tx=static16" in table.stdout
              and "sack=1 malloc=psram" in table.stdout
              and "- 127.0.0.1, other, C6 2.12.13: rx=10/32" in table.stdout,
              table.stdout)
        check("and gives Espressif's own figures beside them",
              "about 30 Mbit/s to the board and 32 from it" in table.stdout)

        # Two boards measured into one file, which is what a household did:
        # the same build on another address is another row, not more runs.
        second = dict(records[0])
        second["host"] = "192.168.1.15"
        out.write_text(json.dumps(records + [second]))
        table = subprocess.run(script + ["--table", "--out", str(out)],
                               capture_output=True, text=True, timeout=30)
        rows = [line for line in table.stdout.splitlines()
                if " | host | 2.12.13 | psram | full | " in line]
        check("two boards in one file are two rows, each with its own runs",
              len(rows) == 2
              and any(r.startswith("| 127.0.0.1 | host | 2.12.13 | psram | "
                                   "full | 2 ") for r in rows)
              and any(r.startswith("| 192.168.1.15 | host | 2.12.13 | psram | "
                                   "full | 1 ") for r in rows), table.stdout)

        nobody = free_port()
        wrong = subprocess.run(script + ["127.0.0.1", "--port", str(nobody),
                                         "--out", str(tmp / "none.json")],
                               capture_output=True, text=True, timeout=30)
        check("an address with no bench says so and stops",
              wrong.returncode == 1 and "No answer" in wrong.stdout,
              wrong.stdout + wrong.stderr)
        check("and writes nothing", not (tmp / "none.json").exists())

        paced = tmp / "paced.json"
        done = subprocess.run(script + ["127.0.0.1", "--port", str(port),
                                        "--rate", "20", "--seconds", "3",
                                        "--rounds", "1", "--out", str(paced)],
                              capture_output=True, text=True, timeout=120)
        record = json.loads(paced.read_text())[0] if paced.exists() else {}
        mbit = record.get("rx", {}).get("mbit")
        check("--rate 20: the board receives about 20 Mbit/s",
              done.returncode == 0 and mbit and 16 < mbit < 21.5,
              (mbit, done.stdout[-600:]))
        check("and the rate is kept with the run", record.get("rate") == 20)
    finally:
        server.kill()
    part_stopped(tmp, binary)


def start(binary, port, mode=""):
    process = subprocess.Popen([str(binary), str(port), "host"]
                               + ([mode] if mode else []),
                               stdout=subprocess.PIPE, text=True)
    process.stdout.readline()
    return process


def part_stopped(tmp, binary):
    """A board that stops reading, one that restarts, and one that goes."""
    print("   a board whose receive path stops:")
    script = [sys.executable, "-u", str(BENCH / "nettest.py")]
    quick = ["--rounds", "1", "--seconds", "2", "--timeout", "3"]

    def run(name, after_stop, wait=8, when="STOPPED"):
        port = free_port()
        board = start(binary, port, "stall")
        # Up for a few seconds first, as a real board always is: a restart is
        # told by an uptime smaller than it should be by now.
        time.sleep(3)
        out = tmp / f"{name}.json"
        process = subprocess.Popen(script + ["127.0.0.1", "--port", str(port),
                                             *quick, "--wait", str(wait),
                                             "--out", str(out)],
                                   stdout=subprocess.PIPE, text=True)
        lines = []
        for line in process.stdout:
            lines.append(line)
            # Once, at the first line that says it: after the script has
            # asked the board whether it is still there would be too late
            # for a board that is meant to be gone by then.
            if when in line and len([x for x in lines if when in x]) == 1:
                board = after_stop(board, port)
        process.wait(timeout=120)
        if board is not None:
            board.kill()
        record = json.loads(out.read_text())[0] if out.exists() else {}
        return process.returncode, "".join(lines), record, out

    code, text, record, up_file = run("up", lambda board, port: board)
    stop = (record.get("stopped") or [{}])[0]
    check("a board that stops reading: the test is called stopped, the run "
          "goes on", code == 0 and "rx     STOPPED: the board took nothing "
          "for 3 s" in text, text[-800:])
    check("and it is said to be still up, not restarted",
          "still up and did not restart" in text and stop.get("state") == "up"
          and stop.get("test") == "rx", (stop, text[-500:]))
    check("the next test still runs",
          (record.get("tx", {}).get("mbit") or 0) > 0, record.get("tx"))

    # Restarted while the receive test is under way, as a board that
    # crashes does: by the time the run asks after it, it is a new one.
    # Not at the STOPPED line, which the run follows at once with its own
    # question -- the old board would still be there to answer it.
    fresh = []

    def restart(board, port):
        def later():
            time.sleep(1.5)
            board.kill()
            board.wait()
            fresh.append(start(binary, port))
        threading.Thread(target=later, daemon=True).start()
        return None
    code, text, record, again_file = run("restarted", restart, when="ping")
    for one in fresh:
        one.kill()
    stop = (record.get("stopped") or [{}])[0]
    check("a board that restarts during a test: said, with the reason",
          "RESTARTED (reset reason: host)" in text
          and stop.get("state") == "restarted" and stop.get("reset") == "host",
          (stop, text[-800:]))
    check("and the run goes on against it",
          code == 0 and (record.get("tx", {}).get("mbit") or 0) > 0,
          text[-500:])

    def vanish(board, port):
        board.kill()
        board.wait()
        return None
    code, text, record, _ = run("gone", vanish, wait=4, when="ping")
    stop = (record.get("stopped") or [{}])[0]
    check("a board that stops answering: said, the run ends, the log is "
          "asked for", code == 1 and "has not answered for 4 s" in text
          and "Its log in ESPHome says why" in text
          and stop.get("state") == "gone", text[-800:])
    check("and what was measured before is still saved",
          record.get("ping", {}).get("lost") == 0, record.get("ping"))

    both = tmp / "stopped.json"
    both.write_text(json.dumps(json.loads(up_file.read_text())
                               + json.loads(again_file.read_text())))
    table = subprocess.run(script + ["--table", "--out", str(both)],
                           capture_output=True, text=True, timeout=30)
    row = [x for x in table.stdout.splitlines()
           if x.startswith("| 127.0.0.1 | host ")]
    check("the table counts the runs that stopped and the restarts",
          row and "| 2 (1 restart) |" in row[0], table.stdout)
    check("and lists each one",
          "- 127.0.0.1, host, rx: the board took nothing for 3 s; the board stayed up"
          in table.stdout and "; the board restarted (host)" in table.stdout,
          table.stdout)


CORE_DEFINES = """#pragma once
// A workstation build of the nettest component: ESPHome's host HAL, and
// nothing switched on unless asked for on the command line.
#define USE_HOST
#define ESPHOME_LOG_LEVEL 5
#define ESPHOME_COMPONENT_COUNT 4
#define ESPHOME_AREA_COUNT 0
#define ESPHOME_DEVICE_COUNT 0
"""


def find_esphome_src(given):
    if given:
        return pathlib.Path(given)
    try:
        import esphome
        return pathlib.Path(esphome.__file__).resolve().parent.parent
    except ImportError:
        return None


def part_syntax(tmp, esphome_src):
    print("2. the component's C++ against ESPHome's own headers:")
    if esphome_src is None:
        print("  skipped: no ESPHome tree (--esphome-src)")
        return
    staged = tmp / "esphome-headers"
    dst = staged / "esphome"
    shutil.copytree(esphome_src / "esphome" / "core", dst / "core")
    # core/gpio.h and core/hal.h reach into the platform's own component.
    shutil.copytree(esphome_src / "esphome" / "components" / "host",
                    dst / "components" / "host")
    (dst / "core" / "defines.h").write_text(CORE_DEFINES)
    base = ["g++", "-std=gnu++20", "-fsyntax-only", "-Wall", "-Wextra",
            "-Werror", f"-I{HARNESS / 'stub'}", f"-I{staged}", f"-I{COMPONENT}"]
    cases = [
        ("nettest.cpp, no Wi-Fi", "nettest.cpp", []),
        ("nettest.cpp, with Wi-Fi", "nettest.cpp", ["-DUSE_WIFI"]),
        ("facts.cpp, a P4 with a C6", "facts.cpp", HOST_DEFINES),
        ("facts.cpp, nothing defined", "facts.cpp", []),
        ("facts.cpp as the board builds it", "facts.cpp",
         HOST_DEFINES + ["-DESP_PLATFORM"]),
        ("servers.cpp as the board builds it", "servers.cpp",
         ["-DESP_PLATFORM"]),
    ]
    for what, name, extra in cases:
        done = subprocess.run(base + extra + [str(COMPONENT / name)],
                              capture_output=True, text=True)
        check(what, done.returncode == 0, done.stderr[-1500:])


PR_SOURCE = re.compile(r"( *)- source: github://pr#23122\n")


def stage_yaml(text, pr_components, psram_optional, malloc=True):
    sys.path.insert(0, str(ROOT / "tools"))
    from checkyaml import GIT_SOURCE  # noqa: E402
    text = GIT_SOURCE.sub(
        lambda m: f"{m.group(1)}- source:\n{m.group(1)}    type: local\n"
                  f"{m.group(1)}    path: {ROOT / (m.group(3) or 'components').strip()}\n",
        text)
    text = PR_SOURCE.sub(
        lambda m: f"{m.group(1)}- source:\n{m.group(1)}    type: local\n"
                  f"{m.group(1)}    path: {pr_components}\n", text)
    text = text.replace("    refresh: 0s\n", "")
    want = f'  psram_optional: "{"true" if psram_optional else "false"}"\n'
    text = text.replace('  psram_optional: "false"\n', want)
    if not malloc:
        text = text.replace('  malloc_to_psram: "true"\n',
                            '  malloc_to_psram: "false"\n')
    return text


def codegen_one(yaml_path, pr_components, psram_optional, malloc=True):
    """Runs inside the esphome venv: prints a JSON line of what came out."""
    sys.path.insert(0, str(ROOT / "tools"))
    from checkyaml import secrets_for  # noqa: E402
    text = pathlib.Path(yaml_path).read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "secrets.yaml").write_text(
            "".join(f'{k}: "{v}"\n' for k, v in secrets_for(text).items()))
        target = tmp / pathlib.Path(yaml_path).name
        target.write_text(stage_yaml(text, pr_components, psram_optional,
                                     malloc))
        import esphome.config as config_module
        from esphome.__main__ import generate_cpp_contents
        from esphome.components.esp32 import KEY_ESP32, KEY_SDKCONFIG_OPTIONS
        from esphome.core import CORE
        CORE.reset()
        CORE.config_path = target
        config = config_module.read_config({})
        if config is None:
            print(json.dumps({"error": "config refused"}))
            return 1
        CORE.config = config
        generate_cpp_contents(config)
        options = {k: v for k, v in
                   CORE.data[KEY_ESP32][KEY_SDKCONFIG_OPTIONS].items()}
        statements = [str(s) for s in CORE.main_statements]
        print("RESULT " + json.dumps({
            "options": {k: v if isinstance(v, (bool, int)) else str(v)
                        for k, v in options.items()},
            "label": [s for s in statements if "set_label" in s],
        }))
    return 0


def part_yaml(args):
    print("3. the two YAMLs through ESPHome's config and codegen:")
    dev = (BENCH / "nettest-dev.yaml").read_text()
    pr = (BENCH / "nettest-pr.yaml").read_text()
    # The same file line for line, but for what makes them two builds.
    expected = dev.replace(
        '# Network test bench, build "dev": ESPHome as it is, nothing changed.',
        '# Network test bench, build "pr": ESPHome with the wifi component of\n'
        "# esphome/esphome#23122, the high-performance Wi-Fi settings sent to the C6.",
    ).replace(
        "    refresh: 0s\n",
        "    refresh: 0s\n"
        "  # The pull request's components, fetched fresh at every build. Both: since\n"
        "  # its 90beeffb the wifi component asks esp32_hosted for the co-processor's\n"
        "  # variant, and dev's esp32_hosted has no such question -- with wifi alone\n"
        "  # the build fails. Dev has not touched either since the PR's base.\n"
        "  - source: github://pr#23122\n"
        "    components: [wifi, esp32_hosted]\n"
        "    refresh: 0s\n",
    ).replace("  label: dev\n", "  label: pr\n")
    check("nettest-pr.yaml is nettest-dev.yaml with the PR's wifi and its "
          "label, and nothing else", pr == expected)
    # A household's panel already names this repository at main, and ESPHome
    # keys its copy of a repository on the address and branch alone and
    # refreshes it once a day -- so the bench's folder was not found in a
    # copy taken before it existed. Reported from the first try.
    for name, text in (("dev", dev), ("pr", pr)):
        check(f"nettest-{name}.yaml fetches the bench fresh at every build",
              "    components: [nettest]\n" in text
              and "    components: [nettest]\n    # Fetched fresh" in text
              and text.count("    refresh: 0s\n") == (1 if name == "dev" else 2))
    if not args.esphome_python:
        print("  skipped: no ESPHome to run (--esphome-python)")
        return
    if not args.pr_components:
        print("  skipped: no tree of the pull request (--pr-components)")
        return
    env = dict(os.environ)
    if args.esphome_src:
        env["PYTHONPATH"] = str(args.esphome_src)
    results = {}
    for name in ("dev", "pr"):
        for optional in (False, True):
            done = subprocess.run(
                [args.esphome_python, __file__, "--codegen-one",
                 str(BENCH / f"nettest-{name}.yaml"), str(args.pr_components),
                 "1" if optional else "0", "1"],
                capture_output=True, text=True, env=env, timeout=600)
            line = [x for x in done.stdout.splitlines()
                    if x.startswith("RESULT ")]
            what = f"{name}, PSRAM {'optional' if optional else 'guaranteed'}"
            check(f"{what}: config and codegen", done.returncode == 0 and line,
                  (done.stdout + done.stderr)[-2000:])
            if line:
                results[(name, optional)] = json.loads(line[0][7:])
    if len(results) != 4:
        return
    for optional in (False, True):
        mode = "optional" if optional else "guaranteed"
        dev_o = results[("dev", optional)]["options"]
        pr_o = results[("pr", optional)]["options"]
        rmt_dev = {k: v for k, v in dev_o.items() if "WIFI_RMT" in k}
        rmt_pr = {k: v for k, v in pr_o.items() if "WIFI_RMT" in k}
        want = dict(PR_OPTIONS, **(PR_WITHOUT_PSRAM if optional else {}))
        print(f"    PSRAM {mode}: dev sets {rmt_dev or 'no WIFI_RMT option'}")
        print(f"    PSRAM {mode}: pr sets  {rmt_pr}")
        check(f"PSRAM {mode}: dev sends the C6 nothing", rmt_dev == {},
              rmt_dev)
        check(f"PSRAM {mode}: pr sends it the pull request's values",
              rmt_pr == want, rmt_pr)
        # CONFIG_ESP_WIFI_* belong to a radio of the P4's own, which it does
        # not have: since 90beeffb the PR no longer writes them there, and
        # nothing on a P4 reads them. Everything else must be the same.
        others_dev = {k: v for k, v in dev_o.items() if "WIFI_RMT" not in k
                      and not k.startswith("CONFIG_ESP_WIFI_")}
        others_pr = {k: v for k, v in pr_o.items() if "WIFI_RMT" not in k
                     and not k.startswith("CONFIG_ESP_WIFI_")}
        differ = {k for k in set(others_dev) | set(others_pr)
                  if others_dev.get(k) != others_pr.get(k)}
        check(f"PSRAM {mode}: and every other option is the same in both",
              not differ, sorted(differ))
        check(f"PSRAM {mode}: the high-performance request reached lwIP",
              dev_o.get("CONFIG_LWIP_TCP_WND_DEFAULT") == (
                  65534 if optional else 512000),
              dev_o.get("CONFIG_LWIP_TCP_WND_DEFAULT"))
    for name in ("dev", "pr"):
        options = results[(name, False)]["options"]
        check(f"{name}: malloc() may spill into PSRAM, as the file asks",
              options.get("CONFIG_SPIRAM_USE_MALLOC") is True
              and options.get("CONFIG_SPIRAM_USE_CAPS_ALLOC") is False
              and options.get("CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL") == 131072,
              {k: v for k, v in options.items() if "SPIRAM_" in k})
    done = subprocess.run(
        [args.esphome_python, __file__, "--codegen-one",
         str(BENCH / "nettest-dev.yaml"), str(args.pr_components), "0", "0"],
        capture_output=True, text=True, env=env, timeout=600)
    line = [x for x in done.stdout.splitlines() if x.startswith("RESULT ")]
    plain = json.loads(line[0][7:])["options"] if line else {}
    check("malloc_to_psram: false is ESPHome as it is: malloc() internal",
          plain.get("CONFIG_SPIRAM_USE_CAPS_ALLOC") is True
          and "CONFIG_SPIRAM_USE_MALLOC" not in plain,
          {k: v for k, v in plain.items() if "SPIRAM_" in k}
          or (done.stdout + done.stderr)[-1500:])
    check("each build is labelled in the code it generates",
          any('"dev"' in s for s in results[("dev", False)]["label"])
          and any('"pr"' in s for s in results[("pr", False)]["label"]),
          [results[("dev", False)]["label"], results[("pr", False)]["label"]])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--codegen-one":
        return codegen_one(sys.argv[2], sys.argv[3], sys.argv[4] == "1",
                           sys.argv[5] != "0")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--esphome-python")
    parser.add_argument("--esphome-src")
    parser.add_argument("--pr-components")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        part_host(tmp)
        part_syntax(tmp, find_esphome_src(args.esphome_src))
    part_yaml(args)
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
