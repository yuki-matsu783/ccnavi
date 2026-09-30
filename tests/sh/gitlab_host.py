"""ホストの応答の見本を返す GitLab の代役（ADR-0093 の 8.9。段階 5）。

見本は `chrome-extension/ccnavi-approval/test/fixtures/host/gitlab/<場面>/` にある。拡張の試験
（`test/helpers/gitlab-fixture.ts`）も同じ見本を同じ規則で返す。規則は 2 つの代役で揃える。

- `GET /api/v4/user` → `user.json`（無ければ 403）
- `GET /api/v4/projects/<namespace>%2F<project>/merge_requests?state=opened&source_branch=<b>`
  → `b` が `scene.json` のものなら `mrs.json`、違えば `[]`
- `GET .../merge_requests/<iid>/discussions?page=<N>` → iid が `mrs.json` のものなら
  `discussions.<N>.json`（無ければ `[]`。N の既定は 1）
- `GET .../merge_requests/<iid>/reviewers?page=<N>` → 同じく `reviewers.<N>.json`
- ほかは 404

sh の試験は PATH の先頭に `curl` の代役を置き、このファイルを
`python gitlab_host.py curl ...` で起こす。
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCENES = os.path.join(
    ROOT, "chrome-extension", "ccnavi-approval", "test", "fixtures", "host", "gitlab"
)
API = "https://gitlab.com/api/v4"


def scene_names() -> list[str]:
    return sorted(n for n in os.listdir(SCENES) if os.path.isdir(os.path.join(SCENES, n)))


def _load(scene: str, name: str):
    path = os.path.join(SCENES, scene, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def answer(scene: str, method: str, url: str) -> tuple[int, object]:
    """1 つの要求への答え（状態, JSON）。`url` は API の根からのパスとクエリ（%2F のまま）"""
    meta = _load(scene, "scene.json") or {}
    parts = urlsplit(url)
    path = parts.path
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    if method == "GET" and path == "/user":
        found = _load(scene, "user.json")
        return (200, found) if found is not None else (403, {"message": "403 Forbidden"})
    base = f"/projects/{meta.get('namespace', '')}%2F{meta.get('project', '')}"
    if method == "GET" and path == base:
        return 200, {"id": 42, "default_branch": "main"}
    if method == "GET" and path == f"{base}/merge_requests":
        if query.get("state") == "opened" and query.get("source_branch") == meta.get("branch"):
            return 200, _load(scene, "mrs.json")
        return 200, []
    pieces = path[len(base) + 1 :].split("/") if path.startswith(base + "/") else []
    iid = str(((_load(scene, "mrs.json") or [{}])[0] or {}).get("iid", ""))
    if (
        method == "GET"
        and len(pieces) == 3
        and pieces[0] == "merge_requests"
        and pieces[2] in ("discussions", "reviewers")
    ):
        if pieces[1] != iid:
            return 200, []
        page = query.get("page", "1")
        found = _load(scene, f"{pieces[2]}.{page}.json") if page.isdigit() else None
        return 200, found if found is not None else []
    return 404, {"message": "404 Not Found"}


def curl(argv: list[str]) -> int:
    """curl の代役。`ccnavi-review.sh` が打つ形（`-fsS -X <M> -H ... [--data-binary @-] <URL>`）"""
    method, url = "GET", ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method = argv[i + 1]
            i += 2
            continue
        if a in ("-H", "--header", "--connect-timeout", "--max-time"):
            i += 2
            continue
        if a == "--data-binary":
            sys.stdin.read()
            i += 2
            continue
        if a.startswith("http"):
            url = a
        i += 1
    if not url.startswith(API):
        sys.stderr.write(f"curl: 見本の外の URL: {url}\n")
        return 6
    if os.environ.get("FAKE_GITLAB_NO_USER") and url[len(API) :] == "/user":
        sys.stderr.write("curl: (22) The requested URL returned error: 403\n")
        return 22
    status, data = answer(os.environ["FAKE_GITLAB_SCENE"], method, url[len(API) :])
    with open(os.environ.get("FAKE_GITLAB_LOG", os.devnull), "a", encoding="utf-8") as log:
        log.write(f"{method} {url[len(API) :]}\n")
    if status >= 400:
        sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
        return 22
    sys.stdout.write(json.dumps(data, ensure_ascii=False))
    return 0


def install(bin_dir: str, python: str) -> None:
    """PATH の先頭に置く代役（`curl` と、使えない `glab`）を bin_dir に書く。"""
    os.makedirs(bin_dir, exist_ok=True)
    for name, text in (
        ("curl", f"#!/bin/sh\nexec '{python}' '{os.path.abspath(__file__)}' curl \"$@\"\n"),
        # glab があっても使わせない（認証の無い glab は疎通の試しで落ちて curl に切り替わる）
        ("glab", "#!/bin/sh\nexit 1\n"),
    ):
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(path, 0o755)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "curl":
        sys.exit(curl(sys.argv[2:]))
    sys.stderr.write("使い方: gitlab_host.py curl <引数>...\n")
    sys.exit(2)
