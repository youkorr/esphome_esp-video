# USB-UVC — external USB camera on the ESP32-P4

The `esp_video` component can drive a **USB (UVC-class) camera** plugged into the
ESP32-P4 **USB-OTG** port, in addition to (or instead of) a MIPI-CSI sensor.
The feature is **disabled by default**: existing MIPI-CSI configurations are
unchanged and pay no overhead.

> ⚠️ **Status: not tested on hardware.** The code compiles the USB-Host stack +
> Espressif's UVC driver and starts enumeration; validate on your board.

---

## 1. Enabling

```yaml
esp_video:
  i2c_id: bsp_bus
  xclk_pin: GPIO36
  enable_jpeg: true
  enable_isp: true
  enable_uvc: true        # <-- enable the USB-UVC host
```

When `enable_uvc: true`:

1. The `CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE` flag is defined and the
   managed component **`espressif/usb_host_uvc`** (native 2.x driver, ESP32-P4
   support) is pulled in automatically — it also provides the
   `esp_private/uvc_esp_video.h` glue the driver depends on.
2. The **USB-Host stack** and the **UVC driver** are installed at startup by
   `esp_video_init()` (esp_video owns the USB Host Lib: `init_usb_host_lib = true`).
3. A connected UVC camera is **enumerated as a V4L2 device**: `/dev/videoN`.

| Option | Default | Notes |
|--------|---------|-------|
| `enable_uvc` | `false` | `true` = compile and start the USB-UVC host. |

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

Enabling UVC makes the camera **available** as a V4L2 device (`/dev/videoN`,
first UVC camera = `/dev/video40`). The USB-Host stack is installed by
`esp_video` itself (`init_usb_host_lib = true`) — there is **no separate ESPHome
`usb_host` component to add**, and you must not install one elsewhere or the USB
Host Lib install will fail (see §6).

To actually expose the UVC stream, use a consumer that opens `/dev/videoN`:

- **Home Assistant** — the **`esp_video_camera`** component publishes the UVC
  stream as a native HA `camera` entity. Just set `device: uvc`:

  ```yaml
  esp_video:
    i2c_id: bsp_bus
    enable_uvc: true        # brings up the USB host + UVC driver

  esp_video_camera:
    name: "USB Camera"
    device: uvc             # /dev/video40 (uvc1..uvc9 for additional cameras)
    resolution: "1280x720"
    max_framerate: 15
  ```

> **Note:** the `esp_cam_sensor` component (the one referenced by `face2face`'s
> `camera_id:`) is **MIPI-CSI specific** — it does not drive the UVC node. Use
> `esp_video_camera` (above) to consume a UVC camera.

---

## 5. Expected startup logs

```
[esp_video] USB-UVC host enabled: external USB cameras will appear as /dev/videoN
[esp_video_init] Installing USB Host
[esp_video_init] USB Host installed
```
When the camera is plugged in, the UVC driver enumerates it and creates the
V4L2 device.

---

## 6. Troubleshooting

| Symptom | Hint |
|---------|------|
| Build: errors on `usb/uvc_host.h` or `uvc_esp_video.h` | `usb_host_uvc` version mismatch — adjust `ref="2.4.1"` in `components/esp_video/__init__.py`. |
| Camera not detected | Check Host/OTG mode and the camera's VBUS (5 V) power. |
| `Failed to install USB Host driver` | Another component already installed the USB Host Lib (do **not** add a separate `usb_host` component — `esp_video` owns it), or the port is not in host mode. |
| No image in Home Assistant | Use `esp_video_camera` with `device: uvc` to consume `/dev/video40` (see §4). Check the camera negotiates **MJPEG**. |
| No image in `face2face` | The UVC node is not consumed by `esp_cam_sensor` (MIPI-CSI only); use `esp_video_camera` instead (see §4). |

---

## 7. References

- UVC Host driver: <https://components.espressif.com/components/espressif/usb_host_uvc>
- ESP-IDF USB Host: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/api-reference/peripherals/usb_host.html>
- Driver in this component: `src/device/esp_video_usb_uvc_device.c`
