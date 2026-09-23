"""走った機械の git の設定が、テストから締め出されているかを見張る。

締め出しそのものは `tests/__init__.py` の `_block_host_git_config` が置く。ここは
それが効いているかを見る側で、効かなくなる道が 3 つあるので 3 つとも見る。

1. 締め出しを外す（`tests/__init__.py` から消える、環境変数の名前が変わる）
2. `tests/inproc.py` が環境を空にするときに巻き添えで落とす。ccnavi は判定の中で
   git を起こす（`ccnavi/gitcmd.py`）ので、この道だけがホストの設定を読み直す
3. 検査対象の sh が環境を消毒するときに巻き添えで落とす。`ccnavi-git.sh` は
   `GIT_CONFIG_COUNT` などを unset していて、そこに名前が足されると黙って外れる

3 のぶんは `tests/sh/test_gitwrap.py` が持つ（ラッパーを起こす道具があちらにある）。

締め出さないと何が起きるかは、実害が 2 つとも出ている。`init.defaultBranch = main` を
持つ機械では `tests/guard/test_post.py` が落ち、`commit.gpgsign = true` を持つ機械では
commit が 10 倍遅くなる。どちらも「テストが緩む」ではなく「テストの答えが機械で変わる」。
"""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import GIT_ENV
from tests.inproc import run_ccnavi


def git_out(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    return done.stdout.strip()


class BlocksTheHostConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-gitenv-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_the_block_points_at_a_file_that_exists(self):
        """指し先が無いと git は黙って「設定なし」として進み、締め出せたように見える。

        見えるだけで、名前を間違えたときも同じ見え方になる。実在を確かめておくと、
        綴りを取り違えた回に気づける。
        """
        self.assertTrue(os.path.isfile(GIT_ENV["GIT_CONFIG_GLOBAL"]))
        self.assertTrue(os.path.isfile(GIT_ENV["GIT_CONFIG_SYSTEM"]))

    def test_the_host_global_config_is_not_visible(self):
        """`git config --global --list` に、締め出しの中身より多くが出てこない。

        走った機械の `~/.gitconfig` に何が入っているかは分からないので、内容では
        なく「締め出しのファイルが持つ鍵だけか」で見る。`user.name` のような
        よくある鍵を名指しすると、その鍵を持たない機械では空振りする。
        """
        listed = git_out(self.dir, "config", "--global", "--list")
        keys = {line.split("=", 1)[0] for line in listed.splitlines() if line}
        self.assertEqual({"init.defaultbranch"}, keys, f"ホストの設定が見えている: {listed}")

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

    ccnavi は判定の中で git を起こすので、ここで落ちるとその道だけがホストの
    `~/.gitconfig` を読み直す。落ちても大半のテストは通ってしまうので、見張りが要る。
    """

    def test_the_block_is_still_there_with_an_empty_env(self):
        seen = {}

        def record(stdin, stdout, stderr, argv):
            seen.update(os.environ)
            return 0

        with mock.patch("ccnavi.cli.run", record):
            run_ccnavi(["--help"], env={})
        for name, value in GIT_ENV.items():
            self.assertEqual(value, seen.get(name), f"{name} が空の環境で落ちている")

    def test_the_caller_can_still_override_it(self):
        """塞ぎ方そのものを試すテストが、塞ぎに上書きされない。"""
        seen = {}

        def record(stdin, stdout, stderr, argv):
            seen.update(os.environ)
            return 0

        with mock.patch("ccnavi.cli.run", record):
            run_ccnavi(["--help"], env={"GIT_CONFIG_GLOBAL": "/nowhere"})
        self.assertEqual("/nowhere", seen.get("GIT_CONFIG_GLOBAL"))


if __name__ == "__main__":
    unittest.main()
