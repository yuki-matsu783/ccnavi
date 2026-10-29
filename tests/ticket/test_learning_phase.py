"""フェーズ定義の欄 `learning`（on / off）の読み方・コピーの往復・同じ定義かの判定を見る。"""

from __future__ import annotations

import unittest

import yaml

from ccnavi.tickets.config import phasetypes


def text(extra: str) -> str:
    return "version: 1\nphases:\n  t1:\n    title: t\n" + extra


class LearningPhaseTest(unittest.TestCase):
    def test_default_is_on(self) -> None:
        types, _ = phasetypes.parse(text(""))
        self.assertEqual(types["t1"].learning, "on")

    def test_off_forms(self) -> None:
        for extra in ("    learning: off\n", '    learning: "off"\n', "    learning: OFF\n"):
            with self.subTest(extra=extra):
                types, _ = phasetypes.parse(text(extra))
                self.assertEqual(types["t1"].learning, "off")
        types, _ = phasetypes.parse(text("    learning: on\n"))
        self.assertEqual(types["t1"].learning, "on")

    def test_invalid_learning_is_error(self) -> None:
        types, problems = phasetypes.parse(text("    learning: maybe\n"))
        self.assertTrue(any(p.severity == phasetypes.SEVERITY_ERROR for p in problems))
        self.assertNotIn("t1", types or {})

    def test_copy_roundtrip(self) -> None:
        off, _ = phasetypes.parse(text("    learning: off\n"))
        on, _ = phasetypes.parse(text(""))
        off_copy = phasetypes.as_copy(off["t1"])
        self.assertEqual(off_copy["learning"], "off")
        self.assertNotIn("learning", phasetypes.as_copy(on["t1"]))
        loaded = yaml.safe_load(yaml.safe_dump(off_copy))
        again, _ = phasetypes.parse(yaml.safe_dump({"version": 1, "phases": {"t1": loaded}}))
        self.assertEqual(again["t1"].learning, "off")
        self.assertFalse(phasetypes.same(off["t1"], on["t1"]))


if __name__ == "__main__":
    unittest.main()
