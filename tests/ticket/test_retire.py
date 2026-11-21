"""親を閉じたときのワークツリーとブランチの片付け（`worktree retire` と `sync`）。

判断の中身（退避の後の片付け・取り消した子・記録の列挙・放棄・MR のマージの後の片付け）は、
go/internal/tickets/lifecycle/retire の見本（`TestMatchesGolden`）が 1 字単位で見る。
ここで見るのは、見本が持たない次の観点。

1. `ready` が片付けの記録を書く。退避した後の片付けの通し（無視されたファイルの退避を含む）
2. 確かめられない状態では何も消さない（push 済みの親に含まれない子、親の HEAD が記録の
   ブランチでない、手元に無い先頭、push の無いブランチ）
3. 取り消した子のブランチは、確認つきで記録とチケットが揃うときだけ消す
4. 大文字小文字だけが違う名前を同じものとして扱う
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

    def test_a_dirty_child_and_a_merged_head_that_the_pushed_parent_lacks_keep_the_branch(self):
        self.closed_and_pushed()
        memo = write(os.path.join(self.child_tree, "memo.txt"), "書きかけ\n")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertIn(f"git -C {self.child_tree} status", done.stdout)
        self.assertTrue(os.path.isdir(self.child_tree))
        self.assertIn("チェックアウトしている", done.stdout)
        self.assertIn(CHILD, self.branches())
        os.remove(memo)
        git(self.root, "update-ref", "refs/remotes/origin/i0001", "main")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("push 済みの親に含まれない", done.stdout)
        self.assertIn(CHILD, self.branches())


class CancelledChildHarness(RetireHarness):
    """取り消した子を記録に足した状態。テストは持たない（継ぐクラスで同じテストが重ねて回らないように）。"""

    def setUp(self):
        super().setUp()
        self.closed_and_pushed()
        self.add_cancelled("i0001-01-09")

    def add_cancelled(self, name):
        git(self.root, "branch", name, "main")
        held = self.read_record()
        held["children"].append(
            {"ticket": name, "kind": "cancelled", "merged_sha": "", "project": ""}
        )
        write(self.record, json.dumps(held))
        # ready が退避へ移した、取り消した子のチケット。`worktree branch` は記録の子が実際に
        # この親の子かを、ここ（か承認済みの置き場）で確かめ直す。
        self.archived(name, "i0001")

    def archived(self, name, parent):
        path = os.path.join(self.root, "logs", "archive", "self", "done", f"{name}.md")
        lines = ["---", "version: 1", f"ticket: {name}", f"parent: {parent}", "phase: 1"]
        lines += ["title: t", "cancelled_at: 2026-10-01T00:00:00+09:00", "---", ""]
        write(path, "\n".join(lines) + "\n")


class CancelledChildTest(CancelledChildHarness):
    """取り消した子のブランチは、確認つきで、記録の親の子だと確かめられるときだけ消す。"""

    def test_a_confirmed_branch_is_deleted_only_when_the_record_and_the_ticket_agree(self):
        # 記録を書き換えて、この親の子でないブランチを足しても消さない。
        git(self.root, "branch", "i0002-01-01", "main")
        self.archived("i0002-01-01", "i0002")
        held = self.read_record()
        held["children"].append(
            {"ticket": "i0002-01-01", "kind": "cancelled", "merged_sha": "", "project": ""}
        )
        write(self.record, json.dumps(held))
        refused = self.retire("branch", "i0002-01-01", "confirmed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("記録を信じて消さない", refused.stderr)
        self.assertIn("i0002-01-01", self.branches())
        # 壊れた別の親の記録があっても、この親の片付けは止まらない。
        write(
            os.path.join(self.state, "retire", "i0000.json"),
            json.dumps({"parent": "i0000", "project": "", "children": None}),
        )
        write(os.path.join(self.state, "retire", "i0009.json"), "{broken")
        done = self.retire("branch", "i0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ブランチ i0001-01-09 を削除しました", done.stdout)
        self.assertNotIn("i0001-01-09", self.branches())


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

    def test_a_head_that_cannot_be_checked_is_not_guessed_and_nothing_is_removed(self):
        self.closed_and_pushed()
        head = self.pushed_head()

        def kept(done, *needles):
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            for needle in needles:
                self.assertIn(needle, done.stdout)
            self.assertEqual(done.stdout.splitlines()[-1], "record no", done.stdout)
            self.assertTrue(os.path.isdir(self.parent_tree))
            self.assertIn("i0001", self.branches())
            self.assertTrue(os.path.exists(self.record))

        # 未コミットの変更
        memo = write(os.path.join(self.parent_tree, "memo.txt"), "書きかけ\n")
        kept(
            self.tidy(head),
            "未コミットの変更があるので、何も削除しませんでした",
            "チェックアウトしている",
        )
        os.remove(memo)
        # 手元に無い先頭は推測しない
        kept(
            self.tidy("0123456789abcdef0123456789abcdef01234567"),
            "手元に無く、含まれるか確かめられない",
        )
        # push の無いブランチ
        git(self.root, "update-ref", "-d", "refs/remotes/origin/i0001")
        kept(self.tidy(head), "手元に無く、未 push のコミットが無いか確かめられない")
        git(self.root, "update-ref", "refs/remotes/origin/i0001", head)
        # 未 push のコミット。渡された sha を信じると、ブランチの先頭そのものを渡して消せてしまう。
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "未 push")
        own = git(self.parent_tree, "rev-parse", "HEAD").strip()
        kept(
            self.tidy(own),
            "未 push のコミットがある",
            "origin/i0001",
        )
        # 未 push の先頭を持つ親は、push 済みの先頭を渡してもワークツリーを消さない。
        done = self.tidy(head)
        kept(
            done,
            "未 push のコミットがある",
            f"git -C {self.root} branch -D i0001",
            "ワークツリーは、ブランチが片付けの条件を満たさないので消しません",
        )
        self.assertNotIn("ワークツリー i0001 を削除しました", done.stdout)
        # 親の識別子と別のブランチを渡しても消さない。引数の誤りも断る。
        git(self.root, "branch", "other", "main")
        sha = git(self.root, "rev-parse", "other").strip()
        refused = self.tidy(sha, "other")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("親 i0001 のブランチではない", refused.stderr)
        self.assertIn("other", self.branches())
        self.assertEqual(self.tidy("not-a-sha").returncode, 1)
        self.assertIn("i0001", self.branches())

    def test_a_worktree_whose_head_is_not_the_recorded_branch_is_not_removed(self):
        """親のワークツリーが在るのに、成果が失われないと確かめられないときは、何も消さない。"""
        self.closed_and_pushed()
        head = self.pushed_head()

        def kept(done):
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.splitlines()[-1], "record no", done.stdout)
            # 親のワークツリーとブランチだけが対象（合流済みの子は MR の先頭で別に判定する）
            self.assertNotIn("ワークツリー i0001 を削除しました", done.stdout)
            self.assertNotIn("ブランチ i0001 を削除しました", done.stdout)
            self.assertTrue(os.path.isdir(self.parent_tree))
            self.assertTrue(os.path.exists(self.record))
            self.assertIn("HEAD が記録のブランチ i0001 を指していない", done.stdout)

        git(self.parent_tree, "checkout", "--quiet", "--detach")
        kept(self.tidy(head))
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "別のブランチの成果")
        kept(self.tidy(head))
        self.assertIn("elsewhere", self.branches())
        # ブランチの参照が消えた
        git(self.parent_tree, "symbolic-ref", "HEAD", "refs/heads/i0001")
        git(self.root, "update-ref", "-d", "refs/heads/i0001")
        kept(self.tidy(head))


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


class BrokenChildrenTest(RetireHarness):
    """記録の `children` が配列でないとき（数）は、壊れた記録として何も消さずに止める。"""

    def break_children(self, value):
        held = self.read_record()
        held["children"] = value
        write(self.record, json.dumps(held))

    def test_worktree_retire_removes_nothing(self):
        self.closed_and_pushed()
        self.break_children(5)
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("children が配列ではない", done.stderr)
        self.assertTrue(os.path.isdir(self.child_tree))
        self.assertTrue(os.path.isdir(self.parent_tree))


class CaseFoldTest(CancelledChildHarness):
    """大文字小文字だけが違う名前は同じものとして扱う（Linux でも）。"""

    def test_names_that_differ_only_in_case_mean_the_same_record_and_branch(self):
        abandon = ("--cwd", self.root, "worktree", "retire", "--abandon", "I0001")
        # ワークツリーが残っている間は、別の綴りでも記録を消さない。
        refused = self.ccnavi(*abandon)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertTrue(os.path.exists(self.record))
        self.assertEqual(self.retire().returncode, 1)
        self.assertFalse(os.path.exists(self.parent_tree))
        # 別の綴りの記録のファイルも、親が合えば列挙する。
        upper = os.path.join(self.state, "retire", "I0001.json")
        os.rename(self.record, upper)
        done = self.ccnavi("sync", "retired")
        self.assertEqual(done.stdout.splitlines()[1:], ["retired I0001 self 7 i0001"])
        os.rename(upper, self.record)
        # 子のブランチは、大文字小文字だけ違う名前でも見つかる。
        done = self.retire("branch", "I0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("i0001-01-09", self.branches())
        # 子の親は、大文字小文字を見ずに比べる。
        self.add_cancelled("i0001-01-08")
        held = self.read_record()
        held["parent"] = "I0001"
        write(self.record, json.dumps(held))
        done = self.retire("branch", "i0001-01-08", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # 別の綴りの放棄は、同じ記録を消す。
        done = self.ccnavi(*abandon)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.record))
