"""`every > 1` のルールに `--lint` の広い allow 苦情を掛けないことの受入テスト。

`_broad`（ccnavi/lint.py）は、広い allow に `additionalContext` があると
「当たるたびに同じ文が積まれる」と warn する。`every`（渡す回の刻み。i0055）を
書いたルールは当たった中の 1 回しか渡らないので、この理屈は成り立たない。

実装（`_rule_problems` の条件に `rule.every <= 1` を足す）は i0060 フェーズ 2 の範囲。
ここに書くテストは、その実装が無い間は一部が落ちて正しい
（`test_every_over_1_suppresses_*` の 2 本）。残りは今の時点で既に通り、
「読めない `every` は毎回渡る側にする」という既定の挙動を実装後も落とさないための杭になる。

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
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                rules_path,
                "--log",
                "",
                "--state",
                "",
                "--approved",
                "",
                "--lint",
            ],
            cwd=ROOT,
            env=environment,
        )

    def warned(self, stdout: str) -> list[str]:
        return [
            line for line in stdout.splitlines() if "広い allow に additionalContext がある" in line
        ]

    def test_every_over_1_suppresses_the_broad_allow_warning(self):
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("everything", "Read", "", glob="*", additionalContext=NOTE, every=5)],
        )
        done = self.lint(path)
        self.assertEqual(done.returncode, 0, done.stdout)
        lines = self.warned(done.stdout)
        self.assertFalse(any("everything" in line for line in lines), done.stdout)

    def test_without_every_the_warning_still_fires(self):
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("everything", "Read", "", glob="*", additionalContext=NOTE)],
        )
        done = self.lint(path)
        lines = self.warned(done.stdout)
        self.assertTrue(any("everything" in line for line in lines), done.stdout)

    def test_every_1_still_warns(self):
        # 既定を明示しただけで、毎回渡ることに変わりはない。
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("everything", "Read", "", glob="*", additionalContext=NOTE, every=1)],
        )
        done = self.lint(path)
        lines = self.warned(done.stdout)
        self.assertTrue(any("everything" in line for line in lines), done.stdout)

    def test_unreadable_every_still_warns(self):
        # 読めない every は既定の 1 として扱うので、rule.every を見る実装なら自動的に warn が残る。
        # every_written（書いたかどうか）だけを見る実装に書き換えたときに、この杭が落ちる。
        for bad in (0, -1, "x"):
            with self.subTest(every=bad):
                path = self.rules(
                    deny=[rule("push", "Bash", glob="*git push*")],
                    allow=[
                        rule("everything", "Read", "", glob="*", additionalContext=NOTE, every=bad)
                    ],
                )
                done = self.lint(path)
                lines = self.warned(done.stdout)
                self.assertTrue(any("everything" in line for line in lines), done.stdout)

    def test_every_over_1_suppresses_with_context_file(self):
        write(os.path.join(self.root, "docs", "guide.md"), "本文")
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[
                rule(
                    "everything",
                    "Read",
                    "",
                    glob="*",
                    additionalContextFile="docs/guide.md",
                    every=5,
                )
            ],
        )
        done = self.lint(path)
        self.assertEqual(done.returncode, 0, done.stdout)
        lines = self.warned(done.stdout)
        self.assertFalse(any("everything" in line for line in lines), done.stdout)


if __name__ == "__main__":
    unittest.main()
