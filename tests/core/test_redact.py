"""記録から秘密の形を伏せる（redact）の、記録への配線の受入テスト。

伏せるのは記録の 1 行だけで、判定は伏せる前の文字列で下すこと。形ごとの伏せ方と、記録の欄を
切る前に伏せることは `go/internal/records`（redact・audit）の go test が見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests.inproc import run_ccnavi

# 形だけを持つ見本。実際のトークンではない。
LONG = "s3cr3t-value-that-is-long-enough"


class RecordTest(unittest.TestCase):
    """記録の 1 行に伏せた形が入り、判定は伏せる前の文字列で下すこと。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-redact-")
        # 記録の既定の置き場（`--log` は診断でだけ効く）。
        self.log = os.path.join(self.dir, "logs", "decisions.jsonl")

    def _lines(self) -> list[dict]:
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_judgement_sees_the_unredacted_command(self):
        # ルールが秘密の値そのものにヒットするなら、伏せた記録ではなく伏せる前の文字列でヒットする。
        rules = os.path.join(self.dir, ".ccnavi", "config", "common", "rules.yml")
        os.makedirs(os.path.dirname(rules))
        with open(rules, "w", encoding="utf-8") as f:
            f.write(
                "version: 1\n"
                "deny:\n"
                "  - id: no-this-token\n"
                "    match: Bash\n"
                f"    glob: '*{LONG}*'\n"
                "    message: 止める\n"
            )
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'Authorization: Bearer {LONG}' x"},
        }
        proc = run_ccnavi(
            ["--ticket-control", "disable"],
            input=json.dumps(payload),
            env={"CLAUDE_PROJECT_DIR": self.dir, "CCNAVI_MODE": "enable"},
            cwd=self.dir,
        )
        self.assertIn("no-this-token", proc.stdout + proc.stderr)
        (line,) = self._lines()
        self.assertEqual(line["decision"], "deny")
        self.assertEqual(line["rules"], [".ccnavi/config/common/rules.yml:no-this-token"])
        self.assertNotIn(LONG, line["subject"])


if __name__ == "__main__":
    unittest.main()
