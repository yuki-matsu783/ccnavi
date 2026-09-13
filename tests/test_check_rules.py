"""tools/check_rules.py が既定で指す置き場。

置き場を `.ccnavi/common/` へ移したとき（ADR-0042）、見本の綴りだけが前の
`.claude/ccnavi/` のまま残り、引数なしで打つと見本を読めずに落ちていた。
ツールを最後まで回すと、見本の食い違いの有無で終了コードが変わるので、
ここでは既定の綴りが実在することだけを見る。
"""

from __future__ import annotations

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_check_rules():
    """tools/ はパッケージではないので、名前でなく場所で読む。"""
    spec = importlib.util.spec_from_file_location(
        "ccnavi_check_rules", os.path.join(ROOT, "tools", "check_rules.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DefaultPlacesTest(unittest.TestCase):
    def setUp(self):
        self.tool = _load_check_rules()

    def test_the_default_rules_exist(self):
        self.assertTrue(os.path.isfile(self.tool.RULES), self.tool.RULES)

    def test_the_default_samples_exist(self):
        self.assertTrue(os.path.isfile(self.tool.SAMPLES), self.tool.SAMPLES)


if __name__ == "__main__":
    unittest.main()
