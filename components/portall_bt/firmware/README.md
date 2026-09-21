# The Realtek firmware files beside this one

`rtl8761bu_fw.bin` and `rtl8761bu_config.bin` are Realtek's, carried here so a
panel with a TP-Link UB500 (or any other RTL8761BU dongle) can be flashed
without hunting for them.

**Why they are needed at all.** A Realtek Bluetooth controller runs a ROM that
answers every HCI command and does almost nothing on the air. A panel with one
enumerates perfectly, brings Bluedroid all the way up, reports its own
address — and then hears nothing on an inquiry and gets Page Timeout on every
connection. Linux uploads this patch before it calls one a working controller,
and so does `portall_bt`.

**Where they came from.** linux-firmware, `rtl_bt/`, byte for byte:
<https://gitlab.com/kernel-firmware/linux-firmware>

`rtl8761bu_fw.bin` is 44 484 bytes, epatch v1, project id 14 (an 8761B),
firmware version `dfc6d922`. `rtl8761bu_config.bin` is 6 bytes: a magic and a
zero length.

**Licence.** Realtek's, reproduced beside them in
`LICENCE.rtlwifi_firmware.txt`: redistribution in binary form **without
modification** is permitted provided that notice travels with them. They are
unmodified here, and `tools/rtlfw.py` prepares what a dongle is sent at build
time rather than changing either file.

**A dongle that is not a Realtek needs none of this.** A Broadcom's patch is
optional and it works without one; `firmware:` is only ever read for a
controller that reports itself as Realtek and is still running its ROM.

**Other dongles.** linux-firmware's `rtl_bt/` carries a pair per chip. Point
`firmware:` and `firmware_config:` at yours, and run
`tools/rtlfw.py <fw> <config>` first — it prints which ROM versions the pair
covers and how many bytes each one is sent, before anything is flashed.
