# PR description — paste into the esphome/esphome pull request form

> Target: base `esphome/esphome` branch **`dev`**, head = your fork's
> `esp_video_camera` branch. Adjust the links/checkboxes before submitting.

---

## What does this implement/fix?

Adds **`esp_video_camera`**, a new `camera` platform for the **ESP32-P4** that
exposes Espressif's **`esp_video`** (V4L2) pipeline to Home Assistant as a native
camera entity.

It is meant to **complement** the modular camera framework from #7639, not
replace it: it is an `esp_video`-backed backend that brings features the current
framework does not cover —

- full **ISP + IPA** image tuning (AWB / CCM, JSON-configurable);
- hardware **JPEG** (and the esp_video stack also exposes H.264);
- **USB-UVC** external cameras (USB-OTG port);
- extra auto-detected MIPI sensors: **SC2336**, **OV02C10**, on top of
  OV5647 / SC202CS.

Design:

- A **single component** that both initialises the MIPI-CSI pipeline (with an
  optional USB-UVC host) and serves the `camera` entity. It takes an `i2c_id`
  for the sensor SCCB bus.
- Frame source is selectable with `device:` — `jpeg` (hardware encoder, any
  auto-detected MIPI sensor), `uvc`/`uvc0`..`uvc9`, `csi`, or `/dev/videoN`.
- All Espressif dependencies are pulled through the **IDF component manager**
  (`espressif/esp_video`, `espressif/esp_cam_sensor`, `espressif/usb_host_uvc`) —
  **no vendored sources**.

## Types of changes

- [x] New feature (new component / camera platform)

## Related issue or discussion

<!-- link the feature-request / Discord discussion that got maintainer buy-in -->
Fixes/relates to: <link>

## Pull request in esphome-docs with documentation

<!-- open a companion PR adding components/esp_video_camera.rst and link it here -->
esphome/esphome-docs#<NNN>

## Test Environment

- [x] ESP32-P4 (ESP-IDF)
- [ ] Other

## Example entry for `config.yaml`

```yaml
i2c:
  - id: bus_a
    sda: GPIO31
    scl: GPIO32
    frequency: 400kHz

esp_video_camera:
  name: ESP32-P4 Camera
  i2c_id: bus_a
  device: jpeg          # jpeg | uvc / uvc0..uvc9 | csi | /dev/videoN
  resolution: auto
  jpeg_quality: 10
  max_framerate: 10
```

## Checklist

- [x] The code change is tested and works on ESP32-P4. <!-- confirm on hardware -->
- [x] Added a test config under `tests/components/esp_video_camera/`.
- [x] Updated `CODEOWNERS`.
- [x] Code is in English and passes `pre-commit` (clang-format / ruff).
- [ ] Companion documentation PR opened in `esphome/esphome-docs`.

> Note to reviewers: `esp_video` is Espressif's official V4L2 camera framework.
> This platform is complementary to #7639; happy to align naming/structure with
> the existing `camera` platforms if preferred.
