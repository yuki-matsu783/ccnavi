"""`ccnavi ticket new`（親の提案の雛形の生成）の受入テスト。

見るのは 6 つ。

1. 生成物が `--lint` と承認の確認（`--agree --preview --verify`）を通り、`design` を含む plan では
   `wip/design/*` が `allow` に入る（#316 の完了条件）
2. `allow` は plan の順に scope を集め、重複を除く。`phases:` は生成の直後に差し込まれる
3. plan の前のステップが各ステップに先行する一直線
4. `phases.yml` が無いとき、`inherit` だけの plan で `allow` が空になるとき、子の識別子、
   feedback の型、無い型は、何も書かずに理由を言って止まる
5. cwd が識別子と同名のワークツリーでなければ、親のワークツリーの作り方
   （ccnavi-start.sh）を案内して止まる
6. 既にある提案を上書きしない
"""

from __future__ import annotations

import os

import yaml

from ccnavi.tickets import ops, proposal_form, ticket_model
from tests import config_path
from tests.ticket.test_parent_phases import front_of
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import PHASES

IDENT = "feature-9-scaffold-demo"


class TicketNewTestBase(PhaseHarness):
    def setUp(self):
        super().setUp()
        self.tree = self.worktree(IDENT, "main")
        self.todo = os.path.join(self.tree, "wip", "proposals", "todo", IDENT + ".md")

    def new(
        self,
        *extra,
        ident=IDENT,
        cwd=None,
        title="雛形の見本",
        plan="research,design",
        nodefault=(),
    ):
        args = ["ticket", "new", ident, *extra]
        # 親の提案は issue と MR のセット。渡さないテストのために既定を足す（渡したものが優先）
        for flag, value in (("--issue", "9"), ("--mr", "5")):
            if flag not in extra and flag not in nodefault:
                args += [flag, value]
        if title is not None:
            args += ["--title", title]
        if plan is not None:
            args += ["--plan", plan]
        args += ["--cwd", cwd or self.tree]
        return self.ccnavi(*args)

    def written(self):
        with open(self.todo, encoding="utf-8") as f:
            return f.read()

    def fill_placeholders(self):
        """雛形の空欄の目印を、実際の内容に書き換える（目印が残ると承認の検査が止める）。"""
        text = self.written()
        for mark in proposal_form.PLACEHOLDERS:
            text = text.replace(mark, "書いた内容")
        with open(self.todo, "w", encoding="utf-8") as f:
            f.write(text)


class TicketNewTest(TicketNewTestBase):
    def test_the_scaffold_passes_lint_and_the_approval_check(self):
        result = self.new("--issue", "9", "--mr", "5", plan="design,acceptance,implement")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertEqual(front["ticket"], IDENT)
        self.assertEqual(front["issue"], 9)
        self.assertEqual(front["title"], "雛形の見本")
        self.fill_placeholders()
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("error 0 件", lint.stdout)
        # 提案について言う warn は「承認待ち」と「ワークツリーにチケットが無い」だけ。
        about = [x for x in lint.stdout.splitlines() if x.startswith("warn") and IDENT in x]
        self.assertTrue(all("承認待ち" in x or "チケットがありません" in x for x in about), about)
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_the_body_headings_follow_the_parent_headings_with_a_placeholder_each(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        body = self.written().split("---\n", 2)[2]
        headings = [x[3:] for x in body.splitlines() if x.startswith("## ")]
        self.assertEqual(headings, list(proposal_form.PARENT_HEADINGS))
        for name in proposal_form.PARENT_HEADINGS:
            self.assertIn(f"## {name}\n\n{proposal_form.HEADING_TODO[name]}\n", body)

    def test_a_fresh_scaffold_is_refused_for_the_placeholders_left(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 3, verify.stdout + verify.stderr)
        self.assertIn("空欄の目印が残っている", verify.stdout + verify.stderr)

    def test_a_missing_heading_is_only_a_warning_for_the_approval_check(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        self.fill_placeholders()
        text = self.written().replace("## 範囲\n", "### 範囲\n")
        with open(self.todo, "w", encoding="utf-8") as f:
            f.write(text)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertIn("見出しが欠けている", verify.stdout + verify.stderr)
        self.assertIn("## 範囲", verify.stdout + verify.stderr)
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("見出しが欠けている", lint.stdout)

    def test_the_next_line_tells_to_replace_the_placeholders(self):
        result = self.new("--mr", "5")
        nexts = [x for x in result.stdout.splitlines() if x.startswith("次: ")]
        self.assertEqual(len(nexts), 1, result.stdout)
        self.assertIn("目印", nexts[0])
        self.assertIn("承認で止まる", nexts[0])

    def test_the_mr_comes_from_the_flag_only(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        self.assertEqual(front_of(self.written())["mr"], 5)
        os.remove(self.todo)
        # 指定が無ければ作らない。取り込みの記録からは引かない
        record = os.path.join(self.state, "sync", "self", "families", IDENT)
        write(record, f"remote origin\nbranch {IDENT}\nsha 0\nfetched_at 1\nstate present\nmr 7\n")
        done = self.new(nodefault=("--mr",))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("--mr <番号> が必要", done.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_the_issue_and_the_mr_are_both_required(self):
        for flag in ("--issue", "--mr"):
            with self.subTest(flag=flag):
                done = self.new(nodefault=(flag,))
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn(f"{flag} <番号> が必要", done.stderr)
                self.assertFalse(os.path.exists(self.todo))

    def test_the_issue_may_name_another_repository(self):
        self.assertEqual(self.new("--issue", "other/repo#12").returncode, 0)
        self.assertEqual(front_of(self.written())["issue"], "other/repo#12")
        os.remove(self.todo)
        done = self.new("--issue", "other/repo#x")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("--issue には", done.stderr)

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
        self.assertEqual(self.new(title='タイトル: "引用" #1').returncode, 0)
        self.assertEqual(front_of(self.written())["title"], 'タイトル: "引用" #1')

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
        # 親のワークツリーは ccnavi-start.sh が親のブランチを直接チェックアウトして作る。
        # 識別子の名前のブランチ（-b <識別子>）は作らせない。
        self.assertIn("ccnavi-start.sh --mr <MR の番号>", result.stderr)
        self.assertNotIn(f"-b {IDENT}", result.stderr)
        self.assertIn(f".claude/worktrees/{IDENT}", result.stderr)
        self.assertNotIn("sh sh", result.stderr)
        self.assertIn("'sh ", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_the_start_points_a_parent_without_its_worktree_at_ccnavi_start(self):
        """`ticket start` の案内も、親には識別子の名前のブランチを作らせない。子は -b <子>。"""
        parent = ticket_model.Ticket(ticket=IDENT)
        child = ticket_model.Ticket(ticket=f"{IDENT}-01-01", parent=IDENT, phase=1)
        said = ops._create_words(self.root, parent, "/w/p")
        self.assertIn("ccnavi-start.sh --mr <MR の番号>", said)
        self.assertNotIn("-b ", said)
        self.assertIn(f"-b {IDENT}-01-01", ops._create_words(self.root, child, "/w/c"))

    def test_a_worktree_with_another_name_is_not_accepted(self):
        result = self.new(cwd=self.parent_tree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ccnavi-start.sh", result.stderr)

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


class TicketNewScopeTest(TicketNewTestBase):
    """`--scope`: `inherit` の型（quick-work）だけの計画でも、今回の範囲を渡して雛形を作れる。"""

    def setUp(self):
        super().setUp()
        quick_work = (
            "\n  quick-work:\n    kind: work\n    title: 小さい作業\n    review: mr\n"
            "    scope: inherit\n"
        )
        with open(config_path(self.root, "phases"), "w", encoding="utf-8", newline="\n") as f:
            f.write(PHASES.rstrip("\n") + quick_work)

    def test_quick_work_alone_stops_and_points_at_scope(self):
        result = self.new(plan="quick-work")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.todo))
        self.assertIn("--scope", result.stderr)
        self.assertIn("範囲を持つ型を追加する必要はありません", result.stderr)

    def test_scope_gives_quick_work_its_allow(self):
        result = self.new("--scope", "src/ui/error.ts", plan="quick-work")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertEqual([e["glob"] for e in front["allow"]], ["src/ui/error.ts"])
        self.assertEqual(front["plan"], ["quick-work"])
        self.assertIn("quick-work", front["phases"])
        # 次の一手は「次:」で始まる 1 行。承認までは実装しない
        nexts = [line for line in result.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(len(nexts), 1, result.stdout)
        self.assertIn("--agree --preview --verify", nexts[0])
        self.assertIn("承認が済むまで実装はしない", nexts[0])
        self.assertIn("直接実装に切り替えず", result.stdout)
        # 子の提案も同じ承認で通すため先に書く。propose は親の識別子で打つ
        self.assertIn("子の提案", nexts[0])
        self.assertIn("-01-01", nexts[0])
        self.assertIn("同じ承認で通す", nexts[0])
        self.assertIn("親の識別子で打つ。子の識別子では打たない", nexts[0])

    def test_scope_is_added_after_the_collected_scope_without_duplicates(self):
        result = self.new("--scope", "docs/a.md,SRC/*", "--scope", "docs/a.md", plan="implement")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        globs = [e["glob"] for e in front_of(self.written())["allow"]]
        self.assertEqual(globs, ["src/*", "tests/*", "docs/a.md"])

    def test_scope_beyond_the_phase_limit_is_said_at_creation_and_by_verify(self):
        result = self.new("--scope", "docs/**", "--mr", "5", plan="implement")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認しても書けない: `docs/**`", result.stdout)
        self.assertIn("quick-work", result.stdout)
        self.assertIn("案内: 承認しても書けない", result.stdout)
        self.assertNotIn("`src/*` は計画", result.stdout)
        self.fill_placeholders()
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-qm", "proposal")
        verify = self.ccnavi("--agree", "--preview", "--verify", "--cwd", self.tree)
        self.assertIn("承認しても書けない: `docs/**`", verify.stdout + verify.stderr)

    def test_scope_inside_the_phase_limit_or_with_quick_work_is_not_flagged(self):
        result = self.new("--scope", "src/a.py", plan="implement")
        self.assertNotIn("承認しても書けない", result.stdout)
        os.remove(self.todo)
        result = self.new("--scope", "docs/**", plan="quick-work")
        self.assertNotIn("承認しても書けない", result.stdout)
        # パスを 1 つに決められない glob（前置が空か、ワイルドカードが後ろに続く）は言わない
        for glob in ("**/*.py", "*.md", "src/**/*.py"):
            os.remove(self.todo)
            result = self.new("--scope", glob, plan="implement")
            self.assertNotIn("承認しても書けない", result.stdout, glob)

    def test_a_placeholder_from_a_guidance_line_is_refused(self):
        # 案内の行の仮の値（`<触るファイルの glob>`・`<タイトル>`）をそのまま打つと、
        # 実在しない glob やタイトルの提案ができて承認まで通る。作らずに断る
        result = self.new("--scope", "<触るファイルの glob>", plan="quick-work")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仮の値", result.stderr)
        self.assertFalse(os.path.exists(self.todo))
        result = self.new("--scope", "src/a.py", plan="quick-work", title="<タイトル>")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仮の値", result.stderr)
        self.assertFalse(os.path.exists(self.todo))

    def test_the_next_step_runs_through_the_launcher(self):
        result = self.new("--scope", "src/ui/error.ts", plan="quick-work")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # ccnavi を PATH に持たなくても打てる形で書く
        self.assertRegex(result.stdout, r"ccnavi-launcher\.sh --agree --preview --verify ")
        self.assertRegex(result.stdout, r"ccnavi-launcher\.sh --lint")

    def test_scope_must_be_relative_and_stay_inside_the_tree(self):
        for bad in ("/etc/passwd", "../x", "a/../b", "C:/x"):
            with self.subTest(scope=bad):
                result = self.new("--scope", bad, plan="quick-work")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--scope", result.stderr)
        self.assertFalse(os.path.exists(self.todo))
