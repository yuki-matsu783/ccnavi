"""振り分けの sh（scripts/ccnavi-launcher.sh）と、機械の語（ccnavi/platformtag.py）。

配布先では、hook が起動するのは sh で、実体はその隣の `<os>-<arch>/` に並ぶ。語を
読む場所は 3 つ（sh、platformtag、ccnavi-setup.sh）あり、どれかだけずれると
配った場所と探す場所が食い違う。ここでは sh を外から動かし、選んだ置き場を見る。

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
LAUNCHER = os.path.join(ROOT, "scripts", "ccnavi-launcher.sh")
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


@unittest.skipIf(os.name == "nt", "偽の uname と sh の実行ファイルを PATH で差し替える")
@unittest.skipUnless(SHELL, "sh も bash も見つからない")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-launcher-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.bin = os.path.join(self.dir, ".ccnavi", "bin")
        self.launcher = os.path.join(self.bin, "ccnavi")
        os.makedirs(self.bin)
        shutil.copy(LAUNCHER, self.launcher)
        os.chmod(self.launcher, 0o755)
        self.fake = os.path.join(self.dir, "fake-path")

    def build(self, target, name="ccnavi"):
        """組み立ての代わり。どの置き場から起動されたかと、引数と標準入力を返す。"""
        write(
            os.path.join(self.bin, target, name),
            f'#!/bin/sh\nprintf "%s|%s|%s" "{target}/{name}" "$*" "$(cat)"\n',
        )

    def pretend(self, uname_sm):
        write(os.path.join(self.fake, "uname"), f'#!/bin/sh\necho "{uname_sm}"\n')

    def run_launcher(self, *args, stdin="{}", fake=True):
        env = dict(os.environ)
        if fake:
            env["PATH"] = self.fake + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [self.launcher, *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def test_starts_the_build_for_this_machine_in_the_same_words_as_platformtag(self):
        """偽の uname を使わない。sh の語と platformtag の語が揃っていることを見る。"""
        self.build(platformtag.host_target())
        self.build("haiku-riscv64")
        result = self.run_launcher(fake=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(platformtag.host_target() + "/"), result.stdout)

    def test_passes_arguments_and_the_payload_through(self):
        self.pretend("Linux x86_64")
        self.build("linux-x86_64")
        result = self.run_launcher("--root", "/a b", "ticket", stdin='{"hook_event_name":"Stop"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, 'linux-x86_64/ccnavi|--root /a b ticket|{"hook_event_name":"Stop"}'
        )

    def test_git_bash_on_windows_starts_the_exe(self):
        self.pretend("MINGW64_NT-10.0-26100 x86_64")
        self.build("windows-x86_64", "ccnavi.exe")
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("windows-x86_64/ccnavi.exe|"), result.stdout)

    def test_arm64_mac_prefers_its_own_build(self):
        self.pretend("Darwin arm64")
        self.build("darwin-x86_64")
        self.build("darwin-arm64")
        result = self.run_launcher()
        self.assertTrue(result.stdout.startswith("darwin-arm64/"), result.stdout)

    def test_arm64_mac_falls_back_to_the_translated_build(self):
        self.pretend("Darwin arm64")
        self.build("darwin-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("darwin-x86_64/"), result.stdout)

    def test_arm64_linux_does_not_fall_back(self):
        self.pretend("Linux aarch64")
        self.build("linux-x86_64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)

    def test_says_which_machine_has_no_build_and_exits_127(self):
        """実行ファイルそのものが無いときにシェルが返すのと同じ値で終わる。"""
        self.pretend("Linux x86_64")
        self.build("darwin-arm64")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertIn("linux-x86_64", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
