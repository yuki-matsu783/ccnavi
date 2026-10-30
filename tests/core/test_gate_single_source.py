"""関門の条件を調べる git の呼び出しが、`treegate` の 1 か所にあること。

親のワークツリーの未コミットの変更（`status --porcelain --untracked-files=no`）と、
親のブランチの未 push（`rev-list origin/<ブランチ>..HEAD`）を数える実装を、操作ごとに
書き直さない（`.agents/docs/gate-conditions.md`）。`src/ccnavi/` の git の引数の並びを ast で
走査し、`treegate.py` の外に見つかったら失敗させる。別の目的で同じ git を使うものは `EXCEPTIONS`
に理由つきで挙げる。
"""

from __future__ import annotations

import ast
import os
import unittest

from tests import SRC

PACKAGE = os.path.join(SRC, "ccnavi")
TREEGATE = os.path.join("tickets", "lifecycle", "treegate.py")

# (ファイル, 種類) -> 関門の条件でない理由。
EXCEPTIONS = {
    (
        os.path.join("tickets", "lifecycle", "stop_integrate.py"),
        "unpushed",
    ): "統合先（origin/<統合先>）の未取り込みを数える。親のブランチの未 push ではない",
}


def _strings(node: ast.List) -> list[str]:
    out = []
    for item in node.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            out.append(item.value)
        elif isinstance(item, ast.JoinedStr):
            out.append(
                "".join(
                    v.value if isinstance(v, ast.Constant) else "{}"
                    for v in item.values
                    if isinstance(v, (ast.Constant, ast.FormattedValue))
                )
            )
    return out


def _hits() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for base, _, names in os.walk(PACKAGE):
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, PACKAGE)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.List):
                    continue
                words = _strings(node)
                if "status" in words and "--porcelain" in words and "--untracked-files=no" in words:
                    found.add((rel, "dirty"))
                if "rev-list" in words and any("origin/" in w for w in words):
                    found.add((rel, "unpushed"))
    return found


class GateSingleSourceTest(unittest.TestCase):
    def test_only_treegate_reads_dirty_and_unpushed(self):
        outside = sorted(hit for hit in _hits() if hit[0] != TREEGATE and hit not in EXCEPTIONS)
        self.assertEqual(outside, [], "関門の条件は treegate に置く")

    def test_treegate_has_both_reads(self):
        hits = _hits()
        self.assertIn((TREEGATE, "dirty"), hits)
        self.assertIn((TREEGATE, "unpushed"), hits)

    def test_every_exception_is_still_needed(self):
        self.assertEqual(sorted(set(EXCEPTIONS) - _hits()), [])


if __name__ == "__main__":
    unittest.main()
