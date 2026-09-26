"""`finish` の打ち忘れを Stop で促す（ADR-0087）の受入テスト。道具を外から呼んで応答だけを見る。

促すのは、cwd のワークツリーのチケットが着手済みで、未コミットの変更が無く、基準点より先に
コミットがあるときだけ。1 回の連鎖に 1 回（`stop_hook_active`）。何を除くかをここで固定する。

道具は並行するチケットの受入テスト（test_ticket.TicketTest）のものを借りる。借りるだけで、
あちらのテストはここでは走らせない（`load_tests`）。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import settings
from tests.ticket.test_ticket import TicketTest, git, write


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りた TicketTest のテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(StopNudgeTest)):
        if name.startswith("test_"):
            suite.addTest(StopNudgeTest(name))
    return suite


CODE = "NUDGE_TICKET_FINISH"


class StopNudgeTest(TicketTest):
    def stop(self, cwd, *, active=False, mode="enable", event="Stop", agent_id="", extra=()):
        payload = {
            "hook_event_name": event,
            "cwd": cwd,
            "session_id": "s1",
            "stop_hook_active": active,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        if mode == "disable":
            # disable を言えるのは起動した環境だけ（フラグの disable は enable に落ちる）。
            return self.ccnavi(
                *extra, stdin=json.dumps(payload), env={settings.MODE_ENV: "disable"}
            )
        return self.ccnavi("--mode", mode, *extra, stdin=json.dumps(payload))

    def body(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def child_tree(self, name="i0001-01"):
        return os.path.join(self.root, ".claude", "worktrees", name)

    def commit_work(self, tree, rel="src/a/work.py"):
        write(os.path.join(tree, *rel.split("/")), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "work")

    def assert_quiet(self, result):
        """促していない。止める鍵も、促しの文も無い。"""
        body = self.body(result)
        self.assertNotIn("decision", body)
        self.assertNotIn(CODE, result.stdout)

    # ---- 促す

    def test_a_clean_child_with_commits_is_asked_to_finish_once(self):
        """着手済み・未コミット無し・基準点より先にコミットあり → 1 回だけ止めて finish を促す。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        body = self.body(self.stop(tree))
        self.assertEqual(body.get("decision"), "block")
        reason = body["reason"]
        self.assertIn(f"[ccnavi] {CODE} (ticket: i0001-01)", reason)
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        self.assertIn(f"'{ticket_sh} finish i0001-01'", reason)
        self.assertIn("続ける理由", reason)
        self.assertIn("コミットが 1 件", reason)
        # 連鎖の 2 回目（Stop の hook が続けさせた後）は促さない。
        self.assert_quiet(self.stop(tree, active=True))
        # 閉じれば促さない。
        self.assertEqual(self.ccnavi("ticket", "finish", "i0001-01").returncode, 0)
        self.assert_quiet(self.stop(tree))

    def test_dry_run_does_not_block_but_tells_the_user(self):
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        body = self.body(self.stop(tree, mode="dry-run"))
        self.assertNotIn("decision", body)
        self.assertIn("would have blocked this stop", body.get("systemMessage", ""))
        self.assertIn(CODE, body.get("systemMessage", ""))

    # ---- 促さない（除く条件を 1 つずつ固定する）

    def test_uncommitted_changes_mean_the_work_is_not_done(self):
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        write(os.path.join(tree, "src", "a", "draft.py"), "y\n")  # 追跡していないファイルも数える
        self.assert_quiet(self.stop(tree))
        git(tree, "add", "-A")  # 索引にあるだけでも未コミット
        self.assert_quiet(self.stop(tree))

    def test_no_commit_beyond_the_base_point_means_nothing_was_done(self):
        self.family()
        self.assert_quiet(self.stop(self.child_tree()))

    def test_a_ticket_that_is_not_started_is_not_asked(self):
        tree = self.family_without_starting()
        self.commit_work(tree)
        self.assert_quiet(self.stop(tree))

    def test_a_parent_with_open_children_is_not_asked(self):
        """親は `finish` が通る形（close_problems が空）のときだけ。開いた子があれば促さない。"""
        self.family()
        self.commit_work(self.parent_tree, rel="src/parent.py")
        self.assert_quiet(self.stop(self.parent_tree))

    def test_a_parent_held_for_review_is_not_asked(self):
        """レビュー準備中の親は、既存の Stop の案内（利用者を待つ）と食い違わないよう促さない。"""
        self.family(review=(True, True))
        for child in ("i0001-01", "i0001-02"):
            self.assertEqual(self.ccnavi("ticket", "finish", child).returncode, 0)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "done")
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("フェーズ 1 が終わりました", self.reason(said))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "marks")
        self.assert_quiet(self.stop(self.parent_tree))

    def test_the_workspace_root_and_a_worktree_without_a_ticket_are_not_asked(self):
        self.family()
        self.assert_quiet(self.stop(self.root))
        loose = self.worktree("scratch", "main")
        self.commit_work(loose)
        self.assert_quiet(self.stop(loose))

    def test_a_blocked_ticket_is_not_asked(self):
        """信じられない承認済みチケット（親が閉じている）には終わりを勧めない。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        closed = os.path.join(self.approved, "done")
        os.makedirs(closed, exist_ok=True)
        os.replace(
            os.path.join(self.approved, "doing", "i0001.md"), os.path.join(closed, "i0001.md")
        )
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "closed by hand")
        self.assert_quiet(self.stop(tree))

    def test_ticket_control_disabled_is_not_asked(self):
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        self.assert_quiet(self.stop(tree, extra=("--ticket-control", "disable")))

    def test_mode_disable_is_not_asked(self):
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        self.assert_quiet(self.stop(tree, mode="disable"))

    def test_subagent_stop_is_not_the_place(self):
        """サブエージェントの終わりは別の手順（範囲外の差し戻し）で、促しは載せない。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        self.assert_quiet(self.stop(tree, event="SubagentStop", agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
