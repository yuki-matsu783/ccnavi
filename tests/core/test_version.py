"""`ccnavi --version [--json]` と、互換の版を名乗る側（組み立て・sh）の検査。

見るのは 3 つ。

1. `--version` が版・組み立ての元のコミット・受け付けるフラグ・互換の版・書式の版を言い、
   設定もワークスペースも読まずに 0 で終わること
2. フラグは引数の定義から引くので、定義に足したフラグがそのまま並ぶこと
3. 組み立ての元のコミットは build.py が埋めた部品から読み、無ければ `unknown` と言うこと

sh の互換の版との食い違いは tests/core/test_lint_compat.py（`--lint`）と
tests/sh/test_compat_skew.py（sh）が見る。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

import build
from ccnavi import version
from tests import GIT_ENV, ROOT
from tests.inproc import run_ccnavi


def ccnavi(*args: str, root: str | None = None) -> subprocess.CompletedProcess:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    head = ["--root", root] if root else []
    return run_ccnavi([*head, *args], input="", cwd=ROOT, env=environment)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class VersionFlagTest(unittest.TestCase):
    def test_v10_json_says_the_version_the_commit_the_flags_and_the_compat(self):
        """V10"""
        result = ccnavi("--version", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["schema"], version.SCHEMA)
        self.assertEqual(body["version"], version.VERSION)
        # テストはソースで動くので、組み立ての元のコミットは分からない
        self.assertEqual(body["commit"], version.UNKNOWN)
        self.assertIs(body["built"], False)
        self.assertEqual(body["compat"], version.COMPAT)
        self.assertEqual(body["flags"], sorted(body["flags"]))
        for flag in ("--version", "--json", "--lint", "--flow", "--explain", "--root"):
            self.assertIn(flag, body["flags"])
        self.assertEqual(
            set(body["formats"]), {"rules", "phases", "risks", "ticket"}, body["formats"]
        )

    def test_v11_the_text_form_is_one_item_per_line_for_the_sh(self):
        """V11 sh は `compat: <数>` の行を sed で読む。"""
        result = ccnavi("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(f"ccnavi {version.VERSION}\n"), result.stdout)
        self.assertRegex(result.stdout, rf"(?m)^compat: {version.COMPAT}$")
        self.assertRegex(result.stdout, r"(?m)^commit: unknown$")
        self.assertRegex(result.stdout, r"(?m)^flags: .*--version")

    def test_v12_it_reads_no_settings_and_no_workspace(self):
        """V12 読めない設定のワークスペースでも、在らないルートでも答える。"""
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, ".ccnavi", "common", "rules.yml"), "version: [\n")
            broken = ccnavi("--version", root=root)
        self.assertEqual(broken.returncode, 0, broken.stderr)
        self.assertEqual(broken.stderr, "")
        missing = ccnavi("--version", "--json", root=os.path.join(ROOT, "no-such-dir"))
        self.assertEqual(missing.returncode, 0, missing.stderr)

    def test_v13_flags_come_from_the_argument_definition(self):
        """V13 定義に足したフラグは、何も書き足さずに並ぶ。短い表記と位置引数は並べない。"""
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--zeta", action="store_true")
        parser.add_argument("-a", "--alpha", default="")
        parser.add_argument("words", nargs="*")
        self.assertEqual(version.flags(parser), ["--alpha", "--zeta"])
        out = io.StringIO()
        self.assertEqual(version.report(out, parser, as_json=True), 0)
        self.assertEqual(json.loads(out.getvalue())["flags"], ["--alpha", "--zeta"])

    def test_v14_the_version_agrees_with_pyproject(self):
        """V14"""
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            declared = tomllib.load(f)["project"]["version"]
        self.assertEqual(version.VERSION, declared)


class BuildInfoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-stamp-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(sys.modules.pop, version.BUILDINFO_MODULE, None)

    def test_v15_the_commit_written_at_build_is_read_back(self):
        """V15 build.py が書いた部品を、組み立てた実行ファイルと同じく import で読む。"""
        build.write_buildinfo(self.dir, "0123abc-dirty")
        sys.path.insert(0, self.dir)
        self.addCleanup(sys.path.remove, self.dir)
        sys.modules.pop(version.BUILDINFO_MODULE, None)
        self.assertEqual(version.commit(), "0123abc-dirty")

    def test_v16_without_the_stamp_the_commit_is_unknown(self):
        """V16"""
        sys.modules.pop(version.BUILDINFO_MODULE, None)
        self.assertEqual(version.commit(), version.UNKNOWN)

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        subprocess.run(["git", *args], cwd=self.dir, env=env, check=True, capture_output=True)

    @unittest.skipIf(shutil.which("git") is None, "git が無い")
    def test_v17_the_source_commit_is_head_and_says_dirty(self):
        """V17 HEAD を言い、未コミットの変更があれば `-dirty` を付ける。

        リポジトリでなければ unknown。
        """
        self.assertEqual(build.source_commit(self.dir), version.UNKNOWN)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        write(os.path.join(self.dir, "a.txt"), "a\n")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "a")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.dir, capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(build.source_commit(self.dir), head)
        write(os.path.join(self.dir, "a.txt"), "b\n")
        self.assertEqual(build.source_commit(self.dir), head + "-dirty")


if __name__ == "__main__":
    unittest.main()
