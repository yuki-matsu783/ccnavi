"""作業・レビュー計画のテンプレートの下書きの受入テスト。

見るのは次のとおり。

1. 下書きの置き場: 親のツリーの `wip/handoff/<取り込み先の相対パス>`。取り込み先は `--to`
   （既定は project）と `--layer` で決まる。取り込みは `ccnavi user handoff`（test_handoff.py）
2. `--templates --draft <親>`: 完了した親の計画から下書きを作る。`after` を計画の中の添字に直し、
   子のフローをコピーし、同名の下書きは上書きせず断る。取り込み先に同名のテンプレートがあれば断る。
   作業中・退避済み・取り消した親は読めないと言う
3. 下書きの効力: `--templates` の統合結果にも `templates[]` にも入らない。
   `--lint` は error にしない。一覧に件数、`--json` に `drafts[]`
4. `--draft` と読む側はエージェントが打てる。下書きの置き場への Write は判定が変わらず止まらない
"""

from __future__ import annotations

import json
import os
import textwrap
import unittest

from ccnavi.infra import settings
from ccnavi.policy.rules import SEVERITY_ERROR, SEVERITY_WARN
from ccnavi.tickets import plan_templates
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

    # `--draft` の `--to`（下書きの置き場が決まる）。
    to = "project"

    def setUp(self):
        super().setUp()
        # 共通のフェーズ定義。取り込み先が共通のとき、この定義で検査される。
        write(common_path(self.root, "phases"), PHASES)
        self.conf, _ = settings.load(self.root)

    def tpl(self, *args, guard=None):
        """`--templates` と `--lint` を動かす。

        PhaseHarness.ccnavi が足す判定のオプションは付けない。

        `guard` はターミナルの確かめ（CCNAVI_GUARD_TICKET_APPROVAL）。`--templates`
        はオプションでは渡せない。
        """
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
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

    def draft_path(self, name="i0001", tree=None, to=None):
        """下書きの置き場。取り込み先（`target_rel`）の前に `wip/handoff/` を付けたもの。"""
        return os.path.join(
            tree or self.parent_tree, "wip", "handoff", *target_rel(name, to or self.to).split("/")
        )

    def draft(self, parent="i0001", *extra):
        to = ("--to", self.to) if self.to != "project" else ()
        return self.tpl("--templates", "--draft", parent, *to, *extra)

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

    # ---- 取り込み（ほかの保護対象と同じ `user handoff`）

    def handoff(self, rel, tree=None, guard="disable"):
        """下書き 1 本を `ccnavi user handoff <rel> --yes --expect <見せたハッシュ>` で取り込む。"""
        tree = tree or self.parent_tree
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        env[settings.GUARD_TICKET_APPROVAL_ENV] = guard
        shown = run_ccnavi(["--root", tree, "user", "handoff", rel, "--json"], env=env, cwd=ROOT)
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        expect = json.loads(shown.stdout)["expect"]
        return run_ccnavi(
            ["--root", tree, "user", "handoff", rel, "--yes", "--expect", expect, "--json"],
            env=env,
            cwd=ROOT,
        )

    def answer(self, *args):
        result = self.tpl("--templates", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


def target_rel(name="i0001", to="project", layer=""):
    """下書きの取り込み先（ツリーからの相対）。"""
    if to == "common":
        return f".ccnavi/config/common/templates/{name}.yml"
    if layer:
        return f"projects/{layer}/.ccnavi/config/project/templates/{name}.yml"
    return f".ccnavi/config/project/templates/{name}.yml"


def plan_templates_dir(conf, root):
    return settings.common_templates_dir(conf, root)


def slurp(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class MakeTest(Drafts):
    def test_a_draft_is_made_from_a_done_parent_and_round_trips_to_a_readable_template(self):
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback", "implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        result = self.draft()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("wip/handoff/.ccnavi/config/project/templates/i0001.yml", result.stdout)
        self.assertIn(
            "ccnavi user handoff .ccnavi/config/project/templates/i0001.yml", result.stdout
        )
        self.assertIn(
            "全体計画 3 ステップ / フィードバック計画 2 ステップ / フロー 1 本", result.stdout
        )
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
        self.assertIn("フィードバック計画の子のフローはコピーしない", result.stdout)

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
        self.assertEqual(sum(line.startswith("次: ") for line in result.stdout.splitlines()), 1)
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
        self.assertFalse(os.path.isdir(os.path.join(self.parent_tree, "wip", "handoff")))

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
        place = os.path.join(self.parent_tree, "wip", "handoff")
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
        self.assertEqual(answer["path"], "wip/handoff/.ccnavi/config/project/templates/i0001.yml")
        self.assertEqual(answer["target"], ".ccnavi/config/project/templates/i0001.yml")
        self.assertEqual(answer["layer"], "self")
        self.assertEqual(answer["sha"], plan_templates.digest(self.read_draft()))
        self.assertEqual(answer["tree"], "i0001")

    def test_to_common_puts_the_draft_at_the_common_place(self):
        self.done_parent(["design"])
        result = self.draft("i0001", "--to", "common", "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["target"], ".ccnavi/config/common/templates/i0001.yml")
        self.assertEqual(answer["layer"], "common")
        self.assertTrue(os.path.isfile(self.draft_path(to="common")))

    def test_a_layer_puts_the_draft_under_that_project(self):
        project = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(project, ".git"))
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0002.md"),
            parent_text("i0002", ["design"]),
        )
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0002-01-01.md"),
            child_text("i0002-01-01", "i0002", 1, ("src/*",)),
        )
        result = self.tpl("--templates", "--draft", "i0002", "--layer", "lib", "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        answer = json.loads(result.stdout)
        self.assertEqual(
            answer["target"], "projects/lib/.ccnavi/config/project/templates/i0002.yml"
        )
        self.assertEqual(answer["layer"], "lib")

    def test_a_layer_outside_the_parent_tree_is_refused(self):
        os.makedirs(os.path.join(self.root, "projects", "lib", ".git"))
        self.done_parent(["design"])
        result = self.draft("i0001", "--layer", "lib")
        self.assertEqual(result.returncode, 1)
        self.assertIn("外", result.stderr)

    def test_a_layer_with_to_common_and_an_unknown_to_are_refused(self):
        self.done_parent(["design"])
        for extra in (("--to", "common", "--layer", "self"), ("--to", "elsewhere")):
            with self.subTest(extra=extra):
                result = self.draft("i0001", *extra)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(os.path.exists(self.draft_path()))

    def test_an_existing_template_at_the_target_is_not_shadowed(self):
        self.done_parent(["design"])
        existing = os.path.join(self.parent_tree, *target_rel().split("/"))
        write(existing, "name: i0001\n")
        result = self.draft()
        self.assertEqual(result.returncode, 1)
        self.assertIn("既に在る", result.stderr)
        self.assertIn("ccnavi-stage.sh", result.stderr)
        self.assertFalse(os.path.exists(self.draft_path()))

    def test_the_draft_is_taken_by_handoff_and_then_used(self):
        self.done_parent(["design", "implement"], flows={"i0001-02-01": FLOW_IMPL})
        self.assertEqual(self.draft().returncode, 0)
        sha = self.generalize()
        taken = self.handoff(target_rel())
        self.assertEqual(taken.returncode, 0, taken.stdout + taken.stderr)
        placed = os.path.join(self.parent_tree, *target_rel().split("/"))
        with open(placed, "rb") as f:
            self.assertEqual(plan_templates.digest(f.read()), sha)
        self.assertFalse(os.path.exists(self.draft_path()))


class EffectTest(Drafts):
    """下書きには効力が無い。統合結果にも一覧にも入らず、lint の error にもならない。"""

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
        self.assertIn("下書き: 1 件（wip/handoff/）", out)
        self.assertEqual(sum("下書き" in line for line in out.splitlines()), 1)

    def test_drafts_in_the_json_carry_what_a_screen_needs_to_offer_the_import(self):
        self.done_parent(["research", "design"], flows={"i0001-02-01": FLOW_IMPL})
        self.draft()
        sha = self.generalize()
        write(os.path.join(self.root, ".ccnavi", "config", "common", "templates", "i0001.yml"), "x")
        (row,) = self.answer()["drafts"]
        self.assertEqual(row["name"], "i0001")
        self.assertEqual(row["tree"], "i0001")
        self.assertEqual(row["path"], "wip/handoff/.ccnavi/config/project/templates/i0001.yml")
        self.assertEqual(row["target"], ".ccnavi/config/project/templates/i0001.yml")
        self.assertEqual(row["layer"], "self")
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
        self.assertIn("wip/handoff/.ccnavi/config/project/templates/broken.yml", details)
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
            self.draft_path("lib-draft", tree=project), plan_template("lib-draft", "  - design\n")
        )
        (row,) = self.answer()["drafts"]
        self.assertEqual(row["checked_with"], "lib")
        self.assertEqual(row["layer"], "lib")
        self.assertEqual(row["tree"], "lib")
        self.assertEqual(row["path"], "wip/handoff/.ccnavi/config/project/templates/lib-draft.yml")

    def test_drafts_off_the_template_rows_are_not_listed(self):
        write(os.path.join(self.root, "wip", "handoff", "learnings", "x.md"), "x\n")
        write(os.path.join(self.root, "wip", "handoff", ".ccnavi", "docs", "a.md"), "a\n")
        self.assertEqual(self.answer()["drafts"], [])

    def test_the_same_draft_checked_out_in_two_trees_is_one_entry(self):
        self.done_parent(["design"])
        self.draft()
        copy = self.draft_path(tree=self.root)
        write(copy, self.read_draft().decode())
        (row,) = self.answer()["drafts"]
        self.assertEqual(sorted([row["tree"], *row["also_in"]]), ["", "i0001"])


def plan_template(name, plan, description="試し"):
    return f"name: {name}\ndescription: {description}\nplan:\n{plan}"


class CheckTest(Drafts):
    def test_the_check_reports_each_kind_of_hidden_character_as_an_error(self):
        self.done_parent(["design"])
        self.assertEqual(self.draft().returncode, 0)
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


class UserOnlyTest(Drafts):
    """取り込みはユーザ専用。`--draft` と読む側は通る。"""

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
            "ccnavi user handoff",
            "ccnavi user handoff .ccnavi/config/project/templates/i0001.yml --json",
            "ccnavi user handoff --yes --expect abc",
            "ccnavi --handoff",
            "./.ccnavi/bin/ccnavi --root /x user handoff --json",
            "uv run python -m ccnavi user handoff",
            "ccnavi --templates ${A} --yes",
        ):
            self.assertTrue(self.denied(command), command)

    def test_the_agent_can_type_the_draft_and_the_read_side(self):
        for command in (
            "ccnavi --templates --draft i0001",
            "ccnavi --templates --draft i0001 --name feature --to common",
            "ccnavi --templates",
            "ccnavi --templates --json",
            "ccnavi --templates --keyword 調査",
            "ccnavi --templates --show feature",
            "./.ccnavi/bin/ccnavi --templates --draft i0001 --json",
        ):
            self.assertFalse(self.denied(command), command)

    def test_the_message_names_the_handoff(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": "ccnavi user handoff"},
        }
        result = self.ccnavi(
            "--mode", "enable", "--guard-ticket-approval", "enable", stdin=json.dumps(payload)
        )
        self.assertIn("ccnavi user handoff", self.reason(result))

    def test_the_board_forms_are_denied_whatever_the_launcher(self):
        tails = ("user handoff X --yes --expect " + "a" * 64 + " --via board --json",)
        heads = (
            "/tmp/copy ",
            "python3 -u -m ccnavi ",
            "uv run -m ccnavi ",
            "python3 main.py ",
            "uvx ccnavi ",
            "npx ccnavi ",
            "env X=1 ccnavi ",
        )
        for tail in tails:
            for head in heads:
                self.assertTrue(self.denied(head + tail), head + tail)

    def test_a_reader_may_mention_the_forms(self):
        for command in (
            'grep -rn -- "user handoff X --yes" README.md',
            'rg -n "user handoff .* --yes" docs',
        ):
            self.assertFalse(self.denied(command), command)


class JudgmentUnchangedTest(Drafts):
    """判定・組み込みの保護・rules.yml は変えない。"""

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

    def test_the_draft_place_under_the_ccnavi_directory_can_be_edited(self):
        # テンプレートの下書きは `wip/handoff/.ccnavi/config/…` に置く。パスに `.ccnavi/` が入って
        # いても、ツリー直下の `wip/handoff/` の中は守る場所のコピーなので、組み込みの保護
        # （ccnavi ディレクトリの下を名指しのツールで止める glob）の例外になる。
        # コピーにはルールとしての効力が無く、ユーザが取り込むまで何も変わらない。
        for tool in ("Write", "Edit"):
            result = self.write_to(self.draft_path("x"), tool)
            self.assertNotEqual(self.decision(result), "deny", (tool, result.stdout))
            self.assertNotIn("builtin:guard-project-home", result.stdout)

    def test_a_look_alike_of_the_draft_place_stays_protected(self):
        # ツリーの直下でない綴りは、守る場所のコピーではないので除かない
        path = os.path.join(
            self.parent_tree, "docs", "wip", "handoff", ".ccnavi", "config", "x.yml"
        )
        result = self.write_to(path)
        self.assertEqual(self.decision(result), "deny", result.stdout)
        self.assertIn("builtin:guard-project-home", result.stdout)

    def test_other_drafts_in_the_handoff_place_are_not_stopped(self):
        result = self.write_to(os.path.join(self.parent_tree, "wip", "handoff", "notes.md"))
        self.assertNotEqual(self.decision(result), "deny", result.stdout)

    def test_the_effective_places_are_still_protected(self):
        for rel in (
            ".ccnavi/config/common/templates/x.yml",
            ".ccnavi/config/project/templates/x.yml",
        ):
            path = os.path.join(self.parent_tree, *rel.split("/"))
            result = self.write_to(path)
            self.assertEqual(self.decision(result), "deny", (rel, result.stdout))
            self.assertIn("builtin:guard", result.stdout)

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
