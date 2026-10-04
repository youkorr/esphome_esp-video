#!/usr/bin/env python3
"""A zoom a person set on a site must not shrink it on the panel.

WHY THIS EXISTS. Reported with a photograph of google.com on a panel, drawn
at about two thirds of its size, and taps that no longer hit what they were
on: "la page ne fonctionne plus et plus petite". The sign-in page's plain
Chrome shares the panel's profile, noVNC turns a pinch on the telephone into
Ctrl + wheel, and Chrome saves that as a page zoom for the site -- which the
panel's driven browser then applies too.

  - a profile carrying a saved zoom for a host, in the exact shape Chrome
    writes it (captured from a real Chrome zoomed with Ctrl+minus), lays
    the page out wider than the viewport -- the report, reproduced;
  - a tap at the panel's middle then lands off the middle of what is shown;
  - after ha_send.forget_zoom() the same profile lays it out at the
    viewport's own size, and the tap lands where the finger is;
  - the rest of the Preferences file, and a profile with no zoom, are left
    as they were.

Needs Playwright. Takes $CHROMIUM for the browser.
"""
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "components" / "portall"))
BROWSER = os.environ.get("CHROMIUM") or None
W, H = 1280, 730
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = (b"<!doctype html><body style='margin:0'>"
                b"<script>addEventListener('mousedown',e=>window.hit="
                b"[e.clientX,e.clientY])</script>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def look(pw, profile, url):
    context = pw.chromium.launch_persistent_context(
        profile, headless=True, viewport={"width": W, "height": H},
        device_scale_factor=1, executable_path=BROWSER,
        args=["--no-sandbox", "--password-store=basic"])
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url)
        width, ratio = page.evaluate("[innerWidth, devicePixelRatio]")
        page.mouse.click(W // 2, H // 2)
        # Where the click lands on the glass, in panel pixels.
        x, _ = page.evaluate("window.hit")
        return width, ratio, round(x * ratio)
    finally:
        context.close()


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("skipped: needs Playwright")
        return 0
    import ha_send

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    root = tempfile.mkdtemp()
    profile = os.path.join(root, "salon")
    prefs = os.path.join(profile, "Default", "Preferences")

    with sync_playwright() as pw:
        print("A profile with no zoom:")
        width, ratio, landed = look(pw, profile, url)
        check("laid out at the viewport's size", width == W, str(width))
        before = open(prefs, encoding="utf-8").read()
        ha_send.forget_zoom(profile)
        check("and forget_zoom leaves its Preferences untouched",
              open(prefs, encoding="utf-8").read() == before)

        # Chrome's own shape, captured from a real Chrome zoomed out three
        # steps with Ctrl+minus (-1.578 is 75%).
        data = json.load(open(prefs, encoding="utf-8"))
        data.setdefault("partition", {})["per_host_zoom_levels"] = {
            "x": {"127.0.0.1": {"last_modified": "13435627191612754",
                                "zoom_level": -1.5778829311823859}}}
        data["portall_marker"] = "kept"
        with open(prefs, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

        print("A site zoomed to 75% from the telephone (the report):")
        width, ratio, landed = look(pw, profile, url)
        check("the page is laid out wider than the panel", width > W,
              str(width))
        check("so a tap in the middle lands off the middle",
              abs(landed - W // 2) > 50, str(landed))

        print("After forget_zoom:")
        ha_send.forget_zoom(profile)
        data = json.load(open(prefs, encoding="utf-8"))
        check("the zoom is gone from Preferences",
              "per_host_zoom_levels" not in data.get("partition", {}))
        check("and the rest of the file is kept",
              data.get("portall_marker") == "kept")
        width, ratio, landed = look(pw, profile, url)
        check("the page is laid out at the panel's size",
              width == W and ratio == 1, f"{width} at {ratio}")
        check("and a tap lands where the finger is",
              abs(landed - W // 2) <= 1, str(landed))
    server.shutdown()
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
