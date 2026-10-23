"""decide の投稿を操作記録の段階に載せた形（ccnavi-user-decide.sh と ccnavi-user-lib-review.sh）。

見るのは sh の仕事。

1. 受け入れの計画が持つ操作ID で、返信・issue・コメントの段階を記録し、投稿の本文の末尾に
   目印 `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->` を足す。すべて送れたら記録を消す
2. 送れなかった段階は記録に残る。決めたことは戻さず、「引き継ぎ未完了」と `--resume` を言う
3. `--resume` は、保存した判断から続きを送る。送り終えた段階は繰り返さない
4. 送ったか確定できないときは、ホストの一覧を目印とアカウントで照合する。1 件あれば送り済みとし、
   0 件でも、反映待ち（送ってすぐ）やアカウント違いのときは自動で送り直さない

実行ファイルは代役の sh。`ops …` だけは本物の `ccnavi ops` に渡し、受け入れの計画は本物の
操作記録（`tickets/oplog.py`）で書く。GitLab は同じプロセスの小さな HTTP サーバ。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from unittest import mock

from ccnavi.entry import version
from ccnavi.tickets import oplog
from tests import ROOT
from tests.sh import test_review_decide as base

GitLab = base.GitLab

# 実行ファイルの代役。`ops` は本物へ渡す。`--accept-plan` は本物の操作記録に判断を保存して
# 計画を書き、`--yes` は計画の操作ID に「適用した」を記録する。
STUB = """#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\\nfamily i0001\\ntarget no\\n'; exit 0 ;;
*" ops "*) exec "$STUB_PY" -m ccnavi "$@" ;;
esac
printf '%s\\n' "$*" >>"$STUB_LOG"
root="$2"
case " $* " in
*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  "$STUB_PY" "$STUB_BEGIN" "$root/logs/state" "$plan" "$STUB_MRURL" ;;
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

from ccnavi.tickets import oplog

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
    ORIGINAL_GET(self)


def do_post(self):
    path = self.path
    if "/discussions/" in path:
        # 返信は元の代役に任せる
        ORIGINAL_POST(self)
        return
    length = int(self.headers.get("Content-Length", "0") or 0)
    data = json.loads(self.rfile.read(length) or b"{}")
    if path.endswith("/issues"):
        kind, store, url = (
            "issue",
            STORE["issues"],
            f"http://127.0.0.1:{self.port}/demo/greeter/-/issues/1",
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
    GitLab.posted.append((kind, data))
    if kind in STORE["lost_response"]:
        self.reply(500, {"message": "500 lost"})
        return
    self.reply(201, reply)


ORIGINAL_GET = GitLab.do_GET
ORIGINAL_POST = GitLab.do_POST


@unittest.skipIf(
    base.SHELL is None or base.GIT is None or base.NEEDS, "sh / git / jq / curl が無い"
)
class DecideOpsShTest(unittest.TestCase):
    def setUp(self):
        base.ReviewDecideShTest.setUp(self)
        reset_store()
        for target, name in ((GitLab, "do_GET"), (GitLab, "do_POST")):
            patcher = mock.patch.object(target, name, {"do_GET": do_get, "do_POST": do_post}[name])
            patcher.start()
            self.addCleanup(patcher.stop)
        self.begin = os.path.join(self.work, "begin_op.py")
        with open(self.begin, "w", encoding="utf-8") as f:
            f.write(BEGIN)
        with open(self.stub, "w", encoding="utf-8") as f:
            f.write(STUB.replace("{compat}", str(version.COMPAT)))
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.mr_url = f"http://127.0.0.1:{GitLab.port}/demo/greeter/-/merge_requests/1"

    # 既存の道具を借りる。
    calls = base.ReviewDecideShTest.calls

    def run_decide(self, *args: str, stdin: str = "", **extra: str):
        env = dict(
            STUB_PY=sys.executable,
            STUB_BEGIN=self.begin,
            STUB_MRURL=self.mr_url,
            PYTHONPATH=os.path.join(ROOT, "src"),
        )
        env.update(extra)
        return self.decide_with_input(args, stdin, env)

    def decide_with_input(self, args, stdin, env):
        shell = base.SHELL
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        import shutil

        for name in (
            "jq",
            "curl",
            "git",
            "sed",
            "cat",
            "rm",
            "dirname",
            "printf",
            "mkdir",
            "tr",
            "mktemp",
            "head",
            "date",
            "grep",
            "wc",
            "uname",
            "cp",
            "mv",
        ):
            found = shutil.which(name)
            if found and not os.path.exists(os.path.join(tools, name)):
                base.put_tool(found, os.path.join(tools, name))
        full = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        full.update(
            CCNAVI_WORKSPACE=self.ws,
            CCNAVI_BIN_PATH=self.stub,
            GITLAB_TOKEN=base.TOKEN,
            STUB_LOG=self.log,
            STUB_PLAN="",
            STUB_FAIL_YES="",
            PATH=tools + os.pathsep + os.path.dirname(shell),
        )
        full.update(env)
        path = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-user-decide.sh")
        return subprocess.run(
            [shell, path, *args],
            cwd=self.tree,
            env=full,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def record(self):
        ids = oplog.all_ids(self.state)
        self.assertLessEqual(len(ids), 1, ids)
        return oplog.load(self.state, ids[0]) if ids else None

    def choose(self, **extra):
        return self.run_decide("1", "--choices", '{"u1":"issue"}', "--digest", "d0", **extra)

    def age(self, op_id: str, stage: str) -> None:
        """段階の時刻を古くする（送ってすぐは、反映待ちとして確定しないため）。"""
        record = oplog.load(self.state, op_id)
        for row in record["stages"]:
            if row["id"] == stage:
                row["at"] = "2020-01-01T00:00:00+0000"
        with open(oplog.record_path(self.state, op_id), "w", encoding="utf-8") as f:
            json.dump(record, f)

    # ---- 1. 目印と記録

    def test_every_post_carries_the_marker_and_the_record_is_removed(self):
        done = self.choose()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([kind for kind, _ in GitLab.posted], ["reply", "resolve", "issue", "note"])
        issue = next(d for k, d in GitLab.posted if k == "issue")
        note = next(d for k, d in GitLab.posted if k == "note")
        self.assertRegex(issue["description"], r"<!-- ccnavi:op=[0-9a-f]{32} stage=issue -->\s*$")
        self.assertRegex(note["body"], r"<!-- ccnavi:op=[0-9a-f]{32} stage=note -->\s*$")
        # 先頭の目印は変えない
        self.assertTrue(note["body"].startswith("<!-- ccnavi:decide -->"))
        # 本文は保存した判断から作り直し、全文をフェンスで囲んでいる
        self.assertIn("````text", issue["description"])
        self.assertIn("指摘の本文", issue["description"])
        self.assertIn("issue に回した分: #1", note["body"])
        self.assertIsNone(self.record(), "すべて送れたら記録を消す")

    # ---- 2. 送れなかった段階

    def test_a_failed_note_leaves_the_record_and_names_resume(self):
        STORE["reject"].add("note")
        done = self.choose()
        self.assertEqual(done.returncode, 0, done.stderr)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIn("引き継ぎ未完了", answer["warning"])
        self.assertIn("--resume", answer["warning"])
        self.assertTrue(answer["issue_url"].endswith("/issues/1"))
        record = self.record()
        states = {s["id"]: s["state"] for s in record["stages"]}
        self.assertEqual(states, {"reply": "sent", "issue": "sent", "note": "unknown"})
        self.assertTrue(record["applied"]["local"])

    # ---- 3. --resume

    def test_resume_sends_only_the_missing_stage(self):
        STORE["reject"].add("note")
        self.choose()
        record = self.record()
        STORE["reject"].clear()
        self.age(record["id"], "note")
        issues_before = len(STORE["issues"])
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("続きを送った", done.stdout)
        self.assertEqual(len(STORE["issues"]), issues_before, "送り終えた issue を繰り返さない")
        self.assertEqual(len(STORE["notes"]), 1)
        self.assertIn("stage=note", STORE["notes"][0]["body"])
        self.assertIsNone(self.record())

    def test_resume_sends_the_issue_after_the_issue_post_failed(self):
        """issue への送信に失敗しても、--resume で続きを送れる（回帰。issue はいつでも選べる）。"""
        STORE["reject"].add("issue")
        self.choose()
        record = self.record()
        self.assertIsNotNone(record, "送れなかった段階は記録に残る")
        states = {s["id"]: s["state"] for s in record["stages"]}
        self.assertNotEqual(states["issue"], "sent")
        STORE["reject"].clear()
        self.age(record["id"], "issue")
        resumed = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(len(STORE["issues"]), 1)
        self.assertIn("stage=issue", STORE["issues"][0]["description"])
        self.assertIsNone(self.record())

    def test_resume_after_a_resolve_failure_does_not_reply_twice(self):
        GitLab.fail_resolve = True
        first = self.choose()
        self.assertNotEqual(first.returncode, 0, first.stdout)
        replies = [k for k, _ in GitLab.posted if k == "reply"]
        self.assertEqual(len(replies), 1)
        GitLab.fail_resolve = False
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        kinds = [k for k, _ in GitLab.posted]
        self.assertEqual(kinds.count("reply"), 1, "返信済みのスレッドに重ねない")
        self.assertIn("resolve", kinds)

    def test_resume_asks_first_and_stops_on_no(self):
        STORE["reject"].add("note")
        self.choose()
        STORE["reject"].clear()
        stopped = self.run_decide("--resume", stdin="n\n")
        self.assertNotEqual(stopped.returncode, 0)
        self.assertEqual(STORE["notes"], [])
        self.assertIsNotNone(self.record())

    def test_resume_without_a_pending_decision_says_so(self):
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("未完了の判断はありません", done.stdout)

    # ---- 4. 照合

    def test_a_note_that_landed_but_lost_its_response_is_not_posted_twice(self):
        STORE["lost_response"].add("note")
        self.choose()
        record = self.record()
        self.assertEqual({s["id"]: s["state"] for s in record["stages"]}["note"], "unknown")
        self.age(record["id"], "note")
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(STORE["notes"]), 1, "ホストに在るので、送り直さない")
        self.assertIsNone(self.record())

    def test_right_after_a_failure_zero_matches_is_not_confirmed(self):
        STORE["reject"].add("note")
        self.choose()
        STORE["reject"].clear()
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(STORE["notes"], [], "送ってすぐは反映待ちかもしれない。自動では送らない")
        self.assertIn("確定できない", done.stderr)
        self.assertIsNotNone(self.record())

    def test_a_different_account_cannot_confirm_that_nothing_was_sent(self):
        STORE["reject"].add("note")
        self.choose()
        record = self.record()
        # 前に送ったアカウントを記録しておき、今のトークンの持ち主を変える
        oplog.stage_set(self.state, record["id"], "note", oplog.UNKNOWN, account="someone-else")
        self.age(record["id"], "note")
        STORE["reject"].clear()
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(STORE["notes"], [])
        self.assertIn("確定できない", done.stderr)
        self.assertIsNotNone(self.record())


if __name__ == "__main__":
    unittest.main()
