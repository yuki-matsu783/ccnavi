"""作業・レビュー計画のテンプレートを溜める側（下書きと取り込み）の受入テスト。

見るのは次のとおり。

1. 下書きの置き場: `wip/proposals/templates/<名前>.yml`（ツリーごと）。flow.draft_rel と同じ形
2. `--templates --draft <親>`: 完了した親の計画から下書きを作る。`after` を計画の中の添字に直し、
   子のフローを写し、同名は上書きせず断る。作業中・退避済み・取り消した親は読めないと言う
3. 下書きの効力: `--templates` の足し算にも `templates[]` にも入らない。`--lint` は error にしない。
   一覧に件数、`--json` に `drafts[]`
4. `--templates --import`: `--yes` と `--expect` が揃うまで何も書かない。差分は文の前後まで見せる。
   同名は `--replace`。書いた場所を `--templates` が読む。下書きは同じ中身のときだけ消す
5. 書き方: 一時ファイル → fsync → 差し替え。リンク・ハードリンクには書かない
6. ユーザ専用: `--import` はエージェントが打てない（`phase_forms._CLI_FORMS`）。`--draft` は打てる。
   下書きの置き場への Write は判定が変わらず止まらない
"""

from __future__ import annotations

import json
import os
import textwrap
import unittest
from unittest import mock

from ccnavi.infra import fsio, settings
from ccnavi.policy.rules import SEVERITY_ERROR, SEVERITY_WARN
from ccnavi.tickets import flow, plan_templates, template_drafts
from tests import ROOT, common_path, requires_symlink
from tests.inproc import run_ccnavi
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import PHASES, child_text, parent_text

FLOW_IMPL = textwrap.dedent(
    """\
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
)

DENY = "DENY_TICKET_APPROVAL_CLI"


class Drafts(PhaseHarness):
    """完了した親（`done/`）を直接置いて、下書きと取り込みを動かす道具。"""

    def setUp(self):
        super().setUp()
        # 共通のフェーズ定義。取り込み先が共通のとき、この定義で検査される。
        write(common_path(self.root, "phases"), PHASES)
        self.conf, _ = settings.load(self.root)

    def tpl(self, *args, guard=None):
        """`--templates` と `--lint` を動かす。PhaseHarness.ccnavi が足す判定の旗は付けない。

        `guard` は端末の確かめ（CCNAVI_GUARD_TICKET_APPROVAL）。`--templates` は旗では渡せない。
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        if guard is not None:
            env[settings.GUARD_TICKET_APPROVAL_ENV] = guard
        return run_ccnavi(["--root", self.root, *args], env=env, cwd=ROOT)

    # ---- 完了した親

    def done_parent(
        self,
        plan,
        feedback=None,
        flows=None,
        parent="i0001",
        tree=None,
        place="done",
        children=None,
    ):
        """親と子を `done/` に置く。`flows` は {子の識別子: フローの本文}。

        `children` が無ければ、全体計画の n 番目に子 `<親>-<nn>-01` を 1 つずつ置く。
        """
        tree = tree or self.parent_tree
        base = os.path.join(tree, ".ccnavi", "approved")
        write(os.path.join(base, place, parent + ".md"), parent_text(parent, plan, feedback))
        if children is None:
            children = {f"{parent}-{n:02d}-01": n for n in range(1, len(plan) + 1)}
        for child, phase in children.items():
            write(
                os.path.join(base, place, child + ".md"),
                child_text(child, parent, phase, ("src/*",)),
            )
        for child, body in (flows or {}).items():
            write(os.path.join(base, "flows", child + ".yml"), body)

    def draft_path(self, name="i0001", tree=None):
        return plan_templates.draft_file(self.conf, tree or self.parent_tree, name)

    def draft(self, parent="i0001", *extra):
        return self.tpl("--templates", "--draft", parent, *extra)

    def read_draft(self, name="i0001"):
        with open(self.draft_path(name), "rb") as f:
            return f.read()

    def drafted(self, name="i0001") -> plan_templates.Template:
        template, notes = plan_templates.parse_template(self.read_draft(name), name)
        self.assertIsNotNone(template, notes)
        return template

    def generalize(self, name="i0001", description="機能を設計から実装まで進める"):
        """下書きの TODO を直す（エージェントが一般化したあとの形）。"""
        text = self.read_draft(name).decode("utf-8")
        fixed = text.replace(f"{plan_templates.TODO_MARK} 一般化する: 親", description).replace(
            f"{plan_templates.TODO_MARK} 一般化する: 「親」と同じ形の作業をするとき",
            "新しい機能を足すとき",
        )
        self.assertNotIn(plan_templates.TODO_MARK, fixed)
        write(self.draft_path(name), fixed)
        return plan_templates.digest(fixed.encode("utf-8"))

    # ---- 取り込み

    def import_(self, *args, guard="disable"):
        """`--import`。書くときの端末の確かめ（既定は有効）は、テストでは切っておく。"""
        return self.tpl("--templates", "--import", *args, guard=guard)

    def common_file(self, name="i0001"):
        return os.path.join(plan_templates_dir(self.conf, self.root), name + ".yml")

    def answer(self, *args):
        result = self.tpl("--templates", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


def plan_templates_dir(conf, root):
    return settings.common_templates_dir(conf, root)


def slurp(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class PlaceTest(unittest.TestCase):
    def test_the_draft_place_has_the_same_form_as_the_flow_draft(self):
        conf = settings.Settings()
        conf.tickets = ""
        self.assertEqual(plan_templates.draft_dir_rel(conf), "wip/proposals/templates")
        self.assertEqual(
            plan_templates.draft_rel(conf, "feature"), "wip/proposals/templates/feature.yml"
        )
        self.assertEqual(
            plan_templates.draft_file(conf, "/w", "feature"),
            os.path.normpath("/w/wip/proposals/templates/feature.yml"),
        )
        conf.tickets = "./work/props/"
        self.assertEqual(plan_templates.draft_rel(conf, "x"), "work/props/templates/x.yml")
        # 子のフローの下書きと同じ提案の置き場の下に並ぶ。
        self.assertEqual(
            os.path.dirname(os.path.dirname(plan_templates.draft_rel(conf, "x"))),
            os.path.dirname(os.path.dirname(flow.draft_rel(conf, "i0001-01-01"))),
        )

    def test_a_project_tree_has_its_own_place(self):
        conf = settings.Settings()
        self.assertEqual(
            plan_templates.draft_file(conf, "/w/projects/lib", "x"),
            os.path.normpath("/w/projects/lib/wip/proposals/templates/x.yml"),
        )


class MakeTest(Drafts):
    def test_a_draft_is_made_from_a_done_parent_and_round_trips_to_a_readable_template(self):
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback", "implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("wip/proposals/templates/i0001.yml", result.stdout)
        self.assertIn("全体計画 3 項 / フィードバック計画 2 項 / フロー 1 本", result.stdout)
        template = self.drafted()
        self.assertEqual(template.name, "i0001")
        self.assertEqual([i.type for i in template.plan], ["research", "design", "implement"])
        self.assertEqual([i.after for i in template.plan], [[], [1], [2]])
        # 書いたものを読み直しても、書き戻しても変わらない。
        self.assertEqual(plan_templates.dump(template).encode(), self.read_draft())
        self.assertEqual(list(template.flows), [3])
        self.assertEqual(template.flows[3]["name"], "実装の手順")

    def test_after_is_normalized_to_indexes_inside_each_plan(self):
        # 提案ではフィードバック計画の番号が全体計画の続き（4, 5）になる。
        plan = [
            {"type": "research", "after": []},
            {"type": "design", "after": []},
            {"type": "implement", "after": [1, 2]},
        ]
        feedback = [
            {"type": "implement-feedback", "after": []},
            {"type": "implement-feedback", "after": [4]},
        ]
        self.done_parent(plan, feedback)
        self.assertEqual(self.draft().returncode, 0)
        template = self.drafted()
        self.assertEqual([i.after for i in template.plan], [[], [], [1, 2]])
        self.assertEqual([i.after for i in template.feedback], [[], [1]])
        # 親の絶対番号に付け直すと元に戻る（検査が合成する提案の形）。
        parent = plan_templates._composed(template, {})
        self.assertIn("- 4", parent)
        # 順序の検査（終端・範囲）に error が無い。
        types, _ = plan_templates.phasetypes.common_types(self.conf)
        found = plan_templates.check(template, types, None)
        self.assertEqual([p for p in found if p.severity == SEVERITY_ERROR], [])

    def test_review_is_kept(self):
        plan = [{"type": "design", "review": "defer"}, "implement"]
        self.done_parent(plan)
        self.assertEqual(self.draft().returncode, 0)
        self.assertEqual([i.review for i in self.drafted().plan], ["defer", ""])

    def test_flows_come_from_the_first_child_that_has_one(self):
        self.done_parent(
            ["research", "design"],
            children={"i0001-01-01": 1, "i0001-02-01": 2, "i0001-02-02": 2},
            flows={"i0001-02-01": FLOW_IMPL, "i0001-02-02": FLOW_IMPL.replace("実装の手順", "別")},
        )
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stderr)
        template = self.drafted()
        self.assertEqual(list(template.flows), [2])
        self.assertEqual(template.flows[2]["name"], "実装の手順")
        self.assertIn("i0001-02-02 にもフローがある", result.stdout)

    def test_an_unreadable_flow_is_named_and_not_copied(self):
        self.done_parent(["research", "design"], flows={"i0001-01-01": "nodes: [\n"})
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("i0001-01-01 のフローは読めない", result.stdout)
        self.assertEqual(self.drafted().flows, {})

    def test_the_feedback_flows_are_not_copied(self):
        self.done_parent(
            ["design"],
            feedback=["implement-feedback"],
            children={"i0001-01-01": 1, "i0001-02-01": 2},
            flows={"i0001-02-01": FLOW_IMPL},
        )
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.drafted().flows, {})
        self.assertIn("フィードバック計画の子のフローは写さない", result.stdout)

    def test_the_description_carries_a_todo_mark_and_the_check_warns(self):
        self.done_parent(["design"])
        result = self.draft()
        template = self.drafted()
        self.assertTrue(template.description.startswith(plan_templates.TODO_MARK))
        self.assertIn("親", template.description)
        self.assertTrue(template.when.startswith(plan_templates.TODO_MARK))
        self.assertIn("親", template.when)
        self.assertIn("warn", result.stdout)
        self.assertIn(f"`description` に {plan_templates.TODO_MARK} が残っている", result.stdout)
        self.assertIn(f"`when` に {plan_templates.TODO_MARK} が残っている", result.stdout)
        # 次の手に、未追跡の下書きが ready を止めることを言う。
        self.assertIn("未追跡", result.stdout)
        self.assertIn("ready が止まる", result.stdout)
        # 一般化して印を消せば、苦情は消える。
        self.generalize()
        row = self.answer()["drafts"][0]
        self.assertEqual(row["problems"], [])

    def test_the_name_defaults_to_the_parent_and_can_be_given(self):
        self.done_parent(["design"])
        self.assertEqual(self.draft("i0001", "--name", "design-only").returncode, 0)
        self.assertTrue(os.path.exists(self.draft_path("design-only")))
        self.assertFalse(os.path.exists(self.draft_path("i0001")))
        self.assertEqual(self.drafted("design-only").name, "design-only")

    def test_a_bad_name_is_refused(self):
        self.done_parent(["design"])
        for name in ("bad name", "../x", "a" * 65):
            result = self.draft("i0001", f"--name={name}")
            self.assertEqual(result.returncode, 1, name)
            self.assertIn("下書きの名前", result.stderr)
        self.assertFalse(os.path.isdir(os.path.dirname(self.draft_path())))

    def test_an_identifier_that_cannot_be_a_name_asks_for_one(self):
        self.done_parent(["design"], parent="a調査")
        result = self.draft("a調査")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--name", result.stderr)

    def test_an_existing_draft_is_never_overwritten(self):
        self.done_parent(["design"])
        self.assertEqual(self.draft().returncode, 0)
        write(self.draft_path(), "agent edited\n")
        again = self.draft()
        self.assertEqual(again.returncode, 1)
        self.assertIn("既に在る", again.stderr)
        self.assertEqual(self.read_draft(), b"agent edited\n")

    def test_a_parent_still_in_progress_is_refused(self):
        self.done_parent(["design"], place="doing")
        result = self.draft()
        self.assertEqual(result.returncode, 1)
        self.assertIn("作業中", result.stderr)
        self.assertFalse(os.path.exists(self.draft_path()))

    def test_a_parent_archived_by_ready_cannot_be_read(self):
        # ready で閉じると done/ は logs/archive/ へ退避される。そこからは読まない。
        write(
            os.path.join(self.root, "logs", "archive", "self", "done", "i0001.md"),
            parent_text("i0001", ["design"]),
        )
        result = self.draft()
        self.assertEqual(result.returncode, 1)
        self.assertIn("退避済み", result.stderr)
        self.assertIn("logs/archive", result.stderr)
        self.assertFalse(os.path.exists(self.draft_path()))

    def test_an_unknown_or_cancelled_parent_is_refused(self):
        result = self.draft("i0099")
        self.assertEqual(result.returncode, 1)
        self.assertIn("見つからない", result.stderr)
        result = self.draft("not an id")
        self.assertEqual(result.returncode, 1)
        self.done_parent(["design"])
        path = os.path.join(self.parent_tree, ".ccnavi", "approved", "done", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.replace('completed_at: ""', 'cancelled_at: "2026-10-01T00:00:00Z"'))
        result = self.draft()
        self.assertEqual(result.returncode, 1)
        self.assertIn("取り消した", result.stderr)

    @requires_symlink
    def test_a_linked_place_is_not_written(self):
        self.done_parent(["design"])
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        place = os.path.dirname(self.draft_path())
        os.makedirs(os.path.dirname(place), exist_ok=True)
        os.symlink(outside, place)
        result = self.draft()
        self.assertEqual(result.returncode, 1)
        self.assertIn("リンク", result.stderr)
        self.assertEqual(os.listdir(outside), [])

    def test_the_json_answer(self):
        self.done_parent(["design"])
        result = self.tpl("--templates", "--draft", "i0001", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["path"], "wip/proposals/templates/i0001.yml")
        self.assertEqual(answer["sha"], plan_templates.digest(self.read_draft()))
        self.assertEqual(answer["tree"], "i0001")


class EffectTest(Drafts):
    """下書きには効力が無い。足し算にも一覧にも入らず、lint の error にもならない。"""

    def test_a_draft_is_not_in_the_templates_or_the_sums(self):
        self.done_parent(["design"])
        self.draft()
        answer = self.answer()
        self.assertEqual(answer["templates"], [])
        self.assertEqual([row["name"] for v in answer["layers"] for row in v["templates"]], [])
        self.assertEqual(answer["sums"][0]["templates"], [])
        self.assertEqual(self.tpl("--templates", "--show", "i0001").returncode, 1)

    def test_the_list_says_there_are_drafts_in_one_line(self):
        out = self.tpl("--templates").stdout
        self.assertNotIn("下書き", out)
        self.done_parent(["design"])
        self.draft()
        out = self.tpl("--templates").stdout
        self.assertIn("下書き: 1 件（wip/proposals/templates/）", out)
        self.assertEqual(sum("下書き" in line for line in out.splitlines()), 1)

    def test_drafts_in_the_json_carry_what_a_screen_needs_to_offer_the_import(self):
        self.done_parent(["research", "design"], flows={"i0001-02-01": FLOW_IMPL})
        self.draft()
        sha = self.generalize()
        write(os.path.join(self.root, ".ccnavi", "config", "common", "templates", "i0001.yml"), "x")
        (row,) = self.answer()["drafts"]
        self.assertEqual(row["name"], "i0001")
        self.assertEqual(row["tree"], "i0001")
        self.assertEqual(row["path"], "wip/proposals/templates/i0001.yml")
        self.assertEqual(row["sha"], sha)
        self.assertTrue(row["importable"])
        self.assertTrue(row["valid"])
        self.assertEqual(row["exists_in"], ["common"])
        self.assertEqual([p["severity"] for p in row["problems"]], [])
        self.assertEqual([(i["index"], i["after"]) for i in row["plan"]], [(1, []), (2, [1])])
        self.assertEqual(row["flows"][0]["index"], 2)

    def test_the_keyword_narrows_the_drafts_too(self):
        self.done_parent(["design"])
        self.draft()
        self.assertEqual(len(self.answer("--keyword", "親")["drafts"]), 1)
        self.assertEqual(self.answer("--keyword", "無い語")["drafts"], [])

    def test_a_broken_draft_is_listed_but_not_importable(self):
        write(self.draft_path("broken"), "name: [\n")
        (row,) = self.answer()["drafts"]
        self.assertFalse(row["valid"])
        self.assertFalse(row["importable"])
        self.assertEqual(row["problems"][0]["severity"], SEVERITY_ERROR)

    def test_lint_never_makes_a_draft_an_error(self):
        write(self.draft_path("broken"), "name: [\n")
        write(self.draft_path("ends"), plan_template("ends", "  - design\n  - acceptance\n"))
        self.done_parent(["design"])
        self.draft()
        result = self.tpl("--lint", "--json", "--mode", "enable")
        found = [
            p for p in json.loads(result.stdout)["problems"] if p["where"] == "(template-drafts)"
        ]
        self.assertTrue(found)
        self.assertEqual({p["severity"] for p in found} - {"warn", "info"}, set())
        self.assertIn(SEVERITY_WARN, {p["severity"] for p in found})
        details = " ".join(p["detail"] for p in found)
        self.assertIn("wip/proposals/templates/broken.yml", details)
        self.assertIn(plan_templates.TODO_MARK, details)
        # ほかの苦情（効力のある側）には混ざらない。
        others = [p for p in json.loads(result.stdout)["problems"] if "templates" in p["where"]]
        self.assertEqual([p for p in others if p["where"] != "(template-drafts)"], [])
        self.assertEqual(json.loads(result.stdout)["version"], 1)

    def test_a_draft_does_not_make_the_workspace_fail_the_lint(self):
        write(self.draft_path("broken"), "name: [\n")
        text = self.tpl("--lint", "--mode", "enable").stdout
        self.assertIn("template-drafts", text)
        self.assertNotIn("error: (template-drafts)", text)

    def test_a_draft_in_a_project_tree_is_found_at_the_projects_place(self):
        project = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(project, ".git"))
        write(
            plan_templates.draft_file(self.conf, project, "lib-draft"),
            plan_template("lib-draft", "  - design\n"),
        )
        (row,) = self.answer()["drafts"]
        self.assertEqual(row["checked_with"], "lib")
        self.assertNotIn("layer", row)
        self.assertEqual(row["tree"], "lib")
        self.assertEqual(row["path"], "wip/proposals/templates/lib-draft.yml")

    def test_the_same_draft_checked_out_in_two_trees_is_one_entry(self):
        self.done_parent(["design"])
        self.draft()
        copy = plan_templates.draft_file(self.conf, self.root, "i0001")
        write(copy, self.read_draft().decode())
        (row,) = self.answer()["drafts"]
        self.assertEqual(sorted([row["tree"], *row["also_in"]]), ["", "i0001"])


def plan_template(name, plan, description="試し"):
    return f"name: {name}\ndescription: {description}\nplan:\n{plan}"


class ImportTest(Drafts):
    def setUp(self):
        super().setUp()
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        self.assertEqual(self.draft().returncode, 0)
        self.sha = self.generalize()

    # ---- 何も書かない

    def test_without_yes_nothing_is_written_and_the_body_is_shown(self):
        result = self.import_("i0001", "--to", "common")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertEqual(plan_templates.digest(self.read_draft()), self.sha)
        self.assertIn("何も書いていない", result.stdout)
        self.assertIn(self.sha, result.stdout)
        # 新しく置くときは、フローの文を含む本文を全部見せる。
        self.assertIn("先にテスト", result.stdout)
        self.assertIn(".ccnavi/config/common/templates/i0001.yml", result.stdout)
        # 続けて打つ形を見せる。
        self.assertIn(f"--yes --expect {self.sha}", result.stdout)

    def test_yes_needs_expect(self):
        result = self.import_("i0001", "--to", "common", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--expect", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_expect_must_be_the_current_hash_of_the_draft(self):
        for expect in ("0" * 64, "abc"):
            result = self.import_("i0001", "--to", "common", "--yes", "--expect", expect)
            self.assertEqual(result.returncode, 1, expect)
            self.assertIn("今の中身のハッシュと違う", result.stderr)
            self.assertFalse(os.path.exists(self.common_file()))
        # 下見に --expect だけを付けても、違えば断る。
        result = self.import_("i0001", "--to", "common", "--expect", "0" * 64)
        self.assertEqual(result.returncode, 1)

    def test_a_draft_rewritten_after_it_was_shown_is_not_imported(self):
        shown = self.import_("i0001", "--to", "common")
        self.assertIn(self.sha, shown.stdout)
        write(self.draft_path(), self.read_draft().decode() + "# 書き足し\n")
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("書き直された", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))

    # ---- 書く

    def test_yes_and_expect_write_the_template_and_it_is_read_by_templates(self):
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("取り込んだ", result.stdout)
        with open(self.common_file(), "rb") as f:
            self.assertEqual(plan_templates.digest(f.read()), self.sha)
        # 書いた場所を `--templates` が読み、足し算に入る。
        rows = {(r["layer"], r["name"]): r for r in self.answer()["templates"]}
        row = rows[("common", "i0001")]
        self.assertTrue(row["effective"])
        self.assertTrue(row["usable"])
        self.assertEqual(row["sha"], self.sha)
        self.assertEqual(row["path"], ".ccnavi/config/common/templates/i0001.yml")
        shown = self.tpl("--templates", "--show", "i0001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("先にテスト", shown.stdout)
        # 下書きは取り込んだ中身と同じだったので消えた。一覧の件数も無くなる。
        self.assertFalse(os.path.exists(self.draft_path()))
        self.assertIn("消した", result.stdout)
        self.assertEqual(self.answer()["drafts"], [])
        self.assertNotIn("下書き:", self.tpl("--templates").stdout)

    def test_the_project_target_writes_the_layer_place(self):
        result = self.import_("i0001", "--to", "project", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        placed = os.path.join(self.root, ".ccnavi", "config", "project", "templates", "i0001.yml")
        self.assertTrue(os.path.isfile(placed))
        rows = {(r["layer"], r["name"]): r for r in self.answer()["templates"]}
        self.assertTrue(rows[("self", "i0001")]["effective"])

    def test_a_project_layer_is_chosen_with_layer(self):
        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        bad = self.import_("i0001", "--to", "project", "--layer", "nothing")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("レイヤー", bad.stderr)
        result = self.import_(
            "i0001", "--to", "project", "--layer", "lib", "--yes", "--expect", self.sha
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        placed = os.path.join(
            self.root, "projects", "lib", ".ccnavi", "config", "project", "templates", "i0001.yml"
        )
        self.assertTrue(os.path.isfile(placed))
        rows = {(r["layer"], r["name"]) for r in self.answer("--layer", "lib")["templates"]}
        self.assertIn(("lib", "i0001"), rows)

    def test_the_draft_can_be_named_by_its_path(self):
        result = self.import_(self.draft_path(), "--to", "common")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.sha, result.stdout)
        outside = write(
            os.path.join(self.root, "elsewhere", "i0001.yml"), self.read_draft().decode()
        )
        result = self.import_(outside, "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("下書きの置き場", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))

    def test_an_unknown_draft_is_refused(self):
        result = self.import_("nothing", "--to", "common")
        self.assertEqual(result.returncode, 1)
        self.assertIn("'nothing'", result.stderr)

    def test_the_write_goes_through_a_synced_temp_file_and_leaves_no_temp(self):
        with mock.patch.object(fsio, "_sync_file", wraps=fsio._sync_file) as synced:
            result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(synced.call_count, 1)
        place = os.path.dirname(self.common_file())
        self.assertEqual(os.listdir(place), ["i0001.yml"])

    # ---- 検査

    def test_a_draft_with_an_error_is_not_imported_even_with_yes_and_expect(self):
        text = self.read_draft().decode().replace("  after:\n  - 1\n", "  after:\n  - 5\n")
        self.assertNotEqual(text, self.read_draft().decode())
        write(self.draft_path(), text)
        sha = plan_templates.digest(text.encode())
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", result.stdout + result.stderr)
        self.assertIn("取り込めない", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_an_unreadable_draft_is_not_imported(self):
        write(self.draft_path(), "name: [\n")
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", "x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("読めない", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))

    def test_a_warning_is_shown_but_does_not_stop_the_import(self):
        text = self.read_draft().decode().replace("type: implement\n", "type: nothing\n", 1)
        text = text.replace("- implement\n", "- nothing\n", 1)
        write(self.draft_path(), text)
        sha = plan_templates.digest(text.encode())
        shown = self.import_("i0001", "--to", "common")
        self.assertIn("warn", shown.stdout)
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- 同名と差分

    def existing(self, text=None):
        return write(self.common_file(), text if text is not None else self.read_draft().decode())

    def test_an_existing_template_needs_replace(self):
        old = self.existing(self.read_draft().decode().replace("先にテスト", "先に実装"))
        before = slurp(old)
        for extra in ((), ("--yes", "--expect", self.sha)):
            result = self.import_("i0001", "--to", "common", *extra)
            self.assertEqual(result.returncode, 1, extra)
            self.assertIn("--replace", result.stderr)
            self.assertEqual(slurp(old), before)
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_replace_shows_the_diff_with_the_sentences_around_the_change(self):
        self.existing(self.read_draft().decode().replace("先にテスト", "先に実装"))
        result = self.import_("i0001", "--to", "common", "--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("置き換える", out)
        self.assertIn("-", out)
        minus = [line for line in out.splitlines() if line.startswith("-") and "先に実装" in line]
        plus = [line for line in out.splitlines() if line.startswith("+") and "先にテスト" in line]
        self.assertEqual(len(minus), 1, out)
        self.assertEqual(len(plus), 1, out)
        # 前後の行（変わっていない文）も見せる。
        lines = out.splitlines()
        at = lines.index(minus[0])
        before = lines[at - 1]
        after = next(line for line in lines[at:] if line.startswith(" "))
        self.assertTrue(before.startswith(" "), before)
        self.assertTrue(after.startswith(" "), after)
        self.assertIn("@@", out)
        self.assertIn("何も書いていない", out)
        self.assertIn("--replace --yes --expect", out)

    def test_replace_with_yes_and_expect_overwrites_and_removes_the_draft(self):
        old = self.existing(self.read_draft().decode().replace("先にテスト", "先に実装"))
        result = self.import_("i0001", "--to", "common", "--replace", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(plan_templates.digest(slurp(old)), self.sha)
        self.assertFalse(os.path.exists(self.draft_path()))
        self.assertEqual(os.listdir(os.path.dirname(old)), ["i0001.yml"])

    def test_an_identical_existing_template_says_there_is_no_difference(self):
        self.existing()
        result = self.import_("i0001", "--to", "common", "--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("差分は無い", result.stdout)

    def test_replace_without_an_existing_template_is_a_new_one(self):
        result = self.import_("i0001", "--to", "common", "--replace", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("置き換える相手は無い", result.stdout)

    def test_a_name_that_would_clash_across_layers_is_refused(self):
        # 共通へ取り込むと、中身の違う同名のレイヤーの側が使われなくなる。
        own = os.path.join(self.root, ".ccnavi", "config", "project", "templates", "i0001.yml")
        write(own, self.read_draft().decode().replace("先にテスト", "別の中身"))
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("使われなくなる", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        # 逆に、レイヤーへ取り込むと共通の側が先に在って使われない。
        os.remove(own)
        self.existing(self.read_draft().decode().replace("先にテスト", "共通の中身"))
        result = self.import_("i0001", "--to", "project", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("使われない", result.stderr)
        self.assertFalse(os.path.exists(own))

    def test_the_same_content_in_the_other_layer_is_only_an_info(self):
        self.existing()
        result = self.import_("i0001", "--to", "project", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("全欄が同じ", result.stdout)

    # ---- 下書きを消す条件

    def prepared(self):
        settings_, _ = settings.load(self.root)
        plan = template_drafts.prepare(settings_, self.root, "i0001", "common")
        self.assertTrue(plan.ok, plan.errors)
        return plan

    def test_a_draft_rewritten_after_the_import_is_kept_and_said_so(self):
        plan = self.prepared()
        write(self.draft_path(), self.read_draft().decode() + "# 取り込んだあとの書き足し\n")
        result = template_drafts.commit(plan, self.sha)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.written)
        self.assertFalse(result.removed)
        self.assertIn("書き直されている", result.kept)
        self.assertTrue(os.path.exists(self.draft_path()))
        self.assertTrue(os.path.exists(self.common_file()))

    @requires_symlink
    def test_a_draft_that_became_a_link_is_not_removed(self):
        plan = self.prepared()
        real = write(os.path.join(self.root, "real.yml"), self.read_draft().decode())
        os.remove(self.draft_path())
        os.symlink(real, self.draft_path())
        result = template_drafts.commit(plan, self.sha)
        self.assertTrue(result.written)
        self.assertFalse(result.removed)
        self.assertIn("消していない", result.kept)
        self.assertTrue(os.path.islink(self.draft_path()))
        self.assertTrue(os.path.exists(real))

    def test_a_draft_that_became_a_hard_link_is_not_removed(self):
        plan = self.prepared()
        other = write(os.path.join(self.root, "other.yml"), self.read_draft().decode())
        os.remove(self.draft_path())
        try:
            os.link(other, self.draft_path())
        except OSError:
            self.skipTest("ハードリンクを作れない")
        result = template_drafts.commit(plan, self.sha)
        self.assertTrue(result.written)
        self.assertFalse(result.removed)
        self.assertTrue(os.path.exists(self.draft_path()))

    # ---- リンクには書かない

    @requires_symlink
    def test_a_linked_target_is_not_written(self):
        outside = write(os.path.join(self.root, "outside.yml"), "keep\n")
        place = self.common_file()
        os.makedirs(os.path.dirname(place), exist_ok=True)
        os.symlink(outside, place)
        result = self.import_("i0001", "--to", "common", "--replace", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("リンク", result.stderr)
        self.assertEqual(slurp(outside), b"keep\n")
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_a_hard_linked_target_is_not_written(self):
        outside = write(os.path.join(self.root, "outside.yml"), "keep\n")
        place = self.common_file()
        os.makedirs(os.path.dirname(place), exist_ok=True)
        try:
            os.link(outside, place)
        except OSError:
            self.skipTest("ハードリンクを作れない")
        result = self.import_("i0001", "--to", "common", "--replace", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ハードリンク", result.stderr)
        self.assertEqual(slurp(outside), b"keep\n")

    @requires_symlink
    def test_a_linked_place_is_not_written(self):
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        place = os.path.dirname(self.common_file())
        os.makedirs(os.path.dirname(place), exist_ok=True)
        os.symlink(outside, place)
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.exists(self.draft_path()))

    @requires_symlink
    def test_a_linked_draft_cannot_be_imported(self):
        real = write(os.path.join(self.root, "real.yml"), self.read_draft().decode())
        os.remove(self.draft_path())
        os.symlink(real, self.draft_path())
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.islink(self.draft_path()))

    # ---- json

    def test_the_json_preview_and_result(self):
        shown = json.loads(self.import_("i0001", "--to", "common", "--json").stdout)
        self.assertTrue(shown["ok"])
        self.assertEqual(shown["draft"]["sha"], self.sha)
        self.assertEqual(shown["target"], ".ccnavi/config/common/templates/i0001.yml")
        self.assertFalse(shown["written"])
        done = json.loads(
            self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha, "--json").stdout
        )
        self.assertTrue(done["ok"])
        self.assertTrue(done["written"])
        self.assertTrue(done["removed"])

    def test_via_board_skips_the_terminal_check(self):
        # 拡張の子プロセスは端末を持たない。--via board なら端末の確かめを飛ばす。
        result = self.import_(
            "i0001",
            "--to",
            "common",
            "--yes",
            "--expect",
            self.sha,
            "--via",
            "board",
            "--json",
            guard="enable",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(self.common_file()))
        self.assertTrue(json.loads(result.stdout)["written"])

    def test_without_via_board_the_terminal_check_stays(self):
        result = self.import_(
            "i0001", "--to", "common", "--yes", "--expect", self.sha, guard="enable"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("端末", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))

    def test_via_board_needs_expect_and_json(self):
        result = self.import_("i0001", "--to", "common", "--yes", "--via", "board", "--json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--expect", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))
        result = self.import_(
            "i0001", "--to", "common", "--yes", "--expect", self.sha, "--via", "board"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--json", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_via_terminal_is_refused_for_import(self):
        result = self.import_(
            "i0001",
            "--to",
            "common",
            "--yes",
            "--expect",
            self.sha,
            "--via",
            "terminal",
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("board", result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))

    def test_new_text_is_in_the_preview_json(self):
        # 同名が無い（新規の）取り込みの下見に、取り込む本文が出る。
        shown = json.loads(self.import_("i0001", "--to", "common", "--json").stdout)
        self.assertFalse(shown["exists"])
        draft = self.read_draft().decode()
        line = next(item for item in draft.splitlines() if item.startswith("name:"))
        self.assertIn(line, shown["new_text"])

    # ---- 組み合わせの誤り

    def test_wrong_combinations_are_refused(self):
        for args, needle in (
            (["--import", "i0001", "--to", "common"], "--templates"),
            (["--draft", "i0001"], "--templates"),
            (["--templates", "--import", "i0001"], "--to"),
            (["--templates", "--import", "i0001", "--to", "elsewhere"], "--to"),
            (["--templates", "--import", "i0001", "--to", "common", "--yes", "x"], "--yes"),
            (["--templates", "--draft", "i0001", "--import", "i0001"], "一緒に使えない"),
            (["--templates", "--draft", "i0001", "--to", "common"], "--import でだけ"),
            (["--templates", "--draft", "i0001", "--yes"], "--yes"),
            (["--templates", "--name", "x"], "--draft でだけ"),
            (["--templates", "--to", "common"], "--import と一緒に"),
            (["--templates", "--replace"], "--import と一緒に"),
            (["--templates", "--expect", "x"], "--import と一緒に"),
            (["--templates", "--import", "x", "--to", "common", "--layer", "self"], "--layer"),
            (["--templates", "--import", "i0001", "--to", "common", "--keyword", "x"], "--keyword"),
            (["--templates", "--import", "x", "--to", "common", "--lint"], "--lint"),
        ):
            result = self.tpl(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(needle, result.stderr, args)
        self.assertFalse(os.path.exists(self.common_file()))

    def test_a_bare_yes_is_only_for_the_import(self):
        result = self.ccnavi("--agree", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes には識別子", result.stderr)

    def test_a_bare_yes_without_expect_is_refused_before_reading_anything(self):
        result = self.import_("i0001", "--to", "common", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--expect", result.stderr)

    # ---- 書くときの端末の確かめ

    def test_writing_needs_a_terminal_at_run_time_but_the_preview_does_not(self):
        # hook の文字列照合だけを歯止めにしない。書くとき（--yes）は標準入力が端末であること。
        refused = self.import_(
            "i0001", "--to", "common", "--yes", "--expect", self.sha, guard="enable"
        )
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("端末", refused.stderr)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))
        # 下見は端末でなくても動く。
        preview = self.import_("i0001", "--to", "common", guard="enable")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("何も書いていない", preview.stdout)
        # 切り方（テスト用）を使えば書ける。
        done = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # ---- 制御文字・書式文字

    HIDDEN = "\u202e"  # 右から左への上書き
    ZERO = "\u200b"  # 幅ゼロの空白

    def hide(self, *chars):
        """下書きの先にテストの説明へ文字を混ぜる。(新しいハッシュ)。"""
        text = self.read_draft().decode().replace("先にテスト", "先に" + "".join(chars) + "テスト")
        write(self.draft_path(), text)
        return plan_templates.digest(text.encode())

    def test_hidden_characters_are_an_error_and_shown_as_escapes_in_the_preview(self):
        sha = self.hide(self.HIDDEN, self.ZERO)
        result = self.import_("i0001", "--to", "common")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertNotIn(self.HIDDEN, out)
        self.assertNotIn(self.ZERO, out)
        self.assertIn("\\u202e", out)
        self.assertIn("\\u200b", out)
        self.assertIn("制御文字・書式文字が含まれている", out)
        # error の下書きは取り込めない。
        done = self.import_("i0001", "--to", "common", "--yes", "--expect", sha)
        self.assertEqual(done.returncode, 1)
        self.assertFalse(os.path.exists(self.common_file()))
        self.assertTrue(os.path.exists(self.draft_path()))

    def test_the_check_reports_each_kind_of_hidden_character_as_an_error(self):
        for char in ("\u202e", "\u200b", "\u200d", "\u2060", "\ufeff", "\u2028", "\u2029", "\x1b"):
            with self.subTest(char=repr(char)):
                text = self.read_draft().decode()
                template, _ = plan_templates.parse_template(text.encode(), "i0001")
                template.description += char
                found = plan_templates.check(template, None, None)
                self.assertTrue(
                    any(
                        p.severity == SEVERITY_ERROR
                        and "制御文字・書式文字が含まれている" in p.detail
                        for p in found
                    )
                )

    def test_hidden_characters_in_a_comment_are_an_error_but_crlf_is_not(self):
        text = self.read_draft().decode()
        crlf = text.replace("\n", "\r\n")
        write(self.draft_path(), crlf)
        ok = self.import_("i0001", "--to", "common")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        # コメントの中は、読んだ中身に出ないが、生の本文を見て止める。
        write(self.draft_path(), text + "# 末尾\u202e隠す\n")
        bad = self.import_("i0001", "--to", "common")
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertNotIn("\u202e", bad.stdout + bad.stderr)
        self.assertIn("\\u202e", bad.stdout + bad.stderr)

    def test_a_diff_shows_hidden_characters_as_escapes_too(self):
        self.existing(self.read_draft().decode().replace("先にテスト", "先に\u202e実装"))
        result = self.import_("i0001", "--to", "common", "--replace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("\u202e", result.stdout)
        self.assertIn("\\u202e", result.stdout)

    def test_hidden_characters_do_not_change_what_is_written(self):
        # 表示だけを直す。書く中身はバイト列のまま（error でなければ書く）。
        plan = self.prepared()
        self.assertEqual(plan.raw, self.read_draft())

    def test_the_layer_flag_with_to_common_is_refused(self):
        result = self.import_("i0001", "--to", "common", "--layer", "self")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--layer", result.stderr)


class UserOnlyTest(Drafts):
    """`--import` はユーザ専用。エージェントが打った形は止まり、`--draft` と読む側は通る。"""

    def denied(self, command) -> bool:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": command},
        }
        result = self.ccnavi(
            "--mode", "enable", "--guard-ticket-approval", "enable", stdin=json.dumps(payload)
        )
        return DENY in self.reason(result)

    def test_the_agent_cannot_type_the_import(self):
        for command in (
            "ccnavi --templates --import i0001 --to common",
            "ccnavi --templates --import i0001 --to common --yes --expect abc",
            "ccnavi --templates --import i0001 --to project --layer lib --replace",
            "./.ccnavi/bin/ccnavi --templates --import i0001 --to common",
            "uv run python -m ccnavi --templates --import i0001 --to common",
            "ccnavi --import i0001 --to common --templates",
            "cd /tmp && ccnavi --templates --import x --to common",
            "ccnavi --templates --im''port x --to common",
            "ccnavi --templates --import=x --to common",
            "ccnavi --templates --import i0001 --to common $X",
            "ccnavi --templates $A $B",
            "ccnavi --templates ${A} --yes",
        ):
            self.assertTrue(self.denied(command), command)

    def test_the_agent_can_type_the_draft_and_the_read_side(self):
        for command in (
            "ccnavi --templates --draft i0001",
            "ccnavi --templates --draft i0001 --name feature",
            "ccnavi --templates",
            "ccnavi --templates --json",
            "ccnavi --templates --keyword 調査",
            "ccnavi --templates --show feature",
            "./.ccnavi/bin/ccnavi --templates --draft i0001 --json",
        ):
            self.assertFalse(self.denied(command), command)

    def test_the_message_names_the_import(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": "ccnavi --templates --import x --to common"},
        }
        result = self.ccnavi(
            "--mode", "enable", "--guard-ticket-approval", "enable", stdin=json.dumps(payload)
        )
        self.assertIn("--templates --import", self.reason(result))

    def test_the_board_import_forms_are_denied_whatever_the_launcher(self):
        tail = (
            "--templates --import X --to common --yes --expect " + "a" * 64 + " --via board --json"
        )
        heads = (
            "ccnavi ",
            "python3 -u -m ccnavi ",
            "python3 -X utf8 -m ccnavi ",
            "uv run --project . python -m ccnavi ",
            "uv run -m ccnavi ",
            "py -m ccnavi ",
            "python -m ccnavi.__main__ ",
            "python3 main.py ",
            "uv run python main.py ",
            "uvx ccnavi ",
            "npx ccnavi ",
            "pnpm exec ccnavi ",
            "env X=1 ccnavi ",
        )
        commands = [head + tail for head in heads]
        commands += [
            "ccnavi --via board --yes --templates --import X --to common --expect "
            + "a" * 64
            + " --json",
            "ccnavi --templates --import=X --to common --yes --expect " + "a" * 64 + " --via=board",
        ]
        for command in commands:
            self.assertTrue(self.denied(command), command)

    def test_a_reader_may_mention_the_import_flags(self):
        for command in (
            'grep -rn -- "--templates --import X --yes" README.md',
            'rg -n "templates --import .* --yes" docs',
        ):
            self.assertFalse(self.denied(command), command)


class JudgmentUnchangedTest(Drafts):
    """判定・組み込みの保護・rules.yml は変えない。下書きの置き場への書き込みは止まらない。"""

    def write_to(self, path, tool="Write", agent_id=""):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"file_path": path, "content": "x"},
        }
        if agent_id:
            payload["agent_id"] = agent_id
        return self.ccnavi(
            "--guard-core-files", "enable", "--mode", "enable", stdin=json.dumps(payload)
        )

    def decision(self, result) -> str:
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def test_the_agent_may_write_the_draft_place(self):
        for tool in ("Write", "Edit"):
            for agent_id in ("", "sub-1"):
                result = self.write_to(self.draft_path("x"), tool, agent_id)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(self.decision(result), "deny", (tool, agent_id, result.stdout))
                self.assertNotIn("builtin-guard", result.stdout)

    def test_the_shell_may_write_the_draft_place_too(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": "cp /tmp/x wip/proposals/templates/x.yml"},
        }
        result = self.ccnavi(
            "--guard-core-files", "enable", "--mode", "enable", stdin=json.dumps(payload)
        )
        self.assertNotEqual(self.decision(result), "deny", result.stdout)

    def test_the_effective_places_are_still_protected(self):
        for rel in (
            ".ccnavi/config/common/templates/x.yml",
            ".ccnavi/config/project/templates/x.yml",
        ):
            path = os.path.join(self.parent_tree, *rel.split("/"))
            result = self.write_to(path)
            self.assertEqual(self.decision(result), "deny", (rel, result.stdout))
            self.assertIn("builtin-guard", result.stdout)

    def test_the_draft_place_is_not_part_of_the_post_check(self):
        write(self.draft_path("x"), "name: x\n")
        ok = write(os.path.join(self.parent_tree, "wip", "a.md"), "a\n")
        result = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok, content="a")
        said = result.stdout + result.stderr
        self.assertNotIn("POST_TICKET_SCOPE", said)
        self.assertNotIn("POST_VIOLATION", said)

    def test_a_draft_is_not_read_as_a_ticket(self):
        write(self.draft_path("x"), plan_template("x", "  - design\n"))
        git(self.parent_tree, "add", "-A")
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["tickets"], [])


if __name__ == "__main__":
    unittest.main()
