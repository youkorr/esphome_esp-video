#!/usr/bin/env python3
"""The panel's own Files page: a download opened, played, and made a wallpaper.

WHY THIS EXISTS. Asked for in those words: every download "inscrit dans
l'addon, que je puisse utiliser par exemple pour un wallpaper, lecture video
ou musique et fichier comme un PC". The launcher serves the page (files.py)
and a link whose url is `files` opens it. Checked in the browser the add-on
ships, at a panel's size, with the sender's own browser arguments:

  - every screen's downloads are listed, newest first, and a `files` link is
    a tile opening the page;
  - a picture is shown, a song plays, a film plays and can be moved through
    (byte ranges), a text is shown, and a file that cannot be opened says so;
  - a picture made the wallpaper is behind the launcher at once, without a
    restart, and survives one; a film made the wallpaper plays there;
  - removing it brings the configured wallpaper back, and deleting a file
    takes two touches, removes it, and takes it off the wall if it was there;
  - a name that is not in the listing is refused, whatever it reaches for.

Needs Playwright. Takes $CHROMIUM for the browser.
"""
import base64
import io
import math
import os
import pathlib
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "portall"))
sys.path.insert(0, str(HERE / "components" / "portall"))
BROWSER = os.environ.get("CHROMIUM", "")
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def png(rgb):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), rgb).save(buffer, "PNG")
    return buffer.getvalue()


def wav(seconds=3.0):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(22050)
        out.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(i * 440 * 2 * math.pi
                                                  / 22050)))
            for i in range(int(22050 * seconds))))
    return buffer.getvalue()


PDF = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def webm(page):
    """A real three-second VP8 film, made by the browser itself."""
    data = page.evaluate("""() => new Promise(done => {
      const c = document.createElement('canvas'); c.width = 160; c.height = 90;
      const g = c.getContext('2d'); let n = 0;
      const r = new MediaRecorder(c.captureStream(25),
                                  {mimeType: 'video/webm;codecs=vp8'});
      const parts = [];
      r.ondataavailable = e => parts.push(e.data);
      r.onstop = () => { const f = new FileReader();
        f.onload = () => done(f.result.split(',')[1]);
        f.readAsDataURL(new Blob(parts, {type: 'video/webm'})); };
      const t = setInterval(() => { g.fillStyle = n++ % 2 ? '#0f0' : '#00f';
        g.fillRect(0, 0, 160, 90); }, 40);
      r.start(200); setTimeout(() => { clearInterval(t); r.stop(); }, 3000);
    })""")
    return base64.b64decode(data)


def configured_still_works(launcher, root):
    """The wallpaper set in the add-on, untouched by the Files page."""
    import json as _json
    print("The wallpaper set in the add-on still works:")
    folder = tempfile.mkdtemp()
    for i, rgb in enumerate(((1, 2, 3), (4, 5, 6))):
        with open(os.path.join(folder, f"{i}.png"), "wb") as out:
            out.write(png(rgb))
    one = os.path.join(folder, "0.png")
    where = launcher.start([{"name": "x", "url": "https://x.invalid/"}],
                           port=launcher.ANY_PORT, background=one,
                           clock=False, files_root=root,
                           choice_file=os.path.join(folder, "none.json"))
    with urllib.request.urlopen(where + "wallpaper") as answer:
        check("a file", answer.read() == png((1, 2, 3)))
    where = launcher.start([{"name": "x", "url": "https://x.invalid/"}],
                           port=launcher.ANY_PORT, background=folder,
                           slideshow=True, clock=False)
    with urllib.request.urlopen(where + "wallpaper?i=1") as answer:
        second = answer.read()
    with urllib.request.urlopen(where + launcher.SLIDES_PATH.lstrip("/")) as a:
        count = _json.load(a)["count"]
    check("a folder, as a slideshow", second == png((4, 5, 6)) and count == 2,
          str(count))
    where = launcher.start([{"name": "x", "url": "https://x.invalid/"}],
                           port=launcher.ANY_PORT, clock=False,
                           background="https://pictures.invalid/sky.jpg")
    with urllib.request.urlopen(where) as answer:
        check("an address", "https://pictures.invalid/sky.jpg"
              in answer.read().decode())


def on_the_panel(launcher, ha_send, root, film):
    """The whole way: the real sender, a fake panel, real contacts."""
    import socket
    import subprocess
    import threading
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from udisp_send import _HEADER, UDISP_TYPE_JPG

    print("On a panel, through the sender:")
    W, H = 800, 480
    for name in os.listdir(os.path.join(root, "salon")):
        if name != "film.webm":
            os.remove(os.path.join(root, "salon", name))
    where = launcher.start([{"name": "Fichiers", "url": "files"}],
                           port=launcher.ANY_PORT, files_root=root,
                           clock=False)
    strip = ha_send.NavBar(W, H).height
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=BROWSER or None)
        look = browser.new_page(viewport={"width": W, "height": H})
        look.goto(where)
        tile = look.eval_on_selector("a.tile", "a => { const r = "
                                     "a.getBoundingClientRect(); return "
                                     "[r.x + r.width / 2, r.y + r.height / 2]; }")
        look.set_viewport_size({"width": W, "height": H - strip})
        look.goto(where.rstrip("/") + "/files")
        row = look.eval_on_selector("a.row:has-text('film.webm')", "a => { const r = "
                                    "a.getBoundingClientRect(); return "
                                    "[r.x + r.width / 2, r.y + r.height / 2]; }")
        look.click("a.row:has-text('film.webm')")
        look.wait_for_selector(".stage")
        stage = look.eval_on_selector(".stage", "a => { const r = "
                                      "a.getBoundingClientRect(); return "
                                      "[r.x + r.width / 2, r.y + r.height / 2]; }")
        browser.close()

    canvas = Image.new("RGB", (W, H))
    lock = threading.Lock()
    panel = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def accept():
        conn, _ = listener.accept()
        panel.append(conn)
        stream = b""
        try:
            while True:
                data = conn.recv(1 << 20)
                if not data:
                    return
                stream += data
                while len(stream) >= _HEADER.size:
                    _, kind, _, x, y, w, h, packed = _HEADER.unpack_from(stream)
                    total = packed >> 10
                    if len(stream) < _HEADER.size + total:
                        break
                    blob = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG:
                        with lock:
                            canvas.paste(Image.open(io.BytesIO(blob))
                                         .convert("RGB"), (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    def tap(at):
        x, y = int(at[0]), int(at[1])
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    command = [sys.executable, "-u",
               str(HERE / "components" / "portall" / "ha_send.py"),
               "--host", "127.0.0.1", "--port", str(listener.getsockname()[1]),
               "--url", where, "--no-token", "--not-home-assistant",
               "--width", str(W), "--height", str(H), "--audio", "off",
               "--keyboard", "off"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and not panel:
            time.sleep(0.1)
        time.sleep(3)
        tap(tile)
        time.sleep(2)
        tap((row[0], row[1] + strip))
        seen = set()
        end = time.monotonic() + 6
        at = (int(stage[0]), int(stage[1] + strip))
        while time.monotonic() < end:
            with lock:
                r, g, b = canvas.getpixel(at)
            seen.add("green" if g > 150 and r < 80 and b < 80 else
                     "blue" if b > 150 and r < 80 and g < 80 else "other")
            time.sleep(0.03)
        check("a tile and a touch play a film on the panel, frame after "
              "frame", {"green", "blue"} <= seen, str(seen))
        crashed = [l for l in out if "Traceback" in l]
        check("the sender did not crash", not crashed, str(crashed))
    finally:
        process.kill()
        listener.close()


def main():
    try:
        from playwright.sync_api import sync_playwright
        import PIL  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    import launcher
    import ha_send

    root = tempfile.mkdtemp()
    data = tempfile.mkdtemp()
    for screen in ("salon", "cuisine"):
        os.makedirs(os.path.join(root, screen))

    def put(screen, name, blob, age):
        path = os.path.join(root, screen, name)
        with open(path, "wb") as out:
            out.write(blob)
        when = time.time() - age
        os.utime(path, (when, when))
        return path

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=BROWSER or None, args=ha_send.BROWSER_ARGS)
        page = browser.new_page(viewport={"width": 1024, "height": 560},
                                locale="fr-FR")
        page.goto("about:blank")
        film = webm(page)
        put("salon", "rouge.png", png((220, 30, 30)), 60)
        put("salon", "film.webm", film, 50)
        put("cuisine", "chanson.wav", wav(), 40)
        put("cuisine", "notice.pdf", PDF, 30)
        put("cuisine", "lisez-moi.txt", "Bonjour le panneau".encode(), 20)
        put("salon", "archive.bin", b"\x00" * 1000, 10)
        put("salon", "en-cours.mp3.part", b"x", 5)
        choice = os.path.join(data, "wallpaper", "house.json")
        where = launcher.start(
            [{"name": "Fichiers", "url": "files", "icon": "dossier"},
             {"name": "Ailleurs", "url": "https://example.invalid/"}],
            port=launcher.ANY_PORT, files_root=root, choice_file=choice,
            clock=False)
        base = where.rstrip("/")

        print("The tile and the list:")
        page.goto(where)
        href = page.eval_on_selector("a.tile", "a => a.getAttribute('href')")
        check("a link whose url is `files` is a tile opening the page",
              href == "/files", href)
        page.goto(base + "/files")
        if "--picture" in sys.argv:
            page.screenshot(path=sys.argv[sys.argv.index("--picture") + 1])
        names = page.eval_on_selector_all("a.row b", "e => e.map(x => x.textContent)")
        check("every screen's downloads are listed, newest first",
              names == ["archive.bin", "lisez-moi.txt", "notice.pdf",
                        "chanson.wav", "film.webm", "rouge.png"], str(names))
        check("in the panel's language",
              page.inner_text("h1") == "Fichiers", page.inner_text("h1"))

        def view(name):
            page.goto(base + "/files")
            page.click(f"a.row:has-text('{name}')")
            page.wait_for_load_state()

        print("Opened on the glass:")
        view("rouge.png")
        check("a picture is shown", page.eval_on_selector(
            ".stage img", "i => i.complete && i.naturalWidth") == 64)
        view("chanson.wav")
        time.sleep(1.5)
        state = page.eval_on_selector(
            "audio", "a => [a.currentTime, a.paused, !!a.error]")
        check("a song plays", state[0] > 0.5 and not state[1] and not state[2],
              str(state))
        view("film.webm")
        if "--picture" in sys.argv:
            page.screenshot(path=sys.argv[sys.argv.index("--picture") + 1]
                            .replace(".png", "-film.png"))
        time.sleep(1.2)
        state = page.eval_on_selector(
            "video", "v => [v.currentTime, v.paused, !!v.error, v.videoWidth]")
        check("a film plays", state[0] > 0.3 and not state[1]
              and not state[2] and state[3] == 160, str(state))
        page.eval_on_selector("video", "v => { v.currentTime = 2.0; }")
        time.sleep(0.6)
        moved = page.eval_on_selector("video", "v => v.currentTime")
        check("and can be moved through", moved >= 2.0, str(moved))
        request = urllib.request.Request(
            base + "/files/raw?f=salon%2Ffilm.webm",
            headers={"Range": "bytes=100-199"})
        with urllib.request.urlopen(request) as answer:
            piece = answer.read()
            check("a range is answered as a range", answer.status == 206
                  and piece == film[100:200], f"{answer.status} {len(piece)}")
        view("lisez-moi.txt")
        check("a text is shown", "Bonjour le panneau" in page.inner_text("pre"))
        view("notice.pdf")
        check("a PDF is handed to the browser's own viewer", page.eval_on_selector(
            ".stage iframe", "f => f.getAttribute('src')").startswith("/files/raw"))
        view("archive.bin")
        check("a file that cannot be opened says so",
              "ne s'ouvre pas sur l" in page.inner_text("main"))
        check("and offers no wallpaper", page.query_selector(
            "button[data-do=wallpaper]") is None)

        print("The wallpaper:")
        view("rouge.png")
        page.click("button[data-do=wallpaper]")
        page.wait_for_selector("button[data-do=unwallpaper]")
        page.goto(where)
        page.wait_for_timeout(300)
        colour = page.evaluate("""() => {
          const c = document.createElement('canvas'); c.width = c.height = 1;
          return new Promise(done => { const i = new Image();
            i.onload = () => { const g = c.getContext('2d'); g.drawImage(i, 0, 0);
              done([...g.getImageData(0, 0, 1, 1).data].slice(0, 3)); };
            i.src = '/wallpaper?t=' + Date.now(); }); }""")
        check("a picture made the wallpaper is served at once",
              colour == [220, 30, 30], str(colour))
        wall = page.evaluate(
            "() => getComputedStyle(document.querySelector('.wall') || "
            "document.body).backgroundImage")
        check("and the launcher shows it", "/wallpaper" in wall, wall)
        again = launcher.start(
            [{"name": "Fichiers", "url": "files"}], port=launcher.ANY_PORT,
            files_root=root, choice_file=choice, clock=False)
        with urllib.request.urlopen(again + "wallpaper") as answer:
            check("and it survives a restart",
                  answer.read() == png((220, 30, 30)))
        view("film.webm")
        page.click("button[data-do=wallpaper]")
        page.wait_for_selector("button[data-do=unwallpaper]")
        page.goto(where)
        time.sleep(1.5)
        playing = page.evaluate(
            "() => { const v = document.querySelector('video'); "
            "return v ? [v.currentTime, v.paused, !!v.error] : null; }")
        check("a film made the wallpaper plays behind the launcher",
              playing is not None and playing[0] > 0.3 and not playing[1],
              str(playing))
        page.goto(base + "/files")
        page.click("button[data-do=unwallpaper]")
        page.wait_for_function(
            "() => !document.querySelector('button[data-do=unwallpaper]')")
        page.goto(where)
        check("removing it brings the configured one back (none here)",
              page.query_selector("video") is None
              and not os.path.exists(choice))

        print("Deleting:")
        view("rouge.png")
        page.click("button[data-do=wallpaper]")
        page.wait_for_selector("button[data-do=unwallpaper]")
        page.click("button[data-do=delete]")
        time.sleep(0.4)
        check("one touch only asks", os.path.exists(
            os.path.join(root, "salon", "rouge.png")))
        page.click("button[data-do=delete]")
        page.wait_for_url("**/files")
        check("the second deletes it", not os.path.exists(
            os.path.join(root, "salon", "rouge.png")))
        check("and takes it off the wall", not os.path.exists(choice))
        check("and the list no longer shows it",
              "rouge.png" not in page.inner_text("main"))
        browser.close()

    configured_still_works(launcher, root)
    on_the_panel(launcher, ha_send, root, film)

    print("Names that are not in the listing:")
    for bad in ("..%2F..%2Fetc%2Fpasswd", "salon%2F..%2F..%2Fetc%2Fpasswd",
                "salon%2Fen-cours.mp3.part", "salon%2F.hidden", "nothing"):
        try:
            urllib.request.urlopen(base + "/files/raw?f=" + bad)
            refused = False
        except urllib.error.HTTPError as err:
            refused = err.code == 404
        check(f"refused: {bad}", refused)
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
