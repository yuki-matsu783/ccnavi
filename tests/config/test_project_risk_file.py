"""`--project-risk-file <名前>=<パス>` の受入テスト（REQ-DIA-09）。

VS Code 拡張のリスク管理画面が、編集中のレイヤー（自身かプロジェクト）の配点を保存せずに、
共通レイヤーと合わせて検証するための差し替え。config 単体の形式だけでなく、合成したあとの
境目の逆転・同 id の衝突・`script:` の存在確認
（解決先はそのレイヤーの git プロジェクトルート）を見る。
fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ（共通レイヤーに
`big-diff` と `levels: {medium: 20, high: 40, critical: 70}`、lib のレイヤーに `schema` と
`levels: {critical: 50}`。自身のレイヤーと app は risks.yml を持たない）。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.config.test_config_union import COMMON_RISK, ConfigUnionHarness, layer_path, read, write

# config 単体では読めるが、共通の medium 20 と合わせると high 10 < medium 20 で逆転する。
INVERTING = "version: 1\nlevels: {high: 10}\n"

# 共通の `big-diff` と同じ id で中身が違う。
CLASHING = (
    "version: 1\nfactors:\n  - {id: big-diff, points: 99, lines_over: 1, message: 別の行数}\n"
)

SCRIPT_FACTOR = (
    "version: 1\nfactors:\n"
    "  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n"
)


class ProjectRiskFileTest(ConfigUnionHarness):
    def risk_problems(self, severity, where, *args):
        return [p for p in self.problems(severity, *args, where=where) if "(risk)" in p["where"]]

    def edited(self, name, text):
        return write(os.path.join(self.ws, "tmp", name), text)

    def test_a_layer_that_inverts_levels_only_after_the_merge_is_an_error(self):
        tmp = self.edited("lib-risks.yml", INVERTING)
        self.assertEqual(self.risk_problems("error", "(projects/lib)"), [])

        errors = self.risk_problems("error", "(projects/lib)", "--project-risk-file", f"lib={tmp}")
        self.assertTrue(any("high" in p["detail"] for p in errors), errors)
        # 差し替えは診断の中だけ。本来のファイルは書き換えない。
        self.assertEqual(read(layer_path(self.lib, "risk")).count("high"), 0)

        # 共通の差し替え（--risk）に同じ中身を渡す従来の形では、合成後の逆転は見えない。
        # 共通レイヤー単体の `levels` が逆転していないため。レイヤーの側として渡すと見える。
        old = self.risk_problems("error", "(projects/lib)", "--risk", tmp)
        self.assertEqual(old, [])

    def test_self_and_a_project_without_a_risk_file_are_named_too(self):
        tmp = self.edited("risks.yml", INVERTING)
        own = self.risk_problems("error", "(self)", "--project-risk-file", f"self={tmp}")
        self.assertTrue(any("high" in p["detail"] for p in own), own)
        # ファイルの無い app でも、渡せばそのレイヤーの配点として合成される。lib は巻き込まない。
        args = ("--project-risk-file", f"app={tmp}")
        app = self.risk_problems("error", "(projects/app)", *args)
        self.assertTrue(any("high" in p["detail"] for p in app), app)
        self.assertEqual(self.risk_problems("error", "(projects/lib)", *args), [])

    def test_a_clashing_id_is_a_warn_naming_the_layer(self):
        tmp = self.edited("lib-risks.yml", CLASHING)
        warns = self.risk_problems("warn", "(projects/lib)", "--project-risk-file", f"lib={tmp}")
        self.assertTrue(any("big-diff" in p["detail"] for p in warns), warns)
        self.assertTrue(any("lib:big-diff" in p["detail"] for p in warns), warns)
        # error ではない（両方を数える）。
        errors = self.risk_problems("error", "(projects/lib)", "--project-risk-file", f"lib={tmp}")
        self.assertEqual(errors, [])

    def test_script_is_looked_up_under_the_layers_own_project_root(self):
        tmp = self.edited("lib-risks.yml", SCRIPT_FACTOR)
        args = ("--project-risk-file", f"lib={tmp}")

        # 置いてなければ落ちる。差し替えファイルの置き場（ws/tmp）には引かない。
        missing = self.risk_problems("error", "(projects/lib)", *args)
        self.assertTrue(any("count.sh" in p["detail"] for p in missing), missing)

        # ワークスペースルートに置いても lib の基準には効かない（レイヤーごとに解決先が違う）。
        write(os.path.join(self.ws, ".ccnavi", "scripts", "count.sh"), "true\n")
        still = self.risk_problems("error", "(projects/lib)", *args)
        self.assertTrue(any("count.sh" in p["detail"] for p in still), still)

        # lib の置き場にあれば通る。
        write(os.path.join(self.lib, ".ccnavi", "scripts", "count.sh"), "true\n")
        self.assertEqual(self.risk_problems("error", "(projects/lib)", *args), [])

        # 自身のレイヤーはワークスペースルートが基準。上で置いたものが引かれる。
        own = self.edited("self-risks.yml", SCRIPT_FACTOR)
        self.assertEqual(
            self.risk_problems("error", "(self)", "--project-risk-file", f"self={own}"), []
        )
        os.remove(os.path.join(self.ws, ".ccnavi", "scripts", "count.sh"))
        gone = self.risk_problems("error", "(self)", "--project-risk-file", f"self={own}")
        self.assertTrue(any("count.sh" in p["detail"] for p in gone), gone)

    def test_the_swap_does_not_move_what_is_guarded(self):
        tmp = self.edited("lib-risks.yml", COMMON_RISK)
        real = layer_path(self.lib, "risk")
        for flags in ((), ("--project-risk-file", f"lib={tmp}")):
            with self.subTest(flags=flags):
                done = self.ccnavi(*flags, "--test", "--json", "Write", real, guard="enable")
                body = json.loads(done.stdout)
                self.assertEqual(body["verdict"], "deny", done.stdout)
                ids = [hit["id"] for hit in body.get("rules", [])]
                self.assertTrue(any("guard" in i for i in ids), ids)

    def test_is_ignored_outside_diagnostics_and_needs_name_and_path(self):
        tmp = self.edited("lib-risks.yml", INVERTING)
        ignored = self.ccnavi(
            "--mode",
            "enable",
            "--project-risk-file",
            f"lib={tmp}",
            stdin=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Read",
                    "tool_input": {"file_path": os.path.join(self.ws, "README.md")},
                    "cwd": self.ws,
                }
            ),
        )
        self.assertIn("--project-risk-file は診断", ignored.stderr)

        malformed = self.ccnavi("--lint", "--json", "--project-risk-file", "lib")
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("<名前>=<パス>", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
