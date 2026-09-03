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

    python ha_send.py --calibrate --host ip esp32P4 --port 5000 \
        --width 1024 --height 600 --rotate 180

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
| `token` | A long-lived access token, for a panel that shows a dashboard **directly** rather than reaching one through a link. Leave it empty otherwise -- the Home Assistant link carries its own |
| `width`, `height` | Must match the component's |
| `rotate` | Turns the picture, for a panel not mounted upright |
| `touch_rotate`, `touch_mirror_x`, `touch_mirror_y` | From `--calibrate` |
| `fps` | Upper bound on how often a change is acted on. 25 by default; the one setting that decides whether video looks like video. See below |
| `quality` | JPEG quality, 1..95 |
| `keyboard` | The on-screen keyboard's layout, or `off`. See below |
| `blank_after` | Seconds dark before a sleeping panel's page is let go of. See below |
| `keep_profile` | Keep the browser signed in between restarts. See below |
| `import_profile` | A browser profile signed in by hand elsewhere, to start this panel from. **Per panel only** -- it does not belong to a house. See below |
| `user_agent` | What the browser says it is. Empty is right for nearly everything -- it is here for YouTube's television interface. **Per panel or per link only**, because a panel told to say it is a television says it to Home Assistant too. See below |
| `locale` | The language pages are asked for -- `fr-FR`, `de-DE`, `en-GB`. Not cosmetic: without it the browser sends no `Accept-Language` at all and every site serves its own default |
| `stats` | Print what is being sent every five seconds |
| `show_media` | While a video plays, print its playhead and how many seconds are buffered ahead of it. Off by default -- turn it on to diagnose a video that stops |

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
launcher_theme: dark          # dark or light
launcher_background: http://homeassistant:8123/local/wall.jpg
launcher_background_blur: md  # off, sm, md, xl
launcher_background_dim: 40   # 0..100
launcher_background_motion: false   # let a GIF or an MP4 actually move
launcher_columns: 0           # 0 lets the panel decide
launcher_clock: true          # the time and the date above the links
launcher_weather: weather.forecast_home   # empty for none
launcher_clock_size: medium   # small, medium, large, huge
launcher_clock_color: theme   # a palette name, or theme for the theme's colour
launcher_date_size: medium    # the same four
launcher_date_color: theme    # the same palette names
launcher_weather_size: medium # the same four
launcher_align: left          # left, center, right
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
    width: 800
    height: 1280
```

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
the one screen where nobody can find out why a picture did not load. A logo
whose colour would disappear against the panel -- GitHub is nearly black, Sonos
is black -- is drawn in the theme's ink instead.

Prime Video is the exception on that list: it is not in the collection these
come from, so `prime-video` gives a television.

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
| 📹 | `camescope` `frigate` `cctv` `video-camera` `videosurveillance` | 💬 | `message` `messages` `chat` `discussion` |
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
| adguard | `adguard` `adguardhome` | paperlessngx | `paperless` `paperlessngx` `paperless-ngx` |
| audiobookshelf | `audiobookshelf` `abs` | pfsense | `pfsense` |
| bitwarden | `bitwarden` | philipshue | `hue` `philipshue` `philips-hue` |
| calibreweb | `calibre` `calibreweb` `calibre-web` | pihole | `pihole` `pi-hole` |
| docker | `docker` | plex | `plex` |
| duplicati | `duplicati` | portainer | `portainer` |
| eclipsemosquitto | `mosquitto` `broker` | proxmox | `proxmox` `pve` |
| emby | `emby` | qbittorrent | `qbittorrent` `qbit` |
| esphome | `esphome` | radarr | `radarr` |
| frigate | `frigate` | raspberrypi | `raspberrypi` `raspberry-pi` `pi` |
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

50 service logos, drawn from the add-on itself and never fetched.

### The clock, the date and the weather

Above the links, as on Homepage:

```yaml
launcher_clock: true
launcher_weather: weather.forecast_home
```

The date sits **under** the time and carries the year.

**They follow the panel's `locale`.** The browser formats them, so `fr-FR`
gives `19:00` and `mercredi 2 septembre 2026`, `de-DE` gives `Mittwoch, 2.
September 2026`, and this add-on needs to know no language at all.

**No seconds, on purpose.** A digit that changes every second is a rectangle
sent to the panel every second for as long as it is awake -- the same reason
nothing on this page animates. On the minute it is one small rectangle a
minute, and a sleeping panel sends nothing whatever.

The weather is read **by the add-on**, which already has your token and Home
Assistant's address, and served to the page from `127.0.0.1`. The page never
reaches Home Assistant and never carries the token -- putting one into the
storage of every site a panel visits is a leak this project has already had to
close once. It is refreshed every ten minutes; if it cannot be read, the
launcher simply shows no weather and says so once in the log.

**A panel in portrait is fine, and it was measured rather than assumed.** The
page is fluid: the tiles reflow, the bar wraps, and the weather stays at the
right-hand edge. With six links in three groups and the bar in place, tiles are
350x166 at 800x1280, 566x118 at 1280x800 and 454x107 at 1024x600, with nothing
running off the side at any of them -- the same figures as before the date
moved under the clock.

### The wallpaper, and the digital photograph frame

`launcher_background` takes any of four things:

| | |
|---|---|
| nothing | the plain colour |
| an address | the panel fetches it itself. Home Assistant serves `/config/www` at `/local`, so `http://homeassistant:8123/local/wall.jpg` is the easy one |
| a file | under `/config`, `/share` or `/media`, served by the add-on |
| **a folder** | under the same three -- a digital photograph frame |

A folder shows its first picture. Turn `launcher_slideshow` on and it cycles:

```yaml
launcher_background: /media/photos          # the folder
launcher_slideshow: true
launcher_slideshow_seconds: 30              # how long each picture is shown
launcher_slideshow_fade: 1                  # how long one fades into the next
launcher_slideshow_rescan: 60               # minutes between re-reading it
```

`launcher_slideshow_rescan` is what makes a photograph dropped into the folder
appear without restarting the add-on. Only pictures are used -- `.jpg`,
`.jpeg`, `.png`, `.webp`, `.gif`, `.avif`, `.bmp` -- so a stray text file in
the folder is ignored rather than drawn as a broken square.

**What it costs, because this is the one feature here that is never free.**
A panel sends only what changed, so a still page sends nothing at all. A
picture that changes is a **whole panel** on the wire: about 130 KiB at
800x1280 and quality 80. So

- `launcher_slideshow_fade: 0` is a hard cut and costs **one** whole panel;
- a **1 second** fade costs about `fps` of them -- twenty-five at the default;
- a **2 second** fade costs about fifty, which is six megabytes a picture.

`launcher_slideshow_seconds` is what averages that down. Thirty seconds with a
one-second fade is roughly 110 KiB/s; the same fade every five seconds is six
times that. If a panel starts stuttering while the pictures change, the fade
is the setting to lower, not the delay.

### A GIF or a video as the wallpaper

`launcher_background_motion` decides whether it is allowed to move, and it is
**off** by default. Off, an MP4 shows its first frame and a GIF is frozen on
its own first frame -- the picture is there, and it costs the panel nothing.

On, a video wallpaper is the panel's **entire bandwidth for as long as it is
awake**: every frame is a whole panel, for ever, behind whatever else is on
the screen. It works, it was measured working, and it is not something to
leave on by accident. That is why it is a switch of its own rather than
something a `.mp4` in the field turns on by itself.

### Changing how it looks

Each part of the bar has its own setting, and every one of them is a list you
pick from:

```yaml
launcher_clock_size: huge      # small, medium, large, huge
launcher_clock_color: sky      # a palette name, or theme
launcher_date_size: large      # the same four
launcher_date_color: slate     # the same palette names
launcher_weather_size: small   # the same four
launcher_align: center         # left, center, right
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

**And Netflix cannot be signed into either, for a different reason.** Its
sign-in page carries, in its own words at the bottom: *"Cette page est
protegee par Google reCAPTCHA pour nous assurer que vous n'etes pas un
robot."* Reported from a panel pointed at `https://www.netflix.com/fr/` --
which is the right address, nothing wrong with it -- the page answered *"Un
probleme est survenu. Veuillez reessayer dans quelques minutes"* with the
email field **still empty**. Nothing had been typed, so it is not the
password, not the keyboard and not the DRM: reCAPTCHA scores the browser
silently when the page loads, and a driven one scores badly.

That is Google's anti-robot check, the same wall as the Google sign-in and by
the same supplier, and defeating a CAPTCHA is not something this project will
do.

So there are **two** walls for Netflix, and the order matters:

1. **Widevine**, for playing. Look at the log line above before anything else
   -- with no Widevine, signing in would buy nothing at all.
2. **reCAPTCHA**, for signing in. Only `import_profile` gets past it, by not
   trying: the check is on the signing IN, and afterwards it is a cookie.

Even with both solved, a browser gets Widevine **L3**, which Netflix limits to
standard definition -- and full motion at a panel's own resolution is the
expensive case measured under **YouTube** above. The effort is large and the
result is poor. Jellyfin, which needs none of this, is the better answer for a
panel.

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

