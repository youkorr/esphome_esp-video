"""The panel's own Files page: what its pages downloaded, opened on the glass.

Asked as "toutes les telechargements doivent etre inscrits dans l'addon, que
je puisse les utiliser, par exemple pour un wallpaper, lecture video ou
musique et fichier comme un PC". 4.35 kept every download and listed it on
the add-on's page in Home Assistant -- for a telephone or a PC to fetch. This
is the other half: a page the PANEL opens, served by its launcher, listing
the downloads of every screen, newest first, where a picture is shown, a film
or a song played, a PDF read, and a picture or a film made the launcher's
wallpaper.

A link whose url is the word `files` is a tile opening it.

Served only by the launcher, which listens on 127.0.0.1: nothing outside the
add-on's own container can reach it, so it needs no sign-in. Every name a
request carries is looked up in the listing, never joined to a path blind.
"""
import html
import json
import mimetypes
import os
import time

KEYWORD = "files"
PATH = "/files"

KINDS = {
    "image": (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp",
              ".svg"),
    "video": (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv"),
    "audio": (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav",
              ".flac", ".weba"),
    "pdf": (".pdf",),
    "text": (".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml",
             ".ini", ".conf"),
}
GLYPH = {"image": "\U0001F5BC️", "video": "\U0001F3AC", "audio": "\U0001F3B5",
         "pdf": "\U0001F4C4", "text": "\U0001F4DD", "other": "\U0001F4E6"}
# What can be made a wallpaper: a picture, or a film the launcher plays.
WALLPAPER_KINDS = ("image", "video")
TEXT_LIMIT = 200 * 1024


def kind_of(name):
    lowered = str(name).lower()
    for kind, suffixes in KINDS.items():
        if lowered.endswith(suffixes):
            return kind
    return "other"


def entries(root):
    """Every screen's downloads, newest first.

    Each is a dict: ref ("<screen folder>/<name>", what a request names),
    screen, name, path, size, when, kind. A file still arriving is under a
    .part name and is not listed.
    """
    found = []
    if not root or not os.path.isdir(root):
        return found
    for screen in sorted(os.listdir(root)):
        folder = os.path.join(root, screen)
        if screen.startswith(".") or not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            if not entry.is_file() or entry.name.startswith(".") \
                    or entry.name.endswith(".part"):
                continue
            info = entry.stat()
            found.append({"ref": f"{screen}/{entry.name}", "screen": screen,
                          "name": entry.name, "path": entry.path,
                          "size": info.st_size, "when": info.st_mtime,
                          "kind": kind_of(entry.name)})
    return sorted(found, key=lambda e: e["when"], reverse=True)


def find(root, ref):
    """The entry a request names, or None -- only ever one that is listed."""
    for entry in entries(root):
        if entry["ref"] == ref:
            return entry
    return None


def size_text(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def send(handler, path, head_only=False):
    """A file, with byte ranges: a <video> seeks by asking for a range.

    Without them a film plays from the start and cannot be moved through,
    and some players refuse to start at all.
    """
    size = os.path.getsize(path)
    start, end = 0, size - 1
    wanted = handler.headers.get("Range", "")
    partial = False
    if wanted.startswith("bytes="):
        first, _, last = wanted[6:].split(",")[0].partition("-")
        try:
            if first:
                start = int(first)
                end = int(last) if last else end
            elif last:
                start = max(0, size - int(last))
            partial = True
        except ValueError:
            start, end = 0, size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", mimetypes.guess_type(path)[0]
                        or "application/octet-stream")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(end - start + 1))
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if head_only:
        return
    with open(path, "rb") as source:
        source.seek(start)
        left = end - start + 1
        try:
            while left > 0:
                chunk = source.read(min(1 << 16, left))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a player that moved on asks for another range


def _q(ref):
    from urllib.parse import quote
    return quote(ref, safe="")


STYLE = """
 :root { --ground:#0f1219; --card:#1a1f2b; --edge:#2b3242; --ink:#eef1f6;
         --faint:#98a1b3; --accent:#5b8ff0; --warn:#f0a35b; }
 * { box-sizing:border-box; }
 html,body { margin:0; height:100%; background:var(--ground); color:var(--ink);
   font:clamp(16px,2.4vmin,22px)/1.35 system-ui,sans-serif; }
 main { padding:2.5vmin 3vmin; }
 h1 { font-size:1.35em; margin:0 0 .3em; }
 p { color:var(--faint); margin:.2em 0 .9em; }
 ul { list-style:none; margin:0; padding:0; display:grid; gap:1.4vmin;
      grid-template-columns:repeat(auto-fill,minmax(min(100%,22em),1fr)); }
 a.row { display:grid; grid-template-columns:auto 1fr; gap:0 .8em;
   align-items:center; padding:1em 1.1em; border-radius:14px;
   background:var(--card); border:1px solid var(--edge); color:var(--ink);
   text-decoration:none; }
 a.row .g { grid-row:1 / span 2; font-size:1.8em; line-height:1; }
 a.row b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
           font-weight:600; }
 a.row small { color:var(--faint); }
 a.row.wall { border-color:var(--accent); }
 .actions { display:flex; gap:1em; flex-wrap:wrap; margin:0 0 1em; }
 button { font:inherit; padding:.8em 1.2em; border-radius:12px;
   border:1px solid var(--edge); background:var(--card); color:var(--ink); }
 button.main { background:var(--accent); border-color:var(--accent);
               color:#fff; font-weight:600; }
 .stage { display:flex; align-items:center; justify-content:center;
   height:calc(100vh - 12em); min-height:12em; background:#000;
   border-radius:14px; overflow:hidden; }
 .stage img, .stage video { width:100%; height:100%; object-fit:contain; }
 .stage iframe { width:100%; height:100%; border:0; background:#fff; }
 .song { background:var(--card); flex-direction:column; gap:1em; }
 .song .g { font-size:5em; line-height:1; }
 .song audio { width:min(90%,40em); }
 pre { white-space:pre-wrap; overflow-wrap:anywhere; background:var(--card);
   padding:1em; border-radius:14px; max-height:calc(100vh - 12em);
   overflow:auto; margin:0; font-size:.85em; }
 .warn { color:var(--warn); }
"""

# Said in the language the panel's browser is set to -- the add-on's locale
# setting -- and in English otherwise.
WORDS = """
<script>
(function () {
  var fr = (navigator.language || '').toLowerCase().indexOf('fr') === 0;
  var T = fr ? {
    title: "Fichiers", intro: "Ce que les pages des \\u00e9crans ont t\\u00e9l\\u00e9charg\\u00e9. Touchez un fichier pour l'ouvrir.",
    none: "Rien pour l'instant : ce qu'une page t\\u00e9l\\u00e9charge appara\\u00eet ici.",
    wall: "Mettre en fond d'\\u00e9cran", unwall: "Retirer ce fond d'\\u00e9cran",
    unwallany: "Revenir au fond d'\\u00e9cran r\\u00e9gl\\u00e9 dans l'add-on",
    isWall: "C'est le fond d'\\u00e9cran du launcher.", del: "Supprimer",
    sure: "Supprimer ce fichier ?", open: "Ce fichier ne s'ouvre pas sur l'\\u00e9cran. R\\u00e9cup\\u00e9rez-le depuis la page Portall de Home Assistant.",
    cannot: "Le navigateur de l'add-on ne lit pas ce format (souvent une vid\\u00e9o MP4 en H.264 ou de l'AAC). Un fichier WebM, MP3 ou Opus se lit partout.",
    cut: "Seul le d\\u00e9but du fichier est affich\\u00e9."
  } : {
    title: "Files", intro: "What the screens' pages downloaded. Touch a file to open it.",
    none: "Nothing yet: what a page downloads appears here.",
    wall: "Make it the wallpaper", unwall: "Remove this wallpaper",
    unwallany: "Go back to the wallpaper set in the add-on",
    isWall: "This is the launcher's wallpaper.", del: "Delete",
    sure: "Delete this file?", open: "This file cannot be opened on the screen. Fetch it from the Portall page in Home Assistant.",
    cannot: "The add-on's browser cannot play this format (usually an H.264 MP4 or AAC). A WebM, MP3 or Opus file plays everywhere.",
    cut: "Only the start of the file is shown."
  };
  document.querySelectorAll('[data-k]').forEach(function (e) {
    e.textContent = T[e.getAttribute('data-k')] || '';
  });
  function post(what, body) {
    return fetch(what, {method: 'POST', body: body,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}});
  }
  document.querySelectorAll('button[data-do]').forEach(function (b) {
    b.addEventListener('click', function () {
      var what = b.getAttribute('data-do');
      // Asked by a second touch rather than by confirm(): a dialog on the
      // panel is answered by the browser itself, before anybody sees it.
      if (what === 'delete' && !b.dataset.sure) {
        b.dataset.sure = '1';
        b.textContent = T.sure;
        return;
      }
      b.disabled = true;
      post('""" + PATH + """/' + what, 'ref=' + encodeURIComponent(b.getAttribute('data-ref') || ''))
        .then(function () {
          if (what === 'delete') location.replace('""" + PATH + """');
          else location.reload();
        });
    });
  });
  var media = document.querySelector('video, audio');
  if (media) media.addEventListener('error', function () {
    var w = document.getElementById('cannot');
    if (w) w.textContent = T.cannot;
  });
})();
</script>
"""


def _page(body):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>Files</title><style>" + STYLE
            + "</style></head><body><main>" + body + "</main>" + WORDS
            + "</body></html>").encode()


def list_page(root, chosen_ref=None):
    rows = []
    for e in entries(root):
        rows.append(
            f'<li><a class="row{" wall" if e["ref"] == chosen_ref else ""}" '
            f'href="{PATH}/view?f={_q(e["ref"])}">'
            f'<span class="g">{GLYPH[e["kind"]]}</span>'
            f'<b>{html.escape(e["name"])}</b>'
            f'<small>{size_text(e["size"])} &middot; '
            f'{time.strftime("%d/%m %H:%M", time.localtime(e["when"]))} '
            f'&middot; {html.escape(e["screen"])}</small></a></li>')
    undo = ('<div class="actions"><button data-do="unwallpaper" '
            'data-k="unwallany"></button></div>' if chosen_ref else "")
    return _page('<h1 data-k="title"></h1><p data-k="intro"></p>' + undo
                 + ("<ul>" + "".join(rows) + "</ul>" if rows
                    else '<p data-k="none"></p>'))


def view_page(entry, chosen_ref=None):
    ref = html.escape(entry["ref"], quote=True)
    raw = f"{PATH}/raw?f={_q(entry['ref'])}"
    name = html.escape(entry["name"])
    kind = entry["kind"]
    if kind == "image":
        stage = f'<div class="stage"><img src="{raw}" alt="{name}"></div>'
    elif kind == "video":
        stage = (f'<div class="stage"><video src="{raw}" controls autoplay '
                 f'playsinline preload="auto"></video></div>')
    elif kind == "audio":
        stage = (f'<div class="stage song"><span class="g">{GLYPH["audio"]}'
                 f'</span><b>{name}</b><audio src="{raw}" controls autoplay>'
                 f'</audio></div>')
    elif kind == "pdf":
        stage = f'<div class="stage"><iframe src="{raw}"></iframe></div>'
    elif kind == "text":
        try:
            with open(entry["path"], "rb") as source:
                data = source.read(TEXT_LIMIT + 1)
        except OSError:
            data = b""
        stage = ("<pre>" + html.escape(data[:TEXT_LIMIT].decode(
            "utf-8", errors="replace")) + "</pre>"
            + ('<p data-k="cut"></p>' if len(data) > TEXT_LIMIT else ""))
    else:
        stage = '<p data-k="open"></p>'
    actions = []
    if kind in WALLPAPER_KINDS:
        if entry["ref"] == chosen_ref:
            actions.append(f'<button data-do="unwallpaper" data-ref="{ref}" '
                           f'data-k="unwall"></button>')
        else:
            actions.append(f'<button class="main" data-do="wallpaper" '
                           f'data-ref="{ref}" data-k="wall"></button>')
    actions.append(f'<button data-do="delete" data-ref="{ref}" '
                   f'data-k="del"></button>')
    return _page(f'<h1>{name}</h1><p class="warn" id="cannot"></p>'
                 + '<div class="actions">' + "".join(actions) + "</div>"
                 + stage)


def read_choice(choice_file):
    """The ref chosen as the wallpaper, or None."""
    try:
        with open(choice_file, encoding="utf-8") as handle:
            return str(json.load(handle).get("ref") or "") or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def write_choice(choice_file, ref):
    os.makedirs(os.path.dirname(choice_file) or ".", exist_ok=True)
    if ref is None:
        try:
            os.remove(choice_file)
        except OSError:
            pass
        return
    with open(choice_file, "w", encoding="utf-8") as handle:
        json.dump({"ref": ref}, handle)
