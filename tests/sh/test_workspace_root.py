"""ccnavi_workspace が、プロジェクトのコピーではなくワークスペースルートを選ぶこと。

プロジェクト（projects/<名前>/）が `.ccnavi/scripts/` のコピーを持っていても、cwd がその中なら、
cwd から登って最初にヒットするのはプロジェクトになる。
そのまま根にすると logs/ や state がプロジェクトの
中にできるので、`projects/` の親にもワークスペースがあれば、そちらを根にする。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import SH_SCRIPTS, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")


def put_scripts(root):
    dest = os.path.join(root, ".ccnavi", "scripts")
    os.makedirs(dest, exist_ok=True)
    for name in common_sh(SH_SCRIPTS):
        shutil.copy(os.path.join(SH_SCRIPTS, name), dest)
    with open(os.path.join(dest, "probe.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write('. "$(dirname "$0")/ccnavi-common.sh"\nccnavi_workspace\n')
    return os.path.join(dest, "probe.sh")


@unittest.skipUnless(SHELL, "sh が無い")
class WorkspaceRootIsNotAProjectCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = os.path.realpath(self.tmp)
        self.project = os.path.join(self.ws, "projects", "p")
        os.makedirs(os.path.join(self.project, "src"))
        self.script = put_scripts(self.ws)

    def root_from(self, script, cwd):
        env = {k: v for k, v in os.environ.items() if k != "CCNAVI_WORKSPACE"}
        result = subprocess.run(
            [SHELL, script], cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
        return os.path.realpath(result.stdout.strip())

    def test_project_without_a_copy(self):
        self.assertEqual(self.root_from(self.script, self.project), self.ws)

    def test_project_with_its_own_copy(self):
        put_scripts(self.project)
        self.assertEqual(self.root_from(self.script, self.project), self.ws)

    def test_a_subdirectory_of_a_project_with_its_own_copy(self):
        put_scripts(self.project)
        self.assertEqual(self.root_from(self.script, os.path.join(self.project, "src")), self.ws)

    def test_a_project_copy_run_from_inside_the_project(self):
        copy = put_scripts(self.project)
        self.assertEqual(self.root_from(copy, self.project), self.ws)

    def test_a_project_outside_any_workspace_is_its_own_root(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        lone = os.path.join(outside, "projects", "p")
        os.makedirs(lone)
        copy = put_scripts(lone)
        self.assertEqual(self.root_from(copy, lone), lone)

    def test_a_copy_in_a_worktree_is_not_a_root(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "x")
        os.makedirs(tree)
        copy = put_scripts(tree)
        self.assertEqual(self.root_from(copy, tree), self.ws)

    def test_a_directory_that_merely_has_projects_in_its_path(self):
        # `projects/` の下でも、親にワークスペースが無ければ、最初にヒットしたコピーが根のまま。
        other = os.path.join(self.ws, "projects", "p", "projects", "q")
        os.makedirs(other)
        put_scripts(other)
        self.assertEqual(self.root_from(self.script, other), os.path.realpath(other))


if __name__ == "__main__":
    unittest.main()
