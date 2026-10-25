"""配布物（.ccnavi/ の設定・文書・スクリプト）を置いたプロジェクトの `--lint` が問題 0 件。

ccnavi が配るルールと文書が自身の検査に引っかかると、配布直後のプロジェクトで
エージェントが `--lint` を打つたびに警告を見ることになる。たとえば `additionalContextFile` の文書が
上限（ctxfile.MAX_CHARS）を超えると、先頭だけが渡って残りは渡らない。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

import yaml

from ccnavi.policy import ctxfile
from tests import ROOT
from tests.inproc import run_ccnavi

DIST = os.path.join(ROOT, ".ccnavi")


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
        problems = json.loads(r.stdout)["problems"]
        self.assertEqual([], problems, r.stdout)

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
                            self.assertFalse(ctxfile.over_limit(full))
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
