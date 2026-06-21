# USB-UVC — external USB camera on the ESP32-P4

The `esp_video` component can drive a **USB (UVC-class) camera** plugged into the
ESP32-P4 **USB-OTG** port, in addition to (or instead of) a MIPI-CSI sensor.
The feature is **disabled by default**: existing MIPI-CSI configurations are
unchanged and pay no overhead.

> ⚠️ **Status: not yet validated on hardware.** The full path is implemented —
> USB-Host stack + Espressif's UVC driver, enumeration, **and** an `esp_cam_sensor`
> consumer that opens the UVC node and feeds the display pipeline (see §4) — but it
> has not been flashed against a real USB camera. Validate on your board and check
> the logs.

---

## 1. Enabling

```yaml
esp_video:
  i2c_id: bsp_bus
  xclk_pin: GPIO36
  enable_jpeg: true
  enable_isp: true
  enable_uvc: true          # <-- enable the USB-UVC host
  manage_usb_host: true     # <-- esp_video owns the USB Host stack (default)
```

When `enable_uvc: true`:

1. The `CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE` flag is defined and the
   managed component **`espressif/usb_host_uvc`** (native 2.x driver, ESP32-P4
   support) is pulled in automatically — it also provides the
   `esp_private/uvc_esp_video.h` glue the driver depends on.
2. The **USB-Host stack** and the **UVC driver** are installed at startup by
   `esp_video_init()`. esp_video tries to install the USB Host Library itself,
   but the library can only be installed once per system: if another component
   (e.g. ESPHome's `usb_host`) has **already** installed it, esp_video detects
   this (`ESP_ERR_INVALID_STATE`) and **shares the existing stack** instead of
   failing — it then skips its own host-lib daemon task and lets the owner pump
   the host-lib events. So UVC works both standalone and alongside other USB
   components.
3. A connected UVC camera is **enumerated as a V4L2 device** (`/dev/videoN`).
   The UVC driver registers an asynchronous connection callback, so the camera
   is **auto-detected on hotplug** — whether or not it is plugged in at boot.

| Option | Default | Notes |
|--------|---------|-------|
| `enable_uvc` | `false` | `true` = compile and start the USB-UVC host. |
| `manage_usb_host` | `true` | `true` = esp_video installs/owns the USB Host stack. `false` = a separate ESPHome `usb_host:` component owns it and esp_video shares it (coexistence / hot-swap). When `false`, a `usb_host:` block is **required** (enforced at config validation). |

Internal parameters applied (in `esp_video_component.cpp`, adjustable if needed):
number of UVC cameras = 1, host/UVC tasks with 4096-byte stacks, priority 5,
no core affinity.

---

## 2. Hardware prerequisites

- **USB port in Host / OTG mode.** The ESP32-P4 must supply VBUS (5 V) to the
  camera. Depending on the board this requires an OTG cable/adapter and sometimes
  an external power supply — many UVC webcams draw several hundred mA.
- **A UVC-compliant camera** (the vast majority of USB webcams). The negotiated
  formats depend on the camera (often MJPEG and/or YUY2).
- Does **not** conflict with the C6 WiFi (SDIO) or with MIPI-CSI: these are
  separate peripherals.

---

## 3. Resource cost

- When off (`enable_uvc: false`): **zero** — the UVC driver compiles to an empty
  translation unit (fully `#if`-guarded), and no USB stack is linked.
- When on: two lightweight FreeRTOS tasks (USB Host Lib + UVC driver) and the
  camera frame buffers (in PSRAM). No impact on the MIPI-CSI/ISP pipeline or the
  JPEG engine.

---

## 4. Consuming the UVC stream

Enabling UVC makes the camera **available** as a V4L2 device (`/dev/video40` for
the first camera). The `esp_cam_sensor` component can now **consume that node
directly** via a `source:` selector:

```yaml
esp_cam_sensor:
  source: uvc           # mipi_csi (default) | uvc
  # device_path: /dev/video40   # optional override (defaults to the 1st UVC node)
  resolution: VGA       # negotiated against the camera; falls back to VGA
```

How it works (`esp_cam_sensor_camera.cpp`, `start_streaming_uvc_()` /
`capture_frame_uvc_()`):

1. Opens the UVC node (`device_path:` or `/dev/video40` by default) instead of
   the MIPI-CSI node — independent of the MIPI/ISP pipeline, so a UVC-only board
   needs no MIPI sensor.
2. Enumerates the camera's formats (`VIDIOC_ENUM_FMT`) and picks **YUYV** when
   available (preferred), otherwise **MJPEG**.
3. Negotiates the format/resolution (`VIDIOC_S_FMT` → `VIDIOC_G_FMT`), maps the
   driver's frame buffers (`VIDIOC_REQBUFS`/`QUERYBUF`/`mmap`, MMAP mode), and
   streams (`STREAMON`).
4. Each frame is **converted to RGB565** into the existing display buffer pool,
   so `acquire_buffer()` / `release_buffer()` / `get_image_*()` (and therefore
   `lvgl_camera_display`) work unchanged.

> **YUYV vs MJPEG.** YUYV is converted in software (BT.601) — no extra
> dependency, ideal at VGA. **MJPEG decode is opt-in**: add
> `-DUVC_ENABLE_MJPEG_DECODE` to `build_flags` to enable the ESP32-P4 hardware
> JPEG decoder path (experimental). Without it, a camera that only offers MJPEG
> is rejected at `start_streaming` with a clear log message — pick a YUYV mode
> or enable the flag.

---

## 5. Expected startup logs

```
[esp_video] USB-UVC host enabled: external USB cameras will appear as /dev/videoN
[esp_video_init] Installing USB Host
[esp_video_init] USB Host installed
```
If the USB Host stack was already installed by another component, you'll see a
warning instead and esp_video shares it:
```
[esp_video_init] USB Host already installed by another component; UVC will share the existing host stack
```
When the camera is plugged in, the UVC driver enumerates it and creates the
V4L2 device.

---

## 6. Troubleshooting

| Symptom | Hint |
|---------|------|
| Build: errors on `usb/uvc_host.h` or `uvc_esp_video.h` | `usb_host_uvc` version mismatch — adjust `ref="2.4.1"` in `components/esp_video/__init__.py`. |
| Camera not detected | Check Host/OTG mode and the camera's VBUS (5 V) power. |
| `Failed to install USB Host driver` | A real install error (not a double-install — that case is handled by sharing). Check the port is in host mode. |
| `USB Host already installed by another component` | Informational, not an error: esp_video is sharing an existing USB Host stack (e.g. ESPHome's `usb_host`). |
| `manage_usb_host: false` rejected at validation | Add a `usb_host:` component (it owns the host stack esp_video shares), or set `manage_usb_host: true`. |
| `UVC: camera only offers MJPEG but hardware decode is disabled` | Add `-DUVC_ENABLE_MJPEG_DECODE` to `build_flags`, or use a camera/mode that exposes YUYV. |
| `UVC: open(/dev/video40) failed` | No UVC camera enumerated. Check it is plugged in, `enable_uvc: true`, and the port is in host/OTG mode. |

---

## 7. References

- UVC Host driver: <https://components.espressif.com/components/espressif/usb_host_uvc>
- ESP-IDF USB Host: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/api-reference/peripherals/usb_host.html>
- Driver in this component: `src/device/esp_video_usb_uvc_device.c`
