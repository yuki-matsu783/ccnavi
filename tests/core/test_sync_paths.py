"""`ccnavi sync paths` の約束。

`ccnavi-sync.sh` は jq を使わず JSON も読まない。置き場のパスを、
実行ファイルが 1 行 1 項目（`<キー> <値>`）で返す。
統合先の名前は返さない（sh が各リポジトリの `origin/HEAD` で決める）。
置き場は固定で、env では動かない（設計 wip/design/i0064-fixed-places.md）。
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


class SyncPathsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-sync-paths-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))

    def paths(self, env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        result = run_ccnavi(["--root", self.ws, "sync", "paths"], env=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        return dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())

    def test_defaults(self):
        self.assertEqual(
            {
                "approved": ".ccnavi/approved",
                "proposals": "wip/proposals",
                "home": ".ccnavi",
            },
            self.paths(),
        )

    def test_works_with_ticket_control_disabled(self):
        found = self.paths({"CCNAVI_TICKET_CONTROL": "disable"})
        self.assertEqual(".ccnavi/approved", found["approved"])

    def test_place_flags_are_ignored_outside_diagnosis(self):
        # sh は受け取った引数を `"$@"` で渡すので、
        # サブコマンドに置き場のフラグを足されても効かない。
        # 無視したことは標準エラーに出す。
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        elsewhere = os.path.join(self.ws, "elsewhere")
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
                "paths",
            ],
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        found = dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())
        self.assertEqual(".ccnavi/approved", found["approved"])
        self.assertEqual("wip/proposals", found["proposals"])
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
