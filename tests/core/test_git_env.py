"""走った機械の git の設定が、テストから締め出されているかを確かめる。

締め出しそのものは `tests/__init__.py` の `_block_host_git_config` が置く。ここは
それが有効かを見る側で、無効になる経路が 3 つあるので 3 つとも見る。

1. 締め出しを外す（`tests/__init__.py` から消える、環境変数の名前が変わる）
2. `tests/inproc.py` が環境を空にするときに巻き添えで消す。ccnavi は判定の中で
   git を起こす（`go/internal/infra/git`）ので、この経路だけがホストの設定を読み直す
3. 検査対象の sh が環境を消毒するときに巻き添えで消す。`ccnavi-git.sh` は
   `GIT_CONFIG_COUNT` などを unset していて、そこに名前が足されると気づかないうちに外れる

3 のぶんは `tests/sh/test_gitwrap.py` が持つ（ラッパーを起こす道具があちらにある）。

締め出さないと何が起きるかは、2 つとも実際に起きている。`init.defaultBranch = main` を
持つ機械では `tests/guard/test_post.py` が失敗し、`commit.gpgsign = true` を持つ機械では
commit が 10 倍遅くなる。どちらも「テストが緩む」ではなく「テストの答えが機械で変わる」。
"""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import GIT_ENV, exe
from tests.inproc import run_ccnavi


def git_out(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    return done.stdout.strip()


class BlocksTheHostConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-gitenv-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_the_block_points_at_a_file_that_exists(self):
        """指し先が無いと git は何も出さずに「設定なし」として進み、締め出せたように見える。

        見えるだけで、名前を間違えたときも同じ見え方になる。実在を確かめておくと、
        名前を取り違えた回に気づける。
        """
        self.assertTrue(os.path.isfile(GIT_ENV["GIT_CONFIG_GLOBAL"]))
        self.assertTrue(os.path.isfile(GIT_ENV["GIT_CONFIG_SYSTEM"]))

    def test_the_host_global_config_is_not_visible(self):
        """`git config --global --list` に、締め出しの中身より多くが出てこない。

        走った機械の `~/.gitconfig` に何が入っているかは分からないので、内容では
        なく「締め出しのファイルが持つキーだけか」で見る。`user.name` のような
        よくあるキーを名指しすると、そのキーを持たない機械では何も確かめられない。
        """
        listed = git_out(self.dir, "config", "--global", "--list")
        keys = {line.split("=", 1)[0] for line in listed.splitlines() if line}
        self.assertEqual(
            {"init.defaultbranch", "core.excludesfile"}, keys, f"ホストの設定が見えている: {listed}"
        )

    def test_the_host_global_ignore_file_is_not_read(self):
        """`$XDG_CONFIG_HOME/git/ignore` の無視が、テストのリポジトリに効かない。

        Claude Code は `**/.claude/settings.local.json` をここへ書く。効くと、テストが
        `git add -A` で作るリポジトリにその設定ファイルが入らない（#607）。
        """
        xdg = os.path.join(self.dir, "xdg")
        os.makedirs(os.path.join(xdg, "git"))
        with open(os.path.join(xdg, "git", "ignore"), "w", encoding="utf-8") as f:
            f.write("**/.claude/settings.local.json\n")
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".claude"))
        with open(os.path.join(repo, ".claude", "settings.local.json"), "w", encoding="utf-8") as f:
            f.write("{}\n")
        env = {**os.environ, "XDG_CONFIG_HOME": xdg}
        subprocess.run(
            ["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True, env=env
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, env=env)
        listed = subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env
        ).stdout.split()
        self.assertEqual([".claude/settings.local.json"], listed)

    def test_init_makes_the_same_branch_on_every_machine(self):
        """`-b` を渡さない `git init` の既定が、機械にも git の版にも依らない。

        `tests/guard/test_post.py` が `checkout master` する前提がこれ。
        """
        subprocess.run(["git", "init", "--quiet"], cwd=self.dir, check=True, capture_output=True)
        self.assertEqual("master", git_out(self.dir, "branch", "--show-current"))

    def test_commits_are_not_signed(self):
        """署名は入らない。入る機械では commit 1 回が 8 ミリ秒から 90 ミリ秒になる。

        `%G?` は署名の状態。`N` が「署名なし」。
        """
        subprocess.run(["git", "init", "--quiet"], cwd=self.dir, check=True, capture_output=True)
        with open(os.path.join(self.dir, "a.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        for args in (
            ("add", "-A"),
            ("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "s"),
        ):
            subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True)
        self.assertEqual("N", git_out(self.dir, "log", "-1", "--format=%G?"))


class SurvivesTheClearedEnvironmentTest(unittest.TestCase):
    """`run_ccnavi(env=...)` が環境を空にしても、締め出しだけは残る。

    ccnavi は判定の中で git を起こすので、ここで外れるとその経路だけがホストの
    `~/.gitconfig` を読み直す。外れても大半のテストは通ってしまうので、それを確かめるテストが要る。
    実行ファイルに渡す環境は、起こす手前（`tests.inproc` の subprocess.run）で受け取って見る。
    """

    def seen_env(self, env):
        seen = {}

        def record(argv, **kwargs):
            seen.update(kwargs["env"])
            return subprocess.CompletedProcess(argv, 0, "", "")

        exe.path()  # 組み立ては先に済ませる（組み立ても subprocess.run を使う）
        with mock.patch("tests.inproc.subprocess.run", record):
            run_ccnavi(["--help"], env=env, messages=False)
        return seen

    def test_the_block_is_still_there_with_an_empty_env(self):
        seen = self.seen_env({})
        for name, value in GIT_ENV.items():
            self.assertEqual(value, seen.get(name), f"{name} が空の環境で失敗している")

    def test_the_caller_can_still_override_it(self):
        """締め出し方そのものを試すテストが、締め出しに上書きされない。"""
        self.assertEqual(
            "/nowhere", self.seen_env({"GIT_CONFIG_GLOBAL": "/nowhere"}).get("GIT_CONFIG_GLOBAL")
        )


if __name__ == "__main__":
    unittest.main()
