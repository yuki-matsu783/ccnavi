"""tests/scenarios/ の import の向きを、テストで守る。

段の表は `runner.py` の docstring に 1 か所だけある。ここはその表を読んで、
各モジュールの import が同じ段か下の段だけを向くことを確かめる（`tests/core/test_module_layers.py`
の、`src/ccnavi/` に対する検査と同じ考え）。表を直すのは runner.py の docstring。

見るのは 4 つ。

1. 置いてある全モジュール（`actors/` を含む）が、表のどの段かをちょうど 1 つ持つ。表にあって
   置いていないモジュールも止める。モジュールを足したら、どの段かを決めさせる
2. import の行き先（`tests.scenarios` の中）は、同じ段か下の段
3. 循環を許さない
4. `tests.scenarios` の中への相対 import、`import tests.scenarios`、パッケージや `*` を
   名前で取り込む書き方（`from tests.scenarios import actors` など）は使わない
   （行き先を 1 と 2 が読めなくなる）

関数の中の import も数える。test_*.py は検査の対象外（全部を読んで確かめる側）。
"""

import ast
import unittest
from pathlib import Path

DIR = Path(__file__).parents[1] / "scenarios"
PREFIX = "tests.scenarios"
# runner を import すると、グループをまたぐ import になる（tests/core/test_layout.py）。ファイルから読む。
RUNNER = DIR / "runner.py"


def tiers():
    """runner.py の docstring の段の表 → {モジュール名（ドット区切り）: 段の番号}。
    `actors/` は actors の中の全モジュール、「この入口」は runner。"""
    text = " ".join(ast.get_docstring(ast.parse(RUNNER.read_text(encoding="utf-8"))).split())
    start = text.index("0 common")
    table = text[start:].rstrip("。.")
    found = {}
    for part in table.split(" / "):
        num, _, names = part.partition(" ")
        for name in names.split("・"):
            name = "runner" if name == "この入口" else name
            if name in found:
                raise AssertionError(f"段の表に {name} が 2 回ある")
            found[name] = int(num)
    return found


def modules():
    """置いてある検査対象のモジュール（ドット区切り、tests.scenarios からの相対）。"""
    found = set()
    for path in DIR.rglob("*.py"):
        rel = path.relative_to(DIR)
        if path.name == "__init__.py" or path.name.startswith("test_"):
            continue
        found.add(".".join(rel.with_suffix("").parts))
    return found


def tier_of(module, table):
    if module in table:
        return table[module]
    if module.startswith("actors."):
        return table.get("actors/")
    return None


def imports_of(module, known):
    """module が import する tests.scenarios のモジュール（known にあるもの）と、禁止形の一覧。"""
    tree = ast.parse((DIR / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))
    targets, bad = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                bad.append(f"相対 import（行 {node.lineno}）")
                continue
            base = node.module or ""
            if base == PREFIX or base.startswith(PREFIX + "."):
                rel = base[len(PREFIX) :].lstrip(".")
                for alias in node.names:
                    cand = f"{rel}.{alias.name}" if rel else alias.name
                    if cand in known:
                        targets.add(cand)
                    elif rel in known:
                        targets.add(rel)
                    else:
                        bad.append(
                            f"from {base} import {alias.name}（行 {node.lineno}）。"
                            "モジュールを名前で取り込む"
                        )
            elif base == "tests" and any(a.name == "scenarios" for a in node.names):
                bad.append(
                    f"from tests import scenarios（行 {node.lineno}）。モジュールを名前で取り込む"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PREFIX or alias.name.startswith(PREFIX + "."):
                    bad.append(f"import {alias.name}（行 {node.lineno}）。from で書く")
    return targets, bad


class ScenarioLayersTest(unittest.TestCase):
    def test_every_module_has_one_tier(self):
        table = tiers()
        have = modules()
        missing = sorted(m for m in have if tier_of(m, table) is None)
        self.assertEqual(missing, [], f"runner.py の docstring の段の表に無いモジュール: {missing}")
        names = {m.split(".")[0] + "/" if m.startswith("actors.") else m for m in have}
        ghost = sorted(n for n in table if n not in names)
        self.assertEqual(ghost, [], f"段の表にあって置いていないモジュール: {ghost}")

    def test_imports_go_same_tier_or_down(self):
        table = tiers()
        have = modules()
        errors = []
        for mod in sorted(have):
            targets, bad = imports_of(mod, have)
            errors += [f"{mod}: {b}" for b in bad]
            for t in sorted(targets - {mod}):
                if tier_of(t, table) > tier_of(mod, table):
                    a, b = tier_of(mod, table), tier_of(t, table)
                    errors.append(f"{mod}（段 {a}）が上の {t}（段 {b}）を読んでいる")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_no_cycles(self):
        have = modules()
        graph = {m: imports_of(m, have)[0] - {m} for m in have}
        state = {}

        def visit(m, path):
            if state.get(m) == 1:
                self.fail("循環: " + " -> ".join([*path[path.index(m) :], m]))
            if state.get(m) == 2:
                return
            state[m] = 1
            for t in sorted(graph[m]):
                visit(t, [*path, m])
            state[m] = 2

        for m in sorted(have):
            visit(m, [])

    def test_table_numbers_are_contiguous(self):
        nums = sorted(set(tiers().values()))
        self.assertEqual(nums, list(range(len(nums))))


if __name__ == "__main__":
    unittest.main()
