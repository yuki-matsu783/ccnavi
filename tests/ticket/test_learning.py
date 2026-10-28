"""学びの蓄積の記録（learning）の単体テスト。雛形・追記・検証・判定記録の要約・記録の読み出しを見る。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from ccnavi.tickets import learning


class LearningTest(unittest.TestCase):
    def test_scaffold_and_bump(self) -> None:
        text = learning.scaffold_text("a-1", "p")
        entry = learning.entry_text("本文1", "", "", "2026-01-01T00:00:00+0900")
        text = learning.append_entry(text, entry)
        text = learning.append_entry(text, entry)
        self.assertIn("learnings: 2", text)
        self.assertEqual(text.count("- 種類: 未定"), 2)
        self.assertEqual(text.count("- 信号: 訂正"), 2)

    def test_validate(self) -> None:
        self.assertNotEqual(learning.validate("", "", ""), "")
        self.assertNotEqual(learning.validate("本文", "x", ""), "")
        self.assertNotEqual(learning.validate("本文", "", "x"), "")
        self.assertEqual(learning.validate("本文", "skill", "訂正"), "")

    def test_clean_body_limit(self) -> None:
        cleaned = learning.clean_body("あ" * 3000)
        self.assertEqual(len(cleaned), learning.NOTE_LIMIT + len(learning.CUT_MARK))
        self.assertTrue(cleaned.endswith(learning.CUT_MARK))

    def test_stamp14(self) -> None:
        self.assertEqual(learning.stamp14("2026-09-29T12:00:00+0900"), "20260929120000")

    def test_build_digest(self) -> None:
        deny = {
            "decision": "deny",
            "enforced": True,
            "rules": ["r1"],
            "tool": "Bash",
            "subject": "/usr/bin/git push",
        }
        records = [deny, dict(deny), {"decision": "ask"}, {"decision": "nudge"}]
        digest = learning.build_digest(records, True)
        self.assertEqual(digest["counts"], {"deny": 2, "ask": 1, "nudge": 1, "handover": 0})
        self.assertEqual(
            digest["repeated"],
            [{"rule": "r1", "tool": "Bash", "word": "git", "count": 2}],
        )
        self.assertTrue(digest["recorded"])

    def test_read_log_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "decisions.jsonl")
            with open(log, "w", encoding="utf-8") as f:
                f.write(json.dumps({"decision": "deny"}) + "\n")
                f.write("壊れた行\n")
            with open(os.path.join(tmp, "decisions.1.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"decision": "ask"}) + "\n")
            records, recorded = learning.read_log_records(log)
            self.assertEqual(len(records), 2)
            self.assertTrue(recorded)
        self.assertEqual(learning.read_log_records(""), ([], False))


if __name__ == "__main__":
    unittest.main()
