#!/usr/bin/env python3
"""Does the Realtek firmware parser build the right bytes, and refuse the rest?

The format is a header, a metadata table indexed off by one, a backwards walk
for a project id and a four-byte version splice. Every one of those is a place
to be silently wrong -- and silently wrong here means a dongle bricked part
way through a patch, on somebody else's panel.

Two halves. The synthetic cases build files byte by byte, so the expected
answer is stated independently of the parser. The real case runs against
Realtek's own file when it is present, which is the only thing that can say
the transcription matches what linux-firmware actually ships.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rtlfw  # noqa: E402

FAILED = []


def check(what, ok):
    print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        FAILED.append(what)


def build(patches, fw_version=0x11223344, project=14, sig=rtlfw.EPATCH_SIGNATURE):
    """A file with the layout btrtl.c describes, built here rather than parsed.

    `patches` is [(chip_id, payload)]. The payload is written whole; the parser
    is expected to hand back the same bytes with the LAST FOUR replaced by the
    header's version, which is the one transformation in the format that is
    not a copy.
    """
    count = len(patches)
    head = sig + struct.pack("<IH", fw_version, count)
    table = b""
    table += b"".join(struct.pack("<H", c) for c, _ in patches)
    table += b"".join(struct.pack("<H", len(p)) for _, p in patches)
    # Offsets are absolute in the file, so they are only known once the size of
    # everything before the bodies is.
    start = len(head) + 2 * count + 2 * count + 4 * count
    offs, at = [], start
    for _, p in patches:
        offs.append(at)
        at += len(p)
    table += b"".join(struct.pack("<I", o) for o in offs)
    bodies = b"".join(p for _, p in patches)
    # The instruction stream, read BACKWARDS from the extension signature:
    # (data, length, opcode) with opcode 0 length 1 carrying the project id.
    tail = bytes((project, 1, 0)) + rtlfw.EXTENSION_SIG
    return head + table + bodies + tail


def main():
    print("a file built to the format's own description")
    body_a = bytes(range(256)) * 4          # 1024 bytes
    body_b = bytes((0xAA,)) * 500
    fw = build([(1, body_a), (2, body_b)], fw_version=0x11223344)
    out, version, lmp, project = rtlfw.images(fw, b"")
    check("both patches are found", sorted(out) == [0, 1])
    check("and a chip id of N serves ROM version N-1", 1 in out and len(out[1]) == 500)
    check("the header's version is read", version == 0x11223344)
    check("and the project id maps to a chip family", (project, lmp) == (14, 0x8761))

    # The splice: everything but the last four bytes is the body, and those
    # four are the header's version, little-endian.
    got = out[0]
    check("the body is copied whole but for its last four bytes",
          got[:-4] == body_a[:-4])
    check("and those four carry the header's version",
          got[-4:] == struct.pack("<I", 0x11223344))

    # The config is appended, not merged.
    config = struct.pack("<IH", rtlfw.CONFIG_MAGIC, 0) + b""
    out2, _, _, _ = rtlfw.images(fw, config)
    check("a config file is appended to every image",
          out2[0] == got + config and len(out2[1]) == 500 + len(config))

    print("\nand it refuses what it should")

    def refuses(what, data, config=b""):
        try:
            rtlfw.images(data, config)
        except rtlfw.NotFirmware:
            check(what, True)
            return
        check(what, False)

    refuses("a file with no signature", b"nonsense" + fw[8:])
    refuses("the newer RTBTCore format, by name",
            build([(1, body_a)], sig=rtlfw.EPATCH_SIGNATURE_V2))
    refuses("a file that does not end in the extension signature", fw[:-1])
    refuses("a file too short to hold a header", b"Realtech")
    refuses("a config whose magic is wrong", fw, b"\x00\x00\x00\x00\x00\x00")
    refuses("a config whose length disagrees with itself", fw,
            struct.pack("<IH", rtlfw.CONFIG_MAGIC, 99))
    # A patch whose offset and length run off the end: exactly the case that
    # would read somebody else's memory, or send rubbish to a controller.
    bad = bytearray(fw)
    bad[14 + 2 * 2: 14 + 2 * 2 + 2] = struct.pack("<H", 0xFFFF)
    refuses("a patch that does not fit inside the file", bytes(bad))
    # No project id at all -- the instruction stream says end immediately.
    refuses("a file whose instructions name no project",
            build([(1, body_a)])[:-7] + bytes((0xFF, 1, 0xFF)) + rtlfw.EXTENSION_SIG)

    print("\nRealtek's own file, if it is here")
    real = os.environ.get("RTL_FW")
    cfg = os.environ.get("RTL_CFG")
    if not real or not os.path.exists(real):
        print("  --   set RTL_FW (and RTL_CFG) to check against the real thing.")
        print("       linux-firmware carries rtl_bt/rtl8761bu_fw.bin and _config.bin.")
    else:
        data = open(real, "rb").read()
        conf = open(cfg, "rb").read() if cfg and os.path.exists(cfg) else b""
        out, version, lmp, project = rtlfw.images(data, conf)
        print(f"       {len(data)} bytes, project {project}, lmp {lmp:04x}, "
              f"version {version:08x}, ROM versions {sorted(out)}")
        # The figure this repository recorded from Linux long before any of
        # this was written: 30 210 bytes for the RTL8761BU. If the
        # transcription is right it lands on it exactly.
        check("the RTL8761BU's patch comes to the 30 210 bytes Linux uploads",
              out.get(1) is not None and len(out[1]) == 30210)
        check("and it is the patch rather than the file's first bytes",
              out[1][:8] != rtlfw.EPATCH_SIGNATURE)
        check("the config is on the end of it",
              conf == b"" or out[1][-len(conf):] == conf)

    if FAILED:
        print(f"\n{len(FAILED)} problem(s)")
        return 1
    print("\nok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
