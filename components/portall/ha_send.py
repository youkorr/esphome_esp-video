#!/usr/bin/env python3
"""Put a web page on an ESPHome portall panel, over Wi-Fi.

The panel is not running a browser. A machine that is on anyway -- the Home
Assistant server itself, or anything beside it -- renders the page in a browser
with no window, and sends the picture to the panel. Touches come back over the
same socket and are replayed into that browser, so the panel behaves like the
screen of the machine doing the rendering.

A Home Assistant dashboard is what this was written for and what --token is
for: a browser with no keyboard cannot get past a login screen, so the token is
written into storage the way the frontend writes it after one. Nothing else
about this is particular to Home Assistant. Leave --token out and any page at
all is rendered, diffed and sent by the same code.

    ./ha_send.py --calibrate --host "ip esp32P4" --width 1024 --height 600
    ./ha_send.py --host "ip esp32P4"--width 1024 --height 600 \
        --url http://homeassistant.local:8123/lovelace/0 --token <long-lived> \
        --touch-rotate 0
    ./ha_send.py --host "ip esp32P4"--width 1024 --height 600 \
        --url https://example.com/ --touch-rotate 0

Calibrate first, once per board. There is no way to know from here which way a
panel reports its contacts: it depends on how the touch controller is wired and
on the transform: the touch screen was configured with, and no two boards
agree -- a GT911 on one mirrors both axes, the same part on another swaps them,
a GSL3680 on a third mirrors one. ESPHome hands a listener that board's own
display coordinates, which is what LVGL wants and is not necessarily the
orientation the picture is being shown in. So --calibrate draws three targets,
asks for a tap on each, and prints the options that make the two agree. It needs
no browser and no token, so it is the first thing to run on a new panel.

What makes this affordable is that it does not send the picture. It sends the
part of the picture that changed. A dashboard at rest is a clock moving once a
minute against a background that never moves at all: a few kilobytes a second,
where mirroring a desktop costs upwards of a megabyte a second. That is the
difference between a panel on a battery and a panel on a cable.

The board has always been able to draw a rectangle rather than a screen -- the
protocol header carries x, y, width and height, and Espressif's own Windows
driver uses it that way. Every rectangle of one picture is sent with the same
frame identifier so the board admits or drops them together, and never shows
half an update.

Requirements:

    pip install playwright pillow numpy
    playwright install chromium

The token is a long-lived access token from your Home Assistant profile page,
at the bottom under "Long-lived access tokens". It is written into the
browser's local storage the way the frontend writes it after a login, which is
what lets a browser with no keyboard get past the login screen.
"""

import argparse
import base64
import collections
import io
import json
import os
import subprocess
import sys
import threading
import time

# udisp_send.py is next to this file and owns the wire format. Importing it
# rather than restating the header keeps one definition of the protocol.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udisp_send import (  # noqa: E402
    AUDIO_BITS,
    AUDIO_CHANNELS,
    AUDIO_RATE,
    build_audio_header,
    build_header,
    build_heartbeat,
    connect_tcp,
)

# The panel is divided into tiles and each is compared with the last picture.
# Small tiles find changes precisely and cost many rectangles; large ones cost
# few rectangles and send unchanged pixels along with the changed ones. 64 is a
# compromise that keeps a clock's worth of change down to one or two tiles.
TILE = 64
# What one rectangle costs the board on top of its pixels, as a fraction of a
# whole-panel decode. Measured: the panel decodes in 8.5 ms and each rectangle
# adds roughly 1.5 ms of fixed work on top of its share of that.
#
# It is the count that decides whether to give up and send the panel, not the
# area. Twenty scattered rectangles covering 45% cost the board 34 ms against
# 8.5 for the panel, so the panel wins; one rectangle covering 64% -- which is
# what two camera cards look like once the tiles between them join up -- costs
# 233 KiB against 272 for the panel and decodes in 6.9 ms against 8.5, so it
# wins on both counts. Judging by area alone got that case exactly backwards
# and sent a whole panel, every frame, to update two thirds of it.
# The two measurements it is made of, kept apart so it can be worked out for
# a panel rather than assumed. A rectangle adds a fixed 1.5 ms on the board --
# its header, its own JPEG tables, one more DMA transfer set up -- and a
# whole-panel decode took 8.5 ms on the 1024x600 it was measured on, which is
# 0.6144 megapixels.
RECT_FIXED_MS = 1.5
PANEL_DECODE_MS_PER_MPX = 8.5 / 0.6144


def rect_cost_fraction(width, height):
    """What one rectangle costs, as a fraction of redrawing the whole panel.

    This has to be computed and cannot be a constant, because it is a ratio and
    only the numerator is fixed: a whole-panel decode grows with the pixels. At
    1024x600 it comes to 0.176, which is the 0.18 that was measured there and
    hard-coded, so nothing changes on that panel. At 800x1280 -- two thirds
    again as many pixels -- it comes to 0.106.

    Leaving 0.18 on a larger panel is not a small error. The rule gives up on
    rectangles when coverage + fraction x count exceeds one, so at 0.18 six
    rectangles alone exceed it: the whole panel goes out however little of it
    changed. Measured on an 800x1280 dashboard with a camera on it, 17 pictures
    out of 18 in a five second window were whole panels of 132 KiB. At 0.106
    the same rule holds out to nine rectangles and weighs the area again.
    """
    return RECT_FIXED_MS / (PANEL_DECODE_MS_PER_MPX * width * height / 1e6)
# No rectangle narrower or shorter than this.
#
# The P4's JPEG decoder is a DMA engine working in 16x16 units, and a sliver
# stalls it: a 32x128 strip comes back as ESP_ERR_TIMEOUT rather than as
# pixels. Slivers are not rare -- they are the panel's own edge, wherever its
# size is not a multiple of the tile: 800 is twelve tiles of 64 and a
# remainder of 32, so the rightmost column of every picture was one. Nothing
# was ever drawn there, which is why the edge went stale and stayed stale.
#
# Growing such a rectangle backwards costs a few pixels sent twice and fixes
# it outright.
MIN_RECT =  64
# However little changes, redraw everything this often. A dropped rectangle --
# the board was busy, the socket hiccuped -- would otherwise stay wrong on the
# panel forever, because nothing would ever mark that area as changed again.
FULL_REDRAW_SECONDS = 30.0
# How long to let the browser run between looks. Short, because this is
# what bounds how stale a change can be before it is even noticed; not
# zero, because each look is a round trip into the browser.
PUMP_MS = 8
# How often to say "still here" when there is nothing to send. A sender that
# only transmits what changed is silent while nothing changes, and silence is
# indistinguishable from having died; the board's patience has to be longer
# than this, and is.
HEARTBEAT_S = 3.0
# Whether a frame that follows an input skips the rate limit.
#
# The limit exists to stop a busy page spending the whole link on frames
# nobody asked for. A frame that follows a press is the opposite: somebody
# just did something and is waiting to see it happen, and holding it back for
# the rest of the interval is the difference between a panel that feels
# connected to your finger and one that does not. Self-limiting, because it
# only fires as often as there is input.
URGENT_AFTER_INPUT = True
# How long the frame limit is lifted for after a press, and how far.
#
# One free frame is enough to watch a button go down. It is not enough for what
# a press usually starts. Changing dashboard repaints the whole page over about
# a second, and at four frames a second that arrives as four pictures, which
# looks like a slideshow -- reported from a real panel as "it drags between the
# dashboards", and the frame limit was the cause. The limit exists to stop a
# page that never settles spending the link on frames nobody asked for, and for
# a second after a press exactly the opposite is true.
#
# A window that raises the rate rather than one that removes the limit -- but
# not for the reason it first seemed. The link has room: the board's radio was
# measured above 25 Mbit/s serving a camera, and the inbound side, which is the
# one that matters here, is bounded by the 28800-byte receive window over the
# round trip, so 23 Mbit/s at a 10 ms RTT and more on a quiet network. A
# transition peaked at 6.2.
#
# What actually binds during a transition is this machine. Nearly every picture
# then is a whole panel, and decoding, diffing and re-encoding one at 800x1280
# takes around 40 ms: the loop falls from 62 Hz to 43 and the sender tops out
# near seven pictures a second whatever it is allowed. Fifteen therefore does
# not bind there at all. What it stops is the other case -- a small cheap
# change being sent sixty times a second because a finger touched the panel.
# Defaults, overridable: a panel reported that a standing 10 was slow against
# 30 even with the window, and it was right -- fifteen for one second is less
# than the machine can do and less than the thing being watched needs. A
# transition, a scroll settling, a card opening: all of them run longer than a
# second, and the sender was measured serving 21 pictures a second on that
# panel. So the window now goes to what the browser and the machine will give,
# for as long as the movement lasts, and the standing limit is left free to be
# low -- which is the whole point of having two numbers instead of one.
URGENT_WINDOW_S = 2.0
URGENT_FPS = 30.0
# How the browser is started.
#
# The defaults are written for a browser somebody is looking at, and this one
# is not: nothing is ever in the foreground, no window is ever focused, and a
# page can sit for hours with no input. Chromium reads that as a tab nobody
# wants and starts economising -- throttling timers, suspending media, pausing
# a video that has been playing to no one. On a dashboard with two camera cards
# that shows up as one camera live and the other stopped, coming back only when
# something makes the browser pay attention to it again.
BROWSER_ARGS = [
    "--hide-scrollbars",
    "--disable-gpu",
    # A video nobody clicked on is still a video that should play here.
    "--autoplay-policy=no-user-gesture-required",
    # No --mute-audio here, and that is deliberate. It was added once on the
    # argument that the panel has no sound -- which is not true of the panel,
    # only of this link: the board has an ES8311 on I2S, an ESPHome speaker and
    # a media_player entity, and takes audio in over USB. What has no audio is
    # the udisp protocol, which is rectangles and touches. Even granting the
    # narrower claim, it was the wrong thing to do unasked. A
    # site may treat a muted player differently from an audible one, and
    # muting is exactly the kind of change that turns "it does not work" into
    # a variable nobody remembers introducing. If it is ever wanted it belongs
    # behind an option, not in the defaults.
    # Do not stop a stream because the page it is on is not in front.
    "--disable-background-media-suspend",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    # There is no window manager here to ask, and the answer it guesses is
    # "covered", which costs the page its updates.
    "--disable-features=CalculateNativeWinOcclusion",
]
# The way back to the page the panel was pointed at.
#
# A panel used as a launcher -- a Homepage, a Dashy, a Home Assistant view full
# of links -- can go anywhere, and then it is stuck: there is no keyboard, no
# address bar and no Back button, and a video playing full screen swallows
# everything the page is given. So the way home cannot come from the page.
#
# It comes from here instead, the same way the on-screen keyboard does: a
# contact inside the corner below is tested by arithmetic in the sender, and if
# it is held long enough the page is never told it happened. Nothing a site
# does can disable it, because no site is asked.
#
# A corner rather than an edge swipe, because a swipe in from the side is how
# plenty of pages scroll sideways. Held rather than tapped, because a corner
# gets brushed by accident and a whole second of stillness does not. Short of
# that second the tap is delivered normally, so the corner stays usable.
HOME_CORNER_FRACTION = 0.14
HOME_HOLD_S = 1.0
# Or swipe sideways from that same corner, which is the gesture somebody
# actually suggested after living with the hold -- and it is the better one to
# find by accident, because a finger that lands and drags is what a person does
# to a screen they are unsure of, while holding perfectly still for a second is
# something you have to be told to do.
#
# Sideways rather than any direction, because the page under the corner scrolls
# vertically and a swipe down from the top is how you scroll it. A tenth of the
# page's width is far enough that nothing brushes it and short enough to be one
# comfortable movement of a thumb: 128 px on a 1280-wide page, 102 on a 1024.
HOME_SWIPE_FRACTION = 0.10
# And it has to be more sideways than not, or a diagonal scroll that began in
# the corner would take the panel home.
HOME_SWIPE_STRAIGHTNESS = 1.5
# The least time between two scroll injections.
#
# Every input dispatch costs a display frame. Measured on the shipped browser,
# and it is not the page and not the screencast: page.mouse.wheel takes 16.6 ms
# a call, a raw CDP Input.dispatchMouseEvent takes 16.2, and a plain mouse.move
# on about:blank takes 16.7. Chromium acknowledges input on its next frame and
# a synchronous client waits for it.
#
# So the number of calls a second is the whole budget. A finger reports fifty
# times a second, and one wheel each took 830 ms of every 1000: the loop went
# from 110 Hz at rest to 10 Hz while a finger moved, which is a panel reading
# touches ten times a second and painting no faster. Deltas are summed instead
# and sent as one, which scrolls exactly as far -- measured, 200 px either way
# -- for one call instead of fifty.
#
# 30 ms rather than every turn, because Chromium coalesces input per frame
# anyway and nothing above the picture rate is visible.
WHEEL_MIN_INTERVAL_S = 0.030
# How far a finger has to travel before the gesture is scrolling rather than
# a tap. Small enough that a deliberate drag is recognised at once, large
# enough that the wobble of a fingertip on a press is not.
DRAG_THRESHOLD = 12
# How long to wait for a page to say it has loaded before showing it anyway.
# Nothing is given up by the wait ending: the browser goes on loading and the
# screencast shows whatever paints. It is only the difference between a first
# picture of a finished page and a first picture of one still arriving.
LOAD_TIMEOUT_S = 30
# How long to wait between looks while the panel is dark. Long enough that the
# loop stops costing anything, short enough that it is lost in the hundred
# milliseconds the browser takes to repaint after the tap that woke it.
SLEEP_PUMP_MS = 100

# How often --show-media samples a playing video. Two seconds: a player that is
# fetching ahead visibly gains a second of buffer between two lines, and a
# minute of video is thirty lines rather than a screenful.
MEDIA_SAMPLE_MS = 2000


# Where a browser with the proprietary codecs is usually found.
#
# The Chromium that Playwright downloads is built without them. Measured on the
# one this ships with, 141.0.7390.37: H.264 no, AAC no, HLS no, and
# navigator.requestMediaKeySystemAccess does not even exist, so there is no DRM
# of any kind. VP9, VP8, AV1, Opus and Vorbis are all there.
#
# For a dashboard none of that matters. For YouTube it decides whether a video
# plays at all: the player picks its formats by asking isTypeSupported, and a
# stream it cannot decode ends as "un probleme est survenu" a few seconds in --
# which is exactly how it was reported. A Chromium packaged by a distribution
# is built with ffmpeg_branding=Chrome and does have them, so preferring one
# when it is installed costs nothing and fixes the whole class.
#
# Order matters: a real Chrome first, because it also carries Widevine.
SYSTEM_BROWSERS = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
)
# The popover API is what puts the on-screen keyboard in the top layer, above
# Home Assistant's own modal dialogs. It arrived in Chromium 114.
POPOVER_SINCE = 114


# Reported from the page whenever a <video> or an <audio> gives up.
#
# Without this the only thing anybody can say is "the video stops", and every
# cause looks the same from the panel. The element's own error carries
# Chromium's internal reason -- DEMUXER_ERROR_NO_SUPPORTED_STREAMS for a format
# it cannot decode, PIPELINE_ERROR_DECODE for one it started and could not
# finish, a network reason for a stream that went away -- so one line of log
# separates a codec problem from a link problem from a site refusing to serve.
#
# The listener sits on the window in the capture phase: an error on a media
# element does not bubble, but the capture path still runs through the window,
# so nothing has to be swept for or watched as the page builds itself. It
# writes nothing into the page, so no Trusted Types policy can refuse it.
MEDIA_INIT_TEMPLATE = """
(() => {
  const CODES = {1: 'aborted', 2: 'network', 3: 'decode',
                 4: 'format not supported'};
  // A timeline, not just the errors, because the thing being chased does not
  // error at all: a video plays for a few seconds and simply stops, and from
  // the sender all that is visible is a page that has gone still. Which event
  // fired, and what the element looked like when it did, is the difference
  // between "the site paused it", "it ran out of data" and "the tab went
  // invisible" -- three different faults that look identical from outside.
  //
  // Capped, because a playing video fires these often and a log nobody can
  // read is no better than no log. Rate-limited too, so a stall that repeats
  // does not fill the cap in one second.
  const LIMIT = 40;
  let said = 0;
  let last = 0;
  const say = (what) => {
    const now = Date.now();
    if (said >= LIMIT || now - last < 400) return;
    last = now;
    said += 1;
    try { window.__udispMediaError(what); } catch (e) { /* never cost the page */ }
    if (said === LIMIT) {
      try { window.__udispMediaError('(further media events not listed)'); }
      catch (e) { /* nothing to do */ }
    }
  };
  const who = (el) => {
    // Which player this is. A YouTube search page runs a hover preview
    // alongside the real one and pauses it constantly, so a timeline that
    // does not say which element it is describing is unreadable.
    let node = el;
    for (let i = 0; node && i < 6; i++) {
      if (node.id) return node.id;
      node = node.parentElement;
    }
    return el.tagName.toLowerCase();
  };
  const state = (el) => {
    const bits = [
      '<' + who(el) + '>',
      't=' + (el.currentTime || 0).toFixed(1),
      'ready=' + el.readyState,
      'net=' + el.networkState,
      el.paused ? 'paused' : 'playing',
      'page=' + document.visibilityState,
      document.hasFocus() ? 'focused' : 'unfocused',
    ];
    if (el.buffered && el.buffered.length) {
      bits.push('buffered=' +
        (el.buffered.end(el.buffered.length - 1) - (el.currentTime || 0)).toFixed(1) + 's');
    }
    return bits.join(' ');
  };
  const WATCH = ['pause', 'playing', 'waiting', 'stalled', 'ended',
                 'emptied', 'suspend', 'abort'];
  for (const kind of WATCH) {
    window.addEventListener(kind, (ev) => {
      try {
        const el = ev.target;
        if (!el || (el.tagName !== 'VIDEO' && el.tagName !== 'AUDIO')) return;
        say(kind + ': ' + state(el));
      } catch (e) { /* never cost the page anything */ }
    }, true);
  }
  window.addEventListener('error', (ev) => {
    try {
      const el = ev.target;
      if (!el || (el.tagName !== 'VIDEO' && el.tagName !== 'AUDIO')) return;
      const err = el.error;
      if (!err) return;
      let what = CODES[err.code] || ('code ' + err.code);
      if (err.message) what += ': ' + err.message;
      say('error ' + what + ' -- ' + state(el));
    } catch (e) { /* never cost the page anything */ }
  }, true);

  // Everything above fires when something goes WRONG, which cannot answer a
  // question about a video that is going right. "The player never fetches
  // ahead" is exactly that shape of claim: it is about the seconds before the
  // stall, when no event fires at all, so the log is silent precisely where
  // the evidence would be. This prints the playhead and how much is buffered
  // in front of it at a steady beat, so the two can be watched moving -- or
  // not moving, which is the claim.
  //
  // Its own budget rather than the one above: a stall that repeats must not
  // spend the timeline's cap, and a timeline must not spend the errors'.
  const SAMPLE_MS = __SAMPLE_MS__;
  const SAMPLE_LIMIT = 240;
  let sampled = 0;
  if (SAMPLE_MS > 0) {
    setInterval(() => {
      try {
        if (sampled >= SAMPLE_LIMIT) return;
        for (const el of document.querySelectorAll('video, audio')) {
          // Only what is actually running. A page carries players it is not
          // using -- YouTube's hover preview is one -- and a paused element
          // has nothing to say about fetching ahead.
          if (el.paused || el.ended || !el.duration) continue;
          let line = 'playing: ' + state(el);
          // Frames the compositor threw away. "Perte de trame" has two quite
          // different causes and this separates them: dropped here is the
          // BROWSER failing to keep up, and nothing the panel or the link
          // could be blamed for.
          try {
            if (el.getVideoPlaybackQuality) {
              const q = el.getVideoPlaybackQuality();
              line += ' frames=' + q.totalVideoFrames +
                      ' dropped=' + q.droppedVideoFrames;
            }
          } catch (e) { /* not a video, or not implemented */ }
          sampled += 1;
          try { window.__udispMediaError(line); } catch (e) { /* never cost the page */ }
          if (sampled >= SAMPLE_LIMIT) {
            try { window.__udispMediaError('(timeline ends here)'); }
            catch (e) { /* nothing to do */ }
            return;
          }
        }
      } catch (e) { /* never cost the page anything */ }
    }, SAMPLE_MS);
  }
})();
"""


def media_init_js(sample_ms):
    """The media timeline, with its periodic sampler on or off.

    A number rather than a boolean because the beat is the whole design of it:
    fast enough to see a buffer fill, slow enough that a log stays readable.
    """
    return MEDIA_INIT_TEMPLATE.replace("__SAMPLE_MS__", str(int(sample_ms)))

# What the browser can actually decode. The player of any video site asks these
# same questions before it chooses a format, so this is the list that decides
# whether a video can play -- not the user agent, and not the version.
MEDIA_PROBE_JS = """() => {
  const ask = (t) => {
    try {
      return !!(window.MediaSource && MediaSource.isTypeSupported
                && MediaSource.isTypeSupported(t));
    } catch (e) { return false; }
  };
  return {
    h264: ask('video/mp4; codecs="avc1.42E01E"'),
    aac: ask('audio/mp4; codecs="mp4a.40.2"'),
    vp9: ask('video/webm; codecs="vp9"'),
    av1: ask('video/mp4; codecs="av01.0.05M.08"'),
    opus: ask('audio/webm; codecs="opus"'),
    eme: !!navigator.requestMediaKeySystemAccess,
  };
}"""


def _launch(playwright, executable, profile, view, browser_args,
            ignore=(), env=None, locale=None):
    """Start the browser, and fall back to Playwright's own if it will not.

    Preferring a system browser is only safe if being wrong about it costs
    nothing. A path can exist and still not run -- a snap wrapper with no
    snapd behind it, a package half-installed, a binary for another
    architecture -- and the panel should not go dark over a browser that was
    only ever a preference.
    """
    def start(path):
        if profile:
            os.makedirs(profile, exist_ok=True)
            return playwright.chromium.launch_persistent_context(
                profile, args=browser_args, viewport=view,
                device_scale_factor=1, executable_path=path,
                ignore_default_args=list(ignore), env=env, locale=locale,
            )
        return playwright.chromium.launch(
            args=browser_args, executable_path=path,
            ignore_default_args=list(ignore), env=env,
        ).new_context(viewport=view, device_scale_factor=1, locale=locale)

    if executable is None:
        return start(None)
    try:
        return start(executable)
    except Exception as err:  # noqa: BLE001 - anything at all means fall back
        # Collapsed, because Playwright's own message runs to several lines
        # and a log that wraps is a log nobody reads.
        why = " ".join(str(err).split())[:160]
        print(f"Browser: {executable} would not start ({why}), "
              f"using Playwright's own")
        return start(None)


def watch_failed_requests(context, limit=12):
    """Say which requests the page could not make, and why, once per cause.

    Built because a panel showing YouTube's "le contenu n'est pas disponible"
    was diagnosed twice from guesswork and twice wrongly. A page that half
    works is a page where some requests fail, and the browser knows exactly
    which and exactly why -- a name that did not resolve is a filtered DNS, a
    connection refused is something in the way, a blocked-by-client is the
    browser itself. From the panel all three look identical.

    Grouped by host and reason and printed once each, capped, because a page
    that is failing is usually failing hundreds of times a second and a log
    nobody can read is no better than no log.
    """
    seen = set()
    explained = []

    def note(request):
        try:
            from urllib.parse import urlsplit

            host = urlsplit(request.url).hostname or "?"
            failure = request.failure or "?"
            # Not a failure. A media player aborts range requests constantly
            # -- switching quality, seeking, closing a stream it no longer
            # needs -- and reporting those sent a real diagnosis chasing
            # googlevideo.com when nothing there had gone wrong at all.
            if "ERR_ABORTED" in failure:
                return
            key = (host, failure)
            if key in seen or len(seen) >= limit:
                return
            seen.add(key)
            if len(seen) == limit:
                print("Network: (further failures not listed)")
            print(f"Network: {host} -- {failure}")
            # Said once, because it is the one failure whose cause is never
            # the site. A name that does not resolve is this machine's
            # resolver, and on a Home Assistant box that is the Supervisor's
            # own DNS container rather than the router. It is also the failure
            # that looks least like itself from the panel: a video whose next
            # segment cannot be fetched does not error, it simply runs its
            # buffer down to nothing and stops.
            if "ERR_NAME_NOT_RESOLVED" in failure and not explained:
                explained.append(True)
                print(
                    "Network: a name that does not resolve is this machine's "
                    "DNS, not the site. In Home Assistant: Settings > System "
                    "> Network > DNS servers."
                )
        except Exception:  # noqa: BLE001 - a diagnostic must never cost a page
            pass

    context.on("requestfailed", note)


def pick_browser(wanted):
    """Settle which browser executable to run, and say why.

    "auto" prefers a browser installed on the machine over the one Playwright
    downloaded, for one reason only: the downloaded one has no proprietary
    codecs. Everything else about it is fine, and if there is no system browser
    the downloaded one is what runs, exactly as before.
    """
    if wanted in ("", "off", "none"):
        return None
    if wanted != "auto":
        if not os.path.exists(wanted):
            print(f"Browser: {wanted} is not there, using Playwright's own")
            return None
        return wanted
    for path in SYSTEM_BROWSERS:
        if os.path.exists(path):
            return path
    return None


def report_media(page):
    """Print what this browser can decode, and warn when a video site will fail.

    Printed always rather than on demand, because the failure it predicts is
    silent from here and unmistakable from the panel: the video starts, runs
    for a few seconds and stops with a message from the site rather than from
    the browser. Nothing in the sender's log said why until this line existed.
    """
    try:
        can = page.evaluate(MEDIA_PROBE_JS)
    except Exception as err:  # noqa: BLE001 - never fail to start over this
        print(f"Browser: could not be asked what it decodes ({err})")
        return
    yes = lambda k: "yes" if can.get(k) else "no"  # noqa: E731
    print(
        f"Browser: decodes H.264 {yes('h264')}, AAC {yes('aac')}, "
        f"VP9 {yes('vp9')}, AV1 {yes('av1')}, Opus {yes('opus')}; "
        f"DRM {yes('eme')}"
    )
    if not can.get("h264") or not can.get("aac"):
        print(
            "Warning: this browser has no H.264 and no AAC, so a video site "
            "that offers no other format will start a video and then stop it "
            "-- YouTube says 'un probleme est survenu'. Install a Chromium "
            "packaged by your distribution and the sender will prefer it, or "
            "point --browser at one."
        )


# What a headless build calls itself in the client hints, and what a real
# Chrome calls itself instead. Only the brand name differs: the versions, the
# platform and everything else stay the browser's own.
HEADLESS_BRAND = "HeadlessChrome"
CHROME_BRAND = "Google Chrome"

_AGENT_DATA_JS = """async () => {
  const d = navigator.userAgentData;
  if (!d) return null;
  const hi = await d.getHighEntropyValues([
    'architecture', 'bitness', 'fullVersionList', 'model',
    'platformVersion', 'uaFullVersion', 'wow64',
  ]);
  return {
    brands: d.brands, mobile: d.mobile, platform: d.platform,
    architecture: hi.architecture || '', bitness: hi.bitness || '',
    fullVersionList: hi.fullVersionList || [], model: hi.model || '',
    platformVersion: hi.platformVersion || '',
    uaFullVersion: hi.uaFullVersion || '', wow64: !!hi.wow64,
  };
}"""


def _agent_metadata(page, version):
    """The client hints to send beside the user agent, with no headless brand.

    Read from the page when that is possible and synthesised when it is not,
    which is the usual case here: `navigator.userAgentData` only exists in a
    secure context, and the page is still `about:blank` when the disguise is
    put on. Measured -- it comes back None on `about:blank` and on a data:
    URL, and works on http://127.0.0.1.

    Synthesising is safe because the list is not a secret. A real Chrome sends
    three brands: Chromium, Google Chrome, and one deliberately meaningless
    entry that exists to stop servers matching on the list exactly. Only the
    versions have to be right, and those come from the browser itself.
    """
    try:
        data = page.evaluate(_AGENT_DATA_JS)
    except Exception:  # noqa: BLE001 - a disguise must never fail a start
        data = None

    def rename(brands):
        return [
            {"brand": CHROME_BRAND if b["brand"] == HEADLESS_BRAND else b["brand"],
             "version": b["version"]}
            for b in brands or []
        ]

    if data and data.get("brands"):
        return {
            "brands": rename(data.get("brands")),
            "fullVersionList": rename(data.get("fullVersionList")),
            "fullVersion": data.get("uaFullVersion", ""),
            "platform": data.get("platform", ""),
            "platformVersion": data.get("platformVersion", ""),
            "architecture": data.get("architecture", ""),
            "model": data.get("model", ""),
            "mobile": bool(data.get("mobile")),
            "bitness": data.get("bitness", ""),
            "wow64": bool(data.get("wow64")),
        }

    product = version.get("product", "")
    agent = version.get("userAgent", "")
    try:
        full = product.split("/", 1)[1]
    except IndexError:
        return None
    major = full.split(".")[0]
    # The platform has to agree with the user agent string, or the two
    # contradict each other and we are back where we started.
    os_name = "Linux"
    for token, name in (("Windows", "Windows"), ("Mac OS X", "macOS"),
                        ("CrOS", "Chrome OS"), ("Android", "Android")):
        if token in agent:
            os_name = name
            break
    # Chrome on Linux reports the kernel version here, and an empty one is
    # itself a small oddity in a set of headers whose whole point is to look
    # ordinary. Only claimed where it is known to be what Chrome would say.
    try:
        os_version = os.uname().release if os_name == "Linux" else ""
    except Exception:  # noqa: BLE001 - not worth failing a start over
        os_version = ""
    return {
        "brands": [
            {"brand": "Chromium", "version": major},
            {"brand": CHROME_BRAND, "version": major},
            {"brand": "Not?A_Brand", "version": "24"},
        ],
        "fullVersionList": [
            {"brand": "Chromium", "version": full},
            {"brand": CHROME_BRAND, "version": full},
            {"brand": "Not?A_Brand", "version": "24.0.0.0"},
        ],
        "fullVersion": full,
        "platform": os_name,
        "platformVersion": os_version,
        "architecture": "x86" if "x86_64" in agent or "Win64" in agent else "",
        "model": "",
        "mobile": "Android" in agent,
        "bitness": "64" if "x86_64" in agent or "Win64" in agent else "",
        "wow64": False,
    }


def present_browser(session, page, keyboard_wanted, wanted_agent):
    """Name the browser, warn if it is too old, and settle what it says it is.

    Both answers come out of one Browser.getVersion, and it is asked through a
    page's session rather than through the browser object, because a profile
    kept on disk is launched as a context and has no browser object to ask.

    A stale Chromium is invisible from everywhere else: pages render, touches
    land, nothing errors. The only symptom is a keyboard drawn underneath the
    dashboard rather than above it, which looks exactly like one that is not
    drawn at all -- the keys still work, because the hit test is arithmetic in
    here and never asks the page what is on top.

    And a headless one announces itself, in the user agent and in
    navigator.webdriver alike. Plenty of sites read those and serve something
    else: a cut-down page, an interstitial, or a refusal. YouTube says
    "navigateur non compatible", and there is then no search box on the page at
    all -- which from a panel looks exactly like a keyboard that will not come
    up, and was reported as one. The replacement is built from the browser's
    own string, so the platform token stays right wherever this runs.

    Returns the user agent that was set, or None if it was left alone.
    """
    try:
        version = session.send("Browser.getVersion")
    except Exception as err:  # noqa: BLE001 - not worth failing to start over
        print(f"Browser: could not be asked what it is ({err})")
        return None
    print(f"Browser: {version.get('product', 'unknown')}")
    try:
        major = int(version["product"].split("/")[1].split(".")[0])
    except (KeyError, ValueError, IndexError):
        major = None
    if keyboard_wanted and major is not None and major < POPOVER_SINCE:
        print(
            f"Warning: Chromium {major} is older than {POPOVER_SINCE}, where "
            f"the popover API arrived, so the keyboard will be drawn in the "
            f"page rather than above it and Home Assistant's own dialogs will "
            f"cover it. Rebuild the add-on to get a current browser."
        )
    if wanted_agent == "off":
        return None
    agent = wanted_agent
    if not agent:
        real = version.get("userAgent", "")
        if "Headless" not in real:
            return None
        agent = real.replace("HeadlessChrome/", "Chrome/").replace("Headless", "")
    # The user agent string is only half of what a browser says about itself,
    # and changing it alone makes matters worse rather than better.
    #
    # Measured on the shipped build. Left alone, every request carries
    # `sec-ch-ua: "HeadlessChrome";v="141", ...` and navigator.userAgentData
    # says the same -- so cleaning the string fools nobody who reads the
    # headers, which Google does. And overriding the string WITHOUT metadata
    # does something worse: the client hints vanish altogether. brands comes
    # back as [], and the request carries no sec-ch-ua at all. A browser
    # claiming to be Chrome while sending no Sec-CH-UA is a contradiction no
    # real Chrome produces, and a far louder automation signal than the honest
    # answer it replaced.
    #
    # So the metadata goes with it, built from the browser's own values with
    # the one brand that gives it away renamed -- which is exactly what a real
    # Chrome advertises.
    metadata = _agent_metadata(page, version)
    params = {"userAgent": agent}
    if metadata:
        params["userAgentMetadata"] = metadata
    try:
        session.send("Emulation.setUserAgentOverride", params)
    except Exception as err:  # noqa: BLE001 - the honest one still works
        print(f"Browser: would not be disguised ({err})")
        return None
    brands = ", ".join(b["brand"] for b in (metadata or {}).get("brands", []))
    print(f"Browser: saying it is {agent}")
    print("Browser: and its brands are " + (brands or "not set, which is a tell"))
    return agent


def origin_of(url):
    """Scheme and host, which is what a browser scopes storage to."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def install_token(context, url, token):
    """Write the token where the frontend expects to find it after a login.

    Home Assistant's frontend keeps its session in local storage under
    hassTokens. A long-lived token cannot be refreshed, so the expiry is set far
    enough out that it is never reached; the frontend checks it before deciding
    to refresh.
    """
    origin = origin_of(url)
    tokens = {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 315360000,
        "hassUrl": origin,
        "clientId": None,
        "expires": int(time.time() * 1000) + 315360000000,
        "refresh_token": "",
    }
    # Only on Home Assistant's own origin, and this is not tidiness.
    #
    # An init script runs on every document the context ever loads, and
    # `localStorage` belongs to whichever origin that document is on. A panel
    # that only ever showed one dashboard never noticed; a panel used as a
    # launcher visits YouTube, Jellyfin and whatever else is on the page, and
    # the house's long-lived token was being written into the storage of every
    # one of them, where any script on the page can read it.
    #
    # The same guard is what fixes the login screen. The record carries the
    # address it belongs to, and the frontend ignores one whose `hassUrl` is
    # not its own -- so a token installed for the launcher's origin left Home
    # Assistant asking to log in, with the record sitting right there in its
    # storage naming somewhere else. Measured against a real Home Assistant:
    # the dashboard renders when the origins agree and does not when they do
    # not.
    context.add_init_script(
        f"if (window.location.origin === {json.dumps(origin)}) "
        f"window.localStorage.setItem('hassTokens', "
        f"{json.dumps(json.dumps(tokens))});"
    )


def open_page(page, args):
    """Navigate to the page and wait for it to be worth photographing.

    Called again every time the page has been parked while the panel slept, so
    the waits belong here rather than inline: a dashboard that came back
    without them would be photographed as an empty shell and that empty shell
    would become the picture every later difference is measured against.
    """
    from playwright.sync_api import TimeoutError as PageTimeout

    # Not networkidle: the frontend holds a websocket open for as long as it
    # runs, and waiting for the network to go quiet would wait forever.
    try:
        page.goto(args.url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_S * 1000)
    except PageTimeout:
        # A page that is slow is not a page that is broken, and refusing to
        # show it is the worse of the two failures. This took the whole sender
        # down on a panel pointed at youtube.com: the navigation had committed
        # and the site simply had not finished, so every sixty-one seconds the
        # add-on threw its browser away and started again, for ever, and the
        # panel never showed anything at all. The browser is still loading it;
        # the screencast shows whatever paints, the way it does for every other
        # change on the page.
        print(
            f"Warning: {args.url} had not finished loading after "
            f"{LOAD_TIMEOUT_S}s -- showing it as it comes"
        )
    except Exception as err:  # noqa: BLE001 - the caller decides what to do
        # This one really did fail: a name that does not resolve, a refused
        # connection, a scheme nothing will serve. Say what is likely and let
        # the caller try again rather than ending.
        explain_unreachable(args.url, err)
        return False
    # Asked once and remembered. A panel used as a launcher is pointed at a
    # page of links and still wants the token, so that a Home Assistant tile
    # opens logged in -- and on that page the element never appears. Waiting
    # thirty seconds for it is bearable once at startup and is not bearable
    # every time the corner brings the panel home: the loop is blocked
    # throughout, so the panel simply stops for half a minute.
    # A token pointed somewhere else is a panel saying outright that this page
    # is not that dashboard -- it starts on a launcher, and the token is for
    # the tile. There is nothing here to wait thirty seconds for.
    elsewhere = bool(args.token_url) and origin_of(args.token_url) != origin_of(args.url)
    if elsewhere:
        # Known, not merely unasked: this page is not that dashboard. It also
        # buys the shorter settle below -- Home Assistant paints in stages and
        # is worth three seconds, a page of links is not, and the panel is
        # dark for every one of them.
        open_page.is_home_assistant = False
    if args.token and not elsewhere and open_page.is_home_assistant is not False:
        try:
            page.wait_for_selector("home-assistant", timeout=30000)
            open_page.is_home_assistant = True
        except Exception:  # noqa: BLE001 - a page without it is still worth sending
            open_page.is_home_assistant = False
            print("Warning: this does not look like a Home Assistant page, "
                  "so it will not be waited for again")
    # Home Assistant paints in stages -- shell, then cards, then their data --
    # and the first picture is the one full redraw everything else is a
    # difference from, so it is worth letting settle. A page that is not Home
    # Assistant has no such staging, and three seconds of a blocked loop is
    # three seconds of a panel that has stopped.
    page.wait_for_timeout(3000 if open_page.is_home_assistant is not False else 800)
    return True


# None until the first page has been looked at: unknown, not "no".
open_page.is_home_assistant = None


def explain_unreachable(url, error):
    """Say something useful about a page that would not open.

    Playwright's own message names the failure and not the cause, and the
    likeliest cause here is a name that means something where the address was
    written and nothing where the browser is running. A remote-access name --
    Tailscale, Nabu Casa, a dynamic DNS host -- resolves on the network it
    belongs to; a container beside Home Assistant is not on it and does not
    need to be, because Home Assistant is right there.
    """
    from urllib.parse import urlsplit

    text = str(error)
    host = urlsplit(url).hostname or url
    if "ERR_NAME_NOT_RESOLVED" in text:
        print(f"\n{host} does not resolve from where this is running.")
        if os.path.exists("/data/options.json"):
            print(
                "This is running as a Home Assistant add-on, on the same "
                "machine as Home Assistant, so it should ask for it directly:\n"
                "    url: http://homeassistant:8123/lovelace/0\n"
                "That name exists inside every add-on. A remote-access address "
                "-- Tailscale, Nabu Casa, dynamic DNS -- is for reaching the "
                "house from outside and does not resolve in here."
            )
        else:
            print(
                "Use an address that resolves on this machine: the local "
                "hostname, or Home Assistant's IP and port."
            )
    elif "ERR_CONNECTION_REFUSED" in text:
        print(f"\nNothing is listening at {host}. Check the port.")
    elif "ERR_CERT" in text or "SSL" in text:
        print(
            f"\nThe certificate for {host} was refused. Inside the house, "
            "http:// and the plain port avoids the question entirely."
        )
    else:
        return
    print()


def changed_rectangles(previous, current, tile=TILE):
    """Where the two pictures differ, as few rectangles as reasonable.

    Tiles that differ are found first, then merged along each row, and rows that
    ended up with the same run merged down the columns -- a card that grew, a
    dialog that opened. One rectangle costs a header, a JPEG's own tables and a
    decode on the board, so a handful of large ones beats a crowd of small ones
    even when they carry a few unchanged pixels along.

    Returns (x, y, w, h) tuples in pixels.
    """
    import numpy as np

    height, width = current.shape[:2]
    tiles_x = (width + tile - 1) // tile
    tiles_y = (height + tile - 1) // tile

    # One pass over the whole picture rather than a comparison per tile: the
    # difference is a single vectorised operation, and the per-tile question is
    # then just whether its block of the answer holds anything.
    #
    # The colour axis is deliberately left alone. Reducing it away first --
    # np.any(..., axis=-1) -- reads every byte again along the one axis that is
    # not contiguous, and costs fifteen times what the comparison itself does:
    # 9.3 ms against 0.6 ms for a 1024x600 frame. Asking a three-dimensional
    # slice whether it holds anything answers the same question for nothing.
    differing = previous != current

    rectangles = []
    for ty in range(tiles_y):
        top = ty * tile
        bottom = min(top + tile, height)
        row = differing[top:bottom]
        run_start = None
        for tx in range(tiles_x):
            left = tx * tile
            right = min(left + tile, width)
            differs = bool(row[:, left:right].any())
            if differs and run_start is None:
                run_start = left
            elif not differs and run_start is not None:
                rectangles.append((run_start, top, left - run_start, bottom - top))
                run_start = None
        if run_start is not None:
            rectangles.append((run_start, top, width - run_start, bottom - top))

    # Stack rows that cover the same columns and touch. The rows are produced in
    # order, so the candidate is always the one just added.
    merged = []
    for x, y, w, h in rectangles:
        if merged:
            mx, my, mw, mh = merged[-1]
            if mx == x and mw == w and my + mh == y:
                merged[-1] = (mx, my, mw, mh + h)
                continue
        merged.append((x, y, w, h))

    # Widen anything the decoder would choke on, backwards so it stays inside
    # the panel. A panel smaller than the minimum keeps whatever it has.
    grown = []
    for x, y, w, h in merged:
        if w < MIN_RECT and width >= MIN_RECT:
            x, w = min(x, width - MIN_RECT), MIN_RECT
        if h < MIN_RECT and height >= MIN_RECT:
            y, h = min(y, height - MIN_RECT), MIN_RECT
        grown.append((x, y, w, h))
    return grown


class TouchMap:
    """Where a contact on the panel is on the page.

    There is no single answer, because the coordinates a board reports depend on
    how its touch controller is wired and on the transform: it was configured
    with -- and every panel differs. A GT911 on one board reports with both axes
    mirrored, the same part on another reports with the axes swapped, a GSL3680
    on a third mirrors only one. ESPHome hands each of them to a listener in
    that board's own display coordinates, which is what LVGL wants and is not
    necessarily the orientation the picture is being shown in.

    So rather than assume, this holds the whole family of possibilities -- four
    turns, each with or without a mirror -- and calibrate() picks the one that
    matches by asking for a few taps. Eight candidates covers every way a panel
    can be mounted and every transform: that can be written for it.
    """

    def __init__(self, page_w, page_h, panel_w, panel_h, rotate, mirror_x, mirror_y):
        self.page_w = page_w
        self.page_h = page_h
        self.panel_w = panel_w
        self.panel_h = panel_h
        self.rotate = rotate
        self.mirror_x = mirror_x
        self.mirror_y = mirror_y

    def to_page(self, px, py):
        # Normalised first, scaled back at the end. A board can report its
        # contacts on a range that is not the panel's -- the Tab5 does, because
        # its touch screen is told to swap the axes while the coordinates are
        # still scaled to a portrait display -- and a mapping that only turns
        # and mirrors cannot express that. Working in fractions of the panel
        # makes the ranges somebody else's problem, and is exactly the identity
        # when the two agree, which is every panel where they do.
        u = px / max(1, self.panel_w - 1)
        v = py / max(1, self.panel_h - 1)
        if self.mirror_x:
            u = 1.0 - u
        if self.mirror_y:
            v = 1.0 - v
        if self.rotate == 90:
            u, v = v, 1.0 - u
        elif self.rotate == 180:
            u, v = 1.0 - u, 1.0 - v
        elif self.rotate == 270:
            u, v = 1.0 - v, u
        x = int(round(u * (self.page_w - 1)))
        y = int(round(v * (self.page_h - 1)))
        # Clamp rather than skip: a contact on the very last column is a real
        # press, and the browser refuses a point outside the viewport.
        return max(0, min(self.page_w - 1, x)), max(0, min(self.page_h - 1, y))

    def options(self):
        """The command line that reproduces this, for pasting into a service."""
        parts = [f"--touch-rotate {self.rotate}"]
        if self.mirror_x:
            parts.append("--touch-mirror-x")
        if self.mirror_y:
            parts.append("--touch-mirror-y")
        return " ".join(parts)

    @staticmethod
    def candidates(page_w, page_h, panel_w, panel_h):
        """Every way this panel's coordinates could relate to the page's.

        Eight: four turns, each with or without a mirror. Mirroring both axes
        is the same map as turning half way round, so mirror_y is left out of
        the search -- it stays accepted on the command line, because a mapping
        already written down should keep working.

        The shapes are not used to narrow this down. They used to be, on the
        reasoning that a quarter turn swaps the axes and so only turns of a
        matching shape are possible -- true of the picture, and not true of the
        coordinates a board reports, which can be on ranges of their own.
        """
        for rotate in (0, 90, 180, 270):
            for mirror_x in (False, True):
                yield TouchMap(
                    page_w, page_h, panel_w, panel_h, rotate, mirror_x, False
                )


def quality_for(url, page_quality, default):
    """The quality this address asks for, or the default.

    Matched on a prefix rather than exactly, because a site is not one address:
    YouTube walks from /results to /watch?v=... without ever being a different
    place, and a dashboard has a path per view. First match wins, so the list
    is read in the order somebody wrote it.
    """
    for prefix, quality in page_quality:
        if url.startswith(prefix):
            return quality
    return default


def send_picture(endpoint, image, frame_id, transpose, panel_w, panel_h, quality):
    """One whole-panel picture, turned the way the frames are."""
    if transpose is not None:
        image = image.transpose(transpose)
    if image.size != (panel_w, panel_h):
        image = image.resize((panel_w, panel_h))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    payload = buffer.getvalue()
    endpoint.write(build_header(panel_w, panel_h, len(payload), frame_id) + payload)


def _target_picture(page_w, page_h, tx, ty, step, total):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (page_w, page_h), (16, 16, 20))
    draw = ImageDraw.Draw(image)
    radius = max(12, min(page_w, page_h) // 16)
    for r, colour in ((radius, (240, 240, 240)), (radius // 2, (220, 60, 60))):
        draw.ellipse((tx - r, ty - r, tx + r, ty + r), outline=colour, width=4)
    draw.line((tx - radius * 2, ty, tx + radius * 2, ty), fill=(240, 240, 240))
    draw.line((tx, ty - radius * 2, tx, ty + radius * 2), fill=(240, 240, 240))
    draw.text(
        (page_w // 2 - 90, page_h - 40),
        f"Touch the circle  ({step} of {total})",
        fill=(200, 200, 200),
    )
    return image


def calibrate(endpoint, page_w, page_h, panel_w, panel_h, transpose, quality):
    """Ask for a few taps and work out which way the contacts come in.

    Three targets, deliberately off-centre and not in a line: a point in the
    middle is fixed by half the candidates at once, and three points that are
    collinear cannot separate a mirror from a turn. Each target is drawn by this
    script and sent as a whole picture, so calibration works before the browser
    is involved at all.
    """
    samples = []
    frame_id = 0
    targets = [(0.25, 0.22), (0.78, 0.30), (0.30, 0.80)]
    for step, (fx, fy) in enumerate(targets, start=1):
        tx, ty = int(fx * page_w), int(fy * page_h)

        # Drain before the target is shown, never after. Anything already in
        # flight belongs to the previous target or to a finger that had not
        # lifted; anything that arrives once the circle is up is the answer, and
        # throwing that away would leave this waiting for a tap that was made.
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            endpoint.read_touches()
            time.sleep(0.05)

        send_picture(
            endpoint,
            _target_picture(page_w, page_h, tx, ty, step, len(targets)),
            frame_id,
            transpose,
            panel_w,
            panel_h,
            quality,
        )
        frame_id = (frame_id + 1) & 0x3FF

        print(f"  target {step} of {len(targets)}: touch the circle on the panel")
        contact = None
        while contact is None:
            for contacts in endpoint.read_touches():
                if contacts:
                    contact = contacts[0]
                    break
            time.sleep(0.02)
        _, px, py = contact
        print(f"    got {px},{py}")
        samples.append(((tx, ty), (px, py)))

        # Wait for the finger to leave, so the next target does not read it.
        lifted = False
        while not lifted:
            for contacts in endpoint.read_touches():
                if not contacts:
                    lifted = True
            time.sleep(0.02)

    scored = []
    for candidate in TouchMap.candidates(page_w, page_h, panel_w, panel_h):
        error = 0
        for (tx, ty), (px, py) in samples:
            x, y = candidate.to_page(px, py)
            error += abs(x - tx) + abs(y - ty)
        scored.append((error / len(samples), candidate))
    scored.sort(key=lambda pair: pair[0])

    best_error, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else float("inf")
    print(f"\nBest match: {best.options()}  (average miss {best_error:.0f} px)")
    if best_error > min(page_w, page_h) * 0.15:
        print(
            "That is a long way off. Check --width and --height match the "
            "component, and that the taps landed on the circles."
        )
    elif runner_up - best_error < best_error:
        print(
            "The next candidate is nearly as good, so this is a guess. Run the "
            "calibration again, taking care to hit the middle of each circle."
        )
    print("Add those options to the command line to keep this.\n")
    return best


class PageAudio:
    """The sound of the page, taken from a sink nothing is listening to.

    Chromium's debugging protocol does not offer audio, so there is no way to
    ask the browser for it. What there is: give the browser a sound card that
    goes nowhere -- a PulseAudio null sink -- and read its monitor. That works
    for any page, needs no extension, and needs no real sound device, which a
    container does not have.

    One sink per panel, named after it, so two panels do not hear each other.

    **Playwright mutes the browser and does not say so.** Measured on the
    shipped build: every launch carries `--mute-audio`, the sink-input appears
    on PulseAudio unmuted and at full volume, and every sample in it is zero.
    A page whose own AnalyserNode reads 0.21 RMS delivers silence to the sink.
    So the capture is worthless without `ignore_default_args=["--mute-audio"]`,
    and that is the whole difference between this working and not: with it,
    peak 9834 of 32767 for a gain of 0.3, and a Goertzel over one second puts
    every bit of the energy at 440 Hz and none at 220, 660, 880 or 1000.
    """

    # 20 ms a block: small enough that the panel's speaker never runs dry
    # waiting for the next one, large enough that the sixteen-byte header is a
    # rounding error rather than a third of what goes out.
    BLOCK_MS = 20

    def __init__(self, name):
        self.sink = "portall_" + "".join(
            c if c.isalnum() else "_" for c in name
        )[:32]
        self.block = AUDIO_RATE * self.BLOCK_MS // 1000 * (AUDIO_BITS // 8) * AUDIO_CHANNELS
        self._module = None
        self._parec = None
        self._blocks = collections.deque(maxlen=25)  # half a second, no more
        self._thread = None
        self.dropped = 0
        self.captured = 0

    def start(self):
        """True if there is sound to be had; False, with a reason, if not."""
        try:
            out = subprocess.run(
                ["pactl", "load-module", "module-null-sink",
                 f"sink_name={self.sink}",
                 f"sink_properties=device.description={self.sink}"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as err:
            print(f"Audio: no PulseAudio here ({err}), the panel stays silent")
            return False
        if out.returncode != 0:
            print(f"Audio: could not make a sink ({out.stderr.strip()}), "
                  f"the panel stays silent")
            return False
        self._module = out.stdout.strip()
        try:
            self._parec = subprocess.Popen(
                ["parec", f"--device={self.sink}.monitor", "--format=s16le",
                 f"--rate={AUDIO_RATE}", f"--channels={AUDIO_CHANNELS}",
                 f"--latency-msec={self.BLOCK_MS}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError as err:
            print(f"Audio: parec would not run ({err}), the panel stays silent")
            self.close()
            return False
        self._thread = threading.Thread(target=self._read, name="page-audio",
                                        daemon=True)
        self._thread.start()
        print(f"Audio: capturing the page through {self.sink} at "
              f"{AUDIO_RATE} Hz, {AUDIO_BITS} bit, {AUDIO_CHANNELS} channel")
        return True

    def _read(self):
        stream = self._parec.stdout
        while True:
            chunk = stream.read(self.block)
            if not chunk:
                return
            # Bounded on purpose. Sound that could not be sent is sound whose
            # moment has passed: a panel that is behind wants the newest
            # samples, not a backlog to catch up through.
            if len(self._blocks) == self._blocks.maxlen:
                self.dropped += 1
            self._blocks.append(chunk)
            self.captured += len(chunk)

    def take(self, limit=8):
        """Whatever has arrived since the last look, oldest first."""
        out = []
        while self._blocks and len(out) < limit:
            out.append(self._blocks.popleft())
        return out

    def close(self):
        if self._parec is not None:
            self._parec.terminate()
            self._parec = None
        if self._module is not None:
            subprocess.run(["pactl", "unload-module", self._module],
                           capture_output=True, timeout=10, check=False)
            self._module = None


class PanelWriter:
    """Keeps the socket's blocking write off the loop.

    ``sendall`` does not return until the board has taken the bytes, and while
    it waits nothing else in the loop happens at all: the browser is not
    pumped, no contact is read, no picture is made. That is harmless while the
    link keeps up and it is the whole of the stutter when it does not. A radio
    that goes away for a couple of hundred milliseconds leaves whatever was in
    flight to drain afterwards, and at video rates that is hundreds of
    kilobytes -- so the loop stops for as long as the drain takes, which is
    what "it freezes and then carries on" looks like from the panel. Measured
    on a real one: a quarter of a five-second window spent inside this call,
    and whole windows at 99%.

    Writing from a thread turns a stall into the right kind of loss. The loop
    never waits; a link that cannot keep up costs *pictures*, and a picture is
    exactly the thing that is safe to lose, because the one after it replaces
    it entirely.

    One picture is held and no more, and it is all-or-nothing. A rectangle is
    never resent, so half a picture would leave that part of the panel wrong
    until the thirty-second redraw -- the same reason the board's own rate
    limit decides per picture and not per rectangle.
    """

    def __init__(self, endpoint):
        self._endpoint = endpoint
        self._wake = threading.Condition()
        # The one picture in hand, as a list of ready-made byte strings. It is
        # cleared only once the last of them has gone out, so a loop asking
        # whether it may hand over another is really asking whether the panel
        # has caught up.
        self._slot = None
        # Sound waits in its own queue and goes out BETWEEN the rectangles of
        # a picture, not behind them. A whole panel is a quarter of a megabyte
        # and takes a tenth of a second to write on a busy link; audio queued
        # behind that arrives in gaps, and a gap is a click. Interleaving costs
        # nothing -- they are two types on one wire and the board reads
        # whichever turns up.
        self._audio = collections.deque(maxlen=25)
        self._error = None
        self._stop = False
        # Still worth counting even though the loop no longer pays it: it is
        # the measure of whether the panel is the limit.
        self.blocked = 0.0
        # When the write in progress began, or None between writes. Without
        # this the whole of a long write is credited to the window in which it
        # FINISHES: a panel that took twenty-seven seconds to accept a picture
        # printed "panel wait 538%" in one five-second window and nothing in
        # the four before it, which is a percentage of time that cannot exist
        # and a diagnosis nobody could trust. Reported from a panel, on
        # YouTube, where the stalls are longest.
        self._writing_since = None
        self._thread = threading.Thread(
            target=self._run, name="panel-writer", daemon=True
        )
        self._thread.start()

    def _run(self):
        while True:
            with self._wake:
                while self._slot is None and not self._audio and not self._stop:
                    self._wake.wait()
                if self._stop:
                    return
                blobs = self._slot
            if blobs is None:
                # Woken by sound alone, between pictures.
                try:
                    self._drain_audio()
                except OSError as err:  # noqa: PERF203
                    with self._wake:
                        self._error = err
                        self._wake.notify_all()
                    return
                continue
            with self._wake:
                self._writing_since = time.monotonic()
            try:
                for blob in blobs:
                    self._drain_audio()
                    self._endpoint.write(blob)
                self._drain_audio()
            except OSError as err:  # noqa: PERF203
                # Handed to the loop, which owns reconnecting.
                with self._wake:
                    self._error = err
                    self._slot = None
                    self._wake.notify_all()
                return
            with self._wake:
                if self._writing_since is not None:
                    self.blocked += time.monotonic() - self._writing_since
                    self._writing_since = None
                self._slot = None
                self._wake.notify_all()

    def _drain_audio(self):
        """Everything waiting, written now. Called from the writer thread."""
        while True:
            with self._wake:
                if not self._audio:
                    return
                block = self._audio.popleft()
            self._endpoint.write(build_audio_header(len(block)) + block)

    def offer_audio(self, block):
        """Hand over one block of sound. Never blocks, never waits its turn."""
        with self._wake:
            if self._error is not None:
                raise self._error
            self._audio.append(block)
            self._wake.notify()

    def ready(self):
        """Whether the last picture is out and another may be handed over."""
        with self._wake:
            if self._error is not None:
                raise self._error
            return self._slot is None

    def offer(self, blobs):
        """Hand over a whole picture. Only call this after ``ready()``."""
        with self._wake:
            if self._error is not None:
                raise self._error
            self._slot = blobs
            self._wake.notify()

    def take_blocked(self):
        """Seconds spent writing since the last time this was asked.

        Including the write still going on, which is the half that was missing:
        a write is credited as it accrues rather than when it ends, so a stall
        longer than a window is spread across the windows it actually spans
        instead of arriving all at once as a percentage above a hundred.
        """
        with self._wake:
            spent, self.blocked = self.blocked, 0.0
            if self._writing_since is not None:
                now = time.monotonic()
                spent += now - self._writing_since
                # The rest of this write belongs to the next window.
                self._writing_since = now
        return spent

    def close(self):
        with self._wake:
            self._stop = True
            self._wake.notify_all()
        # Never wait on a write that may itself be stuck: the socket is about
        # to be closed under it, and the thread is a daemon.
        self._thread.join(timeout=0.5)


class Screencast:
    """Frames pushed by the browser, rather than pulled out of it.

    Page.captureScreenshot forces a full paint, a compose and an encode every
    time it is called, whether or not anything moved -- and on a live dashboard
    it is slow enough to fall behind and eventually fail outright with "Unable to
    capture screenshot". Chromium's screencast is the other way round: it hands
    over a frame when the page actually changes and stays silent when it does
    not, which is exactly the question this sender is asking.

    Frames must be acknowledged or the stream stops after a handful. The
    acknowledgement is deliberately not sent from the event handler -- that runs
    inside Playwright's own dispatch -- but from the loop, right after pumping.
    """

    def __init__(self, page, width, height, quality):
        self._session = page.context.new_cdp_session(page)
        self._width = width
        self._height = height
        self._quality = quality
        self._latest = None
        self._unacked = []
        # How many pictures the browser has actually made. The count that
        # matters is this one against the count sent: the gap between them is
        # work paid for and thrown away, and it is invisible from every other
        # number this prints.
        self.produced = 0
        # How long the browser takes to hand a picture over after being let
        # go, as a running average. The acknowledgement has to be sent that
        # long before a picture is wanted, not an instant before, or every one
        # of them arrives a whole interval late -- see request().
        self.lead = 0.030
        self._asked_at = None
        self._session.on("Page.screencastFrame", self._on_frame)
        self._running = True
        self._start()

    def _start(self):
        # JPEG, and this is where most of the server's CPU went before.
        #
        # PNG was chosen on the belief that a JPEG would make every tile differ
        # from the last by a pixel or two of ringing, so nothing would ever look
        # unchanged. That is not what happens. JPEG is a block transform: an 8x8
        # block whose pixels went in identical comes out identical, because the
        # coefficients are the same numbers. Ringing is deterministic, not
        # noise. Measured on a dashboard-shaped 1024x600 picture with one clock
        # digit changed, at every quality from 60 to 95 and with and without
        # chroma subsampling: exactly the one tile holding the digit differed,
        # never another. Subsampling widens the unit to a 16x16 MCU, and the
        # tile is a multiple of 16, so the spread stays inside the tile that
        # already changed.
        #
        # What PNG did cost was real. Decoding it here is the half that was
        # measured in this process: 7.2 ms a frame against 2.2 for JPEG, plus
        # 1.8 more because a PNG arrives needing a convert() that a JPEG does
        # not. The browser's own encode is the larger half and is inferred
        # rather than measured -- the same 1024x600 picture costs libpng 22.5 ms
        # and libjpeg 1.6 through Pillow, and Chromium uses those same two
        # libraries on the same pixels. Call the whole of it twenty-odd
        # milliseconds of a core per frame, spent on a distinction the panel
        # cannot show.
        options = {
            "maxWidth": self._width,
            "maxHeight": self._height,
            "everyNthFrame": 1,
        }
        if self._quality:
            options["format"] = "jpeg"
            options["quality"] = self._quality
        else:
            options["format"] = "png"
        self._session.send("Page.startScreencast", options)

    def _on_frame(self, params):
        if self._asked_at is not None:
            # Smoothed, because one frame's paint says little: a page settles,
            # a card grows, the machine gets busy. Weighted towards the recent
            # so it follows the page it is actually on.
            self.lead = 0.7 * self.lead + 0.3 * (time.monotonic() - self._asked_at)
            self._asked_at = None
        self._latest = base64.b64decode(params["data"])
        self._unacked.append(params["sessionId"])
        self.produced += 1

    def take(self):
        """The newest frame since the last call, or None if nothing moved."""
        frame, self._latest = self._latest, None
        return frame

    def request(self, discard=False):
        """Let the browser produce another frame.

        The acknowledgement is not a formality: it is the flow control. Chromium
        keeps about three frames in flight and then waits to be told they
        arrived, so acknowledging on every turn of the loop -- a hundred and
        twenty times a second -- asks it to paint and encode at its own full
        rate, and the frame limit then throws the surplus away once it has
        already been paid for. Measured on an animated page at 800x1280: 59.8
        frames a second produced when acknowledging freely, 28.5 when
        acknowledging at ten a second and 12.0 at four. The pictures that stop
        being made are exactly the ones that were being discarded.

        discard drops whatever is already in hand along with the
        acknowledgement. That is for the moment a press has just been replayed:
        a frame waiting at that instant was painted before the finger landed
        and shows nothing of it, so letting it through would spend the free
        pass on the wrong picture -- the mistake that once made a press feel
        205 ms slow instead of 105. Nothing is lost by dropping it, because a
        picture that is not sent does not advance what the next difference is
        measured against.
        """
        if discard:
            self._latest = None
        if self._unacked and self._asked_at is None:
            self._asked_at = time.monotonic()
        for session_id in self._unacked:
            try:
                self._session.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception:  # noqa: BLE001 - a closed page is handled by the caller
                pass
        self._unacked.clear()

    def restart(self):
        """Ask for a picture of the page as it is now, whatever it is doing.

        A screencast hands over a frame when the page changes, and a page that
        has just been navigated to and then stands still changes exactly once.
        Measured: two frames in the two seconds after arriving at a still page,
        **none** in the two seconds after that, and one more every time the
        screencast is restarted.

        So at a navigation the frame in hand is not something that can be
        thrown away and asked for again -- it is the only picture that page is
        ever going to send. The corner gesture did exactly that: it navigated
        home and then discarded, the way it does after a press, and the panel
        went on showing the dashboard it had left for ever. Reported as Home
        Assistant freezing and not coming back.
        """
        if not self._running:
            return
        self._latest = None
        self._unacked.clear()
        try:
            self._session.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001 - a closed page needs no stopping
            pass
        self._start()

    def freeze_animations(self):
        """Hold every animation on the page still.

        A dashboard with a pulsing icon or a spinner never stops changing, so
        the screencast never goes quiet and the whole pipeline runs for a
        picture nobody is watching. Measured on an animated page: 55.8 frames a
        second, against 0.2 once frozen.

        This goes through the protocol's animation domain rather than through
        CSS, because CSS cannot reach it. Home Assistant builds its cards from
        custom elements, and a rule added to the document does not cross into a
        shadow root -- measured: 60.2 frames a second with the stylesheet in
        place, which is to say no effect whatever. prefers-reduced-motion is no
        better, being only a request the page is free to ignore. The animation
        domain acts on the engine that drives them and does not care where the
        keyframes were declared.

        What it does not reach is anything that is not an animation. A card
        that draws itself on a canvas from requestAnimationFrame, a camera
        tile, a video: none of them go through that engine and none of them
        stop. Measured on one page carrying all three kinds at once -- a
        keyframe animation in a shadow root, a canvas loop and a setInterval
        rewriting text -- 57.3 frames a second before and 59.2 after, because
        the canvas alone is enough to keep the page moving. Each on its own,
        frozen: 0.2 for the animation, 59.7 for the canvas, unchanged for the
        interval. This is worth trying and worth measuring, not worth
        assuming.
        """
        self._session.send("Animation.enable")
        self._session.send("Animation.setPlaybackRate", {"playbackRate": 0})

    def pause(self):
        """Stop the browser producing frames at all."""
        if not self._running:
            return
        self._running = False
        self._latest = None
        self._unacked.clear()
        try:
            self._session.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001 - a closed page needs no stopping
            pass

    def resume(self):
        if self._running:
            return
        self._running = True
        self._start()

    def stop(self):
        self.pause()



# The on-screen keyboard.
#
# A panel has no keys, so a page whose point is to type -- the dashboard's
# search, Assist, the search box of an ordinary web site -- is a dead end. The
# keyboard has to be drawn by the browser, because the panel shows nothing else,
# and it has to put characters into a field that may live several shadow roots
# deep inside a Home Assistant card.
#
# Four earlier attempts failed, and every one of them failed the same way: the
# keyboard was built as something the page could interact with, so it fought the
# page for focus, and the last attempt moved it into a native modal <dialog>,
# which makes everything outside it inert and broke the only two cases that
# existed.
#
# So this one is not something the page can interact with at all. The overlay is
# inert decoration: pointer-events: none, no focus, no listeners, no handlers.
# It cannot take focus away from the field because nothing ever reaches it. What
# reaches it is the sender: a contact that lands inside the keyboard's rectangle
# is never replayed as a click, it is turned into a keystroke and delivered with
# the protocol's own input domain, which puts text into whatever has focus
# without needing to find the element first. The whole shadow-root problem
# simply does not arise.
#
# The overlay goes in the top layer through the popover API rather than on a
# large z-index, because Home Assistant opens its dialogs as native modals and
# the top layer paints above every z-index there is. A manual popover is in that
# same layer without making anything inert -- which is exactly the distinction
# the fourth attempt got wrong.

# The layouts. Each row is a list of (label, width in key units, what it does);
# a row is laid out across the full width whatever its units add up to, so rows
# need not agree on a total.
_SHIFT, _BACK, _ENTER, _SPACE, _HIDE = "shift", "back", "enter", "space", "hide"
_LAYER = "layer"


def _letters(text):
    return [(c, 1.0, "char") for c in text]


KEYBOARD_LAYOUTS = {
    "qwerty": [
        _letters("1234567890"),
        _letters("qwertyuiop"),
        _letters("asdfghjkl"),
        # The erase key carries the glyph rather than a word, because a word
        # has to be in some language and this one was reported as missing
        # outright: "tu as oublie une touche celle de supprimer je le trouve
        # pas". It was there, labelled Back. The symbol needs no translating
        # and no reading.
        [("Shift", 1.5, _SHIFT)] + _letters("zxcvbnm") + [("\u232b", 1.5, _BACK)],
        [("?123", 1.5, _LAYER), ("Hide", 1.5, _HIDE)] + _letters("@-_")
        + [("", 4.0, _SPACE)] + _letters(".") + [("Enter", 2.0, _ENTER)],
    ],
    "azerty": [
        _letters("1234567890"),
        _letters("azertyuiop"),
        _letters("qsdfghjklm"),
        [("Shift", 1.5, _SHIFT)] + _letters("wxcvbn'-") + [("\u232b", 1.5, _BACK)],
        # The at sign is on the bottom row here, where qwerty carries it in
        # the same place. It was missing, and a keyboard you cannot type an
        # address into is a keyboard you cannot sign in with -- which is the
        # whole point of keeping a profile.
        [("?123", 1.5, _LAYER), ("Hide", 1.5, _HIDE)] + _letters("@éèàç")
        + [("", 3.5, _SPACE)] + _letters(".") + [("Enter", 2.0, _ENTER)],
    ],
    # The same five rows, so the band a contact is tested against never moves,
    # and every character a password is likely to want. Asked for by somebody
    # trying to sign into a Jellyfin server from a panel, which is the first
    # time anything here needed more than a search box does. The layers key
    # sits where a phone puts it, bottom left.
    "symbols": [
        _letters("1234567890"),
        _letters("!\"#$%&'()*"),
        _letters("+-=/:;,.?@"),
        [("ABC", 1.5, _LAYER)] + _letters("_[]{}<>\\")
        + [("\u232b", 1.5, _BACK)],
        [("Hide", 1.5, _HIDE)] + _letters("~|`^")
        + [("", 4.0, _SPACE)] + [("Enter", 2.0, _ENTER)],
    ],
}

# The overlay is a fixed share of the page's height whatever the panel is, so a
# key is about the same fraction of a finger everywhere: five rows at 7.5% each.
# Below this a key on a 1024x600 panel stops being reliably hittable.
KEY_MIN_H = 40
KEY_H_FRACTION = 0.075
# Drawn between the keys, not taken out of them: the seam is still hittable.
GAP = 3
# How long focus has to have been gone before the keys are taken down. Long
# enough to ride out a re-render that blurs a field for a frame, short enough
# that putting the keyboard away still feels immediate.
BLUR_GRACE_S = 0.4

# Installed on the context rather than the page, so it survives every
# navigation -- including the one that parks the page on about:blank while the
# panel sleeps, and the one that brings it back.
# How long the corner shows itself for after a page arrives. The gesture is
# invisible otherwise: it was asked for, so the person who asked knows it, and
# nobody else in the house ever would. Long enough to be noticed while somebody
# is walking up to the panel, short enough not to become furniture.
HOME_HINT_SECONDS = 5.0

HOME_HINT_JS = r"""
(() => {
  const ID = '__portall_home';
  // The same rules as the keyboard, for the same reasons, and this is the one
  // place they are worth restating: the mark is decoration and nothing else.
  // pointer-events: none, no listeners, no focus. A contact in the corner is
  // decided by arithmetic in the sender before the page is told anything, so
  // the mark must never be in a position to take a tap that was meant for what
  // is underneath it -- which on Home Assistant is the menu button.
  window.__portallHome = {
    dress(css) {
      // Through the CSSOM, because a page's style-src can forbid a <style>
      // element and cannot forbid this.
      try {
        if (!this.sheet) {
          this.sheet = new CSSStyleSheet();
          document.adoptedStyleSheets = [...document.adoptedStyleSheets, this.sheet];
        }
        this.sheet.replaceSync(css);
        return;
      } catch (err) { /* the element below */ }
      let s = document.getElementById(ID + '_css');
      if (!s) {
        s = document.createElement('style');
        s.id = ID + '_css';
        document.documentElement.appendChild(s);
      }
      s.textContent = css;
    },
    show(css, fraction) {
      let d = document.getElementById(ID);
      if (!d) {
        d = document.createElement('div');
        d.id = ID;
        d.setAttribute('popover', 'manual');
        document.documentElement.appendChild(d);
      }
      this.dress(css);
      d.style.removeProperty('display');
      // The fill is a variable rather than a class, so the sender owns the
      // number and the page only draws it.
      d.style.setProperty('--fill', String(fraction));
      let layered = false;
      try {
        // Re-entered on every change: the top layer stacks in entry order, so
        // a dialog opened after this one would otherwise paint over it.
        if (d.matches(':popover-open')) d.hidePopover();
        d.showPopover();
        layered = true;
      } catch (err) { /* the stylesheet carries a z-index for this case */ }
      return layered;
    },
    hide() {
      const d = document.getElementById(ID);
      if (d) {
        try { if (d.matches(':popover-open')) d.hidePopover(); } catch (err) {}
        d.style.setProperty('display', 'none');
      }
    },
  };
})();
"""


class HomeHint:
    """The mark in the corner that says the way home is there.

    It draws exactly the rectangle the sender tests, from the same fraction, so
    what somebody presses and what they see cannot drift apart -- the rule the
    keyboard's geometry already lives under.

    Three states and no animation: shown faintly when a page arrives, filling
    while a finger is held in the corner, gone otherwise. No animation because
    a corner that pulses is a corner that repaints, and a repaint is a
    rectangle on the wire for as long as the panel is awake.
    """

    def __init__(self, page, page_w, page_h):
        self._page = page
        self._w = max(1.0, page_w * HOME_CORNER_FRACTION)
        self._h = max(1.0, page_h * HOME_CORNER_FRACTION)
        # What the page was last told, so a hold costs ten round trips rather
        # than one per turn of the loop.
        self._at = None
        self.broken = False
        self._warned = False

    def _css(self):
        # The wedge is anchored in the corner and drawn no larger than the
        # rectangle that is actually tested, so it is never a promise the
        # gesture does not keep.
        size = min(self._w, self._h)
        return f"""
        #__portall_home {{
          position: fixed; top: 0; left: 0; margin: 0; padding: 0; border: 0;
          width: {size:.0f}px; height: {size:.0f}px;
          pointer-events: none;
          background: transparent;
          overflow: visible; z-index: 2147483646;
          border-bottom-right-radius: 100%;
          /* Two layers: the quarter that fills as the finger is held, over a
             faint quarter that says where to press. Both are conic gradients
             from the corner itself, so the fill sweeps round the way a
             progress ring does. */
          /* From .25turn, which is three o'clock. A conic gradient centred on
             the top-left corner only has one visible quadrant -- the one
             between three o'clock and six -- and starting the sweep anywhere
             else puts the whole of it off the screen. The first version
             started at .5turn and drew a mark that existed, was open, was 84
             pixels square and painted absolutely nothing. */
          background-image:
            conic-gradient(from .25turn at 0 0,
              rgba(255,255,255,.85) 0turn,
              rgba(255,255,255,.85) calc(var(--fill, 0) * .25turn),
              transparent calc(var(--fill, 0) * .25turn)),
            conic-gradient(from .25turn at 0 0,
              rgba(255,255,255,.16) 0turn,
              rgba(255,255,255,.16) .25turn,
              transparent .25turn);
          /* Against a white page as well as a dark one. */
          filter: drop-shadow(0 0 2px rgba(0,0,0,.55));
        }}
        #__portall_home::backdrop {{ background: transparent; }}
        """

    def set(self, fraction):
        """Draw the corner filled this far, or take it away with None."""
        if self.broken:
            return
        # Rounded, because the eye cannot see a fiftieth of a quarter circle
        # and every change is a round trip into the browser and a rectangle on
        # the wire.
        step = None if fraction is None else round(min(1.0, max(0.0, fraction)), 1)
        if step == self._at:
            return
        self._at = step
        try:
            if step is None:
                self._page.evaluate("window.__portallHome.hide()")
                return
            layered = self._page.evaluate(
                "a => window.__portallHome.show(a[0], a[1])", [self._css(), step]
            )
        except Exception as err:  # noqa: BLE001 - an accessory, never the picture
            # The same rule the keyboard lives under, learnt the same expensive
            # way: a page that will not have an overlay drawn must cost the
            # overlay and nothing else.
            self.broken = True
            print(f"Home: this page will not have the corner mark drawn "
                  f"({err}). The gesture still works.")
            return
        if not layered and not self._warned:
            self._warned = True
            print("Home: the corner mark is drawn in the page rather than in "
                  "the top layer, so a modal dialog can cover it. The gesture "
                  "is unaffected.")

    def forget(self):
        """After a navigation there is a new document and nothing is drawn."""
        self._at = None


KEYBOARD_INIT_JS = r"""
(() => {
  const ID = '__udisp_kb';
  function box() {
    let d = document.getElementById(ID);
    if (!d) {
      d = document.createElement('div');
      d.id = ID;
      // Manual, so nothing the page does dismisses it and nothing outside it
      // becomes inert. This is the whole reason it is a popover and not a div
      // on a large z-index: Home Assistant's dialogs are native modals, and
      // the top layer paints above any z-index whatever.
      d.setAttribute('popover', 'manual');
      document.documentElement.appendChild(d);
    }
    return d;
  }
  // Focus was only ever looked at after a tap, on the reasoning that a tap is
  // the only thing that can move it. True, but not the whole story: what a tap
  // starts can finish much later -- a dialog that animates in, an editor that
  // takes focus once its document has loaded -- and a look taken 450 ms after
  // the finger lifted misses all of it. So the page says so instead. The
  // binding is installed on the context, which means it exists in every frame,
  // including the iframe a Home Assistant ingress add-on lives in.
  const tell = () => {
    if (window.__udispFocusChanged) {
      try { window.__udispFocusChanged(); } catch (err) { /* not installed */ }
    }
  };
  addEventListener('focusin', tell, true);
  // focusout runs before the new focus lands, so ask again once it has.
  addEventListener('focusout', () => setTimeout(tell, 0), true);

  window.__udispKb = {
    // The stylesheet goes in through the CSSOM rather than as a <style>
    // element, because a page's style-src can forbid the element and cannot
    // forbid this. Falls back to the element where constructable sheets are
    // not available.
    dress(css) {
      try {
        if (!this.sheet) {
          this.sheet = new CSSStyleSheet();
          document.adoptedStyleSheets = [...document.adoptedStyleSheets, this.sheet];
        }
        this.sheet.replaceSync(css);
        return;
      } catch (err) { /* the element below */ }
      let s = document.getElementById(ID + '_css');
      if (!s) {
        s = document.createElement('style');
        s.id = ID + '_css';
        document.documentElement.appendChild(s);
      }
      s.textContent = css;
    },
    show(css, keys) {
      const d = box();
      this.dress(css);
      // Built out of the DOM, never out of an HTML string. YouTube -- and
      // Google, and GitHub, and a good many others -- require Trusted Types,
      // and there `d.innerHTML = ...` throws outright: it took the whole
      // sender down every thirteen seconds, restarting for ever. Nothing
      // below can be refused by any policy.
      d.replaceChildren();
      for (const k of keys) {
        const e = document.createElement('div');
        e.className = 'k' + (k.w ? ' f' : '') + (k.s ? ' s' : '');
        e.setAttribute('data-i', k.i);
        e.style.cssText = k.s;
        e.textContent = k.t;
        d.appendChild(e);
      }
      // Whatever hide() left behind. It sets display:none INLINE, and an
      // inline style beats any selector in the sheet -- so failing to clear
      // it here was a keyboard that could never be seen again for the rest of
      // the page's life, however many times it was asked to show. Reported
      // from a panel as having to restart the add-on, which is the only thing
      // that ever gave it a new document.
      d.style.removeProperty('display');
      let layered = false;
      try {
        // Shown again even when it is already showing, because the top layer
        // stacks in the order things entered it: a dialog opened after the
        // keyboard would otherwise paint over the keys and its backdrop would
        // grey them. Leaving and re-entering puts it back on top. It costs
        // nothing visible -- both happen in one task, so no frame is painted
        // in between.
        if (d.matches(':popover-open')) d.hidePopover();
        d.showPopover();
        layered = true;
      } catch (err) {
        // The popover was refused this once. The attribute stays: refusing it
        // once is no reason never to try again, and taking it away was how a
        // single failure turned into a keyboard that could never reach the top
        // layer for the rest of the page's life. The style rule below carries
        // both a display and the largest z-index there is, so what is left is
        // an ordinary fixed element above everything except the top layer --
        // wrong only against a modal dialog, instead of invisible against the
        // whole of Home Assistant.
      }
      // And the one thing a z-index still cannot beat is a modal dialog's
      // backdrop, which is in the top layer with the dialog. Measured on a
      // dialog dimming at 60%: the keys came through at 40% of their colour,
      // which on a dark dashboard is indistinguishable from not being drawn
      // at all. So in this mode the page loses its dim behind a dialog while
      // the keyboard is up, which is a great deal better than losing the
      // keyboard. Only in this mode: one that reached the top layer is above
      // the backdrop already, and the page keeps its dim.
      document.documentElement.classList.toggle('__udisp_kb_flat', !layered);
      return layered;
    },
    hide() {
      const d = document.getElementById(ID);
      if (!d) return;
      try {
        if (d.matches(':popover-open')) d.hidePopover();
      } catch (err) { /* falls through to the style below */ }
      d.style.display = 'none';
      d.replaceChildren();
    },
    highlight(index) {
      const d = document.getElementById(ID);
      if (!d) return;
      for (const k of d.querySelectorAll('.on')) k.classList.remove('on');
      if (index >= 0) {
        const k = d.querySelector('[data-i="' + index + '"]');
        if (k) k.classList.add('on');
      }
    },
    probe() {
      const d = document.getElementById(ID);
      if (!d) return {missing: true};
      const r = d.getBoundingClientRect();
      const s = getComputedStyle(d);
      let open = null;
      try { open = d.matches(':popover-open'); } catch (err) { open = 'unsupported'; }
      return {
        open: open,
        rect: [Math.round(r.x), Math.round(r.y),
               Math.round(r.width), Math.round(r.height)],
        display: s.display, visibility: s.visibility, opacity: s.opacity,
        z: s.zIndex, parent: d.parentElement ? d.parentElement.tagName : null,
        dialogs: document.querySelectorAll('dialog[open]').length,
        view: [innerWidth, innerHeight],
      };
    },
    // What has focus, and the road taken to it. The road is the useful half
    // when the answer is no: "nothing is waiting for text" says nothing about
    // why, and the only way to find out what a panel actually tapped is to be
    // told what it landed on. Measured on a real dashboard's search field, the
    // road is seven elements long and every step of it is a shadow root:
    // HOME-ASSISTANT > HOME-ASSISTANT-MAIN > HA-CONFIG-ENTITIES >
    // HASS-TABS-SUBPAGE-DATA-TABLE > SEARCH-INPUT > HA-TEXTFIELD > INPUT.
    focus() {
      const road = [];
      let a = document.activeElement;
      while (a) {
        road.push(a.tagName + (a.id ? '#' + a.id : ''));
        if (a.shadowRoot) {
          if (a.shadowRoot.activeElement) { a = a.shadowRoot.activeElement; continue; }
          break;
        }
        // A custom element with nothing visible inside it may be holding a
        // closed shadow root. Say so rather than reporting a bare tag name.
        if (a.tagName.indexOf('-') !== -1) road.push('(shadow root ferme?)');
        break;
      }
      let yes = false;
      if (a) {
        const tag = a.tagName;
        if (a.isContentEditable || tag === 'TEXTAREA') {
          yes = true;
        } else if (tag === 'INPUT') {
          const type = (a.getAttribute('type') || 'text').toLowerCase();
          yes = ['button', 'checkbox', 'radio', 'submit', 'reset', 'range',
                 'color', 'file', 'image', 'hidden'].indexOf(type) === -1;
        } else {
          // Widened for a field this cannot see into. A closed shadow root
          // stops the descent at the host, and a host that says it is a text
          // box, or that has a text box in it, is one.
          const role = (a.getAttribute('role') || '').toLowerCase();
          if (['textbox', 'searchbox', 'combobox'].indexOf(role) !== -1) {
            yes = true;
          } else if (tag.indexOf('-') !== -1) {
            const inner = 'input:not([type=button]):not([type=checkbox]),'
                        + 'textarea,[contenteditable=""],[contenteditable=true]';
            yes = !!(a.querySelector && a.querySelector(inner))
               || !!(a.shadowRoot && a.shadowRoot.querySelector(inner));
          }
        }
      }
      return {yes: yes, road: road.join(' > ') || '(rien)'};
    },
    typable() {
      return window.__udispKb.focus().yes;
    },
  };
})();
"""


class _Safe:
    """Swallows what a keystroke may throw.

    A page that closes, navigates or refuses mid-press must cost that
    keystroke and not the panel's picture.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        method = getattr(self._wrapped, name)

        def call(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except Exception as err:  # noqa: BLE001 - one lost keystroke
                print(f"Keyboard: the page would not take that key ({err})")

        return call


class Keyboard:
    """Draws the keys, and turns a contact inside them into a keystroke.

    The geometry is computed here and the same numbers are used twice: once to
    position each key absolutely in the overlay, and once to decide which key a
    contact landed on. There is no second source of truth to drift from the
    first, which is what makes hit testing arithmetic rather than a round trip
    to the page.
    """

    def __init__(self, page, page_w, page_h, layout):
        self._page = page
        self._letters = KEYBOARD_LAYOUTS[layout]
        self._symbols = False
        self._shift = False
        self._pending = []
        # Said once per page, the first time the keyboard goes up.
        self._reported = False
        self._warned = False
        # When focus was first seen to have left, plus what the last look
        # found and how many have been taken, so the log traces focus moving
        # instead of repeating itself.
        self._blur_at = None
        self._road = None
        self._looks = 0
        self._said = 0
        # Hide was pressed while the field kept its focus, so the keys are to
        # stay down until they are asked for again.
        self.dismissed = False
        # Set when a page refuses to have it drawn at all. Cleared by the next
        # page, since the refusal belongs to the document and not to the panel.
        self.broken = False
        self.visible = False
        self._key_h = max(KEY_MIN_H, round(page_h * KEY_H_FRACTION))
        # Both layers are five rows, deliberately: the band a contact is
        # tested against must not move when the layer does, or a finger on its
        # way to a key would land on the page instead.
        self.height = self._key_h * len(self._letters)
        self.top = page_h - self.height
        self.width = page_w
        self._lay()

    def _lay(self):
        """Position every key of the layer now showing."""
        rows = KEYBOARD_LAYOUTS["symbols"] if self._symbols else self._letters
        self.keys = []
        for row, entries in enumerate(rows):
            units = sum(u for _, u, _ in entries) or 1.0
            x = 0.0
            for label, u, action in entries:
                w = self.width * u / units
                self.keys.append({
                    "i": len(self.keys),
                    "x": x,
                    "y": self.top + row * self._key_h,
                    "w": w,
                    "h": self._key_h,
                    "label": label,
                    "action": action,
                })
                x += w

    def contains(self, x, y):
        return self.visible and y >= self.top

    def hit(self, x, y):
        for key in self.keys:
            if (key["x"] <= x < key["x"] + key["w"]
                    and key["y"] <= y < key["y"] + key["h"]):
                return key
        return None

    def _label(self, key):
        if key["action"] != "char":
            return key["label"]
        return key["label"].upper() if self._shift else key["label"]

    def _render(self):
        """The stylesheet, and one entry per key -- not markup.

        Nothing here is an HTML string any more. A page may forbid building
        elements out of one: YouTube requires Trusted Types, and the assignment
        threw "This document requires \'TrustedHTML\' assignment", which took
        the whole sender down with it rather than merely losing the keyboard.
        Handing over the pieces and letting the page put nodes together with
        the DOM cannot be refused by any policy, and the escaping goes away
        with it.
        """
        font = max(14, int(self.keys[0]["h"] * 0.42))
        css = (
            "#__udisp_kb{position:fixed;left:0;right:0;bottom:0;top:auto;"
            "margin:0;padding:0;border:0;width:100%;max-width:100%;"
            f"height:{self.height}px;max-height:none;overflow:visible;"
            "background:#15161a;color:#f2f2f5;display:block;"
            # Only reached when the popover was refused -- the top layer is
            # above every z-index and does not need one. Without it that
            # fallback is a fixed element at z-index auto, which Home
            # Assistant's own shell paints straight over: invisible, while the
            # keys go on working, because the hit test is arithmetic here and
            # never asks the page what is on top.
            "z-index:2147483647;"
            # Nothing here is ever the target of anything: the sender takes the
            # contact before the page sees it. This is what keeps the field's
            # focus, and it is the difference between this attempt and the
            # four before it.
            "pointer-events:none;"
            "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
            "-webkit-font-smoothing:antialiased;}"
            "#__udisp_kb::backdrop{background:transparent;}"
            "html.__udisp_kb_flat dialog::backdrop"
            "{background:transparent !important;}"
            "#__udisp_kb .k{position:absolute;box-sizing:border-box;"
            "border:1px solid #33353f;border-radius:8px;background:#2a2c34;"
            f"font-size:{font}px;line-height:1;display:flex;"
            "align-items:center;justify-content:center;overflow:hidden;}"
            # Everything that is not a letter, so the two kinds of key are
            # told apart at a glance.
            "#__udisp_kb .f{background:#1e2027;color:#b9bcc6;}"
            # A word does not fit a key at the size a letter wants; a single
            # symbol does, and wants it -- an erase key shrunk to the size of
            # the word Shift is one nobody finds.
            f"#__udisp_kb .s{{font-size:{max(11, int(font * 0.6))}px;}}"
            "#__udisp_kb .on{background:#3d7de0;color:#fff;}"
        )
        keys = []
        for key in self.keys:
            # Drawn inset, hit whole. The gap between keys belongs to the key
            # it is drawn out of, so a finger landing on a seam still presses
            # something rather than nothing.
            label = self._label(key)
            keys.append({
                "i": key["i"],
                "w": key["action"] != "char",
                "s": len(label) > 1,
                "t": label,
                "s": (f'left:{key["x"] + GAP:.1f}px;'
                      f'top:{key["y"] - self.top + GAP:.1f}px;'
                      f'width:{key["w"] - 2 * GAP:.1f}px;'
                      f'height:{key["h"] - 2 * GAP}px'),
            })
        return css, keys

    def _show(self):
        css, keys = self._render()
        try:
            layered = self._page.evaluate(
                "a => window.__udispKb.show(a[0], a[1])", [css, keys]
            )
        except Exception as err:  # noqa: BLE001 - never worth the whole sender
            # The keyboard is an accessory. A page that will not let it be
            # drawn must cost the keyboard and nothing else -- YouTube's
            # Trusted Types policy took the entire sender down every thirteen
            # seconds, restarting for ever, over an overlay nobody could have
            # missed being absent.
            self.broken = True
            print(f"Keyboard: this page will not have it drawn ({err}). "
                  f"Carrying on without it.")
            return
        self.visible = True
        if not layered and not self._warned:
            self._warned = True
            print(
                "Warning: this browser refused the popover API, so the "
                "keyboard is drawn in the page rather than in the top layer. "
                "It works, but a native modal dialog can paint over it. "
                "Rebuilding the add-on without a cached image gets a newer "
                "Chromium and this goes away."
            )
        if not self._reported:
            self._reported = True
            self._describe()

    def _describe(self):
        """Say once what the overlay actually became.

        A keyboard painted underneath the page looks exactly like one that was
        never drawn: the keys still work either way, because the hit test is
        arithmetic in here and never asks the page what is on top of what. The
        only way to tell the two apart from a log is to ask the page.
        """
        try:
            s = self._page.evaluate("window.__udispKb.probe()")
        except Exception as err:  # noqa: BLE001 - the answer is the diagnosis
            print(f"Keyboard: the page would not answer ({err})")
            return
        if not s or s.get("missing"):
            print("Keyboard: the overlay is not in the page at all")
            return
        x, y, w, h = s["rect"]
        print(
            f"Keyboard: {w}x{h} at {x},{y} in a {s['view'][0]}x{s['view'][1]} "
            f"page, top layer {s['open']}, display {s['display']}, "
            f"visibility {s['visibility']}, opacity {s['opacity']}, "
            f"z-index {s['z']}, under <{s['parent']}>, "
            f"{s['dialogs']} modal dialog(s) open"
        )

    def hide(self):
        self._blur_at = None
        if not self.visible:
            return
        self.visible = False
        self._shift = False
        try:
            self._page.evaluate("window.__udispKb.hide()")
        except Exception:  # noqa: BLE001 - a page mid-navigation needs no hiding
            pass

    def note_tap(self, x, y):
        """A tap that went to the page rather than to a key.

        Above the keys it undoes a dismissal, so touching a field again is how
        the keyboard comes back. Inside the band it does not: a keyboard put
        away to reach what was underneath must not spring up the moment that
        thing is touched.
        """
        if self.dismissed and y < self.top:
            self.dismissed = False

    def forget(self):
        """The page it was drawn on has gone; the overlay went with it."""
        self.visible = False
        self.dismissed = False
        self.broken = False
        self._shift = False
        self._pending = []
        self._reported = False
        self._blur_at = None
        self._road = None

    def request_sync(self, when):
        """Look at what has focus, now or shortly.

        Focus only ever changes here because a finger touched something, so
        there is no need to watch for it: one look after each tap is enough and
        costs one round trip. Shortly, as well as now, because Home Assistant
        opens its search in a dialog that animates in and focuses its field
        when it lands -- a look taken the instant the tap was replayed sees the
        dialog that is not there yet.
        """
        self._pending.append(time.monotonic() + when)

    def tick(self, now):
        if not self._pending or now < min(self._pending):
            return
        self._pending = [t for t in self._pending if t > now]
        self.sync()

    def sync(self):
        """Is anything, anywhere in the page, waiting for text?

        Every frame is asked, not only the top one. A Home Assistant add-on
        reached through ingress -- File editor, Terminal, anything with a web
        interface -- is shown inside an iframe, and from the document that
        holds it `document.activeElement` is the <iframe> element itself, not
        the field inside. Asking only the top frame therefore says "nothing is
        waiting for text" for the whole class of pages where the keyboard is
        most obviously needed. The top frame is asked first because it is the
        usual answer, and the rest only if it says no.
        """
        if self.broken:
            return
        answer = None
        for frame in self._page.frames:
            try:
                said = frame.evaluate(
                    "window.__udispKb ? window.__udispKb.focus() : null"
                )
            except Exception:  # noqa: BLE001 - a frame mid-navigation, or gone
                continue
            # The top frame's answer is the one worth explaining when nothing
            # is waiting for text, so keep the first and stop at the first yes.
            answer = answer or said
            if said and said.get("yes"):
                answer = said
                break
        wanted = bool(answer and answer.get("yes"))
        if wanted and self.dismissed:
            # Asked to go away while this same field still holds focus. A tap
            # anywhere above the keys undoes it -- see note_tap.
            return
        if wanted:
            self._blur_at = None
            # Drawn again even when it is already up. A dialog that opened
            # since the last look entered the top layer after the keyboard did
            # and is painting over it; re-entering puts the keys back on top.
            # The pixels do not change when nothing else did, so the tile diff
            # finds nothing and none of this reaches the panel.
            self._show()
        elif self.visible:
            # A blur that lasts an instant is not somebody putting the keyboard
            # away. Home Assistant's tables and dialogs re-render, and a field
            # can lose focus for a frame while they do; taking the keys down
            # and putting them back is a whole panel of change each way, and
            # from the other side of the glass it reads as a keyboard that
            # will not stay. So the answer has to hold before it is acted on.
            now = time.monotonic()
            if self._blur_at is None:
                self._blur_at = now
                self.request_sync(BLUR_GRACE_S)
            elif now - self._blur_at >= BLUR_GRACE_S * 0.9:
                self._blur_at = None
                self.dismissed = False
                self.hide()
        else:
            self.dismissed = False
            self._explain(answer)

    def _explain(self, answer):
        """Say why the keyboard did not come up, once per distinct reason.

        Every report of it "not appearing" has so far cost a round trip to
        find out what was tapped, because a keyboard that never appears and a
        page with nothing to type into produce exactly the same silence. This
        prints the road to whatever has focus instead. Deduplicated and
        capped, because it would otherwise say the same thing on every tap on
        the background.
        """
        if answer is None or self._said >= 40:
            return
        road = answer.get("road") or "(rien)"
        self._looks += 1
        # Every change, so the log traces focus moving from one thing to the
        # next; and one in fifteen regardless, so a road that never changes
        # while somebody is tapping still proves the looks are happening at
        # all. Saying it every time would fill the log from the background.
        if road == self._road and self._looks % 15:
            return
        self._road = road
        self._said += 1
        print(f"Keyboard: nothing here takes text (look {self._looks}); "
              f"focus is {road}")

    def highlight(self, key):
        try:
            self._page.evaluate(
                "i => window.__udispKb.highlight(i)", -1 if key is None else key["i"]
            )
        except Exception:  # noqa: BLE001 - only the shading is lost
            pass

    def commit(self, key):
        """Deliver the key. True if what has focus may have changed."""
        self.highlight(None)
        action = key["action"]
        if action == _HIDE:
            # The field keeps its focus, which is the point -- putting the keys
            # away is not giving up on what was being typed. But that means the
            # next look finds something typable and would put them straight
            # back, so the dismissal has to be remembered. Reported from a
            # panel as the keys still swallowing the bottom of the screen after
            # Hide, which is exactly what that re-showing was: nothing drawn,
            # and every tap down there eaten.
            self.dismissed = True
            self.hide()
            return False
        if action == _LAYER:
            self._symbols = not self._symbols
            self._shift = False
            self._lay()
            self._show()
            return False
        if action == _SHIFT:
            self._shift = not self._shift
            self._show()
            return False
        keyboard = _Safe(self._page.keyboard)
        if action == _BACK:
            keyboard.press("Backspace")
        elif action == _ENTER:
            # A search that submits usually closes what it was typed into.
            keyboard.press("Enter")
            return True
        elif action == _SPACE:
            keyboard.type(" ")
        else:
            keyboard.type(self._label(key))
            if self._shift:
                # One capital, the way a phone does it.
                self._shift = False
                self._show()
        return False


class Injector:
    """Replays the panel's contacts into the browser.

    One pointer, because a dashboard is a list of things to press and the second
    finger has nothing to do.

    A contact that lands on the on-screen keyboard never becomes a click. It is
    read as a keystroke and the page is told nothing about it, so whatever was
    being typed into keeps its focus.

    A finger does two things a mouse does not do with one button: it taps, and
    it drags to scroll. Which one it was is only known once it has moved, so the
    press is held back until the finger lifts. A gesture that stayed put becomes
    a click; one that travelled becomes scrolling, by the distance it
    travelled -- drag the content down and the page goes up, the way it does on
    anything with a touch screen. Sending the press immediately instead would
    turn every scroll into a click on whatever was under the finger when it
    landed.
    """

    def __init__(self, page, touch_map, keyboard=None, page_w=0, page_h=0):
        self._page = page
        self._corner = (page_w * HOME_CORNER_FRACTION,
                        page_h * HOME_CORNER_FRACTION)
        self._map = touch_map
        self._keyboard = keyboard
        # Where the finger landed, and where it was last seen. None between
        # gestures.
        self._start = None
        self._last = None
        self._scrolling = False
        # Whether a finger landed during the last handle(). The loop throws
        # away everything painted before a press, and that is right for a press
        # and ruinous for a drag: a finger moving at fifty reports a second
        # would invalidate every frame the browser paints, so none would ever
        # be sent and the page would appear to jump rather than follow.
        self.began = False
        # Scroll waiting to go out, and when one last did.
        self._wheel = [0, 0]
        self._wheel_at = 0.0
        # A gesture that started in the corner: when it landed, whether it
        # began there at all, whether a sideways swipe out of it has fired, and
        # whether it has already been spent on going home.
        self._corner_at = None
        self._from_corner = False
        self._home = False
        self._went_home = False
        # How far sideways out of the corner counts as asking to go home.
        self._swipe = max(40.0, page_w * HOME_SWIPE_FRACTION)
        # --show-touches. Off, this costs nothing at all; on, it is the only
        # thing that can answer "why did that tap open the wrong link".
        self.verbose = False
        # The gesture began on the keyboard, and which key it is still on.
        self._on_keyboard = False
        self._key = None

    def handle(self, reports):
        """Replay the contacts. True if any of them reached the page."""
        clicked = False
        self.began = False
        # Every report, in order. Collapsing a run of them down to its last
        # position would be cheaper but wrong: the first position of a run is
        # where the finger landed and the rest is how far it travelled, and a
        # gesture is exactly the difference between the two. Nothing is spent on
        # this -- the browser is only called when the finger lands, moves or
        # lifts, and the board already stops repeating a finger that is holding
        # still.
        for contacts in reports:
            point = None if not contacts else contacts[0][1:]
            if point is None:
                clicked = self._finish() or clicked
                continue
            x, y = self._map.to_page(*point)
            if self._went_home:
                # The gesture already did its one job. Nothing else it does
                # reaches the page.
                self._last = (x, y)
                continue
            if self._start is None:
                if self._keyboard is not None and self._keyboard.contains(x, y):
                    # Not the page's. The pointer is not even moved there, so
                    # nothing about this contact is visible to the page and the
                    # field being typed into keeps its focus.
                    self._on_keyboard = True
                    self._key = self._keyboard.hit(x, y)
                    self._keyboard.highlight(self._key)
                else:
                    if self._keyboard is not None:
                        self._keyboard.note_tap(x, y)
                    # The finger has just landed. Put the pointer there so a
                    # scroll goes to whatever is under it -- a dashboard has
                    # panes that scroll on their own -- but do not press yet.
                    self._page.mouse.move(x, y)
                if self.verbose:
                    print(f"[{time.strftime('%H:%M:%S')}] contact at "
                          f"({point[0]:.0f},{point[1]:.0f}) on the panel -> "
                          f"({x:.0f},{y:.0f}) on the page", flush=True)
                self._start = self._last = (x, y)
                self._scrolling = False
                self.began = True
                # Two questions, and they need two pieces of state. The hold
                # asks whether the finger is STILL in the corner, so its clock
                # is cleared the moment it leaves. The swipe asks where the
                # finger CAME FROM, and a swipe leaves the corner immediately
                # by definition -- so it cannot be asked of the same flag.
                in_corner = x <= self._corner[0] and y <= self._corner[1]
                self._corner_at = time.monotonic() if in_corner else None
                self._from_corner = in_corner
                self._went_home = False
                continue
            if self._on_keyboard:
                # Sliding off a key abandons it, the way it does on a phone.
                if self._key is not None and self._keyboard.hit(x, y) is not self._key:
                    self._key = None
                    self._keyboard.highlight(None)
                self._last = (x, y)
                continue
            # Sideways out of the corner: home, and the page is told nothing
            # about any of it.
            if self._from_corner and not self._went_home:
                dx, dy = x - self._start[0], y - self._start[1]
                if abs(dx) >= self._swipe:
                    # Decided once, at the moment the finger has gone far
                    # enough sideways, and never revisited. Asking again on
                    # every later report let a long diagonal become a swipe
                    # after the fact: a drag across the whole screen runs out
                    # of screen vertically first, so its sideways travel goes
                    # on growing while its downward travel cannot, and half a
                    # screen later it satisfies a test it failed at the start.
                    if abs(dx) > abs(dy) * HOME_SWIPE_STRAIGHTNESS:
                        self._home = True
                        self._went_home = True
                        self._scrolling = False
                        self._wheel = [0, 0]
                        self._last = (x, y)
                        continue
                    self._from_corner = False
            # A hold is cancelled by leaving the corner, not by wandering
            # inside it. A finger resting on glass is never perfectly still --
            # measured against the panel's own geometry, eight page pixels of
            # wander, which is five of panel at 1.6x, was enough to lose the
            # gesture when any DRAG_THRESHOLD of movement cancelled it. What
            # somebody means by holding the corner is that the finger is in the
            # corner, so that is what is asked.
            if self._corner_at is not None and not (
                x <= self._corner[0] and y <= self._corner[1]
            ):
                self._corner_at = None
            if not self._scrolling and (
                abs(x - self._start[0]) + abs(y - self._start[1]) >= DRAG_THRESHOLD
            ):
                self._scrolling = True
            if self._scrolling:
                # Negated: dragging the content downwards means going up the
                # page, which is a negative wheel. Gathered rather than sent --
                # see WHEEL_MIN_INTERVAL_S for what one call costs.
                self._wheel[0] += self._last[0] - x
                self._wheel[1] += self._last[1] - y
            self._last = (x, y)
        # A gesture that began in the corner and is still going sideways has
        # not been decided yet, so its scroll is held rather than sent: a swipe
        # that ends up going home must not have scrolled the page it was
        # leaving on the way out. The moment it is clearly a vertical drag it
        # is an ordinary scroll again and everything gathered goes out at once,
        # so nothing is lost either way.
        if not (self._from_corner and not self._went_home and self._undecided()):
            self._flush_wheel()
        return clicked

    def corner_progress(self, now):
        """How far a hold in the corner has got, 0..1, or None if none is.

        The same clock the gesture itself is decided by, so what the mark
        draws and what the sender is about to do cannot disagree.
        """
        if self._corner_at is None or self._went_home:
            return None
        return min(1.0, (now - self._corner_at) / HOME_HOLD_S)

    def tick(self, now):
        """True once, when the corner has been asked to take the panel home.

        Two ways ask for it. A swipe sideways out of the corner is decided in
        handle(), where the movement is, and left here to be collected: one
        place for the loop to look means the loop cannot learn about one and
        miss the other.

        The other is the hold, and it has to be asked from the loop rather than
        driven by the contacts, because a finger holding perfectly still
        reports nothing at all -- the board drops an event identical to the one
        before it, which is what stops a resting finger from saying the same
        thing fifty times a second.
        """
        if self._home:
            self._home = False
            return True
        if self._corner_at is None or self._went_home:
            return False
        if now - self._corner_at < HOME_HOLD_S:
            return False
        # Spent. The finger is still down and will go on reporting; every one
        # of those reports is now ignored, and the lift at the end is not a
        # tap. Clearing _start instead would have the next report look like a
        # fresh landing, and the lift after it would click the corner of a page
        # that had only just been loaded -- which is what the first version did.
        self._went_home = True
        self._scrolling = False
        self._wheel = [0, 0]
        return True

    def _flush_wheel(self, force=False):
        """Send what has gathered, if it is time or the gesture has ended."""
        if not self._wheel[0] and not self._wheel[1]:
            return
        now = time.monotonic()
        if not force and now - self._wheel_at < WHEEL_MIN_INTERVAL_S:
            return
        self._wheel_at = now
        dx, dy = self._wheel
        self._wheel = [0, 0]
        self._page.mouse.wheel(dx, dy)

    def _finish(self):
        """The finger left. A gesture that never travelled was a tap.

        True when the page was clicked, which is the sender's cue to look at
        what has focus now.
        """
        clicked = False
        # Whatever is left of the scroll goes now: the finger has gone and
        # there will be no later turn to carry it.
        self._flush_wheel(force=True)
        if self._on_keyboard:
            if self._key is not None:
                # Enter usually closes what was being typed into, so the key
                # says whether focus is worth looking at again.
                clicked = self._keyboard.commit(self._key)
            else:
                self._keyboard.highlight(None)
        elif self._start is not None and not self._scrolling and not self._went_home:
            if self.verbose:
                self._say_target(*self._last)
            self._page.mouse.move(*self._last)
            self._page.mouse.down()
            self._page.mouse.up()
            clicked = True
        self._reset()
        return clicked

    def _say_target(self, x, y):
        """Name what the page has at the point about to be clicked.

        The coordinates alone say a tap landed somewhere; they cannot say it
        landed on the wrong thing, and "the fifth tile opens the second one" is
        a question about the page, not about arithmetic. So the page is asked
        -- once, only under --show-touches, and never allowed to cost the
        picture if it refuses.
        """
        try:
            what = self._page.evaluate(
                """p => {
                    const e = document.elementFromPoint(p[0], p[1]);
                    if (!e) return "nothing";
                    const link = e.closest ? e.closest("a") : null;
                    const target = link || e;
                    const text = (target.textContent || "").trim().slice(0, 40);
                    return target.tagName.toLowerCase()
                        + (target.href ? " -> " + target.href : "")
                        + (text ? "  [" + text + "]" : "");
                }""",
                [x, y],
            )
        except Exception as err:  # noqa: BLE001 - a diagnostic, never the picture
            what = f"the page would not say ({err})"
        print(f"[{time.strftime('%H:%M:%S')}] tap at ({x:.0f},{y:.0f}) on "
              f"{what}", flush=True)

    def release(self):
        # A lost connection is not a tap: drop the gesture rather than clicking
        # wherever the finger happened to be.
        if self._on_keyboard and self._keyboard is not None:
            self._keyboard.highlight(None)
        self._reset()

    def _undecided(self):
        """A swipe out of the corner that could still turn out to be one.

        Bounded on both sides, and the far side is the half that was missing:
        once the finger has gone further sideways than the threshold without
        having fired, it is not a swipe home and never will be, so it is a
        scroll and must be let through. Without that, a drag out of the corner
        that was merely too diagonal to count held its scroll for ever and the
        page never moved at all.
        """
        if self._start is None or self._last is None:
            return False
        dx, dy = self._last[0] - self._start[0], self._last[1] - self._start[1]
        return abs(dx) < self._swipe and abs(dx) >= abs(dy)

    def _reset(self):
        self._wheel = [0, 0]
        self._corner_at = None
        self._from_corner = False
        self._went_home = False
        self._start = self._last = None
        self._scrolling = False
        self._on_keyboard = False
        self._key = None


def main():
    parser = argparse.ArgumentParser(
        description="Render a Home Assistant dashboard onto a portall panel"
    )
    parser.add_argument(
        "--host", required=True, help="the panel's address (its port: option)"
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--url", help="the dashboard to render. Not needed with --calibrate"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HA_TOKEN"),
        help="a Home Assistant long-lived access token, or $HA_TOKEN. Only "
        "Home Assistant needs one: leave it out and any other page is "
        "rendered and sent the same way, with nothing written into its "
        "storage and no dashboard waited for",
    )
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument(
        "--render-width",
        type=int,
        help="draw at this width instead of the panel's, and let the board's "
        "pixel-processing accelerator scale it up. Every pixel is paid for "
        "four times here -- painted, encoded, compared with the last one, "
        "encoded again -- and on the board the scaling is free, being a DMA "
        "engine that is otherwise idle. Must match the component's "
        "render_width, which refuses a size that would not land on whole "
        "panel pixels",
    )
    parser.add_argument(
        "--render-height",
        type=int,
        help="the other half of --render-width. Give both or neither",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="turn the picture clockwise before sending, for a panel that is "
        "not mounted the right way up. Touches are turned back the other way, "
        "so the two stay in agreement. Leave at 0 if the component's rotation: "
        "option is already doing this",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="find out which way this panel reports its contacts, by drawing "
        "three targets and asking for a tap on each. Prints the options to add. "
        "No browser and no token needed -- run it once per board",
    )
    parser.add_argument(
        "--touch-rotate",
        type=int,
        choices=(0, 90, 180, 270),
        help="how far to turn the contacts back, when that is not --rotate. "
        "Every panel reports differently -- it depends on how the controller is "
        "wired and on the transform: it was given -- so take this from "
        "--calibrate rather than guessing",
    )
    parser.add_argument(
        "--touch-mirror-x",
        action="store_true",
        help="the panel reports x the other way round. From --calibrate",
    )
    parser.add_argument(
        "--touch-mirror-y",
        action="store_true",
        help="the panel reports y the other way round. From --calibrate",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=10.0,
        help="upper bound on how often a change is acted on. The browser only "
        "hands over a frame when the page moves, so a still dashboard costs "
        "nothing whatever this is set to",
    )
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality, 1..95")
    parser.add_argument(
        "--capture-quality",
        type=int,
        default=90,
        help="quality of the picture the browser hands over, 1..100, or 0 for "
        "lossless PNG. This is not --quality: that one is what the panel "
        "receives, and this one is what it is made from, so keeping it above "
        "--quality leaves the second encode something to work with. PNG is "
        "the honest answer and costs the server about eight times as much "
        "CPU for a picture the panel cannot tell apart",
    )
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="ignore any token, $HA_TOKEN included, and render the page as an "
        "ordinary web page. Only Home Assistant needs one; this is how to say "
        "a page is not Home Assistant when the environment disagrees",
    )
    parser.add_argument(
        "--page-quality",
        action="append",
        default=[],
        metavar="PREFIX=QUALITY",
        help="a JPEG quality to use while a particular page is open, as an "
        "address prefix and a number: --page-quality "
        "https://www.youtube.com=40. Repeatable, first match wins, and "
        "anything not matched uses --quality. A film wants far fewer bytes "
        "than a dashboard and does not show the difference; this is how to "
        "say so per destination rather than per panel",
    )
    parser.add_argument(
        "--token-url",
        default=None,
        help="the Home Assistant the token belongs to, when it is not the page "
        "being opened. A panel started on a launcher opens a page of links, "
        "and the token is for the dashboard behind one of them: without this "
        "it would be installed for the launcher's address, and Home Assistant "
        "would ask to log in. Defaults to --url",
    )
    parser.add_argument(
        "--urgent-fps",
        type=float,
        default=URGENT_FPS,
        help="the limit that applies for a moment after a contact, instead of "
        "--fps. This is what lets --fps be low: at rest a dashboard costs what "
        "it costs, and the moment somebody touches it the brake comes off. "
        "Raise it if a low --fps still feels slow under the finger; it cannot "
        "make the sender go faster than the machine it runs on",
    )
    parser.add_argument(
        "--urgent-window",
        type=float,
        default=URGENT_WINDOW_S,
        help="how many seconds a contact keeps --urgent-fps in force. A "
        "dashboard transition repaints for about a second and a scroll settles "
        "over longer; each new contact starts the count again",
    )
    parser.add_argument(
        "--rect-cost",
        type=float,
        help="what one rectangle costs the board, as a fraction of redrawing "
        "the whole panel. Worked out from the panel's size when not given -- "
        "0.18 at 1024x600, 0.11 at 800x1280 -- because it is a ratio to a "
        "whole-panel decode and that grows with the pixels. Raise it to prefer "
        "whole panels, lower it to prefer rectangles",
    )
    parser.add_argument(
        "--freeze-animations",
        action="store_true",
        help="hold the page's animations still. A pulsing icon or a spinner "
        "keeps the whole pipeline running for a picture nobody is watching. "
        "Only animations: a camera, a video and a card that draws itself on a "
        "canvas do not go through the animation engine and do not stop -- one "
        "of those on the page is enough for this to change nothing at all. "
        "Try it with --stats and keep it only if the idle rate drops",
    )
    parser.add_argument(
        "--browser",
        default="auto",
        metavar="PATH",
        help="which browser to run. 'auto', the default, prefers one installed "
        "on this machine over the one Playwright downloaded, and falls back to "
        "Playwright's if there is none or if it will not start. The downloaded "
        "one is built without the proprietary codecs -- no H.264, no AAC, no "
        "DRM at all -- so a video site that offers nothing else starts a video "
        "and then stops it, which is what YouTube's 'un probleme est survenu' "
        "is. A Chromium packaged by a distribution has them. Give a path to "
        "name one exactly, or 'off' to keep Playwright's whatever is installed",
    )
    parser.add_argument(
        "--audio",
        default="auto",
        choices=["auto", "off"],
        help="send the page's sound to the panel's speaker, so a video played "
        "in the browser is heard where it is watched. 'auto' does it whenever "
        "PulseAudio is there to capture through, and says why when it is not. "
        "Home Assistant's own audio does not come this way and is unaffected: "
        "the board has a media_player of its own",
    )
    parser.add_argument(
        "--browser-arg",
        action="append",
        default=[],
        metavar="FLAG",
        help="an extra command-line flag for the browser, repeatable. The "
        "escape hatch for anything this does not have a setting of its own. "
        "Note that --host-resolver-rules=MAP is NOT a way to choose a DNS "
        "server: it sends a hostname to a given machine, and MAP * sends "
        "every hostname there, dashboard included",
    )
    parser.add_argument(
        "--profile",
        default="",
        metavar="DIR",
        help="keep the browser's profile in this directory, so a site signed "
        "into stays signed in and a banner dismissed stays dismissed. One "
        "directory per panel: Chromium locks a profile and a second browser "
        "pointed at the same one will not start. Empty throws the profile "
        "away on exit, which is what it did before",
    )
    parser.add_argument(
        "--user-agent",
        default="",
        metavar="STRING",
        help="what the browser says it is. Empty, the default, takes its own "
        "and drops the word Headless from it, because a page that is told it "
        "is talking to an automated browser may refuse to serve its real "
        "self -- YouTube says so outright. 'off' leaves it untouched; "
        "anything else is used verbatim",
    )
    parser.add_argument(
        "--keyboard",
        choices=("off", "qwerty", "azerty"),
        default="qwerty",
        help="draw an on-screen keyboard whenever a text field takes focus, so "
        "a search box is not a dead end on a panel with no keys. It appears "
        "only while something is waiting for text and takes no room otherwise",
    )
    parser.add_argument(
        "--blank-after",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="after this long with the panel asleep, park the page on a blank "
        "one. Stopping the picture does not stop the page -- it goes on "
        "painting and running its timers for a screen nobody can see -- and "
        "only navigating away does. The page is loaded again when the panel "
        "wakes, so the delay is what keeps a short sleep instant. 0 leaves it "
        "running",
    )
    parser.add_argument(
        "--no-touch", action="store_true", help="do not replay the panel's contacts"
    )
    parser.add_argument(
        "--show-touches",
        action="store_true",
        help="print every contact: where it landed on the panel, where that is "
        "on the page, and what the page has at that point. This is what "
        "answers a tap that opens the wrong thing",
    )
    parser.add_argument(
        "--stats", action="store_true", help="print what is being sent every 5 seconds"
    )
    parser.add_argument(
        "--locale",
        default="en-US",
        help="the language the pages are asked for, as a BCP 47 tag -- fr-FR, "
        "de-DE, en-GB. Left to itself the browser sends NO Accept-Language "
        "header at all and reports a language tag no real browser produces, so "
        "every site serves whatever its default is. Measured on the shipped "
        "build: nothing sent, and navigator.languages reading en-US@posix",
    )
    parser.add_argument(
        "--show-media",
        action="store_true",
        help="print a video's playhead and how much it has buffered ahead of "
        "it, every two seconds while it plays. The ordinary media lines only "
        "appear when something goes wrong, which cannot show whether a player "
        "was fetching ahead in the seconds BEFORE it stalled -- and that is "
        "the question every 'the video stops' report comes down to",
    )
    parser.add_argument(
        "--show-changes",
        action="store_true",
        help="with --stats, also name the busiest parts of the screen. Use it "
        "to find the card that never stops redrawing",
    )
    args = parser.parse_args()

    # Chromium rejects the whole startScreencast call for a quality outside
    # this, and the failure surfaces as a page that simply never sends a frame.
    if not 0 <= args.capture_quality <= 100:
        parser.error("--capture-quality must be between 0 and 100")

    if not args.calibrate:
        if not args.url:
            parser.error("--url is required")
        # Checked here rather than left to the browser, which reports a
        # malformed address as a bare navigation failure with the reason
        # buried in a stack trace. The common way to get one is copying a
        # documented line whole -- label included -- into a field that wanted
        # only the value.
        if not args.url.startswith(("http://", "https://")):
            parser.error(
                f"--url must begin with http:// or https://, and this one is "
                f"{args.url!r}. If it starts with something like 'url: ', that "
                f"is the name of the setting and not part of the address: the "
                f"value alone is what goes in."
            )
        if args.no_token:
            args.token = None
        # A token is only what gets past Home Assistant's login screen, and
        # nothing else needs one. Any other page -- a train board, a photo
        # frame, a weather site, something somebody built -- is rendered,
        # diffed and sent by exactly the same code, so leaving --token out is
        # how you ask for one of those. It is not a mistake to refuse.
        #
        # A token of the wrong shape is worth refusing here rather than in the
        # browser. Home Assistant answers a bad one by redirecting to its login
        # page, and all this sender can see of that is a page that is not a
        # dashboard -- so it says to check --url, the scheme and the port, and
        # sends you looking in the one place the fault is not.
        #
        # Two things about the shape are certain and cost nothing to check. It
        # is a JWT, so it has three parts separated by dots. It is signed with
        # HS256, whose signature is 32 bytes, which is 43 base64url characters
        # and never any other number. The way it gets broken is being copied
        # out of the dialog by hand and pasted into a console, which truncates
        # it -- silently, and by a different amount each time.
        # A pasted token picks up spaces and newlines, and one made entirely
        # of them is not a token at all: it is a field somebody meant to leave
        # empty. An add-on's form has no empty state, so that is exactly how a
        # blank arrives -- and refusing it outright is the worst of the three
        # possible answers, because the sender then exits before it opens a
        # browser and the supervisor restarts it for ever. The panel shows
        # nothing, and the log blames a token nobody typed. Treat it as absent
        # and say so: the page is rendered, and a page that turns out to need
        # a login says so on its own screen.
        if args.token is not None and not args.token.strip():
            print(
                "The token is blank, so it is being ignored. A panel showing "
                "Home Assistant needs a real long-lived token; a panel showing "
                "anything else needs none at all.",
                flush=True,
            )
            args.token = None
        if args.token:
            args.token = args.token.strip()
            parts = args.token.split(".")
            if len(parts) != 3:
                parser.error(
                    f"the token is not a JWT: it has {len(parts)} "
                    f"dot-separated parts and a Home Assistant token has "
                    f"three. Copy it again."
                )
            if len(parts[2]) != 43:
                parser.error(
                    f"the token's signature is {len(parts[2])} characters and "
                    f"every Home Assistant token's is 43, so this one was cut "
                    f"or joined while being copied. Consoles do that to a long "
                    f"paste. Copy it in Home Assistant with the dialog's copy "
                    f"button and take it from the clipboard without retyping "
                    f"it: in PowerShell, "
                    f"$env:HA_TOKEN = (Get-Clipboard -Raw).Trim()"
                )

    try:
        from PIL import Image
    except ImportError as err:
        raise SystemExit(f"{err}. Install it: pip install pillow") from err

    # The browser renders at the panel's shape. A quarter turn means the page is
    # taller than it is wide, and the panel is the other way round.
    # What is drawn and sent, which is the panel's size unless the board has
    # been told the host will draw smaller. --width and --height stay the
    # panel's throughout, because that is what a contact is reported in.
    # Zero is how a form with no empty state says "not set", so it means the
    # same as leaving them out.
    if args.render_width == 0:
        args.render_width = None
    if args.render_height == 0:
        args.render_height = None
    if (args.render_width is None) != (args.render_height is None):
        parser.error("--render-width and --render-height go together")
    send_w = args.render_width if args.render_width is not None else args.width
    send_h = args.render_height if args.render_height is not None else args.height
    if send_w > args.width or send_h > args.height:
        parser.error(
            f"--render-width/--render-height {send_w}x{send_h} is larger than "
            f"the panel's {args.width}x{args.height}; the board scales up, not down"
        )

    page_w, page_h = send_w, send_h
    if args.rotate in (90, 270):
        page_w, page_h = send_h, send_w

    transposes = getattr(Image, "Transpose", Image)
    transpose = {
        0: None,
        90: transposes.ROTATE_270,
        180: transposes.ROTATE_180,
        270: transposes.ROTATE_90,
    }[args.rotate]

    # Calibration draws its own targets, so it needs neither a browser nor a
    # token -- which is what makes it the first thing to run on a new board.
    if args.calibrate:
        endpoint = connect_tcp(args.host, args.port)
        try:
            calibrate(
                endpoint, page_w, page_h, send_w, send_h, transpose,
                args.quality,
            )
        finally:
            endpoint.close()
        return

    try:
        import numpy as np
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise SystemExit(
            f"{err}. Install the dependencies:\n"
            "    pip install playwright pillow numpy\n"
            "    playwright install chromium"
        ) from err

    # Which page wants which quality. Parsed once here so a typo is a refusal
    # at startup rather than a panel that quietly never changes quality.
    page_quality = []
    for pair in args.page_quality:
        prefix, _, value = pair.partition("=")
        if not prefix or not value.strip().isdigit():
            parser.error(
                f"--page-quality wants an address and a number joined by =, "
                f"like https://www.youtube.com=40, and this one is {pair!r}"
            )
        quality = int(value)
        if not 1 <= quality <= 95:
            parser.error(f"--page-quality {prefix}: {quality} is not between 1 and 95")
        page_quality.append((prefix, quality))

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    rect_cost = (
        # From the PANEL, not from what is drawn. The rule protects the board,
        # and the board's cost for a whole picture barely shrinks when the host
        # draws smaller: the decode does, but the accelerator's pass and the
        # write to the display are still panel-sized. Judging by the render
        # size makes the fraction larger, saturates it sooner, and sends whole
        # pictures more often -- measured on one page, 49.9 KiB/s that way
        # against 34.5 this way, which is the opposite of the point.
        rect_cost_fraction(args.width, args.height)
        if args.rect_cost is None
        else args.rect_cost
    )
    # A press lifts the limit to this for a moment, so that what the press
    # started -- most often a whole new dashboard -- does not arrive as a
    # slideshow. Never slower than the standing limit.
    urgent_interval = min(interval, 1.0 / args.urgent_fps) if args.urgent_fps > 0 else 0.0
    if args.stats:
        print(
            f"One rectangle costs {rect_cost:.3f} of a whole panel at "
            f"{send_w}x{send_h}, so anything from "
            f"{int(1.0 / rect_cost) + 1} of them is sent whole"
        )

    # What the panel is waiting through before it shows anything. Every part
    # of this has been slow at some point -- a browser fetching a fresh
    # profile, a page that would not load, and a thirty-second wait for a
    # dashboard element on a page that never had one -- and from the panel all
    # three look identical: a black screen for a while. So it is timed and
    # said out loud, once, at the moment it ends.
    started = time.monotonic()
    with sync_playwright() as playwright:
        view = {"width": page_w, "height": page_h}
        # Settled once, before anything is launched, so both paths below agree
        # and the log says which browser is about to run.
        # Before the browser, because the browser has to be pointed at the
        # sink and a sink that does not exist yet cannot be.
        audio = None
        if args.audio != "off":
            candidate = PageAudio(args.host)
            if candidate.start():
                audio = candidate

        # Whatever the caller added, after the defaults, so it can override
        # one of them if it means to.
        browser_args = BROWSER_ARGS + list(args.browser_arg)
        if args.browser_arg:
            print("Browser: also " + " ".join(args.browser_arg))
        executable = pick_browser(args.browser)
        if executable:
            print(f"Browser: running {executable}")
        # Playwright puts --mute-audio on every launch and says nothing about
        # it. With it there the capture below reads nothing but zeroes, however
        # loudly the page is playing -- measured, 0.21 RMS inside the page and
        # silence at the sink. Dropped only when the sound is wanted, so a
        # panel with --audio off keeps the quieter browser.
        launch_env = None
        ignore = []
        if audio is not None:
            ignore = ["--mute-audio"]
            launch_env = dict(os.environ, PULSE_SINK=audio.sink)
        if args.profile:
            # A profile on disk, so that what somebody signs into stays signed
            # in. Cookies, local storage and the rest live here instead of in a
            # directory the browser throws away when it exits -- which is why
            # a panel showing YouTube met the consent banner again on every
            # restart, and why signing into anything at all was pointless.
            #
            # One directory per panel, and not negotiable: Chromium locks a
            # profile, and a second browser pointed at the same one refuses to
            # start.
            print(f"Browser: keeping its profile in {args.profile}")
            context = _launch(
                playwright, executable, args.profile, view, browser_args,
                ignore, launch_env, args.locale,
            )
        else:
            context = _launch(playwright, executable, None, view, browser_args,
                              ignore, launch_env, args.locale)
        # Installed on the context so it is in every frame of every page,
        # including the ones a site makes for itself and the one that comes
        # back after the page is parked. It is the line that names why a video
        # stopped, which nothing in the log did before.
        context.expose_function(
            "__udispMediaError", lambda what: print(f"Media: {what}")
        )
        context.add_init_script(media_init_js(
            MEDIA_SAMPLE_MS if args.show_media else 0))
        # On the context, so it survives every navigation the panel makes --
        # including the one the corner itself performs.
        context.add_init_script(HOME_HINT_JS)
        # Which requests the page could not make. Silent on a page that works.
        watch_failed_requests(context)
        if args.token:
            install_token(context, args.token_url or args.url, args.token)
        # Filled in once the keyboard exists, which is after the page. The
        # binding has to be installed before the page is created so that every
        # frame has it, including ones the page makes later.
        keyboard_holder = {}
        if args.keyboard != "off":
            # On the context, so it survives every navigation the sender makes
            # -- parking the page while the panel sleeps, and bringing it back.
            context.add_init_script(KEYBOARD_INIT_JS)
            # Called from the page whenever focus moves, in whatever frame.
            # It only notes that a look is due: this runs inside Playwright's
            # own dispatch, and calling back into the page from here would be
            # asking it a question while it is in the middle of answering one.
            context.expose_function(
                "__udispFocusChanged",
                lambda: (
                    keyboard_holder["kb"].request_sync(0.0)
                    if "kb" in keyboard_holder
                    else None
                ),
            )
        # A persistent context opens a page itself; a fresh browser does not.
        page = context.pages[0] if context.pages else context.new_page()
        user_agent = present_browser(
            context.new_cdp_session(page), page, args.keyboard != "off",
            args.user_agent,
        )
        report_media(page)
        if user_agent is not None:
            # The other half of the same tell. A page that reads one usually
            # reads the other.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        browser_ready = time.monotonic() - started
        print(
            f"Opening {args.url} at {page_w}x{page_h}"
            + ("" if args.token else " (no token, so this is not treated as "
                                     "Home Assistant)")
        )
        # A page that will not open is a reason to wait, not a reason to end.
        # Ending means the add-on throws the browser away and starts the whole
        # thing again, which fixes nothing a retry would not and costs the
        # panel everything it was showing. After a few tries the loop starts
        # anyway: the browser's own error page is at least a picture, and a
        # sleep and a wake will navigate again.
        # Three tries and no more. Retrying at all is for the add-on starting
        # with the house, before the network is up; past that the page is not
        # coming, and every extra wait here is a panel that has not even been
        # connected to yet, because that happens below.
        for attempt in range(3):
            if open_page(page, args):
                break
            wait = min(5 * 2 ** attempt, 20)
            print(f"Trying {args.url} again in {wait}s")
            page.wait_for_timeout(wait * 1000)
        else:
            print(
                f"Warning: {args.url} would not open. Carrying on, so the "
                f"panel shows what the browser has and a wake tries again."
            )
        print(f"Ready {time.monotonic() - started:.1f}s after starting "
              f"({browser_ready:.1f}s of it the browser)")
        if "/auth/authorize" in page.url:
            print(
                "Warning: Home Assistant is asking to log in, so the token was "
                "not accepted. Check --url points at the same address the token "
                "was created on, scheme and port included."
                if args.token
                else "Warning: this page went to a Home Assistant login screen. "
                "It needs a token: pass --token, or set HA_TOKEN."
            )

        touch_map = TouchMap(
            page_w,
            page_h,
            args.width,
            args.height,
            args.rotate if args.touch_rotate is None else args.touch_rotate,
            args.touch_mirror_x,
            args.touch_mirror_y,
        )
        keyboard = (
            None
            if args.keyboard == "off" or args.no_touch
            else Keyboard(page, page_w, page_h, args.keyboard)
        )
        if keyboard is not None:
            keyboard_holder["kb"] = keyboard
            # A page can arrive with a field already focused.
            keyboard.request_sync(0.5)
        injector = (
            None if args.no_touch
            else Injector(page, touch_map, keyboard, page_w, page_h)
        )
        if injector is not None:
            injector.verbose = args.show_touches
        # Shown when a page arrives and while a finger is held there, so the
        # gesture is discoverable by somebody who was never told about it. It
        # cannot be a button: a panel has no Back button and a site playing
        # full screen swallows whatever the page is given, which is why the
        # gesture is decided in the sender. So the mark is decoration over the
        # place the sender is already testing.
        # What is being encoded at right now, and the address it belongs to.
        # Both only move when --page-quality is in use.
        send_quality = args.quality
        quality_url = None
        hint = None if args.no_touch else HomeHint(page, page_w, page_h)
        hint_until = time.monotonic() + HOME_HINT_SECONDS
        capture = Screencast(page, page_w, page_h, args.capture_quality)
        if args.freeze_animations:
            capture.freeze_animations()
        frame_id = 0
        # The newest rendering, kept across connections: a panel that comes back
        # needs a whole picture, and the browser will not send another frame
        # until something on the page moves.
        image = None
        current = None
        # How often each tile has been in a rectangle, for --show-changes.
        heat = np.zeros(
            ((send_h + TILE - 1) // TILE, (send_w + TILE - 1) // TILE),
            dtype=np.int32,
        )

        # One pass per connection: losing the panel waits for it to come back
        # rather than ending, so this can be left running as a service.
        while True:
            endpoint = connect_tcp(args.host, args.port)
            writer = PanelWriter(endpoint)
            previous = None
            last_full = 0.0
            rectangles_sent = 0
            bytes_sent = 0
            blocked = 0.0
            # A stutter is a tail, and an average over five seconds hides a
            # tail completely: a panel that freezes for half a second every
            # three still reports a healthy mean. These two are what a stutter
            # actually looks like -- the longest a picture went unsent, and
            # the longest a single turn of the loop took, which localises it.
            worst_gap = 0.0
            worst_turn = 0.0
            turn_at = time.monotonic()
            pictures = 0
            # Pictures thrown away because the last one had not finished
            # going out. This is the loop declining to queue behind a panel
            # that is behind, and it is the intended behaviour rather than a
            # fault -- but a large number means the link, not the sender, is
            # what sets the frame rate.
            skipped = 0
            loops = 0
            pending = None
            fulls = 0
            last_send = 0.0
            last_sent = time.monotonic()
            # Assumed awake until the panel says otherwise; it announces its
            # state as soon as a sender connects, so this is only the first
            # instant.
            awake = True
            # When it went dark, and whether the page has been let go of.
            asleep_since = 0.0
            parked = False
            # Until when a press has the frame limit raised for it. Everything
            # painted before the press is dropped when it lands, so there is no
            # question of the window covering a picture that predates it.
            urgent_until = 0.0
            stats_at = time.monotonic()
            try:
                while True:
                    # Pump the browser's events -- this is the only place the
                    # screencast frames arrive -- on a short beat, not on the
                    # frame interval. Waiting a whole interval before looking
                    # means a change that landed a millisecond after the last
                    # look sits untouched for the rest of it, which is most of
                    # what "the animation is sluggish" is made of. The interval
                    # still caps how often anything is sent, below.
                    # A dark panel needs none of that beat. Nothing is
                    # being sent to it, and the only thing worth hearing is
                    # the byte that says it woke up -- which is read once a
                    # turn whatever the turn costs. The beat itself is not
                    # free: every one of them is a round trip through the
                    # browser's protocol, and at eight milliseconds that is a
                    # hundred and twenty-five a second for nothing.
                    page.wait_for_timeout(PUMP_MS if awake else SLEEP_PUMP_MS)
                    started = time.monotonic()
                    if awake:
                        worst_turn = max(worst_turn, started - turn_at)
                    turn_at = started

                    # Stopping the picture does not stop the page. Measured
                    # with the screencast stopped and the panel dark: 59.8
                    # animation frames a second and 20 timer callbacks a
                    # second, still being painted and run for a screen nobody
                    # can see. Neither of the obvious levers touches it --
                    # 58.5 with the renderer throttled twentyfold, 59.8 with
                    # the page declared frozen through the lifecycle API --
                    # and navigating away is the only thing that does: 0.0 and
                    # 0.0. So a panel that has been dark for a while gives its
                    # page up altogether, and the server stops paying for it.
                    #
                    # The delay is the whole design. A panel woken inside it
                    # comes back instantly because nothing was given up; only
                    # one left dark long enough that nobody is about to look
                    # at it pays the reload. And what it pays is not a black
                    # screen: the board still holds the last picture it was
                    # sent, so the wait shows a stale dashboard rather than
                    # nothing.
                    if (args.blank_after and not awake and not parked
                            and started - asleep_since >= args.blank_after):
                        print("Panel asleep, letting the page go")
                        if keyboard is not None:
                            keyboard.forget()
                        try:
                            page.goto("about:blank", wait_until="domcontentloaded")
                            parked = True
                        except Exception as err:  # noqa: BLE001
                            # Try again after another delay rather than on
                            # every turn of the loop.
                            print(f"Could not park the page ({err})")
                            asleep_since = started

                    # Asked before the frame is taken, because it gates
                    # both what is decoded and what is sent -- and because it
                    # raises whatever the writer thread hit, so a lost socket
                    # still reaches the reconnect below.
                    # Sound first, and every turn: it goes into its own queue
                    # and out between rectangles, so it never waits behind a
                    # picture. A panel that is asleep is sent none of it.
                    if audio is not None and awake:
                        for block in audio.take():
                            writer.offer_audio(block)

                    free = writer.ready()
                    frame = capture.take()
                    if frame is not None:
                        # A finished picture thrown away because the panel had
                        # not finished taking the one before it. This is the
                        # link setting the frame rate, and it is the intended
                        # behaviour -- the loop declines to queue rather than
                        # stopping to wait -- but it is the number that says so.
                        if pending is not None and not free:
                            skipped += 1
                        pending = frame
                    # Inside the window that a press opens, the faster limit.
                    # Nothing needs to be said about which frame the press
                    # belongs to any more: the press threw away everything that
                    # had been painted before it, so every frame that arrives
                    # from here is one that answers it.
                    limit = urgent_interval if started < urgent_until else interval
                    # Hold the newest frame back until the send rate allows it.
                    # Nothing is lost by waiting: take() only ever returns the
                    # latest, so a frame held here is replaced rather than
                    # queued.
                    shot = None
                    if (pending is not None and free
                            and started - last_send >= limit):
                        shot, pending = pending, None
                        last_send = started

                    # Ask for another picture only once there is somewhere to
                    # put it. Holding the acknowledgement back is what stops
                    # the browser painting frames this loop would only throw
                    # away -- see Screencast.request.
                    #
                    # How early matters, and one pump was far too early to be
                    # early enough. A paint and an encode take twenty to forty
                    # milliseconds and the pump is eight, so the picture landed
                    # after the deadline it was meant for and went out on the
                    # next one: measured on a page that never settles, 20.9
                    # pictures a second reaching the panel at --fps 30 against
                    # 26.3 from the sender that acknowledged freely. That is
                    # the whole of "the old one was smoother on video". Asking
                    # a measured paint-time ahead instead keeps one picture in
                    # flight and no more.
                    # With a margin over the average, because being late by a
                    # little costs a whole interval and being early costs
                    # nothing but a picture waiting a few milliseconds in hand.
                    lead = min(max(capture.lead * 1.5, PUMP_MS / 1000.0), limit)
                    if pending is None and started - last_send >= limit - lead:
                        capture.request()

                    if shot is not None:
                        image = Image.open(io.BytesIO(shot))
                        # A JPEG frame already arrives as RGB, and convert()
                        # to the mode a picture is already in still copies the
                        # whole of it -- 1.8 ms a frame for nothing.
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        if transpose is not None:
                            image = image.transpose(transpose)
                        if image.size != (send_w, send_h):
                            image = image.resize(
                                (send_w, send_h), Image.BILINEAR
                            )
                        current = np.asarray(image)

                    stale = started - last_full >= FULL_REDRAW_SECONDS
                    # Nothing to send until the browser has produced something.
                    # After that, a new frame is a reason to send, and so is a
                    # panel that has just reconnected and knows nothing. A
                    # sleeping one is never a reason: only the heartbeat below
                    # goes out, which is enough to hold the connection open.
                    want_send = awake and image is not None and (
                        shot is not None or previous is None or stale
                    )
                    # Nothing is queued behind a panel that is behind. Skipping
                    # here rather than after the encode also saves the encode,
                    # and leaving `previous` alone means the next picture is
                    # diffed against the last one actually SENT -- so a skipped
                    # frame costs nothing but itself.
                    if want_send and not free:
                        want_send = False
                    if want_send:
                        # Everything, when there is nothing to compare against
                        # yet, and once in a while regardless: a rectangle lost
                        # to a busy board or a hiccuping socket would otherwise
                        # stay wrong forever, because nothing would ever mark
                        # that area as changed again.
                        if previous is None or stale:
                            rectangles = [(0, 0, send_w, send_h)]
                            last_full = started
                            fulls += 1
                        else:
                            rectangles = changed_rectangles(previous, current)
                            covered = sum(w * h for _, _, w, h in rectangles)
                            # Redraw everything only when doing so is actually
                            # cheaper than the pieces.
                            panel = send_w * send_h
                            if covered / panel + rect_cost * len(
                                rectangles
                            ) > 1.0:
                                rectangles = [(0, 0, send_w, send_h)]
                                last_full = started
                                fulls += 1

                        # Which quality this page asked for. Looked at once
                        # per picture rather than per rectangle, and only when
                        # somebody set any: page.url is local to Playwright
                        # rather than a round trip, but a comparison nobody
                        # needs is still a comparison.
                        if page_quality:
                            here = page.url
                            if here != quality_url:
                                quality_url = here
                                wanted = quality_for(here, page_quality, args.quality)
                                if wanted != send_quality:
                                    send_quality = wanted
                                    print(f"Quality: {send_quality} for {here[:70]}",
                                          flush=True)
                        blobs = []
                        for x, y, w, h in rectangles:
                            buffer = io.BytesIO()
                            image.crop((x, y, x + w, y + h)).save(
                                buffer, format="JPEG", quality=send_quality
                            )
                            payload = buffer.getvalue()
                            blobs.append(
                                build_header(w, h, len(payload), frame_id, x, y)
                                + payload
                            )
                            rectangles_sent += 1
                            bytes_sent += len(payload)
                        # The whole picture in one handover, so the writer
                        # cannot be interrupted halfway through it.
                        writer.offer(blobs)

                        if args.show_changes:
                            for x, y, w, h in rectangles:
                                heat[y // TILE : (y + h) // TILE,
                                     x // TILE : (x + w) // TILE] += 1

                        if rectangles:
                            if pictures:
                                worst_gap = max(worst_gap, started - last_sent)
                            previous = current
                            pictures += 1
                            # One identifier per picture, so the board's rate
                            # limit decides about the picture and not about each
                            # of its rectangles.
                            frame_id = (frame_id + 1) & 0x3FF
                        last_sent = started
                    elif started - last_sent >= HEARTBEAT_S and free:
                        writer.offer([build_heartbeat()])
                        last_sent = started

                    reports = []
                    for kind, body in endpoint.read_messages():
                        if kind == "touch":
                            reports.append(body)
                            continue
                        # The panel went dark or came back. Rendering for a
                        # screen nobody can see costs the server, the network
                        # and the board alike, so stop at the source: the
                        # browser is told to stop producing frames at all.
                        if body != awake:
                            awake = body
                            print("Panel " + ("awake" if awake else "asleep"))
                            if awake:
                                if parked:
                                    print("Loading the page again")
                                    parked = False
                                    if not open_page(page, args):
                                        print("Warning: it would not open; "
                                              "the next wake will try again")
                                    if keyboard is not None:
                                        keyboard.forget()
                                        keyboard.request_sync(0.5)
                                    if args.freeze_animations:
                                        # The animation domain was told about
                                        # a document that no longer exists.
                                        capture.freeze_animations()
                                capture.resume()
                                # It has been showing nothing; whatever it had
                                # is no longer what should be there.
                                previous = None
                                if injector is not None:
                                    injector.release()
                            else:
                                asleep_since = started
                                capture.pause()
                    if args.show_touches and reports:
                        # With the time on them, because "the panel reacts
                        # slowly" has two very different causes and this tells
                        # them apart: a stamp that appears the moment the finger
                        # lands means the board and the network are fine and the
                        # wait is the page reacting, and one that appears late
                        # means the report itself was late.
                        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() % 1 * 1000):03d}"
                        for contacts in reports:
                            if contacts:
                                joined = ", ".join(
                                    f"#{i} at {x},{y}" for i, x, y in contacts
                                )
                                print(f"[{stamp}] touch {joined}")
                            else:
                                print(f"[{stamp}] touch released")
                    if injector is not None and reports:
                        if injector.handle(reports) and keyboard is not None:
                            # Focus only ever moves because something was
                            # tapped, so this is the only moment it is worth
                            # asking about -- and twice, because Home
                            # Assistant opens its search in a dialog that
                            # animates in before focusing its field.
                            keyboard.request_sync(0.0)
                            keyboard.request_sync(0.45)
                        if URGENT_AFTER_INPUT:
                            urgent_until = time.monotonic() + args.urgent_window
                            # Nothing painted before the finger LANDED shows
                            # anything of it, in hand or still in the browser,
                            # so both are thrown away -- but only then.
                            #
                            # Doing it on every report is what made a swipe
                            # erratic. A finger moving reports fifty times a
                            # second; each report threw away the frame in hand
                            # and asked the browser to start again, so almost
                            # nothing painted during a drag ever survived long
                            # enough to be sent. Measured on a scrolling page
                            # with --urgent-fps 30: 8.3 rectangles a second
                            # while the finger moved, a median gap of 77 ms and
                            # a worst of 429 -- half a second of the page not
                            # moving while the finger did. A frame painted
                            # twenty milliseconds ago is a perfectly good
                            # picture of a scroll in progress.
                            if injector is None or injector.began:
                                pending = None
                                capture.request(discard=True)

                    loops += 1
                    now = time.monotonic()
                    # A finger held in the corner asks to go back to the page
                    # this panel was pointed at -- its launcher, whatever that
                    # is. Nothing else can offer that: there is no Back button
                    # on a panel, and a site playing full screen would swallow
                    # one anyway.
                    # The mark, from the same clock the gesture uses: filling
                    # while a finger is held there, faint for a few seconds
                    # after a page arrives, and gone the rest of the time. Only
                    # while the panel is awake -- a dark screen is shown
                    # nothing at all.
                    if hint is not None and awake:
                        held = (None if injector is None
                                else injector.corner_progress(now))
                        if held is not None:
                            hint.set(held)
                        elif now < hint_until:
                            hint.set(0.0)
                        else:
                            hint.set(None)
                    if injector is not None and injector.tick(now):
                        print(f"Home: back to {args.url}")
                        if not open_page(page, args):
                            print("Warning: home would not open")
                        if keyboard is not None:
                            keyboard.forget()
                            keyboard.request_sync(0.5)
                        if hint is not None:
                            # A new document, so nothing is drawn on it yet --
                            # and the page somebody has just come home to is
                            # exactly where the mark is worth showing again.
                            hint.forget()
                            hint_until = time.monotonic() + HOME_HINT_SECONDS
                        if args.freeze_animations:
                            capture.freeze_animations()
                        # A different page entirely; nothing of the old one is
                        # worth diffing against.
                        previous = None
                        pending = None
                        # Not request(discard=True): see Screencast.restart.
                        # The page has just changed under us and a still page
                        # will not paint again, so the picture has to be asked
                        # for rather than waited for.
                        capture.restart()
                    if keyboard is not None:
                        keyboard.tick(now)
                    if args.stats and now - stats_at >= 5.0:
                        elapsed = now - stats_at
                        blocked += writer.take_blocked()
                        print(
                            f"{pictures / elapsed:.1f} pictures/s, "
                            # What the browser made, against what was worth
                            # sending. Every frame counted here was painted and
                            # encoded whether or not it was used, so a number
                            # far above the one before it is the cost of
                            # pictures nobody will ever see.
                            f"{capture.produced / elapsed:.1f} made/s, "
                            f"{rectangles_sent / elapsed:.1f} rectangles/s, "
                            # How many of those pictures gave up on rectangles
                            # and sent the whole panel. A dashboard costing
                            # hundreds of kilobytes a second is usually doing
                            # this, and the rectangle count alone does not say
                            # so -- a whole panel is one rectangle, and so is a
                            # card that grew.
                            f"{fulls} whole, "
                            f"{bytes_sent / elapsed / 1024:.1f} KiB/s, "
                            # What share of the wall clock went into a write
                            # that the panel would not accept yet.
                            # What share of the wall clock the writer
                            # thread spent inside a write the panel would not
                            # take yet. The loop no longer waits with it, so
                            # this can sit near 100% without a stutter -- but
                            # then `skipped` is what the link is costing.
                            # Bounded, because a number that cannot be true is
                            # worse than no number: it makes the reader doubt
                            # the whole line. If this ever hits 100 exactly,
                            # the writer never stopped writing in the window.
                            f"panel wait {min(blocked / elapsed, 1.0) * 100:.0f}%, "
                            # Pictures dropped rather than queued behind a
                            # panel that had not finished taking the last one.
                            f"{skipped} skipped, "
                            # The tail, which the averages above cannot show.
                            f"worst gap {worst_gap * 1000:.0f} ms, "
                            f"worst turn {worst_turn * 1000:.0f} ms, "
                            # How often touches are looked at. Anything much
                            # below --fps means the loop is the bottleneck, and
                            # a press waits that long before it is even read.
                            f"loop {loops / elapsed:.1f} Hz"
                        )
                        if args.show_changes and heat.any():
                            # Where the traffic is coming from. A dashboard that
                            # costs hundreds of kilobytes a second has something
                            # on it that never stops moving -- a graph, a camera
                            # tile, a spinner -- and the only way to find it is
                            # to be told which part of the screen it is on.
                            order = np.argsort(heat, axis=None)[::-1][:5]
                            spots = []
                            for flat in order:
                                ty, tx = divmod(int(flat), heat.shape[1])
                                if heat[ty, tx] == 0:
                                    break
                                spots.append(
                                    f"{tx * TILE},{ty * TILE} x{int(heat[ty, tx])}"
                                )
                            print("  busiest areas: " + "; ".join(spots))
                            heat[:] = 0
                        rectangles_sent = bytes_sent = pictures = loops = 0
                        skipped = 0
                        blocked = worst_gap = worst_turn = 0.0
                        capture.produced = fulls = 0
                        stats_at = now
            except OSError as err:
                print(f"Lost the panel ({err}), waiting for it to come back")
                if injector is not None:
                    injector.release()
            finally:
                writer.close()
                endpoint.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
