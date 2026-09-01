# Changelog

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
