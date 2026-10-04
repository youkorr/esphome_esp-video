#!/usr/bin/env python3
"""A file downloaded from a panel's page is kept, and can be fetched again.

WHY THIS EXISTS. Reported as downloading "qui ne fonctionne pas": a page's
download went into Playwright's temporary folder, deleted when the browser
closed, and the panel showed nothing at all. Now the sender keeps it in the
panel's own folder (--downloads, which the add-on gives every panel) and the
add-on's page lists it.

  - a download link on a page, tapped from a fake panel through the return
    channel, is kept whole, under its own name, with no .part left behind;
  - so is one that opens in a new window (target=_blank), the usual shape;
  - the picture goes on while a slow file arrives: save_as() waits, and it
    must not wait in the loop -- pictures are counted at the fake panel
    during the download, on a page that never stops moving;
  - the add-on's page lists the file, gives back its bytes with its name,
    refuses a name that reaches outside the folder, and deletes it.

Needs Playwright and Pillow. Takes $CHROMIUM for the browser.
"""
import http.server
import io
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
SENDER_DIR = HERE / "components" / "portall"
sys.path.insert(0, str(SENDER_DIR))
sys.path.insert(0, str(HERE / "portall"))
BROWSER = os.environ.get("CHROMIUM", "")
W, H = 800, 480
BODY = bytes(range(256)) * 4096          # 1 MiB, every byte value
SLOW_S = 3.0
SECRET = b"\xff\xd8\xff\xe0 a picture for members only"
fails = 0


def check(what, ok, detail=""):
    global fails
    print(("  ok     " if ok else "  ECHEC  ") + what
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        fails += 1


PAGE = (b"<!doctype html><body style='margin:0;height:100vh;background:#246'>"
        b"<a href='/file.bin' style='position:absolute;left:20px;top:120px;"
        b"width:200px;height:80px;background:#fc0;display:block'>file</a>"
        b"<a href='/other.bin' target=_blank style='position:absolute;"
        b"left:300px;top:120px;width:200px;height:80px;background:#0cf;"
        b"display:block'>other</a>"
        b"<a id=blob href='#' style='position:absolute;left:560px;top:120px;"
        b"width:200px;height:80px;background:#f0c;display:block' onclick=\""
        b"fetch('/secret.jpg').then(r=>r.blob()).then(b=>{const a="
        b"document.createElement('a');a.href=window.URL.createObjectURL(b);"
        b"a.download='made.jpg';document.body.appendChild(a);a.click();});"
        b"return false;\">blob</a>"
        b"<a download='tiny.txt' href='data:text/plain;base64,"
        b"Ym9uam91cg==' style='position:absolute;left:560px;top:220px;"
        b"width:200px;height:60px;background:#9f9;display:block'>data</a>"
        b"<a download href='/secret.jpg' style='position:absolute;left:20px;"
        b"top:220px;width:200px;height:60px;background:#ccc;display:block'>"
        b"secret</a>"
        b"<script>document.cookie='member=yes; path=/'</script>"
        b"<div id=m style='position:absolute;left:0;top:300px;width:100%;"
        b"height:100px'></div><script>let n=0;(function go(){n++;"
        b"document.getElementById('m').style.background="
        b"'hsl('+(n*7%360)+',80%,50%)';requestAnimationFrame(go);})();"
        b"</script>")


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/secret.jpg":
            # Served only to the visitor who has the page's cookie, the way
            # a signed-in site serves its pictures.
            if "member=yes" not in (self.headers.get("Cookie") or ""):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(SECRET)))
            self.end_headers()
            self.wfile.write(SECRET)
            return
        if self.path in ("/file.bin", "/other.bin"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             "attachment; filename=" + self.path[1:])
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            slow = self.path == "/file.bin"
            step = len(BODY) // 30
            for at in range(0, len(BODY), step):
                self.wfile.write(BODY[at:at + step])
                if slow:
                    time.sleep(SLOW_S / 30)
            return
        body = PAGE if self.path.startswith("/p") else b"<body>home"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    try:
        import playwright  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("skipped: needs Playwright and Pillow")
        return 0
    from udisp_send import _HEADER, UDISP_TYPE_JPG
    import ha_send
    import signin

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    folder = tempfile.mkdtemp()

    pictures = []
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
                    stream = stream[_HEADER.size + total:]
                    if kind == UDISP_TYPE_JPG:
                        pictures.append(time.monotonic())
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()

    strip = ha_send.NavBar(W, H).height

    def tap(x, y):
        panel[0].sendall(b"T\x01\x00" + x.to_bytes(2, "little")
                         + y.to_bytes(2, "little"))
        time.sleep(0.08)
        panel[0].sendall(b"T\x00")

    def until(test, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.05)
        return False

    command = [sys.executable, "-u", str(SENDER_DIR / "ha_send.py"),
               "--host", "127.0.0.1", "--port",
               str(listener.getsockname()[1]), "--url", f"{site}/home",
               "--no-token", "--not-home-assistant", "--width", str(W),
               "--height", str(H), "--audio", "off", "--keyboard", "off",
               "--control", "--downloads", folder, "--fps", "25"]
    if BROWSER:
        command += ["--browser", BROWSER]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    out = []
    threading.Thread(target=lambda: out.extend(process.stdout),
                     daemon=True).start()

    def ask(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    kept = os.path.join(folder, "file.bin")
    try:
        print("From the panel:")
        until(lambda: panel and pictures, 40)
        ask(f"open {site}/p")
        time.sleep(2.0)
        tap(120, strip + 160)
        until(lambda: any("Download: file.bin from" in l for l in out), 5)
        began = time.monotonic()
        arrived = until(lambda: os.path.exists(kept), SLOW_S + 10)
        took = time.monotonic() - began
        during = [t for t in pictures if began + 0.3 < t < began + took - 0.3]
        rate = len(during) / max(0.1, took - 0.6)
        check("a download link is kept in the panel's folder", arrived)
        check("whole, byte for byte",
              arrived and open(kept, "rb").read() == BODY)
        check("with no half-written file left",
              not any(n.endswith(".part") for n in os.listdir(folder)),
              str(os.listdir(folder)))
        check(f"the picture goes on while it arrives ({rate:.0f} pictures/s "
              f"over {took:.1f}s)", took > SLOW_S * 0.7 and rate > 8)
        tap(400, strip + 160)
        check("one that opens a new window is kept too",
              until(lambda: os.path.exists(os.path.join(folder, "other.bin")),
                    10), str(os.listdir(folder)))
        tap(120, strip + 160)
        check("the same name again does not overwrite the first",
              until(lambda: os.path.exists(
                  os.path.join(folder, "file (2).bin")), SLOW_S + 10),
              str(os.listdir(folder)))
        tap(120, strip + 250)
        check("a file served only with the page's cookie is kept",
              until(lambda: os.path.exists(os.path.join(folder, "secret.jpg")),
                    6) and open(os.path.join(folder, "secret.jpg"), "rb")
              .read() == SECRET, str(os.listdir(folder)))
        tap(660, strip + 160)
        check("a file the page makes itself (blob:) is kept",
              until(lambda: os.path.exists(os.path.join(folder, "made.jpg")),
                    6) and open(os.path.join(folder, "made.jpg"), "rb")
              .read() == SECRET, str(os.listdir(folder)))
        tap(660, strip + 250)
        check("so is a data: address",
              until(lambda: os.path.exists(os.path.join(folder, "tiny.txt")),
                    6) and open(os.path.join(folder, "tiny.txt"), "rb")
              .read() == b"bonjour", str(os.listdir(folder)))
        check("and the browser's own download machinery is never used",
              not any("accept_downloads" in l for l in out))
        said = [l.strip() for l in out if l.startswith("Download:")]
        print("    " + "\n    ".join(said[:12]))
        crashed = [l.strip() for l in out if "Traceback" in l]
        check("the sender did not crash", not crashed, str(crashed))
    finally:
        process.kill()
        listener.close()
        server.shutdown()

    print("On the add-on's page:")
    page = signin.SignIn({"salon": {"profile": None, "downloads": folder}},
                         lambda n: None, lambda n: None, lambda s: None,
                         peers=("127.0.0.1",))
    port = page.serve(0, host="127.0.0.1")
    base = f"http://127.0.0.1:{port}"
    listing = urllib.request.urlopen(base + "/").read().decode()
    check("it lists the files", all(n in listing for n in
                                     ("file.bin", "other.bin", "file (2).bin")))
    reply = urllib.request.urlopen(base + "/file?panel=salon&name=file.bin")
    check("and gives one back whole, under its name",
          reply.read() == BODY and "file.bin" in
          reply.headers.get("Content-Disposition", ""))
    for bad in ("../" + os.path.basename(folder), "..%2Fetc%2Fpasswd",
                ".hidden", "nothing.bin"):
        try:
            urllib.request.urlopen(base + "/file?panel=salon&name=" + bad)
            refused = False
        except urllib.error.HTTPError as err:
            refused = err.code == 404
        check(f"a name outside the folder is refused ({bad})", refused)
    urllib.request.urlopen(urllib.request.Request(
        base + "/delete", data=b"panel=salon&name=other.bin", method="POST"))
    check("and one can be deleted",
          not os.path.exists(os.path.join(folder, "other.bin"))
          and os.path.exists(kept))
    print("ok" if not fails else f"{fails} ECHEC")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
