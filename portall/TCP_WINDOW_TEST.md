# Experimental ESP32-P4 TCP receive window with ESP-Hosted

This branch starts from the main firmware with direct panel buffers and split
audio. It does not change the C6 binary, SDIO clock, sender, or add-on version.
No setting is changed unless `tcp_rx_window` is present. The setting applies
to lwIP on the P4, globally (both TCP ports and other TCP connections).

Keep your current working YAML and external components, except use
`ref: claude/portall-tcp-window` for Portall. In the existing `psram:` block
set `ignore_not_found: false`. In the existing `portall:` block add:

```yaml
tcp_rx_window: 98304
```

Keep panel_buffers, audio_port, resolution, quality, fps and max_rate as before.
Do not copy over your private YAML or change ESP-Hosted. Remove any conflicting
manual sdkconfig overrides, as reported by validation. Mandatory PSRAM also
enables ESPHome's other high-performance defaults; this is not a perfectly
single-variable test against the original optional-PSRAM build.

| Profile | Window | Scale | TCP RX mailbox | TCP/IP mailbox |
|---|---:|---:|---:|---:|
| V1 | 98304 | 1 | 80 | 96 |
| V2 | 131072 | 2 | 96 | 112 |

Mailbox values follow the ESP-IDF Kconfig guidance (window / MSS + 2);
TCP send buffer remains 65534. V2 needs scale 2 because scale 1 can advertise
at most 131070 bytes. Check generated sdkconfig, and capture a fresh TCP
handshake; confirm the P4 advertises window scaling and the sender's
snd_wnd can exceed 65535. Monitor internal heap, PSRAM, audio, frame rate,
retransmissions, stalls, and the SD card. If V1 is stable, change only the
profile selection to 131072 and repeat. To restore the old build, remove the
option, restore your original PSRAM configuration and use main. No changes
to the C6 firmware are necessary for these tests.
