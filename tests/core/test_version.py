"""`ccnavi --version [--json]` と、互換の版を宣言する側（組み立て・sh）の検査。

見るのは 4 つ。

1. `--version` が版・組み立ての元のコミット・受け付けるフラグ・互換の版・書式の版を言い、
   設定もワークスペースも読まずに 0 で終わること
2. フラグは引数の定義から引くので、定義に足したフラグがそのまま並ぶこと
3. 組み立ての元のコミットは、埋めていなければ `unknown` と言うこと（埋め方は test_build.py）

4. 互換の版が、実行ファイル・sh・VS Code 拡張で同じ値であること

実行時に版の食い違いを断る処理は無い（sh と実行ファイルは同時に配る前提）。
sh の互換の版の `--lint` での確かめは tests/core/test_lint_compat.py が見る。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

from ccnavi.entry import version
from tests import ROOT
from tests.inproc import run_ccnavi


def ccnavi(*args: str, root: str | None = None) -> subprocess.CompletedProcess:
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    head = ["--root", root] if root else []
    return run_ccnavi([*head, *args], input="", cwd=ROOT, env=environment)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class VersionFlagTest(unittest.TestCase):
    def test_v10_json_says_the_version_the_commit_and_the_compat(self):
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
        self.assertNotIn("flags", body)
        self.assertNotIn("features", body)
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

    def test_v12_it_reads_no_settings_and_no_workspace(self):
        """V12 読めない設定のワークスペースでも、在らないルートでも答える。"""
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, ".ccnavi", "config", "common", "rules.yml"), "version: [\n")
            broken = ccnavi("--version", root=root)
        self.assertEqual(broken.returncode, 0, broken.stderr)
        self.assertEqual(broken.stderr, "")
        missing = ccnavi("--version", "--json", root=os.path.join(ROOT, "no-such-dir"))
        self.assertEqual(missing.returncode, 0, missing.stderr)

    def test_v14_the_version_agrees_with_pyproject(self):
        """V14"""
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            declared = tomllib.load(f)["project"]["version"]
        self.assertEqual(version.VERSION, declared)


class CompatDeclaredTest(unittest.TestCase):
    def test_v6_the_exe_the_sh_and_the_extension_declare_the_same_compat(self):
        """V6"""
        sh = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
        ts = os.path.join(ROOT, "extensions", "vscode", "ccnavi-board", "src", "core", "version.ts")
        found = []
        for path, pattern in (
            (sh, r"^CCNAVI_COMPAT=([0-9]+)$"),
            (ts, r"^export const EXTENSION_COMPAT = ([0-9]+);$"),
        ):
            with open(path, encoding="utf-8") as f:
                match = re.search(pattern, f.read(), re.MULTILINE)
            self.assertIsNotNone(match, path)
            found.append(int(match.group(1)))
        self.assertEqual(found, [version.COMPAT, version.COMPAT])


class BuildInfoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-stamp-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(sys.modules.pop, version.BUILDINFO_MODULE, None)

    def test_v16_without_the_stamp_the_commit_is_unknown(self):
        """V16"""
        sys.modules.pop(version.BUILDINFO_MODULE, None)
        self.assertEqual(version.commit(), version.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
