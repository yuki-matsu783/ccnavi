"""`ccnavi ticket new`（親の提案の雛形の生成）の受入テスト。

見るのは 6 つ。

1. 生成物が `--lint` と承認の確認（`--agree --preview --verify`）を通り、`design` を含む plan では
   `wip/design/*` が `allow` に入る（#316 の完了条件）
2. `allow` は plan の順に scope を集め、重複を除く。`phases:` は生成の直後に差し込まれる
3. plan の各項は前の項を待つ一直線
4. `phases.yml` が無いとき、`inherit` だけの plan で `allow` が空になるとき、子の識別子、
   feedback の型、無い型は、何も書かずに理由を言って止まる
5. cwd が識別子と同名のワークツリーでなければ、切り方を案内して止まる
6. 既にある提案を上書きしない
"""

from __future__ import annotations

import os

import yaml

from tests import config_path
from tests.ticket.test_parent_phases import front_of
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git

IDENT = "feature-9-scaffold-demo"


class TicketNewTest(PhaseHarness):
    def setUp(self):
        super().setUp()
        self.tree = self.worktree(IDENT, "main")
        self.todo = os.path.join(self.tree, "wip", "proposals", "todo", IDENT + ".md")

    def new(self, *extra, ident=IDENT, cwd=None, title="雛形の見本", plan="research,design"):
        args = ["ticket", "new", ident, *extra]
        if title is not None:
            args += ["--title", title]
        if plan is not None:
            args += ["--plan", plan]
        args += ["--cwd", cwd or self.tree]
        return self.ccnavi(*args)

    def written(self):
        with open(self.todo, encoding="utf-8") as f:
            return f.read()

    def test_the_scaffold_passes_lint_and_the_approval_check(self):
        result = self.new("--issue", "9", plan="design,acceptance,implement")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertEqual(front["ticket"], IDENT)
        self.assertEqual(front["issue"], 9)
        self.assertEqual(front["title"], "雛形の見本")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("error 0 件", lint.stdout)
        # 提案について言う warn は「承認待ち」と「ワークツリーにチケットが無い」だけ。
        about = [x for x in lint.stdout.splitlines() if x.startswith("warn") and IDENT in x]
        self.assertTrue(all("承認待ち" in x or "チケットが無い" in x for x in about), about)
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_a_plan_with_design_puts_the_design_scope_in_allow(self):
        self.assertEqual(self.new(plan="design,implement").returncode, 0)
        globs = [e["glob"] for e in front_of(self.written())["allow"]]
        self.assertIn("wip/design/*", globs)
        self.assertIn("src/*", globs)

    def test_allow_follows_the_plan_order_without_duplicates(self):
        # acceptance の tests/* と implement の tests/* は 1 つにまとめる。
        self.assertEqual(self.new(plan="acceptance,implement,design").returncode, 0)
        front = front_of(self.written())
        self.assertEqual(
            [e["glob"] for e in front["allow"]],
            ["tests/*", "src/*", "wip/design/*"],
        )
        self.assertTrue(all(e["match"] == "Write|Edit" for e in front["allow"]))

    def test_the_plan_is_a_straight_line_and_phases_are_filled(self):
        self.assertEqual(self.new(plan="research,design,implement").returncode, 0)
        front = front_of(self.written())
        self.assertEqual(
            front["plan"],
            ["research", {"type": "design", "after": [1]}, {"type": "implement", "after": [2]}],
        )
        self.assertEqual(set(front["phases"]), {"research", "design", "implement"})
        self.assertEqual(front["phases"]["design"]["scope"], ["wip/design/*"])

    def test_only_the_title_with_special_characters_is_quoted_safely(self):
        self.assertEqual(self.new(title='題: "引用" #1').returncode, 0)
        self.assertEqual(front_of(self.written())["title"], '題: "引用" #1')

    def test_without_phases_yml_it_stops_and_says_why(self):
        os.remove(config_path(self.root, "phases"))
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phases.yml", result.stderr)
        self.assertIn("9.7", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_an_inherit_only_plan_stops_because_allow_would_be_empty(self):
        result = self.new(plan="implement-feedback")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.todo))
        # feedback の型は plan に置けない（allow が空より前に言う）。
        self.assertIn("feedback", result.stderr)

    def test_a_child_id_is_refused(self):
        child = IDENT + "-01-01"
        self.worktree(child, IDENT)
        result = self.new(ident=child, cwd=os.path.join(self.root, ".claude", "worktrees", child))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("子の識別子", result.stderr)

    def test_a_title_with_line_breaks_or_control_characters_is_refused(self):
        for title in ("a\nb", "a\u2028b", "a\x85b", "a\x7fb"):
            with self.subTest(title=title):
                result = self.new(title=title)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("制御文字", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_an_unknown_type_lists_the_usable_ones(self):
        result = self.new(plan="design,nothing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing", result.stderr)
        self.assertIn("research", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_missing_title_or_plan_is_refused(self):
        for kwargs, word in (({"title": None}, "--title"), ({"plan": None}, "--plan")):
            with self.subTest(kwargs=kwargs):
                result = self.new(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(word, result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_outside_the_worktree_it_explains_how_to_cut_one(self):
        result = self.new(cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree add", result.stderr)
        self.assertIn(f".claude/worktrees/{IDENT}", result.stderr)
        self.assertNotIn("sh sh", result.stderr)
        self.assertIn("'sh ", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_a_worktree_with_another_name_is_not_accepted(self):
        result = self.new(cwd=self.parent_tree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree add", result.stderr)

    def test_an_existing_proposal_is_not_overwritten(self):
        self.assertEqual(self.new().returncode, 0)
        before = self.written()
        result = self.new(plan="implement")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("上書きしない", result.stderr)
        self.assertEqual(self.written(), before)

    def test_the_output_is_valid_yaml_front_matter(self):
        self.assertEqual(self.new().returncode, 0)
        head = self.written().split("---\n", 2)[1]
        self.assertIsInstance(yaml.safe_load(head), dict)
