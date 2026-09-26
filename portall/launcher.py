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
import urllib.request
from urllib.parse import parse_qs, urlsplit

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
# Where the page says a wallpaper would not play. A video that cannot be
# decoded paints NOTHING -- the blank rectangle this project forbids -- and
# the page has no other way to reach a log somebody reads.
REPORT_PATH = "/report"
# What the page asks to find out how many pictures the folder holds now.
SLIDES_PATH = "/slides.json"
# A panel with a launcher of its own gets a server of its own, on a port the
# system picks: the address is only ever handed to this add-on's own senders,
# so any free port will do, and asking for one cannot collide with whatever
# else the Home Assistant machine runs. The house's launcher keeps PORT.
ANY_PORT = 0

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
    # Not Tailwind palettes, and asked for by name: a clock somebody wants
    # plainly white on a photograph, or plainly black on a light theme.
    "white": "#ffffff",
    "black": "#000000",
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
    ("\U0001F4F9", "camescope frigate cctv video-camera videosurveillance "
     # Camera makers simple-icons does not carry -- checked against its 3460
     # marks, none of these is in it, so there is no public-domain tracing to
     # embed and a name is the honest answer. The same call the collection's
     # missing Prime Video already got: saying something is better than an
     # empty square.
     "hikvision dahua tapo annke amcrest foscam"),
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

# The tile a logo is drawn on, which is what it has to be legible against.
# Kept beside the rule rather than passed in: these are the same two values
# `card_fallback` uses, and two places to change one colour is one place too
# many.
# LOGO_ rather than TILE/INK: this file already has a TILE, three hundred
# lines further down and holding the tile's markup. Defined after this one it
# simply replaced it, and indexing a STRING with True then returned a single
# character -- every logo raised. Nothing about it is visible from reading the
# diff, and a module-level name diff cannot see it either, because the name is
# present both before and after.
LOGO_TILE = {True: "#161b26", False: "#ffffff"}
LOGO_INK = {True: "#e8ecf4", False: "#161b26"}
# WCAG's own figure for a non-text graphic that has to be made out. Below it a
# mark is a smudge the shape of a logo.
MIN_CONTRAST = 3.0


def _luminance(hex_colour):
    """Relative luminance, WCAG's definition -- which needs the gamma undone.

    The first version of this weighted the sRGB values as they stand. That is
    not a luminance and it is wrong in the direction that matters: a dark
    colour looks brighter than it is, so the marks most at risk of vanishing
    scored best.
    """
    out = []
    for i in (1, 3, 5):
        c = int(hex_colour[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def _readable(hex_colour, dark):
    """The brand colour, unless it would disappear against the tile.

    The question is CONTRAST against the tile, not how bright the colour is on
    its own, and the two part company exactly where this was reported: pure red
    is vivid and perfectly legible on a dark card, and it is not bright --
    YouTube's #FF0000 scored 0.213 against a threshold of 0.22 and was replaced
    by the near-white ink, so a panel in the dark theme drew the YouTube badge
    as a white rectangle. Measured against the real tile, that red is 4.31:1,
    which is comfortable. Netflix cleared the old rule by 0.002, which is luck
    rather than a design.

    The other half had never done anything at all. `luminance > 0.82` catches
    only a near-white mark and no brand in the collection is one, so on a light
    theme the rule replaced NOTHING -- while Spotify sat at 1.92:1, Plex at
    1.97 and Jellyfin at 2.86, all of them washed out on white.

    A recognisable shape in the wrong colour beats a correct colour nobody can
    see, which is why the fallback is the theme's ink either way.
    """
    tile = LOGO_TILE[bool(dark)]
    lighter = max(_luminance(hex_colour), _luminance(tile))
    darker = min(_luminance(hex_colour), _luminance(tile))
    if (lighter + 0.05) / (darker + 0.05) < MIN_CONTRAST:
        return LOGO_INK[bool(dark)]
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


def logo_img(value):
    """The carried bitmap for a brand with no drawing, or None.

    A data: URI rather than an address, for the reason every picture on this
    page follows: nothing is fetched, because a panel is the one screen where
    nobody can find out why an image did not load.

    Not recoloured, unlike the paths above -- these carry their own ground, so
    there is nothing to rescue them from.
    """
    entry = logos.PICTURES.get(str(value or "").strip().lower())
    if entry is None:
        return None
    mime, data, badge = entry
    return (f'<img class="logo{" badge" if badge else ""}" alt="" '
            f'aria-hidden="true" src="data:{mime};base64,{data}">')


def logo_markup(value, dark):
    """Whichever kind of logo this name has, drawn or carried, or None."""
    return logo_svg(value, dark) or logo_img(value)


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


def icon_kind(value, dark):
    """The class that sizes an icon: a picture, a character, or a word.

    A logo or an emoji is a PICTURE and fills the whole square, the size
    Reolink's badge always had -- beside it the others were reported as very
    small, 43 px of logo and 50 of emoji in the same 74 px square. Letters are
    not pictures: "HA" at that size is wider than the square and would be
    clipped, so letters and the quiet empty-field bullet keep the size and the
    tinted ground they had. Beyond two characters it is a word, set smaller
    still -- measured on what will be drawn, not on what was typed, so a name
    from the list is one glyph however long the name is.

    Not called "text": the page already has a .text class, the block holding
    the name and the description, and an icon wearing it became a block with
    its bullet in the top corner.
    """
    if logo_markup(value, dark) is not None:
        return " picture"
    glyph = icon_for(value).rstrip("\uFE0F")
    if len(glyph) > 2:
        return " long"
    # Everything below U+2100 is letters, digits, punctuation and the bullet;
    # the old symbols that became emoji (arrows, snowflake, cog...) sit above
    # it and are asked for in colour by icon_for().
    if all(ord(c) < 0x2100 for c in glyph):
        return " letters"
    return " picture"


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
   /* The ring around the tile a remote is on. Its own variable so that
      `focus_color` changes it in ONE place for both shapes. */
   --ring: var(--accent);
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
 /* Two layers rather than one, so a slideshow can fade the next picture in
    over the one showing. The pair is stacked and only their opacity moves;
    swapping a background-image on a single layer is a hard cut, and the
    fade is the whole of what was asked for. */
 .wall.b { opacity: 0; }
 .wall.fade { transition: opacity %(fade)ss linear; }
 /* A video fills its layer exactly as a picture does. object-fit is the
    video's own version of background-size: cover. */
 .wall > video {
   width: 100%%; height: 100%%; object-fit: cover; display: block;
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
 /* A tile is a CONTAINER, so what is inside it is laid out for the width it
    really got rather than for the panel's. Reported from a 1280x800 panel
    with two photographs: at `columns: 5` the names read "Jellyfi n" and
    "Reoli nk", and at 6 they ran one letter a line down the side of the
    tile. The icon sat BESIDE the name whatever the width, so a narrow tile
    left the name a sliver, and `overflow-wrap: anywhere` then cut words
    wherever it liked. Measured by tools/checktiles.py, which asks the
    browser whether every word of every name landed on one line.

    Being a container also means the tile's own content no longer sets how
    narrow its column may be, so no column count can push a tile off the
    side of the panel. The padding lives on .in for that reason: padding on
    the container itself cannot answer to the container's width. */
 a.tile {
   display: flex; align-items: center; container-type: inline-size;
   min-height: 13vh; border-radius: 3vmin;
   background: %(tile_bg)s; border: 1px solid var(--edge);
   color: inherit; text-decoration: none;
   %(tile_blur)s
   /* A finger has no hover, and a tap that lights nothing looks ignored. */
   -webkit-tap-highlight-color: transparent;
 }
 /* WHAT A PRESS LOOKS LIKE, and why it is a class and not only :active.
 
    Reported from a panel as the tiles having no press effect "comme un button
    lvgl". The `:active` rule was already here and was already being applied --
    and it lasted a MEDIAN OF 2.3 ms, measured by replaying a tap the way the
    sender does and timing mousedown to mouseup in the page. The sender holds a
    contact back until the finger LIFTS, so that a drag can become a wheel
    instead of a click, and then dispatches down and up together. A frame at
    --fps 25 is 40 ms, so the pressed state occupied 6%% of one frame interval:
    the panel receives JPEG rectangles, and a state shorter than a frame is one
    no frame can contain. It was not missing, it was unphotographable.
 
    So `.press` is held by PRESS_JS for long enough to be caught, and the
    effect is made readable across a room -- 1.5%% of scale is 5 px on a tile
    and nobody sees it. No animation: this appears on a press and goes, which
    is two rectangles, rather than pulsing, which is a rectangle for as long as
    the panel is awake. */
 a.tile:active, a.tile.press {
   border-color: var(--accent);
   transform: scale(.96);
   /* --edge is already the accent at 30%% over the card, against --card's 14%%,
      so a pressed tile is the accent stronger rather than a fourth colour to
      keep in step with the palette. */
   background: var(--edge);
 }
 /* Where a remote is pointing. A panel driven by arrow keys has nothing else
    to say which tile is chosen -- there is no pointer and no hover -- so this
    is the whole of the feedback. Thick enough to read across a room, and NOT
    animated: a ring that pulses is a repaint, and a repaint is a rectangle on
    the wire for as long as the panel is awake. */
 a.tile:focus { outline: none; }
 a.tile:focus-visible, a.tile.chosen {
   outline: none;
   border-color: var(--ring);
   box-shadow: 0 0 0 .5vmin var(--ring);
 }
 .in {
   display: flex; align-items: center; gap: 3vmin; width: 100%%;
   padding: 2.6vmin 3.4vmin;
 }
 .icon {
   --side: clamp(44px, 9vw, 74px);
   flex: none; width: var(--side); height: var(--side);
   display: grid; place-items: center; border-radius: 28%%;
   background: color-mix(in srgb, var(--accent) 28%%, transparent);
   /* Every kind of icon as big as Reolink's, which is the whole square: that
      one was the right size and the rest were reported as "very small" beside
      it -- a logo drew 43 px and an emoji 50 in the same 74 px square. An
      emoji's ink is about 1.25 times its font size, so .78 of the square
      draws it about the square's width and no wider, where the clip below
      would cut it. */
   font-size: calc(var(--side) * .78); line-height: 1;
   /* The field asks for a character and somebody will type a word into it,
      because nothing stops them. Left alone that word runs straight across
      the name beside it. Clipped, and set smaller below when it is long, so
      the worst case is a shortened label rather than two overlapping ones. */
   overflow: hidden;
 }
 /* A picture brings its own shape and fills the square, so the tinted ground
    would only peek out round its corners as a frame that does not match it. */
 .icon.picture { background: none; }
 .icon.letters { font-size: clamp(24px, 5vw, 40px); }
 .icon.long { font-size: clamp(12px, 2vw, 17px); font-weight: 700;
              letter-spacing: -.02em; }
 /* A logo is a shape rather than a character, so it is sized as a fraction of
    the square it sits in rather than by a font size -- the whole of it, the
    size a badge like Reolink's already had. It was 58%%, which left the mark
    at 43 px beside Reolink's 74. */
 .icon .logo { width: 100%%; height: 100%%; display: block; }
 /* A carried picture keeps its aspect ratio inside whatever square it gets;
    the transparent margin is cropped off before it is embedded, so at the
    same size as a path it has the same ink and needs nothing more. Immich was reported as
    too small purely because that margin was still on it -- 33 px of drawn ink
    against a glyph's 43. */
 .icon img.logo { object-fit: contain; }
 /* A BADGE brings its own rounded ground, so it IS the icon: it takes the
    whole square rather than sitting inside the tinted one, the way an app
    icon does everywhere else. At 58%% Reolink's blue square was the right
    size and the white R inside it only 23 px, which is what "the logo is
    small" meant -- the square is not the mark, the letter is. */
 .icon img.logo.badge { width: 100%%; height: 100%%; border-radius: 28%%; }
 /* Blocks, not spans. They are written as spans because an <a> may not
    contain a <div>, and a span left inline puts the description on the same
    line as the name with nothing between them. */
 .text { display: block; min-width: 0; }
 .name { display: block; font-size: clamp(16px, 2.7vw, 23px); font-weight: 600;
         overflow-wrap: anywhere; }
 .desc { display: block; margin-top: .25em; color: var(--faint);
         font-size: clamp(12px, 2vw, 17px); overflow-wrap: anywhere; }
 /* Three widths, three layouts. Above 290px nothing changes: that is where
    "Assistant" still fits beside a full-size icon at the name's full size --
    measured, a 272px tile (1280x800, four columns) cut it and a 296px one
    (1024x600, three) did not.

    Between 218 and 290 the icon STAYS beside the name, at its full size,
    and only the words and the padding shrink to the tile. The first
    version of this stacked every tile under 290, which fixed the cut words
    and made four columns 34%% taller than they had been -- reported
    straight back as the tiles being too big. The second shrank the icon
    with the words, and that was reported too: "fallait les garder". The
    icon is the part read across a room, so it is never what gives way.
    Four columns is 274px at 1280x800 and 220px at 1024x600, so both keep
    their row.

    Under 218 too, and on a button, the icon keeps the size it has on
    every other tile.

    Under 218 the name goes UNDER the icon, the way a phone lays out an
    app: five columns at 1280x800 is 215px and six is 176, which is where
    the photographs of "Jellyfi n" came from, and stacked they were
    accepted. overflow-wrap stays as the last resort, so a name nobody could
    fit still wraps rather than running out of its tile. */
 @container (max-width: 290px) {
   .in { gap: 4cqi; padding: 2.6vmin 5cqi; }
   .name { font-size: clamp(16px, 8.5cqi, 23px); }
   .desc { font-size: clamp(12px, 6.5cqi, 17px); }
 }
 @container (max-width: 217px) {
   .in { flex-direction: column; justify-content: center; text-align: center;
         gap: 1.4vmin; padding: 7cqi 6cqi; }
   .name { font-size: clamp(13px, 14cqi, 23px); }
   .desc { font-size: clamp(11px, 10.5cqi, 17px); }
 }
 /* BUTTONS, the second way to lay out a launcher, chosen with `tiles:`.
    Asked for with the household's own LVGL panel as the model: "icone et
    le button et le texte en bas", as in their waveshare.yaml -- a button of
    150x100 on a 1024x600 screen, the icon at the top, the name along the
    bottom, a gradient, and a press that pushes it down 5 px. So a button is
    a fixed width rather than a share of the row: 26vmin, which is their 150
    on that screen and scales with the panel, and a column count caps how
    many sit on a row without stretching them to fill it. Its height is AT
    LEAST their 100 and grows to hold the full-size icon and a name on two
    lines; the row keeps every button in it the same height.

    The description is not shown: a button carries a name and an icon, and
    a third line is what makes a card a card. Qualified by main.buttons, so
    every rule here outranks the container queries above, which size a CARD
    to its width. */
 main.buttons .group { justify-content: start; }
 main.buttons a.tile {
   /* 15 px on a 600 px screen, their own radius. In vmin rather than cqi:
      a container's own size units read its ANCESTOR's container, which
      turned every button into a pill. */
   min-height: 17.3vmin; border-radius: 2.5vmin;
   background-image: linear-gradient(to bottom,
       rgba(255, 255, 255, .08), rgba(0, 0, 0, .14));
   box-shadow: 0 .8vmin 1.6vmin rgba(0, 0, 0, .35);
 }
 main.buttons .in {
   flex-direction: column; justify-content: center; text-align: center;
   gap: 2.5cqi; padding: 4cqi 5cqi; height: 100%%;
 }
 main.buttons .icon { background: none; }
 main.buttons .name { font-size: clamp(12px, 11cqi, 24px); line-height: 1.15; }
 main.buttons .desc { display: none; }
 /* Their `pressed: translate_y: 5`, and LVGL's own pressed style, which
    shortens the shadow as the button goes down: a button that is pushed
    in, rather than the card's shrink. */
 main.buttons a.tile:active, main.buttons a.tile.press {
   transform: translateY(.8vmin);
   box-shadow: 0 .2vmin .6vmin rgba(0, 0, 0, .35);
 }
 /* The ring again, for a button. `main.buttons a.tile` above outranks the
    ring's own rule, so a chosen button kept its drop shadow INSTEAD of the
    ring and was marked only by a thin border -- which on a light panel is
    what "on ne voit pas ce que je selectionne" looked like. The ring and
    the shadow together, so it is still a button. */
 main.buttons a.tile:focus-visible, main.buttons a.tile.chosen {
   box-shadow: 0 0 0 .5vmin var(--ring), 0 .8vmin 1.6vmin rgba(0, 0, 0, .35);
 }
 .empty { color: var(--faint); font-size: clamp(14px, 2.4vw, 20px); }
 /* The clock, the date and the weather, on one line above the links.
    Deliberately without seconds: a digit that changes every second is a
    rectangle on the wire every second for as long as the panel is awake,
    which is the same reason nothing here animates. On the minute it is one
    small rectangle a minute, and an asleep panel sends nothing at all. */
 .now { display: flex; align-items: center; gap: .6em 1.2em;
        flex-wrap: wrap; margin: 0 0 .6em; }
 /* The date sits UNDER the time rather than beside it, which is what a clock
    looks like everywhere else and what leaves the weather its own room on a
    narrow panel. */
 .when { display: flex; flex-direction: column; gap: .15em; }
 .time { font-size: clamp(34px, 8vw, 68px); font-weight: 300;
         letter-spacing: -0.02em; line-height: 1; font-variant-numeric: tabular-nums; }
 .date { color: var(--faint); font-size: clamp(14px, 2.4vw, 22px);
         text-transform: capitalize; }
 .wx { margin-left: auto; display: flex; align-items: center; gap: .35em;
       align-self: center;
       font-size: clamp(16px, 3vw, 26px); }
 .wx .sky { font-size: 1.5em; line-height: 1; }
 .wx .out { color: var(--faint); font-size: .7em; }
 /* Nothing to show is nothing drawn, rather than an empty box where a
    temperature should be. */
 .wx:empty, .now:empty { display: none; }
%(css)s</style></head>
<body>
<div class="wall" id="wa">%(movie)s</div><div class="wall b" id="wb"></div>
<header>%(now)s%(heading)s</header>
<main class="%(tiles)s">%(groups)s</main>
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
      d.textContent = now.toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
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

# A slideshow, and it is worth being clear about what it costs before
# reading the code: a picture that changes is a WHOLE PANEL on the wire, and a
# fade is one whole panel per frame for as long as it lasts. At 800x1280 and
# quality 80 that is roughly 130 KiB a frame, so two seconds of fade at 25 fps
# is about six megabytes -- against a still page, which sends nothing at all.
# The delay between pictures is what averages that down; the fade is what
# multiplies it. Nought is a hard cut and costs exactly one panel.
#
# The list is asked of the add-on rather than built into the page, so a
# photograph dropped into the folder appears without a restart.
SLIDESHOW_JS = """<script>
(() => {
  const a = document.getElementById('wa'), b = document.getElementById('wb');
  if (!a || !b) return;
  const every = %(every)s * 1000, rescan = %(rescan)s * 60000;
  // A list of addresses, whatever they are addresses OF. A folder gives
  // /wallpaper?i=N served from here; pictures already on a server give their
  // own addresses and this page fetches them exactly as a browser would.
  let srcs = %(sources)s, at = 0, top = a;
  const show = () => {
    if (srcs.length < 2) return;
    at = (at + 1) %% srcs.length;
    // The layer underneath is the one to load into: it is invisible, so a
    // picture that takes a moment to arrive is never seen arriving -- and a
    // picture that never arrives leaves the one showing where it is.
    const under = (top === a) ? b : a;
    const img = new Image();
    img.onload = () => {
      under.style.backgroundImage = 'url("' + img.src + '")';
      under.classList.add('fade'); top.classList.add('fade');
      under.style.opacity = '1'; top.style.opacity = '0';
      top = under;
    };
    img.src = srcs[at];
  };
  // Only a folder can gain a picture while the panel is running, so only a
  // folder asks. A list of addresses is what the form says it is.
  const look = async () => {
    try {
      const r = await fetch('%(list)s', {cache: 'no-store'});
      if (r.ok) {
        const j = await r.json();
        if (j && j.count > 0) {
          srcs = Array.from({length: j.count}, (_, i) => '%(path)s?i=' + i);
        }
      }
    } catch (e) { /* the folder is the add-on's business, not the page's */ }
  };
  if (every > 0) setInterval(show, every);
  if (rescan > 0) setInterval(look, rescan);
})();
</script>
"""

# A video that will not play, said out loud. The commonest cause by far is an
# ordinary .mp4: H.264 is patented, so the browser Playwright downloads does
# not carry it -- measured on the shipped build, canPlayType for
# avc1.42E01E is "" while VP9, VP8 and AV1 all answer "probably". From the
# panel that is a wallpaper that simply never appears.
VIDEO_ERROR_JS = """<script>
(() => {
  const v = document.querySelector('.wall video');
  if (!v) return;
  // The code in words. A household reading "4" learns nothing, and 4 is by
  // far the commonest here: it is what Chromium says for a file whose codec
  // it does not carry.
  const WHY = {1: 'the load was cancelled', 2: 'the network failed',
               3: 'it could not be decoded',
               4: 'this browser cannot play that format'};
  const tell = () => {
    const e = v.error || {};
    try {
      fetch('%(report)s?why=' + encodeURIComponent(
        (WHY[e.code] || 'the browser would not play it')
        + (e.message ? ': ' + e.message : '')), {cache: 'no-store'});
    } catch (err) { /* an accessory must never cost the picture */ }
  };
  v.addEventListener('error', tell);
  // The element's own error fires for a container it cannot open; a codec it
  // cannot decode surfaces on the source instead, and a file that is simply
  // not there gives neither until the fetch fails.
  v.addEventListener('stalled', () => { if (v.error) tell(); });
})();
</script>
"""

# A GIF or a video that is NOT allowed to move still has a first frame worth
# showing, and showing it is better than falling back to a flat colour.
# A paused <video> already shows one; a GIF has to be drawn once into a canvas,
# which is what stops it looping.
FREEZE_JS = """<script>
(() => {
  const wall = document.getElementById('wa');
  if (!wall) return;
  // getComputedStyle, not .style: the picture is named in the sheet the page
  // was built with, and nothing has ever written it inline.
  const named = getComputedStyle(wall).backgroundImage || '';
  const found = named.match(/url\(["']?([^"')]+)["']?\)/);
  if (!found) return;
  const src = found[1];
  const img = new Image();
  img.onload = () => {
    try {
      const c = document.createElement('canvas');
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext('2d').drawImage(img, 0, 0);
      wall.style.backgroundImage = 'url("' + c.toDataURL('image/jpeg', 0.9) + '")';
    } catch (e) {
      // A picture fetched from somewhere else taints the canvas and
      // toDataURL throws. It keeps moving, which is the honest outcome:
      // the alternative is a blank wall.
    }
  };
  img.src = src;
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
  };
  // AT ONCE, and then on a timer. Scheduling only the timer is what made a
  // panel show the temperature from whenever the add-on had started: the
  // number in the page it was served was ten hours old and the first fetch
  // that could have corrected it was ten minutes away. Reported as 16 degrees
  // on the panel against 30 on the dashboard.
  draw();
  // Two minutes rather than ten. The add-on refreshes its own reading every
  // ten, so polling at the same interval meant the page could sit twenty
  // minutes behind the house -- and this fetch never leaves the machine, so
  // it costs nothing. Writing the same text into the DOM paints nothing, so
  // an unchanged reading is not a rectangle on the wire.
  setInterval(draw, 120000);
})();
</script>
"""

# Home Assistant's weather states, as the one character each of them is. Its
# own list, because the state names are Home Assistant's and not this page's.
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

# Arrow keys across the tiles, which is what makes a remote or a gamepad worth
# pairing at all.
#
# WITHOUT THIS THE WHOLE CHAIN DOES NOTHING HERE. A tile is a plain <a href>,
# and in a browser the arrow keys do not move the focus between links -- only
# Tab does. So a panel could pair a remote, carry its presses over the socket,
# replay them perfectly into the page, and still sit there, because nothing on
# the page was listening for an arrow. It is the cheapest piece of this whole
# path and the one it could not work without.
#
# Geometric rather than document order: the tiles are a grid, and "down" means
# the tile below rather than the next one in the markup. `along + across * 3`
# is what decides between two candidates the same distance away -- straight
# ahead beats near-and-sideways.
KEYS_JS = """<script>
(function () {
  var WAY = {ArrowUp: 'u', ArrowDown: 'd', ArrowLeft: 'l', ArrowRight: 'r'};
  function middle(el) {
    var r = el.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2};
  }
  function nearest(from, way, all) {
    var here = middle(from), best = null, score = Infinity;
    for (var i = 0; i < all.length; i++) {
      if (all[i] === from) continue;
      var there = middle(all[i]);
      var dx = there.x - here.x, dy = there.y - here.y;
      var along, across;
      if (way === 'u') { along = -dy; across = Math.abs(dx); }
      else if (way === 'd') { along = dy; across = Math.abs(dx); }
      else if (way === 'l') { along = -dx; across = Math.abs(dy); }
      else { along = dx; across = Math.abs(dy); }
      if (along <= 1) continue;
      var s = along + across * 3;
      if (s < score) { score = s; best = all[i]; }
    }
    return best;
  }
  document.addEventListener('keydown', function (e) {
    var way = WAY[e.key];
    if (!way) return;
    var all = [].slice.call(document.querySelectorAll('a.tile'));
    if (!all.length) return;
    /* SWALLOWED HERE, BEFORE ANYTHING ELSE, AND THAT IS THE WHOLE OF A BUG
       REPORTED FROM A PANEL. It used to be called only when a tile was found
       in that direction -- so an arrow at the EDGE of the grid fell through
       to the browser, which scrolls. Pressing up on the top row therefore
       scrolled the launcher away from the tile that still had the focus, and
       the next press moved a focus ring nobody could see. Up is where it
       shows first because up is the one direction the top row has nothing
       in, and a launcher opens with the top row chosen.

       The arrows belong to the grid on this page. There is nothing else on
       it to scroll to. */
    e.preventDefault();
    var from = document.activeElement;
    if (all.indexOf(from) < 0) {
      /* Nothing chosen yet, so the first arrow CHOOSES rather than moves.
         Deliberately not done when the page loads: a focus ring drawn on
         arrival is a rectangle on the wire for every panel in the house,
         including the ones nobody drives with a remote.

         WHICH tile it chooses follows the direction, the way a menu does:
         pressing up reaches for the one at the BOTTOM. Always taking the
         first made up and down do the same thing from a cold page, which is
         its own small "that did not do what I meant". */
      (way === 'u' || way === 'l' ? all[all.length - 1] : all[0]).focus();
      return;
    }
    var next = nearest(from, way, all);
    if (next) next.focus();
  });
})();
</script>"""

PRESS_JS = """<script>
(function () {
  /* HOLD THE PRESSED LOOK LONG ENOUGH FOR A FRAME TO CONTAIN IT.
   *
   * The `:active` rule in the stylesheet is correct and is applied -- and on
   * this path it lasts a MEASURED median of 2.3 ms, because the sender holds
   * a contact back until the finger lifts and then dispatches mousedown and
   * mouseup together. The panel sees JPEG rectangles at `fps`, so 2.3 ms is
   * 6% of one frame at 25 and the press is almost never photographed.
   *
   * 200 ms is chosen against the rate the panel is really running at when
   * this happens: a contact lifts the limit to `urgent_fps` for two seconds,
   * which is 30 by default, so this is about six frames -- and still two at a
   * link capped to 10. It is a timeout rather than an animation, so it costs
   * the two rectangles a press is worth and nothing while the panel idles.
   *
   * Nothing here calls preventDefault: the tap must still reach the link, and
   * a listener that swallowed it would turn a slow tile into a dead one. */
  var HELD_MS = 200;
  function flash(tile) {
    if (!tile) return;
    tile.classList.add('press');
    if (tile.__off) clearTimeout(tile.__off);
    tile.__off = setTimeout(function () {
      tile.classList.remove('press');
      tile.__off = 0;
    }, HELD_MS);
  }
  /* pointerdown rather than mousedown, because it is the unified path and
     fires for a replayed contact and a real finger alike -- and it is the
     FIRST of the four a press produces, so the look starts as early as it
     can. Capture phase, so a page that stops the event later cannot take the
     feedback with it. */
  document.addEventListener('pointerdown', function (e) {
    flash(e.target.closest && e.target.closest('a.tile'));
  }, true);
  /* And the remote, which produces no pointer event at all: OK on a chosen
     tile is a press too, and a panel driven from the sofa is exactly where
     "did that do anything?" is hardest to answer. */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var on = document.activeElement;
    if (on && on.classList && on.classList.contains('tile')) flash(on);
  }, true);
})();
</script>"""

# The avatar: a small face that lives on the launcher, the size of one of its
# buttons, in the bottom right corner until somebody moves it.
#
# The drawing is Eric Nam's lvgl_kawaii_face (MIT), the face its ESPHome
# integration in youkorr/esphome-lvgl-kawaii puts on an LVGL panel, redrawn
# for a browser. It cannot be used as it stands: that is C drawing into LVGL
# canvases, and a panel here runs no LVGL -- it shows JPEG rectangles of a
# page. Its shapes are rounded rectangles and lines, so they carry straight
# across into SVG.
#
# Three rules decide how it behaves, and each is this project's rather than
# the original's:
#
# - IT IS STILL MOST OF THE TIME. Anything that moves is a rectangle on the
#   wire for as long as the panel is awake, and a still launcher costs nothing
#   today. So no animation runs by itself: a blink is a class held for 140 ms
#   every four to eight seconds, a glance a class held for a second and a
#   half, and between them the face paints nothing at all.
# - IT IS MOVED BY THE FINGER, WHICH ARRIVES AS A WHEEL. The sender puts the
#   pointer where the finger lands and turns the drag into wheel events, so a
#   page never sees a mouse drag. Landing on the face arms it; every wheel of
#   that gesture then moves the face by the finger's travel and is swallowed,
#   so the page under it does not scroll. The next landing anywhere else
#   disarms it.
# - IT REMEMBERS WHERE IT WAS PUT, ON THE ADD-ON'S SIDE. The page asks
#   AVATAR_PATH to keep the spot, as fractions of the room left around it, so
#   the same spot survives a restart, a new port and a panel turned round.
#   The page's own storage could not: a panel's own launcher is on a port the
#   system picks, so its origin -- and its storage -- is new at every start.
AVATAR_PATH = "/avatar"
VOICE_PATH = "/voice"

AVATAR_CSS = """
 #av {
   position: fixed; z-index: 50; width: 26vmin; height: 17.33vmin;
   right: 3vmin; bottom: 3vmin; %(place)s
   border-radius: 2.5vmin; overflow: hidden;
   background: #0f131b; border: 1px solid rgba(255,255,255,.14);
   box-shadow: 0 .8vmin 2.4vmin rgba(0,0,0,.45);
   touch-action: none; user-select: none; -webkit-user-select: none;
 }
 #av svg { width: 100%%; height: 100%%; display: block; }
 #av .eye, #av .brow, #av .lift, #av .mouth, #av .look, #av .shut, #av .o {
   transform-box: fill-box; transform-origin: center;
 }
 /* Only a change of mood is eased, because somebody caused it. A blink and a
    glance happen by themselves every few seconds, and eased they cost about
    fourteen pictures each where a snap costs two -- measured, 87 pictures in
    twenty seconds of a still launcher against one without the face. */
 #av .brow, #av .mouth, #av .shut, #av .o {
   transition: transform .12s ease, opacity .12s ease, d .12s ease;
 }
 #av .look { transform: translate(var(--lx, 0px), var(--ly, 0px)); }
 #av .shut, #av .o { opacity: 0; }
 #av.blink .eye { transform: scaleY(.08); }
 /* A resting face lifts its brows now and then, as the original does every
    8.4 s for 1.5 s. Snapped, like a blink: the group that carries it has no
    transition, so it costs two small pictures rather than a run of them. */
 #av.lift .lift.l { transform: translateY(-3px) rotate(4deg); }
 #av.lift .lift.r { transform: translateY(-3px) rotate(1deg); }
 #av[data-mood="happy"] .eye { opacity: 0; }
 #av[data-mood="happy"] .shut { opacity: 1; }
 #av[data-mood="happy"] .mouth { d: path("M58 72 Q75 90 92 72"); }
 /* The brows are the original's own numbers, per mood. There an angle A
    lifts a brow's ends by a quarter of its width times sin(A), so the tilt
    actually drawn is atan(sin(A) / 2); and the height is in pixels of its
    135 px reference face, which is 0.74 of a unit on this 100-unit one. Its
    left brow at +A tilts clockwise and its right brow at +A the other way,
    so the pair is written rotate(L) and rotate(-R). Their colour is the
    original's for a dark panel, rgb(122,137,160).

    Their SHAPE is the household's own LVGL face
    (youkorr/esphome-lvgl-kawaii, lvgl_kawaii_face.c), which they preferred:
    nine tenths of the eye's width, a stroke a tenth of it, and a gap above
    the eye of fs(6) on its 135 px reference. And POINTED at the outer end,
    which the C never asks for: it draws a plain round-capped line, but
    inside each eye's own canvas and only a few pixels below its top, so a
    brow that tilts has its outer end cut off by the canvas edge -- a wedge,
    full and round at the nose, running to a point. That is the face on the
    household's panel, so that is the shape drawn here, 0.12 of the eye at
    its widest -- measured off a video of that panel, where the round cap's
    fs(4) reads that full once the rest of the brow runs to nothing.
    Thinking keeps its tilt and not its drop: that face also narrows its eye
    to two thirds, which leaves the room, and this one's eye stays open -- lowered,
    the brow would rest on it. */
 #av[data-mood="happy"] .brow.l { transform: translateY(-3.7px) rotate(-2deg); }
 #av[data-mood="happy"] .brow.r { transform: translateY(-3.7px) rotate(2deg); }
 #av[data-mood="surprised"] .brow { transform: translateY(-7.4px); }
 #av[data-mood="surprised"] .mouth { opacity: 0; }
 #av[data-mood="surprised"] .o { opacity: 1; }
 #av[data-mood="thinking"] .look { transform: translate(4px, -5px); }
 #av[data-mood="thinking"] .brow {
   transform: rotate(10.6deg);
 }
 #av[data-mood="thinking"] .mouth { d: path("M66 78 Q75 78 86 75"); }
 #av[data-mood="sleepy"] .eye { transform: scaleY(.25); }
 #av[data-mood="sleepy"] .mouth { d: path("M68 78 Q75 79 82 78"); }
 #av[data-mood="sad"] .brow.l { transform: rotate(-12deg); }
 #av[data-mood="sad"] .brow.r { transform: rotate(12deg); }
 #av[data-mood="sad"] .mouth { d: path("M62 82 Q75 72 88 82"); }
"""

# The face, on a 150 x 100 box: the button's own 3:2. Everything that moves
# is its own group, so an expression is a class on the box and nothing more.
# The eyes and brows are the household's LVGL face (draw_eye() in
# lvgl_kawaii_face.c) at the size that keeps the eye centres 60 apart: a
# ROUND eye 0.306 of the face wide, an iris 0.55 of it with a darker ring,
# an oval pupil half the iris wide and 0.6 of it tall, two highlights, and
# the shut eye an arc from 200 to 340 degrees over 0.3 of the eye's height.
AVATAR_HTML = """<div id="av" data-mood="neutral" aria-hidden="true">
<svg viewBox="0 0 150 100">
 <g class="lift l"><path class="brow l" d="M30.3 29 L57.7 25 A2 2 0 0 1 57.7 29 Z"
  fill="#7a89a0"/></g>
 <g class="lift r"><path class="brow r" d="M119.7 29 L92.3 25 A2 2 0 0 0 92.3 29 Z"
  fill="#7a89a0"/></g>
 <g class="eye">
  <circle cx="45" cy="48" r="16.35" fill="#fff"/>
  <g class="look"><circle cx="45" cy="48" r="8.2" fill="#32b4ff"
    stroke="#1e8ce6" stroke-width="1.6"/>
   <ellipse cx="45" cy="48" rx="4.5" ry="5.4" fill="#000"/>
   <ellipse cx="42" cy="44.4" rx="1.8" ry="2.15" fill="#fff"/>
   <ellipse cx="47.25" cy="45.3" rx=".9" ry="1.1" fill="#fff"/></g>
 </g>
 <g class="eye">
  <circle cx="105" cy="48" r="16.35" fill="#fff"/>
  <g class="look"><circle cx="105" cy="48" r="8.2" fill="#32b4ff"
    stroke="#1e8ce6" stroke-width="1.6"/>
   <ellipse cx="105" cy="48" rx="4.5" ry="5.4" fill="#000"/>
   <ellipse cx="102" cy="44.4" rx="1.8" ry="2.15" fill="#fff"/>
   <ellipse cx="107.25" cy="45.3" rx=".9" ry="1.1" fill="#fff"/></g>
 </g>
 <path class="shut" d="M29.6 44.6 Q45 31.8 60.4 44.6" stroke="#fff" stroke-width="4.7"
       stroke-linecap="round" fill="none"/>
 <path class="shut" d="M89.6 44.6 Q105 31.8 120.4 44.6" stroke="#fff" stroke-width="4.7"
       stroke-linecap="round" fill="none"/>
 <ellipse cx="26" cy="72" rx="8" ry="4.5" fill="#ff7a9a" opacity=".35"/>
 <ellipse cx="124" cy="72" rx="8" ry="4.5" fill="#ff7a9a" opacity=".35"/>
 <path class="mouth" d="M62 76 Q75 84 88 76" stroke="#ff5d73" stroke-width="4"
       stroke-linecap="round" fill="none"/>
 <ellipse class="o" cx="75" cy="79" rx="6" ry="7" fill="#ff5d73"/>
</svg></div>"""

AVATAR_JS = """<script>
(function () {
  var box = document.getElementById('av');
  if (!box) return;
  var back = 0, rest = 'neutral';
  /* One expression at a time. A passing one -- a tap, a move -- goes back by
     itself to the one that stands, which is neutral unless the voice
     assistant is listening, thinking or answering. */
  function set(mood, ms) {
    box.dataset.mood = mood || rest;
    clearTimeout(back);
    if (ms) back = setTimeout(function () { set(rest); }, ms);
  }
  function stand(mood) {
    rest = mood || 'neutral';
    set(rest);
  }
  window.portallAvatar = {set: set, stand: stand};

  function blink() {
    box.classList.add('blink');
    setTimeout(function () { box.classList.remove('blink'); }, 140);
    setTimeout(blink, 4000 + Math.random() * 4000);
  }
  setTimeout(blink, 3000);
  function glance() {
    if (box.dataset.mood === 'neutral') {
      var x = [-4, 4, 3][Math.floor(Math.random() * 3)];
      box.style.setProperty('--lx', x + 'px');
      setTimeout(function () { box.style.setProperty('--lx', '0px'); }, 1500);
    }
    setTimeout(glance, 10000 + Math.random() * 8000);
  }
  setTimeout(glance, 9000);
  function lift() {
    if (box.dataset.mood === 'neutral') {
      box.classList.add('lift');
      setTimeout(function () { box.classList.remove('lift'); }, 1500);
    }
    setTimeout(lift, 8000 + Math.random() * 4000);
  }
  setTimeout(lift, 6000);

  /* A tap on the face is somebody saying hello; it is never a link. */
  box.addEventListener('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    set('happy', 2500);
  });

  function on(e) {
    return e.composedPath ? e.composedPath().indexOf(box) >= 0
                          : box.contains(e.target);
  }
  var armed = false, moved = false, save = 0;
  window.addEventListener('mousemove', function (e) { armed = on(e); }, true);
  window.addEventListener('pointerdown', function (e) { armed = on(e); }, true);
  window.addEventListener('wheel', function (e) {
    if (!armed && !on(e)) return;
    armed = true;
    e.preventDefault();
    e.stopPropagation();
    var r = box.getBoundingClientRect();
    var room = [Math.max(1, innerWidth - r.width),
                Math.max(1, innerHeight - r.height)];
    var x = Math.min(room[0], Math.max(0, r.left - e.deltaX));
    var y = Math.min(room[1], Math.max(0, r.top - e.deltaY));
    box.style.left = x + 'px';
    box.style.top = y + 'px';
    box.style.right = 'auto';
    box.style.bottom = 'auto';
    if (!moved) set('surprised');
    moved = true;
    clearTimeout(save);
    save = setTimeout(function () {
      moved = false;
      set('happy', 1500);
      try {
        fetch('%(path)s?x=' + (x / room[0]).toFixed(4) +
              '&y=' + (y / room[1]).toFixed(4)).catch(function () {});
      } catch (err) { /* where it was put is kept for this visit anyway */ }
    }, 600);
  }, {capture: true, passive: false});
})();
</script>"""


# The voice assistant, pushed rather than polled: the page asks and the
# add-on holds the question until the satellite's state changes, so a panel
# nobody is talking to asks three times a minute and draws nothing. A version
# rather than the mood itself, so two changes in a row are never mistaken for
# none. A failure waits and asks again: the face is an accessory.
AVATAR_VOICE_JS = """<script>
(function () {
  if (!window.portallAvatar) return;
  function ask(v) {
    fetch('%(path)s?v=' + v, {cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.v !== v) window.portallAvatar.stand(d.mood);
        ask(d.v);
      })
      .catch(function () { setTimeout(function () { ask(v); }, 5000); });
  }
  ask(-1);
})();
</script>"""


def _avatar_place(at):
    """Where the face goes: its saved spot, or the bottom right corner."""
    if not at:
        return ""
    x, y = at
    return ("right: auto; bottom: auto; "
            f"left: calc((100vw - 26vmin) * {x:.4f}); "
            f"top: calc((100vh - 17.33vmin) * {y:.4f});")


TILE = ('<a class="tile" href="%(url)s"><span class="in">'
        '<span class="icon%(icon_long)s">%(icon)s</span>'
        '<span class="text"><span class="name">%(name)s</span>%(desc)s</span>'
        '</span></a>')

EMPTY = ('<p class="empty">No links yet. Add them under <b>links</b> in this '
         "add-on's configuration, then restart it.</p>")


# What a folder of pictures may hold. Deliberately short: these are what a
# camera and a phone produce, and a folder of holiday photographs should not
# turn into a page of broken squares because something dropped a .txt in it.
PICTURE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp")
MOVIE_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v")


def _moves(name):
    """Whether this is something that would move if it were let to.

    A GIF is in both lists on purpose: it is a picture that plays, so it is a
    picture for the purpose of finding it in a folder and a moving thing for
    the purpose of deciding whether it may move.
    """
    lowered = str(name or "").split("?")[0].lower()
    return lowered.endswith(MOVIE_SUFFIXES) or lowered.endswith(".gif")


def _is_movie(name):
    return str(name or "").split("?")[0].lower().endswith(MOVIE_SUFFIXES)


def _addresses(urls):
    """The pictures somebody gave by address rather than by path.

    A household's photographs are as likely to be on a server already -- a
    NAS, an Immich, anything serving a folder over HTTP -- as under one of the
    three mounts this add-on can read. Those are fetched by the panel's own
    browser, exactly as any wallpaper given by address always was.
    """
    out = []
    for entry in urls or []:
        entry = str(entry or "").strip()
        if entry.startswith(("http://", "https://", "data:")):
            out.append(entry)
        elif entry:
            print(f"Launcher: ignoring the slideshow address {entry!r} -- it "
                  f"has to begin with http:// or https://. A picture on this "
                  f"machine goes in launcher_background as a path instead.",
                  flush=True)
    return out


def _wallpaper(background):
    """What the page should show behind everything, and what to serve for it.

    Returns (kind, value, files):
      "none"                 -- the plain colour
      "url",  an address     -- fetched by the panel itself; Home Assistant's
                                own /local is the obvious place to keep one
      "file", one path       -- served from here, because the page is on
                                127.0.0.1 and no browser loads a file:// URL
      "folder", a path, and the pictures in it, sorted -- a slideshow
    """
    background = str(background or "").strip()
    if not background:
        return "none", None, []
    if background.startswith(("http://", "https://", "data:")):
        return "url", background, []
    if os.path.isfile(background):
        return "file", background, []
    if os.path.isdir(background):
        # Sorted so the order is the one somebody sees in their file manager,
        # which is the only order they can predict or rearrange.
        files = sorted(
            entry for entry in os.listdir(background)
            if entry.lower().endswith(PICTURE_SUFFIXES)
            and os.path.isfile(os.path.join(background, entry))
        )
        if files:
            return "folder", background, files
        print(f"Launcher: {background} is a folder with no pictures in it -- "
              f"the plain colour is being used.", flush=True)
        return "none", None, []
    # Said once, at startup, rather than left as a blank wall nobody can
    # explain from the panel.
    print(f"Launcher: no wallpaper at {background} -- the plain colour is "
          f"being used. Put the file or the folder under /config, /share or "
          f"/media, or give an address the panel can reach.", flush=True)
    return "none", None, []


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


def _one_focus(want):
    """The ring that says which tile a remote is on, or nothing for the theme's.

    Asked for from a light panel: "sur un fond clair ont ne voit pas ce que je
    selectionne". The ring is the accent, and the accent is a middle shade
    chosen to sit on the card rather than to stand out from it -- on a light
    theme or a pale wallpaper it nearly vanishes, and on a panel driven by a
    remote it is the ONLY thing that says where the next OK will land. A name
    from the same list as the clock's colour, so a household picks black or
    yellow in a dropdown rather than learning what a stylesheet is. It sets
    --ring, which both shapes' rings are drawn from.
    """
    tint = PALETTES.get(str(want or "").lower())
    return [f" :root {{ --ring: {tint}; }}"] if tint else []


def _bar(clock_size, clock_color, date_size, date_color, weather_size, align,
         focus_color=FOLLOW_THEME):
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
    out += _one_focus(focus_color)
    where = str(align or "left").lower()
    if where in ("center", "centre", "right"):
        # The weather is pushed right by a margin, which would fight any
        # alignment of the bar as a whole, so it goes when the bar is placed.
        out.append(" .wx { margin-left: 0; }")
        out.append(f" .now {{ justify-content: "
                   f"{'center' if where.startswith('cent') else 'flex-end'}; }}")
    return ("\n".join(out) + "\n") if out else ""


def render(links, title="", subtitle="", theme="dark",
           color=DEFAULT_PALETTE, background="", blur="off", dim=40,
           columns=0, clock=True, weather=None,
           clock_size=DEFAULT_SIZE, clock_color=FOLLOW_THEME,
           date_size=DEFAULT_SIZE, date_color=FOLLOW_THEME,
           weather_size=DEFAULT_SIZE, align="left",
           motion=False, slideshow=False, every=30, fade=1, rescan=60,
           urls=(), mirrored=False, shape="cards", focus_color=FOLLOW_THEME,
           avatar=False, avatar_at=None, voice=False):
    """The page, as one string.

    Every value is escaped. These come from a configuration file a person
    edits, so a name containing an ampersand or a stray angle bracket is a
    typo rather than an attack -- and a typo that silently breaks the page a
    panel comes home to is still the worst kind of bug to chase.
    """
    dark = str(theme).lower() != "light"
    accent = PALETTES.get(str(color).lower(), PALETTES[DEFAULT_PALETTE])

    addresses = _addresses(urls)
    if addresses:
        # Given both, the addresses win: a folder is what somebody had before
        # they filled this in, and the field they just filled in is the one
        # they mean.
        kind, where, files = "urls", None, addresses
    else:
        kind, where, files = _wallpaper(background)
    has_wall = kind != "none"
    # A video is an element rather than a background-image, so the wall is
    # built here rather than in one CSS line. Only a video that is ALLOWED to
    # move becomes one: a still frame of the same file, which is what a paused
    # video shows, costs the panel nothing and is what the switch is for.
    movie = ""
    wall_css = "none"
    wants_video_report = False
    if kind == "url":
        # A picture fetched from somewhere else taints a canvas, so a GIF at
        # an address cannot be frozen where it lies -- measured, it went on
        # looping with motion off. start() copies that one here first, and
        # this is where the copy is used instead.
        first = WALLPAPER_PATH if mirrored else background
    elif kind == "file":
        first = WALLPAPER_PATH
    elif kind == "folder":
        first = f"{WALLPAPER_PATH}?i=0"
    elif kind == "urls":
        first = files[0]
    else:
        first = ""
    source = (files[0] if kind == "urls"
              else where if kind in ("file", "folder") else background)
    if first and _is_movie(source):
        # preload="auto" even when it is not allowed to play. A <video> paints
        # nothing until it has a frame, and a blank rectangle where a
        # photograph should be is the failure nobody can diagnose from a
        # panel. "metadata" was enough for the 12 KiB clip this was measured
        # against -- Chromium fetched the whole of it anyway -- but that is
        # exactly what it need not do for a large one, so the guarantee is
        # worth the fetch.
        movie = (f'<video src="{html.escape(first, quote=True)}" muted '
                 f'playsinline preload="auto"'
                 f'{" autoplay loop" if motion else ""}></video>')
        wants_video_report = True
    elif first:
        wall_css = f'url("{html.escape(first, quote=True)}")'

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
                "icon": (logo_markup(entry.get("icon"), dark)
                         or html.escape(icon_for(entry.get("icon")))),
                # Two characters still read at the full size -- "HA" is a
                # perfectly good icon. Beyond that it is a word, and a word
                # has to be set smaller to stay inside its square. Measured on
                # what will be drawn, not on what was typed: a name from the
                # list is one glyph however long the name is.
                "icon_long": icon_kind(entry.get("icon"), dark),
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
    # Buttons keep their own size: a column count says how many may sit on
    # a row, and each is 26vmin or its share of the row, whichever is less.
    buttons = str(shape or "").strip().lower() == "buttons"
    if buttons:
        grid = (f"repeat({columns}, minmax(0, 26vmin))" if columns > 0
                else "repeat(auto-fill, 26vmin)")

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
            now += ('<div class="when"><span class="time" id="t"></span>'
                    '<span class="date" id="d"></span></div>')
        if weather is not None:
            now += (f'<span class="wx"><span class="sky" id="sky">{sky}</span>'
                    f'<span class="out" id="temp">{temp}</span></span>')
        now += "</div>"
    # Always, and not behind an option. It costs one listener that fires on a
    # key nobody presses unless there is a remote, and an option for it would
    # be one more thing to read past -- which this add-on has already had to
    # take five settings off the form for.
    scripts = KEYS_JS + PRESS_JS + (CLOCK_JS if clock else "") + (
        WEATHER_JS % {"path": WEATHER_PATH} if weather is not None else "")

    # The bar's own rules, after the sheet above and before nothing: there is
    # no stylesheet field any more. It was offered first, on the argument that
    # one general mechanism beats a setting per thing -- and that argument is
    # the maintainer's convenience, not the household's. Homepage names the
    # size of each widget on a fixed scale and has no CSS field at all, which
    # is the shape this follows now.
    sheet = _bar(clock_size, clock_color, date_size, date_color,
                 weather_size, align, focus_color)

    try:
        every, fade, rescan = float(every), float(fade), float(rescan)
    except (TypeError, ValueError):
        every, fade, rescan = 30.0, 1.0, 60.0
    moving = []
    if kind in ("folder", "urls") and slideshow and len(files) > 1:
        moving.append(SLIDESHOW_JS % {
            "every": every,
            # Only a folder can gain a picture while the panel runs.
            "rescan": rescan if kind == "folder" else 0,
            "sources": json.dumps(
                [f"{WALLPAPER_PATH}?i={i}" for i in range(len(files))]
                if kind == "folder" else files),
            "path": WALLPAPER_PATH,
            "list": SLIDES_PATH,
        })
    elif wall_css != "none" and kind not in ("folder", "urls") \
            and _moves(source) and not motion:
        # A GIF nobody asked to move. Frozen rather than dropped: its first
        # frame is a perfectly good wallpaper, and a blank one is not.
        moving.append(FREEZE_JS)
    if wants_video_report:
        moving.append(VIDEO_ERROR_JS % {"report": REPORT_PATH})
    if avatar:
        sheet += AVATAR_CSS % {"place": _avatar_place(avatar_at)}
        moving.append(AVATAR_HTML + AVATAR_JS % {"path": AVATAR_PATH})
        if voice:
            moving.append(AVATAR_VOICE_JS % {"path": VOICE_PATH})

    return PAGE % {
        "css": sheet,
        "now": now,
        "clockjs": scripts + "".join(moving),
        # The tab's name, which no panel ever shows -- but a page with no
        # <title> is one nobody can find in a browser either.
        "title": html.escape(str(title).strip() or "Portall"),
        # No heading at all when there is nothing to head: a page whose
        # every tile is labelled does not need the word "Panel" over it, and
        # a title nobody set is exactly that.
        "heading": (
            (f"<h1>{html.escape(title)}</h1>" if str(title).strip() else "")
            + (f"<p>{html.escape(subtitle)}</p>"
               if str(subtitle).strip() else "")),
        "movie": movie,
        "fade": max(0.0, fade),
        "groups": "".join(body) or EMPTY,
        "columns": grid,
        "tiles": "buttons" if buttons else "cards",
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


def start(links, title="", subtitle="", theme="dark",
          color=DEFAULT_PALETTE, background="", blur="off", dim=40,
          columns=0, clock=True, weather=None,
          clock_size=DEFAULT_SIZE, clock_color=FOLLOW_THEME,
          date_size=DEFAULT_SIZE, date_color=FOLLOW_THEME,
          weather_size=DEFAULT_SIZE, align="left",
          motion=False, slideshow=False, every=30, fade=1, rescan=60,
          urls=(), port=PORT, tiles="cards", focus_color=FOLLOW_THEME,
          avatar=False, avatar_file=None, voice=None):
    """Serve the page for as long as the add-on runs. Returns its address.

    One call is one launcher: its links, its look, its weather, its
    wallpaper and its slideshow, on a server of its own. A panel with a
    launcher of its own is given a call of its own on ANY_PORT, so nothing
    one panel's page shows or fetches is shared with another's -- the
    wallpaper and the weather included, which a page per panel on one shared
    server could not have kept apart.
    """
    addresses = _addresses(urls)
    if addresses:
        print(f"Launcher: {len(addresses)} picture(s) by address"
              + (f", one every {every}s with a {fade}s fade" if slideshow
                 else ", showing the first")
              + ". They are fetched by the panel's own browser, so the "
                "machine serving them has to be reachable from here.",
              flush=True)
        kind, where, files = "urls", None, addresses
    else:
        kind, where, files = _wallpaper(background)

    # A GIF at an address that is not allowed to move is the one case the
    # page cannot handle by itself: freezing it means reading its pixels back
    # out of a canvas, and a browser refuses that for a picture fetched from
    # anywhere else. Copied here once, it is same-origin and the freeze works.
    # Never fatal, and never for a video -- pausing one needs no canvas.
    picture, mime = None, "application/octet-stream"
    mirrored = False
    if kind == "url" and not motion and _moves(where or background) \
            and not _is_movie(where or background):
        try:
            with urllib.request.urlopen(background, timeout=10) as answer:
                picture = answer.read()
                mime = answer.headers.get("Content-Type") or "image/gif"
            mirrored = True
            print(f"Launcher: copied the wallpaper here "
                  f"({len(picture) // 1024} KiB) so it can be held on its "
                  f"first frame; launcher_background_motion lets it play.",
                  flush=True)
        except Exception as err:  # noqa: BLE001 - an accessory, never a cost
            print(f"Launcher: could not copy {background} ({err}) -- it will "
                  f"keep moving, because a picture fetched from elsewhere "
                  f"cannot be frozen by the page.", flush=True)

    # `weather` is a callable returning the latest reading, or None -- the
    # add-on refreshes it in the background and this is asked for it at each
    # request rather than once at startup.
    #
    # Built once at startup was the other half of a panel showing the
    # temperature from whenever the add-on had been started: the page is
    # static bytes, so the number in it never moved however often the add-on
    # re-read the house. It is rebuilt when the reading changes and reused
    # when it has not -- which is at most once every ten minutes, and never
    # at all on a panel with no weather.
    cache = {}

    # Where the avatar was last put, read once and kept in memory. A spot that
    # cannot be read is the corner it starts in -- never a reason to fail.
    spot = {"at": None}
    if avatar and avatar_file:
        try:
            with open(avatar_file, encoding="utf-8") as handle:
                saved = json.load(handle)
            spot["at"] = (min(1.0, max(0.0, float(saved["x"]))),
                          min(1.0, max(0.0, float(saved["y"]))))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def page():
        state = weather() if weather is not None else None
        # The avatar's spot is part of the page: a panel coming home finds
        # the face where it was left, drawn there from the first frame rather
        # than jumping there after a script has run.
        key = (weather_block(state) if state else None, spot["at"])
        held = cache.get("page")
        if held is None or held[0] != key:
            body = render(
                links, title, subtitle, theme, color,
                background, blur, dim, columns, clock,
                state if weather is not None else None,
                clock_size, clock_color, date_size, date_color,
                weather_size, align,
                # Already sifted just above, so the page does not repeat
                # the complaint about an address that is not one.
                motion, slideshow, every, fade, rescan, addresses,
                mirrored, shape=tiles, focus_color=focus_color,
                avatar=avatar, avatar_at=spot["at"],
                voice=voice is not None).encode()
            held = cache["page"] = (key, body)
        return held[1]

    # Once here, so a fault in the page is reported at startup rather than
    # the first time somebody comes home to it.
    page()
    # One picture is read once and held; a folder is read per request. A
    # holiday folder is gigabytes, and the add-on has no business holding it
    # -- while re-reading one file for every panel that comes home would be a
    # disk seek where there is no need for one.
    if not mirrored:
        picture, mime = None, "application/octet-stream"
    if kind == "file":
        try:
            with open(where, "rb") as handle:
                picture = handle.read()
            mime = mimetypes.guess_type(where)[0] or mime
            print(f"Launcher: wallpaper {where} "
                  f"({len(picture) // 1024} KiB)", flush=True)
        except OSError as err:
            # Read once at startup rather than per request: a panel coming
            # home should not wait on a disk, and a file that has gone away
            # should not turn into a broken page later on.
            print(f"Launcher: could not read {where} ({err})", flush=True)
    elif kind == "folder":
        print(f"Launcher: {len(files)} picture(s) in {where}"
              + (f", one every {every}s with a {fade}s fade" if slideshow
                 else ", showing the first")
              + ". A picture that changes is a whole panel on the wire, and "
                "each second of fade is one whole panel per frame.",
              flush=True)

    def from_folder(index):
        """One picture out of the folder, re-read each time it is asked for.

        The folder is re-listed here rather than trusted from startup, so a
        photograph dropped in appears without the add-on being restarted --
        and the name is taken from that listing rather than from the request,
        which is what keeps this from serving anything outside the folder.
        """
        try:
            now = sorted(
                entry for entry in os.listdir(where)
                if entry.lower().endswith(PICTURE_SUFFIXES)
                and os.path.isfile(os.path.join(where, entry))
            )
            if not now:
                return None, None
            name = now[index % len(now)]
            with open(os.path.join(where, name), "rb") as handle:
                return handle.read(), (mimetypes.guess_type(name)[0]
                                       or "application/octet-stream")
        except (OSError, ValueError):
            return None, None

    def how_many():
        try:
            return sum(1 for entry in os.listdir(where)
                       if entry.lower().endswith(PICTURE_SUFFIXES)
                       and os.path.isfile(os.path.join(where, entry)))
        except OSError:
            return 0

    # Complaints already made, so a page reloaded every time somebody comes
    # home does not fill the log with the same sentence.
    said = set()

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
            if self.path.split("?")[0] == REPORT_PATH:
                # Said once per distinct complaint. A wallpaper that will not
                # play would otherwise be a blank rectangle with nothing
                # anywhere to explain it, which is the one failure this page
                # is not allowed to produce.
                why = (parse_qs(urlsplit(self.path).query).get("why") or [""])[0]
                why = " ".join(str(why).split())[:200]
                if why and why not in said:
                    said.add(why)
                    print(f"Launcher: the wallpaper video would not play "
                          f"({why}). The commonest cause is an ordinary .mp4: "
                          f"H.264 is patented and the browser this add-on "
                          f"downloads does not carry it -- the sender's log "
                          f"says 'decodes H.264 no' when that is it. Use a "
                          f"WebM (VP9), or install a Chromium packaged by "
                          f"your distribution, which the sender prefers.",
                          flush=True)
                self.send_response(204)
                self.end_headers()
                return
            if self.path.split("?")[0] == AVATAR_PATH:
                # Where the face was put. Fractions of the room around it, so
                # anything outside 0..1 is not a spot and is not kept.
                asked = parse_qs(urlsplit(self.path).query)
                try:
                    x = float((asked.get("x") or [""])[0])
                    y = float((asked.get("y") or [""])[0])
                    ok = avatar and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
                except ValueError:
                    ok = False
                if ok:
                    spot["at"] = (x, y)
                    if avatar_file:
                        try:
                            os.makedirs(os.path.dirname(avatar_file) or ".",
                                        exist_ok=True)
                            with open(avatar_file, "w", encoding="utf-8") as h:
                                json.dump({"x": x, "y": y}, h)
                        except OSError as err:
                            if "avatar" not in said:
                                said.add("avatar")
                                print(f"Launcher: could not keep where the "
                                      f"avatar was put ({err}); it goes back "
                                      f"to the corner after a restart.",
                                      flush=True)
                self.send_response(204)
                self.end_headers()
                return
            if self.path.split("?")[0] == VOICE_PATH:
                # Held until the voice assistant's state changes, so the face
                # moves the moment it does and nothing is asked in between.
                try:
                    since = int((parse_qs(urlsplit(self.path).query)
                                 .get("v") or ["-1"])[0])
                except ValueError:
                    since = -1
                version, mood = (voice.wait(since) if voice is not None
                                 else (0, "neutral"))
                try:
                    self._reply(json.dumps({"v": version, "mood": mood})
                                .encode(), "application/json")
                except OSError:
                    pass  # the page went away while it was waiting
                return
            if self.path.split("?")[0] == SLIDES_PATH:
                self._reply(json.dumps(
                    {"count": how_many() if kind == "folder" else 0}
                ).encode(), "application/json")
                return
            if self.path.split("?")[0] == WALLPAPER_PATH:
                if kind == "folder":
                    asked = parse_qs(urlsplit(self.path).query).get("i", ["0"])
                    try:
                        index = int(asked[0])
                    except ValueError:
                        index = 0
                    blob, sort = from_folder(index)
                    if blob is None:
                        self.send_error(404)
                        return
                    self._reply(blob, sort)
                    return
                if picture is None:
                    self.send_error(404)
                    return
                # The one thing here worth caching: it does not change while
                # the add-on runs, and a panel coming home should not fetch a
                # megabyte again.
                self._reply(picture, mime)
                return
            self._reply(page(), "text/html; charset=utf-8")

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as err:
        # An accessory must never cost the panels. Something else on the port,
        # or no permission to bind: say so and let every panel that wanted
        # this be told, rather than taking the add-on down with it.
        print(f"Launcher: could not listen on {port or 'any port'} ({err})",
              flush=True)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="launcher",
                     daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"
