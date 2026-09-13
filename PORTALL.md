# Portall — working notes, board and add-on only

Context for a session picking up **`components/portall/`** and the Home
Assistant add-on **`portall/`**, and nothing else in this repository. The
root `CLAUDE.md` covers everything including `components/wired_portall/`,
`portall.exe` and the Windows second-screen work; none of that is in scope
here and none of it needs to be read to work on these two.

Everything below was arrived at against real hardware. The "why" paragraphs
are the expensive part, not the code.

## What it is

Put a **Home Assistant dashboard — and any web page — on an ESP32-P4 panel,
over Wi-Fi, without LVGL**. The board runs no browser and no Home Assistant.
A machine that is on anyway, the Home Assistant server, renders the page in a
headless Chromium and sends the picture as JPEG rectangles. Touches come back
up the same socket and are replayed into that browser, so the panel behaves
like the screen of the machine doing the rendering.

The user's framing, kept verbatim: *"faire fonctionner home assistant sur ces
ecran bien plus simple que lvgl"*.

What is different from every other ESP32-P4 Home Assistant project found by
search (all LVGL tile UIs) is the P4's **hardware JPEG decoder and PPA
rotation**, so the ceiling is the network rather than the CPU.

```
 Home Assistant box                                  ESP32-P4 panel
 ┌───────────────────────────────┐                   ┌──────────────────────┐
 │ add-on (portall/)             │                   │ portall component    │
 │  run.py  supervises one       │  TCP :5000        │  feed_()   parse     │
 │          ha_send.py per panel │ ────────────────▶ │  decode    HW JPEG   │
 │  launcher.py  the home page   │   JPEG rectangles │  PPA       rotate    │
 │                               │   PCM sound       │  draw      panel     │
 │ ha_send.py                    │                   │  speaker             │
 │  headless Chromium screencast │ ◀──────────────── │                      │
 │  tile diff → rectangles       │  'T' touches      │  touchscreen listener│
 │  replays touches into the page│  'S' awake/asleep │                      │
 └───────────────────────────────┘                   └──────────────────────┘
```

## File map

```
components/portall/
  __init__.py        YAML schema, codegen, sdkconfig options, sleep/wake actions
  portall.h          class, TouchEvent, SleepAction/WakeAction templates
  portall.cpp        feed_() byte-stream parser, decode task, PPA, draw
  network.cpp        TCP listener, touch return channel, awake/asleep messages
  touch.cpp          touchscreen listener → HID digitizer and/or network queue
  audio.cpp          speaker: USB Audio Class in, and the network PCM path
  sender_drive.cpp   synthesised FAT12 volume carrying the sender script
  number/            volume control entity (class USBVolumeNumber, logs "Portall")
  udisp_send.py      THE wire format, plus a plain screen-mirroring sender
  ha_send.py         THE Home Assistant sender — 4462 lines, the big one
components/usb_display/       old name, a stub that refuses and names the edits
components/usb_display_tusb/  TinyUSB descriptors; kept its old name deliberately

portall/             the add-on
  config.yaml        options + schema; VERSION LIVES HERE
  Dockerfile         ARG BUNDLE must move with that version — see the trap below
  run.py             supervises one ha_send.py per panel; Weather; profiles
  launcher.py        the panel's home page, served on 127.0.0.1:8099
  logos.py           50 service marks as inline SVG (simple-icons, CC0)
  DOCS.md            the Documentation tab — this is the documentation
  README.md          the Info tab — short, points at DOCS.md
  CHANGELOG.md       the third tab
  icon.png logo.png  drawn by tools/makeicon.py, not kept as opaque binaries

tools/  checkyaml.py checkaddon.py importcheck.py iconlist.py makeicon.py playsound.py
yaml/   example firmware — see "State of the tree" for which ones are portall's
```

`DEPENDENCIES = ["display"]`, `AUTO_LOAD = ["audio"]`. **This component calls
no ESPHome helper outside the components it declares a dependency on.** A
convenience worth one log line is not worth a build that fails on a version
this cannot test — `network::get_use_address()` was renamed under a user and
reached them as a compile error on their own board.

## The wire protocol (udisp, Espressif's)

16-byte header, `struct.Struct("<HBBHHHHI")`:

| field | notes |
|---|---|
| `crc16` | sent as 0; the board validates geometry + length |
| `type` | `UDISP_TYPE_JPG = 3`, `UDISP_TYPE_PCM = 0x10`, `UDISP_TYPE_END = 0xFF` |
| `cmd` | sent as 0 |
| `x`, `y` | rectangle origin — **this is the whole trick** |
| `width`, `height` | rectangle size |
| `packed` | `frame_id` in the low 10 bits, `payload_total` above |

`build_header()` / `build_heartbeat()` live in `udisp_send.py` and `ha_send.py`
imports them. **One definition of the wire format — keep it that way.**

Every rectangle of one picture carries the same `frame_id`, so the board admits
or drops them together and never shows half an update. A `UDISP_TYPE_END`
header with no payload is the heartbeat, every three seconds.

Sound is the same sixteen bytes with the geometry and frame id all zero and
only `payload_total` read: **48 kHz, 16-bit signed little-endian, mono**
(`PORTALL_AUDIO_*` in `portall.h`, `AUDIO_*` in `udisp_send.py`, and the USB
audio class configured to match because they share one speaker and one block
buffer). Mono on purpose — these panels have one speaker and it halves what
the network carries.

## Board-side invariants — each one is a bug that was fixed

**`feed_()` is a byte-stream parser, not a packet parser.** TCP does not
preserve write boundaries. The earlier version had four separate bugs and got
0/10 pictures right under *every* chunk shape. Verified against six shapes
(whole stream, 4096, MTU, 3-byte dribble, random 1–64, random 1–9000). **If you
touch `feed_()`, re-test it against chunk shapes.**

The audio state is shaped like `skipping_` rather than like the frame filling,
because audio is a *stream*: a payload split across two reads is two writes to
the speaker and the split is invisible. `reset_stream_()` clears it too, or the
next sender's first header is read out of the middle of the last one's samples.

**Rate limiting is per picture and only for whole-panel frames from unpaced
transports.** `min_frame_interval_ms_` must never drop a *rectangle* — the
sender does not resend it, so a dropped rectangle stays wrong until the
30-second full redraw. The gate keys on `frame->id`. The network path sets
`frame->paced = true` and is exempt: not reading the socket for a moment is
already the flow control.

**The touch queue drops the OLDEST, never the newest.** This cost 20 seconds of
apparent latency. The last event of a press is the *release*; dropping the
newest drops exactly that. Identical consecutive events are deduped (a finger
resting still says the same thing 50×/s) — which is why `Injector.tick()` is
asked by the loop rather than driven by contacts.

**`queue_touch_` uses `touchscreen::TouchPoints_t` and must stay inside
`#ifdef USE_TOUCHSCREEN`.** Touch is decoupled from `CFG_TUD_HID`. The speaker
half of `audio.cpp` is `#ifdef USE_SPEAKER`; only `setup_uac_` and the
`usb_device_uac` callbacks stay under `#if CFG_TUD_AUDIO`.

**The receive window is the whole inbound ceiling, and this component used to
lower it with its own hand.** `__init__.py` wrote `TCP_WND_DEFAULT` 64800 and
three friends, chosen as the largest multiple of the MSS that fits a 16-bit
window field *without scaling* — a premise that was simply wrong, because
ESPHome turns scaling on and uses **512000** when PSRAM is guaranteed. That was
not a floor being raised, it was a **ceiling lowered eightfold**.
`_request_fast_network()` now calls `network.require_high_performance_
networking()` and sets nothing itself; `SO_RCVBUF` is gone from `network.cpp`
for the same reason. **Before hand-setting anything device-wide, look at what
ESPHome already sets.**

**Network tuning that is still ours** (`network.cpp`): `NET_READ_SIZE = 32768`,
`SO_KEEPALIVE` with `TCP_KEEPIDLE 10 / KEEPINTVL 5 / KEEPCNT 3`, `TCP_NODELAY`,
`NET_RECV_TIMEOUT_S = 30` enforced against the last successful read (silence is
the *normal* state — a still dashboard sends nothing), select slice 5 ms. All
the `__init__.py` options are set **only when `port:` is present**.

**`SleepAction::play` must be `void play(const Ts &...) override`.** Taking
`Ts...` by value compiles as a non-override and silently does nothing.
`esphome config` cannot catch it — **it validates YAML and codegen and never
compiles C++.**

**`set_awake()` only tells the sender.** It does not touch the backlight and
does not stop the panel drawing; those are the YAML's job. A sleeping board
drops contacts at `queue_touch_`, which is what stops the tap that wakes it
from also pressing whatever was underneath.

**Free PSRAM standing still is what a working panel looks like.** Nothing here
allocates while it runs — the decoder's RGB565 output, the PPA rotation buffer
and the frame buffers are taken once in `setup()`. A figure that moved during a
video would be the fault, not the health. `dump_config()` reports what was
taken, measured across `setup()` (~2.5 MiB on the Guition at 800×1280 with no
rotation; a rotated or scaled panel pays another panel-sized buffer).

**The PPA is 72–78% of every draw when rotating**, measured on a real panel —
14865 µs of 18964 on a whole panel. It was written up here as "silicon that was
idle", which was an assumption stated as a fact: idle it may be, cheap it is
not. The whole cost exists only because `rotation:` is not 0. `ppa_burst:`
(default 64) exists but **has never been compiled or run** — the field name and
both enumerators are taken from working code in `youkorr/lvgl_9.5`, not memory.

**Espressif's own open bug is the stall.** `H_SDIO_DRV: task still writing Rx
data to queue!` discards the packet below TCP — espressif/esp-hosted-mcu
**#184 (EHM-206)**, "ESP32-P4 + C6 SDIO: Inbound TCP transfer stalls". Open, no
fix, and their workaround is "pace inbound reads", which is exactly what a
panel cannot do. The knobs (`ESP_HOSTED_SDIO_RX_OPTIMIZATION`, `RX_Q_SIZE`,
`use_psram:`) are one line in a user's YAML and **none of it is measured here**.

## Sender-side invariants — `components/portall/ha_send.py`

It is 214 KB and 4462 lines. **Prefer narrow, anchored edits, `assert
s.count(old) == 1` before every replace, and after an anchored deletion check
that what you expected to survive is still there** — a replacement that spanned
too far has silently deleted `send_picture`, `_target_picture`, `calibrate`,
`Screencast` and (in a sibling file) a whole `--stats` block, each time leaving
a file that still parses. Diff the module-level names against `git show HEAD:`
afterwards; that is the check that catches it in one line.

Map: `rect_cost_fraction` `_launch` `pick_browser` `report_drm` `report_media`
`_agent_metadata` `present_browser` `install_tokens` `open_page`
`changed_rectangles` `TouchMap` `quality_for` `fps_for` `agent_for`
`route_agents` `send_picture` `calibrate` `PageAudio` `PanelWriter`
`Screencast` `HomeHint` `Keyboard` `Injector` `main`.

**Chromium screencast, not screenshots.** `Page.captureScreenshot` gave 0.2
pictures/s and then failed outright. **Acks are sent from the loop, never from
the frame handler** — a measurement of the browser cost read 0.2 frames/s until
that was fixed, then 59.6.

**The screencast is JPEG**, not PNG. JPEG is a block transform, so a block whose
pixels went in identical comes out identical; the ringing is deterministic.
Measured on a 1024×600 dashboard with one clock digit changed, q60–q95: exactly
the one tile differed, never another. `--capture-quality` (default 90) is
separate from `--quality`, which is what the panel receives.

**`TILE = 64`, `MIN_RECT = 64`, `FULL_REDRAW_SECONDS = 30`.** `differing =
previous != current` **without** an `np.any(..., axis=-1)` reduce, which was 15×
slower for the same answer. `MIN_RECT` is not removable: the P4's JPEG decoder
is a DMA engine in 16×16 units and a 32×128 sliver returns `ESP_ERR_TIMEOUT`
rather than pixels. Undersized rectangles grow *backwards* so they stay inside
the panel.

**`rect_cost_fraction(w, h)` judges a full redraw by how many *pieces* an update
is, not by its area** — and it comes out of the geometry (0.176 at 1024×600,
0.106 at 800×1280) rather than being one constant. It **saturates**: the rule
gives up on rectangles when `coverage + fraction × count > 1`, so the fraction
alone sets a count past which the whole panel goes out however little changed.
At the old hard-coded 0.18 that count was six, which a camera tile reaches
easily. It takes the **PANEL** size even when drawing smaller, because the
board's cost for a whole picture barely shrinks.

**The write goes through `PanelWriter`, on a thread, holding one whole picture,
all-or-nothing.** `sendall` from the loop meant a single turn took three
seconds. A link that cannot keep up therefore costs *pictures*, which is the
right thing to lose, and `--stats` counts them as `skipped`. **`SO_SNDBUF` was
capped alongside it and that half was wrong and is gone** — measured the same to
within noise at every drain rate, and it was a ceiling the user had not asked
for. `panel wait` is now the writer thread's time, so it can sit near 100%
without a stutter; `skipped` is what the link is costing.

**Acknowledgement pacing is the flow control.** Chromium keeps ~3 frames in
flight; acking every turn asks it to paint at full rate and `--fps` then
discards the surplus *after* the cost is paid. `Screencast.lead` is the measured
ack-to-arrival time and the ack goes out `1.5 × lead` before the deadline. Waste
falls from 84–93% to 33–35% with no loss of delivered rate.

**A press opens a WINDOW, and it discards everything painted before it — on
LANDING only, never on every report.** Applied to every report it was ruinous: a
finger reports 50×/s and almost nothing painted during a drag survived. And
**every input dispatch costs a display frame** (16.6 ms measured), so
`WHEEL_MIN_INTERVAL_S = 0.030` with the deltas *summed*, which is also more
exact than one wheel per report (400 px of finger → 400 px of page, against
380). Together: 8.3 → **19.5 rect/s**, worst gap 429 → **78 ms**, loop 10 →
**67–84 Hz** during a swipe.

**`fps` is what caps a video, and the urgent window hides that from every test
involving a finger.** Measure the idle path with **no input at all** or the gate
stays invisible.

**`TouchMap` works in normalised fractions and yields all 8 dihedral
candidates.** Pixels failed on the Tab5 by 454 px. `--calibrate` draws three
targets and prints `--touch-rotate` / `--touch-mirror-x` / `--touch-mirror-y`;
it needs no browser and no token. **Run it once per board, always** — there is
no way to know from the sender which way a panel reports contacts. Note ESPHome
runs `listener->update()` **before** the `on_touch` trigger.

**The keyboard is inert decoration and the page must never be able to touch
it.** `pointer-events: none`, no listeners, no focus. Four earlier attempts all
failed by *adding* something. It goes in the **top layer via the popover API**
(fallback needs `z-index: 2147483647` *and* a transparent `dialog::backdrop`),
`showPopover()` is called again on every sync because the top layer stacks in
entry order, and it is built out of the DOM with a constructable
`CSSStyleSheet` — `innerHTML` throws under Trusted Types and took the whole
sender down every thirteen seconds. **The keys working proves nothing about the
keyboard being visible.** `BLUR_GRACE_S = 0.4`. A `__udispFocusChanged` binding
goes on the *context* so it reaches every frame, which is what makes ingress
iframes work.

**`HomeHint` and the home gesture.** Hold the top-left corner
(`HOME_CORNER_FRACTION = 0.14`) for `HOME_HOLD_S = 1.0`, or swipe sideways out
of it (`HOME_SWIPE_FRACTION = 0.10`, `HOME_SWIPE_STRAIGHTNESS = 1.5`). The hold
is cancelled by *leaving the corner*, not by wandering inside it. The decision
is made **once**, when the finger has gone far enough sideways. A spent gesture
is swallowed, not reset. At the navigation the frame in hand must be **kept**
(`Screencast.restart()`, not `request(discard=True)`) — a still page paints
once, so that frame is the only picture it will ever send.

**A token belongs to an ORIGIN.** `install_tokens()` writes one guarded init
script per origin, first wins, and checks `window.location.origin` **in the
browser** — an init script runs on every document, so without that guard the
house's long-lived token was written into the storage of every site a panel
visited. `--token-url` names the address the token belongs to.
**Never echo a token into a file, a log or a commit** — a `print(f"... {entry!r}")`
in this area leaked one into the add-on log for one release.

**`--show-media`, `report_media()`, `report_drm()` and `watch_failed_requests`
exist because a video that stops does not error.** Timelines beat event hooks:
the evidence is in the seconds *before* the stall. Two real findings came out of
them — `ERR_NAME_NOT_RESOLVED` on `googlevideo.com` (a Supervisor DNS setting,
not the site), and `emptied` with 15.8 s still buffered, which is YouTube
verifying its own advertising. Neither is something this project engineers
around.

**Sound for the page**: `pactl load-module module-null-sink`, `PULSE_SINK` in
the browser's environment, `parec` on the monitor. **Playwright passes
`--mute-audio` on every launch and says nothing about it** — this is why the
first attempt captured silence while every indicator said the sound was fine;
`ignore_default_args=["--mute-audio"]`, and only when sound is wanted.
`PageAudio` holds half a second (`deque(maxlen=25)`), drops a block that is
**exactly** zero so a silent page costs nothing, and `_drain_audio()` runs
**between** the rectangles of a picture, never behind them — a gap is a click.
**Lip sync is not done**: nothing timestamps either stream.

## The add-on — `portall/`

Currently **3.2.1**, slug `portall`.

**Version bump trap: `config.yaml`'s `version` and the Dockerfile's `ARG
BUNDLE` must move together.** Docker caches a layer on its command string
alone and every string in that file is fixed, so a box that built the image
once reuses all of it however many times the add-on is updated — which shipped
stale sender code, and then a stale *browser*, which is worse because the
keyboard's top layer needs Chromium 114. `ARG BUNDLE` and the `RUN` that writes
it sit **first in the file** so a bump refetches everything. They are both
`3.2.1` today; `tools/checkaddon.py` checks it.

**A bump is only half the trap: work that lands AFTER one is invisible too.**
Home Assistant offers an update on `config.yaml`'s version alone, so three
commits once reached the repository and no panel. `checkaddon.py` asks git when
the version last moved and whether anything the image *carries* has changed
since — read off the Dockerfile (its `COPY`s, its `ADD`s, and `config.yaml`),
deliberately not `DOCS.md`/`README.md`/`CHANGELOG.md`, which the Supervisor
reads from the repository.

**The image has to be told about every file.** `launcher.py` was written,
imported, tested and never given a `COPY`; the add-on died on
`ModuleNotFoundError` before serving a panel, on every restart. `checkaddon.py`
follows imports **transitively** now and caught the missing `COPY logos.py` on
its first run. Run it before pushing anything under `portall/`.

**And an option can reach the form and never reach a panel.** `locale` went
into `config.yaml`, its schema and `SHARED_KEYS`, and still did nothing:
`command_for()` emits from its own separate list. `checkaddon.py` now RUNS
`command_for()` with each option set and looks for the flag. One legitimate
exception: `keep_profile` reaches the sender as `--profile <dir>` or not at all.

**`given()` is the one place that decides whether a form field was filled in,
and whitespace is not filled in.** An add-on form has no empty state, so a
field nobody filled arrives as `""` and a plain `{**shared, **panel}` lets it
blank the shared one. **And a space is a blank**: `token: ' '` was found in a
user's configuration, sent as `--token " "`, failed the JWT shape check, and
the sender exited before opening a browser — restarting on the 5→120 s backoff
for ever with a log blaming a token nobody had set. **An accessory setting must
never cost the picture.**

The form's own options today, in order:

`port fps quality keyboard keep_profile locale launcher_theme
launcher_background launcher_background_motion launcher_background_blur
launcher_background_dim launcher_columns launcher_clock launcher_weather
launcher_clock_size launcher_clock_color launcher_date_size
launcher_date_color launcher_weather_size launcher_align launcher_slideshow
launcher_slideshow_urls launcher_slideshow_seconds launcher_slideshow_fade
launcher_slideshow_rescan links stats show_media panels`

A **link** carries `name url icon group description token quality fps
user_agent`. A **panel** carries `name host port url token width height rotate
touch_rotate touch_mirror_x touch_mirror_y home_assistant fps quality keyboard
blank_after keep_profile locale user_agent import_profile stats show_media`.

**Home Assistant is a LINK and the token went with it (3.0.0).** `token:` and
`url:` came off the top of the form because **a token belongs to an origin**
and a link is the only thing in the configuration that names one. That was a
major version with a migration section for a hard reason: **removing a key only
warns, but a stored value that is no longer a member of a `list()` is a
validation failure** — an add-on that will not start until somebody edits a
dropdown they have never seen. The url is no longer inheritable, so every panel
needs one, and the message names the panels missing one.

**The launcher is the panel's home page.** `launcher.py` serves it on
127.0.0.1:8099 inside the container, reachable by the senders and by nothing
else; a panel asks with `url: launcher`, which `run.py` rewrites. Everything is
inline and **nothing is fetched** — a panel is the one screen where nobody can
open a console to find out why a picture did not load. The look is Homepage's
and its vocabulary is kept on purpose; the density is not (read across a room,
pressed with a thumb, no hover state). `logos.py` carries **79** marks as
inline SVG from simple-icons (CC0); **527** icon names map onto **116** glyphs
(counted today, not remembered), **every icon carrying both French and English**, one line per icon with every word that
should reach it, and flattening fails loudly on a name used twice.

**The weather is read by the ADD-ON, never by the page.** `run.py` has the token
and the address; giving either to the page would put a long-lived token into
the storage of every site a panel visits. `Weather` polls `/api/states/<entity>`
every ten minutes in a thread and the page asks `127.0.0.1:8099/weather.json`.
It splits the **origin** out of `url:`, which is nearly always a dashboard path.
`page()` rebuilds when the reading changes — rendering once in `start()` is what
froze it at boot for the life of the add-on.

**The launcher and the keyboard live under one rule: an accessory must never
cost the picture.** A bind that fails returns None. `run.py` imports the
launcher inside a `try`. Every failure keeps the last reading and says so once.

**`run.py` also**: starts PulseAudio **before** spawning any sender (one that
got there first found none and `pactl` tried to spawn its own), keeps profiles
under `/data/profiles/<panel>` — **one per panel, never shared**, Chromium locks
one — supervises with backoff 5 → 10 → 20 → 120 s for a run shorter than 20 s,
and kills children on SIGTERM.

**From inside the add-on the URL must be `http://homeassistant:8123`.** A
Tailscale, Nabu Casa or `.local` name gives `ERR_NAME_NOT_RESOLVED`;
`explain_unreachable()` says so. Downloads in the Dockerfile use `curl -f`,
because without it GitHub's "429" HTML page was saved as the script.

**Home Assistant shows `DOCS.md` (Documentation tab), `README.md` (Info tab)
and `CHANGELOG.md`, and no relative link or image in any of them resolves** —
the Supervisor hands them to the frontend as text with no base address.
Getting that half-right cost three releases. `checkaddon.py` fails on a
relative image in `DOCS.md` and on a missing `icon.png` / `logo.png`.

## Hardware and measured behaviour

Three boards confirmed working: **Waveshare ESP32-P4-WIFI6-Touch-LCD-7B**
(1024×600), **M5Stack Tab5** (720×1280 portrait, used landscape at 270°),
**Guition 10"** (800×1280).

- the C6 radio: **above 25 Mbit/s** outbound, measured by the user with VLC
  against the board's own camera — 25 932 kb/s, 3549 frames, **0 lost, 0
  corrupted**. Inbound is a different path; both are far above what this sends
- touch end to end: 3–22 ms
- reactivity floor ≈ **105 ms**, Chromium's repaint and screencast delivery,
  not this pipeline
- idle traffic: **0.0 KiB/s** — a still dashboard genuinely sends nothing
- with a camera in the dashboard: 14.2 pictures/s, 1141 KiB/s
- busiest five-second window ever recorded: 758 KiB/s
- sleeping, CPU over ten seconds: **1.73 s** before `SLEEP_PUMP_MS` and
  `--blank-after`, **0.15–0.17 s** with both. Waking costs 0.10 s unparked
  against 3.12 s parked
- rendering smaller: 800×1280 costs 8.7 ms and 18.0 KiB a picture, 400×640
  costs 1.7 ms and 7.5 KiB. **Not every size divides** — 533×853 into 800×1280
  leaves 2079 panel pixels no rectangle ever covers

**`power_save_mode: none` on a panel.** ESPHome's default is `light` and a
panel taking 24 pictures a second is not a sensor that speaks once a minute.

**Bytes are not what runs out.** A panel saturating at 918 KiB/s while a
minutes-earlier run was fine at 1429 KiB/s is the whole diagnosis: what changed
was `92 whole` — on full motion every picture is a whole panel, a fixed cost the
board pays whatever the JPEG weighs. Lowering quality shrinks the JPEG and
leaves that cost untouched. **A steady 15 beats an erratic 18.**

## State of the tree, checked today (2026-09-13)

Branch `claude/esphome-pr-outdated-mdq36w`, clean. Validation venv here is
**esphome 2026.6.5**; the user builds on **2026.9.0-dev**, which is installable
with `python3.12 -m venv` then `pip install "git+https://github.com/esphome/
esphome@dev"`.

Three of the four files in `yaml/` are portall's (`ws-wired-portall.yaml` is
the out-of-scope sandbox copy). Run against 2026.6.5 with
`ESPHOME=/home/user/esphome-venv/bin/esphome python3 tools/checkyaml.py ...`:

| file | result |
|---|---|
| `yaml/ws-usb-screen.yaml` | **ok** |
| `yaml/tab5-portall-screen.yaml` | unknown — `micro_wake_word` downloads its model from github while validating, so the rest went unchecked |
| `yaml/GUITION_ PORTAL.yaml` | **fails**, on two lines of its own, both in the first thirty |

The Guition file's two faults, and they are the user's own file rather than a
regression: the device `name:` is `GUITION_ PORTAL`, and ESPHome allows only
lowercase, digits, `-` and `_` with no spaces; and `api: encryption: key: ""`
is empty where a 32-byte base64 key is wanted. **With only those two changed it
passes** — checked, on a copy, so the rest of that file including the whole
`portall:` block, the ES8311/mixer/resampler audio wiring and the `number:
platform: portall` entity is valid on 2026.6.5.

**`CLAUDE.md` is stale on this point** and a new session will be misled by it:
it calls `yaml/p4-home-assistant.yaml` "the validated Waveshare firmware" and
refers to `yaml/guition-10-home-assistant.yaml`. The user deleted both
(commits `46a68a9`, `e14b12b`), and `ha-esp32p4 PORTAL.yaml` was renamed to
`GUITION_ PORTAL.yaml`. There is no Waveshare Home Assistant example any more.

## Rules of the house

- **Do not touch code people are using without being asked.** Stated as *"surtout
  il ne faut pas toucher au code Portall que des personnes utilisent"*.
- **A ceiling nobody asked for is a bug even when the reasoning behind it is
  sound.** The send-buffer cap, the receive window, `SO_RCVBUF`, the render-size
  advice and `--mute-audio` were all defensible arguments standing in for a
  measurement, and every one had to be reverted. **Three unasked-for changes**
  is the running count in `CLAUDE.md`; the cost is always the user's time.
- **A capability the reader cannot reach from where they are standing has not
  been delivered.** The keyboard that worked while invisible; the add-on option
  that never reached `command_for()`. This has been recorded four times.
- **Ask the world, not the step.** An exit code is the tool's opinion.
- **The user is right about named per-thing settings over general mechanisms.**
  Five times now — `quality:`, `user_agent:` and `fps:` on a link, the named
  clock/date/weather settings over `launcher_css`. Follow that pattern.
- **Only hand over configuration that has actually been validated.**
  `tools/checkyaml.py` at minimum, and say plainly when something is untested.
  `tools/importcheck.py` on anything touched under `components/` — a validator
  referenced before it is defined is a NameError at *import* time and reaches
  the board's build rather than any YAML check.
- **Never echo a token into a file, a log or a commit.**
- They write French; the codebase and its comments are English, and comments
  explain *why*, in prose, at the place the reasoning is needed.
- Windows/PowerShell is their shell for manual runs: no `\` line continuations
  and no `<chevron>` placeholders in commands handed to them.
- Work on `claude/esphome-pr-outdated-mdq36w`, then merge into `main` with
  `--no-ff` and a `Merge: <what it does>` subject. Commit subjects are
  imperative and say what the change *does for the user*.

## Open items

- The reactivity floor (~105 ms) is Chromium's. Below it needs a different
  capture path.
- **`ppa_burst:` has never been compiled or run.** Nor has anything else C++
  written here since — there is no ESP-IDF toolchain in this container.
- The keyboard has not been tried against **Assist** on a real board. It has no
  accents beyond the azerty bottom row and does not move out of the way of a
  field it covers; both deliberate.
- **Lip sync** for page audio: nothing timestamps either stream.
- **Per-link render size.** Drawing smaller is the strongest lever for full
  motion and is currently panel-wide, because the board sizes `rgb_buffer_` for
  the render size once in `setup()`. Per-link means allocating for the panel
  size and taking the scale from each frame's own dimensions — C++ that cannot
  be compiled or tested here. Identified, not built.
- `--blank-after` frees the page but not the browser. **One browser serving
  several panels** is the next step.
- Not H.264 on the wire: the P4 encodes it in hardware and decodes it only in
  software, which moves the cost onto cores that are idle by design.
