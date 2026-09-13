"""設定 3 本の和の受入テスト。phases と risk の合成の面（設計 §25.4.1、§25.4.2）。

fixture は tests/test_config_union.py の ConfigUnionHarness を継ぐ。
共通層に `design`、自身の層に `docs`、lib の層に `build` / `release` がある。
risk は共通層に `big-diff`、lib の層に `schema` と `levels: {critical: 50}` がある。

どの層を足すかは親の承認済みチケットの `project:` で決まる。lib 向けの提案は
`wip/lib/tickets/` に置き、ワークスペース向けは `wip/tickets/` に置く。

実装はまだ無い。このテストは実装フェーズが緑にする。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.test_config_union import (
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
    "  - {id: common-counted, points: 5, script: .claude/ccnavi/count.sh, message: 共通で数えた}\n"
)
COMMON_MISSING_SCRIPT_FACTOR = (
    "  - {id: gone, points: 5, script: .claude/ccnavi/gone.sh, message: 無い}\n"
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
    """phases の合成（§25.4.1）。承認と --lint で見る。"""

    def approved_copy(self, name):
        return os.path.join(self.approved, name + ".md")

    def phase_problems(self, severity, layer=""):
        """phases の Problem。`layer` を渡すと、その層のものだけ。

        `detail` の部分一致だけで見ると、どの層の話かが決まらない。出どころで先に絞る。
        """
        where = self.project_where(layer) if layer else ""
        return [p for p in self.problems(severity, where=where) if "phases" in p["where"]]

    def test_plan_can_name_a_type_from_the_project_layer(self):
        """§25.4.1: `plan:` がプロジェクトの層の種類を指せる。層から共通層の種類も指せる。"""
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
        """§25.4.1: 空の `project:` は自身の層。プロジェクトの層の種類は他から指せない。"""
        self.propose("i0002", ticket_text("i0002", plan=["docs"], allow=("docs/*",)))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        self.propose("i0003", ticket_text("i0003", plan=["build"], allow=("src/*",)))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("build", refused.stderr)
        self.assertFalse(os.path.exists(self.approved_copy("i0003")))

        os.remove(os.path.join(self.ws, "wip", "tickets", "todo", "i0003.md"))
        self.propose(
            "i0004",
            ticket_text("i0004", project="lib", plan=["docs"], allow=("docs/*",)),
            project="lib",
        )
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(os.path.exists(self.approved_copy("i0004")))

    def test_conflicting_id_across_layers_is_an_error_and_empties_the_layer(self):
        """§25.4.1: 同 id で中身が違えば --lint error。その層は空として扱い、承認が止まる。"""
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
        """§25.4.1: `title` の重なりも層をまたいで error。"""
        write_layer(self.lib, phases=LIB_PHASES_TITLE_OVERLAP)

        errors = self.phase_problems("error", "lib")
        self.assertTrue(any("設計" in p["detail"] for p in errors), errors)

    def test_identical_type_in_a_later_layer_is_dropped_with_info(self):
        """§25.4.1: 全欄一致は重複とみなして後ろを捨て、info で言う。承認は通る。"""
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
        """§25.4.1: `scope` は作業ツリーのルートからの相対のまま。"""
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
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("超えている", refused.stderr)
        self.assertFalse(os.path.exists(self.approved_copy("i0001-01")))

        self.propose(
            "i0001-01",
            ticket_text("i0001-01", project="lib", parent="i0001", phase=1, allow=("src/a/*",)),
            project="lib",
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(self.approved_copy("i0001-01")))

    def test_broken_project_phases_is_an_error_and_the_layer_is_empty(self):
        """§25.2: 壊れた層の phases は空 + --lint error。共通層の種類は使える。"""
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
    """risk の合成（§25.4.2）。lib の子を 1 本閉じて点と記録を見る。"""

    def setUp(self):
        super().setUp()
        write_layer(self.lib, phases=LIB_PHASES_WORK)
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "--quiet", "-m", "phases")

    def risk_problems(self, severity, layer=""):
        """risk の Problem。`layer` を渡すと、その層のものだけ（`detail` の前に出どころで絞る）。"""
        where = self.project_where(layer) if layer else ""
        return [p for p in self.problems(severity, where=where) if "risk" in p["where"]]

    def one_child(self):
        """lib の親と子を承認し、子の作業ツリーを lib から切って着手する。"""
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
        tree = self.worktree(self.lib, "i0001-01")
        started = self.ccnavi("ticket", "start", "i0001-01")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree

    def judge_record(self):
        path = os.path.join(self.approved, "phases", "i0001", "i0001-01.judge.json")
        return json.loads(read(path))

    def commit(self, tree, rel, text):
        write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", rel)

    def record(self):
        path = os.path.join(self.approved, "phases", "i0001", "i0001-01.risk.json")
        return json.loads(read(path))

    def test_factors_concatenate_and_levels_take_the_minimum(self):
        """§25.4.2: factors は連結、levels はキーごとに min。記録の hit に source。"""
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
        """§25.4.2: 書かれていない鍵は参加しない。lib が書かない medium / high は共通層の値。"""
        tree = self.one_child()
        self.commit(tree, "src/a.py", "\n".join(str(i) for i in range(10)) + "\n")

        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertEqual(self.record()["points"], 25)
        # 25 点は medium 20 以上、high 40 未満。
        self.assertIn("(MEDIUM)", closed.stdout)

    def test_same_factor_id_across_layers_is_an_error_and_empties_the_layer(self):
        """§25.4.2: 同 id は --lint error。その層は空で、閉じるときの出力が言う。"""
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
        """§25.4.2: 全欄一致なら重複として後ろを捨て、info で言う。"""
        copied = COMMON_RISK.replace("levels: {medium: 20, high: 40, critical: 70}\n", "")
        write_layer(self.lib, risk=copied)

        self.assertEqual(self.risk_problems("error"), [])
        infos = self.risk_problems("info", "lib")
        self.assertTrue(any("big-diff" in p["detail"] for p in infos), infos)

    def test_inverted_levels_after_merge_is_an_error(self):
        """§25.4.2: 合成後に medium <= high <= critical でなければ error。"""
        write_layer(self.lib, risk="version: 1\nlevels: {high: 10}\n")

        errors = self.risk_problems("error", "lib")
        self.assertTrue(any("high" in p["detail"] for p in errors), errors)

    def test_script_outside_its_layer_is_an_error(self):
        """§25.4.2: 共通層は `.claude/...`、層は `.ccnavi/scripts/...` だけ。互いに指せない。"""
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n"
            "  - {id: x, points: 5, script: .claude/scripts/x.sh, message: x}\n",
        )
        errors = self.risk_problems("error", "lib")
        self.assertTrue(any(".claude/scripts/x.sh" in p["detail"] for p in errors), errors)

        write_layer(self.lib, risk=None)
        self.risk = write(
            os.path.join(self.ws, ".claude", "ccnavi", "risk.yml"),
            "version: 1\nfactors:\n"
            "  - {id: y, points: 5, script: .ccnavi/scripts/y.sh, message: y}\n",
        )
        errors = self.risk_problems("error")
        self.assertTrue(any(".ccnavi/scripts/y.sh" in p["detail"] for p in errors), errors)

    def test_missing_script_in_the_project_root_is_an_error(self):
        """§25.4.2: 指す先が git プロジェクトルートに無ければ --lint error。"""
        write_layer(
            self.lib,
            risk="version: 1\nfactors:\n"
            "  - {id: gone, points: 5, script: .ccnavi/scripts/gone.sh, message: x}\n",
        )
        errors = self.risk_problems("error", "lib")
        self.assertTrue(any("gone.sh" in p["detail"] for p in errors), errors)

    def test_script_in_the_project_layer_runs_from_the_project_root(self):
        """§25.4.2: 層の `script:` はそのプロジェクトの git プロジェクトルートから解く。"""
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
        """§25.4.2: 合成を通しても、共通層の `script:` はワークスペースルートから解いて走る。"""
        write(
            os.path.join(self.ws, ".claude", "ccnavi", "count.sh"),
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
        """§25.4.2: 共通層でも、指す先がワークスペースルートに無ければ --lint error。"""
        write(self.risk, COMMON_RISK + COMMON_MISSING_SCRIPT_FACTOR)

        errors = self.risk_problems("error")
        self.assertTrue(any("gone.sh" in p["detail"] for p in errors), errors)

    def test_broken_project_risk_is_empty_and_named_in_the_fallback(self):
        """§25.2 / §25.4.2: 壊れた risk の層は空 + `fallback` に層の名前 + --lint error。"""
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
        """§25.9: `<子>.judge.json` の各項目に、その項目の層の `source`。"""
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
        """§25.9: 種類を根拠に置く印（`review: none` の skipped）には、その種類の層。"""
        tree = self.one_child()
        self.commit(tree, "src/a.py", "1\n")
        closed = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)

        said = self.hook("Bash", self.parent_tree, event="PostToolUse", command="ls")
        self.assertIn("省略", self.reason(said))
        mark = json.loads(read(os.path.join(self.approved, "phases", "i0001", "1.skipped")))
        self.assertEqual(mark.get("source"), "lib", mark)


if __name__ == "__main__":
    unittest.main()
