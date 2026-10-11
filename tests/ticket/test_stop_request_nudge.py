"""レビュー依頼までの道の途中で終えようとしたときの促しの受入テスト。道具を外から呼んで応答だけを見る。

止めるのは、承認済みの親に作業中の子が残っているか、子が全部閉じたのに（レビューを要する）フェーズの
依頼のマーカー（`requested`）が無いとき。同じ状態では 1 回だけ、進めばまた 1 回。
何を除くかをここで固定する。

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

CODE = "NUDGE_TICKET_REQUEST"
FINISH_CODE = "NUDGE_TICKET_FINISH"


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りたテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(RequestNudgeTest)):
        if name.startswith("test_"):
            suite.addTest(RequestNudgeTest(name))
    return suite


class RequestNudgeTest(base.StopNudgeTest):
    def assert_not_asked(self, result):
        """依頼までの道の促しを出していない。"""
        self.body(result)
        self.assertNotIn(CODE, result.stdout)

    def asked(self, result):
        """止めて、依頼までの道の促しを出した。理由の文を返す。"""
        body = self.body(result)
        self.assertEqual(body.get("decision"), "block", result.stdout)
        self.assertIn(f"[ccnavi] {CODE} (parent: i0001, phase: 1)", body["reason"])
        return body["reason"]

    def close_children(self, work=True):
        """子 2 本を閉じる。`work` なら先に子 1 にコミットを置く（親にまだ合流していない子）。"""
        if work:
            self.commit_work(self.child_tree())
        for child in ("i0001-01-01", "i0001-01-02"):
            self.assertEqual(self.ccnavi("ticket", "finish", child).returncode, 0)

    def mark_requested(self):
        write(
            os.path.join(self.approved, "phases", "i0001", "1.requested"),
            json.dumps({"head": "x", "mr": 1, "host": "github", "url": "u", "at": "2026-01-01"}),
        )

    # ---- 作業中の子が残っている

    def test_open_children_are_asked_to_finish_once_from_the_parent(self):
        self.family()
        reason = self.asked(self.stop(self.parent_tree))
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        self.assertIn("i0001-01-01, i0001-01-02", reason)
        self.assertIn(f"'{ticket_sh} finish i0001-01-01'", reason)
        self.assertIn(f"'{ticket_sh} finish i0001-01-02'", reason)
        # 前置きと同じ考え方の 3 点: 許可は要らない・打つコマンド・本当の問いは書き直す。
        self.assertIn("ユーザの許可は要りません", reason)
        self.assertIn("問いなら、その問いを最後に書き直して", reason)
        # 同じ状態では 1 回だけ。連鎖の 2 回目も止めない。
        self.assert_not_asked(self.stop(self.parent_tree))
        self.assert_not_asked(self.stop(self.parent_tree, active=True))
        # 別のセッションは自分の記録を持つ。
        self.asked(self.stop(self.parent_tree, session="s2"))

    def test_a_child_worktree_is_asked_about_its_own_child_only(self):
        """並行して動く別の子は別のセッションの作業。子のツリーでは自分の子だけを言う。"""
        self.family()
        reason = self.asked(self.stop(self.child_tree()))
        self.assertIn("子 i0001-01-01 が残っています", reason)
        self.assertNotIn("i0001-01-02", reason)

    def test_progress_asks_again_once(self):
        """子を 1 本閉じると状態が変わる。次に終えるときまた 1 回だけ止める。"""
        self.family()
        self.asked(self.stop(self.parent_tree))
        self.assert_not_asked(self.stop(self.parent_tree))
        self.assertEqual(self.ccnavi("ticket", "finish", "i0001-01-01").returncode, 0)
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("子 i0001-01-02 が残っています", reason)
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_a_child_that_is_not_started_is_not_asked(self):
        tree = self.family_without_starting()
        self.assert_not_asked(self.stop(tree))

    # ---- 子が全部閉じた。依頼まで 1 手ずつ

    def test_closed_children_are_asked_to_merge_then_eli5_then_request(self):
        self.family()
        self.close_children()
        review_sh = settings.script_command(self.root, "ccnavi-review.sh")
        git_sh = settings.script_command(self.root, "ccnavi-git.sh")
        # 子 1 のコミットが親に合流していない。
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("レビューの依頼（request）がまだです", reason)
        self.assertIn(f"'{git_sh} merge i0001-01-01'", reason)
        self.assertNotIn("i0001-01-02'", reason)
        self.assert_not_asked(self.stop(self.parent_tree))
        # 合流すると次の一手は ELI5。
        git(self.parent_tree, "merge", "--quiet", "--no-edit", "i0001-01-01")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("wip/eli5/phase-1.html", reason)
        self.assertNotIn(f"'{git_sh} merge", reason)
        self.assert_not_asked(self.stop(self.parent_tree))
        # ELI5 を置くと、次の一手は依頼。
        write(os.path.join(self.parent_tree, "wip", "eli5", "phase-1.html"), "<p>x</p>\n")
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "eli5")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn(
            f"'{review_sh} request --phase 1 --body-file <依頼文> --eli5 wip/eli5/phase-1.html'",
            reason,
        )
        self.assertNotIn("ELI5 の HTML を", reason)
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_a_requested_phase_is_not_asked(self):
        """依頼のマーカーがあれば、レビュー待ち以後。

        変更要求が残る間も `requested` は残るので、同じ扱いで止めない。
        """
        self.family()
        self.close_children(work=False)
        self.mark_requested()
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_a_phase_without_review_is_not_asked(self):
        """レビューが要らないフェーズ（省略）は、依頼を打たない。"""
        self.family(review=(False, False))
        self.close_children(work=False)
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_the_marker_dropped_by_a_new_child_is_asked_again(self):
        """子を足すと依頼のマーカーが外れる。足した子を閉じたあとの、依頼し直しの前も止める。"""
        self.family()
        self.close_children(work=False)
        self.mark_requested()
        self.assert_not_asked(self.stop(self.parent_tree))
        os.remove(os.path.join(self.approved, "phases", "i0001", "1.requested"))
        self.asked(self.stop(self.parent_tree))

    def test_the_parent_closed_early_is_not_asked(self):
        self.family()
        self.close_children(work=False)
        write(
            os.path.join(self.approved, "phases", "i0001", "close-early.json"),
            json.dumps({"at": "2026-01-01"}),
        )
        self.assert_not_asked(self.stop(self.parent_tree))

    # ---- 止めない回

    def test_the_workspace_root_and_a_worktree_without_a_ticket_are_not_asked(self):
        self.family()
        self.assert_not_asked(self.stop(self.root))
        self.assert_not_asked(self.stop(self.worktree("scratch", "main")))

    def test_subagent_stop_is_not_the_place(self):
        self.family()
        self.assert_not_asked(self.stop(self.parent_tree, event="SubagentStop", agent_id="a1"))
        self.assert_not_asked(self.stop(self.parent_tree, agent_id="a1"))
        # 見送った回は数えない。メインの終わりでは止める。
        self.asked(self.stop(self.parent_tree))

    def test_ticket_control_disabled_and_mode_disable_are_not_asked(self):
        self.family()
        self.assert_not_asked(self.stop(self.parent_tree, extra=("--ticket-control", "disable")))
        self.assert_not_asked(self.stop(self.parent_tree, mode="disable"))

    def test_a_state_directory_that_cannot_be_written_means_no_nudge(self):
        """記録を残せないまま止めると、終えるたびに止まる。止めない側を採る。"""
        self.family()
        shutil.rmtree(self.state, ignore_errors=True)
        write(self.state, "ファイルで塞ぐ\n")
        for _ in range(2):
            result = self.stop(self.parent_tree)
            self.assert_not_asked(result)
            self.assertIn("促さない", result.stderr)

    def test_dry_run_blocks_too(self):
        self.family()
        body = self.body(self.stop(self.parent_tree, mode="dry-run"))
        self.assertEqual(body.get("decision"), "block")
        self.assertIn(CODE, body["reason"])
        self.assertNotIn("would have blocked", json.dumps(body, ensure_ascii=False))

    # ---- 1 回の Stop で止める理由は 1 つ

    def test_the_finish_nudge_goes_first_and_this_one_does_not_count(self):
        """finish の促しで止める回は、この促しを出さず、記録もしない。次の回に出る。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        first = self.body(self.stop(tree))
        self.assertIn(FINISH_CODE, first["reason"])
        self.assertNotIn(CODE, first["reason"])
        second = self.body(self.stop(tree))
        self.assertEqual(second.get("decision"), "block")
        self.assertIn(CODE, second["reason"])
        self.assertNotIn(FINISH_CODE, second["reason"])
        self.assert_not_asked(self.stop(tree))

    def test_this_nudge_goes_before_the_stop_rule_and_the_rule_is_not_counted(self):
        with open(self.rules, encoding="utf-8") as f:
            body = json.load(f)
        rule = {"id": "review", "match": "Stop", "glob": "*", "every": 2}
        body["allow"] = [*body.get("allow", []), {**rule, "additionalContext": "振り返る"}]
        write(self.rules, json.dumps(body))
        self.family()
        first = self.body(self.stop(self.parent_tree))
        self.assertIn(CODE, first["reason"])
        self.assertNotIn("NUDGE_STOP_RULE", first["reason"])
        # ここからルールを数える（1 回目は止めない、2 回目で渡す）。
        self.assert_not_asked(self.stop(self.parent_tree))
        second = self.body(self.stop(self.parent_tree))
        self.assertTrue(second["reason"].startswith("NUDGE_STOP_RULE: "), second)
        self.assertNotIn(CODE, second["reason"])

    def test_the_nudge_is_recorded_apart_from_deny(self):
        self.family()
        self.stop(self.parent_tree)
        log = os.path.join(self.root, "logs", "decisions.jsonl")
        with open(log, encoding="utf-8") as f:
            line = json.loads(f.read().splitlines()[-1])
        self.assertEqual((line["decision"], line["code"], line["enforced"]), ("nudge", CODE, True))


if __name__ == "__main__":
    unittest.main()
