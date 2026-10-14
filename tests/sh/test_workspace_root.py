"""ccnavi_workspace が、プロジェクトの写しではなく実行中の sh の置き場をワークスペースルートに選ぶこと。

プロジェクト（projects/<名前>/）が `.ccnavi/scripts/` の写しを持っていても、ワークスペースの sh を
プロジェクトの中から打てば、根はワークスペースになる。cwd から登るだけだとプロジェクトで止まり、
logs/ がプロジェクトの中にできる。
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


@unittest.skipUnless(SHELL, "sh が無い")
class WorkspaceRootIsWhereTheRunningShLives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = os.path.realpath(self.tmp)
        self.project = os.path.join(self.ws, "projects", "p")
        os.makedirs(self.project)
        put_scripts(self.ws)

    def run_probe(self, script, cwd):
        env = {k: v for k, v in os.environ.items() if k != "CCNAVI_WORKSPACE"}
        result = subprocess.run(
            [SHELL, script], cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
        return os.path.realpath(result.stdout.strip())

    def test_project_without_a_copy(self):
        script = os.path.join(self.ws, ".ccnavi", "scripts", "probe.sh")
        self.assertEqual(self.run_probe(script, self.project), self.ws)

    def test_project_with_its_own_copy(self):
        put_scripts(self.project)
        script = os.path.join(self.ws, ".ccnavi", "scripts", "probe.sh")
        self.assertEqual(self.run_probe(script, self.project), self.ws)

    def test_the_projects_own_copy_is_its_own_root(self):
        put_scripts(self.project)
        script = os.path.join(self.project, ".ccnavi", "scripts", "probe.sh")
        self.assertEqual(self.run_probe(script, self.project), os.path.realpath(self.project))

    def test_a_copy_in_a_worktree_is_not_a_root(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "x")
        os.makedirs(tree)
        put_scripts(tree)
        script = os.path.join(tree, ".ccnavi", "scripts", "probe.sh")
        self.assertEqual(self.run_probe(script, tree), self.ws)


if __name__ == "__main__":
    unittest.main()
