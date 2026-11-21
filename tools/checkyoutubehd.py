import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

if not __debug__:
    raise SystemExit('Run without python -O: this fixture requires assertions')

NATIVE_VIEW = os.environ.get('PORTALL_TEST_NATIVE_VIEW') == '1'
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'components/portall/ha_send.py'
OUTPUT = (Path(os.environ['PORTALL_TEST_REPORT'])
          if os.environ.get('PORTALL_TEST_REPORT')
          else Path(tempfile.mkdtemp(prefix='portall-youtube-hd-'))/'report.json')
sys.path.append(str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('portall_hd_fixture', SOURCE)
SOURCE_SHA256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)

class IdentityMap:
    def to_page(self, x, y):
        return x, y

HTML = '''<!doctype html><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#004000}#full{position:fixed;right:4vw;bottom:4vh;width:8vw;height:8vh}input{position:fixed;left:10vw;top:15vh;width:30vw;height:6vh}</style><div id="player" style="position:fixed;inset:0;background:#004000"><input id="search"><button id="full">Fullscreen</button><video id="video" muted style="position:fixed;width:20vw;height:15vh;left:0;bottom:0"></video></div><script>window.result={clicks:0,events:[]};document.getElementById('full').addEventListener('click',async e=>{result.clicks++;result.trusted=e.isTrusted;result.active=navigator.userActivation.isActive;try{await document.getElementById('player').requestFullscreen();result.success=true}catch(e){result.error=e.name+': '+e.message}});document.addEventListener('fullscreenchange',()=>result.events.push(!!document.fullscreenElement));window.startVideo=async()=>{const c=document.createElement('canvas');c.width=1920;c.height=1080;const g=c.getContext('2d');g.fillStyle='#408000';g.fillRect(0,0,c.width,c.height);const v=document.getElementById('video');v.srcObject=c.captureStream(10);window.videoCanvas=c;window.videoTimer=setInterval(()=>{g.fillRect(0,0,c.width,c.height)},100);await v.play()};</script>'''

report = {'offline_fixture_only': True, 'youtube_playback_verified': False,
          'native_view': NATIVE_VIEW,
          'source_sha256': SOURCE_SHA256, 'checks': {}}
checks = report['checks']
media_lines = []

def metrics(page):
    return page.evaluate('({width:innerWidth,height:innerHeight,screenWidth:screen.width,screenHeight:screen.height,dpr:devicePixelRatio})')

def image_size(blob):
    return list(Image.open(io.BytesIO(blob)).size)

def tap(page, injector, x, y):
    injector.handle([[(0, x, y)]])
    clicked = injector.handle([[]])
    page.wait_for_timeout(90)
    injector.tick(time.monotonic())
    page.wait_for_timeout(100)
    return clicked

try:
    with sync_playwright() as p:
        executable = (os.environ.get('PORTALL_TEST_BROWSER')
                      or shutil.which('google-chrome-stable')
                      or shutil.which('google-chrome')
                      or shutil.which('chromium'))
        browser = p.chromium.launch(executable_path=executable, headless=True, args=sender.BROWSER_ARGS+['--disk-cache-size=1048576','--media-cache-size=1048576'])
        try:
            report['browser_version'] = browser.version
            context = browser.new_context(viewport={'width':1280,'height':800}, device_scale_factor=1, has_touch=True)
            # Every URL is fulfilled with our local fixture. No YouTube bytes are fetched.
            context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=HTML))
            context.expose_function('__udispMediaError', lambda line: media_lines.append(line))
            context.add_init_script(sender.KEYBOARD_INIT_JS)
            context.add_init_script(sender.HOME_HINT_JS)
            context.add_init_script(sender.media_init_js(100))
            page = context.new_page()
            view = sender.YouTubeTVView(page,1280,800,native_view=NATIVE_VIEW)
            url = 'https://www.youtube.com/tv#/watch?v=offline-fixture'
            view.reconcile(url)
            page.goto(url,wait_until='domcontentloaded')
            got = metrics(page)
            expected = ((1280,800,1920,1080) if NATIVE_VIEW
                        else (1920,1200,1920,1200))
            assert (got['width'],got['height'],got['screenWidth'],got['screenHeight']) == expected,got
            checks['hd_real_metrics'] = got
            view.set_view(1280,740)
            got = metrics(page)
            expected_size = (1280,740) if NATIVE_VIEW else (1920,1110)
            expected_metrics = expected_size + ((1920,1080) if NATIVE_VIEW else expected_size)
            assert (got['width'],got['height'],got['screenWidth'],got['screenHeight']) == expected_metrics,got
            if NATIVE_VIEW:
                assert view.x == view.y == 1
            checks['toolbar_metrics'] = got

            capture = sender.Screencast(page,1280,800,80,space=view)
            page.wait_for_timeout(180)
            frame = capture.take()
            assert frame is not None,'no screencast frame'
            got = image_size(frame)
            assert got == [1280,740],got
            checks['screencast_dimensions'] = got
            for quality in [0,70]:
                got = image_size(capture.screenshot(quality))
                assert got == [1280,740],got
                checks['fallback_png' if quality == 0 else 'fallback_jpeg'] = got

            injector = sender.Injector(page,IdentityMap(),page_w=1280,page_h=800,bar=object(),space=view)
            injector.set_strip(60)
            box = page.locator('#full').bounding_box()
            x = (box['x']+box['width']/2)/view.x
            y = (box['y']+box['height']/2)/view.y + 60
            assert tap(page,injector,x,y)
            got = page.evaluate('({fullscreen:!!document.fullscreenElement,...result})')
            assert got['fullscreen'] and got['trusted'] and got['active'] and got['clicks'] == 1,got
            checks['injector_fullscreen'] = got
            page.evaluate('document.exitFullscreen()')
            page.wait_for_timeout(100)

            # The keyboard overlay and physical hit-test use the same key geometry.
            keyboard = sender.Keyboard(page,1280,740,'azerty',space=view)
            keyboard._show()
            got = page.evaluate('window.__udispKb.probe()')
            assert abs(got['rect'][2]/view.x-1280)<1,got
            assert abs(got['rect'][3]/view.y-keyboard.height)<1,got
            assert abs(got['rect'][1]/view.y-keyboard.top)<1,got
            checks['keyboard_physical_rect'] = {'rect':[round(got['rect'][i]/(view.x if i%2==0 else view.y),3) for i in range(4)], 'expected_top':keyboard.top,'expected_height':keyboard.height}
            # A field keeps focus when a virtual key is tapped.
            page.locator('#search').focus()
            injector._keyboard = keyboard
            key = next(k for k in keyboard.keys if k['action'] == 'char' and k['label'] == 'a')
            x,y = key['x']+key['w']/2,key['y']+key['h']/2
            injector.handle([[(0,x,y+60)]])
            injector.handle([[]])
            assert page.locator('#search').input_value() == 'a'
            checks['virtual_key_input'] = 'a'
            keyboard.hide()

            hint = sender.HomeHint(page,1280,800,space=view)
            hint.set(0.5)
            got = page.locator('#__portall_home').bounding_box()
            physical = [got['width']/view.x,got['height']/view.y]
            expected = min(1280*sender.HOME_CORNER_FRACTION,800*sender.HOME_CORNER_FRACTION)
            assert all(abs(v-expected)<1 for v in physical),(physical,expected)
            checks['home_hint_physical_dimensions'] = physical
            hint.set(None)

            page.evaluate('startVideo()')
            page.wait_for_timeout(400)
            got = page.evaluate('({width:video.videoWidth,height:video.videoHeight,ready:video.readyState})')
            assert (got['width'],got['height']) == (1920,1080),got
            assert any('decoded=1920x1080' in line for line in media_lines),media_lines[-10:]
            checks['real_local_video_dimensions'] = got
            checks['media_sampler_observed_dimensions'] = [line for line in media_lines if 'decoded=' in line][-2:]

            page.evaluate("history.pushState({},'', '/watch?v=offline-fixture')")
            page.wait_for_timeout(60)
            view.reconcile()
            got = metrics(page)
            assert (got['width'],got['height'],got['screenWidth'],got['screenHeight']) == (1280,740,1280,740),got
            checks['spa_desktop_restoration'] = got
            page.go_back(wait_until='commit')
            page.wait_for_timeout(60)
            view.reconcile()
            got = metrics(page)
            assert (got['width'],got['height'],got['screenWidth'],got['screenHeight']) == expected_metrics,got
            checks['history_return_to_tv'] = got
            other = context.new_page()
            other.goto('https://example.test/native',wait_until='domcontentloaded')
            got = metrics(other)
            assert (got['width'],got['height']) == (1280,800),got
            checks['other_page_unaffected'] = got
            native_capture = sender.Screencast(other,1280,800,80)
            other.wait_for_timeout(120)
            assert image_size(native_capture.take()) == [1280,800]
            assert image_size(native_capture.screenshot()) == [1280,800]
            checks['default_capture_dimensions'] = [1280,800]
            native_capture.stop()
            other.close()
            page.goto('https://example.test/native',wait_until='domcontentloaded')
            got = metrics(page)
            assert (got['width'],got['height'],got['screenWidth'],got['screenHeight']) == (1280,740,1280,740),got
            checks['external_navigation_restoration'] = got
            assert image_size(capture.screenshot(70)) == [1280,740]

            # A fallback still must represent the visible viewport after scrolling.
            view.reconcile(url)
            page.goto(url,wait_until='domcontentloaded')
            page.evaluate('''() => {
                document.getElementById('player').remove();
                document.documentElement.style.cssText='height:auto;overflow:auto';
                document.body.style.cssText='height:3600px;overflow:visible;background:linear-gradient(to bottom,#f00 0px,#f00 1200px,#00f 1200px,#00f 3600px)';
                scrollTo(0,1500);
            }''')
            page.wait_for_timeout(100)
            assert page.evaluate('scrollY') == 1500
            markers = [(48,48,'#ff0000',(255,0,0)),
                       (1232,48,'#00ffff',(0,255,255)),
                       (48,692,'#00ff00',(0,255,0)),
                       (1232,692,'#ffff00',(255,255,0))]
            page.evaluate('''args => {
                for (const [x,y,color] of args.markers) {
                    const m=document.createElement('div');
                    Object.assign(m.style, {position:'absolute',
                        left:((x-16)*args.x)+'px',
                        top:(1500+(y-16)*args.y)+'px',
                        width:(32*args.x)+'px',height:(32*args.y)+'px',
                        background:color});
                    document.body.appendChild(m);
                }
            }''',{'markers':[list(m[:3]) for m in markers],'x':view.x,'y':view.y})
            visible = Image.open(io.BytesIO(capture.screenshot())).convert('RGB')
            pixel = visible.getpixel((640,370))
            reference = Image.open(io.BytesIO(base64.b64decode(capture._session.send('Page.captureScreenshot',{'format':'png'})['data']))).convert('RGB')
            reference_pixel = reference.getpixel((reference.width//2,reference.height//2))
            checks['scrolled_fallback'] = {'scroll_y':1500,'size':list(visible.size),'center_rgb':list(pixel),
                                           'unscaled_reference_center_rgb':list(reference_pixel)}
            assert reference_pixel[2] > 240 and reference_pixel[0] < 15,checks['scrolled_fallback']
            assert pixel[2] > 240 and pixel[0] < 15, checks['scrolled_fallback']
            assert list(visible.size) == [1280,740]
            for x,y,_,color in markers:
                for dx,dy in [(0,0),(-12,0),(12,0),(0,-12),(0,12)]:
                    assert visible.getpixel((x+dx,y+dy)) == color,(x,y,dx,dy)
                for dx,dy in [(-20,0),(20,0),(0,-20),(0,20)]:
                    assert visible.getpixel((x+dx,y+dy)) == (0,0,255),(x,y,dx,dy)
            checks['scrolled_fallback']['marker_checks'] = len(markers)*9
            capture.stop()

            accepted = ['https://www.youtube.com/tv','https://youtube.com/tv/','https://www.youtube.com/tv#/watch?v=x']
            rejected = ['https://www.youtube.com/watch?v=x','https://www.youtube.com/tvfake','https://www.youtube.com.evil.test/tv','https://evil.test/?url=https://www.youtube.com/tv','about:blank']
            assert all(sender.is_youtube_tv(x) for x in accepted)
            assert not any(sender.is_youtube_tv(x) for x in rejected)
            checks['url_scope'] = {'accepted':accepted,'rejected':rejected}

            class FailingMetricsSession:
                calls = 0
                def send(self, method, params):
                    self.calls += 1
                    raise RuntimeError('intentional offline fixture metrics failure')

            failure_session = FailingMetricsSession()
            view._session = failure_session
            view.set_view(1280,700)
            got = metrics(page)
            assert (got['width'],got['height']) == (1280,700),got
            assert view.x == view.y == 1
            view.reconcile(url)
            assert failure_session.calls == 1,failure_session.calls
            view.set_view(1280,800)
            got = metrics(page)
            assert (got['width'],got['height']) == (1280,800),got
            assert failure_session.calls == 1,failure_session.calls
            checks['metrics_failure_restores_native_once'] = {'metrics':got,'failed_cdp_calls':failure_session.calls}
            report['status'] = 'PASS'
        finally:
            browser.close()
except Exception as exc:
    report['status'] = 'FAIL'
    report['error'] = type(exc).__name__+': '+str(exc)
    raise
finally:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
