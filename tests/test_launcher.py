"""振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）と、機械の語（ccnavi/platformtag.py）。

sh は `.ccnavi/scripts/` に、実体は `.ccnavi/bin/<os>-<arch>/` に並ぶ（ADR-0043）。sh は自分の
隣ではなく `../bin/` を探す。語を読む場所は 3 つ（sh、platformtag、ccnavi-setup.sh）あり、
どれかだけずれると配った場所と探す場所が食い違う。ここでは sh を外から動かし、選んだ置き場を見る。

sh の原本は環境変数 `CCNAVI_TEST_LAUNCHER` で差し替えられる。人が写す前に
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

from ccnavi import platformtag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


class HostTargetTest(unittest.TestCase):
    def test_names_the_three_systems_in_the_same_words(self):
        self.assertEqual(platformtag.host_target("win32", "AMD64"), "windows-x86_64")
        self.assertEqual(platformtag.host_target("darwin", "arm64"), "darwin-arm64")
        self.assertEqual(platformtag.host_target("linux", "aarch64"), "linux-arm64")
        self.assertEqual(platformtag.host_target("linux", "x86_64"), "linux-x86_64")

    def test_arm64_falls_back_to_x86_64_only_where_it_is_translated(self):
        self.assertEqual(
            platformtag.runnable_targets("darwin-arm64"), ("darwin-arm64", "darwin-x86_64")
        )
        self.assertEqual(
            platformtag.runnable_targets("windows-arm64"), ("windows-arm64", "windows-x86_64")
        )
        self.assertEqual(platformtag.runnable_targets("linux-arm64"), ("linux-arm64",))


class LaunchedExecutableTest(unittest.TestCase):
    """前の形。名前が `ccnavi-launcher.sh` でない sh は、隣の `<os>-<arch>/` を探す（D-2）。

    導入スクリプトを打ち直す前のワークスペース（`.ccnavi/bin/ccnavi` を指したまま）で、
    隣の実体を控え続けるために残す。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-launched-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.launcher = os.path.join(self.dir, "bin", "ccnavi")
        write(self.launcher, "#!/bin/sh\n")

    def test_prefers_its_own_build_over_a_translated_one(self):
        write(os.path.join(self.dir, "bin", "darwin-x86_64", "ccnavi"), "x86\n")
        write(os.path.join(self.dir, "bin", "darwin-arm64", "ccnavi"), "arm\n")
        found = platformtag.launched_executable(self.launcher, "darwin-arm64")
        self.assertEqual(found, os.path.join(self.dir, "bin", "darwin-arm64", "ccnavi"))

    def test_finds_the_exe_spelling(self):
        write(os.path.join(self.dir, "bin", "windows-x86_64", "ccnavi.exe"), "MZ\n")
        found = platformtag.launched_executable(self.launcher, "windows-arm64")
        self.assertEqual(found, os.path.join(self.dir, "bin", "windows-x86_64", "ccnavi.exe"))

    def test_is_empty_when_no_build_runs_here(self):
        write(os.path.join(self.dir, "bin", "linux-x86_64", "ccnavi"), "elf\n")
        self.assertEqual(platformtag.launched_executable(self.launcher, "darwin-arm64"), "")


class LaunchedFromScriptsTest(unittest.TestCase):
    """L5: 名前が `ccnavi-launcher.sh` なら `../bin/` を探す。それ以外は隣（前の形）。

    切り替えの条件は名前だけ（3.3 節）。`binary_clause` と同じ条件で、どちらかだけ
    条件を足すと、守る場所と控える場所が食い違う。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-launched-scripts-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.scripts = os.path.join(self.dir, ".ccnavi", "scripts")
        self.bin = os.path.join(self.dir, ".ccnavi", "bin")

    def launcher_name(self):
        """`platformtag.LAUNCHER_NAME`。無ければこのテストで落とす。"""
        name = getattr(platformtag, "LAUNCHER_NAME", None)
        if name is None:
            self.fail("platformtag.LAUNCHER_NAME が無い")
        return name

    def test_the_launcher_name_is_the_one_placed_in_scripts(self):
        self.assertEqual(self.launcher_name(), "ccnavi-launcher.sh")

    def test_launcher_named_as_such_looks_in_the_bin_next_to_its_directory(self):
        launcher = os.path.join(self.scripts, self.launcher_name())
        write(launcher, "#!/bin/sh\n")
        write(os.path.join(self.bin, "linux-x86_64", "ccnavi"), "elf\n")
        found = platformtag.launched_executable(launcher, "linux-x86_64")
        self.assertEqual(
            os.path.normpath(found), os.path.join(self.bin, "linux-x86_64", "ccnavi"), found
        )

    def test_launcher_named_as_such_falls_back_in_the_same_order_as_the_sh(self):
        launcher = os.path.join(self.scripts, self.launcher_name())
        write(launcher, "#!/bin/sh\n")
        write(os.path.join(self.bin, "windows-x86_64", "ccnavi.exe"), "MZ\n")
        found = platformtag.launched_executable(launcher, "windows-arm64")
        self.assertEqual(
            os.path.normpath(found), os.path.join(self.bin, "windows-x86_64", "ccnavi.exe"), found
        )

    def test_launcher_named_as_such_does_not_look_next_to_itself(self):
        """L3 と揃える。sh が起動しない置き場を、控える場所として返さない。"""
        launcher = os.path.join(self.scripts, self.launcher_name())
        write(launcher, "#!/bin/sh\n")
        write(os.path.join(self.scripts, "linux-x86_64", "ccnavi"), "elf\n")
        self.assertEqual(platformtag.launched_executable(launcher, "linux-x86_64"), "")

    def test_other_names_still_look_next_to_themselves(self):
        """前の形。同じ場所に置いても、名前が違えば `../bin/` は見ない。"""
        self.launcher_name()
        launcher = os.path.join(self.scripts, "ccnavi")
        write(launcher, "#!/bin/sh\n")
        write(os.path.join(self.bin, "linux-x86_64", "ccnavi"), "bin\n")
        write(os.path.join(self.scripts, "linux-x86_64", "ccnavi"), "next\n")
        found = platformtag.launched_executable(launcher, "linux-x86_64")
        self.assertEqual(found, os.path.join(self.scripts, "linux-x86_64", "ccnavi"))


@unittest.skipIf(os.name == "nt", "偽の uname と sh の実行ファイルを PATH で差し替える")
@unittest.skipUnless(SHELL, "sh も bash も見つからない")
class LauncherTest(unittest.TestCase):
    """L1〜L4: `.ccnavi/scripts/ccnavi-launcher.sh` に置いた sh を外から動かす。"""

    def setUp(self):
        if not os.path.isfile(LAUNCHER):
            self.fail(
                f"振り分けの sh が無い: {LAUNCHER}"
                "（写す前は CCNAVI_TEST_LAUNCHER で原本を名指しする）"
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

    def run_launcher(self, *args, stdin="{}", fake=True, argv=None, cwd=None):
        env = dict(os.environ)
        if fake:
            env["PATH"] = self.fake + os.pathsep + env.get("PATH", "")
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

    def test_starts_the_build_for_this_machine_in_the_same_words_as_platformtag(self):
        """偽の uname を使わない。sh の語と platformtag の語が揃っていることを見る。"""
        self.build(platformtag.host_target())
        self.build("haiku-riscv64")
        result = self.run_launcher(fake=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            result.stdout.startswith("bin/" + platformtag.host_target() + "/"), result.stdout
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
        """`$0` に道筋が無い（`here=.`）ときも、1 段上の `bin/` を探す。"""
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
        """隣（`.ccnavi/scripts/<os>-<arch>/`）は配る場所ではない。自己保護の綴りが当たらない。"""
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


if __name__ == "__main__":
    unittest.main()
