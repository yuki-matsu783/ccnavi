"""親の `ticket start` と計画（issue #317）。

1. 計画の無い親は、子や子の提案があっても `start` が止まる（案内は phases.yml に定義を書き、
   親に plan を足す。書くのはユーザ）
2. 計画のある親で子のないフェーズが残るとき、`start` は成功し、案内を出す。数える子は
   承認済みの子と `todo/` の提案の両方
3. 子のないまま進めても `finish` が止まる。子の `start` は親が着手済みであることが条件のまま
"""

from __future__ import annotations

import os

from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import ticket_text
from tests.ticketsample import child_text, parent_text

HEAD = "子のない（子の提案もありません）フェーズがある"


class StartPlanTest(PhaseHarness):
    def parent_with_plan(self, plan=("research", "design")):
        self.propose("i0001", parent_text("i0001", list(plan), feedback=[]))

    def test_a_parent_without_a_plan_is_stopped_even_with_a_child_proposal(self):
        self.propose("i0001", ticket_text("i0001", allow=("src/*",), plan=0))
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/a/*"]))
        self.commit_parent()
        self.approve()
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 1, started.stdout)
        self.assertIn("plan が無いので着手できない", started.stderr)
        self.assertIn(".ccnavi/config/project/phases.yml", started.stderr)
        self.assertIn("work を 1 つ", started.stderr)
        self.assertIn("書くのはユーザ", started.stderr)
        # 着手の欄は書かれない
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn('started_at: ""', f.read())

    def test_a_parent_with_a_plan_and_no_children_starts_and_is_told(self):
        self.parent_with_plan()
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn(f"{HEAD}: 1（調査）, 2（設計）", started.stdout)
        # 次の一手は、子の提案を書いて approval-request で送ること（識別子の例を埋めて言う）
        nexts = [x for x in started.stdout.splitlines() if x.startswith("次: ")]
        self.assertEqual(len(nexts), 1, started.stdout)
        self.assertIn("子の提案（例: 'i0001-01-01'）", nexts[0])
        self.assertIn("approval-request i0001", nexts[0])
        self.assertIn("`finish` で", started.stdout)
        self.assertEqual(started.stderr, "")

    def test_finish_still_stops_a_parent_whose_phases_have_no_children(self):
        self.parent_with_plan()
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        done = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("終わっていないフェーズがある", done.stderr)

    def test_an_approved_child_takes_its_phase_off_the_list(self):
        self.parent_with_plan()
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        self.approve()
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn(f"{HEAD}: 2（設計）", started.stdout)
        self.assertNotIn("1（調査）", started.stdout)

    def test_a_proposed_child_in_todo_takes_its_phase_off_the_list(self):
        self.parent_with_plan()
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        # 2 番目のフェーズの子は、まだ承認されていない提案
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("approval-request 02")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn(f"{HEAD}: 1（調査）", started.stdout)
        self.assertNotIn("2（設計）", started.stdout)

    def test_no_notice_when_every_phase_has_a_child(self):
        self.parent_with_plan()
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        self.approve()
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("approval-request 02")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertNotIn(HEAD, started.stdout)

    def test_a_child_still_needs_its_parent_to_have_started(self):
        self.parent_with_plan()
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        self.approve()
        self.worktree("i0001-01-01", "i0001")
        refused = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("子より先に親を開始してください", refused.stderr)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        child = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertEqual(child.returncode, 0, child.stderr)
        # 子の着手の次の一手は、サブエージェントへの実装の委任と、終わったときの finish
        nexts = [line for line in child.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(len(nexts), 1, child.stdout)
        self.assertIn("サブエージェント", nexts[0])
        self.assertIn("finish i0001-01-01", nexts[0])
