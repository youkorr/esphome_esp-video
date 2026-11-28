"""Local, opt-in TCP measurements between this add-on and configured panels."""
import html
import json
import re
import secrets
import socket
import statistics
import threading
import time

PATH = "/network-test"
LIMIT = 64 * 1024 * 1024
SECONDS = 5
BLOCK = bytes(range(256)) * 256
_LOCK = threading.Lock()


def read_line(sock, limit=128):
    line = bytearray()
    deadline = time.monotonic() + 3
    while len(line) < limit:
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        if time.monotonic() >= deadline:
            raise TimeoutError("Incomplete speedtest response")
        chunk = sock.recv(1)
        if not chunk:
            break
        line += chunk
        if chunk == b"\n":
            return bytes(line)
    raise OSError("Invalid or incomplete speedtest response")


def connect(host, port, key, op):
    sock = socket.create_connection((host, port), timeout=3)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        start = time.perf_counter()
        sock.sendall(f"PST1 {key} {op}\n".encode("ascii"))
        if read_line(sock) != b"OK\n":
            raise OSError("Speedtest authentication failed")
        return sock, (time.perf_counter() - start) * 1000
    except BaseException:
        sock.close()
        raise


def measure(host, port, key):
    samples = []
    for _ in range(5):
        sock, ms = connect(host, port, key, "P")
        sock.close()
        samples.append(ms)
    sock, _ = connect(host, port, key, "R")
    with sock:
        sent = 0
        end = time.monotonic() + SECONDS
        while sent < LIMIT and time.monotonic() < end:
            sock.settimeout(2)
            block = BLOCK[:min(len(BLOCK), LIMIT - sent)]
            sock.sendall(block)
            sent += len(block)
        sock.shutdown(socket.SHUT_WR)
        match = re.fullmatch(rb"RX ([0-9]+) ([0-9]+)\n", read_line(sock))
        if not match:
            raise OSError("Panel did not acknowledge the received bytes")
        got, elapsed = map(int, match.groups())
        if got != sent or got < 65536 or not 0 < elapsed <= 10000000:
            raise OSError("Incomplete download measurement")
        rx = round(got * 8 / elapsed, 2)
    sock, _ = connect(host, port, key, "T")
    with sock:
        got = 0
        first = last = None
        end = time.monotonic() + 9
        while time.monotonic() < end:
            sock.settimeout(2)
            chunk = sock.recv(65536)
            if not chunk:
                break
            now = time.perf_counter()
            if first is None:
                first = now
            last = now
            got += len(chunk)
            if got > LIMIT + 16384:
                raise OSError("Panel exceeded the byte limit")
        else:
            raise TimeoutError("Upload exceeded the time limit")
        if got < 65536 or first is None or last <= first:
            raise OSError("Incomplete upload measurement")
        tx = round(got * 8 / (last - first) / 1e6, 2)
    return {"rx": rx, "tx": tx, "rtt": round(statistics.median(samples), 2),
            "rx_bytes": sent, "tx_bytes": got}


class Speedtest:
    def __init__(self, panels):
        self.targets = {}
        for name, panel in panels:
            key = str(panel.get("speedtest_key") or "")
            host = str(panel.get("host") or "").strip()
            try:
                port = int(panel.get("speedtest_port") or 5201)
            except (TypeError, ValueError):
                continue
            if host and re.fullmatch(r"[0-9a-fA-F]{64}", key) and 1 <= port <= 65535:
                self.targets[name] = (host, port, key)
        self.nonce = secrets.token_urlsafe(24)

    def page(self):
        buttons = "".join('<button class="target" data-panel="' + html.escape(name, quote=True)
                          + '">' + html.escape(name) + '</button>' for name in self.targets)
        return (PAGE.replace("{{TARGETS}}", buttons)
                .replace("{{NONCE}}", json.dumps(self.nonce))).encode()

    def run(self, requested):
        if not isinstance(requested, dict) or not secrets.compare_digest(
            str(requested.get("nonce", "")).encode(), self.nonce.encode()
        ):
            raise PermissionError("Invalid speedtest request")
        name = requested.get("panel")
        if not isinstance(name, str) or name not in self.targets:
            raise ValueError("Unknown panel")
        if not _LOCK.acquire(blocking=False):
            raise RuntimeError("A network test is already running")
        try:
            return measure(*self.targets[name])
        finally:
            _LOCK.release()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portall • Network test</title><style>
*{box-sizing:border-box}body{margin:0;background:#101725;color:#edf3fb;font:18px system-ui,sans-serif;padding:clamp(18px,5vw,50px)}
main{max-width:850px;margin:auto}a{color:#8acfff}h1{font-size:clamp(28px,5vw,48px);margin:20px 0 8px}
p{color:#aebed3;line-height:1.5}.targets{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}
button{padding:15px 22px;min-height:56px;border:1px solid #53728f;background:#1d344b;border-radius:14px;color:white;font:inherit}
button:disabled{opacity:.5}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}
article{border:1px solid #34475c;border-radius:20px;padding:24px;background:#172435}
small{display:block;color:#aebed3}strong{display:block;font-size:42px;margin:14px 0 4px}
#status{min-height:60px}#detail{font-size:14px}
</style></head><body><main><a href="/">← Portall</a><h1>Network test</h1>
<p>Local TCP • Home Assistant ↔ ESP32-P4. No internet test, no advertisements.</p>
<p>Select a panel to start. Stop video playback first; the display connection stays active.</p>
<div class="targets">{{TARGETS}}</div><section>
<article><small>Download to P4</small><strong id="rx">—</strong>Mbit/s</article>
<article><small>Upload from P4</small><strong id="tx">—</strong>Mbit/s</article>
<article><small>TCP request RTT</small><strong id="rtt">—</strong>ms</article>
</section><p id="status" role="status">Ready.</p><p id="detail"></p>
<p>Experimental: results include concurrent display and application traffic. TCP RTT includes the test service's response time.</p>
</main><script>
const nonce={{NONCE}}, buttons=[...document.querySelectorAll('.target')];
const status=document.getElementById('status'), detail=document.getElementById('detail');
for(const btn of buttons) btn.addEventListener('click',async()=>{
  buttons.forEach(b=>b.disabled=true);
  for(const id of ['rx','tx','rtt']) document.getElementById(id).textContent='—';
  status.textContent='Testing '+btn.dataset.panel+'…';detail.textContent='';
  try {
    const reply=await fetch('/network-test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({panel:btn.dataset.panel,nonce})});
    const result=await reply.json();
    if(!reply.ok || result.error) throw Error(result.error||'Test failed');
    for(const id of ['rx','tx','rtt']) document.getElementById(id).textContent=result[id];
    status.textContent='Test complete: '+btn.dataset.panel;
    detail.textContent='Received by P4: '+result.rx_bytes+' bytes · received by server: '+result.tx_bytes+' bytes';
  } catch(err) {status.textContent=String(err.message||err);}
  finally {buttons.forEach(b=>b.disabled=false);}
});
</script></body></html>"""
