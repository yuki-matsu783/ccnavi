"""直接作業が `integrate` まで進んでいないときの Stop の促しの受入テスト。
道具を外から呼んで応答だけを見る。

促すのは、チケットに結び付かない `.claude/worktrees/<名前>` で、
未コミットの変更か統合先に入っていないコミットがあり、
`integrate <名前>` の記録が無いとき。同じ状態では 1 回だけ。
統合の前提を満たせないときは止めずに理由だけを言う。何を除くかをここで固定する。

道具は `finish` の促しの受入テスト（test_stop_nudge.StopNudgeTest）のものを借りる。
"""

from __future__ import annotations

import json
import os
import shutil
import unittest

from ccnavi.infra import settings
from tests.ticket import test_stop_nudge as base
from tests.ticket.test_ticket import git, write

CODE = "NUDGE_INTEGRATE"


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りたテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(IntegrateNudgeTest)):
        if name.startswith("test_"):
            suite.addTest(IntegrateNudgeTest(name))
    return suite


class IntegrateNudgeTest(base.StopNudgeTest):
    def setUp(self):
        super().setUp()
        # ccnavi 自身の置き場（設定・記録・state）は、本番ではリポジトリが無視している。
        # 統合先のツリーを汚すものは、テストが置く src/ の下のファイルだけにする。
        exclude = git(self.root, "rev-parse", "--git-path", "info/exclude").strip()
        write(os.path.join(self.root, exclude), "/logs/\n/.ccnavi/config/\n")

    def stop(
        self, cwd, *, active=False, event="Stop", agent_id="", integration="main", session="s1"
    ):
        payload = {
            "hook_event_name": event,
            "cwd": cwd,
            "session_id": session,
            "stop_hook_active": active,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        env = {settings.INTEGRATION_ENV: integration} if integration else {}
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload), env=env)

    def direct(self, name="scratch", commit=False, dirty=False):
        """チケットに結び付かない直接作業のワークツリー。"""
        path = self.worktree(name, "main")
        if commit:
            self.commit_work(path)
        if dirty:
            write(os.path.join(path, "src", "draft.py"), "y\n")
        return path

    def asked(self, result):
        body = self.body(result)
        self.assertEqual(body.get("decision"), "block", result.stdout)
        self.assertIn(f"[ccnavi] {CODE} (worktree: scratch)", body["reason"])
        return body["reason"]

    def assert_not_asked(self, result):
        body = self.body(result)
        self.assertNotIn("decision", body, result.stdout)
        self.assertNotIn(CODE, result.stdout)
        return body

    # ---- 促す

    def test_commits_not_in_the_integration_branch_are_asked_once(self):
        path = self.direct(commit=True)
        reason = self.asked(self.stop(path))
        git_sh = settings.script_command(self.root, "ccnavi-git.sh")
        self.assertIn(f"'{git_sh} integrate scratch'", reason)
        self.assertIn(f"'{git_sh} integrate-done scratch -m <メッセージ>'", reason)
        self.assertIn(f"'{git_sh} integrate --abort scratch'", reason)
        self.assertIn("staged", reason)
        self.assertIn("コミット 1 件", reason)
        self.assertIn(os.path.basename(self.root), reason)
        # 同じ状態では 1 回だけ。連鎖の 2 回目も止めない。
        self.assert_not_asked(self.stop(path))
        self.assert_not_asked(self.stop(path, active=True))
        # 進めば（コミットを足せば）また 1 回。
        self.commit_work(path, rel="src/more.py")
        self.assertIn("コミット 2 件", self.asked(self.stop(path)))

    def test_uncommitted_changes_are_asked_and_told_to_commit_first(self):
        path = self.direct(dirty=True)
        reason = self.asked(self.stop(path))
        self.assertIn("未コミットの変更", reason)
        self.assertIn("先にワークツリーの中でコミット", reason)

    def test_the_record_stops_the_hook_from_doing_anything(self):
        """判定の材料は記録だけ。ここは integrate を打たない（統合先のツリーは変わらない）。"""
        path = self.direct(commit=True)
        before = git(self.root, "status", "--porcelain")
        self.asked(self.stop(path))
        self.assertEqual(git(self.root, "status", "--porcelain"), before)
        self.assertEqual(git(self.root, "branch", "--list", "scratch").strip(), "+ scratch")

    # ---- 促さない

    def test_a_clean_worktree_with_nothing_new_is_not_asked(self):
        self.assert_not_asked(self.stop(self.direct()))

    def test_an_integrate_record_means_it_already_went_ahead(self):
        path = self.direct(commit=True)
        for sub in ("ccnavi-integrate/scratch/marker", "ccnavi-integrate-done/scratch"):
            top = sub.split("/")[0]
            gitdir = os.path.join(self.root, git(self.root, "rev-parse", "--git-path", top).strip())
            write(os.path.join(gitdir, *sub.split("/")[1:]), "state staged\n")
            self.assert_not_asked(self.stop(path, session=sub))
            shutil.rmtree(gitdir, ignore_errors=True)
        self.asked(self.stop(path, session="after"))

    def test_a_worktree_bound_to_a_ticket_is_not_asked(self):
        """チケット（承認済み・提案）の名前のワークツリーは、マージリクエストで出す。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        write(os.path.join(tree, "src", "a", "draft.py"), "y\n")
        body = self.body(self.stop(tree))
        self.assertNotIn(CODE, result_text(body))
        # 提案の段階でも結び付く。
        path = self.direct(commit=True)
        write(os.path.join(self.root, "wip", "proposals", "todo", "scratch.md"), "---\n---\n")
        self.assert_not_asked(self.stop(path))

    def test_a_worktree_with_a_family_record_is_not_asked(self):
        path = self.direct(commit=True)
        record = os.path.join(self.state, "sync", "self", "families", "scratch")
        write(record, "state merged\nbranch scratch\n")
        self.assert_not_asked(self.stop(path))

    def test_a_branch_that_is_not_the_name_is_not_asked(self):
        path = self.direct(commit=True)
        git(path, "switch", "--quiet", "-c", "other")
        self.assert_not_asked(self.stop(path))

    def test_the_workspace_root_is_not_asked(self):
        self.assert_not_asked(self.stop(self.root))

    def test_stop_hook_active_subagent_and_ticket_control_are_not_asked(self):
        path = self.direct(commit=True)
        self.assert_not_asked(self.stop(path, active=True))
        self.assert_not_asked(self.stop(path, event="SubagentStop", agent_id="a1"))
        payload = json.dumps({"hook_event_name": "Stop", "cwd": path, "session_id": "s9"})
        off = self.ccnavi(
            "--mode",
            "enable",
            "--ticket-control",
            "disable",
            stdin=payload,
            env={settings.INTEGRATION_ENV: "main"},
        )
        self.assert_not_asked(off)

    def test_a_running_subagent_defers_the_nudge_without_using_it_up(self):
        from ccnavi.hook import subagent

        path = self.direct(commit=True)
        start = {
            "hook_event_name": "SubagentStart",
            "cwd": path,
            "session_id": "s1",
            "agent_id": "a1",
            "agent_type": "general-purpose",
        }
        self.assertEqual(self.ccnavi("--mode", "enable", stdin=json.dumps(start)).returncode, 0)
        self.assertTrue(subagent.running(self.state, "s1"))
        self.assert_not_asked(self.stop(path))
        stop = dict(start, hook_event_name="SubagentStop")
        self.assertEqual(self.ccnavi("--mode", "enable", stdin=json.dumps(stop)).returncode, 0)
        self.asked(self.stop(path))

    # ---- 順序

    def test_the_finish_nudge_comes_first(self):
        """finish の促しで止める回は、直接作業の促しを出さず、数えもしない。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        loose = self.direct(commit=True)
        self.assertIn(base.CODE, self.body(self.stop(tree))["reason"])
        self.asked(self.stop(loose))

    # ---- 統合の前提を満たせない

    def test_a_dirty_integration_tree_is_reported_once_without_stopping(self):
        path = self.direct(commit=True)
        write(os.path.join(self.root, "src", "someone.py"), "z\n")
        body = self.assert_not_asked(self.stop(path))
        message = body["systemMessage"]
        self.assertIn("統合先のツリー", message)
        self.assertIn("未コミットの変更", message)
        self.assertIn("src/someone.py", message)
        # 同じ状態では 1 回だけ言う。
        self.assertNotIn("systemMessage", self.assert_not_asked(self.stop(path)))
        # 綺麗にして状態が進めば、促す。
        os.remove(os.path.join(self.root, "src", "someone.py"))
        self.commit_work(path, rel="src/more.py")
        self.asked(self.stop(path))

    def test_no_integration_branch_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        body = self.assert_not_asked(self.stop(path, integration=""))
        self.assertIn("統合先の名前が決まりません", body["systemMessage"])

    def test_a_busy_integration_tree_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        gitdir = git(self.root, "rev-parse", "--git-path", "SQUASH_MSG").strip()
        write(os.path.join(self.root, gitdir), "squash\n")
        body = self.assert_not_asked(self.stop(path))
        self.assertIn("SQUASH_MSG", body["systemMessage"])

    def test_a_missing_integration_ref_with_uncommitted_changes_is_reported_once(self):
        path = self.direct(dirty=True)
        body = self.assert_not_asked(self.stop(path, integration="nonexistent"))
        self.assertIn("nonexistent", body["systemMessage"])
        self.assertIn("ref", body["systemMessage"])
        self.assertNotIn(
            "systemMessage", self.assert_not_asked(self.stop(path, integration="nonexistent"))
        )

    def test_a_missing_integration_ref_with_only_commits_stays_quiet(self):
        """コミットだけでは、統合先に入っているかを数えられない。誤って促さず黙る。"""
        path = self.direct(commit=True)
        body = self.assert_not_asked(self.stop(path, integration="nonexistent"))
        self.assertNotIn("systemMessage", body)

    # ---- integrate が断る名前

    def test_names_that_integrate_refuses_are_not_asked(self):
        for name in ("release-x", "master", "develop", "release"):
            path = self.direct(name, commit=True)
            write(os.path.join(path, "src", "draft.py"), "y\n")
            result = self.stop(path, session=name)
            self.assertNotIn(CODE, result.stdout, name)
            self.assertNotIn("decision", self.body(result), name)


def result_text(body):
    return json.dumps(body, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
