#!/usr/bin/env python3
"""Turn Realtek's two firmware files into the bytes a dongle is sent.

A Realtek Bluetooth controller runs a ROM that answers HCI and does very
little on the air; Linux uploads a patch before it calls one a working
controller. This is that patch, prepared.

The preparation is done HERE, in Python, rather than on the board -- the
format is a header, a funky metadata table, a backwards walk to find a project
id and a four-byte version splice, and every one of those is a place to be
wrong silently. Python can be run against the real file and checked; C++ on a
panel cannot. So the board is handed bytes and a length, and its only job is
to cut them into 252-byte fragments.

Transcribed from rtlbt_parse_firmware() and btrtl_setup_rtl8723b() in Linux's
drivers/bluetooth/btrtl.c, which is where the format is actually written down.
The files themselves are NOT in this repository: they are Realtek's, they are
redistributed by linux-firmware under their own licence, and a household
downloads them once.
"""
import struct
import sys

EPATCH_SIGNATURE = b"Realtech"
EPATCH_SIGNATURE_V2 = b"RTBTCore"
# The four bytes every v1 image ends with. Linux checks it before trusting
# anything else in the file, and so does this: a truncated download or the
# wrong file entirely is caught here rather than half-way through a flash.
EXTENSION_SIG = bytes((0x51, 0x04, 0xFD, 0x77))
CONFIG_MAGIC = 0x8723AB55

# project id -> the lmp_subver the file is FOR. A file for one chip must not be
# sent to another, and the id is the only thing in the file that says which.
# Copied from project_id_to_lmp_subver in btrtl.c.
PROJECT_ID_LMP = {
    0: 0x1200, 1: 0x8723, 2: 0x8821, 3: 0x8761, 7: 0x8703, 8: 0x8822,
    9: 0x8723, 10: 0x8821, 13: 0x8822, 14: 0x8761, 18: 0x8852, 20: 0x8852,
    25: 0x8852, 36: 0x8851, 44: 0x8922, 47: 0x8852, 51: 0x8761,
}


class NotFirmware(Exception):
    """The file is not a Realtek patch, or not one this can read."""


def project_id(fw):
    """Walk the instructions backwards from the end for the project id.

    They are stored as (data, length, opcode) triples read from the END of the
    file, which is why this counts down rather than up. Opcode 0 with length 1
    carries the id; 0xFF is the end.
    """
    at = len(fw) - len(EXTENSION_SIG)
    floor = 14 + 3  # the header, and room for one triple
    while at >= floor:
        opcode = fw[at - 1]
        length = fw[at - 2]
        data = fw[at - 3]
        at -= 3
        if opcode == 0xFF:
            break
        if length == 0:
            raise NotFirmware("an instruction with length 0")
        if opcode == 0 and length == 1:
            return data
        at -= length
    raise NotFirmware("no project id in the file")


def images(fw, config):
    """Every ROM version this file covers, and the bytes to send for each.

    Returns {rom_version: bytes}. A controller reports its ROM version over
    the air and picks its own; nothing here has to guess which dongle it is.
    """
    if len(fw) <= 8:
        raise NotFirmware("too short to be a firmware file")
    if fw[:8] == EPATCH_SIGNATURE_V2:
        raise NotFirmware(
            "this is the newer RTBTCore format, which this does not read. "
            "It belongs to chips no panel here has met; say so if you have one"
        )
    if fw[:8] != EPATCH_SIGNATURE:
        raise NotFirmware("no Realtech signature -- is this the right file?")
    if fw[-len(EXTENSION_SIG):] != EXTENSION_SIG:
        raise NotFirmware("the file does not end the way a patch does; "
                          "it is truncated or it is not a patch")

    which = project_id(fw)
    if which not in PROJECT_ID_LMP:
        raise NotFirmware(f"project id {which}, which is not one this knows")

    fw_version, count = struct.unpack_from("<IH", fw, 8)
    if count == 0:
        raise NotFirmware("the file carries no patches at all")
    base = 14
    need = base + 8 * count
    if len(fw) < need:
        raise NotFirmware("the patch table runs past the end of the file")
    chip_ids = struct.unpack_from("<%dH" % count, fw, base)
    lengths = struct.unpack_from("<%dH" % count, fw, base + 2 * count)
    offsets = struct.unpack_from("<%dI" % count, fw, base + 4 * count)

    if config:
        magic, total = struct.unpack_from("<IH", config)
        if magic != CONFIG_MAGIC:
            raise NotFirmware("the config file does not carry its own magic")
        if total + 6 != len(config):
            raise NotFirmware(
                f"the config says it is {total + 6} bytes and it is {len(config)}")

    out = {}
    for i in range(count):
        length, offset = lengths[i], offsets[i]
        if length < 4 or offset > len(fw) or length > len(fw) - offset:
            raise NotFirmware(f"patch {i} does not fit inside the file")
        # The last four bytes of every patch are REPLACED by the version out
        # of the header. That is not a checksum and not padding: the
        # controller reports it back as its own version afterwards, which is
        # how anybody can tell a patched dongle from one running its ROM.
        image = bytearray(fw[offset:offset + length])
        image[length - 4:length] = struct.pack("<I", fw_version)
        # A chip reporting ROM version N takes the patch whose chip id is
        # N + 1. Off by one in somebody else's file, and the only reason it is
        # written this way is that it is what btrtl.c does.
        out[chip_ids[i] - 1] = bytes(image) + config
    return out, fw_version, PROJECT_ID_LMP[which], which


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print("usage: rtlfw.py <fw.bin> [config.bin]")
        return 2
    fw = open(argv[1], "rb").read()
    config = open(argv[2], "rb").read() if len(argv) > 2 else b""
    try:
        out, version, lmp, which = images(fw, config)
    except NotFirmware as err:
        print(f"not usable: {err}")
        return 1
    print(f"{argv[1]}: {len(fw)} bytes, project id {which}, for lmp_subver "
          f"{lmp:04x}, firmware version {version:08x}")
    if config:
        print(f"{argv[2]}: {len(config)} bytes of config, appended to each")
    for rom in sorted(out):
        print(f"  a controller reporting ROM version {rom} is sent "
              f"{len(out[rom])} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
