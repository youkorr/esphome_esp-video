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
     the outermost custom element. */
  function deepActive() {
    var el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) {
      el = el.shadowRoot.activeElement;
    }
    return el;
  }

  function isTextField(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
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
  root.setAttribute("part", "udisp-keyboard");
  var style = document.createElement("style");
  style.textContent =
    "#udisp-keyboard{position:fixed;left:0;right:0;bottom:0;z-index:2147483647;" +
    "background:#1c1c1e;padding:6px;box-sizing:border-box;display:none;" +
    "font:600 18px system-ui,sans-serif;user-select:none;-webkit-user-select:none;" +
    "box-shadow:0 -4px 16px rgba(0,0,0,.5)}" +
    "#udisp-keyboard.on{display:block}" +
    "#udisp-keyboard .row{display:flex;gap:5px;margin:5px 0;justify-content:center}" +
    "#udisp-keyboard button{flex:1 1 0;min-width:0;height:44px;border:0;border-radius:7px;" +
    "background:#3a3a3c;color:#fff;font:inherit;padding:0}" +
    "#udisp-keyboard button:active{background:#5a5a5e}" +
    "#udisp-keyboard button.wide{flex:2 1 0}" +
    "#udisp-keyboard button.space{flex:6 1 0}" +
    "#udisp-keyboard button.on{background:#0a84ff}";
  root.appendChild(style);
  var keys = document.createElement("div");
  root.appendChild(keys);

  function key(label, cls, onPress) {
    var b = document.createElement("button");
    b.textContent = label;
    if (cls) b.className = cls;
    // The field must keep focus, so the press must never reach the browser's
    // own focus handling.
    b.addEventListener("pointerdown", function (e) { e.preventDefault(); });
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function (e) { e.preventDefault(); onPress(); });
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

  function show(el) {
    target = el;
    root.classList.add("on");
    // A field under the keyboard cannot be seen while it is typed into.
    try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
  }
  function hide() { root.classList.remove("on"); target = null; }

  /* This runs before the page's own scripts -- that is the point, so it is
     there whatever the frontend does to the document afterwards -- which also
     means the document may not have a body to attach to yet. */
  function attach() {
    (document.body || document.documentElement).appendChild(root);
    render();
  }
  if (document.body) {
    attach();
  } else {
    document.addEventListener("DOMContentLoaded", attach, { once: true });
  }

  // focusin crosses shadow boundaries where focus does not.
  document.addEventListener("focusin", function () {
    var el = deepActive();
    if (isTextField(el)) show(el); else if (target && !root.contains(el)) hide();
  }, true);
  document.addEventListener("focusout", function () {
    setTimeout(function () {
      var el = deepActive();
      if (!isTextField(el)) hide();
    }, 0);
  }, true);
})();
