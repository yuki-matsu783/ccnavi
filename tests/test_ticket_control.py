"""チケット制御の切り替えと、セッション開始で渡す作業の進め方の受入テスト。

見るのは 3 つ。

1. チケット制御が効いているセッションの頭で、直接作業とチケット作業の使い分けが届く
2. `CCNAVI_TICKET_CONTROL=disable` なら届かない
3. 文に載る設定ファイルの綴りと、dry-run の注記
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests.inproc import run_ccnavi
from tests.test_lint import ROOT, SOUND, rules_file, write


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
        self.assertIn(".ccnavi/proposals/", text)
        self.assertIn("ccnavi-ticket.sh", text)
        self.assertNotIn("dry-run", text)

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

    def test_dry_runなら末尾にその旨が付く(self):
        text = self.context(self.start("--mode", "dry-run"))

        self.assertIn("直接作業", text)
        self.assertTrue(text.splitlines()[-1].startswith("（現状: CCNAVI_MODE=dry-run"), text)

    def test_設定ファイルは在るときだけ相対の綴りで載る(self):
        without = self.context(self.start("--mode", "enable"))
        self.assertIn("フェーズと", without)
        self.assertIn("リスクの配点に", without)

        write(self.root, os.path.join(".claude", "ccnavi", "phases.yml"), "phases: []\n")
        write(self.root, os.path.join(".claude", "ccnavi", "risk.yml"), "levels: {}\n")
        with_files = self.context(self.start("--mode", "enable"))
        self.assertIn("フェーズ（.claude/ccnavi/phases.yml）", with_files)
        self.assertIn("リスクの配点（.claude/ccnavi/risk.yml）", with_files)
