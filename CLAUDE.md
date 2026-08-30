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
| `packed`       | `frame_id` in the low 10 bits, `payload_total` above |

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
= 32768`, `SO_KEEPALIVE` with `TCP_KEEPIDLE 10 / KEEPINTVL 5 / KEEPCNT 3`,
`TCP_NODELAY`, `NET_RECV_TIMEOUT_S = 30` enforced by the loop itself against
the last successful read (silence is the *normal* state — a still dashboard
sends nothing; the heartbeat every three seconds is what proves life), select
slice 5 ms (that is how long a contact can sit in the queue). All of the
`__init__.py` options below are set **only when `port:` is present**, because
they are device-wide and a USB-only board should not pay for them.

**The receive window is the whole inbound ceiling, and three settings have to
move together.** A window is how much a sender may have in flight before it
must stop and wait, so the most that can arrive is the window divided by the
round trip — nothing else about the link enters into it. `TCP_WND_DEFAULT` is
**64800**, which is 45 segments of the default 1440-byte MSS and the largest
multiple of it that fits the 16-bit window field a header carries without
window scaling. It was 28800, which is 23 Mbit/s at a 10 ms round trip and
11.5 at 20; a busy five-second window on a panel has been measured at 6.2, so
on a loaded radio the old value was closer than it looked.

`RECVMBOX_SIZE` is **64** and is not free to choose: Espressif's rule is
`TCP_WND / TCP_MSS + 2`, which is 47 here. Raising the window without raising
this makes things worse rather than better — the stack invites the sender to
fill a window it will then drop the tail of.

`SND_BUF_DEFAULT` stays at 28800 and is deliberately *not* raised with the
window. Sending and receiving are not symmetric on lwip, and this board sends
touches: a few bytes. It is not lowered either, because the options are
device-wide and a camera serving JPEG out of the same board is the one thing
that does need a send buffer.

**`SO_RCVBUF` is not the window, and it needs `CONFIG_LWIP_SO_RCVBUF` to exist
at all.** Without that option lwip does not implement it and `setsockopt`
fails with `ENOPROTOOPT` — which it had been doing silently for as long as the
call had been there, since nothing checked the return. It is a ceiling on what
one socket will hold, set above the window so that it never binds first; the
window is what actually governs.

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
caught it; keep measuring. It is no longer a matter of picking the right frame:
a press now throws away everything painted before it, in hand and in the
browser both, so nothing that predates it can be shown.

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

**A headless browser says so, and sites read it.** The UA carries
`HeadlessChrome/` and `navigator.webdriver` is true; measured on the shipped
build, both. Plenty of sites serve something else for that — a cut-down page,
an interstitial, or a refusal — and YouTube says *"navigateur non compatible"*
outright. When it does, there is no search box on the page at all, which from
a panel is indistinguishable from a keyboard that will not come up: it was
reported as the second. `present_browser()` takes the browser's own UA through
`Browser.getVersion` (so the platform token stays right on whatever is running
it), drops the word Headless, and hides `webdriver`. `--user-agent off` keeps
the honest one, a string uses it verbatim. **Not verified against YouTube** —
no route to it from where this was written — and it fixes nothing about the
codecs: that build has no H.264, no AAC and no EME, so a video may still
refuse where a search box now works.

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
lose, and `--stats` counts them as `skipped`. `SO_SNDBUF` is set to 65536 in
`connect_tcp`, which is the board's own receive window (`TCP_WND_DEFAULT`
64800): nothing above it is ever in flight, so the surplus was pure queue and
capping it costs no throughput. On a link that keeps up neither change is
visible — 24.0 pictures/s against 24.3, 70.5 Hz against 73.1.

`panel wait` therefore means something different now: it is the writer thread's
time, not the loop's, so it can sit near 100% without a stutter. When it does,
`skipped` is what the link is costing.

**Where the second actually goes, measured phase by phase.** At 30 whole
800×1280 panels a second, quality 60, the loop's own budget adds to 1000 ms/s
with 4 ms unaccounted: **pump 550, decode 185, encode+send 145, diff 65,
ack 42**. The pump is `wait_for_timeout(8)` — deliberate sleep, not work — so
the real cost is about 440 ms/s and the sender is *not* CPU-bound at 30 fps.
An earlier reading of this said it was, from timing only the long turns; long
turns bill the pump for whatever arrived during it. Time the whole window and
add a residual, or the conclusion inverts.

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
so this is never diagnosed by guesswork again. Currently **1.36.0**.

`run.py` supervises one `ha_send.py` per panel: `SHARED_KEYS` lets the token,
url, port, fps, quality, capture_quality, urgent_fps, urgent_window and stats be
given once at the top and inherited; backoff 5 → 10 → 20 → 120 s for a run
shorter than 20 s; children are killed on SIGTERM.

**A panel's own value wins only if it is a value.** An add-on's form has no
empty state, so a field nobody filled in arrives as `""`, and a plain
`{**shared, **panel}` lets that blank the shared one — silently, and the token
is where it bites.

**The add-on's form offers only what somebody should actually set.**
`rect_cost`, `freeze_animations` and `render_width`/`render_height` are gone
from it: the first is computed from the geometry and only wanted when the board
starts dropping frames, the second does nothing on a dashboard with a camera on
it, and the third costs visible sharpness. All three remain options on
`ha_send.py` for a hand-run sender. A form nobody can read is a form where the
setting that matters gets missed.

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
`usb_display.sleep`, so the sender went on rendering and transmitting for a
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
- `--blank-after` frees the page but not the browser: Chromium stays running
  with an empty tab. One browser serving several panels is the next step.


## Repository conventions

- Work on branch `claude/esphome-pr-outdated-mdq36w`, then merge into `main`
  with `--no-ff` and a `Merge: <what it does>` subject. That is the existing
  shape of `main`'s history.
- Commit subjects are imperative and say what the change *does for the user*,
  not which file moved.
