# Changelog

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
