"""Check the Files UI reports failed saves and only navigates after success.

Offline browser fixture; PORTALL_TEST_BROWSER can select an installed Chrome.
"""
import json
import os
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright

if not __debug__:
    raise SystemExit("Run without -O: this fixture needs assertions")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import files


def main():
    checks = []
    entry = {"ref": "screen/example.webp", "name": "example.webp",
             "kind": "image", "path": "/unused/example.webp",
             "screen": "screen", "size": 1, "when": 0}
    with sync_playwright() as playwright:
        executable = (os.environ.get("PORTALL_TEST_BROWSER")
                      or shutil.which("google-chrome-stable")
                      or shutil.which("google-chrome")
                      or shutil.which("chromium"))
        browser = playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disk-cache-size=1048576", "--media-cache-size=1048576"])
        try:
            for locale in ("fr-FR", "en-US"):
                for action in ("wallpaper", "unwallpaper", "delete"):
                    context = browser.new_context(locale=locale,
                                                  viewport={'width': 360, 'height': 640})
                    state = {"mode": "http-error", "loads": 0, "posts": 0}
                    html = files.view_page(entry, entry["ref"] if action == "unwallpaper" else None)
                    # A long Files page puts its shared error message below
                    # the action. The failure must bring that message on screen.
                    marker = b'<p id="files-error"'
                    assert marker in html, 'Files error markup changed; update the long-page fixture'
                    html = html.replace(marker,
                                        b'<div style="height:1600px" aria-hidden="true"></div>' + marker)

                    def serve(route):
                        request = route.request
                        if request.method == "POST":
                            state["posts"] += 1
                            assert request.url.endswith("/files/" + action)
                            assert request.headers.get("origin") == "http://portall.test"
                            assert request.post_data == "ref=screen%2Fexample.webp"
                            if state["mode"] == "network-error":
                                route.abort()
                            else:
                                route.fulfill(status=500 if state["mode"] == "http-error" else 204)
                        elif request.url.endswith("/files/raw?f=screen%2Fexample.webp"):
                            route.fulfill(status=404)
                        else:
                            state["loads"] += 1
                            route.fulfill(status=200, content_type="text/html", body=html)

                    context.route("**/*", serve)
                    page = context.new_page()
                    page.goto("http://portall.test/files/view?f=screen%2Fexample.webp")
                    button = page.locator('button[data-do="' + action + '"]')
                    for mode in ("http-error", "network-error"):
                        state["mode"] = mode
                        before = state["loads"]
                        button.click()
                        if action == "delete" and state["posts"] == 0:
                            button.click()
                        page.locator("#files-error").wait_for(state="visible")
                        assert button.is_enabled()
                        assert state["loads"] == before, "Failed save must not reload the page"
                        text = page.locator("#files-error").inner_text()
                        assert ("Impossible" if locale == "fr-FR" else "Could not") in text
                        box = page.locator('#files-error').bounding_box()
                        assert box and -1 <= box['y'] < 640 and box['y'] + box['height'] <= 641, (locale, action, mode, box)
                    state["mode"] = "ok"
                    before = state["loads"]
                    with page.expect_navigation(wait_until="domcontentloaded"):
                        button.click()
                    assert state["loads"] > before
                    assert state["posts"] == 3
                    assert page.locator("#files-error").is_hidden()
                    if action == "delete":
                        assert page.url == "http://portall.test/files"
                    checks.append(locale + ": " + action + " handles HTTP/network failure and retry")
                    context.close()
            print(json.dumps({"status": "PASS", "browser": browser.version, "checks": checks}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
