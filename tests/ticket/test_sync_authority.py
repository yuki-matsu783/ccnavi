"""取り込み済みの家族の権威（ADR-0093 の 3.3〜3.6。段階 2c）。

家族の控え（`<控えの置き場>/sync/<リポジトリ>/families/<P>`）がある家族は、権威を親のブランチ
（`.claude/worktrees/<P>` で HEAD が `<P>` を指すツリー）に固定し、決まらなければ承認も状態の操作も
実行前の判定も止める。控えの無い家族は前と同じ答え（D11）。

控えは sh（`ccnavi-sync.sh`）が書くものを、ここでは手で置く。判定は git もネットワークも使わない。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest

from ccnavi import approval, core, fsio, settings, syncstate
from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import git, write


def record_text(name, state, reason="", sha="0" * 40):
    return (
        f"remote origin\nbranch {name}\nsha {sha}\nfetched_at 1\nstate {state}\nreason {reason}\n"
    )


class ReaderTest(unittest.TestCase):
    """控えの読み方。リンクを辿らない、入れ替えの一瞬を待つ、控えが無ければ待たない。"""

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
        # 統合先の控えそのものがリンク。
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
        self.assertEqual({"i0009"}, syncstate.done_ids(integ, ".ccnavi/approved"))
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

    def test_a_swap_that_never_ends_reads_as_no_integration(self):
        self.put("sync/self/integration.old.9/head", "branch main\n")
        started = time.monotonic()
        self.assertIsNone(syncstate.integration(self.state, "self"))
        self.assertGreaterEqual(time.monotonic() - started, 0.2)

    def test_a_directory_without_head_is_waited_for(self):
        os.makedirs(os.path.join(self.state, "sync", "self", "integration"))

        def write_head():
            self.put("sync/self/integration/head", "branch main\nsha late\n")

        timer = threading.Timer(0.08, write_head)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertEqual("late", syncstate.integration(self.state, "self").sha)


class AuthorityHarness(PhaseHarness):
    """親 i0001 と子 i0001-01 を承認し、子のワークツリーを作って着手した状態から始める。"""

    def setUp(self):
        super().setUp()
        self.family(plan=["research", "design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("propose child")
        self.assertEqual(self.approve().returncode, 0)
        self.child_tree = self.run_child("i0001-01")

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
    """控えの無い家族は前と同じ答え（D11）。`sync/` があっても、その家族の控えが無ければ同じ。"""

    def test_answers_do_not_change_without_a_record(self):
        before = (
            self.decision(self.write_to(self.child_tree, "wip/research/a.md")),
            self.decision(self.write_to(self.child_tree, "src/a.py")),
            self.board(),
            self.ccnavi("--lint", "--json").stdout,
        )
        os.makedirs(os.path.join(self.state, "sync", "self", "families"))
        self.record("gone", name="i0099")
        after = (
            self.decision(self.write_to(self.child_tree, "wip/research/a.md")),
            self.decision(self.write_to(self.child_tree, "src/a.py")),
            self.board(),
            self.ccnavi("--lint", "--json").stdout,
        )
        self.assertEqual(before[:3], after[:3])
        self.assertNotEqual("deny", after[0])
        # lint は他の家族（i0099）の控えのことだけを足して言う。
        extra = [
            p
            for p in json.loads(after[3])["problems"]
            if p not in json.loads(before[3])["problems"]
        ]
        self.assertTrue(extra)
        self.assertTrue(all("i0099" in p["where"] + p["detail"] for p in extra), extra)


class PresentTest(AuthorityHarness):
    def test_the_parent_tree_is_the_authority(self):
        self.record("present")
        self.assertNotEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        board = self.board()
        self.assertEqual("", self.blocked_of(board, "i0001-01"))

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
        # 元ツリー（ワークスペースルート）に未コミットで残った写し（ADR-0073 の形）。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        stray = os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-02.md")
        with open(
            os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02.md"),
            encoding="utf-8",
        ) as f:
            write(stray, f.read())
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02.md"))
        copies, _ = approval.scan(self.conf(), self.root)
        before = {t.ticket: t.blocked for t in copies}
        self.assertIn("i0001-02", before)
        self.record("present")
        copies, _ = approval.scan(self.conf(), self.root)
        after = {t.ticket: t.blocked for t in copies}
        # 並びは変えない（落とさない）。印だけが足される。
        self.assertEqual(sorted(before), sorted(after))
        self.assertIn("ワークツリーの外", after["i0001-02"])
        self.assertEqual("", after["i0001-01"])
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        self.assertTrue(
            any(
                p["severity"] == "error" and "i0001-02" in p["detail"] and "外" in p["detail"]
                for p in lint["problems"]
            ),
            lint,
        )
        check = self.ccnavi("sync", "check", "i0001")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertTrue(check.stdout.startswith("error "), check.stdout)

    def test_a_proposal_outside_the_parent_tree_is_not_approved(self):
        self.record("present")
        write(
            os.path.join(self.root, "wip", "proposals", "todo", "i0001-02.md"),
            child_text("i0001-02", "i0001", 1, ["wip/research/*"]),
        )
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertEqual([], preview["batch"])
        self.assertTrue(
            any("ワークツリーの外" in " ".join(r["problems"]) for r in preview["rejected"]),
            preview,
        )

    def test_the_parent_tree_on_another_branch_is_undecided(self):
        self.record("present")
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        self.assertEqual("deny", self.decision(self.write_to(self.child_tree, "wip/research/a")))
        started = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertNotEqual(0, started.returncode)
        self.assertIn("HEAD がブランチ i0001 を指していない", started.stderr)
        self.assertIn("切り直す", started.stderr)

    def test_a_state_operation_does_not_move_a_copy_outside_the_parent_tree(self):
        # 子の写しが親のワークツリーから消え、元ツリーにだけ残った形（ADR-0073 の形）。
        inside = os.path.join(self.approved, "doing", "i0001-01.md")
        with open(inside, encoding="utf-8") as f:
            text = f.read()
        os.remove(inside)
        stray = write(os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001-01.md"), text)
        self.record("present")
        finished = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("ワークツリーの外", finished.stderr)
        self.assertTrue(os.path.isfile(stray))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".ccnavi", "approved", "done")))

    def test_the_check_names_an_unreadable_copy_in_the_parent_tree(self):
        self.record("present")
        self.assertEqual(0, self.ccnavi("sync", "check", "i0001").returncode)
        write(os.path.join(self.approved, "doing", "i0001-05.md"), "---\nticket: [\n---\n")
        check = self.ccnavi("sync", "check", "i0001")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertIn("i0001-05.md", check.stdout)

    def test_the_check_refuses_a_path_like_argument(self):
        check = self.ccnavi("sync", "check", "../x")
        self.assertEqual(1, check.returncode)
        self.assertIn("識別子の形ではない", check.stderr)

    def test_the_parent_is_written_to_the_parent_tree(self):
        self.record("present")
        # 元ツリーに同じ識別子の写しがあっても、書く先は親のブランチ。
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
        finished = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("gone", finished.stderr)
        self.assertIn("ccnavi-sync.sh", finished.stderr)
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertEqual([], preview["batch"])
        self.assertTrue(any("gone" in " ".join(r["problems"]) for r in preview["rejected"]))

    def test_blocked_names_the_reason_and_the_way_out(self):
        self.record("blocked", reason="手元と判定が違う")
        finished = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("手元と判定が違う", finished.stderr)
        self.assertIn("打ち直す", finished.stderr)
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
        finished = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertIn("壊れている", finished.stderr)

    def test_closed_family_does_not_move(self):
        self.record("closed")
        finished = self.ccnavi("ticket", "finish", "i0001-01")
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

    def test_an_identifier_closed_in_the_integration_is_not_new(self):
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        self.integration_done("i0001-02")
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertIn("i0001-02", [b["ticket"] for b in preview["batch"]])
        self.record("present")
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertNotIn("i0001-02", [b["ticket"] for b in preview["batch"]])


class PredecessorTest(AuthorityHarness):
    def test_an_undecided_predecessor_family_is_not_met(self):
        # 先行 i0002-01 は元ツリーの done/ にある（前の池では満たす）。家族 i0002 は取り込み済みだが
        # 親のワークツリーが無い（決まらない）ので、満たしたとみなさない。
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0002-01.md"),
            child_text("i0002-01", "i0002", 1, ["src/*"]).replace(
                'completed_at: ""', 'completed_at: "2026-09-01T00:00:00+0900"'
            ),
        )
        text = child_text("i0001-02", "i0001", 1, ["wip/research/*"]).replace(
            "phase: 1\n", "phase: 1\npredecessors: [i0002-01]\n"
        )
        self.propose("i0001-02", text)
        self.commit_parent()
        before = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertIn("i0001-02", [b["ticket"] for b in before["batch"]], before)
        self.record("present", name="i0002")
        after = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertNotIn("i0001-02", [b["ticket"] for b in after["batch"]])
        self.assertTrue(
            any("家族が決まらない" in " ".join(r["problems"]) for r in after["rejected"]), after
        )

    def test_the_parent_tree_does_not_loosen_a_predecessor(self):
        # 締める向きだけ: 親のワークツリーで閉じていても、前の池で満たしていなければ満たさない。
        Ticket = approval.ticket_mod.Ticket
        done = Ticket(ticket="i0001-09", parent="i0001", state="done", tree_root=self.parent_tree)
        stale = Ticket(ticket="i0001-09", parent="i0001", state="doing", tree_root=self.root)
        pool = {"i0001-09": [done, stale]}
        self.record("present")
        approval.align_imported(self.conf(), self.root, pool)
        self.assertEqual([done, stale], pool["i0001-09"])
        # 親のワークツリーで閉じていなければ、それを採る（前の池が満たしていても）。
        doing = Ticket(ticket="i0001-08", parent="i0001", state="doing", tree_root=self.parent_tree)
        closed = Ticket(ticket="i0001-08", parent="i0001", state="done", tree_root=self.root)
        pool = {"i0001-08": [doing, closed]}
        approval.align_imported(self.conf(), self.root, pool)
        self.assertEqual([doing], pool["i0001-08"])


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
    """承認の指紋は判定が読んだ中身（read_set）で作る（ADR-0093 の 6.2。段階 2c）。"""

    def judged(self):
        conf = self.conf()
        return core.judge_approval(core.read_fs(conf, self.root))

    def test_a_change_in_what_the_judge_read_changes_the_digest(self):
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        first = self.judged()
        self.assertTrue(first.batch)
        self.assertIn("i0001:.ccnavi/approved/doing/i0001-01.md", first.read_set)
        self.assertEqual(first.digest, self.judged().digest)
        # 束の外の写し（作業中の子）の中身が変われば、画面が同じでも指紋は変わる。
        path = os.path.join(self.approved, "doing", "i0001-01.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text + "\n追記\n")
        second = self.judged()
        self.assertEqual(first.screen_text, second.screen_text)
        self.assertNotEqual(first.digest, second.digest)

    def test_session_state_is_not_in_the_read_set(self):
        seen = {
            os.path.join(self.state, "once-x.json"): "a",
            os.path.join(self.state, "sync", "self", "families", "i0001"): "b",
        }
        keys = approval.read_set(self.conf(), self.root, seen)
        self.assertEqual({"(控え):sync/self/families/i0001": "b"}, keys)

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
        from ccnavi import lint

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
    """判定は控えを読むだけで、外部プロセスを起こさない（tree.py の前提）。"""

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
