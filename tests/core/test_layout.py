"""テストの置き場の決まり。

テストは主題ごとのサブパッケージ（`tests/<グループ>/`）に置き、グループ単位で回す
（`.claude/skills/commit/references/test-groups.md`）。置き場を外れたテストは、どちらの形でも
黙って回らなくなるので、ここで止める。

- `tests/` の直下に置くと、全件では回るが、どのグループを回しても入らない
- `__init__.py` の無いディレクトリに置くと、`unittest discover` がそのディレクトリを飛ばす
  （Python 3.11 から名前空間パッケージを辿らない）ので、全件でも回らない
"""

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


if __name__ == "__main__":
    unittest.main()
