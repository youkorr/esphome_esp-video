# Upstreaming plan — getting the camera stack into ESPHome

Goal: get the three components of this repository accepted into **upstream
ESPHome** (`esphome/esphome`), not just shipped as external components:

- `esp_video_camera` — Home Assistant `camera` entity (native API).
- `esp_cam_sensor` — MIPI-CSI sensor configuration + frame provider.
- `lvgl_camera_display` — LVGL canvas live view.

This document is the reference for that effort: per-component status, the
blockers, the **solution for each blocker**, the proposed architecture, the
phased PR plan, and the text to open the upstream discussion.

---

## 0. Hard rules ESPHome enforces (and how we meet them)

| Rule | Consequence for us |
|------|--------------------|
| **No vendored third-party source / no prebuilt blobs** (e.g. `libesp_ipa.a`, copied Espressif `.c`). | Pull everything through the IDF component manager: `add_idf_component("espressif/esp_video", ...)`, `espressif/esp_cam_sensor`, `espressif/esp_ipa`. These are **published managed components** (`esp_cam_sensor` is at v1.4.0 on the ESP Component Registry), so this is feasible. |
| **English only** in code, comments and docs. | Translate the French comments/docs of the upstreamed components. |
| **CI must pass**: `clang-format`, `clang-tidy`, `ruff`, compile tests. | Add `tests/` YAML per component; format the code. |
| **No forks of other ESPHome components.** | Resolved: ESPHome 2026.4 ships LVGL 9.5 + PPA, so `lvgl_camera_display` now uses the **official** `lvgl` — no fork (see §3). |
| **Small, focused PRs**; new platforms need a maintainer **discussion first**. | Phase the work (§4). Open an issue before the big PRs. |
| Companion **docs PR** to `esphome/esphome-docs`. | One docs page per component. |

---

## 1. `esp_video_camera` — ✅ directly upstreamable

The closest to ready. It mirrors the existing `esp32_camera` component: a
`camera::Camera` subclass that serves JPEG/MJPEG frames to the native API.

**Work to do**
- Replace the dependency on the vendored `esp_video` with the managed
  component: `add_idf_component(name="espressif/esp_video", ref="<latest>")`.
- Resolve V4L2 device nodes from the managed component's public headers
  (`esp_video_device.h`, `linux/videodev2.h`).
- Translate comments/strings to English.
- Add `tests/test.esp32-p4-idf.yaml` (compile test).
- `CODEOWNERS` entry + docs page.

No architectural blocker. **This is Phase 1.**

---

## 2. `esp_cam_sensor` — ⚠️ upstreamable after a rework + design sign-off

The sensor **driver** is the managed `espressif/esp_cam_sensor` component, so the
vendoring goes away. The friction is the ESPHome wrapper (`MipiDSICamComponent`):
it carries a lot of logic (RGB565 zero-copy streaming, PPA transforms, `imlib`
drawing, ISP controls) and **overlaps** with `esp_video_camera` — both currently
open and drive the single CSI pipeline.

**Solution: layer the architecture** (propose to maintainers)

```
            espressif/esp_video + espressif/esp_cam_sensor  (managed IDF deps)
                                   │
                    ┌──────────────┴──────────────┐
                    │   esp_video (ESPHome core)   │  pipeline + sensor config,
                    │   owns the CSI stream,       │  single owner of /dev/videoN,
                    │   exposes frames + controls  │  publishes frames to consumers
                    └──────────────┬──────────────┘
                       ┌───────────┴───────────┐
            esp_video_camera                 lvgl_camera_display
            (HA camera entity)               (LVGL canvas consumer)
```

- One component **owns** the CSI pipeline and the sensor (drop the "two
  components each open the CSI" model — it is what forces the "pick one
  consumer" limitation today).
- `esp_video_camera` and `lvgl_camera_display` become **consumers** of that
  frame source. This also removes the single-CSI either/or limitation.
- `imlib` drawing: drop from the upstream version (or gate behind an option) —
  it is an OpenMV import that upstream will not want vendored.

This is **Phase 2** and needs the maintainers to agree on the layering before
coding. Drop `mirror`/`rotation`/`crop` PPA features from the first PR if they
slow review; add them back later.

---

## 3. `lvgl_camera_display` — ✅ blocker resolved by ESPHome 2026.4

**This was the hard blocker; it is now gone.** As of **ESPHome 2026.4.0
(April 2026)** the official `lvgl` component migrated to **LVGL 9.5.0** and added
**native PPA acceleration on the ESP32-P4**. The whole reason the
`youkorr/lvgl_9.5` fork existed — providing `use_ppa` — is therefore covered by
mainline ESPHome.

**And the component never actually called PPA itself.** Its rendering path uses
only **standard LVGL 9 public APIs**:

- `lv_draw_buf_init` / `lv_draw_buf_set_flag(LV_IMAGE_FLAGS_MODIFIABLE)`
- `lv_canvas_set_draw_buf` (canvas) / `lv_image_set_src` (image)
- `lv_obj_invalidate`

It feeds the camera's RGB565 PSRAM buffer into the canvas **zero-copy** (pointer
swap) and lets LVGL render/flush it. PPA was only ever a *global* LVGL render
optimization (now in mainline), not a call made by this component.

**Work to do (small):**
- Depend on the **official** `lvgl` (drop the fork); require **ESPHome ≥ 2026.4**.
- English translation + `tests/` + docs page.
- **Validate on hardware:** with mainline LVGL 9.5 PPA there is a known
  draw-buffer **alignment** caveat (lvgl/lvgl#9868) with a "disable PPA"
  workaround — confirm the zero-copy canvas feed is unaffected, since our buffer
  is the camera buffer, not an LVGL-allocated draw buffer.

So `lvgl_camera_display` is upstreamable on top of the Phase 2 frame source; no
fork, no PPA-blit rewrite needed.

---

## 4. Phased PR plan

1. **Phase 0 — discussion.** Open an issue on `esphome/esphome` (text in §5) to
   get maintainer buy-in for ESP32-P4 `esp_video` camera support and the
   layering in §2.
2. **Phase 1 — `esp_video_camera`.** Managed deps, English, `tests/`, docs page.
   Self-contained, lands first.
3. **Phase 2 — `esp_video` core + `esp_cam_sensor`.** The layered pipeline/sensor
   component (§2). Lands after the §0 design is agreed.
4. **Phase 3 — `lvgl_camera_display`.** On the **official** `lvgl` (ESPHome
   ≥ 2026.4, no fork — §3), on top of Phase 2's frame source.

Each phase = one ESPHome PR + one `esphome-docs` PR.

---

## 5. Draft text for the upstream discussion issue

> **Title:** ESP32-P4 camera support via Espressif `esp_video` (MIPI-CSI + USB-UVC)
>
> **Body:** I'd like to contribute ESP32-P4 camera support to ESPHome, built on
> Espressif's managed `esp_video` / `esp_cam_sensor` components (V4L2 on P4).
> Proposed in phases:
> 1. `esp_video_camera`: a `camera::Camera` platform that serves JPEG/MJPEG from
>    the hardware JPEG encoder (any auto-detected MIPI sensor) or a USB-UVC
>    camera, to the native API — analogous to `esp32_camera`.
> 2. A core `esp_video` component that owns the CSI pipeline + sensor config and
>    exposes frames to consumers.
> 3. An LVGL canvas consumer for on-device live view, using the official `lvgl`
>    component (ESPHome 2026.4+ ships LVGL 9.5 + PPA — no fork).
>
> All third-party code pulled via the IDF component manager (no vendored blobs).
> Looking for guidance on the component layering before I open the PRs.

---

## 6. Status checklist

- [ ] §0 issue opened on `esphome/esphome`
- [ ] Phase 1: `esp_video_camera` upstream-ready branch
- [ ] Phase 1: docs page drafted
- [ ] Phase 2: layering agreed with maintainers
- [ ] Phase 2: `esp_video` + `esp_cam_sensor` reworked onto managed components
- [ ] Phase 3: `lvgl_camera_display` on official `lvgl` (ESPHome ≥ 2026.4), validated on hardware

> Note: the current external-component versions in this repo stay as-is and keep
> working for users while the upstream phases proceed.
