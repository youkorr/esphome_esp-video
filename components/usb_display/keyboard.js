/* An on-screen keyboard for a panel that has no keys.
 *
 * This lives in the page rather than on the board, and that is the whole
 * design. The panel's contacts already arrive here as presses; a keyboard
 * drawn in the page is pressed by exactly the same path, needs no protocol,
 * no firmware and no recompilation, and works on every field Home Assistant
 * has -- a login, a search box, the text field of a card -- without knowing
 * anything about them.
 *
 * Two things make it work with a modern frontend rather than against it:
 *
 *   Focus is never taken. Every touch on a key calls preventDefault on
 *   pointerdown, so the field keeps the caret and stays the active element.
 *
 *   Values are set through the native setter and announced with a composed,
 *   bubbling input event. Assigning to .value directly is invisible to Lit
 *   and Polymer, which is what Home Assistant is built from, and the text
 *   would appear on screen and never reach the component behind it.
 */
(function () {
  if (window.__udispKeyboard) return;
  window.__udispKeyboard = true;

  var LAYOUTS = {
    azerty: [
      "1234567890",
      "azertyuiop",
      "qsdfghjklm",
      "⇧wxcvbn'⌫",
      "éèàçù",
    ],
    qwerty: [
      "1234567890",
      "qwertyuiop",
      "asdfghjkl",
      "⇧zxcvbnm⌫",
      "-_/@.",
    ],
  };
  var SHIFTED = { "'": "?", "-": "_", "/": "\\", "@": "#", ".": ":" };
  var DIGIT_SHIFT = { "1": "&", "2": "é", "3": "\"", "4": "'", "5": "(",
                      "6": "-", "7": "è", "8": "_", "9": "ç", "0": "à" };

  var layout = localStorage.getItem("udispKeyboardLayout") || "LAYOUT_DEFAULT";
  if (!LAYOUTS[layout]) layout = "azerty";
  var shift = false;
  var target = null;

  /* The active element, following shadow roots. Home Assistant puts almost
     every field inside one, so document.activeElement alone only ever names
     the outermost custom element.

     Walking activeElement down the roots is not enough on its own: the chain
     breaks at any host whose own activeElement is null -- a field reached
     through a slot, or one root deeper than the walk expected -- and it stops
     on the wrapper rather than the field. Where there is an event to ask, its
     composed path is the honest answer, because it lists the real target
     first whatever it is nested in. */
  function deepActive(event) {
    if (event && event.composedPath) {
      var path = event.composedPath();
      if (path && path.length) return path[0];
    }
    var el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) {
      el = el.shadowRoot.activeElement;
    }
    return el;
  }

  function isTextField(el) {
    if (!el) return false;
    // Inherited, so this covers a code editor as well: CodeMirror focuses a
    // div inside the editable area rather than the editable element itself.
    if (el.isContentEditable) return true;
    if (el.getAttribute && el.getAttribute("role") === "textbox") return true;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea") return true;
    if (tag !== "input") return false;
    return /^(text|search|password|email|url|tel|number|)$/.test(el.type || "");
  }

  /* Setting .value is not enough: Lit and Polymer listen for the event, and
     the native setter is what a real keystroke would have gone through. */
  function insert(text) {
    if (!target) return;
    if (target.isContentEditable) {
      document.execCommand("insertText", false, text);
      return;
    }
    var proto = target instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    var start = target.selectionStart, end = target.selectionEnd;
    var value = target.value || "";
    if (start === null || start === undefined) { start = end = value.length; }
    setter.call(target, value.slice(0, start) + text + value.slice(end));
    try { target.setSelectionRange(start + text.length, start + text.length); } catch (e) {}
    target.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    // A field rebuilt by the frontend can come back without the caret. Put it
    // back, or the next key goes nowhere.
    if (deepActive() !== target) { try { target.focus(); } catch (e) {} }
  }

  function backspace() {
    if (!target) return;
    if (target.isContentEditable) { document.execCommand("delete", false); return; }
    var proto = target instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    var start = target.selectionStart, end = target.selectionEnd;
    var value = target.value || "";
    if (start === null || start === undefined) { start = end = value.length; }
    if (start === end) start = Math.max(0, start - 1);
    setter.call(target, value.slice(0, start) + value.slice(end));
    try { target.setSelectionRange(start, start); } catch (e) {}
    target.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  }

  function submit() {
    if (!target) return;
    ["keydown", "keypress", "keyup"].forEach(function (type) {
      target.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, composed: true, cancelable: true,
      }));
    });
    target.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  var root = document.createElement("div");
  root.id = "udisp-keyboard";
  /* The top layer, where a modal dialog also lives. Without this the keyboard
     sits in the ordinary stacking order, and no z-index however large paints
     above a dialog opened with showModal -- which is how Home Assistant shows
     several of the things one would want to type into. "manual" is the kind
     that neither takes focus nor closes itself when something else is
     pressed, which is exactly what a keyboard needs. */
  try { root.popover = "manual"; } catch (e) {}
  var style = document.createElement("style");
  style.textContent =
    "#udisp-keyboard{position:fixed;left:0;right:0;bottom:0;top:auto;" +
    "width:auto;height:auto;max-width:none;max-height:none;margin:0;border:0;" +
    "z-index:2147483647;overflow:visible;" +
    "background:#1c1c1e;padding:4px;box-sizing:border-box;display:none;" +
    "font:600 18px system-ui,sans-serif;user-select:none;-webkit-user-select:none;" +
    "box-shadow:0 -4px 16px rgba(0,0,0,.5)}" +
    "#udisp-keyboard.on{display:block}" +
    "#udisp-keyboard:popover-open{display:block}" +
    "#udisp-handle{position:fixed;right:8px;bottom:8px;z-index:2147483646;" +
    "width:44px;height:44px;border:0;border-radius:22px;background:#3a3a3ccc;" +
    "color:#fff;font:22px system-ui;padding:0;opacity:.55}" +
    "#udisp-handle:active{opacity:1}" +
    "#udisp-keyboard .row{display:flex;gap:4px;margin:3px 0;justify-content:center}" +
    // Sized from the panel, not from a number that happened to suit one of
    // them: six rows of a fixed 44 px is over half of a 600-pixel screen.
    "#udisp-keyboard button{flex:1 1 0;min-width:0;border:0;border-radius:7px;" +
    "height:clamp(22px,5.5vh,42px);font-size:clamp(12px,2.2vh,19px);" +
    "background:#3a3a3c;color:#fff;font-family:inherit;font-weight:600;padding:0}" +
    // Held by a class rather than left to :active. A press is a few
    // milliseconds and the picture is sampled every few more, so the
    // highlight has to outlast the finger or it is never once captured --
    // which is why pressing a key looked like nothing happening.
    "#udisp-keyboard button.pressed{background:#0a84ff;color:#fff}" +
    "#udisp-keyboard button.wide{flex:2 1 0}" +
    "#udisp-keyboard button.space{flex:6 1 0}" +
    "#udisp-keyboard button.on{background:#0a84ff}";
  root.appendChild(style);
  var keys = document.createElement("div");
  root.appendChild(keys);

  /* When the keyboard was last touched. A frontend that rebuilds a field on
     every keystroke -- which is what a reactive one does -- blurs and
     refocuses it in the process, and the blur used to be read as the user
     having left. Pressing a key made the keyboard vanish. */
  var touchedAt = 0;

  function key(label, cls, onPress) {
    var b = document.createElement("button");
    b.textContent = label;
    if (cls) b.className = cls;
    // The field must keep focus, so the press must never reach the browser's
    // own focus handling.
    b.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      touchedAt = Date.now();
    });
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function (e) {
      e.preventDefault();
      b.classList.add("pressed");
      setTimeout(function () { b.classList.remove("pressed"); }, 140);
      onPress();
    });
    return b;
  }

  function render() {
    keys.textContent = "";
    LAYOUTS[layout].forEach(function (line, index) {
      var row = document.createElement("div");
      row.className = "row";
      Array.prototype.forEach.call(line, function (ch) {
        if (ch === "⇧") {
          var s = key("⇧", "wide" + (shift ? " on" : ""), function () {
            shift = !shift; render();
          });
          row.appendChild(s);
          return;
        }
        if (ch === "⌫") {
          row.appendChild(key("⌫", "wide", backspace));
          return;
        }
        var shown = ch;
        if (shift) shown = SHIFTED[ch] || DIGIT_SHIFT[ch] || ch.toUpperCase();
        row.appendChild(key(shown, "", function () {
          insert(shown);
          if (shift) { shift = false; render(); }
        }));
      });
      keys.appendChild(row);
      if (index !== LAYOUTS[layout].length - 1) return;
      // Last row: the layout switch, a space bar and the two ways out.
      var last = document.createElement("div");
      last.className = "row";
      last.appendChild(key(layout === "azerty" ? "QWERTY" : "AZERTY", "wide", function () {
        layout = layout === "azerty" ? "qwerty" : "azerty";
        localStorage.setItem("udispKeyboardLayout", layout);
        shift = false;
        render();
      }));
      last.appendChild(key("␣", "space", function () { insert(" "); }));
      last.appendChild(key("⏎", "wide", function () { submit(); hide(); }));
      last.appendChild(key("✕", "", function () { hide(); if (target) target.blur(); }));
      keys.appendChild(last);
    });
  }

  /* The nearest open modal dialog above an element, crossing shadow
     boundaries by hopping from each root to its host. */
  function modalAbove(el) {
    var node = el;
    while (node) {
      if (node.nodeType === 1 && node.tagName === "DIALOG") {
        try { if (node.matches(":modal")) return node; } catch (e) {}
      }
      node = node.parentNode || (node.getRootNode && node.getRootNode().host);
      if (node && node.nodeType === 11) node = node.host;
    }
    return null;
  }

  /* Is the keyboard actually pressable where it is drawn? */
  function reachable() {
    var r = root.getBoundingClientRect();
    if (r.height < 10) return false;
    var at = document.elementFromPoint(r.left + r.width / 2, r.top + 6);
    return !!at && (at === root || root.contains(at));
  }

  function moveTo(host) {
    if (!host || root.parentNode === host) return;
    var was = root.classList.contains("on");
    try { root.hidePopover(); } catch (e) {}
    host.appendChild(root);
    if (handle.parentNode !== host) host.appendChild(handle);
    if (was) {
      root.classList.add("on");
      try { root.showPopover(); } catch (e) {}
    }
  }

  function show(el) {
    target = el;

    /* Parked inside a dialog that has since closed? Come home first: a closed
       dialog does not render its children, so staying there means never being
       seen again. */
    var parent = root.parentNode;
    if (parent && parent.nodeType === 1 && parent.tagName === "DIALOG") {
      var stillModal = false;
      try { stillModal = parent.matches(":modal"); } catch (e) {}
      if (!stillModal) moveTo(document.body || document.documentElement);
    }

    root.classList.add("on");
    try { root.showPopover(); } catch (e) {}

    /* A modal dialog makes everything outside itself inert -- the top layer
       included -- so a keyboard in the body can be drawn above such a dialog
       and still not be pressable through it. Moving it inside the dialog
       fixes that, and costs something everywhere else: within a dialog it
       inherits that dialog's box and its transforms.
       So it is not moved on the guess that it might be needed. It is moved
       when it is demonstrably not reachable where it is -- which for Home
       Assistant, whose overlays are not native dialogs, is never. */
    if (!reachable()) {
      var modal = modalAbove(el);
      if (modal) moveTo(modal);
    }
    // A field under the keyboard cannot be seen while it is typed into.
    try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
  }

  function hide() {
    root.classList.remove("on");
    try { root.hidePopover(); } catch (e) {}
    target = null;
  }

  /* Somewhere to press when the focus was never noticed.
     Home Assistant is a large moving target and a field it introduces
     tomorrow may not look like one to the check above. A panel whose
     keyboard cannot be summoned by hand would simply be unusable that day,
     so there is always a way in. */
  var handle = document.createElement("button");
  handle.id = "udisp-handle";
  handle.textContent = "\u2328";
  handle.setAttribute("aria-label", "clavier");
  handle.addEventListener("pointerdown", function (e) { e.preventDefault(); });
  handle.addEventListener("mousedown", function (e) { e.preventDefault(); });
  handle.addEventListener("click", function (e) {
    e.preventDefault();
    if (root.classList.contains("on")) { hide(); return; }
    // Whatever has the focus, even if the check above would not have called
    // it a field; and if that element hides a field of its own, take that.
    var el = deepActive();
    if (!isTextField(el) && el && el.shadowRoot) {
      var inner = el.shadowRoot.querySelector("input,textarea,[contenteditable]");
      if (inner) { try { inner.focus(); } catch (err) {} el = deepActive(); }
    }
    show(isTextField(el) ? el : deepActive());
  });

  /* This runs before the page's own scripts -- that is the point, so it is
     there whatever the frontend does to the document afterwards -- which also
     means the document may not have a body to attach to yet. */
  function attach() {
    var host = document.body || document.documentElement;
    host.appendChild(root);
    host.appendChild(handle);
    render();
  }
  if (document.body) {
    attach();
  } else {
    document.addEventListener("DOMContentLoaded", attach, { once: true });
  }

  // focusin crosses shadow boundaries where focus does not.
  document.addEventListener("focusin", function (e) {
    var el = deepActive(e);
    if (isTextField(el)) show(el); else if (target && !root.contains(el)) hide();
  }, true);
  document.addEventListener("focusout", function () {
    // Generously late, and never while the keyboard is being used. Leaving is
    // deliberate -- another field, the cross, or somewhere else entirely --
    // and none of those are undone by waiting a moment longer, where hiding
    // in the middle of typing costs the whole gesture.
    setTimeout(function () {
      if (Date.now() - touchedAt < 1000) return;
      var el = deepActive();
      if (!isTextField(el)) hide();
    }, 400);
  }, true);
  /* A field can be entered by being pressed rather than by being focused --
     a code editor moves the caret without the browser calling it a new focus
     -- so a press inside one counts too. */
  document.addEventListener("pointerup", function (e) {
    var el = deepActive(e);
    if (isTextField(el)) show(el);
  }, true);
})();
