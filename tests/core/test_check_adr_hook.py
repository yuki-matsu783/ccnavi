"""`.claude/hooks/check-adr.sh` が、ADR の番号の重なりと置き場の間違いを止める。

ADR は `docs/30_adr/<主題>/NNNN-名前.md` に分けて置く。番号は主題をまたぐ通し番号なので、
別の主題が使っている番号を採ると重なる。書いた直後に exit 2 で返すことを確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

HOOK = os.path.join(ROOT, ".claude", "hooks", "check-adr.sh")


@unittest.skipIf(shutil.which("sh") is None, "sh が無い")
class CheckAdrHookTest(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tree, True)
        for rel in ("rules/0172-a.md", "ticket/0171-b.md"):
            self.put(rel)

    def put(self, rel: str) -> str:
        path = os.path.join(self.tree, "docs", "30_adr", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def run_hook(self, path: str) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_input": {"file_path": path}})
        return subprocess.run(["sh", HOOK], input=payload, capture_output=True, text=True)

    def test_free_number_passes(self):
        done = self.run_hook(self.put("rules/0173-c.md"))
        self.assertEqual(0, done.returncode, done.stderr)

    def test_number_used_in_another_topic_is_stopped(self):
        done = self.run_hook(self.put("ticket/0172-d.md"))
        self.assertEqual(2, done.returncode)
        self.assertIn("0172-a.md", done.stderr)
        self.assertIn("0173", done.stderr)

    def test_next_number_does_not_read_leading_zero_as_octal(self):
        self.put("base/0179-e.md")
        done = self.run_hook(self.put("rules/0172-f.md"))
        self.assertIn("0180", done.stderr)

    def test_sheet_at_the_top_is_stopped(self):
        top = os.path.join(self.tree, "docs", "30_adr", "0173-top.md")
        open(top, "w").close()
        done = self.run_hook(top)
        self.assertEqual(2, done.returncode)
        self.assertIn("主題のディレクトリ", done.stderr)

    def test_other_files_are_ignored(self):
        for path in ("/x/a.py", os.path.join(self.tree, "docs", "30_adr", "rules", "memo.md")):
            self.assertEqual(0, self.run_hook(path).returncode, path)


if __name__ == "__main__":
    unittest.main()
