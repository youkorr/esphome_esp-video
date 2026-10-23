#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Portall's game streamer: a game's screen, shown on a Portall panel.

It runs on the machine where the game runs -- in steam-headless on an Unraid
server, where Steam has a screen of its own that nobody looks at -- and waits
for the Portall add-on. A launcher tile whose address is game://<this machine>
connects to it when it is tapped, and from then on:

- it sends the add-on a whole picture of the screen each time one is asked
  for and the screen has changed (never otherwise: a still menu costs the
  network nothing), at the size the panel wants;
- it plays the panel's touches as a mouse, and the remote's or gamepad's
  buttons as keys, into the same screen;
- it sends what the screen is playing, at the rate the panel's speaker wants.

The add-on does everything a panel needs after that -- the turn, the
differences, the notifications drawn on top, the pace -- so a game is one more
source of pictures to it and nothing downstream knows it is one.

    python3 portall_game.py                 # port 5700, the screen in $DISPLAY

Needs Pillow and mss beside the Python the machine has, and udisp_send.py in
the same folder: that file holds the one definition of what crosses the wire,
for the add-on and for this alike. The keys and the mouse go through X's own
XTEST extension (libXtst), which every X server has; the sound through
PulseAudio's parec, or GStreamer's pulsesrc when parec is not installed.

Anyone who can reach the port can press keys on that screen, so --allow
names the machines that may connect -- the Home Assistant machine, normally.
"""

import argparse
import collections
import ctypes
import ctypes.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

# Beside it when installed; two folders over in a clone of the repository.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_HERE, os.path.join(_HERE, "..", "components", "portall")]

from udisp_send import (  # noqa: E402 - the one definition of the wire
    GAME_FRAME, GAME_INFO, GAME_PORT, GAME_SOUND, GAME_STILL, pack_game)

VERSION = "1"

# Below this many seconds without a change or an input, the screen is looked
# at a few times a second rather than at the frame rate. A menu that nobody
# touches is most of the time, and grabbing a whole screen sixty times a
# second to find it unchanged is a core's worth of nothing.
QUIET_AFTER_S = 1.0
QUIET_POLL_S = 0.1

# How long a key stays down. Sent down and up in the same instant, a key is
# seen by a program that reads its events and missed by a game that looks at
# which keys are held once a frame -- which is how most games read a
# keyboard. Four frames at sixty.
KEY_HOLD_S = 0.06

# One notch of a mouse wheel, in the page pixels the add-on counts a drag in.
WHEEL_STEP = 40.0

# The browser's names for keys (udisp_send.BROWSER_KEYS) as X calls them.
KEYSYMS = {
    "ArrowUp": "Up",
    "ArrowDown": "Down",
    "ArrowLeft": "Left",
    "ArrowRight": "Right",
    "Enter": "Return",
    "Escape": "Escape",
    "Tab": "Tab",
    " ": "space",
    "PageUp": "Prior",
    "PageDown": "Next",
    "Backspace": "BackSpace",
    "Home": "Home",
    "End": "End",
}

# A transport button (udisp_send.MEDIA_ACTIONS) as the media key X has for
# it: what a keyboard's own media row sends, which Steam and most players
# already listen for.
MEDIA_KEYSYMS = {
    "playpause": "XF86AudioPlay",
    "play": "XF86AudioPlay",
    "pause": "XF86AudioPause",
    "stop": "XF86AudioStop",
    "nexttrack": "XF86AudioNext",
    "previoustrack": "XF86AudioPrev",
    "seekforward": "XF86AudioForward",
    "seekbackward": "XF86AudioRewind",
}


def say(text):
    print(time.strftime("%H:%M:%S ") + text, flush=True)


class X11:
    """The screen's keyboard and mouse, through XTEST.

    Behind a lock, because Xlib is not safe from two threads and two can
    reach it for a moment: an add-on replaced by another lets go of its keys
    while the new one starts pressing.
    """

    def __init__(self, display=None):
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        tst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XFlush.argtypes = [ctypes.c_void_p]
        tst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_ulong]
        tst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_int,
                                             ctypes.c_ulong]
        tst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                             ctypes.c_int, ctypes.c_ulong]
        self._x11, self._tst = x11, tst
        self._dpy = x11.XOpenDisplay(display.encode() if display else None)
        if not self._dpy:
            raise OSError(f"cannot open the X display {display or '$DISPLAY'}")
        screen = x11.XDefaultScreen(self._dpy)
        self.size = (x11.XDisplayWidth(self._dpy, screen),
                     x11.XDisplayHeight(self._dpy, screen))
        self._codes = {}
        self._lock = threading.Lock()

    def keycode(self, name):
        with self._lock:
            if name not in self._codes:
                sym = self._x11.XStringToKeysym(name.encode())
                self._codes[name] = (
                    self._x11.XKeysymToKeycode(self._dpy, sym) if sym else 0)
            return self._codes[name]

    def key(self, code, down):
        with self._lock:
            self._tst.XTestFakeKeyEvent(self._dpy, code, 1 if down else 0, 0)
            self._x11.XFlush(self._dpy)

    def move(self, x, y):
        with self._lock:
            self._tst.XTestFakeMotionEvent(self._dpy, -1, int(x), int(y), 0)
            self._x11.XFlush(self._dpy)

    def button(self, number, down):
        with self._lock:
            self._tst.XTestFakeButtonEvent(self._dpy, number,
                                           1 if down else 0, 0)
            self._x11.XFlush(self._dpy)


def open_grabber():
    """A function that grabs the whole screen: (BGRA bytes, (w, h))."""
    import mss

    shooter = (getattr(mss, "MSS", None) or mss.mss)()
    monitors = shooter.monitors
    area = monitors[1] if len(monitors) > 1 else monitors[0]
    # One grabber for every add-on, and an add-on replaced by another may be
    # half way through a grab when the new one starts.
    lock = threading.Lock()

    def grab():
        with lock:
            shot = shooter.grab(area)
            return shot.raw, shot.size

    return grab


def sound_command(rate, channels, how):
    """The command that captures what the screen plays, or None."""
    if how in ("auto", "parec") and shutil.which("parec"):
        return ["parec", "--format=s16le", f"--rate={rate}",
                f"--channels={channels}", "--latency-msec=20",
                "--device=@DEFAULT_MONITOR@"]
    if how in ("auto", "gst") and shutil.which("gst-launch-1.0"):
        return ["gst-launch-1.0", "-q", "pulsesrc", "device=@DEFAULT_MONITOR@",
                "!", "audioconvert", "!", "audioresample", "!",
                f"audio/x-raw,format=S16LE,rate={rate},channels={channels}",
                "!", "fdsink", "fd=1"]
    return None


class Client:
    """One add-on, for as long as it stays connected."""

    def __init__(self, conn, peer, args, x11, grab):
        self.conn = conn
        self.peer = peer
        self.args = args
        self.x11 = x11
        self.grab = grab
        self.closed = False
        self._send_lock = threading.Lock()
        self._wake = threading.Condition()
        self.size = None
        self.quality = 90
        self.fps = args.fps
        self.wanted = False
        self.whole = False
        self.still = False
        self.paused = False
        self.input_at = time.monotonic()
        self.audio = None
        self._sound = None
        self._ups = []
        self._wheel = [0.0, 0.0]
        self.sent = 0

    # -- the socket -------------------------------------------------------

    def send(self, kind, payload):
        with self._send_lock:
            self.conn.sendall(pack_game(kind, payload))

    def close(self, why):
        if self.closed:
            return
        self.closed = True
        say(f"{self.peer}: {why}")
        with self._wake:
            self._wake.notify_all()
        self._stop_sound()
        for code in self._ups:
            try:
                self.x11.key(code[1], False)
            except Exception:  # noqa: BLE001 - leaving anyway
                pass
        try:
            self.conn.close()
        except OSError:
            pass

    def run(self):
        threading.Thread(target=self._pictures, daemon=True,
                         name="portall-game-pictures").start()
        buffer = b""
        self.conn.settimeout(0.01)
        try:
            while not self.closed:
                self._release_keys()
                try:
                    data = self.conn.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        try:
                            self._act(json.loads(line))
                        except (ValueError, TypeError, KeyError) as err:
                            say(f"{self.peer}: a line that is not one ({err})")
        except OSError as err:
            self.close(f"gone ({err})")
            return
        self.close("left")

    # -- what the add-on says ---------------------------------------------

    def _act(self, said):
        if "bye" in said:
            self.close("left")
            return
        if "hello" in said:
            self.size = (max(16, int(said["width"])), max(16, int(said["height"])))
            self.quality = max(10, min(95, int(said.get("quality", 90))))
            self.fps = max(1.0, min(120.0, float(said.get("fps") or self.args.fps)))
            screen = self.x11.size
            say(f"{self.peer}: wants {self.size[0]}x{self.size[1]} of this "
                f"{screen[0]}x{screen[1]} screen, up to {self.fps:g} a second")
            self.send(GAME_INFO, json.dumps({
                "version": VERSION, "screen": list(screen),
                "sound": self.args.audio}).encode())
            self._sound_for(said.get("rate"), said.get("channels"))
            return
        if "want" in said:
            with self._wake:
                self.wanted = True
                self.whole = self.whole or bool(said.get("whole"))
                self._wake.notify_all()
            return
        if "still" in said:
            with self._wake:
                self.still = True
                self._wake.notify_all()
            return
        if "pause" in said:
            with self._wake:
                self.paused = True
            self._stop_sound()
            return
        if "resume" in said:
            with self._wake:
                self.paused = False
                self.wanted = self.whole = True
                self._wake.notify_all()
            if self.audio:
                self._sound_for(*self.audio)
            return
        if "audio" in said:
            sound = said["audio"] or {}
            self._sound_for(sound.get("rate"), sound.get("channels"))
            return
        self.input_at = time.monotonic()
        if "move" in said:
            x, y = said["move"]
            self.x11.move(*self._to_screen(x, y))
        elif "down" in said:
            self.x11.button(1, True)
        elif "up" in said:
            self.x11.button(1, False)
        elif "wheel" in said:
            self._roll(*said["wheel"])
        elif "key" in said:
            self._press(KEYSYMS.get(said["key"], said["key"]))
        elif "media" in said:
            name = MEDIA_KEYSYMS.get(said["media"])
            if name:
                self._press(name)
        elif "type" in said:
            self._type(str(said["type"]))
        with self._wake:
            # Somebody is playing: back to looking at the frame rate.
            self._wake.notify_all()

    def _to_screen(self, x, y):
        width, height = self.size or self.x11.size
        screen_w, screen_h = self.x11.size
        return (max(0, min(screen_w - 1, round(float(x) * screen_w / width))),
                max(0, min(screen_h - 1, round(float(y) * screen_h / height))))

    def _press(self, name):
        code = self.x11.keycode(name)
        if not code:
            say(f"{self.peer}: this screen has no key {name!r}")
            return
        self.x11.key(code, True)
        self._ups.append((time.monotonic() + KEY_HOLD_S, code))

    def _release_keys(self):
        now = time.monotonic()
        while self._ups and self._ups[0][0] <= now:
            self.x11.key(self._ups.pop(0)[1], False)

    def _roll(self, dx, dy):
        # Pixels of a drag, in notches of a wheel: the remainder is kept, so
        # a slow drag still scrolls.
        self._wheel[0] += float(dx)
        self._wheel[1] += float(dy)
        for axis, less, more in ((1, 4, 5), (0, 6, 7)):
            while abs(self._wheel[axis]) >= WHEEL_STEP:
                button = more if self._wheel[axis] > 0 else less
                self.x11.button(button, True)
                self.x11.button(button, False)
                self._wheel[axis] -= WHEEL_STEP if self._wheel[axis] > 0 \
                    else -WHEEL_STEP

    def _type(self, text):
        # Words said to the voice assistant. xdotool knows every keyboard
        # layout's way to every character, which XTEST alone does not.
        if not shutil.which("xdotool"):
            say(f"{self.peer}: words to type and no xdotool to type them")
            return
        subprocess.run(["xdotool", "type", "--delay", "12", "--", text],
                       check=False)

    # -- pictures ---------------------------------------------------------

    def _pictures(self):
        last = None
        sent_at = 0.0
        changed_at = time.monotonic()
        while not self.closed:
            with self._wake:
                while not self.closed and not self.still and (
                        self.size is None or self.paused or not self.wanted):
                    self._wake.wait(0.5)
                if self.closed:
                    return
                still, self.still = self.still, False
                whole = self.whole
            try:
                if still:
                    raw, size = self.grab()
                    self.send(GAME_STILL, self._encode(raw, "PNG", size))
                    continue
                wait = sent_at + 1.0 / self.fps - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                raw, size = self.grab()
                if whole or raw != last:
                    payload = self._encode(raw, "JPEG", size)
                    with self._wake:
                        self.wanted = self.whole = False
                    self.send(GAME_FRAME, payload)
                    last = raw
                    sent_at = changed_at = time.monotonic()
                    self.sent += 1
                    continue
                quiet = time.monotonic() - max(changed_at, self.input_at)
                with self._wake:
                    self._wake.wait(QUIET_POLL_S if quiet > QUIET_AFTER_S
                                    else 1.0 / self.fps)
            except OSError as err:
                self.close(f"gone ({err})")
                return

    def _encode(self, raw, form, size):
        from PIL import Image

        picture = Image.frombytes("RGB", size, bytes(raw), "raw", "BGRX")
        if picture.size != self.size:
            picture = picture.resize(self.size, Image.BILINEAR)
        buffer = io.BytesIO()
        if form == "PNG":
            picture.save(buffer, format="PNG", compress_level=1)
        else:
            picture.save(buffer, format="JPEG", quality=self.quality)
        return buffer.getvalue()

    # -- sound ------------------------------------------------------------

    def _sound_for(self, rate, channels):
        try:
            rate, channels = int(rate or 0), int(channels or 1)
        except (TypeError, ValueError):
            return
        if rate <= 0 or channels not in (1, 2) or self.args.audio == "off":
            return
        self.audio = (rate, channels)
        self._stop_sound()
        if self.paused or self.closed:
            return
        command = sound_command(rate, channels, self.args.audio)
        if command is None:
            say(f"{self.peer}: no parec and no gst-launch-1.0 here, so the "
                f"game is sent without its sound")
            return
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
        except OSError as err:
            say(f"{self.peer}: the sound would not start ({err})")
            return
        self._sound = process
        say(f"{self.peer}: sound at {rate} Hz, {channels} channel(s), "
            f"through {os.path.basename(command[0])}")
        threading.Thread(target=self._read_sound,
                         args=(process, rate * channels * 2 // 50),
                         daemon=True, name="portall-game-sound").start()

    def _read_sound(self, process, block):
        while not self.closed and process is self._sound:
            data = process.stdout.read(block)
            if not data:
                return
            # Digital silence is not worth a byte, the add-on's own rule.
            if not data.strip(b"\x00"):
                continue
            try:
                self.send(GAME_SOUND, data)
            except OSError:
                return

    def _stop_sound(self):
        process, self._sound = self._sound, None
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Show this machine's screen -- a game -- on a Portall "
        "panel, through the Portall add-on.")
    parser.add_argument("--port", type=int, default=GAME_PORT)
    parser.add_argument("--display", default=os.environ.get("DISPLAY"),
                        help="the X screen the game is on (default $DISPLAY)")
    parser.add_argument("--fps", type=float, default=60.0,
                        help="the most pictures a second the add-on is sent")
    parser.add_argument("--allow", default="",
                        help="the addresses that may connect, separated by "
                        "commas -- the Home Assistant machine. Empty lets any "
                        "machine on the network press keys on this screen")
    parser.add_argument("--audio", default="auto",
                        choices=("auto", "parec", "gst", "off"))
    parser.add_argument("--no-wait", dest="wait", action="store_false",
                        help="stop at once when the X screen is not there, "
                        "instead of waiting for it to start")
    args = parser.parse_args()

    if not args.display:
        raise SystemExit("No X screen: set DISPLAY or give --display. In "
                         "steam-headless it is :55.")
    os.environ["DISPLAY"] = args.display
    # Started with the container, this may well be up before the screen it
    # is for: wait for it rather than giving up, and say so once.
    x11 = None
    waiting = False
    while x11 is None:
        try:
            x11 = X11(args.display)
        except OSError as err:
            if not args.wait:
                raise SystemExit(f"{err}. Is the game's screen running?") \
                    from err
            if not waiting:
                say(f"{err} -- waiting for it")
                waiting = True
            time.sleep(5)
    try:
        grab = open_grabber()
        grab()
    except ImportError as err:
        raise SystemExit(f"{err}: pip install mss pillow") from err

    allowed = {a.strip() for a in args.allow.split(",") if a.strip()}
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("", args.port))
    except OSError as err:
        raise SystemExit(f"Port {args.port} is taken ({err}): is the "
                         f"streamer already running?") from err
    server.listen(2)
    say(f"Portall game streamer {VERSION}: the {x11.size[0]}x{x11.size[1]} "
        f"screen {args.display}, waiting on port {args.port} for "
        + (", ".join(sorted(allowed)) if allowed
           else "any machine (give --allow to choose)"))

    current = None
    while True:
        conn, (address, _port) = server.accept()
        if allowed and address not in allowed:
            say(f"{address}: refused, not in --allow")
            conn.close()
            continue
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if current is not None and not current.closed:
            # One panel at a time, and the newest wins: the one asking now is
            # the one somebody is holding.
            current.close(f"replaced by {address}")
        current = Client(conn, address, args, x11, grab)
        say(f"{address}: connected")
        threading.Thread(target=current.run, daemon=True,
                         name="portall-game-client").start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
