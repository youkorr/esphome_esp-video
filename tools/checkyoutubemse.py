"""Exercise the optional YouTube TV MIME adapter in an offline Chromium fixture.

No YouTube response or video is fetched. A passing result verifies API
compatibility and scope, not availability of 1080p in a real YouTube session.
PORTALL_TEST_BROWSER selects Chrome; PORTALL_TEST_REPORT selects the JSON file.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if not __debug__:
    raise RuntimeError("Run this fixture without -O: its assertion checks must stay enabled")

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/portall/ha_send.py"
OUTPUT = (Path(os.environ["PORTALL_TEST_REPORT"])
          if os.environ.get("PORTALL_TEST_REPORT")
          else Path(tempfile.mkdtemp(prefix="portall-youtube-mse-")) / "report.json")
TV_URL = "https://www.youtube.com/tv#/watch?v=offline-mse-fixture"
HTML = """<!doctype html><title>Offline MIME fixture</title><script>
window.earlyProbe = MediaSource.isTypeSupported(
  'video/mp4; codecs="avc1.640028"; framerate=9999');
</script><body>Offline capability fixture</body>"""

sys.path.append(str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("portall_mse_fixture", SOURCE)
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)

report = {
    "offline_fixture_only": True,
    "youtube_playback_verified": False,
    "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    "checks": {},
}
checks = report["checks"]


def new_context(browser, script=None, notes=None):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    # Every page and frame is our local fixture, regardless of its test URL.
    context.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=HTML))
    if notes is not None:
        context.expose_function("__udispMediaError", lambda line: notes.append(line))
    if script is not None:
        context.add_init_script(script)
    return context


def query(page, mime):
    return page.evaluate("type => MediaSource.isTypeSupported(type)", mime)


try:
    if not hasattr(sender, "youtube_tv_media_init_js"):
        raise AssertionError("The sender has no youtube_tv_media_init_js helper")
    with sync_playwright() as p:
        executable = (os.environ.get("PORTALL_TEST_BROWSER")
                      or shutil.which("google-chrome-stable")
                      or shutil.which("google-chrome")
                      or shutil.which("chromium"))
        browser = p.chromium.launch(
            executable_path=executable, headless=True,
            args=sender.BROWSER_ARGS + ["--disk-cache-size=1048576",
                                        "--media-cache-size=1048576"])
        try:
            report["browser_version"] = browser.version
            notes = []
            context = new_context(browser, notes=notes)
            page = context.new_page()
            page.goto(TV_URL, wait_until="domcontentloaded")
            base = page.evaluate("""() => [
                'video/mp4; codecs="avc1.640028"',
                'video/webm; codecs="vp9"',
                'video/mp4; codecs="av01.0.08M.08"'
            ].find(type => MediaSource.isTypeSupported(type))""")
            assert base, "No ordinary video codec is supported by this browser"
            native = {key: query(page, base + extra) for key, extra in {
                "hd": "; width=1920; height=1080; framerate=60",
                "absurd_fps": "; framerate=9999",
                "absurd_width": "; width=99999",
                "negative_width": "; width=-1",
            }.items()}
            assert native["hd"] and native["absurd_fps"] and native["absurd_width"], native
            checks["native_chromium_ignores_tv_parameters"] = native

            page.evaluate("""() => {
                window.savedMSE = MediaSource.isTypeSupported;
                window.savedEME = navigator.requestMediaKeySystemAccess;
                window.savedMC = navigator.mediaCapabilities && navigator.mediaCapabilities.decodingInfo;
                window.mimeCalls = [];
                MediaSource.isTypeSupported = function(type) {
                    mimeCalls.push(Array.from(arguments));
                    return Reflect.apply(savedMSE, this, arguments);
                };
                try { savedMSE(); } catch (e) { window.nativeNoArgumentError = e.name; }
            }""")
            script = sender.youtube_tv_media_init_js(True)
            page.evaluate(script)
            page.evaluate("window.firstAdapter = MediaSource.isTypeSupported")
            page.evaluate(script)
            assert page.evaluate("firstAdapter === MediaSource.isTypeSupported")
            checks["idempotent_install"] = True
            cases = {
                "hd_60": ("; width=1920; height=1080; framerate=60", True),
                "quoted_fractional_fps": ('; WIDTH="1920"; HEIGHT="1080"; framerate="59.976"', True),
                "film_fps": ("; framerate=23.976", True),
                "unspecified_zero_values": ("; width=0; height=0; framerate=0; bitrate=0", True),
                "portrait_hd": ("; width=1080; height=1920", True),
                "too_many_pixels": ("; width=1920; height=1920", False),
                "oversized_width": ("; width=99999", False),
                "oversized_height": ("; height=99999", False),
                "negative_width": ("; width=-1", False),
                "fractional_width": ("; width=1920.5", False),
                "absurd_fps": ("; framerate=9999", False),
                "negative_fps": ("; framerate=-1", False),
                "nonfinite_fps": ("; framerate=Infinity", False),
                "nan_fps": ("; framerate=NaN", False),
                "playback_speed_sanity_bound": ("; framerate=120", True),
                "beyond_fps_sanity_bound": ("; framerate=120.5", False),
                "negative_bitrate": ("; bitrate=-1", False),
                "large_bitrate": ("; bitrate=200000001", False),
                "ordinary_bitrate": ("; bitrate=12000000", True),
                "sdr_transfer": ("; eotf=bt709", True),
                "unspecified_transfer": ('; eotf=""', True),
                "invalid_transfer": ("; eotf=unknown-transfer", False),
                "texture_true": ("; decode-to-texture=true", True),
                "texture_false": ("; decode-to-texture=false", True),
                "invalid_texture": ("; decode-to-texture=invalid", False),
            }
            results = {}
            for name, (extra, expected) in cases.items():
                got = query(page, base + extra)
                assert got is expected, (name, expected, got)
                results[name] = got
            checks["validated_video_extensions"] = results
            hfr_detector = {
                "native_result": native["hd"] and not native["absurd_fps"],
                "adapted_result": results["hd_60"] and not results["absurd_fps"],
            }
            assert hfr_detector == {"native_result": False, "adapted_result": True}
            checks["cobalt_style_hfr_query_pair"] = hfr_detector
            unknown_codec = 'video/mp4; codecs="nonexistent-codec"; width=1920; height=1080; framerate=60'
            assert query(page, unknown_codec) is False
            checks["unsupported_codec_stays_unsupported"] = True

            audio = 'audio/mp4; codecs="mp4a.40.2"'
            audio_supported = page.evaluate("type => savedMSE(type)", audio)
            for count in [1, 2, 8]:
                assert query(page, audio + "; channels=" + str(count)) is audio_supported
            for count in [-1, 0, 99]:
                assert query(page, audio + "; channels=" + str(count)) is False
            checks["audio_channel_bounds"] = True
            webm = 'video/webm; codecs="vp9"'
            webm_supported = page.evaluate("type => savedMSE(type)", webm)
            assert query(page, webm + "; cryptoblockformat=subsample") is webm_supported
            assert query(page, webm + '; cryptoblockformat=""') is webm_supported
            assert query(page, webm + "; cryptoblockformat=invalid") is False
            assert query(page, 'video/mp4; codecs="avc1.640028"; cryptoblockformat=subsample') is False
            assert query(page, base + '; cryptoblockformat=""') is True
            checks["crypto_hint_validation_without_eme_patch"] = True

            unknown = base + '; arbitrary="left;right"; width=1920; height=1080'
            assert query(page, unknown) == page.evaluate("type => savedMSE(type)", unknown)
            forwarded = page.evaluate("mimeCalls[mimeCalls.length - 1][0]")
            assert 'arbitrary="left;right"' in forwarded, forwarded
            assert "codecs=" in forwarded and "width=" not in forwarded, forwarded
            checks["quoted_unknown_parameter_preserved"] = forwarded
            identities = page.evaluate("""() => ({
                eme: savedEME === navigator.requestMediaKeySystemAccess,
                capabilities: savedMC === navigator.mediaCapabilities.decodingInfo,
                noArgument: (()=>{try{MediaSource.isTypeSupported();return null}catch(e){return e.name}})(),
                nativeNoArgument: nativeNoArgumentError
            })""")
            assert identities["eme"] and identities["capabilities"], identities
            assert identities["noArgument"] == identities["nativeNoArgument"], identities
            checks["native_api_identities_and_argument_error"] = identities
            converted = page.evaluate("""base => {
                let conversions=0;
                const result=MediaSource.isTypeSupported({toString(){conversions++;return base}});
                return {result,conversions};
            }""", base)
            assert converted == {"result": True, "conversions": 1}, converted
            checks["native_object_conversion_preserved"] = converted

            page.evaluate("history.pushState({}, '', '/watch?v=offline')")
            assert query(page, base + "; framerate=9999") is True
            page.evaluate("history.pushState({}, '', '/tv#/watch?v=offline')")
            assert query(page, base + "; framerate=9999") is False
            checks["same_document_leave_and_return"] = True
            page.wait_for_timeout(80)
            assert 0 < len(notes) <= 12, notes
            checks["diagnostic_note_budget"] = {"count": len(notes), "notes": notes}

            startup_notes = []
            startup = new_context(browser, script, startup_notes)
            early = startup.new_page()
            early.goto(TV_URL, wait_until="domcontentloaded")
            assert early.evaluate("earlyProbe") is False
            checks["installed_before_application_script"] = True
            for url in ["https://example.test/tv", "https://www.youtube.com/watch?v=offline",
                        "https://www.youtube.com/tvfake", "https://www.youtube.com.evil.test/tv"]:
                off = startup.new_page()
                off.goto(url, wait_until="domcontentloaded")
                assert query(off, base + "; framerate=9999") is True, url
                assert off.evaluate("MediaSource.isTypeSupported.toString().includes('[native code]')"), url
                off.close()
            checks["off_target_documents_unchanged"] = True
            early.evaluate("""url => { const f=document.createElement('iframe');f.src=url;document.body.appendChild(f); }""", TV_URL)
            early.wait_for_timeout(100)
            child = next(f for f in early.frames if f != early.main_frame and "/tv" in f.url)
            assert child.evaluate("type => MediaSource.isTypeSupported(type)", base + "; framerate=9999") is True
            checks["child_frame_unchanged"] = True

            quiet_notes = []
            quiet = new_context(browser, sender.youtube_tv_media_init_js(False), quiet_notes)
            quiet_page = quiet.new_page()
            quiet_page.goto(TV_URL, wait_until="domcontentloaded")
            for _ in range(20):
                assert query(quiet_page, base + "; framerate=9999") is False
            quiet_page.wait_for_timeout(80)
            assert not quiet_notes, quiet_notes
            checks["diagnostics_disabled_is_quiet"] = True
            report["status"] = "PASS"
        finally:
            browser.close()
except Exception as exc:
    report["status"] = "FAIL"
    report["error"] = type(exc).__name__ + ": " + str(exc)
    raise
finally:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
