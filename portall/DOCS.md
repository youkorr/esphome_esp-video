# Portall

Renders Home Assistant dashboards onto ESPHome `portall` panels over the
network, and replays the panels' touches back into them. It exists so the
panels do not depend on a desktop being switched on: this runs on the machine
that is already on all the time.

One instance serves as many panels as you list. Each gets its own browser, its
own process and its own prefix in the log, and is restarted on its own if it
fails.

## What has to be true first

The panel needs `port:` and `touchscreen_id:` in its `portall:` block, and
you need a **long-lived access token** from the bottom of your Home Assistant
profile page.

Run the calibration once per panel, from anywhere, before setting this up --
it prints the `touch_rotate` and mirror values to use, and no two panels agree:

    python ha_send.py --calibrate --host 192.168.1.11 --port 5000 --width 1024 --height 600 --rotate 180

One line, and a real address rather than a placeholder: PowerShell does not
take a backslash as a continuation, and a chevron pasted as it stands is an
error somebody has to work out for themselves.

## Moving from 2.x: Home Assistant is a link now

**Read this before updating to 3.0.0.** Two settings left the top of the
options page, and an update that finds them missing shows a panel a login
screen rather than a dashboard.

`token` and `url` were at the top, above a list of links -- which is what
somebody testing this said made no sense, and they were right. A token belongs
to an **address**, the list already has one per link, and Home Assistant is
just the first link. So that is where both live now:

```yaml
links:
  - name: Home Assistant
    url: http://homeassistant:8123/lovelace/0
    icon: home-assistant
    token: eyJhbGciOi...          # the long-lived token goes HERE
  - name: Jellyfin
    url: http://192.168.1.20:8096
    icon: jellyfin
panels:
  - name: salon
    host: 192.168.1.11
    url: launcher                 # each panel says what it shows
    ...
```

Three steps, and the second is the one that is easy to miss:

1. **Copy your token out of the old `token:` field before you update**, or out
   of the Supervisor's YAML view. It is not shown anywhere else.
2. Put it on the **Home Assistant link**, with that dashboard's address as the
   link's `url:`.
3. Give **every panel a `url:` of its own** -- `launcher` for the page of
   links, or the address of whatever that panel shows. It used to be
   inheritable from the top; there is nothing at the top to inherit any more.

What you get for it: the token is written into the storage of **that address
and nowhere else**, so a panel can carry the house's dashboard alongside
YouTube, Jellyfin and anything else without any of them ever seeing it. A
panel with `home_assistant: false` is given none of the links' tokens at all.

The log says `Every panel needs a host and a url of its own` and names them if
step 3 was missed.

## Moving from 3.x: the form is grouped now

Thirty settings sat at the root of this form, nineteen of them describing the
launcher's appearance, and `panels:` -- the one thing that has to be filled in
-- was the last of them. They are behind five headings now: **panels**,
**links**, **launcher**, **defaults** and **debug**. Nothing was removed and
nothing changed its meaning; every setting is where it always was, under the
heading it belongs to.

**Your configuration does not carry across by itself, and no code inside the
add-on could make it.** The Supervisor drops a key the schema no longer knows
*before* `run.py` ever sees it, which is the same wall 3.0.0 hit. So:

1. In the add-on's options, open the three-dot menu and choose **Edit in
   YAML**. Copy everything.
2. Run it through the converter, which is in this repository:
   `python3 tools/convert4.py < old.yaml > new.yaml`. It also names the two
   settings that are gone rather than dropping them in silence.
3. Paste the result back into Edit in YAML and save.

If you would rather not run anything, paste the old block wherever you got
this add-on from and ask -- the conversion is mechanical.

**What is gone:** `home_corner` and `home_hold`. They existed to make a
three-second hold in a small corner reachable, and the defaults -- 14% of each
axis, one second -- are simply right. (`home_taps` came and went after them,
for the reason under the corner below.)

## Moving from the old add-on

This used to be called **ESP32-P4 Panel**, with the slug `usb_display_panel` --
a name inherited from Espressif's `usb_display`, which stopped describing
anything the day the picture started arriving over Wi-Fi. It is **Portall** now,
and the slug moved with it.

Home Assistant identifies an add-on by its slug, so the Supervisor sees a new
add-on rather than an update. Nothing migrates by itself, and this is the whole
of what to do:

1. Open the old add-on, **Configuration**, and the three-dot menu > **Edit in
   YAML**. Select all of it and copy.
2. Install **Portall** from the same repository, open its Configuration, switch
   the same way to YAML, and paste. Nothing in the options changed, so it goes
   in as it came out.
3. Start Portall and watch its log: `Ready ...s after starting` and
   `Connected to <your panel>` mean it is serving.
4. Stop and uninstall the old add-on. Not before -- two senders pointed at the
   same panel fight over it.

Two things do not come across, and neither is recoverable by copying:

- **The browser profiles.** They live in the old add-on's own `/data`, so
  anything signed into from a panel -- Jellyfin, YouTube, a router page -- has
  to be signed into once more. The house's Home Assistant token is in the
  options and comes across with them.
- **Nothing else.** The panels' firmware is untouched; the ESPHome component
  has been called `portall` for a while and does not change here. You do not
  need to reflash anything.

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

The form has five headings: **panels**, **links**, **launcher**, **defaults**
and **debug**. A panel's own settings sit under `panels:`, with the three
calibration values together under `touch:` and everything that has a default
under `advanced:`. The tables below name each setting; the heading it lives
under is in the example beside it.


Everything except a panel's own name, address, size, calibration and `url` can
be set once at the top and every panel inherits it; a panel that sets one for
itself keeps its own.

**Home Assistant's address and token are not up there.** They are a link, with
the token on the link -- see *Moving from 2.x* above. A token belongs to an
address, and writing it into the storage of only that address is what lets one
panel carry the dashboard alongside every other site it visits.

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
| `token` | An **override**, for the rare panel that opens a *different* Home Assistant from the rest of the house, or that shows a dashboard directly rather than reaching one through a link. Leave it empty otherwise: the Home Assistant link carries the token every panel uses, because a token belongs to an address and a link is the only thing here that names one. The launcher's weather needs neither |
| `width`, `height` | Must match the component's |
| `rotate` | Turns the picture, for a panel not mounted upright |
| `touch_rotate`, `touch_mirror_x`, `touch_mirror_y` | From `--calibrate` |
| `fps` | Upper bound on how often a change is acted on. 25 by default; the one setting that decides whether video looks like video. See below |
| `quality` | JPEG quality, 1..95 |
| `keyboard` | The on-screen keyboard's layout, or `off`. See below |
| `blank_after` | Seconds dark before a sleeping panel's page is let go of, 300 by default. **Per panel only** -- it is not the same thing as the timer that turns your backlight off, see below |
| `keep_profile` | Keep the browser signed in between restarts. See below |
| `import_profile` | A browser profile signed in by hand elsewhere, to start this panel from. **Per panel only** -- it does not belong to a house. See below |
| `user_agent` | What the browser says it is. Empty is right for nearly everything -- it is here for YouTube's television interface. **Per panel or per link only**, because a panel told to say it is a television says it to Home Assistant too. See below |
| `locale` | The language pages are asked for -- `fr-FR`, `de-DE`, `en-GB`. Not cosmetic: without it the browser sends no `Accept-Language` at all and every site serves its own default |
| `homekit` | Put each panel in the iPhone's own Control Centre remote -- the widget built for an Apple TV. Off by default; pair it once from the Home app. See *The remote in the iPhone's Control Centre* |
| `stats` | Print what is being sent every five seconds. **Off**, and worth leaving off -- see the note under the table |
| `show_touches` | Print every contact, where it lands on the page, and what the corner gesture makes of it. Noisy -- for diagnosing a panel that does not react as expected |
| `show_media` | While a video plays, print its playhead and how many seconds are buffered ahead of it. Off by default -- turn it on to diagnose a video that stops |

**On `stats`, and on clearing the log.** Reported from a panel as not being
able to erase the add-on's log -- and the useful half of that is what was
filling it. `stats` shipped **on**, under a comment in the configuration that
said the debug switches were all off, and it prints one line every five
seconds per panel: **17 280 lines a day** for one panel and twice that for
two. Every line worth reading sits somewhere in that.

It is off from 4.13.0. **An existing install keeps what it has**, because the
Supervisor writes these defaults only when an add-on is first installed and a
stored value is yours from then on -- so turn it off yourself if you have been
running it.

> **`debug:` here is NOT ESPHome's `debug:` component, and the two are one
> word apart.** This setting lives in **Settings > Add-ons > Portall >
> Configuration**, in the add-on's own options, and nowhere else. Putting it
> in a panel's ESPHome YAML gets exactly this, because ESPHome has a component
> of the same name that takes `id` and `update_interval` and nothing else:
>
> ```
> debug: [source /config/esphome/ha-esp32p4.yaml:48]
>   [stats] is an invalid option for [debug]. Please check the indentation.
> ```
>
> Reported from a real build. Nothing about a panel's firmware knows what
> `stats` is: it is the SENDER that prints that line, and the sender runs
> here, in the add-on.

Home Assistant itself offers no way to clear an add-on's log: the Supervisor
owns that buffer, an add-on only writes to its own output, and there is no
button and no setting for it here or anywhere else. Restarting the add-on
starts a new container but does not erase what came before. So the only real
lever is how much goes in, which is what the paragraph above is about.

That is the whole form, on purpose. `ha_send.py` has a dozen more settings --
`capture_quality`, `urgent_fps`, `urgent_window`, `browser`, `browser_arg`,
`render_width`/`render_height`, `rect_cost`, `freeze_animations` -- and every
one of them has a default that is right for a panel. A form nobody can read is
a form where the setting that matters gets missed, so they are not offered
here. Run `ha_send.py --help` to see them, and if you really need one from the
add-on, the Configuration page's YAML view will pass any key straight through
to the sender.

## A panel as a launcher, and the way back

The add-on builds the home page itself, from a list you fill in. Put `launcher`
in a panel's `url:` and it starts there:

```yaml
launcher:
  theme: dark                 # dark or light
  columns: 0                  # 0 lets the panel decide
  align: left                 # left, center, right
  clock:
    show: true                # the time and the date above the links
    size: medium              # small, medium, large, huge
    color: theme              # a palette name, or theme for the theme's colour
  date:
    size: medium              # the same four
    color: theme              # the same palette names
  weather:
    entity: weather.forecast_home   # empty for none
    size: medium
  background:
    source: http://homeassistant:8123/local/wall.jpg
    motion: false             # let a GIF or an MP4 actually move
    blur: md                  # off, sm, md, xl
    dim: 40                   # 0..100
  slideshow:
    enabled: false
    seconds: 30
    fade: 1
    rescan: 60
links:
  - name: Home Assistant
    url: http://homeassistant:8123/lovelace/0
    icon: home-assistant
    group: Maison
    description: Salon, lumieres, volets
    token: eyJhbGciOi...          # the dashboard's long-lived token
  - name: Jellyfin
    url: ""
    icon: jellyfin
    group: Media
    description: Films et series
    quality: 40                 # a film needs far fewer bytes than text
panels:
  - name: salon
    host: ""
    url: launcher
    links: Home Assistant, Jellyfin   # what this panel shows; empty = every link
    width: 800
    height: 1280
```

### Each panel its own links, and its own number across

Several panels share one list of links, and **each panel chooses the ones it
shows, in its own entry**. `links:` on a panel is the names of those links,
spelt as they are under `links:` and separated by commas. Left empty, the
panel shows every link -- which is what every panel did before, so nothing you
already have changes:

```yaml
panels:
  - name: salon
    url: launcher
    links: Home Assistant, Jellyfin, YouTube
  - name: cuisine
    url: launcher
    links: Home Assistant, Recettes, YouTube
  - name: bureau
    url: launcher               # no links: -- every link
links:
  - name: Home Assistant
    url: http://homeassistant:8123/lovelace/0
  - name: Jellyfin
    url: http://192.168.1.3:8096
  - name: Recettes
    url: https://www.marmiton.org
  - name: YouTube
    url: https://www.youtube.com/tv
```

Capitals and spaces around a name do not matter. The tiles keep the order of
the list under `links:`, not the order they are named in, so the groups stay
together. A name that matches no link is said in the add-on's log at startup,
with the links that do exist, rather than simply never appearing.

**The number across belongs to the screen.** `launcher: columns:` is the
house's; a panel sets its own under `advanced:` when its screen needs another:

```yaml
panels:
  - name: salon                 # 1280x800: takes the launcher's 4
    width: 1280
    height: 800
    url: launcher
  - name: cuisine               # 1024x600
    width: 1024
    height: 600
    url: launcher
    advanced:
      columns: 3
```

Four across suits a 1280x800 and does not suit a 1024x600: measured in the
add-on's own browser, the tiles are 220 px wide there and the names break
**inside** words -- "Jellyfi n", "YouTu be", "Proxm ox". At three across they
are 298 px and no word breaks. `0` lets the panel decide, which gives three
across at 1280x800 and two at 1024x600.

### Coming back

A panel has no Back button, no address bar and no keyboard, and a site playing
full screen swallows whatever the page is given. So the way home is decided in
the sender, before the page sees anything: **hold the top-left corner**, or
**swipe sideways out of it**. A faint mark is drawn there when a page arrives
and fills while a finger is held, so the gesture can be found by somebody who
was never told about it.

Two settings, on the panel:

```yaml
panels:
  - name: salon
```

**There were briefly two quick taps as a third way, and they are gone.**
Counting taps costs the corner: the first of two cannot be acted on until the
window for the second has passed, so a tap there was either 450 ms late or
never delivered. That is fine on a dashboard and wrong on a page with its own
control in that corner, and no setting makes it not so -- the delay is what the
gesture is. Two ways home, both of which leave the corner alone.

The startup line says where the corner is and what it passes through:

```
Home: corner 14% (179x112 of the page), hold 1s, a tap under 0.35s reaches the page, settle 300ms
```

The corner is 14% of each axis -- on a 1280x800 page, 179x112 -- and the mark
drawn on screen is the same rectangle the sender tests, so what is pressed and
what is seen cannot drift apart.

**A quick tap in that corner reaches the page**, at the default, with no
delay at all -- so whatever a page puts there stays usable.

**A press LONGER than 350 ms does not**, and that half is deliberate. It was
somebody attempting the hold and letting go early, and delivering it is what
used to open Home Assistant's sidebar -- the corner covers that button, so
every failed attempt pressed it and repainted the screen. The mark lights up
instead, to say the gesture is there.

So the corner is an ordinary part of the page for a quick tap and a dead zone
for a slow one. A drag out of it scrolls normally, and the sideways swipe is
unaffected.

**How long coming home actually takes.** The hold is only part of it. A panel
that starts on the launcher used to pay a further **three seconds** on every
trip home: that wait exists because Home Assistant paints in stages -- shell,
then cards, then their data -- and the first picture is the one every later
difference is measured against. A page of links has no such staging, and since
3.3.0 the add-on says so outright. And since 3.3.1 measured what that wait is
actually for -- `first picture 0.0s after the page opened`, so the picture was
ready the moment the wait ended -- the launcher's own settle is **300 ms**.

Coming home is therefore the one-second hold plus about a third of a second,
and the log says so in three pieces, so a slow return can be blamed on the
right one:

```
Home: corner 14% (179x112 of the page), hold 1s, a tap under 0.35s reaches the page, settle 300ms
Home: back to http://127.0.0.1:8099/ -- held 1.0s, opened in 0.3s
Home: first picture 0.0s after the page opened
```

### A Bluetooth remote, and what it does inside a link

A panel can drive a USB Bluetooth dongle, and a remote or a keyboard paired to
it reaches whatever page the panel is showing. That is the board's half, and
it is one line in the panel's own ESPHome YAML:

```yaml
portall_bt:
  id: dongle
  host_stack: bluedroid
  hid: true        # a Bluetooth keyboard or remote
  audio: true      # an AVRCP remote, e.g. the buttons on a car kit or headset
  keys: true       # send what it presses to the page
```

`keys: true` finds the panel's own `portall:` block by itself -- there is only
one per board -- so nothing has to be named. The board sends the key up the
same socket the touches use, and the add-on replays it into the browser.

**What each button does:**

| on the remote | in the page |
|---|---|
| arrows | ArrowUp / ArrowDown / ArrowLeft / ArrowRight |
| OK / Enter | Enter |
| Back / Exit | Escape -- back *within* the page |
| **Menu** | **leaves the link and goes back to your launcher** |
| page up / page down | PageUp / PageDown |
| play, pause, next, previous, volume | nothing here -- those stay media keys |

Menu is the one worth knowing about: before it existed, once a link was open
the only way back was the corner gesture on the glass, which is no use to
somebody sitting down with a remote in their hand. It is the same destination
the corner reaches -- the panel's own `url:`.

**A Bluetooth keyboard sends everything**, not only those: letters, Tab,
Backspace and the rest go straight into whatever the page has focused, which
is what a sign-in form wants.

**A gamepad works too, and its d-pad is the arrows.** Pair it the same way,
with `hid: true`:

| on the controller | in the page |
|---|---|
| d-pad | the four arrows |
| A | Enter |
| B | Escape |
| Back | Escape |
| Home | leaves the link, back to the panel's own `url:` |
| everything else | nothing yet -- see below |

**Nothing in that table is a guess about your controller.** Every HID device
carries a *report descriptor* saying which bits of which report mean what, the
panel reads it when the controller connects, and the mapping is applied to
what the device says about itself. An earlier version instead carried byte
offsets measured off one NVIDIA Shield, and on a real panel it put `up` under
every direction while X and Y did nothing at all -- which is what a fixed
table does the moment it meets a controller it was not written against.

The line to look for at boot says whether yours was read:

```
input device 0955:7214 described itself: 96 bytes, 23 fields
```

A diagonal on the d-pad deliberately moves nothing: a grid of tiles has no
diagonal, and a cleaner press is the answer.

**The other buttons name themselves in the log, once each**, because what X or
Start should mean in a page is not something this can know:

```
gamepad: button 4 is pressed and has no meaning in a page
```

Send that line saying which button it was and it can be given one.

### A telephone is a remote too, and it needs no Bluetooth at all

`remote: true` on the panel's own `portall:` block puts seven buttons in Home
Assistant -- up, down, left, right, OK, back and home:

```yaml
portall:
  id: panel
  # ...
  remote: true
```

They appear on the panel's device page beside its other entities, so the Home
Assistant app on any telephone drives the page the panel is showing, and a
dashboard card can lay them out as a cross. They press through exactly what a
paired remote presses through -- a HID usage on the same socket -- so the two
cannot disagree about what `up` means.

**Why this is worth having beside a Bluetooth remote rather than instead of
one.** Two devices people already own cannot be paired to a panel at all:

- **an iPhone cannot be a Bluetooth HID device.** A panel is a HID *host*; a
  telephone is on the other side of that, whatever the dongle.
- **a recent Xbox controller speaks BLE.** Microsoft's firmware v3 and v4 use
  Bluetooth Classic and pair here; v5 and later moved to Bluetooth Low
  Energy, which this component does not host -- so such a controller answers
  no scan and reads, from the panel, exactly like one that is switched off.

A button in Home Assistant needs neither. It also needs no line of sight and
no batteries, which is most of what a remote is for.

**And the iPhone's own remote widget drives it too** -- the one in Control
Centre, built for an Apple TV. That is `homekit: true` in this add-on's
options rather than anything on the board; see **The remote in the iPhone's
Control Centre** below.

What it is NOT is an Apple TV. The widget lists Apple TVs, which speak Apple's
own Companion Link, *and* HomeKit accessories of the Television kind, which
speak an open protocol. This add-on is the second, and never pretends to be
the first.

**Home and Back are not gamepad buttons**, and it is worth knowing why. On a
Shield, Home, Back, Search, Play/Pause and the volume keys are HID *consumer*
keys (Linux's own `hid-nvidia-shield.c` maps Home as usage `0x223`), so they
arrive on a separate report entirely and never touch the button bits. The
descriptor names them, so Home and Back work without anything further -- but a
controller that sends its media keys some other way will show up as a report
shape instead:

```
a report this cannot read: 3 bytes, id 0x08, starting 08 23 02
```

One line per report shape, so a report that only appears when you press one
button still gets named. `show_reports: true` prints the descriptor and every
report that changes, which is what to turn on if a button does nothing.

### The remote in the iPhone's Control Centre

Turn on **`homekit`** in this add-on's options, under *Defaults*. Each panel
then appears as a television in the Home app, and in the remote widget of the
iPhone's Control Centre -- the one built for an Apple TV -- beside whatever
else is listed there.

```
Defaults
  homekit: true
```

Restart the add-on and read its log. Each panel prints a line and a QR code:

```
[salon] HomeKit remote "salon" is waiting to be paired on port 21180 --
        open the Home app on the iPhone, Add Accessory, More options, and
        enter 856-43-076
```

Point the iPhone's camera at the QR code in the log, or type the eight digits
in the Home app. That is the whole of it. **Unpairing** is removing the
accessory in the Home app, exactly like any other.

What the widget's buttons do:

| button | what the panel does |
|---|---|
| the pad, and its swipes | up, down, left, right between the launcher's tiles |
| the centre of the pad | Enter -- opens the tile |
| **Retour / Back** | **back to this panel's own page, the launcher** |
| ⓘ | Escape -- back *within* the page, where a page listens for it |
| play/pause, next, previous | the browser's own media keys |

The pairing code is kept, so it is the same one after a restart. Each panel
gets a code of its own, on a port of its own, starting at 21180.

**Nothing is paired to the panel and no Bluetooth is involved.** The press
arrives here, where the page is rendered, and goes straight into the browser
-- which is also why it works on a panel whose board has no dongle at all.

Two things to know:

- **This add-on now runs on the house's own network** (`host_network`). A
  HomeKit accessory is found over mDNS, which does not cross from the
  Supervisor's private network to the LAN, and the iPhone has to reach the
  accessory's port directly. It is why Home Assistant's own HomeKit works the
  same way. The launcher still binds to `127.0.0.1` only and is reachable
  from nowhere else; the one consequence is that **port 8099 must be free**
  on the Home Assistant machine.
- **It is not the same as the seven buttons** above. Those are entities in
  Home Assistant, so they work from any telephone, a dashboard card, an
  automation or a voice command. This is the iPhone's own remote, with a pad
  under the thumb and no app to open. Turn on either, or both.

If a panel shows no such line, the log says which of the two it is: a build
without HAP-python in it, or a port that would not open. Neither stops the
panels rendering.

#### And whether the arrows do anything depends on the page

This is the part to read before deciding a remote is broken. A browser does
**not** move focus between links with the arrow keys on its own -- only Tab
does -- so this add-on turns spatial navigation on in the browser it launches,
and a site that drives itself with a remote keeps doing so. What each one does
still differs:

| | arrows | what it needs |
|---|---|---|
| **the launcher** | yes, moves between tiles | nothing -- built in |
| **YouTube `/tv`** | yes | the television user agent on that link, as below |
| **Jellyfin** | yes, and **better once its layout is TV** | Settings > Display > Layout: **TV** |
| **Home Assistant** | **Tab** moves between cards, Enter opens | nothing to set |
| **anything else** | yes -- the browser moves the focus | nothing to set |

**That last row used to read "arrows scroll the page", and a panel is what
changed it.** Driving a gamepad, YouTube was fine and Netflix, Orange TV and
Jellyfin were not -- *"il ya juste parfois le button up et down qui fonctionne
mais difficillement"*. That is not the remote, the dongle or the panel: a
browser does not move focus with the arrows, so on a site with no spatial
navigation of its own they fell through to the browser's default, which is to
SCROLL. Up and down had somewhere to go and left and right had nothing, which
is the report exactly.

The sender now launches the browser with spatial navigation on, so the arrows
move the focus on those sites too. Two things are worth knowing about it:

- **That cost three presses a row until 4.16.0, and it was reported.** The
  browser's own spatial navigation scrolls a row into view before it will
  focus it, so a row fully off the screen costs two presses of scrolling and
  moves on the third -- *"il faut s'y reprendre plusieurs fois pour monter,
  descendre"*, against a YouTube that moves its own focus and never shows it.
  Measured on a grid of links: **3 presses a row, and 1 sideways** where
  nothing has to scroll.

  The sender carries its own fallback now and it is **one press a row**, with
  the chosen tile scrolled into view. Nothing to set.
- **A page that handles its own arrows still wins.** It is a fallback, not an
  override -- measured against a page whose handler deliberately moves the
  opposite way, and the page won. So the launcher, YouTube's television
  interface and Jellyfin in TV layout behave exactly as they did.

Jellyfin keeps its own row because its TV layout is still the better setting:
the browser's fallback moves focus, and Jellyfin's own navigation also gives
Back its meaning and lays the pages out for a remote in the first place.

**Not verified against those sites themselves** -- there is no route to
Netflix, Orange TV or a Jellyfin server from where this was written. What is
measured is the mechanism, on the browser the add-on ships, against a page
built to the shape of one: `tools/checkspatnav.py`.

**If nothing happens at all, the log now says which of the three it is** --
`keys:` not set, a report shape this cannot read (with its bytes), or a
button that crossed and the page ignored. Before, all three were the same
silence.

The Jellyfin row is its own source rather than a guess:
`src/scripts/keyboardNavigation.js` drops every navigation key unless
`layoutManager.tv` is set, and the same file makes Escape mean Back only
there. Set the layout once, per user, in Jellyfin itself.

Home Assistant's frontend has no arrow-key navigation of its own, so a remote
there is Tab, Enter and the Menu button -- which is enough to reach a card and
press it, and not enough to drive a dashboard comfortably. A finger is still
the better answer on that one.

**`quality` on a link is the cheapest saving here.** A film wants far fewer
bytes than a dashboard and does not show the difference, so it is said on the
link rather than on the panel: the panel does not know what it is showing, and
the link does. It applies while that page is open and the panel's own quality
comes back everywhere else. Measured end to end -- the same moving page, opened
from the same launcher, read off the socket by a fake panel:

| | per picture | on the wire |
|---|---|---|
| the panel's quality, 80 | 53.9 KiB | 971 KiB/s |
| the link says `quality: 40` | **34.2 KiB** | **616 KiB/s** |

It matches on the start of the address, because a site is not one address:
YouTube walks from its search page to `/watch?v=...` without becoming a
different place. Leave it out and nothing changes.

`icon` takes a **name from the list below, in French or in English** --
`cuisine` or `kitchen`, `serrure` or `lock`, `reglages` or `settings` -- or
anything you type yourself: an emoji, a letter, two letters. The
names are only a convenience, so an emoji pasted straight in works exactly as
it did before the list existed (on Windows, **Win + .** opens the emoji
picker). A whole word is accepted too and is set smaller so it stays inside its
square.

**The services you are most likely to link to have their own logo** --
`home-assistant`, `jellyfin`, `plex`, `youtube`, `proxmox`, `unraid`,
`truenas`, `docker`, `portainer`, `grafana`, `nextcloud` and forty more. They
are drawn from the add-on itself as inline shapes, in the brand's own colour,
and never fetched: Homepage pulls those from an icon repository, and a panel is
the one screen where nobody can find out why a picture did not load. A logo whose
colour would not stand out against the tile it sits on is drawn in the theme's
ink instead -- GitHub is nearly black and Sonos is black outright, so on a dark
card they would be a hole. The test is the **contrast** against that tile, at
WCAG's 3:1 for a non-text graphic, and not how bright the colour is on its own:
the two part company exactly at YouTube, whose pure red is vivid on a dark card
and is not bright.

Prime Video is the exception on that list: it is not in the collection these
come from, so `prime-video` gives a television.

**Two brands are carried as pictures instead**, from Home Assistant's own
`home-assistant/brands` repository -- which keeps a square icon for every
integration it supports. They are not recoloured against the theme, because
they are already in the brand's real colours, which is the whole point:

- **`reolink`**, because simple-icons has no Reolink at all -- nor Hikvision,
  Dahua or Tapo, checked against all 3460 marks it carries.
- **`immich`**, because its real mark is **five colours** and a simple-icons
  tracing can only be one. That single blue measured 2.46:1 against a dark
  card, failed the contrast rule and was drawn in the ink -- reported from a
  panel as white instead of coloured. Carrying the real one fixes both halves
  at once.

Both are carried here rather than fetched, like everything else on this page.
If a logo you use comes out in the flat theme colour and its real one is not
flat, say so: anything Home Assistant has an integration for can have the
same treatment, and it is one line per brand.

The other camera makers have no mark in either collection, so `hikvision`,
`dahua`, `tapo`, `annke`, `amcrest` and `foscam` give 📹. Ask if you want one
of them drawn properly -- if Home Assistant has an integration for it, it has
an icon.

Nothing is downloaded for any of this. Every icon is a character the browser
already has, and each one was checked against U+FFFF in the browser this add-on
ships -- a glyph the font cannot draw measures exactly as wide as one that has
no drawing at all, and none of these do.

<!-- generated: python3 tools/iconlist.py -->

| icône | les noms qui y mènent | icône | les noms qui y mènent |
|---|---|---|---|
| 🏠 | `maison` `home` `house` | 💾 | `nas` `synology` `disque` `sauvegarde` `backup` `storage` `disk` |
| 🛋 | `salon` `canape` `living-room` `sofa` `lounge` | 📡 | `routeur` `antenne` `router` `antenna` `satellite` |
| 🍳 | `cuisine` `kitchen` `cooking` | 🌐 | `reseau` `internet` `network` `web` `site` |
| 🛏 | `chambre` `lit` `bedroom` `bed` | 📶 | `wifi` `signal` `reseau-sans-fil` |
| 🛁 | `salle-de-bain` `bathroom` `bath` | 🔐 | `vpn` `tunnel` `wireguard` `tailscale` `secure` |
| 🚿 | `douche` `arrosage` `shower` `watering` | 🧱 | `pare-feu` `firewall` `mur` `wall` `opnsense` `pfsense` |
| 🚽 | `toilettes` `toilet` `wc` | ⌨️ | `terminal` `ssh` `shell` `clavier` `keyboard` `invite` |
| 🖥 | `bureau` `ordinateur-fixe` `desk` `office` `proxmox` | 💻 | `code` `ordinateur` `computer` `laptop` `editeur` `editor` `vscode` |
| 🚗 | `garage` `voiture` `car` `garage` | 🔀 | `git` `synchronisation` `sync` `flux` |
| 🌳 | `jardin` `garden` `tree` `exterieur` `outside` | 🐙 | `github` `depot` `repository` |
| 🪴 | `plante` `terrasse` `plant` `patio` `balcony` | 🗃 | `base-de-donnees` `database` `sql` `archives` |
| 🍷 | `cave` `wine` `cellar` | ☁️ | `nuage-fichiers` `nextcloud` `owncloud` `cloud` `drive` |
| 📦 | `grenier` `colis` `attic` `parcel` `package` `delivery` | ⬇️ | `telechargement` `torrent` `download` `downloads` |
| 🚪 | `porte` `entree` `door` `entrance` `hall` | 📈 | `grafana` `supervision` `uptime` `monitoring` `graph` `metrics` `courbes` |
| 🪟 | `fenetre` `window` `volet` `shutter` `blind` | 📨 | `mqtt` `message-broker` `courrier-entrant` |
| 🪜 | `escalier` `stairs` `ladder` `etage` `floor` | 🐝 | `zigbee` `ruche` `z2m` `hive` |
| 🛤 | `couloir` `corridor` `hallway` `route` | 📟 | `esphome` `appareils` `devices` `esp` |
| 🏢 | `immeuble` `building` `appartement` `apartment` | 📄 | `paperless` `document` `documents` `papier` `paper` `scan` |
| 💡 | `lumiere` `ampoule` `light` `bulb` `lamp` `lighting` | 🗝 | `vaultwarden` `bitwarden` `mots-de-passe` `passwords` `vault` |
| 🪔 | `lampe` `lampadaire` `desk-lamp` | 🖨 | `imprimante` `printer` `impression` `printing` |
| 🔌 | `prise` `plug` `socket` `outlet` | 📇 | `scanner` `numerisation` `contacts` |
| ⚡️ | `energie` `electricite` `power` `electricity` `energy` | 📱 | `tablette` `telephone-mobile` `phone` `mobile` `tablet` |
| 🔋 | `batterie` `battery` | 📞 | `telephone` `landline` `call` |
| ☀️ | `soleil` `solaire` `sun` `solar` `sunny` | 📅 | `agenda` `calendrier` `calendar` `schedule` `dates` |
| 📊 | `compteur` `statistiques` `meter` `statistics` `stats` `chart` | 🕑 | `horloge` `heure` `clock` `time` |
| 💨 | `vent` `eolienne` `wind` `air` | ⏱️ | `minuteur` `chronometre` `timer` `stopwatch` |
| 🔥 | `chauffage` `feu` `heating` `fire` `heat` `flame` | 🛒 | `courses` `caddie` `shopping` `groceries` `cart` |
| 🌡 | `temperature` `radiateur` `thermostat` `thermometer` | 📋 | `liste` `listes` `list` `notes` `checklist` |
| ❄️ | `climatisation` `neige` `cold` `snow` `air-conditioning` `freezer` | ✅️ | `taches` `todo` `tasks` `done` |
| 🌬 | `ventilateur` `fan` `breeze` | 🗑 | `poubelle` `dechets` `bin` `trash` `waste` `rubbish` |
| 💧 | `humidite` `eau` `humidity` `water` `moisture` | 🧺 | `lessive` `linge` `laundry` `washing` |
| ⛅️ | `meteo` `weather` `forecast` | 🧹 | `aspirateur` `menage` `vacuum` `cleaning` `broom` |
| 🌧 | `pluie` `rain` | 🤖 | `robot` `aspirateur-robot` `bot` `automation` |
| ☁️ | `nuage` `cloud` | 🚲 | `velo` `bike` `bicycle` `cycling` |
| 🚨 | `alarme` `fumee` `alarm` `siren` `smoke` `emergency` | 🚆 | `train` `rail` `metro` |
| 🔒 | `serrure` `verrou` `lock` `locked` `security` | ✈️ | `avion` `plane` `flight` `airport` `vol` |
| 🔑 | `cle` `key` `keys` | 🚌 | `bus` `autobus` `transport` |
| 📷 | `camera` `photo` `picture` | ✉️ | `courrier` `mail` `email` `lettre` `inbox` |
| 📹 | `camescope` `frigate` `cctv` `video-camera` `videosurveillance` `hikvision` `dahua` `tapo` `annke` `amcrest` `foscam` | 💬 | `message` `messages` `chat` `discussion` |
| 🔔 | `sonnette` `notification` `doorbell` `bell` `alert` | 💶 | `argent` `depenses` `money` `budget` `expenses` `cash` |
| 🚶 | `mouvement` `presence` `motion` `presence-detection` | 🏦 | `banque` `bank` `comptes` `accounts` |
| 🧯 | `gaz` `extincteur` `gas` `extinguisher` | ⚕️ | `sante` `health` `medical` `medecin` `doctor` |
| 👁 | `surveillance` `oeil` `eye` | 🏃 | `sport` `fitness` `course` `running` `exercise` |
| 🛡 | `bouclier` `adguard` `pihole` `shield` `protection` `filtrage` | 🐕 | `chien` `dog` `animaux` `pets` |
| 🎬 | `jellyfin` `plex` `kodi` `film` `cinema` `movies` `movie` `media` | 🐈 | `chat-animal` `cat` `chaton` `kitten` |
| ▶️ | `youtube` `video` `lecture` `play` `watch` | 🏊 | `piscine` `pool` `swimming` `spa` |
| 🎥 | `netflix` `streaming` `projector` | 🍖 | `barbecue` `viande` `bbq` `grill` `meat` |
| 📺 | `television` `tv` `televiseur` `screen` `prime-video` `primevideo` `prime` `disneyplus` `disney` `canalplus` `molotov` | 🔧 | `outils` `bricolage` `tools` `maintenance` `repair` |
| 🎵 | `musique` `spotify` `music` `song` `audio` | ⚙️ | `reglages` `parametres` `settings` `configuration` `setup` |
| 📻 | `radio` `tuner` | ☕️ | `cafe` `coffee` `machine-a-cafe` `kettle` |
| 🎙 | `podcast` `micro-studio` `recording` | 🍽 | `repas` `cuisine-table` `meal` `dinner` `restaurant` |
| 🖼 | `photos` `immich` `gallery` `pictures` `album` | 🧸 | `enfants` `jouets` `kids` `children` `toys` |
| 📖 | `livre` `book` `reading` `library` `calibre` | 🎓 | `ecole` `school` `study` `college` |
| 🎮 | `jeu` `jeux` `game` `games` `gaming` `console` | 💼 | `travail` `bureau-pro` `work` `job` `briefcase` |
| 🎧 | `casque` `headphones` | 🌴 | `vacances` `holiday` `vacation` `beach` `plage` |
| 🔊 | `haut-parleur` `enceinte` `speaker` `volume` `sound` | ⭐️ | `etoile` `favori` `star` `favourite` `favorite` `bookmark` |
| 🎤 | `micro` `microphone` `assistant` `voice` | ❤️ | `coeur` `heart` `favoris` `loved` |
| 🐳 | `docker` `portainer` `container` `containers` `whale` | ℹ️ | `info` `information` `aide` `help` `about` |
| 🖧 | `serveur` `server` `cluster` `machines` `noeuds` `nodes` |  |  |

527 names onto 116 icons.

### Les logos de services

| service | les noms qui y mènent | service | les noms qui y mènent |
|---|---|---|---|
| adguard | `adguard` `adguardhome` | pfsense | `pfsense` |
| audiobookshelf | `audiobookshelf` `abs` | philipshue | `hue` `philipshue` `philips-hue` |
| bitwarden | `bitwarden` | pihole | `pihole` `pi-hole` |
| calibreweb | `calibre` `calibreweb` `calibre-web` | plex | `plex` |
| docker | `docker` | portainer | `portainer` |
| duplicati | `duplicati` | proxmox | `proxmox` `pve` |
| eclipsemosquitto | `mosquitto` `broker` | qbittorrent | `qbittorrent` `qbit` |
| emby | `emby` | radarr | `radarr` |
| esphome | `esphome` | raspberrypi | `raspberrypi` `raspberry-pi` `pi` |
| frigate | `frigate` | reolink | `reolink` |
| gitea | `gitea` `forgejo` | sonarr | `sonarr` |
| github | `github` | sonos | `sonos` |
| grafana | `grafana` | spotify | `spotify` |
| homeassistant | `home-assistant` `homeassistant` `hass` `ha` `lovelace` | synology | `synology` `dsm` |
| homebridge | `homebridge` | tailscale | `tailscale` |
| immich | `immich` | transmission | `transmission` |
| influxdb | `influxdb` `influx` | truenas | `truenas` `freenas` |
| jellyfin | `jellyfin` | twitch | `twitch` |
| mqtt | `mqtt` | ubiquiti | `ubiquiti` `unifi` |
| netflix | `netflix` | unraid | `unraid` |
| nextcloud | `nextcloud` | uptimekuma | `uptimekuma` `uptime-kuma` `kuma` |
| nodered | `nodered` `node-red` | vaultwarden | `vaultwarden` |
| openmediavault | `openmediavault` `omv` | wireguard | `wireguard` |
| openwrt | `openwrt` | youtube | `youtube` `yt` |
| opnsense | `opnsense` | zigbee2mqtt | `zigbee2mqtt` `z2m` |
| paperlessngx | `paperless` `paperlessngx` `paperless-ngx` |  |  |

49 drawn as shapes and 2 carried as a picture, all of them from the add-on itself and never fetched.

### The clock, the date and the weather

Above the links, as on Homepage:

```yaml
launcher:
  clock:
    show: true
  weather:
    entity: weather.forecast_home
```

The date sits **under** the time and carries the year.

**They follow the panel's `locale`.** The browser formats them, so `fr-FR`
gives `19:00` and `mercredi 2 septembre 2026`, `de-DE` gives `Mittwoch, 2.
September 2026`, and this add-on needs to know no language at all.

**No seconds, on purpose.** A digit that changes every second is a rectangle
sent to the panel every second for as long as it is awake -- the same reason
nothing on this page animates. On the minute it is one small rectangle a
minute, and a sleeping panel sends nothing whatever.

The weather is read **by the add-on** and served to the page from `127.0.0.1`.
The page never reaches Home Assistant -- putting a credential into the storage
of every site a panel visits is a leak this project has already had to close
once. It is refreshed every ten minutes; if it cannot be read, the launcher
simply shows no weather and says so once in the log.

**It needs no token and no address from you.** The Supervisor gives every
add-on a credential of its own and proxies `/core/api/...` to Home Assistant
with it, which is what `homeassistant_api: true` in this add-on's `config.yaml`
asks for. So an entity name is the whole setting.

Before 4.1.0 this read through the house's long-lived token, and that made it
depend on *where* you had put one: the token moved onto the links in 3.0.0, so
filling in a panel's `token:` instead -- the nearer of the two fields, and not
a mistake -- left the weather with no address to read from and a log line
naming the wrong half. Nothing to fill in is the fix.

**A panel in portrait is fine, and it was measured rather than assumed.** The
page is fluid: the tiles reflow, the bar wraps, and the weather stays at the
right-hand edge. With six links in three groups and the bar in place, tiles are
350x166 at 800x1280, 566x118 at 1280x800 and 454x107 at 1024x600, with nothing
running off the side at any of them -- the same figures as before the date
moved under the clock.

### The wallpaper, and the digital photograph frame

`launcher.background.source` takes any of four things:

| | |
|---|---|
| nothing | the plain colour |
| an address | the panel fetches it itself. Home Assistant serves `/config/www` at `/local`, so `http://homeassistant:8123/local/wall.jpg` is the easy one |
| a file | under `/config`, `/share` or `/media`, served by the add-on |
| **a folder** | under the same three -- a digital photograph frame |

A folder shows its first picture. Turn `launcher.slideshow.enabled` on and it cycles:

```yaml
launcher:
  background:
    source: /media/photos                   # the folder
  slideshow:
    enabled: true
    seconds: 30                             # how long each picture is shown
    fade: 1                                 # how long one fades into the next
    rescan: 60                              # minutes between re-reading it
```

`launcher.slideshow.rescan` is what makes a photograph dropped into the folder
appear without restarting the add-on. Only pictures are used -- `.jpg`,
`.jpeg`, `.png`, `.webp`, `.gif`, `.avif`, `.bmp` -- so a stray text file in
the folder is ignored rather than drawn as a broken square.

#### Uploading the photographs

There is nothing to install for this: **Home Assistant's own Media panel
uploads them.** Media > My media > **Upload**, choose the files, and they land
in `/media`, which this add-on already reads. Point `launcher.background.source` at
that folder and they are the frame's pictures. Drop more in later and
`launcher.slideshow.rescan` picks them up on its own.

#### Photographs that are already on a server

A NAS, an Immich, anything serving a folder over HTTP -- give the addresses
instead of a folder:

```yaml
launcher:
  slideshow:
    urls:
      - http://192.168.1.3:8080/eTBckVxL/1326045.jpeg
      - http://192.168.1.3:8080/eTBckVxL/1326046.jpeg
    launcher:
  slideshow:
    enabled: true
```

The panel's own browser fetches them, exactly as it fetches any wallpaper
given by address, so whatever serves them has to be reachable from the machine
running this. Given both a folder and a list, the **list wins** -- a folder is
what was there before, and the field just filled in is the one that was meant.
There is no rescan for a list: it is what the form says it is.

**What it costs, because this is the one feature here that is never free.**
A panel sends only what changed, so a still page sends nothing at all. A
picture that changes is a **whole panel** on the wire: about 130 KiB at
800x1280 and quality 80. So

- `launcher.slideshow.fade: 0` is a hard cut and costs **one** whole panel;
- a **1 second** fade costs about `fps` of them -- twenty-five at the default;
- a **2 second** fade costs about fifty, which is six megabytes a picture.

`launcher.slideshow.seconds` is what averages that down. Thirty seconds with a
one-second fade is roughly 110 KiB/s; the same fade every five seconds is six
times that. If a panel starts stuttering while the pictures change, the fade
is the setting to lower, not the delay.

### A GIF or a video as the wallpaper

`launcher.background.motion` decides whether it is allowed to move, and it is
**off** by default. Off, an MP4 shows its first frame and a GIF is frozen on
its own first frame -- the picture is there, and it costs the panel nothing.

On, a video wallpaper is the panel's **entire bandwidth for as long as it is
awake**: every frame is a whole panel, for ever, behind whatever else is on
the screen. It works, it was measured working, and it is not something to
leave on by accident. That is why it is a switch of its own rather than
something a `.mp4` in the field turns on by itself.

**A video wallpaper has to be a WebM, not an ordinary .mp4** -- on the browser
this add-on downloads. H.264 is patented and that build does not carry it:
measured on the shipped Chromium, `canPlayType` for `avc1.42E01E` answers
nothing at all, while VP9, VP8 and AV1 all answer *probably*. From the panel
that is a wallpaper that simply never appears, so the log now says so in one
line when it happens.

Two ways out, and the first is the easy one:

- **convert it once**: `ffmpeg -i film.mp4 -c:v libvpx-vp9 -crf 34 -b:v 0 -an
  film.webm` -- and `-an` because a wallpaper has no business making noise;
- or **install a Chromium packaged by your distribution** on the machine
  running this. Those carry the proprietary codecs, and the sender prefers a
  system browser over its own. Its startup line says which way it went:
  `Browser: decodes H.264 yes` or `no`.

Both work whether the file is under `/config`, `/share` or `/media` or given
by address. One detail that had to be built rather than assumed: a **GIF at an
address** cannot be frozen where it lies, because holding it on one frame
means reading its pixels back out of a canvas and a browser refuses that for a
picture fetched from anywhere else. The add-on copies that one file here once,
so it can. The log says when it does, and says so once if it could not -- in
which case the GIF simply keeps moving. A video needs none of this: pausing
one asks the browser for nothing.

The slideshow list is pictures. A `.mp4` in `launcher.slideshow.urls` is not
loaded -- put it in `launcher.background.source` on its own.

### Changing how it looks

Each part of the bar has its own setting, and every one of them is a list you
pick from:

```yaml
launcher:
  align: center                # left, center, right
  clock:
    size: huge                 # small, medium, large, huge
    color: sky                 # a palette name, or theme
  date:
    size: large                # the same four
    color: slate               # the same palette names
  weather:
    size: small                # the same four
```

This is Homepage's shape rather than its words: a fixed list for each thing
and no stylesheet field anywhere. The sizes are named rather than given in
pixels because *large* is a decision and *72px* is an experiment, and each is
still a range, so it adapts between a 1024x600 panel and a 800x1280 one.
Measured at 1280x800, the clock goes **40px** at `small`, 68 at `medium`, 96 at
`large` and **130px** at `huge`; the date and the weather have the same four.

The colours are Tailwind's palette names -- slate, gray, zinc, neutral, stone,
red, amber, yellow, lime, green, emerald, teal, cyan, sky, blue, indigo,
violet, purple, fuchsia, pink, rose -- plus **white** and **black**, which are
not palettes and are what somebody actually wants over a photograph.
**`theme`** is the default and means what it says: the text colour of
whichever theme is on.

The date sits **under** the time, which is what a clock looks like everywhere
else, and it carries the year.

The weather has no colour of its own on purpose: it is an emoji, which the
browser draws in its own colours whatever you asked for.

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

### When a page half-works: read the `Network:` lines

A page that loads but misbehaves -- YouTube saying "le contenu n'est pas
disponible" while Jellyfin and most other sites are fine -- is a page whose
requests are not all getting through. The browser knows exactly which ones and
exactly why; from the panel all the causes look identical. So the sender prints
them, one line per host and reason, and nothing at all when a page gets
everything it asks for:

```
Network: pub.example.com -- net::ERR_NAME_NOT_RESOLVED
Network: cdn.example.com -- net::ERR_CONNECTION_REFUSED
```

What the reason tells you:

| reason | what it means |
|---|---|
| `ERR_NAME_NOT_RESOLVED` | the name did not resolve -- DNS filtering somewhere between this machine and the internet, whether or not you installed it |
| `ERR_CONNECTION_REFUSED` / `ERR_CONNECTION_TIMED_OUT` | the name resolved but nothing answered -- a firewall, or the host really is down |
| `ERR_BLOCKED_BY_CLIENT` | the browser itself refused it |
| nothing at all | the page got everything, and the fault is elsewhere -- check the `Media:` line and the codec table above |

`ERR_ABORTED` is deliberately not reported: a video player aborts requests
constantly, switching quality and closing streams it no longer needs, and those
are not failures.

**`ERR_NAME_NOT_RESOLVED` is the one to look for, and it is this machine's
DNS rather than the site.** An add-on resolves through Home Assistant's own DNS
container, not through your router, so that is where to fix it: **Settings >
System > Network > DNS servers**. Set an upstream you trust -- `1.1.1.1` or
`8.8.8.8` -- and restart the add-on.

It is worth knowing what that failure looks like from a panel, because it looks
like nothing at all. A video whose next segment cannot be fetched does not
error: it plays out what it already has, runs its buffer down to zero and
stops. The log says both halves:

```
Network: rr1---sn-t0a7sn7d.googlevideo.com -- net::ERR_NAME_NOT_RESOLVED
Media: pause: <movie_player> t=20.0 ready=4 net=2 paused ... buffered=0.0s
```

Read `buffered` before anything else: `0.0s` means the player had nothing left
and stopped because of it, while a pause with ten or twenty seconds still in
hand was somebody -- or the site -- pausing it on purpose. And read the name in
angle brackets: a YouTube search page runs a hover preview beside the real
player and pauses it constantly, so `<inline-preview-player>` lines are noise
and `<movie_player>` lines are not.

**On YouTube, one more thing after the DNS is right: let the advertisement
play.** Pressing Skip and having the video stop a few seconds later is YouTube
checking that its ads were shown, not a fault in the panel -- letting the ad run
to the end plays the video normally. Nothing here can or should change that.

`googlevideo.com` is where YouTube's video bytes come from, and those hostnames
are generated per playback -- which is why changing video sometimes gets one
that resolves and playback works for a while. A dashboard never notices any of
this, because it talks to one name and resolves it once.

### `fps` is what decides whether a video looks like a video

A touch lifts the frame limit to thirty for two seconds, which is what makes a
dashboard feel quick. It is beside the point for anything you *watch*: nobody
touches the panel while a film plays, so that window closes and the picture
falls back to whatever `fps` says. Measured on a page moving continuously with
no finger on it:

| `fps` | pictures a second | gap between them |
|---|---|---|
| 10 | 9.5 | 105 ms |
| 20 | 17.8 | 55 ms |
| 25 (the default) | ~22 | ~45 ms |
| 30 | 25.3 | 38 ms |

A still dashboard costs nothing whatever this is set to -- what does not change
is not sent -- so raising it only costs anything where something moves, which
is exactly where the smoothness is wanted. Lower it for a panel that only ever
shows a dashboard, or for a machine with other work to do.

### What video actually costs, and why 25 Mbit/s is not enough

Even with every codec, video is what this pipeline is worst at, and the reason
is worth stating plainly: **every picture sent is an independent JPEG of the
whole panel.** There is no coding between one picture and the next. A real
video codec sends the *difference* from the previous frame with motion
compensation, which is why a 1080p stream fits in 5 Mbit/s. Whole JPEGs cost
five to ten times that for the same picture.

So the board's radio is not the problem. Measured on a photographic
full-screen picture -- detail everywhere, as a film has:

| drawn at | quality | per picture | at 24 pictures/s | at 15 pictures/s |
|---|---|---|---|---|
| 800x1280 | 80 | 254 KiB | **47.7 Mbit/s** | 29.8 Mbit/s |
| 800x1280 | 60 | 171 KiB | 32.0 Mbit/s | 20.0 Mbit/s |
| 800x1280 | 45 | 142 KiB | 26.6 Mbit/s | 16.6 Mbit/s |
| 640x1024 | 60 | 110 KiB | 20.6 Mbit/s | 12.9 Mbit/s |
| 640x1024 | 45 | 92 KiB | 17.1 Mbit/s | 10.7 Mbit/s |
| 400x640 | 60 | 43 KiB | **8.1 Mbit/s** | 5.1 Mbit/s |

What the radio does is a separate question, and it has been measured: a board
serving its camera sustained **25 932 kb/s to VLC, 3549 frames, 0 lost and 0
corrupted** -- 130 KiB pictures at 25 a second. So the link carries the fourth
and fifth rows comfortably and the third at a squeeze. Nothing here caps it:
the sender writes as fast as the socket will take, and the one place it used to
bound the kernel's send buffer has been removed.

A dashboard never runs into any of this, because only the part that changed is
sent and most of a dashboard does not change.

**If a panel does need less, `render_width` / `render_height` is the lever.**
The page is drawn smaller and the board's accelerator scales it up -- silicon
that is otherwise idle -- and it is the only setting that cuts the cost of a
picture several-fold rather than by a quarter. It is **off by default and meant
to stay off**: the panel's own resolution is the point. Both numbers go on the
panel here *and* under `portall:` on the board, and they have to match, keep
the panel's shape, and divide it into whole pixels -- for an 800x1280 panel,
`400x640` and `640x1024` both do.

The setting that costs nothing to try first is `quality`. Going from 80 to 60 is
a third off the bytes at the same resolution and the same frame rate.

**And the picture arrives without its sound**, which needs saying carefully
because the panel is not short of audio hardware. A board like the Guition has
an ES8311 codec on I2S, an ESPHome `speaker:`, a mixer, a resampler, a
microphone and a `media_player:` entity -- Home Assistant can already play
anything it likes on it. The `portall` component can also take audio in
over USB, as a standard sound card, into that same speaker.

*(Being written: sound for the page. The board half is in -- a new message type
on the same socket, 48 kHz 16-bit mono into the panel's own speaker -- and
`tools/playsound.py` sends it a test tone so you can hear that half work today.
What is missing is the capture on the server, which needs a virtual sound
device beside the browser.)*

What carries no audio **yet** is this link. The udisp protocol is rectangles one
way and touches the other; there is no audio type in the wire format and no
mention of audio anywhere in the network code. So a video rendered by the
add-on plays its sound on the Home Assistant server, where nobody is listening,
and the panel shows a silent picture. Sound over this path would need a new
message type, a capture on the sender side that Chromium does not offer
directly, and lip sync across Wi-Fi on top of a JPEG video path. It is a real
feature, not a setting.

## Staying signed in

Set `keep_profile` and the browser keeps its profile between restarts, one
directory per panel under the add-on's own storage. Without it every restart is
a first visit: a site signed into is signed out again, and a consent banner
comes back. With it, sign in once using the on-screen keyboard and it stays
signed in.

One directory per panel is not a choice: Chromium locks a profile, and a second
browser pointed at the same one refuses to start.

### What a profile costs, and what is done about it

A profile is a browser's whole home -- what it is signed into, and the cache it
fills by itself. The add-on says how big each one is at startup, so the
question never needs a shell inside the container:

```
the browser profile of "salon" is 142 MB
```

Two things keep that figure from running away, and both arrived in 4.11.0
after an add-on was reported at **2.1 GB**:

- **A panel you remove takes its profile with it.** The panels in your
  configuration keep theirs; anything else under the add-on's profile folder
  is removed at startup, and the log says what went. A panel **renamed** counts
  as removed -- its new name is a new profile, so it starts signed out. Nothing
  at all is swept when no panels are configured.
- **The browser's cache is capped at 150 MB per panel.** Left alone Chromium
  sizes its cache from the free space it can see, which here is the whole disk
  Home Assistant is on, and a panel that has been up for months uses it. The
  cap applies as soon as the add-on restarts; nothing has to be deleted by
  hand.

Neither touches what you are signed into. If you want a panel's profile gone
deliberately, take the panel out of the list, start the add-on, and put it
back.

### YouTube: television mode, and the phone as its remote

**This is the only arrangement that works, and it works completely.** Use it as
written rather than as a starting point -- every other route was tried and each
one fails in its own way, listed at the end.

```yaml
links:
  - name: YouTube
    url: https://www.youtube.com/tv
    icon: youtube
    user_agent: "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/537.36 (KHTML, like Gecko) 85.0.4183.93/6.0 TV Safari/537.36"
    quality: 20
    fps: 15
```

Three things are doing the work, and all three are needed.

**1. `/tv` with a television user agent.** `youtube.com/tv` is a junction, not
a page: a television is served the television interface, and everything else
is redirected to the ordinary site. The user agent goes on the **link**, never
on the panel -- a panel-wide one would tell Home Assistant and your launcher
they are talking to a television too.

**2. Sign in with a code, from your phone.** The television interface never
asks for a password: it shows a code, and you enter it at `youtube.com/pair`.
Nothing is typed on the panel and no keyboard is needed. With `keep_profile`
on, which is the default, this is done once.

Google refuses to sign a browser in when it can tell it is being driven -- you
will see *"This browser or app may not be secure"* if you try the ordinary
sign-in form. That is their policy, it is not a fault here, and the code is
how you go around it rather than through it.

**3. Your phone is the remote.** The television interface is built for a
remote control, which a panel does not have -- and the answer is not to build
one. Once the panel is paired, the YouTube app on your phone lists it as a
device: browse there, where you are already signed in and where your
subscriptions are, and send the video to the panel.

So the panel is the screen and the phone is everything else. To leave YouTube
entirely, hold the top-left corner for a second, or swipe sideways out of it.

**Or pair a real remote**, which is the other answer and needs no phone at
all: with `hid: true` and `keys: true` on the board, a Bluetooth remote's
arrows, OK and Back drive the television interface directly, and its Menu
button brings the panel back to your launcher. See *A Bluetooth remote, and
what it does inside a link* above.

#### Why `fps: 15`, and why quality alone did not fix it

Quality was lowered from 40 to 20 on a panel and the video still stuttered.
That is not a failure -- it is the measurement that says where the limit is.
The stats line during a cast:

```
18.4 pictures/s, 22.2 made/s, 18.4 rectangles/s, 92 whole, 918.6 KiB/s,
panel wait 42%, 20 skipped, worst gap 898 ms, worst turn 23 ms, loop 94.3 Hz
```

Read it against the same panel browsing the television interface a few minutes
earlier: **1429 KiB/s at `panel wait 1%`, `0 skipped`**. So it now saturates
carrying **fewer** bytes -- 919 against 1429 -- while waiting forty times as
much. Bytes are not what runs out.

What changed is `92 whole` for 92 pictures: on full motion **every** picture is
a whole panel, and a whole panel is a fixed cost the board pays each time --
one decode of the entire screen and one write of the entire screen -- whatever
the JPEG weighs. So the thing to lower is the **number** of them.

`worst turn 23 ms` and `loop 94.3 Hz` in the same line say the machine running
the add-on is not the problem, and 20 skipped in five seconds is the stutter
itself: the browser makes 22 a second, the panel takes 18, and four are thrown
away unevenly -- which is where `worst gap 898 ms` comes from. **A steady 15
looks far better than an erratic 18.**

So put a frame limit on the link:

```yaml
    fps: 15
```

It applies to that page only, so the dashboard keeps the panel's own rate. A
link that asks to be slower means it, so a touch does not lift it past its
limit either -- otherwise the stutter would come straight back for two seconds
every time somebody brushed the glass.

Try 15, then 12 if it still breaks up. If `skipped` reaches 0 and `panel wait`
falls, the limit is right.

**The sound does not slow down with it.** It is captured from the browser's
own output at 48 kHz and is not tied to the picture rate at all, so a link
limited to 15 pictures a second plays at full quality -- and gets there more
easily, because the pictures no longer being sent were being thrown away in
any case.

What is **not** done is lip sync. Nothing timestamps either stream, so the
sound tends to arrive slightly ahead of the picture it belongs to -- by roughly
what the picture path costs, which is around a tenth of a second. It has not
been measured on a panel. What a lower `fps` does help is the *variation*: an
898 ms gap between pictures is far more visible against steady sound than a
constant small offset is.

#### Why `quality: 20`

Start there and raise it if you want to. Full motion is the one thing this
pipeline finds expensive: every picture is a **whole panel** rather than a few
rectangles, so at 800x1280 a picture costs roughly 70-100 KiB at quality 40 --
which at 25 a second asks for 1.8-2.5 MB/s from a radio measured at 25 Mbit/s.
That is the ceiling, and it is what a stuttering cast is running into.

Quality is the free lever, and video hides compression far better than a
dashboard does: a dashboard at 20 looks poor, a film at 20 usually does not.
The board is not the limit -- Espressif's figure for the P4's JPEG decoder is
1080p at 30 frames a second, far above anything sent here.

If it still stutters, turn `stats` on and look at one line during a cast:

| what the line says | what to do |
|---|---|
| `skipped` above 0, `panel wait` high | too many whole panels a second. Lower the link's `fps` |
| `made/s` well under `fps`, `worst turn` high | the machine running this add-on |
| neither, but `dropped=` climbing under `show_media` | the browser itself |

**Silence is not sent.** A page that is not playing anything produces digital
silence, and sending it would cost **93.8 KiB/s** for ever -- 48 kHz of 16-bit
mono -- at a panel showing a still page. So a block that is exactly zero is
dropped, and `sound` disappears from the line rather than reading 50/s over a
page that is silent. Exactly zero rather than a threshold: a quiet passage one
sample away from silence still goes out.

**And the sound.** The line ends with `sound N/s` whenever there is any. The
capture runs at 48 kHz whatever `fps` is set to, in blocks of 20 ms, so **50 a
second is all of it arriving**. Fewer means the browser produced less -- a
quiet passage, or a page that is not playing. `lost` means the link was too
busy to take it and half a second of it went, which is what audio breaking up
sounds like.

Lowering the link's `fps` helps the sound rather than hurting it: the pictures
it stops sending are pictures the panel was throwing away anyway, and the room
they free is room the sound was competing for.

#### What does not work, and why

Written down so it is not tried again.

| | |
|---|---|
| the ordinary site | **not signed in.** The pairing authenticates the television app, not the browser's Google session -- so the ordinary site stays anonymous, gets advertisements, and the player is torn down a few seconds after one is skipped. Measured on a panel with both tiles open at once |
| a phone or tablet user agent | same problem: it is still the ordinary site underneath |
| a **Chromecast** string (`CrKey/...`) | makes YouTube treat the panel as a cast *receiver* -- the idle "ready to cast" screen, with no interface to sign into |
| the ordinary sign-in form | refused by Google, see above |

### Netflix, Prime Video, Disney+ and anything with DRM

These need **Widevine**, and most of the browsers this can run do not have it.
The log says so on the first secure page a panel opens:

```
Browser: no Widevine DRM, so Netflix, Prime Video, Disney+ and anything else
that requires it will not play.
```

Google Chrome carries Widevine. The Chromium Playwright downloads does not --
measured, it answers `NotSupportedError` for `com.widevine.alpha` -- and a
distribution's Chromium usually does not either. The add-on installs Chrome
where Google publishes a build for the machine, which is amd64 only: on a
Raspberry Pi or any other arm64 box it falls back to the distribution's
Chromium and there is no Widevine to be had. The `Browser: running ...` line
at startup says which one you got.

**Signing in works.** Google's reCAPTCHA does not block a panel -- confirmed
on Spotify, whose sign-in carries it.

**Leave `user_agent` alone and it stays that way.** A site then gets the
honest browser, which is the strongest client a panel can present, and a
fabricated string invents contradictions because everything else in the stack
stays Chrome -- a panel claiming to be an Android app while being Chrome on
x86_64 at a desktop size, sending no `Sec-CH-UA` at all, describes nothing
that exists. Set `user_agent:` on a link only where a site serves a
*different interface* to a different device, which is what the television
mode below is for.

So Widevine is the only thing standing between a panel and Netflix, and the
log line above is what says whether you have it.

**And on a box where Chrome installed, it plays -- reported from a panel:**
*"Netflix fonctionne correctement car il fonctionne sur chrome ... et Widevine
bien present"*. That is the amd64 case working end to end, which is most Home
Assistant machines, and it is worth saying plainly because an earlier version
of this section led with the Raspberry Pi and read as though Netflix were out
of reach generally. It is not. Read the `Browser: running ...` line: if it
names `google-chrome`, you have Widevine.

Two things remain true and neither is a reason not to try it. A browser gets
Widevine **L3**, which Netflix limits to standard definition; and full motion
at a panel's own resolution is the expensive case measured under **YouTube**
above, so `quality:` on that link is the setting worth having. Jellyfin needs
none of this at all, which still makes it the cheaper answer where you have
the choice -- not the only one.

Signing in is a separate question from playing, and worth separating when
something else fails: an ordinary site's sign-in form is plain and the
on-screen keyboard types into it, so a panel can usually reach an account even
where it will never play a stream.

### Jellyfin, and anything with a code

Jellyfin needs nothing from this add-on at all. Its **Quick Connect** is on its
own sign-in page: the panel shows a code, you type that code into Jellyfin on
your server to authorise it, and the panel is signed in. No password is typed
on the panel and no keyboard is needed. Turn Quick Connect on in the Jellyfin
dashboard and it appears on the login page.

That is the shape that works for a screen across a room, and it is worth
preferring wherever a service offers it -- Plex, Emby and YouTube all have
their own version of the same idea.

### Signing in somewhere else, and handing the session over

**Use the television interface above instead.** This route works and is kept
for anybody who wants it, but it is three steps with an obscure flag in the
middle, and it is not what to hand to somebody who just wants YouTube on a
panel.

It comes from an observation worth repeating: **on a Raspberry Pi with
Chromium you can sign in perfectly well.** That is true, and it says exactly
where the difference is. It is not the browser -- it is the same Chromium. It
is that a person is driving it, with no automation attached.

What Google checks is the **signing in**. After that the session is a cookie
like any other, and a cookie written by an ordinary browser works here:
measured, a cookie written by a plain chromium process with no automation of
any kind attached is sent by the automated browser opening the same profile.

So sign in on the Pi, and give the panel what it left behind.

**1. On any machine with a Chromium somebody clicks on** -- a Pi, a laptop, a
desktop -- start it on a folder of its own:

```
chromium --user-data-dir=$HOME/portall-profile --password-store=basic
```

`--password-store=basic` is not optional and it is the step this fails on
without. Chromium encrypts its cookies with a key from the desktop's keyring
when there is one, and that key stays on that machine -- the folder would copy
across and decrypt to nothing. This flag makes it use the fallback key
instead, which is what a container without a keyring uses too.

Sign into YouTube in that window, normally. Then close it.

**2. Copy the folder to Home Assistant**, somewhere the add-on can read --
`/share/portall/salon` is the obvious place. Samba or the File editor add-on
will do it.

**3. Point the panel at it:**

```yaml
panels:
  - name: salon
    host: 192.168.1.50
    import_profile: /share/portall/salon
```

The folder is **copied** into the panel's own profile, once, and only while
that profile is still empty -- so a panel that has since signed into something
else never loses it. `keep_profile` has to be on, which it is by default.
The log says `[salon] started its browser profile from ...` when it happens.

It can be shared rather than per-panel if one sign-in is meant to serve the
house: each panel still gets its own copy, and they go their own way
afterwards.

**Measured end to end** -- a plain browser signs in, the add-on copies the
folder, the automated browser opens it and the server sees the session --
against a local server, because there is no route to Google from where this
was written. What is not tested is Google's own session in particular: it may
tie a session more tightly to a machine than a plain cookie is.

## What a sleeping panel costs

Stopping the picture does not stop the page. A panel that has gone dark stops
being sent anything, but the dashboard it stopped showing goes on painting and
running its timers -- measured with the screencast stopped, 59.8 animation
frames a second and 20 timer callbacks a second, for a screen nobody can see.
Neither throttling the renderer nor declaring the page frozen changes that;
only navigating away does.

So after `blank_after` seconds dark -- 300 by default -- the page is parked on
a blank one. Measured on a container over ten seconds of sleep: 1.1 seconds of
CPU with the page still running against 0.15 with it parked.

It is loaded again when the panel wakes, which takes about three seconds. That
is three seconds showing the dashboard as it was rather than a black screen --
the board still holds the last picture it was sent. The delay is what keeps a
short sleep instant: a panel woken inside it never gave its page up.

**This is not the timer that turns your screen off, and it is not a substitute
for one.** The backlight and the panel's own sleep are the board's business,
in its ESPHome YAML -- `portall.sleep` is what tells the sender to stop
rendering. `blank_after` is what happens on the SERVER a while after that: the
browser lets the page go so a machine that is on anyway stops spending a
second of CPU every ten on a screen nobody is looking at. Because a household
setting a timer in its YAML has no reason to think about that, it is no longer
offered at the top of the options -- it keeps its 300 seconds, and a panel that
wants another number, or `0` to leave the page running, sets it in its own
entry.

## What it costs

A dashboard at rest sends nothing at all: the browser only produces a frame
when the page changes, and only the rectangles that differ are sent. A clock
ticking once a second is about 1.5 KiB/s. A camera card is a different matter
entirely -- that is video, and it costs what video costs.

## The address to give it

From inside an add-on, use the name Home Assistant answers to on the add-on
network, as the Home Assistant **link's** address:

    links:
      - name: Home Assistant
        url: http://homeassistant:8123/lovelace/0
        token: eyJhbGciOi...

Not a remote-access address. Tailscale's `*.ts.net`, Nabu Casa's
`*.ui.nabu.casa` and dynamic DNS names exist to reach the house from outside;
they resolve on the network they belong to, and a container sitting beside
Home Assistant is not on it -- the browser reports ERR_NAME_NOT_RESOLVED. It
does not need to be, either: Home Assistant is on the same machine.

Outside an add-on, any address that resolves where the sender runs will do:
the machine's hostname, or Home Assistant's IP and port.

