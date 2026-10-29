"""親を閉じたときのワークツリーとブランチの片付け（`tickets/lifecycle/retire.py`）。

見るのは 3 つ。

1. `worktree retire`（ready が Draft を外した後）: 子のワークツリーを消し、合流した子のブランチは
   先頭が `merged_sha` と同じで、その sha が push 済みの親に含まれるときだけ消す。取り消した子は
   確認なしでは消さない。未コミットの変更や、git が無視しているファイルがあれば消さない
2. `worktree branch <名前> [confirmed]`: 記録した子のブランチだけを、条件を確かめ直して消す
3. `sync tidy`（MR がマージされたと分かった後）: 手元の先頭が MR の先頭に含まれるときだけ
   親のブランチを消す
"""

from __future__ import annotations

import json
import os

from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import retire
from tests.ticket.test_archive import ReadyHarness
from tests.ticket.test_ticket import git, write

CHILD = "i0001-01-01"


class RetireHarness(ReadyHarness):
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


class RetireAfterReadyTest(RetireHarness):
    def test_ready_notes_the_children_before_the_tickets_move_to_the_archive(self):
        self.closed_and_pushed()
        held = self.read_record()
        self.assertEqual(held["branch"], "i0001")
        merged = git(self.parent_tree, "rev-parse", CHILD).strip()
        self.assertEqual(
            [(c["ticket"], c["kind"], c["merged_sha"]) for c in held["children"]],
            [(CHILD, "merged", merged)],
        )

    def test_retire_removes_the_worktrees_and_the_merged_child_branch(self):
        self.closed_and_pushed()
        self.assertTrue(os.path.isdir(self.child_tree))
        done = self.retire()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"ワークツリー {CHILD} を削除しました", done.stdout)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertFalse(os.path.exists(self.child_tree))
        self.assertFalse(os.path.exists(self.parent_tree))
        # 子のブランチは消え、親のブランチは MR のマージまで残る
        self.assertEqual(self.branches(), ["i0001", "main"])
        # 打ち直しても何も起きない
        again = self.retire()
        self.assertEqual((again.returncode, again.stdout.count("削除しました")), (0, 0))

    def test_a_child_branch_that_moved_after_the_merge_is_kept(self):
        self.closed_and_pushed()
        git(self.child_tree, "commit", "--quiet", "--allow-empty", "-m", "合流のあとの変更")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("合流した先頭", done.stdout)
        self.assertIn(f"git -C {self.root} branch -D {CHILD}", done.stdout)
        self.assertIn(CHILD, self.branches())
        self.assertFalse(os.path.exists(self.child_tree))

    def test_a_merged_head_that_the_pushed_parent_lacks_keeps_the_branch(self):
        self.closed_and_pushed()
        git(self.root, "update-ref", "refs/remotes/origin/i0001", "main")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("push 済みの親に含まれない", done.stdout)
        self.assertIn(CHILD, self.branches())

    def test_a_dirty_child_worktree_and_its_branch_stay(self):
        self.closed_and_pushed()
        write(os.path.join(self.child_tree, "memo.txt"), "書きかけ\n")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertIn("memo.txt", done.stdout)
        self.assertTrue(os.path.isdir(self.child_tree))
        self.assertIn("チェックアウトしている", done.stdout)
        self.assertIn(CHILD, self.branches())

    def test_ignored_files_keep_the_worktree_and_say_why(self):
        self.closed_and_pushed()
        exclude = git(self.child_tree, "rev-parse", "--git-path", "info/exclude").strip()
        exclude = exclude if os.path.isabs(exclude) else os.path.join(self.child_tree, exclude)
        write(exclude, ".env\n")
        write(os.path.join(self.child_tree, ".env"), "SECRET=1\n")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("git が無視しているファイル", done.stdout)
        self.assertIn(".env", done.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.child_tree, ".env")))
        self.assertIn(CHILD, self.branches())


class CancelledChildTest(RetireHarness):
    """取り消した子のブランチは、確認なしでは消さない。"""

    def setUp(self):
        super().setUp()
        self.closed_and_pushed()
        git(self.root, "branch", "i0001-01-09", "main")
        held = self.read_record()
        held["children"].append(
            {"ticket": "i0001-01-09", "kind": "cancelled", "merged_sha": "", "project": ""}
        )
        write(self.record, json.dumps(held))
        # ready が退避へ移した、取り消した子のチケット。`worktree branch` は記録の子が実際に
        # この親の子かを、ここ（か承認済みの置き場）で確かめ直す。
        self.archived("i0001-01-09", "i0001")

    def archived(self, name, parent):
        path = os.path.join(self.root, "logs", "archive", "self", "done", f"{name}.md")
        lines = ["---", "version: 1", f"ticket: {name}", f"parent: {parent}", "phase: 1"]
        lines += ["title: t", "cancelled_at: 2026-10-01T00:00:00+09:00", "---", ""]
        write(path, "\n".join(lines) + "\n")

    def test_retire_lists_it_and_keeps_it(self):
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("取り消した子のブランチ", done.stdout)
        self.assertIn("ユーザに見せる", done.stdout)
        self.assertIn("- i0001-01-09", done.stdout)
        # 確認はユーザが自分で打つコマンドとしてだけ取れる（エージェントからは hook が止める）。
        self.assertIn("! を付けて打つ", done.stdout)
        self.assertRegex(
            done.stdout, r"! sh \S*ccnavi-user-clean-branch\.sh i0001-01-09 --confirmed"
        )
        self.assertIn("i0001-01-09", self.branches())
        # 合流した子と親のワークツリーは片付く
        self.assertNotIn(CHILD, self.branches())
        self.assertFalse(os.path.exists(self.parent_tree))

    def test_branch_without_confirmation_does_not_delete(self):
        refused = self.retire("branch", "i0001-01-09")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("消すのはユーザ", refused.stderr)
        self.assertIn("'! sh ", refused.stderr)
        self.assertIn("i0001-01-09", self.branches())

    def test_branch_with_confirmation_deletes(self):
        done = self.retire("branch", "i0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ブランチ i0001-01-09 を削除しました", done.stdout)
        self.assertNotIn("i0001-01-09", self.branches())

    def test_a_record_that_names_a_branch_outside_the_family_does_not_delete(self):
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
        # チケットの見つからない子も消さない。
        git(self.root, "branch", "i0001-01-08", "main")
        held["children"].append(
            {"ticket": "i0001-01-08", "kind": "cancelled", "merged_sha": "", "project": ""}
        )
        write(self.record, json.dumps(held))
        refused = self.retire("branch", "i0001-01-08", "confirmed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("i0001-01-08", self.branches())

    def test_a_branch_that_was_not_recorded_is_never_deleted(self):
        git(self.root, "branch", "other", "main")
        refused = self.retire("branch", "other", "confirmed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("記録したブランチではない", refused.stderr)
        self.assertIn("other", self.branches())


class TidyAfterMergeTest(RetireHarness):
    """MR がマージされたと分かった後の片付け（親のブランチと片付けの記録）。"""

    def tidy(self, sha):
        return self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", "i0001", sha)

    def pushed_head(self):
        return git(self.root, "rev-parse", "refs/remotes/origin/i0001").strip()

    def test_the_parent_branch_the_worktree_and_the_note_are_removed(self):
        self.closed_and_pushed()
        done = self.tidy(self.pushed_head())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertIn("ブランチ i0001 を削除しました", done.stdout)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "record yes")
        self.assertEqual(self.branches(), ["main"])
        self.assertFalse(os.path.exists(self.record))

    def test_an_unpushed_commit_keeps_the_branch(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "未 push")
        done = self.tidy(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("未 push のコミットがある", done.stdout)
        self.assertIn(f"git -C {self.root} branch -D i0001", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")
        self.assertTrue(os.path.exists(self.record))

    def test_a_branch_that_fails_the_check_keeps_the_worktree(self):
        # 同じ名前のブランチを作り直して使っている親（MR の先頭に含まれない先頭）のワークツリーは、
        # ブランチの確認を通るまで消さない。
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "作り直した親")
        done = self.tidy(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(
            "ワークツリーは、ブランチが片付けの条件を満たさないので消しません", done.stdout
        )
        self.assertNotIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertTrue(os.path.isdir(self.parent_tree))
        self.assertIn("i0001", self.branches())

    def test_a_branch_that_passes_the_check_loses_the_worktree_first(self):
        self.closed_and_pushed()
        done = self.tidy(self.pushed_head())
        lines = done.stdout.splitlines()
        worktree = next(i for i, line in enumerate(lines) if "ワークツリー i0001 を" in line)
        branch = next(i for i, line in enumerate(lines) if "ブランチ i0001 を削除" in line)
        self.assertLess(worktree, branch)

    def test_uncommitted_changes_keep_the_worktree_and_the_branch(self):
        self.closed_and_pushed()
        write(os.path.join(self.parent_tree, "memo.txt"), "書きかけ\n")
        done = self.tidy(self.pushed_head())
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertTrue(os.path.isdir(self.parent_tree))
        self.assertIn("チェックアウトしている", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_a_head_that_is_not_here_is_not_guessed(self):
        self.closed_and_pushed()
        done = self.tidy("0123456789abcdef0123456789abcdef01234567")
        self.assertIn("手元に無く、含まれるか確かめられない", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_the_head_of_the_branch_itself_does_not_count_as_merged(self):
        # 呼び手が渡す sha を信じると、未 push のブランチの先頭そのものを渡せば消せる。
        self.closed_and_pushed()
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "未 push")
        own = git(self.parent_tree, "rev-parse", "HEAD").strip()
        done = self.tidy(own)
        self.assertIn("未 push のコミットがある", done.stdout)
        self.assertIn("origin/i0001", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_a_branch_without_a_pushed_head_is_kept(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.root, "update-ref", "-d", "refs/remotes/origin/i0001")
        done = self.tidy(head)
        self.assertIn("手元に無く、未 push のコミットが無いか確かめられない", done.stdout)
        self.assertIn("i0001", self.branches())

    def test_only_the_branch_of_the_parent_is_tidied(self):
        # 親の識別子と別のブランチを渡しても消さない。
        self.closed_and_pushed()
        git(self.root, "branch", "other", "main")
        sha = git(self.root, "rev-parse", "other").strip()
        refused = self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", "other", sha)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("親 i0001 のブランチではない", refused.stderr)
        self.assertIn("other", self.branches())

    def test_a_bad_argument_is_refused(self):
        self.closed_and_pushed()
        refused = self.tidy("not-a-sha")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("i0001", self.branches())


class NoteTest(RetireHarness):
    """`ready` が書く片付けの記録（`retire/<親>.json`）。"""

    def test_ready_notes_the_mr_number(self):
        self.closed_and_pushed()
        self.assertEqual(self.read_record()["mr"], 7)

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

    def test_a_missing_state_place_is_a_failure_reason(self):
        conf, _ = settings.load(self.root)
        conf.state = ""
        reason = retire.note(self.root, conf, "i0001", "", "", 7)
        self.assertIn("state の置き場が空", reason)

    def test_an_empty_new_mr_keeps_the_previous_one(self):
        self.closed_and_pushed()
        conf, _ = settings.load(self.root)
        self.assertEqual(retire.note(self.root, conf, "i0001", "", self.parent_tree, None), "")
        self.assertEqual(self.read_record()["mr"], 7)
        self.assertEqual(retire.note(self.root, conf, "i0001", "", self.parent_tree, 9), "")
        self.assertEqual(self.read_record()["mr"], 9)
        self.assertEqual(
            [c["ticket"] for c in self.read_record()["children"]], [CHILD], "子の一覧は保つ"
        )


class RetiredEntriesTest(RetireHarness):
    """`ccnavi sync retired`（片付けの列挙元）。"""

    def retired(self):
        done = self.ccnavi("sync", "retired")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], "retired 1")
        return lines[1:]

    def put(self, name, text):
        write(os.path.join(self.state, "retire", name), text)

    def test_nothing_is_just_the_head(self):
        self.assertEqual(self.retired(), [])

    def test_a_noted_parent_is_listed_with_mr_and_branch(self):
        self.closed_and_pushed()
        self.assertEqual(self.retired(), ["retired i0001 self 7 i0001"])

    def test_a_project_parent_is_listed_by_its_name(self):
        self.put(
            "pj0001.json",
            json.dumps({"parent": "pj0001", "project": "app", "branch": "pj0001", "mr": 3}),
        )
        self.assertEqual(self.retired(), ["retired pj0001 app 3 pj0001"])

    def test_an_empty_branch_is_a_dash(self):
        self.put("i0002.json", json.dumps({"parent": "i0002", "project": "", "mr": 5}))
        self.assertEqual(self.retired(), ["retired i0002 self 5 -"])

    def test_a_record_without_mr_is_incomplete(self):
        self.put("i0002.json", json.dumps({"parent": "i0002", "project": "", "branch": "i0002"}))
        (line,) = self.retired()
        self.assertTrue(line.startswith("incomplete i0002 "), line)

    def test_a_broken_record_is_incomplete(self):
        self.put("i0003.json", "{broken")
        (line,) = self.retired()
        self.assertTrue(line.startswith("incomplete i0003 "), line)
        self.assertEqual(line.count("\n"), 0)

    def test_a_record_whose_parent_differs_from_its_name_is_skipped(self):
        self.put("i0004.json", json.dumps({"parent": "i0009", "project": "", "mr": 5}))
        self.assertEqual(self.retired(), [])

    def test_other_files_are_not_listed(self):
        self.put("i0005.json.tmp.1", json.dumps({"parent": "i0005", "mr": 5}))
        self.put("note.txt", "x")
        self.assertEqual(self.retired(), [])

    def test_works_with_ticket_control_disabled(self):
        self.closed_and_pushed()
        done = self.ccnavi("--ticket-control", "disable", "sync", "retired")
        self.assertEqual(done.stdout.splitlines()[1:], ["retired i0001 self 7 i0001"])


class AbandonTest(RetireHarness):
    """`ccnavi worktree retire --abandon <親>`。"""

    def abandon(self, name="i0001"):
        return self.ccnavi("--cwd", self.root, "worktree", "retire", "--abandon", name)

    def test_a_parent_whose_worktree_stays_is_refused(self):
        self.closed_and_pushed()
        refused = self.abandon()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("ワークツリー", refused.stderr)
        self.assertTrue(os.path.exists(self.record))

    def test_the_record_goes_once_the_worktree_is_gone(self):
        self.closed_and_pushed()
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

    def test_a_bad_name_is_refused(self):
        refused = self.abandon("../x")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)

    def test_it_is_only_for_retire(self):
        refused = self.ccnavi("--cwd", self.root, "worktree", "drop", "--abandon", "i0001")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)


class UnverifiableParentTest(RetireHarness):
    """親のワークツリーが在るのに、成果が失われないと確かめられないときは、何も消さない。"""

    def tidy(self, sha, branch="i0001"):
        return self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", branch, sha)

    def pushed_head(self):
        return git(self.root, "rev-parse", "refs/remotes/origin/i0001").strip()

    def assert_nothing_removed(self, done):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[-1], "record no", done.stdout)
        # 親のワークツリーとブランチだけが対象（合流済みの子は MR の先頭で別に判定する）
        self.assertNotIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertNotIn("ブランチ i0001 を削除しました", done.stdout)
        self.assertTrue(os.path.isdir(self.parent_tree))
        self.assertTrue(os.path.exists(self.record))

    def test_a_worktree_whose_branch_ref_is_gone_is_not_removed(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.root, "update-ref", "-d", "refs/heads/i0001")
        done = self.tidy(head)
        self.assert_nothing_removed(done)
        self.assertIn("HEAD が記録のブランチ i0001 を指していない", done.stdout)

    def test_a_head_on_another_branch_than_the_recorded_one_is_not_removed(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "別のブランチの成果")
        done = self.tidy(head)
        self.assert_nothing_removed(done)
        self.assertIn("HEAD が記録のブランチ i0001 を指していない", done.stdout)
        self.assertIn("elsewhere", git(self.root, "branch", "--format=%(refname:short)").split())

    def test_a_detached_head_is_not_removed(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.parent_tree, "checkout", "--quiet", "--detach")
        done = self.tidy(head)
        self.assert_nothing_removed(done)
        self.assertIn("HEAD が記録のブランチ i0001 を指していない", done.stdout)


class BrokenChildrenTest(RetireHarness):
    """記録の `children` が配列でないとき（null・数）は、落ちずに、壊れた記録として扱う。"""

    def break_children(self, value):
        held = self.read_record()
        held["children"] = value
        write(self.record, json.dumps(held))

    def test_sync_tidy_does_not_crash_and_keeps_the_record(self):
        self.closed_and_pushed()
        self.break_children(None)
        head = git(self.root, "rev-parse", "refs/remotes/origin/i0001").strip()
        done = self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", "i0001", head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("片付けの記録を読めない", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "record no")
        self.assertTrue(os.path.exists(self.record))

    def test_sync_retired_names_it_as_incomplete(self):
        self.closed_and_pushed()
        self.break_children(None)
        done = self.ccnavi("sync", "retired")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        (line,) = done.stdout.splitlines()[1:]
        self.assertTrue(line.startswith("incomplete i0001 "), line)
        self.assertIn("children が配列ではない", line)

    def test_worktree_retire_removes_nothing(self):
        self.closed_and_pushed()
        self.break_children(5)
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("children が配列ではない", done.stderr)
        self.assertTrue(os.path.isdir(self.child_tree))
        self.assertTrue(os.path.isdir(self.parent_tree))

    def test_ready_does_not_overwrite_it(self):
        fixture = self.closable()
        write(
            self.record,
            json.dumps({"parent": "i0001", "project": "", "children": None, "mr": 7}),
        )
        done = self.ready(fixture)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("上書きしない", done.stderr)
        self.assertIn("children が配列ではない", done.stderr)


class BrokenSiblingRecordTest(CancelledChildTest):
    def test_a_broken_record_of_another_parent_does_not_stop_the_branch_tidy(self):
        write(
            os.path.join(self.state, "retire", "i0000.json"),
            json.dumps({"parent": "i0000", "project": "", "children": None}),
        )
        write(os.path.join(self.state, "retire", "i0009.json"), "{broken")
        done = self.retire("branch", "i0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertNotIn("i0001-01-09", self.branches())


class CaseFoldTest(CancelledChildTest):
    """大文字小文字だけが違う名前は同じものとして扱う（Linux でも）。"""

    def conf(self):
        conf, _ = settings.load(self.root)
        return conf

    def test_the_record_is_found_by_a_name_that_differs_only_in_case(self):
        self.assertEqual(retire.record_path(self.conf(), "I0001"), self.record)
        # 無いときは与えた名前のまま組む
        self.assertTrue(retire.record_path(self.conf(), "I0777").endswith("I0777.json"))

    def test_abandon_with_another_spelling_keeps_the_record_while_the_worktree_stays(self):
        refused = self.ccnavi("--cwd", self.root, "worktree", "retire", "--abandon", "I0001")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertTrue(os.path.exists(self.record))

    def test_abandon_with_another_spelling_removes_the_same_record(self):
        self.assertEqual(self.retire().returncode, 1)
        self.assertFalse(os.path.exists(self.parent_tree))
        done = self.ccnavi("--cwd", self.root, "worktree", "retire", "--abandon", "I0001")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.record))

    def test_a_record_file_in_another_spelling_is_listed_when_its_parent_matches(self):
        os.rename(self.record, os.path.join(self.state, "retire", "I0001.json"))
        done = self.ccnavi("sync", "retired")
        self.assertEqual(done.stdout.splitlines()[1:], ["retired I0001 self 7 i0001"])

    def test_a_child_branch_is_found_by_a_name_that_differs_only_in_case(self):
        done = self.retire("branch", "I0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("i0001-01-09", self.branches())

    def test_the_parent_of_a_child_is_compared_without_case(self):
        held = self.read_record()
        held["parent"] = "I0001"
        write(self.record, json.dumps(held))
        done = self.retire("branch", "i0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_archived_mr_is_read_from_a_copy_in_another_spelling(self):
        from ccnavi.entry import sh_ask

        path = os.path.join(self.root, "logs", "archive", "self", "done", "I0042.md")
        write(path, "---\nversion: 1\nticket: I0042\nmr: 42\n---\n")
        self.assertEqual(sh_ask._archived_mr(self.root, "", "i0042"), "42")
        self.assertEqual(sh_ask._archived_mr(self.root, "", "i0043"), "")
