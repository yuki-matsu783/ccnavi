"""decide の sh の試験が共有する代役。投稿を操作記録の段階に載せた形で動かす。

実行ファイルの代役（`STUB`）は、`ops …` を本物の `ccnavi ops` に渡し、受け入れの計画を本物の
操作記録（`tickets/model/oplog.py`）で書く。ホストの代役（`tests/sh/test_review_decide.py` の
`GitLab`）には、issue・コメントの作成と一覧を足す（`install`。送ったものは `STORE` に積む）。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest.mock as mock

from ccnavi.entry import version
from tests import ROOT

# 実行ファイルの代役。`ops` は本物へ渡す。`--preview` は決まった一覧を返し、`STUB_PLAN` があれば
# その計画を使う。`--accept-plan` は本物の操作記録に判断を保存して
# 計画を書き、`--yes` は計画の操作ID に「適用した」を記録する。
# `--check` は `STUB_FAIL_CHECK` があれば断る。
STUB = """#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\\nfamily i0001\\ntarget no\\n'; exit 0 ;;
*" ops "*) exec "$STUB_PY" -m ccnavi "$@" ;;
esac
printf '%s\\n' "$*" >>"$STUB_LOG"
root="$2"
case " $* " in
*" --check "*) [ -z "$STUB_FAIL_CHECK" ] || { echo "断った" >&2; exit 1; }; exit 0 ;;
*" --preview "*) printf '{"version":1,"threads":[{"key":"u1"}],"digest":"d0"}\\n' ;;
*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  if [ -n "$STUB_PLAN" ]; then cp "$STUB_PLAN" "$plan"; else
    "$STUB_PY" "$STUB_BEGIN" "$root/logs/state" "$plan" "$STUB_MRURL"
  fi ;;
*" --yes "*)
  [ -z "$STUB_FAIL_YES" ] || { echo "置けない" >&2; exit 1; }
  done_plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-done ] && done_plan="$a"; prev="$a"; done
  op=$(sed -n 's/.*"op": "\\([0-9a-f]*\\)".*/\\1/p' "$done_plan")
  [ -z "$op" ] || "$STUB_PY" -m ccnavi --root "$root" ops applied "$op" local true
  printf '{"version":2,"ok":true,"reviewed":true,"followup":""}\\n' ;;
esac
"""

# 操作記録を作って受け入れの計画を書く（`--accept-plan` の代役）。
BEGIN = """import json
import sys

from ccnavi.tickets.model import oplog

state, plan, mr_url = sys.argv[1:4]
op = oplog.begin(
    state,
    "decide",
    repo={"host": "gitlab", "mr_url": mr_url},
    parent="i0001",
    phase=1,
    mr=1,
    shown=[
        {"key": "u1", "id": "d1", "url": "u1", "path": "a.py", "line": 3, "version": "v1",
         "body": "指摘の本文\\n```py\\nx\\n```"},
    ],
    choices={"u1": "issue"},
    plan={"digest": "d0"},
    stages=[("reply", "reply"), ("issue", "issue"), ("note", "comment")],
)
body = {
    "version": 1,
    "digest": "d0",
    "op": op,
    "threads": [{"id": "d1", "key": "u1", "reply": "<!-- ccnavi:accept -->\\nok\\n"}],
}
with open(plan, "w", encoding="utf-8") as f:
    json.dump(body, f)
"""

# 代役のホストが覚えている投稿。
STORE: dict = {}


def reset_store() -> None:
    STORE.clear()
    STORE.update(
        issues=[],
        notes=[],
        issue_notes=[],
        user="me",
        # 保存してから 500 を返す種類（送れたのに応答だけ失敗した形）
        lost_response=set(),
        # 保存せずに 500 を返す種類
        reject=set(),
    )


def _item(kind: str, data: dict, url: str, ident: int) -> dict:
    return {
        "id": ident,
        "iid": ident,
        "web_url": url,
        "body": data.get("body", ""),
        "description": data.get("description", ""),
        "author": {"username": STORE["user"]},
    }


def do_get(self):
    path = self.path
    if path.split("?")[0].endswith("/user"):
        self.reply(200, {"username": STORE["user"]})
        return
    if "per_page" in path:
        page1 = "page=1" in path
        if "/merge_requests/1/notes" in path:
            self.reply(200, STORE["notes"] if page1 else [])
            return
        if "/issues/1/notes" in path:
            self.reply(200, STORE["issue_notes"] if page1 else [])
            return
        if "/issues?" in path:
            self.reply(200, STORE["issues"] if page1 else [])
            return
    GITLAB.original_get(self)


def do_post(self):
    path = self.path
    if "/discussions/" in path:
        # 返信は元の代役に任せる
        GITLAB.original_post(self)
        return
    length = int(self.headers.get("Content-Length", "0") or 0)
    data = json.loads(self.rfile.read(length) or b"{}")
    if path.endswith("/issues"):
        kind, store, url = (
            "issue",
            STORE["issues"],
            f"http://127.0.0.1:{GITLAB.port}/demo/greeter/-/issues/1",
        )
        entry = _item(kind, data, url, 1)
        reply = {"iid": 1, "web_url": url}
    elif path.endswith("/issues/1/notes"):
        kind, store = "issue-note", STORE["issue_notes"]
        entry = _item(kind, data, f"{path}#note_{len(store) + 20}", len(store) + 20)
        reply = {"id": entry["id"]}
    elif path.endswith("/notes"):
        kind, store = "note", STORE["notes"]
        entry = _item(kind, data, f"{path}#note_{len(store) + 30}", len(store) + 30)
        reply = {"id": entry["id"], "created_at": "2026-09-23T00:00:00Z"}
    else:
        self.reply(405, {"message": "405"})
        return
    if kind in STORE["reject"]:
        self.reply(500, {"message": "500 boom"})
        return
    store.append(entry)
    GITLAB.posted.append((kind, data))
    if kind in STORE["lost_response"]:
        self.reply(500, {"message": "500 lost"})
        return
    self.reply(201, reply)


class _Holder:
    """`install` が渡された `GitLab` を覚える。ハンドラは差し替え前の GET・POST も呼ぶ。"""

    posted: list = []
    port = 0
    original_get = None
    original_post = None


GITLAB = _Holder


def install(testcase, gitlab) -> None:
    """ホストの代役を投稿の形に差し替え、実行ファイルの代役を書き換える（テストの終わりに戻す）。"""
    reset_store()
    GITLAB.original_get = gitlab.do_GET
    GITLAB.original_post = gitlab.do_POST
    GITLAB.posted = gitlab.posted
    GITLAB.port = gitlab.port
    for name, handler in (("do_GET", do_get), ("do_POST", do_post)):
        patcher = mock.patch.object(gitlab, name, handler)
        patcher.start()
        testcase.addCleanup(patcher.stop)
    testcase.begin = os.path.join(testcase.work, "begin_op.py")
    with open(testcase.begin, "w", encoding="utf-8") as f:
        f.write(BEGIN)
    with open(testcase.stub, "w", encoding="utf-8") as f:
        f.write(STUB.replace("{compat}", str(version.COMPAT)))
    os.chmod(testcase.stub, os.stat(testcase.stub).st_mode | stat.S_IXUSR)
    testcase.mr_url = f"http://127.0.0.1:{gitlab.port}/demo/greeter/-/merge_requests/1"


def stub_env(testcase) -> dict:
    """実行ファイルの代役が使う環境変数。"""
    return dict(
        STUB_PY=sys.executable,
        STUB_BEGIN=testcase.begin,
        STUB_MRURL=testcase.mr_url,
        PYTHONPATH=os.path.join(ROOT, "src"),
    )
