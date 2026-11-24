# A network test bench for an ESP32-P4 and its ESP32-C6

Two builds of ESPHome on the same board, one script on a computer, one
table at the end. Written to measure
[esphome/esphome#23122](https://github.com/esphome/esphome/pull/23122)
(high-performance Wi-Fi settings sent to the C6) on a link fast enough to
show what it does.

| file | what it is |
|---|---|
| `nettest-dev.yaml` | ESPHome as it is |
| `nettest-pr.yaml` | the same file, with the pull request's `wifi` and `esp32_hosted` components and the label `pr` |
| `nettest.py` | the measurement, standard-library Python, runs on Windows |
| `components/nettest/` | three plain socket servers, and a line saying what the build is |

The two YAMLs are identical line for line apart from the label and the
`external_components` entry that brings in the pull request. Both carry
`malloc_to_psram` (see below).
`tools/checknettest.py` keeps them so.

## What differs between the two builds

Read off ESPHome's own codegen (03eebae for dev, e59c905's `wifi` for the
PR), every other sdkconfig option being the same in both:

| | dev | pr, PSRAM guaranteed | pr, PSRAM not guaranteed |
|---|---|---|---|
| `WIFI_RMT_STATIC_RX_BUFFER_NUM` | default | 16 | 16 |
| `WIFI_RMT_DYNAMIC_RX_BUFFER_NUM` | default | 64 | 64 |
| `WIFI_RMT_DYNAMIC_TX_BUFFER_NUM` | default | not applicable | 64 |
| `WIFI_RMT_RX_BA_WIN` | default | 32 | 32 |
| `WIFI_RMT_TX_BA_WIN` | default | 32 | 32 |
| `WIFI_RMT_AMPDU_TX/RX_ENABLED` | default (y) | y | y |

Two things decide which of these can change anything on the C6:

- **These values reach the C6 through `esp_wifi_init()`**, as the fields of
  `wifi_init_config_t` that esp-hosted carries to the co-processor. In
  ESP-IDF 5.5 that struct has `static_rx_buf_num`, `dynamic_rx_buf_num`,
  `tx_buf_type`, `static_tx_buf_num`, `dynamic_tx_buf_num`,
  `cache_tx_buf_num`, `ampdu_rx_enable`, `ampdu_tx_enable` and `rx_ba_win`.
  **It has no TX block-ack window.** So `WIFI_RMT_TX_BA_WIN` has no field to
  travel in and does not reach the C6; the C6's own firmware decides it.
- **With PSRAM guaranteed, TX buffers are static in both builds.**
  esp_wifi_remote's `WIFI_RMT_DYNAMIC_TX_BUFFER` depends on
  `!(SPIRAM_TRY_ALLOCATE_WIFI_LWIP && !SPIRAM_IGNORE_NOTFOUND)`, and the
  pull request does not set the static count. So on a board with
  guaranteed PSRAM the pull request changes nothing that the C6 uses for
  sending. The receive side changes: more RX buffers and a larger RX
  block-ack window.

A recent C6 firmware compares the configuration the host sends with the one
it has, and re-initialises Wi-Fi when they differ
(`eh_cp_feat_wifi_init.c` in esp-hosted-mcu). A C6 that reports `0.0.0` is
older than the firmware ESPHome offers. Nothing here says what it does with
these fields. **Update the C6 first.**

## What the first run on a board found: ESPHome stops receiving

The first run used ESPHome dev as it is: guaranteed PSRAM, C6 firmware 3.1.0,
and a computer on a cable sending as fast as it could. The board stopped
taking data within seconds, in every round. Its log:

```
[E][eh_sdio:000][sdio_process_rx]: bus_rx: payload copy alloc failed len=1490
[I][nettest:067]: ... heap=27624/20396 ...    (free internal heap: 476 KB before)
E (105497) task_wdt: Task watchdog got triggered ... loopTask (CPU 1)
E (105497) task_wdt: CPU 0: IDLE0   CPU 1: IDLE1
```

esp-hosted 3.1.0 copies every received packet with a plain `malloc()`
(`eh_host_bus_sdio.c`, `sdio_process_rx`). ESPHome builds with
`CONFIG_SPIRAM_USE_CAPS_ALLOC`, which keeps `malloc()` in internal RAM. And
its high-performance networking opens a 512000-byte TCP window, which is
more than a P4's internal RAM holds once a loss leaves segments waiting.
Internal RAM falls to about 20 KB, every further packet is dropped, and TCP
stops. The watchdog that ended one round fired with both cores idle: the
loop was waiting for something, most likely an answer from the C6 that could
not be received. That is the "SDIO mempool OOM under TCP receive with
guaranteed PSRAM" the pull request's first test reported, on dev and the PR
alike.

So both bench files set `malloc_to_psram: "true"` by default:
`CONFIG_SPIRAM_USE_MALLOC`, with every allocation of up to 128 KB still trying
internal RAM first. It is the same change in both builds, so the comparison
stays fair, and it is what a Portall panel already sets. With `"false"` the
bench is ESPHome as it is, and reproduces the stall. Every reply says which
(`malloc=psram` or `malloc=internal`), and the table keeps them apart.

While a test runs, the bench does not ask the C6 for the signal strength
either, so the bench cannot cause such a wait itself.

## How to measure

1. **Use a computer on a cable to the router.** A computer on Wi-Fi shares
   the air with the board, and the result then measures both radios.
2. Put each YAML in the ESPHome dashboard under the board's own node name
   (`name:` at the top), so it can be flashed over the air. The dashboard
   must be a **dev** ESPHome, because the pull request is written against
   the dev branch. Both builds need the same ESPHome.
3. Flash `nettest-dev.yaml`. In Home Assistant, update **ESP32-C6 Firmware**
   if an update is offered. Every result records the C6's version.
4. On the computer, with the board's address:

   ```
   py nettest.py 192.168.1.50
   ```

5. Flash `nettest-pr.yaml` and run the same command. Then dev again, then
   PR again: going back and forth is what separates the build from the
   evening's radio weather. Leave the board where it was.
6. Print the table:

   ```
   py nettest.py --table
   ```

   It prints Markdown, grouped by the build and the C6 version each board
   reported, with the median and the range of the runs, and the compiled
   values of each build underneath.

To repeat with PSRAM not guaranteed, which is the other row of the table
above, set `psram_optional: "true"` in both files.

`--rate 20` sends to the board at 20 Mbit/s instead of as fast as the link
takes. That is closer to a Portall sender, whose default `max_rate` of
3000 KiB/s is about 24.6 Mbit/s. Results at different rates are kept apart in
the table.

**When a test stops.** A test the board stops taking part in (15 s without
progress, `--timeout`) does not end the run. The run then asks the board
whether it restarted, and with what reset reason (`panic`, `task_wdt`,
`int_wdt`, ...), whether it is still up but stopped reading, or whether it no
longer answers. The table counts such runs and lists each one. Keep the
board's log open in ESPHome during a run: it says what the board was doing
when it stopped.

## What it measures

| | how |
|---|---|
| ping | 20 small UDP round trips |
| TCP to the board | the computer sends for 10 s; **the board** times first byte to last, and says the longest it waited between two reads |
| TCP from the board | the board sends for 10 s; the computer times it |
| 512 KB bursts | ten transfers, each on its own connection, timed by the board. Short transfers never leave TCP's slow start, so they behave more like one picture than a long stream does |
| heap | the board's free internal heap, now and at its lowest since boot |

Espressif's own figures for an ESP32-P4 with a C6 over 4-bit SDIO at 40 MHz,
2.4 GHz, over the air: about **30 Mbit/s of TCP to the P4 and 32 from it**
(esp-hosted-mcu, `docs/design/performance.md`, section 1.1). A result far
below that means the link is what was measured.

The servers are plain BSD sockets on lwIP: three FreeRTOS tasks, the same
stack, priority and core in both builds, 16 KB buffers. Nothing else runs on
the board.

## Status

`tools/checknettest.py` builds the servers and the facts line on a
workstation and measures them with this script over loopback. It checks the
component's C++ against ESPHome's core headers, with and without Wi-Fi. It
runs ESPHome's config and codegen on both YAMLs, with PSRAM guaranteed and
not, and reads the sdkconfig back. It fails when the PR build does not get
the pull request's values.

The bench has **not been compiled by ESP-IDF or flashed** here: there is no
board and no toolchain.
