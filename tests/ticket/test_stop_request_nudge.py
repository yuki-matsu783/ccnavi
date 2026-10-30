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
import time
import unittest
from unittest import mock

from ccnavi.hook import subagent
from ccnavi.infra import settings
from ccnavi.tickets.approval import store as approval
from ccnavi.tickets.lifecycle import stop as ops_stop
from ccnavi.tickets.lifecycle import stop_request as ops_stop_request
from tests.ticket import test_stop_nudge as base
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, record_merge

CODE = "NUDGE_TICKET_REQUEST"
FINISH_CODE = "NUDGE_TICKET_FINISH"


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りたテストは走らせない）。"""
    suite = unittest.TestSuite()
    for cls in (RequestNudgeTest, PlannedRequestNudgeTest):
        for name in sorted(vars(cls)):
            if name.startswith("test_"):
                suite.addTest(cls(name))
    return suite


class RequestNudgeTest(base.StopNudgeTest):
    def assert_not_asked(self, result):
        """依頼までの道の促しを出していない。ほかの理由でも止めていない（`decision` が無い）。"""
        body = self.body(result)
        self.assertNotIn("decision", body, result.stdout)
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
        self.assertIn(f"`{ticket_sh} finish i0001-01-01`", reason)
        self.assertIn(f"`{ticket_sh} finish i0001-01-02`", reason)
        # 前置きと同じ考え方の 3 点: 許可は要らない・打つコマンド・本当の問いは書き直す。
        self.assertIn("ユーザの許可は要りません", reason)
        self.assertIn("問いがある場合は、その問いを最後に書き直して", reason)
        self.assertIn("このまま進めずにターンを終えてください", reason)
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
        self.family(plan=["work-mr"])
        self.close_children()
        review_sh = settings.script_command(self.root, "ccnavi-review.sh")
        git_sh = settings.script_command(self.root, "ccnavi-git.sh")
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        # 子 1 は親に合流した記録が無い（合流は finish が済ませる。ここは確かめを促すだけ）。
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("レビューの依頼（review-request）がまだです", reason)
        self.assertIn("子 i0001-01-01 が親のブランチに合流済みと確かめられません", reason)
        self.assertIn(f"`{ticket_sh} status i0001`", reason)
        self.assertNotIn(f"`{git_sh} merge", reason)
        self.assertNotIn("i0001-01-02 が", reason)
        self.assert_not_asked(self.stop(self.parent_tree))
        # 合流の記録が揃うと次の一手は ELI5。
        git(self.parent_tree, "merge", "--quiet", "--no-edit", "i0001-01-01")
        record_merge(self.parent_tree, "i0001-01-01")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("wip/eli5/phase-1.html", reason)
        self.assertNotIn(f"`{git_sh} merge", reason)
        self.assert_not_asked(self.stop(self.parent_tree))
        # ELI5 を置くと、次の一手は依頼。
        write(os.path.join(self.parent_tree, "wip", "eli5", "phase-1.html"), "<p>x</p>\n")
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "eli5")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn(
            f"`{review_sh} review-request --phase 1 --body-file <依頼文> "
            "--eli5 wip/eli5/phase-1.html`",
            reason,
        )
        self.assertNotIn("ELI5 の HTML を", reason)
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_a_fix_stacked_on_the_child_after_finish_is_asked_before_eli5(self):
        """finish の後に子のブランチへ積んだコミットは親に無い。依頼の前提と同じく、それを言う。"""
        self.family(plan=["work-mr"])
        self.close_children()
        git(self.parent_tree, "merge", "--quiet", "--no-edit", "i0001-01-01")
        record_merge(self.parent_tree, "i0001-01-01")
        self.assertIn("wip/eli5/phase-1.html", self.asked(self.stop(self.parent_tree)))
        # レビュー待ちの子のワークツリーで、合流の後に修正を積む。
        self.commit_work(self.child_tree(), rel="src/a/late.py")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("子 i0001-01-01 の手元のブランチに、合流されていない修正があります", reason)
        self.assertIn("同じフェーズに子を足して", reason)
        self.assertNotIn("ELI5 の HTML を", reason)

    def test_the_request_step_reminds_to_commit_and_push_first(self):
        """ELI5 が在っても未コミットかもしれない。`request` は HEAD の普通のファイルが前提。"""
        self.family(plan=["work-mr"])
        self.close_children(work=False)
        write(os.path.join(self.parent_tree, "wip", "eli5", "phase-1.html"), "<p>x</p>\n")
        reason = self.asked(self.stop(self.parent_tree))
        self.assertIn("review-request --phase 1", reason)
        self.assertIn("未コミットの変更があれば先にコミットして push", reason)

    def test_a_blocked_parent_is_not_asked(self):
        """信頼できない承認済みの親（手で書き換えた `workflow:`）には促さない。"""
        self.family()
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        forged = "workflow: {order: dag, waits: {}, review_at: {}}"
        write(path, text.replace("\n---\n", "\n" + forged + "\n---\n", 1))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "forge")
        # 手で書き換えた親は信頼できない（判定が範囲を使わずに止める）。
        blocked = self.hook("PreToolUse", "Write", self.parent_tree, file_path="src/x.py")
        self.assertIn("DENY_TICKET_BLOCKED", self.reason(blocked))
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_only_the_parent_of_the_cwd_is_looked_at(self):
        """複数の親が居ても、cwd のツリーに結び付いた親だけを見る。"""
        self.family()
        other = self.worktree("i0002", "main")
        self.propose("i0002", tree=other, allow=("src/*", "wip/*"))
        self.propose("i0002-01-01", tree=other, parent="i0002", phase=1, allow=("src/c/*",))
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "second family")
        self.assertEqual(self.approve().returncode, 0)
        # 親 i0002 のツリーの承認済みチケットは、まだ誰も着手していない。
        # 結び付かないツリーは何も言われない。
        self.assert_not_asked(self.stop(other))
        reason = self.asked(self.stop(self.parent_tree))
        self.assertNotIn("i0002", reason)

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
        # 全部のフェーズが済んでいるので、次はフィードバック計画（test_stop_feedback_nudge.py）。
        # 依頼の促しは出ない。
        body = self.body(self.stop(self.parent_tree))
        self.assertNotIn(CODE, json.dumps(body, ensure_ascii=False))
        self.assertIn("NUDGE_TICKET_FEEDBACK", body.get("reason", ""))

    def test_the_marker_dropped_by_a_new_child_is_asked_again(self):
        """子を足すと依頼のマーカーが外れる。足した子を閉じたあとの、依頼し直しの前も止める。"""
        self.family(plan=["work-mr"])
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

    def test_a_parent_whose_worktree_is_not_settled_is_left_out_and_the_rule_still_runs(self):
        """親のワークツリーが決まらない親は促さない。Stop のルールは続けて動く。"""
        with open(self.rules, encoding="utf-8") as f:
            body = json.load(f)
        rule = {"id": "review", "match": "Stop", "glob": "*", "every": 2}
        body["allow"] = [*body.get("allow", []), {**rule, "additionalContext": "振り返る"}]
        write(self.rules, json.dumps(body))
        self.family()
        self.close_children()
        # 親のワークツリーの HEAD がブランチ名と合わず、親のワークツリーが決まらない形にする。
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        self.assert_not_asked(self.stop(self.root))  # ルールの 1 回目（渡さない回）
        result = self.stop(self.parent_tree)
        self.assertNotIn("internal-error", result.stdout)
        self.assertNotIn(CODE, result.stdout)
        self.assertIn("NUDGE_STOP_RULE", result.stdout)

    def test_a_parent_worktree_that_cannot_be_settled_means_no_nudge(self):
        """置き場が決まらない（`NoParentWorktree`）親は、例外を出さずに促しの対象から外す。"""
        self.family()
        self.close_children()
        conf, _ = settings.load(self.root)
        self.assertIsNotNone(ops_stop_request.pending_at_stop(self.root, conf, self.parent_tree))
        with mock.patch.object(
            approval, "home_dir", side_effect=approval.NoParentWorktree("決まらない")
        ):
            self.assertIsNone(ops_stop_request.pending_at_stop(self.root, conf, self.parent_tree))

    def test_a_writer_without_a_settled_parent_worktree_stops_with_one_line(self):
        """書く側は、置き場が決まらなければ traceback ではなく 1 行の理由で止まる（終了 1）。"""
        self.family()
        self.commit_work(self.child_tree())
        with mock.patch.object(
            approval, "home_dir", side_effect=approval.NoParentWorktree("決まらない")
        ):
            result = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("ccnavi: 親のワークツリーが決まらないので止めた: 決まらない。", result.stderr)

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

    def test_a_record_that_cannot_be_written_means_no_nudge(self):
        """記録を残せないまま止めると、終えるたびに止まる。止めない側を採る。"""
        self.family()
        os.makedirs(
            ops_stop.nudge_path(self.state, "s1")
        )  # 記録のファイルの場所をディレクトリで塞ぐ
        for _ in range(2):
            result = self.stop(self.parent_tree)
            self.assert_not_asked(result)
            self.assertIn("促さない", result.stderr)

    def test_a_state_directory_that_cannot_be_listed_means_no_nudge(self):
        self.family()
        shutil.rmtree(self.state, ignore_errors=True)
        write(self.state, "ファイルで塞ぐ\n")
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_dry_run_blocks_too(self):
        self.family()
        body = self.body(self.stop(self.parent_tree, mode="dry-run"))
        self.assertEqual(body.get("decision"), "block")
        self.assertIn(CODE, body["reason"])
        self.assertNotIn("would have blocked", json.dumps(body, ensure_ascii=False))

    # ---- 実行中のサブエージェントが居る間は、委任して待つ回なので止めない

    def running_file(self, agent, session="s1"):
        return subagent._running_path(self.state, session, agent)

    def start_agent(self, agent, cwd=None, session="s1"):
        result = self.hook(
            "SubagentStart", "", cwd or self.parent_tree, agent_id=agent, session=session
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def stop_agent(self, agent, cwd=None, session="s1"):
        return self.hook(
            "SubagentStop", "", cwd or self.parent_tree, agent_id=agent, session=session
        )

    def session_start(self, source, session="s1"):
        payload = {"hook_event_name": "SessionStart", "session_id": session, "source": source}
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_running_subagent_holds_the_nudge_without_using_it_up(self):
        self.family()
        self.start_agent("a1")
        self.assertTrue(os.path.exists(self.running_file("a1")))
        self.assert_not_asked(self.stop(self.parent_tree))
        self.assert_not_asked(self.stop(self.parent_tree))
        # 終わったあとの Stop でまた促せる（見送った回は「1 回だけ」を進めていない）。
        self.assertEqual(self.stop_agent("a1").returncode, 0)
        self.assertFalse(os.path.exists(self.running_file("a1")))
        self.asked(self.stop(self.parent_tree))
        self.assert_not_asked(self.stop(self.parent_tree))

    def test_every_running_subagent_must_finish_including_nested_ones(self):
        self.family()
        self.start_agent("a1")
        self.start_agent("a1-child", cwd=self.child_tree())
        self.stop_agent("a1")
        self.assert_not_asked(self.stop(self.parent_tree))
        self.stop_agent("a1-child", cwd=self.child_tree())
        self.asked(self.stop(self.parent_tree))

    def test_a_running_subagent_of_another_session_does_not_hold_this_one(self):
        self.family()
        self.start_agent("a1", session="s2")
        self.asked(self.stop(self.parent_tree, session="s1"))
        self.assert_not_asked(self.stop(self.parent_tree, session="s2"))

    def test_a_start_without_agent_id_leaves_no_mark(self):
        """キーが決まらないので印は置かない。その体は数えられず、促しが出うる（制限）。"""
        self.family()
        result = self.hook("SubagentStart", "", self.parent_tree)
        self.assertEqual(result.returncode, 0, result.stderr)
        names = os.listdir(self.state) if os.path.isdir(self.state) else []
        self.assertEqual([n for n in names if n.endswith(".running")], [])
        self.asked(self.stop(self.parent_tree))

    def test_a_mark_past_the_limit_is_not_counted(self):
        self.family()
        self.start_agent("a1")
        path = self.running_file("a1")
        old = int(time.time()) - subagent.RUNNING_MAX_SECONDS - 60
        write(path, f"session s1\n{old}\n")
        self.asked(self.stop(self.parent_tree))
        # 限りの内側なら数える。
        write(path, f"session s1\n{int(time.time()) - subagent.RUNNING_MAX_SECONDS + 600}\n")
        self.assert_not_asked(self.stop(self.parent_tree, session="s1", extra=()))

    def test_a_broken_mark_holds_the_nudge_until_it_is_old(self):
        """開始時刻を読めない印は、更新時刻で期限を見る。新しければ促さない側に倒す。"""
        self.family()
        path = self.running_file("a1")
        write(path, "")
        self.assert_not_asked(self.stop(self.parent_tree))
        old = time.time() - subagent.RUNNING_MAX_SECONDS - 60
        os.utime(path, (old, old))
        self.asked(self.stop(self.parent_tree))
        write(path, "session s1\nnot-a-number\n")
        self.assert_not_asked(self.stop(self.parent_tree, session="s1"))

    def test_a_mark_of_a_session_with_the_same_prefix_is_not_ours(self):
        self.family()
        write(self.running_file("a1", session="s1"), f"session s1-other\n{int(time.time())}\n")
        self.asked(self.stop(self.parent_tree))

    def test_startup_and_resume_drop_the_marks_but_compact_and_clear_keep_them(self):
        """起動と再開は新しいプロセスで、前の体は生きていない。compact と clear は同じプロセス。"""
        self.family()
        for source in ("compact", "clear"):
            self.start_agent("a1")
            self.session_start(source)
            self.assertTrue(os.path.exists(self.running_file("a1")), source)
            self.assert_not_asked(self.stop(self.parent_tree))
        for source in ("startup", "resume"):
            self.start_agent("a1")
            self.session_start(source)
            self.assertFalse(os.path.exists(self.running_file("a1")), source)
            self.asked(self.stop(self.parent_tree))
            # 次の回の前提を揃える（促しの記録を破棄する）。
            os.remove(ops_stop.nudge_path(self.state, "s1"))

    def test_startup_and_resume_keep_the_mark_of_a_session_with_the_same_prefix(self):
        self.family()
        other = self.running_file("a1", session="s1-other")
        write(other, f"session s1-other\n{int(time.time())}\n")
        for source in ("startup", "resume"):
            self.session_start(source)
            self.assertTrue(os.path.exists(other), source)

    def test_a_bounced_subagent_keeps_its_mark_and_the_bounce_still_works(self):
        """範囲外の変更で差し戻した回は続けさせるので印を残す。2 度目に終わったときに外す。"""
        self.family()
        child = self.child_tree()
        self.start_agent("sub-1", cwd=child)
        write(os.path.join(child, "src", "b", "outside.py"), "x\n")
        first = self.stop_agent("sub-1", cwd=child)
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        self.assertIn("POST_TICKET_SCOPE", first.stderr)
        self.assertTrue(os.path.exists(self.running_file("sub-1")))
        second = self.stop_agent("sub-1", cwd=child)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(os.path.exists(self.running_file("sub-1")))

    # ---- 1 回の Stop フック呼び出しで、引き止める理由は 1 つだけ

    def test_the_finish_nudge_goes_first_and_the_same_state_is_not_asked_again(self):
        """finish の促しで止めた状態は、この促しも止めない。同じ状態を 2 つの促しが止めないため。"""
        self.family()
        tree = self.child_tree()
        self.commit_work(tree)
        first = self.body(self.stop(tree))
        self.assertIn(FINISH_CODE, first["reason"])
        self.assertNotIn(CODE, first["reason"])
        self.assert_quiet(self.stop(tree))
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


class PlannedRequestNudgeTest(PhaseHarness):
    """計画を持つ親。フェーズの見る場所（延期）と、実際のレビューの状態を作って確かめる。"""

    def stop(self, cwd, session="s1"):
        payload = {
            "hook_event_name": "Stop",
            "cwd": cwd,
            "session_id": session,
            "stop_hook_active": False,
        }
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def close_first_child(self, plan, allow, rel, review=True):
        """計画の 1 番目の子を閉じて親に合流する。親のツリーで子は全部閉じた形になる。"""
        self.family(plan=plan)
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, allow, review=review))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [(rel, "x\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")

    def test_a_merge_request_phase_is_asked_as_a_control(self):
        self.close_first_child(["design", "implement"], ["wip/design/*"], "wip/design/p.md")
        body = self.stop(self.parent_tree)
        self.assertEqual(body.get("decision"), "block")
        self.assertIn(CODE, body["reason"])

    def test_a_deferred_phase_is_not_asked(self):
        """延期したフェーズは依頼できない（次にレビューのある番号と一緒に見る）。"""
        self.close_first_child(
            [("design", "defer"), "implement", "acceptance"], ["wip/design/*"], "wip/design/p.md"
        )
        self.assertNotIn("decision", self.stop(self.parent_tree))

    def test_a_real_change_request_keeps_the_requested_mark_and_is_not_asked(self):
        """依頼したあと、レビュアーが変更要求を付けた。`confirm` は通らず、`requested` が残る。"""
        self.close_first_child(["design", "implement"], ["wip/design/*"], "wip/design/p.md")
        write(os.path.join(self.parent_tree, "wip", "eli5", "phase-1.html"), "<p>x</p>\n")
        self.commit_parent("eli5")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        mark = os.path.join(self.approved, "phases", "i0001", "1.requested")
        self.assertTrue(os.path.exists(mark))
        result = write(
            os.path.join(self.root, "changes.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": {"number": 7, "url": "u/7"},
                    "threads": [],
                    "reviews": [
                        {
                            "state": "CHANGES_REQUESTED",
                            "url": "r/1",
                            "submitted_at": "2026-01-02T00:00:00Z",
                            "author": "9001",
                        }
                    ],
                }
            ),
        )
        refused = self.confirm(result, 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("変更要求", refused.stderr)
        self.assertTrue(os.path.exists(mark))
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )
        self.assertNotIn("decision", self.stop(self.parent_tree))


if __name__ == "__main__":
    unittest.main()
