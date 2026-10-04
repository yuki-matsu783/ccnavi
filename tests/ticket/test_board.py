"""`--explain --json`（ボードの JSON）の受入テスト。

VS Code のボード拡張が読む形を、判定と同じ関数で組んでいることを確かめる。
見るのは 4 つ。

1. 提案・承認済みチケット・マーカー・ワークツリーの有無が、識別子ごとに 1 件にまとまって出る
2. 承認待ち（承認済みチケットの無い提案）が `pending_approval` に出る
3. 親のフェーズとレビュー待ちが `parents` に出る
4. チケット制御が disable なら、空のボードと理由を返す

拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/board.json）と
同じ形であることも見る。形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを
走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import re
import unittest

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import ROOT, write

FIXTURE = os.path.join(
    ROOT, "extensions", "vscode", "ccnavi-board", "test", "fixtures", "board.json"
)


class BoardTest(PhaseHarness):
    def board(self, *extra):
        result = self.ccnavi("--explain", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def scene(self):
        """親 1 本（research → design）。フェーズ 1 は閉じ、フェーズ 2 は着手済みで、
        同じフェーズに未承認の子が 1 枚ある。その子は着手済みの子のワークツリーにも入っている。"""
        self.family(plan=("research", "design"))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.merge("i0001-01-01")

        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        # 承認の後、ワークツリーを切る前に次の子を提案する。切ったワークツリーは
        # 親のブランチの承認済みチケットなので、この提案がそこにも見える。
        self.propose("i0001-02-03", child_text("i0001-02-03", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        self.run_child("i0001-02-02")

    def test_tickets_carry_proposal_copy_marks_and_worktree(self):
        self.scene()
        board = self.board()
        self.assertEqual(board["version"], 1)
        by_id = {t["ticket"]: t for t in board["tickets"]}
        self.assertEqual(sorted(by_id), ["i0001", "i0001-01-01", "i0001-02-02", "i0001-02-03"])

        closed = by_id["i0001-01-01"]
        self.assertIsNone(closed["proposal"])
        self.assertEqual(closed["copy"]["status"], "closed")
        self.assertTrue(closed["worktree"]["exists"])
        self.assertTrue(closed["completed_at"])

        doing = by_id["i0001-02-02"]
        self.assertIsNone(doing["proposal"])
        self.assertEqual(doing["copy"]["status"], "open")
        self.assertTrue(doing["worktree"]["exists"])
        self.assertTrue(doing["worktree"]["path"].endswith("i0001-02-02"))
        self.assertEqual(doing["parent"], "i0001")
        self.assertEqual(doing["phase"], 2)

        waiting = by_id["i0001-02-03"]
        self.assertEqual(waiting["proposal"]["state"], "todo")
        self.assertEqual(waiting["proposal"]["tree"], "i0001")
        self.assertEqual(waiting["copy"], {"status": "none"})
        self.assertFalse(waiting["worktree"]["exists"])
        self.assertEqual(sorted(s["tree"] for s in waiting["seen_in"]), ["i0001", "i0001-02-02"])

        parent = by_id["i0001"]
        self.assertEqual(parent["parent"], "")
        self.assertEqual(parent["copy"]["status"], "open")

    def test_scattered_is_empty_while_the_home_tree_holds_one_copy(self):
        """複数のツリーにあること自体は普通。本物とするツリーに 1 つあれば散在ではない。"""
        self.scene()
        for t in self.board()["tickets"]:
            self.assertEqual(t["scattered"], [], t["ticket"])
        # 正常な場面でも、複数のツリーにあるし状態も食い違う（ワークツリーはブランチを
        # 切った時点のコピーを持つ。承認済みチケットの置き場に在るものも入る）。
        # 数や状態の違いを食い違いに数えない。
        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertEqual(len(by_id["i0001-01-01"]["seen_in"]), 3)
        self.assertEqual(
            sorted({s["state"] for s in by_id["i0001-01-01"]["seen_in"]}), ["doing", "done"]
        )

    def move(self, ticket_id, source_tree, target_tree, state="todo"):
        """提案を 1 つ、ツリーからツリーへ手で動かす。本物とするツリーを作り変えるため。"""
        source = os.path.join(source_tree, "wip", "proposals", state, ticket_id + ".md")
        with open(source, encoding="utf-8") as f:
            text = f.read()
        os.remove(source)
        return write(os.path.join(target_tree, "wip", "proposals", state, ticket_id + ".md"), text)

    def test_scattered_is_empty_when_the_home_tree_is_gone_but_the_origin_holds_one(self):
        """親のツリーが無ければ元ツリーを本物とする。片付けただけの形を散在に数えない。

        親のワークツリーは合流したら片付ける。そこを行き先の無いまま数えると、片付けた
        親子のチケットのカードが全部「複数の場所にある」になり、状態の操作も止まる。
        """
        self.scene()
        self.move("i0001-02-03", self.parent_tree, self.root)

        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertEqual(by_id["i0001-02-03"]["scattered"], [])
        # 複数のツリーにあること自体は残る。決まらなさだけを scattered が言う。
        self.assertEqual(
            [(s["tree"], s["state"]) for s in by_id["i0001-02-03"]["seen_in"]],
            [("", "todo"), ("i0001-02-02", "todo")],
        )

    def test_scattered_lists_every_copy_when_no_authoritative_tree_holds_one(self):
        """親のツリーにも元ツリーにも無ければ、どれが本物か決まらない。候補を全部出す。"""
        self.scene()
        elsewhere = os.path.join(self.root, ".claude", "worktrees", "i0001-01-01")
        self.move("i0001-02-03", self.parent_tree, elsewhere)

        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertEqual(
            sorted((s["tree"], s["state"]) for s in by_id["i0001-02-03"]["scattered"]),
            [("i0001-01-01", "todo"), ("i0001-02-02", "todo")],
        )
        # 巻き込まれていない識別子は空のまま。
        self.assertEqual(by_id["i0001-02-02"]["scattered"], [])

    def test_scattered_says_the_same_tree_holding_two_places(self):
        """動かす途中で止まった形跡は、本物とするツリーの中でも言う（`--lint` と同じ数え方）。"""
        self.scene()
        doing = os.path.join(self.approved, "doing", "i0001-02-02.md")
        with open(doing, encoding="utf-8") as f:
            text = f.read()
        write(os.path.join(self.approved, "done", "i0001-02-02.md"), text)

        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertEqual(
            sorted((s["tree"], s["state"]) for s in by_id["i0001-02-02"]["scattered"]),
            [("i0001", "doing"), ("i0001", "done")],
        )
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("i0001-02-02 が複数の場所にある", lint.stdout)

    def test_a_ticket_waiting_for_review_is_not_scattered(self):
        """`review/` は提案の置き場でもあり承認済みチケットでもある。同じ実体を 2 つと数えない。"""
        self.scene()
        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        review = [t for t in self.board()["tickets"] if t["copy"]["status"] == "review"]
        self.assertEqual([t["ticket"] for t in review], [])
        self.assertEqual(self.close_child("i0001-02-02").returncode, 0)

        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        waiting = by_id["i0001-02-02"]
        self.assertEqual(waiting["copy"]["status"], "review")
        self.assertEqual(waiting["scattered"], [])
        # 同じファイルを 2 つの走査が拾っても、チケットは 1 ツリーに 1 つ。
        self.assertEqual(
            len({(s["tree"], s["state"]) for s in waiting["seen_in"]}), len(waiting["seen_in"])
        )

    def test_a_stale_todo_copy_left_in_a_worktree_is_not_waiting_after_approval(self):
        """場面 A。承認の前に切ったワークツリーの `todo/` の提案は、承認のあとは提案に出さない。

        親のツリーで承認すると、親のツリーの `todo/` は消えて承認済みチケットになる。切った
        ワークツリーには `todo/` が残る。提案だけでまとめると、本物とするツリーに提案が無いので
        残りの古い提案が提案に見え、ボードで「未着手」に戻る。
        """
        self.scene()
        self.assertEqual(self.approve().returncode, 0)
        stale = os.path.join(
            self.root, ".claude", "worktrees", "i0001-02-02", "wip", "proposals", "todo"
        )
        self.assertTrue(os.path.isfile(os.path.join(stale, "i0001-02-03.md")))

        board = self.board()
        by_id = {t["ticket"]: t for t in board["tickets"]}
        self.assertIsNone(by_id["i0001-02-03"]["proposal"])
        self.assertEqual(by_id["i0001-02-03"]["copy"]["status"], "open")
        self.assertEqual(by_id["i0001-02-03"]["scattered"], [])
        self.assertEqual(board["pending_approval"], [])
        # ワークツリーにあること自体は seen_in に残る。
        self.assertIn(
            ("i0001-02-02", "todo"),
            {(s["tree"], s["state"]) for s in by_id["i0001-02-03"]["seen_in"]},
        )

        # 計画の同じ古い提案は `--lint` も言わない（承認の前に切ったワークツリーに残る普通の形）。
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("i0001-02-02/wip/proposals/todo/i0001-02-03.md", lint.stdout)

    def test_a_stale_review_copy_left_in_a_worktree_is_not_in_progress_after_closing(self):
        """場面 B。`review/` の古いチケットが残ったワークツリーがあっても、閉じたものは
        閉じたまま。"""
        self.scene()
        self.assertEqual(self.close_child("i0001-02-02").returncode, 0)
        self.commit_parent()
        self.worktree("i0001-01-09", "i0001")  # review/i0001-02-02 を持ったまま切る
        # 親のツリーで閉じる（レビューを終えて done/ へ）。
        self.move_to_done("i0001-02-02")

        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertIsNone(by_id["i0001-02-02"]["proposal"])
        self.assertEqual(by_id["i0001-02-02"]["copy"]["status"], "closed")

        # review/ の古いチケットは `--lint` も言わない（ボードと承認待ちからは外す）。
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("i0001-01-09/wip/proposals/review/i0001-02-02.md", lint.stdout)

    def test_a_review_copy_in_a_worktree_matching_the_home_tree_is_not_named(self):
        """本物とするツリーと同じ置き場のチケットは、子のワークツリーに入っているだけ。名指ししない。"""
        self.scene()
        self.assertEqual(self.close_child("i0001-02-02").returncode, 0)
        self.commit_parent()
        self.worktree("i0001-01-09", "i0001")
        by_id = {t["ticket"]: t for t in self.board()["tickets"]}
        self.assertEqual(by_id["i0001-02-02"]["copy"]["status"], "review")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("i0001-01-09/wip/proposals/review/i0001-02-02.md", lint.stdout)

    def move_to_done(self, ticket_id):
        source = os.path.join(self.parent_tree, "wip", "proposals", "review", ticket_id + ".md")
        with open(source, encoding="utf-8") as f:
            text = f.read()
        os.remove(source)
        write(os.path.join(self.approved, "done", ticket_id + ".md"), text)

    def test_pending_approval_lists_proposals_without_a_copy(self):
        self.scene()
        self.assertEqual(self.board()["pending_approval"], ["i0001-02-03"])

    def test_parents_carry_phases_and_gates(self):
        self.scene()
        board = self.board()
        self.assertEqual([p["ticket"] for p in board["parents"]], ["i0001"])
        parent = board["parents"][0]
        self.assertFalse(parent["closed"])
        self.assertEqual(parent["plan"], ["research", "design"])
        self.assertIn("作業中", parent["stage"])
        phases = {p["number"]: p for p in parent["phases"]}
        self.assertEqual(sorted(phases), [1, 2])
        self.assertEqual(phases[1]["state"], "ended")
        self.assertEqual(phases[1]["type"], "research")
        self.assertFalse(phases[1]["gate_closed"])
        self.assertFalse(phases[1]["review_waiting"])
        self.assertEqual(phases[2]["state"], "active")
        self.assertEqual(phases[2]["tickets"], ["i0001-02-02"])
        self.assertEqual(phases[2]["states"], {"i0001-02-02": "doing"})
        self.assertTrue(phases[2]["review_required"])

    def test_with_ticket_control_disabled_the_board_is_empty_and_says_why(self):
        board = self.board("--ticket-control", "disable")
        self.assertEqual(board["tickets"], [])
        self.assertEqual(board["parents"], [])
        self.assertEqual(board["settings"]["ticket_control"], "disable")
        self.assertTrue(any("CCNAVI_TICKET_CONTROL" in p for p in board["problems"]))

    def test_settings_say_ticket_control_is_enabled_by_default(self):
        board = self.board()
        self.assertEqual(board["settings"]["ticket_control"], "enable")
        # 承認済みチケットの置き場を空文字で指しても切れない。既定の置き場で有効のまま。
        board = self.board("--approved", "")
        self.assertEqual(board["settings"]["ticket_control"], "enable")
        self.assertTrue(board["settings"]["approved"].endswith("approved"))

    def test_shape_matches_the_extension_fixture(self):
        self.scene()
        board = self.board()
        if os.environ.get("CCNAVI_BOARD_FIXTURE"):
            write(FIXTURE, json.dumps(_portable(board, self.root), ensure_ascii=False, indent=1))
        with open(FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(sorted(fixture), sorted(board))
        self.assertEqual(sorted(fixture["tickets"][0]), sorted(board["tickets"][0]))
        self.assertEqual(sorted(fixture["parents"][0]), sorted(board["parents"][0]))
        self.assertEqual(
            sorted(fixture["parents"][0]["phases"][0]),
            sorted(board["parents"][0]["phases"][0]),
        )


def _portable(value, root: str):
    """絶対パスと時刻を、機械に依らない表記に置き換える。フィクスチャに書く分だけ。"""
    if isinstance(value, dict):
        return {k: _portable(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v, root) for v in value]
    if isinstance(value, str):
        # ワークツリーの根は normcase 済み（Windows では小文字）で出るので、表記を問わず置き換える。
        # ccnavi は根を行き着く先まで解いたパスで出す（macOS の /var → /private/var）。
        # 解いたパスを先に置き換える。後にすると、中に含まれる元のパスだけが先に
        # 置き換わって `/private<root>` が残る。
        text = value.replace("\\", "/")
        for spelling in dict.fromkeys((os.path.realpath(root), root)):
            text = re.sub(
                re.escape(spelling.replace("\\", "/")), "<root>", text, flags=re.IGNORECASE
            )
        return text
    return value


if __name__ == "__main__":
    unittest.main()
