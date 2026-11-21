"""直接作業が `integrate` まで進んでいないときの Stop の促しの受入テスト。
道具を外から呼んで応答だけを見る。

促すのは、チケットに結び付かない `.claude/worktrees/<名前>` で、
統合先に入っていないコミットがあり、`integrate <名前>` の記録が無いとき。同じ状態では 1 回だけ。
未コミットの変更だけでは促さない。版 4 のmarkerがある取り込みの途中も促さない。
統合の前提を満たせないときは止めずに理由だけを言う。

起点 S からの数え方・途中の操作・別のリポジトリの記録・origin の有無・integrate が断る名前は、
go/internal/tickets/lifecycle/integrate の at_stop_test.go が場面ごとに見る。
ここは hook を通した次の経路だけ。

道具は `finish` の促しの受入テスト（test_stop_nudge.StopNudgeTest）のものを借りる。
"""

from __future__ import annotations

import json
import os
import unittest

from tests import script_command, shell_path
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

    def stop(self, cwd, *, active=False, event="Stop", agent_id="", session="s1"):
        payload = {
            "hook_event_name": event,
            "cwd": cwd,
            "session_id": session,
            "stop_hook_active": active,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        self.point_origin_head()
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def point_origin_head(self):
        """統合先は手元の origin/HEAD で決まる。origin が無ければ足して main に向ける。"""
        remotes = git(self.root, "remote").split()
        if "origin" not in remotes:
            git(self.root, "remote", "add", "origin", self.root + ".origin.git")
        git(
            self.root,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        )

    def direct(self, name="scratch", commit=False):
        """チケットに結び付かない直接作業のワークツリー。"""
        path = self.worktree(name, "main")
        if commit:
            self.commit_work(path)
        return path

    def record(self, name="scratch", base="main"):
        """`ccnavi-worktree.sh` が置く直接作業の記録（version 4）。起点 S は既定で main の先頭。"""
        common = os.path.realpath(
            os.path.join(self.root, git(self.root, "rev-parse", "--git-common-dir").strip())
        )
        snap = git(self.root, "rev-parse", base).strip()
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
        git_sh = script_command(self.root, "ccnavi-git.sh")
        cd = f"cd {shell_path(self.root)} && {git_sh}"
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

    def test_the_record_stops_the_hook_from_doing_anything(self):
        """判定の材料は記録だけ。ここは integrate を打たない（統合先のツリーは変わらない）。"""
        path = self.direct(commit=True)
        before = git(self.root, "status", "--porcelain")
        self.asked(self.stop(path))
        self.assertEqual(git(self.root, "status", "--porcelain"), before)
        self.assertEqual(git(self.root, "branch", "--list", "scratch").strip(), "+ scratch")

    # ---- 促さない

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
        path = self.direct(commit=True)
        start = {
            "hook_event_name": "SubagentStart",
            "cwd": path,
            "session_id": "s1",
            "agent_id": "a1",
            "agent_type": "general-purpose",
        }
        self.assertEqual(self.ccnavi("--mode", "enable", stdin=json.dumps(start)).returncode, 0)
        self.assertTrue(
            any(
                n.startswith("subagent-s1-") and n.endswith(".running")
                for n in os.listdir(self.state)
            )
        )
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


if __name__ == "__main__":
    unittest.main()
