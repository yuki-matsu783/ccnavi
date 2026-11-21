"""3 つのタイプと権限モードへの委譲の受入テスト。道具を外から呼んで応答だけを見る。

見るのは 4 つ。

1. 強さの順が deny > ルールが置いた確認 > allow > 権限モードへの委譲であること
2. どのルールも言及しない呼び出しが確認になること
3. ルールが置いた確認と権限モードへの委譲が区別して返ること
4. ルールがチケットより強いこと

3 つ目が要る理由は次のとおり。権限モードへの委譲は設定の穴に起因するので、
穴が直るまで同じ問いが繰り返される。ルールが置いた確認はユーザが意図して置いた
確認ポイントで、繰り返されること自体に価値がある。混ぜると前者の数に
後者が埋もれる。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi


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
        """タイプを渡してルールファイルを 1 本置く。

        書き出すのは JSON。YAML は JSON の上位互換なので、判定が読むのと同じ
        読み手がそのまま受け取る。タイプの強さを見たいテストで、YAML の表記の
        話に付き合わずに済む。
        """
        body = {"version": 1, **sections}
        # 置くのは共通レイヤーの既定の場所。`--rules` は診断でだけ有効で、hook の判定には
        # 届かない。
        return write(common_path(self.root, "rules"), json.dumps(body))

    def judge(self, tool: str, subject: str) -> dict:
        field = "command" if tool == "Bash" else "file_path"
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": {field: subject},
            }
        )
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                "enable",
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        try:
            return json.loads(done.stdout)["hookSpecificOutput"]
        except (ValueError, KeyError) as exc:
            self.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {done.stdout!r}")

    def test_どのルールも言及しなければ確認になり_危険の表明ではないと言う(self):
        # 既定が許可ではなく確認であること。allow を書き切るまで、
        # 言及されていない呼び出しはユーザが見る側になる。
        self.rules(deny=[rule("push", "Bash", "*git push*")])

        reason = self.judge("Bash", "ls -la")

        self.assertEqual(reason.get("permissionDecision"), "ask")
        self.assertIn("UNDECLARED", reason["permissionDecisionReason"])
        # ccnavi は判定を持たないからユーザに聞く。危険だと書くと、受け取った側は
        # 存在しない危険を探しに行く。
        self.assertIn("not a warning about the call itself", reason["permissionDecisionReason"])
        # 繰り返しを止める先はルールの側。そこを言わないと同じ問いが出続ける。
        self.assertIn("allow section", reason["permissionDecisionReason"])

    def test_タイプの強さは_deny_ask_allow_の順(self):
        # 1 つのルールファイルに全部置き、呼び出しごとに強さを見る。
        self.rules(
            deny=[
                rule("a", "Bash", "*git push*", message="拒否の文面"),
                rule("b", "Bash", "* origin *", message="拒否の文面 2 つ目"),
                rule("secrets", "Write", "*/secrets/*", message="止めます"),
            ],
            ask=[
                rule("g", "Bash", "*git *", message="確認の文面"),
                rule("everything", "Write", "*/secrets/*", message="聞きます"),
                rule("migrations", "Write", "*/migrations/*", message="ユーザが中身を見ます"),
            ],
            allow=[
                rule("ls", "Bash", "*ls *", message=""),
                rule("c", "Bash", "*", message=""),
                rule("anything", "Write", "*", message=""),
            ],
        )

        # allow にヒットすれば通る。
        self.assertNotIn("permissionDecision", self.judge("Bash", "ls -la"))
        # ルールが置いた確認は未言及と区別して返り、ユーザが置いた文面が届く。
        asked = self.judge("Write", os.path.join(self.root, "migrations", "0001.sql"))
        self.assertEqual(asked.get("permissionDecision"), "ask")
        self.assertIn("RULE_ASK", asked["permissionDecisionReason"])
        self.assertIn("ユーザが中身を見ます", asked["permissionDecisionReason"])
        # deny が明示的な ask より強い。弱い側の文面は返さない。
        denied = self.judge("Write", os.path.join(self.root, "secrets", "x.txt"))
        self.assertEqual(denied.get("permissionDecision"), "deny")
        self.assertIn("止めます", denied["permissionDecisionReason"])
        self.assertNotIn("聞きます", denied["permissionDecisionReason"])
        # タイプをまたいでヒットしても強いほうだけ。同じタイプなら全部返す
        # （どれか 1 つを選ぶと、選ばれなかったルールの言い分は誰にも届かない）。
        reason = self.judge("Bash", "git push origin main")["permissionDecisionReason"]
        self.assertIn("拒否の文面", reason)
        self.assertIn("拒否の文面 2 つ目", reason)
        self.assertNotIn("確認の文面", reason)

    def test_読み切れないコマンドは確認になり_実行される部分が無いコマンドは何も返さない(self):
        # 対象を確定できなかっただけで、禁じられたことをしたわけではない。
        # コメントだけの行は、何も走らないのでユーザに聞く意味が無い。
        self.rules(
            deny=[rule("push", "Bash", "*git push*")],
            allow=[rule("anything", "Bash", "*", message="")],
        )

        out = self.judge("Bash", "cat <<'EOF'\nhello\n")

        self.assertEqual(out.get("permissionDecision"), "ask")
        self.assertIn("PARSE_UNCERTAIN", out["permissionDecisionReason"])
        self.assertEqual(self.judge("Bash", "# git push origin main"), {})


if __name__ == "__main__":
    unittest.main()
