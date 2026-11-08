"""閉じた親子のチケットの退避（`ready` が `logs/archive/` へ移す）。

- `ready` が条件を確かめてから退避し、打ち直しても通ること
- write-and-push の見分け（`ccnavi write-and-push sort`）と実行後チェックが、
  退避の削除だけを ccnavi の書き込みとして外すこと
- ボードの JSON に退避のチケットが載ること（判定には混ぜない）

退避の構成と移し方、閉じた識別子の使い回し・子の連番が退避を見ることは go の archive・approval・
closeout のテストが見る。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.inproc import same_path
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, parent_text

# write-and-push の見分けの種類（ccnavi が書いたと内容で分かる・見分けられない）。
KIND_B = "b"
KIND_D = "d"


def archive_self(root):
    """ワークスペース自身の退避の置き場。"""
    return os.path.join(root, "logs", "archive", "self")


def closed_text(name, parent="", phase=None, predecessors=()):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if not parent:
        lines += ["mr: 1", "issue: 1"]
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase or 1}"]
    if predecessors:
        lines.append(f"predecessors: [{', '.join(predecessors)}]")
    lines += [
        "human_review:",
        "  required: false",
        "  reason: t",
        f"title: {name} のタイトル",
        "rationale: r",
        "allow:",
        "  - match: Write|Edit",
        '    glob: "src/*"',
        "ccnavi_approved:",
        "  approved_at: '2026-01-01T00:00:00+09:00'",
        'started_at: ""',
        "completed_at: '2026-01-02T00:00:00+09:00'",
        'base_sha: ""',
        "---",
        "",
        "本文",
        "",
    ]
    return "\n".join(lines)


class ReadyHarness(PhaseHarness):
    """`review ready` が条件を確かめてから閉じた親子のチケットを退避する。"""

    def closable(self, before_close=None):
        """親を閉じて片付け、push 済みにする。fixture（マージリクエストの取得結果）を返す。

        `before_close` があれば、親を閉じる直前に呼ぶ。親の finish の結果は `self.parent_closed`。
        """
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        if before_close:
            before_close()
        closed = self.parent_closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("logs/archive/", closed.stdout)
        # 統合先にたまっていた過去の親（閉じた親子のチケット）
        write(os.path.join(self.approved, "done", "old.md"), closed_text("old"))
        write(os.path.join(self.approved, "done", "old-01-01.md"), closed_text("old-01-01", "old"))
        write(os.path.join(self.approved, "phases", "old", "closed.json"), "{}\n")
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def ready(self, fixture):
        return self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)

    def sorted_kinds(self, since=""):
        """write-and-push の見分け（`ccnavi write-and-push sort`）。置き場の相対パス → 種類。"""
        args = ["write-and-push", "sort", "i0001"] + ([since] if since else [])
        done = self.ccnavi(*args)
        self.assertEqual(done.returncode, 0, done.stderr)
        kinds = {}
        for line in done.stdout.splitlines()[1:]:
            kind, _, rel = line.partition(" ")
            if kind != "why":
                kinds[rel] = kind
        return kinds


class ReadyArchivesTest(ReadyHarness):
    """`review ready` が条件を確かめてから閉じた親子のチケットを退避する。"""

    def test_ready_moves_every_closed_family_and_can_be_run_again(self):
        fixture = self.closable()
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        lines = passed.stdout.splitlines()
        with open(lines[0], encoding="utf-8") as f:
            self.assertIn("logs/archive/", f.read())
        self.assertTrue(lines[1].startswith("tree "), lines)
        self.assertTrue(same_path(lines[1][len("tree ") :], self.parent_tree), lines)
        base = os.path.join(self.root, "logs", "archive", "self")
        for rel in ("done/i0001.md", "done/i0001-01-01.md", "done/old.md", "done/old-01-01.md"):
            self.assertTrue(os.path.isfile(os.path.join(base, *rel.split("/"))), rel)
            self.assertFalse(os.path.exists(os.path.join(self.approved, *rel.split("/"))), rel)
        # Draft を外したマーカーも一緒に退避される
        self.assertEqual(read_json(os.path.join(base, "phases", "i0001", "ready.json"))["mr"], 7)
        self.assertTrue(os.path.isfile(os.path.join(base, "phases", "old", "closed.json")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "phases", "i0001")))
        # git の上では削除だけ
        status = git(self.parent_tree, "status", "--porcelain").splitlines()
        self.assertTrue(status)
        self.assertTrue(all(line.startswith(" D ") for line in status), status)
        # 削除をコミットして push してから打ち直しても通る（Draft を外し損ねたとき）
        self.commit_parent("退避")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(again.stdout.splitlines()[0].endswith("review-ready-i0001.md"))
        # ボードには退避のチケットとして載り、置き場のチケットには出ない。子のワークツリーは
        # 片付けてから見る（残っていると、そこにコピーされた承認済みチケットが置き場の側に出る）
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01-01")
        git(self.root, "worktree", "remove", "--force", child)
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        archived = {a["ticket"]: a for a in board["archived"]}
        self.assertEqual(set(archived), {"i0001", "i0001-01-01", "old", "old-01-01"})
        self.assertTrue(archived["i0001"]["path"].startswith(base))
        self.assertNotIn("i0001", {t["ticket"] for t in board["tickets"]})

    def drift_after_push(self, *rels):
        """追跡済みの `rels` を push 済みの状態にしてから書き換え、未コミットの変更にする。"""
        for rel in rels:
            write(os.path.join(self.parent_tree, *rel.split("/")), "old\n")
        self.commit_parent("古いコピー")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        for rel in rels:
            write(os.path.join(self.parent_tree, *rel.split("/")), "new\n")

    def test_ready_names_the_mirror_drift_and_who_aligns_it(self):
        fixture = self.closable()
        self.drift_after_push(".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md")
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("親のワークツリーに未コミットの変更がある", refused.stderr)
        self.assertIn(".ccnavi/config/common/rules.yml", refused.stderr)
        self.assertIn("skills/process-test/SKILL.md", refused.stderr)
        self.assertIn("ccnavi-user-mirror.sh", refused.stderr)
        self.assertIn("コミットも取り消しもしない", refused.stderr)

    def test_ready_does_not_blame_the_mirror_for_other_uncommitted_changes(self):
        fixture = self.closable()
        self.drift_after_push("src/keep.py")
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("親のワークツリーに未コミットの変更がある", refused.stderr)
        self.assertNotIn("ccnavi-user-mirror.sh", refused.stderr)

    def test_ready_does_not_move_anything_while_the_parent_is_not_closable(self):
        self.family(plan=["design"])
        fixture = self.remote()
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "archive")))

    def test_write_and_push_and_the_post_check_count_only_the_archive_removals(self):
        fixture = self.closable()
        # 退避と関係の無い削除も 1 つ混ぜる（開いた親の書きかけ）
        write(os.path.join(self.approved, "stray", "stray.jsonl"), "{}\n")
        self.commit_parent("stray")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.ready(fixture).returncode, 0)
        os.remove(os.path.join(self.approved, "stray", "stray.jsonl"))
        base = archive_self(self.root)
        sorted_out = self.sorted_kinds()
        self.assertEqual(sorted_out[".ccnavi/approved/done/i0001.md"], KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/done/old-01-01.md"], KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/phases/i0001/closed.json"], KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/stray/stray.jsonl"], KIND_D)
        # 退避したコピーが違えば (b) にしない
        write(os.path.join(base, "done", "old-01-01.md"), "書き換えた\n")
        again = self.sorted_kinds()
        self.assertEqual(again[".ccnavi/approved/done/old-01-01.md"], KIND_D)
        self.assertEqual(again[".ccnavi/approved/done/i0001.md"], KIND_B)

    def test_the_post_check_does_not_report_the_archive_removals(self):
        fixture = self.closable()
        # 閉じていない親（done/ に無い）の子。退避の対象にならない
        write(os.path.join(self.approved, "done", "zzz-01-01.md"), closed_text("zzz-01-01", "zzz"))
        self.commit_parent("stray")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.approved, "done", "zzz-01-01.md")))
        os.remove(os.path.join(self.approved, "done", "zzz-01-01.md"))
        result = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        said = result.stdout + result.stderr
        # 退避と関係の無いチケットの削除は今までどおり報告する
        self.assertIn("done/zzz-01-01.md", said)
        self.assertNotIn("done/i0001.md", said)
        self.assertNotIn("done/old-01-01.md", said)
        self.assertNotIn("phases/i0001/closed.json", said)


class ReviewFindingsTest(ReadyHarness):
    """レビューで挙がった指摘の再現（直す前は失敗する）。"""

    def test_1_a_stale_doing_copy_in_a_child_tree_goes_with_the_child_worktree(self):
        """ready の後、閉じた子のワークツリーを片付ければ、古い作業中のコピーは残らない。"""
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.commit_parent("退避")
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01-01")
        self.assertTrue(
            os.path.isfile(os.path.join(child, ".ccnavi", "approved", "doing", "i0001-01-01.md"))
        )
        git(self.root, "worktree", "remove", "--force", child)
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        opened = {
            t["ticket"] for t in board["tickets"] if t["copy"]["status"] in ("open", "review")
        }
        self.assertNotIn("i0001-01-01", opened)
        self.assertNotIn("i0001", opened)

    def test_3_a_stopped_move_is_finished_by_the_next_ready(self):
        fixture = self.closable()
        base = os.path.join(self.root, "logs", "archive", "self")
        write(os.path.join(base, "done", "old-01-01.md"), "違う中身\n")
        stopped = self.ready(fixture)
        self.assertNotEqual(stopped.returncode, 0)
        os.remove(os.path.join(base, "done", "old-01-01.md"))
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        for rel in ("done/i0001.md", "done/old.md", "done/old-01-01.md", "phases/old/closed.json"):
            self.assertFalse(os.path.exists(os.path.join(self.approved, *rel.split("/"))), rel)
            self.assertTrue(os.path.isfile(os.path.join(base, *rel.split("/"))), rel)

    def test_4_committed_removals_are_carried_by_write_and_push(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        kinds = self.sorted_kinds()
        self.assertEqual(kinds[".ccnavi/approved/done/i0001.md"], KIND_B)
        self.assertEqual(kinds[".ccnavi/approved/done/i0001-01-01.md"], KIND_B)
        # コミットしたあとの未送信の確かめ（since）でも (b)
        self.commit_parent("退避")
        kinds = self.sorted_kinds(git(self.parent_tree, "rev-parse", "HEAD~1").strip())
        self.assertEqual(kinds[".ccnavi/approved/done/i0001.md"], KIND_B)

    def test_5_a_removal_outside_ready_is_not_carried(self):
        self.family(plan=["design"])
        write(os.path.join(self.approved, "done", "old.md"), closed_text("old"))
        self.commit_parent("old")
        # 退避に同じ中身を置き、ready を経ずに消す
        base = archive_self(self.root)
        write(os.path.join(base, "done", "old.md"), closed_text("old"))
        os.remove(os.path.join(self.approved, "done", "old.md"))
        kinds = self.sorted_kinds()
        self.assertNotEqual(kinds[".ccnavi/approved/done/old.md"], KIND_B)

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_6_a_linked_logs_dir_is_not_trusted_by_write_and_push(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        logs = os.path.join(self.root, "logs")
        moved = os.path.join(self.root, "elsewhere")
        os.rename(logs, moved)
        os.symlink(moved, logs)
        kinds = self.sorted_kinds()
        self.assertNotIn(KIND_B, set(kinds.values()))


class SecondReviewTest(ReadyHarness):
    """2 回目のレビューの指摘の再現（直す前は失敗する）。"""

    def test_2_a_mark_of_a_finished_ready_does_not_carry_a_later_removal(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.commit_parent("退避")
        # 退避を済ませた後に、同じ中身のチケットがツリーに戻り（取り込みなど）、ready の外で消えた
        git(self.parent_tree, "checkout", "HEAD~1", "--", ".ccnavi/approved/done/old.md")
        git(self.parent_tree, "commit", "--quiet", "-m", "戻った")
        os.remove(os.path.join(self.approved, "done", "old.md"))
        self.assertNotEqual(self.sorted_kinds()[".ccnavi/approved/done/old.md"], KIND_B)

    def test_3_ready_again_needs_the_merge_request_to_be_a_draft(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.commit_parent("退避")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # Draft が外れていれば ready は終わっている。打ち直しとして通さない
        undrafted = os.path.join(self.root, "fixture-undrafted.json")
        write(
            undrafted,
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": False}}),
        )
        self.assertNotEqual(self.ready(undrafted).returncode, 0)
        # 分からなければ（draft の欄が無い）通さない
        unknown = os.path.join(self.root, "fixture-unknown.json")
        write(unknown, json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}))
        self.assertNotEqual(self.ready(unknown).returncode, 0)
        # ready のマーカーが無ければ（今のツリーで退避を始めていなければ）打ち直しとして通さない
        os.remove(os.path.join(self.root, "logs", "archive", "self", "ready", "i0001.json"))
        self.assertNotEqual(self.ready(fixture).returncode, 0)

    def test_9_the_reason_of_an_early_close_is_read_from_the_tree_too(self):
        fixture = self.closable()
        write(
            os.path.join(self.approved, "phases", "i0001", "close-early.json"),
            json.dumps({"reason": "ここまでで十分"}),
        )
        self.commit_parent("早めに閉じた")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # 退避の行き先をディレクトリにして、close-early.json の手前で止める
        base = os.path.join(self.root, "logs", "archive", "self")
        blocker = os.path.join(base, "phases", "i0001", "close-early.json")
        os.makedirs(blocker)
        self.assertNotEqual(self.ready(fixture).returncode, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(self.approved, "phases", "i0001", "close-early.json"))
        )
        os.rmdir(blocker)
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        with open(again.stdout.splitlines()[0], encoding="utf-8") as f:
            self.assertIn("ここまでで十分", f.read())

    def test_11_lint_sees_an_archived_parent_tree_off_its_branch(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.commit_parent("退避")
        git(self.parent_tree, "checkout", "--quiet", "-b", "elsewhere")
        linted = self.ccnavi("--lint")
        self.assertIn("i0001 のワークツリーが elsewhere の上にある", linted.stdout + linted.stderr)


if __name__ == "__main__":
    unittest.main()
