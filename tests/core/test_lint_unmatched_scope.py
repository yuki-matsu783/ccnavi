"""`--lint` の「scope がどのファイルにも当たらない」warn（`check.unmatched_scopes`）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import settings
from ccnavi.policy.rules import SEVERITY_WARN
from ccnavi.tickets.config import check

PHASES = """version: 1
phases:
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope: ["src/*", "tests/*"]
  research:
    kind: work
    title: 調査
    review: false
    scope: ["wip/research/*"]
"""


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class UnmatchedScopes(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        write(os.path.join(self.root, ".ccnavi/config/common/phases.yml"), PHASES)

    def problems(self):
        conf, _ = settings.load(self.root)
        return check.unmatched_scopes(conf, self.root)

    def test_textkit_layout_warns_only_for_src(self):
        write(os.path.join(self.root, "textkit/a.py"))
        write(os.path.join(self.root, "tests/test_a.py"))
        found = self.problems()
        self.assertEqual([p.severity for p in found], [SEVERITY_WARN])
        self.assertIn("`src/*`", found[0].detail)
        self.assertIn("implement", found[0].rule)

    def test_matching_layout_is_silent(self):
        write(os.path.join(self.root, "src/a.py"))
        write(os.path.join(self.root, "tests/test_a.py"))
        self.assertEqual(self.problems(), [])


if __name__ == "__main__":
    unittest.main()
