"""テストの置き場の決まり。

テストは主題ごとのサブパッケージ（`tests/<グループ>/`）に置き、グループ単位で回す
（`.claude/skills/commit/references/test-groups.md`）。置き場を外れたテストは、どちらの形でも
気づかないうちに回らなくなるので、ここで止める。

- `tests/` の直下に置くと、全件では回るが、どのグループを回しても入らない
- `__init__.py` の無いディレクトリに置くと、`unittest discover` がそのディレクトリを飛ばす
  （Python 3.11 から名前空間パッケージを辿らない）ので、全件でも回らない

グループのテストは他のグループのモジュールを import しない。import すると、1 グループだけを
回したつもりでも他のグループのモジュールが読み込まれ、そちらを動かすと黙って壊れる。
共有する部品は `tests/` 直下の補助モジュール（`inproc.py`・`ticketsample.py` など）に置く。
"""

import ast
import os
import unittest

from tests import ROOT

TESTS = os.path.join(ROOT, "tests")
GUIDE = ".claude/skills/commit/references/test-groups.md"


def is_test_module(name):
    return name.startswith("test_") and name.endswith(".py")


class LayoutTest(unittest.TestCase):
    def test_no_test_module_sits_directly_under_tests(self):
        stray = sorted(n for n in os.listdir(TESTS) if is_test_module(n))
        self.assertEqual(stray, [], f"tests/<グループ>/ へ移す。どのグループかは {GUIDE}")

    def test_every_directory_holding_tests_is_a_package(self):
        missing = []
        for base, dirs, files in os.walk(TESTS):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            holds_tests = base != TESTS and any(is_test_module(n) for n in files)
            if holds_tests and "__init__.py" not in files:
                missing.append(os.path.relpath(base, ROOT))
        self.assertEqual(missing, [], "__init__.py が無いと discover がこのディレクトリを飛ばす")

    def test_groups_do_not_import_each_other(self):
        groups = sorted(
            d for d in os.listdir(TESTS) if os.path.isfile(os.path.join(TESTS, d, "__init__.py"))
        )
        crossing = []
        for group in groups:
            base = os.path.join(TESTS, group)
            for current, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in sorted(files):
                    if not name.endswith(".py"):
                        continue
                    path = os.path.join(current, name)
                    for target in imported_groups(path, groups):
                        if target != group:
                            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                            crossing.append(f"{rel} -> tests.{target}")
        self.assertEqual(crossing, [], f"共有する部品は tests/ 直下の補助モジュールへ出す。{GUIDE}")


def imported_groups(path, groups):
    """`path` が import する `tests.<グループ>` のグループ名。

    `from tests import <グループ>` の形も数える。
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
            if node.module == "tests":
                names += [f"tests.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if len(parts) >= 2 and parts[0] == "tests" and parts[1] in groups:
                found.append(parts[1])
    return found


if __name__ == "__main__":
    unittest.main()
