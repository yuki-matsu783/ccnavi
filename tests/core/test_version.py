"""`ccnavi --version [--json]` の受入テスト。

`--version` が版・組み立ての元のコミット・互換の版・書式の版を言い、設定もワークスペースも
読まずに 0 で終わること。組み立ての元のコミットは、埋めていなければ `unknown` と言う。
版と互換の版が pyproject・sh・VS Code 拡張と同じであることは `go/internal/entry/version` の
go test が見る。sh の互換の版の `--lint` での確かめは tests/core/test_lint_compat.py が見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

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
        self.assertEqual(body["schema"], 1)
        self.assertRegex(body["version"], r"^[0-9]+\.[0-9]+\.[0-9]+$")
        # テストの実行ファイルはコミットを埋めずに組むので、組み立ての元のコミットは分からない
        self.assertEqual(body["commit"], "unknown")
        self.assertIs(body["built"], False)
        self.assertIsInstance(body["compat"], int)
        self.assertNotIn("flags", body)
        self.assertNotIn("features", body)
        self.assertEqual(
            set(body["formats"]), {"rules", "phases", "risks", "ticket"}, body["formats"]
        )

    def test_v11_the_text_form_is_one_item_per_line_for_the_sh(self):
        """V11 sh は `compat: <数>` の行を sed で読む。"""
        body = json.loads(ccnavi("--version", "--json").stdout)
        result = ccnavi("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(f"ccnavi {body['version']}\n"), result.stdout)
        self.assertRegex(result.stdout, rf"(?m)^compat: {body['compat']}$")
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


if __name__ == "__main__":
    unittest.main()
