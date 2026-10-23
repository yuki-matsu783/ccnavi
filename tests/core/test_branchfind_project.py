"""依頼文の URL のリポジトリ（`Ref.repo`）を projects/ のプロジェクトと比べる。"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.infra import settings, tree
from ccnavi.tickets import branchfind
from ccnavi.tickets.branchfind import ISSUE, Ref


def make_repo(path: str, url: str) -> None:
    os.makedirs(os.path.join(path, ".git"))
    with open(os.path.join(path, ".git", "config"), "w", encoding="utf-8") as f:
        f.write(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n')


class ProjectOfRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        make_repo(self.root, "https://host.example/ryutec/claudecode-navi.git")
        make_repo(
            os.path.join(self.root, "projects", "guided-setup-tour-extension"),
            "git@host.example:ryutec/r-tech-lab/y-taniyama/Guided-Setup-Tour-Extension.git",
        )
        self.conf = settings.Settings(projects=os.path.join(self.root, "projects"))

    def test_origin_repo_keeps_the_last_two_segments(self):
        proj = os.path.join(self.root, "projects", "guided-setup-tour-extension")
        self.assertEqual(tree.origin_repo(proj), "y-taniyama/Guided-Setup-Tour-Extension")
        self.assertEqual(tree.origin_repo(self.root), "ryutec/claudecode-navi")
        self.assertEqual(tree.origin_repo(os.path.join(self.root, "nothing")), "")

    def test_a_url_repo_finds_its_project_ignoring_case(self):
        found = branchfind.project_of_repo(
            self.conf, self.root, "y-taniyama/guided-setup-tour-extension"
        )
        self.assertEqual(found, "guided-setup-tour-extension")

    def test_the_workspace_own_repo_needs_no_cd(self):
        self.assertEqual(
            branchfind.project_of_repo(self.conf, self.root, "ryutec/claudecode-navi"), ""
        )

    def test_an_unknown_repo_is_none(self):
        self.assertIsNone(branchfind.project_of_repo(self.conf, self.root, "acme/widgets"))

    def lines(self, ref):
        return "\n".join(branchfind._start_lines(self.conf, self.root, [ref]))

    def test_the_guidance_names_the_project_and_cds_into_it(self):
        said = self.lines(Ref(ISSUE, 2, "y-taniyama/guided-setup-tour-extension"))
        sh = settings.script_command(self.root, "ccnavi-start.sh")
        proj = os.path.join(self.root, "projects", "guided-setup-tour-extension")
        self.assertIn(
            f"'cd {settings.shell_path(proj)} && {sh} --issue 2"
            " --repo y-taniyama/guided-setup-tour-extension'",
            said,
        )

    def test_the_workspace_own_repo_cds_to_the_root(self):
        said = self.lines(Ref(ISSUE, 2, "ryutec/claudecode-navi"))
        self.assertIn(f"'cd {settings.shell_path(self.root)} && ", said)

    def test_the_guidance_refuses_to_run_when_the_project_is_not_cloned(self):
        said = self.lines(Ref(ISSUE, 2, "acme/widgets"))
        self.assertIn("clone", said)
        self.assertNotIn("--issue 2 --repo", said)

    def test_a_ref_without_a_repo_keeps_the_plain_command(self):
        said = self.lines(Ref(ISSUE, 2))
        sh = settings.script_command(self.root, "ccnavi-start.sh")
        self.assertIn(f"'{sh} --issue 2'", said)


if __name__ == "__main__":
    unittest.main()
