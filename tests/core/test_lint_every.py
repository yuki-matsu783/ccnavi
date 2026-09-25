"""`--lint` が `every`（渡す回の刻み）について言うこと。

見るのは 2 つ。

1. `every` の値が 1 以上の整数か。`0` と `-1` と `"x"` は error。刻みとして読めない値を
   黙って無視すると、書いた人は刻んだつもりのまま毎回渡ることになる
2. 渡すものを 1 つも持たない `every` は warning。刻んでも渡す文が無ければ何も起きない。
   `additionalContextOnce` だけ、`additionalContextFile` だけの `every` は渡すものが
   あるので咎めない

`every: 1` は `every` 無しと同じ意味になるだけで誤りではないので、何も言わない。

実装はまだ無い。このテストは実装フェーズ（i0055 のフェーズ 2）で通るようになる。
渡す回の刻みそのものは tests/config/test_rule_every.py。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

NOTE = "ルールと突き合わせること。"


def rule(name: str, **extra) -> dict:
    """狭い allow。広い allow の warn（既存の検査）に引っかからない形で書く。"""
    return {"id": name, "match": "Write", "glob": "*/src/*", **extra}


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class LintEveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.addCleanup(self.dir.cleanup)

    def rules(self, *allow: dict) -> str:
        body = {
            "version": 1,
            "deny": [
                {"id": "push", "match": "Bash", "glob": "*git push*", "message": "push は人が行う"}
            ],
            "allow": list(allow),
        }
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

    def said(self, stdout: str, name: str) -> list[str]:
        """このルールについて lint が言ったこと。何も言われていなければ空。"""
        return [line for line in stdout.splitlines() if f": {name}: " in line]

    def test_every_must_be_a_positive_integer(self):
        """刻みとして読めない値は error。正しい値には何も言わない。"""
        path = self.rules(
            rule("zero", additionalContext=NOTE, every=0),
            rule("minus", additionalContext=NOTE, every=-1),
            rule("word", additionalContext=NOTE, every="x"),
            rule("just-one", additionalContext=NOTE, every=1),
            rule("five", additionalContext=NOTE, every=5),
        )
        done = self.lint(path)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        for name in ("zero", "minus", "word"):
            said = self.said(done.stdout, name)
            self.assertTrue(
                any(s.startswith("error:") and "every" in s for s in said),
                f"{name} に every の error が無い:\n{done.stdout}",
            )
        # every: 1 は every 無しと同じ意味になるだけで、誤りではない。
        self.assertEqual(self.said(done.stdout, "just-one"), [], done.stdout)
        self.assertEqual(self.said(done.stdout, "five"), [], done.stdout)

    def test_every_without_anything_to_deliver_is_a_warning(self):
        """渡すものを 1 つも持たない `every` は warning。Once だけ・本文だけは咎めない。"""
        write(os.path.join(self.root, "docs", "guide.md"), "突き合わせの観点")
        path = self.rules(
            rule("silent", every=5),
            rule("only-once", additionalContextOnce=NOTE, every=5),
            rule("only-file", additionalContextFile="docs/guide.md", every=5),
        )
        done = self.lint(path)
        self.assertEqual(done.returncode, 0, done.stdout)
        said = self.said(done.stdout, "silent")
        self.assertTrue(
            any(s.startswith("warn:") and "every" in s for s in said),
            f"文を持たない every の warn が無い:\n{done.stdout}",
        )
        # 「渡す回の最初の 1 回」として効くので、Once だけの every は咎めない。
        self.assertEqual(self.said(done.stdout, "only-once"), [], done.stdout)
        # 文が無くても本文は渡る。
        self.assertEqual(self.said(done.stdout, "only-file"), [], done.stdout)


if __name__ == "__main__":
    unittest.main()
