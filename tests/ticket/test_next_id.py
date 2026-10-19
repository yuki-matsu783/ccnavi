"""`ccnavi ticket next-id [<先頭の語> <slug>]`。issue の無い親の識別子の番号を答える。

番号は、承認待ち・作業中・閉じた親の識別子の番号の最大 + 1。子の識別子は数えない。
引数なしなら番号だけ、`<先頭の語> <slug>` なら識別子を出し、形に合わなければ理由を言って止まる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi
from tests.ticket.test_lint_branch_names import ticket_text, write


class NextIdTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-next-id-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )

    def propose(self, name, **kwargs):
        write(
            os.path.join(self.ws, "wip", "proposals", "todo", name + ".md"),
            ticket_text(name, **kwargs),
        )

    def place(self, state, name, **kwargs):
        write(
            os.path.join(self.ws, ".ccnavi", "approved", state, name + ".md"),
            ticket_text(name, approved=True, **kwargs),
        )

    def next_id(self, *words, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.update(env or {})
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            ["--root", self.ws, "ticket", "next-id", *words],
            input="",
            env=environment,
        )

    def test_first_number_is_one(self):
        result = self.next_id()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1\n", result.stdout)

    def test_counts_proposals_doing_and_done(self):
        self.propose("feature-3-a")
        self.place("doing", "fix-7-b")
        self.place("done", "chore-12-c")
        self.assertEqual("13\n", self.next_id().stdout)

    def test_children_do_not_count(self):
        self.place("doing", "feature-4-a")
        self.place("doing", "feature-4-a-01-01", parent="feature-4-a")
        self.assertEqual("5\n", self.next_id().stdout)

    def test_builds_identifier(self):
        self.propose("feature-9-a")
        result = self.next_id("chore", "skill-commit-push")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("chore-10-skill-commit-push\n", result.stdout)

    def test_japanese_slug(self):
        self.assertEqual("feature-1-統合先の解決\n", self.next_id("feature", "統合先の解決").stdout)

    def test_refuses_unusable_forms(self):
        for words, why in (
            (("zzz", "x"), "先頭の語 zzz は使えない"),
            (("feature", "bad-12"), "末尾が `-<2 桁>`"),
            (("feature", "a/b"), "slug a/b は使えない"),
            (("feature", "ab..c"), ".."),
            (("feature", "a."), "."),
            (("feature", "x.lock"), ".lock"),
            (("feature", "x" * 45), "推奨の長さ"),
            (("feature",), "2 つで実行します。"),
        ):
            with self.subTest(words=words):
                result = self.next_id(*words)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertIn(why, result.stderr)

    def test_works_when_ticket_control_is_disabled(self):
        self.propose("feature-3-a")
        result = self.next_id(env={"CCNAVI_TICKET_CONTROL": "disable"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("4\n", result.stdout)

    def test_counts_local_branches_and_worktree_directories(self):
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-qm",
                "x",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "branch", "chore-12-x"], cwd=self.ws, check=True)
        self.assertEqual("13\n", self.next_id().stdout)
        os.makedirs(os.path.join(self.ws, ".claude", "worktrees", "fix-20-y"))
        self.assertEqual("21\n", self.next_id().stdout)
        # origin の追跡ブランチは数えない。
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/feature-99-z", "HEAD"],
            cwd=self.ws,
            check=True,
        )
        self.assertEqual("21\n", self.next_id().stdout)

    def test_a_tag_with_the_branch_name_and_child_shaped_names_still_count(self):
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-qm",
                "x",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "branch", "feature-50-a"], cwd=self.ws, check=True)
        subprocess.run(["git", "tag", "feature-50-a"], cwd=self.ws, check=True)
        self.assertEqual("51\n", self.next_id().stdout)
        subprocess.run(["git", "branch", "fix-60-x-10-19"], cwd=self.ws, check=True)
        self.assertEqual("61\n", self.next_id().stdout)

    def test_a_workspace_that_is_not_a_git_repository_is_not_an_error(self):
        shutil.rmtree(os.path.join(self.ws, ".git"))
        result = self.next_id()
        self.assertEqual((0, "1\n"), (result.returncode, result.stdout), result.stderr)

    def test_a_repository_that_cannot_be_counted_gives_exit_2_and_no_number(self):
        project = os.path.join(self.ws, "projects", "p")
        os.makedirs(project)
        # `.git` があるが中身が壊れていて、ブランチを数えられないプロジェクト。
        write(os.path.join(project, ".git"), "gitdir: /nonexistent/ccnavi-next-id\n")
        for words in ((), ("feature", "x")):
            with self.subTest(words=words):
                result = self.next_id(*words)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertEqual("", result.stdout)
                self.assertIn("ユーザに確かめてください", result.stderr)


if __name__ == "__main__":
    unittest.main()
