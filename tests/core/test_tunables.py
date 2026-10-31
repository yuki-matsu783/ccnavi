"""環境変数で動かす秒数のしきい値（`infra/tunables.py`）。

見るのは次の 3 つ。

- 無い値・読めない値・0 以下・有限でない値は既定に戻る
- 正の数は、整数でも小数でもそのまま使う
- 使う側のモジュールは、既定のままなら元の値と同じ値を使う
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
    def test_env_names_are_distinct(self):
        envs = [t.env for t in vars(tunables).values() if isinstance(t, tunables.Tunable)]
        self.assertEqual(5, len(envs))
        self.assertEqual(len(envs), len(set(envs)))

    def test_users_keep_the_original_defaults(self):
        # 開発者のシェルに CCNAVI_*_SECONDS があるものは、使う側の値の比較から外す。
        pairs = [
            (tunables.HOOK_DEADLINE, 3.0),
            (tunables.INTEGRATE_PENDING, 2.0),
            (tunables.SUBAGENT_RUNNING_MAX, 4 * 3600.0),
            (tunables.TURN_STALE, 30 * 60.0),
            (tunables.STOP_REVIEW_TTL, 600.0),
        ]
        for tunable, default in pairs:
            with self.subTest(env=tunable.env):
                self.assertEqual(default, tunable.default)
        used = {
            tunables.HOOK_DEADLINE: judge.DEADLINE_SECONDS,
            tunables.INTEGRATE_PENDING: events.PENDING_SECONDS,
            tunables.SUBAGENT_RUNNING_MAX: subagent.RUNNING_MAX_SECONDS,
            tunables.TURN_STALE: turn.STALE_SECONDS,
            tunables.STOP_REVIEW_TTL: stopreview.DEFAULT_TTL_SECONDS,
        }
        for tunable, value in used.items():
            if tunable.env not in os.environ:
                with self.subTest(env=tunable.env):
                    self.assertEqual(tunable.default, value)


if __name__ == "__main__":
    unittest.main()
