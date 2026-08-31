#!/usr/bin/env python3
"""Send sound to a portall panel, and nothing else.

The half of the audio path that lives on the Home Assistant server does not
exist yet: capturing a browser's output needs a virtual sound device beside it,
which is its own piece of work. This is the other half on its own, so the board
can be tested before that arrives -- connect, send PCM, listen.

It carries no picture. A panel already showing a dashboard keeps showing it:
sound and rectangles are different types on the same wire and the board reads
whichever turns up.

    python3 tools/playsound.py --host 192.168.1.11
    python3 tools/playsound.py --host 192.168.1.11 --wav quelquechose.wav

Standard library only, so it runs from any machine that can reach the panel --
including the Windows one this project is usually driven from.
"""
import argparse
import math
import socket
import struct
import sys
import time
import wave

# The same sixteen bytes as a rectangle. Kept here rather than imported so this
# runs from a bare checkout with nothing installed; the real sender imports the
# one definition in udisp_send.py, and these two must not drift -- if you change
# the wire format, change it there and copy it here.
HEADER = struct.Struct("<HBBHHHHI")
TYPE_PCM = 0x10
RATE = 48000
BITS = 16
CHANNELS = 1

# 20 ms a block: small enough that the panel's speaker never runs dry waiting
# for the next one, large enough that the header is 1.6% of what goes out
# rather than a third of it.
BLOCK_MS = 20
BLOCK_SAMPLES = RATE * BLOCK_MS // 1000
BLOCK_BYTES = BLOCK_SAMPLES * (BITS // 8) * CHANNELS


def audio_header(payload_len):
    """Geometry means nothing for sound, so all of it goes out as zero."""
    return HEADER.pack(0, TYPE_PCM, 0, 0, 0, 0, 0, (payload_len & 0x3FFFFF) << 10)


def tone(seconds, hz=440.0, level=0.25):
    """A sine, because a sine is unmistakable and needs no file.

    Faded in and out over ten milliseconds: a wave that starts at full
    amplitude begins with a step, and a step is a click.
    """
    out = bytearray()
    total = int(RATE * seconds)
    fade = RATE // 100
    for n in range(total):
        gain = min(1.0, n / fade, (total - n) / fade)
        value = int(32767 * level * gain * math.sin(2 * math.pi * hz * n / RATE))
        out += struct.pack("<h", value)
    return bytes(out)


def from_wav(path):
    """Whatever the file is, as 48 kHz 16-bit mono.

    Nearest-sample resampling and a plain average across channels. Both are
    crude and both are right here: this exists to prove the panel makes a
    sound, not to be a player.
    """
    with wave.open(path, "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit(
            f"{path} is {width * 8}-bit; this only reads 16-bit WAV files"
        )
    count = len(frames) // (2 * channels)
    samples = struct.unpack("<%dh" % (count * channels), frames[: count * 2 * channels])
    if channels > 1:
        samples = [
            sum(samples[i * channels : (i + 1) * channels]) // channels
            for i in range(count)
        ]
    if rate != RATE:
        out_count = int(count * RATE / rate)
        samples = [samples[int(i * rate / RATE)] for i in range(out_count)]
    return struct.pack("<%dh" % len(samples), *samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="the panel's address")
    parser.add_argument("--port", type=int, default=5000, help="its port:")
    parser.add_argument("--wav", help="a 16-bit WAV file; a test tone if left out")
    parser.add_argument("--seconds", type=float, default=5.0, help="how long a tone")
    parser.add_argument("--hz", type=float, default=440.0, help="what note")
    parser.add_argument("--loop", action="store_true", help="play it again for ever")
    args = parser.parse_args()

    pcm = from_wav(args.wav) if args.wav else tone(args.seconds, args.hz)
    print(
        f"{len(pcm) / 2 / RATE:.1f} s of {RATE} Hz {BITS}-bit mono, "
        f"{len(pcm) / 1024:.0f} KiB, in {BLOCK_MS} ms blocks"
    )

    sock = socket.create_connection((args.host, args.port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"Connected to {args.host}:{args.port}")
    # The board talks back -- touches, and whether it is awake. Nothing here
    # wants any of it, but a socket nobody reads eventually stops accepting.
    sock.setblocking(False)

    sent = 0
    started = time.monotonic()
    try:
        while True:
            for at in range(0, len(pcm), BLOCK_BYTES):
                block = pcm[at : at + BLOCK_BYTES]
                sock.setblocking(True)
                sock.sendall(audio_header(len(block)) + block)
                sock.setblocking(False)
                try:
                    sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    pass
                sent += len(block)
                # Paced, because the panel plays it at 48 kHz whatever this
                # does: sending faster only fills a buffer until it overflows,
                # and the board's log then says the speaker is not draining.
                due = started + sent / 2 / RATE
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            if not args.loop:
                break
    except KeyboardInterrupt:
        print()
    except OSError as err:
        raise SystemExit(f"The panel went away ({err})")
    finally:
        sock.close()
    print(f"Sent {sent / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
