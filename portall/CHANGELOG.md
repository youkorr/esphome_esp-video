# Changelog

## 4.21.1

- **`max_rate` is now a real ceiling.** In 4.21.0 a scene so heavy that it
  was still over the limit at the lowest quality (25) went over it anyway --
  measured on a panel at 2400: two windows at 2759 and 2854 KiB/s, with the
  picture frozen for a quarter of a second. Now the next picture waits the
  few milliseconds the link needs instead, so that scene loses a few pictures
  a second and the rate never goes past `max_rate`. `fps: 30` can stay on a
  video link. With `stats` on, `12 held` at the end of a line counts the
  pictures that waited.

## 4.21.0

- **Steadier video on ESP32-C6 panels.** New setting **`max_rate`** under
  *Common settings*, 2400 KiB/s by default, and on every link. A busy video
  scene is now sent a little softer so the panel's Wi-Fi is never asked for
  more than it can take; the quality comes back as soon as the scene is
  simpler, and the number of pictures a second does not change. Before, a
  busy scene went past what the ESP32-C6 carries and froze the picture and
  the sound for a third of a second at a time. `0` turns it off. With `stats`
  on, `quality 38-50` at the end of a line shows it working.

## 4.20.1

- **Icons are full size again.** 4.19.7 and 4.20.0 made the icons smaller
  on narrow tiles and on buttons. They are back to the size they have
  everywhere else, at every column count and in both shapes. Only the
  words and the spacing shrink on a narrow tile now, so no word is cut and
  four columns are no taller than before. A button can be a little taller
  than 150x100 so the full-size icon and a two-line name fit.

## 4.20.0

- **Links can be buttons.** A new setting, **`tiles`**, under *Launcher*
  and in each screen's own launcher: `cards` (as before) or `buttons` --
  smaller buttons with the icon on top and the name underneath, like a
  panel built with LVGL, pushed down when pressed. A button keeps its size
  instead of filling the row, and does not show the description. Nothing
  changes unless you choose `buttons`.

## 4.19.7

- **Four columns are compact again.** 4.19.6 put the icon above the name
  on every narrow tile, which made four columns a third taller than before.
  At four columns the icon now stays beside the name and both shrink a
  little to fit, so no word is cut and the tiles are no taller than with
  three. Only five and six columns put the icon above the name. One to
  three columns are unchanged.

## 4.19.6

- **Tile names are no longer cut in the middle of a word.** With 4 to 6
  columns, a tile was too narrow for its name to sit beside the icon, and
  names came out as "Jellyfi n" or one letter per line. A narrow tile now
  puts its icon above its name, like apps on a phone, and sizes the text to
  the tile. Wide tiles look exactly as before. Checked at 1280x800,
  1024x600 and 800x1280 with 3 to 6 columns: no cut word, no tile off the
  screen.

## 4.19.5

- **Stereo sound for a panel, as a test.** A panel's *Advanced* gains
  **`stereo`**: the page's sound goes out in two channels instead of one, at
  192 KiB/s instead of 96. Off by default, so nothing changes unless it is
  turned on. **Flash the panel with the latest `portall` first** -- an older
  board plays stereo at half speed -- and give the speaker at the end two
  channels (`num_channels: 2` on a Bluetooth speaker). See *Stereo* in the
  documentation.

## 4.19.4

- **The iPhone's volume buttons and mute now work on the HomeKit remote.**
  The accessory had no speaker, so the widget had nothing to send them to --
  the one control that did nothing. They now turn the page's sound on that
  panel up and down, in ten steps, and mute it; unmuting returns to the same
  level, and volume-up while muted unmutes. The level is kept by the add-on,
  so a panel whose sender restarts does not come back at full volume. The
  panel's own volume slider in Home Assistant is separate and unchanged.
  Nothing to do: the accessory announces that it changed, so the iPhone reads
  it again by itself. If the volume still does nothing after the update,
  remove the panel from the Home app and pair it again once.

## 4.19.3

- **Swiping sends up to 45 pictures a second instead of 30.** For the two
  seconds after the screen is touched, the limit was 30; with 4.19.2 making
  30 really mean 30, that limit was what still held a swipe back. It is 45 now.
  Measured against a panel that never makes the add-on wait: 30 pictures a
  second before, 36 to 39 after, and no more above 45 because there the
  browser decides. On a slow link nothing changes. A link with its own `fps:`
  still caps it, so a film is not affected.
- **Documentation:** the Configuration page's YAML view does NOT pass unknown
  settings through to the sender, as DOCS.md used to say. Home Assistant
  removes any option the add-on does not declare.

## 4.19.2

- **Up to a quarter more pictures a second while something moves.** The frame
  limit restarted from whichever turn of the sender's loop noticed a new
  picture, and the loop looks only every fifteen milliseconds or so -- so every
  interval was rounded up: at `fps: 30` the panel received about 23.6 pictures
  a second even when nothing else was short. The limit now keeps to its
  schedule. Measured against a panel that never makes the sender wait: 23.6 ->
  30.0 at 30, 13.9 -> 15.0 at 15, the longest gap no longer; unchanged on a
  slow link, where the link is what decides. Nothing to configure.

## 4.19.1

- **The settings page speaks French (and English), in six numbered sections.**
  Reported as not organised: the form showed raw keys -- panels, links,
  launcher, launchers. Every setting now has a name and a one-line
  explanation, down to the fields inside a screen or a link:
  🖥️ 1 · Mes écrans, 🔗 2 · Liens communs, 🎨 3 · Apparence de la page de
  liens, 🧩 4 · Page de liens propre à un écran, ⚙️ 5 · Réglages communs,
  🐞 6 · Diagnostic. The page follows Home Assistant's language. Nothing you
  have saved changes: the names are what the form shows, the keys in
  **Edit in YAML** are the same as before.

## 4.19.0

- **Each panel its own launcher, entirely.** Asked for as each panel having
  its own links, buttons, columns and every launcher option, independent of
  the others. A new list, `launchers:`, takes one entry per panel -- `panel:`
  names it -- with its own `links:` and any launcher setting: `theme`,
  `columns`, `align`, the clock, the date, the weather, the background and the
  slideshow. What an entry leaves out is the house launcher's. Each panel's
  launcher is served on its own, so its wallpaper and weather are its own too.
  A panel with no entry shows the house's `links:` and `launcher:`, exactly as
  before -- nothing already configured changes.
- **Removed: `links:` on a panel (4.18.0) and `columns:` under a panel's
  `advanced:` (4.17.0).** Both are an entry under `launchers:` now. If you set
  either, move it there; Home Assistant drops the old fields with a warning in
  its log and the add-on still starts.

## 4.18.1

- **The log says how to choose a panel's links.** Reported as not working on
  4.18.0, from a configuration whose panels had no `links:` line at all --
  so every panel showed every link, which is what an empty choice means.
  The field is optional, so the form never shows it. At startup a panel that
  chooses nothing now says so and names the line to add under it.

## 4.18.0

- **Each panel chooses its own links, in its own entry.** Reported on 4.17.0:
  each panel still did not have its own links chosen independently. 4.17.0
  put the choice on each LINK, as a list of panels, so changing what one
  screen shows meant visiting every link. A panel now takes `links:` -- the
  names of the links it shows, separated by commas -- and every panel chooses
  on its own. Left empty it shows every link, as before. A name that matches
  no link is said in the log at startup.
- **`panels:` on a link is gone.** If you set it in 4.17.0, move the choice to
  the panels' own `links:`. The Supervisor drops the old field with a warning
  in its log; it does not stop the add-on.

## 4.17.0

- **Each panel shows its own links.** Reported: with several panels, every one
  showed every link. A link now takes `panels:` -- a panel's name, or several
  separated by commas -- and appears on those panels only. Left empty it
  appears on every panel, exactly as before, so nothing already configured
  changes. A name that matches no panel is said in the log at startup rather
  than silently hiding the link.
- **Each panel its own number of columns.** Reported: four across suits a
  1280x800 and not a 1024x600. Measured in the add-on's own browser, four
  across at 1024x600 makes tiles 220 px wide and breaks names inside words
  ("Jellyfi n", "YouTu be"); three across gives 298 px and breaks none. A
  panel now takes `columns:` under `advanced:`, overriding `launcher:
  columns:` for that screen only.

## 4.16.0

- **One arrow, one move, inside every link.** Reported from panels using the
  project: up, down, left and right need several presses each inside a link,
  "compared to YouTube which is very fluid". Measured on the shipped browser
  against a grid of links with no key handler of its own, taller than the
  panel: **three presses per row**, and one sideways where nothing has to
  scroll. That is Chromium's own spatial navigation, which scrolls a row into
  view before it will focus it -- YouTube's television interface never shows
  it because it moves its own focus and never falls through to the browser.

  The sender now carries its own fallback: one press, one move, with the
  chosen thing scrolled into view. It is **one press per row** on the same
  fixture.

  It stands aside wherever the page navigates for itself, through two gates:
  an arrow the page swallows is never touched, and a page that moves the
  focus without swallowing is noticed on the next turn and left alone for the
  rest of its life. The launcher, YouTube `/tv` and Jellyfin in TV layout are
  unchanged, and a text field keeps its arrows for the caret.

## 4.15.1

- **The Back button of the iPhone's remote now brings a panel home.** Reported
  from a panel: inside any link there was no way back to the launcher, while
  the same thing worked from a paired controller and from the `Remote home`
  button in Home Assistant.

  The way home was mapped onto HomeKit's `Exit` key -- which is in HomeKit's
  own table and **on no button of the Control Centre remote**. That widget
  gives a television exactly five: the pad, Select, Back, Play/Pause and ⓘ.
  So the one action a panel cannot do without sat where nobody could press it.

  Back goes home now, and Escape moves to the ⓘ button. It is the opposite
  split from a Bluetooth remote, deliberately: that one has both a Back and a
  Menu, and this one does not. Escape can afford the obscure button because
  it does nothing on nearly every page a panel shows.

## 4.15.0

- **The iPhone's own remote drives a panel now.** Turn on `homekit` under
  *Defaults*, restart, and each panel appears as a television in the Home app
  and in the remote widget of the iPhone's Control Centre -- the one built for
  an Apple TV. The pad moves between the launcher's tiles, its centre opens
  one, Back is Escape and the TV button brings the panel home.

  Pairing is the Home app's own: the add-on's log prints a QR code to point a
  camera at, and a code to type if you would rather. Unpairing is removing the
  accessory. Nothing is paired to the panel itself and no Bluetooth is
  involved -- the press arrives at the add-on, where the page is rendered.

  It replaces a recipe that was three things to configure: a `universal`
  media player pretending to be a television, a HomeKit bridge in accessory
  mode, and an automation to carry each key across. That is a mechanism to
  operate rather than a thing that exists, which is not what anybody asked
  for.

- **This add-on now runs on the house's own network.** A HomeKit accessory is
  found over mDNS, which does not reach the LAN from the Supervisor's private
  network, and the iPhone has to reach its port directly -- the same reason
  Home Assistant's own HomeKit works that way. The launcher still binds to
  `127.0.0.1` only and is reachable from nowhere else; the one consequence is
  that **port 8099 must be free** on the Home Assistant machine.

## 4.14.0

- **Every site's own press effect works now, not just the launcher's.**
  Reported from a panel as "jellyfin fait pareil que netflix le button ne
  dispose aucun effect" -- and naming those two and not Home Assistant is the
  whole diagnosis. The sender held a tap back until the finger lifted and then
  sent the press and the release together, so `:active` -- which is tied to
  the button really being down -- lasted about as long as one task. Measured
  through a real screencast: a button styled with `:active` alone, which is
  what Jellyfin and Netflix use, appeared pressed in **0 of 1** frames; one
  that animates its own feedback, which is what Home Assistant's Material
  buttons do, appeared in 15 of 25 either way. That is why only two of the
  four were reported.

  A tap now holds the button down for 80 ms, which is what a finger does
  anyway, so the page paints its own pressed state and a frame can contain it.
  The panel shows the press at the moment it used to show nothing, and the
  page acts 80 ms later -- which is the right way round, because what makes an
  interface feel quick is the first acknowledgement rather than the
  completion. `--press-hold 0` on a hand run restores the old behaviour.

## 4.13.0

- **A link tile now looks pressed when you press it.** Reported from a panel
  as the tiles having no effect "comme un button lvgl". The rule was already
  there and was already being applied -- and it lasted a measured median of
  **2.3 ms**, because the sender holds a contact back until the finger lifts
  and then sends the press and the release together. A frame at 25 pictures a
  second is 40 ms, so the pressed look filled 6% of one frame and was almost
  never photographed. It is held for 200 ms now -- about six frames at the
  rate a panel runs at just after a touch -- and made readable across a room.
  The OK button of a remote flashes the chosen tile too.

- **`stats` is off by default, and it should always have been.** It sat under
  a comment reading "all off" and shipped as on, printing one line every five
  seconds per panel: 17 280 lines a day for one panel, which buries everything
  else in the log. **An existing install keeps what it has** -- the Supervisor
  writes these defaults only when the add-on is first installed -- so turn
  `stats` off under **debug** in the add-on's configuration if you have been
  running it.

## 4.12.0

- **A remote or a gamepad now moves between links on sites that do not
  handle the arrows themselves.** Reported from a panel driving an NVIDIA
  Shield: YouTube fine, and Netflix, Orange TV and Jellyfin where "sometimes
  up and down work, with difficulty". That is not the remote or the dongle --
  a browser does not move focus with the arrows, only Tab does, so on a site
  with no spatial navigation of its own they fell through to the browser and
  SCROLLED the page. Up and down had somewhere to go and left and right had
  nothing, which is the report word for word. The browser is launched with
  spatial navigation on now. A page that handles its own arrows still wins --
  measured -- so the launcher, YouTube's television interface and Jellyfin in
  TV layout are unchanged.

## 4.11.1

- **A panel with `keep_profile` off now says what its old profile is costing.**
  Turning the option off leaves the folder where it is -- deliberately, so
  turning it back on finds what you were signed into -- but nothing opens it
  again, so somebody who switched it off to save space would have watched a
  gigabyte not move with no way to learn why. The log names it and says both
  ways out.

## 4.11.0

- **The profile of a panel you removed is removed too.** Reported from a real
  add-on whose `/data` had reached **2.1 GB**: three browser profiles on
  disk, of which one belonged to the only panel still configured. Nothing had
  ever removed one, so a panel renamed or taken out of the list left a whole
  browser's home behind for good. The panels you have configured keep theirs;
  everything else under `/data/profiles` goes at startup, and the log says
  what went and how much it freed.

  Nothing is swept when no panels are configured -- a configuration that
  failed to load must never be read as a house with no panels -- and turning
  `keep_profile` off does not hand that panel's folder to the sweep.

- **A panel's browser no longer fills the disk with cache.** Chromium sizes
  its own cache from the free space it can see, which inside an add-on is the
  whole host disk, so the one panel still configured above held **1.2 GB** on
  its own with a single cache entry of 76 MB in it. It is capped at 150 MB
  now, and the number was measured rather than picked. The same setting
  governs the compiled-code cache, and your add-on's four biggest entries
  there were 13.4 MB each -- Home Assistant's own bundle, on the page a panel
  shows all day. At 150 MB that is still cached, to the byte; at 60 it is
  refused outright. So the cap costs nothing and a tidier-looking number
  would have made every page load slower.

  Chrome's machine-learning accessories are turned off with it. They download
  a model and keep it in the profile -- 46 MB of it in every profile above --
  for a browser whose whole job is to paint a dashboard.

- **The log says what each profile costs**, at startup, so nobody has to open
  a shell inside the container to find out. A figure that keeps climbing past
  a few hundred megabytes is now a bug rather than a panel that has been
  running a long time.

## 4.10.1

- **An arrow at the edge of the tile grid no longer scrolls the page away.**
  Reported from a panel as up causing trouble, and the Bluetooth log showed
  the press being decoded perfectly -- because the fault was here. The
  launcher swallowed an arrow only when it FOUND a tile in that direction, so
  up on the top row (and down on the bottom, and left on the first column)
  fell through to the browser, which scrolls. From the glass that is the
  launcher jumping away from the tile you had just chosen, and the focus ring
  ending up somewhere you cannot see. The arrows belong to the grid on this
  page; there is nothing else on it to scroll to.

- **The first arrow now chooses by direction**: down or right reaches for the
  first tile, up or left for the last, the way a menu does. It always took
  the first before, so up and down did the same thing from a cold page.

## 4.10.0

- **A remote or a gamepad can drive the links.** The tiles are plain
  `<a href>`, and in a browser the arrow keys do not move the focus between
  links -- only Tab does. So a panel could pair a remote, carry its presses
  across the socket and replay them perfectly into the page, and still sit
  there. The launcher listens for the arrows now and moves between tiles
  geometrically: down means the tile below, not the next one in the markup.

  The chosen tile shows a ring in the accent colour, because a panel driven by
  arrows has nothing else to say where it is -- no pointer, no hover. It is not
  animated: a ring that pulses is a repaint, and a repaint is a rectangle on
  the wire for as long as the panel is awake. Nothing is focused when the page
  loads, so a panel nobody drives with a remote sends exactly what it sent
  before; the first arrow chooses rather than moves.

- **`portall.home` is back**, one release after it was removed -- and putting
  it back needed no change to any sender at all. 4.9.0 kept the sender's half
  of the `'H'` message deliberately, on the grounds that a board is flashed by
  hand while the sender is fetched when this image is built, so the two are
  never updated together and the tolerant end is the one to keep. A remote with
  a Back button is what wanted it, and that patience is what paid.

- **`portall.key`** is the new half: it replays one key into the browser
  rendering the page. What crosses the wire is the HID usage the device itself
  reported, and this sender turns it into the browser's own name for that key
  -- one table, here, correctable by rebuilding this image rather than by
  reflashing every panel in the house.

## 4.9.0

- **`portall.home` is removed.** It was added so the way back could be
  something other than a finger, and nobody was calling it: the corner gesture
  is how a panel comes home. A message type nothing sends is a thing to keep
  in step for no one.

  Gone from the component, the header and the wire. **The sender still
  understands the message**, deliberately -- a board is flashed by hand and the
  sender is fetched when the add-on's image is built, so the two are never
  updated together and the tolerant end is the one to keep.

- **`portall.sleep` and `portall.wake` stay, and the pair is the point.** They
  are the only thing on the board that reaches the SENDER: nothing else calls
  `set_awake`, no touch wakes it, and ESPHome cannot do it natively because
  the sender is a process on the Home Assistant server rather than anything on
  the panel. Turning the backlight off without `portall.sleep` leaves the
  server rendering and transmitting a dashboard into the dark; `portall.wake`
  is what starts it again. Using neither is a perfectly good choice -- it just
  costs that traffic.

## 4.8.0

- **Reolink and Immich are drawn at the right size now**, reported as too
  small beside the other logos. Two different causes.

  **Immich carried a transparent margin**: 25% of its picture was nothing at
  all, so 33 px of its 43 were drawn where a simple-icons glyph fills all 43.
  That margin is cropped off before it is embedded, and it measures 42x42
  against Jellyfin's 43x42 -- the same.

  **Reolink is a badge**, a white R on its own blue rounded square, and the
  square is not the mark. Drawn at the size a bare glyph wants, the letter
  inside it was 23 px against a glyph's 43. A badge brings its own ground, so
  it now takes the whole icon square the way an app icon does everywhere
  else, and the R reads at 39.

  Which of the two a picture is comes from its own alpha rather than a
  judgement: 95% opaque is a badge, 57% is a glyph with the background cut
  away.

## 4.7.0

- **Immich draws its five colours again.** Reported from a panel as white
  instead of coloured, and 4.5.0 is what did it: the mark came from
  simple-icons, whose tracings are a single colour, and that blue measured
  **2.46:1** against a dark card -- below the 3:1 the new contrast rule asks
  for, so it was drawn in the theme's ink.

  The rule is right and 2.46:1 really is too faint. What was wrong is asking a
  five-colour logo to be one colour at all, so `immich` is now carried as Home
  Assistant's own icon for that integration, like `reolink`. Measured on the
  rendered page, both themes: red, orange, green, blue and pink, five distinct
  colours, identical on dark and light.

  If another logo comes out flat and its real one is not, say so -- anything
  Home Assistant has an integration for can have the same treatment.

## 4.6.0

- **`reolink` draws the real Reolink logo.** simple-icons, where the other 50
  marks come from, carries 3460 brands and Reolink is not one of them -- so
  there was no public-domain tracing to embed and the name gave a camera emoji.

  Home Assistant keeps a square icon for every integration it supports, in its
  own `home-assistant/brands` repository, and that is the right source for an
  add-on: same project, same nominative use, and the icon a household already
  sees beside that integration. It is carried here rather than fetched, like
  every other picture on the launcher, and it is not recoloured -- it brings
  its own blue ground.

  Measured on the rendered page, both themes: the image loads at 256x256,
  draws at 43x43 on the tile, and reads Reolink's blue.

## 4.5.0

- **The YouTube logo was a white badge on a dark panel.** Its red is `#FF0000`,
  whose relative luminance is 0.213 against a threshold of 0.22 -- so the rule
  meant to rescue near-black marks swapped it for the near-white ink, and the
  play badge came out white. Netflix cleared the same threshold by 0.002, which
  was luck rather than a design.

  The measure was wrong: what decides whether a mark can be made out is its
  **contrast against the tile**, not how bright it is on its own, and those two
  part company exactly at a saturated red. Against the real dark card, that red
  is **4.31:1** -- comfortable. The rule is now WCAG's 3:1 for a non-text
  graphic, computed with the gamma properly undone.

- **And the light-theme half of that rule had never done anything at all.** It
  fired only above 0.82, which is near-white, and no brand in the collection is
  near-white -- so on a light theme it replaced **nothing of fifty**, while
  Spotify sat at 1.92:1, Plex at 1.97 and Jellyfin at 2.86, all washed out.
  Sixteen marks are legible there now.

  Measured on the rendered page, both themes: YouTube `rgb(255,0,0)` on dark
  where it was `rgb(232,236,244)`, GitHub still the ink, Spotify and Jellyfin
  keeping their colours on dark and taking the ink on light.

- **`reolink` gives a camera** rather than nothing, along with `hikvision`,
  `dahua`, `tapo`, `annke`, `amcrest` and `foscam`. None of them is in
  simple-icons -- checked against all 3460 marks it carries -- so there is no
  public-domain tracing to embed, and a name is the honest answer. The same
  call Prime Video already had.

## 4.4.0

**`home_taps` is gone.** It was added two releases ago, and counting taps
costs the corner in a way no setting can undo: the first of two taps cannot
be acted on until the window for the second has passed, so a tap there is
either late or swallowed. A page may have its own control under that corner
-- Jellyfin's player puts its Back arrow exactly on it -- so the way home is
the hold and the sideways swipe again, as it was before.

If you had set it, the Supervisor drops the key and says so once. Nothing
else to do.

- **A quick tap in the top-left corner reaches the page**, with no delay.
  Under 350 ms is a tap and goes straight through.

- **A press longer than that still reaches nothing.** It was somebody
  attempting the hold and letting go early, and delivering it is what used to
  land on Home Assistant's sidebar button and repaint the whole screen at
  410 KiB/s. The mark lights up instead.

- The startup line says so rather than printing a number:
  `hold 1s, a tap under 0.35s reaches the page`.

Seven cases measured on a panel's own geometry.

## 4.3.0

- `home_taps` defaulted to `0`, so a new install got the hold and the swipe.
  4.4.0 removes the setting outright, for the reason above.

## 4.2.0

- **A tap in the top-left corner reaches the page again.** Jellyfin's player
  puts its Back arrow exactly where the home corner is, and a panel watching a
  film could not leave it -- every tap there was swallowed. So was anything
  else a page puts in that corner.

  A single tap is now held back for 450 ms and then delivered where it landed:
  the cost of a double tap everywhere it exists, since the first of two cannot
  be acted on until the window for the second has passed. Two taps still go
  home and the page is told nothing at all.

  A press **longer** than 350 ms in the corner still reaches nothing, and that
  half stays: it was somebody attempting the hold and letting go early, and
  delivering it is what used to open Home Assistant's sidebar on every failed
  attempt. Quick tap through, slow press swallowed.

  Nine cases measured against a panel's own geometry, and the fault reproduced
  against 4.1.0 first: the quick corner tap pressed the page **0 times** there
  and once here, a corner tap followed by a tap elsewhere lost one of the two,
  and the swipe, the diagonal drag and the downward drag are unchanged.

## 4.1.0

- **The launcher's weather works without a token.** It read Home Assistant
  through the house's long-lived token, so it depended on *where* that token
  had been put -- and 3.0.0 moved tokens onto the links, so filling in a
  panel's `token:` instead left the weather with no address to read from. An
  add-on does not need any of that: the Supervisor gives it a credential of its
  own and proxies `/core/api/...` to Home Assistant with it. `entity:` is now
  the whole setting, and nothing else about your configuration matters to it.

  This needs `homeassistant_api: true`, which is new in this version -- so the
  weather starts working on the update and not before.

- **The failure line could not tell the truth.** With no token on a link it
  said "there is no url: to read it from" while a url sat right there. It can
  only appear at all outside a Home Assistant add-on now, where it names both
  halves at once.

- **The form says which of the two tokens to fill in.** The link's is the one
  that matters -- a token belongs to an address, and a link is the only thing
  here that names one. A panel's `token:` is an override, for the rare panel
  that opens a *different* Home Assistant, and both the form and the
  documentation now say so where somebody filling one in will read it.

## 4.0.0

**Read the migration below before updating.**

- **The form is grouped.** Thirty settings sat at the root, nineteen of them
  describing the launcher's appearance, and `panels:` -- the one thing that has
  to be filled in for anything to appear -- was the last of them. They are
  behind five headings now: **panels**, **links**, **launcher**, **defaults**,
  **debug**. A panel goes from twenty-three fields to six, with the three
  calibration values together under `touch:` and everything that has a default
  under `advanced:`.

  **Nothing was removed and nothing changed its meaning.** Proved rather than
  asserted: the grouped defaults, put back flat, are the old defaults key for
  key with no value changed; the sender's command line is identical; and the
  launcher page rendered from each is byte for byte the same 8030 bytes.

  One function does the ungrouping, so the regroup cost that function rather
  than every reader of every setting -- `launcher.py` and `ha_send.py` are
  untouched.

- **Migration, and it is manual for a reason.** The Supervisor drops a key the
  schema no longer knows *before* `run.py` ever sees it, so no code inside the
  add-on can carry a 3.x configuration across. Open the add-on's options, use
  the three-dot **Edit in YAML**, copy the lot, and run it through
  `tools/convert4.py` in this repository -- or paste it where you got this
  add-on from and ask. The converter also names the settings that are gone
  rather than dropping them in silence.

- **`home_corner` and `home_hold` are gone**, as of 3.7.1. The corner is 14% of
  each axis and the hold is one second; two taps in that corner need neither.

- **And the check that catches this class of fault was itself broken by the
  change.** `checkaddon.py` walked only dictionaries, so with `panels:` spelled
  as a list it examined eight of twenty-seven settings and passed -- a panel's
  whole `advanced:` group, the newest part of the form, never looked at once.
  It walks lists now, resolves a renamed flag out of `run.py`'s own table, and
  runs each probe through the real loader rather than through `command_for()`
  alone, so the group in the middle is exercised too. Both faults were
  reproduced against it before it was believed.

## 3.8.0

- **A `debug:` group, to find out whether this form can be grouped at all.**
  The options are thirty keys deep at the root, nineteen of them describing the
  launcher's appearance, and `panels:` -- the one thing anybody must fill in --
  is the last of them. Folding related settings behind one heading is the
  obvious answer, and `links:` and `panels:` already prove the Supervisor folds
  a list of objects. Whether it folds a plain dictionary the same way has never
  been seen here, and moving keys is the one step that cannot be undone.

  So this release moves nothing. `debug:` carries `stats`, `show_media` and
  `show_touches` **alongside** the three flat keys, which stay exactly where
  they are. Tick either and the diagnostic turns on; a Supervisor that will not
  fold the group costs nothing at all.

  What to look at: open the add-on's options and see whether `debug` appears as
  a heading with three switches under it, or as something unusable. That
  answers it, and the full regroup -- five headings instead of thirty keys --
  follows or does not.

## 3.7.1

- **`home_corner` and `home_hold` come off the form.** They were added because
  a three-second hold in a small corner was barely reachable, and they were the
  right answer to that. Two taps need neither: a corner is far easier to hit
  twice than to stand still in, so the default 14% and 1s are simply right and
  nobody has to read past two settings to find that out.

  The behaviours stay exactly where they were -- the hold still works at one
  second, the corner is still 14% of each axis. Only the knobs are gone, and
  the Supervisor drops a key it no longer knows with a warning rather than a
  failure. A panel carrying `home_corner: 25` goes back to 14, which is 179x112
  page pixels and ample for a tap.

## 3.7.0

- **Two taps in the corner bring the panel home.** About 300 ms against one to
  three seconds of holding perfectly still, and it is the same corner with the
  same mark, so there is nothing new to find. `home_taps: 2` by default; `1`
  makes a single tap do it and `0` leaves only the hold and the sideways swipe.

  It is 3.5.0 that made this possible: once a press in the corner stopped
  reaching the page, the corner was free to count presses. Before that, every
  tap of a double tap would have pressed whatever sits underneath -- on a Home
  Assistant dashboard, the sidebar button.

  Driven against a stub browser on a panel's own geometry over nine cases: two
  taps 0.2s apart go home and 0.9s apart do not; one tap does not; a tap
  elsewhere between two corner taps resets the count; an ordinary double tap
  away from the corner still clicks the page twice; the hold and the swipe are
  untouched; and `1` and `0` behave as they say.
- The startup line and the return line name which of the four ways asked --
  `held 1.0s`, `tapped`, `swiped`, or `asked by the board`.

## 3.6.0

- **The way back is no longer only a finger: `portall.home`.** Until now the
  gesture lived entirely in the sender, which is what made it work at all -- a
  panel has no Back button and a site playing full screen swallows whatever
  the page is given, so the corner has to be decided by arithmetic before the
  page sees the contact. The cost of that was that only a finger could ever
  ask.

  The board can ask now, so anything in its YAML can: a physical button, a
  Home Assistant automation, a presence sensor releasing, a voice command.
  It sits beside `portall.sleep` and `portall.wake`, which already go the same
  way, and like them it only TELLS the sender -- nothing on the board changes.

  ```yaml
  button:
    - platform: template
      name: "Retour a l'accueil"
      on_press:
        - portall.home: udisp
  ```

  Needs the component from this repository's `main` on the panel as well as
  this add-on: the board is what sends the new message.

## 3.5.1

- **The return now reports one figure, which is what a stopwatch gives.**
  The log offered its parts -- hold, opening, picture, socket -- and never
  their sum, so comparing it with what somebody counts on the glass took
  arithmetic. It now ends with `Home: 1.4s from the finger landing to the
  picture leaving for the panel (hold 1.0s as asked, then 0.4s)`.

  Everything in that figure happens on the machine doing the rendering. If
  the screen takes longer, the difference is the board, and no setting here
  shortens it.

  Simulated across the gesture at 0.5, 1, 2 and 3 seconds and at loop rates
  from 110 Hz down to 10 Hz, with and without a finger that shifts while it
  holds: the trigger lands within 11 ms of what was asked in every case, so
  `home_hold: 1` and `home_hold: 3` differ by exactly the two seconds
  between them.

## 3.5.0

- **A press in the corner no longer reaches the page.** This is what the
  "coming home takes five seconds" reports were actually made of, and it took
  a timestamped log to see: there was never one slow gesture, there were
  *two* gestures. A first attempt lasting 0.13s was too short to go home, so
  it was delivered as an ordinary press -- and the corner covers Home
  Assistant's sidebar button, which opened and repainted the whole screen
  (410 KiB/s in the window after it). A second, proper hold then followed.
  First finger to picture on the wire: 4.67s, of which the gesture itself was
  3.4s.

  The corner belongs to the way back now. A press that lands in it is never
  passed on, however short.

- **And a swallowed press lights the mark again**, rather than doing nothing
  and saying nothing -- which is what made somebody try again, and trying
  again is where the seconds went.

- Only a press that would have been a *tap* is swallowed: a drag out of the
  corner is a scroll and is untouched, a sideways swipe still goes home, and
  a press anywhere else is unaffected. Checked over all six.

## 3.4.1

- **The return is now timed all the way to the socket.** `offer()` hands a
  picture to a thread and returns at once, so `first picture 0.0s` meant
  *given to the writer*, not *gone*. A panel's log showed 3.4s from finger to
  that line while the screen was reported as taking over five, and the gap
  had to be either the writing or the board -- with no way to tell which. The
  log now says when the picture finished going down the socket, and says
  outright that anything later than that is the board rather than the
  machine rendering.
- **One reading of the clock per timestamp.** `strftime` and `time()` were
  read separately, so at a second boundary they disagreed -- a real log
  carried `19:27:30.9` followed by `19:27:30.0` on a *later* line. The corner
  lines carry milliseconds now too, since that is the resolution they are
  read at.

## 3.4.0

- **Coming home sent the page you were leaving, first.** Reported as three
  seconds of hold taking more than five to arrive, and the log could not show
  it because the log was measuring the wrong picture.

  At a navigation the sender clears what it diffs against, which makes the
  next turn want to send whether or not the browser has painted anything new
  -- and the last decoded frame is still the page being left. So the panel
  received a full redraw of the dashboard, 130 KiB of it, and only then the
  launcher, once Chromium had painted and encoded the new page. From the
  glass that is a return that drags; from the log it read `first picture
  0.0s`, which was true and about the wrong picture.

  The frame in hand is now dropped along with the comparison. Nothing is sent
  until the new page has actually painted, and the timing line says `first
  picture of the new page`, which is the figure that was wanted all along.
- **Waking a parked panel had the same fault**, in the same shape: it woke on
  the page it had before it slept and changed to the real one a moment later.

## 3.3.4

- **The log claimed a successful hold had been abandoned.** A hold that has
  gone home leaves its clock in place -- only the "already spent" flag is
  set -- so the finger lifting half a second later printed *the hold is
  abandoned* about a gesture that had just worked. A diagnostic that accuses
  a working gesture of failing is worse than no diagnostic at all, and this
  one was a day old.
- **And the success now says so where it happens**, with the thing nobody can
  know from the glass: `hold complete after 3.00s -- going home. Keeping the
  finger down past this point changes nothing`. A panel measured 3.94s of
  finger for a 3s hold, because there is no way to feel the moment it fires.

## 3.3.3

- **Every line in this log now carries the time it happened at.** The
  supervisor does not stamp an add-on's output, so three lines in a row could
  be a second or a minute apart -- which is the entire question whenever
  something is "too slow".
- **`show_touches` is now a setting**, and the corner gesture says what it
  makes of each contact. A hold can be lost three ways and all three used to
  be the same silence: the finger drifts out of the corner, the finger lifts
  early, or it never landed in the corner at all. Each says so now, with how
  far the hold had got:

  ```
  17:06:37.4 [salon] corner: landed at (0,0), inside 179x112 -- holding for 3s
  17:06:38.6 [salon] corner: left at (0,199) after 1.20s of 3s -- the hold is
                     lost and does not restart until the finger lifts
  ```

  Noisy by design and off by default -- turn it on only while diagnosing.

## 3.3.2

- **Coming home now costs what you set it to, and no more.** With
  `home_hold: 3` a panel measured 3.0 s of hold, 0.8 s of opening and 0.0 s
  to the first picture -- and that last figure is what settles it: the
  picture was ready the moment the wait ended, so most of the 0.8 s was
  allowance for a page that had already arrived. The launcher is served from
  localhost by the add-on itself, so its settle is **300 ms** now. A panel
  showing Home Assistant keeps its three seconds, and an ordinary site keeps
  its 800 ms; they paint in stages and this one does not.
- One function decides that wait and the startup line reads from it, so what
  the log says and what the sender does cannot drift apart.

## 3.3.1

- **Coming home is now timed out loud, in its three separate pieces.**
  Reported as more than seven seconds with `home_hold: 3`, and three rounds
  of this were spent guessing because nothing in the log could say where the
  time went. The gesture side is accurate -- measured, a 3.0 s hold fires at
  3.02 s -- so the rest is elsewhere, and now it says so:

  ```
  Home: corner 14% (179x112 of the page), hold 3s, settle 800ms
  Home: back to http://127.0.0.1:8099/ -- held 3.1s, opened in 0.9s
  Home: first picture 0.3s after the page opened
  ```

  The hold is the gesture, the open is the navigation plus the settle, and
  the picture is the panel. A single total tells you none of the three.
- The startup line also names the corner and the hold **actually in force**,
  so a setting that never reached the sender stops looking like a setting
  that reached it and did something unexpected.

## 3.3.0

- **The corner that brings a panel home is now yours to size and to time.**
  `home_corner` is how big it is, as a percentage of each axis (14 by
  default, so a 179x112 corner on a 1280x800 page); `home_hold` is how long a
  finger must stay in it (1.0 s by default). Both are per panel. The mark
  drawn in the corner follows `home_corner`, so what is pressed and what is
  seen cannot drift apart, and short of the hold the tap still reaches the
  page -- the corner stays usable for whatever is under it.
- **And coming home stopped costing three seconds it had no use for.**
  Reported as three to five seconds to get back to the launcher. Only one of
  those was the hold. The rest was the settle written for Home Assistant,
  which paints in stages -- shell, then cards, then their data -- and which a
  panel on the launcher was paying too: with no token there was no dashboard
  address to compare against, so the page stayed *unknown* rather than
  *not Home Assistant*, and unknown takes the long wait. The add-on knows
  perfectly well when it has pointed a panel at its own launcher, and says so
  now. Measured: **3000 ms before, 800 ms after**, with a panel showing a real
  dashboard untouched at 3000.

## 3.2.1

- **A video wallpaper that will not play now says so.** Reported as an .mp4
  that never started. It is not the configuration: H.264 is patented and the
  browser this add-on downloads does not carry it -- measured on the shipped
  build, `canPlayType` for `avc1.42E01E` answers nothing while VP9, VP8 and
  AV1 all answer *probably*. A `<video>` that cannot decode its file paints
  **nothing**, which is the blank rectangle nobody can diagnose from a panel.
  The page now reports the failure to the add-on and the log names the cause
  and the two ways out, once rather than on every visit.
- The documentation says it too, with the one-line conversion to WebM.

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
