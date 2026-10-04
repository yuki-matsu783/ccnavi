"""前の名前 `--approve`（`--agree` に改名した）の扱い。

案内を標準エラーに出して終了コード 2 で終わる。別名ではないので、承認の処理は
呼ばれない。ヘルプにも `--version` の flags 一覧（契約）にも出ない。
エージェントが `--approve` を打つのを止める組み込みの deny は、判定側
（`tickets.phase`）にあり、ここでは変えない。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from unittest import mock

from ccnavi.entry import cli
from ccnavi.tickets import phase
from tests import ROOT
from tests.inproc import run_ccnavi


class ApproveRenamedTest(unittest.TestCase):
    def test_says_it_was_renamed_and_exits_2(self):
        for argv in (
            ["--approve"],
            ["--approve", "i0002"],
            ["--approve=x"],
            ["--agree", "--approve"],
        ):
            with self.subTest(argv=argv):
                result = run_ccnavi(argv, input="", cwd=ROOT)
                self.assertEqual(2, result.returncode)
                self.assertIn("--agree に改名しました。", result.stderr)
                self.assertIn("ccnavi-agree.sh", result.stderr)
                self.assertEqual("", result.stdout)

    def test_runs_nothing_of_the_approval(self):
        with (
            mock.patch.object(cli.core, "approve") as approve,
            mock.patch.object(cli.core, "approve_yes") as approve_yes,
            mock.patch.object(cli, "_parsed") as parsed,
        ):
            stderr = io.StringIO()
            code = cli.run(io.StringIO(""), io.StringIO(), stderr, ["--approve", "--yes", "x"])
        self.assertEqual(2, code)
        for called in (approve, approve_yes, parsed):
            called.assert_not_called()

    def test_is_in_neither_the_help_nor_the_flags(self):
        flags = json.loads(run_ccnavi(["--version", "--json"], cwd=ROOT).stdout)["flags"]
        self.assertNotIn("--approve", flags)
        self.assertIn("--agree", flags)
        self.assertIn("--approved", flags)
        text = run_ccnavi(["--version"], cwd=ROOT).stdout
        self.assertNotIn("--approve ", text)
        helped = run_ccnavi(["--help"], cwd=ROOT).stderr
        self.assertNotIn("--approve ", helped)
        self.assertNotIn("--approve\n", helped)

    def test_the_builtin_deny_still_stops_the_agent_from_typing_it(self):
        """エージェントが打つ `--approve` は、組み込みの deny が先に止める（案内には届かない）。"""
        with tempfile.TemporaryDirectory() as root:
            rule = phase.ticket_approval_rule("", root)
        for command in ("ccnavi --approve", "ccnavi --approve i0002", "ccnavi --approve --yes x"):
            with self.subTest(command=command):
                self.assertIsNotNone(rule.compiled.search(command))


if __name__ == "__main__":
    unittest.main()
