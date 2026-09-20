"""ルールファイルを読めないときの受入テスト。

別ファイルにしてあるのは、ここが「ガードが落ちたときの振る舞い」という
独立した関心で、壊れたルールファイルを自分で用意する必要があるため。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# YAML として壊れている。閉じていない並び 1 つ。書き損じの典型。
BROKEN = "version: 2\ndeny: [\n  - id: x\n"


def run(root, payload, log="", env=None):
    """道具を 1 回動かす。ワークスペースルートを呼び出しごとに変えられる。

    ルールは `--rules` では渡さない。あれは診断でだけ効き、hook の判定には
    届かない（ADR-0067）。読めないルールは `--root` の下の共通層に置く。

    コアファイルの控えと復元は切る。リポジトリ自身をワークスペースルートにして動くので、
    切らないと、作業ツリーで消した設定ファイルや、ccnavi ディレクトリの名前を動かした先へ
    `logs/state` の控えが書き戻される。組み込みの既定はこの設定に依らず入るので、
    見たいものは変わらない。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.update(env or {})
    return run_ccnavi(
        [
            "--root",
            root,
            "--log",
            log,
            "--mode",
            "enable",
        ],
        input=payload,
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
        # 共通層のルールが読めないワークスペース。
        self.root = self.directory.name
        self.broken = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(self.broken), exist_ok=True)
        with open(self.broken, "w", encoding="utf-8") as f:
            f.write(BROKEN)
        # ルールファイルがそもそも無いワークスペース。読めない理由が違うだけで、
        # 落ちる先は同じ既定。
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.without_rules = empty.name

    def test_壊れたルールでもセッションは死なない(self):
        # ここが要件の核。拒否側へ倒すと、壊れたファイルを直すための呼び出しまで
        # 止まって回復できなくなる。既定モードが block なので、ルールを置く前に
        # hook を登録しただけでセッションが死ぬ。
        #
        # 既定にはプロジェクトの allow が無いので、無害な呼び出しも権限モードへの委譲に
        # なる。人が答えれば進むので、道は塞がっていない。塞がるのは deny だけ。
        for root, why in ((self.root, "broken"), (self.without_rules, "missing")):
            with self.subTest(rules=why):
                result = run(root, pre_tool_use("Bash", "command", "cat README.md"))
                self.assertEqual(result.returncode, 0, f"無害な呼び出しを止めた: {result.stderr!r}")
                self.assertNotEqual(
                    out_of(self, result).get("permissionDecision"),
                    "deny",
                    "無害な呼び出しを拒否した",
                )

    def test_既定に落ちたことは呼び出しごとに伝える(self):
        # 黙って落ちると、ガードが立っているように見えて実際は何も見ていない
        # 状態が続く。止まっているより悪い。止まっていれば誰かが気づく。
        out = out_of(self, run(self.root, pre_tool_use("Bash", "command", "cat README.md")))
        said = out.get("permissionDecisionReason", "") + out.get("additionalContext", "")

        self.assertIn("built-in defaults", said)
        self.assertIn(self.broken, said, "どのファイルが読めなかったのかを言っていない")

    def test_既定でも読み取りは通る(self):
        # いちばん数が多く、作業ツリーを変えようがない。ここまで確認を出すと、
        # 本当に見てほしい 1 件がその中に埋もれる。
        result = run(self.root, pre_tool_use("Read", "file_path", "README.md"))

        self.assertNotIn("permissionDecision", out_of(self, result))

    def test_既定でも取り返しの付かない操作は止まる(self):
        # 落ちた先が素通しでは、壊すだけでガードを外せることになる。
        for command in ["rm -rf /tmp/x", "git push origin main", "git reset --hard HEAD~1"]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_既定は設定の修復を妨げない(self):
        # REQ-PRE-06 が「読み取りと設定自身の修復を妨げない」と書いている意味。
        # ここを止めると直す道が 1 本も残らない。
        #
        # Write / Edit は Claude Code の権限モードに従う。妨げてはいないが、ガードが落ちている
        # あいだにガードの設定を書き換える操作なので、人が 1 度見る側に置く。
        for tool in ("Read", "Write", "Edit"):
            with self.subTest(tool=tool):
                result = run(self.root, pre_tool_use(tool, "file_path", ".ccnavi/common/rules.yml"))
                self.assertNotEqual(
                    out_of(self, result).get("permissionDecision"),
                    "deny",
                    f"{tool} による修復を止めた",
                )

    def test_既定はシェルから設定を書き換えさせない(self):
        # 上と対になっている。ここを開けると、シェルでルールを壊し、壊れた結果
        # 緩んだ既定に落ちる、という順路ができる。壊す側と直す側で経路を分ける。
        out = out_of(
            self,
            run(self.root, pre_tool_use("Bash", "command", "echo x > .ccnavi/common/rules.yml")),
        )

        self.assertEqual(out.get("permissionDecision"), "deny")
        # 止めた先に道が無いと、拒否は行き止まりになる。
        self.assertIn("Write", out["permissionDecisionReason"])

    def test_既定でもシェルからの書き込みは綴りを変えても止まる(self):
        for command in [
            "echo x >> .claude/hooks/lint-py.sh",
            "sed -i s/deny/allow/ .ccnavi/common/rules.yml",
            "cp /tmp/x .ccnavi/scripts/ccnavi-git.sh",
            "echo {} > .claude/settings.json",
            "cd .claude/worktrees/w && echo x > ../../scripts/ccnavi-git.sh",
            # `cd` で入ってから書く形（issue #61、ADR-0067）。行き先の綴りから場所の
            # 名前が消えるので、移った先から見た綴りにも当てないと素通りする。
            "cd .ccnavi/common && echo x > rules.yml",
            "cd .claude && echo x > settings.json",
            "cd .claude/hooks && echo x > lint-py.sh",
            "cd .claude && rm -rf hooks",
        ]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_既定のシェルの守りは設定で動かした置き場にも当たる(self):
        # 実行ファイル・ccnavi ディレクトリ・共通層は設定で動く。既定の側だけ空の設定で
        # 組んでいると、動かしたワークスペースではルールファイルが壊れたときにだけ
        # そこへの書き込みが止まらない（issue #14）。
        # 絶対パスは `/` で綴る。bash は引用されない `\` を落とすので、`\` の綴りのままでは
        # そのコマンドは設定ファイルに書かない。
        for env, command in [
            ({"CCNAVI_PROJECT_HOME": ".navi"}, "echo x > projects/lib/.navi/config/rules.yml"),
            ({"CCNAVI_PROJECT_HOME": ".navi"}, "rm -rf .navi"),
            ({"CCNAVI_BIN_PATH": "tools/guard/ccnavi"}, "cp /tmp/x tools/guard/ccnavi"),
            ({}, f"echo x > {self.broken.replace(os.sep, '/')}"),
        ]:
            with self.subTest(command=command):
                out = out_of(
                    self,
                    run(self.root, pre_tool_use("Bash", "command", command), env=env),
                )
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")
                self.assertIn("builtin-guard-config-via-bash", out["permissionDecisionReason"])

    def test_既定はマージの解決を妨げない(self):
        # 衝突マーカーの入ったルールファイルは YAML として読めないので、
        # 衝突を解いている最中は必ず既定に落ちている。そこで解決の手が止まると、
        # ガードが落ちた状態から出られない。どれもファイルに新しい文面を書かない。
        for command in [
            "sh .ccnavi/scripts/ccnavi-git.sh restore --ours -- .ccnavi/common/rules.yml",
            "sh .ccnavi/scripts/ccnavi-git.sh add -- .ccnavi/common/rules.yml",
            "cat .ccnavi/common/rules.yml",
            "grep -n conflict .ccnavi/common/rules.yml",
        ]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertNotEqual(out.get("permissionDecision"), "deny", f"止めた: {command!r}")

    def test_既定に落ちたことは記録に残る(self):
        # ガードが落ちたまま何回動いたかは、これでしか数えられない。
        log = os.path.join(self.directory.name, "log.jsonl")
        run(self.root, pre_tool_use("Bash", "command", "cat README.md"), log=log)
        with open(log, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].get("fallback"), "builtin-rules")
        self.assertEqual(records[0].get("detail"), self.broken)
        # 判定には達しているので skip ではない。既定にプロジェクトの allow が
        # 無いので、どのルールも言及しない呼び出しは Claude Code の権限モードに従う。
        self.assertEqual(records[0]["decision"], "ask")
        self.assertEqual(records[0].get("code"), "UNDECLARED")


if __name__ == "__main__":
    unittest.main()
