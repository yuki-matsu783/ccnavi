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
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_sync_authority import AuthorityHarness
from tests.ticket.test_ticket import git, read_json, write

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
        git(self.root, "remote", "add", "origin", os.path.join(self.root, "nowhere.git"))
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
        self.last_why = [line[4:] for line in result.stdout.splitlines() if line.startswith("why ")]
        return sorted(
            tuple(line.split(" ", 1))
            for line in result.stdout.splitlines()[1:]
            if not line.startswith("why ")
        )

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
        self.append_event(
            {"at": "t", "ticket": "i0001", "kind": "phase-mark", "phase": 2, "mark": "pending"}
        )
        self.assertEqual(
            self.sort(), sorted([("b", pending), ("b", skipped), ("b", self.events())])
        )

    def test_b_is_only_the_hook_marks_of_this_family(self):
        """跡は親の phase-mark の pending・skipped だけ。

        別の親・別の種類・全角の数字・新しい跡のファイルは (b) にしない。
        """
        self.append_event(
            {"at": "t", "ticket": "i0001", "kind": "phase-mark", "phase": 1, "mark": "reviewed"}
        )
        self.assertEqual(self.sort(), [("c", self.events())])
        git(self.parent_tree, "checkout", "--", self.events())
        self.append_event(
            {"at": "t", "ticket": "i0099", "kind": "phase-mark", "phase": 1, "mark": "pending"}
        )
        self.assertEqual(self.sort(), [("c", self.events())])
        git(self.parent_tree, "checkout", "--", self.events())
        other = f"{APPROVED}/phases/i0099/2.pending"
        wide = f"{APPROVED}/phases/i0001/２.pending"
        fresh = f"{APPROVED}/events/i0099.ndjson"
        self.put(other, json.dumps({"review": "mr", "at": "t"}))
        self.put(wide, json.dumps({"review": "mr", "at": "t"}))
        self.put(fresh, json.dumps({"at": "t", "ticket": "i0099", "kind": "phase-mark"}) + "\n")
        found = dict((path, kind) for kind, path in self.sort())
        self.assertEqual(found[other], "d")
        self.assertEqual(found[wide], "d")
        self.assertEqual(found[fresh], "d")

    def test_an_event_line_that_is_not_utf8_says_why(self):
        path = os.path.join(self.parent_tree, *self.events().split("/"))
        with open(path, "ab") as f:
            f.write(b'{"at": "t", "ticket": "i0001", "kind": "\xff"}\n')
        self.assertEqual(self.sort(), [("d", self.events())])
        self.assertTrue(any("UTF-8" in why for why in self.last_why), self.last_why)

    def test_crlf_in_the_working_tree_still_reads_as_an_append(self):
        """autocrlf で作業ツリーの跡だけが CRLF でも、hook の追記は (b)。"""
        path = os.path.join(self.parent_tree, *self.events().split("/"))
        with open(path, "rb") as f:
            body = f.read()
        row = {"at": "t", "ticket": "i0001", "kind": "phase-mark", "phase": 2, "mark": "pending"}
        with open(path, "wb") as f:
            f.write(body.replace(b"\n", b"\r\n") + json.dumps(row).encode() + b"\n")
        self.assertEqual(self.sort(), [("b", self.events())])

    def test_moves_written_by_a_human_decision_are_c(self):
        """人のレビュー（review/ から done/）と締め（doing/ から done/）、マーカーの消去は (c)。"""
        review = "wip/proposals/review/i0001-01.md"
        self.put(review, child_text("i0001-01", "i0001", 1, ["wip/research/*"]))
        mark = f"{APPROVED}/phases/i0001/1.pending"
        self.put(mark, json.dumps({"review": "mr", "at": "t"}))
        self.commit_parent("in review")
        os.remove(os.path.join(self.parent_tree, *review.split("/")))
        os.remove(os.path.join(self.parent_tree, *mark.split("/")))
        done = f"{APPROVED}/done/i0001-01.md"
        self.put(done, child_text("i0001-01", "i0001", 1, ["wip/research/*"]))
        found = dict((path, kind) for kind, path in self.sort())
        self.assertEqual(found, {review: "c", done: "c", mark: "c"})

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
        self.assertIn("置き場の外に書き込みがあった", result.stderr)
        self.assertIn("wip/proposals/todo/i0001-02.md", listed)
        self.assertIn(f"{APPROVED}/doing/i0001-02.md", listed)

    def test_record_tree_needs_record_writes(self):
        result = self.ccnavi("--record-tree", self.parent_tree, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--record-writes と一緒に", result.stderr)


class HumanEntryGuardTest(AuthorityHarness):
    def test_the_human_entries_are_denied_to_the_agent(self):
        for command in (
            "sh .ccnavi/scripts/ccnavi-review.sh chat 1",
            "sh .ccnavi/scripts/ccnavi-review.sh config-synced i0001",
            "sh .ccnavi/scripts/ccnavi-review.sh close-early --reason r",
            # シェルに選択肢を付けた形も同じ（段階 2d のレビュー）
            "sh -x .ccnavi/scripts/ccnavi-push-approved.sh",
            "bash -e -x .ccnavi/scripts/ccnavi-review.sh chat 1",
            "sh -x .ccnavi/scripts/ccnavi-sync.sh --forget i0001",
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
        # 依頼・確認・Draft 外しはエージェントの経路のまま（止めない）。
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
    """本物の実行ファイルで、`--record-tree` 付きの依頼・行き先・Draft 外しが置き場だけを書く。

    PhaseHarness は控えの置き場を `--state` で動かしている（上書きした置き場）。下書きはそこへ
    書かれ、一覧にも置き場の外にも数えない（段階 2d のレビューの 7）。
    """

    WRITERS = ("requested", "confirm", "ready")

    def setUp(self):
        super().setUp()
        self.recorded = []

    def ccnavi(self, *args, stdin=""):
        writes = any(a in args for a in self.WRITERS) or ("--yes" in args and "--reviewed" in args)
        if not writes:
            return super().ccnavi(*args, stdin=stdin)
        target = os.path.join(self.root, "logs", "state", "c1", "self", "r.writes")
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
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        shown = super().ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--accept-unresolved",
            "--preview", "--json", "--result", fixture,
        )  # fmt: skip
        digest = json.loads(shown.stdout)["digest"]
        decided = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--accept-unresolved",
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
        self.assertTrue(
            read_json(os.path.join(self.approved, "phases", "i0001", "ready.json"))["mr"]
        )
        self.assertEqual(len(self.recorded), 3)
        self.places_only()


class ChooseTest(PhaseHarness):
    """対話の decide の前半（`--choose-out`）: 選んで書くだけで、置き場に何も置かない（決定 A）。"""

    def test_choose_writes_only_the_choices(self):
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
        self.commit_parent("requested")
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "x"}]
        data["reviews"] = []
        write(fixture, json.dumps(data))
        out = os.path.join(self.state, "choose.json")
        chosen = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--accept-unresolved",
            "--choose-out", out, "--result", fixture, stdin="k\n",
        )  # fmt: skip
        self.assertEqual(chosen.returncode, 0, chosen.stdout + chosen.stderr)
        answer = read_json(out)
        self.assertEqual(answer["choices"], {"u/7#t0": "keep"})
        status = git(self.parent_tree, "status", "--porcelain")
        self.assertEqual(status.strip(), "")
        outside = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--accept-unresolved",
            "--choose-out", os.path.join(self.root, "x.json"), "--result", fixture, stdin="k\n",
        )  # fmt: skip
        self.assertEqual(outside.returncode, 1)
        done = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "1", "--accept-unresolved",
            "--yes", json.dumps(answer["choices"]), "--digest", answer["digest"],
            "--json", "--result", fixture,
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class BypassTest(AuthorityHarness):
    """C1 の対象の家族（取り込み済みで origin がある）では、C1 を通らない状態の操作を断る。"""

    def setUp(self):
        super().setUp()
        self.record("present")
        git(self.root, "remote", "add", "origin", os.path.join(self.root, "nowhere.git"))

    def test_state_operations_without_record_tree_are_refused(self):
        before = self.ccnavi("c1", "family", "i0001")
        self.assertIn("target yes", before.stdout)
        refused = self.ccnavi("ticket", "finish", "i0001-01")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("C1 の対象", refused.stderr)
        fixture = write(os.path.join(self.root, "r.json"), json.dumps({"host": "x"}))
        for args in (
            ("review", "confirm", "--phase", "1", "--result", fixture),
            ("review", "ready", "--result", fixture),
            ("--reviewed", "1", "--accept-unresolved", "--result", fixture),
        ):
            result = self.ccnavi("--cwd", self.parent_tree, *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("C1 の対象", result.stderr, args)

    def test_record_risk_and_human_entries_are_not_refused_here(self):
        risk = self.ccnavi("ticket", "record-risk", "i0001-01", "x", "yes", "--reason", "r")
        self.assertNotIn("C1 の対象", risk.stderr)
        chat = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat")
        self.assertNotIn("C1 の対象", chat.stderr)
