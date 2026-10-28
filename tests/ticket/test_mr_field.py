"""親チケットの `mr:`（MR の番号）の受入テスト。

見るのは 4 つ。

1. 型: `mr:` は番号（`12`・`"!12"`・`"#12"`）を読む。別のリポジトリの表記・0・文字列は error。
   子に書くと warn で無視する。スクリプトだけが書く欄（SCRIPT_FIELDS）には入れない
2. 書き足し: `ticket_fields.set_mr` は空のときだけ書く。同じ値は何もしない、違う値は書かない
3. 改版: 片方が空なら比べない。両方に値があって違えば error
4. 実行後チェックの例外の条件（`ticket_fields.plain_int_field`・`mr_is_empty`）
"""

from __future__ import annotations

import unittest
from unittest import mock

from ccnavi.infra import settings
from ccnavi.tickets.model import ticket as ticket_mod
from ccnavi.tickets.model import ticket_fields, ticket_model
from ccnavi.tickets.proposal import agree_candidates


def parent_text(mr=None, child=False):
    lines = ["---", "version: 1", "ticket: top-01-01" if child else "ticket: login"]
    if child:
        lines += ["parent: top", "phase: 1"]
    if not child:
        lines.append("issue: 12")
    lines += ["title: ログイン", "plan: [research]"]
    if mr is not None:
        lines.append(f"mr: {mr}")
    lines += ["allow:", '- {glob: "src/*", match: "Write|Edit"}', "---", "", "本文", ""]
    return "\n".join(lines)


class MrFieldTypeTest(unittest.TestCase):
    def parse(self, mr, child=False):
        t, problems = ticket_mod.parse(parent_text(mr, child))
        return t, problems

    def test_numbers_are_read(self):
        for raw, number in (("12", 12), ('"!12"', 12), ('"#12"', 12)):
            with self.subTest(raw=raw):
                t, problems = self.parse(raw)
                self.assertEqual([], [p for p in problems if p.severity == "error"], problems)
                self.assertEqual(number, t.mr)

    def test_other_forms_are_errors(self):
        for raw in ("acme/other!12", "acme/other#12", "0", "x", "-3", "true"):
            with self.subTest(raw=raw):
                _, problems = self.parse(raw)
                errors = [p for p in problems if p.severity == "error"]
                self.assertEqual(1, len(errors), raw)
                self.assertIn("`mr`", errors[0].detail)

    def test_no_field_and_an_empty_field_are_none(self):
        self.assertIsNone(self.parse(None)[0].mr)
        self.assertIsNone(self.parse('""')[0].mr)

    def test_a_child_warns_and_ignores_it(self):
        t, problems = self.parse("12", child=True)
        self.assertIsNone(t.mr)
        self.assertTrue(
            any(p.severity == "warn" and "`mr`" in p.detail for p in problems), problems
        )

    def test_it_is_not_a_script_field(self):
        self.assertNotIn("mr", ticket_model.SCRIPT_FIELDS)


class SetMrTest(unittest.TestCase):
    def test_an_empty_field_is_written(self):
        for text in (parent_text(), parent_text('""'), parent_text("")):
            with self.subTest(text=text):
                new, result = ticket_fields.set_mr(text, 12)
                self.assertEqual(ticket_fields.MR_SET, result)
                self.assertEqual(12, ticket_mod.parse(new)[0].mr)
                # 欄の行のほかは 1 文字も変わらない
                self.assertEqual(
                    ticket_fields.script_shape(text, ("mr",)),
                    ticket_fields.script_shape(new, ("mr",)),
                )

    def test_the_same_value_does_nothing(self):
        for raw in ("12", '"!12"', '"#12"'):
            text = parent_text(raw)
            self.assertEqual((text, ticket_fields.MR_SAME), ticket_fields.set_mr(text, 12))

    def test_another_value_is_not_overwritten(self):
        text = parent_text("13")
        self.assertEqual((text, ticket_fields.MR_DIFFERS), ticket_fields.set_mr(text, 12))
        text = parent_text("acme/other!12")
        self.assertEqual((text, ticket_fields.MR_DIFFERS), ticket_fields.set_mr(text, 12))

    def test_no_front_matter_is_unreadable(self):
        self.assertEqual(("本文\n", ticket_fields.MR_UNREADABLE), ticket_fields.set_mr("本文\n", 1))


class ExceptionConditionTest(unittest.TestCase):
    def test_plain_integer_only(self):
        for raw, number in (("12", 12), ("012", 12)):
            self.assertEqual(number, ticket_fields.plain_int_field(parent_text(raw), "mr"))
        for raw in ('"12"', '"!12"', "12 # c", "0", "-1", "x", "", "[12]"):
            with self.subTest(raw=raw):
                self.assertIsNone(ticket_fields.plain_int_field(parent_text(raw), "mr"))
        self.assertIsNone(ticket_fields.plain_int_field(parent_text(), "mr"))
        self.assertIsNone(ticket_fields.plain_int_field(None, "mr"))

    def test_continued_value_is_not_plain(self):
        text = parent_text("12").replace("mr: 12\n", "mr: 12\n  extra\n")
        self.assertIsNone(ticket_fields.plain_int_field(text, "mr"))

    def test_empty_means_no_field_or_no_value(self):
        self.assertTrue(ticket_fields.mr_is_empty(parent_text()))
        self.assertTrue(ticket_fields.mr_is_empty(parent_text('""')))
        self.assertTrue(ticket_fields.mr_is_empty(parent_text("")))
        self.assertFalse(ticket_fields.mr_is_empty(parent_text("12")))
        self.assertFalse(ticket_fields.mr_is_empty(parent_text("x")))
        self.assertFalse(ticket_fields.mr_is_empty(None))
        self.assertFalse(ticket_fields.mr_is_empty("本文\n"))


class RevisionTest(unittest.TestCase):
    def problems(self, current_mr, revised_mr):
        current = ticket_mod.parse(parent_text(current_mr))[0]
        revised = ticket_mod.parse(parent_text(revised_mr))[0]
        conf, _ = settings.load("/nonexistent-ccnavi-root")
        with mock.patch.object(agree_candidates, "plan_problems", return_value=[]):
            found = agree_candidates.revision_problems(
                "/nonexistent-ccnavi-root", conf, revised, current, None
            )
        return [p for p in found if "`mr:`" in p.detail]

    def test_an_empty_side_is_not_compared(self):
        self.assertEqual([], self.problems("12", None))
        self.assertEqual([], self.problems(None, "12"))
        self.assertEqual([], self.problems("12", "12"))

    def test_two_values_that_differ_are_an_error(self):
        self.assertEqual(1, len(self.problems("12", "13")))


if __name__ == "__main__":
    unittest.main()
