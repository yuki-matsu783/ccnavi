"""親を閉じたときのワークツリーとブランチの片付け（`worktree retire` と `sync`）。

判断の中身（退避の後の片付け・取り消した子・確かめられない状態・大文字小文字・壊れた記録・記録の列挙・放棄・
MR のマージの後の片付け）は、go/internal/tickets/lifecycle/retire の見本（`TestMatchesGolden`）と
`scenes_test.go` の表が見る。ここで見るのは、`ready`・`worktree retire`・`sync` を
sh から起こす通し。
"""

from __future__ import annotations

import json
import os

from tests import treecache
from tests.ticket.test_archive import ReadyHarness
from tests.ticket.test_ticket import git, write

CHILD = "i0001-01-01"


class RetireHarness(ReadyHarness):
    @treecache.cached
    def closed_and_pushed(self):
        """親を閉じて ready を済ませ、退避の削除をコミットして push した状態。fixture を返す。"""
        fixture = self.closable()
        done = self.ready(fixture)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.commit_parent("退避")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def retire(self, *words):
        return self.ccnavi("--cwd", self.root, "worktree", *(words or ("retire", "i0001")))

    @property
    def child_tree(self):
        return os.path.join(self.root, ".claude", "worktrees", CHILD)

    def branches(self):
        return git(self.root, "branch", "--format=%(refname:short)").split()

    @property
    def record(self):
        return os.path.join(self.state, "retire", "i0001.json")

    def read_record(self):
        with open(self.record, encoding="utf-8") as f:
            return json.load(f)

    def tidy(self, sha, branch="i0001"):
        return self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", branch, sha)

    def pushed_head(self):
        return git(self.root, "rev-parse", "refs/remotes/origin/i0001").strip()


class RetireAfterReadyTest(RetireHarness):
    def test_ready_notes_the_children_and_the_mr_before_the_tickets_move_to_the_archive(self):
        self.closed_and_pushed()
        held = self.read_record()
        self.assertEqual(held["branch"], "i0001")
        self.assertEqual(held["mr"], 7)
        merged = git(self.parent_tree, "rev-parse", CHILD).strip()
        self.assertEqual(
            [(c["ticket"], c["kind"], c["merged_sha"]) for c in held["children"]],
            [(CHILD, "merged", merged)],
        )

    def test_retire_removes_the_worktrees_and_the_merged_child_branch(self):
        self.closed_and_pushed()
        write(os.path.join(self.root, ".gitignore"), "/scratchpad\n")
        exclude = git(self.child_tree, "rev-parse", "--git-path", "info/exclude").strip()
        exclude = exclude if os.path.isabs(exclude) else os.path.join(self.child_tree, exclude)
        write(exclude, ".env\n")
        write(os.path.join(self.child_tree, ".env"), "SECRET=1\n")
        self.assertTrue(os.path.isdir(self.child_tree))
        done = self.retire()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"ワークツリー {CHILD} を削除しました", done.stdout)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertIn("git が無視しているファイルを", done.stdout)
        self.assertFalse(os.path.exists(self.child_tree))
        self.assertFalse(os.path.exists(self.parent_tree))
        # 無視されたファイルは消えずに退避される。
        kept = os.path.join(self.root, "scratchpad", "worktrees", CHILD, ".env")
        self.assertTrue(os.path.isfile(kept))
        # 子のブランチは消え、親のブランチは MR のマージまで残る
        self.assertEqual(self.branches(), ["i0001", "main"])
        # 打ち直しても何も起きない
        again = self.retire()
        self.assertEqual((again.returncode, again.stdout.count("削除しました")), (0, 0))


class TidyAfterMergeTest(RetireHarness):
    """MR がマージされたと分かった後の片付け（親のブランチと片付けの記録）。"""

    def test_the_parent_branch_the_worktree_and_the_note_are_removed(self):
        self.closed_and_pushed()
        done = self.tidy(self.pushed_head())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "record yes")
        self.assertEqual(self.branches(), ["main"])
        self.assertFalse(os.path.exists(self.record))
        # 先にワークツリーを消し、あとでブランチを消す。
        lines = done.stdout.splitlines()
        worktree = next(i for i, line in enumerate(lines) if "ワークツリー i0001 を削除" in line)
        branch = next(i for i, line in enumerate(lines) if "ブランチ i0001 を削除" in line)
        self.assertLess(worktree, branch)


class NoteTest(RetireHarness):
    """`ready` が書く片付けの記録（`retire/<親>.json`）。中身は go の見本が見る。"""

    def test_a_broken_record_is_not_overwritten_and_ready_stops(self):
        fixture = self.closable()
        write(self.record, "{broken")
        done = self.ready(fixture)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("上書きしない", done.stderr)
        with open(self.record, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")
        # 退避に進んでいないので、親はワークツリーに残り、直せば打ち直せる。
        self.assertTrue(os.path.exists(os.path.join(self.approved, "done", "i0001.md")))
        os.remove(self.record)
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.read_record()["mr"], 7)

    def test_an_unreadable_closed_ticket_stops_ready(self):
        fixture = self.closable()
        write(os.path.join(self.approved, "done", "i0001-01-77.md"), "---\nticket: [x\n---\n")
        self.commit_parent("読めない閉じたチケット")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        done = self.ready(fixture)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("読めない閉じたチケットがあり", done.stderr, done.stderr)
        self.assertFalse(os.path.exists(self.record))


class RetiredEntriesTest(RetireHarness):
    """`ccnavi sync retired`（片付けの列挙元）の行の形。記録の読み方は go の見本が見る。"""

    def retired(self, *flags):
        done = self.ccnavi(*flags, "sync", "retired")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], "retired 1")
        return lines[1:]

    def put(self, name, text):
        write(os.path.join(self.state, "retire", name), text)

    def test_the_lines(self):
        self.assertEqual(self.retired(), [])
        self.closed_and_pushed()
        self.assertEqual(self.retired(), ["retired i0001 self 7 i0001"])
        # チケット制御を切っても動く。
        self.assertEqual(
            self.retired("--ticket-control", "disable"), ["retired i0001 self 7 i0001"]
        )
        self.put(
            "pj0001.json",
            json.dumps({"parent": "pj0001", "project": "app", "branch": "pj0001", "mr": 3}),
        )
        self.put("i0002.json", json.dumps({"parent": "i0002", "project": "", "mr": 5}))
        self.put("i0003.json", json.dumps({"parent": "i0003", "project": "", "branch": "i0003"}))
        self.put("i0004.json", "{broken")
        self.put("i0005.json", json.dumps({"parent": "i0009", "project": "", "mr": 5}))
        self.put("i0006.json.tmp.1", json.dumps({"parent": "i0006", "mr": 5}))
        self.put("note.txt", "x")
        lines = self.retired()
        self.assertEqual(
            sorted(line for line in lines if line.startswith("retired")),
            ["retired i0001 self 7 i0001", "retired i0002 self 5 -", "retired pj0001 app 3 pj0001"],
        )
        incomplete = sorted(line for line in lines if line.startswith("incomplete"))
        self.assertEqual(len(incomplete), 2, lines)
        self.assertTrue(incomplete[0].startswith("incomplete i0003 "), incomplete)
        self.assertTrue(incomplete[1].startswith("incomplete i0004 "), incomplete)
        self.assertEqual(len(lines), 5, lines)


class AbandonTest(RetireHarness):
    """`ccnavi worktree retire --abandon <親>`。"""

    def abandon(self, name="i0001"):
        return self.ccnavi("--cwd", self.root, "worktree", "retire", "--abandon", name)

    def test_the_record_goes_only_once_the_worktree_is_gone(self):
        self.closed_and_pushed()
        refused = self.abandon()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("ワークツリー", refused.stderr)
        self.assertTrue(os.path.exists(self.record))
        self.assertEqual(self.retire().returncode, 0)
        self.assertTrue(os.path.exists(self.record))
        done = self.abandon()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.record))
        # ブランチには触らない
        self.assertIn("i0001", self.branches())
        again = self.abandon()
        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        self.assertIn("記録はありません", again.stderr)
        # 悪い名前と、retire 以外に付けた --abandon は断る。
        self.assertEqual(self.abandon("../x").returncode, 1)
        other = self.ccnavi("--cwd", self.root, "worktree", "drop", "--abandon", "i0001")
        self.assertEqual(other.returncode, 1, other.stdout + other.stderr)
