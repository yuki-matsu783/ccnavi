"""`--project-phases-file <名前>=<パス>` の受入テスト（REQ-DIA-09）。

VS Code 拡張のフェーズ管理画面が、編集中の層の種類を保存せずに共通層と合成して検証するための
差し替え。fixture は tests/test_config_union.py の ConfigUnionHarness を継ぐ（共通層に `design`
「設計」、自身の層に `docs`、lib の層に `build` / `release`）。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.test_config_union import LIB_PHASES, ConfigUnionHarness, layer_path, read, write

# lib の層の build の表示名を、共通層の design と同じ「設計」にしたもの。
LIB_TITLE_OVERLAP = LIB_PHASES.replace("title: ビルド", "title: 設計")

# 共通層の種類と id も表示名も重ならない種類。`phases:` の続きに繋げる。
NOTES_TYPE = "  notes:\n    kind: work\n    title: メモ\n    review: none\n    scope: inherit\n"

# 種類の無い層。実行ファイルは読めないとして error にする（拡張はこの形を書き出さない）。
EMPTY_LAYER = "version: 1\nphases: {}\n"


class ProjectPhasesFileTest(ConfigUnionHarness):
    def phase_errors(self, where, *args):
        return [p for p in self.problems("error", *args, where=where) if "(phases)" in p["where"]]

    def test_swaps_one_layers_phase_types_and_lints_them_merged_with_the_common_layer(self):
        # 差し替えなしなら lib の層は共通層と合成できる。
        self.assertEqual(self.phase_errors("(projects/lib)"), [])

        edited = write(os.path.join(self.ws, "tmp", "lib-phases.yml"), LIB_TITLE_OVERLAP)
        errors = self.phase_errors("(projects/lib)", "--project-phases-file", f"lib={edited}")
        self.assertTrue(errors, "表示名の重なりが lib の層の error として出ない")
        # 差し替えは診断の中だけ。本来のファイルは書き換えない。
        self.assertEqual(read(layer_path(self.lib, "phases")), LIB_PHASES)

    def test_self_names_the_workspaces_own_layer(self):
        own = read(layer_path(self.ws, "phases"))
        edited = write(
            os.path.join(self.ws, "tmp", "self-phases.yml"),
            own + "  clash:\n    kind: work\n    title: 設計\n    review: mr\n    scope: inherit\n",
        )
        errors = self.phase_errors("(self)", "--project-phases-file", f"self={edited}")
        self.assertTrue(errors, "自身の層の差し替えが検証に届かない")

        # 共通層と重ならない種類を足しただけなら通る。
        added = write(os.path.join(self.ws, "tmp", "self-added.yml"), own + NOTES_TYPE)
        self.assertEqual(self.phase_errors("(self)", "--project-phases-file", f"self={added}"), [])

        # 種類の無い層は error。拡張が層のファイルを最初の保存まで作らないのはこのため。
        empty = write(os.path.join(self.ws, "tmp", "empty.yml"), EMPTY_LAYER)
        self.assertTrue(self.phase_errors("(self)", "--project-phases-file", f"self={empty}"))

    def test_is_ignored_outside_diagnostics_and_needs_name_and_path(self):
        edited = write(os.path.join(self.ws, "tmp", "lib-phases.yml"), LIB_TITLE_OVERLAP)
        ignored = self.ccnavi(
            "--mode",
            "enable",
            "--project-phases-file",
            f"lib={edited}",
            stdin=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Read",
                    "tool_input": {"file_path": os.path.join(self.ws, "README.md")},
                    "cwd": self.ws,
                }
            ),
        )
        self.assertIn("--project-phases-file は診断", ignored.stderr)

        malformed = self.ccnavi("--lint", "--json", "--project-phases-file", "lib")
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("<名前>=<パス>", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
