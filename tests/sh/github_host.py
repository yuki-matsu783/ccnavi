"""ホストの応答の見本を返す GitHub の代役。

Chrome と手元の sh が同じ見本から同じ JSON を組むことを見るために使う。

見本は `extensions/chrome/ccnavi-approval/test/fixtures/host/github/<場面>/` にある。拡張の試験
（`test/helpers/host-fixture.ts`）も同じ見本を同じ規則で返す。規則は 2 つの代役で揃える。

- `GET /user` → `user.json`（無ければ 403）
- `GET /repos/<o>/<r>/pulls?head=<o>:<branch>` → `branch` が `scene.json` のものなら
  `pulls.json`、違えば `[]`
- `GET /repos/<o>/<r>/pulls/<番号>/reviews?page=<N>` → 番号が `pulls.json` のものなら
  `reviews.<N>.json`（無ければ `[]`。N の既定は 1）
- `POST /graphql` の `reviewThreads` → 番号が同じで、変数の cursor（sh は `c`、拡張は `after`）が
  null なら `threads.1.json`、`threads.<k>.json` の `endCursor` と同じなら `threads.<k+1>.json`
- `POST /graphql` の `viewer` → `user.json` の `login`
- GraphQL は、見本の応答が持つ欄（`THREAD_FIELDS`・`login`）が問い合わせに語として全部あるとき
  だけ答える
- 依頼の投稿（`GET`/`POST /repos/<o>/<r>/issues/<番号>/comments`）は、状態のファイル
  （環境変数 `FAKE_GITHUB_STATE`）に溜めて返す（write-and-push のハーネスで request を通すため）
- `FAKE_GITHUB_GRAPHQL` が `blocked` なら、`POST /graphql` はすべて 403
  （Claude Code のクラウドと同じ本文。`gh` は本文を返し、`curl` では番号だけが残る）。
  `forbidden` なら別の理由の 403、`unauthorized` なら 401。
  `blocked` のときだけ、REST の代わりの道（`.../pulls/<番号>/ccr/...`）が答える。
  通常の GitHub では 404（`FAKE_GITHUB_NO_CCR` を付けると、`blocked` でも 404）
- REST の見本は、環境変数 `FAKE_GITHUB_REST` が指すファイルに置く
  （`review_threads`・`comments` の配列と、`body`・`draft`）。
  返信・解決・Draft 外しの書き込みは、`FAKE_GITHUB_MUTATIONS` に
  `{"rest": "<M> <path>", "body": ...}` で残す
- ほかは 404

sh の試験は PATH の先頭に `curl` の代役を置き、このファイルを
`python github_host.py curl ...` で起こす。
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCENES = os.path.join(
    ROOT, "extensions", "chrome", "ccnavi-approval", "test", "fixtures", "host", "github"
)
API = "https://api.github.com"


# 見本の応答が持つ欄。問い合わせからどれかを外せば、実際のホストは答えにその欄を入れないので、
# 代役も答えない（欄の名前を見ずに見本を返すと、問い合わせの欄を削っても試験が通ってしまう）。
# 拡張の代役と同じ
THREAD_FIELDS = (
    "reviewThreads",
    "pageInfo",
    "hasNextPage",
    "endCursor",
    "nodes",
    "id",
    "isResolved",
    "comments",
    "url",
    "path",
    "line",
    "body",
    "createdAt",
)


def _missing_fields(query: str, fields) -> list[str]:
    """問い合わせに語として現れない欄の名前。"""
    return [f for f in fields if not re.search(rf"(?<![A-Za-z0-9_]){f}(?![A-Za-z0-9_])", query)]


def scene_names() -> list[str]:
    """レビューの場面。"""
    return sorted(n for n in os.listdir(SCENES) if os.path.isdir(os.path.join(SCENES, n)))


def _load(scene: str, name: str):
    path = os.path.join(SCENES, scene, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _comments(state: str) -> list[dict]:
    if not state or not os.path.exists(state):
        return []
    with open(state, encoding="utf-8") as f:
        return json.load(f)


def _mutation(q: str, variables: dict) -> tuple[int, object]:
    """受け入れの返信と解決（GraphQL の mutation）。

    受けた問い合わせと変数を `FAKE_GITHUB_MUTATIONS` に 1 行ずつ JSON で残す（形の試験が読む）。
    問い合わせが使う変数（`$id`・`$b`）を渡していなければ、実際のホストと同じく errors で答える。
    `FAKE_GITHUB_FAIL_MUTATION` が `reply` か `resolve` なら、その mutation を errors で断る。
    """
    log = os.environ.get("FAKE_GITHUB_MUTATIONS", "")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"query": q, "variables": variables}, ensure_ascii=False) + "\n")
    used = set(re.findall(r"\$([A-Za-z_]+)", q.split("{", 1)[0]))
    missing = sorted(v for v in used if v not in variables)
    if missing:
        return 200, {"errors": [{"message": f"変数が無い: {', '.join(missing)}"}]}
    fail = os.environ.get("FAKE_GITHUB_FAIL_MUTATION", "")
    if "addPullRequestReviewThreadReply" in q:
        if fail == "reply":
            return 200, {"errors": [{"message": "Could not resolve to a node"}]}
        return 200, {"data": {"addPullRequestReviewThreadReply": {"comment": {"id": "PRRC_1"}}}}
    if "markPullRequestReadyForReview" in q:
        _set_draft(False)
        return 200, {
            "data": {
                "markPullRequestReadyForReview": {"pullRequest": {"number": 42, "isDraft": False}}
            }
        }
    if "resolveReviewThread" in q:
        if fail == "resolve":
            return 200, {"errors": [{"message": "Could not resolve to a node"}]}
        return 200, {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}
    return 200, {"errors": [{"message": "知らない mutation"}]}


def _rest() -> dict:
    path = os.environ.get("FAKE_GITHUB_REST", "")
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _draft() -> bool:
    path = os.environ.get("FAKE_GITHUB_DRAFT_STATE", "")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return bool(_rest().get("draft", True))


def _set_draft(value: bool) -> None:
    path = os.environ.get("FAKE_GITHUB_DRAFT_STATE", "")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)


def _note(entry: dict) -> None:
    log = os.environ.get("FAKE_GITHUB_MUTATIONS", "")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _graphql_refusal() -> tuple[int, object] | None:
    """`FAKE_GITHUB_GRAPHQL` が GraphQL を断るときの答え。"""
    mode = os.environ.get("FAKE_GITHUB_GRAPHQL", "")
    if mode == "blocked":
        message = "GitHub GraphQL is not available from Claude Code sessions"
        return 403, {"message": message}
    if mode == "forbidden":
        return 403, {"message": "Resource not accessible by personal access token"}
    if mode == "unauthorized":
        return 401, {"message": "Bad credentials"}
    return None


def _rest_answer(method: str, pieces: list[str], query: dict, body: str, number: str):
    """REST の代わりの道の答え。コメントの一覧と返信も含む。pieces は `pulls/<番号>/...`。"""
    if pieces[:1] != ["pulls"] or len(pieces) < 2 or pieces[1] != number:
        return None
    rest = pieces[2:]
    rest_data = _rest()
    served = os.environ.get("FAKE_GITHUB_GRAPHQL", "") == "blocked"
    served = served and not os.environ.get("FAKE_GITHUB_NO_CCR")
    if method == "GET" and not rest:
        return 200, {
            "number": int(number),
            "node_id": "PR_kwDOAbCdEs5ABCD",
            "title": "Draft: 機能を足す",
            "body": rest_data.get("body", ""),
            "draft": _draft(),
        }
    if method == "GET" and rest == ["comments"]:
        return 200, rest_data.get("comments", []) if query.get("page", "1") == "1" else []
    if method == "POST" and len(rest) == 3 and rest[0] == "comments" and rest[2] == "replies":
        _note({"rest": f"POST {'/'.join(pieces)}", "body": json.loads(body or "{}")})
        return 201, {"id": 9001, "in_reply_to_id": int(rest[1])}
    if not served or rest[:1] != ["ccr"]:
        return None
    if method == "GET" and rest == ["ccr", "review_threads"]:
        return 200, rest_data.get("review_threads", [])
    if method == "POST" and len(rest) == 4 and rest[1] == "comments" and rest[3] == "resolve":
        _note({"rest": f"POST {'/'.join(pieces)}", "body": json.loads(body or "{}")})
        return 200, {}
    if method == "POST" and rest == ["ccr", "ready_for_review"]:
        _note({"rest": f"POST {'/'.join(pieces)}", "body": json.loads(body or "{}")})
        _set_draft(False)
        return 200, {}
    return None


def answer(scene: str, method: str, url: str, body: str, state: str = "") -> tuple[int, object]:
    """1 つの要求への答え（状態, JSON）。"""
    meta = _load(scene, "scene.json") or {}
    owner, repo = meta.get("owner", ""), meta.get("repo", "")
    parts = urlsplit(url)
    path = parts.path
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    base = f"/repos/{owner}/{repo}"
    if method == "GET" and path == "/user":
        found = _load(scene, "user.json")
        # 持ち主の無いトークン（GitHub Actions の GITHUB_TOKEN など）は 403
        return (200, found) if found is not None else (403, {"message": "Resource not accessible"})
    if method == "GET" and path == f"{base}/pulls":
        if query.get("head") == f"{owner}:{meta.get('branch')}":
            return 200, _load(scene, "pulls.json")
        return 200, []
    pieces = path[len(base) + 1 :].split("/") if path.startswith(base + "/") else []
    number = str(((_load(scene, "pulls.json") or [{}])[0] or {}).get("number", ""))
    found = _rest_answer(method, pieces, query, body, number)
    if found is not None:
        return found
    if method == "PATCH" and pieces[:1] == ["pulls"]:
        return 200, {"number": int(number)}
    if method == "GET" and pieces[:1] == ["pulls"] and pieces[2:] == ["reviews"]:
        if pieces[1] != number:
            return 200, []
        page = query.get("page", "1")
        found = _load(scene, f"reviews.{page}.json") if page.isdigit() else None
        return 200, found if found is not None else []
    if len(pieces) == 3 and pieces[0] == "issues" and pieces[2] == "comments":
        posted = _comments(state)
        if method == "GET":
            return 200, posted if query.get("page", "1") == "1" else []
        if method == "POST":
            text = json.loads(body or "{}").get("body", "")
            n = len(posted) + 1
            note = {
                "id": n,
                "body": text,
                "html_url": f"https://github.com/{owner}/{repo}/pull/{pieces[1]}#issuecomment-{n}",
                "created_at": "2026-09-29T00:00:00Z",
            }
            with open(state, "w", encoding="utf-8") as f:
                json.dump([*posted, note], f)
            return 201, note
    if method == "POST" and path == "/graphql":
        refused = _graphql_refusal()
        if refused is not None:
            return refused
        req = json.loads(body or "{}")
        q, variables = req.get("query", ""), req.get("variables") or {}
        if q.lstrip().startswith("mutation"):
            return _mutation(q, variables)
        if "reviewThreads" in q and str(variables.get("n", variables.get("number"))) == number:
            missing = _missing_fields(q, THREAD_FIELDS)
            if missing:
                return 200, {"errors": [{"message": f"問い合わせに欄が無い: {', '.join(missing)}"}]}
            cursor = variables.get("c", variables.get("after"))
            k = 1
            while True:
                page = _load(scene, f"threads.{k}.json")
                if page is None:
                    return 200, {"errors": [{"message": f"見本に cursor {cursor} のページが無い"}]}
                if cursor is None:
                    return 200, page
                info = page["data"]["repository"]["pullRequest"]["reviewThreads"]["pageInfo"]
                if info["endCursor"] == cursor:
                    cursor = None
                k += 1
        if "viewer" in q and not _missing_fields(q, ("login",)):
            return 200, {"data": {"viewer": {"login": (_load(scene, "user.json") or {})["login"]}}}
    return 404, {"message": "Not Found"}


def curl(argv: list[str]) -> int:
    """curl の代役。`ccnavi-review.sh` が打つ形（`-fsS -X <M> -H ... [--data-binary @-] <URL>`）"""
    method, url, body = "GET", "", ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method = argv[i + 1]
            i += 2
            continue
        if a in ("-H", "--header"):
            i += 2
            continue
        if a == "--data-binary":
            body = sys.stdin.read()
            i += 2
            continue
        if a.startswith("http"):
            url = a
        i += 1
    if not url.startswith(API):
        sys.stderr.write(f"curl: 見本の外の URL: {url}\n")
        return 6
    if os.environ.get("FAKE_GITHUB_NO_USER") and url[len(API) :] == "/user":
        sys.stderr.write("curl: (22) The requested URL returned error: 403\n")
        return 22
    status, data = answer(
        os.environ["FAKE_GITHUB_SCENE"],
        method,
        url[len(API) :],
        body,
        os.environ.get("FAKE_GITHUB_STATE", ""),
    )
    with open(os.environ.get("FAKE_GITHUB_LOG", os.devnull), "a", encoding="utf-8") as log:
        log.write(f"{method} {url[len(API) :]}\n")
    if status >= 400:
        sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
        return 22
    sys.stdout.write(json.dumps(data, ensure_ascii=False))
    return 0


def gh(argv: list[str]) -> int:
    """gh の代役（`gh api --hostname <h> [--method <M>] [--input -] <rel>`）。

    見本を返し、標準エラーに雑音を出す。

    標準エラーの雑音は、`ccnavi-review.sh` が gh の応答に標準エラーを混ぜないことを見るため。
    `FAKE_GH_SLOW_USER` に秒を入れると、`user` の答えをその秒だけ遅らせる（時間の上限の試験）。
    """
    if argv[:1] != ["api"]:
        return 1
    method, rel, body, i = "GET", "", "", 1
    while i < len(argv):
        a = argv[i]
        if a in ("--hostname", "--method", "--input"):
            if a == "--method":
                method = argv[i + 1]
            if a == "--input":
                body = sys.stdin.read()
            i += 2
            continue
        rel = a
        i += 1
    sys.stderr.write("gh: 更新があります（雑音）\n")
    path = "/" + rel if not rel.startswith("/") else rel
    if path == "/user" and os.environ.get("FAKE_GH_SLOW_USER"):
        import time

        time.sleep(float(os.environ["FAKE_GH_SLOW_USER"]))
    meta = _load(os.environ["FAKE_GITHUB_SCENE"], "scene.json") or {}
    if path == f"/repos/{meta.get('owner')}/{meta.get('repo')}":
        sys.stdout.write("{}")
        return 0
    status, data = answer(
        os.environ["FAKE_GITHUB_SCENE"], method, path, body, os.environ.get("FAKE_GITHUB_STATE", "")
    )
    with open(os.environ.get("FAKE_GITHUB_LOG", os.devnull), "a", encoding="utf-8") as log:
        log.write(f"{method} {path}\n")
    sys.stdout.write(json.dumps(data, ensure_ascii=False))
    if status >= 400:
        # 本物の gh は 4xx でも本文を標準出力へ書き、標準エラーには「gh: <message> (HTTP N)」を出す
        message = data.get("message", "") if isinstance(data, dict) else ""
        sys.stderr.write(f"gh: {message} (HTTP {status})\n")
    return 0 if status < 400 else 1


def install(bin_dir: str, python: str) -> None:
    """PATH の先頭に置く代役（`curl` と、使えない `gh`）を bin_dir に書く。"""
    os.makedirs(bin_dir, exist_ok=True)
    for name, text in (
        ("curl", f"#!/bin/sh\nexec '{python}' '{os.path.abspath(__file__)}' curl \"$@\"\n"),
        # gh があっても使わせない（認証の無い gh は疎通の試しで失敗して curl に切り替わる）
        ("gh", "#!/bin/sh\nexit 1\n"),
    ):
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(path, 0o755)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "curl":
        sys.exit(curl(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "gh":
        sys.exit(gh(sys.argv[2:]))
    sys.stderr.write("使い方: github_host.py curl|gh <引数>...\n")
    sys.exit(2)
