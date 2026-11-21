"""テンプレートを使う側（`ccnavi ticket new <識別子> --template <名前>`）の受入テスト。

見るのは次のとおり。

1. 親の提案: テンプレートのステップ（`type`・`review`・`after`）を
   `plan:` / `feedback:` にコピーする。
   `after` は計画の中の添字から提案の絶対番号に付け直す
   （フィードバック計画は全体のステップ数を足す）。`phases:` のコピーが入り、
   `--lint` と承認の確認（`--agree --preview --verify`）、
   承認の検査が error にならない
2. 往復: 完了した親から `--templates --draft` で作った下書きを `user handoff` で取り込み、
   `--template` で使うと、元の計画と同じ形・同じフローになる
3. 子のフローの下書き: 全体計画の n 番目のステップの最初の子 `<親>-<NN>-01` の
   `wip/proposals/flows/<子>.yml` に書く。フローとして読める。効力が無いことを出力で言う
4. 断る: 無い名前、使えない名前（error・読めない）、定義が phases.yml に無い、kind が
   置き場と合わない、`--plan` との併用、既にある提案・フロー下書き。断ったときは何も書かない
5. 統合結果: 共通とレイヤーで衝突した名前は共通が引かれ、そのことを言う。`--layer` で選ぶ
"""

from __future__ import annotations

import os
import re
import textwrap

import yaml

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
          - {type: design, deferred: true, after: [1]}
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


# 雛形の空欄の目印（`（ここに…を書く）`）。
PLACEHOLDER = re.compile(r"（ここに[^）\n]*を書く）")


def raw_step(item):
    """計画のステップを、書き方に依らない形（type・review・deferred・after）にする。"""
    if isinstance(item, str):
        item = {"type": item}
    return {
        "type": item["type"],
        "review": item.get("review"),
        "deferred": bool(item.get("deferred", False)),
        "after": list(item.get("after", [])),
    }


def steps(front, key):
    return [raw_step(i) for i in front.get(key) or []]


def read_flow(path):
    """フローの下書きの中身。"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


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
        where = "common" if layer == "common" else "project"
        directory = os.path.join(self.root, ".ccnavi", "config", where, "templates")
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

    def assert_approvable(self):
        """空欄の目印を埋めてコミットすれば、`--lint` に error が無く、承認の確認が通る。"""
        text = PLACEHOLDER.sub("書いた内容", self.written())
        with open(self.todo, "w", encoding="utf-8") as f:
            f.write(text)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("error 0 件", lint.stdout)
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def nothing_written(self):
        self.assertFalse(os.path.exists(self.todo))
        self.assertFalse(os.path.exists(self.flows_dir()))


class ProposalTest(Use):
    def setUp(self):
        super().setUp()
        self.put("feature", FEATURE)

    def test_the_proposal_passes_the_approval_checks(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        # 雛形の空欄の目印は、書き換えるまで承認の検査が止める
        self.assertTrue(PLACEHOLDER.search(self.written()))
        # 全体計画だけの提案は、承認のあとで全体計画を改版できる形のまま（検査を通る）
        self.assert_approvable()

    def test_a_template_without_feedback_or_flows_and_a_warning_that_does_not_stop(self):
        self.put("small", SMALL)
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--with-feedback", result.stdout)
        self.assertIn("フローの下書きはありません", result.stdout)
        self.assertFalse(os.path.exists(self.flows_dir()))
        plan = ["design", {"type": "implement", "after": [1]}]
        self.assertEqual(front_of(self.written())["plan"], plan)
        # フィードバック計画の無いテンプレートに --with-feedback を付けても、何も足さない
        os.remove(self.todo)
        result = self.new("--with-feedback", template="small")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("feedback", front_of(self.written()))
        # テンプレートの warn は出すが、止めない
        os.remove(self.todo)
        self.put("todo", variant("todo", ("設計して", "TODO 設計して")))
        result = self.new(template="todo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warn:", result.stdout)
        self.assertIn("TODO", result.stdout)


class FlowDraftTest(Use):
    def setUp(self):
        super().setUp()
        self.put("feature", FEATURE)

    def test_an_existing_flow_draft_or_child_proposal_stops_it_and_nothing_is_written(self):
        write(self.draft_of(3), "keep\n")
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertIn(self.child(3), result.stderr)
        self.assertFalse(os.path.exists(self.todo))
        with open(self.draft_of(3), encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep\n")
        os.remove(self.draft_of(3))
        write(os.path.join(self.tree, "wip", "proposals", "todo", self.child(3) + ".md"), "x\n")
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertFalse(os.path.exists(self.todo))
        # 子の識別子が長さの上限に収まらない親は断る
        long = "feature-9-" + "x" * 50
        tree = self.worktree(long, "main")
        result = self.new(ident=long, cwd=tree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("子の識別子", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(tree, "wip", "proposals")))


class RefusalTest(Use):
    def test_a_template_that_cannot_be_used_is_refused_without_writing(self):
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("無い", result.stderr)
        self.nothing_written()
        # after が自分以降を指す（順序の error）
        self.put("bad", variant("bad", ("after: [1]", "after: [2]")))
        result = self.new(template="bad")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr)
        self.assertIn("bad", result.stderr)
        self.nothing_written()
        # 名前とファイル名が合わない（読めない）
        self.put("odd", variant("other"))
        result = self.new(template="odd")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("利用できない", result.stderr)
        self.nothing_written()
        # phases.yml に無い型
        self.put("ghost", variant("ghost", ("implement", "ghost-type")))
        result = self.new(template="ghost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ghost-type", result.stderr)
        self.assertIn("phases.yml", result.stderr)
        self.nothing_written()
        # phases.yml が無い
        self.put("small", SMALL)
        os.remove(os.path.join(self.root, ".ccnavi", "config", "common", "phases.yml"))
        os.remove(os.path.join(self.root, ".ccnavi", "config", "project", "phases.yml"))
        result = self.new(template="small")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phases.yml", result.stderr)
        self.nothing_written()


class LayerTest(Use):
    def test_a_name_that_clashes_uses_the_common_one_and_says_so(self):
        self.put("small", SMALL)
        self.put("small", SMALL.replace("設計して実装する", "別の中身"), layer="self")
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note:", result.stdout)
        self.assertIn("共通と同じ名前で中身が違う", result.stdout)
        self.assertIn(".ccnavi/config/common/templates/small.yml", result.stdout)
        # レイヤーにだけあるテンプレートも使える
        os.remove(self.todo)
        os.remove(os.path.join(self.root, ".ccnavi", "config", "common", "templates", "small.yml"))
        result = self.new(template="small")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(".ccnavi/config/project/templates/small.yml", result.stdout)

    def test_the_template_options_are_only_for_ticket_new_template(self):
        self.put("small", SMALL)
        for argv, needle in (
            (["--templates", "--with-feedback"], "--with-feedback は"),
            (["--templates", "--template", "small", "--with-feedback"], "--template は"),
            (["ticket", "status", "--with-feedback"], "--with-feedback は"),
            (
                ["ticket", "new", "x", "--title", "t", "--plan", "design", "--with-feedback"],
                "--with-feedback は",
            ),
            (["--templates", "--template", "small"], "--template は"),
            (["ticket", "status", "--template", "small"], "--template は"),
            (["ticket", "status", "--layer", "self"], "--templates"),
        ):
            with self.subTest(argv=argv):
                result = self.tpl(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(needle, result.stderr)
        self.nothing_written()


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
        {"type": "design", "deferred": True, "after": [1]},
        {"type": "implement", "after": [1, 2]},
    ]
    FEEDBACK = [
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [4]},
    ]
    # defer・複数の after・同じ型の繰り返し、フィードバック計画の after が複数（5, 6 の続き）。
    RICH_PLAN = [
        {"type": "research", "after": []},
        {"type": "design", "deferred": True, "after": [1]},
        {"type": "design", "after": [1]},
        {"type": "implement", "after": [2, 3]},
    ]
    RICH_FEEDBACK = [
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [5]},
        {"type": "implement-feedback", "deferred": True, "after": [5, 6]},
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

        original = front_of(parent_text("i0001", plan, feedback))
        return original, front_of(self.written())

    def assert_same_plan(self, original, made):
        self.assertEqual(steps(made, "plan"), steps(original, "plan"))
        self.assertEqual(steps(made, "feedback"), steps(original, "feedback"))

    def test_a_template_drafted_from_a_done_parent_gives_the_same_plan_and_flow(self):
        original, made = self.roundtrip(self.PLAN, self.FEEDBACK, "i0001-03-01")
        self.assert_same_plan(original, made)
        self.assertEqual(read_flow(self.draft_of(3)), yaml.safe_load(FLOW_IMPL))

    def test_defer_multiple_after_repeated_types_and_feedback_after_round_trip(self):
        original, made = self.roundtrip(self.RICH_PLAN, self.RICH_FEEDBACK, "i0001-04-01")
        self.assert_same_plan(original, made)
        self.assertEqual([i["after"] for i in steps(made, "feedback")], [[], [5], [5, 6], [5, 7]])
        self.assertEqual(read_flow(self.draft_of(4))["name"], "実装の手順")

    def test_without_with_feedback_the_main_plan_alone_round_trips(self):
        self.done_parent(self.PLAN, feedback=self.FEEDBACK, flows={"i0001-03-01": FLOW_IMPL})
        self.assertEqual(self.draft().returncode, 0)
        self.generalize()
        taken = self.take()
        self.assertEqual(taken.returncode, 0, taken.stdout + taken.stderr)
        result = self.new(template="i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        original = front_of(parent_text("i0001", self.PLAN, self.FEEDBACK))
        made = front_of(self.written())
        self.assertEqual(steps(made, "plan"), steps(original, "plan"))
        self.assertFalse(made.get("feedback"))
        self.assertIn("フィードバック計画（2 ステップ）がある", result.stdout)
