# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Validated, local visual preferences for the launcher.

Only the page's appearance belongs here. Home Assistant services, automations,
weather sources and assistant configuration remain add-on configuration.
``FIELDS`` is also the JSON-serializable contract used by the settings dialog.
"""

import json
import os
import tempfile
import threading
from urllib.parse import urlsplit

PATH = "/appearance.json"

# Keep these palette names in sync with launcher.PALETTES without importing the
# launcher: it imports this module to create its preferences and settings page.
_PALETTE_NAMES = (
    ("theme", "Selon le thème", "Follow theme"),
    ("slate", "Ardoise", "Slate"),
    ("gray", "Gris", "Gray"),
    ("zinc", "Zinc", "Zinc"),
    ("neutral", "Neutre", "Neutral"),
    ("stone", "Pierre", "Stone"),
    ("red", "Rouge", "Red"),
    ("amber", "Ambre", "Amber"),
    ("yellow", "Jaune", "Yellow"),
    ("lime", "Citron vert", "Lime"),
    ("green", "Vert", "Green"),
    ("emerald", "Émeraude", "Emerald"),
    ("teal", "Sarcelle", "Teal"),
    ("cyan", "Cyan", "Cyan"),
    ("sky", "Bleu ciel", "Sky"),
    ("blue", "Bleu", "Blue"),
    ("indigo", "Indigo", "Indigo"),
    ("violet", "Violet", "Violet"),
    ("purple", "Pourpre", "Purple"),
    ("fuchsia", "Fuchsia", "Fuchsia"),
    ("pink", "Rose", "Pink"),
    ("rose", "Rose rouge", "Rose"),
    ("white", "Blanc", "White"),
    ("black", "Noir", "Black"),
)


def _options(*items):
    return [{"value": value, "fr": fr, "en": en} for value, fr, en in items]


_COLORS = _options(*_PALETTE_NAMES)
_SIZES = _options(
    ("small", "Petite", "Small"), ("medium", "Moyenne", "Medium"),
    ("large", "Grande", "Large"), ("huge", "Très grande", "Huge"),
)


def _field(key, section, kind, fr, en, options=(), **constraints):
    return {
        "key": key, "section": section, "kind": kind,
        "fr": fr, "en": en, "options": list(options), **constraints,
    }


FIELDS = [
    _field("layout", "layout", "choice", "Présentation", "Layout", _options(
        ("classic", "Classique", "Classic"),
        ("dashboard", "Tableau de bord", "Dashboard"))),
    _field("theme", "layout", "choice", "Thème", "Theme", _options(
        ("dark", "Sombre", "Dark"), ("light", "Clair", "Light"))),
    _field("columns", "layout", "number", "Colonnes", "Columns", _options(
        (0, "Automatique", "Automatic"),
        *((number, str(number), str(number)) for number in range(1, 7))),
        min=0, max=6, step=1),
    _field("align", "layout", "choice", "Alignement", "Alignment", _options(
        ("left", "À gauche", "Left"), ("center", "Centré", "Center"),
        ("right", "À droite", "Right"))),
    _field("tiles", "tiles", "choice", "Style des tuiles", "Tile style", _options(
        ("cards", "Cartes", "Cards"), ("buttons", "Boutons", "Buttons"))),
    _field("focus_color", "tiles", "choice", "Couleur du focus", "Focus color", _COLORS),
    _field("tile_background", "tiles", "choice", "Fond des tuiles", "Tile background", _options(
        ("solid", "Uni", "Solid"), ("transparent", "Transparent", "Transparent"))),
    _field("tile_size", "tiles", "choice", "Taille des tuiles", "Tile size", _options(
        ("tiny", "Très petite", "Tiny"), ("small", "Petite", "Small"),
        ("medium", "Moyenne", "Medium"))),
    _field("tile_text_color", "tiles", "choice", "Couleur du texte", "Tile text color", _COLORS),
    _field("clock", "clock", "bool", "Afficher l’horloge et la date", "Show clock and date"),
    _field("clock_size", "clock", "choice", "Taille de l’horloge", "Clock size", _SIZES),
    _field("clock_color", "clock", "choice", "Couleur de l’horloge", "Clock color", _COLORS),
    _field("date_size", "clock", "choice", "Taille de la date", "Date size", _SIZES),
    _field("date_color", "clock", "choice", "Couleur de la date", "Date color", _COLORS),
    _field("weather_visible", "weather", "bool", "Afficher la météo", "Show weather"),
    _field("weather_size", "weather", "choice", "Taille de la météo", "Weather size", _SIZES),
    _field("weather_color", "weather", "choice", "Couleur de la météo", "Weather color", _COLORS),
    _field("avatar", "assistant", "bool", "Afficher l’avatar", "Show avatar"),
    _field("background", "background", "text", "Image ou vidéo de fond", "Background image or video",
           max_length=2048),
    _field("background_motion", "background", "bool", "Animer le fond", "Animate background"),
    _field("background_blur", "background", "choice", "Flou du fond", "Background blur", _options(
        ("off", "Aucun", "Off"), ("sm", "Léger", "Small"),
        ("md", "Moyen", "Medium"), ("xl", "Fort", "Strong"))),
    _field("background_dim", "background", "number", "Assombrissement du fond", "Background dimming",
           min=0, max=100, step=1),
]

DEFAULTS = {
    "layout": "classic", "theme": "dark", "columns": 0, "align": "left",
    "tiles": "cards", "focus_color": "theme", "tile_background": "solid",
    "tile_size": "medium", "tile_text_color": "theme", "avatar": False,
    "clock": True, "clock_size": "medium", "clock_color": "theme",
    "date_size": "medium", "date_color": "theme", "weather_visible": True,
    "weather_size": "medium", "weather_color": "theme", "background": "",
    "background_motion": False, "background_blur": "off", "background_dim": 40,
}

# Allowed values are derived from the public schema; validation and controls
# therefore cannot accidentally acquire different sets of choices.
VALS = {
    field["key"]: tuple(option["value"] for option in field["options"])
    for field in FIELDS if field["options"]
}
_BY_KEY = {field["key"]: field for field in FIELDS}


def _background_valid(value):
    """Check syntax only: never open a path or contact a remote background."""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if value.startswith("data:image/"):
        return "," in value
    try:
        parsed = urlsplit(value)
        if parsed.scheme:
            # Accessing .port also checks malformed or out-of-range ports.
            parsed.port
            return (parsed.scheme.lower() in ("http", "https")
                    and bool(parsed.netloc) and bool(parsed.hostname)
                    and not any(char.isspace() for char in value)
                    and "\\" not in parsed.netloc)
        return not value.startswith("//")
    except ValueError:
        return False


def _validated(values, complete=False):
    if not isinstance(values, dict):
        raise ValueError("appearance must be an object")
    if any(key not in _BY_KEY for key in values):
        raise ValueError("unknown appearance setting")
    if complete and set(values) != set(_BY_KEY):
        raise ValueError("appearance defaults must include every setting")
    for key, value in values.items():
        field = _BY_KEY[key]
        kind = field["kind"]
        if kind == "bool":
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean")
        elif kind == "number":
            if type(value) is not int or not field["min"] <= value <= field["max"]:
                raise ValueError(f"{key} must be an integer from {field['min']} to {field['max']}")
        elif kind == "choice":
            if type(value) is not str or value not in VALS[key]:
                raise ValueError(f"invalid {key}")
        elif kind == "text":
            if type(value) is not str or len(value) > field["max_length"]:
                raise ValueError(f"{key} must be a string of at most {field['max_length']} characters")
            if not _background_valid(value):
                raise ValueError("background must be an HTTP(S) URL or a local path")
    return dict(values)


class Preferences:
    """Validated overrides, persisted before becoming visible to readers.

    Only overrides are stored, so reset and untouched fields follow the
    add-on's defaults when it next starts. A missing path keeps them in RAM.
    Older files containing only theme and columns remain valid overrides;
    snapshots always include every current field.
    """

    def __init__(self, defaults: dict, path=None):
        self._defaults = _validated({**DEFAULTS, **_validated(defaults)}, complete=True)
        self._path = os.fspath(path) if path is not None else None
        self._lock = threading.RLock()
        self._overrides = {}
        if self._path is not None:
            try:
                with open(self._path, encoding="utf-8") as source:
                    self._overrides = _validated(json.load(source))
            except (OSError, UnicodeError, ValueError, TypeError):
                self._overrides = {}

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._defaults, **self._overrides}

    def has_override(self, key):
        with self._lock:
            return key in self._overrides

    def _persist(self, overrides):
        if self._path is None:
            return
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", delete=False,
                    dir=directory,
                    prefix=".appearance-", suffix=".tmp") as target:
                temporary = target.name
                json.dump(overrides, target, separators=(",", ":"))
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def update(self, payload) -> dict:
        with self._lock:
            changes = _validated(payload)
            if changes:
                overrides = {**self._overrides, **changes}
                self._persist(overrides)
                self._overrides = overrides
            return self.snapshot()

    def reset(self) -> dict:
        with self._lock:
            self._persist({})
            self._overrides = {}
            return self.snapshot()


# Keep the launcher's existing appearance.OPEN_BUTTON/HTML/CSS/JS API.
if __package__:
    from .appearance_ui import OPEN_BUTTON, HTML, CSS, JS
else:
    from appearance_ui import OPEN_BUTTON, HTML, CSS, JS
