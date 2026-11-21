"""Exercise the real launcher HTTP API and Supervisor option write-back."""
import argparse
import copy
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

if not __debug__:
    raise SystemExit('Run without -O: this fixture needs assertions')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portall'))
import appearance
import files
import run as runner


class Supervisor(http.server.BaseHTTPRequestHandler):
    options = {}
    failure = False
    writes = 0
    lock = threading.RLock()

    def log_message(self, *args):
        pass

    def do_GET(self):
        assert self.path == '/addons/self/info'
        assert self.headers.get('Authorization') == 'Bearer fixture-only'
        with self.lock:
            data = json.dumps({'result': 'ok', 'data': {'options': self.options}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        assert self.path == '/addons/self/options'
        assert self.headers.get('Authorization') == 'Bearer fixture-only'
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.failure:
            self.send_error(503)
            return
        with self.lock:
            type(self).options = copy.deepcopy(data['options'])
            type(self).writes += 1
        self.send_response(200)
        self.send_header('Content-Length', '15')
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')


def request(url, path='', payload=None, form=False, origin=True):
    headers = {}
    data = None
    if payload is not None:
        data = (urllib.parse.urlencode(payload).encode() if form
                else json.dumps(payload).encode())
        headers['Content-Type'] = ('application/x-www-form-urlencoded' if form
                                   else 'application/json')
        if origin:
            headers['Origin'] = url.rstrip('/')
    req = urllib.request.Request(url.rstrip('/') + '/' + path.lstrip('/'),
                                 data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as response:
        return response.code, response.read(), response.headers


def look(url):
    status, blob, _ = request(url, 'appearance.json')
    assert status == 200
    return json.loads(blob)


def image(path, color):
    Image.new('RGB', (48, 32), color).save(path, format='WEBP', lossless=True)
    return path.read_bytes()


def layout_check(page):
    """Import, Reset, Cancel and Apply remain reachable on smaller panels."""
    for width, height in ((320, 480), (360, 640), (480, 800),
                          (600, 1024), (800, 1280), (1280, 800)):
        page.set_viewport_size({'width': width, 'height': height})
        page.locator('#appearance-open').click()
        page.locator('#appearance-dialog[open]').wait_for()
        assert page.locator('#appearance-previous').is_visible()
        bounds = page.evaluate('''() => {
          const dialog = document.getElementById('appearance-dialog').getBoundingClientRect();
          return [...document.querySelectorAll('.appearance-footer button')]
            .filter(button => !button.hidden).map(button => {
              const box = button.getBoundingClientRect();
              return {id: button.id, visible: box.width > 0 && box.height > 0,
                within: box.left >= dialog.left && box.right <= dialog.right
                  && box.top >= dialog.top && box.bottom <= dialog.bottom};
            });
        }''')
        assert all(button['visible'] and button['within'] for button in bounds), (width, height, bounds)
        page.locator('#appearance-cancel').click()


def browser_check(url, new_background, saved_background, executable):
    """Exercise touch controls, real fetches and failure recovery in Chrome."""
    from playwright.sync_api import sync_playwright
    before = copy.deepcopy(Supervisor.options)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disk-cache-size=1048576'])
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 800},
                                          has_touch=True, locale='fr-FR')
            page = context.new_page()
            page.goto(url)
            def tap(selector):
                target = page.locator(selector)
                target.scroll_into_view_if_needed()
                box = target.bounding_box()
                assert box is not None
                page.touchscreen.tap(box['x'] + box['width'] / 2,
                                     box['y'] + box['height'] / 2)
            tap('#appearance-open')
            page.locator('#appearance-dialog[open]').wait_for()
            assert page.locator('#appearance-previous').is_visible()
            tap('#appearance-tab-background')
            page.locator('#appearance-background').fill(str(new_background))
            Supervisor.failure = True
            tap('#appearance-apply')
            page.locator('#appearance-error:not([hidden])').wait_for()
            assert page.locator('#appearance-dialog').evaluate('(d) => d.open')
            assert page.locator('#appearance-background').input_value() == str(new_background)
            assert Supervisor.options == before
            Supervisor.failure = False
            with page.expect_navigation():
                tap('#appearance-apply')
            assert Supervisor.options['launcher']['background']['source'] == str(new_background)
            assert look(url)['background'] == str(new_background)
            tap('#appearance-open')
            page.locator('#appearance-dialog[open]').wait_for()
            tap('#appearance-tab-background')
            assert page.locator('#appearance-background').input_value() == str(new_background)
            tap('#appearance-cancel')
            layout_check(page)
            context.close()
        finally:
            Supervisor.failure = False
            browser.close()
    assert request(url, 'appearance.json', {'background': str(saved_background)})[0] == 200


def main(browser=None):
    checks = []
    service = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Supervisor)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    old_env = os.environ.get('SUPERVISOR_TOKEN')
    os.environ['SUPERVISOR_TOKEN'] = 'fixture-only'
    try:
        with tempfile.TemporaryDirectory(prefix='portall-appearance-integration-') as folder:
            temp = Path(folder)
            runner.AVATAR_DIR = str(temp / 'avatar')
            runner.WALLPAPER_DIR = str(temp / 'wallpaper')
            runner.DOWNLOADS = str(temp / 'downloads')
            runner.launcher.PORT = 0  # Independent simulated restarts need independent listeners.
            runner.SUPERVISOR_API = f'http://127.0.0.1:{service.server_port}'
            runner.follow_voice = lambda *args, **kwargs: None
            class NoWeather:
                def __init__(self, *args, **kwargs):
                    pass
                def start(self):
                    return None
            runner.Weather = NoWeather
            blue = temp / 'configured.webp'
            red = temp / 'legacy.webp'
            green = temp / 'edited.webp'
            blue_bytes = image(blue, '#1234ee')
            red_bytes = image(red, '#dd1234')
            green_bytes = image(green, '#12cc34')
            download = Path(runner.DOWNLOADS) / 'salon' / 'download.webp'
            download.parent.mkdir(parents=True)
            download_bytes = image(download, '#123456')
            legacy_path = Path(runner.appearance_file(''))
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(json.dumps({'background': str(red), 'columns': 5}))
            original_legacy = legacy_path.read_bytes()
            choice = runner.avatar_file('').replace(runner.AVATAR_DIR, runner.WALLPAPER_DIR)
            files.write_choice(choice, 'salon/download.webp')
            links = [{'name': 'Example', 'url': 'https://example.test/',
                      'token': '!secret keep_me'}]
            base = {'links': links, 'launcher': {
                'layout': 'classic', 'theme': 'dark', 'columns': 2,
                'clock': {'show': True, 'size': 'medium'},
                'weather': {'show': True, 'entity': 'weather.fixture'},
                'background': {'source': str(blue), 'motion': True, 'blur': 'off', 'dim': 0},
                'avatar_voice': 'assist_satellite.fixture',
                'slideshow': {'enabled': False, 'urls': []}},
                'panels': [{'name': 'Salon', 'host': 'fixture', 'url': 'launcher'}],
                'launchers': [], 'defaults': {'locale': 'fr-FR'}}
            Supervisor.options = copy.deepcopy(base)
            def boot():
                config = runner.regroup(copy.deepcopy(Supervisor.options))
                return runner.start_launcher(config, port=0)
            url = boot()
            assert look(url)['background'] == str(blue)
            assert request(url, 'wallpaper')[1] == blue_bytes
            assert request(url, 'appearance.json')[2].get('X-Portall-Previous-Appearance') == 'available'
            assert legacy_path.read_bytes() == original_legacy
            checks.append('HA background wins over both legacy stores without deleting them')

            status, _, _ = request(url, 'appearance.json', {
                'layout': 'dashboard', 'columns': 4, 'weather_visible': False,
                'background': str(green), 'background_dim': 0})
            assert status == 200
            saved = Supervisor.options
            assert saved['launcher']['layout'] == 'dashboard'
            assert saved['launcher']['columns'] == 4
            assert saved['launcher']['weather']['show'] is False
            assert saved['launcher']['weather']['entity'] == 'weather.fixture'
            assert saved['launcher']['background']['source'] == str(green)
            assert saved['launcher']['background']['dim'] == 0
            assert saved['defaults'] == base['defaults']
            assert saved['links'] == base['links']
            assert request(url, 'wallpaper')[1] == green_bytes
            checks.append('Apply writes scoped visual values to Supervisor and publishes the new wallpaper')

            before = copy.deepcopy(Supervisor.options)
            Supervisor.failure = True
            assert request(url, 'appearance.json', {'background': str(red)})[0] == 500
            Supervisor.failure = False
            assert Supervisor.options == before
            assert look(url)['background'] == str(green)
            assert request(url, 'wallpaper')[1] == green_bytes
            checks.append('Supervisor failure leaves the active look and wallpaper unchanged')

            if browser:
                browser_check(url, red, green, browser)
                checks.append('Chrome touch editor writes HA options, reloads saved values and keeps the draft after an API failure')

            assert request(url, 'files/wallpaper', {'ref': 'salon/download.webp'}, form=True)[0] == 204
            assert Supervisor.options['launcher']['background']['source'] == str(download)
            assert request(url, 'wallpaper')[1] == download_bytes
            restarted = boot()
            assert look(restarted)['background'] == str(download)
            assert request(restarted, 'wallpaper')[1] == download_bytes
            checks.append('A downloaded wallpaper is saved in HA and survives restart')

            Supervisor.options['launcher']['background'].update(source=str(blue), clear=False)
            changed_in_ha = boot()
            assert look(changed_in_ha)['background'] == str(blue)
            assert request(changed_in_ha, 'wallpaper')[1] == blue_bytes
            checks.append('A later HA edit replaces the downloaded wallpaper after restart')

            assert request(changed_in_ha, 'appearance.json', {'reset': True})[0] == 200
            assert look(changed_in_ha) == appearance.DEFAULTS
            assert request(changed_in_ha, 'wallpaper')[0] == 404
            assert Supervisor.options['launcher']['weather']['entity'] == 'weather.fixture'
            assert Supervisor.options['launcher']['avatar_voice'] == 'assist_satellite.fixture'
            assert legacy_path.read_bytes() == original_legacy
            checks.append('Reset clears every active wallpaper while preserving services and legacy data')

            assert request(changed_in_ha, 'appearance.json', {'restore_previous': True})[0] == 200
            assert Supervisor.options['launcher']['columns'] == 5
            assert Supervisor.options['launcher']['background']['source'] == str(download)
            assert request(changed_in_ha, 'wallpaper')[1] == download_bytes
            checks.append('Explicit legacy import recovers previous choices through Supervisor')

            assert request(changed_in_ha, 'appearance.json', {'columns': 3}, origin=False)[0] == 403
            assert request(changed_in_ha, 'files/unwallpaper', {'ref': 'salon/download.webp'}, form=True, origin=False)[0] == 403
            checks.append('Cross-origin appearance and file mutations are rejected')

            Supervisor.options['panels'] = [
                {'name': name, 'host': 'fixture', 'url': 'launcher'}
                for name in ['Salon', 'Bureau', 'Entree']]
            Supervisor.options['launchers'] = [
                {'panel': 'Salon', 'links': links, 'background': '', 'background_clear': True},
                {'panel': 'Bureau', 'links': links, 'theme': 'light'}]
            untouched = copy.deepcopy(Supervisor.options['launchers'][1])
            def boot_all():
                config = runner.regroup(copy.deepcopy(Supervisor.options))
                return runner.start_launchers(config, Supervisor.options['panels'])
            _, own = boot_all()
            salon = own['salon'][0]
            assert look(salon)['background'] == ''
            assert request(salon, 'wallpaper')[0] == 404
            assert request(salon, 'appearance.json', {'clock': False, 'columns': 0, 'background': ''})[0] == 200
            assert Supervisor.options['launchers'][0]['clock'] is False
            assert Supervisor.options['launchers'][0]['columns'] == 0
            assert Supervisor.options['launchers'][0]['background_clear'] is True
            assert Supervisor.options['launchers'][1] == untouched
            _, restarted_own = boot_all()
            salon = restarted_own['salon'][0]
            assert look(salon)['background'] == ''
            assert look(salon)['clock'] is False
            assert request(salon, 'appearance.json', {'reset': True})[0] == 200
            assert Supervisor.options['launchers'][0] == {'panel': 'Salon', 'links': links}
            assert Supervisor.options['launchers'][1] == untouched
            assert look(salon)['background'] == str(download)
            checks.append('Per-screen clear, zero, false and reset inheritance survive restart without changing another screen')

            standalone_pref = temp / 'standalone.json'
            standalone_pref.write_text(json.dumps({'background': str(red)}))
            standalone_choice = str(temp / 'standalone-choice.json')
            files.write_choice(standalone_choice, 'salon/download.webp')
            standalone = runner.launcher.start(
                links, background=str(blue), appearance_file=standalone_pref,
                choice_file=standalone_choice, files_root=runner.DOWNLOADS, port=0)
            assert request(standalone, 'wallpaper')[1] == download_bytes
            assert request(standalone, 'appearance.json', {'reset': True})[0] == 200
            assert request(standalone, 'wallpaper')[1] == blue_bytes
            assert request(standalone, 'appearance.json')[2].get('X-Portall-Previous-Appearance') is None
            checks.append('Standalone JSON preferences remain supported and Reset removes the downloaded override')

            slide = 'https://assets.example.test/slideshow-picture.webp'
            for clear, expected_slide in [('absent', True), (False, False), (True, False)]:
                Supervisor.options = copy.deepcopy(base)
                Supervisor.options['launcher']['slideshow'].update(
                    enabled=True, urls=[slide], after=0)
                if clear != 'absent':
                    Supervisor.options['launcher']['background']['clear'] = clear
                slideshow_url = boot()
                assert (slide.encode() in request(slideshow_url)[1]) is expected_slide
                assert request(slideshow_url, 'appearance.json', {'columns': 4})[0] == 200
                assert (slide.encode() in request(slideshow_url)[1]) is expected_slide
                restarted_slideshow = boot()
                assert (slide.encode() in request(restarted_slideshow)[1]) is expected_slide
                if clear is True:
                    assert look(restarted_slideshow)['background'] == ''
                    assert request(restarted_slideshow, 'wallpaper')[0] == 404
                elif clear is False:
                    assert request(restarted_slideshow, 'wallpaper')[1] == blue_bytes
            checks.append('Existing behind-links slideshows retain priority until an explicit wallpaper choice, including after Apply and restart')

            Supervisor.options['launcher']['background']['clear'] = False
            Supervisor.options['launcher']['slideshow']['after'] = 2
            saver_url = boot()
            assert slide.encode() not in request(saver_url)[1]
            assert slide.encode() in request(saver_url, 'saver')[1]
            checks.append('An explicit home wallpaper keeps separately configured screen-saver pictures')
            print(json.dumps({'status': 'PASS', 'checks': checks, 'supervisor_writes': Supervisor.writes}))
    finally:
        if old_env is None:
            os.environ.pop('SUPERVISOR_TOKEN', None)
        else:
            os.environ['SUPERVISOR_TOKEN'] = old_env
        service.shutdown()
        service.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', help='Chrome/Chromium executable for the touch UI regression')
    main(parser.parse_args().browser)
