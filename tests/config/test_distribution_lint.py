"""配布物（.ccnavi/ の設定・文書・スクリプト）を置いたプロジェクトの `--lint` は問題 0 件になる。

ccnavi が配るルールと文書が自身の検査に引っかかると、配布直後のプロジェクトで
エージェントが `--lint` を打つたびに警告を見ることになる。たとえば `additionalContextFile` の文書が
上限（MAX_CHARS 文字）を超えると、先頭までしか渡らず、残りは渡らない。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

import yaml

from tests import ROOT
from tests.inproc import run_ccnavi

DIST = os.path.join(ROOT, ".ccnavi")
# 1 回の応答に載せる文書の上限（文字数）。超えた分は渡らない。
MAX_CHARS = 4000


class DistributionLintTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        dest = os.path.join(self.root, ".ccnavi")
        os.makedirs(os.path.join(dest, "config"))
        shutil.copytree(
            os.path.join(DIST, "config", "common"), os.path.join(dest, "config", "common")
        )
        for part in ("docs", "scripts"):
            shutil.copytree(os.path.join(DIST, part), os.path.join(dest, part))
        shutil.copy(os.path.join(DIST, "CCNAVI.md"), dest)

    def test_distributed_lint_has_no_problem(self):
        r = run_ccnavi(["--root", self.root, "--lint", "--json", "--mode", "enable"], cwd=self.root)
        self.assertEqual([], json.loads(r.stdout)["problems"], r.stdout)

    def test_every_context_file_of_the_common_rules_fits_the_limit(self):
        with open(os.path.join(DIST, "config", "common", "rules.yml"), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        checked = 0
        for rules in data.values():
            for rule in rules if isinstance(rules, list) else []:
                for key in ("additionalContextFile", "additionalContextOnceFile"):
                    rel = rule.get(key) if isinstance(rule, dict) else None
                    if rel:
                        checked += 1
                        with self.subTest(rule=rule.get("id"), file=rel):
                            full = os.path.join(ROOT, rel.replace("/", os.sep))
                            self.assertTrue(os.path.isfile(full))
                            with open(full, encoding="utf-8", errors="replace") as f:
                                self.assertLessEqual(len(f.read()), MAX_CHARS)
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
