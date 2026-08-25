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

The log names the browser at startup — `Browser: Chromium 141.0.7390.37` — and
warns if it is older than 114, which is where the API that puts the keyboard
above Home Assistant's own dialogs arrived. An add-on update refetches the
browser, so an old one there means the image was built long ago and rebuilt
from cache.

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

