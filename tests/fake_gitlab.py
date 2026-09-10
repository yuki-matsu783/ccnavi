"""GitLab API v4 の代役。`.claude/scripts/ccnavi-review.sh` を実物なしで通すための的。

自動テストからは呼んでいない。人が手で 1 周させるための道具として置いてある。

## なぜ要るか

本物の GitLab CE は 4GB 要る。手元の機械に無いことが普通にあり、無いままだと
sh の HTTP 経路（curl / gh / glab、jq での組み立て、ページング、投稿、認証）が
1 行も走らない。そこがいちばん実測できていない場所なので、GitLab と同じ形の JSON を
返すサーバを立てて、sh の側だけを実際に走らせる。

**これで分かるのは sh が動くことだけ。** 返す形が本物と同じかどうかは分からない。
本物で確かめる代わりにはならない。

## 使い方

    python tests/fake_gitlab.py <state.json> <port>

状態は JSON ファイル 1 つ。最初は次の形で置く。

    {"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}

対象のプロジェクトは、origin を `http://127.0.0.1:<port>/demo/greeter.git` にして、
push だけ手元の bare リポジトリへ向ける（`git remote set-url --push`）。
`GITLAB_TOKEN` に下の TOKEN を置くと、sh は curl 経路でここを叩く。

人間役（issue を立てる、スレッドを立てる、解決する、変更要求を出す、マージする）は
curl で直に叩く。sh と同じ道具を使わないほうが、片方の壊れがもう片方に隠れない。

## 時計をずらしてある

本物のホストと手元の時計は揃わない。揃っている前提の実装がすぐ壊れるので、
既定で 3 分進めてある（SKEW）。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "fake-gitlab-token-0001"
LOCK = threading.Lock()
STATE_PATH = ""
SKEW = timedelta(minutes=3)


def now() -> str:
    return (datetime.now(UTC) + SKEW).isoformat().replace("+00:00", "Z")


def load() -> dict:
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def next_id(state: dict) -> int:
    state["seq"] = int(state.get("seq", 100)) + 1
    return state["seq"]


def mr_of(state: dict, iid: int) -> dict | None:
    for mr in state.get("mrs", []):
        if int(mr["iid"]) == iid:
            return mr
    return None


def page(items: list, query: dict) -> list:
    per = int(query.get("per_page", ["20"])[0])
    num = int(query.get("page", ["1"])[0])
    start = (num - 1) * per
    return items[start : start + per]


_PROJ = r"^/api/v4/projects/(?P<proj>[^/]+)"
_MR = _PROJ + r"/merge_requests/(?P<iid>\d+)"

ROUTES: list[tuple[str, re.Pattern]] = [
    ("project", re.compile(_PROJ + r"$")),
    ("mrs", re.compile(_PROJ + r"/merge_requests$")),
    ("mr", re.compile(_MR + r"$")),
    ("discussions", re.compile(_MR + r"/discussions$")),
    ("discussion", re.compile(_MR + r"/discussions/(?P<did>[^/]+)$")),
    ("reviewers", re.compile(_MR + r"/reviewers$")),
    ("reviewer", re.compile(_MR + r"/reviewers/(?P<uid>\d+)$")),
    ("notes", re.compile(_MR + r"/notes$")),
    ("merge", re.compile(_MR + r"/merge$")),
    ("issues", re.compile(_PROJ + r"/issues$")),
    ("issue", re.compile(_PROJ + r"/issues/(?P<iid>\d+)$")),
    ("version", re.compile(r"^/api/v4/version$")),
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # BaseHTTPRequestHandler.handle() と名前がぶつかるので、処理の本体は act に置く。
    # ぶつけると「本文が届かない」形で静かに壊れる（実測で 30 分溶かした）。

    def log_message(self, fmt, *args):
        sys.stderr.write(f"fake-gitlab {self.command} {self.path}\n")

    def send_json(self, code: int, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        if self._body is not None:
            return self._body
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            self._body = {}
            return self._body
        raw = self.rfile.read(length)
        try:
            self._body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            # UTF-8 でない本文はここで落ちる。Windows のコンソール経由で日本語を
            # 引数に渡すと CP932 になり、静かに空の本文になる。名指しで言う。
            sys.stderr.write(f"fake-gitlab: 本文を読めない ({exc}) raw={raw[:120]!r}\n")
            self._body = {}
        return self._body

    def authed(self) -> bool:
        if self.headers.get("PRIVATE-TOKEN") == TOKEN:
            return True
        return (self.headers.get("Authorization") or "") == f"Bearer {TOKEN}"

    def route(self):
        parsed = urllib.parse.urlparse(self.path)
        for name, pattern in ROUTES:
            m = pattern.match(parsed.path)
            if m is not None:
                return name, m.groupdict(), urllib.parse.parse_qs(parsed.query)
        return "", {}, {}

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def dispatch(self, method: str):
        self._body = None
        name, args, query = self.route()
        if name == "version":
            return self.send_json(200, {"version": "18.5.4-fake", "revision": "fake"})
        if not self.authed():
            return self.send_json(401, {"message": "401 Unauthorized"})
        if not name:
            return self.send_json(404, {"message": "404 Not Found"})
        with LOCK:
            state = load()
            proj = urllib.parse.unquote(args.get("proj", ""))
            if proj != state.get("project"):
                return self.send_json(404, {"message": f"404 Project Not Found: {proj}"})
            try:
                code, payload = self.act(method, name, args, query, state)
            except KeyError as exc:
                return self.send_json(404, {"message": f"404 Not Found {exc}"})
            save(state)
        return self.send_json(code, payload)

    def act(self, method, name, args, query, state):
        iid = int(args["iid"]) if "iid" in args else 0

        if name == "project":
            return 200, {"id": 1, "path_with_namespace": state["project"]}

        if name == "mrs" and method == "GET":
            found = [
                self.mr_view(mr)
                for mr in state["mrs"]
                if self.wanted(mr, "state", query) and self.wanted(mr, "source_branch", query)
            ]
            return 200, page(found, query)

        if name == "mrs" and method == "POST":
            data = self.body()
            mr = {
                "iid": len(state["mrs"]) + 1,
                "state": "opened",
                "source_branch": data.get("source_branch", ""),
                "target_branch": data.get("target_branch", "main"),
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "discussions": [],
                "reviewers": [],
                "notes": [],
                "created_at": now(),
            }
            host = self.headers.get("Host")
            mr["web_url"] = f"http://{host}/{state['project']}/-/merge_requests/{mr['iid']}"
            state["mrs"].append(mr)
            return 201, self.mr_view(mr)

        if name == "mr" and method == "GET":
            return 200, self.mr_view(mr_of(state, iid) or {})

        if name == "mr" and method == "PUT":
            # 題の書き換え（Draft を外す）だけを受ける。
            mr = mr_of(state, iid)
            data = self.body()
            if "title" in data:
                mr["title"] = data["title"]
            if "squash" in data:
                mr["squash"] = bool(data["squash"])
            return 200, self.mr_view(mr)

        if name == "discussions" and method == "GET":
            return 200, page(mr_of(state, iid)["discussions"], query)

        if name == "discussions" and method == "POST":
            mr = mr_of(state, iid)
            data = self.body()
            note = {
                "id": next_id(state),
                "body": data.get("body", ""),
                # MR 上のスレッドは、差分の位置が無くても解決できる。
                "resolvable": True,
                "resolved": False,
                "created_at": now(),
                "author": {"id": 2, "username": data.get("author", "reviewer")},
            }
            if data.get("path"):
                note["position"] = {"new_path": data.get("path"), "new_line": data.get("line", 1)}
            discussion = {"id": f"d{note['id']}", "individual_note": False, "notes": [note]}
            mr["discussions"].append(discussion)
            return 201, discussion

        if name == "discussion" and method == "PUT":
            mr = mr_of(state, iid)
            resolved = query.get("resolved", ["true"])[0] == "true"
            for d in mr["discussions"]:
                if d["id"] == args["did"]:
                    for n in d["notes"]:
                        if n.get("resolvable"):
                            n["resolved"] = resolved
                    return 200, d
            raise KeyError(args["did"])

        if name == "reviewers" and method == "GET":
            return 200, page(mr_of(state, iid)["reviewers"], query)

        if name == "reviewer" and method == "PUT":
            mr = mr_of(state, iid)
            uid = int(args["uid"])
            wanted = query.get("state", ["reviewed"])[0]
            for r in mr["reviewers"]:
                if r["user"]["id"] == uid:
                    r["state"] = wanted
                    r["updated_at"] = now()
                    return 200, r
            entry = {
                "user": {"id": uid, "username": f"human{uid}"},
                "state": wanted,
                "created_at": now(),
                "updated_at": now(),
            }
            mr["reviewers"].append(entry)
            return 201, entry

        if name == "notes" and method == "POST":
            mr = mr_of(state, iid)
            note = {"id": next_id(state), "body": self.body().get("body", ""), "created_at": now()}
            mr["notes"].append(note)
            return 201, note

        if name == "notes" and method == "GET":
            return 200, page(mr_of(state, iid)["notes"], query)

        if name == "merge" and method == "PUT":
            mr = mr_of(state, iid)
            mr["state"] = "merged"
            mr["merged_at"] = now()
            return 200, self.mr_view(mr)

        if name == "issues" and method == "GET":
            return 200, page(state.setdefault("issues", []), query)

        if name == "issues" and method == "POST":
            data = self.body()
            issue = {
                "iid": len(state.setdefault("issues", [])) + 1,
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "state": "opened",
                "created_at": now(),
            }
            host = self.headers.get("Host")
            issue["web_url"] = f"http://{host}/{state['project']}/-/issues/{issue['iid']}"
            state["issues"].append(issue)
            return 201, issue

        if name == "issue" and method == "PUT":
            for issue in state.setdefault("issues", []):
                if int(issue["iid"]) == iid:
                    if self.body().get("state_event") == "close":
                        issue["state"] = "closed"
                        issue["closed_at"] = now()
                    return 200, issue
            raise KeyError(iid)

        return 405, {"message": f"405 {method} {name}"}

    def wanted(self, mr: dict, key: str, query: dict) -> bool:
        if key not in query:
            return True
        return query[key][0] in ("all", mr.get(key))

    def mr_view(self, mr: dict) -> dict:
        keep = (
            "iid",
            "state",
            "source_branch",
            "target_branch",
            "title",
            "web_url",
            "created_at",
            "merged_at",
        )
        return {k: mr[k] for k in keep if k in mr}


def main() -> int:
    global STATE_PATH
    STATE_PATH, port = sys.argv[1], int(sys.argv[2])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"fake-gitlab listening on 127.0.0.1:{port} state={STATE_PATH}\n")
    sys.stderr.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
