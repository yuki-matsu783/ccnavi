"""閉じた親子のチケットの退避（`ready` が `logs/archive/` へ移す）。

- 退避の構成と移し方（tickets/model/archive.py）
- `ready` が条件を確かめてから退避し、打ち直しても通ること
- write-and-push の見分け（`write_and_push.classify_all`）
と実行後チェック（`post_findings._script_writes`）が、
  退避の削除だけを ccnavi の書き込みとして外すこと
- 閉じた識別子の使い回し・子の連番・先行を引く対応表が退避を見ること
- ボードの JSON に退避のチケットが載ること（判定には混ぜない）
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

from ccnavi.infra import pathcase, settings
from ccnavi.tickets.approval import checks as approval_checks
from ccnavi.tickets.approval import ops as approval_ops
from ccnavi.tickets.approval import write_and_push
from ccnavi.tickets.lifecycle import treegate
from ccnavi.tickets.model import archive
from ccnavi.tickets.model import ticket as ticket_mod
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, parent_text


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


class ArchiveTreeHarness(unittest.TestCase):
    """tickets/model/archive.py の構成と移し方。git は使わない。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-archive-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.approved = os.path.join(self.root, "tree", ".ccnavi", "approved")
        for name, parent in (
            ("i0001", ""),
            ("i0001-01-01", "i0001"),
            ("old", ""),
            ("old-01-01", "old"),
            ("open-01-01", "open"),
        ):
            write(os.path.join(self.approved, "done", f"{name}.md"), closed_text(name, parent))
        write(os.path.join(self.approved, "doing", "open.md"), closed_text("open"))
        write(os.path.join(self.approved, "phases", "i0001", "1.reviewed.json"), "{}")
        write(os.path.join(self.approved, "phases", "i0001", "i0001-01-01.risk.json"), "{}")
        write(os.path.join(self.approved, "phases", "open", "1.pending.json"), "{}")
        write(os.path.join(self.approved, "flows", "i0001-01-01.yml"), "steps: []\n")
        write(os.path.join(self.approved, "flows", "open-01-01.yml"), "steps: []\n")


class ArchiveModuleTest(ArchiveTreeHarness):
    """tickets/model/archive.py の構成と移し方。git は使わない。"""

    def test_plan_takes_only_closed_families(self):
        self.assertEqual(archive.closed_parents(self.approved), ["i0001", "old"])
        todo = archive.plan(self.approved, archive.closed_parents(self.approved))
        self.assertEqual(todo.parents, ["i0001", "old"])
        # 子のチケットが先、親のチケットが最後（止まったときに親が done/ に残る）
        self.assertEqual(todo.tickets, ["i0001-01-01", "old-01-01", "i0001", "old"])
        self.assertEqual(todo.files[-1], "done/old.md")
        self.assertIn("phases/i0001/1.reviewed.json", todo.files)
        self.assertIn("flows/i0001-01-01.yml", todo.files)
        # 開いた親（doing/ に在る）の子・フロー・マーカーは移さない
        for rel in todo.files:
            self.assertNotIn("open", rel)

    def test_move_copies_then_removes(self):
        todo = archive.plan(self.approved, ["i0001"])
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertEqual(failed, "")
        self.assertEqual(moved, todo.files)
        base = archive.base_dir(self.root, "")
        self.assertEqual(base, os.path.join(self.root, "logs", "archive", "self"))
        for rel in todo.files:
            self.assertFalse(os.path.exists(os.path.join(self.approved, *rel.split("/"))), rel)
            self.assertTrue(os.path.isfile(os.path.join(base, *rel.split("/"))), rel)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "phases", "i0001")))
        # 退避から引ける
        self.assertEqual(archive.ids(self.root, ""), {"i0001", "i0001-01-01"})
        self.assertEqual(archive.ids(self.root), {"i0001", "i0001-01-01"})
        self.assertEqual(archive.archived_parent(self.root, "", "i0001").ticket, "i0001")
        self.assertIsNone(archive.archived_parent(self.root, "", "i0001-01-01"))
        self.assertEqual([t.state for t in archive.find(self.root, "i0001-01-01")], ["done"])

    def test_move_again_with_the_same_content_only_removes(self):
        """前の回の残り（同じ中身）が退避に在れば、コピーせずに元だけ消す（打ち直し）。"""
        todo = archive.plan(self.approved, ["old"])
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "old.md"), closed_text("old"))
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertEqual(failed, "")
        self.assertIn("done/old.md", moved)

    def test_move_refuses_to_overwrite_a_different_ticket(self):
        todo = archive.plan(self.approved, ["old"])
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "old.md"), closed_text("old") + "違う\n")
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertIn("違う中身", failed)
        self.assertNotIn("done/old.md", moved)
        self.assertTrue(os.path.isfile(os.path.join(self.approved, "done", "old.md")))

    def test_move_again_replaces_a_different_mark(self):
        """write-and-push が push できずに戻した後の打ち直しでは、マーカーだけが前の回と違う。

        マーカーは置き換えて進む。
        """
        todo = archive.plan(self.approved, ["i0001"])
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "phases", "i0001", "1.reviewed.json"), '{"at": "前"}')
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertEqual(failed, "")
        self.assertIn("phases/i0001/1.reviewed.json", moved)
        with open(os.path.join(base, "phases", "i0001", "1.reviewed.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{}")

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_a_linked_archive_place_is_not_followed(self):
        elsewhere = tempfile.mkdtemp(prefix="ccnavi-archive-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "logs"))
        os.symlink(elsewhere, os.path.join(self.root, "logs", "archive"))
        todo = archive.plan(self.approved, ["old"])
        _, failed = archive.move(self.root, "", self.approved, todo)
        self.assertIn("シンボリックリンク", failed)
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertEqual(archive.ids(self.root), set())


class ChecksHarness(unittest.TestCase):
    """閉じた識別子の使い回し・子の連番・先行を引く対応表が退避を見る。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-archive-checks-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "i0001.md"), closed_text("i0001"))
        write(os.path.join(base, "done", "i0001-01-03.md"), closed_text("i0001-01-03", "i0001"))
        self.conf, _ = settings.load(self.root)


class ArchiveChecksTest(ChecksHarness):
    """閉じた識別子の使い回し・子の連番・先行を引く対応表が退避を見る。"""

    def test_the_next_child_skips_the_archived_numbers(self):
        self.assertEqual(
            approval_ops.next_child_id(self.conf, self.root, "i0001", 1), "i0001-01-04"
        )

    def follower(self):
        text = closed_text("i0002-01-01", "i0002", predecessors=["i0001-01-03"])
        t, problems = ticket_mod.parse(text)
        self.assertIsNotNone(t, problems)
        self.assertEqual(t.predecessors, ["i0001-01-03"])
        return t

    def states(self, root, t):
        """承認・lint と同じ引き方（対応表 → 親のブランチで読み直す）で、先行の状態。"""
        conf, _ = settings.load(root)
        pool = approval_checks.predecessor_pool_of([t], [], [], [])
        approval_checks.align_imported(conf, root, pool)
        return [p.state for p in approval_checks.predecessor_states(t, pool)]

    def other_pc(self):
        """退避の無い別の PC（同じリポジトリの、別のワークスペース）。"""
        root = tempfile.mkdtemp(prefix="ccnavi-archive-other-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, ".claude"))
        git(root, "init", "--quiet", "-b", "main")
        return root

    def test_a_predecessor_in_the_archive_is_not_read_and_pcs_agree(self):
        """手元の退避にだけある先行は閉じたと読まない。退避の無い PC と同じ答えになる。

        閉じて片付いた親子のチケットの先行は、どの PC でも同じく「どの置き場にも無い」になる。
        """
        t = self.follower()
        self.assertEqual(self.states(self.root, t), [approval_checks.PRED_MISSING])
        self.assertEqual(self.states(self.other_pc(), t), [approval_checks.PRED_MISSING])


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

    def changed(self):
        status = git(self.parent_tree, "status", "--porcelain", "--no-renames").splitlines()
        return sorted(line[3:] for line in status)

    def sorted_kinds(self, changed=None, since=""):
        places = (".ccnavi/approved", "wip/proposals/review")
        return {
            rel: kind
            for kind, rel, _ in write_and_push.classify_all(
                self.parent_tree,
                places,
                "i0001",
                self.changed() if changed is None else changed,
                since,
                (self.root, ""),
            )
        }


class ReadyArchivesTest(ReadyHarness):
    """`review ready` が条件を確かめてから閉じた親子のチケットを退避する。"""

    def test_ready_moves_every_closed_family_and_can_be_run_again(self):
        fixture = self.closable()
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        lines = passed.stdout.splitlines()
        with open(lines[0], encoding="utf-8") as f:
            self.assertIn("logs/archive/", f.read())
        self.assertEqual(pathcase.fold(lines[1]), pathcase.fold(f"tree {self.parent_tree}"))
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

    def test_mirror_drift_text_counts_other_changes_and_folds_case_and_long_lists(self):
        conf = settings.Settings()
        drift = [f"skills/process-test/references/common/r{n}.md" for n in range(11)]
        paths = ["SKILLS/process-test/SKILL.md", "src/keep.py"] + drift
        text = treegate.dirty_refusal(treegate.READY, "", conf, paths).line
        self.assertIn("ずれ以外の変更が 1 件ある", text)
        self.assertIn("ほか 2 件", text)
        self.assertIn("--yes --expect <値>", text)
        self.assertNotIn("ずれ以外", treegate.dirty_refusal(treegate.READY, "", conf, drift).line)

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
        status = git(self.parent_tree, "status", "--porcelain", "--no-renames").splitlines()
        changed = sorted(line[3:] for line in status)
        places = (".ccnavi/approved", "wip/proposals/review")
        base = archive.base_dir(self.root, "")
        sorted_out = {
            rel: kind
            for kind, rel, _ in write_and_push.classify_all(
                self.parent_tree, places, "i0001", changed, "", (self.root, "")
            )
        }
        self.assertEqual(sorted_out[".ccnavi/approved/done/i0001.md"], write_and_push.KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/done/old-01-01.md"], write_and_push.KIND_B)
        self.assertEqual(
            sorted_out[".ccnavi/approved/phases/i0001/closed.json"], write_and_push.KIND_B
        )
        self.assertEqual(sorted_out[".ccnavi/approved/stray/stray.jsonl"], write_and_push.KIND_D)
        # 退避の置き場を渡さなければ、前のとおり ccnavi の書き込みとは見分けない（止める）
        plain = write_and_push.classify_all(self.parent_tree, places, "i0001", changed, "", None)
        self.assertNotIn(write_and_push.KIND_B, {kind for kind, _, _ in plain})
        # 退避したコピーが違えば (b) にしない
        write(os.path.join(base, "done", "old-01-01.md"), "書き換えた\n")
        again = {
            rel: kind
            for kind, rel, _ in write_and_push.classify_all(
                self.parent_tree, places, "i0001", changed, "", (self.root, "")
            )
        }
        self.assertEqual(again[".ccnavi/approved/done/old-01-01.md"], write_and_push.KIND_D)
        self.assertEqual(again[".ccnavi/approved/done/i0001.md"], write_and_push.KIND_B)

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
        self.assertEqual(kinds[".ccnavi/approved/done/i0001.md"], write_and_push.KIND_B)
        self.assertEqual(kinds[".ccnavi/approved/done/i0001-01-01.md"], write_and_push.KIND_B)
        # コミットしたあとの未送信の確かめ（since）でも (b)
        self.commit_parent("退避")
        changed = sorted(
            p
            for p in git(
                self.parent_tree, "diff", "--name-only", "--no-renames", "HEAD~1", "HEAD"
            ).splitlines()
            if p
        )
        kinds = self.sorted_kinds(changed, "HEAD~1")
        self.assertEqual(kinds[".ccnavi/approved/done/i0001.md"], write_and_push.KIND_B)

    def test_5_a_removal_outside_ready_is_not_carried(self):
        self.family(plan=["design"])
        write(os.path.join(self.approved, "done", "old.md"), closed_text("old"))
        self.commit_parent("old")
        # 退避に同じ中身を置き、ready を経ずに消す
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "old.md"), closed_text("old"))
        os.remove(os.path.join(self.approved, "done", "old.md"))
        kinds = self.sorted_kinds()
        self.assertNotEqual(kinds[".ccnavi/approved/done/old.md"], write_and_push.KIND_B)

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_6_a_linked_logs_dir_is_not_trusted_by_write_and_push(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        logs = os.path.join(self.root, "logs")
        moved = os.path.join(self.root, "elsewhere")
        os.rename(logs, moved)
        os.symlink(moved, logs)
        kinds = self.sorted_kinds()
        self.assertNotIn(write_and_push.KIND_B, set(kinds.values()))


class ArchiveModuleReviewTest(ArchiveTreeHarness):
    """tickets/model/archive.py の指摘の再現。"""

    def test_7_a_parent_named_like_a_child_is_not_taken(self):
        write(os.path.join(self.approved, "done", "rel.md"), closed_text("rel"))
        write(os.path.join(self.approved, "done", "rel-01-01.md"), closed_text("rel-01-01"))
        todo = archive.plan(self.approved, ["rel"])
        self.assertEqual(todo.tickets, ["rel"])
        # 子の形でも、親（parent: を持たない）なら閉じた親として数える
        self.assertIn("rel-01-01", archive.closed_parents(self.approved))

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_6_links_in_done_are_not_taken(self):
        os.symlink(
            os.path.join(self.approved, "done", "old.md"),
            os.path.join(self.approved, "done", "lnk.md"),
        )
        self.assertNotIn("lnk", archive.closed_parents(self.approved))
        todo = archive.plan(self.approved, archive.closed_parents(self.approved))
        self.assertNotIn("done/lnk.md", todo.files)

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_6_a_linked_sub_place_in_the_archive_is_not_written(self):
        elsewhere = tempfile.mkdtemp(prefix="ccnavi-archive-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        base = archive.base_dir(self.root, "")
        os.makedirs(base)
        os.symlink(elsewhere, os.path.join(base, "phases"))
        todo = archive.plan(self.approved, ["i0001"])
        _, failed = archive.move(self.root, "", self.approved, todo)
        self.assertIn("シンボリックリンク", failed)
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertTrue(os.path.isfile(os.path.join(self.approved, "done", "i0001.md")))


class ArchivedStandingTest(ChecksHarness):
    """親子のチケットの立ち位置: 手元の退避からは閉じたと推定しない。"""

    def test_an_archived_family_without_its_tree_is_undecided(self):
        from ccnavi.tickets.model import standing

        st = standing.standing(self.conf, self.root, "i0001")
        self.assertIn("ワークツリー", st.stop)

    def test_lookups_stay_in_the_same_repository(self):
        lib = archive.base_dir(self.root, "lib")
        write(os.path.join(lib, "done", "i0009-01-01.md"), closed_text("i0009-01-01", "i0009"))
        self.assertEqual(archive.find(self.root, "i0009-01-01", ""), [])
        self.assertEqual(
            [t.project for t in archive.find(self.root, "i0009-01-01", "lib")], ["lib"]
        )


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
        self.assertNotEqual(
            self.sorted_kinds()[".ccnavi/approved/done/old.md"], write_and_push.KIND_B
        )

    def test_2_the_mark_only_counts_the_four_places(self):
        write(os.path.join(archive.base_dir(self.root, ""), "other", "x.md"), "x")
        self.assertFalse(
            write_and_push.archived_removal("other/x.md", None, b"x", {"other/x.md"}, self.root, "")
        )

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


class SecondReviewModuleTest(ArchiveTreeHarness):
    """2 回目のレビューの指摘の再現（tickets/model/archive.py）。"""

    def test_10_a_ticketless_child_flow_goes_with_its_family(self):
        write(os.path.join(self.approved, "flows", "i0001-01-09.yml"), "steps: []\n")
        todo = archive.plan(self.approved, ["i0001"])
        self.assertIn("flows/i0001-01-09.yml", todo.files)
        # 開いた親（doing/ に在る）の子のフローは拾わない
        self.assertNotIn("flows/open-01-01.yml", todo.files)


class SecondReviewChecksTest(ChecksHarness):
    """2 回目のレビューの指摘の再現（判定）。"""

    def test_5_ready_does_not_archive_from_the_workspace_root(self):
        from ccnavi.tickets.review import close as review_close

        approved = os.path.join(self.root, ".ccnavi", "approved")
        write(os.path.join(approved, "done", "i0001.md"), closed_text("i0001"))
        write(os.path.join(approved, "done", "other.md"), closed_text("other"))
        parent, _ = ticket_mod.parse(closed_text("i0001"))
        out, err = io.StringIO(), io.StringIO()
        code = review_close._archive_closed(
            out, err, self.root, self.conf, parent, approved, "note.md"
        )
        self.assertNotEqual(code, 0)
        self.assertIn("親のワークツリー", err.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(approved, "done", "other.md")))
        self.assertNotIn("other", archive.ids(self.root, ""))

    def test_6_other_letter_case_is_still_found_in_the_archive(self):
        self.assertIsNotNone(archive.archived_fields(self.root, "", "I0001"))
        self.assertEqual(
            approval_ops.next_child_id(self.conf, self.root, "I0001", 1), "I0001-01-04"
        )


class PhaseChildIdsTest(ArchiveTreeHarness):
    """子の識別子が `<親>-<フェーズ番号>-<連番>` の親子の退避。"""

    def test_children_of_several_phases_go_with_their_parent(self):
        write(
            os.path.join(self.approved, "done", "i0001-02-01.md"),
            closed_text("i0001-02-01", "i0001", 2),
        )
        write(os.path.join(self.approved, "flows", "i0001-02-01.yml"), "steps: []\n")
        # チケットが消えた子（チケットはどこにも無く、フローだけが残る）
        write(os.path.join(self.approved, "flows", "i0001-03-02.yml"), "steps: []\n")
        # 識別子が `<親>-<2 桁>` の親。新しい形では子の形ではないので、別の親子のまま
        write(os.path.join(self.approved, "doing", "i0001-02.md"), closed_text("i0001-02"))
        write(os.path.join(self.approved, "flows", "i0001-02.yml"), "steps: []\n")
        todo = archive.plan(self.approved, ["i0001"])
        self.assertEqual(todo.tickets, ["i0001-01-01", "i0001-02-01", "i0001"])
        for rel in (
            "flows/i0001-03-02.yml",
            "flows/i0001-02-01.yml",
        ):
            self.assertIn(rel, todo.files)
        self.assertNotIn("flows/i0001-02.yml", todo.files)
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertEqual(failed, "")
        self.assertIn("done/i0001-02-01.md", moved)
        self.assertEqual(
            {t.ticket for t in archive.closed_tickets(self.root)},
            {"i0001", "i0001-01-01", "i0001-02-01"},
        )


class PhaseChildIdsChecksTest(ChecksHarness):
    """新しい子の識別子で、子の連番が退避を見る。"""

    def test_the_next_child_counts_the_archived_children_of_the_same_phase(self):
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "i0001-02-07.md"), closed_text("i0001-02-07", "i0001", 2))
        self.assertEqual(
            approval_ops.next_child_id(self.conf, self.root, "i0001", 2), "i0001-02-08"
        )
        self.assertEqual(
            approval_ops.next_child_id(self.conf, self.root, "i0001", 3), "i0001-03-01"
        )


if __name__ == "__main__":
    unittest.main()
