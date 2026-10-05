"""ファサード（`core`・`diagnose`）の名前への patch を、テストで止める。

`core.py` と `diagnose.py` は、実体のモジュール（`core_approve`・`diagnose_try` など）の名前を
束ね直す薄いファサード。`mock.patch.object(core, "x")` は `core` の属性を差し替えるだけで、
実体のモジュールの中の呼び出し（`core_base.x(...)`）には効かない。効いたつもりで通るテストは、
差し替えたはずの処理を本物のまま走らせる。

`tests/` を `ast` で走査し、次の形を見つける。許可リスト（`ALLOWED`）にない patch は失敗にする。

- `mock.patch.object(<core か diagnose>, "名前")`（`cli.core` のような属性参照も含む）
- `mock.patch("ccnavi.hook.core.名前")`、`mock.patch("ccnavi.entry.diagnose.名前")`

patch は実体のモジュールに対して行う。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests import ROOT

TESTS = Path(ROOT) / "tests"

FACADES = ("core", "diagnose")
FACADE_PATHS = ("ccnavi.hook.core.", "ccnavi.entry.diagnose.")

# (tests/ からの相対パス, patch 先の式, 名前)。許可する理由をそれぞれに書く。
ALLOWED = {
    # 呼び手の `cli` が `core.approve` / `core.approve_yes` を呼ぶたびに属性参照で引くので、
    # `cli.core` の名前を差し替えると効く。
    ("core/test_approve_renamed.py", "cli.core", "approve"),
    ("core/test_approve_renamed.py", "cli.core", "approve_yes"),
}


def _is_patch(func: ast.expr, last: str) -> bool:
    """`mock.patch.object` / `patch.object`（`last` が `object`）か、`mock.patch` / `patch` か。"""
    if last == "object":
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "object"
            and _is_patch(func.value, "patch")
        )
    if isinstance(func, ast.Name):
        return func.id == "patch"
    return isinstance(func, ast.Attribute) and func.attr == "patch"


def _facade_patches(tree: ast.AST) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if _is_patch(node.func, "object"):
            tail = first.attr if isinstance(first, ast.Attribute) else getattr(first, "id", "")
            if tail not in FACADES or len(node.args) < 2:
                continue
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                found.append((ast.unparse(first), name.value))
        elif _is_patch(node.func, "patch"):
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                for prefix in FACADE_PATHS:
                    rest = first.value.removeprefix(prefix)
                    if first.value.startswith(prefix) and "." not in rest:
                        found.append((first.value.removesuffix("." + rest), rest))
    return found


class FacadePatchTest(unittest.TestCase):
    def test_no_patch_on_a_facade_name_outside_the_allowed_list(self):
        unknown = []
        seen = set()
        for path in sorted(TESTS.rglob("*.py")):
            rel = path.relative_to(TESTS).as_posix()
            for target, name in _facade_patches(ast.parse(path.read_text(encoding="utf-8"))):
                seen.add((rel, target, name))
                if (rel, target, name) not in ALLOWED:
                    unknown.append(f"tests/{rel}: patch {target}.{name}")
        self.assertEqual(
            [],
            unknown,
            "ファサードの名前への patch は、実体のモジュール（core_approve など）の中の呼び出しに"
            "効かない。実体のモジュールに対して patch する",
        )
        self.assertEqual(set(), ALLOWED - seen, "許可リストに、もう無い patch が残っている")

    def test_the_scan_finds_each_form(self):
        tree = ast.parse(
            "mock.patch.object(core, 'a')\n"
            "mock.patch.object(cli.diagnose, 'b')\n"
            "mock.patch('ccnavi.hook.core.c')\n"
            "mock.patch('ccnavi.entry.diagnose.d')\n"
            "mock.patch('ccnavi.hook.core_base.e')\n"
            "mock.patch.object(core_base, 'f')\n"
        )
        self.assertEqual(
            [
                ("core", "a"),
                ("cli.diagnose", "b"),
                ("ccnavi.hook.core", "c"),
                ("ccnavi.entry.diagnose", "d"),
            ],
            sorted(_facade_patches(tree), key=lambda p: p[1]),
        )


if __name__ == "__main__":
    unittest.main()
