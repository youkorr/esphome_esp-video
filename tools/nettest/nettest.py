#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Measure an ESP32-P4 + C6 running the nettest firmware, from a computer.

    python nettest.py 192.168.1.50
    python nettest.py 192.168.1.50 --rate 20
    python nettest.py --table

Each run asks the board what it was built with, then measures:

  ping    20 small UDP round trips
  rx      this computer sends to the board for --seconds; the BOARD times
          what arrives (first byte to last), so the figure is what it
          received, not what this computer managed to queue, and says the
          longest it waited between two reads. As fast as the link takes,
          or at --rate Mbit/s, which is how a Portall sender sends
  tx      the board sends to this computer for --seconds; timed here
  burst   10 transfers of 512 KB to the board, each on its own connection,
          timed by the board -- short transfers that never leave TCP's
          slow start, which is closer to a picture than a long stream is

Every run is appended to nettest-results.json beside this script (--out to
change it), and --table prints a Markdown table of all of them, grouped by
the build each board said it was. Run it from a computer on a CABLE to the
router: a computer on Wi-Fi shares the air with the board, and then the
figure is the two radios, not the board.

A test the board stops taking part in does not end the run: it is recorded,
and the board is asked whether it restarted (and why), is still up, or does
not answer at all -- the three need different next steps.

Standard library only, so it runs on a stock Python on Windows.
"""
import argparse
import json
import os
import socket
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "nettest-results.json")
WRITE = 65536
PACED_WRITE = 8192
BURST = 512 * 1024
BURSTS = 10

# Espressif's own figures for an ESP32-P4 with a C6 over 4-bit SDIO at
# 40 MHz, 2.4 GHz, over the air (esp-hosted-mcu docs/design/performance.md,
# section 1.1). A result far below these says the link is what was measured.
REFERENCE = {"rx": 30.0, "tx": 32.0}


def ask(host, port, message, timeout=2.0):
    """One UDP question, one answer, or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(timeout)
        udp.sendto(message, (host, port))
        try:
            return udp.recvfrom(2048)[0]
        except socket.timeout:
            return None


def parse_facts(text):
    """'bytes=1 build=pr rx=16/64 ...' -> {'bytes': '1', 'build': 'pr', ...}."""
    out = {}
    for word in text.split():
        key, sep, value = word.partition("=")
        if sep:
            out[key] = value
    return out


def ping(host, port, count=20):
    times = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(1.0)
        for _ in range(count):
            start = time.perf_counter()
            udp.sendto(b"p", (host, port))
            try:
                udp.recvfrom(64)
                times.append((time.perf_counter() - start) * 1000)
            except socket.timeout:
                pass
            time.sleep(0.05)
    return {"median_ms": round(statistics.median(times), 1) if times else None,
            "lost": count - len(times), "of": count}


def send_to_board(host, port, seconds=None, size=None, rate=0.0, timeout=15):
    """Send for `seconds` or `size` bytes, at `rate` Mbit/s if not 0, and
    return the board's own account. Raises OSError when the board stops
    taking data for `timeout` seconds."""
    # Paced in smaller pieces, so a fixed rate is not a train of 64 KB bursts.
    write = PACED_WRITE if rate else WRITE
    block = bytes(range(256)) * (write // 256)
    with socket.create_connection((host, port), timeout=timeout) as tcp:
        tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sent = 0
        start = time.perf_counter()
        while True:
            if seconds is not None and time.perf_counter() - start >= seconds:
                break
            if size is not None and sent >= size:
                break
            if rate:
                due = start + sent * 8 / (rate * 1e6)
                if due > time.perf_counter():
                    time.sleep(due - time.perf_counter())
            piece = block if size is None else block[:min(write, size - sent)]
            tcp.sendall(piece)
            sent += len(piece)
        tcp.shutdown(socket.SHUT_WR)
        reply = b""
        while not reply.endswith(b"\n"):
            data = tcp.recv(1024)
            if not data:
                break
            reply += data
    facts = parse_facts(reply.decode("ascii", "replace"))
    got = int(facts.get("bytes", 0))
    us = int(facts.get("us", 0))
    gap = facts.get("gap")
    return {"sent": sent, "got": got, "us": us,
            "gap_ms": int(gap) if gap is not None and gap.isdigit() else None,
            "mbit": round(got * 8 / us, 2) if us > 0 else None}, facts


def receive_from_board(host, port, seconds, timeout=15):
    with socket.create_connection((host, port), timeout=timeout) as tcp:
        tcp.sendall(f"{seconds}\n".encode())
        got = 0
        first = last = None
        while True:
            data = tcp.recv(65536)
            if not data:
                break
            now = time.perf_counter()
            if first is None:
                first = now
            last = now
            got += len(data)
    took = (last - first) if first is not None else 0
    return {"got": got, "s": round(took, 3),
            "mbit": round(got * 8 / took / 1e6, 2) if took > 0 else None}


def failure(err, timeout):
    """What a test that stopped says, in words."""
    if isinstance(err, (socket.timeout, TimeoutError)):
        return f"the board took nothing for {timeout} s"
    if isinstance(err, ConnectionRefusedError):
        return "the board refused the connection"
    if isinstance(err, (ConnectionResetError, ConnectionAbortedError)):
        return "the board dropped the connection"
    return f"{type(err).__name__}: {err}"


def diagnose(host, port, before, before_at, limit=90):
    """After a test stopped: did the board restart, is it still up, or gone?

    Its uptime is compared with what it should be by now, so a restart is
    told apart from a board that merely stopped reading. A firmware from
    before the uptime was added cannot say, and that is said too."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        answer = ask(host, port + 2, b"i", timeout=2)
        if answer is not None:
            now = parse_facts(answer.decode("ascii", "replace"))
            if "up" not in now or "up" not in before:
                return {"state": "answers", "facts": now}, (
                    "it answers again; this firmware does not say whether it "
                    "restarted (flash the bench again to know)")
            expected = int(before["up"]) + (time.monotonic() - before_at)
            # Its uptime is whole seconds: 2 s of slack, and a restart
            # takes longer than that to come back from.
            if int(now["up"]) < expected - 2:
                return {"state": "restarted", "reset": now.get("reset"),
                        "facts": now}, (
                    f"it RESTARTED (reset reason: {now.get('reset')}), "
                    f"up {now['up']} s")
            return {"state": "up", "facts": now}, (
                "it is still up and did not restart: it stopped reading")
        time.sleep(2)
    return {"state": "gone"}, f"it has not answered for {limit} s"


def save(path, records):
    saved = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(saved + records, handle, indent=1)


def run(args):
    host, port = args.host, args.port
    answer = ask(host, port + 2, b"i")
    if answer is None:
        print(f"No answer from {host} on UDP {port + 2}. Is the nettest "
              f"firmware on that board, and is this the board's address?")
        return 1
    facts = parse_facts(answer.decode("ascii", "replace"))
    facts_at = time.monotonic()
    print(f"Board: {answer.decode('ascii', 'replace')}")
    rate = f"{args.rate:g} Mbit/s" if args.rate else "as fast as it takes"
    gone = False
    for round_ in range(args.rounds):
        print(f"Round {round_ + 1} of {args.rounds}, build {facts.get('build')}, "
              f"sending {rate}:")
        record = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": host,
                  "note": args.note, "rate": args.rate, "facts": facts,
                  "stopped": []}

        def stopped(test, err):
            nonlocal facts, facts_at, gone
            print(f"  {test:6} STOPPED: {failure(err, args.timeout)}")
            state, said = diagnose(host, port, facts, facts_at, args.wait)
            print(f"         {said}")
            record["stopped"].append({"test": test,
                                      "error": failure(err, args.timeout),
                                      **{k: v for k, v in state.items()
                                         if k != "facts"}})
            if "facts" in state:
                facts, facts_at = state["facts"], time.monotonic()
            gone = state["state"] == "gone"

        record["ping"] = ping(host, port + 2)
        print(f"  ping   {record['ping']['median_ms']} ms, "
              f"{record['ping']['lost']} of {record['ping']['of']} lost")
        if (record["ping"]["median_ms"] or 0) > 50:
            print(f"         that is slow for a ping across a room. The radio "
                  f"says its power save is {facts.get('ps', 'unknown')}: a "
                  f"radio that sleeps between beacons holds every packet for "
                  f"one.")
        try:
            rx, said = send_to_board(host, port, seconds=args.seconds,
                                     rate=args.rate, timeout=args.timeout)
            record["rx"] = rx
            print(f"  rx     {rx['mbit']} Mbit/s, the board received "
                  f"{rx['got']} of {rx['sent']} bytes, its longest wait "
                  f"between two reads {rx['gap_ms']} ms")
        except OSError as err:
            record["rx"] = {"mbit": None}
            stopped("rx", err)
        if not gone:
            time.sleep(1)
            try:
                tx = receive_from_board(host, port + 1, args.seconds,
                                        timeout=args.timeout)
                record["tx"] = tx
                print(f"  tx     {tx['mbit']} Mbit/s "
                      f"({tx['got']} bytes in {tx['s']} s)")
            except OSError as err:
                record["tx"] = {"mbit": None}
                stopped("tx", err)
        bursts = []
        if not gone:
            time.sleep(1)
            for _ in range(BURSTS):
                try:
                    one, _ = send_to_board(host, port, size=BURST,
                                           timeout=args.timeout)
                except OSError as err:
                    stopped("burst", err)
                    break
                if one["mbit"] is not None:
                    bursts.append(one["mbit"])
                time.sleep(0.2)
        record["burst"] = {"mbit": bursts,
                           "median": round(statistics.median(bursts), 2)
                           if bursts else None}
        if bursts:
            print(f"  burst  {record['burst']['median']} Mbit/s median of "
                  f"{len(bursts)}")
        if not gone:
            after = ask(host, port + 2, b"i")
            if after is not None:
                facts, facts_at = (parse_facts(after.decode("ascii", "replace")),
                                   time.monotonic())
                record["facts"] = facts
                print(f"  heap   {facts.get('heap')} (free/lowest), "
                      f"rssi {facts.get('rssi')}")
        save(args.out, [record])
        if gone:
            print("The board does not answer. Its log in ESPHome says why: "
                  "keep it open during the next run.")
            break
        if round_ + 1 < args.rounds:
            time.sleep(2)
    print(f"Saved to {args.out}. Now flash the OTHER build and run this again;"
          f" go back and forth at least twice, then: py nettest.py --table")
    return 1 if gone else 0


def spread(values, digits=1):
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    middle = statistics.median(values)
    if len(values) == 1 or min(values) == max(values):
        return f"{middle:.{digits}f}"
    return (f"{middle:.{digits}f} [{min(values):.{digits}f}, "
            f"{max(values):.{digits}f}]")


def table(path):
    if not os.path.exists(path):
        print(f"Nothing measured yet in {path}.")
        return 1
    with open(path, encoding="utf-8") as handle:
        records = json.load(handle)
    groups = {}
    for record in records:
        facts = record.get("facts", {})
        # By board first: two boards measured into one file are two
        # experiments, and every record says which address it measured.
        key = (record.get("host", "?"), facts.get("build", "?"),
               facts.get("c6", "?"), facts.get("malloc", "?"),
               record.get("rate") or 0)
        groups.setdefault(key, []).append(record)
    print("| board | build | C6 firmware | malloc | sent at | runs | "
          "TCP to the board, Mbit/s | "
          "its longest wait, ms | TCP from the board, Mbit/s | "
          "512 KB bursts, Mbit/s | ping, ms | runs that stopped | "
          "lowest free internal heap | RSSI |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (host, build, c6, malloc, rate), runs in sorted(groups.items()):
        lowest = [int(r["facts"]["heap"].split("/")[1]) for r in runs
                  if "/" in r["facts"].get("heap", "")]
        rssi = [int(r["facts"]["rssi"]) for r in runs
                if r["facts"].get("rssi", "").lstrip("-").isdigit()]
        halted = [r for r in runs if r.get("stopped")]
        restarts = sum(1 for r in halted for one in r["stopped"]
                       if one.get("state") == "restarted")
        stops = (f"{len(halted)}" + (f" ({restarts} restart"
                                     f"{'s' if restarts > 1 else ''})"
                                     if restarts else "")) if halted else "0"
        print(f"| {host} | {build} | {c6} | {malloc} "
              f"| {f'{rate:g} Mbit/s' if rate else 'full'} "
              f"| {len(runs)} "
              f"| {spread([r.get('rx', {}).get('mbit') for r in runs])} "
              f"| {spread([r.get('rx', {}).get('gap_ms') for r in runs], 0)} "
              f"| {spread([r.get('tx', {}).get('mbit') for r in runs])} "
              f"| {spread([r.get('burst', {}).get('median') for r in runs])} "
              f"| {spread([r['ping']['median_ms'] for r in runs])} "
              f"| {stops} "
              f"| {min(lowest) if lowest else '-'} "
              f"| {spread(rssi, 0)} |")
    print()
    print("Median of the runs, [lowest, highest] beside it. "
          f"Espressif's own figures for this pair: about {REFERENCE['rx']:.0f}"
          f" Mbit/s to the board and {REFERENCE['tx']:.0f} from it "
          "(esp-hosted-mcu, docs/design/performance.md, section 1.1).")
    print()
    print("What each build was compiled with, as the board reported it:")
    seen = set()
    for (host, build, c6, malloc, rate), runs in sorted(groups.items()):
        if (host, build, c6, malloc) in seen:
            continue
        seen.add((host, build, c6, malloc))
        facts = runs[-1]["facts"]
        keep = " ".join(f"{k}={facts[k]}" for k in
                        ("rx", "tx", "cache", "ba", "wnd", "snd", "sack",
                         "malloc", "reserve", "ps") if k in facts)
        print(f"- {host}, {build}, C6 {c6}: {keep}")
    stopped = [(r.get("host", "?"), r["facts"].get("build"), one)
               for r in records for one in r.get("stopped", [])]
    if stopped:
        print()
        print("Tests that stopped:")
        for host, build, one in stopped:
            what = {"restarted": f"the board restarted ({one.get('reset')})",
                    "up": "the board stayed up",
                    "gone": "the board stopped answering",
                    "answers": "the board answered again"}.get(one.get("state"), "")
            print(f"- {host}, {build}, {one['test']}: {one['error']}; {what}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host", nargs="?", help="the board's IP address")
    parser.add_argument("--port", type=int, default=5201,
                        help="the nettest port; it also uses the next two")
    parser.add_argument("--seconds", type=int, default=10,
                        help="length of each rx and tx test (default 10)")
    parser.add_argument("--rounds", type=int, default=3,
                        help="measurements per run (default 3)")
    parser.add_argument("--rate", type=float, default=0,
                        help="send to the board at this many Mbit/s instead "
                             "of as fast as it takes (Portall's default "
                             "max_rate of 3000 KiB/s is about 24.6)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="seconds without progress before a test is "
                             "called stopped (default 15)")
    parser.add_argument("--wait", type=int, default=90,
                        help="seconds to wait for the board to answer after "
                             "a test stopped (default 90)")
    parser.add_argument("--note", default="",
                        help="kept with the results, for instance the room")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--table", action="store_true",
                        help="print the results so far as a Markdown table")
    args = parser.parse_args(argv)
    if args.table:
        return table(args.out)
    if not args.host:
        parser.error("give the board's IP address, or --table")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
