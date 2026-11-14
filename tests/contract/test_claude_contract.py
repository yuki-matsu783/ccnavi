"""claude -p の代役（`tests/fake_claude.py`）が、本物から録った形と振る舞いに合っているか。

本物の記録は `tests/manual/claude/record_hooks.py` が作る `tests/contract/claude/recorded.json`。
場面（木・hook・打つ手）は `tests/contract/claudeside.py` で本物と共有する。

- 形: 代役が出す欄は本物にもあり、型は本物の型に含まれる。本物にだけある欄は比べない
  （代役は ccnavi と実行器が読む欄だけを出す）。ただし ccnavi が読む hook の欄（`READS`）は、
  本物にあれば代役にもなければならない
- 振る舞い: `observations`（呼ばれたイベントの並び、止めたときの理由の出どころ、Stop の差し戻し、
  サブエージェント、`--resume`、hook の環境）は本物と同じ
"""

from __future__ import annotations

import json
import tempfile
import unittest

from tests.contract import claudeside

# ccnavi が hook の payload から読む欄（`go/internal/infra/hookio/decode.go` の欄の一覧）
READS = (
    "hook_event_name",
    "tool_name",
    "session_id",
    "cwd",
    "permission_mode",
    "tool_use_id",
    "tool_input",
    "tool_response",
    "agent_id",
    "agent_type",
    "error",
    "is_interrupt",
    "stop_hook_active",
    "source",
    "prompt",
    "transcript_path",
    "background_tasks",
    "session_crons",
)
# tool_response の中で ccnavi が読む欄（Write の作成・Agent の識別子）
RESPONSE_READS = {"PostToolUse:Write": ("type",), "PostToolUse:Agent": ("agentId",)}


def not_in_real(fake: dict, real: dict, path: str) -> list[str]:
    """代役の形のうち、本物に無い欄と、本物の型に含まれない型。"""
    out = []
    if not set(fake["t"]) <= set(real["t"]):
        out.append(f"{path}: 型 {fake['t']} が本物 {real['t']} に無い")
    for k, sub in (fake.get("keys") or {}).items():
        if k not in (real.get("keys") or {}):
            out.append(f"{path}.{k}: 本物に無い欄")
        else:
            out += not_in_real(sub, real["keys"][k], f"{path}.{k}")
    if "items" in fake and "items" in real:
        out += not_in_real(fake["items"], real["items"], f"{path}[]")
    return out


class ClaudeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(claudeside.RECORDED, encoding="utf-8") as f:
            cls.real = json.load(f)
        cls.base = tempfile.TemporaryDirectory()
        cls.fake = claudeside.run_fake(cls.base.name)

    @classmethod
    def tearDownClass(cls):
        cls.base.cleanup()

    def test_shapes_are_in_real(self):
        problems = []
        for section in ("hooks", "stream"):
            for key, node in self.fake[section].items():
                real = self.real[section].get(key)
                if real is None:
                    problems.append(f"{section}/{key}: 本物に無い")
                else:
                    problems += not_in_real(node, real, f"{section}/{key}")
        self.assertEqual(problems, [])

    def test_fake_has_fields_ccnavi_reads(self):
        problems = []
        for key, real in self.real["hooks"].items():
            fake = self.fake["hooks"].get(key)
            if fake is None:
                problems.append(f"{key}: 代役が出さない")
                continue
            for field in READS:
                if field in real["keys"] and field not in fake["keys"]:
                    problems.append(f"{key}.{field}: 代役に無い")
            for field in RESPONSE_READS.get(key, ()):
                if field not in fake["keys"]["tool_response"]["keys"]:
                    problems.append(f"{key}.tool_response.{field}: 代役に無い")
        self.assertEqual(problems, [])

    def test_observations_match_real(self):
        self.assertEqual(self.fake["observations"], self.real["observations"])


if __name__ == "__main__":
    unittest.main()
