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
from tests.ticket.test_ticket import TicketTest, git, write

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りた TicketTest のテストは走らせない）。"""
    suite = unittest.TestSuite()
    for case in (HistoryTest,):
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


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json_lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
