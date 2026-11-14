"""台本のある受入シナリオを、使い捨てのワークスペースでスタブで回す（全量テストに入る）。

本物の claude は呼ばず、ユーザのワークスペースにも触れない。回せるシナリオの条件は
`schema.stub_unready`、ワークスペースの組み方は `sandbox.py`。シナリオに台本を足すと、ここで回る。
"""

import contextlib
import io
import unittest

from tests.scenarios import commands
from tests.scenarios.common import ROOT


class StubScenariosTest(unittest.TestCase):
    def test_scripted_scenarios_pass(self):
        paths = sorted((ROOT / "tests/scenarios").glob("*.yml"))
        with contextlib.redirect_stdout(io.StringIO()):
            results = commands.run_stub(paths)
        ran = [(sid, rc, note) for sid, rc, note in results if rc is not None]
        self.assertTrue(ran, "台本のあるシナリオが 1 本も無い")
        failed = [f"{sid}: 終了コード {rc}（{note}）" for sid, rc, note in ran if rc != 0]
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
