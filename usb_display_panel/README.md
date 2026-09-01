# Portall

![Portall](logo.png)

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
| `fps` | Upper bound on how often a change is acted on. 25 by default; the one setting that decides whether video looks like video. See below |
| `quality` | JPEG quality, 1..95 |
| `keyboard` | The on-screen keyboard's layout, or `off`. See below |
| `blank_after` | Seconds dark before a sleeping panel's page is let go of. See below |
| `keep_profile` | Keep the browser signed in between restarts. See below |
| `stats` | Print what is being sent every five seconds |

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
launcher_title: Maison
launcher_subtitle: ""
launcher_theme: dark          # dark or light
launcher_color: sky           # a Tailwind palette name, as Homepage uses
launcher_background: http://homeassistant:8123/local/wall.jpg
launcher_background_blur: md  # off, sm, md, xl
launcher_background_dim: 40   # 0..100
launcher_columns: 0           # 0 lets the panel decide
links:
  - name: Home Assistant
    url: http://homeassistant:8123/lovelace/0
    icon: "\U0001F3E0"
    group: Maison
    description: Salon, lumieres, volets
  - name: Jellyfin
    url: http://192.168.1.20:8096
    icon: "\U0001F3AC"
    group: Media
    description: Films et series
panels:
  - name: salon
    host: 192.168.1.11
    url: launcher
    width: 800
    height: 1280
```

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

