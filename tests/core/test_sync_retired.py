"""`ccnavi sync retired` の答えの形と、置き場のフラグが効かないこと。

片付けの記録の置き場は固定で、env でも診断のフラグでも動かない
（設計 wip/design/i0064-fixed-places.md）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class PlaceFlagsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-sync-retired-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))

    def test_place_flags_are_ignored_outside_diagnosis(self):
        # sh は受け取った引数を `"$@"` で渡すので、
        # サブコマンドに置き場のフラグを足されても効かない（置き場は固定）。
        # 無視したことは標準エラーに出す。
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        elsewhere = os.path.join(self.ws, "elsewhere")
        write(
            os.path.join(elsewhere, "retire", "i0001.json"),
            '{"parent": "i0001", "project": "", "branch": "i0001", "mr": 42, "children": []}\n',
        )
        result = run_ccnavi(
            [
                "--root",
                self.ws,
                "--approved",
                "x/approved",
                "--tickets",
                "x/proposals",
                "--state",
                elsewhere,
                "--log",
                "",
                "sync",
                "retired",
            ],
            env=environment,
        )
        self.assertEqual((0, "retired 1\n"), (result.returncode, result.stdout), result.stderr)
        for flag in ("--approved", "--tickets", "--state", "--log"):
            self.assertIn(f"ccnavi: {flag} は診断", result.stderr)


class SyncRetiredCommandTest(unittest.TestCase):
    """`ccnavi sync retired` は答えの形だけをここで見る（中身は tests/ticket/test_retire.py）。"""

    def test_the_head_comes_first_without_any_note(self):
        ws = tempfile.mkdtemp(prefix="ccnavi-sync-retired-")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        os.makedirs(os.path.join(ws, ".claude"))
        result = run_ccnavi(["--root", ws, "sync", "retired"])
        self.assertEqual((0, "retired 1\n"), (result.returncode, result.stdout))


if __name__ == "__main__":
    unittest.main()
