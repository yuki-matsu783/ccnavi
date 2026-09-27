"""`tools/run_tests.py` が、discover と同じものを回すことの検査。

あの道具は「同じテストを、分けて同時に回す」もの。速くなっても、回る本数が減っていたら
意味が逆になる。減ったことは失敗として出ないので（回らなかったテストは何も言わない）、
ここで突き合わせる。

契約は 1 つだけ。**`modules()` が並べたものの和集合が、`unittest discover` が拾う
モジュールと一致すること。** 並べる順や同時に回す本数は速さの都合なので、契約に入れない。

`tests/fixtures/` を拾わないことを名指しで見るのは、ここが実際に踏みやすい穴だから。
`tests/` の下のディレクトリを名前だけで数えるとグループに見えるが、`__init__.py` が
無いので discover は飛ばす。道具の側だけが拾うと `unittest tests.fixtures` が
「importable でない」で落ち、実行全体が失敗になる。
"""

from __future__ import annotations

import importlib.util
import os
import unittest

from tests import ROOT


def _load_run_tests():
    """tools/ はパッケージではないので、名前でなく場所で読む。"""
    spec = importlib.util.spec_from_file_location(
        "ccnavi_run_tests", os.path.join(ROOT, "tools", "run_tests.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_run_tests()


def discovered_modules():
    """`unittest discover -s tests -t .` が拾うテストモジュールの名前。"""
    found = set()
    suite = unittest.TestLoader().discover(
        start_dir=os.path.join(ROOT, "tests"), top_level_dir=ROOT
    )
    stack = [suite]
    while stack:
        item = stack.pop()
        if isinstance(item, unittest.TestSuite):
            stack.extend(item)
        else:
            found.add(type(item).__module__)
    return found


class CoversTheSameModulesTest(unittest.TestCase):
    def test_the_plan_matches_discover(self):
        planned = set(TOOL.modules([]))
        found = discovered_modules()
        self.assertEqual(
            found - planned, set(), "discover は拾うのに run_tests.py が回さないモジュール"
        )
        self.assertEqual(
            planned - found, set(), "run_tests.py だけが回すモジュール。discover に入らない"
        )

    def test_the_plan_has_no_duplicates(self):
        planned = TOOL.modules([])
        self.assertEqual(len(planned), len(set(planned)))

    def test_fixtures_is_not_a_group(self):
        """固定データの置き場はグループではない。`__init__.py` が無いほうで弾く。"""
        self.assertNotIn("fixtures", TOOL.groups())
        self.assertTrue(os.path.isdir(os.path.join(ROOT, "tests", "fixtures")), "見本が動いた")


class TakesTheSpellingsPeopleTypeTest(unittest.TestCase):
    def test_a_group_can_be_named_in_several_ways(self):
        by_slash = TOOL.modules(["tests/ticket"])
        self.assertEqual(by_slash, TOOL.modules(["tests/ticket/"]))
        self.assertEqual(by_slash, TOOL.modules(["tests.ticket"]))
        self.assertTrue(by_slash, "グループを名指ししたのに空")
        self.assertTrue(all(m.startswith("tests.ticket.") for m in by_slash))

    def test_a_single_module_can_be_named(self):
        self.assertEqual(
            ["tests.core.test_run_tests"], TOOL.modules(["tests/core/test_run_tests.py"])
        )
        self.assertEqual(["tests.core.test_run_tests"], TOOL.modules(["tests.core.test_run_tests"]))

    def test_an_unknown_name_plans_nothing(self):
        """知らない名前で黙って全件にならない。そうなると、名指しが効いていないことに気づけない。"""
        self.assertEqual([], TOOL.modules(["tests/nosuchgroup"]))


class HeaviestFirstTest(unittest.TestCase):
    def test_the_biggest_module_starts_first(self):
        """一番大きいものを最後に回すと、他の枠が空いたまま 1 本を待つことになる。"""
        biggest = max(
            (
                os.path.getsize(os.path.join(ROOT, "tests", group, name)),
                f"tests.{group}.{name[:-3]}",
            )
            for group in TOOL.groups()
            for name in os.listdir(os.path.join(ROOT, "tests", group))
            if name.startswith("test_") and name.endswith(".py")
        )[1]
        self.assertEqual(biggest, TOOL.modules([])[0])


if __name__ == "__main__":
    unittest.main()
