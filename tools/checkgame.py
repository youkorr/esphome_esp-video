#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Does a game tile show the game, and does the panel drive it?

WHY THIS EXISTS. Asked for as a Steam button on the launcher, "comme les links
que je dispose ... et je serais sur steam os": a tile whose address is
game://<the Unraid server>, which shows that machine's screen -- Steam in
steam-headless -- instead of a web page, with the panel's touches, its remote
or gamepad and its speaker all going to the game. game/portall_game.py runs
beside the game; the add-on's sender connects to it when the tile is tapped
(GameCapture in ha_send.py).

Everything here is the shipped code: the launcher served by run.py, the real
sender, the real streamer on an X server of its own (Xvfb), and a fake panel
that reassembles what reaches it and talks back the way a board does. The X
screen stands in for steam-headless's; xev on it says what arrived as input.

  1. the pieces: game:// addresses, the wire's framing, and every key and
     media button the sender can send having a name on the X side -- the
     hand-copied pair this project keeps paying for;
  2. the whole way: the Steam tile tapped on the glass shows the game; a
     still game sends nothing; a change arrives; a tap and a key reach the
     game where they should; its sound reaches the panel at the rate the
     panel asked for; the corner held brings the launcher back; a server
     that does not answer, and one that goes away mid-game, each leave the
     launcher on the glass and say so on it.

    tools/checkgame.py                    # the shipped sender
    tools/checkgame.py --sender OLD.py    # an older one, to see it fail

Needs Xvfb, xsetroot and xev (x11-utils), PulseAudio for the sound part, and
Playwright with a Chromium. Takes --browser or $CHROMIUM.
"""

import argparse
import io
import math
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import wave

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE / "portall"), str(HERE / "components" / "portall"),
                str(HERE / "game")]

import launcher  # noqa: E402
import run  # noqa: E402
import udisp_send  # noqa: E402
from udisp_send import (  # noqa: E402
    _HEADER, UDISP_TYPE_JPG, UDISP_TYPE_PCM, game_address, pack_game,
    unpack_game)

parser = argparse.ArgumentParser()
parser.add_argument("--sender", default=str(HERE / "components" / "portall"
                                            / "ha_send.py"))
parser.add_argument("--browser", default=os.environ.get("CHROMIUM", ""))
ARGS = parser.parse_args()

faults = []


def check(what, ok, detail=""):
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""), flush=True)
    if not ok:
        faults.append(what)


def settle(test, seconds, step=0.05):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if test():
            return True
        time.sleep(step)
    return test()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- 1. the pieces -----------------------------------------------------------

def pieces():
    print("The pieces:")
    for url, want in (
            ("game://tower", ("tower", udisp_send.GAME_PORT)),
            ("game://192.168.1.3:5801", ("192.168.1.3", 5801)),
            ("GAME://Tower/", ("Tower", udisp_send.GAME_PORT)),
            ("game://[fe80::1]:5702", ("fe80::1", 5702)),
            ("game://tower:port", None),
            ("game://", None),
            ("http://tower", None),
            ("files", None)):
        check(f"{url!r} is {want}", game_address(url) == want,
              repr(game_address(url)))

    stream = (pack_game(b"F", b"\xff\xd8 a picture") + pack_game(b"P", b"")
              + pack_game(b"P", bytes(range(200))))
    whole = True
    for cut in range(len(stream) + 1):
        first, rest = unpack_game(stream[:cut])
        second, rest = unpack_game(rest + stream[cut:])
        if first + second != [(b"F", b"\xff\xd8 a picture"), (b"P", b""),
                              (b"P", bytes(range(200)))] or rest:
            whole = False
            break
    check("three messages come out whole however the stream is cut", whole,
          f"cut at {cut}")

    import portall_game
    missing = [n for n in udisp_send.BROWSER_KEYS.values()
               if n not in portall_game.KEYSYMS]
    check("every key the sender sends has an X name on the game's side",
          not missing, repr(missing))
    missing = [a for a in udisp_send.MEDIA_ACTIONS.values()
               if a not in portall_game.MEDIA_KEYSYMS]
    check("and every transport button", not missing, repr(missing))
    check("the Steam logo is drawn on a tile, dark and light",
          launcher.logo_markup("steam", True) is not None
          and launcher.logo_markup("steam", False) is not None)


# --- 2. the whole way --------------------------------------------------------

W, H = 1280, 800
RED, BLUE, WHITE = (255, 0, 0), (0, 0, 255), (255, 255, 255)


def near(colour, want, slack=40):
    return all(abs(a - b) <= slack for a, b in zip(colour, want))


class Panel:
    """A fake panel: reassembles pictures, keeps the sound, talks back."""

    def __init__(self):
        from PIL import Image
        self.canvas = Image.new("RGB", (W, H))
        self.lock = threading.Lock()
        self.pictures = 0
        self.sound = []
        self.conn = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from PIL import Image
        conn, _ = self.listener.accept()
        self.conn = conn
        # Like a board: the rate its speaker wants, as soon as it accepts.
        conn.sendall(b"A" + bytes([44, 2]))
        stream, last_id = b"", None
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                stream += data
                while len(stream) >= _HEADER.size:
                    _, kind, _, x, y, w, h, packed = _HEADER.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    blob = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG:
                        piece = Image.open(io.BytesIO(blob)).convert("RGB")
                        with self.lock:
                            self.canvas.paste(piece, (x, y))
                            if packed & 0x3FF != last_id:
                                last_id = packed & 0x3FF
                                self.pictures += 1
                    elif kind == UDISP_TYPE_PCM:
                        with self.lock:
                            self.sound.append((h or udisp_send.AUDIO_RATE,
                                               w or 1, blob))
        except OSError:
            pass

    def at(self, x, y):
        with self.lock:
            return self.canvas.getpixel((x, y))

    def tap(self, x, y, hold=0.08):
        self.conn.sendall(b"T\x01\x00" + struct.pack("<HH", int(x), int(y)))
        time.sleep(hold)
        self.conn.sendall(b"T\x00")

    def key(self, page, usage):
        self.conn.sendall(b"K" + struct.pack("<HH", page, usage))


def tone_wav(path, seconds=3.0, rate=44100):
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * n
                                                   / rate)))
            for n in range(int(seconds * rate))))


def whole_way():
    print("The whole way, with a fake panel and an X screen standing in for "
          "steam-headless:")
    for tool in ("Xvfb", "xsetroot", "xev"):
        if not shutil.which(tool):
            print(f"  --   skipped: no {tool} here")
            return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  --   skipped: no Playwright here")
        return

    scratch = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / \
        f"checkgame-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    display = f":{90 + os.getpid() % 9}"
    xenv = dict(os.environ, DISPLAY=display)
    children = []

    def spawn(command, log, env=None):
        child = subprocess.Popen(command, env=env or os.environ.copy(),
                                 stdin=subprocess.PIPE if log is None
                                 else subprocess.DEVNULL,
                                 stdout=open(log, "w") if log else
                                 subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        children.append(child)
        return child

    def logged(path, text):
        try:
            return text in pathlib.Path(path).read_text()
        except OSError:
            return False

    # -noreset, or the screen is wiped each time its last client leaves --
    # xsetroot is the only one at first.
    spawn(["Xvfb", display, "-screen", "0", f"{W}x{H}x24", "-nolisten",
           "tcp", "-noreset"], scratch / "xvfb.log")
    settle(lambda: subprocess.run(["xsetroot", "-solid", "red"], env=xenv,
                                  capture_output=True).returncode == 0, 10)

    # The game's sound: a sink of its own, made the default, as
    # steam-headless's own is.
    pulse = shutil.which("pactl") and shutil.which("paplay")
    if pulse and subprocess.run(["pactl", "info"],
                                capture_output=True).returncode != 0:
        subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1"],
                       capture_output=True)
        settle(lambda: subprocess.run(["pactl", "info"],
                                      capture_output=True).returncode == 0, 5)
    if pulse:
        subprocess.run(["pactl", "load-module", "module-null-sink",
                        "sink_name=gamesink"], capture_output=True)
        subprocess.run(["pactl", "set-default-sink", "gamesink"],
                       capture_output=True)

    game_port = free_port()
    streamer_log = scratch / "streamer.log"
    streamer = spawn([sys.executable, "-u", str(HERE / "game" /
                                                "portall_game.py"),
                      "--display", display, "--port", str(game_port)],
                     streamer_log, xenv)
    settle(lambda: logged(streamer_log, "waiting on port"), 10)

    config = run.regroup({"links": [
        {"name": "Steam", "url": f"game://127.0.0.1:{game_port}",
         "icon": "steam"},
        {"name": "Ailleurs", "url": "http://127.0.0.2:9/", "icon": "home"},
    ], "launcher": {}})
    home = run.start_launcher(config, port=launcher.ANY_PORT)

    # Where the Steam tile is, asked of the launcher itself at the panel's
    # size, so the ruler cannot drift from the page.
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": ARGS.browser}
                                       if ARGS.browser else {}))
        look = browser.new_page(viewport={"width": W, "height": H})
        look.goto(home)
        box = look.evaluate("""() => {
            const t = [...document.querySelectorAll('a.tile')]
                .find(a => a.href.startsWith('game:'));
            const r = t.getBoundingClientRect();
            return [r.x + r.width / 2, r.y + r.height / 2];
        }""")
        browser.close()
    tile = (round(box[0]), round(box[1]))

    panel = Panel()
    sender_log = scratch / "sender.log"
    command = [sys.executable, "-u", ARGS.sender, "--host", "127.0.0.1",
               "--port", str(panel.port), "--url", home, "--no-token",
               "--not-home-assistant", "--width", str(W), "--height", str(H),
               "--keyboard", "off", "--control"]
    if ARGS.browser:
        command += ["--browser", ARGS.browser]
    sender = subprocess.Popen(command, stdin=subprocess.PIPE,
                              stdout=open(sender_log, "w"),
                              stderr=subprocess.STDOUT, text=True)
    children.append(sender)
    where = (W // 2, H - 40)  # below the tiles, clear of any notification
    try:
        check("the sender starts", settle(lambda: logged(sender_log, "Ready"),
                                          60))
        check("the launcher reaches the panel",
              settle(lambda: panel.pictures > 0 and panel.conn is not None,
                     15))
        time.sleep(1.5)
        launcher_colour = panel.at(*where)
        check("and is not the game", not near(launcher_colour, RED),
              repr(launcher_colour))

        panel.tap(*tile)
        check("the Steam tile shows the game", settle(
            lambda: logged(sender_log, "Game: showing 127.0.0.1"), 5)
            and settle(lambda: near(panel.at(*where), RED), 5),
            repr(panel.at(*where)))
        check("the streamer was asked for the panel's size",
              logged(streamer_log, f"wants {W}x{H}"))
        check("and is told how to come back, on the glass",
              logged(sender_log, "Notice: on the screen"))

        # Still: once the notification is gone and the finished picture
        # sent, nothing goes out at all.
        time.sleep(8.0)
        before = panel.pictures
        time.sleep(2.0)
        check("a still game sends nothing", panel.pictures == before,
              f"{panel.pictures - before} pictures in 2 s")

        sent_at = time.monotonic()
        subprocess.run(["xsetroot", "-solid", "blue"], env=xenv)
        shown = settle(lambda: near(panel.at(*where), BLUE), 3, step=0.005)
        check(f"a change on the game's screen reaches the panel "
              f"({(time.monotonic() - sent_at) * 1000:.0f} ms)", shown,
              repr(panel.at(*where)))

        # Input, read off xev on the same screen: its white window is the
        # picture from here on, which is a change arriving too.
        xev_log = scratch / "xev.log"
        spawn(["xev", "-geometry", f"{W}x{H}+0+0", "-event", "keyboard",
               "-event", "button"], xev_log, xenv)
        check("a window opening on the game's screen arrives too",
              settle(lambda: near(panel.at(*where), WHITE), 5),
              repr(panel.at(*where)))
        panel.tap(400, 500)
        check("a tap lands on the game where the finger was",
              settle(lambda: logged(xev_log, "root:(400,500)")
                     and logged(xev_log, "ButtonRelease"), 3),
              pathlib.Path(xev_log).read_text()[-300:])
        panel.key(0x07, 0x4F)
        check("the remote's right arrow is the game's Right",
              settle(lambda: logged(xev_log, "keysym 0xff53, Right"), 3))
        panel.key(0x0C, 0xCD)
        check("its play/pause is the media key",
              settle(lambda: logged(xev_log, "XF86AudioPlay"), 3))

        if pulse:
            wav = str(scratch / "tone.wav")
            tone_wav(wav)
            with panel.lock:
                panel.sound.clear()
            player = subprocess.Popen(["paplay", "-d", "gamesink", wav])
            time.sleep(2.0)
            player.wait()
            with panel.lock:
                blocks = list(panel.sound)
            rates = {rate for rate, _, _ in blocks}
            check(f"the game's sound reaches the panel ({len(blocks)} "
                  f"blocks in 2 s)", len(blocks) >= 30, repr(len(blocks)))
            check("at the rate the panel asked for", rates == {44100},
                  repr(rates))
            loud = max((max(abs(v) for v in struct.unpack(
                f"<{len(b) // 2}h", b)) for _, _, b in blocks if b),
                default=0)
            check("and it is the tone, not silence", loud > 5000, repr(loud))
        else:
            print("  --   no PulseAudio: the sound is not checked")

        panel.conn.sendall(b"T\x01\x00" + struct.pack("<HH", 15, 15))
        time.sleep(1.2)
        panel.conn.sendall(b"T\x00")
        check("the corner held leaves the game", settle(
            lambda: logged(sender_log, "Game: left 127.0.0.1 (home)"), 5))
        check("and the launcher is back on the glass",
              settle(lambda: near(panel.at(*where), launcher_colour, 12), 5),
              repr(panel.at(*where)))
        check("the streamer hears it leave",
              settle(lambda: logged(streamer_log, "127.0.0.1: left"), 3))

        streamer.terminate()
        streamer.wait()
        mark = sender_log.read_text().count("did not answer")
        panel.tap(*tile)
        check("a game that does not answer is said",
              settle(lambda: sender_log.read_text().count("did not answer")
                     > mark, 6))
        check("on the glass too, and the launcher stays",
              settle(lambda: not near(panel.at(*where), WHITE), 2)
              and logged(sender_log, "Notice: on the screen"))

        streamer = spawn([sys.executable, "-u", str(HERE / "game" /
                                                    "portall_game.py"),
                          "--display", display, "--port", str(game_port)],
                         scratch / "streamer2.log", xenv)
        settle(lambda: logged(scratch / "streamer2.log", "waiting on port"),
               10)
        time.sleep(6.0)  # the notification gone
        panel.tap(*tile)
        check("shown again", settle(lambda: near(panel.at(*where), WHITE), 6),
              repr(panel.at(*where)))
        streamer.terminate()
        check("a game that goes away mid-game leaves the launcher",
              settle(lambda: logged(sender_log, "Game: left 127.0.0.1 (it "
                                    "closed the connection)")
                     or logged(sender_log, "Game: left 127.0.0.1 (the "
                               "connection broke"), 5)
              and settle(lambda: near(panel.at(*where), launcher_colour, 12),
                         5), repr(panel.at(*where)))
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.kill()
        if pulse:
            subprocess.run(["pactl", "unload-module", "module-null-sink"],
                           capture_output=True)
    if faults:
        print("  logs in", scratch)


def through_gstreamer():
    """The sound the way steam-headless will take it: GStreamer, no parec.

    steam-headless installs gstreamer1.0-pulseaudio and not
    pulseaudio-utils (read in its Dockerfile), so there the streamer falls
    back to pulsesrc on the default sink's monitor. Asked of the streamer
    directly, with --audio gst forcing that path.
    """
    print("The game's sound through GStreamer, as in steam-headless:")
    if not (shutil.which("gst-launch-1.0") and shutil.which("Xvfb")
            and shutil.which("paplay")):
        print("  --   skipped: no gst-launch-1.0, Xvfb or paplay here")
        return
    if subprocess.run(["pactl", "info"], capture_output=True).returncode:
        subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1"],
                       capture_output=True)
        settle(lambda: subprocess.run(["pactl", "info"],
                                      capture_output=True).returncode == 0, 5)
    subprocess.run(["pactl", "load-module", "module-null-sink",
                    "sink_name=gstsink"], capture_output=True)
    subprocess.run(["pactl", "set-default-sink", "gstsink"],
                   capture_output=True)
    display = f":{80 + os.getpid() % 9}"
    xenv = dict(os.environ, DISPLAY=display)
    port = free_port()
    scratch = pathlib.Path(os.environ.get("TMPDIR", "/tmp"))
    wav = str(scratch / f"checkgame-tone-{os.getpid()}.wav")
    tone_wav(wav, seconds=3.0, rate=48000)
    children = [subprocess.Popen(["Xvfb", display, "-screen", "0",
                                  "640x400x24", "-nolisten", "tcp",
                                  "-noreset"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)]
    try:
        time.sleep(1.0)
        children.append(subprocess.Popen(
            [sys.executable, "-u", str(HERE / "game" / "portall_game.py"),
             "--display", display, "--port", str(port), "--audio", "gst"],
            env=xenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        link = None
        until = time.monotonic() + 10
        while link is None and time.monotonic() < until:
            try:
                link = socket.create_connection(("127.0.0.1", port), 1)
            except OSError:
                time.sleep(0.2)
        if link is None:
            check("the streamer starts", False)
            return
        link.sendall(b'{"hello": 1, "width": 320, "height": 200, '
                     b'"rate": 22050, "channels": 1}\n')
        time.sleep(1.5)  # GStreamer's pipeline coming up
        player = subprocess.Popen(["paplay", "-d", "gstsink", wav])
        blocks, buffer = [], b""
        link.settimeout(0.2)
        until = time.monotonic() + 3.0
        while time.monotonic() < until:
            try:
                buffer += link.recv(1 << 16)
            except socket.timeout:
                continue
            messages, buffer = unpack_game(buffer)
            blocks += [p for k, p in messages if k == b"P"]
        player.wait()
        link.close()
        sizes = {len(b) for b in blocks}
        loud = max((max(abs(v) for v in struct.unpack(f"<{len(b) // 2}h", b))
                    for b in blocks if b), default=0)
        check(f"blocks of the game's sound arrive ({len(blocks)} in 3 s)",
              len(blocks) >= 40, repr(len(blocks)))
        check("each 20 ms at the 22050 Hz asked for", sizes == {882},
              repr(sizes))
        check("and it is the tone", loud > 5000, repr(loud))
    finally:
        for child in children:
            child.terminate()
        subprocess.run(["pactl", "unload-module", "module-null-sink"],
                       capture_output=True)
        try:
            os.remove(wav)
        except OSError:
            pass


pieces()
whole_way()
through_gstreamer()
print()
print("all good" if not faults else f"{len(faults)} fault(s)")
sys.exit(1 if faults else 0)
