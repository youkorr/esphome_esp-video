"""The page a panel comes home to, built from the add-on's own settings.

A panel pointed at a page of links is a launcher, and the point of putting the
page here rather than asking for a URL is that there is then nothing else to
install and nothing else to keep running: the add-on already has the list, and
it already has a process that outlives every panel.

The shape is taken from Homepage (gethomepage.dev), which is what was asked
for, and its vocabulary is kept so that somebody who knows one knows the other:
a **theme** of dark or light, a **color** named after Tailwind's palettes, a
**background** picture with a blur and a dim over it, groups of links under
their own headers, and cards that carry an icon, a name and a description.

What is deliberately not taken from it is the density. Homepage is read at a
desk with a mouse; this is read across a room and pressed with a thumb, so the
cards are large, there is no hover state, and nothing is small enough to need
aiming at.

Everything is inline and only the wallpaper is ever fetched. The container has
no promise of reaching the internet, a font or an icon pack that fails to load
leaves holes where the labels should be, and a panel is exactly where nobody
can open the console to find out why. Icons are whatever character you put in
the field -- an emoji, a letter -- because a character always draws. A
wallpaper that will not load leaves the plain colour behind it, which is why it
is allowed to be fetched at all.
"""
import html
import http.server
import json
import mimetypes
import os
import threading

import logos

# Inside the add-on's own container, which is where the senders run too, so
# nothing of this is reachable from the network.
PORT = 8099
ADDRESS = f"http://127.0.0.1:{PORT}/"
# What somebody writes in a panel's url: to be sent here.
KEYWORD = "launcher"
# Where the wallpaper is served from when it is a file on disk rather than an
# address. One path, so the page can name it before the file has been read.
WALLPAPER_PATH = "/wallpaper"

# Homepage names its palettes after Tailwind's, so these do too. Only the
# middle shade is given: the surfaces, the borders and the text are mixed from
# it in the page itself, which is both shorter than twenty hex values apiece
# and impossible to get inconsistent. These are Tailwind's 500s to the eye
# rather than to the digit -- they are decoration, and nothing depends on the
# exact number.
PALETTES = {
    "slate": "#64748b",
    "gray": "#6b7280",
    "zinc": "#71717a",
    "neutral": "#737373",
    "stone": "#78716c",
    "red": "#ef4444",
    "amber": "#f59e0b",
    "yellow": "#eab308",
    "lime": "#84cc16",
    "green": "#22c55e",
    "emerald": "#10b981",
    "teal": "#14b8a6",
    "cyan": "#06b6d4",
    "sky": "#0ea5e9",
    "blue": "#3b82f6",
    "indigo": "#6366f1",
    "violet": "#8b5cf6",
    "purple": "#a855f7",
    "fuchsia": "#d946ef",
    "pink": "#ec4899",
    "rose": "#f43f5e",
}
DEFAULT_PALETTE = "slate"

# Homepage's own words for how much to blur what is behind the cards.
BLURS = {"off": "0px", "sm": "4px", "md": "10px", "xl": "24px"}

# The icons somebody can ask for by name, because hunting for an emoji in a
# form field is not a thing anybody enjoys and a panel is filled in once. The
# names are French, which is what the person filling in this form writes, with
# the English word beside them where it is the one that comes to mind.
#
# Names are a convenience and not a restriction: anything not in here is drawn
# as the characters themselves, so an emoji pasted straight into the field
# works exactly as it did before this list existed.
#
# Every glyph was checked against U+FFFF in the browser the add-on ships --
# a character the font cannot draw measures exactly as wide as one that has no
# drawing by definition, which is how the erase key on the keyboard was checked
# before it shipped. 115 glyphs, none undrawn.
# The icons somebody can ask for by name, because hunting for an emoji in a
# form field is not a thing anybody enjoys and a panel is filled in once.
#
# Every icon carries BOTH names, French and English, because a household does
# not have one language and neither does the person filling this in at eight in
# the evening. One glyph per line with all the words that should reach it, so
# adding a language is adding words to a line rather than a second dictionary
# to keep in step -- which is how the first version, with English on a
# handful of entries and not the rest, went wrong.
#
# Names are a convenience and never a restriction: anything not here is drawn
# as the characters themselves, so an emoji pasted straight into the field
# works exactly as it did before the list existed.
#
# Every glyph was checked against U+FFFF in the browser the add-on ships -- a
# character the font cannot draw measures exactly as wide as one that has no
# drawing by definition, which is how the keyboard's erase key was checked
# before it shipped.
ICON_NAMES = (
    # the house and its rooms
    ("\U0001F3E0", "maison home house"),
    ("\U0001F6CB", "salon canape living-room sofa lounge"),
    ("\U0001F373", "cuisine kitchen cooking"),
    ("\U0001F6CF", "chambre lit bedroom bed"),
    ("\U0001F6C1", "salle-de-bain bathroom bath"),
    ("\U0001F6BF", "douche arrosage shower watering"),
    ("\U0001F6BD", "toilettes toilet wc"),
    ("\U0001F5A5", "bureau ordinateur-fixe desk office proxmox"),
    ("\U0001F697", "garage voiture car garage"),
    ("\U0001F333", "jardin garden tree exterieur outside"),
    ("\U0001FAB4", "plante terrasse plant patio balcony"),
    ("\U0001F377", "cave wine cellar"),
    ("\U0001F4E6", "grenier colis attic parcel package delivery"),
    ("\U0001F6AA", "porte entree door entrance hall"),
    ("\U0001FA9F", "fenetre window volet shutter blind"),
    ("\U0001FA9C", "escalier stairs ladder etage floor"),
    ("\U0001F6E4", "couloir corridor hallway route"),
    ("\U0001F3E2", "immeuble building appartement apartment"),
    # light and power
    ("\U0001F4A1", "lumiere ampoule light bulb lamp lighting"),
    ("\U0001FA94", "lampe lampadaire desk-lamp"),
    ("\U0001F50C", "prise plug socket outlet"),
    ("⚡", "energie electricite power electricity energy"),
    ("\U0001F50B", "batterie battery"),
    ("☀", "soleil solaire sun solar sunny"),
    ("\U0001F4CA", "compteur statistiques meter statistics stats chart"),
    ("\U0001F4A8", "vent eolienne wind air"),
    # climate
    ("\U0001F525", "chauffage feu heating fire heat flame"),
    ("\U0001F321", "temperature radiateur thermostat thermometer"),
    ("❄", "climatisation neige cold snow air-conditioning freezer"),
    ("\U0001F32C", "ventilateur fan breeze"),
    ("\U0001F4A7", "humidite eau humidity water moisture"),
    ("⛅", "meteo weather forecast"),
    ("\U0001F327", "pluie rain"),
    ("☁", "nuage cloud"),
    # keeping the place safe
    ("\U0001F6A8", "alarme fumee alarm siren smoke emergency"),
    ("\U0001F512", "serrure verrou lock locked security"),
    ("\U0001F511", "cle key keys"),
    ("\U0001F4F7", "camera photo picture"),
    ("\U0001F4F9", "camescope frigate cctv video-camera videosurveillance"),
    ("\U0001F514", "sonnette notification doorbell bell alert"),
    ("\U0001F6B6", "mouvement presence motion presence-detection"),
    ("\U0001F9EF", "gaz extincteur gas extinguisher"),
    ("\U0001F441", "surveillance oeil eye"),
    ("\U0001F6E1", "bouclier adguard pihole shield protection filtrage"),
    # media
    ("\U0001F3AC", "jellyfin plex kodi film cinema movies movie media"),
    ("▶", "youtube video lecture play watch"),
    ("\U0001F3A5", "netflix streaming projector"),
    ("\U0001F4FA", "television tv televiseur screen prime-video primevideo "
     "prime disneyplus disney canalplus molotov"),
    ("\U0001F3B5", "musique spotify music song audio"),
    ("\U0001F4FB", "radio tuner"),
    ("\U0001F399", "podcast micro-studio recording"),
    ("\U0001F5BC", "photos immich gallery pictures album"),
    ("\U0001F4D6", "livre book reading library calibre"),
    ("\U0001F3AE", "jeu jeux game games gaming console"),
    ("\U0001F3A7", "casque headphones"),
    ("\U0001F50A", "haut-parleur enceinte speaker volume sound"),
    ("\U0001F3A4", "micro microphone assistant voice"),
    # machines and services
    ("\U0001F433", "docker portainer container containers whale"),
    ("\U0001F5A7", "serveur server cluster machines noeuds nodes"),
    ("\U0001F4BE", "nas synology disque sauvegarde backup storage disk"),
    ("\U0001F4E1", "routeur antenne router antenna satellite"),
    ("\U0001F310", "reseau internet network web site"),
    ("\U0001F4F6", "wifi signal reseau-sans-fil"),
    ("\U0001F510", "vpn tunnel wireguard tailscale secure"),
    ("\U0001F9F1", "pare-feu firewall mur wall opnsense pfsense"),
    ("⌨", "terminal ssh shell clavier keyboard invite"),
    ("\U0001F4BB", "code ordinateur computer laptop editeur editor vscode"),
    ("\U0001F500", "git synchronisation sync flux"),
    ("\U0001F419", "github depot repository"),
    ("\U0001F5C3", "base-de-donnees database sql archives"),
    ("☁", "nuage-fichiers nextcloud owncloud cloud drive"),
    ("⬇", "telechargement torrent download downloads"),
    ("\U0001F4C8", "grafana supervision uptime monitoring graph metrics courbes"),
    ("\U0001F4E8", "mqtt message-broker courrier-entrant"),
    ("\U0001F41D", "zigbee ruche z2m hive"),
    ("\U0001F4DF", "esphome appareils devices esp"),
    ("\U0001F4C4", "paperless document documents papier paper scan"),
    ("\U0001F5DD", "vaultwarden bitwarden mots-de-passe passwords vault"),
    ("\U0001F5A8", "imprimante printer impression printing"),
    ("\U0001F4C7", "scanner numerisation contacts"),
    ("\U0001F4F1", "tablette telephone-mobile phone mobile tablet"),
    ("\U0001F4DE", "telephone landline call"),
    # everyday life
    ("\U0001F4C5", "agenda calendrier calendar schedule dates"),
    ("\U0001F551", "horloge heure clock time"),
    ("⏱", "minuteur chronometre timer stopwatch"),
    ("\U0001F6D2", "courses caddie shopping groceries cart"),
    ("\U0001F4CB", "liste listes list notes checklist"),
    ("✅", "taches todo tasks done"),
    ("\U0001F5D1", "poubelle dechets bin trash waste rubbish"),
    ("\U0001F9FA", "lessive linge laundry washing"),
    ("\U0001F9F9", "aspirateur menage vacuum cleaning broom"),
    ("\U0001F916", "robot aspirateur-robot bot automation"),
    ("\U0001F6B2", "velo bike bicycle cycling"),
    ("\U0001F686", "train rail metro"),
    ("✈", "avion plane flight airport vol"),
    ("\U0001F68C", "bus autobus transport"),
    ("✉", "courrier mail email lettre inbox"),
    ("\U0001F4AC", "message messages chat discussion"),
    ("\U0001F4B6", "argent depenses money budget expenses cash"),
    ("\U0001F3E6", "banque bank comptes accounts"),
    ("⚕", "sante health medical medecin doctor"),
    ("\U0001F3C3", "sport fitness course running exercise"),
    ("\U0001F415", "chien dog animaux pets"),
    ("\U0001F408", "chat-animal cat chaton kitten"),
    ("\U0001F3CA", "piscine pool swimming spa"),
    ("\U0001F356", "barbecue viande bbq grill meat"),
    ("\U0001F527", "outils bricolage tools maintenance repair"),
    ("⚙", "reglages parametres settings configuration setup"),
    ("☕", "cafe coffee machine-a-cafe kettle"),
    ("\U0001F37D", "repas cuisine-table meal dinner restaurant"),
    ("\U0001F9F8", "enfants jouets kids children toys"),
    ("\U0001F393", "ecole school study college"),
    ("\U0001F4BC", "travail bureau-pro work job briefcase"),
    ("\U0001F334", "vacances holiday vacation beach plage"),
    ("⭐", "etoile favori star favourite favorite bookmark"),
    ("❤", "coeur heart favoris loved"),
    ("ℹ", "info information aide help about"),
)

# name -> glyph, flattened once at import. A name written twice is a mistake
# worth failing on rather than resolving silently to whichever line came last:
# the two entries would disagree and only one of them would ever be reachable.
ICONS = {}
for _glyph, _words in ICON_NAMES:
    for _word in _words.split():
        if _word in ICONS and ICONS[_word] != _glyph:
            raise ValueError(f"the icon name {_word!r} is used twice")
        ICONS[_word] = _glyph

def _readable(hex_colour, dark):
    """The brand colour, unless it would disappear against the panel.

    GitHub is very nearly black and Sonos is black outright; on a dark tile
    they are a hole rather than a logo, and the same is true of a white mark on
    a light theme. Relative luminance decides it, and the theme's own ink is
    what they fall back to -- a recognisable shape in the wrong colour beats a
    correct colour nobody can see.
    """
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if dark and luminance < 0.22:
        return "#e8ecf4"
    if not dark and luminance > 0.82:
        return "#161b26"
    return hex_colour


def logo_svg(value, dark):
    """The inline drawing for a service name, or None if there is not one.

    Inline because nothing here is fetched, and as a path rather than an image
    so it takes the colour it is given -- see _readable above.
    """
    entry = logos.LOGOS.get(str(value or "").strip().lower())
    if entry is None:
        return None
    colour, path = entry
    return (f'<svg class="logo" viewBox="0 0 24 24" aria-hidden="true" '
            f'fill="{_readable(colour, dark)}"><path d="{path}"/></svg>')


def icon_for(value):
    """The glyph for what somebody typed: a name from the list, or the text.

    The variation selector matters more than it looks. Half of these are
    characters that predate emoji -- an arrow, a snowflake, a cog -- and a
    browser draws those as TEXT unless it is asked otherwise: thin, flat and
    the colour of the label beside them, next to a row of full-colour emoji.
    U+FE0F is what asks. Only for the old block: anything from U+1F000 up is
    an emoji already and adding it there would be noise.
    """
    text = str(value or "").strip()
    if not text:
        # The dot a tile gets when nobody chose anything, and it is a plain
        # bullet on purpose -- asking for its emoji form gives a different,
        # heavier mark than the quiet placeholder this is meant to be.
        return "\N{BULLET}"
    glyph = ICONS.get(text.lower(), text)
    if len(glyph) == 1 and ord(glyph) < 0x1F000:
        glyph += "\uFE0F"
    return glyph


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
 :root {
   color-scheme: %(scheme)s;
   --accent: %(accent)s;
   /* Every other colour is mixed from the accent and the theme's own end of
      the scale, so a palette is one value to set and cannot fall out of step
      with itself. color-mix has been in Chromium since 111 and the add-on
      already refuses to be quiet about a browser older than 114. */
   --ground: %(ground_fallback)s;
   --ground: color-mix(in srgb, var(--accent) 8%%, %(ground_end)s);
   --card: %(card_fallback)s;
   --card: color-mix(in srgb, var(--accent) 14%%, %(card_end)s);
   --edge: color-mix(in srgb, var(--accent) 30%%, %(card_end)s);
   --ink: %(ink)s;
   --faint: color-mix(in srgb, var(--ink) 55%%, var(--ground));
 }
 * { box-sizing: border-box; }
 html, body { margin: 0; min-height: 100%%; }
 body {
   background: var(--ground); color: var(--ink);
   font: 500 16px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif;
   display: flex; flex-direction: column; min-height: 100vh;
 }
 /* The picture is a layer of its own rather than the body's background,
    because a blur belongs to it alone: put on the body it would take the
    text with it, and the dim would have to be fought back out of the cards. */
 .wall {
   position: fixed; inset: 0; z-index: -1;
   background: var(--ground) center/cover no-repeat;
   background-image: %(wall)s;
   filter: blur(%(blur)s) brightness(%(brightness)s);
   transform: scale(1.06);   /* so a blur does not show the page's edge */
 }
 header { padding: 4vh 5vw 1vh; }
 h1 { margin: 0; font-size: clamp(22px, 4.5vw, 40px); font-weight: 650;
      letter-spacing: -0.01em; }
 header p { margin: .4em 0 0; color: var(--faint);
            font-size: clamp(13px, 2.2vw, 18px); }
 main { flex: 1; padding: 1vh 5vw 5vh; }
 section { margin-top: 3vh; }
 /* Homepage's "underlined" header, which is the one that still reads as a
    heading when there is a photograph behind it. */
 h2 {
   margin: 0 0 1.6vh; padding-bottom: .5em;
   font-size: clamp(15px, 2.4vw, 21px); font-weight: 600;
   letter-spacing: .08em; text-transform: uppercase; color: var(--faint);
   border-bottom: 2px solid var(--edge);
 }
 .group { display: grid; gap: 2.4vmin; grid-template-columns: %(columns)s; }
 a.tile {
   display: flex; align-items: center; gap: 3vmin;
   min-height: 13vh; padding: 2.6vmin 3.4vmin; border-radius: 3vmin;
   background: %(tile_bg)s; border: 1px solid var(--edge);
   color: inherit; text-decoration: none;
   %(tile_blur)s
   /* A finger has no hover, and a tap that lights nothing looks ignored. */
   -webkit-tap-highlight-color: transparent;
 }
 a.tile:active { border-color: var(--accent); transform: scale(.985); }
 .icon {
   flex: none; width: clamp(44px, 9vw, 74px); height: clamp(44px, 9vw, 74px);
   display: grid; place-items: center; border-radius: 28%%;
   background: color-mix(in srgb, var(--accent) 28%%, transparent);
   font-size: clamp(24px, 5vw, 40px); line-height: 1;
   /* The field asks for a character and somebody will type a word into it,
      because nothing stops them. Left alone that word runs straight across
      the name beside it. Clipped, and set smaller below when it is long, so
      the worst case is a shortened label rather than two overlapping ones. */
   overflow: hidden;
 }
 .icon.long { font-size: clamp(12px, 2vw, 17px); font-weight: 700;
              letter-spacing: -.02em; }
 /* A logo is a shape rather than a character, so it is sized as a fraction of
    the square it sits in rather than by a font size. */
 .icon .logo { width: 58%%; height: 58%%; display: block; }
 /* Blocks, not spans. They are written as spans because an <a> may not
    contain a <div>, and a span left inline puts the description on the same
    line as the name with nothing between them. */
 .text { display: block; min-width: 0; }
 .name { display: block; font-size: clamp(16px, 2.7vw, 23px); font-weight: 600;
         overflow-wrap: anywhere; }
 .desc { display: block; margin-top: .25em; color: var(--faint);
         font-size: clamp(12px, 2vw, 17px); overflow-wrap: anywhere; }
 .empty { color: var(--faint); font-size: clamp(14px, 2.4vw, 20px); }
 /* The clock, the date and the weather, on one line above the links.
    Deliberately without seconds: a digit that changes every second is a
    rectangle on the wire every second for as long as the panel is awake,
    which is the same reason nothing here animates. On the minute it is one
    small rectangle a minute, and an asleep panel sends nothing at all. */
 .now { display: flex; align-items: baseline; gap: .6em 1.2em;
        flex-wrap: wrap; margin: 0 0 .6em; }
 .time { font-size: clamp(34px, 8vw, 68px); font-weight: 300;
         letter-spacing: -0.02em; line-height: 1; font-variant-numeric: tabular-nums; }
 .date { color: var(--faint); font-size: clamp(14px, 2.4vw, 22px);
         text-transform: capitalize; }
 .wx { margin-left: auto; display: flex; align-items: center; gap: .35em;
       font-size: clamp(16px, 3vw, 26px); }
 .wx .sky { font-size: 1.5em; line-height: 1; }
 .wx .out { color: var(--faint); font-size: .7em; }
 /* Nothing to show is nothing drawn, rather than an empty box where a
    temperature should be. */
 .wx:empty, .now:empty { display: none; }
%(css)s</style></head>
<body>
<div class="wall"></div>
<header>%(now)s<h1>%(title)s</h1>%(subtitle)s</header>
<main>%(groups)s</main>
%(clockjs)s</body></html>
"""

# The clock, written to tick on the MINUTE rather than on a timer that drifts.
# Formatted by the browser, so it follows the panel's `locale:` -- a French
# household gets "lundi 2 septembre" without this page knowing any French.
CLOCK_JS = """<script>
(() => {
  const t = document.getElementById('t'), d = document.getElementById('d');
  if (!t) return;
  const draw = () => {
    const now = new Date();
    try {
      t.textContent = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
      d.textContent = now.toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'});
    } catch (e) { /* a page that will not format is a page with no clock */ }
    // To the next minute, not every 60000 ms from now: a timer that starts
    // half a minute late stays half a minute late for ever, and the minute
    // would turn over in the middle of nothing.
    setTimeout(draw, 60000 - (now.getSeconds() * 1000 + now.getMilliseconds()) + 50);
  };
  draw();
})();
</script>
"""

# The weather, asked of the ADD-ON rather than of the internet -- run.py has
# the token and the address of Home Assistant, and the page has neither. So
# this fetch never leaves the machine, and a failure leaves the last reading
# rather than a hole.
WEATHER_JS = """<script>
(() => {
  const sky = document.getElementById('sky'), temp = document.getElementById('temp');
  if (!sky) return;
  const draw = async () => {
    try {
      const r = await fetch('%(path)s', {cache: 'no-store'});
      if (r.ok) {
        const w = await r.json();
        if (w && w.icon) { sky.textContent = w.icon; temp.textContent = w.text || ''; }
      }
    } catch (e) { /* keep what is on the page */ }
    setTimeout(draw, 600000);
  };
  setTimeout(draw, 600000);
})();
</script>
"""

# Home Assistant's own weather states, which are a fixed list.
SKY = {
    "clear-night": "\U0001F319", "cloudy": "\u2601\uFE0F",
    "fog": "\U0001F32B\uFE0F", "hail": "\U0001F328\uFE0F",
    "lightning": "\U0001F329\uFE0F", "lightning-rainy": "\u26C8\uFE0F",
    "partlycloudy": "\u26C5", "pouring": "\U0001F327\uFE0F",
    "rainy": "\U0001F326\uFE0F", "snowy": "\u2744\uFE0F",
    "snowy-rainy": "\U0001F328\uFE0F", "sunny": "\u2600\uFE0F",
    "windy": "\U0001F4A8", "windy-variant": "\U0001F4A8",
    "exceptional": "\u26A0\uFE0F",
}


def weather_block(state):
    """What Home Assistant said, as the two spans the page updates.

    Given nothing, the spans are still there and empty -- `.wx:empty` hides
    the box, and the script fills it when the first reading arrives.
    """
    if not state:
        return "", ""
    icon = SKY.get(str(state.get("condition") or "").lower(), "")
    text = str(state.get("text") or "")
    return html.escape(icon), html.escape(text)

TILE = ('<a class="tile" href="%(url)s">'
        '<span class="icon%(icon_long)s">%(icon)s</span>'
        '<span class="text"><span class="name">%(name)s</span>%(desc)s</span>'
        '</a>')

EMPTY = ('<p class="empty">No links yet. Add them under <b>links</b> in this '
         "add-on's configuration, then restart it.</p>")


def _wallpaper(background):
    """What the page should name as its picture, and what to serve for it.

    Returns (css, path_to_serve). An address is used as it stands -- the panel
    fetches it itself, and Home Assistant's own /local is the obvious place to
    keep one. A path is served from here instead, because the page is on
    127.0.0.1 and a file:// URL in a page is refused by every browser there is.
    """
    background = str(background or "").strip()
    if not background:
        return "none", None
    if background.startswith(("http://", "https://", "data:")):
        return f'url("{html.escape(background, quote=True)}")', None
    if os.path.isfile(background):
        return f'url("{WALLPAPER_PATH}")', background
    # Said once, at startup, rather than left as a blank wall nobody can
    # explain from the panel.
    print(f"Launcher: no wallpaper at {background} -- the plain colour is "
          f"being used. Put the file under /config, /share or /media, or give "
          f"an address the panel can reach.", flush=True)
    return "none", None


WEATHER_PATH = "/weather.json"

# Named rather than a number of pixels because "large" is a decision and "72px"
# is an experiment. The words are the plain ones rather than Homepage's own
# xs/sm/md/xl/2xl scale: what is worth copying from that dashboard is the shape
# -- a fixed list per thing and no stylesheet field anywhere -- and somebody
# filling in a form at eight in the evening reads "large" faster than "2xl".
#
# Each step is a clamp, so it adapts between a 1024x600 panel and a 800x1280
# one; the middle value is the one that governs on a panel.
CLOCK_SIZES = {
    "small": "clamp(22px, 5vw, 40px)",
    "medium": "clamp(34px, 8vw, 68px)",
    "large": "clamp(44px, 11vw, 96px)",
    "huge": "clamp(56px, 15vw, 130px)",
}
DATE_SIZES = {
    "small": "clamp(11px, 1.8vw, 16px)",
    "medium": "clamp(14px, 2.4vw, 22px)",
    "large": "clamp(17px, 3vw, 28px)",
    "huge": "clamp(20px, 3.6vw, 34px)",
}
WEATHER_SIZES = {
    "small": "clamp(13px, 2.2vw, 19px)",
    "medium": "clamp(16px, 3vw, 26px)",
    "large": "clamp(21px, 4vw, 34px)",
    "huge": "clamp(26px, 5vw, 44px)",
}
DEFAULT_SIZE = "medium"

# "theme" is the palette name for no palette at all: the clock takes the text
# colour of whichever theme is on. A word rather than an empty field, because
# a dropdown whose first entry is blank reads as a setting nobody finished.
FOLLOW_THEME = "theme"


def _one_size(rule, table, want):
    """One font-size rule, or nothing when the size is the default."""
    want = str(want or DEFAULT_SIZE).lower()
    if want not in table or want == DEFAULT_SIZE:
        return []
    return [f" {rule} {{ font-size: {table[want]}; }}"]


def _one_colour(rule, want):
    """One colour rule, or nothing when it follows the theme."""
    tint = PALETTES.get(str(want or "").lower())
    return [f" {rule} {{ color: {tint}; }}"] if tint else []


def _bar(clock_size, clock_color, date_size, date_color, weather_size, align):
    """The rules the named settings come to, or nothing when all are default.

    Every one of them is a list in the add-on's form, so what arrives here is
    a word from a fixed set or nothing -- there is no stylesheet field behind
    this any more, and nothing a household types reaches the page as CSS.
    """
    out = []
    out += _one_size(".time", CLOCK_SIZES, clock_size)
    out += _one_size(".date", DATE_SIZES, date_size)
    out += _one_size(".wx", WEATHER_SIZES, weather_size)
    out += _one_colour(".time", clock_color)
    # The date is faint by default, so a palette given for it has to beat that
    # rule as well as set a colour -- both live on .date, so the later one in
    # the sheet wins and this is it.
    out += _one_colour(".date", date_color)
    where = str(align or "left").lower()
    if where in ("center", "centre", "right"):
        # The weather is pushed right by a margin, which would fight any
        # alignment of the bar as a whole, so it goes when the bar is placed.
        out.append(" .wx { margin-left: 0; }")
        out.append(f" .now {{ justify-content: "
                   f"{'center' if where.startswith('cent') else 'flex-end'}; }}")
    return ("\n".join(out) + "\n") if out else ""


def render(links, title="Panel", subtitle="", theme="dark",
           color=DEFAULT_PALETTE, background="", blur="off", dim=40,
           columns=0, clock=True, weather=None,
           clock_size=DEFAULT_SIZE, clock_color=FOLLOW_THEME,
           date_size=DEFAULT_SIZE, date_color=FOLLOW_THEME,
           weather_size=DEFAULT_SIZE, align="left"):
    """The page, as one string.

    Every value is escaped. These come from a configuration file a person
    edits, so a name containing an ampersand or a stray angle bracket is a
    typo rather than an attack -- and a typo that silently breaks the page a
    panel comes home to is still the worst kind of bug to chase.
    """
    dark = str(theme).lower() != "light"
    accent = PALETTES.get(str(color).lower(), PALETTES[DEFAULT_PALETTE])
    wall_css, _ = _wallpaper(background)
    has_wall = wall_css != "none"

    # Grouped in the order the groups first appear, so the list in the form is
    # the order on the panel and nobody has to think about sorting.
    groups = {}
    for link in links:
        if not link.get("url"):
            continue
        groups.setdefault(str(link.get("group") or "").strip(), []).append(link)

    body = []
    for name, entries in groups.items():
        tiles = "".join(
            TILE % {
                "url": html.escape(str(entry.get("url", "")), quote=True),
                "icon": (logo_svg(entry.get("icon"), dark)
                         or html.escape(icon_for(entry.get("icon")))),
                # Two characters still read at the full size -- "HA" is a
                # perfectly good icon. Beyond that it is a word, and a word
                # has to be set smaller to stay inside its square. Measured on
                # what will be drawn, not on what was typed: a name from the
                # list is one glyph however long the name is.
                "icon_long": (
                    ""
                    if logo_svg(entry.get("icon"), dark) is not None
                    else " long"
                    if len(icon_for(entry.get("icon")).rstrip("\uFE0F")) > 2
                    else ""
                ),
                "name": html.escape(str(entry.get("name") or entry["url"])),
                "desc": (
                    '<span class="desc">%s</span>'
                    % html.escape(str(entry["description"]))
                    if str(entry.get("description") or "").strip() else ""
                ),
            }
            for entry in entries
        )
        heading = f"<h2>{html.escape(name)}</h2>" if name else ""
        body.append(f'<section>{heading}<div class="group">{tiles}</div></section>')

    # Nought means let the panel decide, which is what a launcher shown on
    # three different shapes of screen wants: a fixed count either wastes a
    # wide one or crushes a narrow one.
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        columns = 0
    grid = (f"repeat({columns}, 1fr)" if columns > 0
            else "repeat(auto-fit, minmax(min(42vw, 300px), 1fr))")

    try:
        dim = max(0, min(100, int(dim)))
    except (TypeError, ValueError):
        dim = 40

    # The clock, the date and the weather, above the links. A panel that wants
    # none of them draws none of them, and the header is what it always was.
    sky, temp = weather_block(weather)
    now = ""
    if clock or weather is not None:
        now = '<div class="now">'
        if clock:
            now += '<span class="time" id="t"></span><span class="date" id="d"></span>'
        if weather is not None:
            now += (f'<span class="wx"><span class="sky" id="sky">{sky}</span>'
                    f'<span class="out" id="temp">{temp}</span></span>')
        now += "</div>"
    scripts = (CLOCK_JS if clock else "") + (
        WEATHER_JS % {"path": WEATHER_PATH} if weather is not None else "")

    # The bar's own rules, after the sheet above and before nothing: there is
    # no stylesheet field any more. It was offered first, on the argument that
    # one general mechanism beats a setting per thing -- and that argument is
    # the maintainer's convenience, not the household's. Homepage names the
    # size of each widget on a fixed scale and has no CSS field at all, which
    # is the shape this follows now.
    sheet = _bar(clock_size, clock_color, date_size, date_color,
                 weather_size, align)

    return PAGE % {
        "css": sheet,
        "now": now,
        "clockjs": scripts,
        "title": html.escape(title),
        "subtitle": (f"<p>{html.escape(subtitle)}</p>"
                     if str(subtitle).strip() else ""),
        "groups": "".join(body) or EMPTY,
        "columns": grid,
        "scheme": "dark" if dark else "light",
        "accent": accent,
        # The end of the scale everything is mixed towards, and a plain value
        # first for anything that cannot mix.
        "ground_end": "#0b0e14" if dark else "#f4f6fa",
        "ground_fallback": "#0b0e14" if dark else "#f4f6fa",
        "card_end": "#161b26" if dark else "#ffffff",
        "card_fallback": "#161b26" if dark else "#ffffff",
        "ink": "#e8ecf4" if dark else "#161b26",
        "wall": wall_css,
        # Both belong to the picture and only to the picture. Left applied
        # with no wallpaper set, the dim darkened the plain colour instead --
        # a light theme came out mud grey, which is nobody's idea of light.
        "blur": BLURS.get(str(blur).lower(), "0px") if has_wall else "0px",
        # A photograph is nearly always too bright to read white text over, so
        # the dim is what makes a wallpaper usable rather than a decoration
        # somebody turns off again.
        "brightness": f"{max(0, 100 - dim)}%" if has_wall else "100%",
        # Cards float over a picture and sit solid on a plain colour. This is
        # Homepage's cardBlur without an option for it: over a photograph it
        # is what makes the text readable, and over a flat colour it does
        # nothing at all, so there is nothing to decide.
        "tile_bg": ("color-mix(in srgb, var(--card) 72%, transparent)"
                    if has_wall else "var(--card)"),
        "tile_blur": ("backdrop-filter: blur(12px) saturate(140%); "
                      "-webkit-backdrop-filter: blur(12px) saturate(140%);"
                      if has_wall else ""),
    }


def start(links, title="Panel", subtitle="", theme="dark",
          color=DEFAULT_PALETTE, background="", blur="off", dim=40,
          columns=0, clock=True, weather=None,
          clock_size=DEFAULT_SIZE, clock_color=FOLLOW_THEME,
          date_size=DEFAULT_SIZE, date_color=FOLLOW_THEME,
          weather_size=DEFAULT_SIZE, align="left"):
    """Serve the page for as long as the add-on runs. Returns its address.

    One server for every panel: they all come home to the same list, and a
    second copy of it would only be a second thing to keep in step.
    """
    # `weather` is a callable returning the latest reading, or None. Called
    # rather than passed by value because the page outlives any one reading:
    # the add-on refreshes it in the background and the page asks for it every
    # ten minutes.
    first = weather() if weather is not None else None
    body = render(links, title, subtitle, theme, color, background, blur,
                  dim, columns, clock,
                  first if weather is not None else None,
                  clock_size, clock_color, date_size, date_color,
                  weather_size, align).encode()
    _, wallpaper = _wallpaper(background)
    picture, kind = None, "application/octet-stream"
    if wallpaper:
        try:
            with open(wallpaper, "rb") as handle:
                picture = handle.read()
            kind = mimetypes.guess_type(wallpaper)[0] or kind
            print(f"Launcher: wallpaper {wallpaper} "
                  f"({len(picture) // 1024} KiB)", flush=True)
        except OSError as err:
            # Read once at startup rather than per request: a panel coming
            # home should not wait on a disk, and a file that has gone away
            # should not turn into a broken page later on.
            print(f"Launcher: could not read {wallpaper} ({err})", flush=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # the add-on log is for panels, not for page requests

        def _reply(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            # Rebuilt on every restart, and a panel that came home to a stale
            # copy would show links that were edited away.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.split("?")[0] == WEATHER_PATH:
                # Whatever the add-on last read, as it stands. A reading that
                # has not arrived yet is an empty object rather than an error:
                # the page keeps what it has and asks again.
                state = weather() if weather is not None else None
                sky, temp = weather_block(state)
                self._reply(
                    json.dumps({"icon": sky, "text": temp}).encode(),
                    "application/json",
                )
                return
            if self.path.split("?")[0] == WALLPAPER_PATH:
                if picture is None:
                    self.send_error(404)
                    return
                # The one thing here worth caching: it does not change while
                # the add-on runs, and a panel coming home should not fetch a
                # megabyte again.
                self._reply(picture, kind)
                return
            self._reply(body, "text/html; charset=utf-8")

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as err:
        # An accessory must never cost the panels. Something else on the port,
        # or no permission to bind: say so and let every panel that wanted
        # this be told, rather than taking the add-on down with it.
        print(f"Launcher: could not listen on {PORT} ({err})", flush=True)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="launcher",
                     daemon=True).start()
    return ADDRESS
