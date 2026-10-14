#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A key on a dark panel wakes it and goes no further.

portall's press_key() and press_home() are what a remote, a gamepad, a
keyboard and remote:'s buttons in Home Assistant reach a panel through. They
give a key the touch's two rules: on_key: runs first, so a YAML can light the
screen and restart its timeout the way on_touch does, and on a dark panel the
key that wakes it is not also passed to a page nobody could see.

The order is the part worth a check. on_key: is where a YAML calls
light.turn_on, whose on_turn_on calls portall.wake -- so by the time on_key
has run the panel reads as awake, and a test of "is it asleep" taken AFTER the
trigger would send the waking key straight to the page.

network.cpp needs sockets, FreeRTOS and a heap no workstation has, so the
three functions are LIFTED out of the shipped file and compiled against a
small Portall that records what reached the page. A change to their shape
fails the lift loudly rather than checking something else.

    python3 tools/checkkeywake.py
"""

import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NETWORK = ROOT / "components" / "portall" / "network.cpp"

HARNESS = r"""
#include <cstdint>
#include <cstdio>
#include <functional>
#include <vector>

#define ESP_LOGD(tag, ...) ((void) 0)
static const char *const TAG = "portall";

template<typename... Ts> struct Trigger {
  std::function<void(Ts...)> observer;
  void trigger(Ts... x) {
    if (observer)
      observer(x...);
  }
};

namespace esphome {
namespace portall {

struct Portall {
  bool asleep_{false};
  std::vector<Trigger<> *> key_triggers_;
  std::vector<uint32_t> sent;  // page << 16 | usage, and 0xFFFFFFFF for home
  void send_key(uint16_t page, uint16_t usage) { sent.push_back((uint32_t) page << 16 | usage); }
  void ask_home() { sent.push_back(0xFFFFFFFFu); }
  bool note_press_();
  void press_key(uint16_t usage_page, uint16_t usage);
  void press_home();
};

@@LIFTED@@

}  // namespace portall
}  // namespace esphome

using esphome::portall::Portall;
static int failures = 0;
static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

int main() {
  {
    Portall p;
    Trigger<> on_key;
    int fired = 0;
    on_key.observer = [&] { fired++; };
    p.key_triggers_.push_back(&on_key);
    p.press_key(0x07, 0x51);
    ok("a key on a lit panel runs on_key", fired == 1);
    ok("and reaches the page", p.sent.size() == 1 && p.sent[0] == (0x07u << 16 | 0x51));
  }
  {
    Portall p;
    p.asleep_ = true;
    Trigger<> on_key;
    int fired = 0;
    on_key.observer = [&] { fired++; };
    p.key_triggers_.push_back(&on_key);
    p.press_key(0x07, 0x51);
    ok("a key on a dark panel runs on_key, which is what lights it", fired == 1);
    ok("and does not reach the page", p.sent.empty());
  }
  {
    // What a YAML really does: on_key turns the backlight on, whose
    // on_turn_on calls portall.wake -- synchronously, inside the trigger.
    Portall p;
    p.asleep_ = true;
    Trigger<> on_key;
    on_key.observer = [&] { p.asleep_ = false; };
    p.key_triggers_.push_back(&on_key);
    p.press_key(0x07, 0x52);
    ok("a key that wakes the panel through on_key is still not sent on", p.sent.empty());
    p.press_key(0x07, 0x52);
    ok("and the next one is", p.sent.size() == 1);
  }
  {
    Portall p;
    p.asleep_ = true;
    p.press_home();
    ok("the way home on a dark panel goes nowhere", p.sent.empty());
    p.asleep_ = false;
    p.press_home();
    ok("and on a lit one goes home", p.sent.size() == 1 && p.sent[0] == 0xFFFFFFFFu);
  }
  {
    Portall p;
    Trigger<> first, second;
    int a = 0, b = 0;
    first.observer = [&] { a++; };
    second.observer = [&] { b++; };
    p.key_triggers_.push_back(&first);
    p.key_triggers_.push_back(&second);
    p.press_key(0x07, 0x28);
    ok("every on_key automation runs, not only the last one", a == 1 && b == 1);
  }
  return failures == 0 ? 0 : 1;
}
"""


def lift(text, signature):
    """The whole definition that starts with `signature`, braces balanced."""
    at = text.find(signature)
    if at < 0 or text.count(signature) != 1:
        raise SystemExit(f"  ECHEC  {signature!r} is not in network.cpp exactly once; update this tool")
    depth = 0
    i = text.index("{", at)
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[at : i + 1]
        i += 1


def main():
    text = NETWORK.read_text()
    lifted = "\n\n".join(
        lift(text, sig)
        for sig in (
            "bool Portall::note_press_()",
            "void Portall::press_key(uint16_t usage_page, uint16_t usage)",
            "void Portall::press_home()",
        )
    )
    # The lifted code calls nothing the stand-in does not have; a new call
    # into the real class is a reason to look at this tool again.
    for call in re.findall(r"this->(\w+)", lifted):
        if call not in ("asleep_", "key_triggers_", "note_press_", "send_key", "ask_home"):
            raise SystemExit(f"  ECHEC  the press path now calls {call}; teach this tool about it")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        source = tmp / "keywake.cpp"
        source.write_text(HARNESS.replace("@@LIFTED@@", lifted))
        binary = tmp / "keywake"
        build = subprocess.run(
            ["g++", "-std=gnu++17", "-Wall", "-Wextra", "-o", str(binary), str(source)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            print("  ECHEC  the lifted press path did not build")
            print(build.stderr[-3000:])
            return 1
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        print(run.stdout.rstrip())
        return run.returncode


if __name__ == "__main__":
    sys.exit(main())
