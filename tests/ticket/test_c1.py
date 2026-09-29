"""C1 の実行ファイルの側（ADR-0093 の 4.3・4.4。段階 2d）の受入テスト。

見るのは 4 つ。sh の側（ロック・取り込み・コミット・push・戻し）は tests/sh/test_c1_sh.py が見る。

1. `ccnavi c1 family <識別子>`: 家族（子なら親）と、C1 の対象か。控えの無い家族・chat だけの家族は
   対象外（D11）、決まらない家族は stop
2. `ccnavi c1 sort <親> [<版>]`: 置き場の変更の見分け（4.4 の (b)・(c)・(d)、数えない一時ファイル、
   record-risk の記録）。未コミットとコミット済み（`<版>..HEAD`）の両方
3. `--record-tree`: 書いたパスの一覧の基点を親のワークツリーにし、置き場の外に書けば error
   （一覧は書く。D34 の configsync の写しは例外で、tests/config/test_configsync.py が見る）
4. 人の判断の入口の sh（`ccnavi-review.sh chat / config-synced / close-early`）はエージェントから
   止める
"""

from __future__ import annotations

import json
import os

from ccnavi import c1
from tests.ticket.test_phases import child_text
from tests.ticket.test_sync_authority import AuthorityHarness
from tests.ticket.test_ticket import git, write

APPROVED = ".ccnavi/approved"


def lines(out):
    return dict(
        (line.split(" ", 1) + [""])[:2] for line in out.splitlines() if line and line != c1.HEAD
    )


class FamilyTest(AuthorityHarness):
    def ask(self, ident):
        result = self.ccnavi("c1", "family", ident)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], c1.HEAD)
        return lines(result.stdout)

    def test_no_record_is_not_a_target(self):
        answer = self.ask("i0001-01")
        self.assertEqual(answer["family"], "i0001")
        self.assertEqual(answer["target"], "no")
        self.assertIn("控えが無い", answer["why"])
        self.assertNotIn("tree", answer)

    def test_a_present_family_is_a_target_with_its_tree(self):
        self.record("present")
        answer = self.ask("i0001-01")
        self.assertEqual(answer["target"], "yes")
        self.assertEqual(answer["repo"], "self")
        self.assertEqual(os.path.realpath(answer["tree"]), os.path.realpath(self.parent_tree))
        self.assertEqual(answer["approved"], APPROVED)
        self.assertEqual(answer["review"], "wip/proposals/review")
        self.assertEqual(answer["state"], "present")

    def test_a_gone_family_stops_with_hints(self):
        self.record("gone")
        result = self.ccnavi("c1", "family", "i0001")
        answer = lines(result.stdout)
        self.assertEqual(answer["target"], "stop")
        self.assertIn("gone", answer["why"])
        self.assertIn("hint ", result.stdout)

    def test_a_bad_id_is_refused(self):
        result = self.ccnavi("c1", "family", "../x")
        self.assertEqual(result.returncode, 1)


class ChatOnlyFamilyTest(AuthorityHarness):
    def setUp(self):
        super(AuthorityHarness, self).setUp()
        self.family(plan=["chores"])

    def test_a_chat_only_family_is_not_a_target(self):
        self.record("present")
        result = self.ccnavi("c1", "family", "i0001")
        answer = lines(result.stdout)
        self.assertEqual(answer["target"], "no")
        self.assertIn("chat だけの家族", answer["why"])


class SortTest(AuthorityHarness):
    def setUp(self):
        super().setUp()
        self.record("present")
        self.commit_parent("settle")

    def sort(self, since=""):
        result = self.ccnavi("c1", "sort", "i0001", *([since] if since else []))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], c1.HEAD)
        return sorted(tuple(line.split(" ", 1)) for line in result.stdout.splitlines()[1:])

    def put(self, rel, text):
        return write(os.path.join(self.parent_tree, *rel.split("/")), text)

    def events(self):
        return f"{APPROVED}/events/i0001.ndjson"

    def append_event(self, row):
        path = os.path.join(self.parent_tree, *self.events().split("/"))
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row) + "\n")

    def test_nothing_changed_lists_nothing(self):
        self.assertEqual(self.sort(), [])

    def test_hook_marks_and_event_lines_are_b(self):
        pending = f"{APPROVED}/phases/i0001/2.pending"
        skipped = f"{APPROVED}/phases/i0001/3.skipped"
        self.put(pending, json.dumps({"review": "mr", "source": "common", "at": "t"}))
        self.put(skipped, json.dumps({"deferred_to": 4, "at": "t"}))
        self.append_event({"at": "t", "ticket": "i0001", "kind": "phase-mark", "phase": 2})
        self.assertEqual(
            self.sort(), sorted([("b", pending), ("b", skipped), ("b", self.events())])
        )

    def test_forged_or_human_marks_are_not_b(self):
        reviewed = f"{APPROVED}/phases/i0001/1.reviewed"
        early = f"{APPROVED}/phases/i0001/2.skipped"
        extra = f"{APPROVED}/phases/i0001/3.pending"
        self.put(reviewed, json.dumps({"by": "chat", "at": "t"}))
        self.put(early, json.dumps({"by": "close-early", "at": "t"}))
        self.put(extra, json.dumps({"review": "mr", "at": "t", "reviewed": True}))
        found = dict((path, kind) for kind, path in self.sort())
        self.assertEqual(found[reviewed], "c")
        self.assertEqual(found[early], "c")
        self.assertEqual(found[extra], "d")

    def test_a_rewritten_hook_mark_is_not_b(self):
        rel = f"{APPROVED}/phases/i0001/2.pending"
        self.put(rel, json.dumps({"review": "mr", "at": "t"}))
        self.commit_parent("mark")
        self.put(rel, json.dumps({"review": "mr", "at": "u"}))
        self.assertEqual(self.sort(), [("d", rel)])

    def test_an_edited_event_history_is_not_b(self):
        path = os.path.join(self.parent_tree, *self.events().split("/"))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace('"approved"', '"started"', 1))
        self.assertEqual(self.sort(), [("d", self.events())])
        git(self.parent_tree, "checkout", "--", self.events())
        self.append_event({"at": "t", "ticket": "i0001"})  # kind が無い
        self.assertEqual(self.sort(), [("d", self.events())])

    def test_flows_approvals_and_other_files(self):
        flow = f"{APPROVED}/flows/i0001-01.yml"
        copy = f"{APPROVED}/doing/i0001-02.md"
        other = f"{APPROVED}/notes.txt"
        review = "wip/proposals/review/i0001-01.md"
        self.put(flow, "steps: []\n")
        self.put(copy, child_text("i0001-02", "i0001", 1, ["wip/research/*"]))
        self.put(other, "x\n")
        self.put(review, "x\n")
        found = dict((path, kind) for kind, path in self.sort())
        self.assertEqual(found, {flow: "c", copy: "c", other: "d", review: "d"})

    def test_judge_records_are_kept_and_temp_files_skipped(self):
        judge = f"{APPROVED}/phases/i0001/i0001-01.judge.json"
        temp = f"{APPROVED}/flows/.i0001-01.yml.123.tmp"
        part = f"{APPROVED}/doing/.i0001.md.abc.part"
        self.put(judge, "{}")
        self.put(temp, "x")
        self.put(part, "x")
        self.assertEqual(self.sort(), sorted([("keep", judge), ("skip", temp), ("skip", part)]))

    def test_committed_changes_since_a_revision(self):
        base = git(self.parent_tree, "rev-parse", "HEAD").strip()
        pending = f"{APPROVED}/phases/i0001/2.pending"
        reviewed = f"{APPROVED}/phases/i0001/1.reviewed"
        self.put(pending, json.dumps({"review": "mr", "at": "t"}))
        self.put(reviewed, json.dumps({"by": "chat", "at": "t"}))
        self.put("src/code.py", "x = 1\n")
        self.commit_parent("unsent")
        self.assertEqual(self.sort(base), sorted([("b", pending), ("c", reviewed)]))
        # 未コミットの judge.json は keep だが、コミット済みの未送信なら見分けない（d）。
        judge = f"{APPROVED}/phases/i0001/i0001-01.judge.json"
        self.put(judge, "{}")
        self.commit_parent("judge")
        self.assertIn(("d", judge), self.sort(base))

    def test_a_bad_revision_is_refused(self):
        result = self.ccnavi("c1", "sort", "i0001", "HEAD --output=/tmp/x")
        self.assertEqual(result.returncode, 1)


class RecordTreeTest(AuthorityHarness):
    def setUp(self):
        super().setUp()
        self.commit_parent("settle")

    def place(self):
        return os.path.join(self.root, "logs", "state", "c1", "self")

    def run_recorded(self, *args):
        target = os.path.join(self.place(), "i0001.t.writes")
        result = self.ccnavi("--record-writes", target, "--record-tree", self.parent_tree, *args)
        listed = []
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as f:
                listed = [line for line in f.read().splitlines() if line]
        return result, listed

    def test_the_list_is_relative_to_the_parent_tree(self):
        self.worktree("i0001-02", "i0001")
        result, listed = self.run_recorded("ticket", "cancel", "i0001-01", "--reason", "r")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{APPROVED}/done/i0001-01.md", listed)
        self.assertIn(f"{APPROVED}/doing/i0001-01.md", listed)
        self.assertTrue(all(not os.path.isabs(p) for p in listed), listed)

    def test_writing_outside_the_places_is_an_error_and_the_list_is_kept(self):
        # 承認は提案（wip/proposals/todo/）を消す。C1 の置き場の外なので error。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/research/*"]))
        self.commit_parent("propose")
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        result, listed = self.run_recorded(
            "--approve", "--yes", "i0001-02", "--digest", preview["digest"], "--json"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("置き場の外に書いた", result.stderr)
        self.assertIn("wip/proposals/todo/i0001-02.md", listed)
        self.assertIn(f"{APPROVED}/doing/i0001-02.md", listed)

    def test_record_tree_needs_record_writes(self):
        result = self.ccnavi("--record-tree", self.parent_tree, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--record-writes と一緒に", result.stderr)


class HumanEntryGuardTest(AuthorityHarness):
    def test_the_human_entries_are_denied_to_the_agent(self):
        for sub in ("chat 1", "config-synced i0001", "close-early --reason r"):
            command = f"sh .ccnavi/scripts/ccnavi-review.sh {sub}"
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
        # 依頼・確認・Draft 外しはエージェントの道のまま（止めない）。
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
