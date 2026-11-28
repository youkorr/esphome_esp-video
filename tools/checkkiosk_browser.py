"""Real Chromium smoke test of the kiosk context and its PIN keypad."""
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portall"))
from kiosk import Kiosk
from playwright.sync_api import sync_playwright, Error

HTML = b"""<!doctype html><body><home-assistant></home-assistant>
<a id="admin" href="/config">Settings</a>
<script>
const a=document.querySelector('home-assistant').attachShadow({mode:'open'});
a.innerHTML='<home-assistant-main></home-assistant-main>';
const b=a.querySelector('home-assistant-main').attachShadow({mode:'open'});
b.innerHTML='<ha-sidebar>Menu</ha-sidebar><ha-panel-lovelace></ha-panel-lovelace>';
const c=b.querySelector('ha-panel-lovelace').attachShadow({mode:'open'});
c.innerHTML='<hui-root></hui-root>';
const d=c.querySelector('hui-root').attachShadow({mode:'open'});
d.innerHTML='<app-header>Dashboard header</app-header><main>Dashboard</main>';
</script></body>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML)

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(service_workers="block")
        kiosk = Kiosk(base + "/dashboard-wall/0", "123456")
        kiosk.install(context)
        page = context.new_page()
        kiosk.page = page
        page.goto(kiosk.url)
        page.locator("ha-sidebar").wait_for(state="hidden")
        page.locator("app-header").wait_for(state="hidden")
        page.evaluate("history.pushState({}, '', '/config')")
        assert page.url == kiosk.url
        page.locator("#admin").click()
        assert page.url == kiosk.url
        page.evaluate("history.pushState({}, '', '/dashboard-wall/sub')")
        assert page.url.endswith("/dashboard-wall/sub")
        try:
            page.goto(base + "/config")
            raise AssertionError("navigation should be blocked")
        except Error:
            pass
        page.goto(kiosk.url)
        kiosk.gesture()
        for n in "123456":
            page.get_by_role("button", name=n, exact=True).click()
        page.get_by_role("button", name="OK", exact=True).click()
        page.wait_for_timeout(200)
        assert not kiosk.locked and kiosk.reload_required
        page.goto(kiosk.url)
        page.locator("ha-sidebar").wait_for(state="visible")
        page.goto(base + "/config")
        kiosk.lock()
        page.goto(kiosk.url)
        page.locator("ha-sidebar").wait_for(state="hidden")
        # A different browser context stays unrestricted.
        other = browser.new_page()
        other.goto(base + "/config")
        other.locator("ha-sidebar").wait_for(state="visible")
        assert other.url.endswith("/config")
        browser.close()
        print("Kiosk Chromium navigation, chrome, PIN and session isolation: OK")
finally:
    server.shutdown()
