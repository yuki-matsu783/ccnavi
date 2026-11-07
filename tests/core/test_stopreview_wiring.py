"""Stop の AI 判定の入口（`ccnavi stop-review material|record`）が、
読めない入力で止めないこと。

判定役が渡す結果が読めないとき・transcript から鍵を導けないときも、判定役の手を止めず
`{"ok": true}` や理由を返す。計画・材料・判定結果を通す筋は
tests/core/test_stopreview_scenarios.py が、計画の作り方と試行の状態遷移は
`go/internal/records/stopreview`・`go/internal/hook/events` の go test が見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests.inproc import run_ccnavi


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.transcript = os.path.join(self.root, "t.jsonl")
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "a1", "message": {"content": "x"}}))
            f.write("\n")

    def run_cli(self, verb, transcript, stdin=""):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(CCNAVI_LEARNING="disable", CCNAVI_STOP_REVIEW_AGENT="1")
        done = run_ccnavi(
            ["--root", self.root, "stop-review", verb, "s1", transcript],
            input=stdin,
            cwd=self.root,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_record_garbage_returns_ok(self):
        self.assertEqual(
            json.loads(self.run_cli("record", self.transcript, "not json")), {"ok": True}
        )

    def test_record_without_material_returns_ok(self):
        self.assertEqual(json.loads(self.run_cli("record", self.transcript, "{}")), {"ok": True})

    def test_no_key(self):
        self.assertIn("no_key", self.run_cli("material", os.path.join(self.root, "nonexistent")))


if __name__ == "__main__":
    unittest.main()
