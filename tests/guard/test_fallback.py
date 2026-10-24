"""ルールファイルを読めないときの受入テスト。

別ファイルにしてあるのは、ここが「ガードが機能しなくなったときの振る舞い」という
独立した関心で、不正なルールファイルを自分で用意する必要があるため。
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# YAML として不正である。閉じていないリスト 1 つ。書き損じの典型。
BROKEN = "version: 2\ndeny: [\n  - id: x\n"


def run(root, payload, env=None):
    """道具を 1 回動かす。ワークスペースルートを呼び出しごとに変えられる。

    ルールは `--rules` では渡さない。あれは診断でだけ有効で、hook の判定には
    届かない。読めないルールは `--root` の下の共通レイヤーに置く。

    コアファイルのバックアップと復元は切る。リポジトリ自身をワークスペースルートにして動くので、
    切らないと、ワークツリーで消した設定ファイルや、ccnavi ディレクトリの名前を動かした先へ
    `logs/state` のバックアップが書き戻される。組み込みの既定はこの設定に依らず入るので、
    見たいものは変わらない。
    """
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.update(env or {})
    return run_ccnavi(
        [
            "--root",
            root,
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
        # 共通レイヤーのルールが読めないワークスペース。
        self.root = self.directory.name
        self.broken = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(self.broken), exist_ok=True)
        with open(self.broken, "w", encoding="utf-8") as f:
            f.write(BROKEN)
        # ルールファイルがそもそも無いワークスペース。読めない理由が違うだけで、
        # 行き着く先は同じ既定。
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.without_rules = empty.name

    def test_不正なルールでもセッションは動き続ける(self):
        # ここが要件の中心。拒否にすると、不正なファイルを直すための呼び出しまで
        # 止まって回復できなくなる。既定モードが block なので、ルールを置く前に
        # hook を登録しただけでセッションが何もできなくなる。
        #
        # 既定にはプロジェクトの allow が無いので、無害な呼び出しも権限モードへの委譲に
        # なる。ユーザが答えれば進むので、先へ進む方法は残っている。進めなくなるのは deny だけ。
        for root, why in ((self.root, "broken"), (self.without_rules, "missing")):
            with self.subTest(rules=why):
                result = run(root, pre_tool_use("Bash", "command", "cat README.md"))
                self.assertEqual(result.returncode, 0, f"無害な呼び出しを止めた: {result.stderr!r}")
                self.assertNotEqual(
                    out_of(self, result).get("permissionDecision"),
                    "deny",
                    "無害な呼び出しを拒否した",
                )

    def test_既定に戻ったことは呼び出しごとに伝える(self):
        # 何も言わずに既定に戻ると、ガードが働いているように見えて実際は何も見ていない
        # 状態が続く。止まっているより悪い。止まっていれば誰かが気づく。
        out = out_of(self, run(self.root, pre_tool_use("Bash", "command", "cat README.md")))
        said = out.get("permissionDecisionReason", "") + out.get("additionalContext", "")

        self.assertIn("built-in defaults", said)
        self.assertIn(self.broken, said, "どのファイルが読めなかったのかを言っていない")

    def test_既定でも読み取りは通る(self):
        # いちばん数が多く、ワークツリーを変えようがない。ここまで確認を出すと、
        # 本当に見てほしい 1 件がその中に埋もれる。
        result = run(self.root, pre_tool_use("Read", "file_path", "README.md"))

        self.assertNotIn("permissionDecision", out_of(self, result))

    def test_既定でも取り返しの付かない操作は止まる(self):
        # 戻った先が何でも通す設定では、壊すだけでガードを外せることになる。
        for command in ["rm -rf /tmp/x", "git push origin main", "git reset --hard HEAD~1"]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_既定は読み取りを妨げず_修復は下書きとユーザの取り込みに限る(self):
        # 要件の「読み取りと設定自身の修復を妨げない」は、修復の道が残っていることを言う。
        # 直接 Write / Edit で直す道は通さない
        # （builtin:guard-ccnavi-config は、読めない間も外れない）。
        # 残る道は 1 つで、wip/handoff/ に下書きを置き、ユーザが ccnavi user handoff で取り込む。
        rules_file = os.path.join(self.root, ".ccnavi", "config", "common", "rules.yml")
        draft = os.path.join(
            self.root, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml"
        )
        # 自己防衛を切ると、名前で書くツールの 4 本は足されない。ここでは有効にして見る
        # （ワークスペースルートは一時ディレクトリなので、バックアップがリポジトリを汚さない）。
        guard = {"CCNAVI_GUARD_CORE_FILES": "enable"}
        result = run(self.root, pre_tool_use("Read", "file_path", rules_file), guard)
        self.assertNotEqual(out_of(self, result).get("permissionDecision"), "deny", "読めない")
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                out = out_of(
                    self, run(self.root, pre_tool_use(tool, "file_path", rules_file), guard)
                )
                self.assertEqual(out.get("permissionDecision"), "deny", f"{tool} で直せてしまう")
                reason = out["permissionDecisionReason"]
                self.assertIn("builtin:guard-ccnavi-config", reason)
                self.assertIn("wip/handoff/.ccnavi/config/common/rules.yml", reason)
                self.assertIn("ccnavi user handoff", reason)
                out = out_of(self, run(self.root, pre_tool_use(tool, "file_path", draft), guard))
                self.assertNotEqual(
                    out.get("permissionDecision"), "deny", f"{tool} で下書きを置けない"
                )

    def test_既定はシェルから設定を書き換えさせない(self):
        # 上と対になっている。ここを通すと、シェルでルールを壊し、その結果
        # 緩んだ既定に戻る、という順路ができる。壊す側（シェル）と、直す側（下書きと
        # ユーザの取り込み）を分ける。
        out = out_of(
            self,
            run(
                self.root,
                pre_tool_use("Bash", "command", "echo x > .ccnavi/config/common/rules.yml"),
            ),
        )

        self.assertEqual(out.get("permissionDecision"), "deny")
        # 止めた先に別の方法が無いと、拒否されたまま進めなくなる。
        self.assertIn(
            "wip/handoff/.ccnavi/config/common/rules.yml", out["permissionDecisionReason"]
        )
        self.assertIn("ccnavi user handoff", out["permissionDecisionReason"])
        self.assertNotIn("Write か Edit のツールで書いてください", out["permissionDecisionReason"])

    def test_既定でもシェルからの書き込みは表記を変えても止まる(self):
        for command in [
            "echo x >> .claude/hooks/lint-py.sh",
            "sed -i s/deny/allow/ .ccnavi/config/common/rules.yml",
            "cp /tmp/x .ccnavi/scripts/ccnavi-git.sh",
            "echo {} > .claude/settings.json",
            "cd .claude/worktrees/w && echo x > ../../scripts/ccnavi-git.sh",
            # `cd` で入ってから書く形（issue #61）。行き先のパスから場所の
            # 名前が消えるので、移った先から見たパスにも適用しないと止められずに通る。
            "cd .ccnavi/config/common && echo x > rules.yml",
            "cd .claude && echo x > settings.json",
            "cd .claude/hooks && echo x > lint-py.sh",
            "cd .claude && rm -rf hooks",
        ]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_既定のシェルの保護は設定で動かした置き場にもヒットする(self):
        # 実行ファイルは設定で動く（ccnavi ディレクトリと共通レイヤーは固定）。
        # 既定の側だけ空の設定で組んでいると、動かしたワークスペースではルールファイルが
        # 不正になったときにだけそこへの書き込みが止まらない（issue #14）。
        # 絶対パスは `/` で書いて shlex.quote で引用する。bash は引用されない `\` を取り除くので、
        # `\` の表記のまま埋め込むと、ガードが見る行き先が変わる。
        for env, command in [
            ({"CCNAVI_ENTRY_PATH": "tools/guard/ccnavi"}, "cp /tmp/x tools/guard/ccnavi"),
            ({}, f"echo x > {shlex.quote(self.broken.replace(os.sep, '/'))}"),
        ]:
            with self.subTest(command=command):
                out = out_of(
                    self,
                    run(self.root, pre_tool_use("Bash", "command", command), env=env),
                )
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")
                self.assertIn("builtin:guard-config-via-bash", out["permissionDecisionReason"])

    def test_既定はマージの解決を妨げない(self):
        # 衝突マーカーの入ったルールファイルは YAML として読めないので、
        # 衝突を解いている最中は必ず既定を使っている。そこで解決の手が止まると、
        # ガードが機能しない状態から出られない。どれもファイルに新しい文面を書かない。
        for command in [
            "sh .ccnavi/scripts/ccnavi-git.sh restore --ours -- .ccnavi/config/common/rules.yml",
            "sh .ccnavi/scripts/ccnavi-git.sh add -- .ccnavi/config/common/rules.yml",
            "cat .ccnavi/config/common/rules.yml",
            "grep -n conflict .ccnavi/config/common/rules.yml",
        ]:
            with self.subTest(command=command):
                out = out_of(self, run(self.root, pre_tool_use("Bash", "command", command)))
                self.assertNotEqual(out.get("permissionDecision"), "deny", f"止めた: {command!r}")

    def test_既定に戻ったことは記録に残る(self):
        # ガードが機能しないまま何回動いたかは、これでしか数えられない。
        # 記録はワークスペースルートの既定の置き場に残る（`--log` は診断でだけ効く）。
        log = os.path.join(self.root, "logs", "decisions.jsonl")
        run(self.root, pre_tool_use("Bash", "command", "cat README.md"))
        with open(log, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].get("fallback"), "builtin:rules")
        self.assertEqual(records[0].get("detail"), self.broken)
        # 判定には達しているので skip ではない。既定にプロジェクトの allow が
        # 無いので、どのルールも言及しない呼び出しは Claude Code の権限モードに従う。
        self.assertEqual(records[0]["decision"], "ask")
        self.assertEqual(records[0].get("code"), "UNDECLARED")

    def test_既定に戻った回の未言及の文は読めなかったファイルを根拠にしない(self):
        # 読めていないファイルを出所に名指しし、そこへ allow を足せと言うと、同じ応答の
        # 退避の文（先にそのファイルを直す）と食い違う。出所は組み込みの既定にする。
        # 権限モードが無い payload は ask、確認できる者が居ないモードは deny になる。
        for permission_mode, decision in (("", "ask"), ("bypassPermissions", "deny")):
            with self.subTest(permission_mode=permission_mode):
                payload = json.loads(pre_tool_use("Bash", "command", "frobnicate x"))
                payload["permission_mode"] = permission_mode
                out = out_of(self, run(self.root, json.dumps(payload)))
                self.assertEqual(out.get("permissionDecision"), decision)
                reason = out["permissionDecisionReason"]
                undeclared = reason[reason.index("[ccnavi] UNDECLARED") :]
                self.assertIn("(source: (ccnavi built-in defaults))", undeclared)
                self.assertNotIn(self.broken, undeclared)
                self.assertNotIn("allow section of", undeclared)
                self.assertIn("see the note above", undeclared)
                # 退避の文は 1 度だけ。同じ直し方を 2 度並べない。
                self.assertEqual(reason.count("could not be read, so ccnavi is judging"), 1)


if __name__ == "__main__":
    unittest.main()
