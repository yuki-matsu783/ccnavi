"""write-and-push の実行ファイルの側の受入テスト。

write-and-push は、親子のチケットで状態を書く操作を
「ロック → 取り込み → 書く → コミット → push」の 1 操作にする形。
実行ファイルは対象かの見分けと書いたパスの一覧だけを持ち、git の操作は sh が持つ。

見るのは 2 つ。sh の側（ロック・取り込み・コミット・push・戻し）は
tests/sh/test_write_and_push_*.py（道具は tests/sh/write_and_push_harness.py）が見る。
`ccnavi write-and-push family`（対象かの見分け）と `sort`（置き場の変更の見分け）は、Go の単体テスト
（`go/internal/tickets/approval/wap/`）が見る。

1. `--record-tree`: 書いたパスの一覧の基点を親のワークツリーにし、置き場の外に書けば error
   （一覧は書く。例外は無い）
2. ユーザの判断の入口の sh（`ccnavi-user-close-early.sh`）は
   エージェントから止める
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_standing import AuthorityHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, parent_text

APPROVED = ".ccnavi/approved"


class RecordTreeTest(AuthorityHarness):
    def setUp(self):
        super().setUp()
        self.commit_parent("settle")

    def place(self):
        return os.path.join(self.root, "logs", "state", "write-and-push", "self")

    def run_recorded(self, *args):
        target = os.path.join(self.place(), "i0001.t.writes")
        result = self.ccnavi("--record-writes", target, "--record-tree", self.parent_tree, *args)
        listed = []
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as f:
                listed = [line for line in f.read().splitlines() if line]
        return result, listed

    def test_the_list_is_relative_to_the_parent_tree(self):
        self.worktree("i0001-01-02", "i0001")
        result, listed = self.run_recorded("ticket", "cancel", "i0001-01-01", "--reason", "r")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{APPROVED}/done/i0001-01-01.md", listed)
        self.assertIn(f"{APPROVED}/doing/i0001-01-01.md", listed)
        self.assertTrue(all(not os.path.isabs(p) for p in listed), listed)

    def test_writing_outside_the_places_is_an_error_and_the_list_is_kept(self):
        # 承認は提案（wip/proposals/todo/）を消す。write-and-push の置き場の外なので error。
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("approval-request")
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        result, listed = self.run_recorded(
            "user", "agree", "--yes", "i0001-01-02", "--digest", preview["digest"], "--json"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("置き場の外に書き込みがあった", result.stderr)
        self.assertIn("wip/proposals/todo/i0001-01-02.md", listed)
        self.assertIn(f"{APPROVED}/doing/i0001-01-02.md", listed)

    def test_record_tree_needs_record_writes(self):
        result = self.ccnavi("--record-tree", self.parent_tree, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--record-writes と一緒に", result.stderr)


class HumanEntryGuardTest(AuthorityHarness):
    def test_the_human_entries_are_denied_to_the_agent(self):
        for command in (
            "sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason r",
            # シェルに選択肢を付けた形も同じ
            "sh -x .ccnavi/scripts/ccnavi-user-push-approved.sh",
            "ccnavi worktree retire --abandon i0001",
        ):
            result = self.ccnavi(
                "--guard-ticket-approval",
                "enable",
                "--mode",
                "enable",
                stdin=json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "cwd": self.parent_tree,
                        "session_id": "s1",
                        "tool_input": {"command": command},
                    }
                ),
            )
            self.assertEqual("deny", self.decision(result), command + result.stdout)
        # 依頼・確認・Draft 外しはエージェントの経路のまま。
        allowed = self.ccnavi(
            "--guard-ticket-approval",
            "enable",
            "--mode",
            "enable",
            stdin=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": self.parent_tree,
                    "session_id": "s1",
                    "tool_input": {"command": "sh .ccnavi/scripts/ccnavi-review.sh confirm"},
                }
            ),
        )
        self.assertNotEqual("deny", self.decision(allowed), allowed.stdout)


class RecordTreeReviewTest(PhaseHarness):
    """実際の実行ファイルで、`--record-tree` 付きの依頼・行き先・Draft 外しが置き場だけを書く。

    PhaseHarness は state の置き場を `--state` で動かしている（上書きした置き場）。下書きはそこへ
    書かれる。
    """

    WRITERS = ("requested", "confirm", "ready")

    def setUp(self):
        super().setUp()
        self.recorded = []

    def ccnavi(self, *args, stdin=""):
        writes = any(a in args for a in self.WRITERS) or (
            "--yes" in args and "user" in args and "reviewed" in args
        )
        if not writes:
            return super().ccnavi(*args, stdin=stdin)
        target = os.path.join(self.root, "logs", "state", "write-and-push", "self", "r.writes")
        result = super().ccnavi(
            "--record-writes", target, "--record-tree", self.parent_tree, *args, stdin=stdin
        )
        with open(target, encoding="utf-8") as f:
            listed = [line for line in f.read().splitlines() if line]
        self.recorded.append((args, result, listed))
        return result

    def places_only(self):
        for args, result, listed in self.recorded:
            self.assertNotIn("置き場の外", result.stderr, args)
            for path in listed:
                self.assertTrue(
                    path.startswith((".ccnavi/approved/", "wip/proposals/review/")), (args, path)
                )

    def test_request_decide_and_ready_write_only_the_places(self):
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
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        shown = super().ccnavi(
            "--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved",
            "--preview", "--json", "--result", fixture,
        )  # fmt: skip
        digest = json.loads(shown.stdout)["digest"]
        decided = self.ccnavi(
            "--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved",
            "--yes", "{}", "--digest", digest, "--json", "--result", fixture,
        )  # fmt: skip
        self.assertEqual(decided.returncode, 0, decided.stdout + decided.stderr)
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        passed = self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        archived = os.path.join(self.root, "logs", "archive", "self")
        self.assertTrue(read_json(os.path.join(archived, "phases", "i0001", "ready.json"))["mr"])
        self.assertEqual(len(self.recorded), 3)
        self.places_only()


class ChooseTest(PhaseHarness):
    """対話の decide の前半（`--choose-out`）: 選んで書くだけ。"""

    def test_choose_writes_only_the_choices(self):
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
        self.commit_parent("requested")
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "x"}]
        data["reviews"] = []
        write(fixture, json.dumps(data))
        out = os.path.join(self.state, "choose.json")
        chosen = self.ccnavi(
            "--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved",
            "--choose-out", out, "--result", fixture, stdin="k\n",
        )  # fmt: skip
        self.assertEqual(chosen.returncode, 0, chosen.stdout + chosen.stderr)
        with open(out, encoding="utf-8") as f:
            answer = dict(line.rstrip("\n").split(" ", 1) for line in f)
        self.assertEqual(json.loads(answer["choices"]), {"u/7#t0": "keep"})
        status = git(self.parent_tree, "status", "--porcelain")
        self.assertEqual(status.strip(), "")
        outside = self.ccnavi(
            "--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved",
            "--choose-out", os.path.join(self.root, "x.json"), "--result", fixture, stdin="k\n",
        )  # fmt: skip
        self.assertEqual(outside.returncode, 1)
        done = self.ccnavi(
            "--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved",
            "--yes", answer["choices"], "--digest", answer["digest"],
            "--json", "--result", fixture,
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class BypassTest(AuthorityHarness):
    """write-and-push の対象の親子のチケット（取り込み済みで origin がある）では、
    write-and-push を通らない状態の操作を断る。
    """

    mark_write_and_push = False

    def setUp(self):
        super().setUp()
        git(self.root, "remote", "add", "origin", os.path.join(self.root, "nowhere.git"))

    def test_state_operations_without_record_tree_are_refused(self):
        before = self.ccnavi("write-and-push", "family", "i0001")
        self.assertIn("target yes", before.stdout)
        for args in (("finish", "i0001-01-01"), ("start", "i0001"), ("approval-request", "i0001")):
            refused = self.ccnavi("ticket", *args)
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            self.assertIn("write-and-push の対象", refused.stderr, args)
            self.assertIn("ccnavi-ticket.sh", refused.stderr, args)
        fixture = write(os.path.join(self.root, "r.json"), json.dumps({"host": "x"}))
        for args in (
            ("review", "confirm", "--phase", "1", "--result", fixture),
            ("review", "ready", "--result", fixture),
            ("user", "reviewed", "1", "--accept-unresolved", "--result", fixture),
        ):
            result = self.ccnavi("--cwd", self.parent_tree, *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("write-and-push の対象", result.stderr, args)

    def test_record_risk_and_human_entries_are_not_refused_here(self):
        risk = self.ccnavi("ticket", "record-risk", "i0001-01-01", "x", "yes", "--reason", "r")
        self.assertNotIn("write-and-push の対象", risk.stderr)
