"""`--explain --json`（ボードの JSON）の受入テスト。

VS Code のボード拡張が読む形を、判定と同じ関数で組んでいることを確かめる。
見るのは 4 つ。

1. 提案・写し・印・作業ツリーの有無が、識別子ごとに 1 件にまとまって出る
2. 承認待ち（写しの無い提案）が `pending_approval` に出る
3. 親のフェーズとゲートが `parents` に出る
4. 写しの置き場が無ければ、空のボードと理由を返す

拡張側のフィクスチャ（vscode-extension/ccnavi-board/test/fixtures/board.json）と
同じ形であることも見る。形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを
走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import re
import unittest

from tests.test_phases import PhaseHarness, child_text
from tests.test_ticket import ROOT, write

FIXTURE = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "test", "fixtures", "board.json")


class BoardTest(PhaseHarness):
    def board(self, *extra):
        result = self.ccnavi("--explain", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def scene(self):
        """親 1 本（research → design）。フェーズ 1 は閉じ、フェーズ 2 は着手済みで、
        同じフェーズに未承認の子が 1 枚ある。その子は着手済みの子の作業ツリーにも写っている。"""
        self.family(plan=("research", "design"))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.merge("i0001-01")

        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        # 承認の後、作業ツリーを切る前に次の子を提案する。切った作業ツリーは
        # 親のブランチの写しなので、この提案がそこにも見える。
        self.propose("i0001-03", child_text("i0001-03", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        self.run_child("i0001-02")

    def test_tickets_carry_proposal_copy_marks_and_worktree(self):
        self.scene()
        board = self.board()
        self.assertEqual(board["version"], 1)
        by_id = {t["ticket"]: t for t in board["tickets"]}
        self.assertEqual(sorted(by_id), ["i0001", "i0001-01", "i0001-02", "i0001-03"])

        closed = by_id["i0001-01"]
        self.assertEqual(closed["proposal"]["state"], "done")
        self.assertEqual(closed["copy"]["status"], "closed")
        self.assertTrue(closed["worktree"]["exists"])
        self.assertTrue(closed["completed_at"])

        doing = by_id["i0001-02"]
        self.assertEqual(doing["proposal"]["state"], "doing")
        self.assertEqual(doing["copy"]["status"], "open")
        self.assertTrue(doing["worktree"]["exists"])
        self.assertTrue(doing["worktree"]["path"].endswith("i0001-02"))
        self.assertEqual(doing["parent"], "i0001")
        self.assertEqual(doing["phase"], 2)

        waiting = by_id["i0001-03"]
        self.assertEqual(waiting["proposal"]["state"], "todo")
        self.assertEqual(waiting["proposal"]["tree"], "i0001")
        self.assertEqual(waiting["copy"], {"status": "none"})
        self.assertFalse(waiting["worktree"]["exists"])
        self.assertEqual(sorted(s["tree"] for s in waiting["seen_in"]), ["i0001", "i0001-02"])

        parent = by_id["i0001"]
        self.assertEqual(parent["parent"], "")
        self.assertEqual(parent["copy"]["status"], "open")

    def test_pending_approval_lists_proposals_without_a_copy(self):
        self.scene()
        self.assertEqual(self.board()["pending_approval"], ["i0001-03"])

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
        self.assertEqual(phases[2]["state"], "active")
        self.assertEqual(phases[2]["tickets"], ["i0001-02"])
        self.assertEqual(phases[2]["states"], {"i0001-02": "doing"})
        self.assertTrue(phases[2]["review_required"])

    def test_without_copies_the_board_is_empty_and_says_why(self):
        board = self.board("--approved", "")
        self.assertEqual(board["tickets"], [])
        self.assertEqual(board["parents"], [])
        self.assertTrue(any("写しの置き場" in p for p in board["problems"]))

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
    """絶対パスと時刻を、機械に依らない綴りに置き換える。フィクスチャに書く分だけ。"""
    if isinstance(value, dict):
        return {k: _portable(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v, root) for v in value]
    if isinstance(value, str):
        # 作業ツリーの根は normcase 済み（Windows では小文字）で出るので、綴りを問わず置き換える。
        text = value.replace("\\", "/")
        return re.sub(re.escape(root.replace("\\", "/")), "<root>", text, flags=re.IGNORECASE)
    return value


if __name__ == "__main__":
    unittest.main()
