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
        self.point_origin_head(integration)
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def point_origin_head(self, integration):
        """統合先は手元の origin/HEAD で決まる。空なら origin の無いリポジトリにする。"""
        remotes = git(self.root, "remote").split()
        if not integration:
            if "origin" in remotes:
                git(self.root, "remote", "remove", "origin")
            return
        if "origin" not in remotes:
            git(self.root, "remote", "add", "origin", self.root + ".origin.git")
        git(
            self.root,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            f"refs/remotes/origin/{integration}",
        )

    def direct(self, name="scratch", commit=False, dirty=False):
        """チケットに結び付かない直接作業のワークツリー。"""
        path = self.worktree(name, "main")
        if commit:
            self.commit_work(path)
        if dirty:
            write(os.path.join(path, "src", "draft.py"), "y\n")
        return path

    def record(self, name="scratch", base="main", snap=None, **extra):
        """`ccnavi-worktree.sh` が置く直接作業の記録（version 4）。起点 S は既定で main の先頭。"""
        common = os.path.realpath(
            os.path.join(self.root, git(self.root, "rev-parse", "--git-common-dir").strip())
        )
        snap = snap or git(self.root, "rev-parse", base).strip()
        fields = {
            "version": "4",
            "base_branch": base,
            "base_sha": snap,
            "snap_sha": snap,
            "snap_wip": "no",
            "side_by_side": "no",
            "main_tree": os.path.realpath(self.root),
            "common_dir": common,
            "confirm": "-",
            "created": "1",
        }
        fields.update(extra)
        text = "".join(f"{k} {v}\n" for k, v in fields.items() if v is not None)
        write(os.path.join(common, "ccnavi-direct", name), text)
        return common

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

    def test_commits_not_in_the_base_branch_are_asked_once(self):
        path = self.direct(commit=True)
        self.record()
        reason = self.asked(self.stop(path))
        git_sh = settings.script_command(self.root, "ccnavi-git.sh")
        cd = f"cd {settings.shell_path(self.root)} && {git_sh}"
        self.assertIn(f"`{cd} integrate scratch`", reason)
        self.assertIn(f"`{cd} integrate-done scratch -m '<メッセージ>'`", reason)
        self.assertIn(f"`{cd} integrate --abort scratch`", reason)
        self.assertIn("一時ワークツリー", reason)
        self.assertIn("取り込み先（main）に入っていないコミット 1 件", reason)
        self.assertNotIn("統合先", reason)
        self.assertIn(os.path.basename(self.root), reason)
        # 同じ状態では 1 回だけ。連鎖の 2 回目も止めない。
        self.assert_not_asked(self.stop(path))
        self.assert_not_asked(self.stop(path, active=True))
        # 進めば（コミットを足せば）また 1 回。
        self.commit_work(path, rel="src/more.py")
        self.assertIn("コミット 2 件", self.asked(self.stop(path)))

    def test_commits_are_counted_from_the_recorded_start_point(self):
        """入っていないコミットは S..先頭で数える。S より前のコミットは数えない。"""
        path = self.direct(commit=True)
        start = git(path, "rev-parse", "HEAD").strip()
        self.record(snap=start)
        self.assert_not_asked(self.stop(path))
        self.commit_work(path, rel="src/more.py")
        self.assertIn("コミット 1 件", self.asked(self.stop(path, session="s2")))

    def test_a_workspace_with_uncommitted_changes_in_the_main_checkout_does_not_stop_the_nudge(
        self,
    ):
        path = self.direct(commit=True)
        self.record()
        write(os.path.join(self.root, "src", "someone.py"), "z\n")
        self.asked(self.stop(path))

    def test_a_switched_base_branch_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        self.record()
        git(self.root, "switch", "--quiet", "-c", "other")
        body = self.assert_not_asked(self.stop(path))
        self.assertIn("取り込み先が切り替わっています", body["systemMessage"])
        self.assertIn("other", body["systemMessage"])

    def test_a_mid_operation_in_the_main_checkout_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        self.record()
        gitdir = git(self.root, "rev-parse", "--git-path", "MERGE_HEAD").strip()
        write(os.path.join(self.root, gitdir), "0" * 40 + "\n")
        body = self.assert_not_asked(self.stop(path))
        self.assertIn("MERGE_HEAD", body["systemMessage"])

    def test_a_record_from_another_repository_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        self.record(common_dir=os.path.join(os.path.dirname(self.root), "elsewhere", ".git"))
        body = self.assert_not_asked(self.stop(path))
        self.assertIn("共通ディレクトリ", body["systemMessage"])

    def test_a_broken_record_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        self.record(snap_sha=None)
        body = self.assert_not_asked(self.stop(path))
        self.assertIn("壊れて", body["systemMessage"])

    def test_no_record_points_to_adopt(self):
        path = self.direct(commit=True)
        reason = self.asked(self.stop(path))
        self.assertIn("--adopt --base main", reason)
        self.assertIn("ccnavi-worktree.sh scratch", reason)

    def test_an_origin_ahead_of_the_base_branch_is_warned(self):
        path = self.direct(commit=True)
        self.record()
        ahead = git(path, "rev-parse", "HEAD").strip()
        git(self.root, "update-ref", "refs/remotes/origin/main", ahead)
        reason = self.asked(self.stop(path))
        self.assertIn("origin/main が手元の main より 1 件先に進んでいます", reason)

    def test_uncommitted_changes_are_asked_and_told_to_commit_first(self):
        path = self.direct(dirty=True)
        reason = self.asked(self.stop(path))
        self.assertIn("未コミットの変更", reason)
        self.assertIn("先にコミットしてください", reason)
        work = settings.shell_path(path)
        git_sh = settings.script_command(self.root, "ccnavi-git.sh")
        self.assertIn(f"`cd {work} && {git_sh} commit -m '<メッセージ>'`", reason)

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

    def test_a_marker_asks_for_integrate_done_or_abort(self):
        path = self.direct(commit=True)
        self.record()
        gitdir = os.path.join(
            self.root, git(self.root, "rev-parse", "--git-path", "ccnavi-integrate").strip()
        )
        write(os.path.join(gitdir, "scratch", "marker"), "version 4\nhead x\ntip y\nc_tree z\n")
        reason = self.asked(self.stop(path))
        self.assertIn("integrate-done scratch", reason)
        self.assertIn("integrate --abort scratch", reason)
        self.assertIn("保存していない編集", reason)
        self.assert_not_asked(self.stop(path))

    def test_a_marker_of_unknown_version_asks_the_user_without_commands(self):
        path = self.direct(commit=True)
        gitdir = os.path.join(
            self.root, git(self.root, "rev-parse", "--git-path", "ccnavi-integrate").strip()
        )
        write(os.path.join(gitdir, "scratch", "marker"), "state staged\nbranch_sha x\n")
        reason = self.asked(self.stop(path))
        self.assertIn("版を知りません", reason)
        self.assertIn("git reset --merge", reason)
        self.assertNotIn("integrate-done scratch", reason)

    def test_a_done_copy_with_the_same_head_means_it_already_went_ahead(self):
        path = self.direct(commit=True)
        head = git(path, "rev-parse", "HEAD").strip()
        gitdir = os.path.join(
            self.root, git(self.root, "rev-parse", "--git-path", "ccnavi-integrate-done").strip()
        )
        write(os.path.join(gitdir, "scratch"), f"state staged\nbranch_sha {head}\n")
        self.assert_not_asked(self.stop(path, session="done"))
        shutil.rmtree(gitdir, ignore_errors=True)
        self.asked(self.stop(path, session="after"))

    def test_a_done_copy_does_not_silence_a_reused_name(self):
        """名前を使い直したときの 3 通り。

        新しいコミット・同じ HEAD で未コミット・同じ HEAD で変更なし。
        """
        path = self.direct(commit=True)
        head = git(path, "rev-parse", "HEAD").strip()
        gitdir = os.path.join(
            self.root, git(self.root, "rev-parse", "--git-path", "ccnavi-integrate-done").strip()
        )
        copy = os.path.join(gitdir, "scratch")
        write(copy, f"state staged\nbranch_sha {head}\n")
        self.assert_not_asked(self.stop(path, session="same"))
        write(os.path.join(path, "src", "draft.py"), "y\n")
        self.asked(self.stop(path, session="dirty"))
        os.remove(os.path.join(path, "src", "draft.py"))
        self.commit_work(path, rel="src/more.py")
        self.asked(self.stop(path, session="newer"))

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

    def test_a_worktree_whose_branch_is_on_origin_is_not_asked(self):
        """origin に在るブランチのワークツリーは、`integrate` が断る（親のブランチとみなす）ので、
        促さない。促したのに `integrate` が断る食い違いを作らない。"""
        path = self.direct(commit=True)
        self.asked(self.stop(path))
        git(self.root, "update-ref", "refs/remotes/origin/scratch", "scratch")
        self.assert_not_asked(self.stop(path, session="s2"))

    def test_a_pushed_direct_worktree_with_a_record_is_still_asked(self):
        """直接作業のブランチは `ccnavi-git.sh push` で origin へ送れる。記録（ccnavi-direct）が
        あるワークツリーは、origin にあっても親とみなさず、促しを止めない。"""
        path = self.direct(commit=True)
        self.record()
        git(self.root, "update-ref", "refs/remotes/origin/scratch", "scratch")
        self.asked(self.stop(path))

    def test_a_branch_that_is_not_the_name_is_not_asked(self):
        path = self.direct(commit=True)
        git(path, "switch", "--quiet", "-c", "other")
        self.assert_not_asked(self.stop(path))

    def test_the_workspace_root_is_not_asked(self):
        self.assert_not_asked(self.stop(self.root))

    def test_stop_hook_active_and_subagent_are_not_asked_but_ticket_control_does_not_matter(self):
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
        )
        # チケット制御が無効でも、直接作業はチケットに結び付かないので促す。
        self.asked(off)

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

    def test_no_integration_branch_is_reported_without_stopping(self):
        path = self.direct(commit=True)
        body = self.assert_not_asked(self.stop(path, integration=""))
        self.assertIn("origin が無いので統合先が決まらない", body["systemMessage"])

    def test_an_origin_without_head_asks_for_set_head(self):
        path = self.direct(commit=True)
        git(self.root, "remote", "add", "origin", self.root + ".origin.git")
        body = self.assert_not_asked(self.ccnavi("--mode", "enable", stdin=self.payload(path)))
        self.assertIn("git remote set-head origin -a", body["systemMessage"])

    def payload(self, cwd, session="s1"):
        return json.dumps({"hook_event_name": "Stop", "cwd": cwd, "session_id": session})

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
