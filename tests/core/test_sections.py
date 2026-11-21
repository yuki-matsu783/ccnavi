"""どのルールも言及しない呼び出しが確認になることの受入テスト。道具を外から呼んで応答だけを見る。

ルールが置いた確認と権限モードへの委譲は区別して返す。委譲は設定の穴に起因するので、穴が直るまで同じ問いが
繰り返される。ルールが置いた確認はユーザが置いた確認ポイントで、繰り返されること自体に価値がある。
混ぜると前者の数に後者が埋もれる。

強さの順（deny > ask > allow）と読み切れないコマンドの扱いは Go（go/internal/hook/judge の sections_test.go）に
移した。ここには、実行ファイルを通した 1 本だけを残す。
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


if __name__ == "__main__":
    unittest.main()
