"""ルールファイルを読めないときの受入テスト。

別ファイルにしてあるのは、ここが「ガードが落ちたときの振る舞い」という
独立した関心で、壊れたルールファイルを自分で用意する必要があるため。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# JSON として壊れている。末尾のカンマ 1 つ。書き損じの典型。
BROKEN = '{"version": 1, "rules": [{"id": "x", "match": "Bash", "pattern": "y", }]}'


def run(rules_path, payload, log=""):
    """道具を 1 回動かす。ルールファイルの場所を呼び出しごとに変えられる。"""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ccnavi",
            "--rules",
            rules_path,
            "--log",
            log,
            "--mode",
            "block",
        ],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=environment,
    )


def pre_tool_use(tool, field, value):
    return json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {field: value}}
    )


def out_of(case, result):
    try:
        return json.loads(result.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError) as exc:
        case.fail(
            f"標準出力が期待した JSON ではない: {exc}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.broken = os.path.join(self.directory.name, "rules.json")
        with open(self.broken, "w", encoding="utf-8") as f:
            f.write(BROKEN)
        self.missing = os.path.join(self.directory.name, "not-there.json")

    def test_壊れたルールでもセッションは死なない(self):
        # ここが要件の核。拒否側へ倒すと、壊れたファイルを直すための呼び出しまで
        # 止まって回復できなくなる。既定モードが block なので、ルールを置く前に
        # hook を登録しただけでセッションが死ぬ。
        for rules_path in (self.broken, self.missing):
            with self.subTest(rules=os.path.basename(rules_path)):
                result = run(rules_path, pre_tool_use("Bash", "command", "cat README.md"))
                self.assertEqual(result.returncode, 0, f"無害な呼び出しを止めた: {result.stderr!r}")
                self.assertNotIn(
                    "permissionDecision", out_of(self, result), "無害な呼び出しを拒否した"
                )

    def test_既定に落ちたことは通した回にも伝える(self):
        # 黙って落ちると、ガードが立っているように見えて実際は何も見ていない
        # 状態が続く。止まっているより悪い。止まっていれば誰かが気づく。
        result = run(self.broken, pre_tool_use("Bash", "command", "cat README.md"))
        context = out_of(self, result).get("additionalContext", "")

        self.assertIn("built-in defaults", context)
        self.assertIn(self.broken, context, "どのファイルが読めなかったのかを言っていない")

    def test_既定でも取り返しの付かない操作は止まる(self):
        # 落ちた先が素通しでは、壊すだけでガードを外せることになる。
        for command in ["rm -rf /tmp/x", "git push origin main", "git reset --hard HEAD~1"]:
            with self.subTest(command=command):
                out = out_of(self, run(self.broken, pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_既定は設定の修復を妨げない(self):
        # REQ-PRE-06 が「読み取りと設定自身の修復を妨げない」と書いている意味。
        # ここを止めると直す道が 1 本も残らない。
        for tool in ("Read", "Write", "Edit"):
            with self.subTest(tool=tool):
                result = run(
                    self.broken, pre_tool_use(tool, "file_path", ".claude/ccnavi/rules.json")
                )
                self.assertNotIn(
                    "permissionDecision", out_of(self, result), f"{tool} による修復を止めた"
                )

    def test_既定はシェルから設定を書き換えさせない(self):
        # 上と対になっている。ここを開けると、シェルでルールを壊し、壊れた結果
        # 緩んだ既定に落ちる、という順路ができる。壊す側と直す側で経路を分ける。
        out = out_of(
            self,
            run(self.broken, pre_tool_use("Bash", "command", "echo x > .claude/ccnavi/rules.json")),
        )

        self.assertEqual(out.get("permissionDecision"), "deny")
        # 止めた先に道が無いと、拒否は行き止まりになる。
        self.assertIn("Write", out["permissionDecisionReason"])

    def test_既定に落ちたことは記録に残る(self):
        # ガードが落ちたまま何回動いたかは、これでしか数えられない。
        log = os.path.join(self.directory.name, "log.jsonl")
        run(self.broken, pre_tool_use("Bash", "command", "cat README.md"), log=log)
        with open(log, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].get("fallback"), "builtin-rules")
        self.assertEqual(records[0].get("detail"), self.broken)
        # 判定には達しているので skip ではない。
        self.assertEqual(records[0]["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
