"""提案の形式検査（`proposal_form.form_problems`）のテスト。文字列を調べるだけなので直接呼ぶ。

見るのは 8 つ。

1. 新規の親の rationale に目印が残っていれば error
2. 本文に目印が残っていれば error。残っている目印を名指しする
3. 同じ内容を `revision=True` で渡せば warn
4. 親の新規の提案で見出しが 2 つ欠ければ、warn が 1 件で 2 つとも名指しされる
5. コードフェンスの中にだけある `## 目的` は欠けとして数える
6. `### 目的`（深さ違い）だけなら欠けとして数える
7. 子の提案は見出しの欠けを言わない（目印の残りは言う）
8. 見出しがそろい、目印がない親は何も言わない
"""

from __future__ import annotations

import unittest

from ccnavi.policy import rules
from ccnavi.tickets.model import proposal_form, ticket_model

FILLED = "".join(f"## {name}\n\n書いた内容\n\n" for name in proposal_form.PARENT_HEADINGS)


def ticket(*, body=FILLED, rationale="理由を書いた", child=False):
    t = ticket_model.Ticket()
    t.ticket = "feature-1-demo-01-01" if child else "feature-1-demo"
    if child:
        t.parent = "feature-1-demo"
        t.phase = 1
    t.rationale = rationale
    t.body = body
    return t


def without(*names):
    return "".join(
        f"## {name}\n\n書いた内容\n\n"
        for name in proposal_form.PARENT_HEADINGS
        if name not in names
    )


class FormProblemsTest(unittest.TestCase):
    def test_a_placeholder_left_in_the_rationale_is_an_error_for_a_new_proposal(self):
        found = proposal_form.form_problems(ticket(rationale=proposal_form.RATIONALE_TODO))
        self.assertEqual([p.severity for p in found], [rules.SEVERITY_ERROR])
        self.assertIn("rationale", found[0].detail)
        self.assertIn(proposal_form.RATIONALE_TODO, found[0].detail)

    def test_a_placeholder_left_in_the_body_is_an_error_and_named(self):
        mark = proposal_form.HEADING_TODO["範囲"]
        body = FILLED.replace("書いた内容", mark, 1)
        found = proposal_form.form_problems(ticket(body=body))
        self.assertEqual([p.severity for p in found], [rules.SEVERITY_ERROR])
        self.assertIn("本文", found[0].detail)
        self.assertIn(mark, found[0].detail)

    def test_the_same_content_is_a_warn_for_a_revision(self):
        t = ticket(
            rationale=proposal_form.RATIONALE_TODO, body=FILLED + proposal_form.PLACEHOLDERS[1]
        )
        found = proposal_form.form_problems(t, revision=True)
        self.assertEqual([p.severity for p in found], [rules.SEVERITY_WARN] * 2)
        new = proposal_form.form_problems(t)
        self.assertEqual([p.severity for p in new], [rules.SEVERITY_ERROR] * 2)

    def test_two_missing_headings_make_one_warn_naming_both(self):
        found = proposal_form.form_problems(ticket(body=without("範囲", "対象外")))
        self.assertEqual([p.severity for p in found], [rules.SEVERITY_WARN])
        self.assertIn("## 範囲", found[0].detail)
        self.assertIn("## 対象外", found[0].detail)
        self.assertNotIn("## 目的", found[0].detail)

    def test_a_heading_only_inside_a_code_fence_counts_as_missing(self):
        for fence in ("```", "~~~"):
            body = without("目的") + f"{fence}\n## 目的\n{fence}\n"
            found = proposal_form.form_problems(ticket(body=body))
            self.assertEqual(len(found), 1, fence)
            self.assertIn("## 目的", found[0].detail)

    def test_a_heading_after_a_closed_fence_counts(self):
        body = "```\ncode\n```\n" + FILLED
        self.assertEqual(proposal_form.form_problems(ticket(body=body)), [])

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        body = without("目的") + "````md\n```\n## 目的\n```\n````\n"
        found = proposal_form.form_problems(ticket(body=body))
        self.assertEqual(len(found), 1)
        self.assertIn("## 目的", found[0].detail)

    def test_a_fence_is_closed_only_by_a_line_of_the_marks(self):
        body = without("目的") + "```py\n## 目的\n```not close\n## 目的\n```\n"
        found = proposal_form.form_problems(ticket(body=body))
        self.assertEqual(len(found), 1)

    def test_an_unclosed_fence_swallows_the_rest(self):
        found = proposal_form.form_problems(ticket(body="```\n" + FILLED))
        self.assertEqual(len(found), 1)
        for name in proposal_form.PARENT_HEADINGS:
            self.assertIn(f"## {name}", found[0].detail)

    def test_four_spaces_of_indent_is_a_code_block_not_a_fence(self):
        body = "    ```\n" + FILLED
        self.assertEqual(proposal_form.form_problems(ticket(body=body)), [])

    def test_crlf_line_ends_are_read(self):
        body = FILLED.replace("\n", "\r\n")
        self.assertEqual(proposal_form.form_problems(ticket(body=body)), [])

    def test_a_heading_of_another_depth_counts_as_missing(self):
        body = without("目的") + "### 目的\n\n書いた内容\n"
        found = proposal_form.form_problems(ticket(body=body))
        self.assertEqual(len(found), 1)
        self.assertIn("## 目的", found[0].detail)

    def test_a_child_gets_no_heading_complaint_but_still_the_placeholder_one(self):
        self.assertEqual(proposal_form.form_problems(ticket(body="", child=True)), [])
        found = proposal_form.form_problems(
            ticket(body="", rationale=proposal_form.RATIONALE_TODO, child=True)
        )
        self.assertEqual([p.severity for p in found], [rules.SEVERITY_ERROR])

    def test_a_revision_gets_no_heading_complaint(self):
        self.assertEqual(proposal_form.form_problems(ticket(body=""), revision=True), [])

    def test_a_complete_parent_without_placeholders_gets_nothing(self):
        self.assertEqual(proposal_form.form_problems(ticket()), [])

    def test_heading_names_match_after_trimming_the_spaces(self):
        body = FILLED.replace("## 目的", "## 目的  ")
        self.assertEqual(proposal_form.form_problems(ticket(body=body)), [])


if __name__ == "__main__":
    unittest.main()
