"""ツール名の一覧（`infra/toolnames.py`）が 1 か所だけにあること。

同じ組をモジュールごとに書き直すと、ツールが増えたときに直し漏れが出る。
`src/ccnavi/` の中で、ツール名の組をリテラルで並べてよいのは `infra/toolnames.py` だけ。
"""

from __future__ import annotations

import ast
import os
import unittest

from ccnavi.infra import toolnames

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src", "ccnavi")
NAMES = {
    "Write",
    "Edit",
    "NotebookEdit",
    "Agent",
    "Task",
    "Bash",
    "PowerShell",
    "Read",
    "Grep",
    "Glob",
}


def literal_groups(path: str) -> list[tuple[int, tuple[str, ...]]]:
    """タプルかリストのリテラルで、ツール名を 2 つ以上並べたもの。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)):
            items = tuple(
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
            if len(items) >= 2 and len([i for i in items if i in NAMES]) >= 2:
                found.append((node.lineno, items))
    return found


class ToolNamesTest(unittest.TestCase):
    def test_no_other_module_lists_tool_names(self):
        found = []
        for base, _dirs, files in os.walk(SRC):
            for name in files:
                path = os.path.join(base, name)
                if not name.endswith(".py") or path.endswith(os.path.join("infra", "toolnames.py")):
                    continue
                found += [
                    f"{os.path.relpath(path, SRC)}:{line} {items}"
                    for line, items in literal_groups(path)
                ]
        self.assertEqual([], found, "ツール名の組は infra/toolnames.py から引く")

    def test_groups_keep_their_meaning(self):
        self.assertEqual(("Write", "Edit", "MultiEdit", "NotebookEdit"), toolnames.WRITE_TOOLS)
        self.assertEqual(("Agent", "Task"), toolnames.AGENT_TOOLS)
        self.assertEqual({"Bash", "PowerShell"}, set(toolnames.SHELL_TOOLS))
        self.assertTrue(set(toolnames.WRITE_TOOLS) <= set(toolnames.STRICT_TOOLS))
        self.assertTrue(set(toolnames.AGENT_TOOLS) <= set(toolnames.STRICT_TOOLS))
        self.assertTrue(set(toolnames.READ_TOOLS) <= set(toolnames.READ_ONLY_TOOLS))
        self.assertFalse(set(toolnames.WRITE_TOOLS) & set(toolnames.READ_ONLY_TOOLS))


if __name__ == "__main__":
    unittest.main()


class HandWrittenMatchTest(unittest.TestCase):
    """手で書く match（共通ルールと開発用 hook）は、Edit を挙げたら MultiEdit も挙げる。

    MultiEdit は Edit と同じく既存のファイルを直す。片方だけだと、同じ書き込みが
    ツールの選び方だけでルールから外れる。
    """

    def test_edit_comes_with_multiedit(self):
        import json
        import re

        from tests import ROOT

        found = []
        with open(
            os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml"), encoding="utf-8"
        ) as f:
            for number, line in enumerate(f, 1):
                matched = re.match(r"\s*match:\s*(\S+)", line)
                if matched:
                    found.append((f"rules.yml:{number}", matched.group(1)))
        with open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8") as f:
            for event in json.load(f).get("hooks", {}).values():
                found += [("settings.json", entry.get("matcher", "")) for entry in event]
        missing = [
            where
            for where, match in found
            if "Edit" in match.split("|") and "MultiEdit" not in match.split("|")
        ]
        self.assertEqual([], missing)
