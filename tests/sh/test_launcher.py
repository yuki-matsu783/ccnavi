"""振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）。

sh は `.ccnavi/scripts/` に、実体は `.ccnavi/bin/<os>-<arch>/` に並ぶ。sh は `../bin/` を探す。
語を読む場所は 3 つ（sh、go/internal/infra/platformtag、ccnavi-setup.sh）あり、
どれかだけ変わると配った場所と探す場所が食い違う。ここでは sh を外から動かし、
選んだ置き場を見る。platformtag の探す順は go test（platformtag_test.go）が確かめる。

sh の原本は環境変数 `CCNAVI_TEST_LAUNCHER` で差し替えられる。ユーザがコピーする前に
`wip/design/scripts/ccnavi-launcher.sh` を名指しで確かめるため（相対ならリポジトリの
ルートから読む）。

機械を差し替えるときは、PATH の先頭に偽の uname を置く。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.sh.install_harness import host_target

LAUNCHER = os.path.join(
    ROOT,
    os.environ.get("CCNAVI_TEST_LAUNCHER")
    or os.path.join(".ccnavi", "scripts", "ccnavi-launcher.sh"),
)
SHELL = shutil.which("sh") or shutil.which("bash")


def write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


@unittest.skipIf(os.name == "nt", "偽の uname と sh の実行ファイルを PATH で差し替える")
@unittest.skipUnless(SHELL, "sh も bash も見つからない")
class LauncherTest(unittest.TestCase):
    """L1〜L4: `.ccnavi/scripts/ccnavi-launcher.sh` に置いた sh を外から動かす。"""

    def setUp(self):
        if not os.path.isfile(LAUNCHER):
            self.fail(
                f"振り分けの sh が無い: {LAUNCHER}"
                "（コピーする前は CCNAVI_TEST_LAUNCHER で原本を名指しする）"
            )
        self.dir = tempfile.mkdtemp(prefix="ccnavi-launcher-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.scripts = os.path.join(self.dir, ".ccnavi", "scripts")
        self.bin = os.path.join(self.dir, ".ccnavi", "bin")
        self.launcher = os.path.join(self.scripts, "ccnavi-launcher.sh")
        os.makedirs(self.scripts)
        shutil.copy(LAUNCHER, self.launcher)
        os.chmod(self.launcher, 0o755)
        self.fake = os.path.join(self.dir, "fake-path")

    def build(self, target, name="ccnavi", *, place=None, code=0):
        """組み立ての代わり。どの置き場から起動されたかと、引数と標準入力を返す。

        `place` を省くと `.ccnavi/bin/`。終了コードは `code` で返す。
        """
        where = "bin" if place is None else os.path.basename(place)
        write(
            os.path.join(place or self.bin, target, name),
            f'#!/bin/sh\nprintf "%s|%s|%s" "{where}/{target}/{name}" "$*" "$(cat)"\nexit {code}\n',
        )

    def pretend(self, uname_sm):
        write(os.path.join(self.fake, "uname"), f'#!/bin/sh\necho "{uname_sm}"\n')

    def run_launcher(self, *args, stdin="{}", fake=True, argv=None, cwd=None, path_dirs=()):
        env = dict(os.environ)
        if fake:
            env["PATH"] = self.fake + os.pathsep + env.get("PATH", "")
        if path_dirs:
            env["PATH"] = os.pathsep.join([*path_dirs, env.get("PATH", "")])
        return subprocess.run(
            [*(argv or [self.launcher]), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
        )

    # L1

    def test_starts_the_build_for_this_machine_in_the_same_words_as_the_executable(self):
        """偽の uname を使わない。sh の語と実行ファイル（platformtag）の語が揃っていることを見る。"""
        self.build(host_target())
        self.build("haiku-riscv64")
        result = self.run_launcher(fake=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            result.stdout.startswith("bin/" + host_target() + "/"), result.stdout
        )

    def test_passes_arguments_and_the_payload_through(self):
        self.pretend("Linux x86_64")
        self.build("linux-x86_64")
        result = self.run_launcher("--root", "/a b", "ticket", stdin='{"hook_event_name":"Stop"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, 'bin/linux-x86_64/ccnavi|--root /a b ticket|{"hook_event_name":"Stop"}'
        )

    def test_passes_the_exit_code_through(self):
        """hook は実体の終了コードで判定を読む（2 は拒否）。sh が書き換えない。"""
        self.pretend("Linux x86_64")
        self.build("linux-x86_64", code=2)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/linux-x86_64/"), result.stdout)

    def test_finds_the_bin_from_relative_spellings(self):
        """`$0` にディレクトリの部分が無い（`here=.`）ときも、1 段上の `bin/` を探す。"""
        self.pretend("Linux x86_64")
        self.build("linux-x86_64")
        cases = (
            (
                "ワークスペースルートから",
                [SHELL, os.path.join(".ccnavi", "scripts", "ccnavi-launcher.sh")],
                self.dir,
            ),
            ("sh の置き場から", [SHELL, "ccnavi-launcher.sh"], self.scripts),
        )
        for label, argv, cwd in cases:
            with self.subTest(label):
                result = self.run_launcher("x", argv=argv, cwd=cwd)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(
                    result.stdout.startswith("bin/linux-x86_64/ccnavi|x|"), result.stdout
                )

    def test_worktree_without_bin_starts_the_build_in_the_main_root(self):
        """ワークツリーの中の launcher には bin/ が無い。git の共通ディレクトリから本体のルートの bin/ を使う。"""
        git = shutil.which("git")
        if not git:
            self.skipTest("git が無い")
        self.pretend("Linux x86_64")
        self.build("linux-x86_64")
        env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@b"}
        env.update(GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@b")
        for args in (
            ["init", "-q"],
            ["add", ".ccnavi/scripts"],
            ["commit", "-q", "-m", "i"],
            ["worktree", "add", "-q", os.path.join(self.dir, "wt")],
        ):
            subprocess.run([git, "-C", self.dir, *args], check=True, env=env, capture_output=True)
        wt_launcher = os.path.join(self.dir, "wt", ".ccnavi", "scripts", "ccnavi-launcher.sh")
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "wt", ".ccnavi", "bin")))
        result = self.run_launcher("x", argv=[SHELL, wt_launcher])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/linux-x86_64/ccnavi|x|"), result.stdout)

    def test_no_build_anywhere_still_ends_with_127(self):
        """本体のルートにも実体が無いときは、本体の launcher に渡し直しても戻らず 127 で終わる。"""
        self.pretend("Linux x86_64")
        result = self.run_launcher(argv=[SHELL, self.launcher], path_dirs=())
        self.assertEqual(result.returncode, 127, result.stderr)

    def test_git_bash_on_windows_starts_the_exe(self):
        self.pretend("MINGW64_NT-10.0-26100 x86_64")
        self.build("windows-x86_64", "ccnavi.exe")
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/windows-x86_64/ccnavi.exe|"), result.stdout)

    # L2

    def test_arm64_mac_prefers_its_own_build(self):
        self.pretend("Darwin arm64")
        self.build("darwin-x86_64")
        self.build("darwin-arm64")
        result = self.run_launcher()
        self.assertTrue(result.stdout.startswith("bin/darwin-arm64/"), result.stdout)

    def test_arm64_mac_falls_back_to_the_translated_build(self):
        self.pretend("Darwin arm64")
        self.build("darwin-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/darwin-x86_64/"), result.stdout)

    def test_arm64_windows_prefers_its_own_build(self):
        self.pretend("MINGW64_NT-10.0-26100 arm64")
        self.build("windows-x86_64", "ccnavi.exe")
        self.build("windows-arm64", "ccnavi.exe")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/windows-arm64/ccnavi.exe|"), result.stdout)

    def test_arm64_windows_falls_back_to_the_translated_build(self):
        self.pretend("MINGW64_NT-10.0-26100 arm64")
        self.build("windows-x86_64", "ccnavi.exe")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/windows-x86_64/ccnavi.exe|"), result.stdout)

    def test_arm64_linux_does_not_fall_back(self):
        self.pretend("Linux aarch64")
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, "")

    # L3

    def test_does_not_start_a_build_placed_next_to_itself(self):
        """隣（`.ccnavi/scripts/<os>-<arch>/`）は配る場所ではない。自己防衛のパスがヒットしない。"""
        self.pretend("Linux x86_64")
        self.build("linux-x86_64", place=self.scripts)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_prefers_the_bin_even_when_a_build_sits_next_to_itself(self):
        self.pretend("Linux x86_64")
        self.build("linux-x86_64", place=self.scripts)
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin/linux-x86_64/"), result.stdout)

    # L4

    def test_says_which_machine_has_no_build_and_where_it_looked_and_exits_127(self):
        """実行ファイルそのものが無いときにシェルが返すのと同じ値で終わる。

        探した場所は `..` を含むまま出る（2.1 節。正規化しない）。
        """
        self.pretend("Linux x86_64")
        self.build("darwin-arm64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertIn("linux-x86_64", result.stderr)
        self.assertIn(self.scripts + "/../bin/", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_names_the_unknown_machine_when_uname_says_nothing_useful(self):
        self.pretend("Haiku riscv64")
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertIn("unknown-unknown", result.stderr)
        self.assertIn(self.scripts + "/../bin/", result.stderr)

    # L6: bin/ に無いときだけ PATH の ccnavi を使う

    def on_path(self, name="ccnavi", where="from-path"):
        """PATH に通す置き場を作って返す。"""
        place = os.path.join(self.dir, "on-path")
        write(
            os.path.join(place, name),
            f'#!/bin/sh\nprintf "%s|%s" "{where}" "$*"\n',
        )
        return place

    def test_falls_back_to_the_ccnavi_on_path_when_bin_has_no_build(self):
        self.pretend("Linux x86_64")
        place = self.on_path()
        result = self.run_launcher("x", path_dirs=[place])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "from-path|x")

    def test_bin_wins_over_the_ccnavi_on_path(self):
        self.pretend("Linux x86_64")
        self.build("linux-x86_64")
        place = self.on_path()
        result = self.run_launcher(path_dirs=[place])
        self.assertTrue(result.stdout.startswith("bin/linux-x86_64/"), result.stdout)

    def test_finds_the_exe_spelling_on_path(self):
        self.pretend("Linux x86_64")
        place = self.on_path("ccnavi.exe", "exe-on-path")
        result = self.run_launcher(path_dirs=[place])
        self.assertEqual(result.stdout, "exe-on-path|")

    def test_skips_relative_and_empty_path_entries(self):
        """cwd に置かれた ccnavi を拾わない。相対の項目と空の項目は結果が cwd で変わる。"""
        self.pretend("Linux x86_64")
        write(os.path.join(self.dir, "evil", "ccnavi"), '#!/bin/sh\nprintf "evil"\n')
        for entry in ("evil", "", "."):
            with self.subTest(entry=entry):
                result = self.run_launcher(path_dirs=[entry], cwd=self.dir)
                self.assertEqual(result.returncode, 127, result.stdout)
                self.assertEqual(result.stdout, "")

    def test_skips_a_ccnavi_that_is_the_launcher_itself(self):
        """ccnavi という名前で張ったリンクを起動すると自分に戻る。同じファイルは飛ばす。"""
        self.pretend("Linux x86_64")
        place = os.path.join(self.dir, "on-path")
        os.makedirs(place)
        try:
            os.symlink(self.launcher, os.path.join(place, "ccnavi"))
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない")
        result = subprocess.run(
            [os.path.join(place, "ccnavi")],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                **os.environ,
                "PATH": self.fake + os.pathsep + place + os.pathsep + os.environ["PATH"],
            },
            timeout=20,
        )
        self.assertEqual(result.returncode, 127, result.stdout + result.stderr)

    def test_the_127_message_says_path_was_looked_at_too(self):
        self.pretend("Linux x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertIn("PATH", result.stderr)

    def test_keeps_ifs_and_globbing_for_the_rest_of_the_script(self):
        """PATH の探索で IFS と set -f を変える。戻さないと次の呼び出しの語分割が壊れる。"""
        self.pretend("Linux x86_64")
        place = self.on_path()
        self.assertTrue(os.path.isdir(place))
        # 空白を含む PATH の項目でも探せる（項目は : だけで分ける）。
        spaced = os.path.join(self.dir, "a b")
        write(os.path.join(spaced, "ccnavi"), '#!/bin/sh\nprintf "spaced"\n')
        result = self.run_launcher(path_dirs=[spaced])
        self.assertEqual(result.stdout, "spaced")


if __name__ == "__main__":
    unittest.main()
