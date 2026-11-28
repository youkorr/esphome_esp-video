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
        for url in ("launcher", "http://ha:8123/config", "http://ha:8123/"):
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

    def test_no_pin(self):
        kiosk = Kiosk("http://ha/lovelace")
        kiosk.gesture()
        self.assertTrue(kiosk.locked)
        with self.assertRaises(ValueError):
            Kiosk("http://ha/lovelace", "12")


if __name__ == "__main__":
    unittest.main()
