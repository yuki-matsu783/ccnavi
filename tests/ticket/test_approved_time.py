"""承認の時刻。承認は承認済みチケットに時刻を書かないので、表示（ボードの JSON と `--explain`）は
状態の履歴 → `doing/` に足したコミット → 「未コミット（手で置いた）」の順で引く。

git はツリーごとに 1 回だけ打つ。git の無い場（Chrome の Pyodide）では履歴だけを読む。
判定の拒否文には時刻を出さない（hook の中では履歴も git も読まない）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from ccnavi.infra import gitcmd, settings
from ccnavi.tickets import approval, history
from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import git, write


class ApprovedTimeTest(PhaseHarness):
    def conf(self):
        conf, _ = settings.load(self.root)
        conf.state = self.state
        return conf

    def copies(self):
        found, _ = approval.scan(self.conf(), self.root)
        return {t.ticket: t for t in found}

    def board_copy(self, ident):
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        return next(t for t in board["tickets"] if t["ticket"] == ident)["copy"]

    def move_by_hand(self, ident, text, commit=True):
        """GitHub の画面での移動と同じく、提案を doing/ へ動かす（状態の履歴は書かない）。"""
        write(os.path.join(self.approved, "doing", ident + ".md"), text)
        if commit:
            self.commit_parent(f"Rename {ident}")

    def test_the_history_is_read_first(self):
        self.family(plan=["research"])
        copy = self.board_copy("i0001")
        events, _ = history.read(self.approved, "i0001")
        approved = [e for e in events if e["kind"] == history.KIND_APPROVED]
        self.assertEqual(copy["approved_at"], approved[-1]["at"])
        self.assertEqual(copy["approved_from"], approval.APPROVED_FROM_HISTORY)

    def test_a_committed_manual_approval_reads_the_commit_time(self):
        self.family(plan=["research"])
        child = child_text("i0001-01", "i0001", 1, ("wip/research/*",), False)
        self.move_by_hand("i0001-01", child)
        when = git(self.parent_tree, "log", "-1", "--format=%cI").strip()
        copy = self.board_copy("i0001-01")
        self.assertEqual(copy["approved_at"], when)
        self.assertEqual(copy["approved_from"], approval.APPROVED_FROM_COMMIT)

    def test_a_rename_commit_counts_as_added(self):
        # GitHub の画面での移動は rename のコミットになる。--no-renames で足したことにする。
        self.family(plan=["research"])
        child = child_text("i0001-01", "i0001", 1, ("wip/research/*",), False)
        self.propose("i0001-01", child)
        self.commit_parent("propose")
        todo = os.path.join("wip", "proposals", "todo", "i0001-01.md")
        doing = os.path.join(".ccnavi", "approved", "doing", "i0001-01.md")
        git(self.parent_tree, "mv", todo, doing)
        self.commit_parent("Rename")
        shown = git(self.parent_tree, "show", "--stat", "-M", "--format=", "HEAD")
        self.assertIn("=>", shown)
        copy = self.board_copy("i0001-01")
        self.assertEqual(copy["approved_from"], approval.APPROVED_FROM_COMMIT)

    def test_a_reapproval_after_a_withdrawal_reads_the_commit_time(self):
        """取り下げのあとに手で動かして承認し直したら、前の承認の行は使わず、コミットの時刻を読む。"""
        self.family(plan=["research"])
        history.note(self.approved, "i0001", history.KIND_WITHDRAWN, "doing", "todo")
        text_path = os.path.join(self.approved, "doing", "i0001.md")
        with open(text_path, encoding="utf-8") as f:
            text = f.read()
        os.remove(text_path)
        self.commit_parent("withdrawn")
        self.move_by_hand("i0001", text)
        when = git(self.parent_tree, "log", "-1", "--format=%cI").strip()
        copy = self.board_copy("i0001")
        self.assertEqual(copy["approved_from"], approval.APPROVED_FROM_COMMIT)
        self.assertEqual(copy["approved_at"], when)

    def test_an_uncommitted_manual_approval_is_said_so(self):
        self.family(plan=["research"])
        child = child_text("i0001-01", "i0001", 1, ("wip/research/*",), False)
        self.move_by_hand("i0001-01", child, commit=False)
        copy = self.board_copy("i0001-01")
        self.assertEqual(copy["approved_at"], "")
        self.assertEqual(copy["approved_from"], approval.APPROVED_UNCOMMITTED)
        shown = self.ccnavi("--explain").stdout
        self.assertIn("i0001-01（", shown)
        self.assertIn("承認 未コミット（手で置いた）", shown)

    def test_git_runs_once_per_tree_and_not_without_git(self):
        self.family(plan=["research"])
        for n in (1, 2):
            ident = f"i0001-0{n}"
            self.move_by_hand(ident, child_text(ident, "i0001", 1, ("wip/research/*",), False))
        found = self.copies()
        runs = []
        real = gitcmd.run

        def counted(cwd, args, *rest, **kw):
            runs.append(args[0])
            return real(cwd, args, *rest, **kw)

        with mock.patch.object(gitcmd, "run", counted):
            times = approval.approved_times(self.conf(), list(found.values()))
        self.assertEqual(runs, ["log"])
        self.assertEqual(times[found["i0001"].path].source, approval.APPROVED_FROM_HISTORY)
        self.assertEqual(times[found["i0001-02"].path].source, approval.APPROVED_FROM_COMMIT)
        runs.clear()
        with mock.patch.object(gitcmd, "run", counted):
            times = approval.approved_times(self.conf(), list(found.values()), use_git=False)
        self.assertEqual(runs, [])
        self.assertNotIn(found["i0001-02"].path, times)

    def test_a_raised_followup_reads_raised(self):
        self.family(plan=["research"])
        with history.session(history.VIA_TERMINAL, None):
            history.note(self.approved, "i0001-07", history.KIND_RAISED, None, "doing")
        self.assertTrue(approval._history_time(self.approved, "i0001-07"))

    def test_the_refusal_text_does_not_carry_the_approval_time(self):
        self.family(plan=["research"])
        result = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "outside", "x.md"),
            content="x\n",
        )
        said = result.stdout + result.stderr
        self.assertIn("ticket: i0001", said)
        self.assertNotIn("approved", said.split("ticket: i0001", 1)[1].split("\n", 1)[0])


class GitMissingTest(unittest.TestCase):
    def test_a_tree_that_is_not_a_repository_gives_no_time(self):
        with tempfile.TemporaryDirectory() as root:
            conf, _ = settings.load(root)
            found, ok = approval._added_times(conf, root)
        self.assertEqual((found, ok), ({}, False))


if __name__ == "__main__":
    unittest.main()
