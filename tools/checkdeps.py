#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""Which of Portall's pinned third-party pieces have fallen behind.

WHY THIS EXISTS. Asked after Chrome turned out to be weeks old: "c'etait une
erreur de notre part d'avoir pas pense aux mises a jour tierces, verifie
d'autres composants". Chrome now follows Google by itself (followchrome.py),
because every desktop runs the newest one. The pins below do not: Playwright
changed how cookies match in 1.65, HAP-python changed how pairing answers in
5.2 -- each needs the checks in tools/ run against it before it moves. So this
PROPOSES and never installs: the weekly job (.github/workflows/portall-deps.yml)
keeps one open issue per package that is behind, retitles it when a newer one
comes, and closes it once the pin has caught up.

What is read:
  portall/Dockerfile                  every `name==version` pip pin
  components/portall_bt/__init__.py   CHERRYUSB_VERSION, against CherryUSB's
                                      latest GitHub release

  report            print what is behind (exit 0 either way)
  issues REPO       keep REPO's issues in step; needs $GITHUB_TOKEN
  --no-github       skip CherryUSB's release lookup (a machine with no route
                    to GitHub's API)
"""
import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TITLE = "Portall dependency: "
PIN = re.compile(r'(?<![\w.-])([A-Za-z][\w.-]*)(?:\[[^\]]*\])?==(\d[\w.]*)')
CHERRY = re.compile(r'^CHERRYUSB_VERSION = "([\d.]+)"', re.M)


def version_key(text):
    return tuple(int(part) if part.isdigit() else -1
                 for part in re.split(r"[.+-]", text.lstrip("v")))


def get(url, token=None):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "portall-checkdeps"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as answer:
        return json.load(answer)


def pins():
    """(name, pinned, where) for every pin this checks."""
    found = []
    docker = (ROOT / "portall" / "Dockerfile").read_text()
    for line in docker.splitlines():
        if line.lstrip().startswith("#") or "pip install" not in line:
            continue
        for name, version in PIN.findall(line):
            found.append((name, version, "portall/Dockerfile"))
    cherry = CHERRY.search(
        (ROOT / "components" / "portall_bt" / "__init__.py").read_text())
    if cherry:
        found.append(("CherryUSB", cherry.group(1),
                      "components/portall_bt/__init__.py"))
    return found


def latest(name, github=True, token=None):
    """(newest version, its page) or (None, why)."""
    try:
        if name == "CherryUSB":
            if not github:
                return None, "not looked up (--no-github)"
            release = get("https://api.github.com/repos/cherry-embedded/"
                          "CherryUSB/releases/latest", token)
            return release["tag_name"].lstrip("v"), release["html_url"]
        info = get(f"https://pypi.org/pypi/{name}/json")["info"]
        return info["version"], f"https://pypi.org/project/{name}/"
    except Exception as err:  # noqa: BLE001 - reported, never fatal
        return None, f"could not be read ({err})"


def behind(github=True, token=None):
    rows = []
    for name, pinned, where in pins():
        newest, page = latest(name, github, token)
        late = newest is not None and version_key(newest) > version_key(pinned)
        rows.append({"name": name, "pinned": pinned, "latest": newest,
                     "page": page, "where": where, "behind": late})
    return rows


def body(row):
    return (f"**{row['name']} {row['latest']}** is out; Portall pins "
            f"**{row['pinned']}** in `{row['where']}`.\n\n"
            f"{row['page']}\n\n"
            "Nothing was installed. A pinned package moves only after the "
            "checks in `tools/` have run against it, because a new version "
            "can change behaviour a panel depends on. Ask for it to be "
            "updated and tested; this issue closes itself once the pin has "
            "caught up.\n\n"
            "Une nouvelle version est disponible. Elle n'est pas installée "
            "automatiquement : elle doit d'abord être testée.\n\n"
            "_Opened by `.github/workflows/portall-deps.yml`._")


def issues(repo, rows, token):
    # Overridable only so the check can run against a stand-in.
    base = os.environ.get("PORTALL_GITHUB_API", "https://api.github.com")
    api = f"{base}/repos/{repo}/issues"

    def send(url, method, payload):
        request = urllib.request.Request(
            url, method=method, data=json.dumps(payload).encode(), headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "portall-checkdeps"})
        with urllib.request.urlopen(request, timeout=30) as answer:
            return json.load(answer)

    open_ones = {}
    page = 1
    while True:
        batch = get(f"{api}?state=open&per_page=100&page={page}", token)
        for issue in batch:
            if "pull_request" not in issue and \
                    issue["title"].startswith(TITLE):
                name = issue["title"][len(TITLE):].split(" ")[0]
                open_ones[name] = issue
        if len(batch) < 100:
            break
        page += 1
    for row in rows:
        title = (f"{TITLE}{row['name']} {row['latest']} is out "
                 f"({row['pinned']} in use)")
        issue = open_ones.get(row["name"])
        if row["behind"]:
            if issue is None:
                made = send(api, "POST", {"title": title, "body": body(row)})
                print(f"opened #{made['number']}: {title}")
            elif issue["title"] != title:
                send(issue["url"], "PATCH", {"title": title, "body": body(row)})
                print(f"updated #{issue['number']}: {title}")
            else:
                print(f"still open #{issue['number']}: {title}")
        elif issue is not None and row["latest"] is not None:
            send(issue["url"] + "/comments", "POST", {
                "body": f"{row['name']} is pinned at {row['pinned']}, the "
                        f"latest. Closing."})
            send(issue["url"], "PATCH", {"state": "closed",
                                         "state_reason": "completed"})
            print(f"closed #{issue['number']}: {row['name']} caught up")


def main(argv):
    github = "--no-github" not in argv
    argv = [a for a in argv if a != "--no-github"]
    token = os.environ.get("GITHUB_TOKEN")
    if not argv or argv[0] not in ("report", "issues"):
        print(__doc__)
        return 2
    rows = behind(github, token)
    for row in rows:
        state = ("BEHIND" if row["behind"] else
                 "ok    " if row["latest"] else "?     ")
        print(f"  {state} {row['name']} {row['pinned']} -> "
              f"{row['latest'] or row['page']}")
    if argv[0] == "issues":
        if len(argv) < 2 or not token:
            print("issues needs a repository and $GITHUB_TOKEN")
            return 2
        issues(argv[1], rows, token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
