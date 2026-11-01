"""ユーザへの確認（ask）の前の発言を、hook の入口から確かめる（`hook/askcontext.py`）。

見るのは次の点。

- 見出しの判定（`前提・背景`・`想定結果`と別名。本文中の語や別の語は当たらない）
- `AskUserQuestion` は、直前の発言に 2 つの節が無ければ止まり、揃えば通る
- 発言は、最後のユーザの行（プロンプトかツールの結果）より後ろだけを見る。サイドチェーンは見ない
- ccnavi が確認に出す呼び出し（ターン分離の関門の確認・ルールの `ask`）も、節が無ければ確認でなく止まる
- 会話記録に発言が遅れて書かれても、待てば通る
- 記録が読めない・パスが無い、非対話、サブエージェントの呼び出しは検査しない
- dry-run では止めず、返していたはずだと言う
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from ccnavi.hook import askcontext
from tests import ROOT
from tests.inproc import run_ccnavi

BOTH = "## 前提・背景\nファイルを消す必要がある。\n## 想定結果\n許可すると消える。断ると残る。"
GIT = "sh .ccnavi/scripts/ccnavi-git.sh"
DONE = f"{GIT} integrate-done foo -m 'msg x'"


def assistant(text, **extra):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, **extra}


def prompt(text="お願いします"):
    return {"type": "user", "message": {"content": text}}


def tool_result():
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}


class HeadingTest(unittest.TestCase):
    def test_見出しの書き方は何通りかに当たる(self):
        for text in ("## 背景", "**背景**", "【前提・背景】", "前提:", "- 背景：理由", "  背景"):
            with self.subTest(text=text):
                self.assertNotIn("前提・背景", askcontext.absent_sections(text + "\n想定結果:"))

    def test_結果の節は_2_つの言い方に当たる(self):
        for text in ("## 想定結果", "選んだ時の結果:"):
            with self.subTest(text=text):
                self.assertNotIn("想定結果", askcontext.absent_sections("背景:\n" + text))

    def test_本文の中の語と別の見出しは当たらない(self):
        for text in (
            "背景を説明すると、こうです",
            "この背景は大きい",
            "## 結果",
            "選んだ結果",
            "前提を置くと",
        ):
            with self.subTest(text=text):
                self.assertEqual(askcontext.absent_sections(text), list(askcontext.SECTIONS))

    def test_無い節だけを返す(self):
        self.assertEqual(askcontext.absent_sections("## 背景\nx"), ["想定結果"])


class AskContextHookTest(unittest.TestCase):
    attended = "1"

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-ask-context-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        os.makedirs(os.path.join(self.repo, ".ccnavi", "config", "common"))
        self.transcript = os.path.join(self.repo, "transcript.jsonl")
        self.calls = 0

    def write(self, *rows):
        with open(self.transcript, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def hook(self, tool="AskUserQuestion", mode="enable", transcript=True, **body):
        self.calls += 1
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": tool,
            "tool_use_id": f"toolu_{self.calls}",
            "tool_input": {"questions": [{"question": "消してよいですか", "options": []}]},
        }
        if transcript:
            payload["transcript_path"] = self.transcript
        payload.update(body)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_LEARNING"] = "disable"
        environment["CCNAVI_MODE"] = mode
        environment["CCNAVI_ASK_CONTEXT_WAIT_SECONDS"] = "0.3"
        environment.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
        if self.attended is not None:
            environment["CLAUDE_CODE_SESSION_ATTENDED"] = self.attended
        return run_ccnavi(
            ["--root", self.repo], input=json.dumps(payload), cwd=ROOT, env=environment
        )

    def stopped(self, done):
        return askcontext.CODE in done.stdout and '"permissionDecision": "deny"' in done.stdout


class AskUserQuestionTest(AskContextHookTest):
    def test_節が無ければ止まる(self):
        self.write(prompt(), assistant("消してよいですか？"))

        done = self.hook()

        self.assertTrue(self.stopped(done))
        self.assertIn(askcontext.RULE_ID, done.stdout)
        self.assertIn("「前提・背景」・「想定結果」", done.stdout)
        self.assertIn("消してよいですか", done.stdout)

    def test_片方の節が無ければ無いほうだけを言う(self):
        self.write(prompt(), assistant("## 背景\n理由"))

        done = self.hook()

        self.assertTrue(self.stopped(done))
        self.assertIn("「想定結果」の節", done.stdout)
        self.assertNotIn("「前提・背景」の節", done.stdout)

    def test_節が揃えば通る(self):
        self.write(prompt(), assistant(BOTH))

        done = self.hook()

        self.assertFalse(self.stopped(done))
        self.assertNotIn("deny", done.stdout)

    def test_節が_2_つの発言に分かれていても揃えば通る(self):
        self.write(prompt(), assistant("## 背景\n理由"), assistant("## 選んだ時の結果\n消える"))

        self.assertFalse(self.stopped(self.hook()))

    def test_ツールの結果より前の発言は数えない(self):
        self.write(prompt(), assistant(BOTH), tool_result(), assistant("消してよいですか？"))

        self.assertTrue(self.stopped(self.hook()))

    def test_ユーザの発言より前の発言は数えない(self):
        self.write(assistant(BOTH), prompt("どうぞ"))

        self.assertTrue(self.stopped(self.hook()))

    def test_サイドチェーンの発言は数えない(self):
        self.write(prompt(), assistant(BOTH, isSidechain=True))

        self.assertTrue(self.stopped(self.hook()))

    def test_止めた記録は拒否コードと_rules_を残す(self):
        self.write(prompt(), assistant("消してよいですか？"))
        self.hook()

        with open(os.path.join(self.repo, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            record = [json.loads(line) for line in f if line.strip()][-1]

        self.assertEqual(record["code"], askcontext.CODE)
        self.assertEqual(record["rules"], [askcontext.RULE_ID])
        self.assertEqual(record["decision"], "deny")

    def test_発言が遅れて書かれても待てば通る(self):
        self.write(prompt())

        def late():
            time.sleep(0.15)
            with open(self.transcript, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(assistant(BOTH), ensure_ascii=False) + "\n")

        thread = threading.Thread(target=late)
        thread.start()
        done = self.hook()
        thread.join()

        self.assertFalse(self.stopped(done))

    def test_dry_run_では止めず_返していたはずだと言う(self):
        self.write(prompt(), assistant("消してよいですか？"))

        done = self.hook(mode="dry-run")

        self.assertIn("dry-run", done.stdout)
        self.assertIn(askcontext.CODE, done.stdout)
        self.assertNotIn('"permissionDecision": "deny"', done.stdout)


class NotCheckedTest(AskContextHookTest):
    def test_記録のパスが無ければ検査しない(self):
        self.assertFalse(self.stopped(self.hook(transcript=False)))

    def test_記録が読めなければ検査しない(self):
        self.assertFalse(self.stopped(self.hook()))

    def test_サブエージェントの呼び出しは検査しない(self):
        self.write(prompt(), assistant("消してよいですか？"))

        self.assertFalse(self.stopped(self.hook(agent_id="a1")))

    def test_ほかのツールは検査しない(self):
        self.write(prompt(), assistant("調べます"))

        # 権限モードを渡す。渡さないと ccnavi が未知のモードとして確認に出し、検査に当たる。
        done = self.hook(tool="Bash", tool_input={"command": "ls"}, permission_mode="default")

        self.assertFalse(self.stopped(done))


class UnattendedTest(AskContextHookTest):
    attended = "0"

    def test_非対話は検査しない(self):
        self.write(prompt(), assistant("消してよいですか？"))

        self.assertFalse(self.stopped(self.hook()))


class GateConfirmTest(AskContextHookTest):
    """ccnavi が確認に出す呼び出し（ターン分離の関門の確認）。"""

    def run_done(self, **kw):
        return self.hook(tool="Bash", tool_input={"command": DONE}, **kw)

    def test_節が無ければ確認でなく止まる(self):
        self.write(prompt(), assistant("取り込みます"))

        done = self.run_done()

        self.assertTrue(self.stopped(done))
        self.assertNotIn('"permissionDecision": "ask"', done.stdout)

    def test_節が揃えば確認に出る(self):
        self.write(prompt(), assistant(BOTH))

        done = self.run_done()

        self.assertIn('"permissionDecision": "ask"', done.stdout)
        self.assertFalse(self.stopped(done))


RULES = os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml")


class RuleAskTest(AskContextHookTest):
    """ルールの `ask`（運用の rules.yml の direct-worktree-confirm）が返す確認。"""

    def run_adopt(self, **kw):
        shutil.copy(RULES, os.path.join(self.repo, ".ccnavi", "config", "common", "rules.yml"))
        command = "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh feature-1 --base main"
        return self.hook(tool="Bash", tool_input={"command": command}, **kw)

    def test_節が無ければ確認でなく止まる(self):
        self.write(prompt(), assistant("ワークツリーを切ります"))

        done = self.run_adopt()

        self.assertTrue(self.stopped(done))
        self.assertNotIn('"permissionDecision": "ask"', done.stdout)

    def test_節が揃えば確認に出る(self):
        self.write(prompt(), assistant(BOTH))

        done = self.run_adopt()

        self.assertIn('"permissionDecision": "ask"', done.stdout)
        self.assertIn("direct-worktree-confirm", done.stdout)
        self.assertFalse(self.stopped(done))


if __name__ == "__main__":
    unittest.main()
