"""Per-panel dashboard navigation guard. Not a Home Assistant authorization layer."""
import hmac
import json
import re
import time
from urllib.parse import urlsplit


# HA built-in panels are not selectable dashboards. Custom dashboards use
# a URL slug containing a hyphen. This is not an authorization check.
RESERVED = {"developer-tools", "media-browser", "config", "profile",
            "history", "logbook", "map", "energy", "auth", "api",
            "my-home", "home-assistant"}


def dashboard_scope(url):
    """Accept a Lovelace root or custom dashboard URL, not HA admin routes."""
    p = urlsplit(url)
    root = p.path.split("/")[1] if p.path.startswith("/") else ""
    if (p.scheme not in ("http", "https") or not p.hostname or
            p.username is not None or p.password is not None or
            root in RESERVED or
            not (root == "lovelace" or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", root))
            or "%" in p.path or "\\" in p.path or
            any(part in (".", "..") for part in p.path.split("/"))):
        raise ValueError("Kiosk needs an explicit Lovelace/custom dashboard URL")
    return (p.scheme, p.netloc.lower(), "/" + root)


def permitted(url, scope):
    try:
        p = urlsplit(url)
        scheme, host, root = scope
        return (p.scheme == scheme and p.netloc.lower() == host and
                "%" not in p.path and "\\" not in p.path and
                not any(part in (".", "..") for part in p.path.split("/")) and
                (p.path == root or p.path.startswith(root + "/")))
    except ValueError:
        return False


# Run inside the individual browser context, not HA configuration. Hide the
# navigation chrome in the relevant HA shadow roots; history checks prevent
# ordinary SPA navigation before the sender's independent URL check.
INIT = r"""(() => {
    if (window !== window.top) return;
    const scope = __SCOPE__;
    let locked = true;
    const allowed = value => {
        try {
            const u = new URL(value, location.href);
            return u.protocol === scope[0] + ':' &&
                u.host.toLowerCase() === scope[1] &&
                !u.pathname.includes('%') &&
                (u.pathname === scope[2] || u.pathname.startsWith(scope[2] + '/'));
        } catch (_) { return false; }
    };
    for (const name of ['pushState', 'replaceState']) {
        const original = history[name];
        history[name] = function(state, title, url) {
            if (locked && url != null && !allowed(url)) return;
            return original.apply(this, arguments);
        };
    }
    const walk = root => {
        const host = root.host;
        const tag = host && host.localName;
        const styleId = 'portall-kiosk-chrome';
        let style = root.querySelector('#' + styleId);
        if (locked && !style) {
            style = document.createElement('style');
            style.id = styleId;
            style.textContent = 'ha-sidebar,ha-menu-button{display:none!important}' +
                (tag === 'hui-root' || tag === 'ha-panel-lovelace'
                 ? 'app-header,ha-app-layout > .header,.header{display:none!important}' : '');
            (root === document ? document.head : root)?.appendChild(style);
        } else if (!locked && style) style.remove();
        for (const e of root.querySelectorAll(
            'home-assistant,home-assistant-main,ha-drawer,ha-panel-lovelace,hui-root,partial-panel-resolver,ha-app-layout')) {
            if (e.shadowRoot) walk(e.shadowRoot);
        }
    };
    const refresh = () => { if (document.head) walk(document); };
    window.__portallKioskState().then(value => {
        locked = value;
        refresh();
    }).catch(() => {});
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh);
    else refresh();
    // HA creates its shadow roots asynchronously. No work on other contexts.
    setInterval(() => { if (locked) refresh(); }, 750);
    document.addEventListener('click', event => {
        if (!locked) return;
        const link = event.composedPath().find(e => e && e.tagName === 'A' && e.href);
        if (link && !allowed(link.href)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
})();"""

PIN_DIALOG = r"""() => {
    if (document.getElementById('portall-kiosk-pin')) return;
    const host = document.createElement('div');
    host.id = 'portall-kiosk-pin';
    host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;background:#000b;';
    const root = host.attachShadow({mode:'open'});
    root.innerHTML = '<style>:host{font:18px system-ui} section{background:#20242b;color:white;padding:24px;border-radius:18px;width:min(300px,80vw);text-align:center} output{display:block;height:42px;font-size:25px;letter-spacing:5px} .keys{display:grid;grid-template-columns:repeat(3,1fr);gap:8px} button{background:#414954;color:white;border:0;border-radius:10px;min-height:52px;font:inherit} p{font-size:14px;min-height:22px}</style><section><h2>Portall · PIN</h2><output></output><div class="keys"></div><p></p><button id="cancel">Cancel</button></section>';
    let value = '';
    const output = root.querySelector('output');
    const status = root.querySelector('p');
    const keys = root.querySelector('.keys');
    const show = () => output.textContent = '•'.repeat(value.length);
    for (const key of ['1','2','3','4','5','6','7','8','9','⌫','0','OK']) {
        const button = document.createElement('button');
        button.textContent = key;
        button.addEventListener('click', async () => {
            if (key === '⌫') value = value.slice(0,-1);
            else if (key === 'OK') {
                const code = value;
                value = ''; show();
                button.disabled = true;
                try {
                    if (await window.__portallKioskUnlock(code)) host.remove();
                    else status.textContent = 'Incorrect PIN or temporarily locked';
                } catch (_) { status.textContent = 'Unlock unavailable'; }
                finally { button.disabled = false; }
            } else if (value.length < 8) value += key;
            show();
        });
        keys.appendChild(button);
    }
    root.querySelector('#cancel').onclick = () => host.remove();
    document.body.appendChild(host);
    setTimeout(() => host.remove(), 60000);
}"""


class Kiosk:
    def __init__(self, url, pin="", clock=time.monotonic):
        self.scope = dashboard_scope(url)
        if pin and not re.fullmatch(r"[0-9]{4,8}", pin):
            raise ValueError("Kiosk PIN must contain 4 to 8 digits")
        self.url, self.pin, self.clock = url, pin, clock
        self.locked = True
        self.page = None
        self.armed_until = self.unlocked_until = self.blocked_until = 0
        self.failures = 0
        self.reload_required = False

    def allowed(self, url):
        return not self.locked or permitted(url, self.scope)

    def _source_ok(self, source):
        return (self.page is not None and source.get("page") is self.page
                and source.get("frame") is self.page.main_frame)

    def _state(self, source):
        return self.locked if self._source_ok(source) else True

    def _unlock(self, source, pin):
        now = self.clock()
        if (not self._source_ok(source) or not self.locked or not self.pin
                or not permitted(self.page.url, self.scope)
                or now >= self.armed_until or now < self.blocked_until):
            return False
        if not isinstance(pin, str) or not hmac.compare_digest(self.pin, pin):
            self.failures += 1
            if self.failures >= 5:
                self.failures = 0
                self.blocked_until = now + 60
                self.armed_until = 0
            return False
        self.failures = 0
        self.armed_until = 0
        self.locked = False
        self.unlocked_until = now + 300
        self.reload_required = True
        return True

    def lock(self):
        if not self.locked:
            self.locked = True
            self.armed_until = 0
            self.reload_required = True

    def tick(self, awake):
        if not awake or (not self.locked and self.clock() >= self.unlocked_until):
            self.lock()

    def gesture(self):
        if not self.locked:
            self.lock()
        elif self.pin and self.clock() >= self.blocked_until:
            self.armed_until = self.clock() + 60
            self.page.evaluate(PIN_DIALOG)

    def install(self, context):
        context.expose_binding("__portallKioskState", self._state)
        context.expose_binding("__portallKioskUnlock", self._unlock)
        context.add_init_script(INIT.replace("__SCOPE__", json.dumps(self.scope)))
        context.route("**/*", self.route)

    def route(self, route):
        # Keep HA resources/API calls untouched. Documents outside the allowed
        # dashboard are blocked while locked, including popup/iframe documents.
        request = route.request
        if (self.locked and request.is_navigation_request()
                and not permitted(request.url, self.scope)):
            route.abort()
        else:
            route.continue_()
