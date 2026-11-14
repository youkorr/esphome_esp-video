# esphome_esp-video

Two things live here, both for Espressif's **ESP32-P4** and both used through
[ESPHome](https://esphome.io/):

- **[Portall](#portall--home-assistant-on-a-panel-over-wi-fi)** — Home
  Assistant, and any web page, on a touch panel over Wi-Fi. No LVGL.
- **[Camera components](#camera-components)** — video capture, MIPI-CSI sensor
  drivers and an LVGL camera widget.

---

## Portall — Home Assistant on a panel over Wi-Fi

<img src="portall/logo.png" alt="Portall" width="320">

Put a Home Assistant dashboard on an ESP32-P4 touch panel **without writing a
single line of LVGL**. The board is not running Home Assistant and is not
running a browser: the machine that is already on all the time — your Home
Assistant server — renders the page in a headless Chromium and sends the
picture. Touches come back up the same socket and are replayed into that
browser, so the panel behaves like the screen of the machine doing the
rendering.

```
 Home Assistant box                                  ESP32-P4 panel
 ┌───────────────────────────────┐                   ┌──────────────────────┐
 │ Portall add-on                │  TCP :5000        │ portall component    │
 │  headless Chromium            │ ────────────────▶ │  hardware JPEG decode│
 │  tile diff → JPEG rectangles  │   only what moved │  PPA rotate / scale  │
 │  replays touches into the page│ ◀──────────────── │  draw to the panel   │
 └───────────────────────────────┘  touches, sleep   └──────────────────────┘
```

Only the parts of the screen that changed are sent, so **a still dashboard
costs 0 KiB/s**. The P4 decodes the JPEG in hardware and rotates or scales it
with the PPA, so the ceiling is the network rather than the CPU.

**What you get**

- Any web page, not only Home Assistant — Jellyfin, a camera, a train board
- A launcher page built by the add-on, themed like Homepage, with 520 named
  icons and 50 real service logos, all carried rather than fetched
- An on-screen keyboard, so a panel with no keys can use a search box
- YouTube in television mode: sign in with a code from your phone, then use
  the phone as the remote — see the add-on's documentation
- Sleep and wake from Home Assistant, presence-driven if you want
- Sound for the page, over the same socket, into the panel's own speaker
- Several panels from one add-on, each with its own browser and its own log

**Measured** on three boards (Waveshare 7B 1024×600, M5Stack Tab5 720×1280,
Guition 10" 800×1280): touch end to end 3–22 ms, ~105 ms from a tap to the
picture changing, 22–25 pictures a second on a moving page, 0 KiB/s at rest.

### Install the add-on

In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
and add

```
https://github.com/youkorr/esphome_esp-video
```

or click
[![Open your Home Assistant instance and show the add add-on repository dialog.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fyoukorr%2Fesphome_esp-video)

Then install **Portall**, and read its **Documentation** tab — the options,
the launcher, the keyboard, the gestures and what each costs are all there.
The source is under [`portall/`](portall/).

### Flash the panel

The board runs the `portall` ESPHome component. Validated example
configurations are in [`yaml/`](yaml/) — start from
`yaml/p4-home-assistant.yaml` (Waveshare) or
`yaml/guition-10-home-assistant.yaml` (Guition 10").

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
    components: [portall]

portall:
  display_id: main_screen
  touchscreen_id: my_touch
  port: 5000
  width: 800
  height: 1280
```

The token, the URL and the panel list belong in the add-on's options, not in
the firmware.


```

## License

The **original ESPHome integration code** in this repository (the Python
configuration/codegen and the C++ glue) is released under the
[MIT License](LICENSE), Copyright (c) 2024-2026 youkorr. That includes
Portall -- its ESPHome components, the stand-alone `usb_bluetooth` folder and
the Home Assistant add-on -- and every one of its source files says so in an
SPDX header of its own.

This repository also **bundles third-party source code** that keeps its own
license and is **not** relicensed as MIT:

- **Espressif** `esp_video` / `esp_cam_sensor` / `esp_ipa` / `esp_sccb_intf`
  sources — **Apache-2.0** (Copyright © Espressif Systems (Shanghai) CO LTD).
- **ESPHome** — the external-component framework these build on — **MIT/GPLv3**
  (a compiled firmware is a combined work subject to ESPHome's terms).
- **Realtek** Bluetooth firmware (`rtl8761bu_*.bin`, carried for Portall's
  Bluetooth components) — Realtek's own licence, beside the files.
- A few service marks and icons in Portall's add-on (simple-icons, CC0;
  Google's Material Symbols, Apache-2.0; Home Assistant's brand icons), each
  named in `portall/logos.py`, and a touch screen HID report descriptor from
  Espressif's `usb_extend_screen` example (MIT), named in
  `components/usb_display_tusb/usb_descriptors.h`.

See [NOTICE](NOTICE) for the full breakdown and
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) for the Apache-2.0 text.
