"""別のリポジトリの課題 `issue: owner/repo#N`（ADR-0093 の 3.1 の 8・8.7。段階 5）の受入テスト。

見るのは 3 つ。

1. `issue:` は番号（`12`・`"#12"`）のほかに `owner/repo#12`（GitLab の入れ子のグループも）を読む。
   `..` を含む綴りや 0 は読まない
2. MR の下書きの `Closes` は、同じリポジトリなら `#12`、別のリポジトリなら `owner/repo#12`
3. 改版で課題のリポジトリを変えることはできない（題・課題番号と同じ）
"""

from __future__ import annotations

import unittest
from unittest import mock

from ccnavi import agree, review, settings
from ccnavi import ticket as ticket_mod


def parent_text(issue):
    return "\n".join(
        [
            "---",
            "version: 1",
            "ticket: login",
            "title: ログイン",
            "plan: [research]",
            f"issue: {issue}",
            "allow:",
            '- {glob: "src/*", match: "Write|Edit"}',
            "---",
            "",
        ]
    )


class IssueRefTest(unittest.TestCase):
    def parse(self, issue):
        t, problems = ticket_mod.parse(parent_text(issue))
        return t, [p for p in problems if p.severity == ticket_mod.SEVERITY_ERROR]

    def test_numbers_and_references_are_read(self):
        for raw, repo, number in (
            ("12", "", 12),
            ('"#12"', "", 12),
            ("acme/other#12", "acme/other", 12),
            ("group/sub/proj#7", "group/sub/proj", 7),
        ):
            with self.subTest(raw=raw):
                t, errors = self.parse(raw)
                self.assertEqual([], errors)
                self.assertEqual((t.issue_repo, t.issue), (repo, number))

    def test_bad_references_are_errors(self):
        for raw in ("acme#12", "acme/../x#12", "acme/other#0", "acme/other#x", "acme/other"):
            with self.subTest(raw=raw):
                _, errors = self.parse(raw)
                self.assertEqual(1, len(errors), raw)
                self.assertIn("owner/repo#12", errors[0].detail)

    def test_closes_names_the_repository_when_it_is_another(self):
        same, _ = self.parse("12")
        other, _ = self.parse("acme/other#12")
        self.assertIn("Closes #12", review.mr_draft(same).splitlines())
        self.assertIn("Closes acme/other#12", review.mr_draft(other).splitlines())
        self.assertEqual("acme/other#12", ticket_mod.issue_label(other))
        self.assertEqual("", ticket_mod.issue_label(ticket_mod.Ticket(ticket="x")))

    def test_a_revision_cannot_move_the_issue_to_another_repository(self):
        current, _ = self.parse("12")
        revised, _ = self.parse("acme/other#12")
        conf, _ = settings.load("/nonexistent-ccnavi-root")
        # 計画の検査（種類の定義を読む）はここでは見ない
        with mock.patch.object(agree, "plan_problems", return_value=[]):
            found = agree.revision_problems(
                "/nonexistent-ccnavi-root", conf, revised, current, None
            )
        self.assertTrue(any("課題番号" in p.detail for p in found), found)


if __name__ == "__main__":
    unittest.main()
