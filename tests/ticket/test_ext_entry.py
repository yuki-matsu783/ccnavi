"""Chrome 拡張の公開の入口（`ccnavi.entry.ext`）を守る受入テスト。

拡張（`extensions/chrome/ccnavi-approval/py/ccnavi_chrome.py`）は本体の内部の名前を直接参照していたため、
本体で改名・移動すると拡張だけが壊れた。拡張は今、`ccnavi.entry.ext` の再公開モジュールだけを呼ぶ。

確かめるのは次の 3 点。

1. `ccnavi_chrome.py` は、`ccnavi.entry.ext` 以外の `ccnavi.` 下のモジュールを import しない
2. `ext` 経由で引いている `<モジュール>.<属性>` を AST で全部集め、`ext` の同名のモジュールにその属性があることを確かめる。
   名前を消すと、拡張が壊れる前にここが落ちる
3. `ext` はモジュールをそのまま再公開する（別のオブジェクトを作らない）。
   `settings` の属性を差し替えたとき（`test_swap_points.py` の `ENV_CONSTANTS`）、その差し替えは `ext` 経由でも効く
"""

from __future__ import annotations

import ast
import os
import unittest

from ccnavi.entry import ext
from ccnavi.infra import settings
from tests.ticketsample import CHROME

CHROME_PY = os.path.join(CHROME, "py", "ccnavi_chrome.py")
EXT_MODULE = "ccnavi.entry.ext"


def _parse():
    with open(CHROME_PY, encoding="utf-8") as f:
        return ast.parse(f.read(), CHROME_PY)


def used_attributes() -> dict[str, set[str]]:
    """`ext` から取り込んだ名前（別名は元の名前に戻す）→ 拡張が引いている属性の集まり。"""
    tree = _parse()
    alias_of: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == EXT_MODULE:
            for a in node.names:
                alias_of[a.asname or a.name] = a.name
    found: dict[str, set[str]] = {name: set() for name in alias_of.values()}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in alias_of
        ):
            found[alias_of[node.value.id]].add(node.attr)
    return found


class ExtEntryTest(unittest.TestCase):
    def test_only_the_ext_module_is_imported_from_ccnavi(self):
        found = []
        for node in ast.walk(_parse()):
            if isinstance(node, ast.ImportFrom):
                from_ccnavi = node.level or (node.module or "").split(".")[0] == "ccnavi"
                if from_ccnavi and (node.level or node.module != EXT_MODULE):
                    found.append(f"{node.lineno}: from {'.' * node.level}{node.module}")
            elif isinstance(node, ast.Import):
                found += [
                    f"{node.lineno}: import {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] == "ccnavi"
                ]
        self.assertEqual(found, [], f"ccnavi_chrome.py が {EXT_MODULE} 以外を import している")

    def test_every_used_attribute_exists(self):
        used = used_attributes()
        self.assertTrue(used, "ext 経由の使用が 1 つも見つからない")
        missing = [
            f"{module}.{attr}"
            for module, attrs in sorted(used.items())
            for attr in sorted(attrs)
            if not hasattr(getattr(ext, module, None), attr)
        ]
        self.assertEqual(missing, [], "拡張が引く名前が本体に無い")

    def test_every_imported_name_is_exported(self):
        for module in used_attributes():
            self.assertIn(module, ext.__all__, module)
            self.assertTrue(hasattr(ext, module), module)

    def test_modules_are_republished_as_they_are(self):
        # モジュールそのものなので、settings を差し替えると ext 経由でも見える
        self.assertIs(ext.settings, settings)
        saved = settings.DEFAULT_TICKETS
        self.addCleanup(setattr, settings, "DEFAULT_TICKETS", saved)
        settings.DEFAULT_TICKETS = "swapped"
        self.assertEqual(ext.settings.DEFAULT_TICKETS, "swapped")


if __name__ == "__main__":
    unittest.main()
