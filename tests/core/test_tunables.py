"""環境変数で動かす秒数のしきい値（`infra/tunables.py`）。

見るのは次の 3 つ。

- 無い値・読めない値・0 以下・有限でない値は既定に戻る
- 正の数は、整数でも小数でもそのまま使う
- 使う側のモジュールが、既定のままなら元の値を持つ
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from ccnavi.hook import events, judge, subagent
from ccnavi.infra import tunables
from ccnavi.records import stopreview, turn

T = tunables.Tunable("CCNAVI_TEST_TUNABLE_SECONDS", 3.0)


class TunableValueTest(unittest.TestCase):
    def read(self, written: str | None) -> float:
        env = {} if written is None else {T.env: written}
        with mock.patch.dict(os.environ, env):
            if written is None:
                os.environ.pop(T.env, None)
            return T.value()

    def test_unset_and_unreadable_values_fall_back_to_the_default(self):
        for written in (None, "", "abc", "0", "-1", "nan", "inf", "-inf"):
            with self.subTest(written=written):
                self.assertEqual(3.0, self.read(written))

    def test_positive_numbers_are_used(self):
        self.assertEqual(10.0, self.read("10"))
        self.assertEqual(0.5, self.read(" 0.5 "))


class DefaultsTest(unittest.TestCase):
    def test_users_keep_the_original_defaults(self):
        self.assertEqual(3.0, judge.DEADLINE_SECONDS)
        self.assertEqual(2.0, events.PENDING_SECONDS)
        self.assertEqual(4 * 3600, subagent.RUNNING_MAX_SECONDS)
        self.assertEqual(30 * 60, turn.STALE_SECONDS)
        self.assertEqual(600.0, stopreview.DEFAULT_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
