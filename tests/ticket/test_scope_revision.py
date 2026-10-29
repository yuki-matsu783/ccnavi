"""親の改版で範囲（allow / ask / deny）を変える規則の受入テスト。

見るのは 4 つ。

1. 判定: 範囲だけが違う提案も改版の候補になる。書く順だけの違いは違いにしない
2. 錠: 範囲を変える改版は、作業中（doing/）の子が居ないときだけ通る。範囲が同じなら子を見ない
3. 書き込み: 改版は範囲を提案の値に差し替える。提案に無い節は外す
4. 画面: 範囲の差分（追加・削除）を deny / ask / allow ごとに出す
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from ccnavi.infra import settings
from ccnavi.tickets.approval import store as approval
from ccnavi.tickets.model import ticket as ticket_mod
from ccnavi.tickets.proposal import agree_candidates, agree_digest, agree_screen
from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_phases import parent_text as plan_parent_text

ROOT = "/nonexistent-ccnavi-root"


def parent_text(allow=("src/*",), ask=(), deny=()):
    lines = ["---", "version: 1", "ticket: login", "title: ログイン", "plan: [research]"]
    for name, globs in (("deny", deny), ("ask", ask), ("allow", allow)):
        if globs:
            lines.append(f"{name}:")
            lines += [f'- {{glob: "{g}", match: "Write|Edit"}}' for g in globs]
    lines += ["---", "", "本文", ""]
    return "\n".join(lines)


def read(**kwargs):
    return ticket_mod.parse(parent_text(**kwargs))[0]


class ScopeDiffersTest(unittest.TestCase):
    def test_a_proposal_that_only_changes_the_scope_is_a_revision(self):
        self.assertTrue(approval.plan_differs(read(allow=("src/*", "docs/*")), read()))

    def test_an_entry_in_another_section_is_a_difference(self):
        self.assertTrue(approval.plan_differs(read(ask=("src/*",), allow=()), read()))

    def test_the_order_of_entries_is_not_a_difference(self):
        a = read(allow=("src/*", "docs/*"))
        b = read(allow=("docs/*", "src/*"))
        self.assertFalse(approval.plan_differs(b, a))

    def test_the_same_scope_is_not_a_revision(self):
        self.assertFalse(approval.plan_differs(read(), read()))


class ScopeLockTest(unittest.TestCase):
    def problems(self, revised, current, busy):
        conf, _ = settings.load(ROOT)
        with (
            mock.patch.object(agree_candidates, "plan_problems", return_value=[]),
            mock.patch.object(agree_candidates, "open_children", return_value=busy) as lookup,
        ):
            found = agree_candidates.revision_problems(ROOT, conf, revised, current, None)
        return found, lookup

    def test_widening_with_no_child_in_progress_passes(self):
        found, _ = self.problems(read(allow=("src/*", "docs/*")), read(), [])
        self.assertEqual([], [p for p in found if p.severity == "error"], found)

    def test_narrowing_with_no_child_in_progress_passes(self):
        found, _ = self.problems(read(allow=("src/a/*",)), read(), [])
        self.assertEqual([], [p for p in found if p.severity == "error"], found)

    def test_a_child_in_progress_stops_the_revision_and_is_named(self):
        found, _ = self.problems(read(allow=("src/*", "docs/*")), read(), ["login-01-01"])
        errors = [p for p in found if p.severity == "error"]
        self.assertEqual(1, len(errors), found)
        self.assertIn("login-01-01", errors[0].detail)
        self.assertIn("cancel", errors[0].detail)

    def test_an_unchanged_scope_does_not_look_at_the_children(self):
        found, lookup = self.problems(read(), read(), ["login-01-01"])
        self.assertEqual([], [p for p in found if p.severity == "error"], found)
        lookup.assert_not_called()


class GlobRegexTest(unittest.TestCase):
    """glob と regex は文字列が同じでもヒットする範囲が違う。差分から隠さない。"""

    def read_entry(self, entry):
        text = (
            "---\nversion: 1\nticket: login\ntitle: t\nplan: [research]\nallow:\n"
            f'- {{{entry}, match: "Write|Edit"}}\n---\n\nb\n'
        )
        return ticket_mod.parse(text)[0]

    def test_swapping_glob_for_regex_shows_in_the_diff(self):
        before = self.read_entry('glob: "src/app"')
        after = self.read_entry('regex: "src/app"')
        self.assertTrue(approval.plan_differs(after, before))
        text = "\n".join(agree_screen._scope_diff_lines(before, after))
        self.assertIn("allow 追加: regex(src/app)", text)
        self.assertIn("allow 削除: src/app", text)

    def test_a_glob_that_looks_like_the_old_prefix_is_not_confused_with_a_regex(self):
        before = self.read_entry('glob: "regex:src/app"')
        after = self.read_entry('regex: "src/app"')
        self.assertTrue(approval.plan_differs(after, before))
        text = "\n".join(agree_screen._scope_diff_lines(before, after))
        self.assertIn("allow 追加: regex(src/app)", text)
        self.assertIn("allow 削除: regex:src/app", text)

    def test_a_duplicate_entry_is_not_a_difference(self):
        once = read(allow=("src/*",))
        twice = read(allow=("src/*", "src/*"))
        self.assertFalse(approval.plan_differs(twice, once))
        self.assertEqual([], agree_screen._scope_diff_lines(once, twice))


class UnreadableScopeTest(unittest.TestCase):
    def problems(self, revised):
        conf, _ = settings.load(ROOT)
        with (
            mock.patch.object(agree_candidates, "plan_problems", return_value=[]),
            mock.patch.object(agree_candidates, "open_children", return_value=[]),
        ):
            found = agree_candidates.revision_problems(ROOT, conf, revised, read(), None)
        return [p for p in found if p.severity == "error"]

    def test_an_entry_the_judge_cannot_read_stops_a_scope_revision(self):
        text = parent_text(allow=("src/*", "docs/*")).replace(
            '- {glob: "docs/*", match: "Write|Edit"}', '- {glob: "docs/*", match: "Read"}'
        )
        errors = self.problems(ticket_mod.parse(text)[0])
        self.assertEqual(1, len(errors), errors)
        self.assertIn("読めない項", errors[0].detail)

    def test_a_non_list_section_stops_a_scope_revision(self):
        text = parent_text().replace("allow:\n", "ask: oops\nallow:\n")
        errors = self.problems(ticket_mod.parse(text)[0])
        self.assertTrue(any("リストではない" in p.detail for p in errors), errors)


class RevisedFrontTest(unittest.TestCase):
    def test_the_scope_is_replaced_with_the_proposal(self):
        current = read(allow=("src/*",))
        revised = read(allow=("src/*", "docs/*"), deny=("src/secret/*",))
        front = agree_digest.revised_front(current, revised)
        self.assertEqual(front["allow"], revised.raw["allow"])
        self.assertEqual(front["deny"], revised.raw["deny"])

    def test_a_section_the_proposal_lacks_is_removed(self):
        current = read(allow=("src/*",), ask=("docs/*",))
        revised = read(allow=("src/*",))
        front = agree_digest.revised_front(current, revised)
        self.assertNotIn("ask", front)
        self.assertIn("allow", front)

    def test_the_current_ticket_is_not_modified(self):
        current = read(allow=("src/*",))
        agree_digest.revised_front(current, read(allow=("docs/*",)))
        self.assertEqual(["src/*"], current.paths("allow"))


class ScopeDiffScreenTest(unittest.TestCase):
    def test_added_and_removed_entries_are_listed_per_section(self):
        lines = agree_screen._scope_diff_lines(
            read(allow=("src/*", "old/*")),
            read(allow=("src/*", "docs/*"), deny=("src/secret/*",)),
        )
        text = "\n".join(lines)
        self.assertIn("■ 範囲の差分", text)
        self.assertIn("allow 追加: docs/*", text)
        self.assertIn("allow 削除: old/*", text)
        self.assertIn("deny 追加: src/secret/*", text)
        self.assertNotIn("src/*\n", text.replace("src/secret/*", ""))

    def test_no_difference_prints_nothing(self):
        self.assertEqual([], agree_screen._scope_diff_lines(read(), read()))


class ScopeRevisionFlowTest(PhaseHarness):
    """承認から取り消しまでを通した流れ。"""

    PLAN = ["research", "design"]
    WIDER = ("src/*", "wip/*", "tests/*", "docs/*")

    def doing(self, name):
        return os.path.join(self.approved, "doing", name + ".md")

    def read_doing(self, name):
        with open(self.doing(name), encoding="utf-8") as f:
            return f.read()

    def revise(self, allow):
        self.propose(
            "i0001", plan_parent_text("i0001", self.PLAN, allow=allow, copy=self.phases_text)
        )
        self.commit_parent("revise")
        return self.approve()

    def test_a_scope_only_revision_replaces_the_scope_and_shows_the_diff(self):
        self.family(plan=self.PLAN)
        result = self.revise(self.WIDER)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("親の改版", result.stdout)
        self.assertIn("■ 範囲の差分", result.stdout)
        self.assertIn("allow 追加: docs/*", result.stdout)
        self.assertIn("glob: docs/*", self.read_doing("i0001"))
        self.assertFalse(
            os.path.exists(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md"))
        )

    def test_narrowing_shows_the_removed_entry(self):
        self.family(plan=self.PLAN)
        result = self.revise(("src/*", "wip/*"))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("allow 削除: tests/*", result.stdout)

    def test_a_stale_copy_in_a_child_worktree_does_not_count_after_cancel(self):
        """取り消したあとも子のワークツリーには切り出した時点の doing/ のコピーが残る。"""
        self.family(plan=self.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("child")
        self.assertEqual(0, self.approve().returncode)
        worktree = self.run_child("i0001-01-01")
        self.assertTrue(
            os.path.exists(os.path.join(worktree, ".ccnavi", "approved", "doing", "i0001-01-01.md"))
        )
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる")
        self.assertEqual(0, cancelled.returncode, cancelled.stdout + cancelled.stderr)
        self.commit_parent("cancel")
        result = self.revise(self.WIDER)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_a_child_in_progress_stops_the_revision_until_it_is_cancelled(self):
        self.family(plan=self.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("child")
        self.assertEqual(0, self.approve().returncode)
        stopped = self.revise(self.WIDER)
        self.assertNotEqual(0, stopped.returncode, stopped.stdout + stopped.stderr)
        self.assertIn("i0001-01-01", stopped.stdout + stopped.stderr)
        self.assertNotIn("docs/*", self.read_doing("i0001"))

        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる")
        self.assertEqual(0, cancelled.returncode, cancelled.stdout + cancelled.stderr)
        self.commit_parent("cancel")
        result = self.approve()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("docs/*", self.read_doing("i0001"))


if __name__ == "__main__":
    unittest.main()
