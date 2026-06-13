# Gap analysis — official ESPHome camera framework vs this `esp_video` stack

Purpose: decide precisely what is worth contributing upstream, and build the body
of the ESPHome discussion issue. It compares the **official** ESPHome modular
camera framework ([PR #7639](https://github.com/esphome/esphome/pull/7639),
merged 2025-11-24, by `camera` codeowner **DT-art1**) with the components in this
repository.

> Items marked **(verify)** still need confirmation on real hardware / from the
> upstream source — treat them as best-effort from the PR description and docs.

---

## What the official framework already provides

- A **modular camera pipeline** plugging into ESPHome's `camera` component, with
  multiple processors and **multiple outputs** to different requesters.
- **MIPI-CSI** capture on ESP32-P4.
- Hardware **JPEG** encoding; **ISP** support.
- A Home Assistant **camera** entity (native API) — same `camera::Camera` base.
- Overlay graphics via ESPHome's display rendering.
- Tested sensors: **OV5647, OV2710, SC202CS**, plus classic ESP32 cameras.
- Custom pipeline — **does not** use Espressif's `esp_video` framework.

## What this `esp_video` stack provides

- Built on Espressif's **official `esp-video-components`** (V4L2), maintained by
  Espressif.
- Full **ISP + IPA**: AWB, CCM, JSON-tunable image quality pipeline.
- Hardware **JPEG and H.264** encoders (V4L2 M2M).
- **USB-UVC** host (external USB cameras) → `/dev/video40`+.
- Auto-detected sensors: **SC202CS, OV5647, OV02C10, SC2336**.
- **PPA** transforms (crop / resize / rotate / mirror).
- Standard **V4L2** multi-consumer interface (HA camera, LVGL, detection).
- **LVGL canvas** live view (`lvgl_camera_display`) on the official `lvgl` (9.5).
- Shares the ESPHome **I²C** bus (no SCCB conflict).

---

## Side-by-side

| Capability | Official framework (#7639) | This `esp_video` stack |
|------------|----------------------------|------------------------|
| Plugs into `camera` component / HA entity | ✅ | ✅ (`esp_video_camera`) |
| MIPI-CSI on ESP32-P4 | ✅ | ✅ |
| Hardware **JPEG** | ✅ | ✅ |
| Hardware **H.264** | ❌ (verify) | ✅ |
| **ISP** | ✅ | ✅ |
| **IPA** image tuning (AWB/CCM, JSON) | ❌ / minimal (verify) | ✅ |
| **USB-UVC** external cameras | ❌ (verify) | ✅ |
| OV5647 | ✅ | ✅ |
| SC202CS | ✅ | ✅ |
| OV2710 | ✅ | ❌ |
| **SC2336** | ❌ (verify) | ✅ |
| **OV02C10** | ❌ (verify) | ✅ |
| **PPA** crop/resize/rotate/mirror | partial (verify) | ✅ |
| **LVGL canvas** live view | ❓ (verify) | ✅ |
| Backend | custom pipeline | Espressif official V4L2 |

---

## Unique value of this stack (the upstream argument)

The pieces the official framework does **not** appear to cover, and that justify
an `esp_video`-backed **platform alongside** it:

1. **USB-UVC** external cameras.
2. Sensors **SC2336** and **OV02C10** (auto-detected).
3. Hardware **H.264** encoding.
4. Full **IPA** image-quality tuning (JSON-configurable).
5. The **LVGL canvas** live-view consumer *(verify whether the official framework
   feeds an LVGL canvas; if it does, this point drops)*.

Everything else (HA camera, MIPI, JPEG, ISP, OV5647/SC202CS) **overlaps** — so
those are not, by themselves, a reason to upstream a second backend.

---

## Recommendation

1. **Test the official framework on real P4 hardware** with OV5647/SC202CS to
   confirm the **(verify)** rows above (especially H.264, UVC, SC2336/OV02C10,
   LVGL canvas).
2. **Keep all components** in this repo as external components regardless — they
   work today and serve users now.
3. For upstream, propose an **`esp_video` camera platform** that coexists with the
   official framework (`camera::Camera` subclass), led by the confirmed unique
   features (1–5). Open the discussion issue first (body ≈ this document +
   `UPSTREAMING.md` §5).
4. If a confirmed gap is small (e.g. only SC2336/OV02C10), the cheaper path may be
   to **add those sensors to the official framework** instead of a whole backend —
   decide after step 1.
