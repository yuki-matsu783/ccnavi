"""`ccnavi ticket status [<親>]` の受入テスト。

承認はチケットの中身を変えないので、手で置いた承認と ccnavi の承認は中身で見分けられない。
エージェントがファイルを読んで推測しないよう、置き場・承認の時刻・着手・未コミットか未 push か・
止まっている理由・次の一手を ccnavi が言う。見るのは次のとおり。

1. 手で置いて未コミットの承認: 時刻は「未コミット（手で置いた）」、ユーザに運んでもらう案内。
   write-and-push の対象でなければ「start へ進んでよい」、対象なら
   「ユーザがコミットして push するまで start は止まる」
2. コミット済みで未 push・push 済みを、手元のリモート追跡の ref で言い分ける
3. 止まっている理由: 親が未着手・先行・write-and-push が止める親子。基準点が HEAD
の祖先でないことと、
   再開で残った欄は注意
4. 承認待ち・閉じたもの。状態を動かす行には「親（メインエージェント）だけが実行する」
5. 親を省けば開いている親子を全部、知らない親なら終了コード 1
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from ccnavi.tickets import ticket_fields, write_and_push
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text


class TicketStatusTest(PhaseHarness):
    def status(self, *args):
        result = self.ccnavi("ticket", "status", *args)
        return result

    def said(self, *args):
        result = self.status(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def by_hand(self, ident="i0001", text=None):
        """GitHub の画面で動かしたのと同じく、中身を変えずに doing/ へ置く。"""
        text = text or parent_text(ident, ["research"])
        write(os.path.join(self.approved, "doing", ident + ".md"), text)
        return text

    def write_and_push_target(self, ident):
        """本物の write-and-push の見分け（`ccnavi write-and-push family`）の答え。"""
        result = self.ccnavi("write-and-push", "family", ident)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())
        return rows.get("target", "")

    def block(self, out, ident):
        """1 つのチケットの行（`- <識別子>` から次の `- ` か親子の見出しまで）。"""
        lines = out.splitlines()
        start = next(i for i, x in enumerate(lines) if x.startswith(f"- {ident} "))
        end = start + 1
        while end < len(lines) and lines[end].startswith("    "):
            end += 1
        return "\n".join(lines[start:end])

    def test_an_unfinished_operation_is_listed_even_without_open_tickets(self):
        begun = self.ccnavi("ops", "begin", "comment", "p1", "0", "7", "k", "comment:comment")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        out = self.said()
        self.assertIn("引き継ぎ未完了", out)
        self.assertIn(begun.stdout.strip(), out)

    def test_explain_says_what_dry_run_still_stops_and_what_happens_when_it_cannot_decide(self):
        out = self.ccnavi("--explain").stdout
        self.assertIn("dry-run のときに止めるもの", out)
        self.assertIn("判定に達せなかったとき", out)
        self.assertIn("Write・Edit・NotebookEdit・Agent: enable は止める、dry-run は止める", out)

    def test_lint_reports_an_unfinished_operation(self):
        self.by_hand()
        begun = self.ccnavi("ops", "begin", "comment", "p1", "0", "7", "k", "comment:comment")
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        out = self.ccnavi("--lint").stdout
        self.assertIn("引き継ぎ未完了", out)

    # ---- 1. 手で置いて未コミット

    def test_a_ticket_placed_by_hand_is_uncommitted_and_may_start_when_not_write_and_push(self):
        self.by_hand()
        out = self.said("i0001")
        self.assertIn("リモートからの取得は行いません", out)
        self.assertIn("最新の状態と異なる場合があります", out)
        mine = self.block(out, "i0001")
        self.assertIn("作業中（doing/）", mine)
        self.assertIn("承認: 未コミット（手で置いた）", mine)
        self.assertIn("着手: 未着手", mine)
        self.assertIn("置き場のファイル: 未コミット", mine)
        self.assertIn("ccnavi-user-push-approved.sh i0001", mine)
        self.assertIn("エージェントはコミットも push もしない", mine)
        self.assertIn("start へ進んでよい", mine)
        self.assertIn("ccnavi-ticket.sh start i0001", mine)
        self.assertIn("親（メインエージェント）だけが実行する", mine)
        # エージェントが運ぶコマンド（git の add・commit・push）は出さない。
        self.assertNotIn("ccnavi-git.sh add", out)
        self.assertNotIn("commit", mine)
        # 状態は何も動かさない
        self.assertFalse(os.path.exists(os.path.join(self.approved, "events")))

    def test_an_uncommitted_ticket_of_a_write_and_push_family_waits_for_the_user(self):
        # マージリクエストを持つフェーズ（design）を含める。持たない親子は write-and-push の対象外。
        self.by_hand(text=parent_text("i0001", ["research", "design"]))
        # write-and-push の対象にする: 親のワークツリーがあり、origin がある。
        git(self.parent_tree, "remote", "add", "origin", os.path.join(self.root, "nowhere.git"))
        self.assertEqual(self.write_and_push_target("i0001"), "yes")
        mine = self.block(self.said("i0001"), "i0001")
        self.assertIn("ユーザがコミットして push するまで start は実行できません", mine)
        self.assertNotIn("start へ進んでよい", mine)
        self.assertNotIn("ccnavi-ticket.sh start", mine)

    def test_a_family_write_and_push_stops_names_the_reason_and_does_not_say_go(self):
        # ここだけは write-and-push の見分けを差し替える。本物の状態で「write-and-push
        # が止める」を作ると、
        # 立ち位置の側（親のワークツリーが無い・別のブランチに居るなど）が先に
        # 止まる理由を持ち、status はそちらを言ってこの行を出さない（同じ理由を 2 度言わない）。
        # 立ち位置が止めずに write-and-push だけが止める本物の形は、親のワークツリーのパスに改行が
        # ある場合だけで、Windows ではそのパスを作れない。
        self.by_hand(text=parent_text("i0001", ["research", "design"]))
        stop = (write_and_push.TARGET_STOP, "親のワークツリーが決まらない", None)
        with mock.patch.object(write_and_push, "target", return_value=stop):
            mine = self.block(self.said("i0001"), "i0001")
        self.assertIn(
            "止まっている理由: write-and-push では状態の操作を実行できません: "
            "親のワークツリーが決まらない",
            mine,
        )
        self.assertNotIn("start へ進んでよい", mine)
        self.assertNotIn("ccnavi-ticket.sh start", mine)

    def test_a_hand_moved_planned_parent_is_read_by_its_after(self):
        """手で動かした計画付きの親も計画の `after` で読む。一直線と読み替える注意は出さない。"""
        self.by_hand(text=parent_text("i0001", ["research", "design"]))
        mine = self.block(self.said(), "i0001")
        self.assertNotIn("一直線", mine)
        self.assertNotIn("止まっている理由", mine)

    def test_a_hand_moved_parent_with_a_broken_plan_says_why_it_stops(self):
        """手で動かした親の計画が壊れていれば（終端が 1 つでない）、status が止める理由を言う。"""
        self.by_hand(text=parent_text("i0001", ["research", {"type": "design", "after": []}]))
        mine = self.block(self.said(), "i0001")
        self.assertIn("止まっている理由", mine)
        self.assertIn("待たない", mine)

    def test_fields_written_by_start_are_not_an_approval_to_carry(self):
        self.family(plan=["research"])
        self.start_parent()
        mine = self.block(self.said("i0001"), "i0001")
        self.assertIn("未コミットの変更がある", mine)
        self.assertNotIn("ccnavi-user-push-approved.sh", mine)

    # ---- 2. 未 push・push 済み

    def test_committed_tickets_are_told_apart_by_the_remote_tracking_ref(self):
        self.family(plan=["research"])
        mine = self.block(self.said("i0001"), "i0001")
        self.assertIn("リモート追跡参照がない", mine)
        # 承認は時刻を残さない
        self.assertNotIn("未コミット（手で置いた）", mine)
        self.assertNotIn("承認: 不明", mine)
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.parent_tree, "remote", "add", "origin", bare)
        git(self.parent_tree, "push", "--quiet", "-u", "origin", "i0001")
        self.assertIn("push 済み（origin/i0001）", self.block(self.said("i0001"), "i0001"))
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text + "\n追記\n")
        self.commit_parent("touch")
        self.assertIn(
            "未push（origin/i0001 に未反映の変更があります）",
            self.block(self.said("i0001"), "i0001"),
        )

    # ---- 3. 止まっている理由

    def test_a_child_of_an_unstarted_parent_is_stopped(self):
        self.family(plan=["research"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        self.approve()
        child = self.block(self.said("i0001"), "i0001-01-01")
        self.assertIn("止まっている理由: 親チケット i0001 が未着手", child)
        self.assertNotIn("start i0001-01-01", child)
        parent = self.block(self.said("i0001"), "i0001")
        self.assertIn("ccnavi-ticket.sh start i0001", parent)

    def test_a_planted_base_sha_off_the_worktree_history_is_a_warning(self):
        self.family(plan=["research"])
        other = self.worktree("elsewhere", "main")
        write(os.path.join(other, "x.txt"), "x\n")
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "elsewhere")
        stray = git(other, "rev-parse", "HEAD").strip()
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.replace('base_sha: ""', f'base_sha: "{stray}"', 1))
        self.commit_parent("planted")
        mine = self.block(self.said("i0001"), "i0001")
        # 基準点が HEAD の祖先でないときは warn を出す。started_at の無い base_sha は
        # 判定が blocked で止めるので、止まっている理由はそちらで出る。
        self.assertIn("注意: 基準点", mine)
        self.assertIn("HEAD の祖先でない", mine)
        self.assertIn("止まっている理由: `started_at` が無いのに `base_sha` がある", mine)
        self.assertNotIn("ccnavi-ticket.sh start i0001'", mine)

    def test_fields_left_by_a_resume_are_a_warning(self):
        self.family(plan=["research"])
        self.start_parent()
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.replace('completed_at: ""', 'completed_at: "2026-09-01T00:00:00+0900"'))
        self.commit_parent("resumed")
        mine = self.block(self.said("i0001"), "i0001")
        self.assertIn("注意: 作業中なのに completed_at", mine)
        self.assertIn("finish i0001", mine)

    # ---- 4. 承認待ち・閉じた

    def test_a_pending_proposal_waits_for_the_users_approval(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        mine = self.block(self.said("i0001"), "i0001")
        self.assertIn("承認待ち（todo/）", mine)
        self.assertIn("ユーザの承認を待つ", mine)
        self.assertNotIn("start", mine)

    def test_a_cancelled_child_is_shown_closed(self):
        self.family(plan=["research"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        self.approve()
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "要らなくなった")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        child = self.block(self.said("i0001"), "i0001-01-01")
        self.assertIn("クローズ済み（done/）", child)
        self.assertIn("取り消し", child)
        self.assertIn("要らなくなった", child)

    def test_a_finished_child_shows_where_it_was_merged(self):
        """finish で閉じた子は、親のブランチへ合流した先頭（`merged_sha`）を閉じ方に出す。"""
        self.family(plan=["research"])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        self.approve()
        self.run_child("i0001-01-01", [("wip/research/summary.md", "a\n")])
        finished = self.close_child("i0001-01-01")
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        done = next(
            os.path.join(base, name)
            for base, _, names in os.walk(self.root)
            for name in names
            if name == "i0001-01-01.md" and os.path.basename(base) == "done"
        )
        with open(done, encoding="utf-8") as f:
            text = f.read()
        write(done, ticket_fields.set_fields(text, {"merged_sha": "0123456789ab" * 3}))
        child = self.block(self.said("i0001"), "i0001-01-01")
        self.assertRegex(child, r"閉じ方: 完了（[^）]*、合流 0123456789ab）")

    # ---- 5. 親を省く・知らない親

    def test_without_a_parent_every_open_family_is_listed(self):
        self.by_hand()
        out = self.said()
        self.assertIn("== 親子 i0001", out)
        self.assertEqual(self.said("i0001-01-01"), self.said("i0001"))

    def test_an_unknown_parent_is_an_error(self):
        result = self.status("i9999")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("i9999", result.stderr)

    def test_nothing_open_says_so(self):
        out = self.said()
        self.assertIn("チケットはありません。", out)


if __name__ == "__main__":
    unittest.main()
