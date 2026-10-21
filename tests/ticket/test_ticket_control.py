"""チケット制御の切り替えと、セッション開始で渡す作業の進め方の受入テスト。

見るのは 3 つ。

1. チケット制御が有効なセッションの頭で、直接作業とチケット作業の使い分けが届く
2. `CCNAVI_TICKET_CONTROL=disable` なら届かない（チケットに依らない作業の決まりは届く）
3. 入口の sh のパスと、dry-run の注記。頭では言わないもの（レビューの sh、設定ファイルの
   パス）が載っていないこと
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path, put_ccnavi_md
from tests.inproc import run_ccnavi

# 不備の無いルールのファイル。ルールの中身はここの主題ではない。
RULES = {
    "version": 1,
    "deny": [
        {
            "id": "git-push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "git push is not run by the agent. Ask the user to push.",
        }
    ],
    "allow": [{"id": "anything", "match": "Read", "regex": "."}],
}


def rules_file(root: str) -> str:
    path = common_path(root, "rules")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(RULES, f, indent=2)
    return path


class TicketControlTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.rules = rules_file(self.root)
        put_ccnavi_md(self.root)

    def start(self, *extra, env=None):
        payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1"})
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.update(env or {})
        return run_ccnavi(
            ["--root", self.root, "--rules", self.rules, *extra],
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
        # レビューの sh はフェーズの終わりと ready の手順で、設定ファイルのパスは承認の
        # ときの検査で名指しされる。頭で渡す文はそのぶん短くしてある。
        text = self.context(self.start("--mode", "enable"))

        self.assertNotIn("ccnavi-review.sh", text)
        self.assertNotIn("phases.yml", text)
        self.assertNotIn("risks.yml", text)

    # disable でも、ccnavi が前提にしている作業の決まり（ワークツリー・git の入口など）は
    # チケットに依らないので届く（tests/core/test_session_conventions.py）。ここでは
    # チケットに関わる文が 1 つも載らないことを見る。
    def assert_no_ticket_text(self, text: str) -> None:
        self.assertNotIn("チケット制御を使っている", text)
        self.assertNotIn("直接作業", text)
        self.assertNotIn("ccnavi-ticket.sh", text)

    def test_disableならチケットの文は届かない(self):
        text = self.context(self.start("--mode", "enable", "--ticket-control", "disable"))

        self.assert_no_ticket_text(text)

    def test_環境変数でもdisableにできる(self):
        text = self.context(
            self.start("--mode", "enable", env={"CCNAVI_TICKET_CONTROL": "disable"})
        )

        self.assert_no_ticket_text(text)

    def test_読めない値はenableとして届く(self):
        result = self.start("--mode", "enable", env={"CCNAVI_TICKET_CONTROL": "off"})

        self.assertIn("直接作業", self.context(result))
        self.assertIn("CCNAVI_TICKET_CONTROL='off' は設定ではありません", result.stderr)

    def test_dry_runなら末尾にその旨と次からの従い方が付く(self):
        text = self.context(self.start("--mode", "dry-run"))

        self.assertIn("直接作業", text)
        last = text.splitlines()[-1]
        self.assertTrue(last.startswith("（現状: CCNAVI_MODE=dry-run"), text)
        # 止まらないことだけで終えない。通ったことを許可と読ませない。
        self.assertIn("許可と読まず", last)
        self.assertIn("次からは従う", last)
