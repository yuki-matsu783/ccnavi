"""共通レイヤーと config が無いとき・不正なときの受入テスト。

ファイルが無いのは「設定が無い」正常で、ルール・リスクの配点・フェーズ定義のすべてで空として扱う。
記録の `fallback` にも `--lint` にも出さない。不正なときだけ `fallback` を残す。
組み込みの deny は、共通レイヤーと config の有無・状態によらず常に当たる。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra import settings
from ccnavi.tickets import risk
from tests.config.test_config_union import (
    BROKEN,
    COMMON_RISK,
    COMMON_RULES,
    LIB_RISK,
    LIB_RULES,
    ConfigUnionHarness,
    layer_path,
    write,
)

# 組み込みの既定は `Read` だけを通す（allow）。deny は取り返しの付かない操作。
RM_RF = "rm -rf /tmp/x"
READ = os.path.join("README.md")


class AbsentRulesTest(ConfigUnionHarness):
    def remove_common_rules(self):
        os.remove(self.rules)

    def remove_own_layer(self):
        for kind in ("rules", "phases", "risk"):
            path = layer_path(self.ws, kind)
            if os.path.exists(path):
                os.remove(path)

    def test_a_missing_common_layer_is_empty_and_is_not_a_fallback(self):
        """共通レイヤーが無くても、config のレイヤーを足して動く。`fallback` は残さない。"""
        self.remove_common_rules()

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "generated", "x.py"))

        self.assert_denied(denied, "self:generated")
        self.assertNotIn("fallback", self.last_record())
        self.assertNotIn("built-in defaults", self.reason(denied))

    def test_builtin_deny_stays_on_without_the_common_layer(self):
        """共通レイヤーが無くても、組み込みの deny（取り返しの付かない操作）は当たる。"""
        self.remove_common_rules()

        for tool, field, value in (
            ("Bash", "command", RM_RF),
            ("Bash", "command", "git push origin main"),
            ("Bash", "command", "git reset --hard HEAD~1"),
            ("Write", "file_path", os.path.join(self.ws, ".env")),
        ):
            with self.subTest(value=value):
                denied = self.hook(tool, self.ws, **{field: value})
                self.assert_denied(denied)
                self.assertTrue(
                    any(name.startswith("builtin-") for name in self.last_record()["rules"]),
                    self.last_record(),
                )
                self.assertNotIn("fallback", self.last_record())

    def test_the_builtin_credentials_deny_has_a_leading_boundary(self):
        """土台の認証情報の止めは、実際の `.env`・`.ssh/` を止め、フィールド参照は巻き込まない。"""
        self.remove_common_rules()
        for tool, field, value in (
            ("Bash", "command", "cat .env"),
            ("Bash", "command", "cat .envrc"),
            ("Bash", "command", "cat x/.env.local"),
            ("Bash", "command", "cat ~/.ssh/id_rsa"),
            ("Read", "file_path", os.path.join(self.ws, ".env")),
            ("Write", "file_path", os.path.join(self.ws, "x", ".npmrc")),
        ):
            with self.subTest(value=value):
                self.assert_denied(
                    self.hook(tool, self.ws, **{field: value}), "builtin-credentials"
                )
        for command in ("jq -r '.hooks[0],.env.X' a.json", "echo process.env.HOME"):
            with self.subTest(command=command):
                self.hook("Bash", self.ws, command=command)
                self.assertNotIn("builtin-credentials", self.last_record().get("rules", []))

    def test_builtin_deny_stays_on_under_the_common_layer(self):
        """共通レイヤーが在っても、組み込みの deny は土台として当たる。"""
        denied = self.hook("Bash", self.ws, command="git reset --hard HEAD~1")

        self.assert_denied(denied, "builtin-git-reset-hard")

    def test_without_both_layers_the_builtin_defaults_decide(self):
        """共通レイヤーも config も無ければ、組み込みの既定だけで判定する。`fallback` は残さない。

        既定は `Read` を通す（allow）。deny と合わせて、組み込みの既定の全部。"""
        self.remove_common_rules()
        self.remove_own_layer()

        read = self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))
        self.assertEqual(self.last_record()["decision"], "allow", read.stdout)
        self.assertEqual(self.last_record()["rules"], ["builtin-read-anything"])
        self.assertNotIn("fallback", self.last_record())
        self.assert_denied(self.hook("Bash", self.ws, command=RM_RF), "builtin-recursive-delete")

    def test_the_read_allow_is_not_added_when_a_layer_exists(self):
        """`Read` を通す allow は組み込みの既定だけのとき限り。config が 1 本でも在れば足さない。"""
        self.remove_common_rules()
        write(layer_path(self.ws, "rules"), json.dumps({"version": 1, "deny": []}))

        self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))

        self.assertNotIn("builtin-read-anything", self.last_record().get("rules", []))

    def test_a_broken_common_layer_falls_back_to_the_builtin_defaults_only(self):
        """共通レイヤーのルールが不正なら、組み込みの既定だけで判定する。`fallback` を残す。"""
        write(self.rules, BROKEN)

        generated = self.hook(
            "Write", self.ws, file_path=os.path.join(self.ws, "generated", "x.py")
        )

        self.assertNotEqual(self.decision(generated), "deny", generated.stdout)
        self.assertEqual(self.last_record()["fallback"], "builtin-rules")
        self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))
        self.assertEqual(self.last_record()["decision"], "allow")
        self.assert_denied(self.hook("Bash", self.ws, command=RM_RF))

    def test_a_broken_layer_is_empty_and_is_named_in_the_fallback(self):
        """config のレイヤーが不正なら空。共通レイヤーは効き、`fallback` に名前を残す。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        denied = self.hook("Write", self.lib, file_path=os.path.join(self.lib, ".env"))

        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record()["fallback"], "lib")

    def test_lint_says_nothing_about_missing_files(self):
        """`--lint` は、共通レイヤーと config の 3 本が無いことを不備として言わない。"""
        self.remove_common_rules()
        os.remove(self.risk)
        self.remove_own_layer()
        for kind in ("rules", "phases", "risk"):
            os.remove(layer_path(self.lib, kind))

        errors = self.problems("error")
        warns = [
            p for p in self.problems("warn") if p["where"] in ("(rules)", "(phases)", "(risk)")
        ]

        self.assertEqual(errors, [])
        self.assertEqual(warns, [])

    def test_lint_still_says_a_broken_common_layer(self):
        """不正な共通レイヤーは error。"""
        write(self.rules, BROKEN)

        errors = self.problems("error", where="(rules)")

        self.assertTrue(errors)

    def test_explain_shows_a_missing_layer_as_not_placed(self):
        """`--explain` は、置いていないレイヤーを「置いていない（無い = 空）」と言う。"""
        self.remove_common_rules()

        out = self.ccnavi("--explain").stdout

        self.assertIn("このレイヤーは置いていない", out)
        self.assertNotIn("読めない: ", out.split("■ rules 自身のレイヤー")[0])


class AbsentRiskTest(ConfigUnionHarness):
    def definition(self, project="lib"):
        conf, _ = settings.load(self.ws)
        return risk.layer_definition(conf, self.ws, project)

    def test_without_both_files_the_builtin_items_are_used(self):
        """共通レイヤーにも config にも risks.yml が無ければ、組み込みの 4 項目。"""
        os.remove(self.risk)
        os.remove(layer_path(self.lib, "risk"))

        definition, problems = self.definition()

        self.assertEqual(
            [f.id for f in definition.factors], ["big-diff", "many-files", "ci", "deletes"]
        )
        self.assertEqual(definition.fallback, "")
        self.assertEqual(problems, [])

    def test_a_missing_common_layer_counts_only_the_config_items(self):
        """共通レイヤーの risks.yml だけが無ければ、共通レイヤーは空。config の項目だけを数える。"""
        os.remove(self.risk)

        definition, problems = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["schema"])
        self.assertEqual(definition.levels, {"critical": 50})
        self.assertEqual(definition.fallback, "")
        self.assertEqual(problems, [])

    def test_a_missing_config_counts_only_the_common_items(self):
        """config の risks.yml だけが無ければ、共通レイヤーの項目だけを数える。"""
        os.remove(layer_path(self.lib, "risk"))

        definition, _ = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["big-diff"])
        self.assertEqual(definition.fallback, "")

    def test_a_broken_config_is_empty_and_named(self):
        """不正な config のレイヤーは空。共通レイヤーの項目だけを数え、`fallback` に名前を残す。"""
        write(layer_path(self.lib, "risk"), "version: 1\nfactors: [\n")

        definition, _ = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["big-diff"])
        self.assertEqual(definition.dropped, ["lib"])
        self.assertTrue(definition.fallback)

    def test_a_broken_common_layer_falls_back_to_the_builtin_items(self):
        """不正な共通レイヤーは組み込みの配点に戻り、レイヤーは足さない。"""
        write(self.risk, "version: 1\nfactors: [\n")

        definition, _ = self.definition()

        self.assertEqual(
            [f.id for f in definition.factors], ["big-diff", "many-files", "ci", "deletes"]
        )
        self.assertTrue(definition.fallback)


class MirrorIsNotReadInTheWorkspaceTest(ConfigUnionHarness):
    """ワークスペースの中では、プロジェクトのミラー（`.ccnavi/common/`）を共通レイヤーとして読まない。"""

    MIRROR_RULES = {
        "version": 1,
        "deny": [
            {
                "id": "mirror-only",
                "match": "Write|Edit",
                "glob": "*/mirror-target/*",
                "message": "from the mirror.",
            }
        ],
    }

    def place_mirror(self):
        mirror = os.path.join(self.lib, ".ccnavi", "common")
        write(os.path.join(mirror, "rules.yml"), json.dumps(self.MIRROR_RULES))
        write(
            os.path.join(mirror, "risks.yml"),
            COMMON_RISK + "  - {id: m, points: 1, lines_over: 0}\n",
        )
        write(os.path.join(mirror, "phases.yml"), "version: 1\nphases: [\n")

    def test_the_mirror_does_not_decide_in_the_workspace(self):
        """ミラーのルールは、プロジェクトへの書き込みの判定に入らない（共通レイヤーは 1 本）。"""
        self.place_mirror()

        result = self.hook(
            "Write", self.lib, file_path=os.path.join(self.lib, "mirror-target", "x.py")
        )

        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        self.assertNotIn("mirror-only", result.stdout)
        shown = json.loads(self.ccnavi("--explain", "--json").stdout)
        ids = [r["id"] for layer in shown["layers"] for r in layer["rules"]["deny"]]
        self.assertNotIn("mirror-only", ids)

    def test_the_mirror_risk_and_phases_are_not_read_either(self):
        """配点とフェーズ定義も同じ。不正なミラーの phases.yml があっても何も言わない。"""
        self.place_mirror()
        conf, _ = settings.load(self.ws)

        definition, _ = risk.layer_definition(conf, self.ws, "lib")

        self.assertEqual([f.id for f in definition.factors], ["big-diff", "schema"])
        paths = [p["detail"] for p in self.problems("error")]
        self.assertFalse([d for d in paths if "mirror" in d or "lib/.ccnavi/common" in d], paths)


class StandaloneCloneTest(ConfigUnionHarness):
    """プロジェクトを単体で clone したとき。

    そのプロジェクトがルートになる。`.ccnavi/common/`（ミラー）が共通レイヤー、`.ccnavi/config/` が
    自身のレイヤーとして読まれ、ワークスペースの中と同じ「共通 + 1 レイヤー」の和で判定される。
    """

    def setUp(self):
        super().setUp()
        # lib を単体の clone に見立てる。ミラーは共通レイヤーの写し。
        self.mirror = os.path.join(self.lib, ".ccnavi", "common")
        write(os.path.join(self.mirror, "rules.yml"), json.dumps(COMMON_RULES))
        write(os.path.join(self.mirror, "risks.yml"), COMMON_RISK)
        self.ws = self.lib
        self.state = os.path.join(self.lib, "logs", "state")
        self.log = os.path.join(self.lib, "logs", "decisions.jsonl")

    def test_the_mirror_is_the_common_layer_and_the_config_is_the_own_layer(self):
        """ミラーのルールは裸の id、config のルールは `self:` の id で当たる。"""
        env = self.hook("Write", self.lib, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(env, "credentials")
        self.assertEqual(self.hit_rules(), ["credentials"])
        self.assertEqual(self.last_record()["source"], "common")

        schema = self.hook("Write", self.lib, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_denied(schema, "self:schema")
        self.assertEqual(self.hit_rules(), ["self:schema"])
        self.assertEqual(self.last_record()["source"], "self")
        self.assertEqual(LIB_RULES["deny"][0]["id"], "schema")

    def test_the_sum_is_the_common_layer_plus_one_layer(self):
        """Bash も同じ和（共通 + 自身のレイヤー）。プロジェクトのレイヤーは無い。"""
        denied = self.hook("Bash", self.lib, command="terraform apply")
        self.assert_denied(denied, "terraform")
        denied = self.hook("Bash", self.lib, command="psql -c x")
        self.assert_denied(denied, "self:raw-psql")

    def test_the_risk_is_the_common_layer_plus_the_own_layer(self):
        """配点も共通 + 自身のレイヤーの和。境目の点は小さいほう。"""
        conf, _ = settings.load(self.lib)

        definition, problems = risk.layer_definition(conf, self.lib, "")

        self.assertEqual([f.id for f in definition.factors], ["big-diff", "schema"])
        self.assertEqual(risk.effective_levels(definition.levels)["critical"], 50)
        self.assertEqual([p for p in problems if p.severity != "info"], [])
        self.assertIn("critical", LIB_RISK)

    def test_the_mirror_script_resolves_from_the_clone_root(self):
        """ミラーの `script:` は `.ccnavi/common/scripts/` を指し、その clone のルートから解く。"""
        write(
            os.path.join(self.mirror, "risks.yml"),
            COMMON_RISK
            + "  - {id: counted, points: 5, script: .ccnavi/common/scripts/count.sh, message: m}\n",
        )
        write(os.path.join(self.mirror, "scripts", "count.sh"), "printf 1\n")
        conf, _ = settings.load(self.lib)

        definition, _ = risk.layer_definition(conf, self.lib, "")
        counted = definition.factor("counted")

        self.assertEqual(counted.home, self.lib)
        self.assertEqual(risk.script_problems(definition), [])

    def test_the_phases_come_from_the_config_only(self):
        """フェーズ定義は config の 1 本（`.ccnavi/config/phases.yml`）。"""
        sums = {s["name"]: s for s in json.loads(self.ccnavi("--explain", "--json").stdout)["sums"]}

        self.assertEqual(list(sums), ["self"])
        self.assertEqual(
            [t["id"] for t in sums["self"]["phases"]["types"]], ["design", "build", "release"]
        )
        self.assertEqual(
            sums["self"]["phases"]["path"],
            os.path.join(self.lib, ".ccnavi", "config", "phases.yml"),
        )

    def test_lint_reads_the_mirror_as_the_common_layer(self):
        """`--lint` もミラーを共通レイヤーとして読む。ミラーが不正なら error で名指しする。"""
        write(os.path.join(self.mirror, "rules.yml"), BROKEN)

        errors = self.problems("error", where="(rules)")

        self.assertTrue(errors)


class ExplainSumsTest(ConfigUnionHarness):
    """`--explain --json` の `sums[]`。拡張が足し算を自分で組まずに済むための合成結果。"""

    def sums(self):
        body = json.loads(self.ccnavi("--explain", "--json").stdout)
        return {s["name"]: s for s in body["sums"]}

    def test_every_sum_is_the_common_layer_plus_one_layer(self):
        sums = self.sums()

        self.assertEqual(sorted(sums), ["app", "lib", "self"])
        self.assertEqual(sums["lib"]["layers"], ["common", "lib"])
        for name, expected in (
            (
                "self",
                ["credentials", "terraform", "guard-approved", "self:generated", "self:dropdb"],
            ),
            ("lib", ["credentials", "terraform", "guard-approved", "lib:schema", "lib:raw-psql"]),
            ("app", ["credentials", "terraform", "guard-approved"]),
        ):
            with self.subTest(sum=name):
                self.assertEqual([r["id"] for r in sums[name]["rules"]["deny"]], expected)

    def test_a_sum_keeps_what_the_layers_overview_drops(self):
        """`layers[]` は重ねの途中経過なので、先のレイヤーと全欄が同じ定義は後ろから外れる。
        `sums[]` は「共通 + そのレイヤー」だけで重ねるので、外れない。"""
        same = {"version": 1, "deny": [COMMON_RULES["deny"][0]]}
        write(layer_path(self.ws, "rules"), json.dumps(same))
        write(layer_path(self.lib, "rules"), json.dumps(same))
        body = json.loads(self.ccnavi("--explain", "--json").stdout)
        sums = {s["name"]: s for s in body["sums"]}

        # どちらの和も共通レイヤーの定義だけ（全欄一致の重複は後ろを捨てる）。
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]],
            [r["id"] for r in COMMON_RULES["deny"]],
        )
        # lib のレイヤー自身の宣言は、他のレイヤーの定義に引かれない。
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]][:1],
            [r["id"] for r in sums["self"]["rules"]["deny"]][:1],
        )

    def test_the_risk_sum_carries_the_effective_levels_and_factors(self):
        sums = self.sums()

        self.assertEqual(sums["lib"]["risk"]["levels"], {"medium": 20, "high": 40, "critical": 50})
        self.assertEqual([f["id"] for f in sums["lib"]["risk"]["factors"]], ["big-diff", "schema"])
        self.assertEqual([f["source"] for f in sums["lib"]["risk"]["factors"]], ["common", "lib"])
        self.assertEqual([f["id"] for f in sums["app"]["risk"]["factors"]], ["big-diff"])

    def test_the_phases_are_the_one_file_that_the_layer_names(self):
        sums = self.sums()

        self.assertEqual([t["id"] for t in sums["self"]["phases"]["types"]], ["docs"])
        self.assertEqual(
            [t["id"] for t in sums["lib"]["phases"]["types"]], ["design", "build", "release"]
        )
        self.assertEqual(sums["app"]["phases"]["types"], [])
        self.assertEqual(sums["app"]["phases"]["unreadable"], "")
        self.assertEqual(sums["lib"]["phases"]["order"], "sequential")

    def test_a_missing_or_broken_layer_is_said_in_its_own_sum(self):
        write(layer_path(self.lib, "rules"), BROKEN)

        sums = self.sums()

        self.assertTrue(sums["lib"]["rules"]["unreadable"])
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]],
            [r["id"] for r in COMMON_RULES["deny"]],
        )
        self.assertTrue(sums["app"]["rules"]["missing"])
        self.assertFalse(sums["self"]["rules"]["missing"])


if __name__ == "__main__":
    unittest.main()
