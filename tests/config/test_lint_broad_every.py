"""`every > 1` のルールに `--lint` の広い allow 苦情を掛けないことの受入テスト。

`broad`（go/internal/policy/rulelint）は、広い allow に `additionalContext` があると
「ヒットするたびに同じ文が積まれる」と warn する。`every`（渡す回の刻み。i0055）を
書いたルールはヒットした中の 1 回しか渡らないので、この理屈は成り立たない。

読めない `every` は毎回渡る側にするので、苦情は残る。

道具は外から呼ぶ（`tests/inproc.py` の `run_ccnavi`）。書き方は
`tests/core/test_additional_context.py` の `test_lint_warns_on_broad_allow_only` に揃えた。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

NOTE = "テストの文面。"


def rule(name: str, match: str, message: str = "文面", **extra) -> dict:
    return {"id": name, "match": match, "message": message, **extra}


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class LintBroadEveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.addCleanup(self.dir.cleanup)

    def rules(self, **sections) -> str:
        body = {"version": 1, **sections}
        return write(os.path.join(self.root, "rules.yml"), json.dumps(body))

    def lint(self, rules_path: str) -> subprocess.CompletedProcess:
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                rules_path,
                "--lint",
            ],
            cwd=ROOT,
            env=environment,
        )

    def warned(self, stdout: str) -> list[str]:
        return [
            line for line in stdout.splitlines() if "広い allow に additionalContext がある" in line
        ]

    def test_only_every_over_1_suppresses_the_broad_allow_warning(self):
        """every が 2 以上のルールだけ苦情が消える。1・読めない値・every 無しは毎回渡るので残る。"""
        write(os.path.join(self.root, "docs", "guide.md"), "本文")

        def broad(name, **extra):
            return rule(name, "Read", "", glob="*", **extra)

        allow = [
            broad("suppressed", additionalContext=NOTE, every=5),
            broad("suppressed-file", additionalContextFile="docs/guide.md", every=5),
            broad("no-every", additionalContext=NOTE),
            broad("every-1", additionalContext=NOTE, every=1),
            broad("every-0", additionalContext=NOTE, every=0),
            broad("every-minus", additionalContext=NOTE, every=-1),
            broad("every-word", additionalContext=NOTE, every="x"),
        ]
        path = self.rules(deny=[rule("push", "Bash", glob="*git push*")], allow=allow)
        done = self.lint(path)
        lines = self.warned(done.stdout)
        for r in allow:
            with self.subTest(r["id"]):
                warned = any(f": {r['id']}: " in line for line in lines)
                self.assertEqual(warned, not r["id"].startswith("suppressed"), done.stdout)


if __name__ == "__main__":
    unittest.main()
