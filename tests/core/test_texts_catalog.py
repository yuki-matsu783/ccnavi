"""文面ファイルと、それを引くコード・sh の食い違いを見る。

ccnavi のリポジトリのソースがあるときだけ確かめられる点検（配布先の `--lint` には呼ぶ側が無い）。

- 引かれている識別子は、共通レイヤーの文面ファイルにある
- 文面ファイルの識別子は、どこかから引かれている
- 呼ぶ側が渡す値の名前は、文面ファイルの `values` と一致する
- 共通レイヤーの文面ファイルは検査を通る
"""

from __future__ import annotations

import ast
import glob
import os
import re
import unittest

from ccnavi.policy import texts
from tests import ROOT

SRC = os.path.join(ROOT, "src", "ccnavi")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")

# sh の呼び出し。識別子の `$` を含むものは、頭の部分だけが決まっている。
_SH_CALL = re.compile(r'ccnavi_text "[^"]*" "?([A-Z][A-Z0-9_$a-z]*)"?((?: "[a-z_@]+=[^"]*")*)')
_SH_VALUE = re.compile(r'"([a-z_@]+)=')


def python_calls() -> list[tuple[str, str, set[str] | None, str]]:
    """（ファイル, 識別子, 値の名前（辞書が書かれていなければ None）, 場所）。"""
    found = []
    for path in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "say"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "texts"
                and len(node.args) >= 2
            ):
                continue
            ident = node.args[1]
            if not (isinstance(ident, ast.Constant) and isinstance(ident.value, str)):
                continue
            names = set()
            values = node.args[2] if len(node.args) > 2 else None
            for kw in node.keywords:
                if kw.arg == "values":
                    values = kw.value
            if values is None:
                names = set()
            elif isinstance(values, ast.Dict) and all(
                isinstance(k, ast.Constant) for k in values.keys
            ):
                names = {k.value for k in values.keys}
            else:
                names = None
            found.append((path, ident.value, names, f"{os.path.relpath(path, ROOT)}:{node.lineno}"))
    return found


def sh_calls() -> list[tuple[str, str, set[str]]]:
    found = []
    for path in glob.glob(os.path.join(SCRIPTS, "*.sh")):
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                for hit in _SH_CALL.finditer(line):
                    names = {n for n in _SH_VALUE.findall(hit.group(2)) if not n.startswith("@")}
                    found.append((hit.group(1), f"{os.path.basename(path)}:{number}", names))
    return found


def source_text() -> str:
    parts = []
    for path in [
        *glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True),
        *glob.glob(os.path.join(SCRIPTS, "*.sh")),
    ]:
        with open(path, encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = texts.load(ROOT)

    def test_the_common_files_pass_the_check(self):
        self.assertEqual([], [str(p) for p in self.catalog.problems])
        self.assertGreater(len(self.catalog.entries), 20)

    def test_every_id_the_code_asks_for_exists_with_the_same_values(self):
        self.assertGreaterEqual(len(python_calls()), 8, "say の呼び出しを拾えていない")
        self.assertGreaterEqual(len(sh_calls()), 25, "ccnavi_text の呼び出しを拾えていない")
        for _, ident, names, where in python_calls():
            with self.subTest(where=where):
                entry = self.catalog.entries.get(ident)
                self.assertIsNotNone(entry, f"{where}: {ident} が文面ファイルに無い")
                if names is not None:
                    self.assertEqual(set(entry.values), names, f"{where}: {ident} の値の名前")
        for ident, where, names in sh_calls():
            with self.subTest(where=where):
                if "$" in ident:
                    head = ident.split("$", 1)[0]
                    self.assertTrue(
                        [i for i in self.catalog.entries if i.startswith(head)],
                        f"{where}: {ident}",
                    )
                    continue
                entry = self.catalog.entries.get(ident)
                self.assertIsNotNone(entry, f"{where}: {ident} が文面ファイルに無い")
                self.assertEqual(set(entry.values), names, f"{where}: {ident} の値の名前")

    def test_every_id_in_the_files_is_asked_for_somewhere(self):
        body = source_text()
        heads = [i.split("$", 1)[0] for i, _, _ in sh_calls() if "$" in i]
        for ident in self.catalog.entries:
            with self.subTest(ident=ident):
                used = ident in body or any(ident.startswith(h) for h in heads)
                self.assertTrue(used, f"{ident} をどこも引かない")


if __name__ == "__main__":
    unittest.main()
