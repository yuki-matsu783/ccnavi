"""チケット制御の切り替えと、セッション開始で渡す作業の進め方の受入テスト。

見るのは 3 つ。

1. チケット制御が効いているセッションの頭で、直接作業とチケット作業の使い分けが届く
2. `CCNAVI_TICKET_CONTROL=disable` なら届かない
3. 入口の sh の綴りと、dry-run の注記。頭では言わないもの（レビューの sh、設定ファイルの
   綴り）が載っていないこと
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests.core.test_lint import ROOT, SOUND, rules_file
from tests.inproc import run_ccnavi


class TicketControlTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.rules = rules_file(self.root, SOUND)

    def start(self, *extra, env=None):
        payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1"})
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.update(env or {})
        return run_ccnavi(
            ["--root", self.root, "--rules", self.rules, "--state", "", "--log", "", *extra],
            input=payload,
            cwd=ROOT,
            env=environment,
        )

    def context(self, result) -> str:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_セッション開始で直接作業とチケット作業の使い分けが届く(self):
        text = self.context(self.start("--mode", "enable"))

        self.assertIn("チケット制御を使っている", text)
        self.assertIn("直接作業", text)
        self.assertIn("チケット作業", text)
        self.assertIn("wip/proposals/", text)
        self.assertIn("ccnavi-ticket.sh", text)
        self.assertNotIn("dry-run", text)

    def test_後から届くものは頭では言わない(self):
        # レビューの sh はフェーズの段階と ready の手順で、設定ファイルの綴りは承認の
        # ときの検査で名指しされる。頭で渡す文はそのぶん短くしてある。
        text = self.context(self.start("--mode", "enable"))

        self.assertNotIn("ccnavi-review.sh", text)
        self.assertNotIn("phases.yml", text)
        self.assertNotIn("risks.yml", text)

    def test_disableなら何も届かない(self):
        text = self.context(self.start("--mode", "enable", "--ticket-control", "disable"))

        self.assertEqual(text, "")

    def test_環境変数でもdisableにできる(self):
        text = self.context(
            self.start("--mode", "enable", env={"CCNAVI_TICKET_CONTROL": "disable"})
        )

        self.assertEqual(text, "")

    def test_読めない値はenableに倒れて届く(self):
        result = self.start("--mode", "enable", env={"CCNAVI_TICKET_CONTROL": "off"})

        self.assertIn("直接作業", self.context(result))
        self.assertIn("CCNAVI_TICKET_CONTROL='off' is not a setting", result.stderr)

    def test_dry_runなら末尾にその旨と次からの従い方が付く(self):
        text = self.context(self.start("--mode", "dry-run"))

        self.assertIn("直接作業", text)
        last = text.splitlines()[-1]
        self.assertTrue(last.startswith("（現状: CCNAVI_MODE=dry-run"), text)
        # 止まらないことだけで終えない。通ったことを許可と読ませない。
        self.assertIn("許可と読まず", last)
        self.assertIn("次から従う", last)
