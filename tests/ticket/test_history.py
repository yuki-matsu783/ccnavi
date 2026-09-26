"""状態の跡（ADR-0086）の受入テスト。道具を外から呼んで、跡のファイルと応答を見る。

見るのは 5 つ。

1. 状態を動かす操作が、動かすたびに 1 行ずつ足すこと。欄（時刻・識別子・種類・元と先の置き場・経路）
2. マーカーの跡は親に残ること（依頼・レビュー済み・終わりの告知・開き直し）
3. 書けなくても状態は動き、書けなかったことは警告として出ること
4. ボードの JSON に新しい側が載ること
5. 跡のファイルを、実行後の監視が「エージェントの書き込み」として咎めないこと

道具は並行するチケットの受入テスト（test_ticket.TicketTest）のものを借りる。借りるだけで、
あちらのテストはここでは走らせない（`load_tests`）。
"""

from __future__ import annotations

import json
import os
import re
import unittest

from ccnavi import history
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import TicketTest, git, read_json, write

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りた TicketTest のテストは走らせない）。"""
    suite = unittest.TestSuite()
    for case in (HistoryTest, PhaseHistoryTest, ReadBoundaryTest):
        for name in sorted(vars(case)):
            if name.startswith("test_"):
                suite.addTest(case(name))
    return suite


class HistoryTest(TicketTest):
    def lines(self, ticket_id, tree=None):
        path = os.path.join(tree or self.approved, "events", ticket_id + ".ndjson")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def kinds(self, ticket_id):
        return [(e["kind"], e["from"], e["to"], e["via"]) for e in self.lines(ticket_id)]

    def test_each_move_leaves_one_line_with_the_fields(self):
        """承認・着手・閉じる（レビュー不要なので done へ）が、それぞれ 1 行ずつ足す。

        レビュー待ちへ動く形は test_markers_are_left_on_the_parent が見る。
        """
        self.family(review=(False, False))
        for child in ("i0001-01", "i0001-02"):
            done = self.ccnavi("ticket", "finish", child)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn("警告", done.stderr)

        self.assertEqual(
            self.kinds("i0001-01"),
            [
                ("approved", "todo", "doing", "terminal"),
                ("started", "doing", "doing", "cli"),
                ("finished", "doing", "done", "cli"),
            ],
        )
        self.assertEqual(
            self.kinds("i0001-02"),
            [
                ("approved", "todo", "doing", "terminal"),
                ("started", "doing", "doing", "cli"),
                ("finished", "doing", "done", "cli"),
            ],
        )
        for entry in self.lines("i0001-01"):
            self.assertRegex(entry["at"], ISO_UTC)
            self.assertEqual(entry["ticket"], "i0001-01")
        started = self.lines("i0001-01")[1]
        tree = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        self.assertEqual(started["base_sha"], git(tree, "rev-parse", "HEAD").strip())

    def test_cancel_records_the_reason(self):
        self.family()
        cancelled = self.ccnavi("ticket", "cancel", "i0001-02", "--reason", "要らなくなった")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        last = self.lines("i0001-02")[-1]
        self.assertEqual((last["kind"], last["from"], last["to"]), ("cancelled", "doing", "done"))
        self.assertEqual(last["reason"], "要らなくなった")

    def test_a_board_approval_says_board(self):
        """ボードの `--approve --yes` は経路 board で残る。"""
        self.propose("i0001", allow=("src/*",))
        shown = self.ccnavi("--approve", "--preview", "--json")
        digest = json.loads(shown.stdout)["digest"]
        placed = self.ccnavi("--approve", "--yes", "i0001", "--digest", digest, "--json")
        self.assertEqual(placed.returncode, 0, placed.stdout + placed.stderr)
        self.assertEqual(self.kinds("i0001"), [("approved", "todo", "doing", "board")])

    def test_markers_are_left_on_the_parent(self):
        """終わりの告知（hook）・依頼・レビュー済み・子の done への移動が、それぞれ残る。"""
        self.family(review=(True, False))
        for child in ("i0001-01", "i0001-02"):
            tree = os.path.join(self.root, ".claude", "worktrees", child)
            write(os.path.join(tree, "src", child[-1], "work.py"), "x\n")
            git(tree, "add", "-A")
            git(tree, "commit", "--quiet", "-m", "work")
        self.close_phase()
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("フェーズ 1 が終わりました", self.reason(said))
        fixture = self.remote(merge=("i0001-01", "i0001-02"))
        requested = self.request(fixture)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        confirmed = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", "1", "review", "confirm", "--result", fixture
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)

        marks = [
            (e["kind"], e.get("phase"), e.get("mark"), e["via"], e["from"], e["to"])
            for e in self.lines("i0001")
            if e["kind"] == history.KIND_PHASE_MARK
        ]
        self.assertEqual(
            marks,
            [
                ("phase-mark", 1, "pending", "hook", None, None),
                ("phase-mark", 1, "requested", "cli", None, None),
                ("phase-mark", 1, "reviewed", "cli", None, None),
            ],
        )
        finished = self.lines("i0001-01")[-2]
        self.assertEqual(
            (finished["kind"], finished["from"], finished["to"]), ("finished", "doing", "review")
        )
        settled = self.lines("i0001-01")[-1]
        self.assertEqual(
            (settled["kind"], settled["from"], settled["to"], settled["phase"]),
            ("settled", "review", "done", 1),
        )

    def test_a_new_child_in_an_ended_phase_leaves_a_reopening(self):
        """終わったフェーズに子を足すとマーカーが消える。消したことが親に残る。"""
        self.family(review=(False, False))
        self.close_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.propose("i0001-03", parent="i0001", phase=1, allow=("src/c/*",))
        self.assertEqual(self.approve().returncode, 0)
        reopened = [e for e in self.lines("i0001") if e["kind"] == history.KIND_PHASE_REOPENED]
        self.assertEqual(len(reopened), 1, self.lines("i0001"))
        self.assertEqual(reopened[0]["phase"], 1)
        self.assertEqual(reopened[0]["cleared"], ["skipped"])

    def test_a_failed_write_does_not_stop_the_move_and_warns(self):
        """跡が書けなくても（置き場がファイルで塞がっている）、状態は動き、警告が出る。"""
        self.family()
        events = os.path.join(self.approved, "events")
        for name in os.listdir(events):
            os.remove(os.path.join(events, name))
        os.rmdir(events)
        write(events, "ディレクトリではない\n")
        done = self.ccnavi("ticket", "finish", "i0001-02")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "done", "i0001-02.md")))
        self.assertIn("ccnavi: 警告: i0001-02 の履歴（finished）", done.stderr)
        self.assertIn("状態は動いた", done.stderr)

    def test_the_board_carries_the_newest_lines(self):
        self.family()
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        entry = next(t for t in board["tickets"] if t["ticket"] == "i0001-01")
        self.assertEqual([e["kind"] for e in entry["history"]], ["approved", "started"])
        # 上限を超えたら新しい側だけ。
        path = os.path.join(self.approved, "events", "i0001-01.ndjson")
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            for i in range(history.BOARD_LIMIT + 5):
                f.write(json.dumps({"at": "x", "kind": f"k{i}", "from": None, "to": None}) + "\n")
            f.write("読めない行\n")
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        entry = next(t for t in board["tickets"] if t["ticket"] == "i0001-01")
        self.assertEqual(len(entry["history"]), history.BOARD_LIMIT)
        self.assertEqual(entry["history"][-1]["kind"], f"k{history.BOARD_LIMIT + 4}")
        self.assertTrue(any("i0001-01 の履歴" in p for p in board["problems"]), board["problems"])

    def test_the_post_monitor_does_not_report_the_history_it_wrote(self):
        """`ticket start` が足した跡は、実行後の監視が保護領域の変更として咎めない（ADR-0075）。"""
        self.family_without_starting()
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        after = self.hook(
            "PostToolUse",
            "Bash",
            self.parent_tree,
            command="sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        )
        self.assertNotIn("events", self.reason(after))
        self.assertEqual(after.returncode, 0, after.stderr)

    def test_the_log_is_only_appended(self):
        """既に在る行は書き換えない。手で書いた行も残る。"""
        self.family_without_starting()
        path = os.path.join(self.approved, "events", "i0001.ndjson")
        before = read_text(path)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        after = read_text(path)
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(after.splitlines()), len(before.splitlines()) + 1)
        self.assertEqual(read_json_lines(path)[-1]["kind"], "started")

    def test_a_broken_character_in_the_reason_is_kept_and_does_not_stop_the_move(self):
        """不正な UTF-8 由来のサロゲートが理由に混ざっても、状態は動き、跡は元の文字列で読める。"""
        self.family()
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01", "--reason", "bad\udcff")
        self.assertEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
        self.assertNotIn("警告", cancelled.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "done", "i0001-01.md")))
        last = self.lines("i0001-01")[-1]
        self.assertEqual((last["kind"], last["reason"]), ("cancelled", "bad\udcff"))

    def test_a_blocked_history_does_not_stop_a_batch_approval(self):
        """跡が書けなくても、まとめて承認した全部が置かれ、1 件ずつ警告が出る。"""
        self.propose("i0001", allow=("src/*",))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        self.propose("i0001-02", parent="i0001", phase=1, allow=("src/b/*",))
        write(os.path.join(self.approved, "events"), "ディレクトリではない\n")
        approved = self.ccnavi("--approve", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        for name in ("i0001", "i0001-01", "i0001-02"):
            self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", name + ".md")))
            self.assertIn(f"ccnavi: 警告: {name} の履歴（approved）", approved.stderr)

    def test_a_name_that_is_not_an_identifier_is_neither_written_nor_read(self):
        """識別子の形でなければ（区切り文字・先頭の点）、跡のファイルに使わない。書かずに言い、読みは空。"""
        base = os.path.join(self.root, "h")
        with history.session(history.VIA_CLI, None):
            for bad in ("../x", "a/b", ".hidden", ""):
                failed = history.note(base, bad, history.KIND_STARTED, "doing", "doing")
                self.assertIn("識別子の形ではない", failed)
                self.assertEqual(history.path(base, bad), "")
                entries, why = history.read(base, bad)
                self.assertEqual(entries, [])
                self.assertIn("識別子の形ではない", why)
            self.assertEqual(len(history.pending_failures()), 4)
        self.assertFalse(os.path.exists(base))


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json_lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class PhaseHistoryTest(PhaseHarness):
    """計画を持つ親で動く跡（改版・続きの子・親のマーカー・締め）を固定する。"""

    def lines(self, ticket_id):
        path = os.path.join(self.approved, "events", ticket_id + ".ndjson")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def chat_phase(self):
        """`review: chat` のフェーズを 1 つ終わらせる（test_phases.ChatReviewTest と同じ手順）。"""
        self.family(plan=["chores"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("src/a1.py", "x\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")

    def test_a_followup_child_is_raised_and_the_phase_reopens(self):
        self.chat_phase()
        passed = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n直す点\n\n"
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        raised = self.lines("i0001-02")
        self.assertEqual(len(raised), 1, raised)
        self.assertEqual(
            (raised[0]["kind"], raised[0]["from"], raised[0]["to"], raised[0]["via"]),
            ("raised", None, "doing", "terminal"),
        )
        self.assertEqual(raised[0]["followup_of"], ["i0001-01"])
        self.assertEqual(raised[0]["phase"], 1)
        settled = self.lines("i0001-01")[-1]
        self.assertEqual((settled["kind"], settled["via"]), ("settled", "terminal"))
        parent = [(e["kind"], e.get("mark"), e["via"]) for e in self.lines("i0001")]
        self.assertIn(("phase-mark", "reviewed", "terminal"), parent)
        reopened = [e for e in self.lines("i0001") if e["kind"] == "phase-reopened"]
        self.assertEqual(reopened[-1]["cleared"], ["reviewed", "pending"])

    def test_a_feedback_revision_and_closing_the_parent_are_left(self):
        self.chat_phase()
        passed = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n\n")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["chores"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "finish", "i0001").returncode, 0)
        kinds = [(e["kind"], e["from"], e["to"], e.get("mark")) for e in self.lines("i0001")]
        self.assertIn(("revised", "doing", "doing", None), kinds)
        revised = next(e for e in self.lines("i0001") if e["kind"] == "revised")
        self.assertTrue(revised["feedback"])
        self.assertEqual(
            kinds[-2:], [("finished", "doing", "done", None), ("parent-mark", None, None, "closed")]
        )

    def test_close_early_is_left_on_the_parent_and_the_cancelled_child(self):
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/research/summary.md", "s\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.start_parent()
        fixture = self.remote()
        done = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--close-early",
            "--reason",
            "ここまで",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        cancelled = self.lines("i0001-02")[-1]
        self.assertEqual(
            (cancelled["kind"], cancelled["from"], cancelled["to"], cancelled["via"]),
            ("cancelled", "doing", "done", "terminal"),
        )
        parent = [(e["kind"], e.get("phase"), e.get("mark"), e["via"]) for e in self.lines("i0001")]
        self.assertIn(("parent-mark", None, "close-early", "terminal"), parent)
        self.assertIn(("phase-mark", 2, "skipped", "terminal"), parent)
        self.assertEqual(
            read_json(os.path.join(self.approved, "phases", "i0001", "close-early.json"))["reason"],
            "ここまで",
        )


class ReadBoundaryTest(unittest.TestCase):
    """読む窓（末尾 READ_LIMIT_BYTES）の切れ目が行の頭にちょうど当たっても、完全な行を捨てない。"""

    def setUp(self):
        import shutil
        import tempfile

        self.base = tempfile.mkdtemp(prefix="ccnavi-history-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        os.makedirs(os.path.join(self.base, "events"))

    def fill(self, width, count, tail=""):
        line = json.dumps({"k": "a" * width})
        with open(history.path(self.base, "x"), "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(line + "\n" for _ in range(count)) + tail)
        return len(line) + 1

    def test_the_window_starting_exactly_at_a_line_keeps_that_line(self):
        # 1 行 128 バイト（改行込み）なら、窓の中にちょうど 512 行が入る。
        size = self.fill(128 - 10, history.READ_LIMIT_BYTES // 128 + 1)
        self.assertEqual(size, 128)
        entries, why = history.read(self.base, "x", limit=0)
        self.assertEqual(why, "")
        self.assertEqual(len(entries), history.READ_LIMIT_BYTES // 128)

    def test_the_window_starting_inside_a_line_drops_only_that_cut_line(self):
        # 末尾に 8 バイトの行を足すと、窓の頭は 128 バイトの行の途中（8 バイト目）に当たる。
        tail = json.dumps({"z": 1}, separators=(",", ":")) + "\n"
        self.assertEqual(len(tail), 8)
        size = self.fill(128 - 10, history.READ_LIMIT_BYTES // 128 + 1, tail=tail)
        self.assertEqual(size, 128)
        entries, why = history.read(self.base, "x", limit=0)
        # 切れ端を捨て、残りの完全な行だけを読む（読めない行とは数えない）。
        self.assertEqual(why, "")
        self.assertEqual(len(entries), history.READ_LIMIT_BYTES // 128)
        self.assertEqual(entries[-1], {"z": 1})

    def test_a_short_file_is_read_whole(self):
        self.fill(10, 3)
        entries, why = history.read(self.base, "x", limit=0)
        self.assertEqual((len(entries), why), (3, ""))
