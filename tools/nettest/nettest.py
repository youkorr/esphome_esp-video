#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Measure an ESP32-P4 + C6 running the nettest firmware, from a computer.

    python nettest.py 192.168.1.50
    python nettest.py 192.168.1.50 --rounds 3 --seconds 10
    python nettest.py --table

Each run asks the board what it was built with, then measures:

  ping    20 small UDP round trips
  rx      this computer sends to the board for --seconds; the BOARD times
          what arrives (first byte to last), so the figure is what it
          received, not what this computer managed to queue
  tx      the board sends to this computer for --seconds; timed here
  burst   10 transfers of 512 KB to the board, each on its own connection,
          timed by the board -- short transfers that never leave TCP's
          slow start, which is closer to a picture than a long stream is

Every run is appended to nettest-results.json beside this script (--out to
change it), and --table prints a Markdown table of all of them, grouped by
the build each board said it was. Run it from a computer on a CABLE to the
router: a computer on Wi-Fi shares the air with the board, and then the
figure is the two radios, not the board.

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


def send_to_board(host, port, seconds=None, size=None):
    """Send for `seconds` or `size` bytes; return the board's own account."""
    block = bytes(range(256)) * (WRITE // 256)
    with socket.create_connection((host, port), timeout=15) as tcp:
        tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sent = 0
        start = time.perf_counter()
        while True:
            if seconds is not None and time.perf_counter() - start >= seconds:
                break
            if size is not None and sent >= size:
                break
            piece = block if size is None else block[:min(WRITE, size - sent)]
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
    return {"sent": sent, "got": got, "us": us,
            "mbit": round(got * 8 / us, 2) if us > 0 else None}, facts


def receive_from_board(host, port, seconds):
    with socket.create_connection((host, port), timeout=15) as tcp:
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


def run(args):
    host, port = args.host, args.port
    answer = ask(host, port + 2, b"i")
    if answer is None:
        print(f"No answer from {host} on UDP {port + 2}. Is the nettest "
              f"firmware on that board, and is this the board's address?")
        return 1
    facts = parse_facts(answer.decode("ascii", "replace"))
    print(f"Board: {answer.decode('ascii', 'replace')}")
    results = []
    for round_ in range(args.rounds):
        print(f"Round {round_ + 1} of {args.rounds}, build {facts.get('build')}:")
        record = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "host": host,
                  "note": args.note, "facts": facts}
        record["ping"] = ping(host, port + 2)
        print(f"  ping   {record['ping']['median_ms']} ms, "
              f"{record['ping']['lost']} of {record['ping']['of']} lost")
        rx, said = send_to_board(host, port, seconds=args.seconds)
        record["rx"] = rx
        record["facts"] = {**facts, **{k: v for k, v in said.items()
                                       if k not in ("bytes", "us")}}
        print(f"  rx     {rx['mbit']} Mbit/s, the board received "
              f"{rx['got']} of {rx['sent']} bytes")
        time.sleep(1)
        tx = receive_from_board(host, port + 1, args.seconds)
        record["tx"] = tx
        print(f"  tx     {tx['mbit']} Mbit/s ({tx['got']} bytes in {tx['s']} s)")
        time.sleep(1)
        bursts = []
        for _ in range(BURSTS):
            one, _ = send_to_board(host, port, size=BURST)
            if one["mbit"] is not None:
                bursts.append(one["mbit"])
            time.sleep(0.2)
        record["burst"] = {"mbit": bursts,
                           "median": round(statistics.median(bursts), 2)
                           if bursts else None}
        print(f"  burst  {record['burst']['median']} Mbit/s median of "
              f"{len(bursts)}")
        after = ask(host, port + 2, b"i")
        if after is not None:
            record["facts"] = parse_facts(after.decode("ascii", "replace"))
            print(f"  heap   {record['facts'].get('heap')} (free/lowest), "
                  f"rssi {record['facts'].get('rssi')}")
        results.append(record)
        if round_ + 1 < args.rounds:
            time.sleep(2)
    saved = []
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as handle:
            saved = json.load(handle)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(saved + results, handle, indent=1)
    print(f"Saved to {args.out}. Now flash the OTHER build and run this again;"
          f" go back and forth at least twice, then: python nettest.py --table")
    return 0


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
        key = (facts.get("build", "?"), facts.get("c6", "?"))
        groups.setdefault(key, []).append(record)
    print("| build | C6 firmware | runs | TCP to the board, Mbit/s | "
          "TCP from the board, Mbit/s | 512 KB bursts, Mbit/s | ping, ms | "
          "lowest free internal heap | RSSI |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (build, c6), runs in sorted(groups.items()):
        lowest = [int(r["facts"]["heap"].split("/")[1]) for r in runs
                  if "/" in r["facts"].get("heap", "")]
        rssi = [int(r["facts"]["rssi"]) for r in runs
                if r["facts"].get("rssi", "").lstrip("-").isdigit()]
        print(f"| {build} | {c6} | {len(runs)} "
              f"| {spread([r['rx']['mbit'] for r in runs])} "
              f"| {spread([r['tx']['mbit'] for r in runs])} "
              f"| {spread([r['burst']['median'] for r in runs])} "
              f"| {spread([r['ping']['median_ms'] for r in runs])} "
              f"| {min(lowest) if lowest else '-'} "
              f"| {spread(rssi, 0)} |")
    print()
    print("Median of the runs, [lowest, highest] beside it. "
          f"Espressif's own figures for this pair: about {REFERENCE['rx']:.0f}"
          f" Mbit/s to the board and {REFERENCE['tx']:.0f} from it "
          "(esp-hosted-mcu, docs/design/performance.md, section 1.1).")
    print()
    print("What each build was compiled with, as the board reported it:")
    for (build, c6), runs in sorted(groups.items()):
        facts = runs[-1]["facts"]
        keep = " ".join(f"{k}={facts[k]}" for k in
                        ("rx", "tx", "cache", "ba", "wnd", "snd", "sack")
                        if k in facts)
        print(f"- {build}, C6 {c6}: {keep}")
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
