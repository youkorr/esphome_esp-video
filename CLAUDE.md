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
a 65534 send buffer whenever PSRAM is guaranteed, which every board this runs
on has. So those four lines were not a floor being raised. They were a
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

**`dump_config()` opened with "USB Extended Display" and mentioned the network
as an afterthought** -- literally `Also listening on TCP port 5000` -- on
panels where the network is the whole point and the USB socket carries nothing
but power. Worse, its last line handed over `python udisp_send.py`, the USB
sender, to somebody whose picture comes from the add-on.

It is ordered by the transport actually in use now: `Portall:`, the resolution,
**Over the network** with the port and the touches, then **Over USB** with the
identifiers, and a sender line that matches -- the add-on and its geometry when
`port:` is set, `udisp_send.py` when it is not. It also prints the address to
put in the add-on, `network::get_use_address()` read off the board rather than
guessed at from a router page, behind `#ifdef USE_NETWORK` so a board built
without networking still compiles.

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

## Repository conventions

- Work on branch `claude/esphome-pr-outdated-mdq36w`, then merge into `main`
  with `--no-ff` and a `Merge: <what it does>` subject. That is the existing
  shape of `main`'s history.
- Commit subjects are imperative and say what the change *does for the user*,
  not which file moved.
