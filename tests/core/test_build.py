"""組み立ての sh（scripts/ccnavi-build.sh）。

sh は Go で `dist/ccnavi/` に組み、それを hook が起動する置き場 `.ccnavi/bin/<os>-<arch>/` へ
コピーする。振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）が起動するのはこちらなので、
コピーし損ねると hook は前の実行ファイルを起動し続ける。

Go は動かさない。PATH の先頭に偽の `go` を置き、`go build` に渡った環境と引数を、組んだふりの
実行ファイルの中身として書かせる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import GIT_ENV, ROOT

BUILD_SH = os.path.join(ROOT, "scripts", "ccnavi-build.sh")
SHELL = shutil.which("sh")
# `go env GOHOSTOS` / `GOHOSTARCH` は linux/amd64 と答え、`go build -o <出力>` は出力に
# GOOS・GOARCH・CGO_ENABLED と引数を書く。FAIL_BUILD があれば 3 で失敗する。
FAKE_GO = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    "env)\n"
    '\tcase "$2" in GOHOSTOS) echo linux ;; GOHOSTARCH) echo amd64 ;; esac\n'
    "\texit 0\n"
    "\t;;\n"
    "build)\n"
    '\t[ -z "${FAIL_BUILD:-}" ] || exit 3\n'
    '\tall="$*"\n'
    '\twhile [ "$#" -gt 0 ]; do\n'
    '\t\tif [ "$1" = -o ]; then out=$2; fi\n'
    "\t\tshift\n"
    "\tdone\n"
    "\tprintf 'GOOS=%s GOARCH=%s CGO_ENABLED=%s\\n%s\\n' "
    '"$GOOS" "$GOARCH" "$CGO_ENABLED" "$all" >"$out"\n'
    '\tchmod +x "$out"\n'
    "\texit 0\n"
    "\t;;\n"
    "esac\n"
    "exit 1\n"
)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def git(root, *args):
    return subprocess.run(
        ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV},
    ).stdout.strip()


@unittest.skipUnless(SHELL and shutil.which("git"), "sh と git が要る")
class BuildTest(unittest.TestCase):
    """B1"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-build-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = os.path.join(self.dir, "repo")
        write(os.path.join(self.root, "scripts", "ccnavi-build.sh"), read(BUILD_SH))
        write(os.path.join(self.root, "go", "cmd", "ccnavi", "main.go"), "package main\n")
        write(os.path.join(self.root, ".gitignore"), "/dist/\n/build/\n/.ccnavi/bin/\n")
        self.tools = os.path.join(self.dir, "tools")
        write(os.path.join(self.tools, "go"), FAKE_GO, mode=0o755)
        self.bin = os.path.join(self.root, ".ccnavi", "bin")
        self.dist = os.path.join(self.root, "dist")

    def build(self, *args, **env):
        merged = {**os.environ, **GIT_ENV, **env}
        merged["PATH"] = os.pathsep.join([self.tools, os.environ.get("PATH", "")])
        return subprocess.run(
            [SHELL, os.path.join(self.root, "scripts", "ccnavi-build.sh"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=merged,
            check=False,
        )

    def test_builds_for_this_machine_and_copies_into_its_place(self):
        done = self.build()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        built = read(os.path.join(self.dist, "ccnavi", "ccnavi"))
        self.assertIn("GOOS=linux GOARCH=amd64 CGO_ENABLED=0", built)
        self.assertEqual(read(os.path.join(self.dist, "ccnavi.target")), "linux-x86_64\n")
        live = os.path.join(self.bin, "linux-x86_64", "ccnavi")
        self.assertEqual(read(live), built)
        self.assertTrue(os.access(live, os.X_OK))
        # コピーであって移すのではない。dist/ は導入スクリプトの配布元。
        self.assertTrue(os.path.isfile(os.path.join(self.dist, "ccnavi", "ccnavi")))

    def test_stamps_the_source_commit(self):
        """git が無い・リポジトリでないときは unknown、未コミットの変更があれば -dirty。"""
        done = self.build()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        stamp = "-X ccnavi/internal/entry/version.commit="
        self.assertIn(stamp + "unknown", read(os.path.join(self.dist, "ccnavi", "ccnavi")))

        subprocess.run(["git", "init", "-q", self.root], check=True, env={**os.environ, **GIT_ENV})
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        head = git(self.root, "rev-parse", "HEAD")
        self.build()
        built = read(os.path.join(self.dist, "ccnavi", "ccnavi"))
        self.assertIn(f"{stamp}{head} ", built)

        write(os.path.join(self.root, "go", "cmd", "ccnavi", "main.go"), "package main // x\n")
        self.build()
        self.assertIn(f"{stamp}{head}-dirty ", read(os.path.join(self.dist, "ccnavi", "ccnavi")))

    def test_builds_for_another_machine(self):
        done = self.build("--target", "windows-x86_64")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        built = read(os.path.join(self.dist, "ccnavi", "ccnavi.exe"))
        self.assertIn("GOOS=windows GOARCH=amd64", built)
        self.assertEqual(read(os.path.join(self.dist, "ccnavi.target")), "windows-x86_64\n")
        self.assertTrue(os.path.isfile(os.path.join(self.bin, "windows-x86_64", "ccnavi.exe")))

    def test_does_not_keep_what_only_the_previous_build_had(self):
        """dist/ccnavi/ も置き場も入れ替える。別の機械向けの組み立ての名前を残さない。"""
        self.build("--target", "darwin-arm64")
        write(os.path.join(self.bin, "linux-x86_64", "前の版の残り"), "old\n")
        write(os.path.join(self.bin, "darwin-x86_64", "ccnavi"), "other\n", mode=0o755)

        done = self.build()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(os.listdir(os.path.join(self.dist, "ccnavi")), ["ccnavi"])
        self.assertEqual(os.listdir(os.path.join(self.bin, "linux-x86_64")), ["ccnavi"])
        # 他の機械向けの置き場には触らない。入れ替えの途中の置き場も残さない。
        self.assertEqual(read(os.path.join(self.bin, "darwin-x86_64", "ccnavi")), "other\n")
        self.assertEqual(
            sorted(os.listdir(self.bin)), ["darwin-arm64", "darwin-x86_64", "linux-x86_64"]
        )
        self.assertEqual(sorted(os.listdir(self.dist)), ["ccnavi", "ccnavi.target"])

    def test_a_failed_copy_says_dist_is_new_and_the_place_is_as_before(self):
        write(self.bin, "置き場を作れなくする\n")
        done = self.build()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("dist/ は新しい。.ccnavi/bin/linux-x86_64/ は前のまま", done.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.dist, "ccnavi", "ccnavi")))
        self.assertEqual(read(self.bin), "置き場を作れなくする\n")

    def test_a_failed_swap_of_dist_stops_before_the_target_and_the_place(self):
        """dist/ccnavi.target は dist/ccnavi/ の版の目印なので、入れ替えが通るまで書き換えない。"""
        write(self.dist, "置き換えられなくする\n")
        done = self.build()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("dist/ccnavi/ へ置き換えられなかった", done.stderr)
        self.assertEqual(read(self.dist), "置き換えられなくする\n")
        self.assertFalse(os.path.exists(self.bin))

    def test_a_failed_go_build_changes_nothing(self):
        self.build()
        before = read(os.path.join(self.dist, "ccnavi", "ccnavi"))
        done = self.build("--target", "windows-x86_64", FAIL_BUILD="1")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual(read(os.path.join(self.dist, "ccnavi", "ccnavi")), before)
        self.assertEqual(read(os.path.join(self.dist, "ccnavi.target")), "linux-x86_64\n")

    def test_refuses_a_target_it_cannot_read(self):
        for target in ("haiku-x86_64", "linux-riscv64", "linux"):
            with self.subTest(target=target):
                done = self.build("--target", target)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertFalse(os.path.exists(self.dist))


if __name__ == "__main__":
    unittest.main()
