"""親子のチケットで正とする側（立ち位置）。

正とする側は、全部の親子のチケットで同じく親のブランチ（`.claude/worktrees/<P>` で HEAD が `<P>` を
指すツリー）に固定する。決まらなければ（親のワークツリーが無い・HEAD が合わない・途中の操作がある）
承認も状態の操作も実行前チェックも止める。判定は親のワークツリーの有無・HEAD・途中の操作だけで決まる。
判定は git もネットワークも使わない。

`AuthorityHarness` は、親 i0001 と子 i0001-01-01 を承認して子のワークツリーを作った状態を用意する。
ほかのテストも使う。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra import pathcase, settings
from ccnavi.tickets import (
    approval,
    approval_checks,
    approval_ops,
    standing,
    ticket_model,
)
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text


class AuthorityHarness(PhaseHarness):
    """親 i0001 と子 i0001-01-01 を承認し、子のワークツリーを作って着手した状態から始める。"""

    def setUp(self):
        super().setUp()
        self.family(plan=["research", "design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("propose child")
        self.assertEqual(self.approve().returncode, 0)
        self.child_tree = self.run_child("i0001-01-01")

    def write_to(self, tree, rel):
        return self.hook(
            "PreToolUse",
            "Write",
            tree,
            file_path=os.path.join(tree, *rel.split("/")),
            content="x\n",
        )

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def conf(self):
        conf, _ = settings.load(self.root)
        conf.state = self.state
        conf.approved = ".ccnavi/approved"
        conf.tickets = settings.DEFAULT_TICKETS
        return conf

    def board(self):
        return json.loads(self.ccnavi("--explain", "--json").stdout)

    def drop_parent(self):
        """子と親のワークツリーを片付ける（親のブランチは残る）。"""
        git(self.root, "worktree", "remove", "--force", self.child_tree)
        git(self.root, "worktree", "remove", "--force", self.parent_tree)


class ParentTreeTest(AuthorityHarness):
    def test_the_parent_tree_is_the_authority(self):
        self.assertNotEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        board = self.board()
        self.assertEqual("", self.blocked_of(board, "i0001-01-01"))

    def blocked_of(self, board, ticket):
        for parent in board.get("parents", []):
            for t in [parent, *parent.get("children", [])]:
                if t.get("ticket") == ticket:
                    return t.get("blocked", "")
        for t in board.get("tickets", []):
            if t.get("ticket") == ticket:
                return t.get("blocked", "")
        raise AssertionError(f"{ticket} が板に無い")

    def test_a_copy_only_outside_the_parent_tree_is_not_trusted(self):
        # 元ツリー（ワークスペースルート）に未コミットで残ったチケット。
        # 正とする側のツリーが無いときに元ツリーを採る形。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        stray = os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-02-02.md")
        with open(
            os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02-02.md"),
            encoding="utf-8",
        ) as f:
            write(stray, f.read())
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02-02.md"))
        copies, _ = approval.scan(self.conf(), self.root)
        after = {t.ticket: t.blocked for t in copies}
        # 理由だけが足される。
        self.assertIn("i0001-02-02", after)
        self.assertIn("ワークツリーの外", after["i0001-02-02"])
        self.assertEqual("", after["i0001-01-01"])
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertTrue(
            any(
                p["severity"] == "error" and "i0001-02-02" in p["detail"] and "外" in p["detail"]
                for p in lint["problems"]
            ),
            lint,
        )
        check = self.ccnavi("sync", "check", "i0001", "self")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        lines = check.stdout.splitlines()
        self.assertEqual("check 1", lines[0])
        self.assertTrue(lines[1].startswith("error "), check.stdout)
        # ユーザが親のブランチへ移してコミットする手順も言う。
        self.assertIn("移してコミットと push", check.stdout)

    def test_a_proposal_outside_the_parent_tree_is_not_approved(self):
        write(
            os.path.join(self.root, "wip", "proposals", "todo", "i0001-01-02.md"),
            child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]),
        )
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual([], preview["batch"])
        self.assertTrue(
            any("ワークツリーの外" in " ".join(r["problems"]) for r in preview["rejected"]),
            preview,
        )

    def test_the_parent_tree_on_another_branch_is_undecided(self):
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        self.assertEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        started = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, started.returncode)
        self.assertIn(
            "HEAD のブランチ名を変換した識別子がフォルダ名（i0001）と合わない", started.stderr
        )
        self.assertIn("に戻してください", started.stderr)

    def test_a_state_operation_does_not_move_a_copy_outside_the_parent_tree(self):
        # 子のチケットが親のワークツリーから消え、元ツリーにだけ残った形。
        inside = os.path.join(self.approved, "doing", "i0001-01-01.md")
        with open(inside, encoding="utf-8") as f:
            text = f.read()
        os.remove(inside)
        stray = write(
            os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-01-01.md"), text
        )
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("ワークツリーの外", finished.stderr)
        self.assertTrue(os.path.isfile(stray))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".ccnavi", "approved", "done")))

    def test_the_check_names_an_unreadable_copy_in_the_parent_tree(self):
        self.assertEqual(0, self.ccnavi("sync", "check", "i0001").returncode)
        write(os.path.join(self.approved, "doing", "i0001-01-05.md"), "---\nticket: [\n---\n")
        check = self.ccnavi("sync", "check", "i0001")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertIn("i0001-01-05.md", check.stdout)

    def test_the_check_refuses_a_path_like_argument(self):
        check = self.ccnavi("sync", "check", "../x")
        self.assertEqual(1, check.returncode)
        self.assertIn("有効な識別子の形式ではありません", check.stderr)

    def test_the_parent_is_written_to_the_parent_tree(self):
        # 元ツリーに同じ識別子のチケットがあっても、書く先は親のブランチ。
        write(os.path.join(self.root, ".ccnavi", "approved", "done", "i0001.md"), "x\n")
        where = approval.home_dir(self.conf(), self.root, "i0001", "")
        self.assertEqual(
            pathcase.fold(os.path.join(self.parent_tree, ".ccnavi", "approved")),
            pathcase.fold(where),
        )


class NoParentTreeTest(AuthorityHarness):
    """親のワークツリーが無い親子のチケットは決まらず、状態の操作も承認も止める。

    記録は持たないので、片付けた（`worktree remove`）後は、元ツリーに残ったコピーがあっても
    止めが外れない。
    """

    def test_a_missing_parent_tree_stops_the_state_and_the_approval(self):
        copies = {}
        for name in ("i0001.md", "i0001-01-01.md"):
            with open(os.path.join(self.approved, "doing", name), encoding="utf-8") as f:
                copies[name] = f.read()
        self.drop_parent()
        # 元ツリーに同じ親子のチケットを残しても、代用しない。
        stray = os.path.join(self.root, ".ccnavi", "approved", "doing")
        for name, text in copies.items():
            write(os.path.join(stray, name), text)
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("親のワークツリー", finished.stderr)
        self.assertIn("がありません", finished.stderr)
        # 解き方は、切り直すか、捨てた親子なら片付ければよい（`--forget` は無い）。
        self.assertIn("切り直してください", finished.stderr)
        self.assertIn("片付ければよい", finished.stderr)
        self.assertNotIn("--forget", finished.stderr)
        for word in ("gone", "blocked"):
            self.assertNotIn(word, finished.stderr)

    def test_review_operations_stop_too(self):
        parent_tree = self.parent_tree
        self.drop_parent()
        fixture = write(
            os.path.join(self.root, "fixture.json"),
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}),
        )
        ready = self.ccnavi("--cwd", parent_tree, "review", "ready", "--result", fixture)
        self.assertNotEqual(0, ready.returncode)

    def test_the_standing_has_no_record_and_no_closed(self):
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertEqual("", st.stop)
        self.assertEqual("i0001", st.home.name)
        self.assertEqual("self", st.repo)
        self.assertFalse(hasattr(st, "closed"))
        self.assertFalse(hasattr(st, "record"))
        self.drop_parent()
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertIsNone(st.home)
        self.assertIn("親のワークツリー", st.stop)
        self.assertEqual("<ブランチ名>", st.branch_name)

    def test_the_guidance_says_how_to_drop_an_abandoned_family(self):
        self.drop_parent()
        st = standing.standing(self.conf(), self.root, "i0001")
        text = "\n".join(standing.guidance(self.root, st))
        self.assertIn("ccnavi-git.sh", text)
        self.assertIn("worktree add .claude/worktrees/i0001", text)
        self.assertIn("捨てた親子のチケットなら、親のワークツリーを片付ければよい", text)
        self.assertNotIn("--forget", text)


class BusyParentTest(AuthorityHarness):
    def test_a_parent_tree_in_the_middle_of_a_rebase_says_so(self):
        gitdir = git(self.parent_tree, "rev-parse", "--git-dir").strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(self.parent_tree, gitdir)
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(gitdir, "rebase-merge"))
        with open(os.path.join(gitdir, "HEAD"), "w", encoding="utf-8") as f:
            f.write(head + "\n")
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("途中の操作（rebase-merge）", finished.stderr)


class MarkTest(AuthorityHarness):
    def test_an_earlier_reason_is_kept(self):
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        Ticket = ticket_model.Ticket
        t = Ticket(
            ticket="i0001-01-01", parent="i0001", tree_root=self.parent_tree, blocked="前の理由"
        )
        approval_checks.mark_imported(self.conf(), self.root, [t])
        self.assertTrue(t.blocked.startswith("前の理由 / "), t.blocked)
        self.assertIn("合わない", t.blocked)

    def test_a_same_named_tree_in_another_repository_is_not_guessed(self):
        """同じ名前の親のワークツリーが別のリポジトリにもあれば決めない。

        ワークスペースのユーザの付けた名前 `web-i0012` と、
        プロジェクト web の issue 12
        の親子のチケットが並ぶ形。
        """
        from ccnavi.infra import tree

        fams = standing.Families(self.conf(), self.root)
        mine = [w for w in fams.worktrees() if w.name == "i0001"]
        self.assertEqual(1, len(mine), mine)
        self.assertEqual("", fams.standing_any("i0001").stop)
        fams = standing.Families(self.conf(), self.root)
        fams._worktrees = [
            *fams.worktrees(),
            tree.Tree("i0001", os.path.join(self.root, "x"), "web"),
        ]
        st = fams.standing_any("i0001")
        self.assertIn("複数のリポジトリ（self, web）", st.stop)
        # リポジトリをつければ引ける。
        self.assertEqual("", fams.standing_any("i0001", "").stop)

    def test_the_repo_key_helpers_live_in_tree(self):
        from ccnavi.infra import tree

        self.assertEqual("self", tree.repo_key(""))
        self.assertEqual("web", tree.repo_key("web"))
        self.assertEqual("", tree.project_of_key("self"))
        self.assertEqual("web", tree.project_of_key("web"))


class PredecessorTest(AuthorityHarness):
    def propose_with_predecessor(self, predecessor):
        text = child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]).replace(
            "phase: 1\n", f"phase: 1\npredecessors: [{predecessor}]\n"
        )
        self.propose("i0001-01-02", text)
        self.commit_parent()
        return json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)

    def test_a_predecessor_family_without_the_parent_worktree_is_not_met(self):
        # 先行 i0002-01-01 は元ツリーの done/ にしかない。親子のチケット i0002 は親のワークツリーが
        # 無く（決まらない）、元ツリーのコピーでは代用しないので、満たしたとみなさない。
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0002-01-01.md"),
            child_text("i0002-01-01", "i0002", 1, ["src/*"]).replace(
                'completed_at: ""', 'completed_at: "2026-09-01T00:00:00+0900"'
            ),
        )
        after = self.propose_with_predecessor("i0002-01-01")
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in after["batch"]])
        self.assertTrue(
            any("親子のチケットが決まらない" in " ".join(r["problems"]) for r in after["rejected"]),
            after,
        )

    def test_a_predecessor_that_is_nowhere_says_to_drop_it(self):
        # 閉じて片付いた別の親子のチケットの子は、どの置き場にも無い。取り込み済みなので
        # predecessors から消してもらう（置き換えは何も無い）。
        after = self.propose_with_predecessor("i0009-01-01")
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in after["batch"]])
        text = " ".join(" ".join(r["problems"]) for r in after["rejected"])
        self.assertIn("にもありません", text)
        self.assertIn("predecessors から削除してください", text)

    def test_the_parent_tree_does_not_loosen_a_predecessor(self):
        # 厳しくする向きだけ: 親のワークツリーで閉じていても、
        # 前の対応表で満たしていなければ満たさない。
        Ticket = ticket_model.Ticket
        done = Ticket(
            ticket="i0001-01-09", parent="i0001", state="done", tree_root=self.parent_tree
        )
        stale = Ticket(ticket="i0001-01-09", parent="i0001", state="doing", tree_root=self.root)
        pool = {"i0001-01-09": [done, stale]}
        approval_checks.align_imported(self.conf(), self.root, pool)
        self.assertEqual([done, stale], pool["i0001-01-09"])
        # 親のワークツリーで閉じていなければ、それを採る（前の対応表が満たしていても）。
        doing = Ticket(
            ticket="i0001-01-08", parent="i0001", state="doing", tree_root=self.parent_tree
        )
        closed = Ticket(ticket="i0001-01-08", parent="i0001", state="done", tree_root=self.root)
        pool = {"i0001-01-08": [doing, closed]}
        approval_checks.align_imported(self.conf(), self.root, pool)
        self.assertEqual([doing], pool["i0001-01-08"])


class UnreadableChildTest(AuthorityHarness):
    """マージのコンフリクトマーカーなどで読めなくなった子の承認済みチケットが、親のツリーにある。"""

    def start_second_child(self):
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("propose second")
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree("i0001-01-02", "i0001")
        started = self.ccnavi("ticket", "start", "i0001-01-02")
        self.assertEqual(started.returncode, 0, started.stderr)
        return tree

    def corrupt(self, name="i0001-01-02"):
        write(
            os.path.join(self.approved, "doing", f"{name}.md"),
            "<<<<<<< HEAD\nticket: x\n=======\nticket: y\n>>>>>>> other\n",
        )

    def test_the_unreadable_child_is_not_given_the_range_of_the_parent(self):
        """読めない子のワークツリーで、親の範囲を借りて書けることはない。

        子のワークツリー自身に読める写しがあれば、親のワークツリーの外にしか無いチケットとして
        止まる（deny）。その写しも読めなければ、チケットのないツリーと同じ扱い（ccnavi の判定は
        undeclared で、親の範囲では許さない）。
        """
        second = self.start_second_child()
        self.corrupt()
        blocked = self.write_to(second, "wip/research/a")
        self.assertEqual("deny", self.decision(blocked), blocked.stdout)
        self.assertIn("DENY_TICKET_BLOCKED", self.reason(blocked))
        write(
            os.path.join(second, ".ccnavi", "approved", "doing", "i0001-01-02.md"),
            "<<<<<<< HEAD\nticket: x\n=======\nticket: y\n>>>>>>> other\n",
        )
        undeclared = self.write_to(second, "wip/research/a")
        self.assertNotEqual("allow", self.decision(undeclared), undeclared.stdout)
        self.assertIn("UNDECLARED", self.reason(undeclared))

    def test_the_siblings_keep_working_and_the_check_names_the_unreadable_copy(self):
        """失ったもの: 読めない子があっても、読める兄弟と親の書き込みは止まらない。

        読めないことは `sync check` の表示と終了コードでだけ伝わる。
        """
        self.start_second_child()
        self.corrupt()
        self.assertNotEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        self.assertNotEqual("deny", self.decision(self.write_to(self.parent_tree, "wip/handoff/x")))
        check = self.ccnavi("sync", "check", "i0001")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertIn("i0001-01-02.md", check.stdout)


class GuidanceByKindTest(AuthorityHarness):
    """止める理由の種別（`Standing.kind`）で解き方を分ける。文面の部分一致には頼らない。"""

    def guidance(self, st):
        return "\n".join(standing.guidance(self.root, st))

    def test_a_mismatched_head_names_the_raw_head_not_a_placeholder(self):
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertEqual(standing.STOP_MISMATCH, st.kind)
        self.assertEqual("elsewhere", st.head)
        self.assertEqual("", st.branch)
        text = self.guidance(st)
        self.assertIn("今の HEAD: elsewhere", text)
        self.assertIn("に戻してください", text)
        self.assertNotIn("<ブランチ名>", text)
        self.assertNotIn("切り直してください", text)

    def test_a_detached_head_says_it_does_not_point_at_a_branch(self):
        git(self.parent_tree, "checkout", "--quiet", "--detach")
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertEqual(standing.STOP_MISMATCH, st.kind)
        self.assertIn("HEAD がブランチを指していない", self.guidance(st))

    def test_a_busy_tree_says_to_finish_the_operation(self):
        gitdir = git(self.parent_tree, "rev-parse", "--git-dir").strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(self.parent_tree, gitdir)
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(gitdir, "rebase-merge"))
        with open(os.path.join(gitdir, "HEAD"), "w", encoding="utf-8") as f:
            f.write(head + "\n")
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertEqual(standing.STOP_BUSY, st.kind)
        self.assertIn("途中の操作", self.guidance(st))
        self.assertNotIn("切り直してください", self.guidance(st))

    def test_an_absent_tree_says_to_recut_it(self):
        self.drop_parent()
        st = standing.standing(self.conf(), self.root, "i0001")
        self.assertEqual(standing.STOP_ABSENT, st.kind)
        self.assertIn("切り直してください", self.guidance(st))

    def test_a_family_in_two_repositories_does_not_say_to_recut_the_tree(self):
        from ccnavi.infra import tree

        fams = standing.Families(self.conf(), self.root)
        fams._worktrees = [
            *fams.worktrees(),
            tree.Tree("i0001", os.path.join(self.root, "x"), "web"),
        ]
        st = fams.standing_any("i0001")
        self.assertEqual(standing.STOP_AMBIGUOUS, st.kind)
        text = self.guidance(st)
        self.assertIn("複数のリポジトリ", text)
        self.assertIn("1 つのリポジトリに絞って", text)
        self.assertNotIn("切り直してください", text)
        self.assertNotIn("worktree add", text)

    def test_a_standing_that_does_not_stop_has_no_kind(self):
        self.assertEqual("", standing.standing(self.conf(), self.root, "i0001").kind)


class ClosedIdentifierReuseTest(AuthorityHarness):
    """承認では、閉じた識別子の再利用を検知しない（検知は開始時のホストへの照会だけ）。"""

    def test_a_proposal_that_reuses_an_archived_identifier_is_approved(self):
        write(
            os.path.join(self.root, "logs", "archive", "self", "done", "i0002.md"),
            "---\nversion: 1\nticket: i0002\ntitle: 閉じた親\n"
            "completed_at: 2026-01-01T00:00:00+0900\n---\n",
        )
        other = self.worktree("i0002", "main")
        self.propose("i0002", parent_text("i0002", ["research"]), tree=other)
        result = self.approve()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(other, ".ccnavi", "approved", "doing", "i0002.md"))
        )


class ReadyTest(AuthorityHarness):
    def test_ready_needs_the_parent_in_done(self):
        fixture = write(
            os.path.join(self.root, "fixture.json"),
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}),
        )
        ready = self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)
        self.assertNotEqual(0, ready.returncode)
        self.assertIn("done/ にありません", ready.stderr)
        self.assertIn("finish i0001", ready.stderr)


class LintTest(AuthorityHarness):
    def test_local_settings_may_hold_nothing_that_decides_approval(self):
        path = os.path.join(self.root, ".claude", "settings.local.json")
        write(path, json.dumps({"env": {"CCNAVI_LOG_LEVEL": "DEBUG"}}))
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        self.assertFalse([p for p in problems if "settings.local.json" in p["detail"]])
        write(
            path,
            json.dumps(
                {
                    "env": {
                        "CCNAVI_MODE": "disable",
                        "CCNAVI_RESTORE_IF_DENY": "disable",
                        "CCNAVI_LOG_LEVEL": "DEBUG",
                    }
                }
            ),
        )
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        named = [p for p in problems if "settings.local.json" in p["detail"]]
        self.assertEqual(2, len(named), named)
        self.assertTrue(all(p["severity"] == "error" for p in named))
        self.assertTrue(any("CCNAVI_MODE" in p["detail"] for p in named))
        self.assertTrue(any("CCNAVI_RESTORE_IF_DENY" in p["detail"] for p in named))

    def test_a_parent_tree_on_another_branch_is_named(self):
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        self.assertTrue(
            any(
                p["where"] == "(.claude/worktrees/i0001)" and "elsewhere" in p["detail"]
                for p in problems
            ),
            problems,
        )


class HookNoProcessTest(AuthorityHarness):
    """判定は親のワークツリーのファイルを読むだけで、外部プロセスを起こさない（tree.py の前提）。"""

    def test_the_judge_does_not_spawn(self):
        import subprocess
        from unittest import mock

        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("起こした")):
            conf = self.conf()
            copies, _ = approval.scan(conf, self.root)
            approval_ops.predecessor_pool(conf, self.root)
            standing.standing(conf, self.root, "i0001")
        self.assertTrue(copies)


if __name__ == "__main__":
    unittest.main()
