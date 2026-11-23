#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""The add-on writes, every hour, what its browsers and the machine hold.

WHY THIS EXISTS. Stutters that had come "depuis un bout de temps" went away
with a restart of the Home Assistant VM, and only with that. Something
accumulates, and nothing wrote down the hours before a stutter. So the add-on
says, ten minutes after its start and every hour after:

  - the machine's memory still available, and how much is in swap (inside a
    container /proc/meminfo is the VM's, which is the half wanted);
  - how much CPU the host kept from the VM (steal), the other way a stutter
    comes and goes on a VM pinned to cores the host also uses;
  - this add-on's whole memory, from its own cgroup, with the file cache
    Linux takes back said apart;
  - each screen's browser: the sender, its driver and Chrome's processes,
    added as PSS so shared pages are counted once, with how long it has run
    and how it moved since the line before and since the first.

Checked on a made-up /proc whose numbers are known -- including a command
name with parentheses in it, which is how Chrome names its helpers -- then on
a real process tree that holds a known amount of memory, then through the
thread run.py starts, against a sender put where serve() puts one.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
fails = 0
MB = 1_000_000


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)


def stat_line(pid, name, parent, start):
    # pid (comm) state ppid pgrp session tty tpgid flags minflt cminflt
    # majflt cmajflt utime stime cutime cstime priority nice threads
    # itrealvalue starttime ...
    rest = [parent, pid, pid, 0, -1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 20, 0, 1, 0,
            start, 1000, 100]
    return f"{pid} ({name}) S " + " ".join(str(x) for x in rest) + "\n"


def fake_proc(root, available_kb, swap_free_kb, steal, total_ticks, pss):
    write(root, "meminfo",
          f"MemTotal:        8000000 kB\nMemFree:          100000 kB\n"
          f"MemAvailable:   {available_kb} kB\nSwapTotal:       1000000 kB\n"
          f"SwapFree:        {swap_free_kb} kB\n")
    user = total_ticks - steal
    write(root, "stat",
          f"cpu  {user} 0 0 0 0 0 0 {steal} 0 0\ncpu0 1 2 3\n")
    write(root, "uptime", "36000.00 100.00\n")
    # 10 the sender, 11 the driver, 12 Chrome, 13 a renderer, 14 a helper
    # named the way Chrome names one; 20 is somebody else's.
    tree = {10: ("python3", 1), 11: ("node", 10), 12: ("chrome", 11),
            13: ("chrome (renderer) x", 12), 14: ("Chrome_ChildIOT)", 12),
            20: ("pulseaudio", 1)}
    for pid, (name, parent) in tree.items():
        # Started an hour and a half after the machine booted, at 100 ticks
        # a second: so running for 8 h 30 min at an uptime of 10 h.
        write(root, f"{pid}/stat", stat_line(pid, name, parent, 540000))
        if pid in pss:
            write(root, f"{pid}/smaps_rollup",
                  f"00400000-7fff [rollup]\nRss:  {pss[pid] * 3} kB\n"
                  f"Pss:  {pss[pid]} kB\n")
        write(root, f"{pid}/status", f"Name:\t{name}\nVmRSS:\t{9999} kB\n")


def main():
    import run

    print("on a made-up /proc:")
    root = tempfile.mkdtemp()
    proc = os.path.join(root, "proc")
    cgroup = os.path.join(root, "cgroup")
    write(cgroup, "memory.current", "1500000000\n")
    write(cgroup, "memory.stat", "anon 1000000000\nfile 400000000\n")
    pss = {10: 40_000, 11: 60_000, 12: 200_000, 13: 300_000, 14: 100_000,
           20: 50_000}
    fake_proc(proc, 3_000_000, 600_000, 0, 10000, pss)

    info = run.read_meminfo(proc)
    check("meminfo is read in bytes",
          info["MemAvailable"] == 3_000_000 * 1024
          and info["SwapTotal"] == 1_000_000 * 1024, info)
    stats = run.process_stats(proc)
    check("a command with parentheses in it does not shift the fields",
          stats.get(13) == (12, 540000) and stats.get(14) == (12, 540000),
          stats)
    tree = run.process_tree(10, stats)
    check("the tree is the sender and everything under it, and no more",
          tree == {10, 11, 12, 13, 14}, tree)
    check("a pid that is not there is no tree",
          run.process_tree(99, stats) == set())

    now = [1_700_000_000.0]
    watch = run.MemoryWatch(proc, cgroup, clock=lambda: now[0])
    now[0] += 600
    fake_proc(proc, 3_000_000, 600_000, 30, 11000, pss)
    first = watch.report({"salon": 10})
    print("    " + "\n    ".join(first))
    text = "\n".join(first)
    check("the machine: available, of total, in swap",
          first[0].startswith("Memory: the machine has 3.07 GB available "
                              "of 8.19 GB, 410 MB in swap"), first[0])
    check("the host's share of the CPU over the window, 30 of 1000 ticks",
          "the host kept 3% of its CPU in the last 10 min" in first[0],
          first[0])
    check("the add-on's own memory, its file cache said apart",
          "  this add-on: 1.50 GB, of which 400 MB is file cache" in text,
          text)
    check("a browser is the PSS of its whole tree, five processes",
          "  salon's browser: 717 MB in 5 processes" in text, text)
    check("and how long it has run, from the process's own start",
          "running for 8 h 30 min" in text, text)
    check("the first reading has nothing to compare with",
          "+" not in text.split("salon's browser")[1], text)

    pss2 = {**pss, 13: 450_000}
    now[0] += 3600
    fake_proc(proc, 2_500_000, 500_000, 30, 12000, pss2)
    second = watch.report({"salon": 10})
    print("    " + "\n    ".join(second))
    line = second[-1]
    when = time.strftime("%d/%m %H:%M", time.localtime(1_700_000_600.0))
    check("the next line says how it moved, over the real window",
          "+154 MB in the last 1 h 0 min" in line, line)
    check("and since the first reading, named by when it was",
          f"+154 MB since {when}" in line, line)
    check("no CPU taken in a window where steal did not move",
          "the host kept 0% of its CPU" in second[0], second[0])

    now[0] += 3600
    third = watch.report({"salon": 12})
    check("a sender started again is said, and measured afresh",
          "started again since the last line" in third[-1]
          and "+" not in third[-1].split("processes")[1], third[-1])

    os.remove(os.path.join(proc, "13", "smaps_rollup"))
    fourth = watch.report({"salon": 12})
    check("a process the kernel gives no PSS for is counted by its RSS, "
          "and the line says so",
          "(RSS, which counts shared memory more than once)" in fourth[-1],
          fourth[-1])
    check("a panel with no sender running is left out",
          len(watch.report({"cuisine": 99})) == 2)

    old = os.path.join(root, "v1")
    write(old, "memory/memory.usage_in_bytes", "900000000\n")
    write(old, "memory/memory.stat", "cache 100000000\nrss 800000000\n")
    check("cgroup v1 is read too", run.read_cgroup(old) == (900_000_000,
                                                            100_000_000))
    check("no cgroup, no line about the add-on",
          run.read_cgroup(os.path.join(root, "none")) is None)

    print("the machine running short:")
    low = run.MemoryWatch(proc, cgroup)
    fake_proc(proc, 3_000_000, 600_000, 30, 12000, pss)
    check("plenty: nothing said", low.went_low() is None)
    fake_proc(proc, 700_000, 600_000, 30, 12000, pss)
    check("under a tenth: said", low.went_low() == "low")
    check("and not said again while it stays short", low.went_low() is None)
    fake_proc(proc, 1_000_000, 600_000, 30, 12000, pss)
    check("back above a tenth but under the margin: still quiet",
          low.went_low() is None)
    fake_proc(proc, 1_300_000, 600_000, 30, 12000, pss)
    check("past the margin: said that it has room again",
          low.went_low() == "back")
    gone = run.MemoryWatch(os.path.join(root, "nowhere"), cgroup)
    check("no /proc at all: nothing, rather than an error every minute",
          gone.went_low() is None)

    print("on a real process tree:")
    holder = ("import subprocess, sys, time\n"
              "block = bytes(range(256)) * (40_000_000 // 256)\n"
              "if sys.argv[1] == 'parent':\n"
              "    kids = [subprocess.Popen([sys.executable, __file__, 'kid'])"
              " for _ in range(2)]\n"
              "print('ready', flush=True)\n"
              "time.sleep(60)\n")
    script = os.path.join(root, "holder.py")
    with open(script, "w") as handle:
        handle.write(holder)
    parent = subprocess.Popen([sys.executable, script, "parent"],
                              stdout=subprocess.PIPE, text=True)
    try:
        parent.stdout.readline()
        deadline = time.time() + 20
        while time.time() < deadline:
            tree = run.process_tree(parent.pid, run.process_stats())
            if len(tree) >= 3:
                break
            time.sleep(0.2)
        time.sleep(1.5)  # the children allocate after they start
        tree = run.process_tree(parent.pid, run.process_stats())
        total = sum(run.process_memory(p)[0] for p in tree)
        check("three processes holding 40 MB each add to at least 120 MB",
              len(tree) == 3 and total >= 120 * MB,
              f"{len(tree)} processes, {total / MB:.0f} MB")

        print("through the thread run.py starts:")
        said = []
        run.say = said.append
        run.MEMORY_FIRST_S = 0.5
        run.MEMORY_CHECK_S = 0.2
        run.MEMORY_EVERY_S = 3600
        with run._running_lock:
            run._current["salon"] = parent
        run.report_memory()
        deadline = time.time() + 10
        while time.time() < deadline and not any(
                "salon's browser" in line for line in said):
            time.sleep(0.2)
        print("    " + "\n    ".join(said))
        check("a sender serve() runs is measured by its name",
              any(line.startswith("  salon's browser: ")
                  and "in 3 processes" in line for line in said), said)
        check("the first line comes after MEMORY_FIRST_S and the next not "
              "within the hour",
              sum(line.startswith("Memory: the machine") for line in said)
              == 1, said)
    finally:
        parent.kill()
        for p in run.process_tree(parent.pid, run.process_stats()):
            try:
                os.kill(p, 9)
            except OSError:
                pass

    import inspect
    check("main() starts it", "report_memory()" in inspect.getsource(run.main))

    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
