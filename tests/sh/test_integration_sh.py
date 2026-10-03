"""統合先の名前を決める順（ccnavi-common.sh の ccnavi_integration。ADR-0093 の D30）。

ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への push の拒否）・
ccnavi-review.sh（マージリクエストの宛先）が同じ関数を読む。順は
CCNAVI_INTEGRATION_BRANCH → ccnavi-sync.sh の控え（sync/<リポジトリ>/integration/head）→
origin/HEAD → origin/main・origin/master。どれも無ければ「分からない」（終了コード 1）。

使い捨てのワークスペースを組み、ccnavi-common.sh を読む sh から関数を呼んで、標準出力と
終了コードだけを見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")


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
    """origin/<名前> を手元に置く（取ってきた後の形。ネットワークには出ない）。"""
    git(repo, "update-ref", f"refs/remotes/origin/{name}", "HEAD")


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-integ-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        shutil.copy(COMMON, scripts)
        seed(self.ws)

    def integration(self, tree, **env):
        full = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        full.update(env)
        script = '. "$1/.ccnavi/scripts/ccnavi-common.sh"\nccnavi_integration "$2" "$1"\n'
        return subprocess.run(
            [SHELL, "-c", script, "sh", self.ws, tree],
            cwd=tree,
            env=full,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def assertIntegration(self, expected, tree=None, **env):
        result = self.integration(tree or self.ws, **env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, result.stdout.strip())

    def record(self, repo_key, branch):
        write(
            os.path.join(self.ws, "logs", "state", "sync", repo_key, "integration", "head"),
            f"remote origin\nbranch {branch}\nsource default\n",
        )

    def test_nothing_known_is_exit_1(self):
        """origin/HEAD も origin/main・master も無ければ当てずっぽうで名乗らない。"""
        result = self.integration(self.ws)
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)

    def test_origin_main_then_master(self):
        remote_ref(self.ws, "master")
        self.assertIntegration("master")
        remote_ref(self.ws, "main")
        self.assertIntegration("main")

    def test_origin_head_wins_over_main(self):
        """デフォルトブランチが develop-v1.0.0 のように固定の並びに無い名前でも読む。"""
        remote_ref(self.ws, "main")
        remote_ref(self.ws, "develop-v1.0.0")
        head = "refs/remotes/origin/develop-v1.0.0"
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", head)
        self.assertIntegration("develop-v1.0.0")

    def test_the_sync_record_wins_over_origin_head(self):
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        remote_ref(self.ws, "main")
        self.record("self", "develop-v1.0.0")
        self.assertIntegration("develop-v1.0.0")

    def test_the_environment_wins_over_the_record(self):
        self.record("self", "develop-v1.0.0")
        self.assertIntegration("trunk", CCNAVI_INTEGRATION_BRANCH="trunk")

    def test_the_record_follows_ccnavi_state(self):
        write(
            os.path.join(self.ws, "elsewhere", "sync", "self", "integration", "head"),
            "branch develop-v2\n",
        )
        self.assertIntegration("develop-v2", CCNAVI_STATE="elsewhere")

    def test_a_worktree_reads_the_record_of_its_repository(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0001")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0001")
        self.record("self", "develop-v1.0.0")
        self.assertIntegration("develop-v1.0.0", tree)

    def test_a_project_reads_its_own_record_and_refs(self):
        """プロジェクトは自分の控えと自分の origin を読み、ワークスペースのものは読まない。"""
        project = os.path.join(self.ws, "projects", "app")
        seed(project)
        self.record("self", "develop-v1.0.0")
        result = self.integration(project)
        self.assertEqual(1, result.returncode, result.stdout)

        remote_ref(project, "main")
        self.assertIntegration("main", project)

        self.record("app", "release-2")
        self.assertIntegration("release-2", project)

        tree = os.path.join(self.ws, ".claude", "worktrees", "i0002")
        git(project, "worktree", "add", "-q", tree, "-b", "i0002")
        self.assertIntegration("release-2", tree)


if __name__ == "__main__":
    unittest.main()
