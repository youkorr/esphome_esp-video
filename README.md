# esphome_esp-video

External [ESPHome](https://esphome.io/) components for video capture, camera
sensor drivers and LVGL camera display on Espressif ESP32 targets (especially
the **ESP32-P4** with the MIPI-CSI interface).

## Components

| Component | Role |
|-----------|------|
| `esp_video` | Video pipeline (CSI / DVP / ISP / JPEG) built on Espressif's `esp_video` framework. Must always be present. |
| `esp_cam_sensor` | Camera sensor drivers + hardware transforms (PPA: crop / resize / rotation / mirror). |
| `lvgl_camera_display` | LVGL widget that displays the live camera stream on a `canvas`, with optional detection overlays. |

These three components work together:

```
[MIPI-CSI sensor] → esp_cam_sensor → esp_video (ISP/encoding) → lvgl_camera_display → LVGL canvas
```

## Installation in ESPHome

Reference this repository as an external component source:

```yaml
external_components:
  # Camera drivers + display (this repository)
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
    components: [esp_video, esp_cam_sensor, lvgl_camera_display]
    refresh: 0s

  # Custom LVGL 9.5 — REQUIRED by lvgl_camera_display (PPA acceleration)
  - source:
      type: git
      url: https://github.com/youkorr/lvgl_9.5
      ref: main
    components: [lvgl, image, font]
    refresh: always
```

> **Note:** The `mipi_dsi` component (ESP32-P4 MIPI-DSI display interface) is now
> **integrated natively into ESPHome** — no external component is needed.

> **Note:** These components target the **ESP32-P4** (`esp32` + `esp-idf` framework).
> PSRAM is mandatory for the video buffers.

> **Mandatory LVGL dependency.** `lvgl_camera_display` copies frames into an
> LVGL `canvas` using the **PPA** hardware accelerator. This requires the
> **LVGL 9.5** fork (`https://github.com/youkorr/lvgl_9.5`, MIT licensed) — the
> official ESPHome `lvgl` component does not provide `use_ppa`.

### 1. I²C bus

The sensor is driven over I²C (SCCB). Declare the bus and give it an `id` that
will be reused by `esp_video` and `esp_cam_sensor`:

```yaml
i2c:
  - id: bsp_bus
    sda: GPIO31
    scl: GPIO32
    frequency: 400kHz

psram:
  mode: hex
  speed: 200MHz
```

### 2. Video pipeline: `esp_video`

```yaml
esp_video:
  i2c_id: bsp_bus
  xclk_pin: GPIO36          # XCLK clock pin (or -1 / NO_CLOCK with on-board oscillator)
  xclk_freq: 24000000       # 1 to 40 MHz (24 MHz typical)
  enable_jpeg: true         # hardware JPEG encoder
  enable_isp: true          # ISP pipeline (RAW → RGB565)
  enable_uvc: false         # USB-UVC host (external USB camera) — see below
  use_heap_allocator: true  # allocate buffers in PSRAM
```

| Option | Default | Description |
|--------|---------|-------------|
| `i2c_id` | *(required)* | I²C bus shared with the sensor |
| `xclk_pin` | `GPIO36` | XCLK pin (`GPIO36`, an integer, `-1` or `NO_CLOCK`) |
| `xclk_freq` | `24000000` | XCLK frequency (1–40 MHz) |
| `enable_jpeg` | `true` | Enable the hardware JPEG encoder |
| `enable_isp` | `true` | Enable the ISP (RAW → RGB565 conversion) |
| `enable_uvc` | `false` | USB-UVC host: support an external USB camera on the P4 USB-OTG port (see [USB-UVC](#usb-uvc-external-usb-camera)) |
| `use_heap_allocator` | `true` | Place video buffers in PSRAM |
| `enable_xclk_init` | `false` | Generate XCLK via LEDC (non-M5Stack boards) |

> **Note: the H.264 encoder is disabled.** The hardware H.264 device is compiled
> under `#if CONFIG_ESP_VIDEO_ENABLE_HW_H264_VIDEO_DEVICE`, a flag the component
> never sets — so `/dev/video11` is not created. There is no `enable_h264`
> option (it would be rejected during validation). Only **ISP** and **hardware
> JPEG** are active.

#### USB-UVC (external USB camera)

Set `enable_uvc: true` to plug an external **USB (UVC) camera** into the
ESP32-P4 USB-OTG port, in addition to (or instead of) a MIPI-CSI sensor. It is
**off by default** — MIPI-CSI-only builds are unchanged and pay no overhead.

```yaml
esp_video:
  i2c_id: bsp_bus
  enable_uvc: true   # external USB camera on the P4 USB-OTG port
```

When enabled, the USB host stack + Espressif's `usb_host_uvc` driver are compiled
in and started, and a connected UVC camera is enumerated as a `/dev/videoN` V4L2
device. Requires the USB port in host/OTG mode with VBUS power for the camera.

> Full details, prerequisites and troubleshooting:
> [`components/esp_video/README_USB_UVC.md`](components/esp_video/README_USB_UVC.md).
> Note: the UVC node is not yet consumed by `esp_cam_sensor` (which is MIPI-CSI
> specific) — a consumer opening `/dev/videoN` is still required.

### 3. Sensor: `esp_cam_sensor`

```yaml
esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: ov5647       # ov5647 | ov02c10 | sc202cs | sc2336
  resolution: "640x480"     # see per-sensor tables below
  pixel_format: "RGB565"    # RGB565 (zero-copy LVGL) | YUYV | UYVY | NV12 | JPEG | RAW8
  framerate: 30             # 1–60 fps
  jpeg_quality: 15          # 1–63 (when pixel_format is JPEG)
  mirror_x: false           # horizontal mirror (PPA hardware)
  mirror_y: false           # vertical mirror (PPA hardware)
  rotation: 0               # 0 / 90 / 180 / 270° (PPA hardware)
  crop_offset_x: 0          # horizontal crop (pixels from the left)
```

| Option | Default | Description |
|--------|---------|-------------|
| `sensor_type` | `sc202cs` | `ov5647`, `ov02c10`, `sc202cs` or `sc2336` (`sensor:` accepted as an alias) |
| `i2c_id` | `0` | Shared I²C bus |
| `lane` | `1` | Number of MIPI lanes (1–4) |
| `xclk_pin` | `GPIO36` | XCLK pin |
| `xclk_freq` | `24000000` | XCLK frequency |
| `sensor_addr` | `0x36` | Sensor I²C address |
| `resolution` | `720P` | Resolution (alias or `WxH`, see below) |
| `pixel_format` | `JPEG` | Output pixel format |
| `framerate` | `30` | Frames per second (1–60) |
| `mirror_x` / `mirror_y` | – | Hardware mirroring (PPA) |
| `rotation` | – | Hardware rotation 0/90/180/270° (PPA) |
| `crop_offset_x` | `0` | Crop offset (0–800) |
| `output_width` / `output_height` | `0` | PPA hardware resize (0 = no resize) |

#### Generic resolution aliases

Valid for all sensors (passed to the native driver):

| Alias | Dimensions |
|-------|------------|
| `QVGA` | 320 × 240 |
| `VGA` / `480P` | 640 × 480 |
| `720P` | 1280 × 720 |
| `1080P` | 1920 × 1080 |
| `"WxH"` | free dimensions (e.g. `"800x600"`) |

## Supported sensors and resolutions

Four sensors are compiled into the driver. The tables below list every
resolution that is actually wired into the build (driver format tables +
`CONFIG_CAMERA_*` flags enabled in `esp_video_build.py`). White balance, ISP and
detection are handled automatically through the sensor registers and IPA JSON
configuration.

### OV5647 (MIPI 2-lane, 5 MP)

Raspberry Pi Camera v1 type sensor. RAW output converted to RGB565 by the ISP.
Native formats from `ov5647.c` (all compiled):

| Resolution | FPS | Format | Notes |
|------------|-----|--------|-------|
| 800 × 640 | 50 | RAW8 | native |
| 800 × 800 | 50 | RAW8 | native |
| 800 × 1280 | 50 | RAW8 | native (portrait) |
| 1280 × 960 | 45 | RAW10 | native, binning |
| 1920 × 1080 | 30 | RAW10 | native (1080P) |

```yaml
esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: ov5647
  resolution: "1280x960"
  pixel_format: "RGB565"
  framerate: 30
```

### OV02C10 (MIPI 1-lane, 2 MP)

Native formats from `ov02c10.c` (RAW10, all compiled):

| Resolution | FPS | Notes |
|------------|-----|-------|
| 640 × 368 | 30 | **recommended** — ~16:9, ~98% FOV, 16-byte aligned (rotation safe) |
| 640 × 480 | 30 | VGA 4:3 — 25% horizontal crop (1.33× zoom, 75% FOV) |
| 800 × 600 | 30 | SVGA 4:3 — 25% horizontal crop |
| 480 × 640 | 30 | portrait (270° rotation handled by LVGL) |
| 1288 × 728 | 30 | near HD 16:9, full sensor downscaled by the ISP |
| 1920 × 1080 | 30 | 1080P — full sensor, 100% FOV |

> **Note:** `960x540` is disabled (persistent watchdog) — use `800x600` or `1288x728`.

```yaml
esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: ov02c10
  resolution: "640x368"   # best FOV / 16:9 trade-off
  pixel_format: "RGB565"
  framerate: 30
```

### SC202CS (MIPI 1-lane, 2 MP)

Native 1600 × 1200 sensor with 2×2 binning. Formats from `sc202cs.c`
(all compiled):

| Resolution | FPS | Format | Notes |
|------------|-----|--------|-------|
| 800 × 600 | 30 | RAW8 | centered crop (custom-applied, recommended for small displays) |
| 1280 × 720 | 30 | RAW8 | 720P (default driver format) |
| 1600 × 900 | 30 | RAW10 | 16:9 |
| 1600 × 1200 | 30 | RAW8 / RAW10 | full resolution (UXGA) |

```yaml
esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: sc202cs
  resolution: "1280x720"
  pixel_format: "RGB565"
  framerate: 30
```

> **Color tuning (SC202CS).** The ISP color correction matrix (CCM) is applied on
> the **hardware ISP** at init (zero per-frame CPU cost). The loader selects the
> CCM closest to a ~5000K (daylight) white point, which fixes the washed-out look
> without the green tint earlier builds had. To skip all SC202CS color tuning
> (fall back to the flat/washed-out image), set
> `ESP_IPA_DISABLE_SC202CS_TUNING=y` in `sdkconfig_options`.

### SC2336 (MIPI 1/2-lane, 2 MP)

Resolutions enabled through the `CONFIG_CAMERA_SC2336_*` flags in
`esp_video_build.py`:

| Resolution | FPS | Format |
|------------|-----|--------|
| 640 × 480 | 50 | RAW10 |
| 800 × 800 | 30 | RAW8 / RAW10 |
| 1024 × 600 | 30 | RAW8 |
| 1280 × 720 | 30 | RAW10 |
| 1920 × 1080 | 30 | RAW10 |

```yaml
esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: sc2336
  resolution: "1280x720"
  pixel_format: "RGB565"
  framerate: 30
```

> **Tip:** For LVGL, prefer `pixel_format: RGB565`: it is the native format of the
> LVGL `canvas`, which allows a zero-copy transfer without conversion.

## Camera display in LVGL (Canvas)

`lvgl_camera_display` copies every sensor frame into an LVGL `canvas` widget.
Setup is done in **two steps**:

1. **Declare a `canvas` widget** in an LVGL page.
2. **Bind the canvas to the component** with `configure_canvas()` once LVGL is ready.

### Component declaration

```yaml
lvgl_camera_display:
  id: camera_display
  camera_id: tab5_cam        # id of the esp_cam_sensor
  canvas_id: camera_canvas   # id of the LVGL canvas widget
  update_interval: 33ms      # ~30 FPS
  # optional detection overlays:
  # face_detection_id: face_detect
  # yolo11_detection_id: yolo_detect
  # pedestrian_detection_id: ped_detect
```

| Option | Default | Description |
|--------|---------|-------------|
| `camera_id` | *(required)* | `id` of the `esp_cam_sensor` |
| `canvas_id` | *(required)* | `id` of the target LVGL `canvas` widget |
| `update_interval` | `33ms` | Refresh period (33 ms ≈ 30 FPS) |
| `face_detection_id` | – | Face detection overlay |
| `yolo11_detection_id` | – | YOLO11 detection overlay |
| `pedestrian_detection_id` | – | Pedestrian detection overlay |

### LVGL configuration (LVGL 9.5)

The `lvgl` block must use the **LVGL 9.5** fork with PPA acceleration. This fork
adds the following options:

| Option | Description |
|--------|-------------|
| `use_ppa` | Enable PPA hardware acceleration for display blits (required by the camera canvas). |
| `use_ppa_img` | Use the PPA accelerator for image widgets (hardware blit/scale of images). |
| `fps_benchmark` | Log the rendered frames-per-second for benchmarking. |
| `perf_monitor` | Enable the LVGL performance monitor overlay (CPU / FPS). |

```yaml
lvgl:
  use_ppa: true               # PPA hardware acceleration (LVGL 9.5 fork)
  use_ppa_img: true           # PPA acceleration for image widgets
  fps_benchmark: true         # log rendered FPS
  perf_monitor: true          # on-screen performance monitor
  byte_order: little_endian
  displays:
    - main_display
  touchscreens:
    - touch
  pages:
    - id: camera_page
      widgets:
        - canvas:
            id: camera_canvas
            width: 640          # = sensor resolution width
            height: 480         # = sensor resolution height
            x: 192              # on-screen position
            y: 60
            bg_color: 0x000000
            border_width: 0
            radius: 0
            pad_all: 0
```

**Important:** the `canvas` must have exactly the sensor resolution dimensions
(here 640×480) otherwise the image will be cropped or distorted.

### Activation: `configure_canvas()`

The canvas must be bound to the component **once LVGL is initialized**. Three
triggers are possible: the `lvgl` `on_idle` (the method used in production), a
`template` switch, or the page `on_load`.

**Recommended method — LVGL `on_idle`** (the canvas is bound only once):

```yaml
lvgl:
  use_ppa: true
  use_ppa_img: true
  fps_benchmark: true
  perf_monitor: true
  byte_order: little_endian
  displays:
    - main_display
  on_idle:
    - timeout: 5s
      then:
        - lambda: |-
            static bool canvas_configured = false;
            if (!canvas_configured) {
              auto canvas = id(camera_canvas);
              if (canvas != nullptr) {
                id(camera_display).configure_canvas(canvas);
                canvas_configured = true;
                ESP_LOGI("lvgl", "Canvas configured for the 640x480 camera");
              }
            }
```

**Variant — `template` switch** (enable/disable the stream on demand):

```yaml
switch:
  - platform: template
    name: "LVGL Camera Display"
    id: lvgl_display_enable_switch
    restore_mode: RESTORE_DEFAULT_OFF
    optimistic: true
    turn_on_action:
      - lambda: |-
          auto canvas = id(camera_canvas);
          auto *disp = id(camera_display);
          if (canvas != nullptr && disp != nullptr) {
            disp->configure_canvas(canvas);
            disp->set_enabled(true);     // start refreshing
          }
    turn_off_action:
      - lambda: |-
          id(camera_display).set_enabled(false);
```

## Complete minimal example

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
    components: [esp_video, esp_cam_sensor, lvgl_camera_display]
    refresh: 0s
  - source:
      type: git
      url: https://github.com/youkorr/lvgl_9.5
      ref: main
    components: [lvgl, image, font]
    refresh: always

psram:
  mode: hex
  speed: 200MHz

i2c:
  - id: bsp_bus
    sda: GPIO31
    scl: GPIO32
    frequency: 400kHz

esp_video:
  i2c_id: bsp_bus
  xclk_pin: GPIO36
  xclk_freq: 24000000
  enable_jpeg: true
  enable_isp: true
  use_heap_allocator: true

esp_cam_sensor:
  id: tab5_cam
  i2c_id: bsp_bus
  sensor_type: ov5647
  resolution: "640x480"
  pixel_format: "RGB565"
  framerate: 30

lvgl_camera_display:
  id: camera_display
  camera_id: tab5_cam
  canvas_id: camera_canvas
  update_interval: 33ms

lvgl:
  use_ppa: true
  use_ppa_img: true
  fps_benchmark: true
  perf_monitor: true
  byte_order: little_endian
  displays:
    - main_display
  on_idle:
    - timeout: 5s
      then:
        - lambda: |-
            static bool canvas_configured = false;
            if (!canvas_configured) {
              if (id(camera_canvas) != nullptr) {
                id(camera_display).configure_canvas(id(camera_canvas));
                canvas_configured = true;
              }
            }
  pages:
    - id: camera_page
      widgets:
        - canvas:
            id: camera_canvas
            width: 640
            height: 480
            x: 0
            y: 0
```

## License

The **original ESPHome integration code** in this repository (the Python
configuration/codegen and the C++ glue) is released under the
[MIT License](LICENSE), Copyright (c) 2024-2026 youkorr.

This repository also **bundles third-party source code** that keeps its own
license and is **not** relicensed as MIT:

- **Espressif** `esp_video` / `esp_cam_sensor` / `esp_ipa` / `esp_sccb_intf`
  sources — **Apache-2.0** (Copyright © Espressif Systems (Shanghai) CO LTD).
- **ESPHome** — the external-component framework these build on — **MIT/GPLv3**
  (a compiled firmware is a combined work subject to ESPHome's terms).

See [NOTICE](NOTICE) for the full breakdown and
[LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) for the Apache-2.0 text.
