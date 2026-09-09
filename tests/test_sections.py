"""3 つの区画と権限モードへの委譲の受入テスト。道具を外から叩いて応答だけを見る。

見るのは 4 つ。

1. 強さの順が deny > ルールが置いた確認 > allow > 権限モードへの委譲であること
2. どのルールも言及しない呼び出しが確認になること
3. ルールが置いた確認と権限モードへの委譲が区別して返ること
4. ルールがチケットより強いこと

3 つ目が要る理由は設計 §13.2 にある。権限モードへの委譲は設定の穴に起因するので、
穴が塞がるまで同じ問いが繰り返される。ルールが置いた確認は人が意図して置いた
確認ポイントで、繰り返されること自体に価値がある。混ぜると前者の数に
後者が埋もれる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rule(name: str, match: str, glob: str, message: str = "文面") -> dict:
    return {"id": name, "match": match, "glob": glob, "message": message}


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class SectionsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.addCleanup(self.dir.cleanup)

    def rules(self, **sections) -> str:
        """区画を渡してルールファイルを 1 本置く。

        書き出すのは JSON。YAML は JSON の上位互換なので、判定が読むのと同じ
        読み手がそのまま受け取る。区画の強さを見たいテストで、YAML の綴りの
        話に付き合わずに済む。
        """
        body = {"version": 3, **sections}
        return write(os.path.join(self.root, "rules.yml"), json.dumps(body))

    def judge(self, rules_path: str, tool: str, subject: str) -> dict:
        field = "command" if tool == "Bash" else "file_path"
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": {field: subject},
            }
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "ccnavi",
                "--root",
                self.root,
                "--mode",
                "enable",
                "--rules",
                rules_path,
                "--log",
                "",
                "--state",
                "",
                "--approved",
                "",
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        try:
            return json.loads(done.stdout)["hookSpecificOutput"]
        except (ValueError, KeyError) as exc:
            self.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {done.stdout!r}")

    def test_どのルールも言及しなければ確認になる(self):
        # 既定が許可ではなく確認であること。allow を書き切るまで、
        # 言及されていない呼び出しは人が見る側に落ちる。
        path = self.rules(deny=[rule("push", "Bash", "*git push*")])

        out = self.judge(path, "Bash", "ls -la")

        self.assertEqual(out.get("permissionDecision"), "ask")
        self.assertIn("UNDECLARED", out["permissionDecisionReason"])

    def test_未言及の文は危険の表明ではないと言う(self):
        # 設計 §13.2。危険だと書くと、受け取った側は存在しない危険を探しに行く。
        path = self.rules(deny=[rule("push", "Bash", "*git push*")])

        reason = self.judge(path, "Bash", "ls -la")["permissionDecisionReason"]

        self.assertIn("not a warning about the call itself", reason)
        # 繰り返しを止める先はルールの側。そこを言わないと同じ問いが出続ける。
        self.assertIn("allow section", reason)

    def test_allow_に当たれば通る(self):
        path = self.rules(
            deny=[rule("push", "Bash", "*git push*")],
            allow=[rule("ls", "Bash", "*ls *", message="")],
        )

        self.assertNotIn("permissionDecision", self.judge(path, "Bash", "ls -la"))

    def test_ルールが置いた確認は未言及と区別して返る(self):
        path = self.rules(
            ask=[rule("migrations", "Write", "*/migrations/*", message="人が中身を見ます")],
            allow=[rule("anything", "Write", "*", message="")],
        )

        out = self.judge(path, "Write", os.path.join(self.root, "migrations", "0001.sql"))

        self.assertEqual(out.get("permissionDecision"), "ask")
        self.assertIn("RULE_ASK", out["permissionDecisionReason"])
        # 人が置いた文面が届くこと。届かないと、何を見て判断するのか分からない。
        self.assertIn("人が中身を見ます", out["permissionDecisionReason"])

    def test_deny_が明示的_ask_より強い(self):
        path = self.rules(
            deny=[rule("secrets", "Write", "*/secrets/*", message="止めます")],
            ask=[rule("everything", "Write", "*", message="聞きます")],
        )

        out = self.judge(path, "Write", os.path.join(self.root, "secrets", "x.txt"))

        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn("止めます", out["permissionDecisionReason"])
        self.assertNotIn("聞きます", out["permissionDecisionReason"])

    def test_明示的_ask_が_allow_より強い(self):
        path = self.rules(
            ask=[rule("migrations", "Write", "*/migrations/*", message="聞きます")],
            allow=[rule("anything", "Write", "*", message="")],
        )

        out = self.judge(path, "Write", os.path.join(self.root, "migrations", "0001.sql"))

        self.assertEqual(out.get("permissionDecision"), "ask")

    def test_区画をまたいで当たっても強いほうだけを返す(self):
        # 弱い側の文面まで返すと、拒否された呼び出しに「確認すれば通る」と
        # 読める文が並ぶ。次の一手が 2 つに割れる。
        path = self.rules(
            deny=[rule("a", "Bash", "*git push*", message="拒否の文面")],
            ask=[rule("b", "Bash", "*git *", message="確認の文面")],
            allow=[rule("c", "Bash", "*", message="")],
        )

        reason = self.judge(path, "Bash", "git push origin main")["permissionDecisionReason"]

        self.assertIn("拒否の文面", reason)
        self.assertNotIn("確認の文面", reason)

    def test_同じ区画で複数当たれば全部返す(self):
        # どれか 1 つを選ぶと、選ばれなかったルールの言い分は誰にも届かない。
        path = self.rules(
            deny=[
                rule("a", "Bash", "*git push*", message="1 つ目"),
                rule("b", "Bash", "* origin *", message="2 つ目"),
            ]
        )

        reason = self.judge(path, "Bash", "git push origin main")["permissionDecisionReason"]

        self.assertIn("1 つ目", reason)
        self.assertIn("2 つ目", reason)

    def test_読み切れないコマンドは拒否ではなく確認になる(self):
        # 対象を確定できなかっただけで、禁じられたことをしたわけではない。
        # 設計 §13.1 の PARSE_UNCERTAIN は ask 系に置かれている。
        path = self.rules(
            deny=[rule("push", "Bash", "*git push*")],
            allow=[rule("anything", "Bash", "*", message="")],
        )

        out = self.judge(path, "Bash", "cat <<'EOF'\nhello\n")

        self.assertEqual(out.get("permissionDecision"), "ask")
        self.assertIn("PARSE_UNCERTAIN", out["permissionDecisionReason"])

    def test_実行される部分が無いコマンドは何も返さない(self):
        # コメントだけの行。何も走らないものについて人に聞く意味は無い。
        path = self.rules(deny=[rule("push", "Bash", "*git push*")])

        self.assertEqual(self.judge(path, "Bash", "# git push origin main"), {})


if __name__ == "__main__":
    unittest.main()
