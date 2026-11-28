import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portall"))
from kiosk import Kiosk, dashboard_scope, permitted


class KioskTest(unittest.TestCase):
    def test_scope(self):
        scope = dashboard_scope("http://ha:8123/dashboard-wall/0")
        for url in ("http://ha:8123/dashboard-wall", "http://ha:8123/dashboard-wall/sub?x=1"):
            self.assertTrue(permitted(url, scope), url)
        for url in ("http://ha:8123/config", "http://ha:8123/dashboard-wall-other",
                    "https://ha:8123/dashboard-wall", "http://other/dashboard-wall",
                    "http://ha:8123/dashboard-wall/%2e%2e/config",
                    "http://ha:8123/dashboard-wall/../config",
                    "http://ha:8123@evil/dashboard-wall"):
            self.assertFalse(permitted(url, scope), url)
        for url in ("launcher", "http://ha:8123/config", "http://ha:8123/",
                    "http://ha:8123/developer-tools/state", "http://ha:8123/media-browser"):
            with self.assertRaises(ValueError):
                dashboard_scope(url)

    def test_pin(self):
        now = [100.]
        kiosk = Kiosk("http://ha/lovelace/0", "123456", clock=lambda: now[0])
        page = SimpleNamespace(main_frame=object(), url=kiosk.url, evaluate=lambda script: None)
        kiosk.page = page
        source = {"page": page, "frame": page.main_frame}
        self.assertFalse(kiosk._unlock(source, "123456"))
        kiosk.gesture()
        self.assertFalse(kiosk._unlock({"page": page, "frame": object()}, "123456"))
        for _ in range(5):
            self.assertFalse(kiosk._unlock(source, "111111"))
        self.assertFalse(kiosk._unlock(source, "123456"))
        now[0] += 61
        kiosk.gesture()
        self.assertTrue(kiosk._unlock(source, "123456"))
        self.assertTrue(kiosk.allowed("http://ha/config"))
        now[0] += 301
        kiosk.tick(True)
        self.assertTrue(kiosk.locked)
        self.assertFalse(kiosk.allowed("http://ha/config"))
        kiosk.gesture()
        self.assertTrue(kiosk._unlock(source, "123456"))
        kiosk.tick(False)
        self.assertTrue(kiosk.locked)

    def test_sender_configuration(self):
        import os
        import run
        from unittest.mock import patch
        panel = {"name": "wall", "host": "panel", "url": "http://ha/lovelace/0",
                 "kiosk_mode": True, "kiosk_pin": "123456",
                 "token": "dedicated", "page_token": ["admin"], "keep_profile": True}
        argv = run.command_for(panel)
        self.assertIn("--kiosk-mode", argv)
        self.assertNotIn("--profile", argv)
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--page-token", argv)
        self.assertNotIn("123456", argv)
        with patch.dict(os.environ, {"HA_TOKEN": "shared"}):
            env = run.sender_env(panel)
            self.assertNotIn("HA_TOKEN", env)
            self.assertEqual(env["PORTALL_KIOSK_PIN"], "123456")
        ordinary = run.command_for({"name": "other", "host": "panel",
                                    "url": "http://ha/config", "keep_profile": True})
        self.assertNotIn("--kiosk-mode", ordinary)
        self.assertIn("--profile", ordinary)
        self.assertIn("--resume", ordinary)

    def test_no_pin(self):
        kiosk = Kiosk("http://ha/lovelace")
        kiosk.gesture()
        self.assertTrue(kiosk.locked)
        with self.assertRaises(ValueError):
            Kiosk("http://ha/lovelace", "12")


if __name__ == "__main__":
    unittest.main()
