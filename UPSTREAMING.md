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

## ⚠️ Important reality check (2026-06) — ESPHome already has a camera framework

Before reworking this stack for upstream, note that ESPHome **already merged** a
**"Modular Camera Framework with MIPI-CSI support"** for ESP32-P4
([PR #7639](https://github.com/esphome/esphome/pull/7639), merged 2025-11-24, by
**DT-art1**, a `camera` codeowner). It already provides, **in official ESPHome**:

- MIPI-CSI camera on ESP32-P4, hardware **JPEG** encoding, **ISP**;
- a Home Assistant **camera** entity;
- tested sensors: **OV5647, SC202CS, OV2710** + classic ESP32 cameras.

Crucially it does **not** use Espressif's `esp_video` — it is a custom modular
pipeline. Espressif's `esp-video-components` (esp_video + esp_cam_sensor +
esp_ipa) remains the *only* official Espressif camera path, but ESPHome
deliberately chose a different architecture.

**Consequence for this plan:** upstreaming this `esp_video`-based stack as a
**parallel** framework is likely to be **rejected as redundant**. The productive
path is to **extend the existing official framework** where it falls short, not
replace it. The unique value of this repo that the official framework does *not*
cover today:

- sensors not yet supported upstream: **SC2336**, **OV02C10**;
- **USB-UVC** cameras;
- the **LVGL canvas** live-view consumer (verify whether the official framework
  feeds an LVGL canvas).

**Recommended next step:** do a gap analysis (official framework vs this repo) on
real hardware, then contribute the missing pieces to DT-art1's framework. The
phased plan below stays as a fallback if the maintainers prefer an `esp_video`
backend, but treat it as Plan B.

### Decision: keep `esp_video` — pitch it as a *platform*, not a competing framework

We want to **keep the `esp_video` stack** because it has real advantages over a
hand-rolled pipeline. The way to reconcile "keep `esp_video`" with "get it
upstream" is to plug it into ESPHome's **existing `camera` component** as an
**alternative platform/backend** (subclassing `camera::Camera`), the same way
`esp32_camera` and the modular MIPI framework are platforms. `esp_video_camera`
already does exactly this, so it is architecturally compatible — not a rival.

**Advantages of `esp_video` to lead the upstream pitch with:**

- Built on Espressif's **official** V4L2 framework (`esp-video-components`),
  maintained by Espressif and tracking new sensors / ISP features.
- Full **ISP + IPA**: auto white balance, CCM, JSON-tunable image pipeline →
  better image quality than a minimal pipeline.
- Hardware **JPEG** *and* **H.264** encoders (V4L2 M2M devices).
- **USB-UVC** host support (external USB cameras) — not in the official framework.
- Broad, **auto-detected** sensor set: SC202CS, OV5647, OV02C10, **SC2336**, …
  (incl. sensors the official framework does not cover yet).
- **PPA** hardware transforms (crop / resize / rotate / mirror).
- Standard **V4L2** interface → multiple consumers (HA camera, LVGL, detection)
  off one pipeline.
- Shares the ESPHome **I²C** bus (no SCCB conflict).

**Implication:** the goal is *coexistence*, not replacement. Open the §5 issue
framed as "an `esp_video`-backed camera platform alongside the existing
framework", leading with the advantages above (especially ISP/IPA quality,
H.264, USB-UVC and the extra sensors). Keep all three core components; they plug
into the official `camera` component rather than re-implementing it.

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

No architectural blocker. **This is Phase 2** (built on the Phase 1 core).

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

This is part of the **Phase 1 core** and needs the maintainers to agree on the
layering before coding. Drop `mirror`/`rotation`/`crop` PPA features from the
first PR if they slow review; add them back later.

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

**Compatibility audit (static, done):**
- ✅ The official `lvgl` provides a **`canvas`** widget (`widgets/canvas.py`),
  same widget typing as the fork → the `id(canvas)` → `configure_canvas()`
  wiring is unchanged.
- ✅ Every LVGL symbol the component uses is **standard LVGL 9.5** public API
  (`lv_draw_buf_init`, `lv_draw_buf_set_flag`, `lv_canvas_set_draw_buf`,
  `lv_image_set_src`, `lv_obj_check_type`/`lv_canvas_class`, `lv_obj_invalidate`).
- ❌ Only YAML change required: **remove** the fork-only `lvgl:` keys
  `use_ppa`, `use_ppa_img`, `fps_benchmark`, `perf_monitor` (not valid in the
  official component). Valid keys used (`byte_order`, `displays`,
  `touchscreens`, `pages`, `on_idle`) are all official.

**Work to do (small):**
- Depend on the **official** `lvgl` (drop the fork); require **ESPHome ≥ 2026.4**.
- English translation + `tests/` + docs page.
- **Validate on hardware:** with mainline LVGL 9.5 PPA there is a known
  draw-buffer **alignment** caveat (lvgl/lvgl#9868) with a "disable PPA"
  workaround — confirm the zero-copy canvas feed is unaffected, since our buffer
  is the camera buffer, not an LVGL-allocated draw buffer.

So `lvgl_camera_display` is upstreamable as part of the **Phase 1 core** (§4); no
fork, no PPA-blit rewrite needed.

---

## 4. Phased PR plan

**Phase 1 is the functional core: `esp_video` + `esp_cam_sensor` +
`lvgl_camera_display` must go together — without all three, nothing captures or
displays.** These three are interdependent (`esp_video` brings up the pipeline,
`esp_cam_sensor` configures the sensor and provides frames, `lvgl_camera_display`
shows them), so they are developed and landed as **one coordinated set**.
`esp_video_camera` (the Home Assistant entity) is an add-on built **on top** and
comes after.

1. **Phase 0 — discussion.** Open an issue on `esphome/esphome` (text in §5) to
   get maintainer buy-in for ESP32-P4 `esp_video` support and the component
   layering (§2).
2. **Phase 1 — core stack (the three foundational components):**
   - `esp_video` — pipeline init via managed `espressif/esp_video` (no vendoring).
   - `esp_cam_sensor` — sensor config + frame provider via managed
     `espressif/esp_cam_sensor` (drop the vendored drivers, `imlib`, etc.).
   - `lvgl_camera_display` — LVGL canvas consumer on the **official** `lvgl`
     (ESPHome ≥ 2026.4, no fork — §3).

   All three: English, `tests/`, docs pages, `CODEOWNERS`. Coordinated set
   (one branch; may be split into reviewable PRs but landed together since they
   are non-functional apart).
3. **Phase 2 — `esp_video_camera`.** The Home Assistant `camera` entity, built on
   the Phase 1 pipeline. Managed deps, English, `tests/`, docs page.

Each phase = one ESPHome PR + one `esphome-docs` PR.

---

## 5. Draft text for the upstream discussion issue

> **Title:** An `esp_video`-backed camera platform for ESP32-P4 (ISP/IPA, H.264, USB-UVC), alongside the existing MIPI framework
>
> **Body:** Following the modular camera framework in #7639, I'd like to add an
> **`esp_video`-backed camera platform** for ESP32-P4 — *complementing*, not
> replacing it — plugging into the existing `camera` component
> (`camera::Camera`). It is built on Espressif's official `esp-video-components`
> (V4L2) and brings: full **ISP + IPA** image tuning, hardware **JPEG and H.264**,
> **USB-UVC** host cameras, and extra auto-detected sensors (**SC2336**,
> **OV02C10**) on top of OV5647/SC202CS. Proposed in two phases:
>
> **Phase 1 — the functional core (three interdependent components, landed
> together; nothing works without all three):**
> 1. `esp_video`: brings up the P4 camera pipeline (CSI / ISP / JPEG).
> 2. `esp_cam_sensor`: sensor configuration + frame provider.
> 3. `lvgl_camera_display`: LVGL canvas live view, on the official `lvgl`
>    component (ESPHome 2026.4+ ships LVGL 9.5 + PPA — no fork needed).
>
> **Phase 2 — on top of the core:**
> 4. `esp_video_camera`: a `camera::Camera` platform serving JPEG/MJPEG (hardware
>    JPEG encoder for any auto-detected MIPI sensor, or a USB-UVC camera) to the
>    native API — analogous to `esp32_camera`.
>
> All third-party code pulled via the IDF component manager (no vendored blobs).
> Looking for guidance on the component layering before I open the PRs.

---

## 6. Status checklist

- [ ] §0 issue opened on `esphome/esphome` (layering agreed with maintainers)
- [ ] Phase 1 core — `esp_video`: pipeline on managed `espressif/esp_video` (no vendoring)
- [ ] Phase 1 core — `esp_cam_sensor`: on managed `espressif/esp_cam_sensor` (drop vendored drivers/imlib)
- [ ] Phase 1 core — `lvgl_camera_display`: on official `lvgl` (ESPHome ≥ 2026.4), validated on hardware
- [ ] Phase 1 core — English, `tests/`, docs pages, `CODEOWNERS` for the three
- [ ] Phase 2 — `esp_video_camera`: Home Assistant entity on top of the core

> Note: the current external-component versions in this repo stay as-is and keep
> working for users while the upstream phases proceed.
