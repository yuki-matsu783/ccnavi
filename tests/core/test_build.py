"""組み立ての出力を hook が起動する置き場へ写す（build.py の install）。

`build.py` は PyInstaller の出力 `dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へ写す
（設計 5 節の 4）。振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）が起動するのは
こちらなので、写し損ねると hook は古い実行ファイルを起動し続ける。onedir の `_internal/` は
前後の版で中身が変わるので、前の版にだけあったファイルが残ると混ざった版が動く。

PyInstaller は動かさない。偽の `dist/ccnavi/` を作って `install(dist_dir, root, target)` に渡す。
`dist_dir` は写す元のフォルダ（`dist/ccnavi/` そのもの）として渡す。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import build


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def tree(top):
    """`top` の下のファイルを、`/` でつないだ相対の綴りの集合で返す。"""
    found = set()
    for here, _dirs, files in os.walk(top):
        for name in files:
            rel = os.path.relpath(os.path.join(here, name), top)
            found.add(rel.replace(os.sep, "/"))
    return found


class InstallTest(unittest.TestCase):
    """B1"""

    TARGET = "linux-x86_64"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-build-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = os.path.join(self.dir, "repo")
        self.dist = os.path.join(self.root, "dist", "ccnavi")
        self.bin = os.path.join(self.root, ".ccnavi", "bin")
        self.live = os.path.join(self.bin, self.TARGET)
        os.makedirs(self.root)

    def install(self):
        """`build.install`。無ければこのテストで落とす。"""
        install = getattr(build, "install", None)
        if install is None:
            self.fail("build.install が無い")
        return install(self.dist, self.root, self.TARGET)

    def fake_build(self, version):
        """PyInstaller の onedir の出力のふり。"""
        write(os.path.join(self.dist, "ccnavi"), f"#!/bin/sh\necho {version}\n", mode=0o755)
        write(os.path.join(self.dist, "_internal", "base_library.zip"), f"base {version}\n")
        write(os.path.join(self.dist, "_internal", "python3.12", "lib.so"), f"lib {version}\n")

    def test_copies_the_build_into_the_directory_for_its_machine(self):
        self.fake_build("v1")
        self.install()

        self.assertEqual(tree(self.live), tree(self.dist))
        for rel in tree(self.dist):
            with self.subTest(rel=rel):
                self.assertEqual(
                    read(os.path.join(self.live, rel)), read(os.path.join(self.dist, rel))
                )

    def test_leaves_the_build_output_in_place(self):
        """写すのであって移すのではない。`dist/` は代わりに通る sh の既定の探し先でもある。"""
        self.fake_build("v1")
        self.install()
        self.assertEqual(
            tree(self.dist), {"ccnavi", "_internal/base_library.zip", "_internal/python3.12/lib.so"}
        )

    @unittest.skipIf(os.name == "nt", "実行ビットは POSIX でだけ意味がある")
    def test_keeps_the_executable_bit(self):
        """落ちると、sh の `exec` が 126 で終わり hook が起動しない。"""
        self.fake_build("v1")
        self.install()
        self.assertTrue(os.access(os.path.join(self.live, "ccnavi"), os.X_OK))

    def test_does_not_keep_files_only_the_previous_build_had(self):
        write(os.path.join(self.live, "ccnavi"), "old\n", mode=0o755)
        write(os.path.join(self.live, "_internal", "base_library.zip"), "base old\n")
        write(os.path.join(self.live, "_internal", "gone.so"), "only in the old build\n")
        write(os.path.join(self.live, "stale", "left.txt"), "only in the old build\n")

        self.fake_build("v2")
        self.install()

        self.assertEqual(tree(self.live), tree(self.dist))
        self.assertIn("v2", read(os.path.join(self.live, "ccnavi")))

    def test_leaves_builds_for_other_machines_and_no_leftovers(self):
        """他の機械の組み立ては写す先ではない。入れ替えの途中の置き場も残さない。"""
        other = os.path.join(self.bin, "darwin-arm64", "ccnavi")
        write(other, "arm\n", mode=0o755)
        write(os.path.join(self.live, "ccnavi"), "old\n", mode=0o755)

        self.fake_build("v2")
        self.install()

        self.assertEqual(read(other), "arm\n")
        self.assertEqual(sorted(os.listdir(self.bin)), ["darwin-arm64", self.TARGET])


if __name__ == "__main__":
    unittest.main()
