#!/usr/bin/env python3
"""Write the add-on's settings page in words a household can read.

WHY THIS EXISTS. The add-on's form showed its raw keys -- panels, links,
launcher, launchers, defaults, debug -- and was reported as not organised,
from somebody who finds Home Assistant hard: "je vois panel et launcher ce
n'est pas organise". Home Assistant's Supervisor reads translations/<lang>.yaml
beside config.yaml and its frontend shows each option's `name` and
`description` in place of the key, down into list entries and groups through
`fields` (supervisor/apps/validate.py SCHEMA_TRANSLATION_CONFIGURATION, and
the frontend's supervisor-app-config.ts, both read rather than remembered).

The keys do not change, so nothing stored has to move. Both languages come out
of the one table below, so a field cannot have a French name and no English
one; tools/checkaddon.py checks that every option has a name in both.
"""

import pathlib
import yaml

OUT = pathlib.Path(__file__).resolve().parent.parent / "portall" / "translations"
T = lambda fr, en, dfr=None, den=None, **fields: {"fr": (fr, dfr, fields), "en": (en, den, fields)}

def link_fields():
    return dict(
        name=T("Nom du bouton", "Button name", "Le texte écrit sur la tuile.", "The text written on the tile."),
        url=T("Adresse", "Address",
              "La page qui s'ouvre quand on touche la tuile. Pour Home Assistant : http://homeassistant:8123/lovelace/0",
              "The page the tile opens. For Home Assistant: http://homeassistant:8123/lovelace/0"),
        icon=T("Icône", "Icon",
               "Un nom (jellyfin, youtube, cuisine, home-assistant…) ou un emoji. La liste est dans la documentation.",
               "A name (jellyfin, youtube, kitchen, home-assistant…) or an emoji. The list is in the documentation."),
        group=T("Groupe", "Group", "Les tuiles d'un même groupe sont rangées ensemble, sous ce titre.",
                "Tiles in the same group are shown together, under this heading."),
        description=T("Petit texte", "Small text", "Une ligne sous le nom, par exemple « Films et séries ».",
                      "One line under the name, for example \"Films and series\"."),
        token=T("🔑 Jeton Home Assistant", "🔑 Home Assistant token",
                "Seulement pour le lien Home Assistant : un jeton longue durée (votre profil > Sécurité). Il ouvre le tableau de bord déjà connecté.",
                "Only for the Home Assistant link: a long-lived token (your profile > Security). It opens the dashboard already logged in."),
        quality=T("✨ Qualité de l'image", "✨ Picture quality",
                  "Pour cette page seulement, de 1 à 95. Un film se contente de 40 ; vide = la qualité de l'écran.",
                  "For this page only, 1 to 95. A film is fine at 40; empty = the screen's own quality."),
        fps=T("🎬 Images par seconde", "🎬 Pictures per second",
              "Pour cette page seulement. Plus bas = plus régulier sur une vidéo ; vide = celui de l'écran.",
              "For this page only. Lower = steadier on video; empty = the screen's own."),
        max_rate=T("📶 Débit maximum (Ko/s)", "📶 Maximum rate (KiB/s)",
                   "Pour cette page seulement. Une scène chargée est envoyée un peu moins nette pour rester dessous ; 0 = sans limite, vide = celui de l'écran.",
                   "For this page only. A busy scene is sent a little softer to stay under it; 0 = no limit, empty = the screen's own."),
        user_agent=T("Se présenter comme…", "Present itself as…",
                     "Rarement utile. Pour YouTube en mode télévision, voir la documentation.",
                     "Rarely needed. For YouTube's television mode, see the documentation."),
        voice=T("🗣️ Autres noms à dire", "🗣️ Other names to say",
                "Pour l'ouvrir à la voix : ce que l'assistant vocal comprend quand vous dites le nom, séparé par des virgules. Par exemple « gelée fine, jelly fin » pour Jellyfin.",
                "To open it by voice: what the voice assistant writes when you say the name, separated by commas. For example \"jelly fin, jellyfish\" for Jellyfin."),
    )

SIZES = ("Taille", "Size")
LOOK = dict(
    theme=T("Thème", "Theme", "Sombre (dark) ou clair (light).", "Dark or light."),
    columns=T("Tuiles par ligne", "Tiles per row",
              "0 = automatique. 4 pour un grand écran 1280x800, 3 pour un 7 pouces 1024x600.",
              "0 = automatic. 4 for a large 1280x800 screen, 3 for a 7-inch 1024x600."),
    align=T("Position de l'horloge", "Clock position", "À gauche, au centre ou à droite.", "Left, centre or right."),
    tiles=T("Forme des liens", "Shape of the links",
            "cards : grandes cartes, icône à côté du nom. buttons : petits boutons, icône en haut et nom en dessous.",
            "cards: wide cards, the icon beside the name. buttons: small buttons, the icon on top and the name underneath."),
    focus_color=T("🎯 Couleur de la sélection", "🎯 Selection colour",
                  "Le cadre autour du lien choisi à la télécommande ou à la manette. Sur un fond clair, prenez black ou yellow.",
                  "The ring around the link chosen with a remote or a gamepad. On a light background, pick black or yellow."),
    avatar=T("😊 Avatar", "😊 Avatar",
             "Un petit visage de la taille d'un bouton, en bas à droite. Glissez-le du doigt pour le mettre où vous voulez ; il y reste.",
             "A small face the size of a button, in the bottom right corner. Drag it with a finger to put it anywhere; it stays there."),
    avatar_shape=T("🐻 Forme de l'avatar", "🐻 The avatar's shape",
                   "mochi (une boule douce), robot (son antenne s'allume avec l'assistant vocal), cat (un chat), bear (un ourson) ou ghost (un fantôme).",
                   "mochi (a soft ball), robot (its antenna lights with the voice assistant), cat, bear or ghost."),
    avatar_voice=T("🎙️ Assistant vocal de l'avatar", "🎙️ The avatar's voice assistant",
                   "Le visage écoute, réfléchit et répond avec lui. Vide : le seul assistant vocal de Home Assistant. S'il y en a plusieurs, mettez celui de cet écran (assist_satellite.guition par exemple). off : aucun.",
                   "The face listens, thinks and answers with it. Empty: Home Assistant's only voice assistant. With several, put this screen's (assist_satellite.guition, say). off: none."),
)

cfg = dict(
    panels=T("🖥️ 1 · Mes écrans", "🖥️ 1 · My screens",
             "Un élément par écran ESP32-P4. Nom, adresse IP, taille, et ce qu'il affiche.",
             "One entry per ESP32-P4 screen: name, IP address, size, and what it shows.",
             name=T("Nom de l'écran", "Screen name", "Un nom simple : salon, cuisine… Sert aussi dans « Page de liens propre à un écran ».",
                    "A simple name: living, kitchen… Also used in \"A screen's own link page\"."),
             host=T("Adresse IP de l'écran", "Screen IP address", "Par exemple 192.168.1.11. Elle est dans le journal de l'écran (ESPHome).",
                    "For example 192.168.1.11. It is in the screen's ESPHome log."),
             url=T("Ce qu'il affiche", "What it shows",
                   "Écrivez launcher pour la page de liens, ou l'adresse d'une page.",
                   "Write launcher for the page of links, or the address of a page."),
             width=T("Largeur (pixels)", "Width (pixels)", "La largeur de l'écran, par exemple 1024 ou 800.", "The screen's width, e.g. 1024 or 800."),
             height=T("Hauteur (pixels)", "Height (pixels)", "La hauteur de l'écran, par exemple 600 ou 1280.", "The screen's height, e.g. 600 or 1280."),
             rotate=T("Rotation de l'image", "Picture rotation", "0, 90, 180 ou 270 degrés, pour que l'image soit à l'endroit.",
                      "0, 90, 180 or 270 degrees, so the picture is the right way up."),
             touch=T("👆 Toucher", "👆 Touch", "Donné par la calibration (voir la documentation). Si l'image est tournée, tournez aussi le toucher.",
                     "Given by the calibration (see the documentation). If the picture is rotated, rotate the touch too.",
                     rotate=T("Rotation du toucher", "Touch rotation"),
                     mirror_x=T("Inverser gauche/droite", "Mirror left/right"),
                     mirror_y=T("Inverser haut/bas", "Mirror up/down")),
             advanced=T("🛠️ Avancé", "🛠️ Advanced", "Rien d'obligatoire ici : chaque réglage vide reprend « Réglages communs ».",
                        "Nothing required here: every empty setting takes \"Common settings\".",
                        port=T("Port réseau", "Network port", "5000 sauf si l'écran dit autre chose.", "5000 unless the screen says otherwise."),
                        token=T("🔑 Jeton pour cet écran seulement", "🔑 Token for this screen only",
                                "Laissez vide : le jeton se met sur le lien Home Assistant.", "Leave empty: the token goes on the Home Assistant link."),
                        home_assistant=T("Montre Home Assistant", "Shows Home Assistant",
                                         "Éteint = cet écran ne reçoit aucun jeton (un site ordinaire, un horaire de train…).",
                                         "Off = this screen gets no token (an ordinary site, a train board…)."),
                        fps=T("🎬 Images par seconde", "🎬 Pictures per second"),
                        quality=T("✨ Qualité de l'image", "✨ Picture quality", "1 à 95.", "1 to 95."),
                        max_rate=T("📶 Débit maximum (Ko/s)", "📶 Maximum rate (KiB/s)", "0 = sans limite.", "0 = no limit."),
                        keyboard=T("⌨️ Clavier à l'écran", "⌨️ On-screen keyboard", "azerty, qwerty, ou off pour aucun.", "azerty, qwerty, or off for none."),
                        blank_after=T("Libérer la page après (secondes)", "Release the page after (seconds)",
                                      "Quand l'écran dort depuis ce temps, le serveur arrête la page. 300 par défaut.",
                                      "Once the screen has slept this long, the server stops the page. 300 by default."),
                        keep_profile=T("💾 Rester connecté aux sites", "💾 Stay signed in to sites",
                                       "Garde cookies et connexions entre deux redémarrages.", "Keeps cookies and sign-ins across restarts."),
                        locale=T("Langue", "Language", "Par exemple fr ou fr-FR.", "For example en-GB or fr-FR."),
                        user_agent=T("Se présenter comme…", "Present itself as…", "Rarement utile.", "Rarely needed."),
                        import_profile=T("Importer un profil de navigateur", "Import a browser profile", "Voir la documentation.", "See the documentation."),
                        stereo=T("🔊 Son en stéréo", "🔊 Stereo sound",
                                 "Le double de données. Carte à jour obligatoire, et num_channels: 2 sur l'enceinte Bluetooth.",
                                 "Twice the data. Needs the board updated, and num_channels: 2 on the Bluetooth speaker."),
                        stats=T("🐞 Statistiques", "🐞 Statistics"),
                        show_media=T("🐞 Suivre les vidéos", "🐞 Follow videos"),
                        show_touches=T("🐞 Afficher chaque toucher", "🐞 Show every touch"))),
    links=T("🔗 2 · Liens communs", "🔗 2 · Shared links",
            "Les tuiles de la page de liens, pour tous les écrans qui n'ont pas la leur (voir 4).",
            "The tiles of the page of links, for every screen without its own (see 4).",
            **link_fields()),
    launcher=T("🎨 3 · Apparence de la page de liens", "🎨 3 · Look of the page of links",
               "Couleurs, horloge, météo, fond d'écran. S'applique à tous les écrans ; un écran peut changer ce qu'il veut en 4.",
               "Colours, clock, weather, wallpaper. Applies to every screen; a screen can change any of it in 4.",
               **LOOK,
               voice_links=T("🗣️ Ouvrir les liens à la voix", "🗣️ Open links by voice",
                             "Dites « ouvre Jellyfin » ou « retour à l'accueil » à l'assistant vocal de l'écran. L'add-on écrit pour cela l'automatisation « Portall : ouvrir un lien à la voix » dans Home Assistant et la tient à jour avec vos liens.",
                             "Say \"open Jellyfin\" or \"go home\" to the screen's voice assistant. The add-on writes the automation \"Portall : ouvrir un lien à la voix\" into Home Assistant for it and keeps it in step with your links."),
               clock=T("🕒 Horloge", "🕒 Clock", None, None,
                       show=T("Afficher l'horloge", "Show the clock"), size=T(*SIZES),
                       color=T("Couleur", "Colour", "theme = la couleur du thème.", "theme = the theme's own colour.")),
               date=T("📅 Date", "📅 Date", None, None, size=T(*SIZES), color=T("Couleur", "Colour")),
               weather=T("⛅ Météo", "⛅ Weather", None, None,
                         entity=T("Entité météo", "Weather entity", "Par exemple weather.forecast_maison. Vide = pas de météo.",
                                  "For example weather.forecast_home. Empty = no weather."),
                         size=T(*SIZES)),
               background=T("🖼️ Fond d'écran", "🖼️ Wallpaper", None, None,
                            source=T("Image", "Picture", "Une adresse (http://…) ou un fichier / dossier dans /media.",
                                     "An address (http://…) or a file / folder in /media."),
                            motion=T("Animer (GIF, vidéo)", "Animate (GIF, video)", "Coûte des images sur le réseau.", "Costs pictures on the network."),
                            blur=T("Flou", "Blur"), dim=T("Assombrir (0 à 100)", "Darken (0 to 100)")),
               slideshow=T("🎞️ Diaporama", "🎞️ Slideshow", None, None,
                           enabled=T("Faire défiler les images", "Cycle the pictures"),
                           urls=T("Adresses des images", "Picture addresses", "Une par ligne.", "One per line."),
                           seconds=T("Secondes par image", "Seconds per picture"),
                           fade=T("Fondu (secondes)", "Fade (seconds)", "0 = coupure nette, plus léger pour l'écran.", "0 = hard cut, lighter on the screen."),
                           rescan=T("Relire le dossier (minutes)", "Re-read the folder (minutes)"))),
    launchers=T("🧩 4 · Page de liens propre à un écran", "🧩 4 · A screen's own page of links",
                "Pour qu'un écran ait SES boutons et SON apparence, indépendants des autres. Ajoutez un élément par écran concerné.",
                "To give a screen ITS own buttons and look, independent of the others. Add one entry per screen.",
                panel=T("Pour l'écran", "For the screen", "Le nom de l'écran, exactement comme en 1 (salon, cuisine…).",
                        "The screen's name, exactly as in 1 (living, kitchen…)."),
                links=T("🔗 Ses liens", "🔗 Its links", "Les tuiles de cet écran seulement.", "This screen's tiles only.", **link_fields()),
                **LOOK,
                clock=T("🕒 Afficher l'horloge", "🕒 Show the clock"),
                clock_size=T("🕒 Taille de l'horloge", "🕒 Clock size"),
                clock_color=T("🕒 Couleur de l'horloge", "🕒 Clock colour"),
                date_size=T("📅 Taille de la date", "📅 Date size"),
                date_color=T("📅 Couleur de la date", "📅 Date colour"),
                weather=T("⛅ Entité météo", "⛅ Weather entity", "Par exemple weather.forecast_maison.", "For example weather.forecast_home."),
                weather_size=T("⛅ Taille de la météo", "⛅ Weather size"),
                background=T("🖼️ Fond d'écran", "🖼️ Wallpaper", "Une adresse (http://…) ou un fichier / dossier dans /media.",
                             "An address (http://…) or a file / folder in /media."),
                background_motion=T("🖼️ Animer le fond", "🖼️ Animate the wallpaper"),
                background_blur=T("🖼️ Flou du fond", "🖼️ Wallpaper blur"),
                background_dim=T("🖼️ Assombrir le fond (0 à 100)", "🖼️ Darken the wallpaper (0 to 100)"),
                slideshow=T("🎞️ Diaporama", "🎞️ Slideshow"),
                slideshow_urls=T("🎞️ Adresses des images", "🎞️ Picture addresses"),
                slideshow_seconds=T("🎞️ Secondes par image", "🎞️ Seconds per picture"),
                slideshow_fade=T("🎞️ Fondu (secondes)", "🎞️ Fade (seconds)"),
                slideshow_rescan=T("🎞️ Relire le dossier (minutes)", "🎞️ Re-read the folder (minutes)")),
    defaults=T("⚙️ 5 · Réglages communs", "⚙️ 5 · Common settings",
               "Valent pour tous les écrans, sauf ce qu'un écran change dans son « Avancé ».",
               "Apply to every screen, except what a screen changes in its \"Advanced\".",
               port=T("Port réseau", "Network port", "5000, comme dans le YAML de l'écran.", "5000, as in the screen's YAML."),
               fps=T("🎬 Images par seconde", "🎬 Pictures per second", "25 convient à presque tout.", "25 suits nearly everything."),
               quality=T("✨ Qualité de l'image", "✨ Picture quality", "1 à 95. Plus bas = plus fluide, moins net.", "1 to 95. Lower = smoother, less sharp."),
               max_rate=T("📶 Débit maximum (Ko/s)", "📶 Maximum rate (KiB/s)",
                          "Ce que le Wi-Fi d'un écran reçoit sans décrocher. Une vidéo chargée est envoyée un peu moins nette pour rester dessous. 2400 convient à l'ESP32-C6 ; 0 = sans limite.",
                          "What a screen's Wi-Fi takes without stalling. A busy video is sent a little softer to stay under it. 2400 suits the ESP32-C6; 0 = no limit."),
               keyboard=T("⌨️ Clavier à l'écran", "⌨️ On-screen keyboard"),
               keep_profile=T("💾 Rester connecté aux sites", "💾 Stay signed in to sites"),
               locale=T("Langue des pages", "Language of the pages", "fr pour le français.", "en for English."),
               homekit=T("📱 Télécommande iPhone (HomeKit)", "📱 iPhone remote (HomeKit)",
                         "Chaque écran apparaît dans l'app Maison et dans la télécommande du Centre de contrôle.",
                         "Each screen appears in the Home app and in Control Centre's remote.")),
    debug=T("🐞 6 · Diagnostic", "🐞 6 · Diagnostics",
            "À laisser éteint. À allumer seulement pour chercher un problème : cela remplit le journal.",
            "Leave off. Only turn on to track down a problem: it fills the log.",
            stats=T("Statistiques toutes les 5 secondes", "Statistics every 5 seconds"),
            show_media=T("Suivre les vidéos", "Follow videos"),
            show_touches=T("Afficher chaque toucher", "Show every touch")),
)

def build(lang, node):
    name, desc, fields = node[lang]
    out = {"name": name}
    if desc:
        out["description"] = desc
    if fields:
        out["fields"] = {k: build(lang, v) for k, v in fields.items()}
    return out

for lang in ("fr", "en"):
    doc = {"configuration": {k: build(lang, v) for k, v in cfg.items()}}
    head = ("# The names and explanations the add-on's settings page shows, read by\n"
            "# Home Assistant's Supervisor. Generated by tools/maketranslations.py --\n"
            "# edit that, not this, so the two languages cannot drift apart.\n")
    (OUT / f"{lang}.yaml").write_text(
        head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100))
print("ok")
