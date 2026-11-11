# Experimental direct JPEG decode into the panel's buffers

This firmware option is off by default. It is intended first for a Guition
JC8012P4A1 at 800x1280, RGB565, rotation 0. It uses the panel-buffer approach
from ppa-alternative, through the MIPI DSI driver in youkorr/lvgl_9.5. It does
not add an LVGL renderer or a PPA rotation to Portall.

A whole-screen JPEG can be decoded directly into a free DPI frame buffer.
Presenting it changes the buffer the display reads, avoiding the normal
full-picture DMA copy. A rectangle still decodes into Portall's usual decode
buffer and follows its normal blit path. Before a direct frame can change the
display's target, every earlier rectangle blit must finish. Thus a rectangle
continues to update the latest picture, preserving its unchanged pixels.

The driver needs **three** buffers. Its color-transfer-done event fires
immediately on a pointer swap and cannot authorize reusing the old buffer.
The replaced buffer is withheld for two later frame-buffer-complete events:
one ISR may already have selected the old buffer before the swap. The CPU
cache is cleaned and invalidated before JPEG DMA writes a buffer. Failed
decodes cancel their leases, and a buffer wait is bounded at 100 ms; when no
buffer is available Portall keeps its existing decode-and-copy path.

## Test on the Guition

Keep the private settings, audio, touchscreen, networking and other options
of your working YAML. Only the following entries change. These are prototype
branches, to test before merging; a normal add-on update does not install them.

In the existing external Portall source, use:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
      ref: codex/portall-panel-buffers
    components: [portall, portall_bt]
    refresh: 0s
  - source:
      type: git
      url: https://github.com/youkorr/lvgl_9.5
      ref: codex/portall-direct-buffers
    components: [mipi_dsi]
    refresh: 0s
  # Keep the other external components already in your working YAML.
```

The existing display becomes:

```yaml
display:
  - platform: mipi_dsi
    model: JC8012P4A1
    id: main_screen
    pixel_mode: 16
    color_depth: 16
    framebuffers: 3
    update_interval: never
    auto_clear_enabled: false
```

Add `panel_buffers: true` to the existing `portall:` block. Keep its
`display_id`, 800x1280 size, rotation 0, network, touch and speaker settings.
Compile and flash with your own secrets. The public YAML's cleared API key
must be replaced by your working private configuration for a full build.

The initial mode requires native dimensions divisible by 16, RGB565 and no
rotation, render scaling, LVGL, canvas, big-endian output, display writer or
test card. Two additional native frame buffers cost about **3.91 MiB of
PSRAM** at 800x1280, on top of Portall's existing decode buffers and queues.

At boot expect:

```text
panel_buffers: whole JPEGs decode into the panel's three buffers; rectangles use normal blits
```

During playback the usual decode/draw statistics are followed by:

```text
panel buffers: ... whole JPEGs decoded directly, ... ms/s waiting for buffers and earlier blits
```

A missing API, mismatched panel or incompatible geometry logs a warning and
retains the old copy path. A zero direct-JPEG count means that no full native
picture took the new path; small changed windows still use normal copies.

For an A/B comparison, keep this driver and `framebuffers: 3` in **both**
builds and change only `panel_buffers: false` / `true`. Use the same clip,
resolution, quality, frame limit and running services. Collect the add-on's
`Media:` and `pictures/s` lines and the board's decode/draw/direct-buffer
lines after the stream settles. A known 50-fps source with a link limit of
50 is more useful for finding spare capacity than a film that supplies only
25 fps. A source's cadence remains the ceiling; this option adds no frames.

## Validation and limits

- The Guition 800x1280 video firmware builds with ESPHome 2026.12.0-dev and
  ESP-IDF 5.5.6 using the two modified components.
- `tools/checkdirectpanel.py` compiles the shipped Portall parser, decoder,
  draw pipeline and the driver's real ownership state on a workstation.
  A scanner runs on another thread, including delayed ISR notifications.
  Tests mix full pictures, partial rectangles and failed decodes and check
  order, unchanged pixels and buffer reuse, with the option on and off.
- Deliberately releasing a buffer one completion too soon or removing the
  rectangle-to-full-frame barrier must fail those tests.
- The existing pipeline and display-API regression tests still pass.

These are build and correctness checks. Smoothness, memory-bus contention,
tearing and the performance gain must still be measured on the physical
Guition. The ESPHome/LVGL speedups in ppa-alternative are a different workload.

Run the workstation checks with:

```sh
python tools/checkdirectpanel.py --driver /path/to/lvgl_9.5 --esphome /path/to/esphome
python tools/checkpipeline.py --esphome /path/to/esphome --lvgl /path/to/lvgl
```
