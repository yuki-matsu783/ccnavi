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
import tempfile
import unittest

from tests.inproc import run_ccnavi

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
        return run_ccnavi(
            [
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

    def test_file_body_follows_the_text_and_is_cut_at_the_limit(self):
        from ccnavi import ctxfile

        write(os.path.join(self.root, "docs", "guide.md"), "# 決まり\n\nテストは tests/ に置く。\n")
        write(os.path.join(self.root, "docs", "long.md"), "あ" * (ctxfile.MAX_CHARS + 50))
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[
                rule(
                    "src",
                    "Write",
                    "",
                    glob="*/src/*",
                    additionalContext=NOTE,
                    additionalContextFile="docs/guide.md",
                ),
                rule("doc", "Write", "", glob="*/docs/*", additionalContextFile="docs/long.md"),
                rule("none", "Write", "", glob="*/etc/*", additionalContextFile="docs/none.md"),
            ],
        )
        got = self.judge(path, "Write", os.path.join(self.root, "src", "a.py"))
        self.assertEqual(
            got.get("additionalContext"), f"{NOTE}\n\n# 決まり\n\nテストは tests/ に置く。"
        )
        cut = self.judge(path, "Write", os.path.join(self.root, "docs", "x.md"))[
            "additionalContext"
        ]
        self.assertTrue(cut.startswith("あ" * ctxfile.MAX_CHARS + "\n\n(ccnavi: docs/long.md は"))
        self.assertNotIn("あ" * (ctxfile.MAX_CHARS + 1), cut)
        self.assertIn("続きはこのファイルを読む", cut)
        # 無いファイルは何も足さない。
        self.assertEqual(self.judge(path, "Write", os.path.join(self.root, "etc", "a")), {})
        # 試験にも本文が出る。
        shown = self.run_ccnavi(path, "--test", "Write", os.path.join(self.root, "src", "a.py"))
        self.assertIn("テストは tests/ に置く。", shown.stdout)

    def test_file_in_the_worktree_wins_over_the_root(self):
        write(os.path.join(self.root, "docs", "guide.md"), "ルートの案内")
        wt = os.path.join(self.root, ".claude", "worktrees", "feat")
        write(os.path.join(wt, "docs", "guide.md"), "作業ツリーの案内")
        # 本物の作業ツリーと見なされるには、.git ファイルと登録簿の相互参照が要る。
        gitdir = os.path.join(self.root, ".git", "worktrees", "feat")
        write(os.path.join(wt, ".git"), f"gitdir: {gitdir}\n")
        write(os.path.join(gitdir, "gitdir"), os.path.join(wt, ".git") + "\n")
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[rule("src", "Write", "", glob="*/src/*", additionalContextFile="docs/guide.md")],
        )
        inside = self.judge(path, "Write", os.path.join(wt, "src", "a.py"))
        self.assertEqual(inside.get("additionalContext"), "作業ツリーの案内")
        outside = self.judge(path, "Write", os.path.join(self.root, "src", "a.py"))
        self.assertEqual(outside.get("additionalContext"), "ルートの案内")

    def test_once_file_is_delivered_once_and_lint_checks_the_path(self):
        from ccnavi import ctxfile

        state = os.path.join(self.root, "state")
        write(os.path.join(self.root, "docs", "once.md"), "最初に 1 度だけ")
        write(os.path.join(self.root, "docs", "long.md"), "い" * (ctxfile.MAX_CHARS + 1))
        path = self.rules(
            deny=[rule("push", "Bash", glob="*git push*")],
            allow=[
                rule("src", "Write", "", glob="*/src/*", additionalContextOnceFile="docs/once.md"),
                rule("gone", "Write", "", glob="*/a/*", additionalContextFile="docs/gone.md"),
                rule("long", "Write", "", glob="*/b/*", additionalContextFile="docs/long.md"),
                rule("up", "Write", "", glob="*/c/*", additionalContextFile="../secret.md"),
                rule("abs", "Write", "", glob="*/d/*", additionalContextOnceFile="/etc/passwd"),
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
        first = self.run_ccnavi(path, "--mode", "enable", "--state", state, payload=payload)
        self.assertIn("最初に 1 度だけ", first.stdout)
        second = self.run_ccnavi(path, "--mode", "enable", "--state", state, payload=payload)
        self.assertEqual(second.stdout.strip(), "")

        lint = self.run_ccnavi(path, "--lint")
        self.assertIn("warn: gone: additionalContextFile の docs/gone.md が無い", lint.stdout)
        self.assertIn(
            f"warn: long: additionalContextFile の docs/long.md は {ctxfile.MAX_CHARS} 文字",
            lint.stdout,
        )
        self.assertIn("error: up: additionalContextFile の ../secret.md: `..`", lint.stdout)
        self.assertIn("error: abs: additionalContextOnceFile の /etc/passwd: 絶対パス", lint.stdout)
        self.assertNotIn("src:", lint.stdout)
        # 外を指す欄は実行時も読まない。
        self.assertEqual(self.judge(path, "Write", os.path.join(self.root, "c", "a")), {})


if __name__ == "__main__":
    unittest.main()
