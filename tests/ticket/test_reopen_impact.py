"""フェーズを開き直したとき、後続の着手済み・レビュー済みのフェーズを挙げて方針を促す案内。

設計フェーズに子を足すと、設計フェーズのマーカーだけが消える。着手済みの実装の子や、済んだ
受入テストのレビュー状態はそのまま残るので、設計の修正が後続に届いたかは自動では決まらない。
案内は、影響しうる後続を挙げ、継続・追加修正・取り消しの方針をユーザと決めるよう促す。
"""

from __future__ import annotations

import unittest
from unittest import mock

from ccnavi.tickets import phase
from ccnavi.tickets.approval import marks as approval_marks
from ccnavi.tickets.config import workflow
from ccnavi.tickets.model import ticket_model


def phase_of(number, states, marks=()):
    ph = phase.Phase(parent="p", number=number)
    ph.states = {f"p-{number:02d}-{i + 1:02d}": s for i, s in enumerate(states)}
    ph.marks = {m: {} for m in marks}
    ph.owner = ticket_model.Ticket(ticket="p")
    return ph


class ReopenImpactTest(unittest.TestCase):
    def run_with(self, phases, waits, number=1):
        with (
            mock.patch.object(phase, "phases_of", return_value=phases),
            mock.patch.object(workflow, "waits_of", side_effect=lambda _p, n: waits.get(n, [])),
        ):
            return phase.reopen_impact("/r", mock.Mock(), "p", number)

    def test_started_and_reviewed_followers_are_listed(self):
        phases = [
            phase_of(1, [ticket_model.DONE], [approval_marks.MARK_REVIEWED]),
            phase_of(2, [ticket_model.DONE], [approval_marks.MARK_REVIEWED]),
            phase_of(3, [ticket_model.DOING]),
            phase_of(4, []),
        ]
        lines = self.run_with(phases, {2: [1], 3: [1, 2], 4: [1, 2, 3]})
        text = "\n".join(lines)
        self.assertIn("継続・追加修正・取り消し", text)
        self.assertIn("自動では取り消さない", text)
        self.assertIn("フェーズ 2: 着手済み・レビュー済み", text)
        self.assertIn("フェーズ 3: 着手済み", text)
        self.assertNotIn("フェーズ 4", text, "着手もレビューもしていない後続は挙げない")
        self.assertNotIn("フェーズ 1:", text, "開き直すフェーズ自身は挙げない")

    def test_a_phase_that_does_not_wait_on_it_is_not_listed(self):
        phases = [phase_of(1, [ticket_model.DONE]), phase_of(2, [ticket_model.DOING])]
        self.assertEqual([], self.run_with(phases, {2: []}))

    def test_nothing_started_after_it_says_nothing(self):
        phases = [phase_of(1, [ticket_model.DONE]), phase_of(2, [])]
        self.assertEqual([], self.run_with(phases, {2: [1]}))


if __name__ == "__main__":
    unittest.main()
