# ESP32-P4 Panel

Renders Home Assistant dashboards onto ESPHome `usb_display` panels over the
network, and replays the panels' touches back into them. It exists so the
panels do not depend on a desktop being switched on: this runs on the machine
that is already on all the time.

One instance serves as many panels as you list. Each gets its own browser, its
own process and its own prefix in the log, and is restarted on its own if it
fails.

## What has to be true first

The panel needs `port:` and `touchscreen_id:` in its `usb_display:` block, and
you need a **long-lived access token** from the bottom of your Home Assistant
profile page.

Run the calibration once per panel, from anywhere, before setting this up --
it prints the `touch_rotate` and mirror values to use, and no two panels agree:

    python ha_send.py --calibrate --host ip esp32P4 --port 5000 \
        --width 1024 --height 600 --rotate 180

## As a Home Assistant add-on

Settings -> Add-ons -> Add-on store -> the three dots -> Repositories, and add:

    https://github.com/youkorr/esphome_esp-video

Then install **ESP32-P4 Panel**, fill in the panels in its Configuration tab
and start it. It will start with Home Assistant from then on.

The first build downloads a browser, so give it several minutes and about
1.5 GB of disk.

## With Docker

    mkdir -p config && cp panels.example.json config/panels.json
    $EDITOR config/panels.json
    docker compose up -d

## With systemd

See the header of `esp32p4-panel.service` for the six commands.

## Options

Everything except a panel's own name, address, size and calibration can be set
once at the top and every panel inherits it; a panel that sets one for itself
keeps its own.
The token in particular is the same for a whole house, and long enough that
repeating it per panel is mostly a way to get one of them wrong.

The token is what gets a browser with no keyboard past Home Assistant's login
screen, and it is still needed even here, where this runs beside Home
Assistant. An add-on is given a SUPERVISOR_TOKEN, but that authenticates to the
Supervisor rather than to Home Assistant as a user: it cannot log a browser in.
Only a long-lived access token can.

**A panel does not have to show Home Assistant.** Nothing else in this is
particular to it: point a panel's `url` at any page -- a train board, a weather
site, a photo frame, something you built -- leave its `token` empty, and the
page is rendered, cut into rectangles and sent exactly the same way, with
touches replayed into it. Leaving the token out is how you ask for that: with
one, the sender writes it into the page's storage and waits for a Home
Assistant dashboard to appear; without one it does neither.

| Option | What it is |
|---|---|
| `name` | What this panel is called in the log |
| `host`, `port` | The panel's address, and its `port:` |
| `url` | The page to render. A Home Assistant dashboard, or any other site. From inside an add-on a Home Assistant address is `http://homeassistant:8123/...` -- see below |
| `token` | A long-lived access token. Leave it empty for a page that is not Home Assistant |
| `width`, `height` | Must match the component's |
| `rotate` | Turns the picture, for a panel not mounted upright |
| `touch_rotate`, `touch_mirror_x`, `touch_mirror_y` | From `--calibrate` |
| `fps` | Upper bound on how often a change is acted on |
| `quality` | JPEG quality, 1..95 |
| `keyboard` | The on-screen keyboard's layout, or `off`. See below |
| `blank_after` | Seconds dark before a sleeping panel's page is let go of. See below |
| `browser` | Which browser renders the pages. `auto` prefers one with the video codecs, `off` keeps Playwright's own, or give a full path. See below |
| `stats` | Print what is being sent every five seconds |

## The keyboard

A panel has no keys, so a page whose point is to type -- the dashboard's
search, Assist, the search box of an ordinary site -- would otherwise be a dead
end. Whenever a text field takes focus the browser draws a keyboard across the
bottom of the page, and it goes away again when nothing is waiting for text.

A contact that lands on it is never replayed as a click: it is turned into a
keystroke and the page is told nothing about it, which is how the field being
typed into keeps its focus. That holds through a Home Assistant card's shadow
roots, through a native modal dialog, through the iframe every ingress add-on
is shown in -- File editor, Terminal, anything with a web interface -- and into
a `contenteditable` editor. All four are tested.

It covers the bottom of the screen while it is up, and it does not move out of
the way of a field underneath it. On a page that is one large editor, put the
cursor in the top half.

Set `keyboard` to `azerty` or `qwerty` for the layout the letters are in, or to
`off` to leave it out.

The keys are the ones a search needs: digits, letters, Shift for one capital,
the erase key `⌫` at the right of the fourth row, Enter, a space bar, and Hide
to put the keyboard away without losing what you were typing. `?123` at the bottom left swaps
the letters for a layer of symbols -- everything a password is likely to want,
and back with `ABC`. There is no forward delete: a touch keyboard is not a desk
one, and every key added beyond what is actually needed is another key to have
to find.

The log names the browser at startup — `Browser: Chromium 141.0.7390.37` — and
warns if it is older than 114, which is where the API that puts the keyboard
above Home Assistant's own dialogs arrived. An add-on update refetches the
browser, so an old one there means the image was built long ago and rebuilt
from cache.

## Video, and why it is the worst thing to ask of this

Measured on the Chromium Playwright downloads, 141.0.7390.37: **no H.264, no
AAC, no HLS**, and `navigator.requestMediaKeySystemAccess` does not even exist,
so no DRM of any kind. VP9, VP8, AV1, Opus and Vorbis are all there. A dashboard
never notices any of that. A video site does: its player chooses its formats by
asking the browser what it can decode, and a stream it cannot decode ends as
"un probleme est survenu" a few seconds in.

So the image now carries a second browser -- Google Chrome where there is a
build for the architecture, the distribution's Chromium otherwise, both of which
have the codecs -- and `browser: auto`, the default, prefers it. If neither
could be installed, or the one that was will not start, the sender falls back to
Playwright's own and says so. Nothing about a dashboard changes either way.

The log settles which happened, at every start:

```
Browser: running /usr/bin/google-chrome-stable
Browser: decodes H.264 yes, AAC yes, VP9 yes, AV1 yes, Opus yes; DRM yes
```

and when a video stops anyway, the reason the browser gave for it:

```
Media: format not supported: DEMUXER_ERROR_NO_SUPPORTED_STREAMS
```

That line is worth quoting in a bug report -- it separates a codec the browser
lacks from a stream that went away from a site refusing to serve.

`browser: off` keeps Playwright's own whatever is installed, and a full path
names one exactly.

Even with every codec, video is what this pipeline is worst at. It sends the
part of the picture that changed, and a video changes all of it every frame, so
every picture is a whole panel: about 100 KiB, and the machine running the
sender tops out near seven a second whatever it is allowed. And there is no
sound at all -- the panel's speaker is a USB sound card fed by whatever the
cable is plugged into, and nothing carries audio over the network.

A silent slideshow at seven frames a second, costing a core of the server. Worth
knowing before spending an evening on it.

## Staying signed in

Set `keep_profile` and the browser keeps its profile between restarts, one
directory per panel under the add-on's own storage. Without it every restart is
a first visit: a site signed into is signed out again, and a consent banner
comes back. With it, sign in once using the on-screen keyboard and it stays
signed in.

One directory per panel is not a choice: Chromium locks a profile, and a second
browser pointed at the same one refuses to start.

## What a sleeping panel costs

Stopping the picture does not stop the page. A panel that has gone dark stops
being sent anything, but the dashboard it stopped showing goes on painting and
running its timers -- measured with the screencast stopped, 59.8 animation
frames a second and 20 timer callbacks a second, for a screen nobody can see.
Neither throttling the renderer nor declaring the page frozen changes that;
only navigating away does.

So after `blank_after` seconds dark, the page is parked on a blank one.
Measured on a container over ten seconds of sleep: 1.1 seconds of CPU with the
page still running against 0.15 with it parked.

It is loaded again when the panel wakes, which takes about three seconds. That
is three seconds showing the dashboard as it was rather than a black screen --
the board still holds the last picture it was sent. The delay is what keeps a
short sleep instant: a panel woken inside it never gave its page up. Set
`blank_after` to 0 to leave the page running.

## What it costs

A dashboard at rest sends nothing at all: the browser only produces a frame
when the page changes, and only the rectangles that differ are sent. A clock
ticking once a second is about 1.5 KiB/s. A camera card is a different matter
entirely -- that is video, and it costs what video costs.

## The address to give it

From inside an add-on, use the name Home Assistant answers to on the add-on
network:

    url: http://homeassistant:8123/lovelace/0

Not a remote-access address. Tailscale's `*.ts.net`, Nabu Casa's
`*.ui.nabu.casa` and dynamic DNS names exist to reach the house from outside;
they resolve on the network they belong to, and a container sitting beside
Home Assistant is not on it -- the browser reports ERR_NAME_NOT_RESOLVED. It
does not need to be, either: Home Assistant is on the same machine.

Outside an add-on, any address that resolves where the sender runs will do:
the machine's hostname, or Home Assistant's IP and port.

