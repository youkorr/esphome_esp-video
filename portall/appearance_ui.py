# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Touch-friendly appearance editor, driven by the public appearance schema.

All choice menus live in the page DOM. In particular, no native select or color
popup is used: the display's CDP touch injector must be able to hit every choice.
"""

OPEN_BUTTON = """<button type="button" class="appearance-open" id="appearance-open"
 aria-haspopup="dialog" aria-controls="appearance-dialog" title="Réglages">
 <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <path d="m9 3-.5 2.1-2 .9-1.9-.6-3 5.2 1.6 1.5v2.2l-1.6 1.5 3 5.2 2-.6 1.9.9.5 2.1h6l.5-2.1 1.9-.9 2 .6 3-5.2-1.6-1.5v-2.2l1.6-1.5-3-5.2-1.9.6-2-.9L15 3Z"
   transform="translate(0 -1) scale(1 .96)" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
  <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.5"/>
 </svg><span data-appearance-text="settings">Réglages</span>
</button>"""

HTML = """<p id="appearance-load-error" role="status" hidden></p>
<dialog id="appearance-dialog" aria-labelledby="appearance-title" aria-describedby="appearance-intro">
 <form id="appearance-form">
  <div class="appearance-header">
   <div class="appearance-heading">
    <span class="appearance-eyebrow" data-appearance-text="eyebrow">VOTRE ACCUEIL</span>
    <h2 id="appearance-title" data-appearance-text="appearance">Apparence de la page de liens</h2>
    <p id="appearance-intro" data-appearance-text="intro">Un accueil à votre image, jusque dans les détails.</p>
   </div>
   <button type="button" id="appearance-close" class="appearance-icon-button" aria-label="Fermer">
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
   </button>
  </div>
  <div class="appearance-workspace">
   <nav class="appearance-navigation" aria-label="Rubriques de l’apparence">
    <div id="appearance-tabs" role="tablist" aria-orientation="vertical"></div>
    <p class="appearance-navigation-note" data-appearance-text="localHelp">Les réglages de voix et de veille restent dans la configuration de l’extension.</p>
   </nav>
   <div id="appearance-panels" class="appearance-panels"></div>
  </div>
  <footer class="appearance-footer">
   <p id="appearance-error" role="alert" tabindex="-1" hidden></p>
   <div class="appearance-footer-row">
    <button type="button" id="appearance-reset" class="appearance-quiet" data-appearance-text="reset">Réinitialiser</button>
    <div class="appearance-actions">
     <button type="button" id="appearance-cancel" data-appearance-text="cancel">Annuler</button>
     <button type="submit" id="appearance-apply" class="appearance-primary" data-appearance-text="apply">Appliquer</button>
    </div>
   </div>
  </footer>
 </form>
</dialog>"""

CSS = """
 #appearance-open, #appearance-dialog {
   --ap-ink: var(--ink, #16263f); --ap-muted: var(--faint, #627087);
   --ap-surface: var(--card, #fff); --ap-edge: var(--edge, #dce3ed);
   --ap-accent: var(--ring, #397ce8);
 }
 #appearance-open, #appearance-dialog button, #appearance-dialog input {
   box-sizing: border-box; font: inherit; color: var(--ap-ink);
   min-height: 48px; border: 1px solid var(--ap-edge); border-radius: 12px;
   background: var(--ap-surface); padding: 10px 16px; touch-action: manipulation;
 }
 #appearance-open {
   display: inline-flex; align-items: center; justify-content: center; gap: 10px;
   min-width: 48px; flex-shrink: 0; cursor: pointer;
   font-size: clamp(18px, 2vw, 23px); min-height: 54px;
 }
 #appearance-open svg { flex-shrink: 0; }
 #appearance-dialog {
   box-sizing: border-box; width: min(1080px, calc(100vw - 32px));
   height: min(790px, calc(100dvh - 24px)); max-height: calc(100dvh - 24px);
   padding: 0; overflow: hidden; margin: auto;
   color: var(--ap-ink); background: var(--ap-surface);
   border: 1px solid var(--ap-edge); border-radius: 24px;
   box-shadow: 0 28px 100px #0007; font: 16px/1.4 system-ui, sans-serif;
   text-align: left; letter-spacing: normal;
 }
 #appearance-dialog::backdrop { background: #070f21b8; }
 #appearance-dialog *, #appearance-dialog *::before, #appearance-dialog *::after { box-sizing: border-box; }
 #appearance-dialog [hidden] { display: none !important; }
 #appearance-form { display: flex; flex-direction: column; height: 100%; min-height: 0; margin: 0; }
 #appearance-dialog button { cursor: pointer; font-size: 14px; font-weight: 550; line-height: 1.3; }
 #appearance-dialog .appearance-header {
   display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
   flex-shrink: 0; padding: 26px 30px 22px; border-bottom: 1px solid var(--ap-edge);
 }
 #appearance-dialog .appearance-heading { min-width: 0; }
 #appearance-dialog .appearance-eyebrow {
   display: block; margin-bottom: 7px; color: var(--ap-accent); font-size: 11px;
   font-weight: 750; letter-spacing: .13em;
 }
 #appearance-dialog h2 { margin: 0; padding: 0; border: 0; color: var(--ap-ink); text-transform: none; font-size: clamp(22px, 3vw, 29px); font-weight: 700; line-height: 1.2; letter-spacing: -.035em; }
 #appearance-intro { margin: 9px 0 0; color: var(--ap-muted); font-size: 14px; }
 #appearance-dialog .appearance-icon-button { display: grid; place-items: center; flex-shrink: 0; width: 48px; padding: 0; background: transparent; }
 #appearance-dialog .appearance-workspace { display: grid; grid-template-columns: 206px minmax(0, 1fr); flex: 1; min-height: 0; }
 #appearance-dialog .appearance-navigation {
   padding: 20px 14px; overflow-y: auto; border-right: 1px solid var(--ap-edge);
   background: color-mix(in srgb, var(--ap-ink) 3%, var(--ap-surface));
 }
 #appearance-tabs { display: flex; flex-direction: column; gap: 7px; }
 #appearance-tabs button {
   display: flex; align-items: center; gap: 11px; width: 100%; padding: 12px;
   text-align: left; border-color: transparent; color: var(--ap-muted); background: transparent;
 }
 #appearance-tabs button svg { flex-shrink: 0; }
 #appearance-tabs button[aria-selected="true"] {
   color: var(--ap-ink); border-color: var(--ap-edge); background: var(--ap-surface);
   box-shadow: 0 2px 7px #00000008;
 }
 #appearance-tabs button[aria-selected="true"] svg { color: var(--ap-accent); }
 #appearance-dialog .appearance-navigation-note { margin: 24px 12px 0; color: var(--ap-muted); font-size: 12px; line-height: 1.65; }
 #appearance-panels { min-width: 0; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
 #appearance-dialog .appearance-panel { margin: 0; padding: 25px 28px 30px; }
 #appearance-dialog .appearance-section-header { margin-bottom: 22px; }
 #appearance-dialog h3 { margin: 0 0 6px; font-size: 22px; line-height: 1.25; letter-spacing: -.025em; }
 #appearance-dialog .appearance-section-description { margin: 0; color: var(--ap-muted); font-size: 14px; line-height: 1.6; }
 #appearance-dialog .appearance-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 290px), 1fr)); gap: 15px; align-items: start; }
 #appearance-dialog .appearance-field {
   min-width: 0; padding: 18px; border: 1px solid var(--ap-edge); border-radius: 17px;
   background: color-mix(in srgb, var(--ap-ink) 1.5%, var(--ap-surface));
 }
 #appearance-dialog .appearance-field[data-key="background"] { grid-column: 1 / -1; }
 #appearance-dialog .appearance-field-label { display: block; margin: 0 0 12px; font-size: 14px; font-weight: 650; }
 #appearance-dialog .appearance-field-help { margin: 10px 0 0; font-size: 12px; color: var(--ap-muted); line-height: 1.55; }
 #appearance-dialog .appearance-choice { display: flex; flex-wrap: wrap; gap: 7px; }
 #appearance-dialog .appearance-choice button { flex: 1; min-width: 68px; padding: 10px 11px; }
 #appearance-dialog button[aria-checked="true"], #appearance-dialog button[aria-selected="true"].appearance-option {
   border-color: var(--ap-accent); background: color-mix(in srgb, var(--ap-accent) 12%, var(--ap-surface));
 }
 #appearance-dialog .appearance-stepper { display: grid; grid-template-columns: 48px minmax(0, 1fr) 48px; gap: 8px; }
 #appearance-dialog .appearance-stepper button { padding: 0; font-size: 24px; font-weight: 400; }
 #appearance-dialog .appearance-number {
   display: flex; align-items: center; justify-content: center; min-height: 48px; min-width: 0;
   font-size: 16px; font-weight: 650; border: 1px solid var(--ap-edge); border-radius: 12px; padding: 4px;
 }
 #appearance-dialog .appearance-switch { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 14px; }
 #appearance-dialog .appearance-switch-track { display: block; flex-shrink: 0; width: 36px; height: 22px; padding: 3px; border-radius: 20px; background: var(--ap-muted); }
 #appearance-dialog .appearance-switch-track::after { content: ''; display: block; width: 16px; height: 16px; border-radius: 50%; background: #fff; }
 #appearance-dialog .appearance-switch[aria-checked="true"] .appearance-switch-track { background: var(--ap-accent); }
 #appearance-dialog .appearance-switch[aria-checked="true"] .appearance-switch-track::after { transform: translateX(14px); }
 #appearance-dialog .appearance-picker { min-width: 0; }
 #appearance-dialog .appearance-picker-toggle { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; }
 #appearance-dialog .appearance-picker-toggle .appearance-current { flex: 1; min-width: 0; overflow-wrap: anywhere; }
 #appearance-dialog .appearance-chevron { width: 17px; height: 17px; flex-shrink: 0; }
 #appearance-dialog .appearance-picker-toggle[aria-expanded="true"] .appearance-chevron { transform: rotate(180deg); }
 #appearance-dialog .appearance-options {
   display: grid; grid-template-columns: 1fr; gap: 7px; margin-top: 10px; padding: 10px;
   border: 1px solid var(--ap-edge); border-radius: 14px; background: var(--ap-surface);
 }
 #appearance-dialog .appearance-palette { grid-template-columns: repeat(auto-fit, minmax(75px, 1fr)); padding: 8px; }
 #appearance-dialog .appearance-option { display: flex; align-items: center; gap: 8px; text-align: left; min-width: 0; padding: 9px; }
 #appearance-dialog .appearance-palette .appearance-option { flex-direction: column; justify-content: center; gap: 5px; font-size: 11px; min-height: 58px; text-align: center; }
 #appearance-dialog .appearance-swatch { display: inline-block; width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%; border: 1px solid #8190a570; background: var(--swatch); }
 #appearance-dialog input[type="text"] { display: block; width: 100%; min-width: 0; font-size: 15px; }
 #appearance-dialog input::placeholder { color: var(--ap-muted); opacity: .8; }
 #appearance-dialog .appearance-disabled-note { color: var(--ap-muted); margin: 10px 0 0; font-size: 12px; line-height: 1.55; }
 #appearance-dialog .appearance-field.is-disabled .appearance-field-label { color: var(--ap-muted); }
 #appearance-dialog .appearance-footer { position: sticky; bottom: 0; flex-shrink: 0; padding: 17px 28px; border-top: 1px solid var(--ap-edge); background: var(--ap-surface); z-index: 2; }
 #appearance-dialog .appearance-footer-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
 #appearance-dialog .appearance-actions { display: flex; align-items: center; gap: 10px; }
 #appearance-dialog .appearance-primary { border-color: var(--ap-accent); background: var(--ap-accent); color: #fff; padding-inline: 25px; }
 #appearance-dialog .appearance-quiet { border-color: transparent; color: var(--ap-muted); background: transparent; padding-inline: 10px; }
 #appearance-error { margin: 0 0 12px; padding: 10px 12px; border-left: 3px solid var(--ap-accent); color: var(--ap-ink); font-size: 13px; line-height: 1.5; }
 #appearance-load-error {
   position: fixed; bottom: 85px; left: 20px; z-index: 100; max-width: min(380px, calc(100vw - 40px));
   margin: 0; padding: 14px 18px; font: 15px/1.5 system-ui, sans-serif;
   color: var(--ink, #16263f); background: var(--card, #fff); border: 1px solid var(--edge, #dce3ed); border-radius: 14px;
 }
 #appearance-open:focus-visible, #appearance-dialog :focus-visible { outline: 3px solid var(--ap-accent); outline-offset: 3px; }
 #appearance-dialog button:disabled, #appearance-dialog input:disabled { opacity: .45; cursor: default; }
 #appearance-open:disabled { opacity: .6; cursor: wait; }
 #appearance-dialog form[aria-busy="true"] { cursor: wait; }
 @media (max-width: 720px) {
   #appearance-dialog { border-radius: 19px; }
   #appearance-dialog .appearance-header { padding: 20px 20px 17px; gap: 10px; }
   #appearance-dialog .appearance-workspace { display: flex; flex-direction: column; }
   #appearance-dialog .appearance-navigation { flex-shrink: 0; padding: 10px 14px; overflow: hidden; border-right: 0; border-bottom: 1px solid var(--ap-edge); }
   #appearance-tabs { flex-direction: row; overflow-x: auto; gap: 5px; padding: 3px; scrollbar-width: thin; }
   #appearance-tabs button { flex: 0 0 auto; width: auto; white-space: nowrap; gap: 8px; padding: 11px 13px; }
   #appearance-dialog .appearance-navigation-note { display: none; }
   #appearance-panels { flex: 1; }
   #appearance-dialog .appearance-panel { padding: 20px; }
   #appearance-dialog .appearance-footer { padding: 13px 20px; }
 }
 @media (max-width: 450px) {
   #appearance-dialog .appearance-header { padding: 17px 15px; }
   #appearance-dialog h2 { font-size: 22px; }
   #appearance-intro { font-size: 12px; }
   #appearance-dialog .appearance-eyebrow { font-size: 10px; }
   #appearance-dialog .appearance-panel { padding: 18px 14px; }
   #appearance-dialog .appearance-field { padding: 15px; }
   #appearance-dialog .appearance-footer { padding: 11px 13px; }
   #appearance-dialog .appearance-footer-row { gap: 7px; }
   #appearance-dialog .appearance-actions { gap: 7px; }
   #appearance-dialog .appearance-footer button { padding-inline: 10px; font-size: 12px; }
 }
"""

JS = """<script>
(() => {
  const open = document.getElementById('appearance-open');
  const dialog = document.getElementById('appearance-dialog');
  const schema = document.getElementById('appearance-fields');
  if (!open || !dialog || !schema) return;
  let fields;
  try { fields = JSON.parse(schema.textContent); } catch (_) { return; }
  if (!Array.isArray(fields)) return;
  const fr = /^fr/i.test(navigator.language || document.documentElement.lang || 'fr');
  const lang = fr ? 'fr' : 'en';
  const words = fr ? {
    settings: 'Réglages', eyebrow: 'VOTRE ACCUEIL', appearance: 'Apparence de la page de liens',
    intro: 'Un accueil à votre image, jusque dans les détails.',
    localHelp: 'Les réglages de voix et de veille restent dans la configuration de l’extension.',
    reset: 'Réinitialiser', cancel: 'Annuler', apply: 'Appliquer', close: 'Fermer',
    sections: 'Rubriques de l’apparence', enabled: 'Activé', disabled: 'Désactivé',
    decrease: 'Diminuer', increase: 'Augmenter',
    loadError: 'Impossible de charger les réglages. Réessayez.',
    saveError: 'Enregistrement impossible. Vos choix sont conservés : réessayez.',
    classicOnly: 'Disponible avec la présentation Classique. Votre choix est conservé.',
    clockHidden: 'Activez l’horloge et la date pour utiliser ce réglage.',
    weatherHidden: 'Activez la météo pour utiliser ce réglage.',
    columnsHelp: 'Automatique adapte le nombre de colonnes à la largeur de l’écran.',
    backgroundHelp: 'Indiquez une adresse d’image, de vidéo ou un chemin local. Laissez vide pour garder le fond du thème.',
    backgroundPlaceholder: 'https://… ou /media/fond.jpg',
    dimHelp: '0 % laisse le fond intact ; 100 % l’assombrit entièrement.',
    focusHelp: 'La couleur qui souligne le lien sélectionné au clavier ou à la télécommande.',
    motionHelp: 'Autorise les vidéos et les images animées du fond.',
    resetHelp: 'Reprendre les réglages de l’extension'
  } : {
    settings: 'Settings', eyebrow: 'YOUR HOME SCREEN', appearance: 'Link page appearance',
    intro: 'Make this home screen yours, down to the details.',
    localHelp: 'Voice and idle settings remain in the add-on configuration.',
    reset: 'Reset', cancel: 'Cancel', apply: 'Apply', close: 'Close',
    sections: 'Appearance sections', enabled: 'On', disabled: 'Off',
    decrease: 'Decrease', increase: 'Increase',
    loadError: 'Could not load settings. Please try again.',
    saveError: 'Could not save. Your choices are kept: please try again.',
    classicOnly: 'Available with the Classic layout. Your choice is kept.',
    clockHidden: 'Show the clock and date to use this setting.',
    weatherHidden: 'Show the weather to use this setting.',
    columnsHelp: 'Automatic adapts the number of columns to the screen width.',
    backgroundHelp: 'Enter an image or video address, or a local path. Leave empty to keep the theme background.',
    backgroundPlaceholder: 'https://… or /media/background.jpg',
    dimHelp: '0% leaves the background unchanged; 100% darkens it completely.',
    focusHelp: 'The color highlighting the link selected with a keyboard or remote.',
    motionHelp: 'Allow background videos and animated images.',
    resetHelp: 'Restore the add-on settings'
  };
  const sections = [
    {key: 'layout', fr: 'Disposition', en: 'Layout',
     description: fr ? 'Choisissez la structure et les couleurs de votre accueil.' : 'Choose the structure and theme of your home screen.',
     icon: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 9v12"/>'},
    {key: 'tiles', fr: 'Liens', en: 'Links',
     description: fr ? 'Ajustez les tuiles et la façon dont vos liens se distinguent.' : 'Adjust your link tiles and how they stand out.',
     icon: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>'},
    {key: 'clock', fr: 'Horloge / date', en: 'Clock / date',
     description: fr ? 'Gardez l’heure et la date à la bonne place, à la bonne taille.' : 'Keep the time and date clear and easy to read.',
     icon: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'},
    {key: 'weather', fr: 'Météo', en: 'Weather',
     description: fr ? 'Personnalisez l’affichage des informations météo.' : 'Customize how your weather information is displayed.',
     icon: '<path d="M7 16a4 4 0 1 1 1-7 5 5 0 0 1 10 1 3 3 0 0 1 0 6Z"/><path d="m8 19-1 2m6-2-1 2m6-2-1 2"/>'},
    {key: 'assistant', fr: 'Assistant', en: 'Assistant',
     description: fr ? 'Choisissez la présence visuelle de votre assistant.' : 'Choose whether your assistant appears on the home screen.',
     icon: '<rect x="4" y="6" width="16" height="14" rx="5"/><path d="M12 3v3M8 11h.01M16 11h.01M9 16h6"/>'},
    {key: 'background', fr: 'Fond d’écran', en: 'Background',
     description: fr ? 'Créez l’ambiance avec une image, une vidéo et une lumière adaptées.' : 'Set the mood with a background image, video, and the right brightness.',
     icon: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>'}
  ];
  const swatches = {
    theme: 'linear-gradient(135deg, #e8eef9 49%, #233350 50%)',
    slate: '#64748b', gray: '#6b7280', zinc: '#71717a', neutral: '#737373', stone: '#78716c',
    red: '#ef4444', amber: '#f59e0b', yellow: '#eab308', lime: '#84cc16', green: '#22c55e',
    emerald: '#10b981', teal: '#14b8a6', cyan: '#06b6d4', sky: '#0ea5e9', blue: '#3b82f6',
    indigo: '#6366f1', violet: '#8b5cf6', purple: '#a855f7', fuchsia: '#d946ef',
    pink: '#ec4899', rose: '#f43f5e', white: '#fff', black: '#121826'
  };
  const form = document.getElementById('appearance-form');
  const tabs = document.getElementById('appearance-tabs');
  const panels = document.getElementById('appearance-panels');
  const error = document.getElementById('appearance-error');
  const loadError = document.getElementById('appearance-load-error');
  const records = new Map();
  const pickers = [];
  let initial = {}, draft = {}, previousFocus = open, saving = false, loading = false;
  let activeSection = sections[0].key;
  const label = item => item[lang] || item.fr || item.en || String(item.value ?? item.key ?? '');
  const button = (text, className = '') => {
    const element = document.createElement('button');
    element.type = 'button'; element.textContent = text; element.className = className;
    return element;
  };
  const icon = path => '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg>';
  const chevron = '<svg class="appearance-chevron" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const swatch = value => {
    const dot = document.createElement('span'); dot.className = 'appearance-swatch';
    dot.style.setProperty('--swatch', swatches[value] || '#64748b'); dot.setAttribute('aria-hidden', 'true');
    return dot;
  };
  document.querySelectorAll('[data-appearance-text]').forEach(element => {
    const value = words[element.dataset.appearanceText];
    if (value) element.textContent = value;
  });
  open.title = words.settings;
  dialog.lang = lang;
  document.getElementById('appearance-close').setAttribute('aria-label', words.close);
  document.getElementById('appearance-reset').title = words.resetHelp;
  dialog.querySelector('.appearance-navigation').setAttribute('aria-label', words.sections);
  function closePickers(except) {
    pickers.forEach(picker => { if (picker !== except) picker.close(); });
  }
  function selectSection(key, focusTab = false) {
    activeSection = key; closePickers();
    tabs.querySelectorAll('[role="tab"]').forEach(tab => {
      const selected = tab.dataset.section === key;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) { tab.focus(); tab.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
    });
    panels.querySelectorAll('[role="tabpanel"]').forEach(panel => { panel.hidden = panel.dataset.section !== key; });
    panels.scrollTop = 0;
  }
  sections.forEach((section, index) => {
    const tab = button('', 'appearance-tab');
    tab.id = 'appearance-tab-' + section.key; tab.dataset.section = section.key;
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'appearance-panel-' + section.key);
    tab.innerHTML = icon(section.icon);
    const text = document.createElement('span'); text.textContent = label(section); tab.append(text);
    tab.addEventListener('click', () => selectSection(section.key));
    tab.addEventListener('keydown', event => {
      let next;
      if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % sections.length;
      if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + sections.length - 1) % sections.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = sections.length - 1;
      if (next !== undefined) { event.preventDefault(); selectSection(sections[next].key, true); }
    });
    tabs.append(tab);
    const panel = document.createElement('div');
    panel.className = 'appearance-panel'; panel.id = 'appearance-panel-' + section.key;
    panel.dataset.section = section.key; panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    const header = document.createElement('div'); header.className = 'appearance-section-header';
    const title = document.createElement('h3'); title.textContent = label(section);
    const description = document.createElement('p'); description.className = 'appearance-section-description'; description.textContent = section.description;
    header.append(title, description);
    const grid = document.createElement('div'); grid.className = 'appearance-fields';
    panel.append(header, grid); panels.append(panel);
  });
  selectSection(activeSection);
  function setValue(key, value) {
    draft[key] = value;
    records.get(key)?.update();
    updateAvailability();
  }
  function makeChoice(field, record) {
    const group = document.createElement('div'); group.className = 'appearance-choice';
    group.id = 'appearance-' + field.key; group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-labelledby', record.label.id);
    const controls = field.options.map((option, index) => {
      const choice = button(label(option)); choice.setAttribute('role', 'radio'); choice.dataset.value = String(option.value);
      choice.addEventListener('click', () => setValue(field.key, option.value));
      choice.addEventListener('keydown', event => {
        let next;
        if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % controls.length;
        if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + controls.length - 1) % controls.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = controls.length - 1;
        if (next !== undefined) { event.preventDefault(); setValue(field.key, field.options[next].value); controls[next].focus(); }
      });
      group.append(choice); return choice;
    });
    record.controls = controls;
    record.update = () => {
      group.dataset.value = String(draft[field.key]); group.value = draft[field.key];
      controls.forEach((choice, index) => {
        const selected = field.options[index].value === draft[field.key];
        choice.setAttribute('aria-checked', String(selected)); choice.tabIndex = selected ? 0 : -1;
      });
    };
    return group;
  }
  function makePicker(field, record) {
    const palette = /(?:^|_)color$/.test(field.key);
    const wrapper = document.createElement('div'); wrapper.className = 'appearance-picker';
    const toggle = button('', 'appearance-picker-toggle'); toggle.id = 'appearance-' + field.key;
    toggle.setAttribute('role', 'combobox'); toggle.setAttribute('aria-haspopup', 'listbox'); toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-labelledby', record.label.id + ' ' + toggle.id + '-current');
    const current = document.createElement('span'); current.className = 'appearance-current'; current.id = toggle.id + '-current';
    const dot = palette ? swatch(draft[field.key]) : null;
    if (dot) toggle.append(dot);
    toggle.append(current); toggle.insertAdjacentHTML('beforeend', chevron);
    const list = document.createElement('div'); list.hidden = true;
    list.id = toggle.id + '-options'; list.className = 'appearance-options' + (palette ? ' appearance-palette' : '');
    list.setAttribute('role', 'listbox'); list.setAttribute('aria-labelledby', record.label.id);
    toggle.setAttribute('aria-controls', list.id);
    const picker = {
      close() { list.hidden = true; toggle.setAttribute('aria-expanded', 'false'); },
      show(focusChoice) {
        closePickers(picker); list.hidden = false; toggle.setAttribute('aria-expanded', 'true');
        if (focusChoice) (options.find((_, index) => field.options[index].value === draft[field.key]) || options[0])?.focus();
      }, list, toggle
    };
    pickers.push(picker);
    const options = field.options.map((option, index) => {
      const choice = button('', 'appearance-option'); choice.dataset.value = String(option.value);
      choice.setAttribute('role', 'option'); choice.tabIndex = -1;
      if (palette) choice.append(swatch(option.value));
      const text = document.createElement('span'); text.textContent = label(option); choice.append(text);
      choice.addEventListener('click', () => { setValue(field.key, option.value); picker.close(); toggle.focus(); });
      choice.addEventListener('keydown', event => {
        let next;
        if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % options.length;
        if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + options.length - 1) % options.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = options.length - 1;
        if (next !== undefined) { event.preventDefault(); options[next].focus(); }
        if (event.key === 'Tab') picker.close();
      });
      list.append(choice); return choice;
    });
    toggle.addEventListener('click', () => { if (list.hidden) picker.show(false); else picker.close(); });
    toggle.addEventListener('keydown', event => {
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); picker.show(true); }
    });
    record.controls = [toggle, ...options];
    record.update = () => {
      const selected = field.options.find(option => option.value === draft[field.key]);
      current.textContent = selected ? label(selected) : String(draft[field.key] ?? '');
      toggle.dataset.value = String(draft[field.key]); toggle.value = draft[field.key];
      if (dot) dot.style.setProperty('--swatch', swatches[draft[field.key]] || '#64748b');
      options.forEach((choice, index) => choice.setAttribute('aria-selected', String(field.options[index].value === draft[field.key])));
    };
    wrapper.append(toggle, list); return wrapper;
  }
  function makeNumber(field, record) {
    const wrapper = document.createElement('div'); wrapper.className = 'appearance-stepper';
    const minus = button('−'), plus = button('+');
    minus.setAttribute('aria-label', words.decrease + ' : ' + label(field));
    plus.setAttribute('aria-label', words.increase + ' : ' + label(field));
    const value = document.createElement('div'); value.id = 'appearance-' + field.key;
    value.className = 'appearance-number'; value.tabIndex = 0; value.setAttribute('role', 'spinbutton');
    value.setAttribute('aria-labelledby', record.label.id); value.setAttribute('aria-valuemin', field.min); value.setAttribute('aria-valuemax', field.max);
    const step = field.key === 'background_dim' ? 10 : (field.step || 1);
    const move = amount => setValue(field.key, Math.max(field.min, Math.min(field.max, Number(draft[field.key]) + amount)));
    minus.addEventListener('click', () => move(-step)); plus.addEventListener('click', () => move(step));
    value.addEventListener('keydown', event => {
      if (record.disabled || saving) return;
      if (['ArrowUp', 'ArrowRight', 'ArrowDown', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        if (event.key === 'Home') setValue(field.key, field.min);
        else if (event.key === 'End') setValue(field.key, field.max);
        else move(['ArrowUp', 'ArrowRight'].includes(event.key) ? step : -step);
      }
    });
    record.controls = [minus, plus];
    record.update = () => {
      const number = draft[field.key];
      const option = (field.options || []).find(item => item.value === number);
      const text = option ? label(option) : String(number) + (field.key === 'background_dim' ? ' %' : '');
      value.textContent = text; value.dataset.value = String(number); value.value = number;
      value.setAttribute('aria-valuenow', number); value.setAttribute('aria-valuetext', text);
    };
    record.setDisabled = disabled => {
      minus.disabled = disabled || draft[field.key] <= field.min;
      plus.disabled = disabled || draft[field.key] >= field.max;
      value.setAttribute('aria-disabled', String(disabled)); value.tabIndex = disabled ? -1 : 0;
    };
    wrapper.append(minus, value, plus); return wrapper;
  }
  function makeBool(field, record) {
    const toggle = button('', 'appearance-switch'); toggle.id = 'appearance-' + field.key;
    toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-labelledby', record.label.id);
    const text = document.createElement('span'), track = document.createElement('span');
    track.className = 'appearance-switch-track'; track.setAttribute('aria-hidden', 'true'); toggle.append(text, track);
    toggle.addEventListener('click', () => setValue(field.key, !draft[field.key]));
    record.controls = [toggle];
    record.update = () => {
      toggle.setAttribute('aria-checked', String(draft[field.key])); toggle.dataset.value = String(draft[field.key]);
      toggle.value = draft[field.key]; text.textContent = draft[field.key] ? words.enabled : words.disabled;
    };
    return toggle;
  }
  function makeText(field, record) {
    const input = document.createElement('input'); input.type = 'text'; input.id = 'appearance-' + field.key;
    input.autocomplete = 'off'; input.spellcheck = false;
    if (field.max_length) input.maxLength = field.max_length;
    if (field.key === 'background') input.placeholder = words.backgroundPlaceholder;
    input.addEventListener('input', () => setValue(field.key, input.value));
    record.controls = [input]; record.update = () => { if (input.value !== draft[field.key]) input.value = draft[field.key] ?? ''; };
    return input;
  }
  function renderFields() {
    closePickers(); pickers.length = 0; records.clear();
    panels.querySelectorAll('.appearance-fields').forEach(grid => grid.replaceChildren());
    fields.forEach(field => {
      const grid = panels.querySelector('#appearance-panel-' + field.section + ' .appearance-fields');
      if (!grid || !Object.prototype.hasOwnProperty.call(draft, field.key)) return;
      const card = document.createElement('div'); card.className = 'appearance-field'; card.dataset.key = field.key;
      const title = document.createElement('label'); title.className = 'appearance-field-label'; title.id = 'appearance-label-' + field.key;
      title.htmlFor = 'appearance-' + field.key; title.textContent = label(field);
      const note = document.createElement('p'); note.className = 'appearance-disabled-note'; note.hidden = true;
      note.id = 'appearance-note-' + field.key;
      const record = {field, card, label: title, note, controls: [], update() {}};
      let control;
      if (field.kind === 'choice') control = (/(?:^|_)color$/.test(field.key) || field.options.length > 4) ? makePicker(field, record) : makeChoice(field, record);
      else if (field.kind === 'number' || field.kind === 'int') control = makeNumber(field, record);
      else if (field.kind === 'bool') control = makeBool(field, record);
      else control = makeText(field, record);
      card.append(title, control);
      const helpKey = {columns: 'columnsHelp', background: 'backgroundHelp', background_dim: 'dimHelp', focus_color: 'focusHelp', background_motion: 'motionHelp'}[field.key];
      if (helpKey) {
        const help = document.createElement('p'); help.className = 'appearance-field-help'; help.id = 'appearance-help-' + field.key; help.textContent = words[helpKey]; card.append(help);
        const focusTarget = control.id === 'appearance-' + field.key ? control : control.querySelector('[id="appearance-' + field.key + '"]') || control;
        focusTarget.setAttribute('aria-describedby', help.id);
      }
      card.append(note); grid.append(card); records.set(field.key, record); record.update();
    });
    updateAvailability();
  }
  function updateAvailability() {
    records.forEach(record => {
      const key = record.field.key;
      let reason = '';
      if (draft.layout === 'dashboard' && ['align', 'tiles'].includes(key)) reason = words.classicOnly;
      if (draft.clock === false && ['clock_size', 'clock_color', 'date_size', 'date_color'].includes(key)) reason = words.clockHidden;
      if (draft.weather_visible === false && ['weather_size', 'weather_color'].includes(key)) reason = words.weatherHidden;
      record.disabled = Boolean(reason); record.card.classList.toggle('is-disabled', record.disabled);
      record.note.textContent = reason; record.note.hidden = !reason;
      record.controls.forEach(control => { control.disabled = saving || record.disabled; });
      if (record.setDisabled) record.setDisabled(saving || record.disabled);
      if (record.disabled) record.controls.forEach(control => control.setAttribute('aria-describedby', record.note.id));
      else record.controls.forEach(control => {
        if (control.getAttribute('aria-describedby') === record.note.id) control.removeAttribute('aria-describedby');
      });
    });
  }
  open.addEventListener('click', async () => {
    if (loading || dialog.open) return;
    loading = true; previousFocus = document.activeElement;
    open.disabled = true; open.setAttribute('aria-busy', 'true'); loadError.hidden = true; error.hidden = true;
    try {
      const response = await fetch('/appearance.json', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) throw new Error('load');
      const values = await response.json();
      if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('values');
      initial = {...values}; draft = {...values}; renderFields(); selectSection('layout');
      dialog.showModal();
      const firstChoice = panels.querySelector('[role="tabpanel"]:not([hidden]) .appearance-choice button[aria-checked="true"]') || panels.querySelector('[role="tabpanel"]:not([hidden]) button:not(:disabled), [role="tabpanel"]:not([hidden]) input:not(:disabled)');
      (firstChoice || document.getElementById('appearance-close')).focus();
    } catch (_) {
      loadError.textContent = words.loadError; loadError.hidden = false;
    } finally {
      loading = false; open.disabled = false; open.removeAttribute('aria-busy');
    }
  });
  dialog.addEventListener('click', event => { if (!event.target.closest('.appearance-picker')) closePickers(); });
  dialog.addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.key === 'Escape') {
      const picker = pickers.find(item => !item.list.hidden);
      if (picker) { event.preventDefault(); picker.close(); picker.toggle.focus(); }
    }
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); if (!saving) dialog.close(); });
  dialog.addEventListener('close', () => {
    closePickers(); initial = {}; draft = {};
    (previousFocus && previousFocus.isConnected ? previousFocus : open).focus();
  });
  ['appearance-cancel', 'appearance-close'].forEach(id => document.getElementById(id).addEventListener('click', () => { if (!saving) dialog.close(); }));
  async function save(payload) {
    if (saving) return;
    saving = true; error.hidden = true; closePickers(); form.setAttribute('aria-busy', 'true');
    const controls = Array.from(form.querySelectorAll('button, input'));
    controls.forEach(control => { control.disabled = true; }); updateAvailability();
    try {
      const response = await fetch('/appearance.json', {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error('save');
      location.reload();
    } catch (_) {
      error.textContent = words.saveError; error.hidden = false; if (dialog.open) error.focus();
    } finally {
      saving = false; form.removeAttribute('aria-busy');
      controls.forEach(control => { control.disabled = false; }); updateAvailability();
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const changes = {};
    fields.forEach(field => { if (draft[field.key] !== initial[field.key]) changes[field.key] = draft[field.key]; });
    if (Object.keys(changes).length) save(changes); else dialog.close();
  });
  document.getElementById('appearance-reset').addEventListener('click', () => save({reset: true}));
})();
</script>"""
