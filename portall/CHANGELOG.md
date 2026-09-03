# Changelog

## 3.2.0

- **The weather on the launcher was frozen at whatever the add-on had started
  with.** Reported as 16 degrees on a panel against 30 on the dashboard, and
  as taking ten minutes to catch up -- which is exactly what it was. Two
  faults, both on the page:
  - the script scheduled its first reading for **ten minutes after the page
    loaded** instead of reading at once, so nothing could correct the number
    before then;
  - and the page itself was built **once, at startup**, so the number in it
    never moved however often the add-on re-read the house.

  Measured against a stand-in Home Assistant, the shipped 3.1.0 beside this
  one: 3.1.0 makes **0** requests for the reading at load and still shows
  `16°C` after the panel comes home; this makes one per load and shows
  `30°C`. The page also asks every two minutes now rather than every ten --
  that fetch never leaves the machine, and writing the same text paints
  nothing, so an unchanged reading is not a rectangle on the wire.
- **`blank_after` is off the top of the form.** It is not the timer that turns
  a screen off -- that is the board's own YAML -- it is what the SERVER does a
  while after the panel has gone dark: let the page go, so a machine that is
  on anyway stops spending a second of CPU every ten on a screen nobody is
  looking at. It keeps its 300 seconds, and stays available on a panel's own
  entry for anyone who wants another number or `0`.

## 3.1.0

- **`launcher_slideshow_urls`** -- the photograph frame, for pictures that are
  already on a server. A NAS, an Immich, anything serving a folder over HTTP:
  give the addresses, one per entry, instead of a folder this add-on can read.
  The panel's own browser fetches them. Given both, the list wins.
- **Uploading photographs needs nothing from this add-on**, and the
  documentation now says where: Home Assistant's Media panel (Media > My media
  > Upload) writes into `/media`, which is already mounted here. Point
  `launcher_background` at that folder.
- **A GIF given by address can be frozen now.** Holding one on its first frame
  means reading its pixels back out of a canvas, which a browser refuses for a
  picture fetched from anywhere else -- so with `launcher_background_motion`
  off it went on looping. The add-on copies that one file here once, where the
  page can freeze it, and says so in the log. A video never needed this:
  pausing one asks the browser for nothing.
- Measured on the pixels, on a server serving pictures at addresses with no
  extension in the path, which is the shape a photo server really produces:
  three cycling and wrapping, the first one held with the slideshow off, an
  address that is not one ignored with a line saying why, and a GIF and a
  video by address each still and moving on either side of the switch.

## 3.0.0

**Read "Moving from 2.x" in the documentation before updating.** Two settings
left the top of the options page, and an update that finds them missing shows
a panel a login screen rather than a dashboard.

- **Home Assistant is a link now.** `token` and `url` are gone from the top of
  the form; the dashboard's address and its token go on the **Home Assistant
  link**, beside `quality:`, `fps:` and `user_agent:` which already lived
  there. Asked for in those words after somebody testing this could not see
  why a token sat above a list of links, and they were right: a token belongs
  to an address, and the list already had one per link.
- **Copy your token out before you update** -- it is not shown anywhere else
  once the field is gone -- and give **every panel a `url:` of its own**
  (`launcher`, or the page it shows). The url used to be inheritable from the
  top and there is nothing at the top to inherit any more. The log names any
  panel that is missing one.
- The token is written into the storage of **that address and nowhere else**,
  which is what lets one panel carry the house's dashboard alongside YouTube,
  Jellyfin and anything else without any of them seeing it. Measured across
  three origins in a browser: each gets its own token, a repeat of the same
  address is ignored, and a third origin gets nothing at all.
- `home_assistant: false` on a panel now withholds the links' tokens as well
  as its own, which is what that switch always meant.
- `port`, `fps` and `quality` **stay** at the top. They are the panel's, not
  the link's -- and `fps` and `quality` already exist per link as overrides.

## 2.6.0

- **Five settings are gone from the form**, because they only filled it:
  `launcher_title`, `launcher_subtitle`, `launcher_color`, `user_agent` and
  `import_profile`. A launcher whose every tile is labelled does not need the
  word "Panel" over it, so with no title there is now no heading at all.
  `user_agent` and `import_profile` are **not** removed as features -- they
  stay on a panel's own entry, and `user_agent` on a link, which is where they
  always belonged: a panel told to say it is a television was saying it to
  Home Assistant as well.
- **The date sits under the time, and carries the year.**
- **White and black** join the palette names for `launcher_clock_color` and
  `launcher_date_color`. Neither is a Tailwind palette, and both are what
  somebody actually wants over a photograph.
- **A digital photograph frame.** `launcher_background` now takes a FOLDER as
  well as a file or an address; `launcher_slideshow` cycles through it, with
  `launcher_slideshow_seconds`, `launcher_slideshow_fade` and
  `launcher_slideshow_rescan` -- the last so a photograph dropped in appears
  without a restart. Off, a folder shows its first picture.
- **A GIF or an MP4 wallpaper can move**, behind `launcher_background_motion`,
  off by default. Off, a video shows its first frame and a GIF is frozen on
  its own -- the picture is there and costs nothing.
- **What the moving wallpapers cost, in one line, because it is the only thing
  here that is never free:** a picture that changes is a whole panel on the
  wire, about 130 KiB at 800x1280. A hard cut costs one; each second of fade
  costs about `fps` of them; a playing video costs that for ever.

## 2.5.0

- **`launcher_css` is gone.** It was offered first, on the argument that one
  general mechanism beats a setting per thing -- and that argument is the
  maintainer's convenience, not the household's. Nobody should have to write a
  stylesheet to make the date bigger. Homepage was read rather than remembered
  before this was rewritten: it names the size of each widget from a fixed
  list and has no CSS field at all, which is the shape followed here.
- **The date has its own size and its own colour**, beside the clock's:
  `launcher_date_size` and `launcher_date_color`.
- **The colours are lists now, not a typed-in name.** `launcher_clock_color`
  and `launcher_date_color` offer the same palette names as `launcher_color`,
  with **`theme`** for the theme's own text colour. If the update reports an
  invalid configuration -- 2.4.4 stored an empty value here, which is no longer
  one of the choices -- set both to `theme` and save.
- The sizes keep the words they had: small, medium, large, huge. Homepage's own
  scale runs `xs` to `4xl`, and "large" is what somebody filling in a form at
  eight in the evening reads faster.
- The weather has no colour of its own on purpose: it is an emoji, which the
  browser draws in its own colours whatever it was told.

## 2.4.4

- **The clock, the date and the weather get plain settings**, which is what was
  asked for and what should have been offered first:
  `launcher_clock_size` and `launcher_weather_size` (small, medium, large,
  huge), `launcher_align` (left, center, right) and `launcher_clock_color`
  (a palette name, the same list as `launcher_color`).
  The sizes are named rather than in pixels because *large* is a decision and
  *72px* is an experiment, and each is still a range so it adapts between panel
  shapes. Measured at 1280x800: 40px at `small` through **130px** at `huge`.
- `launcher_css` stays, and stays **last**, so it still overrides these for
  anything they cannot say.

## 2.4.3

- **`launcher_css`** -- your own styling for the launcher, last in the page's
  stylesheet so it wins. This is where the clock's size, its place and its
  colour live, and the date's and the weather's and the tiles' too. One option
  rather than a setting per thing: the next ask would have been the date, then
  the weather, then the cards, and a form nobody can read is a form where the
  setting that matters gets missed. The documentation lists the selectors and
  carries lines to copy.
- It can only ever look wrong: a stylesheet runs nothing, an unknown rule is
  skipped, and anything resembling the end of the element is neutralised --
  verified against a deliberate attempt to close the tag and write markup
  after it.

## 2.4.2

- **The weather now appears.** It asked Home Assistant at the shared `url:` as
  it stands -- and that is nearly always a dashboard, so it was requesting
  `http://homeassistant:8123/lovelace/0/api/states/weather.home`, a 404 every
  time. It takes the origin now. Reported as the weather never showing while
  the clock beside it worked.
- The failure line names **the address it tried**, which is what would have
  made the above obvious in one look instead of none. And an entity that was
  asked for but cannot be read for a structural reason -- no `url:`, no
  `token:` -- now says so rather than staying silent.

## 2.4.1

- **Silence is no longer sent to the panel**, which was costing **93.8 KiB/s**
  for ever on any panel with sound -- 48 kHz of 16-bit mono, streamed at a
  screen showing a page that was playing nothing. Found in a user's log the
  day after the counter that shows it arrived: `0.0 pictures/s, 0.0 KiB/s ...
  sound 50/s` on a still page, which contradicts the one thing this project
  advertises loudest -- that an idle panel costs nothing.
  Exactly-zero blocks are dropped, so a quiet passage one sample away from
  silence still goes out.

## 2.4.0

- **The launcher has a clock, a date and the weather**, above the links, as on
  Homepage. `launcher_clock` is on by default; `launcher_weather` takes a Home
  Assistant weather entity and is empty for none.
  - They are formatted by the browser, so they follow the panel's `locale`:
    `fr-FR` gives `19:00` and `mercredi 2 septembre` without this add-on
    knowing any French.
  - **No seconds**, deliberately: a digit changing every second is a rectangle
    sent to the panel every second for as long as it is awake. On the minute it
    is one small rectangle a minute.
  - The weather is read by the **add-on**, which already has the token and the
    address, and served to the page from 127.0.0.1 -- the page never reaches
    Home Assistant and never carries the token. Refreshed every ten minutes; a
    reading that cannot be had leaves the launcher with no weather and one line
    in the log.

- **The `DRM yes/no` field is gone, because it was wrong twice over.** It
  reported whether `navigator.requestMediaKeySystemAccess` exists, asked on
  `about:blank` -- where, measured, it does not exist for any browser, so the
  line said `no` always. And in a secure context, where it does exist, that
  says nothing about Widevine: the same browser answers `NotSupportedError`
  for `com.widevine.alpha`.
- In its place, one honest line on the first secure page a panel opens, asking
  for Widevine itself: **`Browser: no Widevine DRM, so Netflix, Prime Video,
  Disney+ and anything else that requires it will not play`**. Nothing is
  printed on an insecure page, where the question cannot be asked at all.

## 2.3.4

- `stats` now ends with **`sound N/s`** whenever there is sound, and `lost`
  when the link was too busy to take it. The capture runs at 48 kHz whatever
  `fps` is, in 20 ms blocks, so 50 a second is all of it arriving -- and
  "the audio breaks up" had no number to look at before this. The sound is
  **not** slowed by a link's `fps`: it is not tied to the picture rate at all,
  and a lower limit gives it more room rather than less.

## 2.3.3

- **A frame limit per link** (`fps:` beside `quality:`), and it is the setting
  that fixes a stuttering cast where lowering the quality did not. Measured on
  a panel: at quality 20 it saturated carrying **919 KiB/s with 42% waiting**,
  where the same panel had carried **1429 KiB/s at 1%** browsing a few minutes
  earlier. Fewer bytes, forty times the waiting -- so bytes are not what runs
  out. On full motion every picture is a whole panel, and a whole panel is a
  fixed cost the board pays each time whatever the JPEG weighs, so the number
  of them is the thing to lower. `fps: 15` on the YouTube link; the dashboard
  keeps the panel's own rate. A touch does not lift a capped link past its
  limit, or the stutter would return for two seconds on every brush of the
  glass.

## 2.3.2

- **The image is about 323 MB smaller.** `playwright install chromium` fetches
  two browsers -- the full Chromium (597 MB measured) and the headless shell
  (323 MB) -- and the sender stopped wanting the shell when it started
  preferring the full build: the shell is a cut-down one and looks like it to
  any site, which matters to anybody signing in from a panel. It was a fallback
  below a fallback, since a system browser is preferred above both. `--no-shell`
  drops it, and an older Playwright that has never heard of the flag still
  installs both.
- If no browser at all will start, the log now names each one that was looked
  for instead of leaving a bare "Executable doesn't exist".

## 2.3.1

- Pages are now told the panel **has a touchscreen**. It was reporting
  `navigator.maxTouchPoints: 0` on a device whose only input is a finger --
  harmless on a desktop page, a flat contradiction on one given a phone or
  tablet user agent, which some sites answer by serving the desktop layout
  anyway. Measured safe for the way contacts are replayed: a press still fires
  pointerdown, mousedown, mouseup and click, and scrolling is unchanged.
  Off with `no_touch`.
- **YouTube has one documented arrangement now, and it works completely**:
  `youtube.com/tv` with a smart-television `user_agent` on the link,
  `quality: 20`, signed in with a code typed on your phone, and the phone as
  the remote. Every other route is listed with the way it fails -- the
  pairing does not sign the ordinary site in, a phone or tablet string lands
  on that same site, and a Chromecast string gets the idle "ready to cast"
  screen. See **YouTube: television mode, and the phone as its remote**.
- `quality: 20` is the starting point there, and the arithmetic is why: full
  motion makes every picture a whole panel, so 800x1280 at quality 40 asks for
  1.8-2.5 MB/s from the radio. Video hides compression far better than a
  dashboard does.

## 2.3.0

- **A `user_agent` per link**, beside the `quality` per link, and for the same
  reason: a panel is not a television anywhere except on one tile. Setting it
  on the panel told Home Assistant and the launcher they were talking to a
  television too. It is applied to the **request** rather than afterwards,
  which is the only place it can work -- `youtube.com/tv` is a junction, not a
  page, and by the time the address could be read the redirect has happened.
- Use a **smart-television** string. A **Chromecast** one (`CrKey/...`) makes
  YouTube show its "ready to cast" screen and wait for a phone to send it
  something, which is not the television interface and has no sign-in code on
  it. The previous release recommended one; it was wrong.

## 2.2.1

- **`import_profile`** -- sign in on a machine with an ordinary browser, and
  hand the profile to a panel. This is the answer to Google refusing to sign a
  driven browser in, and it comes from the right observation: a Raspberry Pi
  with Chromium signs in fine, because a person is driving it. What Google
  checks is the signing in; afterwards the session is a cookie, and a cookie
  written by an ordinary browser works here -- measured end to end. See
  **Signing in somewhere else** in the documentation, and note the
  `--password-store=basic` step, which the copy fails silently without.

- The log now says **where a page actually ended up** when that is not where it
  was sent: `Arrived at ... (asked for ...)`. A site may decide the browser is
  not the sort it serves an address to and redirect --
  `youtube.com/tv` does exactly that unless the browser says it is a
  television -- and from the log that was indistinguishable from the address
  being wrong. Nothing is printed for a page that did not move.
- The client hints sent beside a `user_agent` now follow **it** rather than the
  browser underneath. Before, a panel claiming to be a Chromecast sent
  `Chrome/85` in one header and `"Chromium";v="141"` in the other.

## 2.2.0

- **`show_media`** -- a diagnostic for the one report nothing in the log could
  answer: a video that plays for a few seconds and stops. While a video is
  playing it prints the playhead and how many seconds are buffered **in front
  of it**, every two seconds, plus the frames the browser itself dropped. The
  media lines that existed before only appeared once something had already
  gone wrong, which is the wrong moment -- the evidence is in the seconds
  before the stall, when nothing fires and the log is silent. Off by default.
- **`locale`** -- the language pages are asked for, `en-US` unless you change
  it. Measured on the shipped browser: left to itself it sent **no
  `Accept-Language` header at all** and reported `en-US@posix` as its language,
  which no real browser produces. So every site served its own default language
  whatever the household speaks. Set it to `fr-FR` and YouTube, Jellyfin and
  the rest come up in French.
- **`user_agent`**, and a better browser when the installed one is missing --
  both for people trying to reach an account from a panel, which is where
  subscriptions live. Google refuses to sign a browser in when it can tell it
  is driven, so the answer for YouTube is its **television interface**, made
  for devices with a screen and no keyboard: you enter a code on your phone
  instead of a password. See **Google, and YouTube subscriptions** in the
  documentation.
- The fallback browser is now the **full Chromium** rather than the headless
  shell. Measured on the same page: the shell has no `window.chrome`, reports
  0 plugins where a real Chrome reports 5, says its PDF viewer is off, and
  answers `denied` where a real browser answers `default`. Costs a tenth more
  processor and nothing in frame rate. It is also taken when an installed
  browser exists but will not start, which the previous code did not do.

## 2.0.0

**The slug moved, so this is not an update -- it is a new add-on.** It was
`usb_display_panel`, inherited from Espressif's `usb_display`, and it stopped
describing anything the day the picture started arriving over Wi-Fi. Home
Assistant identifies an add-on by its slug, so the Supervisor sees a new one:
options do not carry over and `/data` starts empty.

See **Moving from the old add-on** in the documentation. In short: copy the old
add-on's options with **Edit in YAML**, paste them here, start this one, and
only then uninstall the old one. No option changed its name, type or meaning.
The panels' firmware is untouched -- nothing to reflash.

- Named **Portall** in the store, with an icon and a logo of its own.
- Service logos in the launcher: `home-assistant`, `jellyfin`, `plex`,
  `youtube`, `proxmox`, `unraid` and forty-five more, drawn from the add-on
  itself and never fetched.
- Icons by name, in French or in English -- `cuisine` or `kitchen`, `serrure`
  or `lock` -- 520 names onto 116 icons. Typing an emoji still works.
- The launcher takes a theme, a Tailwind colour, a wallpaper with a blur and a
  dim, groups of links, and a description under each name.
- Swipe sideways from the top-left corner to come home, as well as holding it,
  and the corner now shows itself when a page arrives.
- `--show-touches` says where a contact landed, where that is on the page and
  what the page has there.

## 1.58.0

- Coming home no longer leaves the panel showing the page it left: a still
  page paints once, so the frame at a navigation cannot be thrown away.

## 1.57.0

- Holding the corner survives a real finger: the gesture is cancelled by
  leaving the corner, not by a few pixels of wander.
- Startup says how long it took and how much of it was the browser.

## 1.56.0

- A panel started on the launcher opens Home Assistant logged in: the token is
  installed for the dashboard's own address rather than the launcher's, and it
  is no longer written into the storage of every other site a panel visits.

## 1.55.0

- The launcher is shipped in the image. It was imported and never copied, so
  the add-on died before serving a panel.

## 1.54.0

- A blank token field no longer stops a panel starting.
