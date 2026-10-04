"""閉じた親子のチケットの退避（`ready` が `logs/archive/` へ移す）。

- 退避の並びと移し方（archive.py）
- `ready` が条件を確かめてから退避し、打ち直しても通ること
- C1 の見分け（`c1.classify_all`）と実行後チェック（`post._script_writes`）が、退避の削除だけを
  ccnavi の書き込みとして外すこと
- 閉じた識別子の使い回し・子の連番・先行の池が退避を見ること
- ボードの JSON に退避のチケットが載ること（判定には混ぜない）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.hook import c1
from ccnavi.infra import settings
from ccnavi.tickets import approval, archive, history
from ccnavi.tickets import ticket as ticket_mod
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import git, read_json, write


def closed_text(name, parent="", phase=None, predecessors=()):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase or 1}"]
    if predecessors:
        lines.append(f"predecessors: [{', '.join(predecessors)}]")
    lines += [
        "human_review:",
        "  required: false",
        "  reason: t",
        f"title: {name} の題",
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


class ArchiveModuleTest(unittest.TestCase):
    """archive.py の並びと移し方。git は使わない。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-archive-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.approved = os.path.join(self.root, "tree", ".ccnavi", "approved")
        for name, parent in (
            ("i0001", ""),
            ("i0001-01", "i0001"),
            ("old", ""),
            ("old-01", "old"),
            ("open-01", "open"),
        ):
            write(os.path.join(self.approved, "done", f"{name}.md"), closed_text(name, parent))
        write(os.path.join(self.approved, "doing", "open.md"), closed_text("open"))
        write(os.path.join(self.approved, "phases", "i0001", "1.reviewed.json"), "{}")
        write(os.path.join(self.approved, "phases", "i0001", "i0001-01.risk.json"), "{}")
        write(os.path.join(self.approved, "phases", "open", "1.pending.json"), "{}")
        for name in ("i0001", "i0001-01", "old", "open", "open-01"):
            write(os.path.join(self.approved, "events", f"{name}.ndjson"), '{"a": 1}\n')
        write(os.path.join(self.approved, "flows", "i0001-01.yml"), "steps: []\n")
        write(os.path.join(self.approved, "flows", "open-01.yml"), "steps: []\n")

    def test_plan_takes_only_closed_families(self):
        self.assertEqual(archive.closed_parents(self.approved), ["i0001", "old"])
        todo = archive.plan(self.approved, archive.closed_parents(self.approved))
        self.assertEqual(todo.parents, ["i0001", "old"])
        # 子のチケットが先、親のチケットが最後（止まったときに親が done/ に残る）
        self.assertEqual(todo.tickets, ["i0001-01", "old-01", "i0001", "old"])
        self.assertEqual(todo.files[-1], "done/old.md")
        self.assertIn("phases/i0001/1.reviewed.json", todo.files)
        self.assertIn("events/old.ndjson", todo.files)
        self.assertIn("flows/i0001-01.yml", todo.files)
        # 開いた親（doing/ に在る）の子・跡・フロー・マーカーは移さない
        for rel in todo.files:
            self.assertNotIn("open", rel)

    def test_move_copies_then_removes_and_notes_the_trace(self):
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
        entries, _ = history.read(base, "i0001")
        self.assertEqual(entries[-1]["kind"], history.KIND_ARCHIVED)
        self.assertEqual((entries[-1]["from"], entries[-1]["to"]), ("done", "archive"))
        # 退避から引ける
        self.assertEqual(archive.ids(self.root, ""), {"i0001", "i0001-01"})
        self.assertEqual(archive.ids(self.root), {"i0001", "i0001-01"})
        self.assertEqual(archive.archived_parent(self.root, "", "i0001").ticket, "i0001")
        self.assertIsNone(archive.archived_parent(self.root, "", "i0001-01"))
        self.assertEqual([t.state for t in archive.find(self.root, "i0001-01")], ["done"])

    def test_move_again_with_the_same_content_only_removes(self):
        """前の回の残り（同じ中身）が退避に在れば、写さずに元だけ消す（打ち直し）。"""
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

    def test_move_again_replaces_a_different_mark_or_trace(self):
        """C1 が push できずに戻した後の打ち直しでは、跡と印だけが前の回と違う。置き換えて進む。"""
        todo = archive.plan(self.approved, ["i0001"])
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "events", "i0001.ndjson"), '{"a": 0}\n')
        write(os.path.join(base, "phases", "i0001", "1.reviewed.json"), '{"at": "前"}')
        moved, failed = archive.move(self.root, "", self.approved, todo)
        self.assertEqual(failed, "")
        self.assertIn("events/i0001.ndjson", moved)
        with open(os.path.join(base, "phases", "i0001", "1.reviewed.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{}")
        entries, _ = history.read(base, "i0001")
        self.assertEqual(entries[0], {"a": 1})

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


class ArchiveChecksTest(unittest.TestCase):
    """閉じた識別子の使い回し・子の連番・先行の池が退避を見る。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-archive-checks-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "i0001.md"), closed_text("i0001"))
        write(os.path.join(base, "done", "i0001-03.md"), closed_text("i0001-03", "i0001"))
        self.conf, _ = settings.load(self.root)

    def test_a_reused_identifier_is_refused(self):
        t, _ = ticket_mod.parse(closed_text("i0001"))
        t.state = ticket_mod.TODO
        problems = approval.integration_problems(self.conf, self.root, t)
        self.assertEqual(len(problems), 1)
        self.assertIn("退避", problems[0].detail)
        self.assertEqual(approval.integration_closed(self.conf, self.root, [t]), {"i0001"})
        fresh, _ = ticket_mod.parse(closed_text("i0002"))
        self.assertEqual(approval.integration_problems(self.conf, self.root, fresh), [])

    def test_the_next_child_skips_the_archived_numbers(self):
        self.assertEqual(approval.next_child_id(self.conf, self.root, "i0001"), "i0001-04")

    def test_a_predecessor_in_the_archive_is_met(self):
        text = closed_text("i0002-01", "i0002", predecessors=["i0001-03"])
        t, problems = ticket_mod.parse(text)
        self.assertIsNotNone(t, problems)
        self.assertEqual(t.predecessors, ["i0001-03"])
        pool = approval.predecessor_pool_of([t], [], [], [], self.root)
        self.assertEqual([h.state for h in pool["i0001-03"]], ["done"])
        self.assertEqual(approval.unmet_predecessors(t, pool), [])
        # root を渡さなければ見ない（前のまま）
        pool = approval.predecessor_pool_of([t], [], [], [])
        self.assertNotIn("i0001-03", pool)


class ReadyArchivesTest(PhaseHarness):
    """`review ready` が条件を確かめてから閉じた親子のチケットを退避する。"""

    def closable(self):
        """親を閉じて片付け、push 済みにする。fixture（マージリクエストの写し）を返す。"""
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("logs/archive/", closed.stdout)
        # 統合先にたまっていた過去の親（閉じた親子のチケット）
        write(os.path.join(self.approved, "done", "old.md"), closed_text("old"))
        write(os.path.join(self.approved, "done", "old-01.md"), closed_text("old-01", "old"))
        write(os.path.join(self.approved, "phases", "old", "closed.json"), "{}\n")
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def ready(self, fixture):
        return self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)

    def test_ready_moves_every_closed_family_and_can_be_run_again(self):
        fixture = self.closable()
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        lines = passed.stdout.splitlines()
        with open(lines[0], encoding="utf-8") as f:
            self.assertIn("logs/archive/", f.read())
        self.assertEqual(lines[1], f"tree {self.parent_tree}")
        base = os.path.join(self.root, "logs", "archive", "self")
        for rel in ("done/i0001.md", "done/i0001-01.md", "done/old.md", "done/old-01.md"):
            self.assertTrue(os.path.isfile(os.path.join(base, *rel.split("/"))), rel)
            self.assertFalse(os.path.exists(os.path.join(self.approved, *rel.split("/"))), rel)
        # Draft を外した印も一緒に退避される
        self.assertEqual(read_json(os.path.join(base, "phases", "i0001", "ready.json"))["mr"], 7)
        self.assertTrue(os.path.isfile(os.path.join(base, "phases", "old", "closed.json")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "phases", "i0001")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "events", "i0001.ndjson")))
        # git の上では削除だけ（退避は logs/ の下で追跡しない）
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
        # 片付けてから見る（残っていると、そこに写った承認済みチケットが置き場の側に出る）
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        git(self.root, "worktree", "remove", "--force", child)
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        archived = {a["ticket"]: a for a in board["archived"]}
        self.assertEqual(set(archived), {"i0001", "i0001-01", "old", "old-01"})
        self.assertTrue(archived["i0001"]["path"].startswith(base))
        self.assertEqual(archived["i0001"]["history"][-1]["kind"], history.KIND_ARCHIVED)
        self.assertNotIn("i0001", {t["ticket"] for t in board["tickets"]})

    def test_ready_does_not_move_anything_while_the_parent_is_not_closable(self):
        self.family(plan=["design"])
        fixture = self.remote()
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "archive")))

    def test_c1_and_the_post_check_count_only_the_archive_removals(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        # 退避と関係の無い削除も 1 つ混ぜる（開いた親の跡）
        write(os.path.join(self.approved, "events", "stray.ndjson"), "{}\n")
        git(self.parent_tree, "add", ".ccnavi/approved/events/stray.ndjson")
        git(self.parent_tree, "commit", "--quiet", "-m", "stray", "--", ".ccnavi/approved/events")
        os.remove(os.path.join(self.approved, "events", "stray.ndjson"))
        status = git(self.parent_tree, "status", "--porcelain", "--no-renames").splitlines()
        changed = sorted(line[3:] for line in status)
        places = (".ccnavi/approved", "wip/proposals/review")
        base = archive.base_dir(self.root, "")
        sorted_out = {
            rel: kind
            for kind, rel, _ in c1.classify_all(
                self.parent_tree, places, "i0001", changed, "", (self.root, "")
            )
        }
        self.assertEqual(sorted_out[".ccnavi/approved/done/i0001.md"], c1.KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/done/old-01.md"], c1.KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/phases/i0001/closed.json"], c1.KIND_B)
        self.assertEqual(sorted_out[".ccnavi/approved/events/stray.ndjson"], c1.KIND_D)
        # 退避の置き場を渡さなければ、前のとおり ccnavi の書き込みとは見分けない（止める）
        plain = c1.classify_all(self.parent_tree, places, "i0001", changed, "", None)
        self.assertNotIn(c1.KIND_B, {kind for kind, _, _ in plain})
        # 退避の写しが違えば (b) にしない
        write(os.path.join(base, "done", "old-01.md"), "書き換えた\n")
        again = {
            rel: kind
            for kind, rel, _ in c1.classify_all(
                self.parent_tree, places, "i0001", changed, "", (self.root, "")
            )
        }
        self.assertEqual(again[".ccnavi/approved/done/old-01.md"], c1.KIND_D)
        self.assertEqual(again[".ccnavi/approved/done/i0001.md"], c1.KIND_B)

    def test_the_post_check_does_not_report_the_archive_removals(self):
        fixture = self.closable()
        # 閉じていない親（done/ に無い）の子。退避の対象にならない
        write(os.path.join(self.approved, "done", "zzz-01.md"), closed_text("zzz-01", "zzz"))
        self.commit_parent("stray")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.approved, "done", "zzz-01.md")))
        os.remove(os.path.join(self.approved, "done", "zzz-01.md"))
        result = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        said = result.stdout + result.stderr
        # 退避と関係の無いチケットの削除は今までどおり報告する
        self.assertIn("done/zzz-01.md", said)
        self.assertNotIn("done/i0001.md", said)
        self.assertNotIn("done/old-01.md", said)
        self.assertNotIn("phases/i0001/closed.json", said)


if __name__ == "__main__":
    unittest.main()


class ReviewFindingsTest(ReadyArchivesTest):
    """レビューで挙がった指摘の再現（直す前は落ちる）。"""

    def changed(self):
        status = git(self.parent_tree, "status", "--porcelain", "--no-renames").splitlines()
        return sorted(line[3:] for line in status)

    def sorted_kinds(self, changed=None, since=""):
        places = (".ccnavi/approved", "wip/proposals/review")
        return {
            rel: kind
            for kind, rel, _ in c1.classify_all(
                self.parent_tree,
                places,
                "i0001",
                self.changed() if changed is None else changed,
                since,
                (self.root, ""),
            )
        }

    def test_1_a_stale_doing_copy_in_a_child_tree_does_not_come_back(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.commit_parent("退避")
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        self.assertTrue(
            os.path.isfile(os.path.join(child, ".ccnavi", "approved", "doing", "i0001-01.md"))
        )
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        opened = {
            t["ticket"] for t in board["tickets"] if t["copy"]["status"] in ("open", "review")
        }
        self.assertNotIn("i0001-01", opened)
        self.assertNotIn("i0001", opened)

    def test_3_a_stopped_move_is_finished_by_the_next_ready(self):
        fixture = self.closable()
        base = os.path.join(self.root, "logs", "archive", "self")
        write(os.path.join(base, "done", "old-01.md"), "違う中身\n")
        stopped = self.ready(fixture)
        self.assertNotEqual(stopped.returncode, 0)
        os.remove(os.path.join(base, "done", "old-01.md"))
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)
        for rel in ("done/i0001.md", "done/old.md", "done/old-01.md", "phases/old/closed.json"):
            self.assertFalse(os.path.exists(os.path.join(self.approved, *rel.split("/"))), rel)
            self.assertTrue(os.path.isfile(os.path.join(base, *rel.split("/"))), rel)

    def test_4_committed_events_are_carried_by_c1(self):
        fixture = self.closable()
        self.assertTrue(os.path.isfile(os.path.join(self.approved, "events", "i0001.ndjson")))
        self.assertEqual(self.ready(fixture).returncode, 0)
        kinds = self.sorted_kinds()
        self.assertEqual(kinds[".ccnavi/approved/events/i0001.ndjson"], c1.KIND_B)
        self.assertEqual(kinds[".ccnavi/approved/events/i0001-01.ndjson"], c1.KIND_B)
        # 退避の跡の最後は「退避した」
        entries, _ = history.read(archive.base_dir(self.root, ""), "i0001")
        self.assertEqual(entries[-1]["kind"], history.KIND_ARCHIVED)
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
        self.assertEqual(kinds[".ccnavi/approved/events/i0001.ndjson"], c1.KIND_B)
        self.assertEqual(kinds[".ccnavi/approved/done/i0001.md"], c1.KIND_B)

    def test_5_a_removal_outside_ready_is_not_carried(self):
        self.family(plan=["design"])
        write(os.path.join(self.approved, "done", "old.md"), closed_text("old"))
        write(os.path.join(self.approved, "events", "old.ndjson"), '{"a": 1}\n')
        self.commit_parent("old")
        # 退避に同じ中身を置き、ready を経ずに消す
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "old.md"), closed_text("old"))
        write(os.path.join(base, "events", "old.ndjson"), '{"a": 1}\n')
        os.remove(os.path.join(self.approved, "done", "old.md"))
        os.remove(os.path.join(self.approved, "events", "old.ndjson"))
        kinds = self.sorted_kinds()
        self.assertNotEqual(kinds[".ccnavi/approved/done/old.md"], c1.KIND_B)
        self.assertNotEqual(kinds[".ccnavi/approved/events/old.ndjson"], c1.KIND_B)

    @unittest.skipIf(os.name == "nt", "シンボリックリンクを作れないことがある")
    def test_6_a_linked_logs_dir_is_not_trusted_by_c1(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        logs = os.path.join(self.root, "logs")
        moved = os.path.join(self.root, "elsewhere")
        os.rename(logs, moved)
        os.symlink(moved, logs)
        kinds = self.sorted_kinds()
        self.assertNotIn(c1.KIND_B, set(kinds.values()))


class ArchiveModuleReviewTest(ArchiveModuleTest):
    """archive.py の指摘の再現。"""

    def test_7_a_parent_named_like_a_child_is_not_taken(self):
        write(os.path.join(self.approved, "done", "rel.md"), closed_text("rel"))
        write(os.path.join(self.approved, "done", "rel-01.md"), closed_text("rel-01"))
        todo = archive.plan(self.approved, ["rel"])
        self.assertEqual(todo.tickets, ["rel"])
        # 子の形でも、親（parent: を持たない）なら閉じた親として数える
        self.assertIn("rel-01", archive.closed_parents(self.approved))

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

    def test_4_a_stop_does_not_note_tickets_that_were_not_moved(self):
        todo = archive.plan(self.approved, ["i0001", "old"])
        base = archive.base_dir(self.root, "")
        write(os.path.join(base, "done", "old.md"), "違う\n")
        _, failed = archive.move(self.root, "", self.approved, todo)
        self.assertIn("違う中身", failed)
        # old のチケットは移していないので、退避の跡にも「退避した」は無い
        base = archive.base_dir(self.root, "")
        with open(os.path.join(base, "events", "old.ndjson"), encoding="utf-8") as f:
            self.assertNotIn("archived", f.read())
        entries, _ = history.read(base, "i0001")
        self.assertEqual(entries[-1]["kind"], history.KIND_ARCHIVED)
        # ツリーの跡には書かない（退避の側にだけ書く）
        for name in os.listdir(os.path.join(self.approved, "events")):
            with open(os.path.join(self.approved, "events", name), encoding="utf-8") as f:
                self.assertNotIn("archived", f.read(), name)


class ArchivedStandingTest(ArchiveChecksTest):
    """取り込みの立ち位置: 親のワークツリーが無く、手元の退避に親があれば閉じた親子のチケット。"""

    def test_an_archived_family_without_its_tree_is_closed(self):
        from ccnavi.tickets import syncstate

        record = os.path.join(self.conf.state, "sync", "self", "families", "i0001")
        write(record, "remote origin\nbranch i0001\nsha x\nfetched_at 1\nstate present\nreason \n")
        st = syncstate.standing(self.conf, self.root, "i0001")
        self.assertTrue(st.closed, st.stop)
        # 退避に無い親子のチケットは前のとおり「決まらない」
        write(record.replace("i0001", "i0002"), "state present\n")
        other = syncstate.standing(self.conf, self.root, "i0002")
        self.assertFalse(other.closed)
        self.assertIn("ワークツリー", other.stop)

    def test_lookups_stay_in_the_same_repository(self):
        lib = archive.base_dir(self.root, "lib")
        write(os.path.join(lib, "done", "i0009-01.md"), closed_text("i0009-01", "i0009"))
        self.assertEqual(archive.find(self.root, "i0009-01", ""), [])
        self.assertEqual([t.project for t in archive.find(self.root, "i0009-01", "lib")], ["lib"])
        text = closed_text("i0002-01", "i0002", predecessors=["i0009-01"])
        t, _ = ticket_mod.parse(text)
        pool = approval.predecessor_pool_of([t], [], [], [], self.root)
        self.assertNotIn("i0009-01", pool)
