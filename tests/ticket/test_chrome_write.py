"""Chrome の承認の入口（go/cmd/ccnavi-chrome）の受入テスト。

見るのは 6 つ。

1. 親のワークツリー相当: ホストに無い親子のチケットは仮のワークツリーが組まれず、その親子の
   チケットは「親のワークツリーがありません」で決まらない。判定の入力に読めない
   （バイナリの）ファイルがあれば止める
2. 版ずれ: 統合先の互換のマーカーが違えば、書く操作（見せたものつきの plan）を
   受けない
3. 見せた一覧とダイジェスト: 違えば書くものを出さない
4. 書く先は親のブランチ `P` だけ。予約の名前・統合先の名前へは書かない
5. マーカーに経路（chrome）・アカウントが入る
6. 手元の ccnavi が、Chrome の書いた承認済みチケットを同じに読む（判定し直しで error が出ない）

書く先の名前の断り・ホストから来たパスの断り・パスの書き換えは go の entry/chrome のテストが見る。
"""

from __future__ import annotations

import json
import os

from tests import exe
from tests.ticket.test_swap_points import CoreHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import Chrome, child_text, load_chrome, parent_text

ACTOR = {"account": "alice", "version": "9.9.9"}


class ChromeWriteHarness(CoreHarness):
    def setUp(self):
        super().setUp()
        self.compat(exe.compat())

    def compat(self, value):
        write(
            os.path.join(self.root, *Chrome.COMPAT_FILE.split("/")),
            f"#!/bin/sh\nCCNAVI_COMPAT={value}\n",
        )

    def answer(self, request):
        """Chrome の入口の答え（error も含めてそのまま）。"""
        return json.loads(
            load_chrome().handle(json.dumps(request), os.path.join(self.root, "memfs"))
        )

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


class RecordsTest(ChromeWriteHarness):
    def test_an_absent_family_stops_and_its_stale_copy_is_undecided(self):
        """P の上に古いチケット（閉じた i0009-01-01）があっても、
        i0009 がホストに無ければ決まらない。"""
        self.family(plan=["research"])
        text = child_text("i0009-01-01", "i0009", 1, ["wip/research/*"], False).replace(
            'completed_at: ""', 'completed_at: "2026-01-01T00:00:00+0000"'
        )
        write(os.path.join(self.approved, "done", "i0009-01-01.md"), text)
        child = child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False).replace(
            "human_review:", 'predecessors: ["i0009-01-01"]\nhuman_review:', 1
        )
        self.propose("i0001-01-01", child)
        self.commit_parent()
        request = self.chrome_request("plan", "i0001")
        request["snapshot"]["absent"] = ["i0009"]
        body = self.answer(request)
        self.assertNotIn("error", body, body)
        self.assertEqual(body["identifiers"], [])
        rejected = {r["ticket"]: r["problems"] for r in body["rejected"]}
        self.assertTrue(
            any(
                "i0009" in p and "親のワークツリー（.claude/worktrees/i0009）がありません" in p
                for p in rejected["i0001-01-01"]
            ),
            rejected,
        )

    def test_a_requested_family_missing_from_the_snapshot_is_refused(self):
        """要求した親子のブランチ自体が snapshot の branches に無ければ、偽のツリーを組まずに断る。

        記録（gone）を足さなくても、拡張の側で止まる。ホストに無いと分かっていても同じ。
        """
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        for absent in ([], ["i0003"]):
            request = self.chrome_request("plan", "i0003")
            request["snapshot"]["absent"] = absent
            for op in ("board", "plan"):
                with self.subTest(op=op, absent=absent):
                    body = self.answer({**request, "op": op})
                    self.assertIn("family のブランチが snapshot にありません", body["error"])
                    self.assertNotIn("identifiers", body)

    def test_an_unreadable_input_stops_the_board_and_the_plan(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        request = self.chrome_request("plan", "i0001")
        request["snapshot"]["branches"]["main"]["binary"] = [".ccnavi/approved/done/x.md"]
        self.assertIn("判定できません", self.answer(request)["error"])
        board = self.answer({**request, "op": "board"})
        self.assertIn("バイナリ", board["undecided"])
        self.assertNotIn("batch", board)


class WriteGuardTest(ChromeWriteHarness):
    def test_a_different_compat_refuses_writing_but_still_shows(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        first = self.preview()
        self.compat(int(exe.compat()) + 1)
        board = self.answer(self.chrome_request("board", "i0001"))
        self.assertFalse(board["write"]["allowed"])
        self.assertIn("拡張を更新してください。", board["write"]["reason"])
        self.assertEqual([e["ticket"] for e in board["batch"]], ["i0001"])
        shown = {"ids": first["identifiers"], "digest": first["digest"]}
        refused = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIn("互換", refused["error"])

    def test_a_different_digest_writes_nothing(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        first = self.preview()
        shown = {"ids": first["identifiers"], "digest": "0" * 64}
        body = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIsNotNone(body["mismatch"])
        self.assertIsNone(body["changes"])
        self.assertEqual(body["code"], "CORE_APPROVE_DIGEST_CHANGED")
        # 見せた識別子が今の一覧と違えば、中身より先にそちらの種別
        shown = {"ids": ["i0001", "i0002"], "digest": first["digest"]}
        body = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIsNone(body["changes"])
        self.assertEqual(body["code"], "CORE_APPROVE_LIST_CHANGED")
        # 見せた後に提案が変われば、同じダイジェストでも書かない
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()
        shown = {"ids": first["identifiers"], "digest": first["digest"]}
        body = self.answer(self.chrome_request("plan", "i0001", shown=shown))
        self.assertIsNotNone(body["mismatch"])
        self.assertIsNone(body["changes"])
        self.assertEqual(body["code"], "CORE_APPROVE_DIGEST_CHANGED")

    def test_nothing_to_approve_carries_its_reason_code(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        body = self.answer(self.chrome_request("plan", "i0001"))
        self.assertIsNone(body["changes"])
        self.assertIsNone(body["mismatch"])
        self.assertEqual(body["code"], "CORE_APPROVE_NOTHING")


class WrittenCopyTest(ChromeWriteHarness):
    def test_the_local_ccnavi_reads_what_chrome_wrote_the_same(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        body = self.plan()
        self.assertEqual(body["identifiers"], ["i0001", "i0001-01-01"])
        self.apply(body["changes"]["i0001"], self.parent_tree)
        # 手元の判定し直し（C3）で error が出ない
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual(preview["batch"], [])
        explained = self.ccnavi("--lint", "--json")
        errors = [p for p in json.loads(explained.stdout)["problems"] if p["severity"] == "error"]
        self.assertEqual(errors, [])
        # 子のワークツリーを切って着手の判定が通る形（手元の続きの操作が読める）
        doing = os.path.join(self.approved, "doing")
        self.assertEqual(sorted(os.listdir(doing)), ["i0001-01-01.md", "i0001.md"])


class EntryDetailTest(ChromeWriteHarness):
    def test_every_writing_op_checks_compat_and_the_branch(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.compat(int(exe.compat()) + 1)
        for op, extra in (
            ("plan", {}),
            ("confirm", {"phase": 1, "result": {"host": "h", "mr": {"number": 1, "url": "u"}}}),
        ):
            body = self.answer(self.chrome_request(op, "i0001", **extra))
            self.assertIn("互換", body.get("error", ""), op)


class ProjectTest(ChromeWriteHarness):
    def test_a_proposal_naming_a_project_is_not_approved_from_the_workspace(self):
        """Chrome の承認はワークスペースのリポジトリだけ。プロジェクトの提案は承認しない。

        プロジェクトのリポジトリが拡張に登録されていなければ、プロジェクトが絡む提案は承認しない。
        """
        text = parent_text("i0001", ["research"]).replace(
            "ticket: i0001\n", "ticket: i0001\nproject: web\n", 1
        )
        self.propose("i0001", text)
        self.commit_parent()
        body = self.preview()
        self.assertEqual(body["identifiers"], [])
        self.assertEqual([r["ticket"] for r in body["rejected"]], ["i0001"])
