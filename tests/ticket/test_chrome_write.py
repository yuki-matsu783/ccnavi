"""Chrome の承認・取り下げ（ADR-0093 の段階 3）の Python の入口の受入テスト。

見るのは 7 つ。

1. 取り込みの控え相当: ホストに無く統合先でも閉じていない家族は `gone` で組み、その家族の
   写しは決まらない（3.3 の 3・5）。判定の入力に読めない（バイナリの）ファイルがあれば止める
2. 版ずれ（7.3・D31）: 統合先の互換のマーカーが違えば、書く操作（見せたものつきの plan・withdraw）を
   受けない
3. 見せた一覧と指紋（8.3 の 2）: 違えば書くものを出さない
4. 書く先は親のブランチ `P` だけ（8.4）。予約の名前・統合先の名前へは書かない（8.5）
5. 跡の行に経路（chrome）・アカウント・拡張の版が入る（7.3・8.8）
6. 手元の ccnavi が、Chrome の書いた写しを同じに読む（判定し直しで error が出ない）。
   error が出れば、Chrome と手元の版の違いとして名指しする（7.3）
7. 取り下げの可否をボードに出す（8.8）
"""

from __future__ import annotations

import json
import os

from ccnavi.entry import lint, version
from ccnavi.infra import settings
from ccnavi.tickets import history
from tests.ticket.test_core import CoreHarness, _chrome
from tests.ticket.test_phases import child_text, parent_text
from tests.ticket.test_ticket import git, write

ACTOR = {"account": "alice", "version": "9.9.9"}


class ChromeWriteHarness(CoreHarness):
    def setUp(self):
        super().setUp()
        self.compat(version.COMPAT)

    def compat(self, value):
        write(
            os.path.join(self.root, *lint.SH_COMPAT_FILE.split(os.sep)),
            f"#!/bin/sh\nCCNAVI_COMPAT={value}\n",
        )

    def answer(self, request):
        """Chrome の入口の答え（error も含めてそのまま）。"""
        return json.loads(_chrome().handle(json.dumps(request), os.path.join(self.root, "memfs")))

    def preview(self, family="i0001"):
        body = self.answer(self.chrome_request("plan", family))
        self.assertNotIn("error", body, body)
        return body

    def plan(self, family="i0001", **extra):
        first = self.preview(family)
        shown = {"ids": first["identifiers"], "digest": first["digest"]}
        return self.answer(
            self.chrome_request(
                "plan", family, only=first["identifiers"], shown=shown, actor=ACTOR, **extra
            )
        )

    def apply(self, changes, tree):
        """Chrome の Changes を 1 コミットとしてツリーに書く（拡張の書き込みの代わり）。"""
        for row in changes:
            path = os.path.join(tree, *row["path"].split("/"))
            if row["op"] == "delete":
                os.remove(path)
            else:
                write(path, row["content"])
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ccnavi: 承認（Chrome 拡張）")

    def conf(self):
        conf, _ = settings.load(self.root)
        conf.state = self.state
        conf.approved = ".ccnavi/approved"
        return conf


class RecordsTest(ChromeWriteHarness):
    def test_an_absent_family_is_gone_and_its_stale_copy_is_undecided(self):
        """P の上に古い写し（閉じた i0009-01）があっても、i0009 がホストに無ければ決まらない。"""
        self.family(plan=["research"])
        text = child_text("i0009-01", "i0009", 1, ["wip/research/*"], False).replace(
            'completed_at: ""', 'completed_at: "2026-01-01T00:00:00+0000"'
        )
        write(os.path.join(self.approved, "done", "i0009-01.md"), text)
        child = child_text("i0001-01", "i0001", 1, ["wip/research/*"], False).replace(
            "human_review:", 'predecessors: ["i0009-01"]\nhuman_review:', 1
        )
        self.propose("i0001-01", child)
        self.commit_parent()
        request = self.chrome_request("plan", "i0001")
        request["snapshot"]["absent"] = ["i0009"]
        self.mirror_records(_chrome(), request)
        body = self.answer(request)
        self.assertNotIn("error", body, body)
        self.assertEqual(body["identifiers"], [])
        rejected = {r["ticket"]: r["problems"] for r in body["rejected"]}
        self.assertTrue(any("i0009" in p and "gone" in p for p in rejected["i0001-01"]), rejected)
        records = _chrome().records(
            request["snapshot"], _chrome()._placement(None), ["i0001", "i0009"]
        )
        self.assertIn("state gone", records["sync/self/families/i0009"])
        self.assertIn("state present", records["sync/self/families/i0001"])
        # 先頭の sha は控えに書かない（指紋が関係の無い push で変わらないように。6.2）
        self.assertTrue(all("sha" not in text for text in records.values() if "\n" in text))

    def test_an_unreadable_input_stops_the_board_and_the_plan(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        request = self.chrome_request("plan", "i0001")
        request["snapshot"]["branches"]["main"]["binary"] = [".ccnavi/approved/done/x.md"]
        self.assertIn("決まらない", self.answer(request)["error"])
        board = self.answer({**request, "op": "board"})
        self.assertIn("バイナリ", board["undecided"])
        self.assertNotIn("batch", board)


class WriteGuardTest(ChromeWriteHarness):
    def test_a_different_compat_refuses_writing_but_still_shows(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        first = self.preview()
        self.compat(version.COMPAT + 1)
        board = self.answer(self.chrome_request("board", "i0001"))
        self.assertFalse(board["write"]["allowed"])
        self.assertIn("拡張を更新する", board["write"]["reason"])
        self.assertEqual([e["ticket"] for e in board["batch"]], ["i0001"])
        shown = {"ids": first["identifiers"], "digest": first["digest"]}
        refused = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIn("7.3", refused["error"])
        withdraw = self.answer(
            self.chrome_request("withdraw", "i0001", ids=["i0001"], prior={"i0001": "x"})
        )
        self.assertIn("7.3", withdraw["error"])

    def test_a_different_digest_writes_nothing(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        first = self.preview()
        shown = {"ids": first["identifiers"], "digest": "0" * 64}
        body = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIsNotNone(body["mismatch"])
        self.assertIsNone(body["changes"])
        # 見せた後に提案が変われば、同じ指紋でも書かない
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()
        shown = {"ids": first["identifiers"], "digest": first["digest"]}
        body = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIsNotNone(body["mismatch"])
        self.assertIsNone(body["changes"])

    def test_writes_only_to_the_parent_branch(self):
        chrome = _chrome()
        chrome._one_branch({"changes": {"i0001": []}}, "i0001")
        with self.assertRaises(chrome.Refused):
            chrome._one_branch({"changes": {"i0001": [], "i0003": []}}, "i0001")
        with self.assertRaises(chrome.Refused):
            chrome._one_branch({"changes": {"main": []}}, "i0001")

    def test_reserved_names_and_the_integration_are_not_written(self):
        chrome = _chrome()
        compat = f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n"
        snap = {
            "integration": {"name": "trunk", "source": "setting", "head": "0" * 40},
            "branches": {"trunk": {"head": "0" * 40, "files": {chrome.COMPAT_FILE: compat}}},
        }
        self.assertEqual(chrome._write_refusal(snap, "i0001"), "")
        for name in ("main", "Develop", "release-1", "trunk", "TRUNK"):
            self.assertIn("予約の名前か統合先の名前", chrome._write_refusal(snap, name), name)


class WrittenCopyTest(ChromeWriteHarness):
    def test_the_event_carries_chrome_the_account_and_the_version(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        body = self.plan()
        self.assertIsNone(body["mismatch"])
        self.assertEqual(sorted(body["changes"]), ["i0001"])
        events = next(
            r for r in body["changes"]["i0001"] if r["path"].endswith("events/i0001.ndjson")
        )
        line = json.loads(events["content"].splitlines()[-1])
        self.assertEqual(
            {k: line[k] for k in ("kind", "via", "actor", "version")},
            {"kind": "approved", "via": "chrome", "actor": "alice", "version": "9.9.9"},
        )

    def test_the_local_ccnavi_reads_what_chrome_wrote_the_same(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        body = self.plan()
        self.assertEqual(body["identifiers"], ["i0001", "i0001-01"])
        self.apply(body["changes"]["i0001"], self.parent_tree)
        # 手元の控えは Chrome と同じ（取り込み済みの家族）。判定し直し（C3）で error が出ない
        self.assertEqual(lint.family_check(self.conf(), self.root, "i0001", "self"), [])
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual(preview["batch"], [])
        explained = self.ccnavi("--lint", "--json")
        errors = [p for p in json.loads(explained.stdout)["problems"] if p["severity"] == "error"]
        self.assertEqual(errors, [])
        events, _ = history.read(self.approved, "i0001-01")
        self.assertEqual(events[-1]["via"], "chrome")
        # 子のワークツリーを切って着手の判定が通る形（手元の続きの操作が読める）
        doing = os.path.join(self.approved, "doing")
        self.assertEqual(sorted(os.listdir(doing)), ["i0001-01.md", "i0001.md"])

    def test_a_disagreement_names_the_chrome_and_local_versions(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        body = self.plan()
        self.apply(body["changes"]["i0001"], self.parent_tree)
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        write(copy, text.replace("  - research", "  - no-such-phase", 1))
        problems = lint.family_check(self.conf(), self.root, "i0001", "self")
        self.assertTrue(problems, "壊した写しは判定し直しで error になる")
        self.assertTrue(
            problems[0].detail.startswith(f"Chrome 9.9.9 と手元 {version.VERSION} で判定が違う: "),
            problems[0].detail,
        )

    def test_a_later_local_touch_is_not_blamed_on_chrome(self):
        """Chrome の承認の後に手元の跡（着手など）があれば、違いを Chrome の版のせいにしない。"""
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        body = self.plan()
        self.apply(body["changes"]["i0001"], self.parent_tree)
        events = os.path.join(self.approved, "events", "i0001.ndjson")
        with open(events, "a", encoding="utf-8") as f:
            f.write(
                json.dumps({"at": "x", "ticket": "i0001", "kind": "started", "via": "cli"}) + "\n"
            )
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        write(copy, text.replace("  - research", "  - no-such-phase", 1))
        problems = lint.family_check(self.conf(), self.root, "i0001", "self")
        self.assertTrue(problems)
        self.assertFalse(any(p.detail.startswith("Chrome ") for p in problems), problems)


class EntryDetailTest(ChromeWriteHarness):
    def test_relative_folds_only_at_the_head_of_a_path(self):
        chrome = _chrome()
        self.assertEqual(chrome._relative("/ws", "wip/ws/todo/i0001.md"), "wip/ws/todo/i0001.md")
        self.assertEqual(
            chrome._relative("/ws", "読めない: /ws/.claude/worktrees/i0001/wip/ws/x.md"),
            "読めない: i0001:wip/ws/x.md",
        )
        self.assertEqual(chrome._relative("/ws", "'/ws/a' と a/ws/b"), "'a' と a/ws/b")

    def test_a_history_that_cannot_be_written_stops_the_plan(self):
        chrome = _chrome()
        chrome._unwritten("/ws", "")
        with self.assertRaises(chrome.Refused) as caught:
            chrome._unwritten("/ws", "ccnavi: 警告: i0001 の履歴を /ws/x に書けない\n")
        self.assertEqual(str(caught.exception), "ccnavi: i0001 の履歴を x に書けない")

    def test_every_writing_op_checks_compat_and_the_branch(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.compat(version.COMPAT + 1)
        for op, extra in (
            ("plan", {}),
            ("withdraw", {"ids": ["i0001"], "prior": {}}),
            ("confirm", {"phase": 1, "result": {"host": "h", "mr": {"number": 1, "url": "u"}}}),
        ):
            body = self.answer(self.chrome_request(op, "i0001", **extra))
            self.assertIn("7.3", body.get("error", ""), op)


class WithdrawableTest(ChromeWriteHarness):
    def test_the_board_says_which_copies_can_be_withdrawn(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        board = self.answer(self.chrome_request("board", "i0001"))
        self.assertEqual(
            board["withdrawable"],
            [{"ticket": "i0001", "title": board["withdrawable"][0]["title"], "problems": []}],
        )
        self.start_parent()
        self.commit_parent("started")
        board = self.answer(self.chrome_request("board", "i0001"))
        problems = board["withdrawable"][0]["problems"]
        self.assertTrue(any("着手済み" in p for p in problems), problems)


class ProjectTest(ChromeWriteHarness):
    def test_a_proposal_naming_a_project_is_not_approved_from_the_workspace(self):
        """段階 3 はワークスペースのリポジトリだけ。プロジェクトの提案は承認しない（3.3 の 7）"""
        text = parent_text("i0001", ["research"]).replace(
            "ticket: i0001\n", "ticket: i0001\nproject: web\n", 1
        )
        self.propose("i0001", text)
        self.commit_parent()
        body = self.preview()
        self.assertEqual(body["identifiers"], [])
        self.assertEqual([r["ticket"] for r in body["rejected"]], ["i0001"])
