# Upstream-ready `esp_video_camera` for ESPHome

This folder contains an **upstream-ready** version of `esp_video_camera`,
structured exactly like ESPHome's own tree so you can copy it into a fork of
[`esphome/esphome`](https://github.com/esphome/esphome) and open a PR. It is kept
**separate** from this repository's working external components — nothing here
changes the components your users already consume.

```
upstream/
├── esphome/components/esp_video_camera/   → copy to esphome/components/esp_video_camera/
│   ├── __init__.py
│   ├── esp_video_camera.h
│   ├── esp_video_camera.cpp
│   └── i2c_helper.h
├── tests/components/esp_video_camera/      → copy to tests/components/esp_video_camera/
│   └── test.esp32-p4-idf.yaml
├── docs/esp_video_camera.rst               → copy to esphome-docs/components/esp_video_camera.rst
└── CODEOWNERS.snippet                      → append the line to esphome's CODEOWNERS
```

## What is different from the repo's `esp_video_camera`

- **English** only (comments, logs, docs).
- **No vendored Espressif sources.** The IDF dependencies are pulled through the
  component manager: `espressif/esp_video` and `espressif/esp_cam_sensor`.
- **Self-contained**: this single component both **initialises the camera
  pipeline** (MIPI-CSI, optional USB-UVC) and **serves the Home Assistant camera
  entity**, so it does not need the repo's separate `esp_video` component. It
  takes an `i2c_id` for the sensor bus.

## ⚠️ Status

**Skeleton — not yet compiled on hardware.** It is structured and written to
ESPHome conventions, but the following must be verified on a real ESP32-P4 build
(ESP-IDF) before opening the PR. Search for `TODO(upstream)` markers:

1. The exact **managed component versions** (`ref=`) for `espressif/esp_video`
   and `espressif/esp_cam_sensor`.
2. The exact **sdkconfig option names** the managed components expose for sensor
   auto-detection / ISP / JPEG (the `CONFIG_CAMERA_*` / `CONFIG_ESP_VIDEO_*`
   keys). They mirror the `-D` flags used by this repo today, but confirm against
   the managed component's `Kconfig`.
3. That `i2c_master_get_bus_handle()` returns the ESPHome bus handle on your
   board (it does in this repo's `i2c_helper.h`).

## How to submit (summary)

1. Open a **discussion issue** on `esphome/esphome` first (body = `UPSTREAMING.md`
   §5 + `GAP_ANALYSIS.md` from this repo). Get maintainer agreement.
2. Fork `esphome/esphome`, branch from `dev`, copy the files above.
3. `pre-commit run -a`, then compile the test:
   `esphome compile tests/components/esp_video_camera/test.esp32-p4-idf.yaml`.
4. Append the `CODEOWNERS` line; open the PR to `esphome:dev`.
5. Open the companion docs PR on `esphome/esphome-docs`.

See `../UPSTREAMING.md` (Plan B) and `../GAP_ANALYSIS.md` on the
`claude/esp-video-home-assistant-7g8a1n` branch for the full strategy.
