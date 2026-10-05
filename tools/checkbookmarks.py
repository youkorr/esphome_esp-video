#!/usr/bin/env python3
"""The bar's star opens the profile's Chrome bookmarks -- seen from the panel.

WHY THIS EXISTS. Reported as "il manque mes favoris dans le navigateur web":
a panel's browser has no toolbar, so the bookmarks Chrome keeps in the
screen's profile had nowhere to be seen.

First read_bookmarks() on a made-up profile -- both of Chrome's files, a
folder, an address listed twice, a bookmarklet that cannot open. Then the
shipped ha_send.py against a fake panel that reassembles the rectangles, a
site on `localhost` and the bookmarks page on 127.0.0.1 -- two sites, so a
SameSite=Strict cookie is withheld from an ordinary link between them:

  - the sender says how many bookmarks the profile holds;
  - a link opens, and the star in its bar opens the bookmarks page;
  - a bookmark tapped on the glass opens, carrying the site's Strict
    cookie (it is opened like an address typed into the bar);
  - and the bar's back returns to the bookmarks.

--sender PATH runs another copy of ha_send.py (an older release), which is
how the report is reproduced: there is no star to press.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
BROWSER = os.environ.get("CHROMIUM", "")
SENDER = (sys.argv[sys.argv.index("--sender") + 1]
          if "--sender" in sys.argv else str(SENDER_DIR / "ha_send.py"))
W, H = 800, 480

HOME = (0x80, 0x20, 0x20)
A = (0x20, 0x60, 0xA0)
B = (0x30, 0x90, 0x40)
SHELF = (0x12, 0x16, 0x1E)
fails = 0
cookies = {}


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


def near(colour, want, slack=14):
    return all(abs(a - b) < slack for a, b in zip(colour, want))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.strip("/").split("?")[0]
        cookies[name] = self.headers.get("Cookie") or ""
        colour = {"home": HOME, "a": A, "b": B}.get(name, HOME)
        body = (f"<!doctype html><body style='margin:0;height:100vh;"
                f"background:rgb{colour}'></body>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if name == "a":
            self.send_header("Set-Cookie",
                             "login=yes; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)


def chrome_file(children):
    return {"roots": {"bookmark_bar": {"type": "folder", "name": "bar",
                                       "children": children},
                      "other": {"type": "folder", "children": []},
                      "synced": {"type": "folder", "children": []}},
            "version": 1}


def url(name, address):
    return {"type": "url", "name": name, "url": address}


def reading():
    import ha_send
    print("Reading Chrome's files:")
    with tempfile.TemporaryDirectory() as profile:
        sections, skipped = ha_send.read_bookmarks(profile)
        check("a profile with no bookmarks reads as none",
              sections == [] and skipped == 0)
        os.makedirs(os.path.join(profile, "Default"))
        with open(os.path.join(profile, "Default", "Bookmarks"), "w") as f:
            json.dump(chrome_file([
                url("Jellyfin", "https://jelly.example/"),
                {"type": "folder", "name": "Travail", "children": [
                    url("Unraid", "http://192.168.1.3/")]},
                url("Marque-page", "javascript:alert(1)"),
                url("Netflix", "https://www.netflix.com/")]), f)
        with open(os.path.join(profile, "Default", "AccountBookmarks"),
                  "w") as f:
            json.dump(chrome_file([url("Jellyfin again",
                                       "https://jelly.example/"),
                                   url("Google", "https://www.google.com/")]),
                      f)
        sections, skipped = ha_send.read_bookmarks(profile)
        paths = [tuple(p) for p, _ in sections]
        check("the bar's own bookmarks come before its folder's",
              paths == [("bookmark_bar",), ("bookmark_bar", "Travail")],
              str(paths))
        names = [n for _, items in sections for n, _ in items]
        check("both files are read, and an address is listed once",
              names == ["Jellyfin", "Netflix", "Google", "Unraid"],
              str(names))
        check("a bookmarklet is counted, not listed", skipped == 1,
              str(skipped))
        page = ha_send.Bookmarks(profile, True).page()
        check("the page names the folder under its root",
              "Barre de favoris › Travail" in page)
        check("and says what it could not open", "1 favori(s)" in page)
        with open(os.path.join(profile, "Default", "Bookmarks"), "w") as f:
            json.dump(chrome_file([url("<b>x</b>", "https://e.example/")]), f)
        os.remove(os.path.join(profile, "Default", "AccountBookmarks"))
        check("a name is shown as text, never as markup",
              "&lt;b&gt;x&lt;/b&gt;" in ha_send.Bookmarks(profile, True).page())
    check("a screen with no profile says why it has none",
          "keep_profile" in ha_send.Bookmarks(None, True).page())


def glass():
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import ha_send

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    home = f"http://127.0.0.1:{port}/home"
    site = f"http://localhost:{port}"

    profile = tempfile.mkdtemp()
    os.makedirs(os.path.join(profile, "Default"))
    with open(os.path.join(profile, "Default", "Bookmarks"), "w") as f:
        json.dump(chrome_file([url("Page B", f"{site}/b"),
                               url("Page A", f"{site}/a")]), f)

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
                    payload = stream[_HEADER.size:_HEADER.size + total]
                    stream = stream[_HEADER.size + total:]
                    if kind != UDISP_TYPE_JPG:
                        continue
                    try:
                        pic = Image.open(io.BytesIO(payload)).convert("RGB")
                    except Exception:  # noqa: BLE001
                        continue
                    with lock:
                        canvas.paste(pic, (x, y))
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    bar = ha_send.NavBar(W, H)
    strip = bar.height
    spots = {b["name"]: (int(b["x"] + b["w"] / 2), strip // 2)
             for b in bar.buttons}
    middle = (W // 2, H * 2 // 3)
    low = (W // 2, H - 6)

    def colour(at):
        with lock:
            return canvas.getpixel(at)

    def wait(want, at=middle, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if near(colour(at), want):
                return True
            time.sleep(0.05)
        return False

    def tap(at):
        x, y = at
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    command = [sys.executable, "-u", SENDER,
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", home,
               "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control", "--profile", os.path.join(profile, "p"),
               "--locale", "fr-FR"]
    # The profile the sender opens is a copy made below, so Chrome's own
    # rewrite of the file on the way out cannot touch the fixture.
    import shutil
    shutil.copytree(profile, os.path.join(profile, "p"),
                    ignore=shutil.ignore_patterns("p"))
    if BROWSER:
        command += ["--browser", BROWSER]
    os.environ.pop("HA_TOKEN", None)
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def ask(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def said(pattern, seconds=8):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for line in list(out):
                found = re.search(pattern, line)
                if found:
                    return found
            time.sleep(0.05)
        return None

    try:
        print("On the glass:")
        check("the panel's own page arrives", wait(HOME, seconds=40))
        check("the log says how many bookmarks the profile holds",
              said(r"Bookmarks: 2 in this screen's profile") is not None,
              "".join(line for line in out if "Bookmark" in line))
        ask(f"open {site}/a")
        check("a link opens", wait(A))
        time.sleep(0.5)
        tap(spots.get("bookmarks", (W - 10, strip // 2)))
        opened = said(r"Bar: bookmarks -> (http://127\.0\.0\.1:\d+/)")
        check("the star opens the bookmarks page", opened is not None
              and wait(SHELF, at=low), str(colour(low)))
        if opened is None:
            return
        # Where the first bookmark is drawn, asked of the same page in the
        # same browser at the size the sender gives it (the panel less the
        # strip).
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=BROWSER or None)
            probe = browser.new_page(viewport={"width": W,
                                               "height": H - strip})
            probe.goto(opened.group(1))
            box = probe.locator("a.bm").first.bounding_box()
            browser.close()
        cookies.pop("b", None)
        tap((int(box["x"] + box["width"] / 2),
             int(box["y"] + box["height"] / 2) + strip))
        check("a bookmark tapped on the glass opens", wait(B))
        check("and the site's Strict login goes with it",
              "login=yes" in cookies.get("b", ""), repr(cookies.get("b")))
        time.sleep(0.5)
        tap(spots["back"])
        check("the bar's back returns to the bookmarks",
              wait(SHELF, at=low), str(colour(low)))
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    reading()
    glass()
    print("\nAll good." if not fails else f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
