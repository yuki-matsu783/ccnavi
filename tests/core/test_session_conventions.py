"""セッション開始で渡す、ccnavi が前提にしている作業の決まりの受入テスト（REQ-SES-06）。

見るのは 5 つ。

1. 起動のたびに、ワークツリー・git の入口・下書きの置き場・サブエージェント・合意の要る変更が
   1 行ずつ届く。git の入口は打てる絶対パスで、詳しいことは --help と拒否の案内に聞く形
2. チケット制御が有効なときだけ、承認済みチケットの状態の確かめ方（`status`）が載る
3. プロジェクトの置き場にプロジェクトがあるときだけ、cd してから作業する行が載る
4. md の索引の案内が出る回だけ、詳しい決まりを --docs で引く行が載る
5. サブエージェントの起動（agent_id がある）には出さない。文は短い
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.core.test_lint import SOUND, rules_file
from tests.inproc import run_ccnavi

HEAD = "[ccnavi] ccnavi が前提にしている作業の決まり。"


def block(text: str) -> str:
    """additionalContext から作業の決まりの段落だけを抜く。無ければ空。"""
    for part in text.split("\n\n"):
        if part.startswith(HEAD):
            return part
    return ""


class SessionConventionsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        self.rules = rules_file(self.root, SOUND)

    def start(self, *extra, **payload_extra) -> str:
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", **payload_extra}
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.root, "--rules", self.rules, "--state", "", "--log", "", *extra],
            input=json.dumps(payload),
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_作業の決まりが1行ずつ届く(self):
        text = block(self.start("--mode", "enable"))

        self.assertIn(".claude/worktrees/<名前>", text)
        self.assertIn("--help（worktree add）", text)
        git_sh = f"sh {self.root}/.ccnavi/scripts/ccnavi-git.sh"
        self.assertIn(git_sh, text)
        self.assertIn("迂回せず", text)
        self.assertIn("scratchpad/", text)
        self.assertIn("サブエージェントにバックグラウンドで任せる", text)
        self.assertIn("実装する前にユーザと合意する", text)

    def test_チケット制御が有効ならstatusの確かめ方が載る(self):
        text = block(self.start("--mode", "enable"))

        self.assertIn(f"sh {self.root}/.ccnavi/scripts/ccnavi-ticket.sh status [<親>]", text)

    def test_チケット制御がdisableでも決まりは届きstatusは載らない(self):
        text = block(self.start("--mode", "enable", "--ticket-control", "disable"))

        self.assertIn("ccnavi-git.sh", text)
        self.assertNotIn("ccnavi-ticket.sh", text)

    def test_プロジェクトがあるときだけcdの行が載る(self):
        self.assertNotIn("cd してから", block(self.start("--mode", "enable")))

        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        text = block(self.start("--mode", "enable"))

        self.assertIn("projects/<名前>/ を直すときは、そこへ cd してから作業する", text)

    def test_索引の案内が出る回だけdocsの行が載る(self):
        self.assertNotIn("--docs", block(self.start("--mode", "enable")))

        subprocess.run(["git", "init", "-q", self.root], check=True)
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("**/index.jsonl\n")
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: guide\n---\n# x\n")
        text = self.start("--mode", "enable")

        self.assertIn("--docs で、--keyword <語> を付けて引いてください", block(text))
        # 「下の案内」が指す索引の案内は、決まりより後ろにある。
        self.assertLess(text.index(HEAD), text.index("[ccnavi] ドキュメント（*.md）"))

    def test_dry_runの注記は進め方の最後の行のまま(self):
        text = self.start("--mode", "dry-run")

        self.assertTrue(text.splitlines()[-1].startswith("（現状: CCNAVI_MODE=dry-run"), text)
        self.assertLess(text.index(HEAD), text.index("チケット制御を使っている"))

    def test_サブエージェントには出さない(self):
        self.assertEqual(block(self.start("--mode", "enable", agent_id="a1")), "")

    def test_常駐の文脈なので短い(self):
        # 毎回の起動・再開・compact に届く。項目ごとに 1 行、全体で数百字に収める。
        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        text = block(self.start("--mode", "enable"))
        body = text.replace(self.root, "<root>")

        self.assertLessEqual(len(text.splitlines()), 9, text)
        self.assertLess(len(body), 700, body)


if __name__ == "__main__":
    unittest.main()
