"""Keep launchers available when optional appearance configuration is invalid.

Use real launcher HTTP servers and a local Supervisor fixture. No external
network or panel is needed. Configured URLs and paths may be longer or contain
spaces even though newly entered appearance API values remain restricted.
"""
import contextlib
import copy
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from PIL import Image

if not __debug__:
    raise SystemExit("Run without -O: this fixture needs assertions")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import appearance
import run as runner


class Supervisor(http.server.BaseHTTPRequestHandler):
    options = {}

    def log_message(self, *args):
        pass

    def reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.path == "/addons/self/info"
        self.reply({"result": "ok", "data": {"options": self.options}})

    def do_POST(self):
        assert self.path == "/addons/self/options"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).options = copy.deepcopy(payload["options"])
        self.reply({"result": "ok"})


def request(url, path="appearance.json", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/" + path, data=data,
        headers={"Content-Type": "application/json", "Origin": url.rstrip("/")})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as response:
        return response.code, response.read()


def look(url):
    status, body = request(url)
    assert status == 200
    return json.loads(body)


def main():
    checks = []
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Supervisor)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    old_token = os.environ.get("SUPERVISOR_TOKEN")
    original_factory = runner.appearance_store.make_commit
    try:
        os.environ["SUPERVISOR_TOKEN"] = "fixture-only"
        with tempfile.TemporaryDirectory(prefix="portall-appearance-startup-") as folder:
            root = Path(folder)
            runner.AVATAR_DIR = str(root / "avatar")
            runner.WALLPAPER_DIR = str(root / "wallpaper")
            runner.DOWNLOADS = str(root / "downloads")
            runner.SUPERVISOR_API = f"http://127.0.0.1:{service.server_port}"
            runner.launcher.PORT = 0
            runner.follow_voice = lambda *args, **kwargs: None
            class NoWeather:
                def __init__(self, *args, **kwargs):
                    pass
                def start(self):
                    return None
            runner.Weather = NoWeather
            local = root / "picture with spaces.webp"
            Image.new("RGB", (16, 16), "blue").save(local)
            spaced_url = "https://fixture.test/picture with spaces.webp"
            long_url = "https://fixture.test/image.webp?signature=" + "x" * 2500
            links = [{"name": "Fixture links remain available", "url": "https://example.test/"}]
            panels = [{"name": name, "host": "fixture", "url": "launcher"}
                      for name in ("Common", "Local", "Long", "Invalid")]
            Supervisor.options = {
                "links": links, "panels": panels,
                "launcher": {"background": {"source": spaced_url}},
                "launchers": [
                    {"panel": "Local", "links": links, "background": str(local)},
                    {"panel": "Long", "links": links, "background": long_url},
                    {"panel": "Invalid", "links": links,
                     "background": "javascript:invalid-startup-fixture"}]}
            original_options = copy.deepcopy(Supervisor.options)
            config = runner.regroup(copy.deepcopy(Supervisor.options))
            diagnostics = io.StringIO()
            with contextlib.redirect_stdout(diagnostics):
                common, own = runner.start_launchers(config, config["panels"])
            assert 'appearance values for background' in diagnostics.getvalue()
            assert 'invalid-startup-fixture' not in diagnostics.getvalue()
            assert common is not None and len(own) == 3
            for url in [common, *(value[0] for value in own.values())]:
                status, html = request(url, "")
                assert status == 200 and b"Fixture links remain available" in html
            assert look(common)["background"] == spaced_url
            assert look(own["local"][0])["background"] == str(local)
            assert request(own["local"][0], "wallpaper")[1] == local.read_bytes()
            assert look(own["long"][0])["background"] == long_url
            assert look(own["invalid"][0])["background"] == ""
            assert Supervisor.options == original_options
            checks.append("Spaced URL/local path and long URL start; invalid appearance keeps every launcher's links available")

            for url, expected in ((common, spaced_url), (own["long"][0], long_url)):
                assert request(url, payload={"columns": 3})[0] == 200
                assert look(url)["background"] == expected
            assert Supervisor.options["launcher"]["background"]["source"] == spaced_url
            assert Supervisor.options["launchers"][1]["background"] == long_url
            checks.append("Unrelated managed saves preserve schema-valid spaced and long configured backgrounds")

            invalid_url = own["invalid"][0]
            assert request(invalid_url, payload={"background": "https://fixture.test/fixed.webp"})[0] == 200
            assert look(invalid_url)["background"] == "https://fixture.test/fixed.webp"
            assert request(invalid_url, payload={"background": "javascript:still-invalid"})[0] == 400
            assert request(invalid_url, payload={"background": long_url})[0] == 400
            checks.append("The editor can repair invalid configuration while new API input stays validated")

            blocked_file = Path(runner.appearance_file(" [Blocked]"))
            blocked_file.parent.mkdir(parents=True, exist_ok=True)
            blocked_file.write_text('{"columns":5}')
            old_bytes = blocked_file.read_bytes()
            def broken_factory(*args, **kwargs):
                raise ValueError("Deliberate fixture initialization failure")
            runner.appearance_store.make_commit = broken_factory
            blocked = runner.start_launcher(
                runner.regroup(copy.deepcopy(Supervisor.options)), port=0, label=" [Blocked]")
            assert blocked is not None and look(blocked)["columns"] == 3
            before = look(blocked)
            assert request(blocked, payload={"columns": 4})[0] == 500
            assert look(blocked) == before and blocked_file.read_bytes() == old_bytes
            checks.append("Unexpected store initialization failure keeps links and rejects managed saves without local fallback")

            standalone = runner.launcher.start(
                links, port=0, background="javascript:invalid-fixture", dim="not-a-number",
                columns=float("inf"), layout="not-a-layout", align="not-an-alignment")
            values = look(standalone)
            for field in ("background", "background_dim", "columns", "layout", "align"):
                assert values[field] == appearance.DEFAULTS[field]
            assert request(standalone, "")[0] == 200
            checks.append("Standalone invalid optional appearance fields degrade individually to safe defaults")
            print(json.dumps({"status": "PASS", "checks": checks}))
    finally:
        runner.appearance_store.make_commit = original_factory
        if old_token is None:
            os.environ.pop("SUPERVISOR_TOKEN", None)
        else:
            os.environ["SUPERVISOR_TOKEN"] = old_token
        service.shutdown()
        service.server_close()


if __name__ == "__main__":
    main()
