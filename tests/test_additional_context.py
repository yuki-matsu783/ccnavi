"""ルールの `additionalContext` の受入テスト。道具を外から叩いて応答だけを見る。

見るのは 5 つ。

1. allow に当たったルールの文が `additionalContext` でモデルに渡る
2. deny と ask では、理由（`permissionDecisionReason`）と一緒に `additionalContext` が付く。
   両方が届くことは Claude Code 2.1 で実測した
3. dry-run でも文は届く（判定の代わりの文に続く）
4. 書いていないルールでは鍵ごと出ない
5. `--lint` は広い allow（何にでも当たる、選択肢が 3 つ以上）に書いた文を warn にし、
   狭い allow には何も言わない。`--test` は理由と文の両方を見せる
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOTE = "作業ツリーの中だけで直すこと。main には触らない。"


def rule(name: str, match: str, message: str = "文面", **extra) -> dict:
    return {"id": name, "match": match, "message": message, **extra}


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class AdditionalContextTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.addCleanup(self.dir.cleanup)

    def rules(self, **sections) -> str:
        body = {"version": 3, **sections}
        return write(os.path.join(self.root, "rules.yml"), json.dumps(body))

    def run_ccnavi(
        self, rules_path: str, *args: str, payload: str = ""
    ) -> subprocess.CompletedProcess:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "ccnavi",
                "--root",
                self.root,
                "--rules",
                rules_path,
                "--log",
                "",
                "--state",
                "",
                "--approved",
                "",
                *args,
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )

    def judge(self, rules_path: str, tool: str, subject: str, mode: str = "enable") -> dict:
        field = "command" if tool == "Bash" else "file_path"
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": {field: subject},
            }
        )
        done = self.run_ccnavi(rules_path, "--mode", mode, payload=payload)
        if not done.stdout.strip():
            return {}
        try:
            return json.loads(done.stdout)["hookSpecificOutput"]
        except (ValueError, KeyError) as exc:
            self.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {done.stdout!r}")

    def test_allow_carries_the_text_as_additional_context(self):
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("src", "Write", "", glob="*/src/*", additionalContext=NOTE)],
        )
        out = self.judge(path, "Write", os.path.join(self.root, "src", "a.py"))
        self.assertNotIn("permissionDecision", out)
        self.assertEqual(out.get("additionalContext"), NOTE)

    def test_deny_and_ask_carry_reason_and_context_together(self):
        path = self.rules(
            deny=[
                rule("push", "Bash", "push は人が行う", glob="*git push*", additionalContext=NOTE)
            ],
            ask=[
                rule(
                    "mig", "Write", "移行は人が見る", glob="*/migrations/*", additionalContext=NOTE
                )
            ],
            allow=[rule("src", "Write", "", glob="*/src/*")],
        )
        denied = self.judge(path, "Bash", "git push origin main")
        self.assertEqual(denied.get("permissionDecision"), "deny")
        self.assertIn("push は人が行う", denied["permissionDecisionReason"])
        self.assertEqual(denied.get("additionalContext"), NOTE)

        asked = self.judge(path, "Write", os.path.join(self.root, "migrations", "001.sql"))
        self.assertEqual(asked.get("permissionDecision"), "ask")
        self.assertIn("移行は人が見る", asked["permissionDecisionReason"])
        self.assertEqual(asked.get("additionalContext"), NOTE)

    def test_dry_run_still_delivers_the_text(self):
        path = self.rules(
            deny=[
                rule("push", "Bash", "push は人が行う", glob="*git push*", additionalContext=NOTE)
            ],
            allow=[rule("src", "Write", "", glob="*/src/*")],
        )
        out = self.judge(path, "Bash", "git push origin main", mode="dry-run")
        self.assertNotIn("permissionDecision", out)
        self.assertIn("would have stopped", out["additionalContext"])
        self.assertIn(NOTE, out["additionalContext"])

    def test_rules_without_the_field_add_nothing(self):
        path = self.rules(
            deny=[rule("push", "Bash", "push は人が行う", glob="*git push*")],
            allow=[rule("src", "Write", "", glob="*/src/*")],
        )
        self.assertNotIn("additionalContext", self.judge(path, "Bash", "git push"))
        self.assertEqual(self.judge(path, "Write", os.path.join(self.root, "src", "a.py")), {})

    def test_lint_warns_on_broad_allow_only(self):
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[
                rule("everything", "Read", "", glob="*", additionalContext=NOTE),
                rule("many", "Bash", "", regex=r"\b(ls|cat|sed)\b", additionalContext=NOTE),
                rule("two", "Bash", "", regex=r"\b(ls|cat)\b", additionalContext=NOTE),
                rule("narrow", "Write", "", glob="*/src/*", additionalContext=NOTE),
                rule("class", "Bash", "", regex=r"^[a|b|c]x\b", additionalContext=NOTE),
            ],
        )
        done = self.run_ccnavi(path, "--lint", "--mode", "enable")
        self.assertEqual(done.returncode, 0, done.stdout)
        warned = [line for line in done.stdout.splitlines() if "additionalContext" in line]
        self.assertEqual(len(warned), 2, done.stdout)
        self.assertTrue(any("everything" in w and "何にでも当たる" in w for w in warned), warned)
        self.assertTrue(any("many" in w and "選択肢が 3 つ以上" in w for w in warned), warned)

    def test_once_delivers_the_text_only_the_first_time_per_context(self):
        state = os.path.join(self.root, "state")
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("src", "Write", "", glob="*/src/*", additionalContextOnce=NOTE)],
        )
        target = os.path.join(self.root, "src", "a.py")

        def hit(session: str, agent: str = "", event: str = "PreToolUse") -> dict:
            payload = json.dumps(
                {
                    "hook_event_name": event,
                    "tool_name": "Write",
                    "session_id": session,
                    "agent_id": agent,
                    "cwd": self.root,
                    "tool_input": {"file_path": target},
                }
            )
            done = self.run_ccnavi(path, "--mode", "enable", "--state", state, payload=payload)
            self.assertEqual(done.returncode, 0, done.stderr)
            if not done.stdout.strip():
                return {}
            return json.loads(done.stdout)["hookSpecificOutput"]

        # 1 回目は届き、2 回目は届かない。
        self.assertEqual(hit("s1").get("additionalContext"), NOTE)
        self.assertEqual(hit("s1"), {})
        # 別のセッションと、同じセッションのサブエージェントは別の文脈。
        self.assertEqual(hit("s2").get("additionalContext"), NOTE)
        self.assertEqual(hit("s1", agent="a1").get("additionalContext"), NOTE)
        self.assertEqual(hit("s1", agent="a1"), {})
        # セッションの開始（compact の後も含む）で忘れる。
        hit("s1", event="SessionStart")
        self.assertEqual(hit("s1").get("additionalContext"), NOTE)
        # 控えの置き場が無ければ毎回届く。
        self.assertEqual(self.judge(path, "Write", target).get("additionalContext"), NOTE)
        self.assertEqual(self.judge(path, "Write", target).get("additionalContext"), NOTE)
        # 試験は控えを消費しない。
        self.run_ccnavi(path, "--state", state, "--test", "Write", target)
        self.assertEqual(hit("s3").get("additionalContext"), NOTE)

    def test_both_texts_join_the_first_time_and_only_the_short_one_after(self):
        state = os.path.join(self.root, "state")
        always = "src は自由に直してよい。"
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[
                rule(
                    "src",
                    "Write",
                    "",
                    glob="*/src/*",
                    additionalContext=always,
                    additionalContextOnce=NOTE,
                )
            ],
        )
        target = os.path.join(self.root, "src", "a.py")
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "session_id": "s1",
                "cwd": self.root,
                "tool_input": {"file_path": target},
            }
        )

        def hit() -> str:
            done = self.run_ccnavi(path, "--mode", "enable", "--state", state, payload=payload)
            return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]

        self.assertEqual(hit(), f"{always}\n\n{NOTE}")
        self.assertEqual(hit(), always)
        self.assertEqual(hit(), always)

    def test_test_shows_reason_and_context(self):
        path = self.rules(
            deny=[
                rule("push", "Bash", "push は人が行う", glob="*git push*", additionalContext=NOTE)
            ],
            allow=[rule("src", "Write", "", glob="*/src/*", additionalContext=NOTE)],
        )
        done = self.run_ccnavi(path, "--test", "Bash", "git push", "--json")
        body = json.loads(done.stdout)
        self.assertIn("push は人が行う", body["response"])
        self.assertIn(NOTE, body["response"])
        allowed = self.run_ccnavi(path, "--test", "Write", os.path.join(self.root, "src", "a.py"))
        self.assertIn("verdict: allow", allowed.stdout)
        self.assertIn(NOTE, allowed.stdout)


if __name__ == "__main__":
    unittest.main()
