"""ccnavi 自身の設定ファイルを守る面の受入テスト。

道具を外から動かす。本物の git リポジトリを一時ディレクトリに作り、実行前の
payload で控えを取らせ、設定ファイルを壊してから実行後の payload を渡し、
ファイルが実際にどうなったかを読む。

ここで確かめたいのは 1 つに尽きる。ルールファイルを壊す道と、壊れたことに
気づく道が、同じファイルに乗っていないこと。ルール由来の保護は、ルールを
空にされると保護領域ごと消える。この面はそこを埋めるために在る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES = {
    "version": 3,
    "deny": [
        {
            "id": "push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "git push is not run by the agent.",
        }
    ],
    "allow": [{"id": "anything-else", "match": "Bash|Read|Write|Edit|MultiEdit", "regex": "."}],
}

SETTINGS = {"hooks": {"PreToolUse": [], "PostToolUse": []}, "env": {"CCNAVI_MODE": "enable"}}


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SelfGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-selfguard-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

        git(self.repo, "init", "--quiet")
        self.settings = os.path.join(self.repo, ".claude", "settings.json")
        self.rules = os.path.join(self.repo, ".claude", "ccnavi", "rules.yml")
        write(self.settings, json.dumps(SETTINGS, indent=2) + "\n")
        write(self.rules, json.dumps(RULES))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "init")

        self.state = os.path.join(self.repo, "state")
        self.log = os.path.join(self.repo, "log.jsonl")

    def run_hook(self, event, mode="enable", setting="enable", session="s1", **tool_input):
        payload = json.dumps(
            {
                "hook_event_name": event,
                "tool_name": "Bash",
                "tool_input": tool_input or {"command": "ls"},
                "session_id": session,
            }
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "ccnavi",
                "--root",
                self.repo,
                "--rules",
                self.rules,
                "--state",
                self.state,
                "--log",
                self.log,
                "--mode",
                mode,
                "--restore-setting-files",
                setting,
                # ルール由来の保護は切っておく。両方が同じファイルについて
                # 別々に口を出すと、どちらが戻したのかがテストから見えない。
                "--restore-if-deny",
                "disable",
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )

    def records(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # 控えを取って戻す

    def test_書き換えられたルールファイルは直前の内容に戻る(self):
        self.run_hook("PreToolUse")
        write(self.rules, json.dumps({"version": 3, "deny": []}))

        result = self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), RULES)
        self.assertIn("rules.yml", result.stdout)
        self.assertIn("restored", result.stdout)

    def test_ルールを空にされても保護は消えない(self):
        # ルール由来の保護は、保護領域をルールファイルから導く。deny を空に
        # されるとその一覧ごと消えるので、実行後の監視は何も検知しない。
        # この面はルールを読まずに対象を決めるので、そこで止まらない。
        self.run_hook("PreToolUse")
        write(self.rules, json.dumps({"version": 3, "deny": [], "ask": [], "allow": []}))
        write(self.settings, "{}\n")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), RULES)
        self.assertEqual(json.loads(read(self.settings)), SETTINGS)

    def test_hook_の登録を消された設定ファイルも戻る(self):
        self.run_hook("PreToolUse")
        broken = dict(SETTINGS)
        broken["hooks"] = {"PreToolUse": []}
        write(self.settings, json.dumps(broken))

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.settings))["hooks"], SETTINGS["hooks"])

    def test_直前の断面に戻すのでコミットしていない編集は残る(self):
        # git から戻すとコミット済みの内容まで巻き戻り、人の書きかけが消える。
        # 控えから戻せば、戻る先はこのツール呼び出しの直前になる。
        edited = json.dumps(
            RULES
            | {"version": 3, "ask": [{"id": "x", "match": "Bash", "regex": "y", "message": "z"}]}
        )
        write(self.rules, edited)
        self.run_hook("PreToolUse")
        write(self.rules, "{}")

        self.run_hook("PostToolUse")

        self.assertEqual(json.loads(read(self.rules)), json.loads(edited))

    # 消されたとき

    def test_消された設定ファイルは実行前に戻る(self):
        self.run_hook("PreToolUse")
        os.remove(self.settings)

        result = self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(self.settings))
        self.assertEqual(json.loads(read(self.settings)), SETTINGS)
        self.assertIn("settings.json", result.stdout)

    def test_控えが無ければ_git_から戻る(self):
        # 実行前を通らずに消された場合。控えの置き場ごと消えた形も同じ。
        os.remove(self.rules)

        self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(self.rules))
        self.assertEqual(json.loads(read(self.rules)), RULES)

    # 置いていないファイル

    def test_置いていない設定ファイルについては何も言わない(self):
        # settings.local.json は置かないのが普通。無いことを毎回報告すると、
        # 呼び出しのたびに 1 行増えて、本当に言うべき 1 行が埋もれる。
        result = self.run_hook("PreToolUse")

        self.assertNotIn("settings.local.json", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_置いていない設定ファイルが現れたら言うが消さない(self):
        self.run_hook("PreToolUse")
        local = os.path.join(self.repo, ".claude", "settings.local.json")
        write(local, '{"hooks": {}}\n')

        result = self.run_hook("PostToolUse")

        self.assertTrue(os.path.exists(local), "人が置くこともあるファイルを消さない")
        self.assertIn("settings.local.json", result.stdout)

    # 戻す前に止める

    def test_ルールに書かなくてもシェルからの書き込みは止まる(self):
        # RULES にこの場所を守るルールは 1 件も無い。それでも止まるのが要点で、
        # 止める側もルールファイルの外に置いてあることを確かめている。
        result = self.run_hook("PreToolUse", command="echo x > .claude/ccnavi/rules.yml")

        self.assertIn("deny", result.stdout)
        self.assertIn("builtin-guard-setting-files", result.stdout)

    def test_設定ファイルを読むだけなら通る(self):
        # 場所の名前が出たかどうかでは止めない。ここは読むほうが普通の場所で、
        # 名前で止めると、いちばんガードを直したいときにいちばん強く効く。
        result = self.run_hook("PreToolUse", command="cat .claude/ccnavi/rules.yml")

        self.assertNotIn("deny", result.stdout)

    def test_disable_なら止める側も足さない(self):
        result = self.run_hook(
            "PreToolUse", setting="disable", command="echo x > .claude/ccnavi/rules.yml"
        )

        self.assertNotIn("builtin-guard-setting-files", result.stdout)

    # 設定で切る

    def test_disable_は控えも取らず戻しもしない(self):
        self.run_hook("PreToolUse", setting="disable")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", setting="disable")

        self.assertEqual(read(self.rules), "{}")
        self.assertEqual(result.stdout, "")

    def test_dry_run_は戻さず戻すはずだったと言う(self):
        self.run_hook("PreToolUse", setting="dry-run")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", setting="dry-run")

        self.assertEqual(read(self.rules), "{}", "言うだけで触らない")
        self.assertIn("would-restore", result.stdout)

    def test_モードが_dry_run_なら設定が_enable_でも触らない(self):
        # dry-run は「呼び出しにも作業ツリーにも手を出さない」が約束。
        # 守る側だけがその外に出ると、試している最中に誰も頼んでいない
        # ファイル操作が起きる。
        self.run_hook("PreToolUse", mode="dry-run")
        write(self.rules, "{}")

        result = self.run_hook("PostToolUse", mode="dry-run")

        self.assertEqual(read(self.rules), "{}")
        self.assertIn("would-restore", result.stdout)

    # 記録

    def test_記録に何をしたかが残る(self):
        self.run_hook("PreToolUse")
        write(self.rules, "{}")
        self.run_hook("PostToolUse")

        line = self.records()[-1]
        self.assertIn("guarded", line)
        self.assertIn("rules:restored", line["guarded"])


if __name__ == "__main__":
    unittest.main()
