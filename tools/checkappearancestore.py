"""Exercise appearance commits against a real HTTP Supervisor stand-in."""

import concurrent.futures
import copy
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

if not __debug__:
    raise SystemExit("Run without -O: this fixture requires assertions")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import appearance
import appearance_store
import run

REPORT = {"status": "RUNNING", "supervisor_http_fixture": True, "checks": []}


def checked(name):
    REPORT["checks"].append(name)


state = {
    "options": {
        "defaults": {"quality": 80},
        "links": [{"name": "Home", "url": "https://ha.test", "token": "!secret home_token"}],
        "launcher": {"theme": "dark", "columns": 3, "avatar": True,
                     "clock": {"show": True, "size": "large"},
                     "weather": {"entity": "!secret weather", "show": True},
                     "background": {"source": "http://assets.test:8080/images/ship.webp",
                                    "motion": True, "blur": "off", "dim": 0}},
        "panels": [{"name": "salon", "advanced": {"youtube_tv_hd": True}}],
        "launchers": [
            {"panel": "Salon", "links": [{"name": "Own", "url": "https://own.test"}],
             "theme": "light", "clock": False, "background": "!secret salon_wall",
             "weather_visible": False, "avatar_voice": "assist_satellite.salon"},
            {"panel": "cuisine", "links": [{"name": "Other", "url": "https://other.test"}]}],
        "unrelated_future_option": {"keep": [0, False, "!secret future"]},
    },
    "fail_get": False, "fail_post": False, "posts": 0,
}
state_lock = threading.Lock()


class Supervisor(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, value, code=200):
        payload = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        assert self.headers.get("Authorization") == "Bearer fixture-token"

    def do_GET(self):
        self.authorized()
        assert self.path == "/addons/self/info"
        with state_lock:
            failed, options = state["fail_get"], copy.deepcopy(state["options"])
        self.reply({"result": "error"} if failed else
                   {"result": "ok", "data": {"options": options}}, 503 if failed else 200)

    def do_POST(self):
        self.authorized()
        assert self.path == "/addons/self/options"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert set(body) == {"options"}
        time.sleep(0.01)  # Expose stale read/replace races between two writers.
        with state_lock:
            failed = state["fail_post"]
            if not failed:
                state["options"] = body["options"]
                state["posts"] += 1
        self.reply({"result": "error"} if failed else {"result": "ok", "data": {}},
                   500 if failed else 200)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Supervisor)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
API = f"http://127.0.0.1:{server.server_port}"
shared_fallback = {**appearance.DEFAULTS, "background": "https://resolved.test/shared.webp"}
own_fallback = {**appearance.DEFAULTS, "background": "https://resolved.test/salon.webp"}
store = appearance_store.AppearanceStore(API, "fixture-token", shared_fallback=shared_fallback)
own = appearance_store.AppearanceStore(
    API, "fixture-token", panel_name="salon", fallback=own_fallback,
    shared_fallback=shared_fallback)

try:
    with tempfile.TemporaryDirectory(prefix="portall-appearance-store-") as directory:
        legacy = Path(directory) / "appearance.json"
        previous = {"theme": "light", "background": "https://old.test/previous.webp"}
        legacy.write_text(json.dumps(previous))
        before_file = legacy.read_bytes()
        prefs = appearance.Preferences(
            {"theme": "dark", "background": "http://assets.test:8080/images/ship.webp",
             "background_dim": 0, "background_motion": True}, legacy, commit=store.commit)
        assert prefs.managed and prefs.snapshot()["theme"] == "dark"
        assert prefs.snapshot()["background"].endswith("ship.webp")
        assert prefs.snapshot()["background_dim"] == 0 and prefs.snapshot()["background_motion"]
        assert prefs.previous() == previous and legacy.read_bytes() == before_file
        assert not prefs.has_override("background")
        checked("Supervisor defaults win over recoverable legacy overrides")

        untouched = copy.deepcopy(state["options"])
        result = prefs.update({"theme": "light", "columns": 0, "clock": False})
        assert result["theme"] == "light" and result["columns"] == 0 and result["clock"] is False
        expected = copy.deepcopy(untouched)
        expected["launcher"]["theme"] = "light"
        expected["launcher"]["columns"] = 0
        expected["launcher"]["clock"]["show"] = False
        assert state["options"] == expected and legacy.read_bytes() == before_file
        checked("changed fields only; zero, false, secrets and unrelated options preserved")

        for key in ("fail_get", "fail_post"):
            snapshot, stored = prefs.snapshot(), copy.deepcopy(state["options"])
            state[key] = True
            try:
                prefs.update({"theme": "dark"})
                raise AssertionError("Failed Supervisor commit was reported successful")
            except OSError:
                pass
            finally:
                state[key] = False
            assert prefs.snapshot() == snapshot and state["options"] == stored
            assert legacy.read_bytes() == before_file
        checked("GET/POST failures do not publish or persist local success")

        state["options"]["external_change"] = {"preserve": "!secret another"}
        prefs.update({"background_dim": 0, "background_motion": True})
        assert state["options"]["external_change"] == {"preserve": "!secret another"}
        checked("latest Supervisor options are merged at save time")

        own_values = own.commit({"columns": 0})
        assert own_values["values"]["background"] == own_fallback["background"]
        assert state["options"]["launchers"][0]["background"] == "!secret salon_wall"
        assert own_values["values"]["weather_visible"] is False
        assert own_values["values"]["clock"] is False
        assert not own_values["background_override"]
        checked("panel secret values use resolved panel fallback without replacing references")

        selected = store.commit({"background": "https://selected.test/background.webp"})
        assert selected["background_override"]
        assert state["options"]["launcher"]["background"]["clear"] is False
        checked("absent clear keeps legacy priority; explicit false selects the source")

        state["options"]["launcher"]["background"]["source"] = "!secret shared_wall"
        own_reset = own.commit({}, reset=True)
        assert own_reset["values"]["background"] == shared_fallback["background"]
        assert own_reset["values"]["weather_visible"] is True
        assert state["options"]["launchers"][0] == {
            "panel": "Salon", "links": untouched["launchers"][0]["links"],
            "avatar_voice": "assist_satellite.salon"}
        checked("panel reset inherits shared secret fallback and keeps links/service fields")

        own_clear = own.commit({"background": "", "weather_visible": False})
        assert own_clear["values"]["background"] == "" and own_clear["background_override"]
        assert state["options"]["launchers"][0]["background_clear"] is True
        reload_options = copy.deepcopy(state["options"])
        flat = run.regroup(copy.deepcopy(reload_options))
        merged = run.launcher_config(flat, reload_options["launchers"][0])
        restarted = appearance.Preferences(
            appearance_store.values_from_flat(merged), legacy, commit=own.commit,
            background_override=merged.get("launcher_background_clear") is not None)
        assert restarted.snapshot()["background"] == "" and restarted.has_override("background")
        checked("explicit empty panel background survives restart")

        state["options"]["launcher"]["background"] = {
            "source": "https://example.test/shared.webp", "clear": True}
        state["options"]["launchers"][0]["background_clear"] = False
        result = own.commit({"columns": 2})
        flat = run.regroup(copy.deepcopy(state["options"]))
        merged = run.launcher_config(flat, state["options"]["launchers"][0])
        restarted = appearance.Preferences(
            appearance_store.values_from_flat(merged), commit=own.commit,
            background_override=merged.get("launcher_background_clear") is not None)
        assert result["values"]["background"] == "https://example.test/shared.webp"
        assert restarted.snapshot() == result["values"]
        checked("explicit panel clear=false agrees before and after restart")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(store.commit, {"theme": "dark"}),
                       pool.submit(own.commit, {"clock": False})]
            for future in futures:
                future.result()
        assert state["options"]["launcher"]["theme"] == "dark"
        assert state["options"]["launchers"][0]["clock"] is False
        checked("concurrent shared and panel read/replace writes are serialized")

        old_token, old_api = os.environ.get("SUPERVISOR_TOKEN"), run.SUPERVISOR_API
        try:
            os.environ["SUPERVISOR_TOKEN"] = "fixture-token"
            run.SUPERVISOR_API = API
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(store.commit, {"clock": True}),
                           pool.submit(run.show_enabled_switch)]
                for future in futures:
                    future.result()
            assert state["options"]["launcher"]["clock"]["show"] is True
            assert state["options"]["panels"][0]["enabled"] is True
        finally:
            run.SUPERVISOR_API = old_api
            if old_token is None:
                os.environ.pop("SUPERVISOR_TOKEN", None)
            else:
                os.environ["SUPERVISOR_TOKEN"] = old_token
        checked("startup enabled-switch migration shares the appearance write lock")

        reset = store.commit({}, reset=True)
        assert reset["values"] == appearance.DEFAULTS
        assert state["options"]["launcher"]["weather"]["entity"] == "!secret weather"
        assert state["options"]["links"] == untouched["links"]
        assert state["options"]["launcher"]["background"]["clear"] is True
        checked("shared factory reset changes visual fields only")

        before = copy.deepcopy(state["options"])
        try:
            prefs.update({"background": "javascript:alert(1)"})
            raise AssertionError("Unsafe background accepted")
        except ValueError:
            pass
        assert before == state["options"]
        checked("invalid appearance rejected before Supervisor write")

        local_path = Path(directory) / "standalone.json"
        local = appearance.Preferences({"columns": 3}, local_path)
        assert not local.managed and local.previous() == {}
        local.update({"columns": 0, "clock": False, "background": ""})
        loaded = appearance.Preferences({"columns": 3}, local_path)
        assert loaded.snapshot()["columns"] == 0 and loaded.snapshot()["clock"] is False
        assert loaded.has_override("background")
        loaded.reset()
        assert loaded.snapshot()["columns"] == 3
        assert json.loads(local_path.read_text()) == {}
        checked("standalone JSON persistence and reset retain their prior behavior")

    assert set(appearance_store.SHARED_PATHS) == set(appearance.DEFAULTS)
    REPORT["status"] = "PASS"
except Exception as error:
    REPORT["status"] = "FAIL"
    REPORT["error"] = type(error).__name__ + ": " + str(error)
    raise
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    if os.environ.get("PORTALL_TEST_REPORT"):
        Path(os.environ["PORTALL_TEST_REPORT"]).write_text(json.dumps(REPORT, indent=2) + "\n")
    print(json.dumps(REPORT))
