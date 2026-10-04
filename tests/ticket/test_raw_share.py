"""承認済みチケットの置き場を、1 回の起動で 1 度だけ読むことの受入テスト。

`--explain`（ボードの JSON と文の一覧）と `--lint` は、親ごとの局面とフェーズ
（`phase.stage`・`phase.phases_of`・`phase.order_problems`）まで同じ読み（`approval.read_raw`）を
持ち回る。親ごとに読み直すと、読む回数が親の数とツリーの数の積で増え、置き場が育った
ワークスペースでは VS Code の拡張の期限を超える。

hook も同じ。実行前チェック（レビュー待ちの止め）、実行後チェック（範囲とフェーズの知らせ）、
ターンの終わり（範囲と `finish` の促し）は、置き場を 1 度だけ読む。実行後チェックが作業ツリーを
戻した回だけは、置き場も戻っていることがあるので読み直す。

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

from ccnavi.hook import post
from ccnavi.records import audit
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
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("src/a1.py", "x\n")])
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")

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

    def stop(self, cwd):
        payload = {"hook_event_name": "Stop", "cwd": cwd, "session_id": "s1"}
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

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

    # ---- hook

    def test_post_tool_use_reads_once_and_announces(self):
        """実行後チェックは、範囲とフェーズの知らせ（`phase.announce`）で同じ読みを使う。"""
        self.ended_phase()
        result, reads = self.counted(
            lambda: self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        )
        self.assertIn("ccnavi-review.sh chat 1", self.reason(result))
        self.assertEqual(reads.counts(), ONCE)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.pending")))

    def test_pre_tool_use_hold_reads_once(self):
        """レビュー待ちの止めは、親を引くのとフェーズを組むのと承認の知らせで同じ読みを使う。"""
        self.ended_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        for tool, extra in (("Agent", {"description": "次の子"}), ("Bash", {"command": "make"})):
            with self.subTest(tool=tool):
                result, reads = self.counted(
                    lambda tool=tool, extra=extra: self.hook(
                        "PreToolUse", tool, self.parent_tree, **extra
                    )
                )
                self.assertIn("DENY_PHASE_REVIEW", self.reason(result))
                self.assertEqual(reads.counts(), ONCE)

    def test_pre_tool_use_at_the_workspace_root_reads_once(self):
        """ワークスペースルートのシェルは止めの側で読まず、承認の知らせの 1 度だけ。"""
        self.ended_phase()
        result, reads = self.counted(
            lambda: self.hook("PreToolUse", "Bash", self.root, command="ls")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(reads.counts(), ONCE)

    def test_stop_reads_once(self):
        """ターンの終わりは、範囲と `finish` の促し（閉じられるかの検査）で同じ読みを使う。"""
        self.ended_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        result, reads = self.counted(lambda: self.stop(self.parent_tree))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(reads.counts(), ONCE)

    def test_post_tool_use_reads_again_after_it_may_have_restored(self):
        """実行後チェックが作業ツリーを戻しうる回（戻しが有効で、差し戻す変更がある）は読み直す。

        戻した先が置き場だと、最初の読みはもう古い。読み直さないと、戻す前のチケットで
        フェーズの知らせを組む。戻しが無効なら、同じ回でも読み直さない。
        """
        self.ended_phase()

        def denied(*args, **kwargs):
            kwargs["record"].decision = audit.DENY
            return ""

        for restore, expected in (("enable", 2), ("disable", 1)):
            with self.subTest(restore=restore):
                payload = {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "cwd": self.parent_tree,
                    "session_id": "s1",
                    "tool_input": {"command": "ls"},
                }
                with mock.patch.object(post, "check", side_effect=denied):
                    result, reads = self.counted(
                        lambda restore=restore, payload=payload: self.ccnavi(
                            "--mode",
                            "enable",
                            "--restore-if-deny",
                            restore,
                            stdin=json.dumps(payload),
                        )
                    )
                self.assertIn(result.returncode, (0, 2), result.stderr)
                self.assertEqual(reads.read_raw.call_count, expected)
