# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Local appearance overrides and the launcher's small settings dialog."""

import json
import os
import tempfile
import threading

PATH = "/appearance.json"


def _validated(values, complete=False):
    if not isinstance(values, dict):
        raise ValueError("appearance must be an object")
    if any(key not in ("theme", "columns") for key in values):
        raise ValueError("unknown appearance setting")
    if complete and set(values) != {"theme", "columns"}:
        raise ValueError("appearance defaults need theme and columns")
    if "theme" in values and values["theme"] not in ("dark", "light"):
        raise ValueError("theme must be dark or light")
    if "columns" in values:
        columns = values["columns"]
        if type(columns) is not int or not 0 <= columns <= 6:
            raise ValueError("columns must be an integer from 0 to 6")
    return dict(values)


class Preferences:
    """Validated overrides, persisted before becoming visible to readers.

    Only overrides are stored, so reset and untouched fields follow the
    add-on's defaults when it next starts. A missing path keeps them in RAM.
    """

    def __init__(self, defaults: dict, path=None):
        self._defaults = _validated(defaults, complete=True)
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


OPEN_BUTTON = """<button type="button" class="appearance-open" id="appearance-open"
 aria-haspopup="dialog" aria-controls="appearance-dialog" title="Réglages">
 <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <path d="m9 3-.5 2.1-2 .9-1.9-.6-3 5.2 1.6 1.5v2.2l-1.6 1.5 3 5.2 2-.6 1.9.9.5 2.1h6l.5-2.1 1.9-.9 2 .6 3-5.2-1.6-1.5v-2.2l1.6-1.5-3-5.2-1.9.6-2-.9L15 3Z"
   transform="translate(0 -1) scale(1 .96)" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
  <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.5"/>
 </svg><span data-appearance-text="settings">Réglages</span>
</button>"""

HTML = """<p id="appearance-load-error" role="status" hidden></p>
<dialog id="appearance-dialog" aria-labelledby="appearance-title">
 <form id="appearance-form">
  <h2 id="appearance-title" data-appearance-text="appearance">Apparence</h2>
  <p class="appearance-help" data-appearance-text="help">Personnalisez cet accueil.</p>
  <label for="appearance-theme" data-appearance-text="theme">Thème</label>
  <select id="appearance-theme" name="theme">
   <option value="dark" data-appearance-text="dark">Sombre</option>
   <option value="light" data-appearance-text="light">Clair</option>
  </select>
  <label for="appearance-columns" data-appearance-text="columns">Colonnes</label>
  <select id="appearance-columns" name="columns">
   <option value="0" data-appearance-text="auto">Automatique</option>
   <option value="1">1</option><option value="2">2</option>
   <option value="3">3</option><option value="4">4</option>
   <option value="5">5</option><option value="6">6</option>
  </select>
  <p class="appearance-help" data-appearance-text="resetHelp">Réinitialiser reprend les réglages de l’extension.</p>
  <p id="appearance-error" role="alert" tabindex="-1" hidden></p>
  <button type="button" id="appearance-reset" data-appearance-text="reset">Réinitialiser</button>
  <div class="appearance-actions">
   <button type="button" id="appearance-cancel" data-appearance-text="cancel">Annuler</button>
   <button type="submit" id="appearance-apply" data-appearance-text="apply">Appliquer</button>
  </div>
 </form>
</dialog>"""

CSS = """
 #appearance-open, #appearance-dialog button, #appearance-dialog select {
   font: inherit; color: var(--ink, #10203b); min-height: 48px;
   border: 1px solid var(--edge, #c4ced9); border-radius: 12px;
   background: var(--card, #fff); padding: 10px 16px;
 }
 #appearance-open {
   display: inline-flex; align-items: center; justify-content: center; gap: 10px;
   min-width: 48px; flex-shrink: 0; cursor: pointer;
   font-size: clamp(18px, 2vw, 23px); min-height: 54px;
 }
 #appearance-open svg { flex-shrink: 0; }
 #appearance-dialog {
   box-sizing: border-box; width: min(440px, calc(100vw - 32px));
   max-height: calc(100dvh - 32px); overflow: auto; padding: 28px;
   color: var(--ink, #10203b); background: var(--card, #fff);
   border: 1px solid var(--edge, #c4ced9); border-radius: 22px;
   box-shadow: 0 24px 80px #0006; font: inherit;
 }
 #appearance-dialog::backdrop { background: #0008; }
 #appearance-dialog h2 { margin: 0; font-size: 26px; letter-spacing: -.02em; }
 #appearance-dialog .appearance-help {
   margin: 10px 0 20px; color: var(--faint, #506070); line-height: 1.5;
 }
 #appearance-dialog label { display: block; margin: 18px 0 8px; font-weight: 600; }
 #appearance-dialog select { display: block; box-sizing: border-box; width: 100%; }
 #appearance-dialog button { cursor: pointer; }
 #appearance-dialog .appearance-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 20px; }
 #appearance-apply { font-weight: 650 !important; border-color: var(--ring, #2479ed) !important; }
 #appearance-open:focus-visible, #appearance-dialog :focus-visible {
   outline: 3px solid var(--ring, #2479ed); outline-offset: 3px;
 }
 #appearance-dialog [disabled], #appearance-open[disabled] { opacity: .6; cursor: wait; }
 #appearance-error { border-left: 3px solid var(--ring, #2479ed); padding-left: 12px; line-height: 1.5; }
 #appearance-load-error {
   position: fixed; bottom: 80px; left: 20px; z-index: 100;
   max-width: min(360px, calc(100vw - 40px)); padding: 14px 18px;
   color: var(--ink, #10203b); background: var(--card, #fff);
   border: 1px solid var(--edge, #c4ced9); border-radius: 12px;
 }
"""

JS = """<script>
(() => {
  const open = document.getElementById('appearance-open');
  const dialog = document.getElementById('appearance-dialog');
  if (!open || !dialog) return;
  const fr = /^fr/i.test(navigator.language || '');
  const words = fr ? {
    settings: 'Réglages', appearance: 'Apparence', help: 'Personnalisez cet accueil.',
    theme: 'Thème', dark: 'Sombre', light: 'Clair', columns: 'Colonnes', auto: 'Automatique',
    resetHelp: 'Réinitialiser reprend les réglages de l’extension.',
    reset: 'Réinitialiser', cancel: 'Annuler', apply: 'Appliquer',
    loadError: 'Impossible de charger les réglages. Réessayez.',
    saveError: 'Enregistrement impossible. Vos choix sont conservés : réessayez.'
  } : {
    settings: 'Settings', appearance: 'Appearance', help: 'Customize this home screen.',
    theme: 'Theme', dark: 'Dark', light: 'Light', columns: 'Columns', auto: 'Automatic',
    resetHelp: 'Reset restores the add-on settings.',
    reset: 'Reset', cancel: 'Cancel', apply: 'Apply',
    loadError: 'Could not load settings. Please try again.',
    saveError: 'Could not save. Your choices are kept: please try again.'
  };
  document.querySelectorAll('[data-appearance-text]').forEach(element => {
    element.textContent = words[element.dataset.appearanceText];
  });
  open.title = words.settings;
  dialog.lang = fr ? 'fr' : 'en';
  const form = document.getElementById('appearance-form');
  const theme = document.getElementById('appearance-theme');
  const columns = document.getElementById('appearance-columns');
  const error = document.getElementById('appearance-error');
  const loadError = document.getElementById('appearance-load-error');
  let previousFocus = open, saving = false, loading = false;
  open.addEventListener('click', async () => {
    if (loading || dialog.open) return;
    loading = true;
    previousFocus = document.activeElement;
    open.disabled = true;
    open.setAttribute('aria-busy', 'true');
    loadError.hidden = true;
    error.hidden = true;
    try {
      const response = await fetch('/appearance.json', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) throw new Error('load');
      const values = await response.json();
      if (!values || !['dark', 'light'].includes(values.theme) ||
          !Number.isInteger(values.columns) || values.columns < 0 || values.columns > 6) throw new Error('values');
      theme.value = values.theme;
      columns.value = String(values.columns);
      dialog.showModal();
      theme.focus();
    } catch (_) {
      loadError.textContent = words.loadError;
      loadError.hidden = false;
    } finally {
      loading = false;
      open.disabled = false;
      open.removeAttribute('aria-busy');
    }
  });
  dialog.addEventListener('keydown', event => event.stopPropagation());
  dialog.addEventListener('cancel', event => { event.preventDefault(); dialog.close(); });
  dialog.addEventListener('close', () => {
    (previousFocus && previousFocus.isConnected ? previousFocus : open).focus();
  });
  document.getElementById('appearance-cancel').addEventListener('click', () => dialog.close());
  async function save(payload) {
    if (saving) return;
    saving = true;
    error.hidden = true;
    form.setAttribute('aria-busy', 'true');
    const controls = [theme, columns, document.getElementById('appearance-reset'), document.getElementById('appearance-apply')];
    controls.forEach(control => { control.disabled = true; });
    try {
      const response = await fetch('/appearance.json', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error('save');
      location.reload();
    } catch (_) {
      error.textContent = words.saveError;
      error.hidden = false;
      if (dialog.open) error.focus();
    } finally {
      saving = false;
      form.removeAttribute('aria-busy');
      controls.forEach(control => { control.disabled = false; });
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    save({theme: theme.value, columns: Number(columns.value)});
  });
  document.getElementById('appearance-reset').addEventListener('click', () => save({reset: true}));
})();
</script>"""
