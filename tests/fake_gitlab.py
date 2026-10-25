"""GitLab API v4 の代役の本体。手で使う入口は `tests/manual/gitlab/fake_gitlab.py`。

説明（何のためか、状態ファイルの形、時計のずらし、関数としての使い方）は入口の docstring にある。
自動テストは、ここの `start` を import して起こす。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ccnavi.records import diaglog

TOKEN = "fake-gitlab-token-0001"
DEFAULT_SKEW = timedelta(minutes=3)
READY_LIMIT = 5.0
LOG_NAME = "fake-gitlab"


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
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
_ISSUE = _PROJ + r"/issues/(?P<iid>\d+)"

# 本文の `Closes #N`（Closes/Fixes/Resolves の活用形。`:` は任意）。実物の閉じる語に合わせる
CLOSING = re.compile(
    r"\b(?:clos(?:e[sd]?|ing)|fix(?:e[sd]|ing)?|resolv(?:e[sd]?|ing))\b:?\s+#(\d+)",
    re.IGNORECASE,
)

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
    ("issue", re.compile(_ISSUE + r"$")),
    ("issue_mrs", re.compile(_ISSUE + r"/related_merge_requests$")),
    ("issue_notes", re.compile(_ISSUE + r"/notes$")),
    ("closes_issues", re.compile(_MR + r"/closes_issues$")),
    ("discussion_notes", re.compile(_MR + r"/discussions/(?P<did>[^/]+)/notes$")),
    ("member", re.compile(_PROJ + r"/members/all/(?P<uid>\d+)$")),
    ("user", re.compile(r"^/api/v4/user$")),
    ("version", re.compile(r"^/api/v4/version$")),
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # BaseHTTPRequestHandler.handle() と名前がぶつかるので、処理の本体は act に置く。
    # 重なると、気づかないうちに本文が届かなくなる（原因を探すのに 30 分かかった）。

    def log_message(self, fmt, *args):
        self.server.record(f"{self.command} {self.path}")

    def now(self) -> str:
        return self.server.now()

    def setup(self):
        super().setup()
        self.server.track(self.connection)

    def finish(self):
        try:
            super().finish()
        finally:
            self.server.untrack(self.connection)

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
            # UTF-8 でない本文はここで失敗する。Windows のコンソール経由で日本語を
            # 引数に渡すと CP932 になり、気づかないうちに本文が空になる。名指しで言う。
            sys.stderr.write(f"fake-gitlab: 本文を読めない ({exc}) raw={raw[:120]!r}\n")
            self._body = {}
        return self._body

    def authed(self) -> bool:
        token = self.server.token
        if self.headers.get("PRIVATE-TOKEN") == token:
            return True
        return (self.headers.get("Authorization") or "") == f"Bearer {token}"

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
        # 本文は先に読み切る。401 や 404 で返すときも読まないと、keep-alive の同じ接続で
        # 次の要求の頭に本文が残り、400 になる
        self.body()
        name, args, query = self.route()
        if name == "version":
            return self.send_json(200, {"version": "18.5.4-fake", "revision": "fake"})
        if not self.authed():
            return self.send_json(401, {"message": "401 Unauthorized"})
        if not name:
            return self.send_json(404, {"message": "404 Not Found"})
        with self.server.lock:
            state = load(self.server.state_path)
            proj = urllib.parse.unquote(args.get("proj", ""))
            # /user はプロジェクトに属さない
            if name != "user" and proj != state.get("project"):
                return self.send_json(404, {"message": f"404 Project Not Found: {proj}"})
            try:
                code, payload = self.act(method, name, args, query, state)
            except KeyError as exc:
                return self.send_json(404, {"message": f"404 Not Found {exc}"})
            save(self.server.state_path, state)
        return self.send_json(code, payload)

    def act(self, method, name, args, query, state):
        iid = int(args["iid"]) if "iid" in args else 0
        # 無い MR は 404（実物と同じ）。issue の iid とは別の番号なので、issue_* の経路は除く。
        # issue_* でない経路の iid は、すべて MR のもの
        if "iid" in args and not name.startswith("issue") and mr_of(state, iid) is None:
            raise KeyError(iid)

        if name == "user":
            return 200, {"id": 1, "username": "fake-user", "name": "Fake User", "state": "active"}

        if name == "member":
            return 200, {
                "id": int(args["uid"]),
                "username": "fake-user",
                "state": "active",
                "access_level": 40,
            }

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
                "created_at": self.now(),
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
                # マージリクエスト上のスレッドは、差分の位置が無くても解決できる。
                "resolvable": True,
                "resolved": False,
                "created_at": self.now(),
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
                    r["updated_at"] = self.now()
                    return 200, r
            entry = {
                "user": {"id": uid, "username": f"human{uid}"},
                "state": wanted,
                "created_at": self.now(),
                "updated_at": self.now(),
            }
            mr["reviewers"].append(entry)
            return 201, entry

        if name == "notes" and method == "POST":
            mr = mr_of(state, iid)
            note = {
                "id": next_id(state),
                "body": self.body().get("body", ""),
                "created_at": self.now(),
            }
            mr["notes"].append(note)
            return 201, note

        if name == "notes" and method == "GET":
            return 200, page(mr_of(state, iid)["notes"], query)

        if name == "merge" and method == "PUT":
            mr = mr_of(state, iid)
            mr["state"] = "merged"
            mr["merged_at"] = self.now()
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
                "created_at": self.now(),
            }
            host = self.headers.get("Host")
            issue["web_url"] = f"http://{host}/{state['project']}/-/issues/{issue['iid']}"
            state["issues"].append(issue)
            return 201, issue

        if name == "closes_issues" and method == "GET":
            wanted = {int(n) for n in CLOSING.findall(mr_of(state, iid).get("description") or "")}
            found = []
            for issue in state.setdefault("issues", []):
                if int(issue["iid"]) in wanted:
                    short = f"#{issue['iid']}"
                    refs = {"short": short, "relative": short, "full": state["project"] + short}
                    found.append({**issue, "references": refs})
            return 200, page(found, query)

        if name.startswith("issue") and method in ("GET", "PUT", "POST"):
            issue = next((i for i in state.setdefault("issues", []) if int(i["iid"]) == iid), None)
            if issue is None:
                raise KeyError(iid)
            if name == "issue" and method == "GET":
                return 200, issue
            if name == "issue_mrs" and method == "GET":
                # 本文で `#N` と参照した MR（実物の related_merge_requests は参照した MR を含む）
                ref = re.compile(rf"#{iid}(?!\d)")
                found = [
                    self.mr_view(mr)
                    for mr in state["mrs"]
                    if ref.search(mr.get("description") or "")
                ]
                return 200, page(found, query)
            if name == "issue_notes" and method == "GET":
                return 200, page(issue.setdefault("notes", []), query)
            if name == "issue_notes" and method == "POST":
                note = {
                    "id": next_id(state),
                    "body": self.body().get("body", ""),
                    "created_at": self.now(),
                }
                issue.setdefault("notes", []).append(note)
                return 201, note

        if name == "discussion_notes" and method == "POST":
            mr = mr_of(state, iid)
            for d in mr["discussions"]:
                if d["id"] == args["did"]:
                    note = {
                        "id": next_id(state),
                        "body": self.body().get("body", ""),
                        "created_at": self.now(),
                        "author": {"id": 2, "username": "reviewer"},
                    }
                    d["notes"].append(note)
                    return 201, note
            raise KeyError(args["did"])

        if name == "issue" and method == "PUT":
            for issue in state.setdefault("issues", []):
                if int(issue["iid"]) == iid:
                    if self.body().get("state_event") == "close":
                        issue["state"] = "closed"
                        issue["closed_at"] = self.now()
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
        view = {k: mr[k] for k in keep if k in mr}
        # 実物の MR も持つ項目。sh（ccnavi-common-review.sh の find_mr など）が、jq でこれを見て
        # 「このプロジェクトから出た MR か」を絞り込む。
        # 代役のプロジェクトの id は 1（`project` の答え）
        view["source_project_id"] = mr.get("source_project_id", 1)
        return view


class Server(ThreadingHTTPServer):
    """起動した代役。`start` が返す。状態はすべてこのインスタンスが持つ。"""

    # keep-alive の接続を開いたままの相手がいても、stop が待たされないようにする。
    # 既定（daemon_threads が False、block_on_close が True）だと、server_close が各接続の
    # スレッドの終わりを join する。相手が接続を切らないかぎり、スレッドは次の要求を待ち続けるので、
    # join が返らない。daemon_threads が True なら join の対象にならない。
    # block_on_close は、その前提に頼らず明示しておく。
    daemon_threads = True
    block_on_close = False

    def __init__(self, state_path: str, skew: timedelta, token: str, port: int = 0, echo=False):
        self.state_path = state_path
        self.skew = skew
        self.token = token
        self.lock = threading.Lock()
        self.requests: list[str] = []
        self.echo = echo
        self._conns: set[socket.socket] = set()
        self._conn_lock = threading.Lock()
        self._stopped = False
        self._thread: threading.Thread | None = None
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def now(self) -> str:
        return (datetime.now(UTC) + self.skew).isoformat().replace("+00:00", "Z")

    def record(self, line: str) -> None:
        """アクセスのログ（`METHOD パス`）。`requests` に溜め、`echo` なら標準エラーにも出す。"""
        self.requests.append(line)
        if self.echo:
            sys.stderr.write(f"fake-gitlab {line}\n")

    def track(self, conn: socket.socket) -> None:
        with self._conn_lock:
            self._conns.add(conn)

    def untrack(self, conn: socket.socket) -> None:
        with self._conn_lock:
            self._conns.discard(conn)

    def stop(self) -> None:
        """止める。2 回目以降は何もしない。開いたままの接続も切る。"""
        if self._stopped:
            return
        self._stopped = True
        self.shutdown()
        self.server_close()
        with self._conn_lock:
            conns = list(self._conns)
        for conn in conns:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        if self._thread is not None:
            self._thread.join(READY_LIMIT)
        log().info("停止した", port=self.port, requests=len(self.requests))

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def log() -> diaglog.Logger:
    return diaglog.get(LOG_NAME)


def as_skew(skew) -> timedelta:
    if skew is None:
        return DEFAULT_SKEW
    if isinstance(skew, timedelta):
        return skew
    return timedelta(seconds=skew)


def start(
    state_path: str,
    *,
    skew: timedelta | float | None = None,
    initial_state: dict | None = None,
    port: int = 0,
    echo: bool = False,
) -> Server:
    """代役を別スレッドで立てて、接続を受けられるのを確かめてから返す。

    確かめる上限は 5 秒で、超えたら TimeoutError。

    `port` を 0 にすると OS に決めさせる（`Server.port`）。空きポートを先に探してから
    bind し直すと、その間に取られるので、探さない。0 以外は手で使う入口のためにある。
    `skew` は時計を進める量。`timedelta` か秒数で、既定は 3 分。0 も負も渡せる。
    `initial_state` を渡すと、`state_path` に書いてから起こす。渡さなければ `state_path` が
    すでにあること。
    """
    if initial_state is not None:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(initial_state, f, ensure_ascii=False, indent=1)
    elif not os.path.exists(state_path):
        raise FileNotFoundError(f"状態ファイルが無い: {state_path}")
    server = Server(state_path, as_skew(skew), TOKEN, port, echo)
    thread = threading.Thread(target=server.serve_forever, name="fake-gitlab", daemon=True)
    server._thread = thread
    thread.start()
    try:
        wait_ready(server.port)
    except BaseException:
        server.stop()
        raise
    log().info(
        "起動した",
        port=server.port,
        state=state_path,
        skew_s=int(server.skew.total_seconds()),
    )
    return server


def wait_ready(port: int) -> None:
    deadline = time.monotonic() + READY_LIMIT
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"fake-gitlab が {READY_LIMIT} 秒以内に接続を受けなかった"
                ) from None
            time.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    state_path, port = args[0], int(args[1])
    server = start(state_path, port=port, echo=True)
    sys.stderr.write(f"fake-gitlab listening on 127.0.0.1:{server.port} state={state_path}\n")
    sys.stderr.flush()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
