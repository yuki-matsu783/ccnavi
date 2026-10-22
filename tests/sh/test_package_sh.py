"""scripts/ccnavi-package.sh の受入テスト。

ccnavi のソースに見立てた使い捨てのリポジトリを組み、そこで package を打つ。zip の中身は
zipfile で読む。最後の 1 本は、zip を展開して本物の install（setup つき）で入れ、launcher から
実行ファイルを起動するところまで通す。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from tests import ROOT
from tests.sh.install_harness import REAL_GIT, count_next, script, write_exec
from tests.sh.test_doctor_sh import THIS_MACHINE

PACKAGE = script("ccnavi-package.sh")
FAKE_EXE = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "--version)\n"
    "\tprintf 'ccnavi 0.1.0\\ncommit: COMMIT\\nbuilt: yes\\ncompat: 18\\n'\n"
    "\t;;\n"
    "--lint)\n"
    "\tprintf 'error 0 件、warn 0 件、info 0 件\\n'\n"
    "\t;;\n"
    "esac\n"
    "exit 0\n"
)


def have_all() -> bool:
    need = ("sh", "git", "jq", "zip", "unzip", "find", "cp", "sha256sum")
    return all(shutil.which(n) for n in need) and os.name == "posix"


def write(path: str, text: str = "x\n") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(root: str, *args: str) -> str:
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        [REAL_GIT, "-C", root, *cfg, *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@unittest.skipUnless(have_all(), "sh・git・jq・zip・unzip・sha256sum が要る")
class PackageTest(unittest.TestCase):
    CHROME = os.path.join("extensions", "chrome", "ccnavi-approval")
    VSC_VERSION_TS = os.path.join("src", "core", "version.ts")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.root = os.path.join(self.base, "repo")
        self.out = os.path.join(self.base, "out")
        self.build_repo(self.root)

    # ---- 見立てのソース

    def build_repo(self, root: str, real_ccnavi: bool = False) -> None:
        os.makedirs(root)
        subprocess.run([REAL_GIT, "init", "-q", root], check=True)
        os.makedirs(os.path.join(root, "scripts"))
        for name in ("package", "setup", "install", "doctor"):
            shutil.copy(script(f"ccnavi-{name}.sh"), os.path.join(root, "scripts"))
        write(os.path.join(root, "build.py"), "")
        write(os.path.join(root, ".gitignore"), "dist/\nbuild/\n")
        write(os.path.join(root, "src", "ccnavi", "entry", "version.py"), "COMPAT = 18\n")
        vsc = os.path.join(root, "extensions", "vscode", "ccnavi-board")
        write(os.path.join(vsc, "package.json"), '{"name":"ccnavi-board","version":"0.1.0"}\n')
        write(os.path.join(vsc, self.VSC_VERSION_TS), "export const EXTENSION_COMPAT = 18;\n")
        write(os.path.join(root, self.CHROME, "package.json"), '{"version":"0.4.0"}\n')
        if real_ccnavi:
            shutil.copytree(
                os.path.join(ROOT, ".ccnavi"),
                os.path.join(root, ".ccnavi"),
                symlinks=True,
                ignore=shutil.ignore_patterns("bin", "logs", "wip", "scratchpad"),
            )
            shutil.rmtree(os.path.join(root, ".ccnavi", "config", "project"), ignore_errors=True)
        else:
            write(os.path.join(root, ".ccnavi", "CCNAVI.md"), "md\n")
            write(os.path.join(root, ".ccnavi", "scripts", "ccnavi-x.sh"), "echo x\n")
            write(os.path.join(root, ".ccnavi", "docs", "a.md"), "doc\n")
        write(os.path.join(root, ".ccnavi", "config", "common", "rules.yml"), "version: 1\n")
        write(os.path.join(root, ".ccnavi", "config", "project", "phases.yml"), "mine\n")
        for junk in (
            ".ccnavi/scripts/ccnavi-index.jsonl",
            ".ccnavi/docs/.DS_Store",
            ".claude/skills/s/ccnavi-index.jsonl",
        ):
            write(os.path.join(root, junk))
        write(os.path.join(root, ".claude", "skills", "s", "SKILL.md"), "skill\n")
        for top in ("logs", "wip", "tests"):
            write(os.path.join(root, top, "keep.txt"))
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")
        self.build_dist(root)

    def build_dist(self, root: str, commit: str | None = None) -> None:
        head = commit or git(root, "rev-parse", "HEAD")
        dist = os.path.join(root, "dist")
        shutil.rmtree(dist, ignore_errors=True)
        write(os.path.join(dist, "ccnavi.target"), THIS_MACHINE + "\n")
        write_exec(os.path.join(dist, "ccnavi", "ccnavi"), FAKE_EXE.replace("COMMIT", head))
        write(os.path.join(dist, "ccnavi", "_internal", "lib.so.1"), "lib\n")
        os.symlink("lib.so.1", os.path.join(dist, "ccnavi", "_internal", "lib.so"))
        write(os.path.join(dist, "ccnavi-board-0.1.0.vsix"), "current")
        write(os.path.join(dist, "ccnavi-board-0.0.9.vsix"), "old")
        cdist = os.path.join(root, self.CHROME, "dist")
        write(os.path.join(cdist, "manifest.json"), '{"version":"0.4.0"}\n')
        write(os.path.join(cdist, "background.js"), 'x({compat:18,version:"0.4.0"});\n')

    def package(self, *args: str, root: str | None = None, env: dict | None = None):
        root = root or self.root
        return subprocess.run(
            ["sh", os.path.join(root, "scripts", "ccnavi-package.sh"), "--out", self.out, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    def zip_path(self) -> str:
        names = [n for n in os.listdir(self.out) if n.endswith(".zip")]
        self.assertEqual(len(names), 1, names)
        return os.path.join(self.out, names[0])

    def members(self) -> list[str]:
        with zipfile.ZipFile(self.zip_path()) as z:
            return [n.split("/", 1)[1] for n in z.namelist() if "/" in n and n.split("/", 1)[1]]

    # ---- 中身

    def test_contents_in_and_out(self):
        done = self.package()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = set(self.members())
        for want in (
            "dist/ccnavi/ccnavi",
            "dist/ccnavi.target",
            "dist/ccnavi-board-0.1.0.vsix",
            "extensions/chrome/ccnavi-approval/dist/manifest.json",
            ".ccnavi/CCNAVI.md",
            ".ccnavi/config/common/rules.yml",
            ".ccnavi/scripts/ccnavi-x.sh",
            ".ccnavi/docs/a.md",
            ".claude/skills/s/SKILL.md",
            "scripts/ccnavi-setup.sh",
            "scripts/ccnavi-install.sh",
            "scripts/ccnavi-doctor.sh",
            "VERSION",
            "INSTALL.md",
        ):
            self.assertIn(want, got)
        for unwanted in (
            "dist/ccnavi-board-0.0.9.vsix",
            ".ccnavi/config/project/phases.yml",
            ".ccnavi/scripts/ccnavi-index.jsonl",
            ".ccnavi/docs/.DS_Store",
            ".claude/skills/s/ccnavi-index.jsonl",
            "scripts/ccnavi-package.sh",
            "logs/keep.txt",
            "wip/keep.txt",
            "tests/keep.txt",
            "src/ccnavi/entry/version.py",
        ):
            self.assertNotIn(unwanted, got)
        self.assertEqual(count_next(done.stdout), 1)

    def test_version_and_install_md(self):
        self.package()
        with zipfile.ZipFile(self.zip_path()) as z:
            top = z.namelist()[0].split("/")[0]
            version = z.read(f"{top}/VERSION").decode()
            install_md = z.read(f"{top}/INSTALL.md").decode()
        head = git(self.root, "rev-parse", "HEAD")
        self.assertIn(f"commit: {head}", version)
        self.assertIn(f"target: {THIS_MACHINE}", version)
        self.assertIn("compat: 18", version)
        for word in ("shasum -a 256", "sha256sum", "certutil -hashfile", "すべて展開", "unzip"):
            self.assertIn(word, install_md)
        self.assertIn("--check", install_md)
        self.assertIn("~/.claude/skills/", install_md)
        self.assertTrue(top.startswith(f"ccnavi-{head[:7]}-{THIS_MACHINE}"))

    def test_mismatched_or_missing_extensions_are_left_out_with_a_note(self):
        os.remove(os.path.join(self.root, "dist", "ccnavi-board-0.1.0.vsix"))
        manifest = os.path.join(self.root, self.CHROME, "dist", "manifest.json")
        write(manifest, '{"version":"0.3.0"}\n')
        done = self.package()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.members()
        self.assertFalse([m for m in got if m.endswith(".vsix")])
        self.assertFalse([m for m in got if "ccnavi-approval" in m])
        self.assertIn("VS Code 拡張", done.stdout)
        self.assertIn("Chrome 拡張", done.stdout)

    def test_chrome_with_old_compat_is_left_out(self):
        bundle = os.path.join(self.root, self.CHROME, "dist", "background.js")
        write(bundle, "x({compat:17});\n")
        done = self.package()
        self.assertFalse([m for m in self.members() if "ccnavi-approval" in m])
        self.assertIn("互換の版", done.stdout)

    def test_vsix_with_other_compat_is_left_out(self):
        vsc = os.path.join(self.root, "extensions", "vscode", "ccnavi-board")
        write(os.path.join(vsc, self.VSC_VERSION_TS), "export const EXTENSION_COMPAT = 17;\n")
        done = self.package()
        self.assertFalse([m for m in self.members() if m.endswith(".vsix")])
        self.assertIn("互換の版", done.stdout)

    # ---- 毎回作り直す・照合

    def test_second_run_carries_nothing_from_the_first(self):
        self.package()
        stale = write(os.path.join(self.root, "build", "package", "stale-dir", "old.txt"))
        leftover = os.path.join(self.root, "build", "package", "ccnavi-old.zip")
        write(leftover)
        done = self.package()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(stale))
        self.assertFalse(os.path.exists(leftover))
        self.assertEqual(len([m for m in self.members() if "stale" in m or "old.txt" in m]), 0)

    def test_commit_mismatch_is_2(self):
        write(os.path.join(self.root, "newfile.txt"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "next")
        done = self.package()
        self.assertEqual(done.returncode, 2)
        self.assertIn("--build", done.stderr)
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_missing_target_or_executable_is_2(self):
        os.remove(os.path.join(self.root, "dist", "ccnavi", "ccnavi"))
        self.assertEqual(self.package().returncode, 2)
        self.build_dist(self.root)
        os.remove(os.path.join(self.root, "dist", "ccnavi.target"))
        self.assertEqual(self.package().returncode, 2)

    def test_dirty_tree_marks_the_name(self):
        write(os.path.join(self.root, ".ccnavi", "CCNAVI.md"), "changed\n")
        done = self.package()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.zip_path().endswith("-dirty.zip"))
        self.assertIn("-dirty", done.stdout)

    def test_sha256_is_written_and_matches(self):
        self.package()
        zipped = self.zip_path()
        self.assertTrue(os.path.exists(zipped + ".sha256"))
        done = subprocess.run(
            ["sha256sum", "-c", os.path.basename(zipped) + ".sha256"],
            cwd=self.out,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.package("--nope").returncode, 2)

    # ---- zip の作り方

    def isolated_path_without_zip(self) -> dict:
        bin_dir = os.path.join(self.base, "nozip")
        os.makedirs(bin_dir)
        for name in (
            *("sh", "git", "jq", "find", "cp", "rm", "mkdir", "sed", "tr", "cut", "grep", "cat"),
            *("sha256sum", "dirname", "basename", "uname", "date", "ls", "chmod", "head"),
        ):
            real = shutil.which(name)
            if real:
                os.symlink(real, os.path.join(bin_dir, name))
        write_exec(
            os.path.join(bin_dir, "uv"),
            '#!/bin/sh\nwhile [ "$1" != "-m" ]; do shift; done\nshift\n'
            f'exec "{sys.executable}" -m "$@"\n',
        )
        env = dict(os.environ)
        env["PATH"] = bin_dir
        return env

    def test_python_zipfile_is_refused_with_symlinks(self):
        done = self.package(env=self.isolated_path_without_zip())
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("シンボリックリンク", done.stderr)

    def test_python_zipfile_without_symlinks(self):
        os.remove(os.path.join(self.root, "dist", "ccnavi", "_internal", "lib.so"))
        done = self.package(env=self.isolated_path_without_zip())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("dist/ccnavi/ccnavi", self.members())

    # ---- zip → 展開 → 入れる → launcher

    def test_zip_unzip_install_and_launch(self):
        shutil.rmtree(self.root)
        self.build_repo(self.root, real_ccnavi=True)
        done = self.package()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        unpacked = os.path.join(self.base, "unpacked")
        os.makedirs(unpacked)
        subprocess.run(["unzip", "-q", self.zip_path(), "-d", unpacked], check=True)
        folder = os.path.join(unpacked, os.listdir(unpacked)[0])
        link = os.path.join(folder, "dist", "ccnavi", "_internal", "lib.so")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), "lib.so.1")
        ws = os.path.join(self.base, "ws")
        os.makedirs(ws)
        subprocess.run([REAL_GIT, "init", "-q", ws], check=True)
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        env["HOME"] = os.path.join(self.base, "home")
        os.makedirs(env["HOME"])
        installer = os.path.join(folder, "scripts", "ccnavi-install.sh")
        checked = subprocess.run(
            ["sh", installer, ws, "--from", folder, "--check"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        real = subprocess.run(
            ["sh", installer, ws, "--from", folder],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(real.returncode, 0, real.stdout + real.stderr)
        self.assertEqual(count_next(real.stdout), 1)
        launcher = os.path.join(ws, ".ccnavi", "scripts", "ccnavi-launcher.sh")
        version = subprocess.run(
            ["sh", launcher, "--version"], capture_output=True, text=True, check=False
        )
        head = git(self.root, "rev-parse", "HEAD")
        self.assertIn(f"commit: {head}", version.stdout)


if __name__ == "__main__":
    unittest.main()
