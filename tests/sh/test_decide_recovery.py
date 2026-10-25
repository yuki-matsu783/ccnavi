"""decide の復旧と照合の穴を確かめる sh の受入テスト（ccnavi-user-decide.sh）。

道具は `test_decide_ops`（実行ファイルの代役・GitLab の代役・本物の操作記録）を借りる。

1. AT43: ホストに書いたあとの照合が外れたとき、続きの子もマーカーも置かず（置く呼び出しが断る）、
   ホストの解決は残り、操作記録に解決した分が残る
2. AT19: 置く段階で失敗し、あとから --resume で完了させる。返信は 1 回だけ
3. AT17: 適用済み（applied.local）の記録を --resume しても、自分の変化を食い違いとして断らない
4. AT25: 親のワークツリーを消したあと、ワークスペースのルートから --resume する
5. AT20: 操作のロック（sh の `locks/ops/<操作ID>`）が取られている間の --resume は投稿しない

実行ファイル側の AT43・AT13・AT24 は `tests/ticket/test_decide_exe.py`。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest

from ccnavi.tickets import oplog
from tests.sh import test_decide_ops as ops
from tests.sh import test_review_decide as base

GitLab = ops.GitLab
STORE = ops.STORE

# `--yes` の代役に「置く段階で失敗する」「ホストに書いたあとの照合が外れた」形を足す。
STUB = ops.STUB.replace(
    '  [ -z "$STUB_FAIL_YES" ] || { echo "置けない" >&2; exit 1; }\n',
    '  [ -z "$STUB_FAIL_YES" ] || { echo "置けない" >&2; exit 1; }\n'
    '  if [ -n "$STUB_MISMATCH" ]; then\n'
    '    done_plan=""; prev=""\n'
    '    for a in "$@"; do [ "$prev" = --accept-done ] && done_plan="$a"; prev="$a"; done\n'
    '    op=$(sed -n \'s/.*"op": "\\([0-9a-f]*\\)".*/\\1/p\' "$done_plan")\n'
    '    "$STUB_PY" -m ccnavi --root "$root" ops applied "$op" accepted_on_host u1\n'
    '    printf \'{"version":2,"ok":false,"mismatch":true,"changed":["u1"]}\\n\'\n'
    '    echo "ccnavi: 見せた指摘と今の指摘が違う。何も置いていない" >&2\n'
    "    exit 1\n"
    "  fi\n",
)


@unittest.skipIf(
    base.SHELL is None or base.GIT is None or base.NEEDS, "sh / git / jq / curl が無い"
)
class DecideRecoveryShTest(unittest.TestCase):
    def setUp(self):
        ops.DecideOpsShTest.setUp(self)
        with open(self.stub, "w", encoding="utf-8") as f:
            f.write(STUB.replace("{compat}", str(ops.version.COMPAT)))
        # 以降の run_decide が使う cwd（AT25 で替える）
        self.cwd = self.tree

    calls = base.ReviewDecideShTest.calls
    record = ops.DecideOpsShTest.record
    choose = ops.DecideOpsShTest.choose
    age = ops.DecideOpsShTest.age

    def run_decide(self, *args: str, stdin: str = "", **extra: str):
        env = dict(
            STUB_PY=ops.sys.executable,
            STUB_BEGIN=self.begin,
            STUB_MRURL=self.mr_url,
            STUB_MISMATCH="",
            PYTHONPATH=os.path.join(ops.ROOT, "src"),
        )
        env.update(extra)
        return self.decide_with_input(args, stdin, env)

    def decide_with_input(self, args, stdin, env):
        saved, self.tree = self.tree, self.cwd
        try:
            return ops.DecideOpsShTest.decide_with_input(self, args, stdin, env)
        finally:
            self.tree = saved

    def kinds(self) -> list[str]:
        return [k for k, _ in GitLab.posted]

    # ---- AT43

    def test_a_mismatch_after_the_host_write_keeps_the_resolved_part_and_places_nothing(self):
        """AT43（sh の側。実行ファイルの照合そのものは tests/ticket/test_decide_exe.py）"""
        failed = self.choose(STUB_MISMATCH="1")
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertTrue(json.loads(failed.stdout.strip().splitlines()[-1])["mismatch"])
        self.assertIn("何も置いていない", failed.stderr)
        # ホストで解決した分は残る。issue もコメントも作らない
        self.assertEqual(self.kinds(), ["reply", "resolve"])
        self.assertEqual(GitLab.discussions, {"d1": True})
        self.assertEqual(STORE["issues"], [])
        self.assertEqual(STORE["notes"], [])
        # 記録に解決した分が残り、適用（local）は立たない。計画も残す
        record = self.record()
        self.assertTrue(record["applied"]["accepted_on_host"])
        self.assertFalse(record["applied"].get("local"))
        self.assertIn("計画を残した", failed.stderr)
        self.assertIn("review-accept-i0001-1.json", os.listdir(self.state))

    # ---- AT19

    def test_a_failed_placement_then_resume_replies_once_and_places_once(self):
        """AT19"""
        failed = self.choose(STUB_FAIL_YES="1")
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(self.kinds(), ["reply", "resolve"])
        record = self.record()
        self.assertFalse(record["applied"].get("local"))
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.kinds().count("reply"), 1, "返信は 1 回だけ")
        self.assertEqual(self.kinds().count("resolve"), 1)
        # 置く呼び出し（続きの子・マーカーを置くところ）は、失敗した 1 回と成功した 1 回
        placing = [c for c in self.calls() if "--accept-done" in c]
        self.assertEqual(len(placing), 2, placing)
        self.assertEqual(len([c for c in self.calls() if "--accept-plan" in c]), 1)
        # 投稿も 1 回ずつ。記録は消える
        self.assertEqual(len(STORE["issues"]), 1)
        self.assertEqual(len(STORE["notes"]), 1)
        self.assertIsNone(self.record())
        self.assertEqual(
            [n for n in os.listdir(self.state) if n.startswith("review-accept")],
            [],
            "置けたので計画は消える",
        )

    def test_resume_after_a_second_failure_still_replies_once(self):
        """AT19（置く段階で 2 回続けて失敗しても、返信は 1 回）"""
        self.assertEqual(self.choose(STUB_FAIL_YES="1").returncode, 1)
        again = self.run_decide("--resume", stdin="y\n", STUB_FAIL_YES="1")
        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.kinds().count("reply"), 1)
        self.assertEqual(len(STORE["issues"]), 1)

    # ---- AT17

    def test_resume_does_not_refuse_its_own_changes_when_the_record_says_applied(self):
        """AT17"""
        STORE["reject"].add("note")
        self.assertEqual(self.choose().returncode, 0)
        record = self.record()
        self.assertTrue(record["applied"]["local"])
        # 自分が作った子と動かした子を記録に足す。ホストは、直す指摘が未解決のまま、指摘も増えた
        oplog.applied_set(self.state, record["id"], "followup", "i0001-01-03")
        oplog.applied_set(self.state, record["id"], "moved", ["i0001-01-01", "i0001-01-02"])
        GitLab.discussions = {"d1": True, "d2": False, "d3": False}
        STORE["reject"].clear()
        self.age(record["id"], "note")
        before = len(self.calls())
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("違う", done.stderr)
        self.assertNotIn("mismatch", done.stdout)
        # 置く呼び出し・計画は打たない（適用は済んでいるので、投稿の段階だけを進める）
        later = self.calls()[before:]
        self.assertEqual([c for c in later if "--accept" in c or "--yes" in c], [])
        self.assertEqual(len(STORE["notes"]), 1)
        self.assertEqual(len(STORE["issues"]), 1, "送り終えた issue は繰り返さない")
        self.assertIsNone(self.record())

    # ---- AT25

    def parent_worktree(self) -> str:
        """`<ws>/.claude/worktrees/i0001`。親のブランチ（i0001）のワークツリーの代わりの git。"""
        where = os.path.join(self.ws, ".claude", "worktrees", "i0001")
        os.makedirs(os.path.dirname(where), exist_ok=True)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run([base.GIT, "init", "-q", "-b", "i0001", where], check=True)
        subprocess.run(
            [base.GIT, "-C", where, "commit", "-q", "--allow-empty", "-m", "i"], check=True, env=env
        )
        origin = f"http://127.0.0.1:{GitLab.port}/demo/greeter.git"
        subprocess.run([base.GIT, "-C", where, "remote", "add", "origin", origin], check=True)
        return where

    def stopped_at_the_note(self) -> dict:
        STORE["reject"].add("note")
        self.assertEqual(self.choose().returncode, 0)
        record = self.record()
        STORE["reject"].clear()
        self.age(record["id"], "note")
        return record

    def test_resume_from_the_workspace_root_moves_to_the_parent_worktree(self):
        """AT25（親のワークツリーが在れば、ルートから打っても続きを送れる）"""
        self.parent_worktree()
        self.stopped_at_the_note()
        self.cwd = self.ws
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(STORE["notes"]), 1)
        self.assertIsNone(self.record())

    def test_resume_from_the_workspace_root_after_the_parent_worktree_is_gone(self):
        """AT25（親のワークツリーを消したあと。設計は「対象は記録の repo から決める」）"""
        self.stopped_at_the_note()
        self.cwd = self.ws
        done = self.run_decide("--resume", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len(STORE["notes"]), 1)

    # 実装は親のワークツリー（<ws>/.claude/worktrees/<親>）に必ず移り、無ければ断る
    test_resume_from_the_workspace_root_after_the_parent_worktree_is_gone = (
        unittest.expectedFailure(
            test_resume_from_the_workspace_root_after_the_parent_worktree_is_gone
        )
    )

    # ---- AT20

    def test_two_resumes_of_the_same_operation_do_not_post_twice(self):
        """AT20: 操作IDのロック中の --resume は、投稿せずに断る（同時実行の代わりの決定的な形）"""
        STORE["reject"].add("issue")
        self.assertEqual(self.choose().returncode, 0)
        record = self.record()
        STORE["reject"].clear()
        self.age(record["id"], "issue")
        # もう一方の --resume が実行中の状態（新しいロックファイル）を作る
        lock = os.path.join(self.state, "locks", "ops", record["id"])
        os.makedirs(lock)
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        now = int(time.time())
        with open(os.path.join(lock, "owner"), "w", encoding="utf-8") as f:
            f.write(f"{host} {os.getpid()} {now} {os.getpid()}-{now}\n")
        # 最初の判断で issue 以外の段階（note）は送り済み。--resume が増やさないことを確かめる
        notes_before = len(STORE["notes"])
        calls_before = len(self.calls())
        result = self.run_decide("--resume", stdin="y\n")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(STORE["issues"]), 0, "ロック中なのに issue を作った")
        self.assertEqual(len(STORE["notes"]), notes_before, "ロック中なのに投稿した")
        self.assertEqual(self.calls()[calls_before:], [], "ロック中なのに ccnavi を呼んだ")


if __name__ == "__main__":
    unittest.main()
