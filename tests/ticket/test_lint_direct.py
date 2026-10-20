"""`--lint` の直接作業の検査（lint_direct）の受入テスト。

記録の無いワークツリー・壊れた記録・横で作業する指定・片付け残り・旧形式の marker を、
実際の git のリポジトリで確かめる。
"""

from __future__ import annotations

import os
import unittest

from ccnavi.entry import lint_direct
from ccnavi.infra import settings
from ccnavi.policy.rules import SEVERITY_INFO, SEVERITY_WARN
from tests.ticket import test_stop_nudge as base
from tests.ticket.test_ticket import git, write


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in sorted(vars(LintDirectTest)):
        if name.startswith("test_"):
            suite.addTest(LintDirectTest(name))
    return suite


class LintDirectTest(base.StopNudgeTest):
    def setUp(self):
        super().setUp()
        exclude = git(self.root, "rev-parse", "--git-path", "info/exclude").strip()
        write(os.path.join(self.root, exclude), "/logs/\n/.ccnavi/config/\n")

    def common(self):
        return os.path.realpath(
            os.path.join(self.root, git(self.root, "rev-parse", "--git-common-dir").strip())
        )

    def record(self, name, **over):
        fields = {
            "version": "4",
            "base_branch": "main",
            "base_sha": "x",
            "snap_sha": "x",
            "side_by_side": "no",
            "main_tree": os.path.realpath(self.root),
            "common_dir": self.common(),
        }
        fields.update(over)
        text = "".join(f"{k} {v}\n" for k, v in fields.items() if v is not None)
        write(os.path.join(self.common(), "ccnavi-direct", name), text)

    def problems(self):
        return lint_direct._direct(settings.Settings(projects=""), self.root)

    def said(self, severity, needle, name=""):
        found = [
            p
            for p in self.problems()
            if p.severity == severity and needle in p.detail and name in p.rule
        ]
        self.assertTrue(found, [(p.severity, p.rule, p.detail) for p in self.problems()])
        return found

    def test_a_worktree_without_a_record_is_info(self):
        self.worktree("w1", "main")
        self.said(SEVERITY_INFO, "--adopt", "w1")

    def test_a_worktree_with_a_record_is_not_reported(self):
        self.worktree("w1", "main")
        self.record("w1")
        self.assertEqual([], [p for p in self.problems() if "/w1)" in p.rule])

    def test_a_broken_record_is_warn(self):
        self.worktree("w1", "main")
        self.record("w1", snap_sha=None)
        self.said(SEVERITY_WARN, "snap_sha がありません")
        self.record("w1", version="3")
        self.said(SEVERITY_WARN, "版が 4 ではありません")

    def test_a_valid_side_by_side_record_is_info(self):
        self.worktree("w1", "main")
        self.record("w1", side_by_side="yes")
        self.said(SEVERITY_INFO, "横で作業する指定が有効")

    def test_a_record_without_worktree_or_branch_is_info(self):
        self.record("gone")
        self.said(SEVERITY_INFO, "作業ワークツリーもブランチもありません")

    def test_leftovers_are_info(self):
        os.makedirs(os.path.join(self.root, "logs", "direct-tmp", "self", "t1"))
        git(
            self.root,
            "update-ref",
            "refs/ccnavi/direct/t1/c",
            git(self.root, "rev-parse", "HEAD").strip(),
        )
        write(
            os.path.join(self.common(), "ccnavi-integrate", "t1", "marker"),
            "version 4\nhead x\n",
        )
        self.said(SEVERITY_INFO, "一時ワークツリー")
        self.said(SEVERITY_INFO, "refs/ccnavi/direct/t1/c")
        self.said(SEVERITY_INFO, "marker")

    def test_an_old_format_marker_is_warn(self):
        write(os.path.join(self.common(), "ccnavi-integrate", "o1", "marker"), "state staged\n")
        self.said(SEVERITY_WARN, "旧形式")


if __name__ == "__main__":
    unittest.main()
