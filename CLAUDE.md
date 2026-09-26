# Working notes for Claude

This file is context for a Claude session picking the repository up cold. It is
about `components/portall/` and the add-on that drives it, because that is
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
 │ add-on (portall/)             │                   │ usb_display component│
 │  run.py  supervises one       │  TCP :5000        │  feed_()   parse     │
 │          ha_send.py per panel │ ────────────────▶ │  decode    HW JPEG   │
 │                               │   JPEG rectangles │  PPA       rotate    │
 │ ha_send.py                    │                   │  draw      panel     │
 │  headless Chromium screencast │ ◀──────────────── │                      │
 │  tile diff → rectangles       │  'T' touches      │  touchscreen listener│
 │  replays touches into the page│  'S' awake/asleep │                      │
 └───────────────────────────────┘                   └──────────────────────┘
```

1. **Board firmware** — the `portall` ESPHome component. Also does USB
   (display over cable, HID digitizer, UAC speaker, a mass-storage drive that
   carries the sender script). The network path is an *addition* to the USB
   path, not a replacement: a board can stay plugged in for its speaker while
   the picture arrives over Wi-Fi.
2. **The sender** — `components/portall/ha_send.py`. Runs anywhere with
   Python + Playwright.
3. **The add-on** — `portall/`, so the sender starts with the house
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
| `packed`       | `frame_id` in the low 10 bits, `payload_total` above |

`build_header()` / `build_heartbeat()` live in `udisp_send.py`, and `ha_send.py`
imports them rather than restating the format. **One definition of the wire
format — keep it that way.**

Every rectangle of one picture carries the same `frame_id`, so the board admits
or drops them together and never shows half an update. A `UDISP_TYPE_END` header
with no payload is the heartbeat.

## File map

```
components/portall/
  __init__.py        YAML schema, codegen, sdkconfig options, sleep/wake actions
  portall.h          class, TouchEvent, SleepAction/WakeAction templates
  portall.cpp        feed_() byte-stream parser, decode task, PPA, draw
  network.cpp        TCP listener, touch return channel, awake/asleep messages
  touch.cpp          touchscreen listener → HID digitizer and/or network queue
  audio.cpp          USB Audio Class speaker (Espressif's usb_device_uac)
  sender_drive.cpp   synthesised FAT12 volume carrying the sender script
  number/            volume control entity
  udisp_send.py      the wire format + a plain screen-mirroring sender
  ha_send.py         THE Home Assistant sender (screencast, diff, touch, calib)
components/usb_display/  the old name, kept as a stub that refuses and says
                     exactly what to change
portall/             Home Assistant add-on wrapping ha_send.py
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
= 32768`, `SO_KEEPALIVE` with `TCP_KEEPIDLE 10 / KEEPINTVL 5 / KEEPCNT 3`,
`TCP_NODELAY`, `NET_RECV_TIMEOUT_S = 30` enforced by the loop itself against
the last successful read (silence is the *normal* state — a still dashboard
sends nothing; the heartbeat every three seconds is what proves life), select
slice 5 ms (that is how long a contact can sit in the queue). All of the
`__init__.py` options below are set **only when `port:` is present**, because
they are device-wide and a USB-only board should not pay for them.

**The receive window is the whole inbound ceiling, and this component used to
set it too low with its own hand.** A window is how much a sender may have in
flight before it must stop and wait, so the most that can arrive is the window
divided by the round trip — nothing else about the link enters into it.

`__init__.py` used to write `TCP_WND_DEFAULT` 64800, `SND_BUF_DEFAULT` 28800,
`RECVMBOX_SIZE` 64 and `SO_RCVBUF` itself. 64800 was chosen as the largest
multiple of the 1440-byte MSS that fits the 16-bit window field of a TCP header
**without window scaling** — and that premise was simply wrong. ESPHome's
`network` component turns window scaling on (`CONFIG_LWIP_WND_SCALE`,
`CONFIG_LWIP_TCP_RCV_SCALE 3`) and uses **512000** with 512-deep mailboxes and
a 65534 send buffer whenever PSRAM is guaranteed. So those four lines were not a floor being raised. They were a
**ceiling being lowered by a factor of eight**, and it is what limited a panel
to about 26 Mbit/s at a 20 ms round trip.

The user's own VLC capture is what settled it: the same board serving its
camera through `esp32_camera_web_server` sustained **25 932 kb/s, 3549 frames,
0 lost, 0 corrupted**, and that component sets no lwip options at all — it is
plain `esp_http_server` on top of whatever the build gives it. The throughput
came from ESPHome's defaults, and this component was overriding them downward.

`_request_fast_network()` now calls **`network.require_high_performance_
networking()`** — the documented API, called from a validator, only when
`port:` is present — and sets nothing itself. It is more than lwip: the `wifi`
component reads the same flag and raises its RX/TX buffers, turns on AMPDU
aggregation and moves those buffers into PSRAM. Verified against ESPHome
2026.6.5 that the flag is set with `port:` and not without, and
`yaml/p4-home-assistant.yaml` still validates. `esphome config` will not show
it — the settings are applied in the network component's `to_code`, which
`config` never runs.

**`SO_RCVBUF` is gone from `network.cpp` for the same reason.** It is a ceiling
on what one socket will hold, so a value set there can only ever bind *below*
the window and throttle the thing it looks like it is helping. It was 98304 —
above the old 64800, far below the new 512000. `CONFIG_LWIP_SO_RCVBUF` went
with it.

The lesson is the same one the send buffer taught on the other side: **a
ceiling nobody asked for is a bug even when the reasoning behind it is sound.**
Both were arithmetic, both were self-imposed, and both were defended for
releases. Before hand-setting anything device-wide, look at what ESPHome
already sets.

**CORRECTED: "PSRAM is guaranteed, which every board this runs on has" stood
above, and it is false.** Guaranteed means `psram: ignore_not_found: false`,
and the default is **true** -- so a panel that never wrote that line gets
ESPHome's fallback, a 65534 window with no scaling and 64-deep mailboxes, and
`require_high_performance_networking()` changes nothing. Not one example in
`yaml/` carried it. Found by running the codegen on a real panel's YAML at
2026.8.2 and reading the resulting sdkconfig, rather than trusting the
docstring. That panel also set `CONFIG_LWIP_TCP_WND_DEFAULT: "65534"` in its
own `sdkconfig_options`, and **a user's sdkconfig_options win** over the
network component's: with `ignore_not_found: false` alone it still read 65534.
Both lines had to go before it read 512000.

**Wi-Fi roaming scans are what dropped the sound, and portall now holds them
off while it streams.** From the same panel, one second of log:

    23:44:37  Roam scan (-59 dBm, attempt 1/3)
    23:44:39  Dropped a block: the speaker is not draining (100 times so far)
    23:44:40  Dropped a block: the speaker is not draining (200 times so far)
    23:44:41  wifi took a long time for an operation (642 ms)

ESPHome 2025+ scans every channel for a better access point every five
minutes while the signal is below -49 dBm (`post_connect_roaming`, default
true; `ROAMING_CHECK_INTERVAL`, `ROAMING_GOOD_RSSI` in `wifi_component.h`).
The radio is off its channel for most of a second, the stream stops, then
arrives at once and overflows the mixer, the resampler and the Bluetooth ring
together. 100 blocks of 10 ms each second: two seconds of sound, and the
picture fell to 12 per second in the same window. With `post_connect_roaming:
false` the next 6 minutes of 25 fps video had no roam scan and no dropped
block.

ESPHome has the mechanism for a stream: `wifi.enable_runtime_roaming_
suppression()` at validation, then `request_roaming_suppression()` /
`release_roaming_suppression()` at runtime, which is what `sendspin` does.
`_request_fast_network()` asks for it whenever `port:` is set, and
`network.cpp` requests it on the first read longer than a header -- a
heartbeat is exactly one header -- and releases it after `ROAM_QUIET_MS` of
heartbeats only, and on every disconnect, because the count is the wifi
component's and an unmatched request would stop roaming for good. A still
dashboard therefore still roams; its scans move to when nobody is watching.
Guarded on `USE_WIFI_RUNTIME_ROAMING_SUPPRESSION`, so a panel on Ethernet or
an ESPHome without the API compiles none of it. Present in 2026.8.2 and
2026.10.0-dev alike. **Not compiled** -- the define was read off the codegen.

**The radio's power saving is held with it, which is sendspin's other half.**
Reading `sendspin` for more found `on_request_high_performance()`: it calls
`request_high_performance()` beside the roaming request, which puts the radio
at `power_save_mode: none` while the stream lasts and gives the YAML's mode
back afterwards. ESPHome's default is `light`, and this file already records
`none` as what a panel needs -- so a panel whose YAML never said so now gets
it for the length of a stream, and saves power again when it goes quiet. A
YAML that already says `none` makes both calls do nothing (checked in
`WiFiComponent::request_high_performance`). `enable_runtime_power_save_
control()` at validation, `USE_WIFI_RUNTIME_POWER_SAVE` in C++, both present
in 2026.8.2 and 2026.10.0-dev. `hold_wifi_for_stream()` was extracted and
compiled with `-Werror` against a stub wifi component in all four
combinations of the two defines, with a double request and a double release
each producing exactly one call.

What sendspin also has and was left: a single-frame insert or drop blended
into its neighbour to follow clock drift, instead of dropping a whole 10 ms
block (only worth it if steady drops reappear -- they came from roaming), and
timestamped audio against a Kalman-filtered server clock with a fixed
playback delay (`sendspin-cpp` `time_filter.cpp`, Apache 2.0), which is the
shape lip sync here would take.

**And the error that opened every stream was a false alarm.** "The speaker
has not taken a single byte in 1 blocks. It is refusing this stream" printed
at the top of a stream that then played perfectly: the first block is handed
over the instant `start()` is called, and a mixer source starts from its own
loop afterwards, so it is refused on every panel every time. The diagnosis
now waits for a second of refusals (`NEVER_ACCEPTED_BLOCKS`) and the
underrun warning stays quiet until the speaker has taken something. It also
pointed at `yaml/guition-10-home-assistant.yaml`, which no longer exists.

**Sleep/wake.** `portall.sleep` / `portall.wake` actions, registered
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
caught it; keep measuring. It is no longer a matter of picking the right frame:
a press now throws away everything painted before it, in hand and in the
browser both, so nothing that predates it can be shown.

**`fps` is what caps a video, and the urgent window hides that from every
test that involves a finger.** A touch lifts the limit to `urgent_fps` for two
seconds, so anything measured while poking the panel runs at 30 and looks fine.
Nobody touches a panel while a film plays: the window closes and the picture
falls back to `fps`. Measured on a page moving continuously with **no finger on
it at all** — 9.5 pictures/s at `--fps 10`, 17.8 at 20, 25.3 at 30, with the
per-picture size unchanged at 54.9 KiB, so it is purely the gate.

The add-on's default was **10** and is now **25**. Ten was chosen for a
dashboard and is right for one; it was also a ceiling nobody watching a video
had asked for, and it is why *"ce n'est pas assez fluide"* survived two rounds
of real fixes to the touch path. A still dashboard costs nothing whatever this
is set to — what does not change is not sent — so the number only bites where
something moves.

The lesson is about *how it was hidden*: every swipe measurement in this
session ran inside the urgent window, so none of them could see it. Measure the
idle path with no input at all, or the gate that governs it stays invisible.

**Throwing away the frame in hand belongs to the LANDING, not to every
report, and getting that wrong is what made swiping erratic.** A press
discards everything painted before it — in hand and still in the browser —
because none of it shows the press. Applied to every touch report, that is
ruinous: a finger moving reports fifty times a second, each report threw away
the frame in hand and asked the browser to start again, and almost nothing
painted during a drag survived long enough to be sent.

Measured on a scrolling page at `--urgent-fps 30`, three seconds of continuous
finger:

| | rect/s | median gap | worst gap |
|---|---|---|---|
| every report (as it was) | 8.3 | 77 ms | **429 ms** |
| landing only (now) | 10.8 | 95 ms | **105 ms** |
| the same page scrolling by itself, no finger at all | 9.7 | 103 ms | 110 ms |

The third row is the control and it is what settles it: ~10 a second is what
that machine gives for a full-page scroll at 800×1280, and with a finger it is
now the same. The touch path costs nothing. Before, it cost half a second of
the page standing still while the finger moved — which from the glass is a
page that jumps rather than follows, and was reported as *"le swipe haut et bas
c'est erratique"*.

**And that was only half of it. Every input dispatch costs a display frame.**
Measured on the shipped browser, and it is not the page and not the
screencast: `page.mouse.wheel` takes **16.6 ms** a call, a raw CDP
`Input.dispatchMouseEvent` takes 16.2, and a plain `mouse.move` on
`about:blank` takes 16.7. Chromium acknowledges input on its next frame and a
synchronous client waits for it.

So the number of calls a second is the entire budget, and a finger reports
fifty times a second. One wheel each cost **830 ms of every 1000**: the loop
went from **110 Hz at rest to 10 Hz** while a finger moved — a panel reading
touches ten times a second and painting no faster. That is the rest of "trop
lent", and it is invisible from every metric except the loop rate.

`WHEEL_MIN_INTERVAL_S = 0.030` and the deltas are summed instead of sent one by
one. Chromium coalesces input per frame anyway, so nothing above the picture
rate was ever visible. Three seconds of continuous finger on a scrolling page:

| | rect/s | median gap | worst gap | loop during the swipe |
|---|---|---|---|---|
| as it was | 8.3 | 77 ms | **429 ms** | **10 Hz** |
| discard on landing only | 10.8 | 95 ms | 105 ms | 10 Hz |
| and the wheels summed | **19.5** | **50 ms** | **78 ms** | **67–84 Hz** |
| control: the same page scrolling with no finger | 9.7 | 103 ms | 110 ms | 110 Hz |

The finished swipe now beats the no-finger control, because the urgent window
can finally reach the rate it was always allowed.

**Summing is exact, and it is more exact than sending one per report.**
Measured through the browser: 400 px of finger scrolls **400** px of page in
20, 40 or 8 steps alike, both directions, and 100 gives 100 — where one wheel
per report gave 380 and 80. `tools`-free `injtest.py` in the scratchpad checks
the injector with a stub page and no browser at all, which is the only way to
see the arithmetic on its own.

A measurement trap worth keeping: the first version of the browser-side check
took its baseline from the first scroll event *after* the gesture began, which
already contains a wheel. Coarse gestures fire fewer, larger scrolls, so they
appeared to lose the most — a regression that was entirely in the ruler. Take
the baseline before the gesture.

`Injector.began` is the flag, set only where a gesture starts. And the
measuring matters as much as the fix: the first two rows alone would have
looked like a modest improvement, and the median even got *worse*. It is the
tail and the control together that say the fault is gone.

**Scrolling itself was never wrong**, which is why this took so long to find.
Measured against synthetic swipes: 400 px of finger scrolls 380 px of page in
20, 40 or 8 steps alike, both directions, `deltaMode` 0, and no stray clicks.
The missing 20 is `DRAG_THRESHOLD` — the travel before a drag is recognised as
one — and it is left as it is: it reads as slight stiffness, never as jumping.

**A press opens a window, because one free frame was never the thing that was
needed.** One frame is enough to watch a button go down and useless for what a
press usually starts: changing dashboard repaints the whole page over about a
second, and at `--fps 4` that arrived as four pictures. Reported from the panel
as *"il rame entre les dashboards"*, and the frame limit was the cause — the
`--fps 4` recommended to cut idle cost, paid for at the one moment there is
most to show. `--urgent-window` and `--urgent-fps` raise the limit after each
contact, and the defaults are 2 seconds at 30 because 1 second at 15 was
reported from a panel as still slow against a standing 30 — rightly: fifteen is
less than the machine gives, and a transition or a settling scroll runs longer
than a second. Measured against a fake panel sending a real contact, same page,
same run: 14 rectangles/s standing, 50 in the second after the press at 15/1s
and back to 13 by the next second, against **82 then 86** at 30/2s, back to 16
once it closes. Measured against a fake panel that sends a real
contact up the return channel, same page, same run: 12.7 rectangles/s standing,
**16.0** in the second after the press with the old single free frame — which
is to say the one frame and nothing else — against **45.0** with the window,
back to 16 afterwards.

Not unlimited, but not for the reason it first seemed. **The link is not the
constraint in either direction** — outbound the C6 was measured above 25 Mbit/s
serving a UVC camera through `esp32_camera_web_server`, and inbound, which is
the direction that matters here, the 28800-byte receive window over the round
trip gives 23 Mbit/s at 10 ms and 46 at 5. The busiest window ever recorded on
a panel was 6.2 Mbit/s.

What binds during a transition is the machine running the sender. Nearly every
picture then is a whole panel, and decoding, diffing and re-encoding one at
800×1280 costs around 40 ms: `loop` falls from 62 Hz to 43 and the sender tops
out near **seven pictures a second** whatever it is allowed. So `URGENT_FPS =
15` does not bind there at all; what it stops is the other case, a small cheap
change being sent sixty times a second because a finger touched the panel. The
lever on transitions is the cost of a whole panel — a lower `--quality`, or
rendering smaller and letting the PPA scale up — not the frame limit and not
the network.

**`TouchMap` works in normalised fractions and yields all 8 dihedral
candidates.** Working in pixels failed on the Tab5 by 454 px: `swap_xy` is
applied to *raw* values before calibration and the final scaling is to
`display_width_`/`display_height_`, so a portrait panel used landscape gives
pure scaling (1.838 / 0.620) with no rotation. Fractions make that disappear.

**`Injector` holds the press back until release** so a >12 px movement
(`DRAG_THRESHOLD`) becomes `mouse.wheel(-dx, -dy)` instead of a click.

**A headless browser says so in two places, and changing one of them made
things worse.** The UA carries `HeadlessChrome/` and `navigator.webdriver` is
true. `present_browser()` takes the browser's own UA through
`Browser.getVersion` (so the platform token stays right on whatever is running
it), drops the word Headless, and hides `webdriver`. That much was right, and
it is what got past the "navigateur non compatible" page whose absence of a
search box was reported as a keyboard fault.

The second place is **client hints**, and this is where it went wrong.
Measured on the shipped build against a server that logs its headers:

| | `sec-ch-ua` sent | `navigator.userAgentData.brands` |
|---|---|---|
| untouched | `"HeadlessChrome";v="141", …` | HeadlessChrome, Not?A_Brand, Chromium |
| UA string alone (as shipped) | **nothing at all** | **`[]`** |
| UA + metadata (now) | `"Chromium";v="141", "Google Chrome";v="141", …` | Chromium, Google Chrome, Not?A_Brand |

Cleaning the string fooled nobody who reads the headers — Google does — and
overriding it *without* `userAgentMetadata` **wiped the hints entirely**. A
browser claiming to be Chrome while sending no `Sec-CH-UA` at all is a
contradiction no real Chrome produces, and a louder automation signal than the
honest answer it replaced. It shipped that way for months.

`_agent_metadata()` now sends the metadata alongside, and **synthesises** it
rather than reading it: `navigator.userAgentData` exists only in a secure
context and the page is still `about:blank` when the disguise goes on —
measured, None on `about:blank` and on a `data:` URL, present on
`http://127.0.0.1`. Synthesis is safe because the list is not a secret: three
brands, one of them deliberately meaningless so servers cannot match the list
exactly. Only the versions have to be right and those come from
`Browser.getVersion`; `platformVersion` comes from `os.uname().release`, which
is what Chrome reports on Linux, because an empty one is its own small oddity.

`--user-agent off` keeps the honest one, a string uses it verbatim. **Still not
verified against YouTube** — no route to it from where this was written. What
is verified is the headers, which is the half that was wrong.

**YouTube's video stopping was four wrong guesses and then one measurement.**
The guesses were the codecs, ad filtering at the house's DNS (written into the
README as established, from the user's own hypothesis, and denied by them: no
Pi-hole, no AdGuard Home), the client hints, and the audio device. The
measurement took two lines of the panel's own log:

    Network: rr1---sn-t0a7sn7d.googlevideo.com -- net::ERR_NAME_NOT_RESOLVED
    Media: pause: t=20.0 ready=4 net=2 paused page=visible focused buffered=0.0s

`googlevideo.com` is where the video bytes come from, and the name **did not
resolve**. `buffered=0.0s` at the pause is the consequence: the player had
nothing left ahead of the playhead, so it stopped. Not a codec, not a policy
pause, not the panel, not the link — the next segment could not be fetched
because DNS failed.

Everything the user described follows from it and is the confirmation. Those
hosts are per-session — `rrN---snXXXXXXXX.googlevideo.com` is generated for
each playback — so *"si je change de vidéo je peux relancer"* is a new hostname
that happens to resolve. A dashboard never notices because it talks to one
name, resolved once. Jellyfin never notices for the same reason.

An add-on resolves through the **Supervisor's own DNS container**, not the
router, so this is a Home Assistant setting rather than a house one: Settings >
System > Network > DNS servers. The sender says so now, once, whenever it sees
`ERR_NAME_NOT_RESOLVED` — that failure is the only one whose cause is never the
site.

The general lesson is the one this whole episode kept re-teaching: **a video
that stops does not error, so the error hook could never have found it.** What
found it was reporting the boring events — `pause` with how much was buffered —
and reporting failed requests by host. Both were built after the guessing, and
both paid for themselves on the first run.

`ad.doubleclick.net -- net::ERR_FAILED` was in the same log and is a side-show;
it was the thing that looked most like a cause for two rounds.

**The DNS fix was real, and what is left is YouTube checking its own ads.**
After setting an upstream resolver, the next log carried **no
`ERR_NAME_NOT_RESOLVED` at all** and the `googlevideo` failures were gone. What
remained is a different shape, and the user found it by experiment rather than
by reading anything: *"si vous laissez la pub sans appuyer sur skip il ne
s'arrête pas"* — let the advertisement run to the end and playback is fine;
press Skip and the video stops a few seconds later. `ad.doubleclick.net --
net::ERR_FAILED` sits in the same window, and `ERR_FAILED` is not DNS.

That is YouTube verifying its own advertising, and it is not something this
project should engineer around. The honest answer to a panel is: let the ad
play.

**"The host never knows where the viewer is in the video" is the one theory
the architecture rules out, and it is worth writing down because it will be
offered again.** Relayed from somebody helping: that the machine rendering
never learns where the panel's viewer has got to, so it thinks the playhead is
at 0 s, never preloads, and the video stops -- what is missing is a feedback
channel from the panel.

There is no video client on the panel. The board receives **JPEG rectangles**;
it has no media element, no MSE, no playhead and nothing to report. The browser
on the server *is* the viewer -- it decodes the video itself, advances
`currentTime` itself, and its own MSE buffer decides what to fetch next. Viewer
and host are the same process, so there is no round trip to be missing.

The logs already falsified it before it was offered: `pause: t=20.0 ready=4
net=2 paused page=visible focused buffered=0.0s`. `t=20.0` is the browser's own
playhead twenty seconds in -- if anything thought the viewer was at zero, that
field would read 0.0. `buffered=0.0s` beside it is the real fault, and its
cause was measured: `ERR_NAME_NOT_RESOLVED` on `googlevideo.com`.

**What the theory is right about is that the log was silent where the evidence
is.** Every media line fired on an event -- `pause`, `waiting`, `stalled` --
so the log said nothing at all during the seconds *before* a stall, which is
where the question lives. `--show-media` (`show_media:` in the add-on) samples
every playing element every two seconds: playhead, seconds buffered ahead, and
the frames the browser itself dropped. It separates the three causes that look
identical from a panel -- a buffer that never fills (the fetch), a playhead
that stops with the buffer still full (the site), and dropped frames (the
machine).

Its own budget, separate from the error cap: a stall that repeats must not
spend the timeline's lines and a timeline must not spend the errors'. Verified
against a real playing `<video>` in the shipped browser -- twelve samples, the
playhead advancing, `buffered` falling from 2.7 s to 0.2 s as it ate into the
clip, and `dropped=` reported.

**`--show-media` earned itself on its first real run: the video is torn down
with sixteen seconds still buffered.** From a panel watching YouTube:

    Media: playing: <movie_player> t=40.2 ready=4 net=2 playing ... buffered=19.8s frames=1010 dropped=3
    Media: playing: <movie_player> t=42.2 ready=4 net=2 playing ... buffered=17.8s frames=1060 dropped=3
    Media: playing: <movie_player> t=44.2 ready=4 net=2 playing ... buffered=15.8s frames=1110 dropped=3
    Media: emptied: <movie_player> t=0.0 ready=0 net=3 paused

Every candidate cause this project has chased is ruled out by those four
lines. **`emptied`** is the page calling `load()` or clearing `src` -- the
element is reset, `readyState` 0, `networkState` 3 (NETWORK_EMPTY), the
playhead back at zero. It is not starvation: **15.8 seconds were sitting
there**. It is not the link, not DNS, not the decoder and not the panel, whose
own line in the same window is as good as it gets -- `panel wait 1%, 0
skipped, worst gap 93 ms, loop 87.2 Hz` at 22 pictures a second and 1.4 MB/s.

One detail worth keeping: `buffered` falls by exactly 2.0 s between samples
2.0 s apart, so the far end never moved -- **fetching had already stopped**
about twenty seconds before the tear-down, while the playhead ate through what
was in hand. So the sequence is: the site stops fetching, then resets the
player while a quarter of a minute is still buffered. That is a decision, not
a shortage, and it matches what the user found by experiment -- let the
advertisement run and playback survives, press Skip and it stops seconds
later.

After it, `8.0 pictures/s, 0 whole, 49.4 KiB/s`: a still page, which is the
error screen.

The general lesson is the one this whole area keeps teaching, and it is now
paid for twice: **the evidence for a video that stops is in the seconds
before it stops, when nothing is wrong and no event fires.** Every media line
here used to be event-driven, so the log was silent exactly there.

**Two kinds of `pause` now appear, and the buffer number is what tells them
apart.** `buffered=0.0s` is starvation — nothing left ahead of the playhead.
`buffered=17.1s` with the video paused at `t=4.2` is nothing of the kind: it is
the **hover preview** a YouTube search page runs beside the real player,
starting and stopping as the pointer moves. The timeline was unreadable until
each line named its element, so `who()` walks up to the nearest ancestor with
an id and the line now begins `<inline-preview-player>` or `<movie_player>`.

**`--mute-audio` is out, and both the change and its justification were
wrong.** It was added on the argument that "nothing carries audio over this
path" — stated as though the panel had no sound at all. It was challenged
(*"est tu sur que l'audio fonctionne avec ce que dispose de esp32p4"*) and the
challenge was right. The board is not short of audio: the Guition example has
an **ES8311 codec on I2S** (control over I2C at 0x18, MCLK/BCLK/LRCLK on
GPIO13/12/10, DOUT on GPIO09), an ESPHome `speaker:`, a mixer, a resampler, a
microphone and a `media_player: platform: speaker` — Home Assistant can already
play whatever it wants on that panel. `portall` itself takes audio in over
USB as a sound card, into that same speaker.

What has no audio is **the udisp link**, and only that: the wire format is
rectangles one way and `'T'`/`'S'` the other, and `network.cpp` contains the
word "audio" zero times. So the narrow claim was true and the way it was
written was not. Even granting the narrow claim, muting was a change nobody
asked for, made in the middle of a diagnosis, in exactly the area the user then
suspected — a site may treat a muted player differently from an audible one. If
it is ever wanted it belongs behind an option. **This is the third time an unasked-for change had to be reverted**
(the send-buffer cap, the render-size advice, and now this): the pattern is
always a defensible argument standing in for a measurement, and the cost is
always the user's time.

**A separate thing surfaced in the same log and is worth keeping apart:**
`panel wait 79%`, `worst gap 3441 ms`, `19 skipped` at 2.5 MB/s. That is the
link to the panel saturating for three and a half seconds, and it has nothing
to do with the video stopping — but if the machine running the sender is itself
on Wi-Fi, pushing 2.5 MB/s at the panel competes with fetching the video, and
the two faults can look like one.

**The browser it downloads cannot play video, and that is what YouTube's
"un probleme est survenu" is.** Measured on the exact build the add-on fetches,
141.0.7390.37, through `MediaSource.isTypeSupported`: **H.264 no, AAC no, HLS
no**, and `navigator.requestMediaKeySystemAccess` does not exist at all, so
there is no DRM of any kind. VP9, VP8, AV1, Opus and Vorbis are all yes. A
player picks its formats by asking those questions — not by reading the user
agent — so a site that offers nothing else starts a video on what it can and
stops when it needs one of the missing ones. A dashboard never notices.

Three things came out of that, and only the first is a guess:

- The add-on's image installs a second browser — `playwright install chrome`
  first because Chrome carries Widevine too, the distribution's `chromium` when
  there is no Chrome build for the architecture (Google publishes none for
  arm64, and a Home Assistant box is often a Pi). **Neither is allowed to fail
  the build**: the `RUN` ends in `; true`, because a panel that shows a
  dashboard is worth more than one that plays video. **Not verified** — there
  is no route to YouTube, to `deb.debian.org` or to `dl.google.com` from where
  this was written, and no Docker daemon either, so that Debian builds Chromium
  with `ffmpeg_branding=Chrome` is taken on reputation rather than measured.
- `--browser` now defaults to `auto`: prefer a system browser from
  `SYSTEM_BROWSERS`, and **fall back to Playwright's own if it will not start**.
  That fallback is what makes preferring one safe, and it is exercised —
  Ubuntu's `/usr/bin/chromium-browser` is a snap wrapper that does not run in a
  container, `auto` picks it, it fails, and the sender carries on. `off` keeps
  Playwright's whatever is installed; a path names one exactly.
- `report_media()` prints the codec table at every start, and `MEDIA_INIT_JS`
  reports the reason from the media element itself the first time each one
  fails: `Media: format not supported: DEMUXER_ERROR_COULD_NOT_OPEN: ...`.
  Before this, every cause of "the video stops" looked identical from the
  panel. The listener sits on the **window in the capture phase** — a media
  `error` does not bubble but the capture path still runs through the window —
  so nothing has to be swept for as the page builds itself, and it writes
  nothing into the page, so no Trusted Types policy can refuse it.

**Signing in to Google is a different fault from the video stopping, and the
browser's remaining tells were measured rather than guessed at.** Asked as
*"c'est pour cela que je peux pas m'identifier sur YouTube il refuse?"*. It is
not the same mechanism -- the video stopping was DNS and then YouTube checking
its own advertising, both of which happen long after any sign-in -- and Google
blocking sign-in from an automation-driven browser is not something this
project should engineer around.

What could be measured here, offline, is what a page can still see. On the
shipped build with `present_browser()`'s disguise applied, on the sender's own
page and in a secure context (the two things a first attempt got wrong -- the
override is set per CDP SESSION so a second page does not have it, and
`navigator.userAgentData` does not exist outside a secure context at all, so
about:blank answers null whatever is set):

| | value |
|---|---|
| user agent, and `sec-ch-ua` at the server | Chrome/141, brands Chromium + Google Chrome + Not?A_Brand |
| `navigator.webdriver` | undefined |
| the CDP `Runtime.enable` probe | not detected |
| `window.chrome` | **undefined** (a real Chrome has it) |
| `navigator.plugins.length`, `pdfViewerEnabled` | **0, false** (a real Chrome has 5, true) |
| WebGL renderer | **SwiftShader** |
| `navigator.languages` | **`en-US@posix`** |
| `Accept-Language` at the server | **not sent at all** |

The disguise holds where it was built to hold. The bottom two rows are not
detection at all, they are **broken**: no real browser omits `Accept-Language`,
and `en-US@posix` is the container's POSIX locale leaking through a field that
is supposed to be a BCP 47 tag. The consequence has nothing to do with Google
-- every site served its own default language to a French household.

`--locale` (`locale:` in the add-on, `en-US` by default) fixes both, verified
against a local server that logs its headers: `navigator.languages` becomes
`["fr-FR"]` and the request carries `accept-language: fr-FR`. The other rows
are left alone deliberately: forging `window.chrome` or a plugin list is
anti-detection work, it is not what was asked for, and this project has already
paid three times for changes nobody asked for.

**And adding one option found a hole in the add-on that had nothing to do with
it.** `locale` went into `config.yaml`, into its schema and into run.py's
`SHARED_KEYS`, and still never reached a panel: `command_for()` emits from its
own separate list of keys, which had not been touched. Nothing failed -- the
form simply offered a setting that did nothing, which is worse than not
offering it. It is the missing `COPY launcher.py` again in a different pair of
lists, so `tools/checkaddon.py` now checks it the same way, by RUNNING
`command_for()` with each option set and looking for the flag. The fault was
reproduced against the check before the check was believed, and it caught one
legitimate exception on its first run: `keep_profile` reaches the sender as
`--profile <dir>` or not at all, never under its own name.

**Users want to sign in, because that is where subscriptions live -- and the
answer is the television interface, not a better disguise.** Raised as *"j'ai
des utilisateurs qu'il veulent s'identifier a cause de leurs abonnements"*,
which is a stronger case than it first looks: a Premium account also removes
the advertisement, and the advertisement is the one cause of a stopping video
that survived the DNS fix.

Three routes were measured rather than argued about, same probe, same page
served over 127.0.0.1:

| | headless shell (shipped) | full Chromium | plain process, attached over CDP |
|---|---|---|---|
| `window.chrome` | undefined | **object** | object |
| `chrome.loadTimes` | no | **yes** | yes |
| `navigator.plugins` | 0 | **5** | 5 |
| `pdfViewerEnabled` | false | **true** | true |
| `Notification.permission` | denied | **default** | default |
| user agent, `sec-ch-ua`, `webdriver` | disguised | disguised | **not disguised** |

The third route -- launching the browser as an ordinary process with
`--remote-debugging-port` and attaching with `connect_over_cdp` -- was the
architecturally interesting one and it buys **nothing**: it lands in the same
place as simply using the full build, while losing the disguise the sender
applies through its CDP session. Dropped.

The second is nearly free: **59.6 frames a second against 59.1, 8.0s of CPU
against 8.8s** over the same twelve seconds of a page painting continuously
through a screencast. So `_launch` now reaches for
`playwright.chromium.executable_path` -- which names the FULL Chromium; plain
`launch()` does not use it -- and falls back to the shell only if that will
not start.

**Both fallback paths had to be fixed, not one.** The first attempt only
covered `executable is None`, so the case that actually happens -- a system
browser that exists and will not run, which is Ubuntu's snap wrapper and is
already documented above -- still landed on the shell. Caught by running it:
the log said `would not start ... using Playwright's own` and the probe still
read `window.chrome: undefined`. Proved afterwards with a stub browser type
that only records which executable it was handed, over all four cases: nothing
asked for, a system browser that works, one that fails, and the full Chromium
itself refusing.

**What is NOT done is forging the rest.** `window.chrome` and a plugin list can
be synthesised and this does not do it: that is anti-detection work, nobody
asked for it, and the project has already paid three times for changes nobody
asked for. What is done instead is offering the route that fits the device --
`user_agent` is now an add-on option so a panel can ask for **YouTube's
television interface**, where a code typed on a phone replaces a password
entirely. Not verified: there is no route to any Google service from here. What
is verified is that the option reaches the browser and that the string given is
what a server receives.

**The `DRM yes/no` field was wrong twice over, and a Netflix report is what
exposed it.** `report_media()` printed it from whether
`navigator.requestMediaKeySystemAccess` exists. Measured on the shipped build:

| | API exists | Widevine |
|---|---|---|
| `about:blank`, which is the page it ran on | **no** | no API at all |
| a secure context | yes | **refused: NotSupportedError** |

So the line said `DRM no` for every browser whatever it carried, and a `yes`
would still not have meant what a reader takes it to mean: the API's existence
and Widevine's availability are different questions, and Netflix needs the
second. A field that is always no, and whose yes would mislead, is worse than
no field.

`report_drm()` replaces it: asked once, on the first page that is a **secure
context**, for `com.widevine.alpha` specifically, and silent everywhere else --
a "no" from an insecure page would be a lie. Tested on three states: nothing
said on about:blank, one line on a secure page, nothing on the same page again.

The answer for a panel is that **Chrome carries Widevine and nothing else here
does**, and Google publishes no arm64 Linux build -- so a Home Assistant box on
a Pi falls to the distribution's Chromium and cannot play Netflix at all. That
is not something to engineer around; it is something the log should say in one
line instead of costing a diagnosis.

**Netflix's sign-in is reCAPTCHA, and the page says so itself.** A photograph
of a panel pointed at `https://www.netflix.com/fr/` -- the right address --
showing *"Un probleme est survenu. Veuillez reessayer dans quelques minutes"*
with the email field **still empty**, and along the bottom, in Netflix's own
words: *"Cette page est protegee par Google reCAPTCHA pour nous assurer que
vous n'etes pas un robot."*

Nothing had been typed, so it is not the password, not the keyboard and not
the DRM: reCAPTCHA v3 scores the browser silently on load and a driven one
scores badly. Same wall as the Google sign-in, same supplier. Defeating a
CAPTCHA is not something this project does, and that is a line rather than a
difficulty.

The useful thing is the ORDER of the two walls, because the second makes the
first pointless: **Widevine first**. With no Widevine -- which is every arm64
box, since Google publishes no Chrome for it -- signing in buys nothing at
all, so `report_drm()`'s line is what to read before spending an evening on
`import_profile`. And even with both solved a browser gets Widevine **L3**,
which Netflix limits to standard definition, over the full-motion path that
was just measured as the expensive one.

**CORRECTED BY A PANEL: "Large effort, poor result; Jellyfin is the better
answer and needs none of it" stood here, and it is wrong.** Reported as
*"Netflix fonctionne correctement car il fonctionne sur chrome tu oublier et
Widevine bien present"*. On an **amd64** Home Assistant box `playwright
install chrome` succeeds, `SYSTEM_BROWSERS` prefers `/usr/bin/google-chrome-
stable`, Widevine is there and Netflix plays. That is most Home Assistant
machines, not an edge case.

Every FACT in the paragraph above survives -- arm64 really has no Chrome, L3
really is standard definition -- and the CONCLUSION drawn from them did not.
That is the shape this file already names as the hardest kind to spot: a
correct fact with a wrong conclusion attached, because re-reading it confirms
the fact and never re-asks the conclusion. It was written when a Pi was the
assumed box and nobody re-asked it when Chrome started being installed.

**And it was repeated in chat, to the user, about their own working setup** --
the arm64 caveat stated as a flat wall on a panel that had been playing
Netflix all along. The narrow rule: `report_drm()`'s line and
`Browser: running ...` are the ground truth for a given box, and neither was
consulted before answering.

**And `keepalive` does not exist in this project.** Asked as *"je vois que
keepalive n'est pas dans les link"* -- it is `keep_profile`, and it is a panel
setting because a profile belongs to a browser and there is **one browser per
panel**. Every link a panel opens shares it, which is exactly why signing into
one site stays signed in when the corner brings the panel home and it goes
back. Per link it would have no meaning.

**"Est-ce que le son va suivre" is the right question about a frame limit, and
the answer is that it is not tied to the picture rate at all.** The capture is
`parec` on a null sink at 48 kHz in 20 ms blocks, so it runs at real time
whatever `--fps` says; `fps` governs only how often a picture is made. Lowering
it *helps* the sound, because the pictures it stops sending were being thrown
away anyway and the room they free is room the audio was competing for --
`_drain_audio()` already runs between the rectangles of a picture rather than
behind them.

What could not be answered was "is it actually getting through", because
`--stats` said nothing about sound at all. It ends with `sound N/s` now, and
`lost` when blocks were dropped: the deque is `maxlen=25`, half a second, and
a full one discards its OLDEST on append -- the right end to lose, and exactly
why it has to be counted at the moment it happens rather than noticed later.
50 a second is the whole stream; fewer means the browser produced less.

Tested against a stub endpoint over the three states: a link keeping up (10
offered, 10 sent, 0 lost), a link that stopped taking anything (40 offered
into 25, **15 lost**), and the same queue draining afterwards (25 sent).

**And the first version of it would have crashed every panel running
`stats`.** The line is a bare `print(f"...")`, not a variable, and the sound
was appended to a `line` that does not exist -- a `NameError` five seconds into
any run with the option on, which is the option somebody uses precisely when
something is already wrong. Caught by reading the surrounding code before
believing the edit; the whole thing is assembled into `line` and printed once
now.

**The sound counter found a fault on its second day, and it was ours.** A
user's log, on a panel showing a still page:

    0.0 pictures/s, 0.0 made/s, 0.0 rectangles/s, 0 whole, 0.0 KiB/s,
    panel wait 0%, 0 skipped, worst gap 0 ms, loop 116.5 Hz, sound 50/s

Fifty blocks a second is the whole stream, and the page was playing nothing --
so that is **93.8 KiB/s of digital silence**, sent for ever, at a panel that
had nothing to show either. It contradicts the property this project
advertises loudest: idle traffic 0.0 KiB/s. The `KiB/s` field counts pictures
only, which is why nothing had ever shown it.

`take()` drops a block that is **exactly** zero. Exactly rather than a
threshold, because a null sink with nothing playing gives exact zeros and
anything quieter than exact is somebody's quiet passage. Tested on five cases:
all silence sends nothing, all sound sends everything, silence then sound
sends the sound, a passage one sample away from silence still goes out, and so
does the first block of a sound.

The `sound` field disappears from the stats line over a silent page rather
than reading 50/s, which is the honest reading of what is happening.

**Lip sync is still not done and is now written down as such.** Nothing
timestamps either stream, so the sound arrives slightly ahead of its picture by
roughly what the picture path costs. Not measured on a panel. What a lower
`fps` does help is the *variation*: an 898 ms gap between pictures is far more
visible against steady sound than a constant small offset.

**Quality was not the lever, and the user's own two attempts are what proved
it.** Reported as *"malgre mis en qualite 20 c'est saccade"*, with the line:

    18.4 pictures/s, 22.2 made/s, 18.4 rectangles/s, 92 whole, 918.6 KiB/s,
    panel wait 42%, 20 skipped, worst gap 898 ms, worst turn 23 ms, loop 94.3 Hz

Against the same panel browsing the television interface minutes earlier --
**1429 KiB/s at `panel wait 1%`, 0 skipped, worst gap 93 ms** -- it now
saturates carrying **fewer** bytes while waiting forty times as much. That
single comparison is the whole diagnosis: **bytes are not what runs out.**

What changed is `92 whole` for 92 pictures. On full motion every picture is a
whole panel, and a whole panel is a fixed cost the board pays each time -- one
decode of the entire screen and one write of the entire screen -- whatever the
JPEG weighs. Lowering the quality shrinks the JPEG and leaves that cost
untouched, which is exactly what 40 -> 20 measured.

`worst turn 23 ms` and `loop 94.3 Hz` rule out the machine running the sender,
and `20 skipped` in five seconds IS the stutter: 22 made, 18 taken, four
thrown away unevenly, hence `worst gap 898 ms`. **A steady 15 beats an erratic
18.**

`--page-fps PREFIX=FPS` and `fps:` on a link, the third setting to belong
there after `quality:` and `user_agent:`. One lookup per turn of the loop
rather than per picture, since the limit is consulted before a picture is
released. A capped link caps the URGENT window too: lifting it to
`urgent_fps` for two seconds after every contact would put the stutter
straight back the moment somebody brushed the glass.

**And it corrects something written here two days earlier.** Drawing smaller
was called "the strongest lever by a distance" -- true of the SENDER's time and
of the bytes, both measured, and both irrelevant to this bottleneck. The
board's write to the display is panel-sized whatever the render size is, and
with a PPA pass added the decode saving is partly given back. Neither the
sender nor the link is what is short here, so that lever would not have moved
this number. The ranking only ever holds against a measured bottleneck.

**The phone is the remote, and it was already working while a D-pad was being
designed for the panel.** Reported as *"le partage de youtube caste fonctionne
meme si c'est saccade"*. Once the panel is paired, the YouTube app lists it as
a device and sends it a video; the browsing happens on the phone, where the
account and the subscriptions already are.

So the on-screen remote is **not built and should not be**. The interface that
needed one is driven from a device that has one, and the panel goes back to
being what it is: a screen. Two rounds of design went into a D-pad before this
was said, which is the usual lesson -- ask what the user already does before
building the thing that would let them do it differently.

What remains is that a cast video is *saccade*, and that is arithmetic rather
than a fault. Full motion means every picture is a whole panel: at 800x1280
and quality 40 that is roughly 70-100 KiB a picture, so 25 a second asks for
1.8-2.5 MB/s -- against a radio measured at 25 Mbit/s and a busiest-ever
recorded window of 2.5 MB/s. The board is not the limit and Espressif's own
figure says so: the P4's JPEG decoder does 1080p at 30 fps, far above anything
sent here.

The levers, in order of effect:

- **`quality:` on that link.** Already exists, costs nothing, and video hides
  compression far better than a dashboard does.
- **Drawing smaller and letting the PPA scale up.** Measured elsewhere in this
  file: 400x640 into 800x1280 is 7.5 KiB a picture against 18.0, and 1.7 ms of
  sender time against 8.7. It is the strongest lever by a distance -- and it
  is currently **panel-wide**, because the board sizes `rgb_buffer_` for the
  render size once in `setup()`. Per-link would mean allocating for the panel
  size and taking the scale from each frame's own dimensions, which is C++ that
  cannot be compiled or tested here. Identified, not built.
- **Not H.264.** The P4 encodes it in hardware and decodes it only in
  software, so changing the wire format moves the cost onto the cores that are
  currently idle by design. JPEG per frame stays right.

Nothing on GitHub is worth adopting: `plaincast`, `shanocast`,
`cast-from-container` and `mirrorcast` all go the other way -- casting FROM a
machine TO a device, or audio only. There is no panel-as-receiver project to
borrow from, which is worth recording so it is not searched for again.

**The television pairing does NOT sign the ordinary site in, and predicting
that it would was a deduction standing in for a measurement.** The reasoning
was that a cookie belongs to a domain rather than a path, so `/tv` and `/`
share one jar -- true about cookies, and beside the point. Reported from the
panel: the television tile is signed in, the ordinary tile is not, same
profile, same moment.

So the pairing authenticates the television **app**, not the browser's Google
session -- whatever it stores is scoped to that interface, not the
cookie-based session the ordinary site reads. Written here because it will be
guessed again, and because it was stated confidently in chat before anybody
looked.

**And the two tiles side by side are the controlled experiment this whole
video thread never had.** Same panel, same network, same minute: the
signed-in television interface plays, and the anonymous ordinary site loses
throughput and then loses the video -- which is exactly the `emptied` shape,
the advertisement being verified. Nothing about the link, the board or the
sender differs between the two. The account is what fixed the stopping video,
and this is the proof.

The consequence is that a panel wanting YouTube stays on the television
interface, which needs arrow keys, OK and Back -- so the remote is no longer
an idea to weigh against alternatives. The alternatives are closed.

**A tablet is what the panel actually is, and the browser was denying the
touchscreen.** Proposed by the user after the television interface turned out
to need a remote: *"je pense qu'il serait plus interessant comme une tablette
androide de haut de gamme"*. It is the better answer of the three -- 800x1280
in portrait is a ten-inch tablet, where a phone string gives a narrow column
and a television string gives an interface no finger can drive.

It also exposed a plain inconsistency. `navigator.maxTouchPoints` read **0**
on a device whose only input is a finger. Harmless on a desktop page; on a
page told it is talking to an Android tablet it is two statements that cannot
both be true, which is the client-hints fault in another costume -- and a site
that resolves it by serving the desktop layout would have made the user's test
fail for the wrong reason.

`has_touch` is now on unless `--no-touch`. The thing to check before offering
it was whether it breaks the way contacts are replayed, since a site that
switched to touch handling would stop hearing our mouse events. Measured, with
and without a tablet user agent: a press still fires **pointerdown, mousedown,
mouseup, click** in that order and `mouse.wheel` still scrolls the same 200
pixels. Pointer events are the modern unified path and fire either way; a page
listening only for `touchstart` would not be reached, and none has been seen
doing that.

**The user agent belongs to the LINK, and asking why it did not was right.**
Put as *"pourquoi tu la pas mis dans link"*, after a panel-wide one produced
*"pret a castrer"* on the panel. Both halves of that are findings.

**`CrKey/` is a Chromecast, and a Chromecast is a RECEIVER.** YouTube served
the idle "ready to cast" screen, waiting for a phone to send it something --
not the television interface with a sign-in code on it. A smart-television
string is what asks for that. Recommending the Chromecast string was a guess
dressed as a recipe, and the panel corrected it.

`--page-agent PREFIX=STRING` mirrors `--page-quality` exactly, and `links:`
gains `user_agent:` beside `quality:`. It is applied to the **request**, by
`page.route` registered only for the configured prefixes -- not by noticing
the address afterwards, which cannot work here: `youtube.com/tv` is a junction
rather than a page, so by the time the address can be read the redirect has
already happened and the address is somewhere else. An init script matching
the same prefixes covers what the page's own scripts read, so the header and
`navigator.userAgent` cannot disagree.

**And it shipped a silent hang for one run, which is the lesson.** Playwright
reads a route handler's ARITY: a two-parameter handler is called as
`(route, request)`, so `def handler(route, agent=agent)` had its `agent`
replaced by a Request object. `continue_(headers=...)` was then handed
something that will not serialise, the route was never released, and the page
never loaded at all -- which on a panel is YouTube simply never opening. The
`except` fallback did not save it either. Nothing about it is visible from
reading the code; the test server never logged a request, which is what found
it. A closure, not a default argument.

Verified against a server that redirects the way that one does: the television
link is served the television page and `navigator.userAgent` agrees, an
ordinary page on the same panel is untouched in both.

**And then confirmed from the panel, which is what closes this whole thread:**

    Media: playing: <ytlr-player__player-container-player> t=13.6 ready=4
           net=2 playing page=visible focused buffered=6.4s frames=345 dropped=0

`ytlr` is YouTube's own prefix for its television renderer, so that element
name is the proof the interface is the right one -- it exists neither on the
ordinary site nor on the Chromecast screen that `CrKey/` produced. The account
signed in with a code typed on a phone, no password on the panel and no
keyboard. `dropped=0` is the panel keeping up.

So the ranking that came out of this is settled by measurement rather than
argument: **a code typed elsewhere** works and is what a panel wants;
`import_profile` works and is too many steps; and defeating Google's sign-in
check was never attempted and never needed to be.

**A code typed somewhere else is the shape that fits a panel, and the user
said so from their own life:** *"la connexion par telephone est la bonne comme
je fait avec jellyfin connexion rapide il me donne un code est je inscrit dans
jellyfin de mon server Unraid pour autorisation"*. That is Jellyfin's Quick
Connect, and it needs **nothing from this project** -- it is on Jellyfin's own
sign-in page, the panel shows a code, the code is typed into the server. No
password on the panel, no keyboard.

It also settles the ranking of everything below. `import_profile` works and is
measured, and it is still three steps with an obscure flag in the middle:
*"non trop compliquer pour les utilisateur"*. Keep it for whoever wants it,
and lead with the code.

**And a panel-wide `user_agent` was bad advice as given.** It is set once on
the page's CDP session and persists across navigations, so a panel told to say
it is a television says it to Home Assistant and to the launcher as well. The
documentation now says to put it in that panel's own entry and to look at the
dashboard afterwards. Whether the frontend minds is untested. If it does, the
fix is a user agent per LINK, the way `quality:` already is -- which is the
user's own pattern, and the reason not to build it yet is that nobody has
asked and it is not known to be needed.

**"But a Raspberry Pi with Chromium can sign in" is the observation that
solved it, and it was right.** Put as *"mais pourtant avec un RPI ont peut ce
connecter sur youtube il dispose de chronium"*. It is the same Chromium -- so
the difference is not the browser, it is that a person is driving it, with no
automation attached and no `--headless`. Google blocks the mode, not the
software.

Which points somewhere this had not looked: **Google checks the signing IN.**
Afterwards the session is a cookie, and nothing about a cookie cares how the
browser that receives it is driven. Measured -- a cookie written by a plain
chromium process, launched as an ordinary subprocess with no Playwright, no
CDP and no automation of any kind, is sent by the automated browser opening
the same profile directory.

`import_profile:` is that, as an add-on option: a folder under /share,
/config or /media, **copied** into the panel's own profile. Copied rather than
used where it lies for two reasons -- those mounts are read-only and a browser
must write to its profile, and the profile is the panel's from then on. Only
ever into an EMPTY profile, or a panel that had since signed into something
else would lose it on the next start.

**`--password-store=basic` is the step it fails silently without**, and it is
in the documented recipe for that reason: Chromium encrypts cookies with a key
from the desktop keyring when there is one, and that key never leaves that
machine, so the folder would copy across and decrypt to nothing. A container
has no keyring and uses the fallback key, so the signing-in browser has to be
told to use it too.

Verified end to end against a local server -- plain browser signs in, add-on
copies the folder, automated browser sends the session -- and `seed_profile`
on six cases, of which the one that matters most is a profile the panel
already has, which must not be replaced. What is NOT tested is Google's own
session in particular: there is no route to it from here, and a session may be
tied to a machine more tightly than a plain cookie is.

**A page can be served somewhere other than where it was asked for, and the
log could not say so.** Reported after trying the television interface: *"j'ai
tester https://www.youtube.com/tv dans link il m'affiche une page youtube avec
un commentaire qui vous dirige vers la page video youtube"*. That is the
ordinary shape of a user-agent redirect -- `youtube.com/tv` serves the
television page to a television and sends everyone else to the normal site --
and from a log it was indistinguishable from the address simply being wrong,
because the only line about the address was `Opening <url>` before the
navigation.

`open_page` now prints `Arrived at <where> (asked for <what>)`, and only when
the two differ with a trailing slash discounted, so an ordinary panel gains no
line. Tested against a server that redirects exactly the way that one does:
the line appears without the television user agent, and nothing is printed
with it or on a page that did not move.

**Google's refusal was confirmed from the panel, and the message names the
mechanism.** A photograph of the panel: *"Impossible de vous connecter -- Ce
navigateur ou cette application ne sont peut-etre pas securises."* That is
Google's block on browsers it can tell are driven, and it is not a fault in
this project. The command line was read the way `--mute-audio` was found, and
it settles one hypothesis: Playwright passes **no** `--enable-automation` and
no `--disable-blink-features` -- only `--headless` and
`--remote-debugging-pipe`, out of 55 arguments. There was nothing there to
remove.

**And recommending the television interface exposed a real fault in the
disguise.** `_agent_metadata()` built the client hints from the browser's OWN
`Browser.getVersion`, never from the string being claimed. So a panel given a
Chromecast user agent sent `Chrome/85` in one header and `"Chromium";v="141"`
in the other -- reproduced against the old code before the fix was believed,
85 against 141. It is the same class of fault as sending no hints at all, and
this project had already paid for that one.

The hints now follow whatever is being claimed: a Chrome string gets matching
brands, and a string that is not a Chrome at all -- a Tizen or webOS
television -- gets **no hints**, which is what such a browser really sends.
Verified at a server that logs its headers, all three cases consistent.

**A measurement of the browser cost read 0.2 frames a second at first**, which
is nonsense, and the cause is a lesson this repository already contains:
`Page.screencastFrameAck` was being sent from inside the frame handler. Acks go
from the loop. The corrected run reads 59.6.

**A profile on disk is what makes signing in worth doing.** Without
`--profile` the browser is launched into a directory it throws away, so every
restart is a first visit: a site signed into is signed out, and a consent
banner is back. `launch_persistent_context` keeps cookies, local storage and
the rest. **One directory per panel and never shared** — Chromium locks a
profile and a second browser pointed at the same one will not start. The add-on
puts them under `/data/profiles/<panel>`, which is its own persistent volume.
Verified by writing a cookie and a local-storage entry and restarting the
sender: without a profile the second run found `rien` twice, with one it found
`cookie + localStorage`. It also changes how the browser is launched — a
persistent context has no `Browser` object — which is why the version and the
user agent are now asked of a page's CDP session in `present_browser()`.

**A panel does not have to show Home Assistant, and `--token` is the switch.**
Nothing in the pipeline is particular to Home Assistant: the token exists only
because a browser with no keyboard cannot get past a login screen. Given one,
the sender writes it into storage and waits for the `home-assistant` element;
given none, it does neither and renders whatever the URL points at. In the
add-on the token is a *shared* setting, so a panel showing an ordinary site
would inherit the house's one: `home_assistant: false` on that panel is the
switch, and all it does is drop the token before the sender is started.
`--no-token` is the same thing for a hand run where `$HA_TOKEN` is set.
Verified
end to end against an ordinary page with a CSS animation, a canvas and a
button, 85 pictures at 19.4 rectangles/s. Leaving the token out is the ask, not
a mistake: it used to be a hard `parser.error`, and the thirty-second wait for
a dashboard element that was never coming was the other half of the problem.

**Token injection.** The long-lived token is written into local storage the way
the frontend writes it after a login, which is what gets a browser with no
keyboard past the login screen. Home Assistant frontend fields live in nested
shadow roots: `focusin` `composedPath()[0]` is the reliable target, and values
must go through the native setter plus a composed bubbling `input` event for
Lit/Polymer to notice.

**The keyboard is not something the page can touch, and that is the whole
design.** Four attempts failed because the keyboard was built as a widget: it
took focus, so the field lost it. The fifth is inert decoration —
`pointer-events: none`, no listeners, no focus, nothing. A contact that lands
inside its rectangle is intercepted by `Injector` and never replayed as a
click, so the page is never told it happened and the field keeps its focus.
The character is delivered with `page.keyboard`, which puts it into whatever
has focus without needing to find the element — so the shadow-root problem that
made the earlier attempts hard does not arise at all.

Three consequences worth keeping:

- **It goes in the top layer through the popover API**, and the fallback for
  a browser that refuses one needs `z-index: 2147483647` *and* a transparent
  `dialog::backdrop`. Shipped once without either: the keyboard was drawn
  under Home Assistant's shell, and the report was *"il est invisible est
  quand je touche en bas des lettres s'affichent dans la recherche"* — which
  is the signature of this whole class of fault. The keys keep working
  because the hit test is arithmetic in the sender and never asks the page
  what is on top of what, so **the keys working proves nothing about the
  keyboard being visible**. The `::backdrop` half is separate and was measured:
  a modal dialog dims at 60%, so the keys came through at 40% of their colour,
  (16,17,21) against (42,44,52), which on a dark dashboard reads as not drawn
  at all. `_show()` returns whether the top layer was reached and the sender
  says so in the log, once per page, along with the overlay's rect, display,
  z-index and the number of open modal dialogs.
- **Not on a large z-index alone**, when the popover is available. Home Assistant opens its dialogs as native modals and the top layer
  paints above every z-index there is. A `popover="manual"` is in that same
  layer *without* making anything inert, which is exactly the distinction the
  fourth attempt got wrong. `showPopover()` is called again on every sync, even
  when it is already showing: the top layer stacks in entry order, so a dialog
  opened after the keyboard would paint over it. Measured by reading the pixel
  at a key's centre with a modal dialog open: (37,39,46) before re-stacking,
  the full (42,44,52) after.
- **`hide()` sets `display:none` INLINE, so `show()` has to clear it.** An
  inline style beats every selector in the sheet, so the one line that failed
  to clear it made the keyboard unshowable for the rest of the page's life —
  reported as having to restart the add-on, which was the only thing that ever
  gave it a new document. Its twin: `Hide` left the field focused (deliberately
  — putting the keys away is not giving up on what was being typed), so the
  next look found something typable and set `visible` back to true over an
  overlay that could no longer be drawn. A keyboard nobody could see, eating
  every tap along the bottom of the screen. `dismissed` is what `Hide` now
  sets, and `note_tap` clears it on any tap **above** the keys — never inside
  the band, or a keyboard put away to reach what was under it would spring back
  the moment that thing was touched.
- **The overlay is built out of the DOM, never out of an HTML string, and
  its stylesheet goes in through the CSSOM.** YouTube requires Trusted Types,
  where `innerHTML = ...` throws `This document requires 'TrustedHTML'
  assignment` — and that took the *whole sender* down every thirteen seconds,
  restarting for ever, over an overlay whose absence nobody would have minded.
  Google, GitHub and many banks set the same policy. `createElement` +
  `textContent` cannot be refused by any policy, and a constructable
  `CSSStyleSheet` cannot be refused by a `style-src` either. The wider lesson
  is the safety net that came with it: **the keyboard is an accessory and must
  never cost the picture.** `_show()` sets `broken` and carries on, and every
  keystroke goes through `_Safe`.
- **A label has to be in some language; a symbol does not.** The erase key
  was `Back`, and was reported missing outright — *"tu as oublie une touche
  celle de supprimer je le trouve pas"* — by somebody looking at the keyboard
  it was on. It is `⌫` now, at the letter size rather than the small size the
  word keys use, because an erase key shrunk to fit the word Shift is one
  nobody finds. Checked against tofu before shipping: a glyph with no drawing
  measures the same width as U+FFFF, and this one does not.
- **The sender owns the geometry.** The same numbers position each key and
  decide which key a contact hit, so the drawing and the hit test cannot drift.
  Keys are drawn inset by `GAP` and hit whole, so a finger on a seam still
  presses something.
- **A keyboard that does not come up and a page with nothing to type into are
  the same silence, so the sender now breaks it.** Every report of it "not
  appearing" cost a round trip to find out what had been tapped.
  `focus()` returns the road to whatever holds focus alongside the yes/no, and
  a road that leads nowhere typable is printed once per distinct road, capped
  at eight. Measured against a **real** Home Assistant frontend — installed
  and onboarded for the purpose, because pages merely shaped like it had
  stopped being evidence — the road to its search field is seven elements and
  every step is a shadow root: `HOME-ASSISTANT > HOME-ASSISTANT-MAIN >
  HA-CONFIG-ENTITIES > HASS-TABS-SUBPAGE-DATA-TABLE > SEARCH-INPUT >
  HA-TEXTFIELD > INPUT`. The whole loop was then run against it with a fake
  panel and the keyboard came up correctly, which is what turned "it does not
  work on Home Assistant" into "it works on the pages I can reach, and the log
  will name the one I cannot".
- **`BLUR_GRACE_S = 0.4`, because a blur that lasts a frame is not somebody
  putting the keyboard away.** Home Assistant's tables and dialogs re-render
  and a field can lose focus while they do. Taking the keys down and putting
  them back is a whole panel of change each way, and from the other side of
  the glass it reads as a keyboard that will not stay.
- **The page says when focus moves, and every frame is asked what has it.**
  Looking only after a tap was the first design and it was wrong twice over.
  A tap is indeed the only thing that moves focus, but what a tap *starts* can
  finish much later than the 450 ms it was given — a dialog that animates in,
  an editor that takes focus once its document has loaded. And
  `document.activeElement` in the top frame is the `<iframe>` element, not the
  field inside it, so **every Home Assistant ingress add-on** — File editor,
  Terminal, anything with a web interface — reported "nothing is waiting for
  text". Reproduced with a fake panel: the keyboard never appeared at all. So
  a `__udispFocusChanged` binding is installed on the *context*, which puts it
  in every frame, and `sync()` walks `page.frames` and takes the first yes.
  The tap-triggered looks stay as a fallback.

Tested end to end against a fake panel sending real contacts, both layouts: a
plain field, a field in a shadow root, a field in a native modal `<dialog>`,
shift (one capital then back to lower case), backspace, Enter, the accented
keys, and a full-width button underneath the keyboard that must never be
clicked and never is.

**A sleeping panel still costs the server, and stopping the picture is not
what stops it.** `Screencast.pause()` only stops the pictures; the page goes on
painting and running its timers for a screen nobody can see — measured with the
panel dark, 59.8 animation frames a second and 20 timer callbacks a second.
Neither obvious lever touches it: 58.5 with the renderer throttled twentyfold
through `Emulation.setCPUThrottlingRate`, 59.8 with the page declared frozen
through `Page.setWebLifecycleState`. Only navigating away does: 0.0 and 0.0.

Two things were done about it, and they are independent:

- **`SLEEP_PUMP_MS = 100`** — the loop's beat while the panel is dark. At
  `PUMP_MS = 8` it is 125 round trips a second through the browser's protocol
  for a panel that is being sent nothing. This is free and needs no option.
- **`--blank-after`**, default 300 s — past that, `page.goto("about:blank")`.
  The delay is the design: a panel woken inside it never gave its page up, so a
  short sleep stays instant, and only one dark long enough that nobody is about
  to look at it pays the reload.

Measured over ten seconds of sleep, CPU of the whole process tree: **1.73 s
before either change, 0.98–1.11 s with the slower beat alone, 0.15–0.17 s with
both** — 90% off the original. Waking costs 0.10 s unparked against **3.12 s**
parked, and those three seconds show the dashboard as it was rather than a
black screen, because the board still holds the last picture it was sent.

`install_token` uses `context.add_init_script`, and so does the keyboard, so
both survive the round trip to `about:blank` and back with nothing to redo. The
animation freeze does not — `Animation.setPlaybackRate` is re-applied after a
reload.

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
painted before the finger landed.

**How early it acknowledges is the whole difference between paced and jerky.**
It was one pump, eight milliseconds, and a paint plus an encode takes twenty to
forty: every picture therefore landed *after* the deadline it was meant for and
went out on the next one. Measured against a page that never settles, pictures
actually reaching a fake panel at `--fps 30`: **26.3/s** from a free-running
sender against **20.9/s** paced — which is the whole of a report that an older
copy of this project, kept aside, was smoother on video and animation. It is
the same architecture and an earlier version of the same files, so the
difference had to be something added since, and it was. `Screencast.lead` is
now the measured time from acknowledgement to arrival, smoothed across frames,
and the acknowledgement goes out `1.5 × lead` before the deadline; the margin
is there because being early costs a picture waiting a few milliseconds in
hand and being late costs a whole interval. That gives **24.9/s against 26.2**,
and matches at `--fps 10`. The saving is untouched: 6.0 made/s at `--fps 4`
against the 59.5 of free-running, 34% waste against 93%.

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

**The write is not allowed to stop the loop, and the kernel is not allowed to
hoard.** These are two halves of one fault and both had to move. `sendall`
does not return until the board has taken the bytes, and it is called from the
loop, so while it waits *nothing else happens at all* — no browser pumped, no
contact read, no picture made. And Linux, left alone, grows a send buffer into
the megabytes, so a sender producing faster than the link drains does not block
at all: it fills that buffer, and every picture in it is seconds old by the time
the board decodes it. Together they are exactly the report — *"des lags toutes
les 3 secondes et des fois il ce fige et reprend"*.

Measured against a fake panel draining at 500 KiB/s with a third of a second of
outage every three, which is what a Wi-Fi radio that goes away looks like:

| | worst turn | loop | worst gap |
|---|---|---|---|
| as it was | **3007 ms** | 25.3 Hz | 479 ms |
| send buffer bounded only | 479 ms | 48.4 Hz | 479 ms |
| bounded + written from a thread | **68 ms** | **85.9 Hz** | 461 ms |

A single turn of the loop took three seconds. `PanelWriter` moves the write to
a thread holding **one whole picture** — all-or-nothing, because a rectangle is
never resent — and the loop asks `ready()` before it decodes anything. A link
that cannot keep up therefore costs *pictures*, which is the right thing to
lose, and `--stats` counts them as `skipped`.

**`SO_SNDBUF` was capped at 65536 alongside it, and that half was wrong and is
gone.** The argument for it was that 65536 is the board's own receive window,
so anything above it is pure queue and capping it costs no throughput. The
argument was never measured against a fast link, and it was a ceiling the user
had not asked for: reported as *"tu fais ce que je t'ai pas demandé, tu réduis
le débit ?"*, with a VLC capture showing the board sustaining **25 932 kb/s,
3549 frames, 0 lost, 0 corrupted** — 130 KiB pictures at 25 a second. Removed.

The thread is the half that was worth keeping, and it is free. Measured against
1.34.0 on the same page, same run, drains at 800 / 4000 / unthrottled KiB/s:
**800.0 against 800.1, 1168.5 against 1183.7, 1187.7 against 1179.8 KiB/s** —
the same to within noise at every rate, while the worst single turn of the loop
goes from **2902 ms to 34 ms** and the loop from 48.9 Hz to 87.6.

The lesson worth keeping is not about buffers. **A ceiling nobody asked for is
a bug even when the reasoning behind it is sound**, and this one was defended
with arithmetic instead of a measurement for two releases.

`panel wait` therefore means something different now: it is the writer thread's
time, not the loop's, so it can sit near 100% without a stutter. When it does,
`skipped` is what the link is costing.

**What the radio actually does, measured by the user with VLC** against the
board serving its camera: 25 932 kb/s sustained, 460 660 KiB over the run,
3549 frames displayed, **0 lost and 0 corrupted** — which is 130 KiB a picture
at 25 a second. That is outbound and `portall` is inbound, so it is not the
same path; but it is the same radio, and it settles that the link is not what
stands between this and video at the panel's own resolution.

**Where the second actually goes, measured phase by phase.** At 30 whole
800×1280 panels a second, quality 60, the loop's own budget adds to 1000 ms/s
with 4 ms unaccounted: **pump 550, decode 185, encode+send 145, diff 65,
ack 42**. The pump is `wait_for_timeout(8)` — deliberate sleep, not work — so
the real cost is about 440 ms/s and the sender is *not* CPU-bound at 30 fps.
An earlier reading of this said it was, from timing only the long turns; long
turns bill the pump for whatever arrived during it. Time the whole window and
add a residual, or the conclusion inverts.

**`--show-touches` used to print the word `injected` and nothing else**, which
is worth recording because of what it could not answer. A panel reported that
the fifth tile of a launcher opened the one above it -- a question about
*where* a contact went and *what* was there, and the one option named for
touches said neither. It now prints the panel coordinate, the page coordinate
it maps to, and what the page has at that point:

    contact at (476,640) on the panel -> (640,323) on the page
    tap at (640,323) on a -> http://.../  [Service 5]

The last line is `document.elementFromPoint`, asked once per tap and only under
this flag, wrapped so a page that refuses costs the diagnostic and never the
picture. It settles in one line whether the mapping is wrong or the page is not
where it was thought to be.

Measured against the reported case on the panel's own geometry -- 800x1280 at
90 degrees, five links, taps computed through `TouchMap` and sent up the return
channel -- all five tiles opened their own page. So the fault is in that
panel's calibration rather than in the layout, which is exactly what the flag
now makes visible from the other end.

**`--stats` also reports `panel wait`, which is the one bottleneck the loop
cannot otherwise see.** The socket is blocking, so when the board is behind the
write stalls inside `sendall` and *nothing else happens at all* — no browser
pumped, no contact read. Reading true was checked against a fake panel drained
through a token bucket: **0%** at 4000 KiB/s, **55–60%** at 350. Worth having
because it settles an argument in one line: a stutter with `panel wait` near
zero is not the panel and not the network, whatever it looks like.

**`panel wait` printed 538%, which is a number that cannot exist.** A write
was credited entirely to the window in which it FINISHED, so a panel that took
half a minute to accept a picture showed nothing for four windows and then an
impossible percentage in the fifth. Reported from a panel on YouTube, where
the stalls are longest, and it is the one line that is supposed to settle
whether the link is the limit -- so a figure nobody can believe costs the whole
diagnosis.

The writer now records when a write began and `take_blocked()` counts the part
that has already elapsed, moving the mark forward so the rest belongs to the
next window; the print is bounded at 100% as well, because a number that cannot
be true is worse than no number. Reproduced against a panel that stops reading
for twelve seconds, three stats windows long: **146% before, 90% then 59% then
1% after** -- the stall spread across the windows it actually spans.

`worst gap` and `worst turn` are beside it for the same reason one step
further on: a five-second average hides a tail completely, and a stutter *is* a
tail. They are the longest a picture went unsent and the longest a single turn
of the loop took, which says whether a stall was the socket or something else.

Also measured while building it, and worth keeping: a *steadily* slow panel
degrades gracefully. At 4000 / 700 / 350 KiB/s the rectangles arrived at 21.5 / 17.3 /
8.6 a second with median gaps of 46 / 58 / 114 ms and **no gap above half a
second in any run**. So blocking writes throttle the sender smoothly; they do
not produce freezes. A periodic multi-second stall is coming from somewhere
else — for Jellyfin, most likely its own transcoder, which cannot direct-play
to a browser with no H.264 and serves HLS in three-second segments.

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

Measured again on the same panel after the fraction became geometry-aware:
`whole` per five-second window fell from 14–17 out of 18 pictures to 1–11, and
`rectangles/s` rose from about 5 to about 13, which is the same update arriving
in pieces instead of whole. Within that one run, at an unchanged 3.4–3.8
pictures a second, the counter accounts for nearly all of the cost: windows at
`0 whole` cost **16–30 KiB/s**, at `1 whole` 46–51, and at 6–11 whole 295–422.
So the two regimes are worth keeping apart — where the change is genuinely
small but scattered it went from ~338 KiB/s to ~20, and where the camera is
really refreshing it is about a quarter better, 132 KiB a picture down to ~100.
That second one is real change and no sender setting will remove it. The
before-and-after is across two runs of a live dashboard rather than a
controlled A/B; the within-run table is the solid part.

The risk this trades into is the board's: about 13 rectangles a second instead
of 5, each costing it a header, its own JPEG tables and one more DMA transfer.
The board's own log line is where that shows — `dropped (… decode …)` climbing
means the real fixed cost is above the 1.5 ms this is built on, and
`--rect-cost 0.14` is the step back before 0.18.

**The host may draw smaller than the panel, and the PPA scales it up.**
`render_width:` / `render_height:` on the component, `--render-width` /
`--render-height` on the sender, and they must agree. What it buys is measured
on a dashboard-shaped picture, per picture, on the machine running the sender:

| drawn at | decode | diff | encode | total | bytes |
|----------|--------|------|--------|-------|-------|
| 800×1280 | 7.1 ms | 1.1 | 0.4 | **8.7 ms** | 18.0 KiB |
| 640×1024 | 2.3 ms | 0.7 | 0.3 | **3.3 ms** | 13.0 KiB |
| 400×640  | 1.3 ms | 0.2 | 0.2 | **1.7 ms** | 7.5 KiB |

38% of the work at 1.25×, better than the 64% the pixel ratio suggests, because
the decode dominates and does not scale linearly. The board pays one PPA pass
it was not making before, on silicon that was idle.

**Not every size divides.** A rectangle arrives in the host's coordinates and is
multiplied by panel/render; if that is not exact the rectangles stop meeting.
Checked with a standalone g++ harness over every tile of every candidate:
533×853 into 800×1280 — the 1.5× that was very nearly offered — leaves **2079
panel pixels no rectangle ever covers**, a scatter of stale pixels that only
the thirty-second redraw clears. 640×1024 and 400×640 are exact, gap-free and
overlap-free. The rule the schema enforces: an exact integer ratio, or a render
size on the 64 grid that divides `64 × panel`. Rotation together with scaling is
refused rather than guessed at — the PPA does both in one pass, but that
combination has never run on a board.

**`rect_cost_fraction` takes the PANEL size even when drawing smaller.** The
rule protects the board, and the board's cost for a whole picture barely
shrinks: the decode does, but the accelerator's pass and the write to the
display are still panel-sized. Judging by the render size makes the fraction
larger (0.165 against 0.106), saturates it at seven rectangles instead of ten,
and sends whole pictures far more often — measured on one page, **49.9 KiB/s
that way against 34.5 this way**, which is the opposite of the point.

**The accelerator's burst length was never set, and it is not a neutral
default.** The PPA and the MIPI-DSI controller read the same external memory,
and a longer burst holds it for longer at a time. Measured by this author in
`youkorr/lvgl_9.5` on the same silicon, for LVGL's *fill*: a 64-byte burst
freed enough bandwidth for the display's own fetch to stop flickering under
load, but cost throughput — lottie plus a live camera went from ~28 fps to ~17.
Their conclusion there was 128 for fill, 64 for SRM and blend.

`ppa_burst:` brings that knob here, defaulting to 64. **The LVGL answer is not
automatically this one**: there the hot operation was a fill of small areas
against a compositor, here it is one scale-rotate per rectangle against a
video-rate stream. So the board's stats line now reports the microseconds spent
inside the accelerator, next to the microseconds per draw, which is what
decides it:

    1280x800 @ 24.0 fps, 8200 us/draw (1900 in the PPA at 64-byte bursts), 0 dropped (...)

**Not compiled or run.** There is no ESP-IDF toolchain where this was written,
and `esphome config` validates YAML and codegen but never compiles C++. The
field name and both enumerators are taken from working code in
`youkorr/lvgl_9.5`, not from memory, so they exist on this IDF — but the change
itself has only been schema-checked.

**A panel really is a PC screen over Wi-Fi, and the first log from one says
where the time goes.** `wired_portall` on a Waveshare 7B, fed by
`udisp_send.py --discover` from Windows 11:

    First frame from the host: 78049 bytes compressed, 1024x600 at 0,0
    64x128 @ 6.2 fps, 4093 us/draw (3188 in the PPA), 0 dropped
    1024x600 @ 13.1 fps, 5278 us/draw (3957 in the PPA), 0 dropped
    64x408 @ 9.0 fps, 11105 us/draw (8623 in the PPA), 0 dropped

Two findings, and the first is the one that decided the project was viable.

**No `H_SDIO_DRV` at all, and `0 dropped` on every line** -- no buffer, no too
soon, no decode, no rotate. A sustained INBOUND stream is exactly what
espressif/esp-hosted-mcu#184 is about, and it did not appear. The rectangles
in those lines are the diff working: 64x128, 192x64, 64x408 are pieces of a
desktop, not panels.

**And the PPA is 72-78% of every draw**, on all seven lines of that log --
14865 us of 18964 on a whole panel, 3188 of 4093 on a 64x128 rectangle. This
was written here as "silicon that was idle", which was an assumption stated as
a fact: idle it may be, cheap it is not. The whole of that cost exists only
because `rotation:` is not 0 -- the board allocates the rotation buffer and
makes the pass only when rotating or scaling.

The Waveshare 7B cannot do it in its own hardware either: ESPHome's model for
it is declared `no_transform=True`, so the MADCTL flip that makes a 180 free
on other displays is unavailable. Checked in ESPHome's own source rather than
assumed, after its `rotation_as_transform` said a 180 "is always possible if x
and y mirroring are supported" -- which for this panel they are not.

So the turn is in one of two places and the PC is the one with cycles to
spare. That is the opposite of the advice given when `rotation:` was moved
into the YAML, and it is the log that corrects it.

**Free PSRAM standing still is what a working panel looks like, and it was
read as the opposite.** Reported as *"la psram n'est pas sollicitee meme quant
je lance une video"* -- and it is the natural reading of a log, because the
figure a log prints is how much is *free*. Nothing this component does allocates
while it runs: the decoder's RGB565 output, the accelerator's rotation buffer
and the frame buffers are taken once in `setup()` and written over for every
picture, which is precisely the design. A number that moved during a video
would mean allocation on the frame path, which is the fault, not the health.

So `dump_config()` says how much was taken as well as how much is free, and the
figure is **measured across `setup()`** rather than added up from the sizes
above it -- two of the allocations are not that file's to size, since
`jpeg_alloc_decoder_mem()` rounds to cache lines and reports its own total, and
the speaker's block buffer belongs to `audio.cpp`. On the Guition at 800x1280
with no rotation it is about 2.5 MiB: 2000 KiB of decode output and four frame
buffers of 128 KiB. A rotated or scaled panel pays another panel-sized buffer.

Only `heap_caps_get_free_size(MALLOC_CAP_SPIRAM)` is called, out of a header
`portall.cpp` already includes -- the `get_use_address()` rule again.
**Not compiled.**

**`dump_config()` opened with "USB Extended Display" and mentioned the network
as an afterthought** -- literally `Also listening on TCP port 5000` -- on
panels where the network is the whole point and the USB socket carries nothing
but power. Worse, its last line handed over `python udisp_send.py`, the USB
sender, to somebody whose picture comes from the add-on.

It is ordered by the transport actually in use now: `Portall:`, the resolution,
**Over the network** with the port and the touches, then **Over USB** with the
identifiers, and a sender line that matches -- the add-on and its geometry when
`port:` is set, `udisp_send.py` when it is not.

**It also printed the board's own address for one release, and that broke a
user's build.** `network::get_use_address()` exists in the ESPHome installed
here for validation, 2026.6.5, and in what came after it is
`get_use_address_to(std::span<char, 70>)`. The rename reached them as a
compile error on their own board, which is the worst possible place to find
one -- there is no toolchain where this is written, so `esphome config`
validates the YAML and never the C++.

The rule that came out of it: **this component calls no ESPHome helper outside
the components it declares a dependency on.** A convenience worth one log line
is not worth a build that fails on a version this cannot test.

And the version matters more than it looks. The user builds on **2026.9.0-dev**
and the validation venv here was 2026.6.5 -- three months apart, and the whole
of the difference. It is installable: `python3.12 -m venv` (dev needs 3.12) then
`pip install "git+https://github.com/esphome/esphome@dev"`. Against it, every
example passes -- `guition-10-home-assistant`, `ha-esp32p4 test audio` and
`ws-usb-screen` with `micro_wake_word` stripped for the run, the other two
whole.

Every ESPHome API this component calls was then read against `dev` one by one:
`speaker->play(data,len)`, `start`, `is_running`, `set_volume`,
`set_mute_state`, `set_audio_stream_info`, `audio::AudioStreamInfo(bits,
channels, rate)`, `display->draw_pixels_at` in its eleven-argument form,
`touchscreen->register_listener`, `TouchPoints_t`, `mark_failed(LogString*)`.
All unchanged. The only casualty was the helper that had just been added, which
is the shape of the lesson: the code that had been compiled by users was fine,
and the line written blind was not.

**"The host has not configured this device" warned at panels that were
working.** These are powered over USB-C, so a panel fed by Wi-Fi is nearly
always plugged into a charger -- and a cable carrying nothing but power looks
to TinyUSB exactly like a host with no driver. The line fired on every boot,
at full warning level, on a panel showing a dashboard perfectly.

The question it asks is now whether **anything** is feeding the panel:
`configured_ || net_client_seen_`, the second a one-way latch set where a
sender is accepted. With a sender connected it says nothing at all. With
`port:` set and nothing arriving it is an *info* line naming both halves and
saying the USB side is expected to be silent on a power-only cable; with no
`port:` at all it stays the warning it was, because there an unclaimed device
really is the fault. The patience differs too -- 30 seconds for a network
panel against 10 -- because what feeds it starts with the house: Wi-Fi, then
Home Assistant, then the add-on, then a browser.

**The stall is Espressif's own open bug, and it has a number.**
`H_SDIO_DRV: task still writing Rx data to queue!` comes from
`sdio_drv.c`, and the line under it is `sdio_rx_free_buffer(rxbuff)` -- the
received packet is **discarded**, below TCP, so TCP retransmits and backs off.
That is the whole of a panel freezing for ten seconds and then racing.

espressif/esp-hosted-mcu **issue #184 (EHM-206)**, "ESP32-P4 + C6 SDIO: Inbound
TCP transfer stalls", is the same fault reported against their own code: a
sustained INBOUND bulk transfer stalls, outbound is fine. Open, no fix, and the
workaround offered is "pace inbound reads" -- which is precisely what portall
cannot do, since a panel is a sustained inbound stream by definition.

What their documentation and Kconfig offer, with the defaults ESPHome leaves in
place:

| option | default | note |
|---|---|---|
| `ESP_HOSTED_SDIO_RX_OPTIMIZATION` | **streaming mode** | the path that prints the error; `..._RX_NONE` or `..._RX_MAX_SIZE` are the alternatives |
| `ESP_HOSTED_SDIO_RX_Q_SIZE` / `TX_Q_SIZE` | 20 | 64 is suggested in their own issue threads |
| `ESP_HOSTED_SDIO_CLOCK_FREQ_KHZ` | 40000 | **already the ceiling for a P4 host** -- their help text says so, so 50 MHz is not an option here |
| `esp32_hosted: use_psram:` | ESPHome's own | true puts the Rx mempool in PSRAM, the memory the JPEG decoder and the display controller are already fighting over |

None of this is measured here -- there is no board and no toolchain -- and all
of it is one line in a user's YAML, which is where it belongs until a
measurement says otherwise.

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

`portall/` — `config.yaml`, `Dockerfile`, `run.py`, `README.md`,
`docker-compose.yml`, `esp32p4-panel.service`, `panels.example.json`, plus
`repository.yaml` at the repo root.

**Version bump trap: `config.yaml` version and `Dockerfile`'s `ARG BUNDLE` must
move together.** Docker caches a layer on its command string alone, and every
string in that Dockerfile is fixed — the `pip install` never changes and the
`ADD` URLs never change — so a box that built the image once reused all of it
however many times the add-on was updated. First that shipped stale sender
code. Then it turned out to be shipping a stale *browser* too, which is worse:
the keyboard's top layer needs Chromium 114, and an older one draws it under
the dashboard, where it is invisible while the keys go on working.
`ARG BUNDLE` + the `RUN` that writes it therefore sit **first in the file**,
ahead of the `pip install` as well as the `ADD`s, so a bump refetches
everything — at the cost of the browser download on each update.
`present_browser()` prints the Chromium version at startup and warns below 114,
so this is never diagnosed by guesswork again. Currently **1.65.0**.

**The image carried two Playwright browsers and needed one.** `playwright
install chromium` fetches the full Chromium **and** the headless shell -- 597
MB and 323 MB, measured on the shipped install -- and a plain headless
`launch()` picks the shell. Since `_launch` started reaching for
`playwright.chromium.executable_path`, which names the full build, the shell
became a fallback below a fallback: a system browser is preferred above both.

`--no-shell` drops it, written as `--no-shell || (without it)` so an older
Playwright that has never heard of the flag installs both exactly as before.
That is a third of a gigabyte off an image that often lands on a Pi's SD card.

The second browser stays. Chrome or the distribution's Chromium is there for
the proprietary codecs Playwright's build lacks, and Playwright's own stays as
the one browser the build guarantees -- the codec install is allowed to fail,
so something has to be certain.

With no shell behind it there is nothing left to fall back to, so
`playwrights_own()` now names every browser it looked for when none will start,
rather than letting a bare "Executable doesn't exist" reach a log.

**A bump is only half the trap: work that lands AFTER one is invisible too.**
Asked from the store as *"il ya pas de mise a de addon?"* -- there was no
update offered, because the version had gone to 2.2.1 and the user agent per
link was then built on top of it. Home Assistant offers an update on the
version in `config.yaml` alone, so three commits' worth of work reached the
repository and no panel.

`tools/checkaddon.py` now asks git when the version last moved and whether
anything the image carries has changed since. What counts is read off the
Dockerfile rather than assumed -- the files it COPYs, the senders it ADDs by
URL, and `config.yaml`, whose options the Supervisor only offers on a new
version. Deliberately **not** the whole folder: `DOCS.md`, `README.md` and
`CHANGELOG.md` are read by the Supervisor from the repository, so making a
correction to one of them force a bump would mean every reader re-downloading
a browser for nothing. Reproduced against the user's exact state, in a
worktree checked out at that commit, where it names the three commits -- and
re-checked there after the narrowing, where it still does.

**Two version checks were found doing nothing at all while writing it**, and
both are the same shape of silent no-op this file keeps recording:

- The config.yaml-versus-`ARG BUNDLE` pair searched a variable called `text`,
  which the DOCS.md/README.md loop above it had reassigned. For several
  releases it looked for `ARG BUNDLE` in a README, found nothing, and passed.
  That is the one check meant to stop Docker reusing its cached layers.
- The new staleness check first used `git log -S<version>`, which matches the
  commit that REMOVED a string as readily as the one that added it. With a
  later bump in history it found that one and concluded nothing had changed
  since -- passing on precisely the state it was written to catch. It walks
  the commits touching config.yaml now and stops where the version changed.

Neither was visible from reading. Both were found by running the check against
a state known to be broken, which is the only way this class ever surfaces.

**And the image has to be told about every file, which is not the same as the
repository having it.** `launcher.py` was written beside `run.py`, imported at
the top of it, tested on its own and measured at three panel sizes -- and the
Dockerfile was never given a `COPY` for it. Everything passed: the YAML, the
Python, the launcher's own checks. The add-on then died on
`ModuleNotFoundError: No module named 'launcher'` before serving a single
panel, and it died the same way on every restart.

Nothing in the repository compared one file's imports against another file's
`COPY` lines, so `tools/checkaddon.py` does now -- it parses `run.py` for
imports that exist as files beside it and checks each is shipped, and it
checks `config.yaml`'s version against `ARG BUNDLE` while it is there. Both
faults were reproduced against it before the check was believed.

`run.py` also imports the launcher inside a `try`, because **an accessory must
never cost the picture** and this one cost all of them: a panel with a url of
its own needs nothing from the launcher, and there was no reason for its
absence to stop the supervisor. The message when a panel does ask for the
launcher says which of the two it is -- no links configured, or no launcher in
this build -- because telling somebody who filled the list in that it is empty
sends them to look at the one thing that is right.

`run.py` supervises one `ha_send.py` per panel: `SHARED_KEYS` lets the token,
url, port, fps, quality, capture_quality, urgent_fps, urgent_window and stats be
given once at the top and inherited; backoff 5 → 10 → 20 → 120 s for a run
shorter than 20 s; children are killed on SIGTERM.

**A panel's own value wins only if it is a value.** An add-on's form has no
empty state, so a field nobody filled in arrives as `""`, and a plain
`{**shared, **panel}` lets that blank the shared one — silently, and the token
is where it bites.

**And a space is a blank.** A field somebody cleared by hand arrives as `" "`,
which is not `""` to Python and is the same thing entirely to the person who
typed it. Sent as `--token " "`, it reached the JWT shape check, failed it, and
the sender **exited before it opened a browser** — so the supervisor restarted
it on the 5 → 120 s backoff for ever and the panel showed nothing at all, with
a log blaming a token nobody had set. Found in a user's own configuration, in
which `token: ' '` sat under a panel that had never worked.

`given()` in `run.py` is the one place that decides whether a form field was
filled in, and whitespace is not filled in. The sender guards the same case on
its own, because it is run by hand too: a token that is only whitespace is
announced once and ignored, never refused. **An accessory setting must never
cost the picture** — the same rule the keyboard and the launcher live under,
and the token is the setting most likely to arrive blank.

**The add-on's form offers only what somebody should actually set** — ten
settings, listed in its README. See the note above on what was taken out and
why. A form nobody can read is a form where the setting that matters gets
missed.

From inside the add-on the URL must be `http://homeassistant:8123` — a Tailscale
or `.local` name gives `ERR_NAME_NOT_RESOLVED`. `explain_unreachable()` says so.
Downloads in the Dockerfile use `curl -f`, because without it GitHub's
"429: Too Many Requests" HTML page was saved as the script.

## Hardware and measured behaviour

Three boards, all confirmed working: **Waveshare ESP32-P4-WIFI6-Touch-LCD-7B**
(1024×600), **M5Stack Tab5** (720×1280 portrait, used landscape at 270°),
**Guition 10"** (800×1280).

- the C6 radio: **above 25 Mbit/s** measured outbound, serving a UVC webcam
  through `esp32_camera_web_server`. Inbound is a different ceiling and a lower
  one — `CONFIG_LWIP_TCP_WND_DEFAULT` of 28800 over the round trip, so about
  23 Mbit/s at 10 ms — but both are far above anything this sends: the busiest
  five-second window ever recorded was 758 KiB/s, which is 6.2 Mbit/s
- touch end to end: 3–22 ms
- reactivity floor ≈ 105 ms, dominated by Chromium's repaint and screencast
  delivery, not by this pipeline
- idle traffic: 0.0 KiB/s (a still dashboard genuinely sends nothing)
- with a camera in the dashboard: 14.2 pictures/s, 1141 KiB/s
- 0 dropped frames on the board

**`power_save_mode: none` on a panel, because ESPHome's default is `light`
and nothing here had said otherwise.** A panel receiving twenty-four pictures a
second is not a sensor that speaks once a minute: a radio that sleeps between
beacons adds latency, lets the access point buffer, and makes a burst fragile.
It costs a few tens of milliamps on a screen already fed by a cable.

Added to both Home Assistant examples after a panel showed **`panel wait 100%`
for two consecutive five-second windows with `made/s` at 0.0** -- the socket
taking nothing at all for ten seconds, then draining at 1615 KiB/s and
returning to normal. That shape is not the sender: it made nothing because the
acknowledgement is the flow control and the writer never came back. Whether it
is the radio or the board's own decode is what the BOARD's log says, and that
is the half to ask for next time.

**Presence is the other half of `--blank-after`, and the board's own YAML is
where it lives.** Parking the page costs about three seconds on wake; a
presence sensor spends them while somebody is still crossing the room, so
nobody ever sees the wait. `presence_entity:` is a substitution in both Home
Assistant examples, wired to a `binary_sensor: platform: homeassistant` whose
`on_press` wakes the panel and whose `on_release` starts the countdown, and
the timeout script will not turn the backlight off while the sensor reads
present. An entity that does not exist leaves the sensor with no state, which
reads as off, so the panel behaves exactly as it did before — timer and touch.

`set_awake()` **only tells the sender**. It does not touch the backlight and it
does not stop the panel drawing; those are the YAML's job, and the Guition
example had been turning the backlight off without ever calling
`portall.sleep`, so the sender went on rendering and transmitting for a
black screen. A sleeping board does drop contacts at `queue_touch_`, which is
what stops the tap that wakes it from also pressing whatever was underneath.

`yaml/p4-home-assistant.yaml` is the **validated** Waveshare firmware. It
deliberately contains no `token:`, `url:` or `panels:` — those belong in the
add-on options.

## Mistakes already made — please do not repeat them

- **The on-screen keyboard: four failed rounds, removed, then asked for again
  and rebuilt.** Every one of the four failures came from *adding* something,
  and all four shared one assumption: that the keyboard was a thing the page
  interacts with. `modalAbove` (relocating it into a `<dialog>`) fixed a case
  that had been invented and broke the only two that existed, because a native
  modal `<dialog>` makes everything outside it inert. What exists now drops the
  assumption instead — see **The keyboard** below. Do not put event handlers,
  focus, or `pointer-events` back on it.
- **`page.screenshot()` is not what the panel receives, and testing the two
  halves separately proved nothing about the whole.** The keyboard was checked
  for function against a fake panel (the keys typed) and for looks against a
  page screenshot (it was drawn). It shipped invisible on a real dashboard,
  because nothing had ever looked at the pixels that actually came down the
  socket. The fake panel now reassembles the rectangles it receives into a
  picture and the test reads a colour out of it — which is the only check that
  can fail the way the user did.
- A string replacement that spanned too far silently deleted `send_picture`,
  `_target_picture`, `calibrate` and `Screencast`. A test caught it
  (`NameError`). Prefer narrow, anchored edits in `ha_send.py`; it is ~46 KB.
  It happened twice more since: an anchored edit swallowed the `SKY` map, and
  in `wired_portall/udisp_send.py` one swallowed the whole `--stats` block --
  where the *follow-up* replacement meant to rewrite that block then found
  nothing to match and **did nothing at all, silently**. Neither Python nor a
  syntax check can see it: the file still parses, it simply stopped printing.
  Reported by the user as a sender that said nothing after connecting. So:
  `assert s.count(old) == 1` before every replace, and after an anchored
  deletion check that what you expected to survive is still there.
- `queue_touch_` uses `touchscreen::TouchPoints_t` and must stay inside
  `#ifdef USE_TOUCHSCREEN`. Touch is decoupled from `CFG_TUD_HID`.
- **Two example configs were shipped that do not compile, and a "parse check"
  is what let them through.** `esphome config` found both in one run: a
  fallback hotspot SSID of `${name} Fallback Hotspot` over the 32-character
  limit in the two Guition files, and a `microphone_type:` that the es8311
  schema has never had. `tools/checkyaml.py` runs the real thing — it writes
  throwaway secrets, points `external_components` at the working tree rather
  than at whatever `main` holds, and reports. esphome is a large install and
  belongs in a virtualenv of its own; the tool takes `--esphome` or `$ESPHOME`.
  Its one blind spot is `micro_wake_word`, which downloads its model from
  github.com while validating, and it says so rather than blaming the file.
- **A validator referenced before it is defined is a NameError at import
  time**, and it reaches the board's build rather than any YAML check: the
  schema is a module-level expression, so every name it uses must already be
  bound. Shipped once, on `_validate_render_size`. `esphome config` catches it
  in a second and is not always installable; `tools/importcheck.py` executes a
  component's `__init__.py` against a stand-in for esphome and catches this
  class without needing esphome at all. Run it on anything touched under
  `components/` before pushing.
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
- The keyboard has no accents beyond the four on the azerty bottom row and
  does not move out of the way of a field it covers. Both were left out
  deliberately. The layer of symbols was too, until somebody tried to sign
  into a Jellyfin server from a panel: a password is the first thing here that
  needs more than a search box does, and `?123` is what that bought. Both
  layers are five rows on purpose — the band a contact is tested against must
  not move when the layer does, or a finger on its way to a key would land on
  the page instead.
- The keyboard has been tested against a real Home Assistant frontend, and
  against synthesised versions of a plain field, a shadow root, a native modal
  dialog, an ingress iframe, a `contenteditable` editor and a Trusted Types
  page. It has **not** been tried against Assist on a real board.
- **Sound on the panel for a page the add-on renders** is the one obvious
  capability the network path does not have. The hardware is all there and
  already wired — ES8311 on I2S, an ESPHome speaker, a mixer, a `media_player`
  entity, and a UAC input into the same speaker — so what is missing is
  purely the link: a new udisp message type, a capture of the browser's audio
  (Chromium's protocol does not offer one; it would take a virtual sink beside
  the browser), a jitter buffer, and lip sync over Wi-Fi against a
  JPEG-per-frame video path. A real feature, and the sync is the hard half.
- `--blank-after` frees the page but not the browser: Chromium stays running
  with an empty tab. One browser serving several panels is the next step.


## The panel as a launcher

**The add-on builds the page, and that was the ask.** The first version said
"point `url:` at a Homepage of your own", which was a misreading: what was
wanted was a launcher *in* the add-on. `launcher.py` serves one from a `links:`
list -- name, url, and one character for an icon -- on 127.0.0.1:8099 inside
the add-on's own container, where the senders run too, so it is reachable by
them and by nothing else. A panel asks for it with `url: launcher`, which
`run.py` rewrites before spawning; a panel with a url of its own is untouched.

Everything is inline and nothing is fetched: a container has no promise of
reaching the internet, and an icon pack that failed to load would leave holes
where the labels should be on the one screen where nobody can open a console.
Every value is escaped -- these come from a form somebody types into, so a
stray angle bracket is a typo, and a typo that silently breaks the page a panel
comes home to is the worst kind to chase. Verified: `Camera & <cuisine>` draws
as itself.

Measured at 800×1280, 1280×800 and 1024×600 with six links: tiles no smaller
than 264×131 px, nothing running off the side at any of them. A bind that fails
returns None rather than raising -- **the launcher is an accessory and must
never cost the panels**, the same rule the keyboard lives under.

**Icons are names as well as characters, and the names came from the user
asking for a list.** `icon: jellyfin`, `icon: cuisine`, `icon: kitchen` --
**520 names onto 116 glyphs, every icon carrying both languages**. The first
version put French first and English on about twenty entries, which was the
wrong shape and was said so at once: *"pas que francais mais aussi anglais pour
les icone"*. A household does not have one language, and neither does somebody
filling in a form at eight in the evening.

The list is therefore one line per icon with every word that should reach it,
not one entry per name -- so adding a language is adding words to a line rather
than keeping a second dictionary in step, which is exactly how the first
version came apart. Flattening it fails loudly on a name used twice, and that
guard earned itself on the first run: `surveillance` pointed at both a camera
and an eye, `watch` at both an eye and a play button, `firewall` at both a
shield and a wall, `console` at both a games console and a terminal. Nothing is fetched: Homepage's own icon packs are
downloads, and this page's rule is that a panel is the one screen where nobody
can find out why an image did not load.

The names are a convenience and never a restriction -- anything not in the list
is drawn as the characters themselves, so an emoji pasted into the field works
exactly as it did before the list existed.

Two details that are the difference between a list and a good one:

- **Every glyph was checked against U+FFFF in the browser the add-on ships.**
  A character the font cannot draw measures exactly as wide as one that has no
  drawing by definition -- the same check the keyboard's erase key was put
  through. 115 glyphs, none undrawn.
- **U+FE0F on anything below U+1F000.** Half of these predate emoji -- an
  arrow, a snowflake, a cog -- and a browser draws those as *text* unless
  asked otherwise: thin, flat, and the colour of the label beside them, in a
  row of full-colour emoji. Only for that block; adding it to an emoji is
  noise. Not applied to the empty-field bullet either, whose emoji form is a
  heavier mark than the quiet placeholder it is meant to be.

**And then the real logos, because emoji are not what somebody means by the
icon of Jellyfin.** Reported as missing for Home Assistant, Jellyfin, Prime
Video, YouTube, Unraid and Proxmox -- three of which *did* resolve, to a
clapperboard, a play triangle and a monitor. `home-assistant` genuinely had
gone, dropped in the bilingual rewrite, which is a regression that reached a
user.

`logos.py` carries **50 service marks as inline SVG paths**, from
simple-icons, which places them in the public domain under CC0 1.0; the marks
stay the trademarks of the services they name, used to say which service a
tile opens. **Carried, not fetched** -- Homepage pulls its icons from a
repository, and a panel is the one screen where nobody can open a console to
find out why a picture did not load.

Two things had to be decided rather than copied:

- **A brand colour that disappears is worse than no brand colour.** GitHub is
  nearly black and Sonos is black outright; on a dark tile they are a hole. The
  relative luminance decides, and the theme's ink is the fallback -- a
  recognisable shape in the wrong colour beats a correct colour nobody can see.
- **Prime Video is not in the collection**, so it is a name in the emoji list
  and gets a television. Saying so is better than an empty square.

`tools/checkaddon.py` now follows imports **transitively** -- run.py imports
the launcher, the launcher imports the logos -- and it caught the missing
`COPY logos.py` on its first run, which is the second time that check has paid
for itself on the file it was written for.

`tools/iconlist.py` prints the README's table from the dictionary, because a
documented name that does not work is worse than no list at all.

**And the field had to survive a word being typed into it**, which is what
prompted all this: nothing stopped it, and the word ran straight across the
name beside it. Anything past two characters is set smaller and clipped now --
measured on what is *drawn*, so a name from the list counts as the one glyph
it becomes.

**The weather did not appear, and the address is why.** Reported as *"la
meteo ne s'affiche pas pour l'heure et la date ca fonctionne"* -- which is the
useful half of the report: the clock working means the bar renders and the
script runs, so the fault is the reading.

`Weather` took the shared `url:` as it stands and appended `/api/states/...`.
That `url:` is nearly always a DASHBOARD -- `http://homeassistant:8123/
lovelace/0` -- so it asked for
`http://homeassistant:8123/lovelace/0/api/states/weather.home`, a 404 every
time. It splits out the origin now.

**And the failure line did not say what it had tried**, which is why this was
not obvious from the log: `could not be read (HTTP Error 404)` is the same
sentence whether the entity is misspelt, the token is wrong or the address has
a dashboard glued to it. It names the address now. `why_not()` also breaks the
silence in the other direction -- an entity asked for with no `url:` or no
`token:` used to start no thread and say nothing at all, which is the silent
no-op this file keeps recording.

Verified with the url a panel really has: given
`http://127.0.0.1:8160/lovelace/0`, the stand-in Home Assistant is asked for
`/api/states/weather.forecast_home` and the reading reaches the page.

**The weather was frozen at whatever the add-on had started with, and the
report named the mechanism without meaning to.** *"sur le tableau il indique
16 degree et sur home assistant 30 degree"*, then *"il met environ 10 minute
pour y arriver"*. Ten minutes is `WEATHER_JS`'s interval, and that is the
whole clue.

Two faults, both on the page and neither in the reading:

- `draw()` was **only scheduled**, never called. `setTimeout(draw, 600000)`
  and nothing else, so the first fetch that could correct the page was ten
  minutes away.
- and the body was rendered **once in `start()`** and served as static bytes
  for the life of the add-on, so the number baked into it was from boot
  however often `Weather` re-read the house. That is the half that made it
  survive coming home, and it is the half a reader would not suspect: the
  add-on's own polling was correct all along.

`page()` rebuilds when the reading CHANGES -- keyed on `weather_block()`'s own
output, so it is a few renders a day and a request costs a dictionary lookup.
Per request would re-list a photograph folder from disk every time a panel came
home.

Measured with the shipped 3.1.0 beside it, same stand-in Home Assistant, same
page, the house warming from 16 to 30 in between:

| | fetches at load | first paint | after coming home |
|---|---|---|---|
| 3.1.0 | **0** | 16°C | **16°C** |
| now | 1 per load | 16°C | **30°C** |

The page also asks every two minutes rather than ten: the add-on refreshes on
its own ten-minute timer, so matching intervals let the page sit twenty
minutes behind the house, and this fetch never leaves the machine. Writing the
same text into the DOM paints nothing, so an unchanged reading is not a
rectangle on the wire.

**And the edit that fixed it deleted `SKY`**, which is the mistake this file
already records under a different name: a replacement anchored on
`def weather_block(state):` swallowed the map of Home Assistant's weather
states that sat above it. It surfaced as `NameError: name 'SKY' is not
defined` the moment a test built a page with weather on -- not from reading
the diff. What found it in one line afterwards was diffing the module-level
names against `git show HEAD:` and listing what had gone; that check is worth
running after any anchored edit in a file this size.

**`blank_after` came off the form, and the reason it was there is worth
keeping.** Asked as *"supprime blank after car je me sers deja d'un temps
donne pour eteindre l'ecran sur le yaml de esphome"*. Those are two different
things and the option's name did not say so: the backlight and `portall.sleep`
are the board's, in its own YAML, while `blank_after` is what the SERVER does
a while later -- letting the page go, which is measured at 1.1 s of CPU every
ten seconds against 0.15. So the behaviour stays at its 300 seconds and the
knob moves to a panel's own entry, beside `user_agent` and `import_profile`,
for the same reason: a household has no reason to think about it.

**The video wallpaper that "does not start" is H.264, and the panel could not
say so.** Reported with the address of an ordinary film --
`.../goku-ultra-instinct_2.1920x1080.mp4`. Measured on the shipped browser
with `canPlayType` rather than `MediaSource.isTypeSupported`, because a
wallpaper is a plain `<video src>` and the two are answered by different code:

| | answer |
|---|---|
| `video/mp4; codecs="avc1.42E01E, mp4a.40.2"` | **nothing at all** |
| `video/mp4` with no codec named | maybe -- which is why the container tells you nothing |
| `video/webm; codecs="vp9"`, vp8, av1 | probably |

So the file was never going to play, and a `<video>` with no frame paints
NOTHING -- the blank rectangle this file already forbids twice. The wallpaper
tests here had all used a WebM, which is the one format that build definitely
carries: **the test format was chosen to work.**

`VIDEO_ERROR_JS` reports the element's `error` to `REPORT_PATH` on the
launcher's own server, which prints it once per distinct complaint and names
both ways out. The code is put into words first -- a household reading "4"
learns nothing, and 4 (`MEDIA_ERR_SRC_NOT_SUPPORTED`) is by far the commonest
here. Verified on a real undecodable file: one line for two visits, and
nothing at all for the WebM beside it, with motion on and off.

The sender already prints `Browser: decodes H.264 yes/no` at startup, and that
line is the one to read: the add-on installs a second browser for the
proprietary codecs and that install is allowed to fail, so which answer a box
gives is not knowable from here.

**A stylesheet was the wrong answer, and it took the same person saying so
three times to remove it.** Asked first as *"tu ne donnes pas la possibilite
de changer d'emplacement de taille ou de couleur"*, answered with
`launcher_css`; pushed back on as *"c'etait pas plus simple pour la meteo de
choisir sa taille et pour l'heure et la date de choisir sa taille et
couleur"*, answered with four named settings **beside** the sheet; and settled
by *"je preferre que tu enleve le launcher_css car pour un particulier novice
il va pas comprendre il faut au plus simple"*.

The argument for one general mechanism was option count -- which is the
maintainer's problem, not the household's. **This is the fifth time the same
person has proposed a named per-thing setting over a general mechanism**, after
the quality, the user agent and the frame limit per link, and they were right
every time. Keeping the sheet "as the way out" was the same mistake a second
time: a novice does not want a way out, and an option that exists is an option
somebody has to read past.

**Homepage was read rather than remembered**, because the same person asked for
that too -- *"je prefere que tu regarde comment ils ont homepage"* -- and it
settles the design in three lines:

| | Homepage |
|---|---|
| size | **per widget**, `text_size:` on a fixed scale `xs, sm, md, xl, 2xl, 3xl, 4xl` |
| colour | **global only**, one `color:` in `settings.yaml`, a Tailwind palette name |
| custom CSS | **not a config option at all** |

So the shape is copied and the words are not. `launcher_clock_size`,
`launcher_clock_color`, `launcher_date_size`, `launcher_date_color`,
`launcher_weather_size` and `launcher_align`, every one of them a `list()` in
the form. The date gets its own pair because that is what was asked for twice
(*"pour aggrandir la date et changer sa couleur comment ont fait"*), and the
weather gets no colour because it is an emoji -- a browser draws those in
their own colours whatever the page says.

**The sizes keep `small/medium/large/huge` deliberately, and the reason is a
migration rather than a preference.** Adopting `xs..4xl` would change the
stored value of two options every existing install already has, and a stored
value that is no longer a member of a `list()` is a **validation failure**, not
a warning -- an add-on that will not start until somebody edits a dropdown they
have never seen. A removed key only warns, which is why dropping `launcher_css`
is free and renaming a scale is not. `theme` is the colours' own "no palette",
a word rather than a blank first entry.

Measured in the browser on the COMPUTED style, through `run.start_launcher()`
and a real fetch rather than `render()` on its own: clock 40/68/96/**130** px
at 1280x800, the date 16/22/28/34 independently of it, the weather 19/26/34/44,
`sky` and `rose` reaching `.time` and `.date` separately, an unknown or empty
name falling back to the theme rather than failing, the clock moving 64 -> 331
-> 598 px across left/center/right, and `_bar()` emitting **nothing at all**
when every setting is its default. Tiles unchanged at 566x118 on 1280x800 with
six links in three groups, nothing off the side at any of the three shapes.

**The clock, the date and the weather, because the real Homepage has them.**
Reported from a household testing the launcher: *"il manque l'heure la date et
meteo"*. Three decisions were worth making rather than copying.

**No seconds.** A digit that changes every second is a rectangle on the wire
every second for as long as the panel is awake -- exactly the reason `HomeHint`
does not pulse. The clock ticks on the MINUTE, and to the minute BOUNDARY
rather than every 60000 ms from whenever the page loaded, or the turn-over
would drift into the middle of nothing. One small rectangle a minute, and a
sleeping panel sends none.

**The browser formats them, so they follow the panel's `locale:`.** Measured:
`fr-FR` gives `19:00` and `mercredi 2 septembre`, `de-DE` gives `Mittwoch, 2.
September`, `en-US` gives `07:00 PM`. This add-on knows no language at all,
which is the only version of this that does not rot.

**The weather is read by the ADD-ON, never by the page.** `run.py` already has
the token and Home Assistant's address; the launcher page has neither, and
giving it either would put a long-lived token into the storage of every site a
panel visits -- the leak this project already had to close once. So `Weather`
polls `/api/states/<entity>` every ten minutes in a thread and the page asks
`127.0.0.1:8099/weather.json`, which never leaves the machine. Every failure
keeps the last reading and says so once, not each time.

Verified end to end against a stand-in Home Assistant that checks the bearer
token: read, turned into its two spans, served at `/weather.json`, and present
in the page. And on the failure paths -- an unreachable Home Assistant leaves
`state` None and prints one line, no entity configured starts no thread at
all, and a page built with no weather carries no `.wx` box and keeps its
clock.

Measured in the browser at the three panel shapes with the bar in place: tiles
**350x166 at 800x1280, 566x118 at 1280x800, 454x107 at 1024x600** -- the same
figures as before it existed, and nothing off the side at any of them.

**Five settings came off the form because they only filled it**, said as
*"il ne serve pas il remplit option seulement"*: `launcher_title`,
`launcher_subtitle`, `launcher_color`, `user_agent` and `import_profile`.

Two of them are worth recording rather than just listing. `launcher_color` was
the accent every other colour is mixed from -- at 8% on the ground and 14% on a
card, which is very likely WHY nobody noticed it doing anything; the launcher
is slate now and nothing else changed. And with no title there is now **no
heading at all** rather than the word "Panel" over every launcher: a page whose
every tile is labelled does not need one.

`user_agent` and `import_profile` were removed from the SHARED block only.
Both survive per panel, and `user_agent` per link -- which is where this file
already said they belonged, since a panel told to say it is a television says
it to Home Assistant as well.

**The date sits under the time and carries the year**, which is what a clock
looks like everywhere else. `.when` is a column inside `.now`, so the weather
keeps its own room on a narrow panel. **White and black** joined the palette
names: neither is a Tailwind palette, and both are what somebody wants over a
photograph.

**The digital photograph frame, and what it costs.** `launcher_background`
takes a FOLDER as well as a file or an address -- one setting rather than a
second one beside it -- and `launcher_slideshow` cycles through it, with
`_seconds`, `_fade` and `_rescan`. `launcher_background_motion` lets a GIF or
an MP4 actually move, off by default.

The cost is the part worth keeping, because it is the one feature here that
contradicts the property this project advertises loudest. **A picture that
changes is a whole panel on the wire** -- about 130 KiB at 800x1280 and
quality 80. A hard cut costs one; each second of fade costs about `fps` of
them, so two seconds at 25 is six megabytes a picture; a playing video costs
that for ever, behind everything else on the screen. The delay averages it
down and the fade multiplies it, which is the opposite of where somebody would
look first.

**Three faults, and none was visible from reading the code.**

- **The served path carries no extension.** A file wallpaper is served at
  `/wallpaper`, so `_moves("/wallpaper")` was false and a GIF was never
  frozen. What may move has to be judged from the SOURCE on disk.
- **The picture is named in the STYLESHEET, not inline.** `FREEZE_JS` read
  `wall.style.backgroundImage`, got an empty string and returned at its first
  line. `getComputedStyle` is what has it.
- **A paused `<video>` with only its metadata loaded paints nothing** -- a
  blank rectangle where a photograph should be, which is the failure nobody
  can diagnose from a panel. `preload="auto"` guarantees a frame.

And a measurement trap of the usual shape: the first video test read plain
white for a paused video and it was **the test's own video**, recorded from a
page that was still white when the recording started. The fix was to the
ruler. Playwright's `record_video_dir` is what made a real .webm to test
against with no ffmpeg in the container -- the browser recording a page that
flashes two colours.

Verified on the PIXELS off a screenshot rather than on the markup: three
coloured pictures in a folder cycling red -> green -> blue -> red once a
second and wrapping; the same folder with the slideshow off holding its first
picture for 2.4 s; 7 of 14 samples caught mid-fade with a 2 s fade; a GIF
frozen on one colour with motion off and two colours with it on; a real video
paused on its first frame and playing with it on; a `.txt` in the folder
ignored. The bar measured at all three panel shapes with the date under the
clock -- **350x166 at 800x1280, 566x118 at 1280x800, 454x107 at 1024x600,
nothing off the side** -- which is also the answer to *"si ils mettent la
dalle en portrait est ce cela pas tous ce decaler?"*: the page is fluid and
portrait is the shape it was measured at first.

**The photograph frame could not be tested, and the reason is where the
photographs live.** Reported as *"je n'ai pas tester
launcher_background_motion, launcher_slideshow pour un raison que mes image
ont une adresse ip de mon server par exemple
http://192.168.1.3:8080/eTBckVxL/1326045.jpeg il etait preferable qu'il upload
ce que je veut"*. The slideshow read a FOLDER under /config, /share or /media,
and a household's photographs are as likely to be on a server already.

Two answers, and only one of them is code:

- **Uploading needs nothing from this project.** Home Assistant's own Media
  panel -- Media > My media > Upload -- writes into `/media`, which this
  add-on already mounts read-only. That was checked against Home Assistant's
  documentation rather than remembered, and it is now the documented route for
  a photograph frame.
- **`launcher_slideshow_urls`**, a list of addresses, which the panel's own
  browser fetches exactly as it already fetched a single wallpaper by address.
  A new key rather than making `launcher_background` a list, for the reason
  this file already records: a stored string where a list is now expected is a
  validation failure, and it would have stopped the add-on of everybody who
  had set a wallpaper.

**And the GIF really was broken by address, which the report half-guessed.**
Freezing a GIF means reading its pixels back out of a canvas, and a browser
refuses that for a picture fetched from anywhere else -- so with motion off a
remote GIF went on looping while a local one froze. `start()` copies that one
file here once, where the page can freeze it. A video never needed it: pausing
one asks the browser for nothing, and a remote `.webm` was already correct on
both sides of the switch.

Measured on the pixels, against a server serving pictures at addresses with no
extension in the path -- which is the shape the user's own server produces:
three cycling and wrapping, the first held with the slideshow off, a list
beating a folder when both are given, an entry that is not an address ignored
with one line saying why, and a GIF and a video by address each still and
moving on either side of `launcher_background_motion`.

Two ordering faults in the mirror, both invisible from reading and both fatal:
`mirrored` was passed to `render()` on the line above where it was computed,
and the `picture, mime = None, ...` that follows threw the copied bytes away.

**"Does the slideshow go on running behind a link?" -- no, and it is worth
recording because it is the natural thing to suspect.** Asked as *"si vous
allez dans link home assistant, jellyfin le slide show est fonctionnel en
parallele ce qui ralentit le link"*.

There is **one browser page per panel** and a tile is a plain `<a href>`, so
opening one navigates that page away: the launcher document is destroyed and
its timers -- the slideshow, the clock, the weather -- go with it. There is no
second page to run them in.

Measured against a picture server that counts what it is asked for: **7
pictures in 4 s on the launcher, 0 in the 6 s after clicking a tile, 6 again
in the 4 s after coming home.** Nothing survives the navigation.

What IS true, and is probably what was felt: a slideshow makes the LAUNCHER
expensive, and a tile is tapped while that burst is still in flight. Every
change of picture is a whole panel, and each second of fade is one per frame
-- so a fade of 2 s at 25 fps has fifty whole panels on the wire at the moment
somebody touches the glass. The link is not being slowed by anything running
beside it; it is starting behind a queue the launcher just made.
`launcher_slideshow_fade: 0` and a longer delay is the test, and `stats` says
which it is -- the `whole` count and KiB/s while the launcher is showing
against the same fields once the link is open.

**A quality per LINK, and the reasoning behind it is the user's.** Asked for
in those words -- *"j'aurais preferer que dans les link ont puisse choisir la
qualite c'est plus simple a gerer"* -- after `render_width:` was offered and
refused. It is the better idea: a panel does not know whether it is showing a
film, and a link does.

`--page-quality PREFIX=QUALITY`, repeatable, first match wins, matched on the
start of the address because a site is not one address. Looked up once per
picture rather than per rectangle and only when any was configured; `page.url`
is local to Playwright rather than a round trip. `run.py` builds the list from
the `links:` carrying a `quality:` and hands every panel the same one -- the
links are the house's, and a panel that never opens one is unaffected.

Measured end to end through the launcher, the tile tapped by a fake panel and
the bytes read off the socket: **53.9 KiB a picture at the panel's 80 against
34.2 KiB when the link says 40**, 971 KiB/s against 616.

**The look is Homepage's, and its vocabulary is kept on purpose.** The ask was
*"thème et fond d'écran et disposition des link comme HomePage"*, so
`gethomepage.dev`'s own settings were read rather than invented: `theme` of
dark or light, `color` named after Tailwind's palettes, `background` with a
blur and a dim, groups of links under their own headers, cards carrying an
icon, a name and a description. Somebody who knows that dashboard now knows
this one.

What is deliberately **not** copied is the density. Homepage is read at a desk
with a mouse; this is read across a room and pressed with a thumb, so the cards
stay large and there is no hover state to depend on. Measured with six links in
three groups: tiles 350x166 at 800x1280, 566x118 at 1280x800, 454x107 at
1024x600, nothing off the side at any of them.

One palette value per theme, and everything else `color-mix`ed from it in the
page. Twenty hex values per palette is twenty chances to be inconsistent, and
Chromium has had `color-mix` since 111 -- while the add-on already refuses to
be quiet about a browser older than 114. A plain value is declared first in
every case, so a browser that cannot mix still gets a page.

**The wallpaper is the one thing that is ever fetched, and both ways of giving
it had to work.** An address the panel can reach (`/local` on Home Assistant is
the obvious one) is used as it stands; a path is served by the add-on itself,
because the page is on `127.0.0.1` and no browser will load a `file://` URL
from one. `map: config:ro, share:ro, media:ro` is what makes a path readable at
all. A wallpaper that will not load leaves the plain colour and says so in the
log -- **an accessory must never cost the picture**, and a black rectangle
where a photograph should be is exactly the failure nobody can diagnose from a
panel.

Two faults were caught by looking at the rendered pixels rather than the
markup, which is the rule this project already had to learn once: the
description ran into the name (an `<a>` may not hold a `<div>`, so the parts
are spans -- and a span left inline sits on the same line), and the dim was
being applied with no wallpaper set, so a light theme came out mud grey.

**"Home" is the panel's own `url:` either way**, which is what keeps the
gesture below simple: there is no second notion of home to keep in step. The
reason the way back cannot be a button on the page is the same
reason the on-screen keyboard is not a widget: a panel has no keyboard, no
address bar and no Back button, and a site playing full screen swallows
everything it is given.

So it is decided before the page sees anything. **Hold the top-left corner
(`HOME_CORNER_FRACTION` of each axis) for `HOME_HOLD_S`**, tested by arithmetic
in `Injector`, exactly like `keyboard.contains()`. A corner rather than an edge
swipe because pages scroll sideways; held rather than tapped because a corner
gets brushed and a whole second of stillness does not — and short of the
second, the tap is delivered normally, so the corner stays usable.

**A swipe sideways out of the corner is the other way home, and it is the one
somebody actually suggested after living with the hold** -- *"le plus simple
est le swipe droite ou gauche en haut a gauche de l'ecran"*. It is the better
gesture to find by accident: a finger that lands and drags is what a person
does to a screen they are unsure of, while holding perfectly still for a second
is something you have to be told to do. Both work; neither replaces the other.

Sideways rather than any direction, because the page under that corner scrolls
vertically. `HOME_SWIPE_FRACTION = 0.10` of the page's width -- 128 px on a
1280-wide page -- and `HOME_SWIPE_STRAIGHTNESS = 1.5`, so a diagonal drag stays
a scroll.

Three things had to be got right, and each was a real bug first:

- **The hold and the swipe need two pieces of state.** The hold asks whether
  the finger is STILL in the corner, so its clock is cleared the moment it
  leaves; a swipe leaves the corner immediately by definition. `_from_corner`
  is where it came from, `_corner_at` is whether it is still there.
- **The decision is made ONCE, when the finger has gone far enough sideways.**
  Asked again on every later report, a long diagonal became a swipe after the
  fact: a drag across the whole screen runs out of screen vertically first, so
  its sideways travel goes on growing while its downward travel cannot, and
  half a screen later it passes a test it failed at the start.
- **The scroll is held back only while the gesture could still be one**, which
  is bounded on both sides. Held while `|dx| < swipe`, released the moment it
  is clearly vertical or clearly too far to fire. The first version had only
  the near side, so a drag out of the corner that was merely too diagonal to
  count held its scroll for ever and the page never moved at all.

Ten cases, all measured against the panel's own geometry: hold with 8 px of
wander, quick tap, diagonal drag, swipe right, swipe left, a swipe that began
outside the corner, a drag straight down, a hold in the middle of the page, and
the corner's two edges.

**The corner shows itself, and the mark is decoration in the keyboard's exact
sense.** `HomeHint` -- popover in the top layer, stylesheet through the CSSOM,
built out of the DOM, `pointer-events: none`, no listeners, no focus, and every
failure caught into `broken` so a page that refuses it costs the mark and never
the picture. It draws the same rectangle the sender tests, from the same
fraction, so what is pressed and what is seen cannot drift.

Faint for `HOME_HINT_SECONDS = 5` when a page arrives, filling while a finger
is held, gone otherwise -- and no animation anywhere, because a corner that
pulses is a corner that repaints, and a repaint is a rectangle on the wire for
as long as the panel is awake.

**It shipped invisible in its first version, and the reason is worth keeping:**
a conic gradient centred on the top-left corner has exactly one visible
quadrant, between three o'clock and six. The sweep started at `.5turn` and
therefore drew entirely off the screen -- an element that existed, was open,
was 84 pixels square, carried the right gradient, and painted nothing at all.
`from .25turn` is right.

Measured the only way that can fail the way a user does, on the pixels off the
socket, on three pages -- plain, one with a native modal dialog open, and one
with a Trusted Types policy:

| | corner on arrival | five seconds later | while held |
|---|---|---|---|
| plain | (54,57,62) | (18,21,28) | (224,224,224) |
| modal dialog | (53,54,59) | (15,18,25) | (224,224,224) |
| Trusted Types | (54,57,62) | (18,21,28) | (224,224,224) |

and the tap underneath still reached the page in the two cases where a page can
take one. The third is not the mark's doing: a native modal dialog makes
everything outside it inert, which is the browser's rule and the reason the
keyboard was never allowed to be one.

A first attempt at that table measured the harness's own button -- a browser's
default button is near-white, it sat under the corner, and every reading came
back 229 whatever the mark did. **Sample against a background you chose.**

**`tick()` is asked by the loop, not driven by contacts**, because a finger
holding perfectly still reports *nothing*: the board drops an event identical
to the one before it, which is what stops a resting finger repeating itself
fifty times a second. A long press produces no reports to notice.

**A hold is cancelled by leaving the corner, not by wandering inside it.**
The first version cancelled on `DRAG_THRESHOLD` -- any 12 page pixels of
travel. A finger resting on glass is never that still, and on a 800x1280 panel
shown at 90 degrees the page is 1.6x the panel, so **five panel pixels of
wander lost the gesture**. Reported as the corner simply not working, and
reproduced against the panel's own geometry: 0, 2 and 4 page pixels went home,
8 and 16 did not. What somebody means by holding the corner is that the finger
is in the corner, so that is now what is asked -- and the six cases around it
still behave: a quick tap in the corner reaches the page, a swipe out of the
corner scrolls and does not go home, a hold in the middle of the page clicks,
and the corner's edge is where it says it is.

**A still page paints once, so the frame at a navigation cannot be thrown
away.** The gesture fired, the browser went home, and the panel went on showing
Home Assistant for ever -- reported as *"home assistant ce fige et ne reviens
pas a HomePage"*, and it looked like the gesture failing when the gesture had
already worked. What the log said was `Home: back to ...` followed by
`0.0 made/s` for the rest of the run.

Measured, because three plausible causes were wrong first. A screencast
survives a navigation (20 frames/s before and after, cross-origin included),
and it survives acknowledging the old page's frame ids. What it does not do is
paint a page that is not changing: **two frames in the two seconds after
arriving at a still page, none in the two seconds after that, and one more
every time the screencast is restarted.**

So the frame in hand at a navigation is not one of many, it is the only
picture that page will ever send -- and the home branch was calling
`request(discard=True)`, which is right after a press and ruinous here.
`Screencast.restart()` asks for one instead. Verified with a fake panel
reassembling the rectangles: the picture off the socket is the launcher, where
before it stayed the dashboard.

**A spent gesture must be swallowed, not reset.** The first version cleared
`_start` when it fired, so the next report from the still-down finger looked
like a fresh landing and the lift after it clicked the corner of the page that
had only just loaded. `_went_home` now ignores every remaining report and
suppresses the tap at the end. Caught by a test that watches the *pages*
report themselves: the sequence must read `coin` → `page:ailleurs` →
`page:accueil` and nothing after it.

Verified end to end with a fake panel: a short tap in the corner still reaches
the page, a tap on a link navigates away, and a 1.5 s hold comes home.

**The thirty-second wait for `home-assistant` is asked once, not every time,
and the launcher is what exposed that.** A panel used as a launcher keeps its
token -- so that a Home Assistant tile opens logged in -- and is pointed at a
page where that element never appears. The wait is inside the loop, so it is
not thirty seconds of nothing: it is thirty seconds of a **stopped panel**,
every time the corner brings it home. Measured against the same page with a
token set: before, **no picture at all arrived in the twelve seconds after the
gesture**; after, the first one comes back in **357 ms** and the worst stall is
838 ms.

**Startup is timed and said out loud, because from the panel every part of it
looks the same: a black screen.** `Ready 3.6s after starting (0.5s of it the
browser)`. Reported as over a minute to reach the launcher, and the
thirty-second wait for `home-assistant` on a page that never had one was most
of it -- a token pointed elsewhere now settles that question outright rather
than waiting to find out. The 3-second settle goes with it, so a launcher page
is ready in 800 ms: measured end to end, first bytes at the panel **3.7s
before, 1.6s after**.

`run.py` also starts the sound server **before** spawning any sender rather
than after. A sender that got there first found no PulseAudio, and `pactl`
then tries to spawn its own -- slow, and a second server nobody wanted.

`open_page.is_home_assistant` is None until the first page has been looked at
-- unknown, not "no" -- then True or False for good. The 3-second settle goes
with it: Home Assistant paints in stages and is worth letting settle, a
launcher page has no such staging, so it gets 800 ms.

**Home Assistant became a LINK, and the token went with it -- 3.0.0.** Asked
as *"il ne comprenne pas pourquoi dans option il y a le token, url de home
assistant ... alors qu'il ya deja un link ... tu aurais faire un link a part
pour home assistant"*, relayed from people testing the add-on. It is the same
instinct that produced `quality:`, `user_agent:` and `fps:` on a link, and it
is right for the same reason with one extra: **a token belongs to an ORIGIN**,
which is a fact this file already records twice, and a link is the only thing
in the configuration that names one.

So `token:` and `url:` are off the top of the form. `links:` gains `token:`,
the sender gains repeatable **`--page-token ADDRESS=TOKEN`**, and
`install_tokens()` writes one guarded init script per origin, first wins --
the panel's own token ahead of the links', so a panel that overrides the house
keeps its own. `home_assistant: false` now withholds the links' tokens too,
which is what that switch always meant; it is READ rather than popped in
`load_panels`, because the links are handed out later and that step asks the
same question.

What is NOT moved, and the user did not argue: `port`, `fps` and `quality`
stay at the top. They are the panel's, and the last two already exist per link
as overrides.

**Two things had to be decided rather than coded.**

- **Removing a key only warns; changing a `list()`'s members fails.** That
  asymmetry is why `launcher_css` could go quietly two releases ago and why
  this one is a **major version with a migration section**: a stored `token`
  under a key the schema no longer has is dropped by the Supervisor before
  run.py ever sees it, so no fallback is possible and nothing can carry it
  across. Copy it out first or lose it.
- **The url is no longer inheritable**, so every panel needs one. The message
  names the panels that are missing one and says the setting moved, because a
  configuration that worked yesterday arrives with nothing to show and "every
  panel needs a url" does not explain that.

**A log line printed the token, and it was written in this same change.**
`print(f"Ignoring --page-token {entry!r}")` echoes the right-hand side --
which is a long-lived token -- into a log that is readable from the Home
Assistant interface and is the first thing anybody pastes into a forum. Caught
by reading the test's own output rather than by reading the code. Nothing in
these paths prints an entry now; the address alone, never the value. The rule
was already in this file for a token in a file, a log or a commit, and it was
broken by the change that moved tokens around.

The same run found the shape check too loose: `http://x=not.a.jwt` passed,
because three dot-separated parts was the whole test. Every Home Assistant
token's signature is 43 characters -- measured, and the main `--token` path
already checked it -- so this one does too.

Verified in three places, because the two ends being right has not been enough
before: `install_tokens` in a real browser across three origins served on
three ports (each gets its own token, a repeated address is ignored, the third
origin gets nothing at all); `command_for` against the options a form would
produce (the launcher panel carries `--page-token`, a `home_assistant: false`
panel carries no token anywhere on its line); and the JOIN between them -- the
line run.py builds, fed to the sender's own parser, then to `install_tokens`
with a recording stub, which is where the last several faults of this shape
have lived.

**The token belongs to an ORIGIN, and the launcher is a different one.** A
panel started on the launcher was handed `--url http://127.0.0.1:8099/`, so
`install_token` derived its origin from that and wrote `hassUrl` naming the
launcher. The frontend ignores a record whose `hassUrl` is not its own, so the
Home Assistant tile opened on a login screen -- with the token sitting right
there in that page's storage, naming somewhere else. Reported as *"malgree le
un nouveaux token il ne va pas a la page de home assistant il me demande de me
connecter"*, and it is not the token: any token would have done that.

Measured against a **real** Home Assistant (2024.3.3, onboarded here, a real
long-lived token minted through its own websocket API), same page, same token,
only the origin differing:

| token installed for | dashboard renders |
|---|---|
| the launcher's origin | **no** |
| Home Assistant's origin | **yes** |

`--token-url` names the address the token belongs to and defaults to `--url`;
`run.py` sets it from the shared `url:` whenever it rewrites a panel's url to
the launcher, and says so plainly when there is no address to attach it to.

**The same guard closes a leak the launcher had just opened.** An init script
runs on *every* document the context loads and `localStorage` belongs to
whichever origin that document is on, so the house's long-lived token was
being written into the storage of every site a panel visited -- YouTube,
Jellyfin, anything on the launcher -- where any script on the page can read
it. One dashboard per panel had hidden it; a page of links did not. The script
now writes only when `window.location.origin` matches, checked in the browser
rather than trusted from the sender.

And a token pointed elsewhere is a panel saying outright that this page is not
that dashboard, so the thirty-second wait for `home-assistant` is skipped
entirely on a launcher page rather than merely being asked once.

Verified end to end the way this project requires: the add-on's own command
line, the real sender, the real Home Assistant, and a fake panel reassembling
the rectangles off the socket into a picture -- which came back a logged-in
dashboard, not a login screen.

## Sound for the page, board side

**`UDISP_TYPE_PCM = 0x10`**, alongside Espressif's 0..3 and 0xff. Sixteen bytes
of the same header, because one definition of the wire format is worth more
than a tidier one per kind of thing on it: a rectangle's geometry means nothing
for sound, so `x`, `y`, `width`, `height` and the frame id all go out as zero
and only `payload_total` is read. What follows is **48 kHz, 16-bit signed
little-endian, one channel** — `PORTALL_AUDIO_*` in `portall.h`,
`AUDIO_*` in `udisp_send.py`, and the USB audio class is configured for the
same, because they share one speaker and one block buffer.

Mono on purpose: these panels have one speaker, and it halves what the network
carries — 96 KiB/s beside a picture that has been measured wanting 2.5 MB/s.

**This is only for the page.** Home Assistant's own audio has a `media_player`
on the board and always did; nothing here touches it.

**The tuyau already existed and was locked behind USB.** `on_usb_audio()` took
PCM, gathered it into blocks an ESPHome speaker will accept, carried over what
the speaker would not take and reported underruns — all of it written for the
USB audio class and all of it exactly what the network needs. So the change was
mostly moving a guard: the shared half (`set_speaker`, `on_audio_samples`,
`setup_speaker_`, `flush_audio_block_`, the block buffer, volume and mute) is
now `#ifdef USE_SPEAKER`, and only `setup_uac_` and the `usb_device_uac`
callbacks stay under `#if CFG_TUD_AUDIO`. `on_usb_audio` became
`on_audio_samples` because it is no longer about USB.

**The parser gained a fourth state, and it is shaped like `skipping_` rather
than like the frame filling.** Audio is a *stream*: nothing has to be gathered
before it means something, so a payload split across two reads is two writes to
the speaker and the split is invisible. That is the whole of it —

```cpp
if (this->audio_want_ > 0) {
  const size_t take = this->audio_want_ < len ? this->audio_want_ : len;
  this->on_audio_samples(data, take);
  this->audio_want_ -= take;
  data += take; len -= take; continue;
}
```

— the same three lines `skipping_` was already proven on against six chunk
shapes, which is why this was written to look like it. `reset_stream_()` clears
it too, or the next sender's first header is read out of the middle of the last
one's samples.

**Not compiled.** There is no ESP-IDF toolchain here and `esphome config` never
compiles C++. What was checked: the preprocessor guards balance in both files,
the schema validates, `yaml/p4-home-assistant.yaml` still passes `esphome
config`, and the header packs to the same sixteen bytes as a rectangle.

**`tools/playsound.py` is the board half on its own**, so it can be tested
before the capture exists: connect, send PCM, listen. No picture, standard
library only, so it runs from the Windows machine this project is usually
driven from. A test tone by default, a 16-bit WAV with `--wav`.

Paced to real time on purpose. The panel plays at 48 kHz whatever the sender
does, so sending faster only fills a buffer until it overflows and the board's
log starts saying the speaker is not draining.

Verified against a fake panel that **replays the board's parser** — the same
four states, including the new one — reading the socket in 65536, 1440, 3 and
7 byte chunks: **96000 bytes out, 96000 in, byte-identical every time**, three
of those shapes splitting headers down the middle. That checks the sender and
the shape of the design; it is a Python model of the C++, not the C++.

**The Guition example now wires it**, and it is the one thing that had to be
decided rather than moved: `speaker_id:` used to point straight at
`speaker_id`, the raw I2S output, which put the page's sound in direct
competition with the media player on one bus. It now goes through a resampler
into a **third mixer input** of its own, so a Home Assistant announcement lands
*over* the page rather than fighting it, and stopping one does not stop the
other. The resampler is not optional: portall sends 48 kHz because that is what
a browser produces, the panel runs at 44.1, and a mixer given both refuses the
stream outright — "Incompatible audio streams", which is the noise the code
comments have warned about since the USB path.

Validated with `esphome config`, with `micro_wake_word` removed for the run
because it downloads its model from github while validating.

**`portall.set_volume` exists because a following entity is not a setting.**
`number: platform: portall` shows the volume the sound is at and changes it
when moved -- right for a host that has its own control, and no use at all for
what somebody actually wants at boot: a slider with `restore_value` and an
`initial_value`, remembered across restarts. That is how ESPHome does a
setting, and the user said so plainly. A template number could not do it
because there was nothing for its `on_value` to call.

The value is a fraction, 0 to 1, like every volume in ESPHome, so a slider
that runs to a hundred wants `!lambda 'return x / 100.0;'` -- and the mistake
this will really see is that division being left out, so `set_audio_volume`
clamps rather than refusing and says once what the lambda should have been. A
volume is not worth failing a boot over.

Two things `esphome config` caught that reading would not have: the action was
registered without `synchronous=`, which esphome warns about by name (play()
writes a float and calls the speaker, so it is synchronous), and the id is
generated -- naming one that does not exist is what a copied example does.
Validated against `yaml/guition-10-home-assistant.yaml` with the block added,
`micro_wake_word` stripped for the run as always.

**The volume entity is `number: - platform: portall`, and it governs the
page's sound as well as USB.** A panel reported the volume not working with a
`platform: template` number in its place -- which stores a value and writes to
the log, and is connected to nothing. Nothing had regressed; there was no
volume control in that configuration at all.

Worth keeping because the wording invited it: the class is `USBVolumeNumber`,
its log tag was `usb_display.number` and its dump said "USB Display Volume", on
a board whose sound now mostly arrives over Wi-Fi. The volume is applied in
`on_audio_samples`, which is the one door PCM comes through whichever way it
arrived, so the entity was always right and only its words were wrong. They say
Portall now. The option key `usb_display_id:` became `portall_id:`, with the old
spelling still accepted -- it is generated rather than typed, so refusing it
would buy nothing.

## Sound for the page, server side

**Playwright mutes the browser on every launch and says nothing about it.**
This is the whole reason the first attempt captured silence, and it took an
hour to find because every other indicator says the sound is fine: the page's
own `AnalyserNode` reads **0.21 RMS**, PulseAudio shows a sink-input from
Chromium `Corked: no`, `Mute: no`, volume 100% — and every sample in it is
zero. Reading `/proc/<pid>/cmdline` of the running browser is what found it:

    chrome-headless-shell ['--mute-audio']

`ignore_default_args=["--mute-audio"]` is the fix, and it is passed **only**
when sound is wanted, so `--audio off` keeps the quieter browser. With it:
peak 9834 of 32767 for a page playing at gain 0.3, and a Goertzel over one
second puts every bit of the energy at 440 Hz and none at 220, 660, 880 or
1000. Both builds behave the same — it is Playwright, not the headless shell.

**The capture is a sink nothing listens to.** Chromium's protocol offers no
audio at all, so the only way is to give the browser an output and read it
back: `pactl load-module module-null-sink`, `PULSE_SINK` in the browser's
environment, and `parec` on its monitor. Works for any page, needs no
extension and no real sound device — which a container does not have. One sink
per panel, named after its host, so two panels never hear each other.

`PageAudio` holds **half a second and no more** (`deque(maxlen=25)` of 20 ms
blocks). Sound that could not be sent is sound whose moment has passed: a panel
that is behind wants the newest samples, not a backlog to catch up through.

**Sound goes out BETWEEN the rectangles of a picture, not behind them.**
`PanelWriter._drain_audio()` runs before each blob and after the last, and the
writer wakes for sound alone when no picture is in hand. A whole panel is a
quarter of a megabyte and takes a tenth of a second to write on a busy link;
audio queued behind that arrives in gaps, and a gap is a click.

Verified end to end against a fake panel replaying the board's parser: browser
→ null sink → `parec` → sender → socket → parser, **16.08 s of PCM, peak
9834/32767, all the energy at 440 Hz and none at 220 or 880**, with rectangles
arriving on the same socket at the same time. And `--audio off` creates no sink
and prints no `Audio:` line.

The add-on installs `pulseaudio` and `pulseaudio-utils` — non-fatal, like the
browser — and `run.py` starts one server for all panels before spawning any.
When there is none, the sender says so once and renders the picture regardless.

## The rename to portall

`usb_display` became **`portall`** because the name had stopped describing the
thing: the picture arrives over Wi-Fi, the touches go back over Wi-Fi, and USB
is one transport among several rather than the point. The C++ class is
`Portall`, the namespace `portall`, the files `portall.h` / `portall.cpp`, the
actions `portall.sleep` / `portall.wake`.

Two things deliberately did **not** move, and both for the same reason — the
gain is cosmetic and the risk is not:

- **`components/usb_display_tusb/` and every `CONFIG_USB_DISPLAY_*`.** That is
  the TinyUSB descriptor component and its Kconfig symbols, consumed by C code
  that nothing here can compile. The name is still accurate there, and it is
  invisible to anybody writing YAML.
- **`components/usb_display_tusb/` and every `CONFIG_USB_DISPLAY_*`** (above)
  are still the only things that kept the old name.

**The add-on's slug DID move in the end, in 2.0.0, and the reasoning that kept
it is worth keeping too.** It was `usb_display_panel`, and the argument against
touching it was real: Home Assistant identifies an add-on by its slug, so
moving it makes the Supervisor see a new add-on -- options are not carried
over and `/data` starts empty, which costs every browser profile and every
site signed into from a panel.

What settled it was the user, who pointed out the name came from Espressif's
`usb_display` and described nothing this project still does. The cost is paid
once and is smaller today than it will ever be. So: slug `portall`, folder
`portall/`, **major version 2.0.0** to say out loud that this is not a
drop-in update, and four steps in the README -- of which the important one is
that the Supervisor's own **Edit in YAML** turns "re-enter your configuration"
into copy and paste. What cannot be copied is `/data`, and the README says so
rather than letting it be discovered.

**`url:` in `config.yaml` is where "Visit the Portall page for more details"
comes from**, and it is gone. The only page to point at was the repository
this add-on shares with an ESPHome component, which is not what somebody
reading about a panel wants -- reported as exactly that, from the Info tab.

**And the very next thing those tabs did was draw a broken picture.** `DOCS.md`
opened with `![Portall](logo.png)`, which GitHub resolves and the Supervisor
cannot: it hands the file to the frontend as text and renders it there, with no
base address for a relative path. The logo already sits at the top of the
add-on's page as `logo.png`, so the picture belongs to `README.md`, which is
the file GitHub shows. `tools/checkaddon.py` fails on any relative image in
`DOCS.md` -- the fault was reproduced against the check before it was believed.

**Home Assistant shows BOTH files, on two different tabs, and getting that
half-right is what cost three releases.** `DOCS.md` is the **Documentation**
tab and `README.md` is the **Info** tab -- the first page anybody sees after
installing. The first attempt had the documentation in `README.md` only, so
the Documentation tab was empty; the second moved it and left `README.md` as
"a page for GitHub", which is wrong twice over, because that page is rendered
inside Home Assistant too and it kept both a relative image and a relative
link to `DOCS.md`. Neither resolves: the Supervisor hands these files to the
frontend as text, with no base address. `CHANGELOG.md` is the third tab. It was reported from the store, with a
perfectly good README sitting in the folder that nobody could reach from the
panel they had just installed. `DOCS.md` is the documentation now, `README.md`
is a short page pointing at it, and `tools/checkaddon.py` fails without either
of them -- along with `icon.png` and `logo.png`, which are the other two files
the store reads and silently does without.

`portall/icon.png` and `logo.png` are drawn by `tools/makeicon.py`
rather than kept as binaries nobody can edit: a picture in a repository that
cannot be regenerated is one nobody dares touch. The mark is a doorway inside
a screen, which is the whole of what this project does, and it was checked at
**32, 48 and 64 pixels** -- the sizes a store list actually renders -- because
that is where a clever mark turns to mud. Two faults were invisible at full
size and obvious there: the doorway was drawn *across* the screen's bottom
rail rather than standing on it, which reads as a broken box, and the wordmark
was pale ink on transparent, which disappears on a light card. The logo
carries its own ground now, since the store has both themes and which one a
viewer sees is not something this can know.

**`components/usb_display/` still exists, as a stub that refuses.** Without it,
an unchanged YAML fails with "Component not found", which says nothing. It
carries `CONFIG_SCHEMA = cv.invalid(...)` naming the four edits — and it
**registers `usb_display.sleep` and `usb_display.wake` as well**, which is not
decoration: ESPHome resolves actions before it validates component blocks, so
the first attempt failed with "Unable to find action with the name
'usb_display.wake'" and the explanation was never reached. Verified both ways
against `esphome config`: the renamed Waveshare example is valid, and the same
file with the old names prints the four edits.

It is not an alias. Re-exporting portall's schema from the stub only works when
portall happens to have been loaded too, and a compatibility path that works by
accident is worse than a rename that says so plainly.

## portall.exe -- the Windows sender with nothing to install

**The panel now asks for a program by name, so the program has to exist.** The
waiting screen says `RUN PORTALL.EXE ON YOUR PC`, and the reply to it was
*"que je dispose pas du logiciel que je pense que tu doit crée"* -- which is
fair: a screen that names a file nobody has is worse than a screen that says
nothing.

It is the **same file**, frozen. `components/wired_portall/udisp_send.py` is
what PyInstaller bundles; there is no second program to keep in step, which is
the same rule the wire format lives under.

`.github/workflows/portall-exe.yml` builds it on `windows-latest`, because
**PyInstaller cannot cross-compile** -- a Windows binary has to be built on
Windows and there is none here. `workflow_dispatch` for a build, a `portall-v*`
tag for a release with a stable link. The proof step runs `--version` and
`--help` on the artifact before it is uploaded: a build that produces an exe
which cannot start is worse than a build that fails.

**Three things in the script had to learn which of the two it is**, and each
was a fault waiting rather than a tidy-up:

- **`own_path()`.** PyInstaller puts the real program in `sys.executable` and
  points `__file__` inside a bundle that is unpacked to a temporary directory
  and **deleted afterwards**. `_install_copy()` copied `__file__`, so a login
  task installed from the exe would have pointed at a folder that no longer
  exists -- and it works exactly once, which is the worst way to fail.
- **The login command line.** A frozen build is registered directly; handing
  `portall.exe` to `pythonw.exe` is asking Python to run a Windows binary.
- **`--log-file`, and this is the one that could not be sniffed.** The log
  exists because a run started at login has nowhere to print, and the test for
  that was `sys.stdout is None` -- true under `pythonw.exe`, and **false for
  portall.exe**. The exe is built with a console so somebody who double-clicks
  it sees it working, and the login script then starts it with that console
  HIDDEN (`WScript.Shell.Run …, 0, False`): `sys.stdout` is a perfectly good
  handle to a window nobody can see, so every line goes nowhere while looking
  like it went somewhere. The flag is written into the command line at install
  time, so the login run says which it is rather than guessing.

Verified here by simulating PyInstaller -- `sys.frozen = True` and
`sys.executable` pointed at a stub -- over both modes and all three logging
cases: a console and no flag redirects nothing, a console with the flag writes
the file, and no console at all behaves as it always did. **The exe itself is
not built or run here**, and the workflow has never been executed.

## A second screen is Windows' to make, not this project's

**Reported as *"cela fonctionne mais ce n'est pas un ecran secondaire juste il
recopie l'ecran principal"*, with the sender's own explanation on the screen
above it.** The log was:

    No monitor is 1024x600. What Windows is showing:
        0: 3200x1086, all of them joined
        1: 1920x1080, at 0,0   <- primary
        2: 1280x800, at 1920,-6
    Set the virtual display to exactly 1024x600 and it will be picked up by
    itself. Sending the primary one, scaled, meanwhile.

Every word of that is true and it did not answer the question being asked. It
says what is MISSING; the reader wants to know what they are LOOKING AT, and
that it will not change on its own. `pick_monitor` now says the panel is
showing a copy of the main screen, that portall does not make screens because
Windows does and only makes one when there is a display adapter behind it, and
names the kind of thing to install.

**And the same missing screen is most of the frame rate.** Their stats, at
`--fps 30`:

    12.8 pictures/s, 18.4 rectangles/s, 18 whole, 333.9 KiB/s,
    panel wait 0%, 32 skipped, worst turn 47 ms

`panel wait 0%` rules out the link and the board outright, so the cost is in
the loop, and `skipped` counts turns where the writer still held the last
picture. Measured here on a dashboard-shaped picture, per picture:

| the screen being captured | grab-side work | whole-panel encode |
|---|---|---|
| 1920x1080 squeezed to 1024x600 | **18.7 ms** | 1.7 ms |
| a screen already 1024x600 | **0.7 ms** | 1.7 ms |

Twenty-seven times the work, thrown away for nothing: the scaling exists only
because the screen is the wrong size. The grab itself is 3.4x the pixels too
(2.07 MP against 0.61), and that half cannot be measured here -- there is no
screen in this container. So a virtual display at exactly the panel's size is
not merely how the panel becomes a second desktop; it is also the frame rate.

The message says both now, because the two were reported a week apart as
separate complaints and have one cause.

## Windows will not add a screen over Wi-Fi without something installed

**Pushed back on, rightly, as *"si vous quittez portall.exe vous pouvez plus
vous servir de cet ecran secondaire ... trouve une solution ... meme si tu me
dis que c'est impossible car moi je dis que c'est possible, tu ne cherches pas
au bon endroit"*.** Two of those are fair: the exe's lifetime had never been
addressed at all, and Miracast had been ruled out from memory rather than
re-checked. Researched properly this time, with sources.

**The chip half is closed and now has a citation.** The ESP32-P4 has a
**hardware H.264 ENCODER and a software decoder** (tinyH264) -- Espressif's own
component documentation. Miracast, and Miracast-over-Infrastructure with it,
mandates H.264 in the SINK. So the one way Windows adds a screen over Wi-Fi
with nothing installed is the one way this chip cannot receive. That is not a
gap in the search; it is the reason spacedesk ships a driver rather than
appearing in the wireless-display list.

**And Microsoft's own words say what the answer is instead:** "streaming the
display output over a network to a remote client (remote display) is one of
the typical scenarios where an **IDD** is required." A network screen on
Windows is an indirect display driver. There is no second route.

**Espressif already shipped one, and the board already speaks to it.** The
`usb_extend_screen` example carries a **signed** IDD driver --
`xfz1986_usb_graphic_250224_rc_sign.exe`, built from the open
`chuanjinpang/win10_idd_xfz1986_usb_graphic_driver_display` -- which binds by
product id: **0x2987** for a display-only board, **0x2986** for their composite
one with touch. Install it, plug the board in, and Windows gains a display
adapter. **No exe, nothing running, no terminal.** `components/wired_portall/
__init__.py` has warned about those two identifiers for a while, and
`yaml/ws-usb-screen.yaml` is already that configuration -- so the truly
zero-software route was sitting in this repository, over a cable, unmentioned.
That file now **validates** (it did not: an empty `api:` encryption key, then a
fallback hotspot SSID over the 32-character limit -- the same two faults
CLAUDE.md already records for the Guition pair, in a third file).

So the honest ranking, and it is about *signing* rather than about difficulty:

| | second screen | on the PC | verified |
|---|---|---|---|
| USB + Espressif's signed IDD | yes, real | **nothing** | their driver is signed; this pairing is untested here |
| Wi-Fi + Virtual Display Driver + portall.exe | yes, real | one signed driver, one program at login | driver signed via SignPath, installable by `winget` |
| Wi-Fi + a custom IDD speaking udisp | yes, real | **nothing** | needs an EV certificate; unsigned means test-signing mode, which is worse than running a program |

The third is the dream and the blocker is not code. An unsigned driver forces
Windows into test mode, which is a bigger ask of a household than a program in
the Startup folder. Espressif's is signed because Espressif paid for it.

**`--setup` is the second row made into one command**, because the complaint
underneath was never really about quitting: it was four manual steps, of which
the last has to happen at every login. It discovers the panel, installs the
driver by `winget install --id=VirtualDrivers.Virtual-Display-Driver`, sets the
virtual screen to the panel's own size, and installs the login task.

The resolution is set by **EDITING** the driver's `vdd_settings.xml`, never by
generating one: that file belongs to another project and carries elements this
one has never heard of, so writing a fresh one from a README would mean
inventing a schema -- which is how a working install becomes a driver that will
not start. Only `<resolutions>` and a `<count>` are touched, the original is
kept beside it, and every change is printed. One resolution rather than the
shipped list, which is both what makes `--monitor auto` able to recognise the
screen and the answer to a panel that reported *"il m'affiche plusieurs
ecrans"*.

Tested here on a realistic file over four cases: three resolutions and a count
of three collapse to one and a backup appears; a second run says "already
exactly one screen" and **rewrites nothing** (the first version reported the
same change twice, which reads as a setting that will not stick); a different
panel size changes it again; and a file with no `<resolutions>` is refused,
loudly and byte-for-byte untouched. **Nothing Windows-side is verified** --
no winget, no driver, no admin here.

**What is still true and worth saying plainly: portall.exe is the cable.** A
screen is a screen only while something drives it, which is as true of spacedesk
as of this. The fix is that it starts with Windows and is never looked at, not
that it stops being needed.

Sources: Espressif esp_h264 component docs; Microsoft "Indirect Display Driver
Model Overview"; espressif/esp-iot-solution `usb_extend_screen/windows_driver`;
VirtualDrivers/Virtual-Display-Driver.

## --setup said Done without checking anything, and that is our own rule broken

**Reported straight back: *"il se comporte comme un miroir et si tu quittes
portall.exe rien ne fonctionne, il n'est pas reconnu par windows"* -- from a
run that had just printed "Done. Restart Windows once."** The message was
written to be encouraging and it was not checked against anything at all.
`setup_windows` installed, configured, registered, and then announced success
whatever had happened. That is the silent no-op this file records half a dozen
times, written fresh.

**And the report contains its own diagnosis, which is the useful part.** A
virtual monitor belongs to the DRIVER, not to this program: with the driver
installed, Windows keeps that screen in Display settings whether or not
portall.exe is running. So "Windows does not recognise it" cannot mean the
program was closed -- it means **the driver is not installed**, and the setup
that said it was finished had not managed it.

Two faults, and the first is why it could go unnoticed:

- **`Get-PnpDevice -Class Display` was too narrow.** These drivers enumerate
  under more than one class depending on which one and which version, so a
  class filter is a way to answer "nothing installed" about a driver sitting
  right there -- and then the setup skips the install it was there to do. The
  query has no class filter now, matches Espressif's driver as well as the
  Virtual Display Driver, and returns each device's **Status**: present and in
  Error is a different problem from absent, and the two used to be the same
  empty string.
- **Nothing verified.** `--setup` now ends by asking Windows what it actually
  has and prints that instead of "Done", and **exits 1** when the driver is
  still missing.

**`--check` is the same question on its own, needing no administrator**,
because "it behaves like a mirror" has three causes that look identical from
the glass: no driver, a driver at the wrong size, and a driver Windows has
disabled. Each needs a different next step. It prints the driver and its
status, where the settings file is, every screen Windows shows, the size the
panel advertises, and whether any screen matches it.

Exercised over both states with the driver query and the monitor list stubbed:
their reported three screens with no driver gives NONE INSTALLED and "no screen
is 1024x600"; a fourth screen at the panel's size gives "monitor 3 is exactly
the panel's size, so this is a second desktop rather than a copy". **Still
nothing Windows-side verified** -- no winget, no driver, no administrator here.

## The message everybody reads never named the command that fixes it

**Three reports of the panel still being a mirror, each one pasted from a run
whose own output was on the screen above it.** The message described the
problem correctly and named the CLASS of answer -- "install a virtual display
driver (VDD Control)" -- and never once said `--setup`, which does the whole
thing. `--setup` was mentioned in exactly one place: inside `--check`, which
somebody has to already know about to run.

So the fix existed for three releases and the one page anybody actually reads
did not point at it. That is worse than not having built it: it looks like
being told to go and research something.

The message now prints the command, set apart, with what it will do. And the
asking-for-administrator step is gone: **`--setup` elevates itself**, through
`ShellExecuteW` with the `runas` verb, so a double-click reaches the same place
as a hand-opened administrator terminal. "Right-click the Start menu, choose
Terminal (Administrator), then type this" was reported three times as simply
not happening -- which is fair, because somebody who downloaded one file
expects to run that file.

`--pause` goes with it, and it is in a `finally`: the elevated console closes
the instant the work ends, and the runs worth reading are exactly the ones that
failed, so a `SystemExit` carrying the explanation must keep the window open
too.

**The general shape, which this file has now recorded in three costumes:** a
capability that exists but is not reachable from where the reader is standing
has not been delivered. The keyboard that worked while invisible, the add-on
option that never reached `command_for()`, and now a command nobody was told
about.

Neither the elevation nor the driver install is verified here -- there is no
Windows, no winget and no administrator in this container. What is checked is
that the message prints the command against their exact three-monitor layout,
that `--setup` still refuses cleanly off Windows, and that `--pause` reaches
the help.

## "Comment je le fais avec le terminal ?" is the answer being wrong

**Four rounds ended there**, and the question is the finding. Round one named
the class of driver to go and install. Round two named `--setup`. Round three
made `--setup` elevate itself. Every one of them still ended with a command
somebody had to type somewhere they had to find first -- and a person who
downloaded one file and double-clicked it should not have to learn where
PowerShell lives to make their screen work.

So the plain run now **asks**. `offer_setup` puts one question on the screen at
the one moment the fault is certain and somebody is watching, and Enter is yes.

**Three guards, and the middle one is the one that matters.** A run started at
login has a console nobody can see -- a prompt there would wait for a keypress
for ever with the panel dark and nothing saying why. That is the same hole
`--log-file` exists for, so `--log-file` is what marks it, with an absent or
redirected stdin as the second test. The others: Windows only, and only when
the screen really is the wrong size, so a panel that is already a second
desktop is never asked anything.

Exercised over six states with stdin and the platform stubbed -- a login run, a
console that is not a tty, stdin gone entirely, a screen already the panel's
size, not Windows, and a watched run answering no -- all silent and all False;
then the yes path with the elevation stubbed, which asks and returns True.

**The shape, now recorded a fourth time in this file:** a fix the reader cannot
reach from where they are standing has not been delivered. The invisible
keyboard, the add-on option that never reached `command_for()`, the command
that was never named, and now the command that was named and still had to be
typed in a window nobody could find.

## A screen can be the right size and still be a copy: Windows was duplicating

**Reported after the setup finally worked: *"tout s'est bien passe au
redemarrage de windows, il affiche un miroir de mon ecran primaire, il ne fait
pas secondaire"*.** The driver installed, the resolution took, the restart
happened -- and the panel was still a mirror. Every check this program had said
the screen was there and the right size, and every one of them was true.

**Windows' projection mode is what decides it, and nothing about the driver
does.** In *Duplicate*, both adapters are handed the same desktop: the virtual
display exists, is exactly the panel's size, is found by `--monitor auto`, and
its content IS the primary's. From the glass that is indistinguishable from
having no second screen at all, which is why it survived four rounds of fixing
the driver.

**Two monitors sharing an origin is the signature**, and it is the only thing
here that can see the difference: cloned adapters report the same rectangle.
`duplicated()` returns those indices, `pick_monitor` says so at the moment it
picks such a screen, and `describe_monitors` marks them in every listing --
including `--check`, whose whole job is to separate causes that look identical.

Three things came from it, and the second is the one that should have existed
already:

- **`--extend`**, which runs Windows' own `DisplaySwitch.exe /extend` -- what
  Win+P drives, and it needs **no administrator**. The one fix in this whole
  area that costs nothing.
- **`--setup` now asks for it**, before it checks its work. A virtual display
  can arrive cloning, so a setup that installs the driver and sets the
  resolution and stops has done everything except the step that makes the
  screen a screen.
- and the message names both routes, because Windows+P is faster than finding
  a terminal -- which this file has already recorded four times.

Exercised on both layouts: a virtual screen sharing the primary's origin is
picked and then called out with both fixes; the same screen at its own offset
is picked silently. `duplicated()` returns `[1, 3]` and `[]` respectively.
**Not verified on Windows** -- no DisplaySwitch here, and whether a cloned VDD
really reports a shared origin on their machine is the one assumption in it.
Their `--check` output is what settles that, and it now prints the positions.

## Installed and switched off answers "yes" to every question this asked

**Reported with the fact that settles it: *"il est deja en mode etendre mais
il ne fait pas un ecran a part que je peux glisser des fenetres"*.** Windows was
extending, and there was no third screen to drag anything onto -- so the
duplicate theory of the previous round was wrong, and Windows simply had no
such monitor.

**A driver can be installed and DISABLED, and those are different questions.**
`virtual_display_present()` returned True for any matching device whatever its
Status, so `--setup` saw "already installed", skipped the install it did not
need, and **never did the enable it did**. A driver that is switched off
creates no monitor at all -- which is exactly a panel showing a mirror after a
setup that reported success and a restart that changed nothing.

`virtual_display_state()` returns present, working and the text separately;
`enable_virtual_display()` runs `Enable-PnpDevice` by **instance id** rather
than by name, on every device that is not already OK. `--setup` calls it when
the driver is there and not running, and `--check` says which of the two states
it is, because they need different next steps.

**And the failing run is the diagnostic now.** Four rounds of this ended with
the driver's own state never once being seen, because `--check` is a thing
somebody has to know to run and the ordinary run is the thing they already ran.
So `pick_monitor`'s fallback prints the driver and its status before the
monitor list. Asking somebody to run a second command to find out why the first
one did not work is the same mistake as naming a command they then have to
find a terminal for, in a smaller costume.

Exercised with the state query stubbed: a driver in Error prints
`installed but NOT running, so it makes no monitor` above the monitor list,
then the usual explanation. **Not verified on Windows** -- no `Get-PnpDevice`
and no `Enable-PnpDevice` here, and whether the Status really reads Error
rather than Unknown for a switched-off VDD is the assumption the next run
settles.

## The driver was never installed, and every round since was work on nothing

**Settled by looking in the right place: *"il n'y a que deux moniteurs, sur le
gestionnaire de peripheriques il n'y a pas virtual display"*.** Device Manager
is the ground truth and it says the driver does not exist. So `winget` failed,
and `--setup` went on to set a resolution, ask for Extend and install a login
task **for a driver that was never there** -- three rounds of increasingly
careful work on the steps after the one that had not happened.

Everything this file recorded in between was true and beside the point: the
duplicate-detection, the enable-when-disabled, the projection mode. Each was a
real gap and none of them was the fault.

**The lesson is the one already written here twice, in its third costume: ask
the world, not the step.** `winget` returning 0 is winget's opinion; the
device list is Windows'. `setup_windows` now re-reads the device list after the
install and treats *reported success with no device* exactly as failure --
which is what happened, and what nothing checked.

And on that failure it **opens the releases page** rather than describing it.
winget is not on every Windows, its source can be missing or stale, and it
needs agreements accepted -- so the automated install is the step here most
likely to fail on somebody else's machine, and it is the one this project can
least afford to be casual about. The manual install is an ordinary download.
Naming it in a sentence was the same mistake as naming `--setup` in a sentence,
which this file already records.

It also **stops**, rather than carrying on: nothing downstream can work without
the driver, so configuring it is work nobody will benefit from and a success
message nobody should believe.

Exercised on the case that cost the round -- winget reporting "Successfully
installed" with the device list empty: it prints what winget said, prints and
opens the page, and raises rather than continuing. **Not verified on Windows**;
what is verified is that a lying exit code no longer gets past.

## It works end to end -- and the remaining ask is a dependency, not a bug

**Reported after installing the driver by hand: *"il apparait dans le
gestionnaire de peripheriques et ecran est bien ecran secondaire"*.** So the
whole chain is proven on real hardware: the board's YAML, the mDNS
advertisement, `portall.exe` finding the panel by itself, the virtual display
at 1024x600, and Windows extending onto it. A panel is a second Windows desktop
over Wi-Fi.

**And the objection that came with it is factually right**, which is why it is
recorded rather than argued with: *"je ne veux pas d'installation tiers qui
n'est plus a jour depuis 2025"*. Checked rather than assumed -- the Virtual
Display Driver's latest release is **23 July 2025**. For a Windows *driver*,
fourteen months without one is a real risk and not a preference: Windows
updates move underneath it and nobody is watching.

**Why a driver is needed at all is settled and sourced** (see the section
above): Windows creates a desktop only for a display adapter, and a program
cannot be one. So the dependency cannot be removed by writing better Python.
There are exactly three ways out, and they differ in what they cost:

| | third party | maintained | cost |
|---|---|---|---|
| **Espressif's signed IDD, over USB** | Espressif's own, in their SDK | yes | a cable. `yaml/ws-usb-screen.yaml`, which validates |
| **Parsec's VDD** | a live commercial product's driver | yes | still somebody else's |
| **our own IDD speaking udisp** | **none** | ours | **an EV certificate or a SignPath sponsorship**, plus a Windows driver |

The third is what the user is asking for and the blocker is **not code**. The
starting point exists and is open -- `chuanjinpang/win10_idd_xfz1986_usb_
graphic_driver_display`, which is what Espressif's own driver is built from, so
adapting its transport from a USB pipe to a TCP socket is a bounded change.
What stops it is signing: unsigned means Windows test-signing mode, which is a
far bigger ask of a household than any of the above.

**And the route to signing is known, because it is the one VDD itself took.**
SignPath's foundation programme signs open-source projects for free, and its
own project list carries both the Virtual Display Driver and ParsecVDisplay. So
this is an application and a wait, not a purchase -- but it is a project-level
step, and **nothing about a Windows driver can be built or tested in this
container**: no WDK, no Windows, no certificate. The honest position is that
the board side and the sender are done, and this last piece is paperwork
followed by C++ that only a Windows machine can prove.

Sources: VirtualDrivers/Virtual-Display-Driver releases; signpath.org project
list; espressif/esp-iot-solution `usb_extend_screen/windows_driver`.

## An ESP32-P4 drives a USB Bluetooth dongle, and it is measured

**The C6 is BLE only -- no Classic, so no A2DP and no Classic HID -- and that
was written here as the end of the matter.** It is not. A dongle carries its
own controller, the P4 has a spare USB controller to host it on, and the whole
chain now works on an M5Stack Tab5 with a Broadcom BCM20702A1 in the USB-A
socket:

    HCI Reset answered, status 00, credit 1 -- the dongle is talking
      HCI version 6, LMP version 6, manufacturer 15
      address 00:02:72:DC:33:59
      name "BCM20702A"

LMP version 6 is Bluetooth 4.0, so BR/EDR -- **Classic**. Manufacturer 15 is
Broadcom in the SIG's own list. The address is the controller's real one, not
the `20:70:02:A0:00:00` an unconfigured BCM20702A1 reports. And the name is a
255-byte event reassembled from sixteen 16-byte packets, which is the proof
that the long path works and not only the short one.

`components/portall_bt/` is the firmware, `yaml/tab5-bt-probe.yaml` the
configuration, and `tools/checkbt.py` compiles the component with a plain g++
against stand-in headers -- which is the only C++ check this repository has
ever had and it caught real faults on its first day.

**The correction to make first, because this file said otherwise.** It said
portall takes "the board's single USB OTG controller". The P4 has **two**, one
high-speed and one full-speed, and Espressif's datasheet says the pair is what
allows several USB peripherals in host mode at once. What is true is narrower:
portall takes the HIGH-SPEED one in device mode, and on the Tab5 the USB-A
host socket is on that same controller -- so the dongle and portall cannot
share a Tab5 today. The probe is therefore a firmware of its own with no
`portall:` block, and the follow-up is an option that lets a Wi-Fi-fed panel
skip TinyUSB entirely, which is worth having anyway.

### The seven faults, in the order they were found

Each one cost a round trip to a board, and every one of them is a shape this
file already records under another name.

**CherryUSB's Bluetooth class driver is switched off for ESP-IDF, and the
reason is a constant.** `# set(CONFIG_CHERRYUSB_HOST_BLUETOOTH 1)` is commented
out in its CMakeLists and its Kconfig carries `depends on !IDF_CMAKE`. Then
`osal/idf/usb_config.h` fixes `CONFIG_USBHOST_MAX_INTF_ALTSETTINGS` at **2** --
and a dongle's SCO interface has **six** alternate settings, so the parser
abandons the whole configuration descriptor and nothing enumerates. That
driver could never have bound to anything. `components/cherryusb_patch/` is an
IDF component that compiles nothing and edits that line in the BUILD
directory: every component's CMakeLists runs during CMake's configure step,
after the manager has downloaded its dependencies and before anything is
compiled. The first answer was a fork, and it was wrong -- a second repository
to keep in step and a build that failed for everybody who had not made it,
which is exactly what happened when the pointer was pushed before the fork
existed.

**A dongle's HCI interface is often NOT class 0xE0.** This Broadcom says
`ff/01/01` on interfaces 0 and 1 and `ff` at the device level too, so whole
families ship a vendor coat to make Windows load the manufacturer's stack.
Linux does not treat it as different: btusb.c binds it with
`USB_VENDOR_AND_INTERFACE_INFO(0x0a5c, 0xff, 0x01, 0x01)` and a dozen more
vendors on the same line. **The subclass and the protocol say Bluetooth; the
class says which driver the manufacturer was hoping for.** The TP-Link UB500
(`2357:0604`) is the textbook opposite -- `e0/01/01` everywhere, two
interfaces -- so both paths are proven on real hardware.

**A budget counted in attempts is not a clock.** The first wait assumed each
read cost the half second it asked for, which is true only when a read times
out: a refusal returns in microseconds, so sixteen of them spent a
three-second allowance instantly and the failure reported silence after no
wait at all. The panel's own log showed the command going out and the failure
being logged in the same second.

**And it reported nothing else, twice.** "Nothing came back" is the same
sentence whether the endpoint refused, answered empty, or answered something
else. Then the follow-up reads logged only their successes, so a dongle that
answered one command and not the next produced a log that stopped
mid-sentence. There is one path for a command now and it cannot be quiet:
`hci_ask()` reports the send, the wait with its numbers, and a status byte
that refuses.

**The data toggle lives in the URB, not in the endpoint.** This is the one
worth remembering. `usb_hc_dwc2.c` starts a transfer with `urb->data_toggle ==
0 ? HC_PID_DATA0 : HC_PID_DATA1` and writes the new toggle back into the same
field. A urb declared on the stack therefore begins every read at DATA0 while
the device alternates, so every second packet arrives with the wrong PID and
is discarded -- `HCI Reset` answered, `Read Local Version` NAKed 137 times,
`Read BD Address` answered, `Read Local Name` timed out. **One, miss, one,
miss**, and the same pattern appeared on a second dongle from a different
manufacturer, which is what settled that it was ours. Every class driver in
CherryUSB keeps its urbs in its own structure for exactly this reason. It also
explains the run before it that looked "intermittent": whether the FIRST read
matched depended on where the dongle's toggle happened to be.

**A long event has to be read a packet at a time.** `Read Local Name` answers
with 255 bytes, seventeen packets on a 16-byte endpoint, and asking for all of
it in one periodic transfer timed out five times running while every answer
under 16 bytes arrived at once. CherryUSB's own driver fills its urb with
`ep_mps` and accumulates until a short packet; Linux's btusb reads
`wMaxPacketSize` and reassembles in `hci_recv_fragment`.

**And the fix for that killed the firmware, on a sentence in its own commit
message.** It said "every full packet is a multiple of the packet size, so the
offset handed to DMA stays aligned" -- reasoning from an alignment of 4. With
the data cache on this port wants **64**, the packets are 16, and the second
read pointed 16 bytes past a boundary:

    ASSERT FAIL [!((uintptr_t)urb->transfer_buffer % CONFIG_USB_ALIGN_SIZE)]
    urb->setup or urb->transfer_buffer is not aligned 64
    task_wdt: CPU 1: portall_bt -- Aborting.

The task stopped inside the assert and the watchdog took the whole firmware.
An assumption about somebody else's constant, written as a statement of fact,
in a comment that read as though it had been checked -- and the value was one
grep away. Every read lands in a staging buffer of its own now, a whole cache
line wide, and is copied out afterwards.

### Reading the log, because two numbers look like errors and are not

`usbh_control_transfer` returns the bytes transferred, **setup packet
included**. So `selecting altsetting 0 ... returned 8` is a SET_INTERFACE with
no data stage succeeding, and `control said 11` is 8 + a three-byte HCI
command succeeding. The error codes are in `common/usb_errno.h` and are
NEGATIVE: -3 NODEV (somebody pulled the dongle out), -10 NAK (the endpoint has
nothing to give, which is a controller that never answered rather than a
transfer that failed), -14 TIMEOUT.

### The radio works, and an inquiry is what proves it

    asking for extended results
    listening for Bluetooth devices for about 10 seconds
      found 46:E8:1C:8A:88:DD  audio/video   -64 dBm
    inquiry finished, status 00, 1 device heard

An inquiry is Bluetooth CLASSIC, which is exactly what the C6 cannot do, so a
device named there is one no panel in this project could have heard before.
`inquiry_seconds:` governs it and 0 turns it off; mode 2 is asked for first so
each result carries an RSSI and an extended inquiry response, whose 0x09 field
is the device's own name. A device that publishes no EIR is reported by the
controller with the plain with-RSSI event instead, so an empty name there is
the specification working rather than a lookup that failed.

Two faults on the way, both a number borrowed from the wrong context -- the
same shape as the alignment one above:

- The three inquiry-result events carry the same fourteen bytes in two
  arrangements, so **the Class of Device is at offset 9 in one and 8 in the
  other**. One offset for both folds a reserved byte into it and calls a
  headset a toy.
- **An event shorter than six bytes was dropped at the door.** Six is the
  minimum of a Command COMPLETE and belongs to the function that waits for
  one; `Inquiry Complete` is THREE bytes, so the end of every scan was thrown
  away and the panel sat out its whole deadline before reporting an inquiry
  that had finished on time. Each case checks its own length now.

**And the inquiry takes the Wi-Fi down, which is a real constraint and not a
detail.** Measured twice, on two different channels:

| | inquiry | Wi-Fi |
|---|---|---|
| run 1 | 22:23:35 - 22:23:49 | fails 22:23:38, reconnects **22:23:50** |
| run 2 | 22:29:04 - 22:29:14 | fails 22:29:07, reconnects **22:29:14** |

`Authentication Failed`, `Probe Request Unsuccessful`, three adapter restarts
-- and the Wi-Fi comes back the second the inquiry ends, both times. The
Livebox is on channel 1 in one run and 11 in the other, so it is not a
channel: an inquiry sweeps the whole 2.4 GHz band at full power, which is the
most disruptive thing Bluetooth does, and the dongle's antenna is centimetres
from the C6's.

What follows from it: **a scan is a rare, on-demand event and never a loop**
-- this probe firmware does one per boot because it is a probe. An established
connection should be far gentler, since Bluetooth then hops 79 known channels
instead of sweeping; that is reasoning, not measurement, and the A2DP work is
where it gets tested. A 5 GHz access point would sidestep it entirely.

### What is NOT done

**No host stack.** This speaks the transport, asks four questions and runs an
inquiry; it does not pair, connect or carry audio. NimBLE is BLE only, so
**A2DP needs Bluedroid** -- the only Classic stack in ESP-IDF.

**And ESP-IDF's own Kconfig says that configuration exists**, which settles
the question this file previously recorded as open. In `components/bt/Kconfig`:

    config BT_ENABLED              depends on !APP_NO_BLOBS
    config BT_BLUEDROID_ENABLED    (no dependency at all)
    config BT_CONTROLLER_ENABLED   depends on SOC_BT_SUPPORTED
    config BT_CONTROLLER_DISABLED  (no dependency)
        "This option is recommended for Bluetooth Host only usecases"

`esp32p4/include/soc/soc_caps.h` defines no `SOC_BT_SUPPORTED`, so
`BT_CONTROLLER_ENABLED` is unavailable on a P4 and `BT_CONTROLLER_DISABLED` is
the only choice -- which is exactly the one wanted. **Bluedroid dual-mode,
host only, is selectable on an ESP32-P4 in stock ESP-IDF.** Selectable is not
linked and not running, but it is no longer a guess about whether the route
exists.

That same header is also the citation for the two OTG controllers, in
Espressif's own words: `#define SOC_USB_OTG_PERIPH_NUM (2U)`.

**Selecting it is not enough: ESPHome EXCLUDES the `bt` component from every
build.** The options went in, Bluedroid compiled -- 1430 objects of it -- and
our own file still could not see `esp_bt_main.h`. ESP-IDF said why in as many
words: portall_bt.cpp is in the `src` component, the header is provided by
`bt`, and `bt` is not in src's requirements.

That is ESPHome's doing rather than ESP-IDF's. `DEFAULT_EXCLUDED_IDF_COMPONENTS`
carries `bt` (its own comment says "re-included by request_bluetooth()"), and
`src/CMakeLists.txt` is generated with `REQUIRES
${ESPHOME_PROJECT_BUILTIN_COMPONENTS}` -- which is the discovered component
list MINUS that exclusion set. So the sdkconfig options build the stack and
leave it out of reach, and the one call that fixes both halves is
`esp32.include_builtin_idf_component("bt")`. Verified against the generated
files: before it, no `ESPHOME_PROJECT_BUILTIN_COMPONENTS bt` line exists;
after it, there is one. Present in 2026.6.5 and 2026.10.0-dev alike, which is
why it is called rather than the newer `request_bluetooth()` -- that one does
not exist in 2026.6.5.

**The other half of that sentence used to read "-- that one also writes BLE
sdkconfig defaults nobody here wants", and it was wrong.** Those defaults are
`CONFIG_BT_BLE_42_FEATURES_SUPPORTED` on and `50` off, and they are exactly
what this needed: see the 0x204A section below, which cost three rounds. The
portability reason for not calling it stands; the judgement about its defaults
did not, and the two lines are written here deliberately now.

**And the glue is NOT VHCI, which is what this was about to be written
against.** `esp_vhci_host_send_packet` was the assumption, from the ESP32's
own controller API. Read rather than remembered: `components/bt/controller/
CMakeLists.txt` exposes `../include/<target>/include` **only when the
controller is enabled**, so on a P4 there is no `esp_bt.h` to call at all --
and bluedroid's own `hci_hal_h4.c` guards its include with `#if
(BT_CONTROLLER_INCLUDED == TRUE)` and calls `hci_host_send_packet()` /
`hci_host_register_callback()` instead.

Those come from **`esp_bluedroid_hci.h`**, which is Espressif's supported hook
for exactly this case -- a host with somebody else's controller:

```c
typedef struct esp_bluedroid_hci_driver_callbacks {
    void (*notify_host_send_available)(void);
    int  (*notify_host_recv)(uint8_t *data, uint16_t len);
} esp_bluedroid_hci_driver_callbacks_t;

typedef struct esp_bluedroid_hci_driver_operations {
    void      (*send)(uint8_t *data, uint16_t len);
    bool      (*check_send_available)(void);
    esp_err_t (*register_host_callback)(
                  const esp_bluedroid_hci_driver_callbacks_t *callback);
} esp_bluedroid_hci_driver_operations_t;

esp_err_t esp_bluedroid_attach_hci_driver(
              const esp_bluedroid_hci_driver_operations_t *ops);
esp_err_t esp_bluedroid_detach_hci_driver(void);
```

Three functions, attached before `esp_bluedroid_init()`. What crosses them is
**H4**: one leading byte says command (0x01), ACL (0x02), SCO (0x03) or event
(0x04), which is the same split USB already makes physically -- commands on
the control endpoint, ACL on bulk, events on the interrupt IN. So the mapping
is a demultiplex rather than a translation, and it is what `btusb.c` does too.

The lesson is the ordinary one and it was nearly paid for again: the link
error would eventually have named these symbols, and reading Espressif's
header named them first, correctly, and said what their arguments mean.

**And the build that was meant to answer this said nothing, because the
option is off by default.** The dongle answered, the inquiry ran, the log was
the working probe exactly as before -- and not one line of it was about
Bluedroid, because `yaml/tab5-bt-probe.yaml` carried no `host_stack:` and
`try_host_stack_()` returns at its first line when the option is `none`. A
round trip to a board spent proving nothing had broken.

That is the silent no-op again, in the one costume this file had not yet
recorded: not a check that passed vacuously, but a QUESTION that was never
asked. The probe firmware now asks it in the repository rather than leaving it
to whoever builds.

**And with the question asked, the answer came back on the first run:**

    Bluedroid initialised -- it compiles, it links, and it is up to
      esp_bluedroid_enable() next, once this component can carry its HCI

So **Bluedroid compiles, links and initialises on an ESP32-P4** with
`BT_CONTROLLER_DISABLED`. Selectable was a Kconfig reading; this is the chip.

### The transport, which is the glue

`hci_drv_send` / `hci_drv_check_send_available` /
`hci_drv_register_host_callback`, attached with
`esp_bluedroid_attach_hci_driver()`, plus two reader tasks. The mapping is the
one H4 already implies: a command goes out as a class request on the CONTROL
endpoint, ACL on the bulk pair, events come back on the interrupt IN, and SCO
is isochronous and is not carried -- which costs a headset's microphone, not
its music, since A2DP is ACL.

Five things had to be decided rather than typed, and four of them are
shapes this file already records:

- **The order is Espressif's, not a preference.** Attach the transport, THEN
  `esp_bluedroid_init()`, THEN `enable()` -- their header says the driver is
  attached "before initialization". So `setup()` no longer initialises
  anything: there is no dongle yet to attach, and init came first there. The
  whole sequence moved to `start_host_stack_()`, after the probe.
- **The readers start BEFORE `enable()`.** The first thing enable does is send
  an HCI Reset and wait for its Command Complete; with nothing reading the
  endpoint that wait can only time out.
- **The event reader REUSES `g_event_urb`**, the probe's own. CherryUSB keeps
  the DATA0/DATA1 toggle in the urb, so a fresh one would restart at DATA0
  against a device that has been alternating since the reset -- the "one, miss,
  one, miss" fault above, which cost a round trip to find the first time.
- **ACL needs a task of its own.** Both endpoints block and one reader cannot
  wait on two; btusb submits both at once for the same reason.
- **`check_send_available()` is unconditionally true, and that is a statement
  about `send()`** -- it does not return until the transfer is done, so nothing
  is ever outstanding. It also means `notify_host_send_available()` is never
  called from inside `send()`, which keeps this off the path back into
  Bluedroid's own task.

The alignment rule from the probe is restated where it bites: the buffers
handed to the CONTROLLER are `USB_NOCACHE_RAM_SECTION USB_MEM_ALIGNX`, and the
two frames assembled by `memcpy` are ordinary memory. Confusing those is the
64-byte assert that took the whole firmware down once.

**The proof is one line and costs nothing:** after enable, the address is read
from `esp_bt_dev_get_address()` -- out of Bluedroid, which has never seen the
dongle except through those three function pointers -- and printed beside the
one the probe read for itself. Same dongle, different code path, different
buffer, different task. If they match, the transport is carrying real answers
rather than plausible ones.

### The C++ check was passing on code it had never read

`tools/checkbt.py` compiled the file once, with no `CONFIG_BT_BLUEDROID_ENABLED`
-- so every line of the host stack sits behind an `#ifdef` the only C++ check
in this repository never opened. It is the newest code, it calls the least
familiar API, and no user had ever built it. It compiles **both** ways now,
with stand-ins for `esp_bluedroid_hci.h`, `esp_bt_main.h`, `esp_bt_device.h`
and `esp_err.h` beside the CherryUSB one.

Reproduced against a broken copy before the check was believed: a misspelt
`esp_bluedroid_attach_hci_driver` passes the first configuration and fails the
second, naming the line.

**Still not compiled by a real toolchain**, and the stand-ins are the risk:
they say what these functions are called and what they take, not that the
build will accept them. What is NOT tested at all is ACL -- nothing has
carried a byte of it -- and SCO is not implemented.

### A frame that fills a packet exactly never ends, and Bluedroid stopped on the one that does

The transport carried Bluedroid's first four commands and died on the fifth:

    Bluedroid initialised; handing it the dongle
    BT_HCI: command_timed_out hci layer timeout waiting for response to a
            command. opcode: 0x1004

Four commands answered, which is the useful half of the report: the control
endpoint, the event endpoint, the reassembly and the callback all work. So
the question is what is special about 0x1004, and the answer is arithmetic.

**`Read Local Extended Features` answers in exactly 16 bytes** -- event code,
parameter length, num_hci_command_packets, the opcode, then status, page,
maximum page and eight bytes of features. Sixteen is the interrupt endpoint's
packet size. The reader ended a frame on a read SHORTER than a packet, so it
sat waiting for a packet the controller had no reason to send, the next read
NAKed, and a NAK throws the part-read frame away. For ever.

Of the seven commands Bluedroid sends while it starts, exactly one is a
multiple of sixteen:

| | event | last packet |
|---|---|---|
| 0x0C03 Reset | 6 | 6 |
| 0x1001 Read Local Version | 13 | 13 |
| 0x1002 Read Local Supported Commands | 70 | 6 |
| 0x1003 Read Local Supported Features | 14 | 14 |
| **0x1004 Read Local Extended Features** | **16** | **0** |
| 0x1005 Read Buffer Size | 13 | 13 |
| 0x1009 Read BD Address | 12 | 12 |

and the log stopped on that one. A diagnosis with a number in it, rather than
a guess.

**The probe never met it, and worked by arithmetic nobody had done.** Its four
answers are 6, 13, 12 and 254 bytes; not one is a multiple of 16. The
short-packet rule is how CherryUSB's own driver is written and it is what the
probe was proved against -- so it was inherited as sound and was merely lucky.

**HCI says how long everything is, so read that instead of counting packets.**
An event is code, parameter length, then that many bytes; an ACL packet is
handle, a little-endian length, then that many bytes. `event_length()` and
`acl_length()` are those two sentences, and both readers and the probe now
frame on them. It is also what btusb does -- `hci_recv_fragment` reads the
header rather than watching for short reads -- which is one more place this
project would have saved a round trip by reading the reference all the way
through.

**Bulk makes it worse, not better.** The ACL endpoints here are 64 bytes, so
the old rule would have hung on every payload of 60, 124, 188 and so on --
which is to say the data path was going to fail the same way the moment
anything connected.

### And the test now links against the component rather than describing it

`tools/bttest/framing.cpp` `#include`s `portall_bt.cpp` and calls the SHIPPED
`event_length()` and `acl_length()`, with the blocking read replaced by a
chopper. A test that restates the arithmetic it is checking proves only that
it can copy. `tools/checkbt.py` builds and runs it alongside the two syntax
passes.

It covers Bluedroid's seven startup events, 0x1004 alone, the probe's own four
with Read Local Name among them, **every** event length from 5 to 255 one
packet at a time, and ACL payloads either side of 60, 124 and 188. The last
case is the fault itself, kept rather than remembered: the old short-packet
rule is run against the 16-byte event and must deliver **nothing**.

**Two faults were in the TEST and neither was in the code**, which is the
usual shape:

- The chopper ran the frames together and cut packets across their boundary.
  Each frame is its own USB transfer, so that stream cannot happen -- and it
  made a correct reader look broken on the very first frame.
- `Read Local Extended Features` was given ten return parameters instead of
  eleven, so the event came out 15 bytes and the case that matters was not
  being tested at all. The test said so itself, because the premise is
  asserted: "the premise: 0x1004 answers in 15 bytes, not 16".

### A NAK is silence, not a lost frame, and treating them alike breaks the stream for good

The length rule got past 0x1004 and Bluedroid then refused a frame in its own
parser:

    assert failed: read_command_complete_header hci_packet_parser.c:283
    (parameter_length >= (parameter_bytes_we_read_here + minimum_bytes_after))

Read rather than guessed at: `read_command_complete_header` reads
num_hci_command_packets, the opcode and the status -- four bytes -- and each
caller says how many more it needs (0 for a generic command complete, 7 for
Read Buffer Size, 8 for Read Local Version). So the assert means the frame
handed up declared a parameter length too small for what it is.

And both readers had a way to produce exactly that. On ANY error they did
`filled = 0`, NAKs included -- and a NAK is what an interrupt endpoint with
nothing to report answers, hundreds of times, which the probe itself measured
at about eleven milliseconds each. Clearing on one does not merely drop the
frame being read: **the packets of that frame that have not arrived yet
arrive afterwards and are assembled as a FRESH frame**, whose second byte is
somebody's payload rather than a length. One dropped packet therefore breaks
the stream for good rather than for one frame, and the first thing downstream
reads out of the wreckage is a bad parameter_length.

`read_failed_for_good()` is the distinction: NODEV, NOTCONN and SHUTDOWN clear
and stop, everything else is waited through with what has been read kept. The
probe got away with the old behaviour because nothing else was running to
interrupt a long event; with two reader tasks, Bluedroid's own tasks and Wi-Fi
beside it, the timing is not the same firmware's.

**This is not proven to be THE cause of that assert** -- it is a real defect
that produces exactly its signature, found by reading the code the log pointed
at. What settles it is the next run, because the transport now says what it
hands up.

### The log was silent where the evidence is, for the third time in this file

Bluedroid's startup is a dozen commands and it refuses the first malformed
answer with an assert naming its own parser and nothing whatever about what it
was given. So a fault in this transport reaches a panel as a reboot and a line
about somebody else's file.

`say_frame()` prints the first **twelve** frames handed up -- length and the
first four bytes, which for an event is the code and the parameter length --
then goes quiet for ever. `too_long()` refuses a declared length this reader
cannot hold and says so rather than reading past its own array; it cannot
happen, which is exactly why it must not be silent if it does.

That is the same lesson as `--show-media` and as `--show-touches`: the
evidence for a failure is in the boring frames before it, and every one of
those diagnostics was built after the guessing rather than before it.

The framing test gained the case that was missing: a NAK every other packet
across three multi-packet events. Keeping what was read delivers all three;
the old behaviour delivers one frame of the wrong size, and the test asserts
that it does -- a check that cannot fail on the broken code is not a check.

### Both fixes are confirmed, and the diagnostic stopped one line short of the fault

The next run printed twelve well-formed frames and then asserted, and two of
those twelve are the proof:

    event 70 bytes, first four 0e 44 01 02    Read Local Supported Commands
    event 16 bytes, first four 0e 0e 01 04    Read Local Extended Features

Seventy bytes is five packets arriving whole, which is the NAK fix; sixteen is
the frame that fills a packet exactly, twice, which is the length fix. Every
one of the twelve matches a command in Bluedroid's own startup, in its order:
reset, buffer size, controller-to-host flow control, host buffer size, local
version, address, supported commands, extended features, simple pairing, LE
host supported, extended features page one, and `0e 05 01 0f` -- which is
**LE Read White List Size, 0x200F**.

So the transport is carrying real answers. The assert is on the THIRTEENTH
frame, and the reporter's cap was twelve.

**A budget on a diagnostic is a guess about where the fault is, and this one
guessed wrong by a single line.** The whole point of `say_frame` was to make
the next failure legible, and it stopped exactly one frame before it. Sixty-
four now, which is above the thirty-odd commands Bluedroid sends while it
starts -- a panel that gets past startup pays them once and is quiet
afterwards.

**And the line made a reader decode hex by hand.** `0e 05 01 0f` is an opcode
split across two bytes, the second of which was not printed at all. It says
`complete for opcode 200f, 5 parameters` now. Checked by CAPTURING the
function's own output over five frames a panel really sent, rather than by
recomputing `frame[3] | frame[4] << 8` in the test -- and proved by swapping
the two bytes, which fails all five.

What comes after 0x200F was read out of Espressif's `controller.c` rather
than guessed: `make_ble_read_buffer_size()`, **ungated**. So the thirteenth
frame is the Command Complete for LE Read Buffer Size, and whether it is
malformed by us or simply short because this controller refuses it is what
the next log says. A **BCM20702A1 is Bluetooth 4.0**, and Bluedroid is
written against Espressif's own controller, which answers everything it asks
-- a controller that replies "Unknown HCI Command", which is status and
nothing else, gives a parameter length of 4 where the parser wants more, and
that assert is exactly what that would look like. Not proven; named, so the
next run can settle it in one line.

### Fifteen frames, every one correct, and the arithmetic names the sixteenth

The named opcodes came back and they are Bluedroid's startup exactly:

    1: 0c03 Reset            6: 1009 Read BD Address   11: 1004 page 1
    2: 1005 Read Buffer Size 7: 1002 Supported Cmds    12: 200f LE White List
    3: 0c31 Flow Control     8: 1004 Extended Features 13: 2002 LE Buffer Size
    4: 0c33 Host Buffer Size 9: 0c56 Simple Pairing    14: 201c LE States
    5: 1001 Local Version   10: 0c6d LE Host Supported 15: 2003 LE Features

Thirteen answered the way the previous round predicted -- 9 bytes, 7
parameters, which is what LE Read Buffer Size owes. Fourteen and fifteen the
same. **The transport is not the fault.**

So the sixteenth is, and `controller.c` narrows it to two commands without a
board. Everything left in `start_up()` after 0x2003 is one of:

| | gate | parser |
|---|---|---|
| `ble_read_resolving_list_size` **0x202A** | `HCI_LE_ENHANCED_PRIVACY_SUPPORTED` | needs 1 byte after |
| `ble_write_suggested_default_data_length` 0x2024 | `HCI_LE_DATA_LEN_EXT_SUPPORTED` | generic |
| `ble_read_suggested_default_data_length` **0x2023** | same gate | needs 4 bytes after |
| `ble_set_event_mask` 0x2001 | none | generic |
| `set_event_mask` 0x0C01 (Classic) | none | generic |

**Every ungated one is `parse_generic_command_complete`, which needs a
parameter length of 4 -- the minimum any Command Complete can have. None of
them can assert.** So the asserting frame is the answer to **0x202A or
0x2023**, and both are Bluetooth **4.2** commands gated on a bit in the LE
feature set the dongle itself reported in frame 15.

Which makes the mechanism a contradiction in the dongle rather than in this
component: it **set a 4.2 feature bit and then refused the 4.2 command that
bit invites**, answering `Unknown HCI Command` -- status and nothing else, a
parameter length of 4 where the parser wants 5 or 8. That is a BCM20702A1
running its ROM with no `.hcd` patch loaded, and Bluedroid is written against
Espressif's own controller, which never does this. Deduced from their source
and fifteen measured frames; which of the two it is, is one line away.

**And the diagnostic lost the evidence a SECOND time, by a different
mechanism.** The sixteenth line arrived as `[I][portall_b` and stopped. The
boot dump says why -- `Task Log Buffer Size: 768 bytes` -- ESPHome buffers a
log written from any task but its own loop and the LOOP drains it, so a line
written from the reader task sits in that ring until the main loop next runs.
The abort takes whatever is still in it.

So the reader pauses `REPORT_SETTLE_MS` after each reported line, while the
reporter is still talking: the line reaches the wire before the frame reaches
Bluedroid. A second and a half spread over a startup, once, on a firmware
that is a probe -- against never being able to name the frame that crashes.
Each HCI command has an eight-second timeout, so it is nothing to the host.

**Twice now this diagnostic has been one line short of its own purpose**, for
two unrelated reasons: a cap set by guessing where the fault was, and a log
buffer nobody had thought about. The lesson is not either mechanism. It is
that a diagnostic built to survive a crash has to be checked against the
crash, and both times it was checked against a working run.

**The user has a Bluetooth 5.0 dongle**, which is the other half of the
experiment and costs a swap: *"la clef bluetooth et 4.0 et le tplink 5.0"*.
A 5.x controller answers the 4.2 reads honestly and the contradiction above
cannot arise. What it brings instead is the Realtek firmware upload this file
already records -- 30 210 bytes before it is a working controller -- so a ROM
mode run may report a reduced feature set, which is its own useful reading.

### It is 0x204A, on BOTH dongles, and the fault is ours after all

The user ran both. The Broadcom 4.0 and the TP-Link 5.x agree exactly where
it matters:

    BCM20702A1 (4.0)   16: event 6 bytes, complete for opcode 204a, 4 parameters
    RTL8761BU  (5.x)   19: event 6 bytes, complete for opcode 204a, 4 parameters

Two controllers separated by eight years of the specification, one made by
Broadcom and one by Realtek, refusing the same command in the same way. That
is not a dongle being old. **`0x204A` is `LE Read Periodic Advertiser List
Size`, a Bluetooth 5.0 command**, and four parameters is `Unknown HCI
Command` -- a status and nothing else -- while
`parse_ble_read_periodic_adv_list_size_response` asserts on anything shorter
than five.

And the previous round's theory was wrong in an instructive way. It said a
gate had passed because the dongle set a 4.2 feature bit. It had not: there
is **no gate at all**. From `device/controller.c`:

```c
#if (BLE_50_FEATURE_SUPPORT == TRUE && BLE_42_FEATURE_SUPPORT == FALSE)
#if (BLE_50_EXTEND_SYNC_EN == TRUE)
        response = AWAIT_COMMAND(...read_periodic_adv_list_size());
```

Two `#if`s and no `if`. The command is compiled in and sent whatever the
controller says it can do -- which Espressif may write, because their own
controller is built alongside the host and always supports it. Against
somebody else's controller it is an unconditional 5.0 demand, and the whole
class of "this controller does not support that" was never considered,
because with Bluedroid it never arises.

The difference between the two logs says the same thing from the other side:
the 5.x dongle answered **0x202A** and **0x203A** -- resolving list size and
maximum advertising data length, both of which the 4.0 one never got asked --
and then died on the same 0x204A. The extra frames are the gates working
correctly on a newer controller; 0x204A is the one with no gate.

**The fix is one line, and it is ESPHome's own.**
`CONFIG_BT_BLE_42_FEATURES_SUPPORTED` on -- the guard wants `50 AND NOT 42`,
so turning 42 on compiles the whole block out, 0x203A with it. `50` off
beside it because IDF's documentation says only one of the two should be
enabled, which is why ESPHome writes them as a pair.

**And this file said not to use them.** The note above on
`include_builtin_idf_component` explained preferring it over
`request_bluetooth()` partly because that one "also writes BLE sdkconfig
defaults nobody here wants". Those defaults are these two lines. The
portability half of that reasoning was right -- `request_bluetooth()` does not
exist in 2026.6.5 -- and the judgement half was exactly backwards, at a cost
of three rounds and three trips to a board.

The shape is one this file already records under **a ceiling nobody asked
for**: a defensible-sounding argument about somebody else's defaults,
substituted for finding out what they do. The rule that comes out of it is
narrower and worth keeping: **when a component of ESPHome's sets a default in
the area you are working in, read what the default IS before deciding you do
not want it.**

What is NOT done, and would be the larger answer: this project wants
Bluetooth CLASSIC from a dongle -- the C6 already does BLE -- so
`CONFIG_BT_BLE_ENABLED` off would drop every LE command from startup and with
them this entire class of failure, eight frames of it. That is a bigger
change than the evidence demands and it has not been tried; one measured fix
per round.

### It runs. A Bluetooth Classic HOST on a chip with no Bluetooth radio

Both dongles, first try:

    33: event 14 bytes, complete for opcode 2018, 12 parameters
    Bluedroid is ENABLED on a USB dongle -- a Classic host on a chip with no radio
      Bluedroid reports address E8:48:B8:C8:40:00        (TP-Link, 33 frames)
      Bluedroid reports address 00:02:72:DC:33:59        (Broadcom, 26 frames)

**And the Broadcom's address is the one the probe read in the very first log
of this whole thread.** Different code path, different task, different buffer,
different day -- `esp_bt_dev_get_address()` comes out of Bluedroid, which has
never seen the dongle except through three function pointers. That line was
put there to be the proof and it is.

Four things in those logs are worth keeping.

**`0x2027` is the diagnosis confirming itself.** On the Broadcom:

    23: event 6 bytes, complete for opcode 2027, 4 parameters
    W BT_HCI: opcode=0x2027, status= 01: Illegal Command

Four parameters, `Illegal Command` -- *exactly* the shape that aborted the
firmware on 0x204A. Here it costs a **warning** and the startup carries on,
because this command's answer goes to `parse_generic_command_complete`, which
needs no parameters beyond the status. Same controller, same refusal, same
frame shape; the only difference is which parser Bluedroid hands it to. That
is the mechanism, demonstrated rather than argued.

**`0x2018` repeating after enable is the stack WORKING**, not merely started:
LE Rand, status and eight random bytes, which is Bluedroid's security manager
asking the controller for entropy. Frames 33, 37 and 38 are after the
`ENABLED` line.

**`0x0C14` came back in 254 bytes**, whole -- Read Local Name, seventeen
packets on a sixteen-byte endpoint. That is the longest frame this transport
carries and it is the one the length rule and the NAK rule were both written
for.

**The two dongles differ exactly where they should.** The TP-Link answered
0x202A, 0x2024 and 0x2023 -- resolving list size and the data-length pair --
which the Broadcom was never asked, because those ARE gated on feature bits
and a 4.0 controller does not set them. The gates work; it was the ungated
5.0 command that did not.

**One correction to this file.** It says the RTL8761BU "needs its firmware --
30 210 bytes before it is a working controller". True for full function, and
too strong as written: in ROM mode, with nothing uploaded, it answered every
one of Bluedroid's thirty-three startup commands and brought the host up.
What the firmware buys is what happens after that, which is untested.

**What this is.** `SOC_BT_SUPPORTED` is not defined for the esp32p4 -- the
chip has no Bluetooth of any kind. It is now running Espressif's dual-mode
Bluedroid host, with Classic and A2DP compiled in, over a USB dongle it drives
itself through a CherryUSB host stack that had its Bluetooth driver switched
off for ESP-IDF. Nine faults between the first enumeration and this line, every
one of them recorded above, and seven of them cost a trip to a board.

**What is NOT done**: it has not paired with anything, connected to anything
or carried a byte of ACL. The stack is up; nothing has used it yet.

Fixing it found the other half. That file has never passed `esphome config`:
its `api:` encryption key is an **empty string**, which is the same fault
CLAUDE.md already records for `ws-usb-screen.yaml` and the Guition pair -- a
fourth file, and the reason it was never caught is that nobody had run the
checker on it. It is `!secret api_encryption_key` now, like every other
example here, and it validates with `host_stack: bluedroid` set.
`yaml/GUITION_ PORTAL.yaml` has the same empty key and is left alone: it is
somebody's own configuration rather than an example this repository offers.

**The Realtek needs its firmware.** The TP-Link UB500 answers HCI Reset and
gives a real address from ROM -- its address matched the user's own Windows
screenshot exactly -- and that is the false success this file warned about:
Linux uploads **30 210 bytes** (`rtl8761bu_fw.bin` + `_config.bin`) before it
is a working controller. A loader is a vendor-command loop and a 30 KB blob,
identified and not written. The Broadcom needs none of it -- its `.hcd` is a
patch, and `btbcm_initialize` logs "Patch file not found" and `return 0`.

**And the upstream fix is four lines.** Putting those constants behind
`#ifndef`, the way the rest of `osal/idf/usb_config.h` already does for
everything else, would retire `cherryusb_patch` entirely. The file carries
**Espressif's own copyright** and lives in `cherry-embedded/CherryUSB`. Not
reported yet.

## One board can now be a panel AND a Bluetooth host

**`usb: false` on portall, and the reason is silicon rather than taste.** The
ESP32-P4 has two USB OTG peripherals and a board wires each of its sockets to
one of them; on the M5Stack Tab5 the USB-A HOST socket is the high-speed one,
which is exactly the peripheral portall puts in DEVICE mode. So the whole of
the Bluetooth work above ran on a firmware with `portall_bt:` and no
`portall:` at all -- a Classic host driven by a screen showing nothing, which
is of no use to anybody.

It leaves TinyUSB out of the build entirely: no vendor pipe, no HID digitizer,
no USB sound card, no sender drive, no device PHY. The picture, the touches
and the sound keep arriving over `port:`, which is the only way in left -- and
a board with neither is a **validation failure** rather than one that boots,
allocates every buffer and sits black for ever with a clean log.

**usb_descriptors.h carries two unrelated things and only one of them is
TinyUSB's.** The USB descriptors are; the udisp **frame header** is not, and
the network path parses it. So the component still registers and returns from
its CMakeLists before reaching for a library that is not in the build, rather
than the header being split. One definition of the wire format is worth more
than a tidier pair of files -- the same rule `udisp_send.py` and `ha_send.py`
live under.

**And it exposed a guard that had been wrong since the network path existed.**
The volume entity was `#if CFG_TUD_AUDIO` -- the USB sound card -- while the
volume it moves is applied in `on_audio_samples`, the one door PCM comes
through whichever way it arrived. A board with no USB would have lost its
volume control while still playing the page's sound. `#ifdef USE_SPEAKER` now,
which is where `set_audio_volume` actually lives. This file already records a
panel reporting exactly that symptom with a `platform: template` number in its
place; the wording was corrected then and the condition was not.

**`tools/checkguards.py` is what says the C++ is right, because nothing here
can compile it.** It walks the preprocessor guard stack and asks, of every
line naming a TinyUSB symbol, whether anything above it goes false without a
device. Counting `#if` against `#endif` does not find the fault that matters:
a `tud_*` call outside every guard balances perfectly, compiles in every
configuration anybody here can compile, and fails at the **link**, on somebody
else's board. Reproduced against a copy with the guards stripped -- 29
problems, `tud_vendor_rx_cb` and `tud_mount_cb` among them -- before it was
believed.

It was wrong itself first, and the fix is worth recording: it read `#else` as
unprotected whatever it followed, which is right after `#if CONFIG_...` and
exactly backwards after `#if !CONFIG_...`, the form `on_vendor_rx` happened to
use. The answer was **not** to teach the check about negation. It was to stop
writing the negated form, which no reader could follow either.

## A gamepad and a remote are the same feature

Both are Classic HID, both arrive as input reports over ACL, and they differ
only in which buttons somebody presses. So `hid: true` is the answer to a
panel that wants a gamepad AND to one that wants arrow keys for YouTube's
television interface -- a thread this file spends several pages on -- and
neither waits for the other. `components/portall_bt/hid.cpp`.

**The memory has three parts and only one of them was missing.** Asked for as
*"il faut que le Bluetooth quant il accroche un Bluetooth hote puisse le
memoriser"*.

- **Bluedroid keeps the link keys** -- the cryptography of a pairing -- in NVS
  by itself. Nothing here writes them and they survive a restart untouched.
- **What it does not keep is which address plays which part.**
  `esp_bt_gap_get_bond_device_list` returns addresses and nothing else, so a
  bonded gamepad and a bonded speaker are indistinguishable. One small record
  in ESPHome's preferences answers that. Losing it costs a reconnection;
  losing Bluedroid's costs the pairing.
- **And nothing reconnects on its own.** That is the part that had to be
  written: read the record at startup, call `esp_bt_hid_host_connect`, and
  again on a 2 s -> 60 s backoff while the device is away.

**That third part is also what keeps the picture, which is the nicest thing
about this design.** A connection by ADDRESS runs no inquiry, and an inquiry
is the most disruptive thing Bluetooth does -- measured twice on this board,
the Wi-Fi went down for exactly as long as one ran, on two different channels.
So remembering the device and not killing the picture are the same piece of
work. `pair()` is an action somebody invokes once; nothing else here ever
scans. The panel stays **CONNECTABLE** so a paired gamepad can page it back
when somebody presses its button, and is discoverable only while pairing runs.

**`CONFIG_BT_HID_REMOVE_DEVICE_BONDING_ENABLED` defaults to y and is turned
off.** It throws the pairing away when a device asks for a virtual cable
unplug -- what the HID specification asks for, and from the sofa a gamepad
that has silently forgotten the panel and has to be paired again by somebody
who did nothing wrong. A panel is not a PC being handed between desks.

**The report queue drops the OLDEST, which is the opposite of the queue beside
it.** An enumeration's FIRST event is what somebody is waiting to read about;
a button's LAST event is the release, and a release that never arrives leaves
a key held down for ever. portall's touch queue learned that at a cost of
twenty seconds of apparent latency, and a finger and a thumb are the same
problem.

**What is NOT done: this carries reports, it does not interpret them.** A HID
report descriptor differs per device, so a mapping written with no device to
try it against would be a guess dressed as a recipe -- which this file already
records costing a round trip over the Chromecast user agent. `on_hid_report`
hands the bytes to the YAML and `show_reports:` prints them. The mapping is
written afterwards, with a real device's own log in hand.

### `cv.enum` returns the KEY, and every `if` written against it is taken

Found by writing a validator against the same wrong assumption and **testing
it against the configuration it was meant to refuse**. It did not refuse it.

`cv.enum({"none": False, "bluedroid": True})` returns the key as a string
carrying the mapped value on `.enum_value`. So `config["host_stack"]` is
`"none"`, which is perfectly truthy, and `if config[CONF_HOST_STACK]:` has
always been taken.

Codegen was never affected, and that is exactly what hid it:
`cpp_generator.safe_exp` unwraps an `EnumValue` before emitting it, so
`set_host_stack(...)` emitted `false` correctly while the sdkconfig block
beside it ran anyway. **Every firmware built with `host_stack: none`, and
every one that never mentioned it at all, has been compiling the whole of
Bluedroid -- `CONFIG_BT_ENABLED`, Classic, A2DP, 1430 objects of it -- into a
binary whose own C++ then refused to use it.** Nothing failed. It cost flash
and build time and said nothing.

`_wants(config, key)` is the one place that asks now. The general rule: an
ESPHome enum option is a STRING, whatever its mapping says, and the only
honest way to read it in Python is `.enum_value`.

Only `portall_bt` was affected. The three `_USB_SPEEDS` maps elsewhere index
the dictionary by the key -- `_USB_SPEEDS[config[CONF_USB_SPEED]]` -- which
works precisely because the value IS the key string.

### Reading the headers is what stopped three wrong lines

Each of these would have reached a user as a build error on their own board,
which this file already calls the worst place to find one:

- **`ESP_HIDH_DATA_IND_EVT` carries no report id.** It has status, handle,
  proto_mode, len and data. Every HID example in circulation prints an id, and
  a stand-in header with an invented field would have let code that cannot
  build pass the only C++ check this repository has. Where a device uses
  report ids at all the first byte of the data IS the id; where it does not,
  there is none to be had, so `on_hid_report` hands over exactly what arrived.
- **`BT_HID_ENABLED` is a menuconfig with `BT_HID_HOST_ENABLED` under it**, so
  both have to be set; one alone is a silent nothing.
- **The stand-ins are copied field for field from v5.5.5**, which is what the
  user actually builds with. The first version of this said 5.5.4 "which is
  what ESPHome pins" -- true of the 2026.6.5 venv that happened to be
  installed here for validation, and not of their build at all. They corrected
  it: ESPHome **2026.8.2 and dev both pin ESP-IDF 5.5.5**
  (`ESP_IDF_FRAMEWORK_VERSION_LOOKUP`), checked against both tags afterwards.
  The validation venv is 2026.8.2 now, which needs Python 3.12 and a git
  install -- PyPI here only carries up to 2026.6.5.

  Nothing in the code moved: `esp_a2dp_api.h` and `esp_hidh_api.h` are
  byte-identical between the two tags and `esp_gap_bt_api.h` differs only in
  comments. But the version this repository validates against has to be the
  version somebody flashes, or the check is measuring the wrong thing -- which
  is the same lesson as the helper rename that reached a user as a compile
  error.

`tools/checkbt.py` gained a third configuration, bluedroid + hid, for the
reason the second exists: the newest code sits behind an `#ifdef` no pass had
opened. It earned it on the first run -- the reconnection clock was behind the
Bluedroid guard while the public callbacks that touch it are not, which
compiled two ways out of three.

### Two A2DP source APIs, and a header is not one file

This was written here as a finding and it was **wrong**, so the correction
comes first: *"A2DP in ESP-IDF 5.5.4 is NOT the API every example uses -- the
pull callback that handed raw PCM is gone from the public header."*

It is not gone. `esp_a2dp_api.h` line 12 includes **`esp_a2dp_legacy_api.h`**,
unconditionally, and that file declares:

    typedef int32_t (* esp_a2d_source_data_cb_t)(uint8_t *buf, int32_t len);
    esp_err_t esp_a2d_source_register_data_callback(esp_a2d_source_data_cb_t);

The whole of the mistake was fetching one file with curl, grepping it for
`source_data`, finding zero, and treating that as proof. Every contradiction
that followed was real evidence that the conclusion was wrong and was
explained away instead: Espressif's own example calls the function, their
implementation file defines it, and `btc_av.h` names the type -- three things
that cannot all be true of a symbol that does not exist. **A header is not one
file. Follow what it includes before concluding a symbol is gone.**

What settled it was getting the actual tree rather than guessing filenames:
`git clone --depth 1 --filter=blob:none --no-checkout -b v5.5.5`, then
`git ls-tree` the API directory. That is cheap -- no blobs -- and it would
have ended the question in one step instead of a dozen 404s.

**So there are two APIs and a Kconfig option chooses between them:**

| | `BT_A2DP_USE_EXTERNAL_CODEC` | who encodes |
|---|---|---|
| `esp_a2d_source_register_data_callback` | **n, the default** | Bluedroid, with its own SBC |
| `esp_a2d_source_audio_data_send` | y | the application |

Espressif's help text for that option says "The internal codec in A2DP will be
removed in the future, it is recommend to use external codec for new design."
So the second one is where this is going, and the first is what a default
build supports today.

**The first is what `components/portall_bt/a2dp.cpp` uses, and the migration
is blocked on something dull rather than on effort.** Taking the external
codec means an SBC encoder in this component, and neither candidate can be
read from here: Bluedroid's own is in a **PRIVATE** include directory
(`bluedroid_host_priv_include_dirs`, so `src` cannot reach `sbc_encoder.h`
even though the object is linked in), and `espressif/esp_audio_codec` ships
its per-codec headers only in the registry package -- `include/encoder/`
in its git repository has `esp_audio_enc.h` and nothing per codec, and
components.espressif.com is blocked from here. Writing
`esp_sbc_enc_config_t` from a README would be the invented-field fault this
file records one section earlier, except a wrong struct layout is silent
memory corruption rather than a compile error.

**The format is not a choice and Espressif say so in a comment.** From
`btc_a2dp_source.c`: *"for now hardcode 44.1 khz 16 bit stereo PCM format"*.
So the callback hands over 44100 Hz, signed 16-bit little-endian, TWO
channels interleaved. portall's own page audio is 48 kHz MONO, so feeding
this from there needs a resample and a channel duplication -- both exist as
ESPHome speaker components, and that is the next step rather than this one.

**And the deprecated path is the cheaper one in a way that matters: the
callback is the clock.** Bluedroid asks for exactly the bytes it is about to
encode, when it is about to encode them, from its own task. Nothing in this
component has to know what time it is. The external-codec API would make this
component responsible for real time, and a stutter from getting that wrong is
the kind nobody can diagnose from a panel. `len == -1` is a FLUSH rather than
a length -- their documentation says the return value is ignored for it, and
treating it as a size would read two gigabytes.

## The panel sends its sound to a Bluetooth speaker

`audio: true` on `portall_bt:`, and it is the half of this work the ESP32-C6
can never do: A2DP is Bluetooth Classic.

**The device it was built against is the one the user owns.** Asked for a
gamepad to test the HID side, the answer was *"je n'en dispose pas, le seul
bluetooth que je dispose un bluetooth ugreen pour voiture"* -- a car receiver,
which is an A2DP **sink**, which is exactly what an A2DP source needs to talk
to. So the order inverted for a hardware reason rather than an engineering
one: HID is built and has nothing to try it with, and the speaker path can be
proved this evening.

**One pair action, two kinds of device, and the class of device sorts them.**
`ESP_BT_COD_MAJOR_DEV_AV` is a speaker, a headset or a car receiver;
`ESP_BT_COD_MAJOR_DEV_PERIPHERAL` is a gamepad, a keyboard, a mouse or a
remote. Taking the first of THOSE rather than the first of anything is what
stops a pairing run walking off to the neighbour's telephone -- and it means
the same button serves both profiles, with `Remembered` already carrying a
slot for each.

**Both profiles ride one reconnection clock**, because both are the same act:
a connection by address, which runs no inquiry. That was the point of the
whole design and it did not need a second copy.

**`test_tone:` exists so the chain can be heard before anything real is
plumbed into it**, which is the choice `tools/playsound.py` already made for
the panel's own speaker. A sine in frames rather than bytes, with the phase
carried across calls -- a sine restarted every callback is a click every
callback.

**A short read is a click, so a shortfall is filled with silence and
counted.** A2DP is a stream with a clock at the other end: the sink decodes at
a fixed rate whatever arrives, so returning fewer bytes than asked is a gap
rather than a pause. `pcm_starved_` counts it and the log says so when the
stream suspends, because a starved stream that merely sounds wrong is the
failure nobody can diagnose.

**`CONFIG_BT_A2DP_ENABLE` is now asked for rather than always on.** It was
being set for every build with a host stack, so a panel that only wanted a
gamepad carried an audio stack and an AVRCP with it. Same shape as the
`cv.enum` fault above: a cost nobody chose and nothing reported.

**What is NOT done**: nothing has paired with the UGREEN yet, no C++ here has
been compiled by a real toolchain, and the sound it can send is a test tone.
Feeding it from portall -- 48 kHz mono into 44.1 kHz stereo, through
ESPHome's resampler -- is the next step and is not written.

## It plays. A car receiver, paired and streaming, on the first real run

The whole chain, from a panel's own log:

    scanning for about 10 seconds -- put the device in pairing mode now
      heard 46:E8:1C:8A:88:DD  class 240404
      that is a speaker -- stopping the scan and pairing with it
    scan finished; the Wi-Fi should come back now
    paired with 46:E8:1C:8A:88:DD "UGREEN-90748" -- Bluedroid has the link key in NVS now
    speaker 46:E8:1C:8A:88:DD is connected
    remembering 46:E8:1C:8A:88:DD as this panel's speaker
    audio stream started

**And that address is the one the very first inquiry in this whole thread
heard.** Months of project time earlier, the probe printed `found
46:E8:1C:8A:88:DD audio/video -64 dBm` and nobody knew what it was. It is the
car receiver, and it is now the panel's speaker.

`class 240404` is the sort working: `(0x240404 & 0x1f00) >> 8` is 4,
`ESP_BT_COD_MAJOR_DEV_AV`, so one pair action found a speaker among whatever
else the house was broadcasting and connected the right profile to it.

**`event 257 bytes, code 2f, 255 parameters` is the length rule at its
limit** -- an extended inquiry result, the largest frame HCI defines, carried
whole over a sixteen-byte endpoint. That is seventeen packets reassembled by
the rule that replaced counting short packets.

Two warnings in that log are the diagnosis confirming itself again:
`opcode=0xfc82, status= 01: Illegal Command` is a vendor command this dongle
does not have, answered with a status and nothing else -- the same shape as
0x2027 and 0x204A, costing a warning because its answer goes to a parser that
needs no parameters. And `btm_sec_l2cap_access_req: (initiator) remote
features unknown` is Bluedroid noting it has not read the remote's features
yet at the moment it opens the channel; the pairing completed immediately
after it.

### The device asked for its buttons and was refused

    W BT_L2CAP: L2CAP - rcvd conn req for unknown PSM: 23

**23 is 0x17, which is AVCTP, which is the channel AVRCP runs over.** So that
line is the car kit asking to send play, pause, next and a volume level, and a
panel with no AVRCP registered saying no. On a car receiver those are the
steering wheel and the knob; on headphones it is the button on the earcup.

The log asked for the feature, which is the best kind of request. `audio: true`
now registers the AVRCP **TARGET**, and which role is which is the one thing
worth getting right: the target is the end that RECEIVES commands, because the
target is the player. The panel plays; the thing with buttons on it is the
controller. Registering the other way round would have put this panel in the
role of sending play/pause to a car radio that has no player.

**AVRC has to be initialised BEFORE A2DP, and that is in their header rather
than in a guess.** `esp_a2d_source_init`'s own documentation: *"If you want to
use AVRC together, you should initiate AVRC first."* The same shape as
attaching the HCI driver before `esp_bluedroid_init()` -- an ordering written
down in a header by the people who wrote the stack, in a component that has
already been caught once by not reading one.

The supported command set is **copied from the ALLOWED set** rather than
listed here, which is what Espressif's own examples do and is the only version
that cannot go stale: the stack says what it can carry and this says yes to
all of it. A hand-written list of key codes would quietly stop supporting
whatever the specification gained after it was typed.

`on_media_key` and `on_media_volume` are the triggers. The key codes ARE a
fixed enumeration in `esp_avrc_api.h` -- unlike a HID report descriptor, which
differs per device -- so naming them in the log is reading rather than
guessing. The volume crosses as a **fraction**, because AVRCP carries 0..127
and no YAML should have to know that. And the key state is **0 for PRESSED**,
which is the opposite of every other input API in this component and is
exactly why the stand-in header is copied field for field rather than typed.

### An ACL packet is not an event, and for one release the log said it was

The same log, six times:

    ACL 20 bytes, code 0c, 32 parameters

Three numbers and not one of them true. An ACL packet begins with a 12-bit
connection handle and two 2-bit flags, little-endian, and its LENGTH is the
two bytes after that -- so `0c` is the low half of the handle, `32` is 0x20,
which is the other half with the packet-boundary flag folded in, and there is
no code and no parameter count anywhere in it. `say_frame` was printing the
event layout over ACL bytes.

It survived because **nothing had ever carried ACL**. Every frame this
component had seen until a speaker connected was an event, so the fallback
branch had never been reached by anything it was wrong about. The first real
connection printed six of them.

    ACL 20 bytes, handle 00c, 16 of payload

The test is that exact frame, captured off the reporter's own stdout, and it
asserts both halves: that the new numbers are right AND that `code 0c` and
`32 parameters` are gone -- a half-fix would pass otherwise. Reproduced
against a copy with the branch removed, where it prints the panel's original
line and fails.

**What is still NOT done**: the sound reaching the car has not been confirmed
by ear, the panel's own audio is not plumbed into it (48 kHz mono into
44.1 kHz stereo, through ESPHome's resampler, is the next step), and nothing
has paired over HID because there is no device here to pair with.


## The panel's own sound now reaches that speaker

**Asked as *"il ne faut oublier que le yaml il ya pas d'audio regarde l'audio
du tab5"*, pointing at `yaml/tab5-portall-screen.yaml`.** It is the right
objection: `PortallBT::feed_audio()` was public and NOTHING called it, so the
only thing a paired car receiver could play was the test tone this component
generates for itself. `speaker: - platform: portall_bt` is the door a YAML
pushes real sound through, and `yaml/tab5-portall-bluetooth.yaml` is the whole
chain on one board -- which `usb: false` is what made possible at all.

**The two conversions are split, and where each one goes was read rather than
assumed.** portall produces 48000 Hz 16-bit MONO; A2DP takes 44100 Hz 16-bit
STEREO. Both numbers belong to somebody else.

| | who does it | why there |
|---|---|---|
| 48000 -> 44100 | ESPHome's `resampler` speaker, in the YAML | real signal processing, and ESPHome already has it |
| mono -> stereo | this platform's `play()` | each sample written twice: exact, no filter, no state |

And the channel half has to be here, because ESPHome's own parts will not do
it. Read in 2026.8.2 rather than remembered:

- `AudioResampler::start()` returns **`ESP_ERR_NOT_SUPPORTED`** the moment
  `input_stream_info_.get_channels() != output_stream_info.get_channels()`. A
  resampler's output channel count is always its input's --
  `target_stream_info_` is built with `this->audio_stream_info_.get_channels()`
  -- so the `num_channels:` in its config is validation only.
- The **mixer** does convert channels (`pcm_convert::copy_frames` takes an
  input and an output channel count), and it wants every source already at the
  output's sample rate, restarting the output speaker when one is not.

So the alternative was resampler + mixer, two blocks in a household's YAML for
a duplication that is three lines of arithmetic. Same instinct as every other
time the simpler named thing won here.

**`bits_per_sample`, `num_channels` and `sample_rate` carry DEFAULTS on this
platform, and that is load-bearing rather than tidy.** A resampler pointed at
this speaker **inherits** all three from it --
`esphome.core.entity_helpers.inherit_property_from(CONF_NUM_CHANNELS,
CONF_OUTPUT_SPEAKER)`, which reads the OUTPUT speaker's own config. Leaving
them unset does not mean "anything": the resampler's `to_code` then does
`config[CONF_SAMPLE_RATE]` and raises a **KeyError in somebody else's file**,
which nobody would connect to this one. Verified on the resolved config rather
than by reading: the resampler block comes out `num_channels: 1,
sample_rate: 44100`, and asking this platform for 48000 is refused by its own
schema (`value must be at most 44100`).

**Sound with nothing paired is ACCEPTED and dropped, never refused, and the
reason is a diagnosis rather than politeness.** portall reads a speaker that
never takes a byte as a stream being *refused* and prints its long explanation
about resamplers and mixers -- which here would be a confident account of the
wrong fault. There is no speaker; that is not a stream that will not fit. One
line says so, once.

**The odd byte is carried.** Nothing in this project splits a 16-bit sample
today -- portall flushes 1920-byte blocks and the resampler emits whole frames
-- but half a sample kept as a whole one would swap the two channels for the
rest of the stream, and from a car that is a fault nobody can diagnose. Three
lines, and the test proves it: break the carry and "a sample split across two
calls is not torn" fails.

**The mistake a household will really make is the missing resampler**, so the
log names it. Pointed straight at this platform, the speaker is handed 48000
and the sink decodes at 44100 whatever arrives: everything fast and high,
which reads as the panel being broken. `play()` says once what it got, what
A2DP carries and what block to add. The test captures that line off stdout and
asserts all three numbers are in it.

`tools/bttest/speaker.cpp` links the SHIPPED `play()` and reads the bytes back
through the SHIPPED `fill_pcm()` -- the path Bluedroid's encoder really uses.
Seven cases, and both faults were reproduced against it before it was
believed: passing mono through unduplicated fails two of them, throwing the
odd byte away fails one.

### And it found two checks that had never opened a platform file

Both are the silent no-op this file keeps recording, and both were in the
tools written to catch exactly that.

- **`tools/checkbt.py` globbed `*.cpp`, not `**/*.cpp`.** A platform lives in
  its own subdirectory, the way every ESPHome platform does, so the newest
  file in the component would have compiled nowhere and the tool would have
  printed `ok` about it by never seeing it. It also gained a sixth
  configuration, `-DUSE_SPEAKER`, for the reason the second and third exist:
  without it the file compiles to an empty translation unit.
- **`tools/importcheck.py` could not import ANY platform file in this
  repository.** `from .. import ...` executed as a loose script is an
  ImportError before the first line is looked at, so `components/portall/
  number/__init__.py` had been coming back `?` since it was written -- and
  the one check that catches a NameError at import time had never read a
  platform. It now executes the parent component FOR REAL as a package, which
  catches a second thing as well: a platform importing a name its component
  does not define. Both reproduced against a broken copy first.

`tools/bttest/linkstubs.h` is one copy of every symbol a test on a workstation
cannot have, because two copies drift the moment somebody adds a profile --
and the whole point of linking against the real sources is that the LINKER is
asked whether a declaration still matches its definition.

**What is NOT done.** No C++ here has been compiled by a real toolchain; the
stand-in `speaker.h` and `audio.h` under `tools/btstub/` are copied field for
field from 2026.8.2 and say what these functions are called, not that an
ESP-IDF build will accept them. Nothing has been **heard** -- the chain is
proved to the byte on a workstation and to `esphome config` on the YAML, and
whether a car receiver plays it is one run away. Lip sync is not attempted and
neither is a second speaker: the page's sound goes to Bluetooth OR to the
panel's own loudspeaker, whichever `speaker_id:` names, and both at once would
be a mixer nobody has asked for.


## A build directory kept TinyUSB, and the CMake was asking the wrong question

**Reported as a compile error from a board, on a firmware with no `portall:`
in it at all:**

    tusb_option.h (in "espressif__tinyusb" component) includes tusb_config.h,
    provided by usb_display_tusb component(s). However, usb_display_tusb
    component(s) is not in the requirements list of "espressif__tinyusb".

Two components the reader never configured, named in an error about a third.

**Nothing in a clean build of that firmware asks for either one.** `portall`'s
`to_code` is what adds `usb_display_tusb` and `espressif/tinyusb`, and it does
not run without a `portall:` block; CherryUSB's own manifest was read rather
than assumed and depends on neither. So both arrived from the BUILD DIRECTORY:
`managed_components/` is discovered from disk, and a dependency that stops
being requested does not reliably stop being on disk.

**And that is only how they got there. What made it fail is ours.**
`usb_display_tusb/CMakeLists.txt` decided whether to hand TinyUSB its
`tusb_config.h` by testing **`CONFIG_USB_DISPLAY_DEVICE`** -- which is what
portall's `usb:` option writes, our own intention rather than the build's
state. The two agree in every configuration this repository generates and
disagree the moment a build directory carries TinyUSB anyway: the option says
no, the wiring is skipped, and TinyUSB is left in the build without the one
header it cannot start without.

The fix is the rule this file already records three times in other costumes --
**ask the world, not the step**:

```cmake
idf_build_get_property(build_components BUILD_COMPONENTS)
if(NOT "espressif__tinyusb" IN_LIST build_components)
    return()
endif()
```

`BUILD_COMPONENTS` is the build's own list and it is complete before any
component's CMakeLists runs: `__build_expand_requirements` fills it during
requirement expansion, and `add_subdirectory` reaches components afterwards
(`tools/cmake/build.cmake` in v5.5.5, read rather than remembered). It also
fixes the mirror-image case the old code turned into a hard CMake error --
the option on with no TinyUSB to reach for, where
`idf_component_get_property` fails outright rather than returning empty.

**Wiring it up makes the build succeed and does not make it right**, so the
one state that cannot come out of this repository's codegen -- TinyUSB present
with `usb:` off -- now says so at configure time and names the remedy, because
a build directory is not something a reader can see. On a Tab5 the leftover is
worse than dead flash: TinyUSB claims the high-speed peripheral, which is the
one the USB-A host socket needs, and freeing it is the whole of what `usb:
false` is for.

**`tools/checkcmake.py` is the first thing in this repository that has ever
executed a line of CMake.** `esphome config` never reaches it, `checkguards`
reads C++ and `checkbt` compiles C++; the one file that reached a user as a
build error was the one nothing looked at. It processes the real CMakeLists
with `add_subdirectory` against stand-in IDF functions that RECORD what they
were handed, so a branch is proved by what it did rather than by grepping for
an `if`. Four states, and three of the seven cases run the OLD file and
require it to fail: the reported error reproduces, and so does the hard error
on the reverse case.

What it cannot check is that ESP-IDF's real build agrees -- the stubs say what
these functions are called and what this file does with them, nothing more.

**The lesson is not about CMake.** It is that `usb: false` was tested against
the configurations this repository generates, and a user's build directory is
not one of those. Anything keyed on our own option rather than on the build's
state has the same hole.

## A connected speaker answers no inquiry, so the second pairing never works

**Reported as *"je n'arrive plus a me connecter au bluetooth meme si je vais
reset Forget Bluetooth devices"*, with a log that ends dead:**

    06:34:32  found 46:E8:1C:8A:88:DD  audio/video   -45 dBm
    06:34:40  inquiry finished, status 00, 2 devices heard
    06:34:46  scanning for about 10 seconds -- put the device in pairing mode now
    06:34:46    the Wi-Fi will drop while this runs...
    (nothing)

The boot probe heard that speaker at **-45 dBm six seconds earlier**, which
rules out the radio, the distance and the dongle in one line. So the question
is only what the pairing run did, and the log answers with silence.

**An inquiry cannot find a device that is already connected to this panel, and
that is the fault.** The first pairing works because nothing is remembered and
nothing is connected. Afterwards this component reconnects BY ADDRESS on a
2 s -> 60 s clock, for ever -- which is the whole design and the reason it does
not kill the Wi-Fi. So by the time somebody presses Pair a second time the
speaker is connected to us, is therefore answering nobody's inquiry, and the
scan hears nothing.

**`forget()` made it permanent rather than fixing it.** It removed the bond
from Bluedroid's NVS and cleared this component's own record -- and never hung
up. A live ACL whose key has just been deleted is the worst of both: the device
stays connected, so the next scan still cannot see it, and the key that would
have let it reconnect cleanly is gone. Hanging up is now the FIRST thing both
`pair()` and `forget()` do, and `open_sink_` / `open_hid_` exist to make it
possible: what is connected right now is a different question from what the
panel remembers, and only the first can be disconnected.

**And nothing checked what the stack answered, in the one button a household
presses.** `esp_bt_gap_start_discovery` and `esp_bt_gap_set_scan_mode` both
return `esp_err_t` and both were ignored, so a refused scan printed three
encouraging lines and then nothing at all -- not even `scan finished`, because
that line comes from an event the stack only sends if the scan began. From
outside, a refused scan and a scan that heard nothing are the same silence.
That is this file's most-recorded fault shape wearing its worst costume.

`scan finished` now carries a COUNT, and zero says what zero means.

**The other half of the silence is that a scan cannot report itself.** An
inquiry sweeps the whole 2.4 GHz band and takes this panel's own Wi-Fi down for
exactly as long as it runs -- measured twice on this board, on two channels,
and recorded above. So every line a pairing produces is written into a link
that is not there. `pair_report_tick_()` says the outcome again **three seconds
after the scan ends**, from `loop()`, when there is something to carry it: a
device connected, nothing heard, or heard-but-not-taken. Three different next
steps that used to be one silence.

`tools/bttest/pairing.cpp` drives the SHIPPED `pair()` and `forget()` with
recording stubs and asserts the ORDER -- disconnect before discovery,
disconnect before the bond is removed -- because both faults are about
sequence rather than about any single call. Seven cases; **four of them fail
against the old code**, including the reported silence.

Two smaller things the checks caught while this was written, both the shapes
this file already records:

- `char text[18]` declared above the profile guards is unused when neither
  profile is compiled in -- found by the `host_stack: bluedroid` pass, which
  exists for exactly that.
- `say_pairing_later()` was written inline in the header and could not see
  `now_ms_()`, which is a free `static` in `hid.cpp` rather than a member. The
  first comment explaining it claimed a rule about member-function bodies that
  is simply untrue; a member declared later WOULD have been visible. Corrected
  in place, because a comment stating a false rule is worse than no comment.

**What is NOT settled.** Which of the two halves the panel actually hit is one
run away: if the scan was refused, the new line names the error; if it ran and
heard nothing, the count says so; and either way the three-second report
survives the Wi-Fi drop. No C++ here has been compiled by a real toolchain.

## The "shuuut" was a ring counting in bytes, and the volume reached nothing

**Reported as *"je recois un son qui un bruit shuuut et audio et recommence ...
impossible de controler le volume et le mixer ne fonctionne pas tu a inventer
systeme audio resempler!!!"*, pointing at their own working
`yaml/GUITION_ PORTAL.yaml`.** Three complaints, three separate faults, and
the YAML one was the fairest of them.

**A FRAME IS FOUR BYTES AND AN INDEX OFF THAT GRID IS WHITE NOISE.** The ring
in `a2dp.cpp` dropped and copied BYTES. The moment it overran, the tail moved
by whatever odd number of bytes had overflowed, and from then on every sample
handed to the encoder was the high byte of one and the low byte of the next --
full-scale hiss. That is the "shuuut"; the audio in between is a later drop
happening to realign it by chance, and the cycle is the ring overrunning
again.

**The test tone could never have shown it**, which is why it shipped. The tone
is generated in `fill_pcm` only when the ring is EMPTY, so the one path
anybody had listened to does not use the ring at all and cannot overrun it.
Everything was proved on the path that could not fail.

Everything is whole frames now, on the way in and on the way out. And the
policy moved to the side that may apply it: the producer was advancing
`g_pcm_tail`, **the consumer's own index**, to drop the oldest -- racing the
task that was reading it. The consumer holds the occupancy down to
`PCM_HIGH_WATER` by skipping whole frames, which is race-free; the producer
refuses only what will not fit. The ring is 0.2 s with the mark at 0.1, and
that mark IS the added latency, because the consumer is a real-time clock and
occupancy parks there.

**And nothing at the end of the volume chain obeyed it.** `portall.set_volume`
does not scale anything -- `on_audio_samples` only GATES on the value, so zero
is silence -- and hands the number to `speaker_->set_volume()`. Every block
between passes it on faithfully: a mixer source gives it to the mixer's output
speaker, a resampler to its own. It arrived at `PortallBTSpeaker` and met
`speaker::Speaker::set_volume()`, which stores the value and applies it to an
`audio_dac_`. **A Bluetooth speaker has no codec on this board**, so the chain
ended at nothing. The base class invites the fix in its own comment --
"Individual speaker components can override and implement in software if an
audio dac isn't available" -- so `set_volume`/`set_mute_state` now scale the
samples in Q15 as they are copied. Mute is its own flag rather than a gain of
zero, so unmuting returns to the volume that was set.

That also explains why the same slider works on the panel's own loudspeaker in
the same YAML: an `i2s_audio` speaker ends the chain at an ES8311's register.

**The mixer complaint was right and the file simply had none.**
`yaml/tab5-portall-bluetooth.yaml` went `portall -> resampler -> bt_speaker`,
which is not how anybody writes ESPHome audio and is not what their own file
does. It is `mixer -> resampler -> bt_speaker` now, with the page, the
announcement pipeline and the media pipeline as three sources -- the shape of
`yaml/GUITION_ PORTAL.yaml` with the Bluetooth speaker where the I2S one was.

Three things in that rewrite were read rather than assumed:

- **`timeout: never` on every source, and it is not decoration.** A mixer
  source's `timeout:` **defaults to 500 ms**: half a second with nothing to
  play and it stops, and the next sound restarts it through `finish()` on the
  output and a fresh A2DP stream. Their working file carries it on all three
  sources and that is why.
- **Every source must agree on the sample rate.** `MixerSpeaker::start()`
  takes its rate from the first source to start and then returns
  `ESP_ERR_INVALID_ARG` for any later source at a different one -- surfacing
  as "Incompatible audio streams" on the source that lost, which is an
  announcement that is simply never heard. The task's restart-at-the-source's-
  rate path applies only when exactly ONE source has data. So both media
  pipelines are 48000, matching portall.
- **A resampler's `bits_per_sample` defaults to the word `passthrough`**, and
  a mixer above it INHERITS that field from its output speaker -- then refuses
  it with "Expected integer, but cannot parse passthrough as an integer",
  naming a component nobody wrote wrong. Spell it out on the resampler.

**The resampler itself stays, and "tu a inventer" is the one part that is not
so.** A mixer adapts its output's rate; A2DP cannot, because 44100 is a
hardcoded constant in `btc_a2dp_source.c`. Their Guition file needs no
resampler precisely because its output is I2S, which plays at whatever it is
given. The lesson is narrower than the complaint and worth keeping: **the
pattern was right and the placement was wrong** -- one block between the mixer
and the speaker, where the single rate change belongs, not a lone block
standing in for the mixer.

**Two corrections to this file.**

- It says a `platform: template` number "stores a value and writes to the log,
  and is connected to nothing". That is true of a bare one and false of
  theirs, which carries `on_value: portall.set_volume` with the `x / 100.0`
  lambda -- and theirs also has `restore_value` and an `initial_value`, which
  `platform: portall` cannot. The example uses the template form now, for the
  reason this file already gives two paragraphs earlier: a volume is a
  SETTING.
- It describes "the Guition example" wiring the page's sound through a
  resampler into a third mixer input. There is no such file in `yaml/` any
  more -- only `GUITION_ PORTAL.yaml`, which is the user's own configuration
  and has no resampler in it. The paragraph describes a file that is gone.

**Both faults were reproduced against the old code before the fix was
believed**, which is this file's standing rule: with the byte-granular ring
back, "an overrun leaves a whole number of frames" and "no frame has its two
channels out of step" both fail; with the two overrides removed, all four
volume cases fail. `tools/bttest/speaker.cpp` carries them, linking the
shipped `play()` and reading back through the shipped `fill_pcm()`.

The YAML validates against **2026.8.2**, which is what the user builds, and
the resolved config was read rather than the source: the resampler comes out
targeting 44100 mono 16-bit and every mixer source carries `timeout: never`.
**No C++ here has been compiled by a real toolchain, and nothing has been
heard** -- whether a car receiver plays it cleanly is one run away.

## Both components defaulted to the SAME USB controller, and nothing compared them

**Reported from a Waveshare 7B as *"il faut rebooter quant la clef bluetooth
est brancher"*, with an `Interrupt wdt timeout on CPU1`.** The register dump is
what names the moment, and it is worth keeping because nothing else in the
panic does. Four registers held ASCII:

    A6 0x5b6d3533 "35m["   T3 0x6d2e7961 "ay.m"   T5 0x3a697364 "dsi:"
    A7 0x645b5d43 "C][d"   T4 0x5f697069 "ipi_"   T6 0x5d393833 "389]"

which is `\033[0;35m[C][display.mipi_dsi:389]` -- an ESPHome **dump_config**
line. So the crash is at BOOT, printing the display's own configuration, not
in use and nothing to do with audio. **A panic dump's registers are often full
of the string that was being formatted; decode them before theorising.**

**The ESP32-P4 has two USB OTG peripherals and both components default to the
same one.** `portall:` defaults to `usb: true` with `usb_speed: high`, which
puts TinyUSB on the high-speed controller as a **DEVICE**. `portall_bt:`
defaults to `controller: high_speed`, which puts CherryUSB on that same
register block as a **HOST**. Two drivers, one peripheral, one interrupt line
-- and an interrupt watchdog timeout is exactly what that looks like.

**Nothing compared the two**, so it validated, built, flashed and booted. That
is this file's most-recorded shape in a new costume: not a check that passed
vacuously, but two settings that must agree with nothing asking.

**And the timing is what made it read as a Bluetooth fault.** With the socket
empty the host side enumerates nothing and stays quiet, so the board comes up
perfectly; plug the dongle in and the host begins servicing a peripheral
TinyUSB also owns. "It needs rebooting once the dongle is in" is the collision
described precisely, by somebody with no reason to suspect the display.

`_one_controller_each` is a `FINAL_VALIDATE_SCHEMA` on portall_bt -- the
component's first, there was none -- and it refuses the pair, naming both
settings and both ways out. Three states were run through `esphome config` at
**2026.8.2**, and the first is the one it was written to catch: portall at its
defaults beside portall_bt is **refused**; `usb: false` passes; `controller:
full_speed` passes. A firmware with `portall_bt:` and no `portall:` at all
(`yaml/tab5-bt-probe.yaml`) is untouched, which is why the check reads the
full config rather than assuming a sibling exists.

Two details in it were decisions rather than typing:

- **portall's key names are spelt out, not imported.** portall_bt alone is a
  whole firmware, so a hard `from ... import` would fail to validate for every
  board carrying only this component.
- **It compares what the settings MEAN, not how they are spelt.** portall says
  `high`/`full` and portall_bt says `high_speed`/`full_speed`, so both are
  judged on `startswith("high")` -- a rename on either side still collides
  correctly instead of silently passing, which is the failure mode of every
  hand-copied pair of constants in this file.

**And the fix the user needs next had no diagnostic at all.** Once the
collision is gone, `controller:` is still a guess about how a board is wired,
and a dongle on the OTHER peripheral produced one hopeful line at boot --
`waiting for a device` -- and then silence for ever, identical to a dead
dongle, an unpowered socket and a dead port. `say_if_nothing_arrived_()` says
so once after `NOTHING_ARRIVED_MS = 10000`, names the controller it watched
and the other one to try, and mentions the 5 V rail. Ten seconds cannot race an
enumeration, which takes well under one.

**Which socket a Waveshare 7B wires to which peripheral is NOT established
here** -- that is board wiring, there is no board and no schematic that could
be checked, and guessing it into an example is the "recipe dressed as a guess"
this file already paid for once. The log line above is what settles it in one
flash instead.

**The C++ check earned itself again**, in the shape it has now caught three
times: `now_ms()` and the new constant were defined halfway down the file and
used in `setup()` at the top, which is not a thing reading the diff reveals.
`now_ms()` moved to the top of the file rather than being forward-declared --
a clock is not a detail of the HCI section that happened to need it first.

## The paired devices are entities now, and the add-on is the wrong place for them

**Asked as *"il faut le faire mais aussi reconnue par addon portall avec
activation et desctivation bluetooth tu en pense quoi"*, after a guide that
proposed a list of paired devices and a one-tap unpair.** Two halves, and only
one of them should be built where it was asked for.

**The list and the unpair belong on the BOARD, and they are built.**
`switch: - platform: portall_bt` and `text_sensor: - platform: portall_bt`,
plus `portall_bt.forget_speaker` / `portall_bt.forget_input` beside the
existing `portall_bt.forget`. Every one of them reaches Home Assistant through
the ESPHome API the panel already has open -- they appear on the device page
with the volume slider and the pair button, which is where somebody looking for
"my panel's Bluetooth" actually looks.

**And the add-on half is a no, with a reason rather than a preference.** The
add-on talks to a panel over ONE socket and that socket carries udisp: JPEG
rectangles and PCM down, `'T'` touches and `'S'` awake/asleep up. There is no
control channel and adding one would mean a new message type, a command
vocabulary, a reply path, and a second copy of state that the ESPHome API
already publishes correctly. It would also put Bluetooth controls on a panel
whose add-on entry is only about rendering -- while the same panel's own
device page would carry a different set. **Two places to look is worse than
one, and the one that exists is already the right one.**

The honest version of the ask is therefore already true: from Home Assistant,
one page shows the panel's Bluetooth, and the add-on is not in the way of it.

### Why a gamepad is detected as an audio device, which is the user's own fix

Recorded because it corrects what this file would otherwise have said. The
draft answer to *"les manettes, claviers ou capteurs sont detectes a tort comme
des appareils audio"* was going to be that nothing is mis-sorted, since
`ESP_BT_COD_MAJOR_DEV_PERIPHERAL` and `ESP_BT_COD_MAJOR_DEV_AV` are different
numbers. That is wrong, and their commit says why: **a gamepad with a headphone
jack reports Audio/Video as its MAJOR class**, because the chat headset is what
the controller thinks it is. An NVIDIA Shield controller is the case in hand.

So the sort reads the MINOR class too -- `(cod >> 2) & 0x3F`, where **0x12
inside Audio/Video is "Gaming/Toy"** -- and takes such a device as an input
device when `hid: true`. Theirs, kept verbatim.

**What was added beside it is the silence.** A device heard and then not taken
produced no line at all, so a panel with `hid: false` scanning past a gamepad
looked exactly like a panel that heard nothing. It now says which kind of thing
it heard and which option is off, which is the difference between "the dongle
is broken" and "turn `hid:` on".

### `off` is a flag, and a cleared clock would have turned itself back on

**What off actually does is narrower than the word and the comment says so:**
both profiles hang up and the panel stops PAGING for what it remembers. The
dongle stays enumerated and Bluedroid stays running. Taking those apart at
runtime is `esp_bluedroid_disable`, detaching the HCI driver and stopping two
reader tasks -- on a stack this file records nine separate faults in bringing
up, none of which can be compiled here. What off buys is the paging, which is
the only radio time being spent while nothing is connected, beside a C6 whose
antenna is centimetres from the dongle's.

**And it had to be a flag.** Zeroing `reconnect_backoff_ms_` is what `pair()`
does for the length of a scan and it is not enough to STAY off: the clock is
re-armed in five other places -- every disconnect event in `a2dp.cpp` and
`hid.cpp` puts it back. A switch built that way would turn itself on again the
first time a speaker walked out of range, silently, which is this file's
most-recorded shape. `reconnect_tick_()` asks `bt_off_` instead.

Switching it back on restarts the backoff from its SHORT end, so a speaker
still in the room comes back at once rather than after whatever the clock had
grown to before it was switched off.

### Two slots and no list, which is structural rather than a shortcut

`Remembered` holds one address for a speaker and one for an input device,
because reconnecting BY ADDRESS is the whole reason this component can come
back without an inquiry -- and an inquiry takes the panel's own Wi-Fi down for
as long as it runs, measured twice on this board. So there is nothing to page
through: `text_sensor:` is ONE block with an optional `speaker:` and an
optional `input:` under it, the shape `select: platform: es8388` already uses
in this repository.

Each reads the ADDRESS and whether it is connected -- `none`, `<addr>
connected`, `<addr> paired, away`, or `<addr> (Bluetooth off)`. The address
rather than the name: `Remembered` stores six bytes and nothing else, and
adding a name would change a struct that is already sitting in NVS on every
panel that has ever paired.

`forget_one(bool speaker)` hangs up THAT device first and then removes its
bond, for the reason `forget()` already records: removing a bond under a live
ACL leaves the device connected with its key gone, so the next scan still
cannot see it AND it can no longer come back on its own. The blanket
`portall_bt.forget` stays beside the per-role ones, because it is the only one
that also clears a bond this component never recorded a role for.

### The example kept `hid: false`, and that is no longer true -- see below

`yaml/tab5-portall-bluetooth.yaml` gains the switch, the speaker text sensor
and a `Forget Bluetooth speaker` button -- and deliberately NOT the input ones,
because that board has no gamepad to pair and `hid:` is off. An entity that can
only ever read `none` is the silent no-op this file keeps recording, so the
comment says to add `input:` when `hid: true` rather than shipping it dark.

**Validated by running the CODEGEN, not only `esphome config`.** An
unresolved parent id is exactly the class of fault that `config` cannot see --
it validates YAML and never executes a `to_code`. So the resolved config was
loaded and `generate_cpp_contents()` run against ESPHome **2026.8.2**, and the
generated statements read: `bt_enabled->set_parent(dongle)`,
`...textsensor->set_parent(dongle)` with `->set_speaker(true)`,
`bt_enabled->set_restore_mode(switch_::SWITCH_RESTORE_DEFAULT_ON)` and
`forgetspeakeraction->set_parent(dongle)`. Worth the extra step because
`esphome config` prints nothing at all for an auto-generated `portall_bt_id`,
so the one line that proves the wiring is invisible from its output.

`tools/checkbt.py` gained a seventh and eighth configuration, `-DUSE_SWITCH`
and `-DUSE_TEXT_SENSOR`, for the reason the second and third exist: without
them each new platform compiles to an empty translation unit and the check
prints `ok` about a file it never read.

**SUPERSEDED, and by the hardware rather than by an argument**: a Shield now
pairs, connects and drives the launcher from that board, so the reason `hid:`
was off stopped being true and the input entity stopped being dark. See
**A dark entity is right until the device exists** below.

**What is NOT done.** No C++ here has been compiled by a real toolchain.
Nothing has paired over HID, so the input slot has never held an address. And
`components/portall_bt/universal_hid.h` -- the mapping from a Shield's report
bytes to buttons -- is **included by nothing**, so a paired gamepad's buttons
still reach the YAML as raw bytes through `on_hid_report` and no further.

## A remote drives the links, and the arrow keys were the part nobody would have checked

**Asked three times before it was built, which is the finding.** First
*"il fonctionne pas avec mes link de mon addon?"*, then a link to
**bluepad32**, then -- fairly -- *"tu ma pas dit ce que tu propose ... car il
ya que l'audio qui fonctionne avec le bluetooth"*. Two rounds of correct
analysis and no proposal. That is this file's most-recorded shape in its
fifth costume: an answer the reader cannot act on has not been delivered.

### The fault that would have shipped: `<a href>` does not listen to arrows

```
TILE = ('<a class="tile" href="%(url)s">'
```

No `tabindex`, no `keydown`. **In a browser the arrow keys do not move the
focus between links -- only Tab does.** So the whole chain could have been
built, flashed, and proved end to end on the wire, and a panel would have sat
there doing nothing: the remote pairs, the board carries the press, the sender
replays it perfectly into the page, and the page has never been listening.

It is the cheapest line of this whole path and the one it could not work
without. `KEYS_JS` in `launcher.py` moves between tiles **geometrically** --
down means the tile below, not the next one in the markup -- because the tiles
are a grid. `along + across * 3` is what decides between two candidates the
same distance away: straight ahead beats near-and-sideways.

Two decisions worth keeping:

- **Nothing is focused when the page loads.** A focus ring drawn on arrival is
  a rectangle on the wire for every panel in the house, including the ones
  nobody drives with a remote. The FIRST arrow chooses; after that it moves.
- **The ring does not pulse.** Same rule `HomeHint` lives under: a repaint is
  a rectangle on the wire for as long as the panel is awake.

### What crosses the wire is a HID usage, and the table lives in Python

`'K'`, then a usage page and a usage, both little-endian -- five bytes, the
same fixed shape as `'T'` and `'S'` rather than a length-prefixed thing of its
own.

**The far end of this socket is a BROWSER, and "ArrowDown" is the browser's
word rather than the board's.** So the board sends the usage the device itself
reported and `BROWSER_KEYS` in `udisp_send.py` turns it into a key name: ONE
table, in the sender's Python, corrected by rebuilding the add-on's image --
which happens on its own -- instead of by reflashing every panel in the house.
It is the same split every other part of this project already makes.

A press, not a down and an up. A remote button is a press, holding one to
repeat is not something a grid of tiles needs, and leaving the pair out means
nothing can be left held down by a message that went missing.

`portall.key: down` is the YAML surface: the NAME is resolved to its usage at
codegen, so a YAML never carries a number and the board never carries a table
of names.

### `portall.home` is back, and the sender's tolerance is what paid

4.9.0 removed the action and **deliberately kept the sender's half of `'H'`**,
on the grounds that a board is flashed by hand while the sender is fetched
when the add-on's image is built, so the two are never updated together and
the tolerant end is the one to keep. A remote's Back button is what wanted it.
Putting the board half back needed **no change to any sender at all** -- a
panel flashed with this works against an add-on built any time in the last
several releases.

That is the first time in this file a piece of deliberate patience has been
collected on, and it is worth saying so: the rule is not "keep everything", it
is that the END THAT CANNOT BE UPDATED TOGETHER WITH THE OTHER should be the
tolerant one.

**And the removal left two remnants behind.** The public documentation comment
for `ask_home()` and the protected comment for `home_pending_` were both still
there, describing a method and a member that had been deleted -- a paragraph
each, in a header, about something that did not exist. Neither is visible from
a diff of the change that removed them. The function went back under its own
comments rather than the comments being tidied away.

### `tools/checkkeys.py`, because the two tables sit in two files

`KEYS` in `components/portall/__init__.py` and `BROWSER_KEYS` in
`udisp_send.py` are exactly the hand-copied pair of constants this file names
as the failure mode of every such pair: a name in one and not the other is a
button that crosses the link perfectly and does nothing, and `esphome config`
cannot see it -- one side is Python the board never runs and the other is
Python the board never sees.

It executes both tables rather than parsing them, and it also refuses a usage
carrying a zero, which is `KeyAction`'s own uninitialised value. Reproduced
against a broken copy before it was believed: a name the sender does not know
and a name resolving to zero, both caught, exit 1.

### The test's ruler was wrong before the code was

The first browser test asserted that below *Home Assistant* is *Jellyfin*. It
is not: at 800 px the grid has **two** columns, so *Jellyfin* is to the RIGHT
and *YouTube* is below. Three assertions failed and the code was correct in
all three. The expectations are derived from the geometry the browser actually
computed now -- `below()` and `right_of()` read `getBoundingClientRect()` --
so the test cannot be wrong about a layout it did not choose.

Measured in the shipped Chromium at both panel shapes, on the real launcher
served by its own server: 2 columns at 800x1280 and 3 at 1280x800, arrows
moving correctly in all four directions at both, the bottom of the list
keeping its focus rather than losing it, a letter key stealing nothing, and
the focus ring read off the PIXELS -- (100,116,139) on the chosen tile's edge
against (18,22,30) beside it.

### On bluepad32, which somebody sent and which is the right pointer

- **It does not support the ESP32-P4**, and cannot: its targets are ESP32, S3,
  C3, C6, H2, Pico W, Pico 2 W and Posix, `esp32p4` appears nowhere in the
  tree, and it drives the chip's OWN Bluetooth controller -- which is the one
  thing a P4 has none of.
- **It is BTstack, not Bluedroid** (`REQUIRES "btstack"`), and its own LICENSE
  says so in its first lines: Apache 2.0, but depending on BlueKitchen's
  stack, which is commercial and free for open source. Adopting it means
  replacing the host stack that took nine faults to raise, plus writing a
  BTstack HCI transport over CherryUSB -- BTstack's USB transport is
  libusb/Posix only.
- **The portable prize is `uni_hid_parser_generic.c`**: Apache 2.0, and
  **zero references to btstack**. It does not read raw bytes; it reads
  `(usage_page, usage, value)` already decoded, and ranges them into a unified
  gamepad. That is precisely the mapping this project has been unable to write
  without a device in hand.
- **And reading it found the opening in OUR stack.** ESP-IDF v5.5.5's
  `esp_hidh_api.h`, read rather than remembered:
  `ESP_HIDH_GET_DSCP_EVT` -> `dscp { vendor_id, product_id, version, dl_len,
  dsc_list }`. **Bluedroid already hands us the device's own HID report
  descriptor and its VID/PID.** So the descriptor-driven route is open on the
  stack we have, with no BTstack at all. What is missing is a descriptor
  WALKER -- descriptor plus report to usages -- which is the BlueKitchen-
  licensed piece in BTstack and would have to be written or sourced.

  That corrects what this file would otherwise have said, and what was said in
  chat: that a mapping cannot be written without the user's own log. True of a
  mapping written BY HAND. Not true of one driven by the descriptor, because
  the device says where its own buttons are.

### What is NOT done

- **A gamepad's raw reports still stop at the YAML.** `on_hid_report` hands
  over bytes and nothing decodes them; `universal_hid.h` is still included by
  nothing and would only write to the log if it were.
  **CORRECTED the same day**: the sentence that stood here said the AVRCP
  route is wired "in `yaml/tab5-portall-bluetooth.yaml`", as a lambda a
  household writes. That was wrong twice and the next section is about why.
- No C++ here has been compiled by a real toolchain. What is proved is the
  codegen (`set_usage(7, 81)`, `(7, 82)`, `(7, 40)` and a `HomeAction`, read
  off `generate_cpp_contents` at 2026.8.2), the wire parser against a message
  split one byte at a time, the two tables' join, and the launcher in a real
  browser.
- **Nothing has been driven from an actual remote.** The chain is proved
  piece by piece on a workstation; whether a car receiver's Forward button
  moves a tile is one flash away.

## The mapping was in the household's YAML, and both halves of that were wrong

**Pushed back on as *"voici ce me derange 'le lambda' ... le comportement du
bluetooth doit gerer toutes les peripherique qu'il dispose du bluetooth trouve
une solution ce n'est pas la bonne solution"*.** Right, and this is the
**sixth** time the same person has asked for a named setting over a mechanism
somebody has to operate -- after the quality, the user agent, the frame limit,
the stylesheet and the token, all per link. They have been right every time.

The shipped version asked a YAML to carry this:

```yaml
    - if:
        condition:
          lambda: 'return pressed && code == ESP_AVRC_PT_CMD_FORWARD;'
        then:
          - portall.key: down
```

and it is now one line, `keys: panel`, with the component deciding.

### And reading the specification showed the objection was righter than it knew

The lambda above maps **FORWARD -- next track** onto "move down a tile". That
is an invention, and `esp_avrc_api.h` at v5.5.5 says what it was standing in
front of:

    SELECT 0x00   UP 0x01   DOWN 0x02   LEFT 0x03   RIGHT 0x04
    ROOT_MENU 0x09   EXIT 0x0D   ENTER 0x2B   PAGE_UP 0x37   PAGE_DOWN 0x38

**AVRCP has had real arrows since it was derived from the AV/C panel
subunit**, and a television-style Bluetooth remote sends exactly those. So
there was never anything to decide -- and the wrong mapping was not merely
verbose, it would have bound a remote's arrows to nothing while making its
next-track button walk the list. This file's own shape, again: a defensible
guess in the place a measurement belonged.

`components/portall_bt/keys.cpp` is the one place that decides now, and
**nothing in it is guessed**: the AVRCP table is a transcription of that
header, and the HID one is the Keyboard/Keypad usage page, which is what a
boot-protocol keyboard report carries by definition.

### How portall_bt reaches portall without including its header

`keys: panel` is `cv.use_id`, and `to_code` emits

    dongle->set_key_sink([](uint16_t page, uint16_t usage) {
        panel->send_key(page, usage); });

into **main.cpp**, where both components' headers are already in scope. The
sink is a `std::function` rather than a `Portall *` for exactly that reason:
portall_bt alone is a whole firmware, and a board carrying only it must still
build -- so this component includes portall's header nowhere, and a YAML that
never sets `keys:` never generates that line. The class is named as a string
in the schema for the same reason a hard `from ... import` was refused in
`_one_controller_each`.

Read off `generate_cpp_contents` at 2026.8.2 rather than assumed, because
`esphome config` runs no `to_code` -- which this file recorded as a lesson one
section earlier and applied here without being told twice.

### What it covers, and the one line that says where it stops

**A remote and a keyboard need nothing per device.** AVRCP's commands are an
enumeration; a boot-protocol keyboard report is modifiers, a reserved byte and
six keycodes, and those keycodes ARE usage page 0x07. Both are the
specification rather than anybody's reading of one device.

**A gamepad is not covered and does not pretend to be.** Its report layout
differs per device and is only knowable from its own report descriptor. The
test asserts the boundary from the other side: a gamepad-shaped eight-byte
report with a non-zero second byte produces nothing at all.

Two details that are defects if they are missing:

- **A key still held is in EVERY report a keyboard sends.** Without comparing
  against the last one, a finger resting on Down walks the whole list in a
  second. `held_[6]` is the previous report and only what is NEW crosses.
- **AVRCP sends a PRESSED and a RELEASED for every button**, so the decode is
  on the press alone -- a tile moved twice per press is a remote nobody can
  aim.

### The stand-in header was half a header, and could not have caught this

`tools/btstub/esp_avrc_api.h` carried `POWER` through `BACKWARD` -- the media
transport codes, which were all the component happened to use -- and **not one
of the navigation commands**. So a missing `ESP_AVRC_PT_CMD_UP` would have
been a compile error on somebody's board and nothing here could have seen it
coming. Copied field for field from v5.5.5 now, like the rest of them.

### The test fails against the old mapping, which is the only reason to trust it

`tools/bttest/input.cpp` drives the SHIPPED `feed_avrc_key()` and
`feed_hid_keys()` through a recording sink. Nine navigation commands land on
the right usages; the six transport commands must produce **nothing**, which
is the fix itself kept rather than remembered. Reproduced against a copy
carrying the old table and the repeat suppression removed: three cases fail,
including `play, pause, stop, next, previous and volume move nothing`.

`checkbt` links `keys.cpp` into every test binary -- the linker refused three
of them the moment the drains called into it, which is the arrangement doing
its job.

### `keys: panel` was an id, and a household copied it into a board that had none

**Reported from a real build the same day:**

    Couldn't find ID 'panel'. Please check you have defined an ID with that
    name in your configuration.
      keys: panel

Their `portall:` block is `id: udisp`. The example said `keys: panel`, they
copied the line, and it could not validate. **That is the same objection the
whole section above was built from, still sitting in the option itself**:
there is exactly ONE `portall:` per board, so asking anybody to go and read
their own id is asking them to operate a mechanism rather than name a thing.

`keys: true` now resolves the only one. An id is still accepted, and a typo in
that form is still refused -- so being explicit stays possible without being
required.

**And the first attempt at it died in the build**, which is the part worth
keeping. It built the unnamed id in `to_code`:

    esphome.core.EsphomeError: Circular dependency detected!

esphome fills an id whose name is None from the declared ids of a matching
type -- `if id.id is None and id.type is not None` in `config.py` -- and that
pass walks the **validated config**. An id invented at codegen was never in
it, so nothing resolved it and `get_variable` waited for a variable that would
never be registered. `_keys` does it in the VALIDATOR now.

Four cases run through real esphome on the configuration a panel really sent,
with `portall: id: udisp` throughout, reading the generated C++ rather than
the validator's opinion: `keys: true` and `keys: udisp` both emit
`udisp->send_key(page, usage)`, `keys: panel` is refused, and `keys: false`
emits nothing at all.

### What is NOT done

No C++ compiled by a real toolchain, and nothing driven from an actual remote.
A gamepad still needs the descriptor route. And whether a given remote speaks
AVRCP navigation or HID at all is the device's choice, not this component's --
the log says which it decoded, once per press, so the next run settles it.

## No button on a remote could leave a link, and the question is what found it

**Asked as *"pour l'addon avec les link tu n'a pas expliquer comment je peux
utiliser une telecommande, une manette dans les link?"*.** A fair question
about documentation, and answering it honestly meant looking at what a remote
really does inside each kind of page -- which is where the hole was.

**EXIT and ROOT_MENU both mapped to Escape.** Two buttons doing one thing, and
no button doing the one a remote most needs: once a tile is open, the ONLY way
back to the launcher was the corner gesture on the glass -- a finger held in
the top-left corner for a second. That is a gesture for somebody standing at
the panel, and a remote is for somebody who is not. Nothing on a remote
reached `portall.home`.

So the two part company the way a television's do. **EXIT/Back stays Escape**,
which is back WITHIN the page; **MENU leaves the page**, to the panel's own
`url:`. `HomeSink` is a second `std::function` beside `KeySink`, emitted from
the SAME `keys:` option -- asking a household to configure the way home
separately would be the mechanism that option was written to remove.

**And it cost no sender change at all.** portall's `'H'` message and the
sender's half of it have both been in place since 4.9.0, when the board half
was removed and the sender's was deliberately kept. That is the second time
this file's tolerance rule has been collected on, and it is the same rule:
**the end that cannot be updated together with the other should be the
tolerant one.**

### What the arrows actually do, page by page, read rather than assumed

A browser does NOT move focus between links with the arrows -- only Tab does
-- which this file already records as the fault that would have shipped with
the launcher. So each page answers for itself:

| | arrows | what it needs |
|---|---|---|
| the launcher | yes | nothing -- `KEYS_JS` does it geometrically |
| YouTube `/tv` | yes | the television user agent on that link |
| **Jellyfin** | yes, **only in TV layout** | Settings > Display > Layout: TV |
| Home Assistant | **no** -- Tab moves, Enter opens | nothing to set |
| anything else | arrows scroll, Tab moves focus | -- |

**The Jellyfin row is their source, not a guess.** `src/scripts/
keyboardNavigation.js` at `jellyfin/jellyfin-web`:

```js
if (!layoutManager.tv && isNavigationKey(key)) { return; }
...
case 'Escape': if (layoutManager.tv) { inputManager.handleCommand('back'); }
```

Every arrow is dropped and Escape means nothing outside TV layout, and
`layoutManager` takes that from a saved user setting. So a remote on Jellyfin
does nothing at all until somebody changes one dropdown -- which reads exactly
like a broken remote, and is one line of documentation instead.

Home Assistant's frontend has no arrow navigation of its own, so a remote
there is Tab, Enter and Menu. Said plainly rather than implied: a finger is
still the better answer on a dashboard.

### What is verified

The test drives the SHIPPED `feed_avrc_key()` through a recording HOME sink as
well as a key sink, and **fails against the old mapping** -- reverted in a
copy, `MENU goes home` and `and tells the page nothing at all` both fail,
which is the only reason to trust it. The codegen was read off
`generate_cpp_contents` at 2026.8.2 rather than from the validator's opinion:
`keys: true` and a named id both emit `set_key_sink` AND `set_home_sink`,
`keys: false` emits neither.

**No bump.** The add-on change is `DOCS.md` only, which the Supervisor reads
from the repository -- `tools/checkaddon.py` is the arbiter and it agrees.

**What is NOT done.** No C++ compiled by a real toolchain, and nothing driven
from an actual remote: whether a given device speaks AVRCP navigation or HID
is its own choice, and the log says which it decoded. A HID keyboard has no
home key to map -- the Keyboard/Keypad page has none, and a consumer-control
report cannot be parsed without the device's descriptor -- so on a keyboard
the corner gesture is still the way out.

## A gamepad was connected, every button pressed, and the log said nothing

**Reported as *"j'ai appuyer sur toutes les touche de la manette rien ne
fonctionne sur les link jellifyn,youtube etc"*, with a log that is perfect
right up to the point it goes quiet:**

    paired with 00:04:4B:93:A9:B2 "NVIDIA Controller v01.04"
    input device 00:04:4B:93:A9:B2 is connected
    (nothing, ever)

Paired, bonded, connected, and then not one line however many buttons were
pressed. **Two faults, and the second is the one this file keeps recording.**

**The mapping was sitting in this repository, written by the user, included
by nothing.** `components/portall_bt/universal_hid.h` decodes a Shield
controller's report -- 33 bytes behind report id 0x01, the hat in the HIGH
nibble of byte 2, the face buttons in byte 3 -- and CLAUDE.md has recorded it
as "included by nothing" for several sections without anybody drawing the
conclusion. Meanwhile keys.cpp said a gamepad "cannot be" covered because its
layout is only knowable from its own report descriptor. Both statements were
in the file at the same time, and the second stopped being true the moment
somebody read a descriptor off a real device -- which they had.

**And their numbers corroborate themselves, which is what made it safe to
take rather than merely available.** The hat values they measured -- 0 up, 2
right, 4 down, 6 left, 8 at rest -- are HID's own **Hat Switch** encoding,
eight compass points clockwise from north with one past the last meaning
centred. So it is the specification arriving by way of a measurement, not one
device's quirk, and a second gamepad laying its hat out the same way is the
normal case rather than luck. A diagonal sends NOTHING: a grid of tiles has
no diagonal, and picking one of its two axes for the caller would be the
invention this file exists to keep out -- which their own notes reached
independently, listing four directions and not eight.

**The second fault is that a dropped report was silent**, and it is the shape
this file has now recorded a dozen times in a dozen costumes. `feed_hid_keys`
tested for a boot-protocol keyboard and returned on anything else, so a
controller sending thirty-three bytes a hundred times a second produced
exactly as much log as a controller that was not there. From outside, "the
mapping does not cover this device", "`keys:` is not set" and "the dongle
died" were one silence.

Three lines now separate them, each said ONCE because a device that sends
something unreadable sends it for ever:

- `keys:` unset -- names the setting.
- a shape this cannot read -- prints its length and first bytes, and names
  `show_reports:`.
- **an unmapped BUTTON BIT names its own bit.** That is the useful one: which
  bit is a given controller's Home is not knowable here, and guessing it
  would be the recipe-dressed-as-a-guess this file has paid for. One press of
  that button in a log is now the whole of what is needed to map it, against
  a round trip that would otherwise begin "please turn show_reports on".

**What is verified.** The test drives the SHIPPED `feed_pad_report()` and, for
the real path, `feed_hid_keys()`, through a recording sink: four hat
directions, A, B, a diagonal moving nothing, a held button sent once, a
resting controller sending nothing at all, and an unmapped button reaching
the page as nothing. It **fails against the old code** -- with the routing
reverted, `and it arrives through feed_hid_keys, which is the real path`
fails, which is the user's report reproduced exactly.

**`universal_hid.h` is gone**, on the user's own say-so once it was flagged
to them -- it had become a second copy of a mapping that now ships, and two
copies drift the moment anybody adds a device. Its content and the credit for
it live at the head of `keys.cpp`, which is the only place that decides. The
file was theirs, so it was not deleted until they said to: a dead file is a
maintenance trap and somebody else's file is still somebody else's.

**What is NOT done.** No C++ compiled by a real toolchain, and nothing driven
from an actual controller: the layout is the user's measurement, not this
session's.

**CORRECTED by the panel, one round later.** Two of those three offsets were
wrong -- every d-pad direction printed `up` and X and Y printed nothing -- and
the byte offsets are gone entirely. The device's own report descriptor is read
instead; see **The device says where its own buttons are** below. The lesson
the correction adds is not that the measurement was bad, it is that a
measurement of one device is a table about one device, and the test written
from the same table could not tell.

## "Cherche sur internet, c'est plus simple ?" -- yes, and it found a defect

**Asked as exactly that, about the round trip the previous section had just
built in: press Home, read the bit, send me the line.** It is a fair
challenge and it was right, which makes it the seventh time this user has
been right about approach. Fifteen minutes of reading somebody else's driver
beat asking them to go and press a button.

**Linux ships a driver for this exact controller** -- `hid-nvidia-shield.c`,
merged for 6.5, for the SHIELD 2017 "Thunderstrike", which is what
`NVIDIA Controller v01.04` in their log is. Two things in it matter and both
correct something this repository had already written down.

**HOME IS NOT A GAMEPAD BUTTON.** Its `android_input_mapping()` returns early
unless the usage page is CONSUMER, and then maps Play/Pause `0x0CD`, Volume
Up `0x0E9`, Volume Down `0x0EA`, Search `0x221`, **Home `0x223`** and Back
`0x224`. So the Shield's Home, Back and media keys arrive on a **separate
report** and never touch the face-button byte at all.

The diagnostic shipped one commit earlier told a reader to press Home and
watch for `gamepad: button bit N of byte 3`. That line **cannot ever appear
for Home**. It is this file's most-recorded fault in its purest form: not a
check that passes vacuously, but a fix the reader is sent to a place it can
never be found. And it would have cost them a flash and a round trip to
discover, which is precisely what the search was supposed to save.

**And it says one line in TOTAL was the wrong budget.** `say_unreadable_report_`
named the first unreadable shape and went quiet for ever -- so on a
controller whose sticks stream one report shape constantly, the Home report
would have been swallowed by the very diagnostic meant to catch it. It
reports **once per SHAPE** now (length + report id, capped at six), which is
what makes "press Home and send me the line" a promise that can be kept.

**The second correction is to the user's own deleted file**, and it is worth
recording because the same mistake is easy to make again.
`universal_hid.h` treated report ids `0x03` and `0x04` as a media remote.
They are not: the kernel driver names them
`THUNDERSTRIKE_HOSTCMD_RESP_REPORT_ID = 0x3` and
`THUNDERSTRIKE_HOSTCMD_REQ_REPORT_ID = 0x4` -- NVIDIA's own host-command
channel for battery, haptics, LED and firmware. Decoding them as key presses
would have produced phantom buttons from a battery report.

**What the search did NOT settle**, and the honest limit of it: the kernel
driver leaves face buttons and the hat to the generic HID layer, reading the
device's own report descriptor, so it says nothing about which bit is A. That
half is still the user's measurement, and their hat values being HID's Hat
Switch encoding is still what makes it trustworthy. The consumer report's
layout -- its id, and whether it carries a bitmap or a 16-bit usage -- is not
in that driver either, which is why it is a diagnostic here and not a table.

**And the sentence above contains the answer it says it does not have.** "The
kernel driver leaves face buttons and the hat to the generic HID layer,
reading the device's own report descriptor" was written as the limit of the
search, and it is the whole method: the descriptor is where the answer is, we
already receive it, and reading it needs nothing from Linux or from the user.
It took one more round and one more wrong table to notice, which is worth
recording because the sentence was sitting right here.

**The lesson is narrow and it is not "always search".** It is that a
component talking to a mass-market device is talking to something somebody
else has already written a driver for, and that driver is a measurement
nobody here has to pay for. This project reads Espressif's headers as a
matter of course; it had never once read Linux's.

Verified by capturing the diagnostic's own stdout over two report shapes:
each is named once, a repeat is silent, and nothing reaches the page.
Reproduced against the shipped one-line-in-total version first, where `and a
SECOND shape gets its own line` fails.

Sources: torvalds/linux `drivers/hid/hid-nvidia-shield.c`.

## The device says where its own buttons are, and bluepad32 is why

**Pointed at by the user, twice, and the second time plainly: *"je te comprend
pas dans ce lien tu dispose de tous les elements
https://github.com/ricardoquesada/bluepad32/tree/main"*.** They were right, and
reading it settled in an hour a question two rounds of guessing had not.

The round before this one shipped a gamepad mapping made of FIXED BYTE
OFFSETS -- an NVIDIA Shield's hat in the high nibble of byte 2 of a 33-byte
report, its face buttons in byte 3 -- taken from the user's own measurement
and corroborated by the hat values being HID's Hat Switch encoding. It reached
a panel and two of the three were wrong:

    gamepad: up      (every direction, all four of them)
    gamepad: A -- ok
    gamepad: B -- back
    (x and y: nothing at all, not even the unmapped-bit line)

**And the test passed the whole time**, because the test fed reports laid out
from the same table the code read them with. A check written against the
code's own assumption can only ever confirm it -- which is this file's
most-recorded shape in a costume it had not worn yet.

### What is in that link, and the one piece that is not

bluepad32 was read rather than remembered, and it splits exactly in half:

| | where it lives | licence |
|---|---|---|
| the MAPPING -- (usage page, usage) to a gamepad | `uni_hid_parser_android.c` | Apache 2.0, bluepad32's own |
| the WALK -- report bytes to those usages | `btstack_hid_parser_init/_has_more/_get_field` | BlueKitchen's BTstack |

`uni_hid_parse_input_report` is four lines long and every one of them calls
BTstack. There is no `uni_hid_parser_nvidia.c`; a Shield lands on the ANDROID
parser, and that parser never sees a byte -- it is handed `(usage_page, usage,
value)` already decoded. So the link does carry everything a mapping needs,
and the reason this project could not use it is that the thing underneath it
belongs to a different repository with a non-commercial clause.

**That piece is now ours.** `components/portall_bt/hid_descriptor.cpp` walks a
report descriptor -- HID 1.11 section 6.2.2, the item encoding -- into a flat
table of fields, and decodes a report against it. Written from the
specification rather than copied, which is the same standard every table in
`keys.cpp` already meets.

### And the descriptor was already arriving, unread

`ESP_HIDH_GET_DSCP_EVT` carries `dsc_list`, `dl_len`, `vendor_id` and
`product_id`. CLAUDE.md has recorded that event as the opening for a
descriptor-driven route for two sections, while `keys.cpp` carried byte
offsets and a panel reported that no button worked. **The event was in the
switch's `default:` case.** Nothing was missing; nothing was reading it.

So the whole change is: stage the descriptor on Bluedroid's task, parse it in
`loop()` (the same split the report queue already makes), and map usages
instead of bytes. There is now **no byte offset and no vendor id anywhere in
this component's input path**, deliberately: the moment one appears the fault
above is back.

The mapping follows bluepad32's Android numbering, which is a convention
rather than a rule and is worth writing down as such: Button page usage 1 is
A, 2 is B, 4 is X, 5 is Y, with **3 and 6 skipped** because Android's own
mapping has no C or Z. The hat is the specification: eight compass points
clockwise from north, one past the last meaning centred, and the null value
and the offset both come out of the descriptor rather than a table -- some
devices number a hat from 0 and some from 1.

**Home now works without anybody pressing anything and reporting back.** The
previous round established from Linux's `hid-nvidia-shield.c` that a Shield's
Home is CONSUMER usage 0x223 on its own report, and concluded that mapping it
needed the user's log. It did not: the descriptor names that usage, on that
report, with its page carried in a **four-byte Usage item** whose high half is
the page. A walker that ignores that half puts AC Home on the Generic Desktop
page, where nothing maps it -- the one button somebody most wants, silently
doing nothing. There is a test for exactly that.

### What is verified, and how the old fault is kept

`tools/bttest/descriptor.cpp` links the shipped walker and runs it against
descriptors whose answers are stated independently of it:

- **the boot mouse of the specification's own appendix E.10**, which is
  published rather than derived here: three buttons in bits 0-2, five bits of
  padding, then two signed eight-bit axes -- and X at **bit 8**, which is what
  proves a constant item still moves the cursor.
- two report ids, where report 2's first field must start at **bit 0 of its
  own payload**. One shared cursor is the classic way to land a second
  report's fields a byte out, and here it would have made Home unpressable.
- a sixteen-bit field at bit 4, little-endian, reading 0x1234 and then
  -32768 from its own top bit.
- a logical maximum written 0xFF meaning 255, which the specification does not
  sanction and real devices do anyway.
- Push and Pop, an array field, a short report, a descriptor cut off
  mid-item, and one past the field limit, which keeps what fits and **says**
  it was truncated.

**And the reported fault is reproduced as arithmetic**: over the four
directions, the old rule -- the high nibble of byte 2 -- returns the SAME
value every time, because with the hat where the descriptor really puts it
that nibble is a stick axis. One loop, and it is what the panel saw.

**Two faults were in the tests and neither was in the code**, which is the
usual proportion:

- `mkstemp` REWRITES its template in place, so the second stdout capture was
  handed a path with no `XXXXXX` left, failed, and left `stdout` NULL -- a
  segfault in whichever test printed next, with nothing wrong in the
  component. The template is restored per call now.
- a test asserting a negative value set bit 7 of the last byte when the
  field's own top bit is bit 3 of it. The ruler, again.

**What is NOT done.** No C++ here has been compiled by a real toolchain, and
**the Shield's real descriptor has never been seen** -- the fixtures are
descriptors built to the specification, not that device's. What settles it is
one flash, and this time the failure mode is different in kind: a device whose
descriptor cannot be read now says so in a line naming its size, rather than
answering confidently and wrongly.

Sources: ricardoquesada/bluepad32 `parser/uni_hid_parser.c`,
`parser/uni_hid_parser_android.c`; espressif/esp-idf v5.5.5
`esp_hidh_api.h`; USB HID 1.11 section 6.2.2.

## "up cree des probleme" was the launcher, and the Bluetooth log proves it

**Reported after the descriptor work went in: *"c'est mieux mais up cree des
probleme regarde les logs"*, with a panel log.** The logs are the useful half,
and what they say is that the d-pad is not the fault at all:

    gamepad: right / down / left / left / left / up / right / right / down ...

Every direction decoded, each one once per press, from a controller whose own
descriptor is being read. So `up` crosses the wire correctly and the trouble
is wherever it LANDS -- which is the launcher page, whose log this is not.

**And the fault was there, in the line that decides whether to swallow the
key.**

```js
var next = nearest(from, way, all);
if (next) { next.focus(); e.preventDefault(); }
```

`preventDefault()` only when a tile was FOUND in that direction. So an arrow
at the EDGE of the grid -- up from the top row, down from the bottom, left
from the first column -- fell through to the browser, which scrolls. From the
glass that is the launcher jumping away from the tile somebody had just
chosen, with the focus ring left somewhere off-screen, and the next press
moving a selection nobody can see.

**Up is where it shows first for a structural reason**: a launcher opens on
its top row, and up is the one direction the top row has nothing in. Left has
the same hole and is reached less often; down and right only at the very end
of the list. So "up creates problems" is precisely what this defect looks
like from a sofa.

It is swallowed before anything else now. The arrows belong to the grid on
that page and there is nothing else on it to scroll to -- and a key this does
not handle (PageDown was the one measured) still reaches the browser
untouched, which is what a careless `preventDefault()` at the top would have
broken.

**The first arrow also chose the wrong tile**, which is smaller and was worth
fixing beside it: it always took `all[0]`, so up and down did the same thing
from a cold page. Down or right reaches for the first tile now, up or left for
the last, the way a menu does.

### The test passed against the broken code, and the ruler is why

The first version of the browser case pressed up with the page **already at
the top** -- where a browser cannot scroll up either. It passed against the
shipped launcher and proved nothing. An unswallowed arrow needs somewhere to
go, so the page has to be SCROLLED first, which is exactly the state a panel
is in after somebody has moved around the grid.

Measured on `window.scrollY` in the shipped Chromium at 800x420, against the
real launcher served by its own server: with the page scrolled to 80 px and
the top-left tile focused, up leaves it at 80 and keeps the focus. Against
`git show HEAD:portall/launcher.py` in a worktree beside it, **`up on the top
row does not scroll the page` fails** -- the user's report reproduced, which
is the only reason to trust the fix. Nine cases, including that moving still
works, which a half-fix would break.

`tools/checkarrows.py` is that check, kept: it needs Playwright and a
Chromium, the way `tools/checkyaml.py` needs an esphome, and it is the only
thing in this repository that can see what a key does to a page.

This file already records "the test's ruler was wrong before the code was"
for this same script, over which tile is below which. That was about the
layout; this one is about the STATE the page is in when the key arrives, and
both times the fixture was built from the same assumption as the code.

## The same log said the field limit was too small, in its own words

    input device 0955:7214 described itself: 379 bytes, 64 fields
    (and more than this can hold)

**That is the descriptor path working and reporting its own shortfall on the
first real device it met.** `MAX_FIELDS = 64` was sized for "two sticks, two
triggers, a hat and sixteen buttons with room over" -- which covers a
gamepad's first collection and nothing else. A Shield declares a consumer
report, a battery, NVIDIA's own host-command channel and more besides in 379
bytes, so whatever fell past the cap **did not exist**: a report made only of
dropped fields decodes to nothing and reads, from outside, as a device
sending something unreadable.

192 now, which is under four kilobytes, with report ids at 16 and local
usages at 64.

**And the line could not say what to raise it to.** "More than this can hold"
is a number-free complaint about a number, so the next person guesses -- which
is the round this replaces. `wanted_fields()` counts every input field the
descriptor declares whether or not it is kept, and the warning prints both:
`described itself in 379 bytes and N fields, of which only 192 fit`. The test
asserts the count as well as the cap, and a second case asserts that a
hundred and twenty fields now fit where sixty-four did not.

**`show_reports:` prints the WHOLE descriptor**, sixteen bytes to a line, and
the reason it is all of it rather than a taste is that a descriptor is only
useful entire: it is a stream of items where every one shifts the meaning of
what follows, so the first thirty-two bytes say nothing about where a button
is. The previous version printed thirty-two, which was decoration.

This is also the one fixture this repository has never had. Every gamepad
round so far worked from measurements of REPORTS, because the descriptor they
are laid out by went straight from Bluedroid into the parse and was never in
anybody's hands. One flash with `show_reports: true` makes it a fixture, and
then a controller nobody here owns can be decoded on a workstation.

**A descriptor that has not changed is no longer re-parsed or re-announced**,
which matters on this hardware rather than in principle: see below.

## The Shield drops the link every eleven seconds, and it is not the buttons

The same log, with the timings lined up:

| | connected | dropped | presses in between |
|---|---|---|---|
| 1 | 00:51:28 | 00:51:39 | thirteen |
| 2 | 00:51:40 | 00:51:51 | **none at all** |
| 3 | 00:51:51 | -- | one |

**Eleven seconds each time, and the second window had no button pressed in
it.** So the drop is periodic and has nothing to do with `up` or with any
other key -- which is worth writing down because the report arrived attached
to a key.

What the log says about the mechanism, read rather than guessed:

- `hcif disc complete: hdl 0xd, rsn 0x13` -- **0x13 is Remote User Terminated
  Connection**. The controller hangs up; the panel does not.
- `hcif mode change: mode 2, intv 18` about two seconds after each connect is
  the link entering SNIFF at 11.25 ms. The teardown follows roughly nine
  seconds later.
- `HID-Host - Rcvd CTL L2CAP conn ind, wrong state: 1` and `opcode=0x0405,
  status= 0b: Conn Exists` are a paging COLLISION: the Shield pages back the
  instant it has hung up, while Bluedroid is already paging it.

**Nothing in this component runs on an eleven-second cadence**, which was
checked rather than assumed: the only long constants here are
`RECONNECT_FIRST_MS` 2000, `RECONNECT_MAX_MS` 60000 and `NOTHING_ARRIVED_MS`
10000, and `hid_reconnect_()` returns at its first line while `hid_open_` is
true. So the page requests in that log are Bluedroid's own, not ours.

**It is NOT diagnosed**, and saying otherwise would be the guess-dressed-as-a-
recipe this file has paid for repeatedly. What has been done is to stop it
drowning the evidence: a reconnection re-sends the same descriptor, so parsing
379 bytes and printing the same two lines every eleven seconds buried whatever
else the log was trying to say. The bytes are summed and an identical
descriptor is skipped -- the edge-detection state is still reset, because a
controller that hung up is not still holding what it was holding.

What would settle it is the next log with `show_reports: true`: whether the
controller sends anything at all in the seconds before it hangs up, and
whether the same eleven seconds appear with the panel's Wi-Fi quiet. The
dongle's antenna is centimetres from the C6's, and this file already records
an inquiry taking the Wi-Fi down for exactly as long as it runs.

## A dark entity is right until the device exists, and then it is just missing

**Reported in one line with the block pasted: *"il manque un test sensor pour
les device il y a juste pour le speaker"*.** Correct, and the interesting part
is that the example was RIGHT when it was written and had quietly stopped
being so.

`yaml/tab5-portall-bluetooth.yaml` carried a speaker text sensor and no input
one, with a comment saying why: `hid: false` on that board, and **an entity
that can only ever read `none` is worse than no entity** -- the silent no-op
this file keeps recording, avoided deliberately. Then a Shield paired with a
panel, connected, and moved tiles. The comment's premise died with that run
and the comment stayed.

So the rule survives and its application inverts: `hid: true`, both slots, and
the comment now says to take `input:` back out **along with `hid:`** if a panel
will only ever have a speaker. A conditional written into a file has to be
re-read whenever its condition changes, and nothing does that but somebody
noticing -- which is what happened.

**Three other things in that file went stale in the same run**, all of them
sentences that were true when typed:

- `hid: false` itself, with "nothing here has one to pair with".
- **"A gamepad is NOT covered: its report layout ... is only knowable from its
  own report descriptor."** True, and it stopped being an obstacle the day the
  descriptor started being read. A comment that is a correct fact and a wrong
  conclusion is the hardest kind to spot, because re-reading it confirms it.
- `Pair a Bluetooth speaker`, on the one button that finds both kinds -- the
  comment beside it already said so. `Pair a Bluetooth device` now, with
  `Forget Bluetooth controller` beside the speaker's.

### `tools/checkcodegen.py`, and it reproduces a fault `esphome config` calls ok

Adding two entities meant proving they are WIRED, and `esphome config` cannot
say: it validates YAML and stops before any `to_code` runs, and it prints
nothing at all for an auto-generated `portall_bt_id`. This file already
records doing that step by hand twice. It is a tool now, and it earns itself
on a fault this repository has actually shipped:

| | `esphome config` | `tools/checkcodegen.py` |
|---|---|---|
| a to_code resolving an id that does not exist | **ok** | `ECHEC (codegen: Circular dependency detected!)` |

That is the exact error a household got from `keys: panel`, and the exact one
a first attempt at fixing it produced. Reproduced by breaking the text
sensor's `to_code` in place, running both, and restoring.

It loads each example the way `checkyaml.py` does -- throwaway secrets, a
local `external_components` so the working tree is what is checked -- then
calls `generate_cpp_contents()`; `--show` prints the statements that join one
component to another, which is how these entities were proved rather than
assumed:

    portall_bt_portallbttextsensor_id->set_speaker(true);
    portall_bt_portallbttextsensor_id_2->set_speaker(false);
    portall_bt_forgetinputaction_id->set_parent(dongle);
    dongle->set_hid_host(true);
    dongle->set_key_sink([](uint16_t page, uint16_t usage) { panel->send_key(page, usage); });

It has to run inside the esphome being checked, so it takes that venv's
python rather than a binary. Two blind spots are inherited and named: it
carries checkyaml's micro_wake_word case, because that model is downloaded
DURING validation and a sandbox with no route to github.com would otherwise
report a good file as broken; and `GUITION_ PORTAL.yaml` is skipped by name,
because it is a household's own configuration rather than an example this
repository offers and its `esphome: name:` is theirs to fix.

**`describe_role()` was read rather than trusted**, since the input slot has
never held an address on any panel: `speaker ? remembered_.sink :
remembered_.hid` and `speaker ? a2dp_open_ : hid_open_`, both branches
correct. A slot nothing has ever exercised is exactly where a copy-paste
reads the wrong member.

## /data reached 2.1 GB, and it is two faults that look like one

**Reported as *"super ont avance bien mais je voudrais qu'ont s'occupe de
l'addon 2,1G est enorme"*, with the listing that splits it:**

    2.1G  /data/profiles
    1.2G  /data/profiles/salon        <- the only panel configured
    750M  /data/profiles/Tab5         <- gone
    239M  /data/profiles/salon2       <- gone
     46M  .../optimization_guide_model_store   (in EACH of the three)

The first reading was that this is orphaned profiles, because that is what
the user said first -- *"il conseve tous les profiles ce qu'il sont supprime
alors que mon profile et juste salon"*. It is half of it. The panel they are
KEEPING is the biggest one, so a sweep alone would have left 1.2 GB and the
complaint standing.

**Nothing ever removed a profile, and the panel list is the only thing that
knows.** `sweep_profiles()` in `run.py`, called from `main()` before anything
is started. Three guards, and the first is the only one that can cost
somebody something irreversible:

- **Nothing is swept when no panels are configured.** `main()` refuses to run
  in that state anyway, but a configuration that failed to load looks exactly
  like a house with no panels, and reading it that way deletes everything.
- Only direct children, only directories, never through a link.
- **A panel owns its folder name whether or not it keeps a profile.**
  `profile_name()` is split out from `profile_for()` for exactly this: they
  answer different questions, and turning `keep_profile` off must not hand
  that panel's folder to the sweep.

**And Chromium sizes its own cache from the free space it can see**, which
inside an add-on is the whole disk Home Assistant is on. That is why the live
panel is the biggest: nobody set anything, and a browser that has been up for
months uses what it was offered. A single cache entry in it was 76 MB.

**The cap is one switch and the NUMBER is the whole of the work, because the
same switch governs the compiled-code cache.** Measured on a site built to
fill both, three loads each, 900 KB scripts:

| `--disk-cache-size` | HTTP cache | compiled code |
|---|---|---|
| none / 200M / 100M / 60M | 48.9 MB | 31.5 MB |
| 40 MB | 36.0 | 4.4 |
| 30 MB | 27.0 | **0.0** |
| 20 MB | 18.9 | **0.0** |

The first three rows are the fixture's own ceiling rather than the browser's.
The HTTP cache lands near 90% of the number, and below about 40 MB the code
cache stops storing anything.

**Reading that table alone gives the wrong answer, and the second measurement
is what caught it.** 60 MB looks safe there -- the code cache is still full.
But the add-on's four biggest Code Cache entries were **13.4 MB each**, which
is Home Assistant's own bundle compiled, on the page a panel shows all day,
and the fixture's scripts were far too small to say anything about an entry
that size. Scripts of 0.5, 2, 6 and 13 MB, three loads each, what was kept:

| | kept |
|---|---|
| no cap | 15.5 MB, 5.1 MB, 1.3 MB |
| **150 MB (shipping)** | 15.5 MB, 5.1 MB, 1.3 MB -- the same, to the byte |
| 60 MB | 5.1 MB, 1.3 MB -- **the frontend refused** |

So 150 costs the code cache nothing and holds a panel to an eighth of what
one was measured at, and 60 would have turned the cache off for precisely the
page this exists to show. **A fixture built from convenient sizes measures the
fixture.** The first table is true and it is not about the user's case.

**`--disable-features` is ONE flag now and that is deliberate.** The ML
accessories -- 46 MB of `optimization_guide_model_store` in every profile,
for a browser whose whole job is to paint a dashboard -- go in beside
`CalculateNativeWinOcclusion` rather than in a second flag, because a second
one is at best redundant and at worst replaces the first. **Whether a repeat
merges was not established**: two attempts to measure it picked features
(WebGPU, UserAgentClientHint) that turned out not to be gated by the switch
at all on this build. Putting them together means the answer is not needed,
which is the right shape for a question that resisted two measurements.

**Whether the model stops being downloaded is NOT verified** -- there is no
route to Google's services from here, and a feature name Chromium does not
recognise is ignored in silence, which is this file's most-recorded fault
shape. What is verified is that the browser starts and paints the identical
picture with the flags as without. So `sweep_profiles()` also prints what
each KEPT profile costs, at startup: the next `du` settles it, and it settles
it from the add-on's own log rather than from a shell inside the container.

**A measurement trap worth keeping, and it cost two runs.** `python3 x.py |
tail -8` buffers the whole run, so a script printing a line per case looks
stalled from outside for as long as it takes. One of them was killed on that
evidence and had been working the entire time. Worse, `pkill -f cost.py` run
inside a backgrounded shell **matches that shell's own command line** and
kills the job before it starts -- three background tasks died at once of
exactly that. Watch a run by its process, not by a pipeline's output.

`tools/checkprofiles.py` runs the shipped `sweep_profiles` against real
directories: the reported three-folder case, the empty list, `keep_profile`
off, a panel filed under its address, a file and a link beside the profiles,
and no profiles directory at all. It fails against the old code, though
trivially -- the function did not exist, because the fault was an absence.

## The iPhone cannot see the panel, and the dongle's version is not why

**Asked as *"je voudrais qu'ont fasse fonctionner le dongle Bluetooth 5 de
tplink bien plus performant que le Bluetooth 4 car je voulais me connecter
avec mon iPhone mais il ne vois pas ce Bluetooth 4"*.** The premise is
reasonable and it is wrong, and three readings settle it without a board.

**The panel is not discoverable.** `hid.cpp` sets `ESP_BT_CONNECTABLE,
ESP_BT_NON_DISCOVERABLE` at startup and turns discoverability on only for the
length of `pair()`. That is deliberate and documented above -- a panel in
every telephone's Bluetooth list for ever, for one pairing. So outside those
ten seconds no phone can see it whatever controller is plugged in.

**And the roles are the mirror of what a telephone connects to.** This panel
registers A2DP **SOURCE**, AVRCP **TARGET** and HID **HOST**. An iPhone is
itself an A2DP source looking for sinks, and it cannot be a HID device. Two
sources have nothing to say to each other, so even inside the pair window iOS
has no reason to list it.

**Both roles cannot run at once, and that is structural rather than a
Kconfig.** Read in ESP-IDF v5.5.5 rather than assumed --
`btc_av.c`'s `btc_a2d_src_init()` and `btc_a2d_sink_init()` both call
`btc_av_init(service_id)`, and that function does its work only
`if (btc_av_cb.sm_handle == NULL)`. One control block, one `service_id`, one
state machine: whichever role is initialised second falls through to
`av_init_fail` and returns `BT_STATUS_FAIL`.

**The sink code is already in the binary**, which is the useful half.
`bt_target.h` sets `BTC_AV_SINK_INCLUDED` and `BTC_AV_SRC_INCLUDED` together
off `UC_BT_A2DP_ENABLED`, so `audio: true` already compiles both. Making the
panel a Bluetooth speaker is `esp_a2d_sink_init()` instead of
`esp_a2d_source_init()`, a data callback, and a Class of Device saying
loudspeaker -- not a new dependency. **Not built**: asked what the iPhone
should do, the answer was *"pour l'instant je sais pas mais l'ajout du
Bluetooth 5 voir plus tard Bluetooth 6"*, so the role stays source and this
paragraph is the note for whoever picks it up.

### The log could not have settled it either way

    HCI version 6, LMP version 6, manufacturer 15

Three numbers, and the question being asked was whether this dongle is modern
enough for a telephone. `manufacturer 15` is Broadcom and `LMP version 6` is
Bluetooth 4.0, and nothing in that line says so. Worse, the event carries five
fields and this read three: **`hci_rev` and `lmp_subver` were thrown away**,
and those two are exactly what identifies a Realtek part and whether it is
patched -- so the line meant to say what the dongle is could not have said it.

It now names the maker, the Bluetooth release, and for a Realtek the part and
whether it is running its ROM. All three tables are transcribed rather than
remembered: the releases from the Core Specification's assigned numbers, the
makers from the SIG's company identifiers (15 Broadcom, 93 Realtek, 10
Qualcomm/CSR, 741 Espressif), and the parts from **`ic_id_table` in Linux's
`drivers/bluetooth/btrtl.c`**, extracted by script from the file rather than
typed.

**Matching that table IS the statement "no firmware is loaded"**, and that is
why it is the right table to carry. btrtl.c matches on the values a chip
reports while running its ROM, in order to choose the patch to download; after
a download the controller reports the firmware's own version and matches
nothing. So a hit is not a lookup, it is a diagnosis. The TP-Link UB500 is
`lmp_subver 0x8761, hci_rev 0x000B, hci_ver 10` -- **RTL8761BU, Bluetooth
5.1**, not the 5.0 on the box.

`HCI_RTL_READ_ROM_VERSION = 0xFC6D` is sent only to a Realtek, from
`rtl_read_rom_version()` in that same driver: no parameters, status and one
version byte.

### What is NOT done, and why the loader was not written

Linux uploads `rtl8761bu_fw.bin` + `rtl8761bu_config.bin` -- about 30 KB --
through vendor opcode `0xFC20`, 252 bytes at a time behind an index byte whose
top bit marks the last fragment (`rtl_download_firmware`, `RTL_FRAG_LEN 252`).
That is a bounded piece of work and it is **not written**, for two reasons and
only one of them is technical:

- **Nobody has measured what the ROM cannot do.** This file already records
  the TP-Link answering all thirty-three of Bluedroid's startup commands in
  ROM mode and bringing the host up. Writing a loader before knowing what it
  buys is the defensible-argument-instead-of-a-measurement shape this file
  records more often than any other. The line above is what settles it, in
  one flash, from the panel's own log.
- **The blob cannot be fetched from here.** `git.kernel.org` is refused by
  this container's proxy and the GitHub mirrors tried returned 404, so the
  firmware would have had to be invented -- which for a binary is silent
  corruption rather than a compile error.

`tools/bttest/identify.cpp` links the shipped `bluetooth_release()`,
`maker_name()` and `realtek_rom_part()`. Its Broadcom row is a measurement --
manufacturer 15 and LMP 6 are from the first panel log in this repository --
and the rest guards the transcription: a patched controller must match
nothing, a near miss on the revision must match nothing, both ends of the
table must survive, and a release past the end must be a null rather than a
read off the end of the array. **Reproduced against a shifted table before it
was believed**: dropping one row from the release list fails three cases,
including Bluetooth 6, which is the next thing that was asked for.

### And `keep_profile: off` leaves a folder nobody opens

A consequence of the sweep's own third guard, met the day after it shipped. A
panel with the option off still OWNS its folder name, so the sweep leaves it
alone -- right, because turning the option back on should find what was signed
into -- and `profile_for()` returns None, so nothing ever opens it again.
Somebody who turned the option off to save space would have watched a
gigabyte not move with no way to learn why. `sweep_profiles()` names that
state and gives both ways out, and `tools/checkprofiles.py` asserts the plain
line is NOT printed for a panel that does keep one.

## A failed page re-armed the backoff, so the panel paged without pause

**Reported as *"il n'arrive pas a apparailler"*, with a log whose useful half
is the part nobody was looking at:**

    04:37:43  nothing answered. Put the device in PAIRING mode
    04:37:46  asking the speaker to connect (no scan, by address)
    04:37:50  asking the speaker to connect (no scan, by address)
    04:37:53  :57   04:38:01  :05  :08  :12   (for ever)
    BT_HCI: hcif conn complete: hdl 0x1, st 0x4
    BT_BTC: BTA_AV_OPEN_EVT::FAILED status: 2

**`st 0x4` is Page Timeout**, so the remembered UGREEN is switched off, out of
range or in somebody's car -- that half is not a fault. What is a fault is the
CADENCE. The design in this file says 2 s growing to 60; the log says every
three or four seconds, and it never grows.

**Bluedroid reports a connection that never opened as a DISCONNECTED state**,
so every failed page ran `on_a2dp_closed()`, which put the clock back to its
shortest. The backoff could never get past one doubling: it reset itself on
its own failures. `on_hid_closed()` had the identical line.

This file already recorded the mechanism and drew a different conclusion from
it -- **"the clock is re-armed in five other places -- every disconnect event
in `a2dp.cpp` and `hid.cpp` puts it back"**, written while making `off` a flag
rather than a cleared clock. The sentence was correct and what it implied for
the backoff itself was never followed up.

**Why it matters is arithmetic rather than tidiness.** A page's own timeout is
**5.12 s** by default and the attempts were 3.5 s apart, so they OVERLAP: the
controller was paging essentially without pause. An inquiry and a page compete
for the same radio, the dongle's antenna is centimetres from the C6's, and the
one thing that household was trying to do was run an inquiry. The pairing scan
heard nothing at all -- not a device rejected for its class, nothing. The
picture wobbled with it: 25.4, 23.3, 27.0, 39.2, 18.0 fps in five consecutive
lines.

**The short interval belongs to a real disconnection and only that.** Its
reasoning is unchanged and still right -- a speaker just switched off is the
one most likely to come back in a moment -- and it says nothing about an
address that has not answered in a minute. `was_connected` is the guard, and
it is the same test the log line above it already used.

`tools/bttest/backoff.cpp` drives the SHIPPED `loop()` against a clock it can
move, answering every page the way a switched-off speaker does, and counts
what the panel really does over two minutes rather than reading a field.
`linkstubs.h`'s `millis()` became a variable for it -- it had been a constant
0, which is fine for anything that reads it once and useless for behaviour
that is entirely about time passing.

**Reproduced against the old code before the fix was believed**, and the
numbers are the report:

| | attempts in 120 s | first gap | last gap |
|---|---|---|---|
| as it was | **60** -- one every 2 s, for ever | 2 s | **2 s** |
| now | 5 -- at 2, 6, 14, 30 and 62 s | 4 s | 32 s |

And the case the guard must not break is in the same file: a speaker that
really went away is asked for again **2000 ms** later, unchanged.

**What is NOT diagnosed** is why the UGREEN did not answer -- that is a device
that is off, away, or connected to a car, and this file already records that a
connected speaker answers no inquiry. What the fix buys is that the panel is
no longer competing with its own paging while somebody tries to pair.

## The Realtek needs its firmware, and the panel is what proved it

**Reported as *"l'UGREEN n'est pas connecter ailleur il es juste a cote comme
la manette il n'arrive pas a ce connecter avec le tplink"*.** That is the
controlled experiment this file kept saying was one flash away, and it arrives
as one sentence: **same panel, same firmware, same room, two devices that both
worked on the Broadcom, and neither connects on the TP-Link.**

This file has carried the prediction since the TP-Link first ran: *"in ROM
mode, with nothing uploaded, it answered every one of Bluedroid's thirty-three
startup commands and brought the host up. **What the firmware buys is what
happens after that, which is untested.**"* It is tested now, and what it buys
is everything on the air. The ROM implements HCI, so enumeration, `esp_bt_dev_
get_address()` and the whole of Bluedroid's startup succeed -- and an inquiry
hears nothing and every page ends `st 0x4`, Page Timeout.

**The parse is in Python and the board only cuts bytes up.** `tools/rtlfw.py`
turns Realtek's two files into the image a controller is sent, at CODEGEN, and
`components/portall_bt/__init__.py` emits it as a C array. The format is a
header, a metadata table indexed off by one, a backwards walk through an
instruction stream for a project id, and a four-byte version splice -- four
places to be silently wrong, and silently wrong here is a dongle stuck
half-programmed on somebody else's panel. Python can be run against the real
file; C++ on a board cannot.

**And the arithmetic landed on the number this file already carried.** The
parser, written from `rtlbt_parse_firmware()` with no reference to it, gives
**30 210 bytes** for an RTL8761BU -- which is exactly what CLAUDE.md recorded
from Linux months earlier. 30204 of patch for ROM version 1, plus a config
file that turns out to be **six bytes**: a magic and a zero length.

Three details in the format that no reader would invent, all transcribed:

- **A chip reporting ROM version N takes the patch whose chip id is N + 1.**
- **The last four bytes of the patch are REPLACED** by the version out of the
  header. Not a checksum: the controller reports it back afterwards, which is
  how a patched dongle is told from one on its ROM.
- **The index byte counts 0, 1 ... 0x7f and then wraps to ONE**, because
  Linux's `index = j++; if (index == 0x7f) j = 1;` assigns before it resets.
  Zero appears exactly once in the whole stream.

**The last fragment is marked and may be EMPTY.** `frag_num = len / 252 + 1`
and the last length is `len % 252`, which is zero when the image divides
exactly -- and it is sent anyway, because the 0x80 in the index is what ends
the patch rather than the bytes. A tidier loop would have dropped it.

### A path into the household's config was the wrong place, and a panel said so

The first version took a PATH, on the reasoning that the blobs are Realtek's
and a household downloads them once. The user simply uploaded them to `yaml/`
instead -- the better answer -- and the next build said:

    Could not find file '/config/esphome/rtl8761bu_fw.bin'

`cv.file_` resolves against the CONFIG's directory. Their config is in
`/config/esphome/` and the files were in a repository ESPHome had cloned
somewhere else, so a relative path could never have met them. **This file's
most-recorded shape, for the sixth time**: a fix the reader cannot reach from
where they are standing has not been delivered.

**And `external_components` is what makes the right answer possible.** Read
rather than assumed: `_process_git_config` clones the repository and points at
`repo_dir / "components"`, then `loader.install_meta_finder()` -- it **copies
nothing and filters nothing**. So every file beside `__init__.py` is on disk
at build time for every user, and `Path(__file__).parent / "firmware"` always
finds it. A binary can travel inside an ESPHome component.

So the patch is carried in `components/portall_bt/firmware/` and built in **by
default**: nothing to download, no path to get right, and a household that
plugs in a Realtek dongle never has to learn that it needs a patch at all.
`firmware: none` gets the ~44 KB of flash back on a board that will only ever
see a Broadcom, which needs none; a path names a different chip's pair.

Proved by building the probe two ways and reading the generated C++: with **no
option at all** and the files nowhere near the config, `rtl_firmware_0[14054]`
and `rtl_firmware_1[30210]` are both emitted and wired; with `firmware: none`,
neither is.

### The licence is what made carrying them fine

Realtek's own licence permits **redistribution in binary form without
modification** provided the copyright notice travels with them, so
`LICENCE.rtlwifi_firmware.txt` and a `README.md` saying where they came from
sit in the same directory. They are unmodified; `rtlfw.py` prepares what a
dongle is SENT at build time and changes neither file.

Checked rather than assumed: both are **byte-for-byte identical** to
linux-firmware's, by sha256.

`git.kernel.org` is refused by this container's proxy and every GitHub mirror
tried returned 404; **gitlab.com/kernel-firmware/linux-firmware** is the one
that answers, and the licence lives at `LICENSES/LICENCE.rtlwifi_firmware.txt`
rather than at the root. Worth recording so the next session does not repeat
the search.

**And having them in the tree found a blind spot in two checkers.**
`cv.file_` resolves a relative path against the YAML's OWN directory, so
`firmware: rtl8761bu_fw.bin` is correct for a panel -- and `checkcodegen.py`
and `checkyaml.py` both stage a config into a temp directory and left its
siblings behind, so they called a perfectly good file broken. That is the
worst way round for a check to be wrong. `carry_siblings()` copies any
non-YAML file in the directory whose name appears in the text -- by NAME
rather than by parsing the config, because the names have to be known before
the config can be read at all, since it is the reading that fails without
them.

### What is tested, and the one thing that is not

`tools/checkrtlfw.py` builds files byte by byte to the format's own
description, so every expected answer is stated independently of the parser:
both patches found, the chip-id-minus-one rule, the version splice, the config
appended. Then eight refusals -- no signature, the newer RTBTCore format, a
truncated file, a bad config magic, a config whose length disagrees with
itself, a patch running off the end, a file naming no project. And against
**Realtek's own file** when `RTL_FW` points at it, where the 30 210 is the
assertion.

`tools/bttest/fragments.cpp` links the shipped `rtl_fragment_index()` and
checks it against **Linux's counter form written out separately** over a
thousand fragments -- two formulations of one rule agreeing, where copying the
formula into the test would have proved only that it can be copied. That is
why the index is a pure function of `i` rather than a counter carried through
the loop: a counter has nowhere for a test to look. **Reproduced against the
natural wrong reading** -- wrapping to zero instead of one -- where it parts
company at fragment 128 and fails four cases.

The codegen was read rather than trusted, and then checked rather than read:
with the repository's own files ESPHome emits `static const uint8_t
rtl_firmware_0[14054]` and `rtl_firmware_1[30210]`, both wired through
`add_realtek_firmware` -- and the emitted array is **byte-for-byte identical**
to what `rtlfw.py` produces from those files, compared element by element
rather than by length. A literal of thirty thousand numbers is exactly where a
transcription would go wrong invisibly.

**What is NOT tested is the download itself.** No C++ here has been compiled
by a real toolchain and nothing has sent a fragment to a controller. The loop
is a transcription; `g_hci_cmd` grew from 64 bytes to 256 to hold one, which
is the kind of change that is invisible until a fragment is silently cut
short. What settles it is one flash, and the log now says which dongle it is,
whether it is on its ROM, how many fragments went out, and what version it
reports afterwards.

## The firmware worked, and the "errors" left behind were a stream of silence

**Reported as *"il reconnais les peripherique ugreen et la manette nvdia
shield mais il indique dans les logs des erreur"*.** The first half closes the
Realtek thread: both devices connect on the TP-Link, which is what the patch
bought. The second half is a real finding wearing somebody else's log level.

    E BT_L2CAP: l2cab is_cong_cback_context      (about eight times a second)

**It is not an error, and Bluedroid's own comment says what it is.** Read
rather than guessed -- `stack/l2cap/l2c_link.c` at the line above it: *"If
this is called from uncongested callback context break recursive calling.
This LCB will be served when receiving number of completed packet event."*
And the other half, in `l2c_utils.c`: a channel that was congested and has
drained to half its quota gets the profile's un-congestion callback, with
`is_cong_cback_context` set around the call.

So each line is one full cycle: **the A2DP channel filled its transmit quota,
drained to half, was told it could send again, sent again from inside that
callback, and hit the recursion guard.** Eight times a second, on a dongle
also carrying a gamepad's reports. The message is harmless; what it reports is
a channel that is permanently congested.

**And the congestion was ours.** `on_a2dp_open` asks for `CHECK_SRC_RDY`,
whose acknowledgement asks for `START` -- and the file contained **zero**
occurrences of `SUSPEND`. Nothing ever stopped a stream. With nothing feeding
it, `fill_pcm` fills every request with silence, so from the moment a speaker
connected a panel encoded and transmitted 44.1 kHz stereo SBC of digital
nothing, for ever.

**That is this file's own fault in a new costume**, and the earlier one is
recorded a few sections up: *"Fifty blocks a second is the whole stream, and
the page was playing nothing -- so that is 93.8 KiB/s of digital silence, sent
for ever."* There the fix was `take()` dropping a block that is exactly zero.
Here the same blindness reached a different transport, and cost more than
bandwidth: it congested an L2CAP shared with a HID device.

`a2dp_idle_tick_()` suspends after `A2DP_IDLE_MS` of nothing being FED -- fed
rather than silent, because digital silence arriving from a resampler is the
household's pipeline and not this component's business. **Ten seconds rather
than the half second a mixer source defaults to**: restarting costs an AVDTP
round trip, so two announcements a moment apart must stay in one stream, and
what this is for is the stream that would otherwise never stop. A test tone is
exempt: somebody who asked for one wants it to keep playing.

**Asked ONCE, which is the part a first version got wrong.** `a2dp_playing_`
only moves when `ESP_A2D_AUDIO_STATE_EVT` comes back, so a tick keyed on it
alone asks on every turn of the loop until then -- four lines in the test's
own output before anybody looked, and on a stack that had stopped answering it
would be a command a second for ever. `a2dp_ctrl_asked_` is cleared where the
stack answers. The test asserts the COUNT rather than that it happened.

`tools/bttest/idle.cpp` drives the shipped `loop()` against a movable clock
with the media controls recorded. **Reproduced against the old behaviour** --
nothing ever suspending -- where three of its six cases fail, including
`a stream nobody feeds is suspended`. The two that still pass are the ones
that should: a fed stream is left alone, and a panel with nothing connected
asks the stack for nothing at all.

**Not measured on hardware**: whether those L2CAP lines stop is what the next
log says. The mechanism is read from Espressif's source and the cause is
demonstrated in the component, which is as far as this can go without a board.

## The firmware loads on real hardware, and the log then chose the next three

**A panel's own log, and the line this whole thread was for:**

    this is a RTL8761BU running its ROM -- no firmware patch has been loaded
      Realtek ROM version 1
      loading 30210 bytes of firmware into it, 120 fragments
      firmware loaded -- it now reports revision dfc6 subversion d922

**`dfc6 d922` is the proof, and it is not a coincidence.** The epatch header's
`fw_version` is `0xdfc6d922`, and the format's one transformation is that the
last four bytes of the patch are REPLACED by it. The controller reports it
back split across the two fields the ROM table is matched on -- so a
transcription written from `btrtl.c` and never run is confirmed by the chip
echoing the number the file carried. 120 fragments is `30210 / 252 + 1`.

Everything after it works: the Shield and the UGREEN both pair, `gamepad: up
/ down / left / right`, `A -- ok`, `B -- back`, and `gamepad: home -- back to
this panel's own page` followed by portall's own `Asked to go back`. The
remote chain is proved end to end on hardware for the first time.

**Three faults in the same log, and the first is the diagnostic paying off.**

### The cap was still too small, and the panel said by how much

    input device 0955:7214 described itself in 379 bytes and 339 fields,
    of which only 192 fit -- buttons past that one are not decoded

That line exists because the previous round's version said "and more than
this can hold", which is a number-free complaint about a number. It cost one
flash to turn into an exact figure. **384 now** -- a Report Count of N makes N
fields out of one item, so 379 bytes really do carry 339 of them: sticks at
sixteen bits, a battery, NVIDIA's own host-command reports. 24 bytes a field,
so 9.0 KiB of RAM against 4.5, which is worth saying because it is a board's
memory rather than a number in a file.

**And raising it broke the test, which was the ruler rather than the code.**
`descriptor.cpp` built its overrun fixture from 40 items -- 320 fields --
which stopped overrunning the moment the cap moved. The fixture is derived
from `HidReportMap::MAX_FIELDS` now, so the case cannot stop testing anything
when the number changes. This file already records that shape twice for
`checkarrows.py`; this is the third.

### The stream was suspended before the start it was meant to undo

Three consecutive lines, in the wrong order:

    speaker 46:E8:1C:8A:88:DD is connected
    nothing has been played for 10s, so the stream ... is suspended
    audio stream started

`pcm_fed_at_` begins at zero and a panel is two minutes into its uptime by the
time anybody pairs, so `at - pcm_fed_at_ > A2DP_IDLE_MS` was true the instant
a sink connected. **A clock that has never been set is not the same as one
that has expired**, and an uptime comparison cannot tell them apart unless
somebody sets it. `on_a2dp_open` starts it now.

Shipped one round earlier, in the change that added the idle suspend, and
invisible to every test in it because they all began at `g_now_ms = 0` where
the two states look alike. The new case starts the clock at two minutes,
which is the real one, and **fails against the shipped code**.

### A device does not have to terminate a fixed-width field

    name "TP-Link UB5A Adapter????????????????"

The specification says the 248-byte name is NUL-padded and this dongle pads it
with something else. Printing from the buffer and trusting a terminator is the
same assumption this component has already been caught by; it takes the
printable run and stops, bounded at the field's own width in case there is no
terminator at all.

### What the log settles about the silence, and what it does not

    4446032 bytes of silence were sent for want of anything to play

That is the starvation counter reporting honestly over about fifty seconds of
a stream that was up while the page was quiet -- roughly half of what
44.1 kHz stereo asks for. It is not a fault in itself; it is the cost the idle
suspend exists to bound, and the suspend fired correctly at the end of it.
Whether the `l2cab is_cong_cback_context` flood is gone is **not** settled by
this log: the stream here was suspended within seconds of connecting by the
clock bug above, so the congested case barely ran. The next log is what says.

## The arrows only ever worked where the SITE moved the focus

**Asked as a question rather than a report, which is what made it easy to
answer: *"sur youtube je peux bien naviguer mais sur netfix, orange tv,
jellyfin c'est compliquer il ya juste parfois le button up et down qui
fonctionne mais difficillement"*.**

That last clause is the whole diagnosis and the user wrote it without meaning
to. **A browser does not move focus between links with the arrows -- only Tab
does.** So on a site with no spatial navigation of its own the arrows fall
through to the browser's default, which is to SCROLL: up and down have
somewhere to go, left and right have nothing at all. "Sometimes up and down,
with difficulty" is not a flaky gamepad, it is a page being scrolled by
somebody trying to navigate it.

Everything upstream was already proved right by the same session: the Shield's
descriptor is read, `gamepad: up / down / left / right` decode once per press,
and YouTube's television interface works -- because `ytlr` implements spatial
navigation for itself. DOCS.md already carried the table saying so, with
`anything else | arrows scroll the page` as its last row. **The table was
correct and nobody had asked whether that row had to stay true.**

### One Chromium flag, and the measurement is what made it safe

`--enable-spatial-navigation`, measured on the shipped build against a grid of
links with no key handler -- which is the shape of a media site's rows:

| | focus moved | what the arrows did |
|---|---|---|
| as it was | **0 of 4** | up and down scrolled the page |
| with the flag | **3 of 4** | moved between tiles |

The fourth is not a failure and is worth keeping because it is what
"difficult" would look like if it ever came back: a neighbour that is **off
screen** is scrolled into view by the first press and taken by the second,
and the same press with that neighbour visible takes it on the first. Enter
still activates what is focused.

**The regression risk is the only thing that could have made this a bad trade,
and it is the half that was checked first.** The launcher swallows every arrow
before anything else -- itself a fix this file records under *"up cree des
probleme"* -- so if the flag ran INSTEAD of a page's own handler, the one page
that works today would stop. Measured against a page whose handler calls
`preventDefault()` and deliberately moves the OPPOSITE way, so which of the two
ran is readable rather than inferred: **the page won, with the flag exactly as
without it.** It is a fallback, not an override. The launcher, YouTube `/tv`
and Jellyfin in TV layout are untouched, and only the pages that do nothing
today gain anything.

### The check reads the flag off the shipped list

`tools/checkspatnav.py` imports `ha_send.BROWSER_ARGS` and launches with it,
rather than writing the flag out again -- so removing it from the sender fails
the check. A test carrying its own copy of the thing it checks can only ever
agree with itself, which is the shape this file has now recorded three times
for `descriptor.cpp` and `checkarrows.py`. **Reproduced before it was
believed**: with the flag taken out of `ha_send.py`, six of its ten cases fail,
and the one asserting `no arrow moves the focus at all` passes -- that case IS
the panel's report, kept rather than remembered.

### Two things this does NOT settle

- **Netflix, Orange TV and a Jellyfin server were never reached** -- there is
  no route to any of them from here. What is measured is the MECHANISM, on the
  browser the add-on ships, against a page built to their shape. Whether a
  particular site also fights the focus for its own reasons is one flash away.
- **Netflix has a second wall that has nothing to do with arrows**, already
  recorded above: it needs Widevine, only Chrome carries it, and Google
  publishes no arm64 Linux build. Navigation reaching its tiles does not make
  it play.

And Jellyfin keeps its own row in the table: the browser's fallback moves the
focus, and its TV layout is still the better setting, because that is also
what gives Back its meaning and lays the pages out for a remote at all. One
dropdown, in Jellyfin, per user -- `Settings > Display > Layout: TV`.

The general shape, for the seventh time in this file: **the row of a table
that says "this does not work" is a question nobody has asked yet.** It had
been true since the day it was written, it was documented honestly, and one
flag turned it over.

## "Un text sensor pour une telecommande" -- it existed, and it was unreadable

**Asked as *"je te propose que tu cree un text sensor pour une telecommande"*,
and when the two readings were put to them, answered in two words: *"son
apparaillage"*.** So: show me the remote's pairing.

**The entity already did that, and saying so was the first half of the
answer.** A Bluetooth remote pairs over **HID**, exactly as a gamepad does, so
the `input:` slot of `text_sensor: - platform: portall_bt` is a remote's slot
and there was nothing to build. Asking first was worth it: the other reading
-- a third `remote:` entry beside `speaker:` and `input:` -- would have meant
growing `Remembered`, and an ESPHome preference is found by a hash AND a size,
so every panel that has ever paired would have forgotten what it is paired to.
A question is cheaper than that.

**What it could not do is say WHICH device**, and that is the half worth
building. It published a bare MAC:

    A4:C1:38:9E:22:07 connected

On a panel carrying a gamepad AND a remote, that says nothing at all about
which of them came back -- which is exactly what somebody testing a remote
needs to read. It is now:

    Orange TV remote (A4:C1:38:9E:22:07) connected

The address stays beside the name deliberately: two remotes of one model share
a name, and the address is what a pair or forget acts on.

### The name is in RAM, and that is the design rather than a shortcut

`Remembered` is six bytes per role and is **not touched** -- see above for what
changing it would cost. So the name lives in RAM for as long as the panel is
up, and a panel that has just restarted shows the address alone until the
device connects. Two sources, reached differently:

- **At pairing it is free.** `ESP_BT_GAP_AUTH_CMPL_EVT` carries
  `device_name`, and it was already being LOGGED and thrown away -- the line
  `paired with ... "NVIDIA Controller v01.04"` has been in every pairing log
  this project has ever produced.
- **On a RECONNECT there is no pairing at all**, and `ESP_HIDH_OPEN_EVT`
  carries no name -- checked in the header rather than assumed: it has status,
  conn_status, is_orig, handle and bd_addr, and nothing else. So the name is
  asked for with **`esp_bt_gap_read_remote_name()`**, read out of ESP-IDF
  v5.5.5's `esp_gap_bt_api.h` along with the event and the struct it answers
  in, and copied into the stand-in header field for field.

**It is a Remote Name Request over a link that is already open, not an
inquiry** -- so it neither pages anybody nor sweeps the band, and does not take
the panel's Wi-Fi down the way a scan does. That is reasoned from what the
command is; it is not measured here.

### Three things that are defects if they are missing

- **The slot is chosen by ADDRESS, not by which event carried the name.** A
  pairing and a name request can each arrive for either role, and a device
  this panel does not remember has no slot and must not overwrite one in use.
- **A name is not trusted to be terminated or printable.** The printable run
  and no further, bounded at `MAX_REMOTE_NAME`. This component has already been
  caught by a TP-Link dongle padding its own 248-byte name field with
  something that was not a terminator -- `"TP-Link UB5A Adapter????????????????"`
  on a panel -- and the same care applies to somebody else's name.
- **Forgetting a device takes its name with it.** A stale name beside a NEW
  address reads as correct, which is worse than no name: it is the only one of
  the three that could mislead rather than merely look wrong.

`tools/bttest/naming.cpp` links the shipped `note_remote_name`,
`describe_role`, `forget_one` and `on_hid_open`. **Reproduced against the old
behaviour before it was believed**, both halves: with the name taken out of
`describe_role` six cases fail, and with the clear-on-forget removed the case
named `a different device in that slot does not inherit the old name` fails on
its own.

### And it found a comment I had stacked on top of another one

`MAX_FIELDS` carried TWO comment blocks -- the superseded 192 paragraph sitting
directly above the 384 one that replaced it, both written in this session. The
first read as current to anybody arriving at it. One block now, carrying what
both said. A stale comment is the shape this file records most often; leaving
two of them stacked is the version of it that reads as deliberate.

**What is NOT done.** No C++ here has been compiled by a real toolchain, and
**nothing has paired with an actual remote** -- the input slot has still never
held one. What a remote will really do is the one thing a panel settles: if
its Class of Device reports Audio/Video rather than Peripheral, which a device
carrying a microphone for voice search plausibly does, it would be sorted as a
speaker. The log already says which kind it heard and why, so that is one run
rather than a round trip.

## The press effect existed, and lasted 2.3 ms

**Reported as the link tiles having no effect *"comme un button lvgl"*.** The
natural reading is that nothing was written. `a.tile:active { border-color:
var(--accent); transform: scale(.985); }` had been in the stylesheet all
along, and measuring it is what turned the question over.

Replaying a tap the way the sender does -- `mouse.down()` then `mouse.up()`,
which is what `Injector` sends on a LIFT so that a drag can become a wheel
instead of a click -- and timing mousedown to mouseup inside the page:

| | |
|---|---|
| transform at mousedown | `matrix(0.985, 0, 0, 0.985, 0, 0)` |
| transform at mouseup | `none` |
| how long that lasted | **2.0, 2.1, 2.3, 2.5, 4.3 ms** |

**A frame at `--fps 25` is 40 ms, so the pressed state occupied 6% of one
frame interval.** The panel sees the page as JPEG rectangles at a frame rate;
a state shorter than a frame is one no frame can contain. It was not missing,
it was *unphotographable* -- and even caught, 1.5% of scale is 5 px on a
350 px tile, which nobody reads across a room.

So two independent faults, and the fix is one of each: `PRESS_JS` holds a
`.press` class for **200 ms**, and the look is made legible -- `scale(.96)`,
the accent border, and `var(--edge)` as the background, which is the accent at
30% against `--card`'s 14% and so needs no fourth colour in the palette.

200 is chosen against the rate the panel is really at during a press: a
contact lifts the limit to `urgent_fps` for two seconds, 30 by default, so it
is about six frames -- and still two on a link capped to 10. A timeout rather
than an animation, so it costs the two rectangles a press is worth and nothing
while the panel idles, which is the rule `HomeHint` already lives under.

`pointerdown` in the CAPTURE phase, because it is the unified path and fires
for a replayed contact and a real finger alike, and it is the first of the
four events a press produces. **Nothing calls preventDefault**: a listener
that swallowed the tap would turn a slow tile into a dead one, which is worse
than no effect, and there is a case asserting the tile still navigates.

### Four faults in the ruler, none in the code

`tools/checkpress.py` reads the pixels and the duration. Every failure it
reported on the way was its own:

- **`evaluate_handle` AWAITS a promise.** Starting the watcher that way
  blocked for its full 3 s timeout and the press happened afterwards, so every
  duration read -1. The promise is parked on `window` and collected after.
- **The PNG decoder assumed RGBA.** A PNG says which it is in IHDR, and
  reading four bytes out of a three-byte row lands on somebody else's channel
  -- every sample came back black.
- **50%/50% of a tile is where its NAME is.** The sample was a white glyph
  that does not change, on both sides. It is the top strip now, which is
  padding -- and deliberately at 12%, because the pressed tile's top edge sits
  at 2% of the unpressed box, so the point stays INSIDE the tile in both
  states. Sampling nearer the edge would have shown a difference that was the
  page showing through, which is CLAUDE.md's "sample against a background you
  chose" in a new costume.
- **`wait_for_load_state()` resolves at once when no navigation has started**,
  so it read the address before the click had gone anywhere.

**Reproduced against the reported state before it was believed**: with
`:active` alone and `scale(.985)` back, six of the seven cases fail and the
one that passes is the navigation, which always worked.

## And that was the LAUNCHER's press. Every other site still had none

**Reported one round later as *"jellyfin fait pareil que netflix le button ne
dispose aucun effect"*, and the GROUPING is the whole diagnosis.** Those two
were named and Home Assistant was not -- and the launcher's tiles are one
piece of markup, so a fault there would hit all four equally. Naming two of
them only makes sense if the effect is a property of the SITE.

**`:active` is tied to the button really being down, and this sender sends the
press and the release in the same instant.** `_finish()` did
`mouse.move / mouse.down / mouse.up`, back to back, in both of its branches --
so every page in the world saw a press of about one task. The launcher was
fixed by adding a class it holds for 200 ms; nothing could do that for
somebody else's stylesheet, because **no script can force another page's
`:active`.**

Measured through a real screencast, which is the only picture a panel gets,
pressing a plain button at the centre of a page:

| | frames showing the press |
|---|---|
| styles `:active` and nothing else -- Jellyfin, Netflix | **0 of 1** |
| animates its own feedback (a Material ripple) -- Home Assistant | **15 of 25** |

and the second row is **the same either way**, because an animation runs on
after the button is up. That is exactly why two of the four sites were
reported and two were not.

**The fix is to stop lying about the gesture.** `_press()` puts the button
down and `tick()` lets it go `PRESS_HOLD_S = 0.080` later -- deferred, never
slept on, because a blocking wait in the loop is the fault this file records
under the panel writer and it would have cost 80 ms of every tap's own
pipeline. A sweep found one compositor frame is technically enough (16 ms
works), and 80 is chosen against the thing that actually has to catch it: the
sender throws away the frame in hand after a press and asks for a fresh one,
and that frame is painted and encoded 20-40 ms later, so the button has to
still be down when it is. It is also what a real finger does.

**What it costs is that the click fires on mouseup, so the page acts 80 ms
later -- and that is the right way round rather than a price.** The panel now
shows the button going down at the moment it used to show nothing at all, and
what makes an interface feel quick is the FIRST acknowledgement, not the
completion. That is the whole of "comme un button lvgl". `--press-hold 0`
restores the old behaviour for anybody who disagrees.

Three things had to be got right and each is a defect if it is missing:

- **`handle()` releases before anything else**, whatever the clock says. A
  second contact dispatched on top of a button still held is a down-down no
  page can make sense of. Three taps must be three downs and three ups.
- **`tick()` is where the release happens**, for the reason the corner hold
  already lives there: the finger has gone, so there is nothing left to hang
  it on but the loop.
- **The focus look moved with it.** `keyboard.request_sync(0.0)` was taken the
  instant the tap was replayed, which used to be after the click; it is
  `request_sync(injector.press_hold)` now, or the keyboard would look at focus
  before the page had been clicked at all.

### checkpress.py held the click back for every case that looked at the press

`tools/checkpress.py` suppressed navigation with a capture-phase
`preventDefault` for each case that measured the pressed LOOK, and its one
case that let the click through only asserted that it navigated. So the
combination -- a press that is real, on a page that reacts -- had never been
measured, which is **"testing the two halves separately proved nothing about
the whole"** in its second costume in this file.

`tools/checkpresshold.py` drives the SHIPPED `Injector` (a straight-through
touch map, the real `handle()` and `tick()`) against both page shapes and
counts screencast frames. **The old behaviour is reached through the shipped
option** -- `press_hold=0` is what `--press-hold 0` gives -- so the
reproduction is the real code rather than a reverted copy, and it reads
`0 of 1` exactly as the panel did.

**Two faults in the ruler, both already named in this file.**
`0 of 0 frames` for a button flashing red is impossible, and the cause was an
**inline** `background` beating `#b:active` in the sheet -- the same
specificity fault as the keyboard's `hide()`. And the sample was taken at the
centre of the button, which is where its LABEL is: CLAUDE.md already records
`checkpress.py` reading a tile's white name in both states. A quarter down
now, and judged as "not at rest" rather than "equal to the pressed colour",
because an animation passes through every colour in between and only its first
frame is the one the fixture names.

## `stats: true` sat under a comment saying "all off"

**Reported in the same breath as the tiles: *"les journaux de addon je peux
pas les effacer"*.** The literal answer is that Home Assistant offers no way
to clear an add-on's log -- the Supervisor owns that buffer, an add-on only
writes to its own output, and restarting starts a new container without
erasing what came before. That is not ours to fix.

**What IS ours is how much went into it.** From `config.yaml`:

```yaml
  # All off. Turn one on only while something is already wrong: each
  # writes to the add-on's log and nothing else.
  debug:
    stats: true
```

The comment and the value are on adjacent lines and say opposite things. One
line every five seconds, per panel, is **17 280 lines a day** for one panel
and twice that for two -- so every line worth reading was buried, and a log
nobody wanted is a log nobody can get rid of. `false` now.

**It does not reach an existing install, and the note says so.** The
Supervisor writes these defaults only when an add-on is first installed; a
stored value is the household's from then on. So anybody already running it
has `stats: true` saved and has to turn it off themselves, which the changelog
and DOCS.md both say rather than leaving it to be discovered.

The shape is the one this file records more than any other, and this time both
halves were visible at once on screen: a correct statement of intent, and code
directly beneath it doing the opposite, with nobody re-asking the value
because re-reading the comment confirms the intent.

## The sticks did nothing because nothing read them

**Reported as *"les joystiks ne sont pas fonctionnel"*, with two links** --
`devmapal/nvidia-shield-controller-driver` and `stdll/shield-controller-ubuntu`.
Both were read, and **neither carries an axis table**, which is the useful
finding rather than a dead end: the first is four `ozwpan` kernel patches that
carry the controller's traffic over Wi-Fi, and the second is pairing and udev.
Each makes the Shield appear as an **ordinary HID device** and leaves the
layout to Linux's generic HID layer, reading the device's own report
descriptor -- which is exactly what this component already does.

So the cause was an absence, and it was one line of grep away:
`feed_hid_usage` handled the hat, the four separate d-pad bits, the Button
page and the Consumer page, and **Generic Desktop X, Y, Z and Rz fell off the
end of the function.** The walker decoded them correctly the whole time and
nothing was listening.

**bluepad32's Android parser is what says which axis is which**, and it is
already the source this file's button numbering comes from: `HID_USAGE_AXIS_X`
and `_Y` are the left stick, `_Z` and `_RZ` the right, and brake and throttle
are on the **Simulation** page rather than Generic Desktop.

### Rx and Ry are left out, and the guard is what makes that safe

Plenty of controllers put their TRIGGERS on Rx and Ry, so those two are not
mapped. But a rule that holds because of what most devices do is the
guess-dressed-as-a-recipe this file has paid for repeatedly, so the real
protection is a property rather than a list: **an axis is not steered with
until it has been seen near its own centre.** A stick reports its centre
constantly; a trigger rests at one END of its range for ever and never
reaches that line. A trigger declared on Z would otherwise read as fully
deflected from boot -- a direction nobody can let go of, on a panel whose
focus would sit against one edge of the grid and stay there.

### What comes from the descriptor and what is a judgement

Everything about the arithmetic is the device's: the centre is
`(logical_min + logical_max) / 2` and the deflection is a percentage of the
declared span, so a stick running 0..255 and one running -32768..32767 are
the same axis expressed twice. There is **no byte offset and no device id** in
this path, which is the rule the previous gamepad round was corrected into.

Two numbers are NOT transcriptions and the code says so, because nothing else
in that file is a judgement: **half travel to push, a third to let go.** The
specification fixes no deadzone -- it only says what the axis reports -- so
the numbers answer what this is for, which is moving between tiles across a
room. Half is a deliberate push rather than a thumb resting on the stick, and
the gap between the two is hysteresis: one threshold for both chatters a whole
list past somebody holding the stick near it.

**A stick is a position and everything else here is an edge**, so the edge is
made from the crossing. One push is one tile, exactly as the hat is, and the
thumb has to come back below the release threshold before it counts again.
No repeat: it would need a clock on this path, and nobody asked for one.

### The diagonal guard is written down as what it IS, not what it would be nice to claim

A diagonal fires the axis with the larger deflection, so one push moves one
tile. The first version of that comment said a perfect forty-five degrees
moves nothing -- **and the test proved it wrong before it shipped**: the axes
arrive as separate `feed_hid_usage` calls in descriptor order, so on a stick
slammed from dead centre to a perfect diagonal between two reports, whichever
axis is listed first wins by having seen the other still centred.

The comment says that now. It holds for a thumb, which ramps -- by the
crossing report the other axis is already on its way -- and the case it does
not cover is one no thumb produces. Reaching for it would mean holding every
axis back until the end of a report.

### Eleven of the fourteen cases fail against the shipped code

`tools/bttest/input.cpp` drives the shipped `feed_hid_keys` against the same
descriptor fixture the hat and the buttons already use -- which declares
X/Y/Z/Rz at 0..255 and has done since the descriptor round, so the test
needed no new fixture, only the reports. Streamed the way a controller sends
them: centred, then pushed.

Run in a worktree at HEAD: **11 failures**, every stick case among them. The
three that pass are the ones that should, and that is worth saying rather
than counting -- they assert that NOTHING happens (the trigger guard and the
reconnection), which is trivially true of code that reads no axis at all.

**No add-on bump**: this is `components/portall_bt/` only, which a panel gets
by flashing. `tools/checkaddon.py` is the arbiter and it agrees.

**What is NOT done.** No C++ here has been compiled by a real toolchain, and
**no stick has moved a real tile** -- the fixture is a descriptor built to the
specification, not the Shield's own. What the next flash settles is whether
that controller declares its sticks on the usages bluepad32 says it does; if
it puts them somewhere else, the log now says `gamepad: right (stick at
100%)` with the percentage, so a wrong axis is visible rather than silent.

Sources: ricardoquesada/bluepad32 `parser/uni_hid_parser_android.c`;
devmapal/nvidia-shield-controller-driver and stdll/shield-controller-ubuntu,
both read and both transport rather than HID mapping.

## One panel, four input devices -- and the descriptor is why it was work

**Asked in one line: *"j'ai une question si je dispose de plus peripherique
bluetooth que je voudrais le connecter comment les text_sensor alors qu'il que
que deux text_sensor"*, then *"fait la construction"*.** One slot was never a
design, it was the first version -- `Remembered` held one address for an input
device, so pairing a remote replaced the gamepad silently and the entity could
only ever name one of them.

**The bookkeeping half is the easy half and it is not where the fault would
have been.** `ESP_HIDH_DATA_IND_EVT` carries a **handle and no address at
all** -- checked in the header rather than assumed, which is the same reading
that once stopped three wrong lines in this component -- so a report has to be
routed to the device that sent it. Decoding one controller's report against
another's report descriptor is exactly the confidently-wrong answer the whole
descriptor path was built to stop giving, and with one shared `HidReportMap`
it is what would have happened the moment a second device connected.

So `InputDevice` carries everything that is true of ONE device: its address,
its name, whether it is open, the handle Bluedroid gave the link, its own
parsed map, and every piece of edge-detection state -- the hat position, the
button word, the four analog axes and the six keycodes still held. Two
gamepads sharing a button word is each one's press reading to the other as a
release, which is a fault nobody could diagnose from a sofa.

**`route_` returns -1 rather than guessing**, and that case is real: a device
that connects when all four slots are taken has no map, no hat and no button
word, so its bytes reach `on_hid_report` and `show_reports:` and become no
key. The alternative -- decoding it against the nearest slot -- is the fault
above in its purest form. `remember_hid_` says so out loud with the way out
named, because silently dropping one of the devices somebody already paired is
the quiet loss this component exists to avoid.

**The stored record is a NEW key, and the old one is kept as a mirror.** An
ESPHome preference is found by a hash AND a size, so growing `Remembered` to
hold four addresses would have made every panel that has ever paired forget
what it is paired to -- the speaker included. `RememberedInputs` is its own
record under `portall_bt_inputs`; `load_remembered_` carries the old single
address across once when the new record is absent; and `save_inputs_` keeps
`Remembered.hid` pointing at the first slot so a firmware rolled back to the
one-device build still finds a device rather than paging an address whose key
has since been removed.

**The map is allocated when a descriptor first arrives, never before.** One is
9 KiB -- 384 fields at 24 bytes -- so four inline would be 37 KiB of a board's
internal RAM standing idle on every panel that pairs a speaker and nothing
else. A panel with one gamepad pays what it always did; a panel with none pays
2 KiB of staging buffer. Allocated once per slot and kept: a device that
reconnects reuses it, and freeing and retaking a block that size every eleven
seconds is how a heap gets fragmented.

**One device is paged per tick, taking turns.** A page's own timeout is 5.12 s
by default, so four sent together are four overlapping pages -- which is
precisely the shape this file already records under *"a failed page re-armed
the backoff"*, where a panel paged without pause and starved the inquiry
somebody was trying to pair with.

**The entity is a LIST on one line, not an entity per slot.** A slot is not
something a household chose -- it is wherever a device happened to land -- so
one card per slot would put three empty ones on the device page of every panel
with a single gamepad, which is the dark entity this component already had to
take out once. Bounded at 200 characters with what was left out said rather
than cut off. `portall_bt.forget_input` clears the whole list, and there is no
per-device action deliberately: one would need an index a household has no way
to read, which is the mechanism-instead-of-a-name this project has been
corrected into not building six times.

**The speaker stays at one and that is structural**, not a matching shortfall:
A2DP source is a single stream with one encoder, and a second would mean
mixing and lip-syncing two of them.

### The RUN pass had never opened the HID host

`tools/checkbt.py` compiles five configurations and RUNS the tests, and the
run pass defined `CONFIG_BT_BLUEDROID_ENABLED`, `CONFIG_BT_A2DP_ENABLE` and
`USE_SPEAKER` -- and **not `CONFIG_BT_HID_HOST_ENABLED`**. So
`hid_reconnect_()`, `forget_one`'s hang-up and the whole of the HID callback
were behind an `#ifdef` that no test binary had ever opened, on a component
whose newest work is all input. The syntax passes covered it; nothing ran it.
That is this file's most-recorded shape, in the tool written to catch it --
the same way `checkbt` once globbed `*.cpp` and never saw a platform.

### Two faults in the fixtures before either was in the code

- **A test that segfaults against the old code reports nothing.** The first
  version asked `bt->inputs_[1].map->uses_ids()` straight out, and with the
  routing reverted that slot has no map at all -- so the reproduction died
  before printing a line instead of naming the case. Asked null-safely now.
- **The same direction twice cannot fail.** The case proving a slot-less
  device is not decoded against somebody else's map sent the report the
  routed device had just sent, so a misroute would have been swallowed as
  "no change" and the check passed against the broken build. It sends the
  opposite direction now, and fails.

**Reproduced before it was believed**, in a copy with `route_` returning 0 --
which IS the old behaviour, one map and one button word for every device:
**seven of multi.cpp's cases fail**, including "the gamepad still moves up
after a keyboard connected beside it", which is the user-visible one.

`tools/bttest/descfixtures.h` holds the two report descriptors, one copy, for
the reason `linkstubs.h` gives about its own list. And the preferences
stand-in really STORES now, keyed by the hash AND the size the way the real
one is -- a stub whose `load()` always said "nothing saved" could only ever
exercise a fresh board, and the upgrade is the one moment a household can
silently lose what it paired.

**What is NOT done.** No C++ here has been compiled by a real toolchain, and
**no panel has had two input devices connected at once** -- whether Bluedroid
carries four HID links is its own limit and nothing here has tried it. A slot
that cannot connect reads "paired, away", which is the honest answer either
way. The Shield's own report descriptor has still never been seen: both
fixtures are built to the specification.

## Pair re-paired what the panel already had, so Forget was the only way in

**Reported after the four-slot work flashed: *"pour faire un appareillage
c'est contraignant je suis obliger d'appuis sur forget meme si il y a 0 paire
et essayer d'apparailler qui devient difficile"*, with a log that does the
whole thing in three seconds:**

    21:16:46  hanging up the input device 00:04:4B:93:A9:B2 first
    21:16:46  scanning for about 10 seconds -- put the device in pairing mode now
    21:16:46    heard 46:E8:1C:8A:88:DD  class 240404  major 4 minor 1
    21:16:46    that is a speaker -- stopping the scan and pairing with it
    21:16:47  paired with 46:E8:1C:8A:88:DD "UGREEN-90748"
    21:16:48  asking 00:04:4B:93:A9:B2 to connect (no scan, by address)
    21:16:49  pairing finished: a device is connected.

**Read forwards that is a success, and every line of it is the fault.**
Pressing Pair hung up the gamepad somebody was holding, ran a ten-second
inquiry that took this panel's own Wi-Fi down with it, walked off to the
speaker that had been working all along, re-paired it, and announced that a
device is connected. The new device never got a turn.

**The scan stops at the FIRST device of a wanted kind, and the devices
quickest to answer are the ones already in the room and already paired.** So
a household wanting to add a remote had exactly one way through: Forget
everything first, which is what was being reported -- and pressing Forget
when the entity reads none is somebody working around this without knowing
what they are working around.

**Pair means ADD now.** `heard_device()` passes over any address this panel
already remembers and names the Forget button that would replace it. That is
the whole fix for the report, and everything else follows from it.

**And the hang-up went with it, which is the other half of "contraignant".**
`pair()` dropped every link before scanning, on a reason this file records
and which is real: *an inquiry cannot find a device that is already connected
to this panel*. True -- and it only ever mattered for re-pairing THAT device,
which is now not something Pair does at all. A device already here is skipped
whether it is connected or not, so dropping it buys nothing and costs the
music, the gamepad, and a reconnection afterwards. `forget()` still hangs up
first; that is the case the original fix was really written for and it is
untouched.

**The fix would have shipped a false success, and the test is what caught
it.** `pair_report_tick_()` asked `a2dp_open_ || any_input_open_()` -- which
was a fair question only because pair() had just hung everything up. With
nothing hung up, the speaker that had been playing all along answers it, so
every failed pairing would have ended "a device is connected". It asks about
the device THIS RUN reached for now (`pair_took_` + `pair_target_`), and has
a third answer besides: found and did not connect, which is a different next
step from heard-nothing and from already-have-it.

### The decision lived where no test could reach it

The sorting -- take it, skip it, or say which option is off -- was the body of
`gap_cb`, a `static` function in hid.cpp. A test includes `portall_bt.cpp` and
links `hid.cpp` as a second translation unit, so that function was not
reachable from any of them: **the one thing pressing Pair does had never been
driven by a check.** It is `PortallBT::heard_device(addr, cod, name)` now, and
gap_cb is an extractor that pulls two fields out of the property list and
hands them over.

That is why the reproduction could not be built against the shipped commit at
all -- there was no seam to call. The old behaviour is reverted through the
new seam instead (the skip disabled, `drop_links_()` back in `pair()`, the
report asking the loose question), where **ten of pairing.cpp's cases fail**,
including "pairing leaves the connected speaker alone" and "and it does not
call the speaker that was already here a success".

**One fault was in the test and not in the code**, as usual: the case proving
a new remote is taken did not clear `g_hid_connects` first, so it was really
asserting that the step before it had connected to nothing. It failed against
the old code for the right reason by accident, which is not a reason. Cleared,
and it asserts the remote's own address.

**What is NOT settled.** No panel has run this. The log will say
`this panel already has that speaker -- skipping it` where it used to pair,
and `pairing finished: ... answered the scan but has not connected` is a line
nothing has ever printed. And one line of the reported log is still
unexplained: `hcif conn complete: hdl 0x4, st 0x4` -- a Page Timeout seven
seconds after everything had connected, from a page this component did not
make, since both reconnect paths return while their device is open.

## Forget answered about the RECORD while the device went on playing

**Reported as two things in one line: *"quant j'apparais un device Bluetooth
et que je veux appareiller un autre device il deconnecte celui qui etait
apparaille ensuite il ya des problemes sur Forget pour deconnecter le device
ou speaker"*.** The first half was the round before this one. The second half
is three separate faults, and the audit found a fourth nobody had reported.

**`a2dp_open_` and `InputDevice::open` are this component's OPINION of a
link; the stack's state is the fact — and Forget was gated on the opinion.**

- **"Nothing to forget" was said at a device that was still connected.** The
  record and the link can disagree, and every way they do is ordinary: a
  blanket forget clears the record while the disconnection is still in
  flight, a controller connects when all four slots are full and is never
  remembered at all. In each case pressing Forget printed *no speaker is
  remembered* and did nothing whatever, while the thing went on playing. From
  a household's side that button means "get rid of that thing", and the
  record is half of what that is. It hangs up what is CONNECTED now as well
  as what is remembered, and only says there is nothing to forget when both
  are empty.
- **The disconnect was skipped whenever the flag was false.** So a flag that
  lied by even a moment left a bond removed under a live ACL — which is
  *exactly* the "even Forget does not help" this file already records, still
  reachable by a different door. `drop_link_to_()` hangs up by address and
  asks nothing: a disconnect for something not connected costs an error code
  nobody reads, and a skipped one costs the device.
- **The speaker's live state was never cleared, and the input side's always
  was.** `forget_one(true)` and `forget()` both left `a2dp_open_` true and
  waited for the event, so in between the panel believed it still had a
  speaker — sound pushed into a ring for a device that had been forgotten,
  and a second press of the button answering about a record rather than about
  the link still up. Cleared where the record is, both paths, like the inputs.
- **A controller connected with every slot full could be hung up by no button
  in this component.** `forget_one(false)` and `drop_links_()` both walked
  `used && remembered`, and that device is `used` without being remembered.
  `used` alone now, with no key removed for one there was never a key for.

**And one pairing still hangs something up, deliberately.** A panel drives
ONE speaker — A2DP source is a single stream with one encoder and
`Remembered` has one slot — so a speaker taken by a scan is not being added
beside the old one, it is taking its place. Leaving the old link up asks the
stack for a second sink it cannot carry. Input devices have four slots and
are genuinely added, so nothing is dropped for them, and there is a case
asserting that a second controller does not hang up the first.

**Reproduced against the code the panel was running**, through the same
seams: **eight of pairing.cpp's cases fail**, including "a connected speaker
with no record is still hung up" and "and it is not answered with nothing to
forget".

**What is NOT settled.** No panel has run this. Whether a disconnect followed
immediately by a connect to a different sink is ordered the way it reads is
Bluedroid's business, not something a workstation can show — if the new
speaker does not come up on the first press, that ordering is the first place
to look.

## A scan that hears nothing had two causes and one sentence

**A panel log arrived with no prose, which is the right way to read it: what
it confirms and what it cannot settle are separate questions.** From
`[21:53:40]` to `[21:55:27]`, after the three pairing fixes above were
flashed.

**What it confirms, and all three are fixes landing on hardware:**

- **No `hanging up ...` line when Pair is pressed.** The Shield went on
  working across the scan, which is the whole of *"pour faire un appareillage
  c'est contraignant"*.
- **The Shield was TAKEN rather than skipped** -- nothing was remembered, so
  the new skip rule correctly did not apply -- and the report named it:
  `pairing finished: 00:04:4B:93:A9:B2 is connected.` That line is the one
  written to stop a connected speaker being called a success.
- **`379 bytes, 339 fields` with NO truncation warning.** `MAX_FIELDS = 384`
  was raised from 192 on the strength of a panel printing its own shortfall;
  this is that panel not printing it.

One line in it is not a fault and is worth recording so it is not chased:
`hcif disc complete: hdl 0x1, rsn 0x5` at boot is **Authentication Failure** --
a device paging this panel with a link key that was removed by a Forget. That
is Forget having worked.

**What it cannot settle is the two Pair presses that each ended
`scan finished, 0 device(s) heard`.** Two causes, and they need opposite next
steps:

- the UGREEN was not discoverable -- switched off, out of range, in the car,
  or simply not in pairing mode;
- or the live ACL to the just-paired Shield (`mode 2, intv 18`, so in SNIFF)
  cost the inquiry. An inquiry and an established link share one controller,
  and the dongle's antenna is centimetres from the C6's.

**And the second is a cost the fix above may have introduced.** `pair()` used
to hang everything up before scanning and no longer does, deliberately -- but
the reason that behaviour was there in the first place was a radio argument,
and dropping it means a scan now runs with whatever is connected still
connected. Said plainly rather than argued away: it is a candidate and there
is no board here to rule it out.

**So the log now says what the panel ITSELF was doing while it scanned.**
`note_links_for_scan_()` records the open links at the moment `pair()` runs --
at that moment, not when the report prints, because by then a device may have
come or gone and the question is about the scan -- and the delayed report
names them, or says the radio was free. Two runs of that line settle it:
nothing connected and still nothing heard is the device; something connected
and the same silence, with a later run that hears it once Forget has cleared
the link, is ours.

It goes in the DELAYED report rather than beside the scan, for the reason
`pair_report_tick_()` exists at all: every line a pairing produces is written
into a Wi-Fi link the inquiry has just taken down.

**And there is currently no clean household route to scan with the radio
free.** Forget is the only button that hangs anything up: `set_bt_enabled(false)`
drops the links but `pair()` is refused while Bluetooth is off, and switching
it back on re-pages within two seconds. That is not fixed here -- it is
written down because it is the next thing to build if the line above says the
link is what costs the scan.

Reproduced against the shipped report before the fix was believed: four of
the five new cases fail, and the fifth is the negative one -- *and does not
invent a device to blame* -- which passes trivially against code that prints
nothing at all. **Not measured on hardware**: whether a free radio changes the
count is what the next log says.

Still unexplained from the log before this one: `hcif conn complete: hdl 0x4,
st 0x4` -- a Page Timeout seven seconds after everything had connected, from a
page this component did not make, since both reconnect paths return while
their device is open.

### SETTLED by a panel, and it was the device -- not the radio and not the fix

**The controlled experiment ran and both pairings worked, twenty seconds
apart.** A Shield, then an UGREEN, on one panel:

    22:21:36  scanning for about 10 seconds
    22:21:40    heard 00:04:4B:93:A9:B2  class 000508  major 5 minor 2
    22:21:43  pairing finished: 00:04:4B:93:A9:B2 is connected.
    22:21:45  hcif mode change: hdl 0x2, mode 2, intv 18     <- the Shield, in SNIFF
    22:21:56  scanning for about 10 seconds
    22:22:00    heard 46:E8:1C:8A:88:DD  class 240404  major 4 minor 1
    22:22:03  pairing finished: 46:E8:1C:8A:88:DD is connected.

**The second scan ran with the Shield's ACL up and heard the speaker in four
seconds**, so the hypothesis this file had just recorded -- that a live link
was costing the inquiry, and that removing `drop_links_()` from `pair()` might
be why a previous scan heard nothing -- is **wrong**. The earlier silence was
the UGREEN not being discoverable, which was always the other candidate.

Worth keeping because of the shape: that hypothesis was reasonable, it was
about this session's own change, and it was stated as a candidate rather than
a cause precisely so a run could kill it. One run did. **A candidate named
honestly is cheap to retire; one argued into the file as a finding is not.**

**And the diagnostic built for it never printed**, because the case it names
did not arise. That is the right outcome and not a wasted change -- it costs
nothing on a scan that hears something, and the next silence is still the one
it exists for. Unproven in the field, deliberately said so.

Three other things that log confirms, all of them fixes landing:

- **No `hanging up ...` line anywhere.** Pairing the speaker left the Shield
  connected -- `hdl 0x2` for the controller, `hdl 0x3` for the speaker, both
  live. That is Pair MEANING ADD, on hardware.
- **`the speaker's buttons are connected`** -- the AVRCP target, on the car
  receiver whose own `unknown PSM: 23` asked for it.
- **`0955:7214 described itself: 379 bytes, 339 fields`** with no truncation
  warning, and `event 257 bytes, code 07` on the boot probe: the largest frame
  HCI defines, reassembled whole over a sixteen-byte endpoint.

`hcif disc complete: hdl 0x1, rsn 0x5` at boot is Authentication Failure
again -- a device paging with a link key a Forget removed -- and
`opcode=0xfc82, status= 01: Illegal Command` is the vendor command this dongle
does not carry. Both already recorded, neither a fault.

**What this log does NOT settle** is the `l2cab is_cong_cback_context` flood:
it ends about a second after `audio stream started`, so the idle suspend has
not had its ten seconds and the congested case has barely run.

## The telephone is the remote, and building it found a broken guard

**Asked after the Bluetooth thread ran out of devices: *"il sera interessant
que cette telecommande de l'iPhone qui commande une Apple TV fonctionne ?"*,
answered with *"fait le je serai surpris si ca fonctionne"*.** Two questions
in one, and only the second is buildable.

**The iPhone's Control Centre remote is not Bluetooth at all.** It speaks
Apple's **Companion Link** over the network -- Apple moved the widget off MRP
in iOS 13 -- and it is paired and encrypted the way HomeKit is: HAP, with
sequence-number nonces. `pyatv`, which is the reference for this whole area,
is a CLIENT: it commands an Apple TV, it does not pretend to be one. Making
that widget drive a panel would mean implementing the server side of an
undocumented, encrypted, pairing-authenticated protocol and advertising as an
Apple TV -- and being broken invisibly by the next tvOS. That is a line, not a
difficulty.

**So the answer is the shape this project keeps rediscovering: ask what the
household already has.** They have Home Assistant on the telephone, and this
panel already has the one thing a remote presses through -- `send_key()` and
`ask_home()`, proved on hardware the day before by a gamepad.

`remote: true` on `portall:` puts **seven button entities** on the panel's
device page: up, down, left, right, OK, back, home. The Home Assistant app on
any telephone is then a remote, iPhone included, and a dashboard card lays
them out as a cross. It is the **seventh** time the named per-thing setting
beat a mechanism here, and it is the user's own pattern: no id to read, no
usage to look up, no seven blocks to write.

Two devices make it worth having BESIDE a paired controller rather than
instead of one, and both were established in the same conversation: **an
iPhone cannot be a Bluetooth HID device** (a panel is the host; a telephone
is on the other side of that), and **an Xbox controller on firmware v5 or
later speaks BLE**, which this component does not host -- so it answers no
inquiry and reads exactly like a controller that is switched off. Sony's
DualSense and DualShock 4, and Xbox firmware v3/v4, are BR/EDR and are
reachable today. Read off bluepad32's own table rather than remembered.

**The entities are built in the VALIDATOR, not in to_code**, and that is the
`keys:` lesson collected on rather than re-learnt: esphome names an anonymous
id by walking the VALIDATED config, so an entity invented at codegen has
nothing to resolve it and `get_variable` waits for ever -- the "Circular
dependency detected!" a first attempt at `keys:` shipped. `esphome`'s own
`demo` component is the precedent for the rest: `AUTO_LOAD = ["button"]` and
`button.new_button(conf)` from a component's own `to_code`, with no platform
block.

Home is a FLAG on the button rather than a reserved usage. A sentinel inside
the usage would be one number meaning two things, which is what the gamepad
path was corrected out of once already.

### And `portall.key` has been uncompilable without a speaker for releases

Found while adding the buttons, not by reading the diff: **`KeyAction` and
`HomeAction` sat inside `#ifdef USE_SPEAKER`.** They drive `send_key()` and
`ask_home()`, which are declared unguarded -- so a board with `keys: true`
and no `speaker:` has the methods, does not have the classes, and fails to
build on somebody else's panel with "KeyAction is not a member of portall".

It arrived by an anchored edit that put two classes inside a guard meant for a
third, and it took `SetVolumeAction`'s own comment away from `SetVolumeAction`
with it -- the replacement hazard this file already records four times, in its
worst form, because the file still parses and still compiles **here**.

**Nothing could see it.** `esphome config` never compiles C++; `checkguards.py`
reads TinyUSB symbols and nothing else; and **every example in `yaml/` that
carries a `portall:` block also carries a speaker**, which is the whole reason
it went unnoticed. That last fact is the one worth keeping: a configuration no
example exercises is a configuration nothing checks.

`tools/checkactions.py` is the check, and **the rule is derived rather than
listed** -- a hand-written table of which class may sit under which macro is
the pair-of-constants failure this repository keeps paying for. It reads the
guards above each class and the guards above each `Portall` method that class
calls through `this->parent_->`, and requires them EQUAL. A class more guarded
than its method is unreachable; less guarded is a compile error; both are the
same fault mirrored.

One guard is legitimate and is derived too: a class deriving from
`button::Button` genuinely needs whatever guards the include of that
component's header, or there is no base to derive from. So the allowed set is
the methods' guards plus the base classes' include guards, both read off the
same file. The first version did not know that and reported `RemoteButton`;
the answer was to derive the exception, not to write it down.

**Reproduced against the shipped header before the fix was believed**: run on
`git show HEAD:components/portall/portall.h` it names both classes and exits
1; on the corrected file it is clean.

### What is verified and what is not

Read off `generate_cpp_contents` at 2026.8.2 rather than from the validator's
opinion -- seven buttons, each parented and each carrying the right usage:

    new(portall_remotebutton_id) portall::RemoteButton();
    App.register_button(portall_remotebutton_id, "Remote up", ...)
    portall_remotebutton_id->set_parent(panel);
    portall_remotebutton_id->set_usage(7, 82);
    ...
    portall_remotebutton_id_7->set_home();

and **a panel that does not ask emits none at all** -- `tab5-portall-screen`
carries zero. A `portall:` with `remote: true` and **no speaker** was staged
and generated as well, since that is the configuration the guard bug broke and
no example covers it.

**No C++ here has been compiled by a real toolchain**, as always, and
**nothing has been pressed**: whether a button in Home Assistant moves a tile
on the glass is one flash away. The chain under it is the one a gamepad proved
on hardware the day before, which is the reason to expect it to work -- and
the reason it might not is the half that is new: seven entities registered
from a component's own `to_code` rather than from a platform block.

**No add-on bump**: `DOCS.md` is read by the Supervisor from the repository,
and `tools/checkaddon.py` agrees.

## The iPhone's own remote drives a panel, and the widget is not only for Apple TVs

**Asked three times, and the third time plainly: *"c'etait pas plus de
l'integre dans le code seulement ? et surtout beaucoup plus simple ... ce
qu'il veulent est d'appareille et desappareiller et que cela fonctionne"*.**
That is the **eighth** time this user has asked for a named thing over a
mechanism somebody has to operate, after the quality, the user agent, the
frame limit, the stylesheet, the token and `keys: true`. They have been right
every time.

**And the round before it was a wrong answer stated confidently.** Asked
whether the iPhone's Apple TV remote could drive a panel, the reply was that
the Control Centre widget speaks Apple's **Companion Link** -- HAP-paired,
encrypted, `pyatv` is a client only -- so it would mean impersonating an
Apple TV, which is a line rather than a difficulty. Every fact in that is
true **about Apple TVs**, and it is not the whole list of what the widget
drives.

**It also drives any HomeKit accessory of the Television category.** Read in
Home Assistant's own source rather than remembered:

  * `accessories.py:350-353` -- a `media_player` with `device_class: tv`
    becomes a `TelevisionMediaPlayer`.
  * `type_remotes.py:115-121` -- that accessory always carries the
    **RemoteKey** characteristic.
  * `type_media_players.py:381-385` -- an unhandled key is fired onto the
    event bus, with the comment *"Unhandled keys can be handled by listening
    to the event bus"*.
  * and their documentation says it in words: *"Entities exposed as
    TelevisionMediaPlayer ... are controllable within the Apple Remote widget
    in Control Center."*

So the protocol is open, it is HAP rather than Companion Link, and nothing
has to pretend to be an Apple TV. The shape of the earlier mistake is one
this file already names twice: **a correct fact with a wrong conclusion
attached**, because re-reading it confirms the fact and never re-asks the
conclusion.

### The first answer was a recipe, which is the thing that was objected to

What was offered next was the route above as a *configuration*: a `universal`
media player with `device_class: tv`, a HomeKit bridge in **accessory mode**
(their docs require it -- a Television may not be bridged), and an
automation on `homekit_tv_remote_key_pressed` mapping thirteen key names onto
seven buttons. Every line of it is right and it is three things to configure,
in two files, for one result. That is a mechanism, and the objection landed
on it immediately.

`homekit: true` is the whole of it now.

### It belongs to the ADD-ON, and that does not contradict the Bluetooth ruling

This file already records a firm no to putting Bluetooth controls in the
add-on: there is no control channel on the udisp socket, and the ESPHome API
already publishes that state correctly, so two places to look would be worse
than one. None of that applies here, and the reason is where the key has to
land. **A press has to reach the thing that renders the page**, which is the
sender -- not the board. Routing it through the panel would be a new message
type, a board round trip and a longer path to the same browser.

So `portall/homekit.py` serves one accessory per panel with **HAP-python**,
which is the library Home Assistant's own bridge is built on. Two readings
decided its shape and both are somebody else's shipped code rather than a
guess:

- **One accessory, one driver, one port, one pairing code PER PANEL.** A
  Television may not be bridged; HA's own documentation says `mode` must be
  `accessory` with a single entity.
- **A Television with NO input sources at all is a shipped configuration.**
  `RemoteInputSelectAccessory` returns before adding a single InputSource
  when the player cannot select one. A panel has no inputs, so an accessory
  that is a remote and nothing else is ordinary rather than a stunt.

**pyhap does not persist the pincode**, which is not obvious and bites in
exactly the minutes it can: its encoder writes the MAC, the keys, the paired
clients and the config version, and nothing else, so a code left to it is a
fresh one after every restart -- while somebody is reading the old one off
the screen. `read_pin()` keeps it under `/data/homekit`.

**The accessory outlives the sender, so it holds a `Remote` and not a pipe.**
A sender is a child process that dies and comes back on a 5 -> 120 s backoff;
an accessory is paired once and lives for ever. A version that grabbed
`process.stdin` would write into a closed pipe from the first crash onward --
from the sofa, a remote that simply stopped. There is a case for it, and the
naive version is written out in the test so that it FAILS.

**`host_network: true` is the cost, and it is unconditional.** mDNS does not
cross from the Supervisor's private network to the LAN and the iPhone must
reach the accessory's port directly -- which is why HA's own HomeKit works
this way. The launcher still binds `127.0.0.1` only, so nothing new is
exposed; what changes is that **port 8099 must be free on the host**.

### The check read its expectation out of the table it was checking

The first version of `tools/checkhomekit.py` asserted
`seen == homekit.ACTIONS[name]` -- and then the reproduction run was clean.
Folding **Exit onto Escape**, which is the exact fault this file already
records (EXIT and ROOT_MENU both meaning Escape, and no button going home),
**passed every case**, because the test and the code were reading the same
dictionary.

`WANT` is now stated in the test as what each HomeKit key is *for*, and three
cases hang off it: each key through the real characteristic, that Back and
the TV button do different things, and that the shipped table equals it.
Against the broken copy it is **five failures** instead of one.

That is this file's most-recorded shape appearing inside the tool written to
prevent it, for the fourth time -- after `checkbt` globbing `*.cpp`,
`importcheck` never opening a platform, and `descriptor.cpp` building its
fixture from the constant it was testing.

### What is verified

Thirty-two cases, offline, with the real pyhap: the accessory is built by the
shipped code and every key is pressed through the real **RemoteKey**
characteristic, the line that comes out goes down a real `Remote` into the
shipped `Control`, and what the send loop is handed is read out of the far
end rather than asserted at each one. Also: a press after the sender
restarted reaches the NEW one, a press with no sender is dropped and said
once, the pairing code survives a restart and differs per panel, and
`homekit: true` really puts `--control` on the sender's line -- which
`checkaddon`'s generic sweep passes over, since the flag is deliberately not
named after the option.

**What is NOT verified**: nothing has been paired. There is no iPhone, no
Home app, no mDNS and no LAN here, so whether the widget lists a panel is one
restart away. The one thing that could not be found written down anywhere is
**which button of the widget sends `Exit` and which sends `Information`** --
so the log names each key the first time it arrives, which settles it from a
panel instead of from a guess.

Sources: home-assistant/core `homekit/accessories.py`, `type_remotes.py`,
`type_media_players.py`, `const.py`; home-assistant.io `homekit.markdown`;
HAP-python 5.0.0.

## The way home sat on a button the widget does not have

**Reported from a panel: *"le button retour surement le button home? quant je
suis dans n'importe quelle link"* -- with the control that makes it a
diagnosis rather than a complaint: the same action works from the paired
Shield and from `Remote home` in Home Assistant.** So `ask_home()`, the
sender's home branch and `open_page()` are all fine, and the fault is
upstream of them on the HomeKit path only.

**The sender was checked first and cleared.** Its home branch has no
condition on which page is open -- it calls `open_page(page, args)` and
prints `Home: back to <url>` -- so if `("home", True)` had ever reached the
loop the log would say so. It did not.

**And the cause is that `Exit` is a key the widget cannot send.** The iOS
Control Centre remote gives a HomeKit television exactly five buttons: the
pad, Select, Back, Play/Pause and **ⓘ**. `Exit` is in HomeKit's own RemoteKey
table, which is where it was taken from, and it is on no button of that
widget. So the one action a panel cannot do without was mapped somewhere
nobody could press.

That is **this file's most-recorded shape, for the eighth time**: a
capability that exists and is not reachable from where the reader is
standing has not been delivered. The invisible keyboard, the add-on option
that never reached `command_for()`, the command that was never named, the
command that had to be typed in a window nobody could find, the firmware
path under a config directory, the diagnostic that told somebody to press
Home and watch for a line that could never appear -- and now a remote action
on a phantom button.

**It was also flagged as unknown in this file and shipped anyway**, which is
the part worth keeping. The previous section ends: *"The one thing that
could not be found written down anywhere is which button of the widget sends
`Exit` and which sends `Information`"* -- and the answer to that was
fifteen minutes of searching, which is what this round spent. An unknown
named honestly in a comment is not the same as an unknown resolved, and a
mapping built on one should not have gone out without the search.

### Back goes home, Escape moves to ⓘ

The opposite split from the Bluetooth remote, and deliberately: **the two
devices do not have the same buttons.** An AVRCP remote has a Back AND a
Menu, so Back can stay "back within the page" and Menu can leave it. This
widget has Back and ⓘ.

Of the two, Back is the one somebody reaches for to leave a page, and on a
panel leaving the page IS the launcher. Escape can afford the obscure button
because it does **nothing at all** on nearly every page one of these shows --
this file's own table says so: Home Assistant has no use for it, the
launcher has none, and Jellyfin listens only in its TV layout. `Exit` stays
mapped to home beside Back, since another HomeKit controller may send it and
a television's Exit means the same thing.

### The check now carries the widget's real button set

`WIDGET` in `tools/checkhomekit.py` is the eight key names that widget can
produce, and the case is **"a button the widget REALLY HAS goes home"** --
plus one asserting the pad and Select are not it. That states the FINDING
rather than the fix, so the same mistake cannot be made again by moving home
onto another key HomeKit defines and iOS never sends.

Reproduced against the shipped mapping before the fix was believed: **six
failures**, that case among them, which is the panel's report exactly.

**What is NOT verified**: no iPhone here, so which of Back and ⓘ the
household finds more natural is theirs to say. What is verified is that the
key the widget sends for Back now reaches `("home", True)` and comes out of
the real `Control` at the far end.

Sources: nikf86/homekit-tv-remote README (the widget's five buttons);
home-assistant.io `homekit.markdown`; HAP-python 5.0.0 RemoteKey ValidValues.

## The widget's volume and mute had nothing to send to

**Reported as the one inactive control: *"il y un seul button qui es inactif
c'est le son et mute"*.** The accessory was a `Television` service and nothing
else, and a HomeKit television carries no volume of its own: the iPhone's
volume buttons and the widget's mute go to a **`TelevisionSpeaker` linked to
it**. With none, iOS had nowhere to send them.

Built exactly as Home Assistant's `TelevisionMediaPlayer` builds it for a
player that can step and mute but not set a level (`type_media_players.py`,
read in their tree): Name, Active, VolumeControlType 2, VolumeSelector, and
Mute, which is the service's required characteristic. VolumeSelector carries
only up (0) or down (1), so the LEVEL is kept by the add-on -- ten steps,
squared into a gain because loudness is heard on a log scale -- and what
crosses to the sender is `volume <gain>` on the control channel.

**The gain is applied to the page's sound in the sender**, in `PageAudio.take()`,
which is the one place every block passes. Muted is gain 0 and is counted as
silence and not sent, so a muted panel costs the network nothing and a
Bluetooth speaker behind it suspends on its own ten-second clock. It sits in
front of the board's own volume, which is unchanged; the two multiply.

**A volume is a setting and a key is a moment**, and `run.Remote` treats them
differently for that reason: a key pressed while the sender is down is
dropped, while the last volume is re-sent to every sender that starts, or a
restart would put a panel somebody had turned down back at full.

pyhap bumps the accessory's `c#` when its services change
(`State.set_accessories_hash`), so an already paired iPhone should read the new
speaker without re-pairing. **Not verified on an iPhone** -- nothing here can
pair. What is verified, in `tools/checkhomekit.py`: the speaker built by the
real pyhap and linked, both characteristics pressed through it, the gain
across the real pipe into the real `Control`, the re-send after a restart,
and `PageAudio` scaling samples (negatives included) and sending nothing when
muted. Run against the previous code, the speaker case fails.

**The panel's sound is mono end to end, and a YAML saying stereo changes
nothing.** The capture is one channel (`AUDIO_CHANNELS = 1`), the wire carries
one, and `portall_bt`'s speaker writes each sample to both sides of the A2DP
stream. `channel: stereo` on an I2S speaker governs that speaker only, and on
the Guition that speaker is not the one the page's sound reaches.

**Stereo is now a test, on at ONE place: `stereo: true` in a panel's
Advanced** (4.19.5, asked as *"ont test la stereo si ont vois que ce n'est pas
bon ont l'enleve"* -- so it is built to be removed). The sender captures two
channels and says so in the PCM header's **width** field, the one geometry
field a sound block had never used; mono leaves it 0, so a mono header is
byte for byte what it always was. The board reads it and re-tells its speaker
the shape when it changes (stopping it first if it is playing the other one,
dropping what arrives meanwhile), with its block buffer allocated once for two
channels -- so the board has no setting to keep in step, the pair-of-constants
fault this file keeps recording. What the YAML needs is two channels at the
END of its chain: `num_channels: 2` on a `portall_bt` speaker, which the
resampler and the mixer above it inherit (read off the resolved config at
2026.8.2: mixer, resampler and all three mixer sources come out 2).

**An old board fed stereo plays it at half speed**: it ignores width. That is
why the documentation says flash first, and it is the one hazard the design
cannot remove from this end.

`tools/checkstereo.py` checks the wire (mono bytes unchanged against a header
packed by hand), the capture's block size, the volume on interleaved samples,
the add-on's command line, and **the shipped `audio.cpp` compiled with g++**
against a stand-in for the rest of the class (`tools/audiotest/`, whose
constants are copied out of the real `portall.h` each run) and switched
mono -> stereo -> mono through a recording speaker. `portall.cpp`'s parser
change is NOT compiled -- the real class needs the JPEG decoder and the PPA.
**Heard, and correct**: on the Guition through a Bluetooth headset, left and
right each on their own side. The board's log shows every link of it --
`The page's sound is stereo now`, `First audio: ... 2 channel, played 1920 at
a time`, both ring buffers at 19200 (100 ms of stereo), and `Playing 44100 Hz,
16 bit, 2 channel over Bluetooth`. So the option stays. `--stats` cannot say
mono from stereo: `sound 50/s` counts 20 ms blocks either way.

A `Dropped a block: the speaker is not draining (100 times so far)` came about
eleven minutes into that first stereo run. The count is since boot and each
block is 10 ms, so it is a second of sound over the whole run. It is NOT
diagnosed yet: stereo doubles the resampler's work and the bytes on the wire,
and it could just as well be one Wi-Fi stall. What settles it is how fast the
count climbs, against the same page in mono.

To remove it: the option, `--stereo`, and `channels` on `build_audio_header`
and `PageAudio`; the board can keep reading width, which costs nothing.

## Three presses a row, and the row that said so was already in this file

**Reported from panels other people are running: *"le deplacement up, down,
droite et gauche n'est pas fluide dans les link, vous etes obliger de vous
reprendre plusieurs fois ... comparer a youtube qui est tres fluide"*.** The
comparison is the diagnosis: YouTube's television interface moves its own
focus and never falls through to the browser, so a fault that lives in the
fallback cannot show there.

**And this file predicted it, in the section that shipped the fallback.** The
spatial-navigation round recorded *"the fourth is not a failure and is worth
keeping because it is what 'difficult' would look like if it ever came back:
a neighbour that is off screen is scrolled into view by the first press and
taken by the second"*. That was written as a curiosity. It is the report.

**Measured rather than assumed, and it is worse than the note said.** On the
shipped browser, a media-site grid -- rows of links, no key handler of its
own, taller than the panel:

| | presses per row |
|---|---|
| `--enable-spatial-navigation` alone | **3** |
| this round | **1** |

and sideways, where nothing has to scroll, both cost one. So the number in
the old note was a guess at its own measurement: Chromium scrolls a fixed
amount per press and takes two before the next row is considered near enough
to take. **A behaviour named honestly in a comment is not a behaviour
measured**, which is the same lesson as `Exit` one section above -- twice in
two days, both times a thing this file had written down and not followed up.

### SPATNAV_JS, and the dangerous half is the standing down

Moving the focus is the launcher's own rule, already proved in a browser:
`along + across * 3`, so straight ahead beats near-and-sideways, shadow roots
walked because a Home Assistant dashboard is nothing else, and the first
arrow on a page with nothing focused chooses an end rather than moving.

What had to be decided is when NOT to act, because a page that navigates for
itself must keep every arrow. Two gates, answering different failures:

- **`defaultPrevented`.** A page that handles an arrow nearly always swallows
  it, because otherwise the browser scrolls underneath its own navigation and
  the page jumps -- which is not a guess, it is the fault this project
  shipped in its OWN launcher and had reported back as *"up cree des
  probleme"*.
- **And a look on the next turn**, for a page that moves focus without
  swallowing: if the focus went somewhere neither we nor the browser put it,
  the script stands down for the life of the document. That costs one press,
  once. It cannot be decided synchronously -- the page has not moved anything
  yet when the handler runs -- so it is a `setTimeout(..., 0)` and the page
  wins the first press.

A text field keeps its arrows, and where there is nothing in that direction
nothing is swallowed, so the browser is still free to scroll.

**The flag stays.** It is now a fallback below a fallback: where this script
declines, Chromium's own behaviour is what a panel gets, which is right in
every case where declining was the correct answer.

### The reproduction runs through the shipped file

`tools/checknav.py --without` leaves the script out and runs the same
fixtures, so the reported fault is reproduced against what is shipped rather
than against a reverted copy -- the `--press-hold 0` pattern this file
already records. It reads **`[1, 1, 3, 3, 3, 3]`**, which is the panel's
report as a list of integers.

Ten cases, and the strongest is the last: the **launcher itself**, served by
`launcher.start()` with the script loaded over it. That page swallows every
arrow, so it is the real instance of gate one, on this project's own page
rather than on a fixture built to shape.

**What is NOT verified**: Netflix, Orange TV and a Jellyfin server were never
reached -- there is no route to any of them from here. What is measured is
the mechanism, on the browser the add-on ships, against pages built to their
shape. And a site that moves focus without swallowing costs one press before
the stand-down, which nothing here has seen in the wild.

### The fallback moved twice, and the question that found it was about Netflix

**Reported as the iPhone's remote driving YouTube and not Netflix or
Jellyfin.** Jellyfin was settled from its own source and confirmed on the
panel: `keyboardNavigation.js` returns early on every arrow unless
`layoutManager.tv`, so Settings > Display > Layout: TV is the whole fix. A
television user agent would switch the layout too (`isTv()` is "the UA
contains tv"), and must NOT be used for it: `browserDeviceProfile.js` then
answers yes for HEVC, AC-3 and E-AC-3 on `browser.tizen`, which Chrome does
not decode, so Jellyfin would direct-play files the browser cannot play.

**Netflix has no web television interface** -- its TV UI is a native app --
so the fallback is all it gets, and nothing here can reach Netflix to see
why it does nothing. `NavNotes` makes the page say it: `Arrows on <host>:`
once per (site, kind) -- moved, handled, stopped (a capture listener that
learns whether the key reached the fallback at all), none (with the count of
tabindex -1 elements, the roving-tabindex shape), and stood aside.

**Writing its test found a real fault, and the old test had passed over it.**
A page that moves the focus in the capture phase WITHOUT preventDefault made
the fallback start again from where the page had put it: **two rows per
press**. Reproduced against the shipped script on a grid whose own handler
moves one row: one press from `t0_0` landed on `t2_0`; now `t1_0`. The fix is
the focus recorded as each key sets out -- if the page moved it on the way
down, the page's move is the move. The `RUDE` fixture had only ever been
pressed at the bottom of a list, where a second move finds nothing.

Two faults in the ruler on the way, as usual: "stopped" fired on two quick
presses because the check kept only the LAST key reached (a WeakSet now), and
on a page that stops the key Chromium's own `--enable-spatial-navigation`
still moves the focus, so "nothing moved" was the wrong expectation.

**Not verified on Netflix.** The next log from a panel says which case it is.

## The last 30 to 40% is the whole panel, and four candidates are dead

**Reported after the arrow fix: *"c'est un peut mieux sur tous les link a vu
de toucher et manette, et telecommande iphone il manque environ 30 a 40% de
fluidite"*.** The useful half is **au toucher** -- a finger uses no arrows, no
`SPATNAV_JS` and no key at all, so whatever is left is shared by all three
input paths, which means it is the picture path.

Six measurements at 800x1280 with the shipped code, on a page that never stops
moving. Four of them kill a candidate, which is most of the value.

**A moving page is whole panels, always.** 127 of 128 pictures during a swipe,
62 of 62, 66 of 66 -- whatever the quality. A scroll moves every pixel, so
every tile differs and the rule gives up on rectangles on the first test. A
dashboard-shaped whole panel at quality 80 is **130 KiB**, measured, which
matches what this file already records. So a swipe asks for 130 KiB times
twenty-odd a second -- **2.6 to 3.2 MB/s** -- against a busiest window ever
recorded on one of these panels of 2.5 MB/s.

**The frame limit does nothing at all when something is short.** Against a
model panel drained by a token bucket at 1500 KiB/s, driving the SHIPPED
`Screencast`:

| | pictures reaching the panel | median gap |
|---|---|---|
| `--fps 30`, quality 80 | 7.3/s | 134 ms |
| `--fps 15`, quality 80 | **7.5/s** | **134 ms** |
| `--fps 30`, quality 40 | **12.8/s** | 77 ms |
| the same at 6000 KiB/s, quality 80 | **18.0/s** | 50 ms |

The first two rows are the finding: the writer's own back-pressure already
paces the loop, and the gaps are already even -- median 134, nine in ten under
149, worst 152. So `fps:` on a link is not the lever here, and neither is the
erratic-versus-steady argument this file makes about video: **there was
nothing erratic to fix.** The rate is bytes-per-whole-panel divided by what
the far end takes, and the last two rows are the only two ways to move it.

**And that is exactly why the answer is not "lower the quality".** Halving the
bytes nearly doubles the rate *when the WIRE is what is short*, which is what
a token bucket models. A panel's own earlier log says the wire was not what
was short: 918 KiB/s at `panel wait 42%` against 1429 KiB/s at 1% on the same
panel minutes apart -- fewer bytes, more waiting. That is the BOARD's fixed
cost per whole panel, one decode of the entire screen and one write of the
entire screen, which no quality touches. It is also why *"malgre mis en
qualite 20 c'est saccade"* was a true report of a real experiment.

So `--stats` during one swipe is the line that decides it, and nothing here
can stand in for it: `panel wait` high with KiB/s near 2.5 MB/s is the wire,
where `quality:` on that link doubles the rate; `panel wait` high with KiB/s
well under it is the board, where only a smaller picture helps.

**And a smaller picture is available on a rotated panel after all, which this
file implies it is not.** `_validate_render_size` refuses `render_width:`
together with `rotation:` -- but that is the BOARD's rotation. A panel whose
turn is done by the sender (`rotate:` on its add-on entry, `--rotate` on the
sender) has `rotation: 0` and is not refused. Measured here, per picture at
800x1280: **16.1 ms of sender work and 344 KiB against 3.3 ms and 72.7 KiB**
at 400x640, which is the same 5x this file records. Read off the validator
rather than run -- there is no esphome in this container this time.

### The three candidates that were wrong, in the order they were tried

Each is written down because each is the natural next guess.

- **This session's own arrow script.** `SPATNAV_JS` walks the document and
  every shadow root on every press, in the renderer, at the moment a fresh
  frame is wanted. Measured inside the page: **0.5 ms median** on a grid,
  **1.9 ms** on four hundred nested shadow roots, worst 3.4. Not it, and worth
  knowing before the next person suspects it.
- **The wheel pacing, which is worse the other way round.** A dispatch blocks
  until Chromium acknowledges it on its next display frame -- 16.6 ms, already
  recorded here -- and `WHEEL_MIN_INTERVAL_S = 0.030` permits 33 a second, so
  it looked like up to half of every second spent on input. Raising it makes
  the panel WORSE: 25.5 pictures/s at 30 ms against 15.6 at 80 ms, with the
  median gap going from 36 ms to 71. Fewer, larger wheels move the page in
  jumps, and a page that changes less often hands over fewer frames. The
  distance scrolled is the same at every pacing (4962, 4939, 4927, 4962 px),
  which is the summing being exact. **30 ms is right and the reasoning that
  said otherwise was arithmetic without a measurement**, which is the shape
  this file records more than any other.
- **The rotation, in the sender.** Their page is opened at 1280x800 for an
  800x1280 panel, so every frame is transposed before anything else touches
  it, and nothing here had ever measured that half -- only the board's PPA.
  **0.86 ms a picture**, against a 7.65 ms decode. Not it either.

**A fourth was a fault in the harness and is worth recording as one.** Reusing
one `Screencast` across configurations read **0.5 pictures/s** at `--fps 15`,
which looked exactly like a stall in the shipped pacing. It is the object's
own carried state -- its `lead` and its unacknowledged frames -- not the
sender's. A fresh page and a fresh `Screencast` per run reads 7.5. **A
measurement that accuses shipped code should be re-run against a clean
fixture before it is believed**, which is the ruler-before-the-code lesson in
its fifth costume here.

One thing seen on the way and NOT acted on: on a short link the browser still
paints and encodes **22 frames a second for 7.3 delivered**. The acknowledgement
gate asks `pending is None`, which goes true the instant a picture is taken,
so the browser is let go while the panel is still taking the last one. Gating
it on the writer as well would cut two thirds of the host's paint and encode
during motion and change nothing the panel sees. Identified, not built, and
nobody asked for it.

## `--fps 30` delivered 23.6, and the panel's own stats are what said so

**Reported with `stats` on during a swipe down Home Assistant's entity list:**

    13.0 pictures/s, 27.6 made/s, 16.4 rectangles/s, 15 whole, 1050.9 KiB/s,
    panel wait 18%, 4 skipped, worst gap 133 ms, worst turn 67 ms, loop 80.4 Hz

and a dozen lines like it, `panel wait` between 0 and 18% in every one. That
retired the hypothesis offered just before it -- that pictures pile up between
the server and the glass (writer, kernel send buffer, the board's 512000-byte
window, `frame_buffers: 4`). A panel that the writer waits on less than a fifth
of the time is a panel WAITING for the server, not one with a queue in front
of it. The theory was read out of the code and was plausible; one log line
from the house killed it, which is the order this file keeps asking for. The
long gaps in those logs (480, 742, 1773 ms) all sit in windows where a page
was being opened, not scrolled.

**So the server was measured against a panel that never makes it wait**, with
a page that scrolls itself and the shipped `ha_send.py`: **23.6 pictures a
second at `--fps 30`**, 37 at `--fps 60`. The limit was not being reached. The
per-picture work is not why -- decode 5.9 ms, diff 1.4, whole-panel encode 3
at 800x1280 -- and neither is the browser, whose `lead` read 13 to 23 ms.

**It is the schedule.** `last_send = started` restarted the interval from
whichever turn of the loop NOTICED the picture, and the loop turns every
fifteen milliseconds or so -- so every interval was rounded up to the next
turn, 33 ms becoming about 42. `last_send += limit` keeps the schedule, and a
turn late by more than a whole interval starts a fresh one, or a page that
stood still would come back as a burst. **23.6 -> 30.0 at 30, 13.9 -> 15.0 at
15**, worst gap 65-75 ms against 66-94 before; against a panel draining 1500
KiB/s the two are the same within noise, because there the link decides.

The loss scales with how long a turn is, so it is larger on a slower machine
than here -- the panel above ran its loop at 80 Hz where this ran at 65, with
turns up to 112 ms. Whether this is all of that panel's missing third is what
its next stats line says; the fix is real either way.

`tools/checkpacing.py` runs any copy of the sender against that fake panel and
fails below nine tenths of the limit. Against the previous release it reads
23.5 at 30 and fails; at 15 the old rule loses only one picture a second and
passes, which is why 30 is the case that matters.

**The panel's next line, after 4.19.2, swiping the same Home Assistant pages:**

    24.6 pictures/s, 35.1 made/s, 71.9 rectangles/s, 8 whole, 457.0 KiB/s,
    panel wait 7%, 5 skipped, worst gap 90 ms, worst turn 90 ms, loop 92.1 Hz

against 13 to 17 pictures a second and worst gaps of 480-742 ms in the windows
before. Not a controlled A/B -- a household swiping at a different moment --
but it is the direction and roughly the size the fixture predicted, with the
panel still waiting on the server less than a tenth of the time. The one long
gap in the same log (863 ms) sits in the window where the ESPHome dashboard
was being OPENED, which is a page loading rather than the picture path.

**And then the limit itself was what was left: `URGENT_FPS` 30 -> 45.** The
same household reported a swipe still short by about a quarter, *"au doigt et
visuel"*. With the schedule fixed, 30 meant 30 -- and 30 was the ceiling the
urgent window imposed on every swipe. Measured against the same panel that
never makes the sender wait: **30.0 pictures a second at 30, 36-39 at 45,
35-38 at 60**, so above 45 the browser and the machine decide and a bigger
number buys nothing; against a panel draining 1500 KiB/s, 30 and 45 are the
same within noise. The loop runs near 50 Hz at 45 rather than 65, which is
still as fast as a finger reports. `fps:` on a link still caps it, so a film
link is untouched. Measured through `--fps` rather than through a real press:
the urgent window only swaps which interval the same gate uses.

**`urgent_fps` cannot be set from the add-on, and DOCS.md said it could.** It
claimed the Configuration page's YAML view "will pass any key straight
through to the sender". The Supervisor's own `options.py` drops every option
the schema does not declare, top level and inside list items alike -- the
same `continue` this file already records under `launchers:`. So the default
is the only value a household ever gets, which is why the default had to move
rather than a setting being offered.

## The C6 already gives a panel Bluetooth, and ESPHome already wires it

**Proposed after the dongle turned out to be the obstacle: *"je confronte a un
autre probleme que j'avais pas pense celui de la clef bluetooth que peu de
personne dispose donc j'ai penser a ceci que toute personne dispose avec le
c6"*, pointing at esp-idf's `components/bt`.** The instinct is right and the
answer is better than it looks: **there is nothing to build, and a panel can
have it today in two YAML blocks.**

**ESP-Hosted carries HCI over the SDIO link that is already there.** From
Espressif's own `docs/features/bluetooth.md`: *"The host runs the Bluetooth
application and host stack; the co-processor runs the Bluetooth controller and
radio. ESP-Hosted carries HCI packets between them."* So a P4 runs the host
stack and the C6 is the controller -- which is **the identical architecture
this component already built for the dongle**, with a different wire.

Identical is not a figure of speech. Their `docs/design/bluetooth.md`: *"The
BlueDroid glue implements an HCI driver ops struct
(`esp_bluedroid_hci_driver_operations_t`: send / check_send_available /
register_host_callback) and attaches it to BlueDroid with
`esp_bluedroid_attach_hci_driver()`."* That is line for line what
`portall_bt.cpp` does over CherryUSB. Nine faults were paid for on that path
and the shape of it was right.

**And ESPHome does the whole thing already.** `esp32_ble/__init__.py`, present
as far back as 2026.6.5 and in the 2026.8.2 a panel is built with:

```python
    if "esp32_hosted" in full_config:
        add_idf_sdkconfig_option("CONFIG_BT_CLASSIC_ENABLED", False)
        add_idf_sdkconfig_option("CONFIG_BT_BLE_ENABLED", True)
        add_idf_sdkconfig_option("CONFIG_BT_BLUEDROID_ENABLED", True)
        add_idf_sdkconfig_option("CONFIG_BT_CONTROLLER_DISABLED", True)
        add_idf_sdkconfig_option("CONFIG_ESP_HOSTED_ENABLE_BT_BLUEDROID", True)
        add_idf_sdkconfig_option("CONFIG_ESP_HOSTED_BLUEDROID_HCI_VHCI", True)
```

**Every panel YAML in this repository already carries an `esp32_hosted:`
block**, because that is how the C6 does the Wi-Fi. So the switch is thrown
already and what is missing is only the consumer. ESPHome tests it on this
exact host -- `tests/components/bluetooth_proxy/test.esp32-p4-idf.yaml` is a
P4 with a C6 over SDIO -- and adding

```yaml
esp32_ble_tracker:

bluetooth_proxy:
  active: true
```

to `yaml/ws-wired-portall.yaml` **validates at 2026.8.2**, checked here rather
than assumed.

**What it is NOT is Classic, and that is the whole limit.** Espressif's own
line: *"Classic Bluetooth requires an ESP32 as the co-processor. All other ESP
chips provide BLE only."* So the two things this component has actually proved
on hardware -- an A2DP car receiver and a Classic HID gamepad -- **cannot move
to the C6 at all**. The dongle is not replaced; it is narrowed to what needs
Classic.

The honest split, and it is worth having in one place:

| | C6, no dongle | dongle |
|---|---|---|
| Bluetooth proxy for Home Assistant, BLE sensors | **yes, today, two YAML blocks** | -- |
| a BLE remote or an Xbox controller on firmware v5+ | possible, see below | no (they answer no inquiry) |
| a Bluetooth SPEAKER (A2DP) | **never** | yes, proved |
| a Shield, a DualSense, a DualShock 4 | **never** | yes, proved |

**The proxy is the one that changes what a panel IS**, and it is the part
nobody has to be sold: a screen in every room that is already powered and
already on the network is exactly where a household wants Bluetooth coverage,
and it costs this project no code and no maintenance.

### Nothing refused the pair, and the sdkconfig says it out loud

`tab5-bt-probe.yaml` with `host_stack: bluedroid` and a `bluetooth_proxy:`
block beside it **validated `ok`** before this was written. Two things would
then attach a transport to one Bluedroid, and neither could report it -- a
stack that is up and talks to nothing.

The sdkconfig makes it concrete rather than theoretical: this component sets
`CONFIG_BT_CLASSIC_ENABLED` **True** and esp32_ble sets the same key **False**,
and `add_idf_sdkconfig_option` is `CORE.data[...][name] = value` -- a plain
dict assignment, so the build takes whichever `to_code` ran last and says
nothing. **`_one_controller_each` in a second costume**: two settings that must
agree, with nothing comparing them.

`_one_bluedroid_transport` refuses it and names which half to keep, by what the
panel needs rather than by which is tidier. `FINAL_VALIDATE_SCHEMA` is
`_final_validate`, which runs both -- a schema carries one. Keyed on
**`esp32_ble` in the full config**, because that is AUTO_LOADed by
`esp32_ble_tracker`, `bluetooth_proxy`, `ble_client` and `esp32_ble_server`
alike, so one test catches every way a YAML asks for it.

**Reproduced before it was believed**: both blocks in one file read `ok` at
2026.8.2 against the shipped validator and are refused after, while
`bluetooth_proxy` alone, `tab5-bt-probe.yaml` and
`yaml/tab5-portall-bluetooth.yaml` all still pass.

### A BLE gamepad is buildable, and the component for it is already in IDF

**`components/esp_hid` is built into ESP-IDF** -- not a managed component --
and it carries `src/ble_hidh.c` beside `src/bt_hidh.c`. Its host API is
transport-agnostic: `esp_hidh_init()`, then `ESP_HIDH_OPEN_EVENT` and
`ESP_HIDH_INPUT_EVENT` carrying a report. That is the same report-plus-
descriptor shape `hid_descriptor.cpp` and `keys.cpp` already decode, so a BLE
remote or an Xbox controller on firmware v5 or later -- which this file already
records as unreachable over Classic -- would reuse the decoding entirely and
need a new door rather than a new parser.

**Identified, not built**, and two things are unknown: whether `esp_hid`'s BLE
host coexists with ESPHome's own `esp32_ble` on one Bluedroid (both want the
GATT client), and whether the one panel this would be for has such a device.
Nobody has asked for it yet.

Sources: espressif/esp-hosted-mcu `docs/features/bluetooth.md` and
`docs/design/bluetooth.md`; esphome `esp32_ble/__init__.py`,
`esp32_hosted/__init__.py` (which pins `espressif/esp_hosted` 2.12.12) and
`tests/components/bluetooth_proxy/test.esp32-p4-idf.yaml`; espressif/esp-idf
`components/esp_hid`.

## One launcher server, one page per panel

**Reported as two halves of one fault: *"chaque panels n'a pas de profile
dedie il reprend tous les links du premier panels"*, and four columns that
*"correspond tres bien pour un grand display de 1280x800 mais pas pour un 7"
de 1024x600"*.** Both were true by construction: `launcher.start()` served ONE
page at one address, `run.py` handed that same address to every panel whose
url was `launcher`, and `columns:` lived on the launcher rather than on the
screen. The links were never "the first panel's" -- they sat at the top of the
form above every panel, which reads as belonging to the first one.

**The four columns were not a preference, they were broken words.** Measured
in the shipped browser at 1024x600 with eight ordinary service names: four
across gives 220 px tiles and breaks **seven** names inside the word --
`Assistant, Jellyfin, YouTube, Netflix, Orange, Proxmox, Unraid` -- where
three across gives 298 px and breaks none. Auto (`0`) gives two across there,
which is why somebody set four in the first place.

**Still one server, and the panel is named in the address.**
`launcher.address_for(name)` is `http://127.0.0.1:8099/?panel=<name>`, and the
handler picks the links and the column count from that. A QUERY rather than a
path on purpose: every other thing the page asks for -- `/weather.json`,
`/wallpaper`, `/slides.json`, `/report` -- is absolute and must stay the same
for all of them. The corner gesture brings a panel home to its own `url:`,
which is now that address, so it stays its own without anything else knowing.

- **SUPERSEDED in 4.19.0 by `launchers:` -- see below.** A panel took
  `links:` (4.18.0), a
  comma-separated string of link NAMES, matched without case or surrounding
  spaces. **Empty means every link**, which is what every configuration
  written before it means -- no migration, and a string rather than a list
  because a stored value the schema no longer accepts stops the add-on, which
  this file records under the launcher's size scale. The house's order is
  kept rather than the order typed, so groups stay together.
- **A page asked for with no panel shows every link.** Showing a link somebody
  did not want is recoverable from the glass; hiding one they did is not.
- **A name that matches no link is said at startup, with the links that do
  exist**, because from the glass a typo there is a tile that simply never
  appears.
- **A panel takes `columns:` under `advanced:`**, overriding `launcher:
  columns:` for that screen. It is read by `run.py` for the launcher and
  reaches no sender -- `tools/checkaddon.py` lists it in `ITS_OWN` with that
  reason, and `tools/checkpanels.py` checks `--columns` is not on the line.

**The rewrite that sends a panel to the launcher is `route_to_launcher()`
now**, lifted out of `main()` so the address each panel is really handed can
be checked without starting a sender -- the same reason `heard_device()` was
lifted out of `gap_cb`: a decision no test can reach has never been tested.

`tools/checkpanels.py` drives the add-on's own path -- an options file in the
grouped form the Supervisor writes, `load_panels()`, `start_launcher()`,
`route_to_launcher()` -- then opens each panel's address at its own size in a
real browser and reads the tiles off the page. **Its ruler was wrong first**,
the usual way: it computed "across" as tiles divided by rows, which calls four
tiles three across two across. The widest row is the number.

**Reproduced against the previous commit** in a `git worktree`: one address
for every panel, the kitchen's page byte-identical to the living room's,
carrying the living room's link and four across.

### The choice was on the wrong side, and it worked

**4.17.0 put it on the LINK -- `panels: salon, cuisine` -- and it was reported
straight back: *"chaque panel ne dispose pas c'est propre link choisi
independamment"*.** Nothing was broken: `tools/checkpanels.py` passed against
it, each panel was handed its own address and showed its own tiles. The fault
was where the setting sat. Somebody changing what ONE screen shows goes to
that screen's entry, and with the choice on the links they had to visit every
link and add or remove the panel's name there -- which does not read as each
panel choosing at all.

That is the **ninth** time this user has asked for a setting on the thing it
belongs to, after the quality, the user agent and the frame limit per link,
the stylesheet, the token, `keys: true`, `homekit: true` and `columns:` per
panel. The shape to learn is narrower than "ask first": **when a setting
relates two things, put it on the one the reader is looking at when they want
to change it.** Here that is the panel -- columns had just been put there, for
the same reason, in the same release.

So `links:` on a panel, and `panels:` on a link is **removed rather than kept
beside it**: two places to choose the same thing, which could disagree, is
worse than one, and it was one day old. Removing it is safe, and that was
read rather than assumed -- Supervisor `supervisor/apps/options.py`,
`_nested_validate_dict`: an unknown key inside a list item is logged
(`Unknown option ...`) and skipped, exactly like one at the top level. What
cannot be carried across is the value itself, which the Supervisor drops
before run.py sees it; the changelog says to move it.

**Reproduced against 4.17.0** in a `git worktree` with the new
`checkpanels.py`: a panel's `links:` meant nothing there, both panels showed
all six links, and eight cases fail; all pass on 4.18.0.

**And 4.18.0 was reported as not working, from a configuration with no
`links:` in either panel.** Run through `load_panels()`, `start_launcher()`
and `route_to_launcher()` exactly as pasted -- token replaced -- both panels
got their own address and every link, which is what an empty choice means;
with the two lines added, 6 and 4 links, 4 and 3 across. Nothing was broken.
The field is OPTIONAL, and the Supervisor's form does not show an optional
field nobody has set, so the feature existed and could not be found. This
file's most-recorded shape, again: a capability not reachable from where the
reader stands has not been delivered. 4.18.1 names the line to add in the
startup log, on every panel that chooses nothing.

### And the ask was never a choice among shared links -- 4.19.0

**Said a third time, and plainly: *"panels salon a sont propre links button
collonne toutes option prevus et il faut que panels 2 dispose de ces propre
link button collonne toutes option prevus independant qui ne correspond pas au
premier panel"*.** Two rounds were spent choosing WHICH of one shared list a
panel shows -- from the link side in 4.17, from the panel side in 4.18 -- and
the ask all along was a launcher PER PANEL: its own links, its own columns,
its own clock and wallpaper and everything else. The misreading survived
because each round fixed the sentence that was literally in front of it
("each panel its own links chosen") rather than the thing the sentence
described. The top of the form reads as belonging to the first panel, which
is why every phrasing of it mentioned "the first panel".

**It could not go inside a panel's entry, and the Supervisor's source is why,
not taste.** `supervisor/apps/options.py`, `_check_missing_options`: a key is
optional only when its schema is a plain `"...?"` string. A LIST or a GROUP
nested in a panel entry is compulsory, so adding `links: [...]` or a
`launcher:` group to panels would have stopped every saved configuration on
this update with "Missing option". A new TOP-LEVEL list does not, because
`App.options` merges the add-on's defaults under the user's (`_OPTIONS_MERGER`,
dicts merged, lists replaced), and the default is `launchers: []`. Read in the
Supervisor's own tree, not remembered. The same reason makes an entry FLAT
(`clock_size`, not `clock: {size}`): a group inside a list item would be
compulsory in every entry.

**One server per panel launcher, on a port the system picks.** A page per
panel on one shared server could keep links and columns apart through the
address, and could not keep the wallpaper, the slideshow or the weather apart:
the page fetches `/wallpaper`, `/slides.json` and `/weather.json` by absolute
path. `launcher.start(port=ANY_PORT)` is the whole of it -- each call already
carried its own closure of everything -- and port 0 cannot collide with
anything else the Home Assistant machine runs, since only this add-on's own
senders are ever handed the address. The house's keeps 8099.

`launchers:` entries override the house launcher key by key
(`launcher_<key>`, so there is no second table), their links drive that
panel's quality, fps and user agent alone, and tokens are the one exception:
the panel's own first, then the house's -- a credential for an address rather
than a look, and a Home Assistant tile without one asks to log in.

4.18's panel `links:` and 4.17's `advanced: columns:` are removed rather than
kept beside it: two places to set the same thing is worse than one, both were
days old, and an unknown key inside a list item only warns
(`_nested_validate_dict`). `address_for`, `links_for` and the `?panel=` query
went with them.

`tools/checkpanels.py` drives `load_panels()`, `start_launchers()`,
`route_to_launcher()` and `give_page_settings()`, reads every panel's command
line (never printing it -- it carries a token), and opens each panel's own
address at its own size: exact links per panel, 4 and 3 across, a light
theme on one and dark on the others, a smaller clock, and a wallpaper on one
page and on no other. Two faults were in its ruler first, as usual: a panel
given three links cannot show four across, and the page's ground computes to
`color(srgb ...)` rather than `rgb()`, so the theme is read off the ink.
Against 4.18.1 it cannot run at all -- `start_launchers` did not exist -- which
is the fault being an absence again.

**And the schema was run through the Supervisor's own validator, not only
read.** `supervisor/apps/options.py` loaded with its package imports stubbed
(one line of Python 3.14 `except A, B:` syntax rewritten for 3.11), defaults
merged the way `App.options` does, and the household's pasted configuration
with its token replaced: **valid** as it stands (`launchers: []` from the
default), **valid** carrying 4.18's `links:` and 4.17's `columns:` (two
`Unknown option` warnings), **valid** with two entries -- and a list nested
inside a panel's entry **refused** with `Missing option 'own_links' in
panels`, which is the premise of this whole design measured rather than read.
The Supervisor tree is fetched blobless (`git clone --filter=blob:none
--no-checkout`, then `git checkout HEAD -- supervisor/apps/options.py`);
checking out the whole of `supervisor/` that way takes minutes.

### The form is the add-on's first page, and it showed raw keys -- 4.19.1

**Reported as *"je vois panel et launcher ce n'est pas organise ... mieux
compris pour une personne qui du mal avec home assistant n'hesite pas a
ajouter des couleurs des switch"*.** `panels`, `links`, `launcher`,
`launchers`, `defaults`, `debug`: six English keys, two of them one letter
apart, on the page a household opens first.

**Home Assistant has the mechanism and this add-on had never used it.**
`translations/<lang>.yaml` beside config.yaml, read by the Supervisor
(`store/data.py`, `_read_app_translations`; shape
`SCHEMA_TRANSLATION_CONFIGURATION` in `apps/validate.py`: `name`, optional
`description`, recursive `fields`). The frontend
(`supervisor-app-config.ts`, read in its own tree) puts `name` in place of the
key and `description` under it -- for a top-level group (an expandable
section), for every field of a list entry (the object selector's `fields`) and
for groups inside those. Booleans were already toggles and `list()` options
already dropdowns, so "des switch" was already true; what was missing was
words. "Des couleurs" is an emoji per section, which is the only colour a
Supervisor form can carry.

**The keys do not move, and that is why this is free.** A translation changes
what the form SHOWS, never what is stored, so no saved configuration has to be
touched -- where renaming a key would have been the migration this file warns
about under the launcher's size scale.

**And it needs a version bump, which is not obvious.** An INSTALLED add-on
keeps the copy of its store data taken at install or update
(`apps/data.py`: `system[slug] = deepcopy(app.data)` in both), so a
translation that lands without a bump reaches nobody who already has the
add-on. `tools/checkaddon.py` now counts `translations/` among what the
version must move for, beside config.yaml.

`tools/maketranslations.py` writes both languages from ONE table, so a field
cannot have a French name and no English one; `check_translations()` in
`tools/checkaddon.py` walks the schema and fails on any setting with no name
and any name for no setting -- reproduced on a copy with one field deleted
and one invented, both caught. Both files were also run through the
Supervisor's own `SCHEMA_APP_TRANSLATIONS`, extracted from its source, and
come back unchanged: nothing would be dropped. **What is NOT verified** is the
page as drawn -- there is no Home Assistant frontend here -- so how it looks
is one update away.

## A narrow tile puts its icon above its name -- 4.19.6

**Reported with two photographs of a 1280x800 panel**: at `columns: 5` the
names read "Jellyfi n", "Reoli nk", "YouT ube"; at 6 they ran one letter a
line. The icon sat BESIDE the name at every width, so a narrow tile left the
name a sliver, and `overflow-wrap: anywhere` cut words wherever it liked.
The per-panel `columns:` of 4.19.0 let a household avoid it; it did not stop
a household that asked for five.

A tile is a CSS **container** now (`container-type: inline-size`), and below
**290px** the content stacks, the way a phone lays out an app, with the icon
and the words sized in `cqi` to the tile rather than to the panel. 290 is
measured, not reckoned: a 272px tile (1280x800, four columns) cut
"Assistant" beside a full icon, a 296px one (1024x600, three) did not. The
padding moved from `a.tile` to an inner `.in`, because padding on the
container cannot answer to the container's width -- and being a container
also means a tile's content can no longer widen its column, so no column
count pushes a tile off the panel.

`tools/checktiles.py` asks the browser, through a Range over each word,
whether every word of every name and description landed on one line, at
three panel shapes and 0 to 6 columns. **Against the shipped launcher it
fails nine cases, the two photographs among them**; after, none. And
`checkpanels.py` had a case asserting the OLD fault -- "four across on
1024x600 breaks words in half", kept as the premise for per-panel columns --
which now asserts the opposite. A check that pins a fault down as a fact has
to be turned round the day the fault is fixed, or it fails the fix.

**And stacking at 290 made four columns too tall -- 4.19.7.** Reported
with a photograph of a 1280x800 panel at four columns: *"tu as change pour
les tuiles de 1 a 4, elles sont grandes"*. Measured against the launcher
before 4.19.6, one to three columns were identical to the pixel; only four
had moved, 137 -> 183 px tall at 1280x800 and 145 -> 177 at 1024x600. So
there are three bands now: above 290px as it always was; 218-290 keeps the
icon BESIDE the name with both sized in `cqi` (four columns: 274px and
220px wide, 106 and 83 tall); under 218 stacks (five and six, which the
household had accepted). `checktiles.py` still finds no cut word anywhere.
The lesson is the one about unasked-for changes in a smaller costume: a fix
for five and six columns reached into four, which nobody had complained of.

## Links as buttons, the household's own LVGL layout -- 4.20.0

**Asked with their own panel as the model**: *"reduire la taille des buttons
ou par exemple comme je le fais avec lvgl, icone et le button et le texte en
bas, c'est juste un exemple pour ce qu'ils veulent choisir"*. So a CHOICE,
not a replacement: `tiles: cards|buttons` on the house launcher and on every
`launchers:` entry, default `cards`, so nothing changes for anybody who
does not ask. A new `list()` key is safe to add; changing an existing one's
members is not (see the size scale above).

`buttons` copies their `waveshare.yaml` in `youkorr/lvgl_9.5` rather than a
guess at "an LVGL look": a 150x100 button on 1024x600 (26vmin at 3:2),
radius 15 (2.5vmin), a vertical gradient, the icon at the top and the name
along the bottom, and `pressed: translate_y: 5` -- measured at 4.8 px in the
browser -- with the shorter shadow of LVGL master's own
`lv_example_button_styling.c`. A button keeps its size: a column count caps
how many sit on a row (`repeat(N, minmax(0, 26vmin))`) instead of stretching
them. The description is not drawn on a button.

**Two faults, both invisible from reading.** The option reached `render()`
and did nothing, because `render()` already had a local called `tiles` --
the HTML of the tiles -- which overwrote the parameter before it was read;
it is `shape` inside `render()` now. And the first radius was `10cqi`, which
made every button a pill: a container's OWN size units resolve against its
ancestor's container, not itself. Both were seen on a screenshot, not in a
test -- `checktiles.py` now runs every case in both shapes and adds that
the icon and the name fit INSIDE a button, which a fixed shape can fail
where a card would just grow taller.

### The icon is the part that never gives way -- 4.20.1

**Reported in one line: *"tu as diminue les tailles des icones, fallait les
garder"*.** 4.19.7's middle band and 4.20.0's buttons both sized the icon
in `cqi`, so it shrank with the tile -- 48 px at four columns on 1024x600,
53 px on a button, against the 74 it has everywhere else. The complaint
was about height, and the fix reached for the one thing across a room
that nobody had asked to change: this file's unasked-for-change lesson
again, this time inside a fix.

The icon keeps `clamp(44px, 9vw, 74px)` everywhere now, and the narrow
bands shrink only the words and the side padding. Measured against the
launcher before 4.19.6: the icon is 74 px at every column count and panel
shape, and four columns are **118 px tall at 1280x800 and 107 at 1024x600,
against 137 and 145 before** -- shorter than the original, with no cut
word. A button keeps its 26vmin width, is at least 17.3vmin tall (their
100 px on 1024x600), and grows to hold the icon and a two-line name: 131
px there. `aspect-ratio: 3/2` could not, and `checktiles.py`'s "fits inside
its button" case is what said so, nine times.

## A byte rate, because a fixed quality makes the rate follow the scene -- 4.21.0

**Reported after hours of YouTube at quality 50 and 30 pictures a second:
*"le wifi du C6 plafonne ... il faut un debit stabilise"*.** The add-on's own
stats settled where the edge is, in one run:

| sent | panel wait | skipped | worst gap |
|---|---|---|---|
| 2.3-2.7 MB/s | 0-1% | 0 | 42-47 ms |
| 2.9-3.2 MB/s | 17-30% | 4-8 | 340-400 ms |

YouTube's own stats for nerds read 40 Mbit/s and 90 s buffered, so the source
was never short. And Espressif's performance guide gives P4 + C6 over SDIO at
2.4 GHz **30 Mbit/s of TCP inbound** -- the link sits exactly where the stalls
begin. A fixed quality weighs 80 KiB on a calm shot and past 100 on water or
a crowd, so it is the SCENE that crosses the line, not the settings.

`RateControl` in ha_send.py: each picture is weighed against the rate times
the time it covers (at least one frame interval, at most `MAX_SPAN_S` so a
whole panel after a still page is neither waved through nor punished), a heavy
one lowers the next picture's quality in proportion, a light one lets it climb
back a step at a time, never above what the page asked for and never below
`MIN_QUALITY`. Down fast and up slow on purpose: over the line costs a third
of a second, under it costs sharpness nobody sees in motion.

`max_rate` under defaults (2400 KiB/s), per panel and per link, `--max-rate` /
`--page-rate` on the sender, whose own default is 0 so a hand run is
unchanged. Adding a key with a default to the `defaults` DICT reaches existing
installs, because the Supervisor merges dicts under what is stored.

`tools/checkrate.py` measured the shipped sender against a fake panel and a
page of moving discs: **3519 KiB/s with no limit, 1437 with `--max-rate 1500`,
at 28.9 pictures a second against 28.8**, qualities 27-31 printed by `stats`;
a link given 0 turns it off, a still page is never touched. Its first failure
was the ruler: it expected a 250 KiB picture after ten still seconds to lower
the quality, which over a quarter second is 1 MB/s and should not.

**What the esp-hosted reading found, kept here because it will be asked
again:** the `H_SDIO_DRV: task still writing Rx data to queue!` drop is a
two-slot double buffer in `sdio_drv.c`, identical in 2.12.12 and 2.12.13
(what ESPHome 2026.8.2 and dev pin). esp-hosted 3.x replaces it with a ring
that waits instead of dropping (`ESP_HOSTED_HOST_SDIO_RX_STAGING_SLOTS`), and
its migration table says a **3.x host works with a 2.x co-processor** while a
2.x host with a 3.x co-processor does not. And ESPHome's high-performance
Wi-Fi options are `CONFIG_ESP_WIFI_*`, which do not exist on a P4 build: the
C6 takes its RX buffers and RX block-ack window from the host's
`CONFIG_WIFI_RMT_*` ("always use value from host", `slave_wifi_std.c`), which
nothing set. Espressif's own iperf example sets them 16 / 64 / 32 with SACK.
The ESP32-P4 errata list is identical for v1.0 and v1.3 and touches none of
this.

### The quality has a floor, so it was not a ceiling -- 4.21.1

**Reported as *"il depasse"*, at quality 70, 30 pictures a second and
`max_rate: 2400`.** Most windows held (2170-2415 KiB/s, worst gap 39-60 ms),
and the two that did not were at `quality 25-26` and `25-27`: 2854 and 2759
KiB/s, gaps 270 and 223 ms. At 30 a second 2400 KiB/s is 80 KiB a picture and
those scenes weighed about 95 even at the floor, so RateControl had no lever
left. Reproduced against 4.21.0 with the check's busy page: `--max-rate 900`
sent **1309-1345 KiB/s at quality 25**.

The second lever is a byte budget (`allows()`): it refills at the rate, a
picture spends it, and a picture in debt waits. Asked LAST in the release
condition, so it only ever holds a picture the frame limit would already have
released, and the held frame stays the newest (nothing is requested while
one is pending). The wait is the overshoot -- 15 KiB at 2400 KiB/s is 6 ms.
`BURST_S = 0.15` is what it may save up: a whole panel after a still page
goes out at once, and a busy scene after a quiet one cannot burst far.

**The trap was the two levers fighting.** A picture the budget held went out
late, so judged against the time it really covered it read as exactly at the
rate -- inside the 0.8-1.0 dead band -- and the quality stayed where it was
while the frame rate paid instead. A held picture is weighed against the
frame interval, so the quality goes down first and the wait only takes what
the floor cannot. There is a case for it.

Measured with the shipped sender: `--max-rate 900` holds **900, 898, 900** at
19.6 pictures a second where 4.21.0 sent ~1330 at 29; `--max-rate 1500`
unchanged (quality alone was enough there, 29.2 pictures a second);
`checkpacing.py` unchanged. `stats` ends in `N held` when it bit. With it,
`fps: 30` can stay on a video link -- lowering the frame limit costs every
scene what only the heaviest need.

**What is NOT measured**: a panel. The fake one takes everything at once, so
what is verified is that the sender never offers more than the rate, not that
the C6 stops stalling at it.

## Repository conventions

- Work on branch `claude/esphome-pr-outdated-mdq36w`, then merge into `main`
  with `--no-ff` and a `Merge: <what it does>` subject. That is the existing
  shape of `main`'s history.
- Commit subjects are imperative and say what the change *does for the user*,
  not which file moved.
