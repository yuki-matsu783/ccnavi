"""共通層の置き場は `.ccnavi/common/` 固定（ADR-0052 の受入テスト）。

3 層のうち共通層だけが env（`CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK`）で動き、
自身の層とプロジェクトの層は `<ccnavi ディレクトリ>/config/` 固定、という非対称を無くす。
env を渡しても共通層は既定の置き場のままになる。

**診断のためのフラグ（`--rules` / `--phases` / `--risk`）は残す。** ここを一緒に消すと、
層の合成を確かめるテストが自分の一時ディレクトリを指せなくなる（`ConfigUnionHarness`）。
hook は引数を渡さずに起動するので、フラグが残っていても判定の入口は固定される。

起動は `ConfigUnionHarness.ccnavi` を使わない。あちらは毎回 3 本ともフラグで渡すので、
env が効くかどうかを見られない。ここでは 3 本のフラグを渡さずに起動する。

実装は入っている（ADR-0052）。ここが赤くなったら、env を読む経路が戻ったということ。
"""

from __future__ import annotations

import json
import os
import unittest

from tests import ROOT
from tests.config.test_config_union import ConfigUnionHarness, write
from tests.inproc import run_ccnavi

# 共通層に置いてあるものとは別の中身。env かフラグがこちらを指したときだけ当たる。
OTHER_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "only-in-the-other-file",
            "match": "Read",
            "glob": "*/src/*",
            "message": "this file is not the common layer.",
        }
    ],
}
OTHER_PHASES = "version: 1\nphases:\n  only-here:\n    kind: work\n    title: よそ\n"
OTHER_RISK = "version: 1\nfactors:\n  - id: only-here\n    lines_over: 1\n    points: 99\n"


class CommonLayerPlaceHarness(ConfigUnionHarness):
    """共通層の 3 本をフラグで渡さずに動かす道具。"""

    def setUp(self):
        super().setUp()
        elsewhere = os.path.join(self.ws, "elsewhere")
        self.other_rules = write(os.path.join(elsewhere, "rules.yml"), json.dumps(OTHER_RULES))
        self.other_phases = write(os.path.join(elsewhere, "phases.yml"), OTHER_PHASES)
        self.other_risk = write(os.path.join(elsewhere, "risks.yml"), OTHER_RISK)

    def bare(self, *args, env=None, guard="disable", flags=(), stdin=""):
        """共通層の 3 本をフラグで渡さずに 1 回動かす。

        `flags` を渡したときだけ、その綴りを足す（フラグが残っていることを確かめる側）。
        """
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--projects",
                self.projects,
                "--approved",
                ".ccnavi/tickets",
                "--state",
                self.state,
                "--log",
                self.log,
                "--guard-core-files",
                guard,
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                *flags,
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def decide(self, tool, *, env=None, guard="disable", **tool_input):
        """hook の payload を 1 件渡して判定させる。フラグは 3 本とも渡さない。"""
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": self.ws,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        return self.bare("--mode", "enable", env=env, guard=guard, stdin=json.dumps(payload))

    def layers(self, **options):
        """`--explain --json` の layers[]。"""
        target = os.path.join(self.ws, "src", "a.py")
        result = self.bare("--explain", "--json", "Read", target, **options)
        try:
            return json.loads(result.stdout)["layers"]
        except (ValueError, KeyError) as exc:
            self.fail(f"--explain --json が読めない: {exc}\n{result.stdout}\n{result.stderr}")

    def common(self, **options):
        """共通層の 1 件。"""
        found = [layer for layer in self.layers(**options) if layer["name"] == "common"]
        self.assertEqual(len(found), 1, "共通層は 1 件")
        return found[0]

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason", "")

    def all_three(self):
        """3 本ともよそを指す env。"""
        return {
            "CCNAVI_RULES": self.other_rules,
            "CCNAVI_PHASES": self.other_phases,
            "CCNAVI_RISK": self.other_risk,
        }


class EnvDoesNotMoveTheCommonLayerTest(CommonLayerPlaceHarness):
    """env は共通層を動かさない。"""

    def test_the_rules_env_does_not_move_the_common_layer(self):
        """`CCNAVI_RULES` を渡しても、共通層のルールは `.ccnavi/common/rules.yml`。"""
        layer = self.common(env={"CCNAVI_RULES": self.other_rules})
        self.assertEqual(layer["rules"]["path"], self.rules)

    def test_the_phases_env_does_not_move_the_common_layer(self):
        """`CCNAVI_PHASES` も同じ。"""
        layer = self.common(env={"CCNAVI_PHASES": self.other_phases})
        self.assertEqual(layer["phases_file"]["path"], self.phases)

    def test_the_risk_env_does_not_move_the_common_layer(self):
        """`CCNAVI_RISK` も同じ。"""
        layer = self.common(env={"CCNAVI_RISK": self.other_risk})
        self.assertEqual(layer["risk"]["path"], self.risk)

    def test_the_three_envs_together_do_not_move_anything(self):
        """3 本まとめて渡しても動かない。"""
        layer = self.common(env=self.all_three())
        self.assertEqual(
            [layer["rules"]["path"], layer["phases_file"]["path"], layer["risk"]["path"]],
            [self.rules, self.phases, self.risk],
        )

    def test_the_rules_the_env_names_are_not_applied(self):
        """env が指したファイルの deny は当たらない。置き場だけでなく判定でも確かめる。"""
        result = self.decide(
            "Read",
            env={"CCNAVI_RULES": self.other_rules},
            file_path=os.path.join(self.ws, "src", "a.py"),
        )
        self.assertNotIn("only-in-the-other-file", self.reason(result))


class FlagsStillMoveTheCommonLayerTest(CommonLayerPlaceHarness):
    """診断のためのフラグは残る。消しすぎの見張り。"""

    def test_the_rules_flag_still_moves_the_common_layer(self):
        layer = self.common(flags=("--rules", self.other_rules))
        self.assertEqual(layer["rules"]["path"], self.other_rules)

    def test_the_phases_flag_still_moves_the_common_layer(self):
        layer = self.common(flags=("--phases", self.other_phases))
        self.assertEqual(layer["phases_file"]["path"], self.other_phases)

    def test_the_risk_flag_still_moves_the_common_layer(self):
        layer = self.common(flags=("--risk", self.other_risk))
        self.assertEqual(layer["risk"]["path"], self.other_risk)

    def test_the_flag_wins_over_the_env(self):
        """env を渡してもフラグが勝つ。env は読まれないので当然そうなる。"""
        layer = self.common(
            env={"CCNAVI_RULES": os.path.join(self.ws, "elsewhere", "nowhere.yml")},
            flags=("--rules", self.other_rules),
        )
        self.assertEqual(layer["rules"]["path"], self.other_rules)


class TheDefaultPlaceStaysGuardedTest(CommonLayerPlaceHarness):
    """守る面は既定の置き場に付く。"""

    def test_named_tool_writes_into_the_default_place_are_denied_while_the_env_names_another(self):
        """env がよそを指していても、`.ccnavi/common/` の 3 本は組み込みで止まる。

        いまは守る対象を `conf.rules` などから組み立てるので、env がよそを指すと
        `builtin-guard-common-layer` はそちらに付く。固定になれば既定の置き場に戻る。
        """
        for path in (self.rules, self.phases, self.risk):
            with self.subTest(path=os.path.basename(path)):
                result = self.decide("Write", env=self.all_three(), guard="enable", file_path=path)
                self.assertIn("builtin-guard-common-layer", self.reason(result))


if __name__ == "__main__":
    unittest.main()
