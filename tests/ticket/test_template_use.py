"""テンプレートを使う側（`ccnavi ticket new <識別子> --template <名前>`）の受入テスト。

見るのは次のとおり。

1. 親の提案: テンプレートのステップ（`type`・`review`・`after`）を `plan:` / `feedback:` に写す。
   `after` は計画の中の添字から提案の絶対番号に付け直す
   （フィードバック計画は全体のステップ数を足す）。`phases:` の写しが入り、`--lint` と承認の確認（`--agree --preview --verify`）、
   承認の検査（`agree_candidates.plan_problems`）が error にならない
2. 往復: 完了した親から `--templates --draft` で作った下書きを `user handoff` で取り込み、
   `--template` で使うと、元の計画と同じ形・同じフローになる
3. 子のフローの下書き: 全体計画の n 番目のステップの最初の子 `<親>-<NN>-01` の
   `wip/proposals/flows/<子>.yml` に書く。`flow.load` で読める。効力が無いことを出力で言う
4. 断る: 無い名前、使えない名前（error・読めない）、定義が phases.yml に無い、kind が
   置き場と合わない、`--plan` との併用、既にある提案・フロー下書き。断ったときは何も書かない
5. 統合結果: 共通とレイヤーで衝突した名前は共通が引かれ、そのことを言う。`--layer` で選ぶ
"""

from __future__ import annotations

import os
import textwrap

from ccnavi.infra import settings
from ccnavi.tickets import (
    agree_candidates,
    flow,
    phasetypes,
    plan_templates,
    proposal_form,
    workflow,
)
from ccnavi.tickets import ticket as ticket_mod
from tests.ticket.test_parent_phases import front_of
from tests.ticket.test_template_drafts import FLOW_IMPL, Drafts, target_rel
from tests.ticket.test_ticket import git, write
from tests.ticketsample import parent_text

IDENT = "feature-9-from-template"

FEATURE = textwrap.dedent(
    """\
        name: feature
        description: 機能を設計から実装まで進める
        when: 新しい機能を足すとき
        plan:
          - research
          - {type: design, review: defer, after: [1]}
          - {type: implement, after: [2]}
        feedback:
          - implement-feedback
          - {type: implement-feedback, after: [1]}
        flows:
          3:
        """
) + textwrap.indent(FLOW_IMPL, "    ")

SMALL = """\
name: small
description: 設計して実装する
plan:
  - design
  - {type: implement, after: [1]}
"""


def variant(name: str, *pairs: tuple[str, str]) -> str:
    """SMALL の名前を変え、文字列の置き換えを重ねた本文。"""
    text = SMALL.replace("name: small", f"name: {name}")
    for old, new in pairs:
        text = text.replace(old, new)
    return text


class Use(Drafts):
    """テンプレートを置いて `ticket new --template` を打つ道具。"""

    def setUp(self):
        super().setUp()
        self.tree = self.worktree(IDENT, "main")
        self.todo = os.path.join(self.tree, "wip", "proposals", "todo", IDENT + ".md")

    def put(self, name, text, layer="common"):
        if layer == "common":
            directory = settings.common_templates_dir(self.conf, self.root)
        else:
            directory = settings.layer_templates_dir(self.conf, self.root)
        return write(os.path.join(directory, name + ".yml"), text)

    def new(self, *extra, ident=IDENT, title="試す", cwd=None, template="feature"):
        args = ["ticket", "new", ident, "--title", title]
        if template is not None:
            args += ["--template", template]
        # 親の提案は issue と MR のセット。渡さないテストのために既定を足す（渡したものが優先）
        for flag, value in (("--issue", "9"), ("--mr", "5")):
            if flag not in extra:
                args += [flag, value]
        args += [*extra, "--cwd", cwd or self.tree]
        return self.ccnavi(*args)

    def written(self):
        with open(self.todo, encoding="utf-8") as f:
            return f.read()

    def flows_dir(self):
        return os.path.join(self.tree, "wip", "proposals", "flows")

    def child(self, n, seq=1):
        return f"{IDENT}-{n:02d}-{seq:02d}"

    def draft_of(self, n):
        return os.path.join(self.flows_dir(), self.child(n) + ".yml")

    def nothing_written(self):
        self.assertFalse(os.path.exists(self.todo))
        self.assertFalse(os.path.exists(self.flows_dir()))


class ProposalTest(Use):
    def setUp(self):
        super().setUp()
        self.put("feature", FEATURE)

    def test_the_plan_and_feedback_are_written_with_absolute_after(self):
        result = self.new("--issue", "9", "--with-feedback")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertEqual(front["ticket"], IDENT)
        self.assertEqual(front["issue"], 9)
        self.assertEqual(front["title"], "試す")
        self.assertEqual(
            front["plan"],
            [
                "research",
                {"type": "design", "review": "defer", "after": [1]},
                {"type": "implement", "after": [2]},
            ],
        )
        # フィードバック計画の after は全体計画（3 ステップ）の続きの番号になる: 1 -> 4。
        self.assertEqual(
            front["feedback"],
            ["implement-feedback", {"type": "implement-feedback", "after": [4]}],
        )
        self.assertEqual(
            set(front["phases"]), {"research", "design", "implement", "implement-feedback"}
        )
        self.assertEqual(front["phases"]["design"]["scope"], ["wip/design/*"])

    def test_allow_collects_the_scope_of_the_plan_and_feedback_types(self):
        self.assertEqual(self.new().returncode, 0)
        globs = [e["glob"] for e in front_of(self.written())["allow"]]
        self.assertEqual(globs, ["wip/research/*", "wip/design/*", "src/*", "tests/*"])

    def test_the_proposal_passes_the_approval_checks(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        parsed, notes = ticket_mod.parse(self.written())
        self.assertIsNotNone(parsed, notes)
        config = phasetypes.load_types(self.conf, self.root, "")
        found = agree_candidates.plan_problems(parsed, config)
        self.assertEqual([p.detail for p in found if p.severity == "error"], [])
        # 雛形の空欄の目印は、書き換えるまで承認の検査が止める
        text = self.written()
        for mark in proposal_form.PLACEHOLDERS:
            text = text.replace(mark, "書いた内容")
        with open(self.todo, "w", encoding="utf-8") as f:
            f.write(text)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("error 0 件", lint.stdout)
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_the_output_says_what_was_written_and_what_the_flow_draft_is(self):
        result = self.new("--with-feedback")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn(f"{IDENT} の提案を", out)
        self.assertIn("plan（3 ステップ）", out)
        self.assertIn("feedback（2 ステップ）", out)
        self.assertIn("フローの下書き 1 件", out)
        self.assertIn(self.child(3), out)
        self.assertIn("下書きには効力がありません。", out)
        self.assertIn("フロー編集画面", out)
        self.assertIn("ccnavi-launcher.sh --lint", out)

    def test_the_feedback_plan_is_not_written_unless_asked_for(self):
        result = self.new()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertNotIn("feedback", front)
        self.assertEqual(len(front["plan"]), 3)
        self.assertNotIn("implement-feedback", front["phases"])
        # テンプレートにフィードバック計画があることと、足し方を言う。
        self.assertIn("フィードバック計画（2 ステップ）がある", result.stdout)
        self.assertIn("--with-feedback", result.stdout)
        self.assertNotIn("feedback（", result.stdout)
        # 全体計画だけの提案は、承認のあとで全体計画を改版できる形のまま（検査を通る）。
        parsed, notes = ticket_mod.parse(self.written())
        self.assertIsNotNone(parsed, notes)
        config = phasetypes.load_types(self.conf, self.root, "")
        found = agree_candidates.plan_problems(parsed, config)
        self.assertEqual([p.detail for p in found if p.severity == "error"], [])

    def test_a_template_without_a_feedback_plan_says_nothing_about_it(self):
        self.put("small", SMALL)
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--with-feedback", result.stdout)
        self.assertEqual(
            front_of(self.written())["plan"], ["design", {"type": "implement", "after": [1]}]
        )

    def test_with_feedback_on_a_template_without_one_writes_nothing_extra(self):
        self.put("small", SMALL)
        result = self.new("--with-feedback", template="small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("feedback", front_of(self.written()))

    def test_a_template_without_flows_writes_no_flow_draft(self):
        self.put("small", SMALL)
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("フローの下書きはありません", result.stdout)
        self.assertFalse(os.path.exists(self.flows_dir()))
        self.assertEqual(
            front_of(self.written())["plan"], ["design", {"type": "implement", "after": [1]}]
        )

    def test_a_warning_of_the_template_is_shown_but_does_not_stop(self):
        self.put("todo", variant("todo", ("設計して", "TODO 設計して")))
        result = self.new(template="todo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warn:", result.stdout)
        self.assertIn("TODO", result.stdout)


class FlowDraftTest(Use):
    def setUp(self):
        super().setUp()
        self.put("feature", FEATURE)

    def test_the_flow_goes_to_the_first_child_of_the_phase_and_loads(self):
        self.assertEqual(self.new().returncode, 0)
        place = self.draft_of(3)
        self.assertEqual(os.listdir(self.flows_dir()), [self.child(3) + ".yml"])
        data, why = flow.load(place, self.tree)
        self.assertEqual(why, "")
        self.assertEqual(data["name"], "実装の手順")
        self.assertEqual([n["id"] for n in data["nodes"]], ["s", "p", "e"])
        # フローの本文と同じ中身（先にテスト）が文の前後まで残る。
        self.assertEqual(data["nodes"][1]["data"]["prompt"], "先にテスト")

    def test_the_draft_has_no_effect_it_is_not_the_approved_flow(self):
        self.assertEqual(self.new().returncode, 0)
        approved = flow.flow_file(self.conf, self.tree, self.child(3))
        self.assertFalse(os.path.exists(approved))

    def test_an_existing_flow_draft_is_not_overwritten_and_nothing_is_written(self):
        write(self.draft_of(3), "keep\n")
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertIn(self.child(3), result.stderr)
        self.assertFalse(os.path.exists(self.todo))
        with open(self.draft_of(3), encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep\n")

    def test_an_existing_child_proposal_stops_it(self):
        write(os.path.join(self.tree, "wip", "proposals", "todo", self.child(3) + ".md"), "x\n")
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_a_long_identifier_whose_child_id_does_not_fit_is_refused(self):
        long = "feature-9-" + "x" * 50
        tree = self.worktree(long, "main")
        result = self.new(ident=long, cwd=tree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("子の識別子", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(tree, "wip", "proposals")))


class RefusalTest(Use):
    def test_an_unknown_template_lists_the_usable_ones(self):
        self.put("small", SMALL)
        result = self.new(template="nothing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing", result.stderr)
        self.assertIn("small", result.stderr)
        self.nothing_written()

    def test_no_templates_at_all_is_said(self):
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("無い", result.stderr)
        self.nothing_written()

    def test_a_template_with_an_error_is_not_used(self):
        # after が自分以降を指す（順序の error）。
        self.put("bad", variant("bad", ("after: [1]", "after: [2]")))
        result = self.new(template="bad")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr)
        self.assertIn("bad", result.stderr)
        self.nothing_written()

    def test_a_template_that_cannot_be_read_is_not_used(self):
        self.put("odd", variant("other"))
        result = self.new(template="odd")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("使えない", result.stderr)
        self.nothing_written()

    def test_a_type_missing_from_phases_yml_is_refused_without_writing(self):
        self.put("ghost", variant("ghost", ("implement", "ghost-type")))
        result = self.new(template="ghost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ghost-type", result.stderr)
        self.assertIn("phases.yml", result.stderr)
        self.nothing_written()

    def test_a_type_of_the_wrong_kind_is_refused(self):
        # 全体計画に feedback の定義を置いている（テンプレートの検査は warn にとどめる）。
        self.put("wrong", variant("wrong", ("implement", "implement-feedback")))
        result = self.new(template="wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kind", result.stderr)
        self.nothing_written()

    def test_plan_and_template_together_are_refused(self):
        self.put("small", SMALL)
        result = self.new("--plan", "design", template="small")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--plan", result.stderr)
        self.assertIn("--template", result.stderr)
        self.nothing_written()

    def test_an_existing_proposal_is_not_overwritten(self):
        self.put("small", SMALL)
        self.put("feature", FEATURE)
        self.assertEqual(self.new(template="small").returncode, 0)
        before = self.written()
        result = self.new(template="feature")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertEqual(self.written(), before)

    def test_without_phases_yml_it_stops(self):
        self.put("small", SMALL)
        os.remove(os.path.join(self.root, ".ccnavi", "config", "common", "phases.yml"))
        os.remove(os.path.join(self.root, ".ccnavi", "config", "project", "phases.yml"))
        result = self.new(template="small")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phases.yml", result.stderr)
        self.nothing_written()

    def test_outside_the_worktree_it_explains_how_to_cut_one(self):
        self.put("small", SMALL)
        result = self.new(template="small", cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ccnavi-start.sh --mr <MR の番号>", result.stderr)
        self.assertNotIn("。。", result.stderr)


class LayerTest(Use):
    def test_a_name_that_clashes_uses_the_common_one_and_says_so(self):
        self.put("small", SMALL)
        self.put("small", SMALL.replace("設計して実装する", "別の中身"), layer="self")
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note:", result.stdout)
        self.assertIn("共通と同じ名前で中身が違う", result.stdout)
        self.assertIn(".ccnavi/config/common/templates/small.yml", result.stdout)

    def test_a_template_only_in_the_layer_is_used(self):
        self.put("small", SMALL, layer="self")
        result = self.new(
            template="small",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(".ccnavi/config/project/templates/small.yml", result.stdout)

    def test_an_unknown_layer_is_refused(self):
        self.put("small", SMALL)
        result = self.new("--layer", "nowhere", template="small")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nowhere", result.stderr)
        self.nothing_written()

    def test_layer_without_template_is_refused(self):
        result = self.new("--layer", "self", "--plan", "design", template=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--template", result.stderr)
        self.nothing_written()

    def test_with_feedback_is_only_for_ticket_new_template(self):
        self.put("small", SMALL)
        for argv, needle in (
            (["--templates", "--with-feedback"], "--with-feedback は"),
            (["--templates", "--template", "small", "--with-feedback"], "--template は"),
            (["ticket", "status", "--with-feedback"], "--with-feedback は"),
            (
                ["ticket", "new", "x", "--title", "t", "--plan", "design", "--with-feedback"],
                "--with-feedback は",
            ),
        ):
            with self.subTest(argv=argv):
                result = self.tpl(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(needle, result.stderr)
        self.nothing_written()

    def test_template_is_only_for_ticket_new(self):
        self.put("small", SMALL)
        for argv in (
            ["--templates", "--template", "small"],
            ["ticket", "status", "--template", "small"],
        ):
            with self.subTest(argv=argv):
                result = self.tpl(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--template は", result.stderr)

    def test_layer_still_needs_templates_for_other_commands(self):
        result = self.tpl("ticket", "status", "--layer", "self")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--templates", result.stderr)


class RoundTripTest(Use):
    """完了した親 -> `--draft` -> `user handoff` -> `--template` で、元の計画とフローに戻る。

    下書きは親のツリーにできる。使う側はワークスペースルートのテンプレートを読むので、ここでは
    下書きをワークスペースルートの `wip/handoff/` へ移してから取り込む（親のツリーで取り込んで
    親のブランチから合流するのと同じ結果）。
    """

    to = "common"

    def take(self):
        rel = target_rel(to="common")
        moved = os.path.join(self.root, "wip", "handoff", *rel.split("/"))
        write(moved, self.read_draft().decode("utf-8"))
        os.remove(self.draft_path())
        return self.handoff(rel, tree=self.root)

    PLAN = [
        {"type": "research", "after": []},
        {"type": "design", "review": "defer", "after": [1]},
        {"type": "implement", "after": [1, 2]},
    ]
    FEEDBACK = [
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [4]},
    ]
    # defer・複数の after・同じ型の繰り返し、フィードバック計画の after が複数（5, 6 の続き）。
    RICH_PLAN = [
        {"type": "research", "after": []},
        {"type": "design", "review": "defer", "after": [1]},
        {"type": "design", "after": [1]},
        {"type": "implement", "after": [2, 3]},
    ]
    RICH_FEEDBACK = [
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [5]},
        {"type": "implement-feedback", "review": "defer", "after": [5, 6]},
        {"type": "implement-feedback", "after": [5, 7]},
    ]

    def roundtrip(self, plan, feedback, flow_child):
        """`--draft` -> 取り込み -> `--template ... --with-feedback`。元と作った提案を返す。"""
        self.done_parent(plan, feedback=feedback, flows={flow_child: FLOW_IMPL})
        self.assertEqual(self.draft().returncode, 0)
        self.generalize()
        imported = self.take()
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)

        result = self.new("--with-feedback", template="i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        original, _ = ticket_mod.parse(parent_text("i0001", plan, feedback))
        made, notes = ticket_mod.parse(self.written())
        self.assertIsNotNone(made, notes)
        return original, made

    def assert_same_plan(self, original, made):
        self.assertEqual([i.as_raw() for i in made.plan], [i.as_raw() for i in original.plan])
        self.assertEqual(
            [i.as_raw() for i in made.feedback], [i.as_raw() for i in original.feedback]
        )
        wanted = workflow.compute(original)
        got = workflow.compute(made)
        self.assertEqual(got.waits, wanted.waits)
        self.assertEqual(got.review_at, wanted.review_at)

    def test_a_template_drafted_from_a_done_parent_gives_the_same_plan_and_flow(self):
        original, made = self.roundtrip(self.PLAN, self.FEEDBACK, "i0001-03-01")
        self.assert_same_plan(original, made)
        wanted, why = flow.parse(FLOW_IMPL.encode("utf-8"))
        self.assertEqual(why, "")
        data, why = flow.load(self.draft_of(3), self.tree)
        self.assertEqual(why, "")
        self.assertEqual(data, wanted)

    def test_defer_multiple_after_repeated_types_and_feedback_after_round_trip(self):
        original, made = self.roundtrip(self.RICH_PLAN, self.RICH_FEEDBACK, "i0001-04-01")
        self.assertTrue(workflow.compute(original).review_at)
        self.assert_same_plan(original, made)
        self.assertEqual([i.after for i in made.feedback], [[], [5], [5, 6], [5, 7]])
        data, why = flow.load(self.draft_of(4), self.tree)
        self.assertEqual(why, "")

    def test_without_with_feedback_the_main_plan_alone_round_trips(self):
        self.done_parent(self.PLAN, feedback=self.FEEDBACK, flows={"i0001-03-01": FLOW_IMPL})
        self.assertEqual(self.draft().returncode, 0)
        self.generalize()
        taken = self.take()
        self.assertEqual(taken.returncode, 0, taken.stdout + taken.stderr)
        result = self.new(template="i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        original, _ = ticket_mod.parse(parent_text("i0001", self.PLAN, self.FEEDBACK))
        made, notes = ticket_mod.parse(self.written())
        self.assertIsNotNone(made, notes)
        self.assertEqual([i.as_raw() for i in made.plan], [i.as_raw() for i in original.plan])
        self.assertFalse(made.feedback)
        self.assertIn("フィードバック計画（2 ステップ）がある", result.stdout)

    def test_absolute_is_the_inverse_of_the_draft_normalization(self):
        from ccnavi.tickets import template_use

        template = plan_templates.Template(
            name="t",
            description="d",
            plan=[plan_templates.Item("research"), plan_templates.Item("design", after=[1])],
            feedback=[
                plan_templates.Item("implement-feedback"),
                plan_templates.Item("implement-feedback", after=[1]),
            ],
        )
        plan, feedback = template_use.absolute(template)
        self.assertEqual([i.after for i in plan], [[], [1]])
        # 全体計画は 2 ステップなので、フィードバック計画の 1 は 3 になる。
        self.assertEqual([i.after for i in feedback], [[], [3]])
        # 元の Template は変えない。
        self.assertEqual([i.after for i in template.feedback], [[], [1]])
