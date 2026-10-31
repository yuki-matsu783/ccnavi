"""作業・レビュー計画のテンプレート（読む側）の受入テスト。

見るのは次のとおり。

1. 読み込み: 書式、未知の欄の warn、読んで書いても中身が変わらない往復、フローと同じ読み手の保護
   （リンク・ハードリンク・大きさ・別名・UTF-8）
2. 2 層の統合結果: 全欄が同じなら info でレイヤーの側を破棄する、中身が違えば error で
   レイヤーの側だけを使わない、壊れた共通は空にしてレイヤーを使う
3. 検査: 順序（終端・延期の引き受け手・`after` の範囲）は error、型 id・kind の食い違いは warn、
   `flows` の添字とフローの形
4. `ccnavi --templates` の一覧・検索・表示・`--json`。答えの形は
   tests/fixtures/templates-json.json で固定する
5. `ccnavi --lint` への出方
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest

import yaml

from ccnavi.tickets.proposal import plan_templates
from tests import ROOT, common_path, config_path, requires_symlink
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import write
from tests.ticketsample import PHASES

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "templates-json.json")

FLOW = """\
    id: wf-impl
    name: 実装の手順
    version: 1.0.0
    nodes:
      - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
      - {id: p, type: prompt, name: 実装, position: {x: 1, y: 0}, data: {prompt: 先にテスト}}
      - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
    connections:
      - {id: c1, from: s, to: p, fromPort: output, toPort: input}
      - {id: c2, from: p, to: e, fromPort: output, toPort: input}
"""

FEATURE = (
    """\
name: feature
description: 機能を設計から実装まで進める
when: 新しい機能を足すとき
plan:
  - design
  - {type: implement, after: [1]}
feedback:
  - implement-feedback
flows:
  2:
"""
    + FLOW
)

RESEARCH = """\
name: research
description: 調べてから設計する
plan:
  - research
  - {type: design, after: [1]}
"""


def template(name: str, plan: str, description: str = "試し", extra: str = "") -> str:
    """`plan` は `plan:` の下の行（2 字下げ済み）。"""
    return f"name: {name}\ndescription: {description}\n{extra}plan:\n{plan}"


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-templates-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "phases"), PHASES)

    def common(self, name: str, text: str) -> str:
        return write(self.path(plan_templates.LAYER_COMMON, name), text)

    def own(self, name: str, text: str) -> str:
        return write(self.path("self", name), text)

    def path(self, layer: str, name: str) -> str:
        if layer == "common":
            base = os.path.dirname(common_path(self.root, "rules"))
        elif layer == "self":
            base = os.path.dirname(config_path(self.root, "rules"))
        else:
            base = os.path.join(self.root, "projects", layer, ".ccnavi", "config", "project")
            os.makedirs(os.path.join(self.root, "projects", layer, ".git"), exist_ok=True)
        return os.path.join(base, "templates", name + ".yml")

    def ccnavi(self, *args: str):
        return run_ccnavi(["--root", self.root, *args])

    def answer(self, *args: str) -> dict:
        result = self.ccnavi("--templates", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def entry(self, layer: str, name: str, **opts) -> dict:
        for row in self.answer(**opts)["templates"] if opts else self.answer()["templates"]:
            if row["name"] == name and row["layer"] == layer:
                return row
        self.fail(f"{layer}/{name} が答えに無い")

    def problems(self, row: dict, severity: str) -> list[str]:
        return [p["detail"] for p in row["problems"] if p["severity"] == severity]


class ReadTest(unittest.TestCase):
    def test_a_minimal_template_is_read(self):
        t, notes = plan_templates.parse_template(RESEARCH.encode(), "research")
        self.assertEqual(notes, [])
        self.assertEqual([i.type for i in t.plan], ["research", "design"])
        self.assertEqual(t.plan[1].after, [1])
        self.assertEqual(t.feedback, [])
        self.assertEqual(t.flows, {})

    def test_unknown_fields_are_warned_and_kept(self):
        text = RESEARCH + "owner: someone\n"
        t, notes = plan_templates.parse_template(text.encode(), "research")
        self.assertEqual([(p.severity, "owner" in p.detail) for p in notes], [("warn", True)])
        self.assertEqual(t.extra, {"owner": "someone"})
        self.assertIn("owner: someone", plan_templates.dump(t))

    def test_the_name_must_match_the_file(self):
        t, notes = plan_templates.parse_template(RESEARCH.encode(), "other")
        self.assertIsNone(t)
        self.assertIn("ファイル名", notes[0].detail)

    def test_required_fields(self):
        for text, needle in (
            ("name: a\nplan: [x]\n", "description"),
            ("name: a\ndescription: d\n", "`plan` がありません"),
            ("name: a\ndescription: d\nplan: []\n", "`plan` が空"),
            ("name: a\ndescription: d\nplan: [x]\nflows: [1]\n", "マッピング"),
            ("name: a\ndescription: d\nplan: [x]\nflows: {a: {}}\n", "添字"),
            ("name: a\ndescription: d\nplan: [{type: x, after: 1}]\n", "after"),
            ("name: a\ndescription: d\nplan: [{type: x, after: [true]}]\n", "after"),
            ("- a\n", "マッピング"),
        ):
            t, notes = plan_templates.parse_template(text.encode(), "a")
            self.assertIsNone(t, text)
            self.assertTrue(any(needle in p.detail for p in notes), (text, notes))

    def test_reading_then_writing_keeps_the_content(self):
        t, notes = plan_templates.parse_template((FEATURE + "owner: x\n").encode(), "feature")
        self.assertFalse([p for p in notes if p.severity == "error"])
        text = plan_templates.dump(t)
        again, _ = plan_templates.parse_template(text.encode(), "feature")
        self.assertEqual(again, t)
        self.assertEqual(plan_templates.dump(again), text)
        # 同じ中身は、書き方が違っても同じ書き戻しになる。
        loose = FEATURE.replace("  - design\n", "  - {type: design}\n")
        other, _ = plan_templates.parse_template(loose.encode(), "feature")
        same, _ = plan_templates.parse_template(FEATURE.encode(), "feature")
        self.assertEqual(plan_templates.dump(other), plan_templates.dump(same))

    PROMPTS = (
        "一行目\n二行目\n",
        "一行目\n二行目",
        "段落\n\n次の段落\n",
        "日本語: コロン\n- ハイフン\n# 見出し\n'q' \"d\"\n",
        # literal にできない: 行末の空白、先頭の空白・改行、末尾の空行、タブ、制御文字、復帰。
        "行末の空白 \n次\n",
        "  先頭の空白\n次\n",
        "\n先頭の改行\n",
        "末尾の空行\n\n",
        "tab\there\n次\n",
        "esc\x1b\n次\n",
        "a\r\nb\n",
        "幅ゼロ\u200b\n次\n",
        "改行なし",
    )

    def test_multiline_strings_are_written_as_literal_blocks_and_round_trip(self):
        for prompt in self.PROMPTS:
            with self.subTest(prompt=prompt):
                body = {"id": "x", "name": "n", "nodes": [{"id": "p", "data": {"prompt": prompt}}]}
                t = plan_templates.Template(
                    name="t",
                    description="d",
                    plan=[plan_templates.Item("design")],
                    flows={1: body},
                )
                text = plan_templates.dump(t)
                again, notes = plan_templates.parse_template(text.encode(), "t")
                self.assertIsNotNone(again, notes)
                self.assertEqual(again, t)
                self.assertEqual(again.flows[1]["nodes"][0]["data"]["prompt"], prompt)
                self.assertEqual(plan_templates.dump(again), text)
                literal = "prompt: |" in text
                self.assertEqual(literal, plan_templates._literal_ok(prompt), text)
        # 読める形で出る。
        t = plan_templates.Template(
            name="t",
            description="d",
            plan=[plan_templates.Item("design")],
            flows={1: {"nodes": [{"data": {"prompt": "一行目\n二行目\n"}}]}},
        )
        self.assertIn("prompt: |\n", plan_templates.dump(t))
        self.assertIn("\n          一行目\n          二行目\n", plan_templates.dump(t))

    def test_the_flow_text_uses_the_same_literal_blocks(self):
        body = {"id": "x", "nodes": [{"id": "p", "data": {"prompt": "a\nb\n"}}]}
        text, why = plan_templates.dump_flow(body)
        self.assertEqual(why, "")
        self.assertIn("prompt: |", text)
        self.assertEqual(yaml.safe_load(text), body)

    def test_string_flow_keys_are_read_as_numbers(self):
        text = FEATURE.replace("\n  2:\n", '\n  "2":\n')
        t, _ = plan_templates.parse_template(text.encode(), "feature")
        self.assertEqual(list(t.flows), [2])

    def test_aliases_and_bad_bytes_are_refused(self):
        alias = "name: a\ndescription: &d x\nplan: [a]\nwhen: *d\n"
        t, notes = plan_templates.parse_template(alias.encode(), "a")
        self.assertIsNone(t)
        self.assertIn("別名", notes[0].detail)
        t, notes = plan_templates.parse_template(b"name: \xff\xfe\n", "a")
        self.assertIsNone(t)
        self.assertIn("UTF-8", notes[0].detail)
        t, notes = plan_templates.parse_template(b"name: [\n", "a")
        self.assertIsNone(t)
        self.assertIn("YAML", notes[0].detail)

    def test_a_bom_is_dropped(self):
        t, _ = plan_templates.parse_template(b"\xef\xbb\xbf" + RESEARCH.encode(), "research")
        self.assertIsNotNone(t)


class LayerTest(Harness):
    def test_no_templates_is_normal(self):
        answer = self.answer()
        self.assertEqual(answer["templates"], [])
        self.assertFalse(answer["layers"][0]["exists"])

    def test_common_and_layer_templates_are_added(self):
        self.common("research", RESEARCH)
        self.own("feature", FEATURE)
        rows = {(r["layer"], r["name"]): r for r in self.answer()["templates"]}
        self.assertEqual(set(rows), {("common", "research"), ("self", "feature")})
        self.assertTrue(all(r["effective"] and r["usable"] for r in rows.values()))
        mine = rows[("self", "feature")]
        self.assertEqual(mine["path"], ".ccnavi/config/project/templates/feature.yml")
        self.assertEqual(
            rows[("common", "research")]["path"], ".ccnavi/config/common/templates/research.yml"
        )

    def test_the_same_template_in_both_layers_drops_the_layer_side_with_info(self):
        self.common("research", RESEARCH)
        self.own("research", RESEARCH)
        mine = self.entry("self", "research")
        self.assertFalse(mine["effective"])
        self.assertEqual(mine["dropped"]["reason"], "duplicate")
        self.assertEqual(len(self.problems(mine, "info")), 1)
        self.assertEqual(self.problems(mine, "error"), [])
        self.assertTrue(self.entry("common", "research")["effective"])

    def test_a_different_template_with_the_same_name_is_an_error_and_not_used(self):
        self.common("research", RESEARCH)
        self.own("research", RESEARCH.replace("調べてから", "調べずに"))
        mine = self.entry("self", "research")
        self.assertFalse(mine["effective"])
        self.assertEqual(mine["dropped"]["reason"], "conflict")
        self.assertIn("description", self.problems(mine, "error")[0])
        self.assertTrue(self.entry("common", "research")["effective"])
        # 衝突したのはこの 1 本だけ。ほかのレイヤーのテンプレートは使う。
        self.own("feature", FEATURE)
        self.assertTrue(self.entry("self", "feature")["effective"])

    def test_a_broken_common_template_is_empty_and_the_layer_one_is_used(self):
        self.common("research", "name: [\n")
        self.own("research", RESEARCH)
        common = self.entry("common", "research")
        self.assertFalse(common["valid"])
        self.assertFalse(common["effective"])
        self.assertEqual(common["dropped"]["reason"], "invalid")
        mine = self.entry("self", "research")
        self.assertTrue(mine["effective"])
        self.assertEqual(self.problems(mine, "error"), [])

    def test_a_project_layer_is_added_to_the_common_layer_only(self):
        self.common("research", RESEARCH)
        write(self.path("lib", "feature"), FEATURE)
        self.own("local", template("local", "  - design\n"))
        rows = {(r["layer"], r["name"]) for r in self.answer("--layer", "lib")["templates"]}
        self.assertEqual(rows, {("common", "research"), ("lib", "feature")})
        sums = {s["name"]: s for s in self.answer()["sums"]}
        self.assertEqual(set(sums), {"self", "lib"})
        self.assertEqual(sums["lib"]["layers"], ["common", "lib"])
        refused = self.ccnavi("--templates", "--layer", "nothing")
        self.assertEqual(refused.returncode, 1)

    def test_other_files_and_dot_files_in_the_place(self):
        self.own("feature", FEATURE)
        write(os.path.join(os.path.dirname(self.path("self", "x")), "notes.txt"), "x")
        write(os.path.join(os.path.dirname(self.path("self", "x")), ".feature.yml.swp"), "x")
        layer = self.answer()["layers"][1]
        self.assertEqual([p["severity"] for p in layer["problems"]], ["warn"])
        self.assertIn("notes.txt", layer["problems"][0]["detail"])
        self.assertEqual([r["name"] for r in layer["templates"]], ["feature"])

    def test_a_bad_file_name_is_an_error(self):
        write(self.path("self", "bad name"), RESEARCH)
        row = self.answer()["layers"][1]["templates"][0]
        self.assertFalse(row["valid"])
        self.assertIn("ファイル名", self.problems(row, "error")[0])

    def test_a_big_file_is_not_read(self):
        self.own("big", RESEARCH.replace("name: research", "name: big") + "# " + "x" * 300000)
        row = self.entry("self", "big")
        self.assertFalse(row["valid"])
        self.assertIn("大きすぎる", self.problems(row, "error")[0])

    @requires_symlink
    def test_a_linked_file_is_not_read(self):
        outside = write(os.path.join(self.root, "outside.yml"), RESEARCH)
        place = self.path("self", "research")
        os.makedirs(os.path.dirname(place), exist_ok=True)
        os.symlink(outside, place)
        row = self.entry("self", "research")
        self.assertFalse(row["valid"])
        self.assertIn("シンボリックリンク", self.problems(row, "error")[0])

    @requires_symlink
    def test_a_linked_place_is_not_read(self):
        write(os.path.join(self.root, "elsewhere", "research.yml"), RESEARCH)
        place = os.path.dirname(self.path("self", "x"))
        os.makedirs(os.path.dirname(place), exist_ok=True)
        os.symlink(os.path.join(self.root, "elsewhere"), place)
        layer = self.answer()["layers"][1]
        self.assertEqual(layer["templates"], [])
        self.assertIn("シンボリックリンク", layer["unreadable"])

    def test_a_hard_linked_file_is_not_read(self):
        outside = write(os.path.join(self.root, "outside.yml"), RESEARCH)
        place = self.path("self", "research")
        os.makedirs(os.path.dirname(place), exist_ok=True)
        try:
            os.link(outside, place)
        except OSError:
            self.skipTest("ハードリンクを作れない")
        row = self.entry("self", "research")
        self.assertFalse(row["valid"])
        self.assertIn("ハードリンク", self.problems(row, "error")[0])


class CheckTest(Harness):
    def check(self, text: str, name: str = "t") -> dict:
        self.own(name, text)
        return self.entry("self", name)

    def test_a_good_template_has_no_problem(self):
        self.own("feature", FEATURE)
        row = self.entry("self", "feature")
        self.assertEqual(row["problems"], [])
        self.assertTrue(row["usable"])
        self.assertEqual(
            [(i["index"], i["number"], i["type"], i["after"]) for i in row["plan"]],
            [(1, 1, "design", []), (2, 2, "implement", [1])],
        )
        self.assertEqual([(i["index"], i["number"]) for i in row["feedback"]], [(1, 3)])
        self.assertEqual(row["flows"][0]["index"], 2)
        self.assertEqual(row["flows"][0]["nodes"], 3)

    def test_two_ends_are_an_error(self):
        row = self.check(template("t", "  - design\n  - acceptance\n"))
        self.assertTrue(any("終端は 1 つ" in d for d in self.problems(row, "error")))
        self.assertFalse(row["usable"])

    def test_an_after_out_of_range_is_an_error(self):
        for after in ("[2]", "[0]", "[5]"):
            plan = f"  - design\n  - {{type: implement, after: {after}}}\n"
            row = self.check(template("t", plan))
            self.assertTrue(
                any("`plan[2]` の `after`" in d for d in self.problems(row, "error")), after
            )

    def test_the_feedback_after_counts_inside_the_feedback_plan(self):
        text = template(
            "t",
            "  - design\n",
            extra="",
        ) + ("feedback:\n  - implement-feedback\n  - {type: implement-feedback, after: [1]}\n")
        row = self.check(text)
        self.assertEqual(self.problems(row, "error"), [])
        # 全体計画の番号は指せない（フィードバック計画の中の添字で書く）。
        bad = text.replace("after: [1]", "after: [2]")
        self.assertTrue(self.problems(self.check(bad), "error"))

    def test_a_deferral_without_a_taker_is_an_error(self):
        row = self.check(template("t", "  - {type: design, deferred: true}\n  - implement\n"))
        self.assertTrue(any("延期を引き受ける" in d for d in self.problems(row, "error")))

    def test_a_deferral_to_a_phase_without_review_is_a_warning(self):
        text = template(
            "t", "  - {type: design, deferred: true}\n  - {type: research, after: [1]}\n"
        )
        row = self.check(text)
        self.assertEqual(self.problems(row, "error"), [])
        self.assertTrue(any("レビューがありません" in d for d in self.problems(row, "warn")))

    def test_the_last_item_cannot_be_deferred(self):
        plan = "  - design\n  - {type: implement, after: [1], deferred: true}\n"
        row = self.check(template("t", plan))
        self.assertTrue(any("延期できない" in d for d in self.problems(row, "error")))

    def test_a_review_that_loosens_is_an_error(self):
        row = self.check(template("t", "  - {type: design, review: false}\n"))
        self.assertTrue(any("弱める向き" in d for d in self.problems(row, "error")))

    def test_an_unknown_type_is_only_a_warning(self):
        row = self.check(template("t", "  - docs\n"))
        self.assertEqual(self.problems(row, "error"), [])
        warns = self.problems(row, "warn")
        self.assertTrue(any("`docs`" in d and "phases.yml にありません" in d for d in warns))
        self.assertTrue(row["usable"])

    def test_a_kind_that_does_not_match_the_place_is_only_a_warning(self):
        row = self.check(template("t", "  - implement-feedback\n"))
        self.assertEqual(self.problems(row, "error"), [])
        self.assertTrue(any("kind" in d for d in self.problems(row, "warn")))
        row = self.check(template("t2", "  - design\n") + "feedback:\n  - design\n", "t2")
        self.assertTrue(any("kind" in d for d in self.problems(row, "warn")))

    def test_without_phases_the_types_are_not_checked_with_one_warning(self):
        os.remove(common_path(self.root, "phases"))
        row = self.check(template("t", "  - design\n  - {type: implement, after: [1]}\n"))
        self.assertEqual(self.problems(row, "error"), [])
        self.assertEqual(len(self.problems(row, "warn")), 1)

    def test_the_limit_counts_the_plan_and_the_feedback_plan_together(self):
        def chain(count: int, kind: str) -> list:
            return [
                plan_templates.Item(kind, after=[i - 1] if i > 1 else [])
                for i in range(1, count + 1)
            ]

        def errors(plan: int, feedback: int) -> list[str]:
            t = plan_templates.Template(
                name="t",
                description="d",
                plan=chain(plan, "design"),
                feedback=chain(feedback, "implement-feedback"),
            )
            return [p.detail for p in plan_templates.check(t, None, None) if p.severity == "error"]

        for plan, feedback in ((99, 0), (60, 39), (59, 40), (1, 98)):
            with self.subTest(plan=plan, feedback=feedback):
                self.assertEqual(errors(plan, feedback), [])
        for plan, feedback in ((100, 0), (98, 2), (60, 40), (1, 99)):
            with self.subTest(plan=plan, feedback=feedback):
                found = errors(plan, feedback)
                self.assertTrue(any("上限 99 ステップ" in d for d in found), found)

    def test_hidden_characters_anywhere_in_the_strings_are_an_error(self):
        flow_body = {"id": "x", "name": "n", "nodes": [{"id": "p", "data": {"prompt": "a\u200bb"}}]}
        for where in ("description", "when", "flow"):
            with self.subTest(where=where):
                t = plan_templates.Template(
                    name="t", description="d", when="w", plan=[plan_templates.Item("design")]
                )
                if where == "flow":
                    t.flows = {1: flow_body}
                else:
                    setattr(t, where, "x\u202ey")
                found = plan_templates.check(t, None, None)
                self.assertTrue(
                    any(
                        p.severity == "error" and "制御文字・書式文字が含まれている" in p.detail
                        for p in found
                    )
                )
                # 苦情の文面に生の文字は出ない。
                self.assertFalse(any("\u202e" in p.detail or "\u200b" in p.detail for p in found))
        # 改行とタブは対象外。
        t = plan_templates.Template(
            name="t", description="a\tb\nc", plan=[plan_templates.Item("design")]
        )
        self.assertEqual(
            [p for p in plan_templates.check(t, None, None) if p.severity == "error"], []
        )

    def test_a_changed_definition_only_warns(self):
        # 型が phases.yml から消えても、読むのも一覧も止まらない（使うときの承認で error になる）。
        self.own("feature", FEATURE)
        write(common_path(self.root, "phases"), PHASES.replace("  design:", "  design2:"))
        row = self.entry("self", "feature")
        self.assertEqual(self.problems(row, "error"), [])
        self.assertTrue(self.problems(row, "warn"))

    def test_the_flows_index_must_be_in_the_plan(self):
        text = FEATURE.replace("\n  2:\n", "\n  3:\n")
        row = self.check(text, "feature")
        self.assertTrue(any("添字 3" in d for d in self.problems(row, "error")))
        row = self.check(FEATURE.replace("\n  2:\n", "\n  0:\n"), "feature")
        self.assertTrue(any("添字 0" in d for d in self.problems(row, "error")))

    def test_a_flow_that_cannot_be_read_is_an_error(self):
        text = template("t", "  - design\n") + "flows:\n  1:\n    id: x\n    name: y\n"
        row = self.check(text)
        errors = self.problems(row, "error")
        self.assertTrue(any("`flows[1]`" in d and "nodes" in d for d in errors))

    def test_the_flow_is_linted_like_a_flow_file(self):
        # 終了が無いフローは、フローのファイルと同じ警告になる。
        text = template("t", "  - design\n") + (
            "flows:\n  1:\n    id: x\n    name: y\n    nodes:\n"
            "      - {id: s, type: start, name: a, position: {x: 0, y: 0}, data: {}}\n"
        )
        row = self.check(text)
        self.assertEqual(self.problems(row, "error"), [])
        self.assertTrue(any("`flows[1]`" in d for d in self.problems(row, "warn")))


class CliTest(Harness):
    def setUp(self):
        super().setUp()
        self.common("research", RESEARCH)
        self.own("feature", FEATURE)

    def test_the_list_shows_name_layer_description_and_when(self):
        out = self.ccnavi("--templates")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("research  [common]  調べてから設計する", out.stdout)
        self.assertIn("feature  [self]  機能を設計から実装まで進める", out.stdout)
        self.assertIn("いつ: 新しい機能を足すとき", out.stdout)

    def test_the_keyword_searches_name_description_and_when(self):
        for word, names in (
            ("feat", ["feature"]),
            ("調べ", ["research"]),
            ("機能を足す", ["feature"]),
            ("FEATURE", ["feature"]),
            ("無い語", []),
        ):
            rows = self.answer("--keyword", word)["templates"]
            self.assertEqual([r["name"] for r in rows], names, word)
        both = self.answer("--keyword", "調べ", "--keyword", "feat")["templates"]
        self.assertEqual(sorted(r["name"] for r in both), ["feature", "research"])
        text = self.ccnavi("--templates", "--keyword", "無い語")
        self.assertIn("該当するテンプレートはありません。", text.stdout)

    def test_show_prints_the_content(self):
        out = self.ccnavi("--templates", "--show", "feature")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("テンプレート: feature（self）", out.stdout)
        self.assertIn("2. implement（after 1）", out.stdout)
        self.assertIn("name: feature", out.stdout)
        shown = json.loads(self.ccnavi("--templates", "--show", "feature", "--json").stdout)
        self.assertEqual(shown["template"]["name"], "feature")
        self.assertEqual(shown["template"]["text"], FEATURE)
        self.assertEqual(shown["template"]["sha"], plan_templates.digest(FEATURE.encode()))

    def test_show_of_an_unknown_name_fails(self):
        out = self.ccnavi("--templates", "--show", "nothing")
        self.assertEqual(out.returncode, 1)
        self.assertIn("nothing", out.stderr)

    def test_failures_are_json_on_stdout_when_json_is_asked(self):
        for args in (
            ["--templates", "--show", "nothing", "--json"],
            ["--templates", "--layer", "nowhere", "--json"],
            ["--templates", "--show", "feature", "--layer", "nowhere", "--json"],
        ):
            with self.subTest(args=args):
                out = self.ccnavi(*args)
                self.assertEqual(out.returncode, 1)
                answer = json.loads(out.stdout)
                self.assertFalse(answer["ok"])
                self.assertTrue(answer["error"])
                self.assertEqual(answer["version"], plan_templates.JSON_VERSION)
                self.assertEqual(out.stderr, "")

    def test_sums_use_the_same_dropped_shape_as_the_templates(self):
        self.own("research", RESEARCH.replace("調べてから", "調べずに"))
        self.own("feature", FEATURE.replace("機能を", "別の機能を"))
        self.common("broken", "name: [\n")
        answer = self.answer()
        dropped = {
            (row["layer"], row["name"]): row["dropped"]
            for sums in answer["sums"]
            for row in sums["templates"]
            if row["dropped"] is not None
        }
        self.assertEqual(dropped[("self", "research")]["reason"], "conflict")
        self.assertEqual(dropped[("common", "broken")]["reason"], "invalid")
        for value in dropped.values():
            self.assertEqual(set(value), {"reason", "detail"})
        kept = [r for s in answer["sums"] for r in s["templates"] if r["name"] == "feature"]
        self.assertTrue(any(r["dropped"] is None for r in kept))

    def test_show_prefers_the_effective_one(self):
        self.own("research", RESEARCH.replace("調べてから", "調べずに"))
        shown = json.loads(self.ccnavi("--templates", "--show", "research", "--json").stdout)
        self.assertEqual(shown["template"]["layer"], "common")

    def test_wrong_combinations_are_refused(self):
        for args, needle in (
            (["--show", "feature"], "--templates"),
            (["--layer", "self"], "--templates"),
            (["--templates", "--docs"], "--docs"),
            (["--templates", "--type", "adr"], "--docs でだけ"),
            (["--templates", "--lint"], "--lint"),
            (["--templates", "--show", "a", "--keyword", "b"], "--keyword"),
            (["--templates", "ticket"], "フラグ以外の語"),
            (["--templates", "--show", " "], "名前"),
        ):
            out = self.ccnavi(*args)
            self.assertEqual(out.returncode, 1, args)
            self.assertIn(needle, out.stderr, args)
            self.assertEqual(out.stdout, "", args)

    def test_the_list_says_why_a_broken_template_is_not_used(self):
        self.common("broken", "name: [\n")
        write(os.path.join(os.path.dirname(self.path("self", "x")), "notes.txt"), "x")
        write(self.path("lib", "lib"), template("lib", "  - design\n"))
        out = self.ccnavi("--templates").stdout
        self.assertIn("broken  [common]（使わない: invalid）  YAML として読めない", out)
        self.assertIn("notes.txt", out)
        # 統合結果の相手でないレイヤーの置き場の苦情は、一覧に混ぜない。
        other = self.ccnavi("--templates", "--layer", "lib").stdout
        self.assertNotIn("notes.txt", other)
        self.assertIn("lib  [lib]", other)

    def test_the_keyword_is_still_docs_only_elsewhere(self):
        out = self.ccnavi("--lint", "--keyword", "x")
        self.assertEqual(out.returncode, 1)
        self.assertIn("--docs でだけ", out.stderr)


class LintTest(Harness):
    def lint(self):
        result = self.ccnavi("--lint", "--json", "--mode", "enable")
        return [p for p in json.loads(result.stdout)["problems"] if "templates" in p["where"]]

    def test_no_templates_say_nothing(self):
        self.assertEqual(self.lint(), [])

    def test_a_good_template_says_nothing(self):
        self.common("research", RESEARCH)
        self.own("feature", FEATURE)
        self.assertEqual(self.lint(), [])

    def test_problems_are_reported_with_their_layer(self):
        self.common("research", RESEARCH)
        self.own("research", RESEARCH.replace("調べてから", "調べずに"))
        self.own("ends", template("ends", "  - design\n  - acceptance\n"))
        self.own("unknown", template("unknown", "  - docs\n"))
        write(self.path("lib", "broken"), "name: [\n")
        found = {(p["where"], p["severity"]) for p in self.lint()}
        self.assertEqual(
            found,
            {
                ("(templates/self)", "error"),
                ("(templates/self)", "warn"),
                ("(templates/lib)", "error"),
            },
        )
        details = " ".join(p["detail"] for p in self.lint())
        self.assertIn("research: ", details)
        self.assertIn("ends: ", details)

    def test_the_text_report_counts_the_errors(self):
        self.own("ends", template("ends", "  - design\n  - acceptance\n"))
        result = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("templates/self" in line for line in result.stdout.splitlines()))

    def test_the_lint_version_is_unchanged(self):
        # 欄を足さないので、`--lint --json` の形の版は上げない。
        result = self.ccnavi("--lint", "--json", "--mode", "enable")
        self.assertEqual(json.loads(result.stdout)["version"], 1)


def showcase(root: str) -> None:
    """答えの形の見本にするワークスペース。統合結果・衝突・壊れたファイル・検査の苦情が一通り出る。"""
    write(common_path(root, "phases"), PHASES)
    base = os.path.dirname(common_path(root, "rules")) + "/templates/"
    own = os.path.dirname(config_path(root, "rules")) + "/templates/"
    write(base + "feature.yml", FEATURE)
    write(base + "research.yml", RESEARCH)
    write(base + "broken.yml", "name: [\n")
    write(own + "feature.yml", FEATURE)
    write(own + "research.yml", RESEARCH.replace("調べてから", "調べずに"))
    write(own + "local.yml", template("local", "  - docs\n", "このプロジェクトだけの作業"))
    write(own + "ends.yml", template("ends", "  - design\n  - acceptance\n", "終端が 2 つ"))
    write(own + "notes.txt", "x")
    os.makedirs(os.path.join(root, "projects", "lib", ".git"), exist_ok=True)
    write(
        os.path.join(root, "projects", "lib", ".ccnavi/config/project/templates/lib.yml"),
        template("lib", "  - research\n", "lib の調べもの"),
    )
    # 下書き（効力は無い）。TODO の残り・同名の既存・読めないもの。
    drafts = os.path.join(root, "wip", "handoff", ".ccnavi", "config", "project", "templates") + "/"
    write(
        drafts + "login.yml",
        FEATURE.replace("feature", "login").replace("機能を設計から", "TODO ログインを設計から"),
    )
    write(drafts + "research.yml", RESEARCH)
    write(drafts + "broken.yml", "name: [\n")
    # 同じ中身の下書きがもう 1 つのツリー（lib）にもある（also_in に値が入る）。
    write(
        os.path.join(
            root, "projects", "lib", "wip", "handoff", ".ccnavi/config/project/templates/login.yml"
        ),
        FEATURE.replace("feature", "login").replace("機能を設計から", "TODO ログインを設計から"),
    )


class CheckFileTest(Harness):
    """`--templates --check-file <パス>`。任意のパスのテンプレートを検査する（書かない）。"""

    def check(self, text: str, name: str = "feature", *args: str) -> dict:
        path = write(os.path.join(self.root, "tmp-check", name + ".yml"), text)
        result = self.ccnavi("--templates", "--check-file", path, "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_plan_with_feedback_and_flows_is_read(self):
        got = self.check(FEATURE)
        self.assertTrue(got["ok"])
        self.assertTrue(got["valid"])
        self.assertEqual(got["name"], "feature")
        self.assertEqual(got["sha"], plan_templates.digest(FEATURE.encode()))
        self.assertEqual(got["problems"], [])
        self.assertEqual(got["data"]["plan"], ["design", {"type": "implement", "after": [1]}])
        self.assertEqual(list(got["data"]["flows"]), ["2"])
        plan, feedback = got["plans"]
        self.assertEqual((plan["part"], feedback["part"]), ("plan", "feedback"))
        self.assertEqual([i["number"] for i in plan["items"]], [1, 2])
        self.assertEqual([i["number"] for i in feedback["items"]], [3])
        self.assertEqual(plan["items"][0]["title"], "設計")
        self.assertEqual(plan["after"], {"2": [1]})
        self.assertEqual(plan["ready"], [1])
        self.assertEqual(plan["problems"], [])
        self.assertEqual(plan["source_sha"], got["sha"])
        self.assertIn("implement", [t["id"] for t in got["types"]])

    def test_a_plan_only_template_has_one_graph(self):
        got = self.check(RESEARCH, "research")
        self.assertEqual([p["part"] for p in got["plans"]], ["plan"])
        self.assertFalse(got["plans"][0]["items"][0]["review"])

    def test_unreadable_yaml_is_invalid(self):
        got = self.check("name: [\n")
        self.assertFalse(got["valid"])
        self.assertIsNone(got["data"])
        self.assertEqual(got["plans"], [])
        self.assertEqual(self.problems(got, "error")[:1] != [], True)

    def test_a_bad_after_is_an_error_and_the_graph_still_comes(self):
        got = self.check(template("feature", "  - design\n  - {type: implement, after: [2]}\n"))
        self.assertTrue(got["valid"])
        self.assertTrue(self.problems(got, "error"))
        plan = got["plans"][0]
        self.assertEqual(plan["after"], {"2": [2]})
        self.assertEqual(plan["loose"], [])
        self.assertEqual(plan["ready"], [])
        self.assertTrue(plan["problems"])

    def test_an_unknown_type_is_a_warn_and_the_graph_comes(self):
        got = self.check(template("feature", "  - nothing\n"))
        self.assertTrue(got["valid"])
        self.assertTrue(self.problems(got, "warn"))
        self.assertFalse(self.problems(got, "error"))
        self.assertEqual(got["plans"][0]["items"][0]["title"], "(定義なし) nothing")

    def test_the_layer_is_checked_with_its_own_types(self):
        for layer in ("common", "self"):
            got = self.check(RESEARCH, "research", "--layer", layer)
            self.assertEqual(got["layer"], layer)
            self.assertIn("research", [t["id"] for t in got["types"]])

    def test_an_unknown_layer_fails(self):
        path = write(os.path.join(self.root, "tmp-check", "feature.yml"), FEATURE)
        result = self.ccnavi("--templates", "--check-file", path, "--layer", "nothing", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_a_missing_file_is_an_error_problem(self):
        result = self.ccnavi(
            "--templates", "--check-file", os.path.join(self.root, "none.yml"), "--json"
        )
        got = json.loads(result.stdout)
        self.assertFalse(got["valid"])
        self.assertTrue(self.problems(got, "error"))

    def test_the_text_form_prints_a_summary(self):
        path = write(os.path.join(self.root, "tmp-check", "feature.yml"), FEATURE)
        out = self.ccnavi("--templates", "--check-file", path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("読めた: はい", out.stdout)

    def test_it_does_not_combine_with_other_modes(self):
        path = write(os.path.join(self.root, "tmp-check", "feature.yml"), FEATURE)
        for extra in (["--show", "x"], ["--draft", "p"], ["--keyword", "k"]):
            out = self.ccnavi("--templates", "--check-file", path, *extra)
            self.assertNotEqual(out.returncode, 0, extra)
            self.assertIn("--check-file", out.stderr)
        alone = self.ccnavi("--check-file", path)
        self.assertNotEqual(alone.returncode, 0)
        self.assertIn("--templates と一緒に使う", alone.stderr)

    def test_the_flag_is_listed_in_the_version(self):
        out = self.ccnavi("--version", "--json")
        self.assertIn("--check-file", json.loads(out.stdout)["flags"])

    def test_the_answer_matches_the_fixture(self):
        got = self.check(FEATURE)
        got["sha"] = "<sha>"
        for plan in got["plans"]:
            plan["source_sha"] = "<sha>"
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "templates-check-json.json")
        with open(fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(got, fixture["expect"])
        self.assertTrue(fixture["about"])


class ShapeTest(unittest.TestCase):
    """`--templates --json` の答えの形。拡張と共有する見本（templates-json.json）。"""

    def test_the_answer_matches_the_fixture(self):
        root = tempfile.mkdtemp(prefix="ccnavi-templates-shape-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        showcase(root)
        result = run_ccnavi(["--root", root, "--templates", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout.replace(json.dumps(root)[1:-1], "<root>"))
        with open(FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(self.plain(answer), self.plain(fixture["expect"]))
        self.assertTrue(fixture["about"])

    @classmethod
    def plain(cls, value):
        """YAML ライブラリの版で変わる、読めない理由の文言を外す。"""
        if isinstance(value, str):
            return re.sub(r"(YAML として読めない) \(.*\)", r"\1 (...)", value)
        if isinstance(value, list):
            return [cls.plain(v) for v in value]
        if isinstance(value, dict):
            return {k: cls.plain(v) for k, v in value.items()}
        return value


class NewNameTest(unittest.TestCase):
    """新しく作る名前の検査。読み込みの側（`NAME_RE`）は変えない。"""

    def test_usable_names_pass(self):
        for name in ("feature", "a", "full-phases", "v1.2_x", "console", "com10", "A" * 64):
            self.assertEqual(plan_templates.new_name_problem(name), "", name)

    def test_windows_reserved_names_and_trailing_period_are_refused(self):
        bad = (
            "con",
            "CON",
            "prn",
            "aux",
            "nul",
            "com1",
            "COM9",
            "lpt1",
            "lpt9",
            "con.x",
            "nul.tar.gz",
            "a.",
            "a..",
        )
        for name in bad:
            self.assertNotEqual(plan_templates.new_name_problem(name), "", name)

    def test_the_old_shape_is_still_refused(self):
        for name in ("", "-x", "a b", "a/b", "A" * 65):
            self.assertNotEqual(plan_templates.new_name_problem(name), "", name)

    def test_reading_still_accepts_what_it_accepted(self):
        for name in ("con", "a."):
            self.assertTrue(plan_templates.NAME_RE.match(name), name)


if __name__ == "__main__":
    unittest.main()
