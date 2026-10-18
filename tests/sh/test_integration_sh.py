"""統合先の名前を決める順（ccnavi-common-state.sh の ccnavi_integration）。

ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への push の拒否）・
ccnavi-review.sh（マージリクエストの宛先）が同じ関数を読む。順は
CCNAVI_INTEGRATION_BRANCH → `.claude/settings.local.json` の env（実行ファイルの `sync paths` の
`integration` 行が運ぶ。`ccnavi_integration_local`）→ origin/HEAD → origin/main・origin/master。
どれも無ければ「分からない」（終了コード 1）。統合先のコピー（sync/<リポジトリ>/integration/）は
もう読まない。

使い捨てのワークスペースを組み、ccnavi-common.sh を読む sh から関数を呼んで、標準出力と
終了コードだけを見る。実行ファイルは、`sync paths` の答えを決めた代役の sh に差し替える。
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
        self.asked = os.path.join(self.ws, "asked.log")

    def exe(self, integration):
        """`sync paths` に答える実行ファイルの代役。呼ばれるたびに 1 行を記録する。"""
        path = os.path.join(self.ws, "fake-ccnavi")
        write(
            path,
            "#!/bin/sh\n"
            f'echo "$*" >>"{self.asked}"\n'
            'case "$*" in *"sync paths"*)\n'
            "  printf 'approved .ccnavi/approved\\n'\n"
            f"  printf 'integration %s\\n' '{integration}' ;;\n"
            "esac\n",
        )
        os.chmod(path, 0o755)
        return path

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
        return self.run_sh('ccnavi_integration "$2" "$1"\n', tree, **env)

    def assertIntegration(self, expected, tree=None, **env):
        result = self.integration(tree or self.ws, **env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, result.stdout.strip())

    def test_nothing_known_is_exit_1(self):
        """origin/HEAD も origin/main・master も無ければ当てずっぽうで宣言しない。"""
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
        remote_ref(self.ws, "develop-v1.0.0")
        head = "refs/remotes/origin/develop-v1.0.0"
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", head)
        self.assertIntegration("develop-v1.0.0")

    def test_the_local_setting_wins_over_origin_head(self):
        """settings.local.json の統合先（`sync paths` が運ぶ）が origin/HEAD より先。"""
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        remote_ref(self.ws, "main")
        self.assertIntegration("develop-v1.0.0", CCNAVI_BIN_PATH=self.exe("develop-v1.0.0"))

    def test_the_environment_wins_over_the_local_setting(self):
        self.assertIntegration(
            "trunk",
            CCNAVI_BIN_PATH=self.exe("develop-v1.0.0"),
            CCNAVI_INTEGRATION_BRANCH="trunk",
        )
        # 環境変数があれば、実行ファイルには聞かない。
        self.assertFalse(os.path.exists(self.asked))

    def test_an_empty_answer_falls_back_to_the_default_branch(self):
        remote_ref(self.ws, "main")
        self.assertIntegration("main", CCNAVI_BIN_PATH=self.exe(""))

    def test_no_executable_falls_back_to_the_default_branch(self):
        remote_ref(self.ws, "main")
        self.assertIntegration("main", CCNAVI_BIN_PATH=os.path.join(self.ws, "nowhere"))

    def test_the_old_integration_copy_is_not_read(self):
        """統合先のコピー（sync/<リポジトリ>/integration/head）が残っていても読まない。"""
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        remote_ref(self.ws, "main")
        write(
            os.path.join(self.ws, "logs", "state", "sync", "self", "integration", "head"),
            "remote origin\nbranch develop-v1.0.0\nsource default\n",
        )
        self.assertIntegration("main")

    def test_a_worktree_and_a_project_read_the_workspace_setting(self):
        """settings.local.json はワークスペースのもの。ワークツリーもプロジェクトも同じ答えを得る。
        プロジェクトの origin は、設定が無いときだけ使う。"""
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0001")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0001")
        project = os.path.join(self.ws, "projects", "app")
        seed(project)
        remote_ref(project, "main")
        # 設定が無ければ、それぞれの origin から。ワークツリーは元リポジトリの参照を読む。
        self.assertIntegration("main", project)
        remote_ref(self.ws, "trunk")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.assertIntegration("trunk", tree)
        # 設定があれば、どれも設定の名前。
        exe = self.exe("release-2")
        self.assertIntegration("release-2", tree, CCNAVI_BIN_PATH=exe)
        self.assertIntegration("release-2", project, CCNAVI_BIN_PATH=exe)

    def test_the_executable_is_asked_once_outside_a_substitution(self):
        """`$( )` の外で続けて呼べば、実行ファイルには 1 回しか聞かない。"""
        result = self.run_sh(
            'ccnavi_integration_local "$1" >/dev/null || :\n'
            'ccnavi_integration_local "$1" >/dev/null || :\n'
            'ccnavi_integration "$2" "$1" >/dev/null\n',
            self.ws,
            CCNAVI_BIN_PATH=self.exe("develop-v1.0.0"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        with open(self.asked, encoding="utf-8") as f:
            self.assertEqual(1, len(f.read().splitlines()))


if __name__ == "__main__":
    unittest.main()
