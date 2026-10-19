"""統合先の名前を決める（ccnavi-common-state.sh の ccnavi_integration）。

ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への push の拒否）・
ccnavi-review.sh（マージリクエストの宛先）が同じ関数を読む。決め方は、そのリポジトリの手元の
origin/HEAD → origin/main・origin/master だけ。どれも無ければ「分からない」（終了コード 1）。

使い捨てのワークスペースを組み、ccnavi-common.sh を読む sh から関数を呼んで、標準出力と
終了コードだけを見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import SH_SCRIPTS, common_sh

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
        full = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
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
        ローカルの main・master で代わりにもしない。"""
        git(self.ws, "branch", "-m", "main")
        result = self.integration(self.ws)
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)

    def test_origin_main_then_master(self):
        remote_ref(self.ws, "master")
        self.assertIntegration("master")
        remote_ref(self.ws, "main")
        self.assertIntegration("main")

    def test_origin_head_wins_over_main(self):
        """デフォルトブランチが develop-v1.0.0 のように固定のリストに無い名前でも読む。"""
        remote_ref(self.ws, "main")
        point_origin_head(self.ws, "develop-v1.0.0")
        self.assertIntegration("develop-v1.0.0")

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
