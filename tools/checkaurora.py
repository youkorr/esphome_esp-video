#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Regression checks for the opt-in Aurora layout, with the real launcher."""
import os
import pathlib
import sys
import tempfile
import urllib.parse

from checkdashboard import LINKS, WEATHER, request, load, GEOMETRY
import appearance
import launcher
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as temp:
        address = launcher.start(
            LINKS, port=0, layout="classic", columns=3, avatar=True,
            clock=True, weather=lambda: dict(WEATHER),
            appearance_file=str(pathlib.Path(temp) / "appearance.json"))
        origin = address.rstrip("/")
        assert address
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("CHROMIUM") or None)
            for width, height in ((1280, 720), (1280, 800), (1024, 600), (800, 1280)):
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                assert request(address, {"layout": "aurora", "background_motion": False},
                               origin=origin)[0] == 200
                load(page, address)
                assert page.locator("body.aurora").count() == 1
                assert page.locator("a.tile").count() == len(LINKS)
                assert page.locator("#av").count() == 1
                assert page.locator("#appearance-open").is_visible()
                assert page.locator("#t").is_visible()
                assert page.locator(".wx").is_visible()
                geometry = page.evaluate(GEOMETRY)
                assert geometry["scrollWidth"] <= width + 1, geometry
                assert page.locator("#wa").evaluate("(e) => getComputedStyle(e).animationName") == "none"
                assert request(address, {"background_motion": True}, origin=origin)[0] == 200
                load(page, address)
                assert page.locator("#wa").evaluate("(e) => getComputedStyle(e).animationName") == "portall-aurora-drift"
                page.emulate_media(reduced_motion="reduce")
                assert page.locator("#wa").evaluate("(e) => getComputedStyle(e).animationName") == "none"
                page.goto(urllib.parse.urljoin(address, launcher.SAVER_PATH))
                assert page.locator("body.aurora").count() == 0
                for old in ("classic", "dashboard"):
                    assert request(address, {"layout": old}, origin=origin)[0] == 200
                    load(page, address)
                    assert page.locator("body.aurora").count() == 0
                    assert page.locator("a.tile").count() == len(LINKS)
                    assert page.locator("body.dashboard").count() == int(old == "dashboard")
                assert not errors, errors
                print(f"OK Aurora {width}x{height}: motion, reduced motion, rollback, links")
                page.close()
            browser.close()


if __name__ == "__main__":
    main()
