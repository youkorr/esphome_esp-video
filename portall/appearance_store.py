# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Store launcher appearance in the add-on's existing Supervisor options.

Only visual fields are changed. Each write starts from the latest unresolved
options, so unrelated values and !secret references are kept intact.
"""

import copy
import json
import os
import threading
import urllib.error
import urllib.request

import appearance


# Also used by the startup migration which adds each screen's enabled switch.
OPTIONS_LOCK = threading.RLock()

SHARED_PATHS = {
    "layout": ("layout",), "theme": ("theme",), "columns": ("columns",),
    "align": ("align",), "tiles": ("tiles",), "focus_color": ("focus_color",),
    "tile_background": ("tile_background",), "tile_size": ("tile_size",),
    "tile_text_color": ("tile_text_color",), "avatar": ("avatar",),
    "clock": ("clock", "show"), "clock_size": ("clock", "size"),
    "clock_color": ("clock", "color"), "date_size": ("date", "size"),
    "date_color": ("date", "color"),
    "weather_visible": ("weather", "show"),
    "weather_size": ("weather", "size"), "weather_color": ("weather", "color"),
    "background": ("background", "source"),
    "background_motion": ("background", "motion"),
    "background_blur": ("background", "blur"),
    "background_dim": ("background", "dim"),
}
PANEL_KEYS = {key: key for key in SHARED_PATHS}
# The way home is the one field of the dialog that is not the launcher's: it
# belongs to each screen the page is shown on, and is stored where the form
# keeps it, in that screen's Advanced, so the two never disagree.
GESTURE = "home_gesture"


class StoreError(OSError):
    """An appearance write could not be committed to Supervisor."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def _truth(value):
    return value is True or str(value).strip().lower() in ("true", "yes", "1", "on")


def values_from_flat(config):
    """The resolved startup values, including fallback values for !secret."""
    return appearance.configured({
        key: config.get("launcher_" + key, default)
        for key, default in appearance.DEFAULTS.items()})


def _at(values, path):
    current = values
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _put(values, path, value):
    current = values
    for key in path[:-1]:
        if key not in current or current[key] is None:
            current[key] = {}
        if not isinstance(current[key], dict):
            raise StoreError("The add-on appearance configuration is malformed")
        current = current[key]
    current[path[-1]] = value


def _secret(value):
    return isinstance(value, str) and value.lstrip().startswith("!secret ")


def _given(value):
    return value is not None and not (isinstance(value, str) and not value.strip())


class AppearanceStore:
    """One launcher's visual fields within the existing add-on options."""

    def __init__(self, api, token, *, panel_name=None, fallback=None,
                 shared_fallback=None, background_clear=False,
                 shared_background_clear=False, timeout=5, screens=()):
        self.api = api.rstrip("/")
        self.token = str(token)
        self.panel_name = str(panel_name).strip() if panel_name is not None else None
        self.fallback = appearance.configured(fallback)
        self.shared_fallback = appearance.configured(shared_fallback)
        self.background_clear = bool(background_clear)
        self.shared_background_clear = bool(shared_background_clear)
        self.timeout = timeout
        # The screens this launcher is shown on, by name (or host when a
        # screen has no name), for the way home.
        self.screens = tuple(str(name).strip() for name in screens
                             if str(name).strip())

    def _request(self, suffix, options=None):
        headers = {"Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json"}
        data = None if options is None else json.dumps({"options": options}).encode()
        request = urllib.request.Request(
            self.api + "/addons/self/" + suffix, headers=headers, data=data,
            method="GET" if options is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode())
            if not isinstance(result, dict) or result.get("result") != "ok":
                raise StoreError("Supervisor did not accept the appearance settings")
            return result.get("data")
        except urllib.error.HTTPError as error:
            raise StoreError(
                f"Supervisor refused the appearance settings (HTTP {error.code})",
                status=error.code) from error
        except (OSError, ValueError, UnicodeError) as error:
            if isinstance(error, StoreError):
                raise
            raise StoreError("Supervisor appearance settings are unavailable") from error

    def _options(self):
        data = self._request("info")
        if not isinstance(data, dict) or not isinstance(data.get("options"), dict):
            raise StoreError("Supervisor returned no add-on configuration")
        return copy.deepcopy(data["options"])

    def _panel(self, options):
        entries = options.get("launchers") or []
        if not isinstance(entries, list):
            raise StoreError("The add-on launcher configuration is malformed")
        for entry in entries:
            if (isinstance(entry, dict)
                    and str(entry.get("panel", "")).strip().lower()
                    == self.panel_name.lower()):
                return entry
        raise StoreError("This launcher's configuration was removed; restart Portall")

    def _screen(self, options, key):
        for entry in options.get("panels") or []:
            if not isinstance(entry, dict):
                continue
            own = str(entry.get("name") or "").strip() or str(
                entry.get("host") or "").strip()
            if own.lower() == key.lower():
                return entry
        return None

    def _gesture(self, options):
        """The first of this launcher's screens that says how it goes home."""
        for key in self.screens:
            entry = self._screen(options, key)
            advanced = (entry or {}).get("advanced")
            raw = advanced.get(GESTURE) if isinstance(advanced, dict) else None
            if isinstance(raw, str) and raw.strip().lower() in appearance.VALS[GESTURE]:
                return raw.strip().lower()
            if entry is not None:
                break
        return appearance.DEFAULTS[GESTURE]

    def _put_gesture(self, options, gesture):
        """Every screen of this launcher, in its Advanced; None takes it away."""
        for key in self.screens:
            entry = self._screen(options, key)
            if entry is None:
                continue
            advanced = entry.get("advanced")
            if not isinstance(advanced, dict):
                advanced = entry["advanced"] = {}
            if gesture is None:
                advanced.pop(GESTURE, None)
            else:
                advanced[GESTURE] = gesture

    @staticmethod
    def _resolved(raw, key, fallback):
        return fallback[key] if _secret(raw) else raw

    def _values(self, options):
        shared = options.get("launcher") or {}
        if not isinstance(shared, dict):
            raise StoreError("The add-on appearance configuration is malformed")
        values = dict(appearance.DEFAULTS)
        for key, path in SHARED_PATHS.items():
            raw = _at(shared, path)
            if raw is not None:
                values[key] = self._resolved(raw, key, self.shared_fallback)
        raw_clear = _at(shared, ("background", "clear"))
        selected = raw_clear is not None
        clear = self.shared_background_clear if _secret(raw_clear) else _truth(raw_clear)
        if self.panel_name is not None:
            entry = self._panel(options)
            for key, option in PANEL_KEYS.items():
                raw = entry.get(option)
                value = self._resolved(raw, key, self.fallback)
                if _given(value):
                    values[key] = value
                    if key == "background":
                        clear = False
            raw_clear = entry.get("background_clear")
            if raw_clear is not None:
                selected = True
                clear = self.background_clear if _secret(raw_clear) else _truth(raw_clear)
        if clear:
            values["background"] = ""
        values[GESTURE] = self._gesture(options)
        values = appearance.configured(values)
        return {"values": values,
                "background_override": selected}

    def commit(self, changes, *, reset=False):
        changes = appearance._validated(changes)
        if "background" in changes:
            changes["background"] = changes["background"].strip()
        with OPTIONS_LOCK:
            options = self._options()
            if self.panel_name is None:
                shared = options.get("launcher")
                if shared is None:
                    shared = options["launcher"] = {}
                if not isinstance(shared, dict):
                    raise StoreError("The add-on appearance configuration is malformed")
                selected = appearance.DEFAULTS if reset else changes
                for key, value in selected.items():
                    if key in SHARED_PATHS:
                        _put(shared, SHARED_PATHS[key], value)
                if "background" in selected:
                    _put(shared, ("background", "clear"), not selected["background"].strip())
            else:
                entry = self._panel(options)
                if reset:
                    for key in (*PANEL_KEYS.values(), "background_clear"):
                        entry.pop(key, None)
                else:
                    for key, value in changes.items():
                        if key in PANEL_KEYS:
                            entry[PANEL_KEYS[key]] = value
                    if "background" in changes:
                        entry["background_clear"] = not changes["background"].strip()
            if reset:
                self._put_gesture(options, None)
            elif GESTURE in changes:
                self._put_gesture(options, changes[GESTURE])
            # Validate the returned view before changing durable options. A
            # successful POST is the only point after which the UI may commit.
            result = self._values(options)
            self._request("options", options)
            return result


def make_commit(config, *, shared_config=None, panel_name=None,
                api="http://supervisor", token=None, screens=()):
    """Return a managed commit callback, or None outside a Supervisor app."""
    if token is None:
        token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    shared_config = config if shared_config is None else shared_config
    return AppearanceStore(
        api, token, panel_name=panel_name,
        fallback=values_from_flat(config),
        shared_fallback=values_from_flat(shared_config),
        background_clear=_truth(config.get("launcher_background_clear", False)),
        shared_background_clear=_truth(shared_config.get("launcher_background_clear", False)),
        screens=screens,
    ).commit
