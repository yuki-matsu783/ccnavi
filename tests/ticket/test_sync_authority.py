"""取り込み済みの親子のチケットで本物とする側。

取り込み状態（`<state の置き場>/sync/<リポジトリ>/families/<P>`）がある親子のチケットは、
本物とする側を親のブランチ（`.claude/worktrees/<P>` で HEAD が `<P>` を指すツリー）に固定し、
決まらなければ承認も状態の操作も実行前チェックも止める。
取り込み状態の無い親子のチケットは前と同じ答え。

取り込み状態は sh（`ccnavi-sync.sh`）が書くものを、ここでは手で置く。
判定は git もネットワークも使わない。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest

from ccnavi.hook import core
from ccnavi.infra import fsio, settings
from ccnavi.tickets import agree, approval, syncstate
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import git, write


def record_text(name, state, reason="", sha="0" * 40):
    return (
        f"remote origin\nbranch {name}\nsha {sha}\nfetched_at 1\nstate {state}\nreason {reason}\n"
    )


class ReaderTest(unittest.TestCase):
    """取り込み状態の読み方。リンクを辿らない、入れ替えの一瞬を待つ、取り込み状態が無ければ待たない。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = self._tmp.name

    def put(self, rel, text):
        return write(os.path.join(self.state, *rel.split("/")), text)

    def test_no_records_reads_nothing_and_does_not_wait(self):
        started = time.monotonic()
        self.assertIsNone(syncstate.family(self.state, "self", "i0001"))
        self.assertIsNone(syncstate.integration(self.state, "self"))
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertFalse(syncstate.any_records(self.state))

    def test_a_family_record_is_read_line_by_line(self):
        self.put("sync/self/families/i0001", record_text("i0001", "present", "r x"))
        found = syncstate.family(self.state, "self", "i0001")
        self.assertEqual(("present", "r x", ""), (found.state, found.reason, found.broken))

    def test_an_unknown_state_is_a_broken_record(self):
        self.put("sync/self/families/i0001", record_text("i0001", "weird"))
        found = syncstate.family(self.state, "self", "i0001")
        self.assertEqual("", found.state)
        self.assertIn("state", found.broken)

    def test_links_on_the_way_are_not_followed(self):
        real = self.put("elsewhere/i0001", record_text("i0001", "present"))
        os.makedirs(os.path.join(self.state, "sync", "self", "families"))
        os.symlink(real, os.path.join(self.state, "sync", "self", "families", "i0001"))
        found = syncstate.family(self.state, "self", "i0001")
        self.assertEqual("", found.state)
        self.assertIn("リンク", found.broken)
        # 途中のディレクトリがリンクでも同じ。
        other = os.path.join(self.state, "other")
        os.makedirs(os.path.join(other, "families"))
        write(os.path.join(other, "families", "i0002"), record_text("i0002", "present"))
        os.symlink(other, os.path.join(self.state, "sync", "p"))
        self.assertIn("リンク", syncstate.family(self.state, "p", "i0002").broken)
        # 統合先の取り込み結果そのものがリンク。
        os.makedirs(os.path.join(self.state, "real-integration"))
        write(os.path.join(self.state, "real-integration", "head"), "branch main\n")
        os.symlink(
            os.path.join(self.state, "real-integration"),
            os.path.join(self.state, "sync", "self", "integration"),
        )
        self.assertIn("リンク", syncstate.integration(self.state, "self").broken)

    def test_files_and_names_inside_the_integration_do_not_follow_links(self):
        self.put("sync/self/integration/head", "branch main\nsha abc\nsource default\n")
        self.put("sync/self/integration/.ccnavi/approved/done/i0009.md", "x\n")
        outside = self.put("secret.md", "s\n")
        os.symlink(
            outside,
            os.path.join(self.state, "sync/self/integration/.ccnavi/approved/done/i0010.md"),
        )
        integ = syncstate.integration(self.state, "self")
        self.assertEqual(("main", "abc", ""), (integ.branch, integ.sha, integ.broken))
        self.assertEqual(({"i0009"}, ""), syncstate.done_ids(integ, ".ccnavi/approved"))
        content, why = integ.file(".ccnavi/approved/done/i0010.md")
        self.assertIsNone(content)
        self.assertIn("リンク", why)
        self.assertEqual((b"x\n", ""), integ.file(".ccnavi/approved/done/i0009.md"))

    def test_the_swap_moment_is_read_again(self):
        # sh は integration を .old.$$ に退け、.tmp.$$ を integration に動かして入れ替える。
        base = os.path.join(self.state, "sync", "self")
        self.put("sync/self/integration.tmp.9/head", "branch main\nsha new\n")
        os.makedirs(os.path.join(base, "integration.old.9"))

        def finish_swap():
            os.rename(os.path.join(base, "integration.tmp.9"), os.path.join(base, "integration"))

        timer = threading.Timer(0.08, finish_swap)
        timer.start()
        self.addCleanup(timer.cancel)
        found = syncstate.integration(self.state, "self")
        self.assertIsNotNone(found)
        self.assertEqual("new", found.sha)

    def test_a_swap_that_never_ends_reads_as_broken(self):
        # 取り込んだ形跡があるのに入れ替えが終わらなければ、何も言わずに「取り込み状態が無い」にせず
        # 壊れているとする。
        self.put("sync/self/integration.old.9/head", "branch main\n")
        started = time.monotonic()
        found = syncstate.integration(self.state, "self")
        self.assertIn("入れ替えが終わらない", found.broken)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertEqual((set(), found.broken), syncstate.done_ids(found, ".ccnavi/approved"))

    def test_a_repository_never_taken_in_has_no_integration(self):
        self.put("sync/p/families/w0001", record_text("w0001", "present"))
        self.assertIsNone(syncstate.integration(self.state, "self"))
        self.assertIn("無い", syncstate.integration(self.state, "p").broken)

    def test_names_refuse_dot_dot_and_records_are_not_cut(self):
        self.put("sync/self/integration/head", "branch main\n")
        integ = syncstate.integration(self.state, "self")
        self.assertEqual([], integ.names("../x")[0])
        self.assertIn("読めないパス", integ.names("a/../b")[1])
        self.assertIn("読めないパス", integ.file("..")[1])
        self.put("sync/self/families/i0001", "state present\n" + "x" * (64 * 1024))
        self.assertIn("大きすぎる", syncstate.family(self.state, "self", "i0001").broken)

    def test_a_directory_without_head_is_waited_for(self):
        os.makedirs(os.path.join(self.state, "sync", "self", "integration"))

        def write_head():
            self.put("sync/self/integration/head", "branch main\nsha late\n")

        timer = threading.Timer(0.08, write_head)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertEqual("late", syncstate.integration(self.state, "self").sha)


class AuthorityHarness(PhaseHarness):
    """親 i0001 と子 i0001-01-01 を承認し、子のワークツリーを作って着手した状態から始める。"""

    def setUp(self):
        super().setUp()
        self.family(plan=["research", "design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("propose child")
        self.assertEqual(self.approve().returncode, 0)
        self.child_tree = self.run_child("i0001-01-01")

    def record(self, state, name="i0001", reason="", repo="self"):
        return write(
            os.path.join(self.state, "sync", repo, "families", name),
            record_text(name, state, reason),
        )

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


class NoRecordTest(AuthorityHarness):
    """取り込み状態の無い親子のチケットは前と同じ答え。

    `sync/` があっても、その親子のチケットの取り込み状態が無ければ同じ。
    """

    def stable_board(self):
        # 生成の時刻は秒で変わるので外す（秒の境目で比べがぶれない）。
        board = self.board()
        board.pop("generated_at", None)
        return board

    def test_answers_do_not_change_without_a_record(self):
        before = (
            self.decision(self.write_to(self.child_tree, "wip/research/a.md")),
            self.decision(self.write_to(self.child_tree, "src/a.py")),
            self.stable_board(),
            self.ccnavi("--lint", "--json").stdout,
        )
        os.makedirs(os.path.join(self.state, "sync", "self", "families"))
        self.record("gone", name="i0099")
        after = (
            self.decision(self.write_to(self.child_tree, "wip/research/a.md")),
            self.decision(self.write_to(self.child_tree, "src/a.py")),
            self.stable_board(),
            self.ccnavi("--lint", "--json").stdout,
        )
        self.assertEqual(before[:3], after[:3])
        self.assertNotEqual("deny", after[0])
        # lint は他の親子のチケット（i0099）の取り込み状態と、
        # 取り込んだ形跡があるのに統合先の取り込み結果が無いことだけを足して言う。
        extra = [
            p
            for p in json.loads(after[3])["problems"]
            if p not in json.loads(before[3])["problems"]
        ]
        self.assertTrue(extra)
        self.assertTrue(
            all(
                "i0099" in p["where"] + p["detail"] or p["where"] == "(sync/self/integration)"
                for p in extra
            ),
            extra,
        )


class PresentTest(AuthorityHarness):
    def test_the_parent_tree_is_the_authority(self):
        self.record("present")
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
        # 本物とする側のツリーが無いときに元ツリーを採る形。
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["wip/design/*"]))
        stray = os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-02-02.md")
        with open(
            os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02-02.md"),
            encoding="utf-8",
        ) as f:
            write(stray, f.read())
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02-02.md"))
        copies, _ = approval.scan(self.conf(), self.root)
        before = {t.ticket: t.blocked for t in copies}
        self.assertIn("i0001-02-02", before)
        self.record("present")
        copies, _ = approval.scan(self.conf(), self.root)
        after = {t.ticket: t.blocked for t in copies}
        # リストは変えない（落とさない）。理由だけが足される。
        self.assertEqual(sorted(before), sorted(after))
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
        self.record("present")
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
        self.record("present")
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        self.assertEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        started = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, started.returncode)
        self.assertIn("HEAD がブランチ i0001 を指していない", started.stderr)
        self.assertIn("切り直して", started.stderr)

    def test_a_state_operation_does_not_move_a_copy_outside_the_parent_tree(self):
        # 子のチケットが親のワークツリーから消え、元ツリーにだけ残った形。
        inside = os.path.join(self.approved, "doing", "i0001-01-01.md")
        with open(inside, encoding="utf-8") as f:
            text = f.read()
        os.remove(inside)
        stray = write(
            os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-01-01.md"), text
        )
        self.record("present")
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("ワークツリーの外", finished.stderr)
        self.assertTrue(os.path.isfile(stray))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".ccnavi", "approved", "done")))

    def test_the_check_names_an_unreadable_copy_in_the_parent_tree(self):
        self.record("present")
        self.assertEqual(0, self.ccnavi("sync", "check", "i0001").returncode)
        write(os.path.join(self.approved, "doing", "i0001-01-05.md"), "---\nticket: [\n---\n")
        check = self.ccnavi("sync", "check", "i0001")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertIn("i0001-01-05.md", check.stdout)

    def test_the_check_refuses_a_path_like_argument(self):
        check = self.ccnavi("sync", "check", "../x")
        self.assertEqual(1, check.returncode)
        self.assertIn("識別子の形ではない", check.stderr)

    def test_the_parent_is_written_to_the_parent_tree(self):
        self.record("present")
        # 元ツリーに同じ識別子のチケットがあっても、書く先は親のブランチ。
        write(os.path.join(self.root, ".ccnavi", "approved", "done", "i0001.md"), "x\n")
        where = approval.home_dir(self.conf(), self.root, "i0001", "")
        self.assertEqual(os.path.join(self.parent_tree, ".ccnavi", "approved"), where)


class UndecidedTest(AuthorityHarness):
    def test_gone_stops_the_judge_the_state_and_the_approval(self):
        before = self.decision(self.write_to(self.child_tree, "wip/research/a"))
        self.assertNotEqual("deny", before)
        self.record("gone")
        result = self.write_to(self.child_tree, "wip/research/a")
        self.assertEqual("deny", self.decision(result))
        self.assertIn("gone", self.reason(result))
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("gone", finished.stderr)
        self.assertIn("ccnavi-sync.sh", finished.stderr)
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual([], preview["batch"])
        self.assertTrue(any("gone" in " ".join(r["problems"]) for r in preview["rejected"]))

    def test_blocked_names_the_reason_and_the_way_out(self):
        self.record("blocked", reason="手元と判定が違う")
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("手元と判定が違う", finished.stderr)
        self.assertIn("打ち直して", finished.stderr)
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertTrue(
            any(
                p["severity"] == "error" and p["where"] == "(sync/self/i0001)"
                for p in lint["problems"]
            )
        )

    def test_a_record_behind_a_link_is_broken(self):
        real = write(os.path.join(self.root, "elsewhere"), record_text("i0001", "present"))
        os.makedirs(os.path.join(self.state, "sync", "self", "families"))
        os.symlink(real, os.path.join(self.state, "sync", "self", "families", "i0001"))
        self.assertEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertIn("壊れている", finished.stderr)

    def test_closed_family_does_not_move(self):
        self.record("closed")
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("閉じている", finished.stderr)
        self.assertEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))

    def test_review_operations_stop_too(self):
        self.record("gone")
        fixture = write(
            os.path.join(self.root, "fixture.json"),
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}),
        )
        ready = self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)
        self.assertNotEqual(0, ready.returncode)
        self.assertIn("gone", ready.stderr)


class TombstoneTest(AuthorityHarness):
    """親子のチケットの取り込み状態は削除せずに残り、親のワークツリーを片付けても止めが外れない。

    消すと、決まらないで止めていた親子のチケットが取り込み状態の無い扱いに戻り、止めが外れる。
    """

    def test_folding_the_parent_tree_does_not_lift_the_stop(self):
        self.record("gone")
        stray = os.path.join(self.root, ".ccnavi", "approved", "doing")
        os.makedirs(stray)
        # 元ツリーに同じ親子のチケットを残して、親のワークツリーを片付ける。
        for name in ("i0001.md", "i0001-01-01.md"):
            with open(os.path.join(self.approved, "doing", name), encoding="utf-8") as f:
                write(os.path.join(stray, name), f.read())
        git(self.root, "worktree", "remove", "--force", self.child_tree)
        git(self.root, "worktree", "remove", "--force", self.parent_tree)
        finished = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("gone", finished.stderr)
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertTrue(
            any(
                p["where"] == "(sync/self/i0001)" and p["severity"] == "error"
                for p in lint["problems"]
            )
        )

    def test_a_present_record_without_the_parent_tree_is_undecided_until_closed(self):
        self.record("present")
        git(self.root, "worktree", "remove", "--force", self.child_tree)
        git(self.root, "worktree", "remove", "--force", self.parent_tree)
        st = syncstate.standing(self.conf(), self.root, "i0001")
        self.assertTrue(st.stop and not st.closed, st)
        self.assertIn("片付けても残る", st.stop)
        # 統合先の取り込み結果の done/ に親のチケットがあれば、
        # 親子のチケットの取り込み状態に頼らず閉じた親子のチケット
        # （削除せずに残した取り込み状態は何も言わない）。
        base = os.path.join(self.state, "sync", "self", "integration")
        write(os.path.join(base, "head"), "branch main\nsha abc\n")
        write(
            os.path.join(base, ".ccnavi", "approved", "done", "i0001.md"),
            "---\nversion: 1\nticket: i0001\n---\n",
        )
        st = syncstate.standing(self.conf(), self.root, "i0001")
        self.assertTrue(st.closed, st)
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertFalse([p for p in lint["problems"] if p["where"] == "(sync/self/i0001)"])

    def test_an_old_family_with_the_same_id_is_not_read_as_closed(self):
        # 統合先の done/ のチケットの承認の時刻が、
        # 親のワークツリーのチケットと違えば閉じたとしない。
        self.record("present")
        base = os.path.join(self.state, "sync", "self", "integration")
        write(os.path.join(base, "head"), "branch main\nsha abc\n")
        write(
            os.path.join(base, ".ccnavi", "approved", "done", "i0001.md"),
            "---\nversion: 1\nticket: i0001\nccnavi_approved:\n"
            "  approved_at: 2000-01-01T00:00:00+0900\n---\n",
        )
        st = syncstate.standing(self.conf(), self.root, "i0001")
        self.assertFalse(st.closed)
        self.assertEqual("", st.stop)

    def test_the_forget_command_is_for_people(self):
        result = self.ccnavi(
            "--guard-ticket-approval",
            "enable",
            "--mode",
            "enable",
            stdin=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": self.root,
                    "session_id": "s1",
                    "tool_input": {"command": "sh .ccnavi/scripts/ccnavi-sync.sh --forget i0001"},
                }
            ),
        )
        self.assertEqual("deny", self.decision(result), result.stdout + result.stderr)
        self.assertIn("--forget", self.reason(result))


class BusyParentTest(AuthorityHarness):
    def test_a_parent_tree_in_the_middle_of_a_rebase_says_so(self):
        self.record("present")
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
        self.record("gone")
        Ticket = approval.ticket_mod.Ticket
        t = Ticket(
            ticket="i0001-01-01", parent="i0001", tree_root=self.parent_tree, blocked="前の理由"
        )
        approval.mark_imported(self.conf(), self.root, [t])
        self.assertTrue(t.blocked.startswith("前の理由 / "), t.blocked)
        self.assertIn("gone", t.blocked)

    def test_config_synced_stops_for_an_undecided_family(self):
        import io

        from ccnavi.tickets import configsync

        self.record("gone")
        err = io.StringIO()
        code = configsync.acknowledge(
            io.StringIO("y\n"), io.StringIO(), err, self.conf(), self.root, "i0001"
        )
        self.assertEqual(1, code)
        self.assertIn("gone", err.getvalue())

    def test_the_same_family_in_two_repositories_is_not_guessed(self):
        self.record("present")
        self.record("present", repo="web")
        st = syncstate.standing_any(self.conf(), self.root, "i0001")
        self.assertIn("複数のリポジトリ", st.stop)
        # リポジトリをつければ引ける。
        self.assertEqual("", syncstate.standing_any(self.conf(), self.root, "i0001", "").stop)

    def test_a_same_named_tree_in_another_repository_is_not_guessed(self):
        """取り込み状態が 1 つでも、同じ名前の親のワークツリーが別のリポジトリにあれば決めない。

        ワークスペースのユーザの付けた名前 `web-i0012` と、
        プロジェクト web の issue 12
        の親子のチケットが並ぶ形。
        """
        from ccnavi.infra import tree

        self.record("present")
        fams = syncstate.Families(self.conf(), self.root)
        mine = [w for w in fams.worktrees() if w.name == "i0001"]
        self.assertEqual(1, len(mine), mine)
        self.assertEqual("", fams.standing_any("i0001").stop)
        fams = syncstate.Families(self.conf(), self.root)
        fams._worktrees = [
            *fams.worktrees(),
            tree.Tree("i0001", os.path.join(self.root, "x"), "web"),
        ]
        st = fams.standing_any("i0001")
        self.assertIn("複数のリポジトリ（self, web）", st.stop)
        # リポジトリをつければ引ける。
        self.assertEqual("", fams.standing_any("i0001", "").stop)


class IntegrationDoneTest(AuthorityHarness):
    def integration_done(self, ticket_id):
        write(
            os.path.join(self.state, "sync", "self", "integration", "head"),
            "remote origin\nbranch main\nsource default\nsha abc\nfetched_at 1\n",
        )
        write(
            os.path.join(
                self.state,
                "sync",
                "self",
                "integration",
                ".ccnavi",
                "approved",
                "done",
                f"{ticket_id}.md",
            ),
            "---\nticket: x\n---\n",
        )

    def preview(self):
        return json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)

    def rejected_with(self, preview, ticket, words):
        return any(
            r["ticket"] == ticket and words in " ".join(r["problems"]) for r in preview["rejected"]
        )

    def test_an_identifier_closed_in_the_integration_is_not_new(self):
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        self.assertIn("i0001-01-02", [b["ticket"] for b in self.preview()["batch"]])
        # 親子のチケットの取り込み状態が無くても、取り込んだ形跡のあるリポジトリなら
        # 統合先の done/ で確かめる。古い統合先から切ったブランチで、識別子の再利用を
        # 新規として通さないため。
        self.integration_done("i0001-01-02")
        preview = self.preview()
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in preview["batch"]])
        self.assertTrue(self.rejected_with(preview, "i0001-01-02", "done/ で閉じている"), preview)
        self.record("present")
        preview = self.preview()
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in preview["batch"]])
        # 板にも理由つきで出る（何も言わずに消えない）。
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertTrue(any("i0001-01-02" in p["detail"] for p in lint["problems"]))

    def test_a_new_family_cannot_reuse_a_closed_identifier(self):
        # 取り込み状態の無い新しい親子のチケット（別の親）の識別子が、統合先の done/ で閉じている。
        self.propose("i0005", parent_text("i0005", ["research"]))
        self.commit_parent()
        self.assertIn("i0005", [b["ticket"] for b in self.preview()["batch"]])
        self.integration_done("i0005")
        preview = self.preview()
        self.assertNotIn("i0005", [b["ticket"] for b in preview["batch"]])
        self.assertTrue(self.rejected_with(preview, "i0005", "done/ で閉じている"), preview)

    def test_a_broken_or_missing_integration_does_not_pass_silently(self):
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        base = os.path.join(self.state, "sync", "self")
        # 取り込んだ形跡（親子のチケットの取り込み状態）はあるのに統合先の取り込み結果が無い
        # （最初の push で親子のチケットの取り込み状態ができた直後など）。
        self.record("present")
        preview = self.preview()
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in preview["batch"]])
        self.assertTrue(self.rejected_with(preview, "i0001-01-02", "決まらない"), preview)
        # head の無い取り込み状態（入れ替えが終わらない）。
        os.makedirs(os.path.join(base, "integration"))
        os.makedirs(os.path.join(base, "integration.old.9"))
        preview = self.preview()
        self.assertTrue(self.rejected_with(preview, "i0001-01-02", "入れ替えが終わらない"), preview)
        # 壊れた head（リンク）。
        os.rmdir(os.path.join(base, "integration.old.9"))
        real = write(os.path.join(self.root, "elsewhere-head"), "branch main\n")
        os.symlink(real, os.path.join(base, "integration", "head"))
        preview = self.preview()
        self.assertTrue(self.rejected_with(preview, "i0001-01-02", "決まらない"), preview)
        # 直せば通る。
        os.remove(os.path.join(base, "integration", "head"))
        write(os.path.join(base, "integration", "head"), "branch main\nsha abc\n")
        self.assertIn("i0001-01-02", [b["ticket"] for b in self.preview()["batch"]])


class PredecessorTest(AuthorityHarness):
    def test_an_undecided_predecessor_family_is_not_met(self):
        # 先行 i0002-01-01 は元ツリーの done/ にある（前の対応表では満たす）。
        # 親子のチケット i0002 は取り込み済みだが、親のワークツリーが無い（決まらない）ので、
        # 満たしたとみなさない。
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0002-01-01.md"),
            child_text("i0002-01-01", "i0002", 1, ["src/*"]).replace(
                'completed_at: ""', 'completed_at: "2026-09-01T00:00:00+0900"'
            ),
        )
        text = child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]).replace(
            "phase: 1\n", "phase: 1\npredecessors: [i0002-01-01]\n"
        )
        self.propose("i0001-01-02", text)
        self.commit_parent()
        before = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertIn("i0001-01-02", [b["ticket"] for b in before["batch"]], before)
        self.record("present", name="i0002")
        after = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertNotIn("i0001-01-02", [b["ticket"] for b in after["batch"]])
        self.assertTrue(
            any("親子のチケットが決まらない" in " ".join(r["problems"]) for r in after["rejected"]),
            after,
        )

    def test_the_parent_tree_does_not_loosen_a_predecessor(self):
        # 厳しくする向きだけ: 親のワークツリーで閉じていても、
        # 前の対応表で満たしていなければ満たさない。
        Ticket = approval.ticket_mod.Ticket
        done = Ticket(
            ticket="i0001-01-09", parent="i0001", state="done", tree_root=self.parent_tree
        )
        stale = Ticket(ticket="i0001-01-09", parent="i0001", state="doing", tree_root=self.root)
        pool = {"i0001-01-09": [done, stale]}
        self.record("present")
        approval.align_imported(self.conf(), self.root, pool)
        self.assertEqual([done, stale], pool["i0001-01-09"])
        # 親のワークツリーで閉じていなければ、それを採る（前の対応表が満たしていても）。
        doing = Ticket(
            ticket="i0001-01-08", parent="i0001", state="doing", tree_root=self.parent_tree
        )
        closed = Ticket(ticket="i0001-01-08", parent="i0001", state="done", tree_root=self.root)
        pool = {"i0001-01-08": [doing, closed]}
        approval.align_imported(self.conf(), self.root, pool)
        self.assertEqual([doing], pool["i0001-01-08"])


class ReadyTest(AuthorityHarness):
    def test_ready_needs_the_parent_in_done(self):
        fixture = write(
            os.path.join(self.root, "fixture.json"),
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}),
        )
        ready = self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)
        self.assertNotEqual(0, ready.returncode)
        self.assertIn("done/ に無い", ready.stderr)
        self.assertIn("finish i0001", ready.stderr)


class DigestTest(AuthorityHarness):
    """承認のダイジェストは判定が読んだ中身（read_set）で作る。"""

    def judged(self):
        conf = self.conf()
        return core.judge_approval(core.read_fs(conf, self.root))

    def test_a_change_in_what_the_judge_read_changes_the_digest(self):
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        first = self.judged()
        self.assertTrue(first.batch)
        self.assertIn("self:i0001:.ccnavi/approved/doing/i0001-01-01.md", first.read_set)
        self.assertIn("self:i0001:(HEAD)", first.read_set)
        self.assertIn("(設定):.claude/settings.json", first.read_set)
        self.assertIn("(設定値):approved", first.read_set)
        self.assertEqual(first.digest, self.judged().digest)
        # 一括の外のチケット（作業中の子）の中身が変われば、画面が同じでもダイジェストは変わる。
        path = os.path.join(self.approved, "doing", "i0001-01-01.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text + "\n追記\n")
        second = self.judged()
        self.assertEqual(first.screen_text, second.screen_text)
        self.assertNotEqual(first.digest, second.digest)

    def test_session_state_is_not_in_the_read_set(self):
        once = write(os.path.join(self.state, "once-x.json"), "a")
        record = self.record("present")
        with fsio.reading() as seen:
            fsio.note_read(once, "a")
            fsio.note_read(record, "b")
        keys = agree.read_set(self.conf(), self.root, seen)
        self.assertEqual(["(控え):sync/self/families/i0001"], list(keys))

    def test_keys_do_not_depend_on_a_linked_root(self):
        # 溜めた読みは行き着く先のパス。ルートをリンク越しに渡しても（macOS の /tmp など）
        # 同じ鍵になる。
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        linked = os.path.join(holder.name, "ws")
        os.symlink(self.root, linked)
        target = os.path.join(linked, ".claude", "worktrees", "i0001", "x.md")
        with fsio.reading() as seen:
            fsio.note_read(target, "x")
        keys = agree.read_set(self.conf(), linked, seen)
        self.assertIn("self:i0001:x.md", keys)
        self.assertFalse([k for k in keys if k.startswith("(外)")], keys)

    def test_a_settings_change_changes_the_digest(self):
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        first = self.judged()
        write(os.path.join(self.root, ".claude", "settings.local.json"), "{}\n")
        self.assertNotEqual(first.digest, self.judged().digest)

    def test_keys_of_two_repositories_do_not_collide(self):
        # ワークスペースとプロジェクトで同じブランチ名（main）の同じ相対パス。
        project = os.path.join(self.root, "projects", "web")
        os.makedirs(project)
        git(project, "init", "--quiet", "-b", "main")
        with fsio.reading() as seen:
            fsio.note_read(os.path.join(self.root, "x.md"), "a")
            fsio.note_read(os.path.join(project, "x.md"), "b")
        conf = self.conf()
        conf.projects = os.path.join(self.root, "projects")
        keys = agree.read_set(conf, self.root, seen)
        self.assertIn("self:main:x.md", keys)
        self.assertIn("web:main:x.md", keys)
        self.assertNotEqual(keys["self:main:x.md"], keys["web:main:x.md"])

    def test_nested_readers_are_removed_by_identity(self):
        with fsio.reading() as outer:
            with fsio.reading() as inner:
                pass
            fsio.note_read("/x/after", "a")
        self.assertEqual({}, inner)
        self.assertIn(os.path.normpath(fsio.parent_resolved("/x/after")), outer)

    def test_line_endings_do_not_change_the_digest(self):
        with fsio.reading() as seen:
            fsio.note_read("/x/a", "a\r\nb\n")
        with fsio.reading() as again:
            fsio.note_read("/x/a", b"a\nb\n")
        self.assertEqual(list(seen.values()), list(again.values()))


class LintTest(AuthorityHarness):
    def test_local_settings_may_hold_only_the_integration_branch(self):
        path = os.path.join(self.root, ".claude", "settings.local.json")
        write(path, json.dumps({"env": {"CCNAVI_INTEGRATION_BRANCH": "develop"}}))
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        self.assertFalse([p for p in problems if "settings.local.json" in p["detail"]])
        write(
            path,
            json.dumps(
                {
                    "env": {
                        "CCNAVI_INTEGRATION_BRANCH": "develop",
                        "CCNAVI_TICKETS_APPROVED": "elsewhere",
                        "CCNAVI_STATE": "x",
                        "CCNAVI_LOG_LEVEL": "DEBUG",
                    }
                }
            ),
        )
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        named = [p for p in problems if "settings.local.json" in p["detail"]]
        self.assertEqual(2, len(named), named)
        self.assertTrue(all(p["severity"] == "error" for p in named))
        self.assertTrue(any("CCNAVI_TICKETS_APPROVED" in p["detail"] for p in named))
        self.assertTrue(any("CCNAVI_STATE" in p["detail"] for p in named))

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

    def test_layers_that_differ_from_the_integration_are_named(self):
        base = os.path.join(self.state, "sync", "self", "integration")
        write(os.path.join(base, "head"), "branch main\nsha abc\n")
        with open(self.phases, encoding="utf-8") as f:
            write(os.path.join(base, ".ccnavi", "common", "phases.yml"), f.read())
        with open(self.rules, encoding="utf-8") as f:
            write(os.path.join(base, ".ccnavi", "common", "rules.yml"), f.read())
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        drift = [p for p in problems if p["where"] == "(sync/self/integration)"]
        self.assertEqual([], drift)
        write(os.path.join(base, ".ccnavi", "common", "phases.yml"), "types: {}\n")
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        drift = [p for p in problems if p["where"] == "(sync/self/integration)"]
        self.assertEqual(1, len(drift), drift)
        self.assertEqual("warn", drift[0]["severity"])
        self.assertIn("phases.yml", drift[0]["detail"])

    def test_the_projected_layer_on_the_parent_branch_is_compared(self):
        from ccnavi.entry import lint

        conf = self.conf()
        self_base = os.path.join(self.state, "sync", "self", "integration")
        proj_base = os.path.join(self.state, "sync", "web", "integration")
        write(os.path.join(self_base, "head"), "branch main\n")
        write(os.path.join(proj_base, "head"), "branch main\n")
        write(os.path.join(self_base, ".ccnavi", "common", "rules.yml"), "rules: []\n")
        write(os.path.join(proj_base, ".ccnavi", "config", "phases.yml"), "types: {}\n")
        home = tempfile.mkdtemp(dir=self.root)
        st = syncstate.Standing(
            "w0001",
            "web",
            record=syncstate.Family("w0001", "web", "present"),
            home=approval.tree.Tree("w0001", home, project="web", kind="worktree"),
        )
        problems = lint._projected_layer_problems(conf, st, "(x)")
        # 共通層の rules と、プロジェクトの統合先の phases が P の上に無い。
        self.assertEqual(2, len(problems), problems)
        write(os.path.join(home, ".ccnavi", "config", "rules.yml"), "rules: []\n")
        write(os.path.join(home, ".ccnavi", "config", "phases.yml"), "types: {}\r\n")
        self.assertEqual([], lint._projected_layer_problems(conf, st, "(x)"))
        write(os.path.join(home, ".ccnavi", "config", "risks.yml"), "x: 1\n")
        problems = lint._projected_layer_problems(conf, st, "(x)")
        self.assertEqual(1, len(problems))
        self.assertIn("risks.yml", problems[0].detail)


class HookNoProcessTest(AuthorityHarness):
    """判定は取り込み状態を読むだけで、外部プロセスを起こさない（tree.py の前提）。"""

    def test_the_judge_does_not_spawn(self):
        self.record("present")
        import subprocess
        from unittest import mock

        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("起こした")):
            conf = self.conf()
            copies, _ = approval.scan(conf, self.root)
            approval.predecessor_pool(conf, self.root)
            syncstate.integration(conf.state, "self")
        self.assertTrue(copies)


if __name__ == "__main__":
    unittest.main()
