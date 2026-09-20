"""設定 3 本の和の受入テスト。phases と risk の合成の面（設計 §11.4.1、§11.4.2）。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。
共通層に `design`、自身の層に `docs`、lib の層に `build` / `release` がある。
risk は共通層に `big-diff`、lib の層に `schema` と `levels: {critical: 50}` がある。

どの層を足すかは親の承認済みチケットの `project:` で決まる。lib 向けの提案は
`wip/lib/proposals/` に置き、ワークスペース向けは `wip/proposals/` に置く。

実装はまだ無い。このテストは実装フェーズが緑にする。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.config.test_config_union import (
    COMMON_PHASES,
    COMMON_RISK,
    LIB_PHASES,
    LIB_RISK,
    ConfigUnionHarness,
    git,
    layer_path,
    read,
    ticket_text,
    write,
    write_layer,
)

# 共通層に足す項目。どれも `factors:` の続きなので、COMMON_RISK の後ろに繋げる。
COMMON_SCRIPT_FACTOR = (
    "  - {id: common-counted, points: 5, script: .ccnavi/common/scripts/count.sh,"
    " message: 共通で数えた}\n"
)
COMMON_MISSING_SCRIPT_FACTOR = (
    "  - {id: gone, points: 5, script: .ccnavi/common/scripts/gone.sh, message: 無い}\n"
)
COMMON_JUDGE_FACTOR = "  - {id: outward, points: 10, judge: 外に出す変更か, message: 外向き}\n"

# lib の層に足す定性の項目。judge.json の `source` が層ごとに分かれることを見る。
LIB_JUDGE_FACTOR = (
    "  - {id: untested, points: 10, judge: テストの無い変更か, message: テスト無し}\n"
)

# 同 id で中身が違う（title が違う）design。
LIB_PHASES_CONFLICT = (
    LIB_PHASES
    + """\
  design:
    kind: work
    title: 設計（lib）
    review: mr
    scope: ["design/*"]
"""
)

# 別の id で title だけ共通層の design と重なる。
LIB_PHASES_TITLE_OVERLAP = """\
version: 1
phases:
  build:
    kind: work
    title: 設計
    review: none
    scope: ["src/*"]
"""

# 共通層の design と全欄が同じ定義を持つ lib の層。
LIB_PHASES_COPIED = LIB_PHASES + COMMON_PHASES.split("phases:\n", 1)[1]

# 閉じるときの点を見るための、範囲の上限が無くレビュー不要の種類。
LIB_PHASES_WORK = """\
version: 1
phases:
  work:
    kind: work
    title: 作業
    review: none
    scope: inherit
"""


class PhaseUnionTest(ConfigUnionHarness):
    """phases の合成（§11.4.1）。承認と --lint で見る。"""

    def phase_problems(self, severity, layer=""):
        """phases の Problem。`layer` を渡すと、その層のものだけ。

        `detail` の部分一致だけで見ると、どの層の話かが決まらない。出どころで先に絞る。
        """
        where = self.project_where(layer) if layer else ""
        return [p for p in self.problems(severity, where=where) if "phases" in p["where"]]

    def test_plan_can_name_a_type_from_the_project_layer(self):
        """§11.4.1: `plan:` がプロジェクトの層の種類を指せる。層から共通層の種類も指せる。"""
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["design", "release"], allow=("src/*",)),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(self.approved_copy("i0001")))
        self.assertIn("リリース", self.ccnavi("--explain").stdout)

    def test_the_layer_follows_the_project_of_the_parent(self):
        """§11.4.1: 空の `project:` は自身の層。プロジェクトの層の種類は他から指せない。"""
        self.propose("i0002", ticket_text("i0002", plan=["docs"], allow=("docs/*",)))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        self.propose("i0003", ticket_text("i0003", plan=["build"], allow=("src/*",)))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("build", refused.stderr)
        self.assertFalse(os.path.exists(self.approved_copy("i0003")))

        os.remove(os.path.join(self.ws, "wip", "proposals", "todo", "i0003.md"))
        self.propose(
            "i0004",
            ticket_text("i0004", project="lib", plan=["docs"], allow=("docs/*",)),
            project="lib",
        )
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(os.path.exists(self.approved_copy("i0004")))

    def test_conflicting_id_across_layers_is_an_error_and_empties_the_layer(self):
        """§11.4.1: 同 id で中身が違えば --lint error。その層は空として扱い、承認が止まる。"""
        write_layer(self.lib, phases=LIB_PHASES_CONFLICT)

        errors = self.phase_problems("error", "lib")
        self.assertTrue(any("design" in p["detail"] for p in errors), errors)

        # lib の層が空なので build も無い。共通層の design だけで進む。
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["design", "build"], allow=("src/*",)),
            project="lib",
        )
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("build", refused.stderr)
        self.assertFalse(os.path.exists(self.approved_copy("i0001")))

    def test_title_overlap_across_layers_is_an_error(self):
        """§11.4.1: `title` の重なりも層をまたいで error。"""
        write_layer(self.lib, phases=LIB_PHASES_TITLE_OVERLAP)

        errors = self.phase_problems("error", "lib")
        self.assertTrue(any("設計" in p["detail"] for p in errors), errors)

    def test_identical_type_in_a_later_layer_is_dropped_with_info(self):
        """§11.4.1: 全欄一致は重複とみなして後ろを捨て、info で言う。承認は通る。"""
        write_layer(self.lib, phases=LIB_PHASES_COPIED)

        self.assertEqual(self.phase_problems("error"), [])
        infos = self.phase_problems("info", "lib")
        self.assertTrue(any("design" in p["detail"] for p in infos), infos)

        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["design", "build"], allow=("src/*",)),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_scope_stays_relative_to_the_worktree(self):
        """§11.4.1: `scope` はワークツリーのルートからの相対のまま。

        種類の超過は承認を拒まず、承認画面の「判定で止まるもの」に出る（設計 approve-carry
        §3.1）。相対で読めていれば、`src/a/*` は種類 build の `src/*` に入り、`docs/*` だけが出る。
        """
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["build"], allow=("src/*", "docs/*")),
            project="lib",
        )
        self.propose(
            "i0001-01",
            ticket_text("i0001-01", project="lib", parent="i0001", phase=1, allow=("docs/*",)),
            project="lib",
        )
        self.propose(
            "i0001-02",
            ticket_text("i0001-02", project="lib", parent="i0001", phase=1, allow=("src/a/*",)),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertIn("判定で止まるもの", approved.stdout)
        self.assertIn("`docs/*` は種類", approved.stdout)
        self.assertNotIn("`src/a/*` は種類", approved.stdout)
        self.assertTrue(os.path.exists(self.approved_copy("i0001-01")))
        self.assertTrue(os.path.exists(self.approved_copy("i0001-02")))

    def test_broken_project_phases_is_an_error_and_the_layer_is_empty(self):
        """§11.2: 壊れた層の phases は空 + --lint error。共通層の種類は使える。"""
        write(layer_path(self.lib, "phases"), "version: 1\nphases: [\n")

        self.assertTrue(self.phase_problems("error", "lib"))
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["design"], allow=("wip/*",)),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)


class RiskUnionTest(ConfigUnionHarness):
    """risk の合成（§11.4.2）。lib の子を 1 本閉じて点と記録を見る。"""

    def setUp(self):
        super().setUp()
        write_layer(self.lib, phases=LIB_PHASES_WORK)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "phases")

    def risk_problems(self, severity, layer=""):
        """risk の Problem。`layer` を渡すと、その層のものだけ（`detail` の前に出どころで絞る）。"""
        where = self.project_where(layer) if layer else ""
        return [p for p in self.problems(severity, where=where) if "risk" in p["where"]]

    def start_parent(self, name="i0001"):
        """親を着手する（済んでいれば何もしない）。

        子の着手は親が着手済みであることを前提にする（REQ-TKT-48）。親を飛ばしたまま
        子を進められたころの手順をそのまま残すと、最初の子の着手で止まる。
        """
        started = self.ccnavi("ticket", "start", name)
        if started.returncode != 0:
            self.assertIn("着手済み", started.stderr, started.stdout + started.stderr)

    def one_child(self):
        """lib の親と子を承認し、子のワークツリーを lib から切って着手する。"""
        scope = ("src/*", "schema/*", "wip/*")
        self.propose(
            "i0001",
            ticket_text("i0001", project="lib", plan=["work"], allow=scope),
            project="lib",
        )
        self.propose(
            "i0001-01",
            ticket_text("i0001-01", project="lib", parent="i0001", phase=1, allow=scope),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.parent_tree = self.worktree(self.lib, "i0001")
        self.start_parent()
        tree = self.worktree(self.lib, "i0001-01")
        started = self.ccnavi("ticket", "start", "i0001-01")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree

    def judge_record(self):
        path = self.approved_path("phases", "i0001", "i0001-01.judge.json")
        return json.loads(read(path))

    def commit(self, tree, rel, text):
        write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", rel)

    def record(self):
        path = self.approved_path("phases", "i0001", "i0001-01.risk.json")
        return json.loads(read(path))

    def test_factors_concatenate_and_levels_take_the_minimum(self):
        """§11.4.2: factors は連結、levels はキーごとに min。記録の hit に source。"""
        tree = self.one_child()
        self.commit(tree, "schema/x.sql", "\n".join(str(i) for i in range(10)) + "\n")

        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        record = self.record()
        self.assertEqual(record["points"], 55)
        by_id = {h["id"]: h for h in record["hits"]}
        self.assertEqual(sorted(by_id), ["big-diff", "schema"])
        self.assertEqual(by_id["big-diff"].get("source"), "common")
        self.assertEqual(by_id["schema"].get("source"), "lib")
        # critical は min(70, 50) = 50。55 点は CRITICAL。共通層だけなら HIGH。
        self.assertIn("(CRITICAL)", closed.stdout)

    def test_unwritten_level_keys_do_not_take_part(self):
        """§11.4.2: 書かれていない鍵は参加しない。lib が書かない medium / high は共通層の値。"""
        tree = self.one_child()
        self.commit(tree, "src/a.py", "\n".join(str(i) for i in range(10)) + "\n")

        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(self.record()["points"], 25)
        # 25 点は medium 20 以上、high 40 未満。
        self.assertIn("(MEDIUM)", closed.stdout)

    def test_same_factor_id_across_layers_is_an_error_and_empties_the_layer(self):
        """§11.4.2: 同 id は --lint error。その層は空で、閉じるときの出力が言う。"""
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n  - {id: big-diff, points: 5, files_over: 1, message: x}\n",
        )
        errors = self.risk_problems("error", "lib")
        self.assertTrue(any("big-diff" in p["detail"] for p in errors), errors)

        tree = self.one_child()
        self.commit(tree, "schema/x.sql", "\n".join(str(i) for i in range(10)) + "\n")
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual([h["id"] for h in self.record()["hits"]], ["big-diff"])
        self.assertEqual(self.record()["points"], 25)
        self.assertIn("lib", closed.stdout + closed.stderr)

    def test_identical_factor_in_a_later_layer_is_dropped_with_info(self):
        """§11.4.2: 全欄一致なら重複として後ろを捨て、info で言う。"""
        copied = COMMON_RISK.replace("levels: {medium: 20, high: 40, critical: 70}\n", "")
        write_layer(self.lib, risk=copied)

        self.assertEqual(self.risk_problems("error"), [])
        infos = self.risk_problems("info", "lib")
        self.assertTrue(any("big-diff" in p["detail"] for p in infos), infos)

    def test_inverted_levels_after_merge_is_an_error(self):
        """§11.4.2: 合成後に medium <= high <= critical でなければ error。"""
        write_layer(self.lib, risk="version: 1\nlevels: {high: 10}\n")

        errors = self.risk_problems("error", "lib")
        self.assertTrue(any("high" in p["detail"] for p in errors), errors)

    def test_script_outside_its_layer_is_an_error(self):
        """§11.4.2: 共通層と各層は、互いの scripts/ を指せない。"""
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n"
            "  - {id: x, points: 5, script: .ccnavi/common/scripts/x.sh, message: x}\n",
        )
        errors = self.risk_problems("error", "lib")
        self.assertTrue(any(".ccnavi/common/scripts/x.sh" in p["detail"] for p in errors), errors)

        write_layer(self.lib, risk=None)
        self.risk = write(
            os.path.join(self.ws, ".ccnavi", "common", "risks.yml"),
            "version: 1\nfactors:\n"
            "  - {id: y, points: 5, script: .ccnavi/scripts/y.sh, message: y}\n",
        )
        errors = self.risk_problems("error")
        self.assertTrue(any(".ccnavi/scripts/y.sh" in p["detail"] for p in errors), errors)

    def test_missing_script_in_the_project_root_is_an_error(self):
        """§11.4.2: 指す先が git プロジェクトルートに無ければ --lint error。"""
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n"
            "  - {id: gone, points: 5, script: .ccnavi/scripts/gone.sh, message: x}\n",
        )
        errors = self.risk_problems("error", "lib")
        self.assertTrue(any("gone.sh" in p["detail"] for p in errors), errors)

    def test_script_in_the_project_layer_runs_from_the_project_root(self):
        """§11.4.2: 層の `script:` はそのプロジェクトの git プロジェクトルートから解く。"""
        write(
            os.path.join(self.lib, ".ccnavi", "scripts", "count.sh"),
            'printf \'{"points": 30, "message": "%s"}\' "$CCNAVI_TICKET"\n',
        )
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n"
            "  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n",
        )
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "script")
        self.assertEqual(self.risk_problems("error"), [])

        self.one_child()
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        by_id = {h["id"]: h for h in self.record()["hits"]}
        self.assertEqual(by_id["counted"]["points"], 30)
        self.assertIn("i0001-01", by_id["counted"]["detail"])
        self.assertEqual(by_id["counted"].get("source"), "lib")

    def test_script_in_the_common_layer_runs_through_the_merge(self):
        """§11.4.2: 合成を通しても、共通層の `script:` はワークスペースルートから解いて走る。"""
        write(
            os.path.join(self.ws, ".ccnavi", "common", "scripts", "count.sh"),
            'printf \'{"points": 7, "message": "%s"}\' "$CCNAVI_TICKET"\n',
        )
        write(self.risk, COMMON_RISK + COMMON_SCRIPT_FACTOR)
        self.assertEqual(self.risk_problems("error"), [])

        self.one_child()
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        by_id = {h["id"]: h for h in self.record()["hits"]}
        self.assertEqual(by_id["common-counted"]["points"], 7)
        self.assertIn("i0001-01", by_id["common-counted"]["detail"])
        self.assertEqual(by_id["common-counted"].get("source"), "common")

    def test_missing_script_in_the_common_layer_is_an_error(self):
        """§11.4.2: 共通層でも、指す先がワークスペースルートに無ければ --lint error。"""
        write(self.risk, COMMON_RISK + COMMON_MISSING_SCRIPT_FACTOR)

        errors = self.risk_problems("error")
        self.assertTrue(any("gone.sh" in p["detail"] for p in errors), errors)

    def test_broken_project_risk_is_empty_and_named_in_the_fallback(self):
        """§11.2 / §11.4.2: 壊れた risk の層は空 + `fallback` に層の名前 + --lint error。"""
        write(layer_path(self.lib, "risk"), "version: 1\nfactors: [\n")

        errors = self.risk_problems("error", "lib")
        self.assertTrue(errors, self.problems("error"))

        tree = self.one_child()
        self.commit(tree, "schema/x.sql", "\n".join(str(i) for i in range(10)) + "\n")
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        record = self.record()
        # 共通層の factors だけで点が付く。lib の schema は参加せず、levels も共通層のまま。
        self.assertEqual([h["id"] for h in record["hits"]], ["big-diff"])
        self.assertEqual(record["points"], 25)
        self.assertEqual(record.get("fallback"), "lib", record)
        self.assertIn("lib", closed.stdout + closed.stderr)

    def test_judge_record_names_the_layer_of_each_item(self):
        """§11.9: `<子>.judge.json` の各項目に、その項目の層の `source`。"""
        write(self.risk, COMMON_RISK + COMMON_JUDGE_FACTOR)
        write_layer(self.lib, risk=LIB_RISK + LIB_JUDGE_FACTOR)

        tree = self.one_child()
        self.commit(tree, "src/a.py", "1\n")
        refused = self.ccnavi("ticket", "done", "i0001-01")
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        for factor in ("outward", "untested"):
            judged = self.ccnavi("ticket", "judge", "i0001-01", factor, "yes", "--reason", "そう")
            self.assertEqual(judged.returncode, 0, judged.stdout + judged.stderr)

        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        record = self.judge_record()
        self.assertEqual(sorted(record), ["outward", "untested"])
        self.assertEqual(record["outward"].get("source"), "common", record)
        self.assertEqual(record["untested"].get("source"), "lib", record)

    def test_the_mark_that_rests_on_a_type_names_its_layer(self):
        """§11.9: 種類を根拠に置くマーカー（`review: none` の skipped）には、その種類の層。"""
        tree = self.one_child()
        self.commit(tree, "src/a.py", "1\n")
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)

        said = self.hook("Bash", self.parent_tree, event="PostToolUse", command="ls")
        self.assertIn("省略", self.reason(said))
        mark = json.loads(read(self.approved_path("phases", "i0001", "1.skipped")))
        self.assertEqual(mark.get("source"), "lib", mark)


class LayerPlaceFlagsAreDiagnosisOnlyTest(RiskUnionTest):
    """層を探す先を動かすフラグも、診断の外では効かない（ADR-0067、issue #65）。

    `--projects` と `--project-home` は、共通層の中身を差し替えるのと結果が同じ。
    外すとプロジェクトの層がまるごと消えるので、その層が足していた配点も
    フェーズの種類も落ちる。`.ccnavi/scripts/ccnavi-ticket.sh` は引数を素通しするので、
    この形はエージェントが Bash で打てる。

    土台の子は共通層の `big-diff`（25）と lib の `schema`（30）で 55 点、
    lib の critical は 50 なので CRITICAL。`review: mr` のフェーズなので
    レビュー待ちへ動く。lib の層が消えると 25 点の MEDIUM になり、
    レビューを飛ばして閉じられる。
    """

    def assert_the_lib_layer_still_counted(self, closed):
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        record = self.record()
        self.assertEqual(record["points"], 55, "lib の層が落ちた")
        self.assertIn("schema", {h["id"] for h in record["hits"]})
        self.assertIn("(CRITICAL)", closed.stdout)
        self.assertTrue(
            os.path.exists(os.path.join(self.lib, "wip", "proposals", "review", "i0001-01.md")),
            "レビュー待ちへ動いていない",
        )

    def test_a_project_home_flag_on_ticket_done_does_not_drop_the_layer(self):
        tree = self.one_child()
        self.commit(tree, "schema/x.sql", "\n".join(str(i) for i in range(10)) + "\n")

        closed = self.ccnavi("ticket", "done", "i0001-01", "--project-home", ".nothere")

        self.assertIn("--project-home は診断", closed.stderr, "落としたことを言っていない")
        self.assert_the_lib_layer_still_counted(closed)

    def test_a_projects_flag_on_ticket_done_does_not_drop_the_layer(self):
        tree = self.one_child()
        self.commit(tree, "schema/x.sql", "\n".join(str(i) for i in range(10)) + "\n")

        closed = self.ccnavi("ticket", "done", "i0001-01", "--projects", os.path.join(self.ws, "x"))

        self.assertIn("--projects は診断", closed.stderr, "落としたことを言っていない")
        self.assert_the_lib_layer_still_counted(closed)


if __name__ == "__main__":
    unittest.main()
