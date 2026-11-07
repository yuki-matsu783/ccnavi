"""手で書く match（共通ルールと開発用 hook）のツール名の組。

ツール名の一覧そのものは `go/internal/infra/config` の go test が見る。
"""

from __future__ import annotations

import json
import os
import re
import unittest

from tests import ROOT


class HandWrittenMatchTest(unittest.TestCase):
    """手で書く match（共通ルールと開発用 hook）は、Edit を挙げたら MultiEdit も挙げる。

    MultiEdit は Edit と同じく既存のファイルを直す。片方だけだと、同じ書き込みが
    ツールの選び方だけでルールから外れる。
    """

    def test_edit_comes_with_multiedit(self):
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


if __name__ == "__main__":
    unittest.main()
