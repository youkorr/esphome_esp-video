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
import math
import mimetypes
import os
import threading
import unicodedata
import urllib.request
from urllib.parse import parse_qs, urlsplit

import logos

try:
    import files as library
except ImportError:  # an accessory: a build without it has no Files page
    library = None

# Inside the add-on's own container, which is where the senders run too, so
# nothing of this is reachable from the network.
PORT = 8099
ADDRESS = f"http://127.0.0.1:{PORT}/"
# What somebody writes in a panel's url: to be sent here.
KEYWORD = "launcher"
# Where the wallpaper is served from when it is a file on disk rather than an
# address. One path, so the page can name it before the file has been read.
WALLPAPER_PATH = "/wallpaper"
# A link whose url is this word opens the launcher's own Files page.
FILES_KEYWORD = "files"
FILES_HREF = "/files"
# Where the page says a wallpaper would not play. A video that cannot be
# decoded paints NOTHING -- the blank rectangle this project forbids -- and
# the page has no other way to reach a log somebody reads.
REPORT_PATH = "/report"
# What the page asks to find out how many pictures the folder holds now.
SLIDES_PATH = "/slides.json"
# The screen saver: the same server's pictures, with no links. The sender
# shows it in a page of its own after a while without a touch.
SAVER_PATH = "/saver"
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
    ("\U0001F4C1", "dossier dossiers fichiers fichier folder folders files "
                   "file explorateur explorer"),
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
    # Accents are not required: "téléchargement" and "telechargement" are
    # the same word to whoever types it, and the list spells them without.
    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower())
                    if unicodedata.category(c) != "Mn")
    glyph = ICONS.get(text.lower(), ICONS.get(plain, text))
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
 /* The screen's own Wi-Fi and Bluetooth, small, in a row of their own at
    the top RIGHT of the page, where a telephone or a tablet puts them --
    asked for top left first, then corrected: "c'etait le mieux en haut a
    droite et reduit sa taille". Right whatever launcher_align says: a
    status bar is not part of the clock. Hidden until the add-on has
    something to say. */
 .st { display: flex; align-items: center; gap: .5em; margin: 0 0 .3em;
       justify-content: flex-end; color: var(--ink);
       font-size: clamp(12px, 1.8vw, 17px); }
 .st:not(:has(span:not([hidden]))) { display: none; }
 .st span[hidden] { display: none; }
 .st svg { width: 1.3em; height: 1.3em; fill: none; stroke: currentColor;
           stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round;
           display: block; }
 .st .net .w1 { fill: currentColor; stroke: none; }
 /* Fewer bars: the arcs beyond them faint, the way a telephone draws it. */
 .st .net[data-bars="1"] .w2, .st .net[data-bars="1"] .w3,
 .st .net[data-bars="1"] .w4, .st .net[data-bars="2"] .w3,
 .st .net[data-bars="2"] .w4, .st .net[data-bars="3"] .w4 { opacity: .28; }
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

# The screen saver's own layout: the page of links with no links, the clock
# low on the left the way a lock screen puts it, larger, and in white with a
# shade under it, because it sits on a photograph rather than on a card. Asked
# for as "comme un ecran de veille", and laid out after what a PC lock screen
# and a tablet's photo frame show: the picture, the time, the date, and the
# weather as one small extra. The picture is neither blurred nor dimmed -- a
# photograph frame that darkens its photographs is not one -- so the text
# brings its own shade.
SAVER_CSS = """ main { display: none; }
 header { position: fixed; left: 5vw; right: 5vw; bottom: 6vh; padding: 0;
          color: #fff; text-shadow: 0 .08em .5em rgba(0, 0, 0, .6); }
 header::before { content: ""; position: fixed; inset: auto 0 0 0;
                  height: 45vh; z-index: -1; pointer-events: none;
                  background: linear-gradient(transparent, rgba(0, 0, 0, .45)); }
 .now { align-items: flex-end; }
 .time { font-size: clamp(56px, 14vw, 150px); font-weight: 250; }
 .date { color: #fff; font-size: clamp(18px, 3.2vw, 32px); }
 .wx { font-size: clamp(20px, 3.6vw, 34px); }
 .wx .out { color: #fff; }
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
        // The face dresses for the same reading, when there is a face.
        if (w && window.portallAvatar) window.portallAvatar.weather(w.avatar || '');
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

# A tile is opened BY THE SENDER, not by the page, when a sender is there.
#
# Reported as Unraid asking for its password every time its tile was
# pressed, with "stay signed in" ticked and nothing restarted. Unraid's login
# cookie is SameSite=Strict (session_set_cookie_params and
# session.cookie_samesite in its local_prepend.php, read rather than
# assumed), and a browser does not send a Strict cookie on a navigation that
# starts on ANOTHER site -- which a tile on this page, served from
# 127.0.0.1, always is. Measured on the shipped browser against a site on a
# different host: a click on the tile and a `location.href` both arrive
# WITHOUT the cookie; the same address opened by the sender with page.goto,
# which the browser treats like an address typed into the bar, arrives WITH
# it. So a tile is the one place on a panel where a login that works
# everywhere else looked forgotten.
#
# The sender puts `__udispFollow` on the page; a tap or OK on a tile hands
# it the address and the sender navigates. With no sender -- this page open
# in an ordinary browser -- the link is an ordinary link and nothing changes.
# The press look is untouched: PRESS_JS runs on pointerdown, before this.
#
# And it is handed over on POINTERDOWN, not on the click. The sender holds a
# replayed tap down for PRESS_HOLD_S (80 ms) so that a page's :active look is
# caught by a picture, and only then lets go -- so waiting for the click
# started every tile 80 ms and a mouseup's round trip late. Measured with a
# finger sent up a fake panel's return channel (tools/checklinkspeed.py
# --tap): about 150 ms from the finger lifting to the first picture of the
# new page, against about 60 now. Nothing is lost by not waiting: the sender
# replays a tap only once the finger has lifted without dragging, so a
# pointerdown here is already a decided tap, and the launcher's own press look
# is a class PRESS_JS holds for 200 ms, which stays on screen until the new
# page can paint. The click that follows is swallowed, or it would open the
# tile a second time; OK on a remote is a click with no pointerdown, and is
# handed over there as before.
FOLLOW_JS = """<script>
(function () {
  var handed = 0;
  function tileOf(e) {
    var tile = e.target.closest && e.target.closest('a.tile');
    return tile && /^https?:/.test(tile.href) ? tile : null;
  }
  document.addEventListener('pointerdown', function (e) {
    if (e.defaultPrevented || e.button !== 0 || !window.__udispFollow) return;
    var tile = tileOf(e);
    if (!tile) return;
    handed = Date.now();
    window.__udispFollow(tile.href);
  });
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || !window.__udispFollow) return;
    var tile = tileOf(e);
    if (!tile) return;
    e.preventDefault();
    if (Date.now() - handed < 1000) return;
    window.__udispFollow(tile.href);
  });
})();
</script>"""

# The avatar: a small face that lives on the launcher, the size of one of its
# buttons, in the bottom right corner until somebody moves it.
#
# Three rules decide how it behaves, and each is this project's rather than
# EMO's:
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
 /* No card behind it: the face has a head of its own, drawn in the SVG, and
    the corners of the box around it are left to the page. The box does not
    take a touch -- only what is painted does -- so a tile peeking out from
    under a corner of it can still be pressed. */
 #av {
   position: fixed; z-index: 50; width: 26vmin; height: %(height)s;
   right: 3vmin; bottom: 3vmin; %(place)s
   pointer-events: none;
   touch-action: none; user-select: none; -webkit-user-select: none;
 }
 #av svg {
   width: 100%%; height: 100%%; display: block; overflow: visible;
   pointer-events: none;
   filter: drop-shadow(0 .6vmin 1vmin rgba(0,0,0,.45));
 }
 #av svg > * { pointer-events: visiblePainted; }
 #av .grid { pointer-events: none; }
 /* An expression is the shape of two eyes, eased over 120 ms because
    somebody caused it. A blink and a glance happen by themselves every few
    seconds, and eased they cost about fourteen pictures each where a snap
    costs two -- measured, 87 pictures in twenty seconds of a still launcher
    against one without the face. So .pb, which carries both, has no
    transition at all. */
 #av .pr { transition: d .12s ease, fill .12s ease; }
 #av .look {
   transform: translate(calc(var(--lx, 0px) * 1.6),
                        calc(var(--ly, 0px) * 1.4));
 }
 /* --sl and --sr: the eye nearer to where it looks grows and the other one
    shrinks, the way EMO's and Cozmo's do -- a head turning, drawn on a flat
    screen. A blink squashes each eye about its own middle. */
 #av .pb { transform-box: fill-box; transform-origin: center; }
 #av .pb.l { transform: scale(var(--sl, 1)); }
 #av .pb.r { transform: scale(var(--sr, 1)); }
 #av.blink .pb.l { transform: scale(var(--sl, 1)) scaleY(.08); }
 #av.blink .pb.r { transform: scale(var(--sr, 1)) scaleY(.08); }
 #av[data-px="angry"] .pr { fill: url(#pxred); }
 #av[data-wx="cold"] .pr { fill: url(#pxcold); }
 /* What is drawn instead of the eyes, or beside them. */
 #av .px-x { display: none; }
 #av[data-px="laugh"] .px-laugh, #av[data-px="love"] .px-love,
 #av[data-px="dizzy"] .px-dizzy, #av[data-px="speak"] .px-talk,
 #av[data-px="sad"] .px-tear { display: inline; }
 #av[data-px="laugh"] .look, #av[data-px="love"] .look,
 #av[data-px="dizzy"] .look { display: none; }
 /* Its headphones light up while it listens. */
 #av .pear { fill: #141a26; }
 #av[data-px="listen"] .pear { fill: #35e3ff; filter: url(#pxglow); }
 /* What it carries, each shown by one attribute on the box and nothing else:
    the weather (data-wx), what the voice assistant is doing (data-px) and
    the night (the sleepy mood). A change is a snap, like a blink. */
 #av .acc { display: none; }
 #av[data-wx="rain"] .acc.rain, #av[data-wx="snow"] .acc.flake,
 #av[data-wx="cold"] .acc.flake, #av[data-wx="hot"] .acc.sweat,
 #av[data-px="think"] .acc.dots,
 #av[data-mood="sleepy"] .acc.zzz { display: inline; }
 /* The greeting takes the corner the weather and the voice use, for the
    three seconds it lasts. */
 #av.hello .acc.hello { display: inline; }
 #av.hello .acc.rain, #av.hello .acc.flake, #av.hello .acc.dots,
 #av.hello .acc.zzz, #av.hello .acc.sweat { display: none; }
"""

# The face: a little robot whose face is a screen, drawn after EMO
# (LivingAI's desk robot), which the household asked for by name -- "il doit
# ressembler a EMO". Not EMO: its look and its name are LivingAI's, and its
# animations are not published. What is borrowed is what every screen robot
# shares and what makes EMO read as EMO from across a room: a dark head that
# is all screen, headphones over it, and two big glowing eyes that ARE the
# expression -- they change shape rather than having lids drawn over them,
# and the one nearer to where it looks grows while the other shrinks.
#
# The idea of an eye as numbers is Cozmo's, as RoboEyes and Espressif's own
# expressive_eyes component (espp) write it down: each eye is a rounded box
# with a width, a height, a roundness and a place, whose top edge can come
# down at either corner (cross, sad, sleepy) and whose bottom edge can bow up
# until the eye is a ^ (happy). Every expression below is one line of those
# numbers, turned into a path HERE, so the page only swaps strings and a new
# expression costs a line rather than a drawing. Every path has the same
# commands in the same order, which is what lets the browser ease from one to
# the next.
#
# Drawn on the button's own 3:2 -- 150 x 100 -- with room left round it: the
# viewBox runs from -12 to 162 across and -24 to 118 down, so the headphones
# and what it carries (a cloud, a bubble, the z's) sit outside the head.
AVATAR_VIEW = "-12 -24 174 142"
# The box's height for a 26vmin width, from the viewBox above.
AVATAR_HEIGHT = "21.22vmin"

FACE_EYES = ((51, 54), (99, 54))
FACE_BLUE = ("#a5f3ff", "#2bc4ff")
FACE_RED = ("#ffb4a8", "#ff4d5e")
FACE_COLD = ("#e0f5ff", "#9fd3ff")

# Each eye: w, h and round (its corners) in units of the drawing; dx, dy where it
# moved to; ti / to how far the top edge comes down at the corner nearer the
# nose and at the outer one, and arc how far the bottom edge bows up in the
# middle -- all three as fractions of the eye's height. An eye named in "l"
# or "r" takes those numbers on top.
EYE = {"w": 32, "h": 38, "round": 11, "dx": 0, "dy": 0, "ti": 0, "to": 0, "arc": 0}
_HAPPY = {"w": 34, "h": 26, "round": 13, "arc": .72, "dy": -2}
EXPRESSIONS = {
    "neutral": {},
    "happy": _HAPPY,
    "speak": {"w": 34, "h": 30, "round": 13, "arc": .42, "dy": -5},
    "wink": {"r": dict(_HAPPY)},
    "surprised": {"w": 37, "h": 44, "round": 17},
    "listen": {"w": 34, "h": 41, "round": 13, "dy": -3},
    "think": {"w": 30, "h": 30, "dx": 6, "dy": -7, "ti": .18},
    "angry": {"h": 34, "ti": .5, "dy": 2},
    "sad": {"h": 34, "to": .45, "dy": 4},
    "tired": {"h": 36, "ti": .58, "to": .58, "dy": 4},
    "suspicious": {"dx": 7, "l": {"ti": .55, "to": .5},
                   "r": {"ti": .2, "to": .2}},
    "hot": {"ti": .32, "to": .32, "dy": 2},
    # Drawn instead of eyes (the sheet hides the eyes for them), so the shape
    # they leave behind is only where the next expression eases from.
    "laugh": _HAPPY, "love": {}, "dizzy": {},
}


def _eye_numbers(name, side):
    want = EXPRESSIONS[name]
    numbers = dict(EYE)
    numbers.update({k: v for k, v in want.items() if k not in ("l", "r")})
    numbers.update(want.get(side, {}))
    return numbers


def eye_path(side, name):
    """The outline of one eye in one expression, as an SVG path.

    Always M L Q L Q Q Q L Q Z, whatever the numbers: a path the browser can
    ease into another must have the same commands in the same order."""
    n = _eye_numbers(name, side)
    cx, cy = FACE_EYES[0 if side == "l" else 1]
    x, y, w, h = cx + n["dx"], cy + n["dy"], n["w"], n["h"]
    left, right, top, bottom = x - w / 2, x + w / 2, y - h / 2, y + h / 2
    # The nose is to the right of the left eye and to the left of the right.
    down_l = n["ti"] if side == "r" else n["to"]
    down_r = n["to"] if side == "r" else n["ti"]
    tl, tr = (left, top + h * down_l), (right, top + h * down_r)
    br, bl = (right, bottom), (left, bottom)
    r = min(n["round"], w / 2.2, (bottom - max(tl[1], tr[1])) / 2.2)

    def toward(a, b, d):
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
        return (a[0] + (b[0] - a[0]) * d / length,
                a[1] + (b[1] - a[1]) * d / length)

    # The bottom edge bows up through a control point twice as high as the
    # bow wanted, which is where a quadratic puts its middle.
    lift = n["arc"] * h * 2
    ctrl = (x, bottom - lift)
    points = [
        ("M", toward(tl, tr, r)), ("L", toward(tr, tl, r)),
        ("Q", tr, toward(tr, br, r)), ("L", toward(br, tr, r)),
        ("Q", br, toward(br, ctrl, r)), ("Q", ctrl, toward(bl, ctrl, r)),
        ("Q", bl, toward(bl, tl, r)), ("L", toward(tl, bl, r)),
        ("Q", tl, toward(tl, tr, r)),
    ]
    out = []
    for step in points:
        out.append(step[0] + " ".join(f"{p[0]:.1f} {p[1]:.1f}"
                                      for p in step[1:]))
    return " ".join(out) + " Z"


def _face_spiral(cx, cy):
    points = []
    turn = 0.0
    while turn < 4 * math.pi:
        r = 1.15 * turn
        points.append(f"{cx + r * math.cos(turn):.1f} {cy + r * math.sin(turn):.1f}")
        turn += 0.3
    return "M" + " L".join(points)


def _face_heart(cx, cy):
    return (f"M{cx} {cy + 13} C{cx - 22} {cy - 1} {cx - 11} {cy - 19} {cx} {cy - 7} "
            f"C{cx + 11} {cy - 19} {cx + 22} {cy - 1} {cx} {cy + 13} Z")


def _face_eye(side):
    return (f'<g class="pb {side}"><path class="pr" d="{eye_path(side, "neutral")}" '
            f'fill="url(#pxeye)" filter="url(#pxglow)"/></g>')


def _face_gradient(name, colours):
    top, bottom = colours
    return (f'<linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>')


(_lx, _ly), (_rx, _ry) = FACE_EYES
AVATAR_FACE = (
    """
 <defs>
  <filter id="pxglow" x="-50%" y="-50%" width="200%" height="200%">
   <feGaussianBlur stdDeviation="2.2" result="b"/>
   <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="pxscr"><rect x="13" y="13" width="124" height="82" rx="24"/></clipPath>
  <pattern id="pxgrid" width="2.6" height="2.6" patternUnits="userSpaceOnUse">
   <path d="M0 0 H2.6 M0 0 V2.6" stroke="#03060a" stroke-width=".55"/>
  </pattern>
  <linearGradient id="pxhead" x1="0" y1="0" x2="0" y2="1">
   <stop offset="0" stop-color="#2a3243"/><stop offset="1" stop-color="#0c1018"/>
  </linearGradient>
  <linearGradient id="pxcup" x1="0" y1="0" x2="1" y2="0">
   <stop offset="0" stop-color="#4a5570"/><stop offset="1" stop-color="#262e40"/>
  </linearGradient>"""
    + _face_gradient("pxeye", FACE_BLUE) + _face_gradient("pxred", FACE_RED)
    + _face_gradient("pxcold", FACE_COLD)
    + """
 </defs>
 <path d="M-2 44 C-4 -22 154 -22 152 44" stroke="#2e3648" stroke-width="8"
  stroke-linecap="round" fill="none"/>
 <path d="M3 30 C6 -10 144 -10 147 30" stroke="#5b6782" stroke-width="1.6"
  stroke-linecap="round" fill="none" opacity=".7"/>
 <rect x="4" y="4" width="142" height="100" rx="32" fill="url(#pxhead)"
  stroke="#3d4760" stroke-width="1.5"/>
 <rect x="13" y="13" width="124" height="82" rx="24" fill="#03060a"/>
 <rect x="-12" y="30" width="19" height="48" rx="9" fill="url(#pxcup)"/>
 <rect x="143" y="30" width="19" height="48" rx="9" fill="url(#pxcup)"/>
 <rect class="pear" x="-6" y="40" width="7" height="28" rx="3.5"/>
 <rect class="pear" x="149" y="40" width="7" height="28" rx="3.5"/>
 <g clip-path="url(#pxscr)">
  <g class="look">"""
    + _face_eye("l") + _face_eye("r")
    + f"""</g>
  <path class="px-x px-laugh" d="M{_lx - 10} {_ly - 11} L{_lx + 9} {_ly} L{_lx - 10} {_ly + 11}
   M{_rx + 10} {_ry - 11} L{_rx - 9} {_ry} L{_rx + 10} {_ry + 11}" stroke="url(#pxeye)"
   stroke-width="7" stroke-linecap="round" stroke-linejoin="round" fill="none"
   filter="url(#pxglow)"/>
  <g class="px-x px-love" fill="#ff5c9a" filter="url(#pxglow)">
   <path d="{_face_heart(_lx, _ly)}"/><path d="{_face_heart(_rx, _ry)}"/></g>
  <g class="px-x px-dizzy" stroke="#5fdcff" stroke-width="3" fill="none"
   stroke-linecap="round" filter="url(#pxglow)">
   <path d="{_face_spiral(_lx, _ly)}"/><path d="{_face_spiral(_rx, _ry)}"/></g>
  <ellipse class="px-x px-talk" cx="75" cy="84" rx="9" ry="2" fill="url(#pxeye)"
   filter="url(#pxglow)"/>
  <path class="px-x px-tear" d="M{_lx - 12} {_ly + 18} q4 8 0 11.5 q-4 -3.5 0 -11.5z"
   fill="#7cc8ff"/>
  <rect class="grid" x="13" y="13" width="124" height="82" fill="url(#pxgrid)"
   opacity=".55"/>
 </g>
 <path d="M24 20 Q50 15 74 18" stroke="#fff" stroke-opacity=".07" stroke-width="5"
  stroke-linecap="round" fill="none"/>""")

# What it carries, drawn once and shown by an attribute, in the top right
# corner outside the head.
AVATAR_EXTRAS = """
 <g class="acc rain">
  <g fill="#94a3b8"><circle cx="138" cy="-14" r="6"/>
   <circle cx="146" cy="-16" r="7"/><circle cx="154" cy="-12" r="5"/>
   <rect x="134" y="-14" width="24" height="7" rx="3.5"/></g>
  <path d="M139 -3 l-1.5 4 M147 -3 l-1.5 4 M155 -3 l-1.5 4" stroke="#60a5fa"
        stroke-width="1.8" stroke-linecap="round"/>
 </g>
 <path class="acc flake" d="M148 -21 V-3 M140.2 -16.5 L155.8 -7.5
   M140.2 -7.5 L155.8 -16.5" stroke="#bfdbfe" stroke-width="1.6"
   stroke-linecap="round"/>
 <path class="acc sweat" d="M148 -20 q5 8 0 13 q-5 -5 0 -13z" fill="#7cc8ff"/>
 <g class="acc dots" fill="#35e3ff">
  <circle cx="134" cy="-2" r="2.4"/><circle cx="142" cy="-9" r="3.2"/>
  <circle cx="152" cy="-17" r="4"/></g>
 <g class="acc hello">
  <path d="M104 -24 H156 A6 6 0 0 1 162 -18 V-12 A6 6 0 0 1 156 -6 H116
   L110 -1 L111 -6 H104 A6 6 0 0 1 98 -12 V-18 A6 6 0 0 1 104 -24 Z"
   fill="#f8fafc"/>
  <text x="130" y="-11.5" font-size="9" font-family="sans-serif"
   font-weight="700" fill="#0f172a" text-anchor="middle">Hello</text>
 </g>
 <g class="acc zzz" fill="#94a3b8" font-family="sans-serif" font-weight="700">
  <text x="138" y="-2" font-size="10">z</text>
  <text x="146" y="-10" font-size="13">z</text>
  <text x="155" y="-17" font-size="8">z</text>
 </g>"""

# Which expression the face shows, and what a finger does to it. Listens to
# the attributes the shared script sets -- data-mood, data-voice, data-wx --
# and writes data-px, which the sheet above draws from.
FACE_JS = """<script>
(function () {
  var box = document.getElementById('av');
  if (!box || !window.portallAvatar) return;
  var api = window.portallAvatar;
  var SHAPES = %(shapes)s;
  function pick() {
    var mood = box.dataset.mood, voice = box.dataset.voice;
    if (mood === 'surprised' && voice === 'surprised') return 'listen';
    if (mood === 'thinking') return 'think';
    if (mood === 'happy' && voice === 'happy') return 'speak';
    if (mood === 'sleepy') return 'tired';
    if (mood === 'neutral' && box.dataset.wx === 'hot') return 'hot';
    return SHAPES[mood] ? mood : 'neutral';
  }
  /* Speaking opens and closes a small mouth under its eyes, four times a
     second and only while the voice assistant is answering -- the one
     animation that runs by itself, and it runs for as long as the answer
     does. A snap rather than an ease, like a blink: an eased mouth would be
     a run of pictures for every syllable. Wider as it closes. */
  var talking = 0, said = 0;
  var OPEN = [2, 4.5, 7, 3, 6, 2.5];
  function mouth(moving) {
    var m = box.querySelector('.px-talk');
    clearInterval(talking);
    if (!m || !moving) return;
    function step() {
      var next = said;
      while (next === said) next = Math.floor(Math.random() * OPEN.length);
      said = next;
      m.setAttribute('ry', OPEN[said]);
      m.setAttribute('rx', 11 - OPEN[said] * .6);
    }
    step();
    talking = setInterval(step, 250);
  }
  var shown = '';
  function draw() {
    var now = pick();
    if (now === shown) return;
    shown = now;
    box.dataset.px = now;
    ['l', 'r'].forEach(function (side) {
      box.querySelector('.pb.' + side + ' .pr').style.d =
        'path("' + SHAPES[now][side] + '")';
    });
    mouth(now === 'speak');
  }
  new MutationObserver(draw).observe(box, {attributes: true,
    attributeFilter: ['data-mood', 'data-voice', 'data-wx']});
  draw();

  /* What a finger does to it. One tap is a smile, a wink or a heart, at
     random; three in a second and a half make it laugh; five in three
     seconds make it cross. Registered after the shared handler, so this
     one's mood is the one that stands. */
  var taps = [];
  box.addEventListener('click', function () {
    var t = Date.now();
    taps.push(t);
    taps = taps.filter(function (x) { return t - x < 3000; });
    var recent = taps.filter(function (x) { return t - x < 1500; }).length;
    if (taps.length >= 5) api.set('angry', 2500);
    else if (recent >= 3) api.set('laugh', 2500);
    else api.set(['happy', 'wink', 'love'][Math.floor(Math.random() * 3)], 2500);
  });
  /* Dragged fast, it ends up dizzy: the travel of one drag over its time,
     judged when the drag ends -- after the shared handler's own smile. */
  var travel = 0, began = 0, last = null, done = 0;
  window.addEventListener('wheel', function () {
    var r = box.getBoundingClientRect(), t = Date.now();
    if (last && t - began < 2000) {
      travel += Math.abs(r.left - last[0]) + Math.abs(r.top - last[1]);
    } else {
      travel = 0; began = t;
    }
    last = [r.left, r.top];
    clearTimeout(done);
    done = setTimeout(function () {
      var seconds = Math.max(.1, (Date.now() - began - 700) / 1000);
      if (travel / seconds > 1500) api.set('dizzy', 2000);
      travel = 0; last = null;
    }, 700);
  }, true);
  /* Listened and heard nothing it could use: the voice assistant went from
     listening straight back to idle, with no thinking in between. */
  var voiceWas = box.dataset.voice;
  new MutationObserver(function () {
    var now = box.dataset.voice;
    if (voiceWas === 'surprised' && now === 'neutral') api.set('suspicious', 2000);
    voiceWas = now;
  }).observe(box, {attributes: true, attributeFilter: ['data-voice']});
})();
</script>""" % {"shapes": json.dumps(
    {name: {side: eye_path(side, name) for side in ("l", "r")}
     for name in EXPRESSIONS}, separators=(",", ":"))}


def avatar_html(wx=""):
    """The face, starting with the weather it was served with."""
    return (f'<div id="av" data-mood="neutral" data-voice="neutral" '
            f'data-px="neutral" data-wx="{html.escape(wx)}" '
            f'aria-hidden="true">\n<svg viewBox="{AVATAR_VIEW}">'
            + AVATAR_FACE + AVATAR_EXTRAS + "\n</svg></div>")


# What the weather puts on the face: a cloud when it rains, a snowflake when
# it snows or freezes, sunglasses when it is clear and hot -- or nothing.
# Home Assistant's own condition names, and a temperature in whatever unit it
# gave. 25 and 5 degrees are this page's choice, written here and nowhere else.
HOT_C = 25.0
COLD_C = 5.0
RAIN = ("rainy", "pouring", "lightning", "lightning-rainy", "hail")
SNOW = ("snowy", "snowy-rainy")
CLEAR = ("sunny", "partlycloudy")


def avatar_weather(state):
    """'hot', 'rain', 'snow', 'cold' or '' for a weather reading."""
    if not state:
        return ""
    condition = str(state.get("condition") or "").lower()
    if condition in RAIN:
        return "rain"
    if condition in SNOW:
        return "snow"
    try:
        degrees = float(state.get("temperature"))
    except (TypeError, ValueError):
        return ""
    if "F" in str(state.get("unit") or "").upper():
        degrees = (degrees - 32.0) * 5.0 / 9.0
    if degrees >= HOT_C and condition in CLEAR:
        return "hot"
    if degrees <= COLD_C:
        return "cold"
    return ""


AVATAR_JS = """<script>
(function () {
  var box = document.getElementById('av');
  if (!box) return;
  var back = 0, rest = 'neutral', voice = 'neutral', base = 'neutral';
  /* One expression at a time. A passing one -- a tap, a move -- goes back by
     itself to the one that stands: what the voice assistant is doing while
     it does something, and otherwise the time of day. */
  function set(mood, ms) {
    box.dataset.mood = mood || rest;
    clearTimeout(back);
    if (ms) back = setTimeout(function () { set(rest); }, ms);
  }
  function settle() {
    rest = voice !== 'neutral' ? voice : base;
    set(rest);
  }
  function stand(mood) {
    voice = mood || 'neutral';
    box.dataset.voice = voice;
    settle();
  }
  /* The night: from 22 h to 7 h the face rests with its eyes half shut. Asked
     once a minute, which is what the clock beside it already does, and a
     change is one snap -- a sleepy face costs nothing more than an awake one,
     and less, since it does not glance about. */
  function night() {
    var h = new Date().getHours();
    var now = (h >= 22 || h < 7) ? 'sleepy' : 'neutral';
    if (now !== base) { base = now; settle(); }
    setTimeout(night, 60000);
  }
  night();

  /* It looks at what somebody is doing: where a finger lands, and the tile a
     remote or a gamepad has just chosen. A turn of the eyes toward it for a
     second and a half, which repaints the eyes twice and nothing else. */
  var looking = 0;
  /* Looking to one side, the eye on that side grows and the other shrinks,
     the way EMO's and Cozmo's do -- a head turning, on a flat screen. Only
     sideways: up and down is just looking. */
  function curious(dx, dy) {
    var side = Math.abs(dx) > 2 * Math.abs(dy) ? (dx < 0 ? -1 : 1) : 0;
    box.style.setProperty('--sl', side < 0 ? '1.18' : side > 0 ? '.84' : '1');
    box.style.setProperty('--sr', side > 0 ? '1.18' : side < 0 ? '.84' : '1');
  }
  function lookAt(x, y) {
    var r = box.getBoundingClientRect();
    var dx = x - (r.left + r.width / 2), dy = y - (r.top + r.height / 2);
    var far = Math.sqrt(dx * dx + dy * dy) || 1;
    box.style.setProperty('--lx', (5 * dx / far).toFixed(1) + 'px');
    box.style.setProperty('--ly', (4 * dy / far).toFixed(1) + 'px');
    curious(dx, dy);
    clearTimeout(looking);
    looking = setTimeout(function () {
      box.style.setProperty('--lx', '0px');
      box.style.setProperty('--ly', '0px');
      curious(0, 0);
    }, 1500);
  }
  function lookAtTile(tile) {
    var r = tile.getBoundingClientRect();
    lookAt(r.left + r.width / 2, r.top + r.height / 2);
  }
  document.addEventListener('focusin', function (e) {
    var tile = e.target && e.target.closest && e.target.closest('a.tile');
    if (tile) lookAtTile(tile);
  }, true);

  /* A link asked for by voice, when this page is the one showing: it looks
     at the tile, the tile goes down, and the page opens it itself -- which
     is exactly what a tap on that tile does, so everything after it is the
     path a finger already takes. False when there is no such tile, and the
     sender opens the address the ordinary way. */
  function open(url) {
    var want;
    try { want = new URL(url, location.href).href.replace(/\/$/, ''); }
    catch (e) { return false; }
    var tiles = document.querySelectorAll('a.tile');
    for (var i = 0; i < tiles.length; i++) {
      if (tiles[i].href.replace(/\/$/, '') !== want) continue;
      var tile = tiles[i];
      lookAtTile(tile);
      set('happy');
      setTimeout(function () { tile.classList.add('press'); }, 250);
      /* Through the sender when there is one, for the reason FOLLOW_JS
         gives: a site's Strict login is sent only then. */
      setTimeout(function () {
        if (window.__udispFollow) window.__udispFollow(tile.href);
        else location.href = tile.href;
      }, 750);
      return true;
    }
    return false;
  }
  function weather(kind) { box.dataset.wx = kind || ''; }
  /* The panel's screen came back on: a smile and a word, the way EMO says
     hello when somebody comes back. The word is the panel's own language
     (locale: in the add-on) and the time of day, and the sender asks for
     it when the board says it is awake again. */
  var greeting = 0;
  function greet() {
    var fr = /^fr/i.test(navigator.language || '');
    var h = new Date().getHours();
    var evening = h >= 18 || h < 5;
    box.querySelector('.acc.hello text').textContent =
      fr ? (evening ? 'Bonsoir' : 'Bonjour') : 'Hello';
    box.classList.add('hello');
    set('happy', 3000);
    clearTimeout(greeting);
    greeting = setTimeout(function () { box.classList.remove('hello'); }, 3000);
    return true;
  }
  window.portallAvatar = {set: set, stand: stand, open: open,
                          weather: weather, greet: greet};

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
      curious(x, 0);
      setTimeout(function () {
        box.style.setProperty('--lx', '0px');
        curious(0, 0);
      }, 1500);
    }
    setTimeout(glance, 10000 + Math.random() * 8000);
  }
  setTimeout(glance, 9000);

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
  window.addEventListener('pointerdown', function (e) {
    armed = on(e);
    if (!armed) lookAt(e.clientX, e.clientY);
  }, true);
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
            f"top: calc((100vh - {AVATAR_HEIGHT}) * {y:.4f});")


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
STATUS_PATH = "/status.json"

# The screen's own Wi-Fi and Bluetooth, beside the weather: shown when they
# are connected and not otherwise, the way a telephone's status bar does it.
# Drawn rather than taken from a font, because no emoji is the Bluetooth rune
# and a font's Wi-Fi glyph has no bars. Read by the add-on from Home
# Assistant -- the page only asks /status.json, which never leaves this
# machine -- every ten seconds; writing the same thing back paints nothing.
WIFI_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path class="w4" d="M2 9.5a15 15 0 0 1 20 0"/>'
    '<path class="w3" d="M5 12.8a10.5 10.5 0 0 1 14 0"/>'
    '<path class="w2" d="M8 16a6 6 0 0 1 8 0"/>'
    '<circle class="w1" cx="12" cy="19" r="1.6"/></svg>')
BLUETOOTH_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M7 7l10 10-5 5V2l5 5L7 17"/></svg>')
STATUS_JS = """<script>
(function () {
  var who = new URLSearchParams(location.search).get('panel') || '';
  var net = document.getElementById('net'), bt = document.getElementById('bt');
  function show(s) {
    var w = s && s.wifi;
    net.hidden = !(w && w.on);
    net.setAttribute('data-bars', w && w.bars ? w.bars : 4);
    bt.hidden = !(s && s.bluetooth);
  }
  function ask() {
    fetch('%(path)s?panel=' + encodeURIComponent(who))
      .then(function (r) { return r.json(); }).then(show)
      .catch(function () {});
  }
  ask();
  setInterval(ask, 10000);
})();
</script>"""

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


# How much smaller the links are drawn, by name. Asked for from a 1280x800
# panel with a picture behind the links -- "la reduction des buttons et text"
# -- where the links covered what the picture was of. medium is the size they
# have always had.
TILE_SIZES = {"tiny": 0.65, "small": 0.8, "medium": 1.0}
TILE_BACKGROUNDS = ("solid", "transparent")


def _halo(tint):
    """The shadow that lifts a name off a picture: dark under light text,
    light under dark -- a dark shadow under black text is no help at all.
    With no colour chosen the text is the theme's ink, and the theme's own
    ground is the colour that stands out from it on either theme."""
    if not tint:
        return "color-mix(in srgb, var(--ground) 75%, transparent)"
    if tint:
        h = tint.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        if 0.2126 * r + 0.7152 * g + 0.0722 * b < 128:
            return "rgba(255, 255, 255, .7)"
    return "rgba(0, 0, 0, .6)"


def _tile_look(background="solid", size=DEFAULT_SIZE, text_color=FOLLOW_THEME):
    """The rules for the links' own look, or nothing when all are default.

    Asked for together, from a panel whose wallpaper the links hid: "une vraie
    transparence du bouton, tout en laissant l'icone avec sa propre couleur",
    and "la reduction des boutons et du texte, et la couleur du texte".

    SMALLER is a zoom on the grid rather than a second set of sizes. The
    icon, the name, the padding, the gap and the rounding all come down
    together, so a small button is the same button drawn smaller rather than
    a new layout to measure; and the browser's hit test follows a zoom, so a
    finger lands on the link it is on. A card keeps its column's width and
    only what is inside it shrinks.

    TRANSPARENT is transparent: no ground, no frame, no gradient, no blur and
    no shadow -- which on a transparent box would draw a dark halo round
    nothing. What is left is the icon in its own colours and the name, which
    gets a soft shadow so a white name still reads over a pale sky. The press
    and the ring a remote is on stay, because on a tile with nothing else to
    it they are the only way to see that it was touched or chosen.
    """
    out = []
    tint = PALETTES.get(str(text_color or "").lower())
    zoom = TILE_SIZES.get(str(size or DEFAULT_SIZE).lower(), 1.0)
    if zoom != 1.0:
        out.append(f" .group {{ zoom: {zoom:g}; }}")
    if str(background or "").lower() == "transparent":
        out.append(
            " main a.tile, main.buttons a.tile { background: transparent;"
            " background-image: none; border-color: transparent;"
            " box-shadow: none; backdrop-filter: none;"
            " -webkit-backdrop-filter: none; }\n"
            " main a.tile .icon { background: none; }\n"
            " main a.tile .name, main a.tile .desc {"
            f" text-shadow: 0 1px 3px {_halo(tint)}; }}\n"
            " main a.tile:active, main a.tile.press {"
            " background: color-mix(in srgb, var(--card) 55%, transparent); }\n"
            " main a.tile:focus-visible, main a.tile.chosen,"
            " main.buttons a.tile:focus-visible, main.buttons a.tile.chosen {"
            " border-color: var(--ring); box-shadow: 0 0 0 .5vmin var(--ring); }")
    if tint:
        # The description is faint by default and a colour given for the
        # text has to beat that too, so both are named and this comes later.
        out.append(f" main a.tile .name, main a.tile .desc {{ color: {tint}; }}")
    return out


def _bar(clock_size, clock_color, date_size, date_color, weather_size, align,
         focus_color=FOLLOW_THEME, weather_color=FOLLOW_THEME,
         tile_background="solid", tile_size=DEFAULT_SIZE,
         tile_text_color=FOLLOW_THEME):
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
    # The TEMPERATURE, not the sky: the sky is an emoji and a browser draws
    # those in their own colours whatever a rule says, while the number beside
    # it is text, faint by default, and the same two-rule race as the date --
    # .wx .out is (0,2,0) and this comes later in the sheet.
    out += _one_colour(".wx .out", weather_color)
    out += _one_focus(focus_color)
    out += _tile_look(tile_background, tile_size, tile_text_color)
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
           avatar=False, avatar_at=None, voice=False,
           weather_color=FOLLOW_THEME,
           tile_background="solid", tile_size=DEFAULT_SIZE,
           tile_text_color=FOLLOW_THEME, status=False,
           saver=False, saver_date=True):
    """The page, as one string.

    `saver` draws the screen saver instead of the page of links: the
    pictures alone, full screen, with the clock, the date, the weather and
    the face only where asked for, and not one link. See SAVER_CSS.

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
    for name, entries in ({} if saver else groups).items():
        tiles = "".join(
            TILE % {
                "url": html.escape(
                    FILES_HREF if str(entry.get("url", "")).strip().lower()
                    == FILES_KEYWORD else str(entry.get("url", "")),
                    quote=True),
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
    if status and not saver:
        now = (f'<div class="st"><span class="net" id="net" hidden>'
               f'{WIFI_SVG}</span><span class="bt" id="bt" hidden>'
               f'{BLUETOOTH_SVG}</span></div>') + now
    # Always, and not behind an option. It costs one listener that fires on a
    # key nobody presses unless there is a remote, and an option for it would
    # be one more thing to read past -- which this add-on has already had to
    # take five settings off the form for.
    # The saver has no tile to move between, press or follow: any touch on
    # it is the sender's, and ends it before the page could hear it.
    scripts = ("" if saver else KEYS_JS + PRESS_JS + FOLLOW_JS) + (
        CLOCK_JS if clock else "") + (
        WEATHER_JS % {"path": WEATHER_PATH} if weather is not None else "") + (
        STATUS_JS % {"path": STATUS_PATH} if status else "")

    # The bar's own rules, after the sheet above and before nothing: there is
    # no stylesheet field any more. It was offered first, on the argument that
    # one general mechanism beats a setting per thing -- and that argument is
    # the maintainer's convenience, not the household's. Homepage names the
    # size of each widget on a fixed scale and has no CSS field at all, which
    # is the shape this follows now.
    sheet = _bar(clock_size, clock_color, date_size, date_color,
                 weather_size, align, focus_color, weather_color,
                 tile_background, tile_size, tile_text_color)
    if saver:
        sheet += SAVER_CSS + ("" if saver_date else " .date { display: none; }\n")

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
        sheet += AVATAR_CSS % {"place": _avatar_place(avatar_at),
                               "height": AVATAR_HEIGHT}
        moving.append(avatar_html(avatar_weather(weather))
                      + AVATAR_JS % {"path": AVATAR_PATH} + FACE_JS)
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
        "groups": "".join(body) or ("" if saver else EMPTY),
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
        "blur": (BLURS.get(str(blur).lower(), "0px")
                 if has_wall and not saver else "0px"),
        # A photograph is nearly always too bright to read white text over, so
        # the dim is what makes a wallpaper usable rather than a decoration
        # somebody turns off again.
        "brightness": (f"{max(0, 100 - dim)}%" if has_wall and not saver
                       else "100%"),
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
          avatar=False, avatar_file=None, voice=None,
          weather_color=FOLLOW_THEME,
          tile_background="solid", tile_size=DEFAULT_SIZE,
          tile_text_color=FOLLOW_THEME, files_root=None, choice_file=None,
          status=None, saver_after=0, saver_clock=True, saver_date=True,
          saver_weather=True, saver_avatar=True):
    """Serve the page for as long as the add-on runs. Returns its address.

    One call is one launcher: its links, its look, its weather, its
    wallpaper and its slideshow, on a server of its own. A panel with a
    launcher of its own is given a call of its own on ANY_PORT, so nothing
    one panel's page shows or fetches is shared with another's -- the
    wallpaper and the weather included, which a page per panel on one shared
    server could not have kept apart.
    """
    # The wallpaper, worked out from what is configured -- or from a
    # picture or a video chosen on the panel's own Files page, which
    # wins until it is taken away. A function, so a choice made while
    # the add-on runs is shown without restarting it.
    kind = where = files = picture = addresses = None
    mime, mirrored = "application/octet-stream", False
    configured = (background, urls, motion)

    # With a screen saver the slideshow is the SAVER's, and the page of links
    # keeps its first picture still: the pictures changing behind the tiles
    # was a misreading of what was asked ("doit fonctionner comme un ecran de
    # veille ... sans les link"), and a picture that changes is a whole panel
    # on the wire every time. A delay of nought keeps it the way it was.
    try:
        saving = slideshow and float(saver_after) > 0
    except (TypeError, ValueError):
        saving = False

    def settle(chosen):
        nonlocal kind, where, files, picture, mime, mirrored, addresses
        nonlocal background, urls, motion
        background, urls, motion = configured
        if chosen:
            # Chosen to be seen, so a video or a GIF chosen moves.
            background, urls, motion = chosen, (), True
        picture, mime = None, "application/octet-stream"
        mirrored = False
        addresses = _addresses(urls)
        # With a screen saver, the slideshow's addresses are the saver's and
        # the page of links keeps its own wallpaper -- "cela remplace mon fond
        # ecran" was the addresses winning over it here, as they did when
        # the slideshow ran behind the tiles. Only a launcher given no
        # wallpaper of its own still shows the first of them, still.
        own_wall = saving and bool(str(background or "").strip())
        if addresses:
            print(f"Launcher: {len(addresses)} picture(s) by address"
                  + (", for the screen saver" if saving
                     else f", one every {every}s with a {fade}s fade"
                     if slideshow else ", showing the first")
                  + ". They are fetched by the panel's own browser, so the "
                    "machine serving them has to be reachable from here.",
                  flush=True)
        if addresses and not own_wall:
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

        # One picture is read once and held; a folder is read per request. A
        # holiday folder is gigabytes, and the add-on has no business holding it
        # -- while re-reading one file for every panel that comes home would be a
        # disk seek where there is no need for one.
        if not mirrored:
            picture, mime = None, "application/octet-stream"
        if kind == "file" and not chosen:
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
        elif kind == "file":
            # Chosen: served from disk a piece at a time, since it can be a
            # film and the add-on has no business holding it.
            print(f"Launcher: wallpaper {where}, chosen on the Files page",
                  flush=True)

    def chosen_path():
        """The file chosen on the Files page as the wallpaper, if any."""
        if library is None or not choice_file:
            return None, None
        ref = library.read_choice(choice_file)
        entry = library.find(files_root, ref) if ref else None
        return (ref, entry["path"]) if entry else (None, None)

    settle(chosen_path()[1])
    wall_rev = {"n": 0}

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

    def page(saver=False):
        state = weather() if weather is not None else None
        # The avatar's spot is part of the page: a panel coming home finds
        # the face where it was left, drawn there from the first frame rather
        # than jumping there after a script has run.
        key = (weather_block(state) if state else None, spot["at"],
               wall_rev["n"])
        which = "saver" if saver else "page"
        held = cache.get(which)
        if held is None or held[0] != key:
            shown = saver_weather if saver else True
            body = render(
                links, title, subtitle, theme, color,
                background, blur, dim, columns,
                (saver_clock or saver_date) if saver else clock,
                state if weather is not None and shown else None,
                clock_size, clock_color, date_size, date_color,
                weather_size, align,
                # Already sifted just above, so the page does not repeat
                # the complaint about an address that is not one.
                motion, slideshow and (saver or not saving), every, fade,
                # The saver takes the slideshow's addresses; the page of
                # links only when they are its wallpaper (settle() says).
                rescan, addresses if saver or kind == "urls" else (),
                mirrored, shape=tiles, focus_color=focus_color,
                avatar=avatar and (saver_avatar or not saver),
                avatar_at=spot["at"],
                voice=voice is not None,
                weather_color=weather_color,
                tile_background=tile_background, tile_size=tile_size,
                tile_text_color=tile_text_color,
                status=status is not None,
                saver=saver, saver_date=saver_date).encode()
            if saver and not saver_clock:
                # The clock line carries the date under it, so a saver with
                # the date and no time keeps the line and hides the time.
                body = body.replace(b"</style>",
                                    b" .time { display: none; }\n</style>", 1)
            held = cache[which] = (key, body)
        return held[1]

    # Once here, so a fault in the page is reported at startup rather than
    # the first time somebody comes home to it.
    page()
    if saving:
        page(saver=True)
        print(f"Launcher: the slideshow is a screen saver, after "
              f"{float(saver_after):g} min without a touch", flush=True)
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
                    json.dumps({"icon": sky, "text": temp,
                                "avatar": avatar_weather(state)}).encode(),
                    "application/json",
                )
                return
            if self.path.split("?")[0] == STATUS_PATH:
                # What the add-on last read for this screen, or nothing.
                who = (parse_qs(urlsplit(self.path).query).get("panel")
                       or [""])[0]
                state = status(who) if status is not None else None
                self._reply(json.dumps(state or {}).encode(),
                            "application/json")
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
            if self.path.split("?")[0] == SAVER_PATH:
                self._reply(page(saver=True), "text/html; charset=utf-8")
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
                if picture is None and kind == "file" and library is not None:
                    try:
                        library.send(self, where)
                    except OSError:
                        self.send_error(404)
                    return
                if picture is None:
                    self.send_error(404)
                    return
                # The one thing here worth caching: it does not change while
                # the add-on runs, and a panel coming home should not fetch a
                # megabyte again.
                self._reply(picture, mime)
                return
            if self.path.split("?")[0].startswith(FILES_HREF) \
                    and library is not None:
                self._files_get()
                return
            self._reply(page(), "text/html; charset=utf-8")

        def _files_get(self):
            route = self.path.split("?")[0]
            ref = (parse_qs(urlsplit(self.path).query).get("f") or [""])[0]
            chosen = chosen_path()[0]
            if route in (FILES_HREF, FILES_HREF + "/"):
                self._reply(library.list_page(files_root, chosen),
                            "text/html; charset=utf-8")
                return
            entry = library.find(files_root, ref)
            if entry is None:
                self.send_error(404)
                return
            if route == FILES_HREF + "/view":
                self._reply(library.view_page(entry, chosen),
                            "text/html; charset=utf-8")
            elif route == FILES_HREF + "/raw":
                try:
                    library.send(self, entry["path"])
                except OSError:
                    self.send_error(404)
            else:
                self.send_error(404)

        def do_POST(self):
            if library is None or not self.path.startswith(FILES_HREF + "/"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            asked = parse_qs(self.rfile.read(length).decode(errors="replace"))
            ref = (asked.get("ref") or [""])[0]
            what = self.path.split("?")[0][len(FILES_HREF) + 1:]
            entry = library.find(files_root, ref) if ref else None
            if what == "wallpaper" and entry is not None \
                    and entry["kind"] in library.WALLPAPER_KINDS \
                    and choice_file:
                library.write_choice(choice_file, entry["ref"])
                settle(entry["path"])
                wall_rev["n"] += 1
            elif what == "unwallpaper" and choice_file:
                library.write_choice(choice_file, None)
                settle(None)
                wall_rev["n"] += 1
            elif what == "delete" and entry is not None:
                if chosen_path()[0] == entry["ref"]:
                    library.write_choice(choice_file, None)
                    settle(None)
                    wall_rev["n"] += 1
                try:
                    os.remove(entry["path"])
                    print(f"Launcher: {entry['ref']} deleted from the Files "
                          f"page", flush=True)
                except OSError as err:
                    print(f"Launcher: could not delete {entry['ref']} ({err})",
                          flush=True)
            else:
                self.send_error(404)
                return
            self.send_response(204)
            self.end_headers()

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
