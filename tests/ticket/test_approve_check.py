"""`--approve --preview --check`（承認を頼む前の確認）の受入テスト。

エージェントが提案を書いたあと、人に承認を依頼する前に自分で確かめる枝
（REQ-APV-13）と、書いた回にそれを伝える組み込みのルール（REQ-APV-14）。
見るのは 7 つ。

1. 承認できる状態なら 0 で返り、識別子ごとに「通る」と出る。承認済みチケットは置かない
2. 承認の対象にしない提案があれば 1 で返り、その理由が本文に出る
3. 承認待ちが無ければ 1。提案の置き場を間違えた回がここに出る
4. 承認待ちに無い識別子を指定すれば 1（絞りは `--approve` と同じ意味）
5. 範囲の超過だけなら 0。承認は止まらないので通るが、書けないことは行に添える
6. `--json` は `--preview --json` と同じ形に `check` を足したもので、終了コードも同じ
7. `todo/` に提案を書くと、確認の案内が 1 つの文脈で 1 度だけ届く
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import write

APPROVE_VERSION = 1


class ApproveCheckTest(PhaseHarness):
    def check(self, *extra):
        return self.ccnavi("--approve", "--preview", "--check", *extra)

    # ---- 1. 通る

    def test_pending_batch_passes_and_places_nothing(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("i0001", result.stdout)
        self.assertIn("通る", result.stdout)
        self.assertIn("承認を依頼してよい", result.stdout)
        self.assertNotIn("落ちる", result.stdout)
        # 確かめただけ。承認済みチケットは置かれていない。
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01.md")))

    def test_check_narrowed_to_one_id(self):
        """絞りは `--approve` と同じ意味。指定した 1 件だけを見る。"""
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.check("i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("指定の 1 件", result.stdout)

    # ---- 2. 落ちる提案がある

    def test_a_rejected_proposal_fails_the_check_with_its_reason(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        # 計画に無い番号の子。承認の対象にしない側に載る。
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent()

        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("i0001-05", result.stdout)
        self.assertIn("落ちる", result.stdout)
        self.assertIn("計画に無い", result.stdout)
        self.assertIn("直してから", result.stdout)
        # 通るほうも同じ画面に出る。直す相手が分かるように。
        self.assertIn("通る", result.stdout)

    # ---- 3. 承認待ちが無い

    def test_nothing_pending_is_a_failed_check(self):
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("承認待ちのチケットは無い", result.stdout)
        self.assertIn("todo/", result.stdout)

    # ---- 4. 承認待ちに無い識別子

    def test_an_unknown_id_is_a_failed_check(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.check("i0002")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("承認待ちに無い", result.stdout)

    # ---- 5. 範囲の超過は落とさない

    def test_scope_overflow_passes_but_is_shown(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        # 種類（調査）の範囲を超える子。承認は止まらず、判定が切り詰める。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/design/*",)))
        self.commit_parent()

        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認しても書けない", result.stdout)
        self.assertIn("承認を依頼してよい", result.stdout)

    # ---- 6. JSON

    def test_json_is_the_preview_body_with_the_answer(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.check("--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001"])
        self.assertTrue(body["digest"])
        self.assertEqual(body["check"], {"ok": True, "reason": "ok"})

    def test_json_says_why_it_failed(self):
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        result = self.check("--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertFalse(body["check"]["ok"])
        self.assertTrue(any("broken.md" in p for p in body["problems"]))

    def test_broken_proposal_alongside_a_sound_one_fails_the_check(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("読めない提案がある", result.stdout)

    def test_check_needs_preview(self):
        """`--check` は `--preview` に相乗りする。単独ではエージェントが打てない。"""
        result = self.ccnavi("--approve", "--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--preview", result.stderr)

    # ---- 7. 書いた回に案内が届く

    def test_writing_a_proposal_tells_the_agent_to_check_first(self):
        target = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0002.md")
        first = self.hook("PreToolUse", "Write", self.parent_tree, file_path=target)
        self.assertIn("--approve --preview --check", self.reason(first))
        self.assertIn("承認を依頼する前に", self.reason(first))
        # 止めない。文だけを足す。
        self.assertNotIn("permissionDecision", first.stdout)

    def test_the_notice_comes_once_per_context(self):
        todo = os.path.join(self.parent_tree, "wip", "proposals", "todo")
        first = self.hook("PreToolUse", "Write", self.parent_tree, file_path=todo + "/i0002.md")
        self.assertIn("--approve --preview --check", self.reason(first))
        again = self.hook("PreToolUse", "Write", self.parent_tree, file_path=todo + "/i0003.md")
        self.assertNotIn("--approve --preview --check", self.reason(again))

    def test_the_notice_does_not_reach_other_places(self):
        other = os.path.join(self.parent_tree, "src", "keep.py")
        result = self.hook("PreToolUse", "Write", self.parent_tree, file_path=other)
        self.assertNotIn("--approve --preview --check", self.reason(result))
