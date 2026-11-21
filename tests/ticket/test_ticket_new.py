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

from tests import config_path
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git
from tests.ticketsample import PHASES

IDENT = "feature-9-scaffold-demo"


def front_of(text: str) -> dict:
    """本文の frontmatter を読む。"""
    return yaml.safe_load(text.split("---\n", 2)[1])


# 雛形が置く空欄の目印。
RATIONALE_TODO = "（ここにこの作業をする理由を書く）"
HEADING_TODO = {
    "目的": "（ここにこの作業の目的を書く）",
    "範囲": "（ここにこの作業の範囲を書く）",
    "対象外": "（ここにこの作業の対象外を書く）",
    "完了の確かめ方": "（ここに完了をどう確かめるかを書く）",
    "計画の理由": "（ここに計画の理由を書く）",
}
PLACEHOLDERS = (RATIONALE_TODO, *HEADING_TODO.values())


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
        for mark in PLACEHOLDERS:
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
        self.assertTrue(
            all(
                "承認待ち" in x or "チケットがありません" in x or "子の提案がありません" in x
                for x in about
            ),
            about,
        )
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_the_approval_check_stops_on_placeholders_and_warns_on_a_missing_heading(self):
        self.assertEqual(self.new("--mr", "5").returncode, 0)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "scaffold")
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 3, verify.stdout + verify.stderr)
        self.assertIn("空欄の目印が残っている", verify.stdout + verify.stderr)
        # 目印を埋め、見出しを 1 つ崩すと、承認の確認は warn だけで通る
        self.fill_placeholders()
        text = self.written().replace("## 範囲\n", "### 範囲\n")
        with open(self.todo, "w", encoding="utf-8") as f:
            f.write(text)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "--quiet", "-m", "fill")
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertIn("見出しが欠けている", verify.stdout + verify.stderr)
        self.assertIn("## 範囲", verify.stdout + verify.stderr)
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("見出しが欠けている", lint.stdout)

    def test_an_issue_of_another_repository_and_a_scope_shared_by_two_phases(self):
        # acceptance の tests/* と implement の tests/* は 1 つにまとめる。
        result = self.new("--issue", "other/repo#12", plan="acceptance,implement,design")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.written())
        self.assertEqual(front["issue"], "other/repo#12")
        self.assertEqual([e["glob"] for e in front["allow"]], ["tests/*", "src/*", "wip/design/*"])
        self.assertTrue(all(e["match"] == "Write|Edit" for e in front["allow"]))
        self.assertEqual(
            front["plan"],
            [
                "acceptance",
                {"type": "implement", "after": [1]},
                {"type": "design", "after": [2]},
            ],
        )

    def test_without_phases_yml_it_stops_and_says_why(self):
        os.remove(config_path(self.root, "phases"))
        result = self.new()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phases.yml", result.stderr)
        self.assertIn("9.7", result.stderr)
        self.assertFalse(os.path.exists(self.todo))


class TicketNewScopeTest(TicketNewTestBase):
    """`--scope`: `inherit` の型（quick-work）だけの計画でも、今回の範囲を渡して雛形を作れる。"""

    def setUp(self):
        super().setUp()
        quick_work = (
            "\n  quick-work:\n    kind: work\n    title: 小さい作業\n    review: true\n"
            "    scope: inherit\n"
        )
        with open(config_path(self.root, "phases"), "w", encoding="utf-8", newline="\n") as f:
            f.write(PHASES.rstrip("\n") + quick_work)

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
