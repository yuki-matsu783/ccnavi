"""識別子と違う名前のワークツリーで書いた提案など、運用のミスを `--lint` が名指しする受入テスト。

見るのは次のとおり（どれも同じミスを検知して、正しい方法を案内する形）。

1. 親の提案が識別子と違う名前のワークツリーに在る、子の提案が親と同名でない所に在る（warn）
2. 子の親が承認済みの一覧に無く、別のツリーに在る（error のまま、文面に置き場所を添える）
3. 計画の無い親（warn。`ticket start` も止まる）
4. `.claude/worktrees/` の下で git に登録が無いディレクトリ（warn）
5. 直接作業のワークツリーの名前が `<先頭の語>-<番号>-<slug>` の形でない（warn。
   既存の名前は変えなくてよい）
"""

from __future__ import annotations

import os

from ccnavi.tickets import agree_candidates
from ccnavi.tickets.model import ticket_model
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import git, write


def bare_parent_text(name):
    """計画を持たない親の提案。"""
    return parent_text(name, ["design"], copy=None).replace("plan:\n  - design\n", "", 1)


class LintOpsTest(PhaseHarness):
    def lint_lines(self, *needles):
        lint = self.ccnavi("--lint", "--mode", "enable")
        return [line for line in lint.stdout.splitlines() if all(n in line for n in needles)]

    def propose_in(self, tree_name, name, text):
        base = os.path.join(self.root, ".claude", "worktrees", tree_name)
        path = write(os.path.join(base, "wip", "proposals", "todo", name + ".md"), text)
        git(base, "add", "-A")
        git(base, "commit", "--quiet", "-m", "propose " + name)
        return path

    # ---- 1. 置き場所

    def test_a_parent_proposal_in_a_differently_named_worktree_is_warned(self):
        self.worktree("rename-x", "main")
        self.propose_in(
            "rename-x", "i0002", parent_text("i0002", ["design"], copy=self.phases_text)
        )
        lines = self.lint_lines("i0002", "rename-x")
        self.assertTrue(
            any(
                "提案 i0002 を rename-x に書いている" in line
                and "ccnavi-start.sh --mr <MR の番号>" in line
                and "-b i0002" not in line
                and "このままだと親が取り込み済みにならない" in line
                for line in lines
            ),
            lines,
        )

    def test_a_parent_proposal_in_its_own_worktree_is_not_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.assertEqual(self.lint_lines("提案 i0001 を"), [])

    def test_a_child_proposal_outside_the_parents_worktree_is_warned(self):
        self.worktree("rename-x", "main")
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.propose_in(
            "rename-x", "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/design/*",))
        )
        lines = self.lint_lines("i0001-01-01", "子の提案を rename-x に書いている")
        self.assertTrue(any("親 i0001 のワークツリー" in line for line in lines), lines)

    # ---- 2. 親が別のツリーに在る

    def test_a_missing_parent_names_the_tree_that_holds_it(self):
        fake = ticket_model.Ticket(ticket="i0003", state=ticket_model.DOING, tree="rename-x")
        child = ticket_model.Ticket(ticket="i0003-01-01", parent="i0003", phase=1, tree="other")
        text = agree_candidates.parent_elsewhere_text(
            self.root, self.conf(), child, everything=[fake]
        )
        self.assertIn("親 i0003 が承認されていない", text)
        self.assertIn("親 i0003 は rename-x の doing/ に在る", text)
        self.assertIn("親のワークツリーは i0003 と同名にして、その中に承認済みチケットを置く", text)
        problems, _ = agree_candidates.validate(child, {}, missing=text)
        self.assertEqual([p.detail for p in problems], [text])
        self.assertEqual(problems[0].severity, "error")
        # 同じツリーに在るだけなら添えない。
        same = ticket_model.Ticket(ticket="i0003-01-01", parent="i0003", phase=1, tree="rename-x")
        self.assertEqual(
            agree_candidates.parent_elsewhere_text(self.root, self.conf(), same, everything=[fake]),
            "",
        )

    def conf(self):
        from ccnavi.infra import settings

        return settings.Settings(tickets="wip/proposals")

    # ---- 3. 計画の無い親

    def test_a_parent_without_a_plan_is_warned(self):
        self.propose("i0001", bare_parent_text("i0001"))
        self.commit_parent()
        lines = self.lint_lines("i0001", "plan がありません")
        self.assertTrue(
            any(
                "親 i0001 に plan がありません" in line and "--keyword 提案書式" in line
                for line in lines
            ),
            lines,
        )

    def test_a_parent_with_a_plan_is_not_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.assertEqual(self.lint_lines("plan が無い"), [])

    # ---- 直接作業の名前

    def test_a_direct_work_worktree_with_a_free_name_is_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.worktree("rename-x", "main")
        lines = self.lint_lines("直接作業のワークツリー名 rename-x")
        self.assertTrue(
            any(
                "`<先頭の語>-<番号>-<slug>` の形ではありません" in line
                and "ccnavi-worktree.sh <名前>` で切ってください" in line
                and "既にある名前は変えなくてよい" in line
                for line in lines
            ),
            lines,
        )

    def test_the_form_follows_the_branch_prefixes_setting(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.worktree("feature-12-direct", "main")
        lint = self.ccnavi("--lint", "--mode", "enable", env={"CCNAVI_BRANCH_PREFIXES": "wip"})
        self.assertIn("feature-12-direct が", lint.stdout)

    def test_worktree_name_says_the_form_only_when_it_is_off(self):
        off = self.ccnavi("worktree", "name", "rename-x")
        self.assertEqual(off.returncode, 0)
        self.assertIn("warn: 直接作業のワークツリー名 rename-x が", off.stdout)
        on = self.ccnavi("worktree", "name", "feature-12-x")
        self.assertEqual((on.returncode, on.stdout), (0, ""))

    def test_a_direct_work_worktree_in_the_form_is_not_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        self.worktree("feature-12-direct", "main")
        self.assertEqual(self.lint_lines("直接作業のワークツリー"), [])

    # ---- 4. 登録の無いディレクトリ

    def test_an_unregistered_directory_under_worktrees_is_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        os.makedirs(os.path.join(self.root, ".claude", "worktrees", "leftover"))
        lines = self.lint_lines("leftover")
        self.assertTrue(
            any(
                ".claude/worktrees/leftover は git に登録がありません" in line
                and "ccnavi-clean.sh leftover" in line
                for line in lines
            ),
            lines,
        )
        self.assertEqual(self.lint_lines(".claude/worktrees/i0001 は git"), [])

    def test_a_worktree_registered_in_another_repository_is_not_warned(self):
        self.propose("i0001", parent_text("i0001", ["design"], copy=self.phases_text))
        self.commit_parent()
        other = os.path.join(self.root, ".claude", "worktrees", "elsewhere")
        os.makedirs(other)
        write(
            os.path.join(other, ".git"), "gitdir: /somewhere/projects/p/.git/worktrees/elsewhere\n"
        )
        self.assertEqual(self.lint_lines("elsewhere は git に登録が無い"), [])
