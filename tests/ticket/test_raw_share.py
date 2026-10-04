"""承認済みチケットの置き場を、1 回の起動で 1 度だけ読むことの受入テスト。

`--explain`（ボードの JSON と文の一覧）と `--lint` は、親ごとの局面とフェーズ
（`phase.stage`・`phase.phases_of`・`phase.order_problems`）まで同じ読み（`approval.read_raw`）を
持ち回る。親ごとに読み直すと、読む回数が親の数とツリーの数の積で増え、置き場が育った
ワークスペースでは VS Code の拡張の期限を超える。

数えるのは置き場を読む 3 つの関数（`read_raw`・`scan_all`・`review_all`）と、1 本ずつ読む
`load_copy`。1 度だけ読めば `scan_all` は作業中と閉じたで 2 回、`review_all` は 1 回になり、
`load_copy` は全ツリーの置き場のファイルの数と同じになる。
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
from unittest import mock

from ccnavi.tickets import approval
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text


class Reads:
    """置き場を読む関数の呼ばれた回数。"""

    def __init__(self, stack: contextlib.ExitStack):
        self.read_raw = stack.enter_context(
            mock.patch.object(approval, "read_raw", wraps=approval.read_raw)
        )
        self.scan_all = stack.enter_context(
            mock.patch.object(approval, "scan_all", wraps=approval.scan_all)
        )
        self.review_all = stack.enter_context(
            mock.patch.object(approval, "review_all", wraps=approval.review_all)
        )
        self.load_copy = stack.enter_context(
            mock.patch.object(approval, "load_copy", wraps=approval.load_copy)
        )

    def counts(self) -> tuple[int, int, int]:
        return (self.read_raw.call_count, self.scan_all.call_count, self.review_all.call_count)


ONCE = (1, 2, 1)


class RawShareTest(PhaseHarness):
    # ---- 道具

    def counted(self, run):
        with contextlib.ExitStack() as stack:
            reads = Reads(stack)
            result = run()
        return result, reads

    def ended_phase(self):
        """`review: chat` のフェーズ 1 を終わらせる（レビュー準備中になる手前）。"""
        self.family(plan=["chores", "design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("src/a1.py", "x\n")])
        closed = self.close_child("i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent("close 01")
        self.merge("i0001-01")

    def approved_files(self) -> int:
        """全ツリーの承認済みチケットの置き場（作業中・閉じた）とレビュー待ちのファイルの数。"""
        pattern = [
            os.path.join(self.root, ".ccnavi", "approved", "*", "*.md"),
            os.path.join(
                self.root, ".claude", "worktrees", "*", ".ccnavi", "approved", "*", "*.md"
            ),
            os.path.join(self.root, "wip", "proposals", "review", "*.md"),
            os.path.join(
                self.root, ".claude", "worktrees", "*", "wip", "proposals", "review", "*.md"
            ),
        ]
        return sum(len(glob.glob(p)) for p in pattern)

    # ---- 診断

    def test_explain_and_lint_read_the_approved_copies_once(self):
        """親とツリーが増えても、`--explain` と `--lint` が置き場を読むのは 1 度。"""
        self.ended_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")  # レビュー準備中にする
        # 親をもう 1 つと、ワークツリーを 2 つ足す。読む回数は変わらない。
        self.propose("i0002", parent_text("i0002", ["chores"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.worktree("x1", "i0001")
        self.worktree("x2", "i0001")
        files = self.approved_files()
        for args in (("--explain", "--json"), ("--explain",), ("--lint", "--json")):
            with self.subTest(args=args):
                result, reads = self.counted(lambda args=args: self.ccnavi(*args))
                self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                self.assertEqual(reads.counts(), ONCE)
                self.assertEqual(reads.load_copy.call_count, files)
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        parents = {p["ticket"]: p for p in board["parents"]}
        self.assertEqual(set(parents), {"i0001", "i0002"})
        self.assertIn("レビュー準備中", parents["i0001"]["stage"])
