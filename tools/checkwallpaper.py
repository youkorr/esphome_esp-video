"""Exercise launcher wallpaper CSS in Chrome with local fulfilled fixtures.

No wallpaper service is contacted. This checks the URL the real renderer asks
Chrome to fetch, including query strings, a WebP source and style-tag payloads.
PORTALL_TEST_BROWSER and PORTALL_TEST_REPORT select the browser/report paths.
"""

import hashlib
import html
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

if not __debug__:
    raise SystemExit("Run without python -O: this fixture requires assertions")

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portall/launcher.py"
sys.path.insert(0, str(SOURCE.parent))
import launcher

OUTPUT = (Path(os.environ["PORTALL_TEST_REPORT"])
          if os.environ.get("PORTALL_TEST_REPORT")
          else Path(tempfile.mkdtemp(prefix="portall-wallpaper-")) / "report.json")
report = {"offline_browser_fixture_only": True,
          "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
          "checks": {}}


def fixture_image(kind):
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), (35, 122, 199)).save(stream, format=kind)
    return stream.getvalue()


cases = {
    "signed_query": "https://assets.example.test/wall.png?token=abc&width=1280",
    "literal_entity_text": "https://assets.example.test/wall.png?x=one&amp;y=two&size=800",
    "webp_plain_address": "http://media.example.test:8080/wallpaper.webp",
    "quote_and_backslash": 'https://assets.example.test/quoted"wall\\paper.png?x=one&y=two',
    "style_tag_payload": (
        "https://assets.example.test/wall.png?x=</style>"
        "<script>window.__wallpaperInjected=1</script><style>&size=1280"),
}

try:
    # The style element cannot interpret HTML entities, and '<' must never
    # occur literally in a quoted URL that it contains.
    escaped = launcher._css_url('https://assets.example.test/a"b\\c<d\n.png?a=1&b=2')
    assert "&b=2" in escaped and "&amp;" not in escaped, escaped
    assert "<" not in escaped and "\n" not in escaped, escaped
    assert "\\22 " in escaped and "\\5c " in escaped and "\\a " in escaped, escaped
    report["checks"]["css_string_boundaries"] = True

    movie = "https://assets.example.test/clip.mp4?token=abc&width=1280"
    movie_page = launcher.render([], background=movie, clock=False)
    assert f'<video src="{html.escape(movie, quote=True)}"' in movie_page
    report["checks"]["video_keeps_html_attribute_escaping"] = True

    pictures = {"PNG": fixture_image("PNG"), "WEBP": fixture_image("WEBP")}
    with sync_playwright() as playwright:
        executable = (os.environ.get("PORTALL_TEST_BROWSER")
                      or shutil.which("google-chrome-stable")
                      or shutil.which("google-chrome")
                      or shutil.which("chromium"))
        browser = playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=["--disable-gpu", "--disk-cache-size=1048576",
                  "--media-cache-size=1048576"])
        try:
            report["browser_version"] = browser.version
            for layout in ("classic", "dashboard"):
                for name, url in cases.items():
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    try:
                        expected = page.evaluate("""value => {
                            const url = new URL(value); url.hash = ''; return url.href;
                        }""", url)
                        requested = []

                        def route_request(route):
                            requested.append(route.request.url)
                            if route.request.url == expected:
                                webp = name == "webp_plain_address"
                                route.fulfill(status=200,
                                              content_type="image/webp" if webp else "image/png",
                                              body=pictures["WEBP" if webp else "PNG"])
                            else:
                                route.fulfill(status=404, body="unexpected fixture URL")

                        page.route("**/*", route_request)
                        page.set_content(launcher.render(
                            [{"name": "Fixture", "url": "https://example.test"}],
                            background=url, layout=layout, clock=False, dim=0, blur="off"),
                            wait_until="networkidle")
                        assert requested and all(item == expected for item in requested), requested
                        background = page.locator("#wa").evaluate(
                            "element => getComputedStyle(element).backgroundImage")
                        assert background != "none", background
                        assert page.evaluate("window.__wallpaperInjected === undefined")
                        report["checks"][layout + "/" + name] = {
                            "expected_url": expected, "requested_urls": requested,
                            "background_is_set": True, "injection_executed": False}
                    finally:
                        page.close()
            report["status"] = "PASS"
        finally:
            browser.close()
except Exception as error:
    report["status"] = "FAIL"
    report["error"] = type(error).__name__ + ": " + str(error)
    raise
finally:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
