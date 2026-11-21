"""decide の投稿を操作記録の段階に載せた形（ccnavi-user-decide.sh と ccnavi-user-lib-review.sh）。

見るのは sh の仕事。

1. 受け入れの計画が持つ操作ID で、返信・issue・コメントの段階を記録し、投稿の本文の末尾に
   目印 `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->` を足す。すべて送れたら記録を消す
   （すべて送れる通しは test_review_decide の
   test_choices_create_the_issue_and_post_the_decision が見る）
2. 送れなかった段階は記録に残る。決めたことは戻さず、「引き継ぎ未完了」と `--resume` を言う
3. `--resume` は、保存した判断から続きを送る。送り終えた段階は繰り返さない
4. 送ったか確定できないときは、ホストの一覧を目印とアカウントで照合する。1 件あれば送り済みとし、
   0 件でも、反映待ち（送ってすぐ）やアカウント違いのときは自動で送り直さない

実行ファイルはスタブの sh。`ops …` だけは本物の `ccnavi ops` に渡し、受け入れの計画は実行ファイルと
同じ形の操作記録（`tests/oprecord.py`）で書く。GitLab は同じプロセスの小さな HTTP サーバ。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from tests import oprecord
from tests.sh import decide_support as support
from tests.sh import test_review_decide as base

GitLab = base.GitLab

STORE = support.STORE


@unittest.skipIf(
    base.SHELL is None or base.GIT is None or base.NEEDS, "sh / git / jq / curl が無い"
)
class DecideOpsShTest(unittest.TestCase):
    def setUp(self):
        base.ReviewDecideShTest.setUp(self)

    # 既存の道具を借りる。
    calls = base.ReviewDecideShTest.calls

    def run_decide(self, *args: str, stdin: str = "", **extra: str):
        env = support.stub_env(self)
        env.update(extra)
        return self.decide_with_input(args, stdin, env)

    def decide_with_input(self, args, stdin, env):
        shell = base.SHELL
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        for name in base.STANDARD_TOOLS:
            found = shutil.which(name)
            if found and not os.path.exists(os.path.join(tools, name)):
                base.put_tool(found, os.path.join(tools, name))
        full = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        full.update(
            CCNAVI_WORKSPACE=self.ws,
            CCNAVI_ENTRY_PATH=self.stub,
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
        ids = oprecord.ids(self.state)
        self.assertLessEqual(len(ids), 1, ids)
        return oprecord.load(self.state, ids[0]) if ids else None

    def choose(self, **extra):
        return self.run_decide("1", "--choices", '{"u1":"issue"}', "--digest", "d0", **extra)

    def age(self, op_id: str, stage: str) -> None:
        """段階の時刻を古くする（送ってすぐは、反映待ちとして確定しないため）。"""
        record = oprecord.load(self.state, op_id)
        for row in record["stages"]:
            if row["id"] == stage:
                row["at"] = "2020-01-01T00:00:00+0000"
        oprecord.save(self.state, record)

    # ---- 1. 目印と記録

    def test_every_standard_tool_resolves_in_the_child_path(self):
        # 子プロセスの PATH は tools（と /bin）だけ。
        # macOS では awk が /bin にないため、tools になければ rc=127 で終わる
        self.run_decide("--help")
        tools = os.path.join(self.work, "bin")
        for name in base.STANDARD_TOOLS:
            if shutil.which(name):
                self.assertTrue(os.path.lexists(os.path.join(tools, name)), name)
        self.assertIn("awk", base.STANDARD_TOOLS)

    # ---- 2・3・4. 送れなかった段階から --resume で続きを送るまで

    def test_a_failed_note_is_resumed_only_when_it_is_safe(self):
        """コメントだけ送れなかった判断を、--resume で続ける一連の流れ。

        1. 送れなかった段階は記録に残り、答えは「引き継ぎ未完了」と --resume を言う
        2. --resume は先に聞き、n なら何も送らない
        3. 送ってすぐは、ホストに 0 件でも反映待ちかもしれないので確定しない
        4. 前に送ったアカウントと今のトークンの持ち主が違えば、0 件でも確定しない
        5. 時間が経ち、同じアカウントなら、残った段階（コメント）だけを送り、記録を消す
        """
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
        STORE["reject"].clear()
        issues_before = len(STORE["issues"])

        stopped = self.run_decide("--resume", stdin="n\n")
        self.assertNotEqual(stopped.returncode, 0)
        self.assertEqual(STORE["notes"], [])
        self.assertIsNotNone(self.record())

        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(STORE["notes"], [], "送ってすぐは反映待ちかもしれない。自動では送らない")
        self.assertIn("確定できない", done.stderr)
        self.assertIsNotNone(self.record())

        def note_account(account=None):
            loaded = oprecord.load(self.state, record["id"])
            for row in loaded["stages"]:
                if row["id"] == "note":
                    before = row.get("account")
                    if account is not None:
                        row.update(state="unknown", account=account)
            oprecord.save(self.state, loaded)
            return before

        mine = note_account("someone-else")
        self.assertIsNotNone(mine)
        self.age(record["id"], "note")
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(STORE["notes"], [])
        self.assertIn("確定できない", done.stderr)
        self.assertIsNotNone(self.record())

        note_account(mine)
        self.age(record["id"], "note")
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


if __name__ == "__main__":
    unittest.main()
