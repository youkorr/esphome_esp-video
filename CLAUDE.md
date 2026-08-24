# Working notes for Claude

This file is context for a Claude session picking the repository up cold. It is
about `components/usb_display/` and the add-on that drives it, because that is
where nearly all the recent work is. Everything here was arrived at against real
hardware; the "why" paragraphs are the expensive part, not the code.

## What the project is

Put a **Home Assistant dashboard on an ESP32-P4 panel, over Wi-Fi, without
LVGL**. The board is not running Home Assistant and is not running a browser. A
machine that is on anyway — the Home Assistant server itself — renders the
dashboard in a headless Chromium and sends the picture. Touches come back up the
same socket and are replayed into that browser, so the panel behaves like the
screen of the machine doing the rendering.

The user's framing, kept verbatim because it is the goal: *"faire fonctionner
home assistant sur ces ecran bien plus simple que lvgl"*.

This is not what Espressif's `usb_extend_screen` does (that makes a board a PC
second monitor). Every other ESP32-P4 Home Assistant project found by search
(HomeTiles, GalusPeres, tommzn) is an LVGL tile UI. One project arrived at the
same architecture independently on ESP32-S3
(`ay129-35MR/Waveshare-ESP32-S3-Touch-LCD-4-Home-Assistant-Display`). What is
different here is the P4's **hardware JPEG decoder and PPA rotation**, so the
ceiling is the network rather than the CPU.

## The three pieces

```
 Home Assistant box                                  ESP32-P4 panel
 ┌───────────────────────────────┐                   ┌──────────────────────┐
 │ add-on (usb_display_panel/)   │                   │ usb_display component│
 │  run.py  supervises one       │  TCP :5000        │  feed_()   parse     │
 │          ha_send.py per panel │ ────────────────▶ │  decode    HW JPEG   │
 │                               │   JPEG rectangles │  PPA       rotate    │
 │ ha_send.py                    │                   │  draw      panel     │
 │  headless Chromium screencast │ ◀──────────────── │                      │
 │  tile diff → rectangles       │  'T' touches      │  touchscreen listener│
 │  replays touches into the page│  'S' awake/asleep │                      │
 └───────────────────────────────┘                   └──────────────────────┘
```

1. **Board firmware** — the `usb_display` ESPHome component. Also does USB
   (display over cable, HID digitizer, UAC speaker, a mass-storage drive that
   carries the sender script). The network path is an *addition* to the USB
   path, not a replacement: a board can stay plugged in for its speaker while
   the picture arrives over Wi-Fi.
2. **The sender** — `components/usb_display/ha_send.py`. Runs anywhere with
   Python + Playwright.
3. **The add-on** — `usb_display_panel/`, so the sender starts with the house
   and needs no PC. `run.py` supervises one `ha_send.py` per panel.

## The wire protocol (udisp, Espressif's)

16-byte header, `struct.Struct("<HBBHHHHI")`:

| field          | notes                                             |
|----------------|---------------------------------------------------|
| `crc16`        | sent as 0; the board validates geometry + length  |
| `type`         | `UDISP_TYPE_JPG = 3`, `UDISP_TYPE_END = 0xFF`     |
| `cmd`          | sent as 0                                         |
| `x`, `y`       | rectangle origin — **this is the whole trick**    |
| `width`,`height`| rectangle size                                   |
| `packed`       | `frame_id:10 | payload_total:22`                  |

`build_header()` / `build_heartbeat()` live in `udisp_send.py`, and `ha_send.py`
imports them rather than restating the format. **One definition of the wire
format — keep it that way.**

Every rectangle of one picture carries the same `frame_id`, so the board admits
or drops them together and never shows half an update. A `UDISP_TYPE_END` header
with no payload is the heartbeat.

## File map

```
components/usb_display/
  __init__.py        YAML schema, codegen, sdkconfig options, sleep/wake actions
  usb_display.h      class, TouchEvent, SleepAction/WakeAction templates
  usb_display.cpp    feed_() byte-stream parser, decode task, PPA, draw
  network.cpp        TCP listener, touch return channel, awake/asleep messages
  touch.cpp          touchscreen listener → HID digitizer and/or network queue
  audio.cpp          USB Audio Class speaker (Espressif's usb_device_uac)
  sender_drive.cpp   synthesised FAT12 volume carrying the sender script
  number/            volume control entity
  udisp_send.py      the wire format + a plain screen-mirroring sender
  ha_send.py         THE Home Assistant sender (screencast, diff, touch, calib)
usb_display_panel/   Home Assistant add-on wrapping ha_send.py
yaml/                validated example firmware configs (Waveshare, generic)
```

## Board-side invariants — each one is a bug that was fixed

**`feed_()` is a byte-stream parser, not a packet parser.** TCP does not
preserve write boundaries. The earlier version had four separate bugs: a read
shorter than a header was discarded, a split header was mis-parsed, the whole
rest of a read was appended past `payload_total`, and a skip consumed the whole
read. Verified against six chunk shapes (whole stream, 4096, MTU, 3-byte
dribble, random 1–64, random 1–9000); the old parser got 0/10 pictures right
under *every* shape. **If you touch `feed_()`, re-test it against chunk shapes.**

**Rate limiting is per picture and only for whole-panel frames from unpaced
transports.** `min_frame_interval_ms_` must never drop a *rectangle*: the sender
does not resend it, so a dropped rectangle stays wrong on the panel until the
30-second full redraw. The gate keys on `frame->id` (`drawing_frame_id_` /
`gated_frame_id_`) so a decision is made once per picture. The network path sets
`frame->paced = true` and is exempt — not reading the socket for a moment is
already the flow control.

**The touch queue drops the OLDEST, never the newest.** This cost 20 seconds of
apparent latency. The last event of a press is the *release*; dropping the
newest drops exactly that, and a sender left holding a button that was let go
does not act on it until some later release happens to get through. Identical
consecutive events are deduped (a finger resting still says the same thing 50×/s).

**Network tuning that matters** (`network.cpp` + `__init__.py`): `NET_READ_SIZE
= 16384`, `SO_RCVBUF = 3 ×` that, `SO_KEEPALIVE`, `TCP_NODELAY`,
`NET_RECV_TIMEOUT_S = 30` (silence is the *normal* state — a still dashboard
sends nothing; the heartbeat is what proves life), select slice 5 ms (that is
how long a contact can sit in the queue). `__init__.py` raises
`CONFIG_LWIP_TCP_WND_DEFAULT` / `SND_BUF_DEFAULT` to 28800 and
`RECVMBOX_SIZE` to 32 — **only when `port:` is present.** The receive window is
the hard ceiling for inbound throughput; sending and receiving are not
symmetric on lwip.

**Sleep/wake.** `usb_display.sleep` / `usb_display.wake` actions, registered
`synchronous=True`. The board sends `'S'` + a byte so the sender can stop
rendering for a dark screen. A sleeping panel reports no touches: the tap that
wakes it must not also press what was under the finger.

`SleepAction::play` **must** be `void play(const Ts &...) override` — the base is
`virtual void play(const Ts &...x) = 0`. Taking `Ts...` by value compiles as a
non-override and silently does nothing. There is a standalone g++ harness idea
in the history for this; `esphome config` will not catch it because **`esphome
config` validates YAML and codegen but never compiles C++.**

## Sender-side invariants

**Chromium screencast, not screenshots.** `Page.captureScreenshot` forces a full
paint/compose/encode per call, gave 0.2 pictures/s and then failed outright with
"Unable to capture screenshot" under load. `Page.startScreencast` /
`Page.screencastFrameAck` is push-based and silent when the page does not
change. Acks are sent from the loop, not from the handler. `pause()`/`resume()`
map to stop/startScreencast for panel sleep.

**The screencast is JPEG, and PNG was the server's largest single cost.**
PNG was picked on the belief that JPEG ringing would make every tile differ and
nothing would ever look unchanged. That is wrong: JPEG is a block transform, so
a block whose pixels went in identical comes out identical -- the ringing is
deterministic, not noise. Measured on a 1024x600 dashboard with one clock digit
changed, at q60 through q95, with and without chroma subsampling: exactly the
one tile holding the digit differed, never another (4:2:0 widens the unit to a
16x16 MCU, and `TILE` is a multiple of 16). The decode side is measured in the
sender: 7.2 ms a frame for PNG against 2.2 for JPEG, plus 1.8 for a `convert()`
a JPEG does not need. The browser's encode is the larger half and is *inferred*
— libpng 22.5 ms against libjpeg 1.6 on the same picture through Pillow, and
Chromium uses those libraries — so treat ~20 ms as indicative and measure it on
the real box. `--capture-quality` (default 90, `0` for PNG) is separate from
`--quality`, which is what the panel receives. Outside a tile of pure
white noise, which no encoder keeps and no camera produces, the capture encode
costs 0.7 levels of mean error.

**Tile diff, `TILE = 64`.** `changed_rectangles()` compares tiles against the
previous frame. `differing = previous != current` — **without** an
`np.any(..., axis=-1)` reduce, which was 15× slower for the same answer.

**`rect_cost_fraction(w, h)` — judge a full redraw by how many *pieces* an
update is, not by its area.** Judging by area got the common case exactly
backwards and sent a whole panel every frame to update two thirds of it. But it
is a *ratio* — a rectangle's fixed 1.5 ms over a whole-panel decode — and only
the numerator is fixed, so it cannot be one constant for every panel. It was
hard-coded at the 0.18 measured on 1024×600, where a whole panel decodes in
8.5 ms. It now comes out of the geometry: 0.176 there, unchanged in practice;
0.106 at 800×1280; 0.118 at 720×1280. `--rect-cost` overrides it.

**`MIN_RECT = 64` — do not remove this.** The P4's JPEG decoder is a DMA engine
working in 16×16 units and a sliver stalls it: a 32×128 strip returns
`ESP_ERR_TIMEOUT` rather than pixels. Slivers are the panel's own edge wherever
its size is not a multiple of the tile — 800 px is twelve tiles of 64 and a
remainder of **32**, so the rightmost column of every picture was one, and was
never drawn. Undersized rectangles are grown *backwards* so they stay inside the
panel. A panel smaller than the minimum keeps what it has.

**Urgency is attached to the frame produced AFTER the input, not to the next
frame sent.** The first attempt made latency worse (205 ms vs 105 ms) because
the free pass was consumed by a frame rendered *before* the press. Measurement
caught it; keep measuring.

**`TouchMap` works in normalised fractions and yields all 8 dihedral
candidates.** Working in pixels failed on the Tab5 by 454 px: `swap_xy` is
applied to *raw* values before calibration and the final scaling is to
`display_width_`/`display_height_`, so a portrait panel used landscape gives
pure scaling (1.838 / 0.620) with no rotation. Fractions make that disappear.

**`Injector` holds the press back until release** so a >12 px movement
(`DRAG_THRESHOLD`) becomes `mouse.wheel(-dx, -dy)` instead of a click.

**Token injection.** The long-lived token is written into local storage the way
the frontend writes it after a login, which is what gets a browser with no
keyboard past the login screen. Home Assistant frontend fields live in nested
shadow roots: `focusin` `composedPath()[0]` is the reliable target, and values
must go through the native setter plus a composed bubbling `input` event for
Lit/Polymer to notice.

**`FULL_REDRAW_SECONDS = 30.0`** — however little changes, redraw everything
this often, so a rectangle lost to a busy board or a socket hiccup does not stay
wrong forever.

**The screencast acknowledgement is the flow control, so it is paced.** Chromium
keeps about three frames in flight and then waits to be told they arrived.
Acknowledging on every turn of the loop — 125 times a second — therefore asks it
to paint and encode at its own full rate, and `--fps` then discards the surplus
*after* the cost has been paid. Measured through the sender against a page that
never settles, sent against made: at `--fps 4`, 3.9/s against **59.5**/s before,
3.7 against **5.7** after; at `--fps 10`, 9.4 against 59.7 before, 8.9 against
13.6 after. Waste falls from 84–93% to 33–35%, and it reproduces to within a
frame on a second, quite different page. `Screencast.request()` now acknowledges
only when there is somewhere to put the result — and at once, with the frame in
hand thrown away, when a press has just been replayed, because that one was
painted before the finger landed. Not free: about 4–5% fewer pictures actually
reach the panel.

**`--freeze-animations` only freezes animations, and that is less than it
sounds.** It goes through the protocol's `Animation` domain, not CSS, because
CSS cannot reach a Home Assistant card: a rule added to the document does not
cross into a shadow root — measured, 60.2 frames/s with `animation: none` in
place, which is to say no effect at all, and 60.0 under
`prefers-reduced-motion`, which a page may ignore and does. Through the domain:
55.8 → **0.2**. But a canvas driven by `requestAnimationFrame`, a camera tile
and a video do not go through that engine at all: frozen, a canvas alone still
ran at 59.7/s, and one such card on the page took a three-mover page from 57.3
to 59.2 — no effect whatever. Ack pacing helps those; freezing does not. Try it
with `--stats` and keep it only if the idle rate drops.

**`--stats` reports `made/s` and `whole` because nothing else could.**
`pictures/s`, `rectangles/s` and `KiB/s` all describe what was *sent*, and what
is sent is decided by the page; the cost that matters is paid before that
decision. `made/s` is what the browser handed over, so the gap is the waste.
`whole` counts the pictures that gave up on rectangles and sent the panel
entire — the rectangle count cannot say, since a whole panel is one rectangle
and so is a card that grew, and the two differ by a hundred kilobytes.

**The rectangle cost saturates, and that is how 0.18 broke the 800×1280
panel.** The rule gives up on rectangles when `coverage + fraction × count > 1`,
so the fraction alone sets a count past which the whole panel goes out *however
little of it changed*: at 0.18 that count is **six**. A camera tile and one or
two other moving cards reach six easily. Measured on the Guition with a camera
running: 17 of the 18 pictures in a five-second window were whole panels, 132
KiB each, at 476 KiB/s — and `0 whole` in the same window at rest, so it was
not the thirty-second redraw. This is what the `whole` counter was added to
find, and it found it in one run.

## Calibration — run it once per board, always

```
./ha_send.py --calibrate --host <ip> --width 1024 --height 600
```

There is no way to know from the sender which way a panel reports contacts: it
depends on how the controller is wired and on the `transform:` the touch screen
was given. A GT911 on one board mirrors both axes; the same part on another
swaps them; a GSL3680 on a third mirrors one. `--calibrate` draws three targets,
asks for a tap on each, and prints `--touch-rotate` / `--touch-mirror-x` /
`--touch-mirror-y`. It needs no browser and no token. Note that ESPHome runs
`listener->update()` **before** the `on_touch` trigger.

## The add-on

`usb_display_panel/` — `config.yaml`, `Dockerfile`, `run.py`, `README.md`,
`docker-compose.yml`, `esp32p4-panel.service`, `panels.example.json`, plus
`repository.yaml` at the repo root.

**Version bump trap: `config.yaml` version and `Dockerfile`'s `ARG BUNDLE` must
move together.** Docker caches `ADD` from a URL on the URL string alone, so an
add-on update shipped stale sender code. The fix is the version-carrying
`ARG BUNDLE` + a `RUN` that writes it *before* the `ADD`s, which invalidates
everything after it. Currently **1.15.0**.

`run.py` supervises one `ha_send.py` per panel: `SHARED_KEYS` lets the token,
url, port, fps, quality, capture_quality and stats be given once at the top and
inherited (a panel's own value always wins); backoff 5 → 10 → 20 → 120 s for a
run shorter than 20 s; children are killed on SIGTERM.

From inside the add-on the URL must be `http://homeassistant:8123` — a Tailscale
or `.local` name gives `ERR_NAME_NOT_RESOLVED`. `explain_unreachable()` says so.
Downloads in the Dockerfile use `curl -f`, because without it GitHub's
"429: Too Many Requests" HTML page was saved as the script.

## Hardware and measured behaviour

Three boards, all confirmed working: **Waveshare ESP32-P4-WIFI6-Touch-LCD-7B**
(1024×600), **M5Stack Tab5** (720×1280 portrait, used landscape at 270°),
**Guition 10"** (800×1280).

- touch end to end: 3–22 ms
- reactivity floor ≈ 105 ms, dominated by Chromium's repaint and screencast
  delivery, not by this pipeline
- idle traffic: 0.0 KiB/s (a still dashboard genuinely sends nothing)
- with a camera in the dashboard: 14.2 pictures/s, 1141 KiB/s
- 0 dropped frames on the board

`yaml/p4-home-assistant.yaml` is the **validated** Waveshare firmware. It
deliberately contains no `token:`, `url:` or `panels:` — those belong in the
add-on options.

## Mistakes already made — please do not repeat them

- **The on-screen keyboard: four failed rounds, then removed on request.** Each
  failure came from *adding* something. `modalAbove` (relocating the keyboard
  into a `<dialog>`) fixed a case that had been invented and broke the only two
  that existed. A native modal `<dialog>` makes everything outside it inert,
  including the top layer. The user's instruction was *"je voudrais que tu
  retire tous les clavier ont reviendra plustard"* — it is gone; do not
  reintroduce it unasked.
- A string replacement that spanned too far silently deleted `send_picture`,
  `_target_picture`, `calibrate` and `Screencast`. A test caught it
  (`NameError`). Prefer narrow, anchored edits in `ha_send.py`; it is ~46 KB.
- `queue_touch_` uses `touchscreen::TouchPoints_t` and must stay inside
  `#ifdef USE_TOUCHSCREEN`. Touch is decoupled from `CFG_TUD_HID`.
- `cc1plus` was OOM-killed compiling `esp-tflite-micro`; `compile_process_limit:
  1` under `esphome:` is the workaround -- but check two versions before
  believing it did anything. The native ESP-IDF build path read the option and
  threw it away, so ninja kept running at full parallelism: esphome/esphome
  PR 17857, merged 2026-07-26 for **ESPHome 2026.7.3**, is what forwards it to
  `idf.py` as `IDF_PY_BUILD_JOBS` -- and that variable is itself ignored in
  silence by **ESP-IDF below 5.5.5** (and by 6.0.x). Both gates have to be
  open. The tell that neither is: ninja starts another object while the killed
  one is still in the log. What does not depend on any version is dropping
  `micro_wake_word`, which is the only thing pulling `esp-tflite-micro` in at
  all.
- A 9.4 fps "mystery" turned out to be the webcam dropping frame rate in low
  light, not `max_framerate`. Check the physical world before the code.
- The user once pasted a Home Assistant long-lived token in plaintext. It was
  flagged and they were told to revoke it. Never echo a token back into a file,
  a log or a commit.

## How the user works

- **Research Espressif's sources and the user's own repositories before writing
  code.** They will check.
- **Only give configuration that has actually been validated.** They lost time
  twice to unvalidated YAML. `esphome config` at minimum; say plainly when
  something is untested.
- They write French; the codebase and its comments are English. Comments explain
  *why*, in prose, at the place the reasoning is needed.
- Windows/PowerShell is their shell for manual runs: no `\` line continuations,
  no `<chevron>` placeholders in commands you hand them.

## Open items

- The reactivity floor (~105 ms) is Chromium's, not ours. Anything below that
  needs a different capture path.
- The keyboard, if it ever comes back, should start from the two cases that
  actually exist (the dashboard search magnifier and Assist) and change nothing
  else.
- `components/esp32_camera_web_server`'s `shared_ptr` mutex fix lives on a
  branch and still needs its own upstream PR.

## Repository conventions

- Work on branch `claude/esphome-pr-outdated-mdq36w`, then merge into `main`
  with `--no-ff` and a `Merge: <what it does>` subject. That is the existing
  shape of `main`'s history.
- Commit subjects are imperative and say what the change *does for the user*,
  not which file moved.
