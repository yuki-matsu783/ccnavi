"""`ccnavi --learnings` の受入テスト。

パターンの状態の決め方と wiki の読み方は `go/internal/records/wiki` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi
from tests.learningsample import impact, pattern


class LearningsCliTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-learnings-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        self.write("docs/wiki/patterns/p1.md", pattern(["a", "b", "c"], "rule"))
        self.write("docs/wiki/patterns/p2.md", pattern(["a"], "skill"))
        self.write("docs/wiki/patterns/broken.md", "x\n")
        self.write("docs/wiki/impact/w.md", impact("w", "承認待ち", {"p2": 1}, ""))

    def write(self, rel, text):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def run_cli(self, *args, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.update(env or {})
        return run_ccnavi(["--root", self.root, "--learnings", *args], cwd=ROOT, env=environment)

    def test_json(self):
        done = self.run_cli("--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(done.stdout)
        self.assertEqual(data["threshold"], 3)
        self.assertEqual(data["candidates"], 1)
        self.assertEqual(data["reached"], 1)
        rows = {r["name"]: r for r in data["patterns"]}
        self.assertEqual(rows["p1"]["status"], "候補")
        self.assertTrue(rows["p1"]["reached"])
        self.assertEqual(rows["p2"]["status"], "提案中")
        self.assertEqual([p["proposal"] for p in data["pending"]], ["w"])
        self.assertEqual([b["file"] for b in data["broken"]], ["docs/wiki/patterns/broken.md"])
        self.assertEqual(data["by_target"]["rule"]["candidates"], 1)

    def test_threshold_from_env(self):
        data = json.loads(self.run_cli("--json", env={"CCNAVI_LEARNING_SOURCES": "1"}).stdout)
        self.assertEqual(data["threshold"], 1)
        data = json.loads(self.run_cli("--json", env={"CCNAVI_LEARNING_SOURCES": "zero"}).stdout)
        self.assertEqual(data["threshold"], 3)

    def test_text_names_the_broken_file(self):
        done = self.run_cli()
        self.assertIn("broken.md", done.stdout)
        self.assertIn("承認待ちの提案", done.stdout)

    def test_not_with_other_routes(self):
        done = run_ccnavi(
            ["--root", self.root, "--learnings", "--lint"], cwd=ROOT, env=dict(os.environ)
        )
        self.assertNotEqual(done.returncode, 0)

    def test_missing_root_is_an_error(self):
        done = run_ccnavi(
            ["--root", os.path.join(self.root, "nope"), "--learnings"],
            cwd=ROOT,
            env=dict(os.environ),
        )
        self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
