"""統合先を実行ファイルに聞く口（ccnavi-common-state.sh の ccnavi_integration など）。

決め方（手元の origin/HEAD → origin/main・origin/master）と案内の文は、
実行ファイル（`git integration`）が持ち、go test（integrate の integration_test.go）が見る。
ここでは通しで、sh が答えを読めること、ワークツリーとプロジェクトがそれぞれ
自分のリポジトリで聞くこと、実行ファイルが無いときに「分からない」になることを見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import SH_SCRIPTS, common_sh, exe

SHELL = shutil.which("sh") or shutil.which("bash")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def seed(repo):
    """コミットが 1 件ある使い捨てのリポジトリ。"""
    os.makedirs(repo, exist_ok=True)
    git(repo, "init", "-q", "-b", "work")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    git(repo, "commit", "-q", "--allow-empty", "-m", "seed")


def remote_ref(repo, name):
    """origin/<名前> を手元に置く（取ってきた後の形）。"""
    git(repo, "update-ref", f"refs/remotes/origin/{name}", "HEAD")


def point_origin_head(repo, name):
    remote_ref(repo, name)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{name}")


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-integ-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in common_sh():
            shutil.copy(os.path.join(SH_SCRIPTS, name), scripts)
        seed(self.ws)

    def run_sh(self, script, tree, **env):
        full = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
            "CCNAVI_ENTRY_PATH": exe.path(),
        }
        full.update(env)
        # `$0` に入口のパスを渡す。保護済み sh と同じく、部品は `$0` のディレクトリから読む。
        common = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-common.sh")
        return subprocess.run(
            [SHELL, "-c", f'. "$0"\n{script}', common, self.ws, tree],
            cwd=tree,
            env=full,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def integration(self, tree, **env):
        return self.run_sh('ccnavi_integration "$2"\n', tree, **env)

    def assertIntegration(self, expected, tree=None, **env):
        result = self.integration(tree or self.ws, **env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, result.stdout.strip())

    def test_nothing_known_is_exit_1(self):
        """origin/HEAD も origin/main・master も無ければ当てずっぽうで宣言しない。

        実行ファイルが無いときも同じ。"""
        git(self.ws, "branch", "-m", "main")
        for env in ({}, {"CCNAVI_ENTRY_PATH": os.path.join(self.ws, "no-such-bin")}):
            with self.subTest(env=env):
                result = self.integration(self.ws, **env)
                self.assertEqual(1, result.returncode)
                self.assertEqual("", result.stdout)

    def test_a_worktree_and_a_project_each_read_their_own_repository(self):
        """ワークツリーは元リポジトリの参照を読む。プロジェクトは自分の origin を読む。"""
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0001")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0001")
        project = os.path.join(self.ws, "projects", "app")
        seed(project)
        point_origin_head(project, "trunk")
        point_origin_head(self.ws, "main")
        # ワークスペースの値が main でも、プロジェクトは trunk。
        self.assertIntegration("main", tree)
        self.assertIntegration("trunk", project)

    def test_the_hint_tells_apart_no_origin_and_no_origin_head(self):
        """案内は、origin が無いときと、origin/HEAD が決まらないときで言い分ける。"""
        result = self.run_sh('ccnavi_integration_hint "$2"\n', self.ws)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("origin が無いので統合先が決まらない", result.stdout)
        git(self.ws, "remote", "add", "origin", "https://example.invalid/x.git")
        result = self.run_sh('ccnavi_integration_hint "$2"\n', self.ws)
        self.assertIn("統合先 (origin/HEAD) が決まらない", result.stdout)
        self.assertIn("git remote set-head origin -a", result.stdout)


if __name__ == "__main__":
    unittest.main()
