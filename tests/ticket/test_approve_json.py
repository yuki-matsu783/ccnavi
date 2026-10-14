"""`--agree --preview --json` と `--agree --yes <識別子,…> --json`（承認の JSON）の受入テスト。

VS Code のボード拡張がオーバーレイで承認するための経路。設計 wip/design/approve-popup.md 2。
見るのは 6 つ。

1. `--preview` は一覧の本文と識別子、対象外の提案、読めない提案を JSON で返す。
   承認済みチケットは置かない。範囲の超過だけの子は一覧に載り `overflow[]` を持つ
   （設計 wip/design/approve-carry.md 3.3）
2. 承認待ちが無くても `--preview` は `batch: []` で exit 0
3. `--yes` に一覧と同じ識別子を渡すと承認済みチケットが置かれ、
   `prompt`（Claude Code に渡す文）が返る
4. `--yes` は指した識別子だけを、承認する時点の提案を通常の検査にかけて承認する。
   表示後に提案が変わっても、現在の内容が有効なら承認する。指した識別子が承認待ちに無い、
   現在の提案が検査を通らないときは、何も置かず exit 1（`mismatch` は返さない）
5. `--yes` は端末の壁を通らない。素の `--agree` は今までどおり壁で止まる
6. 拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/approve-*.json）と同じ形

形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket.test_board import _portable
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import ROOT, write
from tests.ticketsample import child_text, parent_text

FIXTURES = os.path.join(ROOT, "extensions", "vscode", "ccnavi-board", "test", "fixtures")

APPROVE_VERSION = 1


class ApproveJsonTest(PhaseHarness):
    def preview(self, *extra):
        result = self.ccnavi("--agree", "--preview", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def yes(self, tickets, *extra):
        """`--agree --yes <識別子,…> --json`。extra はフラグ。

        承認する識別子だけを渡す。プレビューは挟まない（見せたあとの提案の変化を調べないため）。
        """
        return self.ccnavi("--agree", "--yes", ",".join(tickets), "--json", *extra)

    def copy_exists(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def pending_parent_and_child(self):
        """親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()

    # ---- 1. preview は読むだけ

    def test_preview_lists_the_batch_and_does_not_place_copies(self):
        self.pending_parent_and_child()
        # 定義の範囲を超える子。超過は承認を拒まないので一覧に載り、overflow[] を持つ
        # （設計 approve-carry 3.3）。
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ("wip/design/*",)))
        # 計画に無い番号の子。形が不正なので、承認の対象にしない側に載る。
        self.propose("i0001-05-05", child_text("i0001-05-05", "i0001", 5, ("wip/research/*",)))
        # frontmatter の読めない提案。読めない提案の側に載る。
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        body = self.preview()
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(
            [b["ticket"] for b in body["batch"]], ["i0001", "i0001-01-01", "i0001-01-02"]
        )
        parent, child, beyond = body["batch"]
        self.assertIsNone(parent["parent"])
        self.assertIsNone(parent["phase"])
        self.assertFalse(parent["revision"])
        self.assertEqual(parent["overflow"], [])
        self.assertEqual(child["parent"], "i0001")
        self.assertEqual(child["phase"], 1)
        self.assertTrue(
            child["path"].replace("\\", "/").endswith("wip/proposals/todo/i0001-01-01.md")
        )
        self.assertEqual(child["overflow"], [])
        # 超えた項は文字列のリストで、定義の名前と「超えている」を含む。
        self.assertTrue(beyond["overflow"], beyond)
        self.assertTrue(all(isinstance(p, str) for p in beyond["overflow"]), beyond)
        self.assertTrue(
            any("超えている" in p and "調査" in p for p in beyond["overflow"]), beyond["overflow"]
        )
        self.assertIn("チケットの承認リクエスト: 3 件", body["text"])
        self.assertIn("== i0001-01-01", body["text"])
        self.assertIn("== i0001-01-02", body["text"])
        self.assertIn("編集対象としているが", body["text"])
        # rejected[] に残るのは形の不正な子だけ。
        self.assertEqual([r["ticket"] for r in body["rejected"]], ["i0001-05-05"])
        self.assertTrue(any("計画にありません" in p for p in body["rejected"][0]["problems"]))
        self.assertTrue(any("broken.md" in p for p in body["problems"]))
        # 見ただけ。承認済みチケットは置かれていない。
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01-01"))
        self.assertFalse(self.copy_exists("i0001-01-02"))

    def test_letter_case_alone_is_not_an_overflow(self):
        """表記の大文字小文字だけが定義の範囲と違う子は、超過にならない（overflow[] が空）。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("WIP/Research/*",), False)
        )
        self.commit_parent()

        body = self.preview()
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01-01"])
        self.assertEqual(body["batch"][1]["overflow"], [])
        self.assertEqual(body["rejected"], [])
        self.assertNotIn("編集対象としているが", body["text"])

    # ---- 2. 承認待ちが無い

    def test_preview_with_nothing_pending_is_an_empty_batch(self):
        body = self.preview()
        self.assertEqual(body["batch"], [])
        self.assertEqual(body["rejected"], [])
        self.assertIn("承認待ちのチケットはありません。", body["text"])

    # ---- 3. yes は一覧と一致すれば承認する

    def test_yes_with_the_shown_batch_places_copies_and_returns_a_prompt(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(body["approved"], ["i0001", "i0001-01-01"])
        self.assertEqual(len(body["copies"]), 2)
        self.assertTrue(self.copy_exists("i0001"))
        self.assertTrue(self.copy_exists("i0001-01-01"))
        # Claude Code に渡す文。承認された識別子と、後工程の進め方が入っている。
        self.assertIn("i0001", body["prompt"])
        self.assertIn("i0001-01-01", body["prompt"])
        self.assertIn("承認", body["prompt"])
        self.assertIn("ccnavi-ticket.sh start", body["prompt"])
        # 承認したので一覧は空になる。
        self.assertEqual(self.preview()["batch"], [])

    def test_yes_order_of_identifiers_does_not_matter(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001-01-01", "i0001"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001-01-01"))

    # ---- 4. 指した識別子だけを、今の提案で承認する

    def test_yes_approves_only_the_named_tickets(self):
        """親だけを指すと、子は承認されないまま承認待ちに残る。"""
        self.pending_parent_and_child()
        result = self.yes(["i0001"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["approved"], ["i0001"])
        self.assertTrue(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01-01"))
        self.assertEqual([b["ticket"] for b in self.preview()["batch"]], ["i0001-01-01"])

    def test_yes_does_not_approve_a_proposal_added_after_preview(self):
        """見せたあとに増えた提案は、指していなければ承認しない。"""
        self.pending_parent_and_child()
        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001", "i0001-01-01"])
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ("wip/research/*",)))
        self.commit_parent("added after preview")
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["approved"], ["i0001", "i0001-01-01"])
        self.assertTrue(self.copy_exists("i0001-01-01"))
        self.assertFalse(self.copy_exists("i0001-01-02"))
        self.assertEqual([b["ticket"] for b in self.preview()["batch"]], ["i0001-01-02"])

    def test_yes_approves_the_current_content_when_it_changed_after_preview(self):
        """見せたあとに提案の中身が変わっても、現在の内容が有効なら承認する（ダイジェストは見ない）。"""
        inside = "wip/research/*"
        beyond = "src/a/*"
        for label, old, new in (
            ("題", "title: 子 i0001-01-01", "title: 書き換えた題"),
            ("理由", "rationale: r", "rationale: 書き換えた理由"),
            ("範囲（上限の内側）", f'glob: "{inside}"', 'glob: "wip/research/sub/*"'),
            ("本文", "---\n\n本文\n", "---\n\n書き換えた本文\n"),
        ):
            with self.subTest(label):
                self.setUp()
                self.pending_parent_and_child()
                path = self.propose(
                    "i0001-01-01", child_text("i0001-01-01", "i0001", 1, (inside, beyond), False)
                )
                self.commit_parent()
                self.assertIn(beyond, self.preview()["text"])

                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertIn(old, text)
                write(path, text.replace(old, new, 1))
                self.commit_parent("edit after preview")

                result = self.yes(["i0001", "i0001-01-01"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("mismatch", json.loads(result.stdout))
                self.assertTrue(self.copy_exists("i0001"))
                # 置いたのは、書き換えたあとの中身。
                with open(
                    os.path.join(self.approved, "doing", "i0001-01-01.md"), encoding="utf-8"
                ) as f:
                    self.assertIn(new, f.read())

    def test_yes_refuses_an_identifier_that_left_the_pending_list(self):
        """指した識別子が承認待ちから消えていたら、別の提案を代わりに承認しない。"""
        self.pending_parent_and_child()
        self.assertEqual(self.preview()["batch"][1]["ticket"], "i0001-01-01")
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01-01.md"))
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ("wip/research/*",)))
        self.commit_parent("replaced after preview")
        result = self.yes(["i0001-01-01"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi: [CORE_APPROVE_NOTHING] ", result.stderr)
        self.assertIn("i0001-01-01", result.stderr)
        for name in ("i0001", "i0001-01-01", "i0001-01-02"):
            self.assertFalse(self.copy_exists(name), name)

    def test_yes_refuses_unknown_identifiers(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001", "i0001-01-01", "i9999"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("i9999", result.stderr)
        self.assertIn("ccnavi: [CORE_APPROVE_NOTHING] ", result.stderr)
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01-01"))

    def test_yes_refuses_when_a_named_proposal_became_invalid(self):
        """現在の提案が通常の検査を通らなければ、残りの指定も含めて何も承認しない。"""
        self.pending_parent_and_child()
        # 見せたあとで子の番号を計画に無いフェーズへ書き換える（形が不正になる）。
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01-01.md"))
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 5, ("wip/research/*",)))
        self.commit_parent("invalid after preview")
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi: [CORE_APPROVE_NOTHING] ", result.stderr)
        self.assertIn("i0001-01-01", result.stderr)
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01-01"))

    def test_yes_refuses_when_a_named_proposal_is_rejected_by_the_checks(self):
        """指した提案が承認の対象から外れる（計画に無い番号）と、残りだけを承認しない。"""
        self.pending_parent_and_child()
        self.propose("i0001-05-05", child_text("i0001-05-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent("rejected child")
        result = self.yes(["i0001", "i0001-01-01", "i0001-05-05"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi: [CORE_APPROVE_NOTHING] ", result.stderr)
        self.assertIn("計画にありません", result.stderr)
        self.assertIn("i0001-05-05", result.stderr)
        for name in ("i0001", "i0001-01-01", "i0001-05-05"):
            self.assertFalse(self.copy_exists(name), name)

    def test_yes_keeps_the_revision_filter_check(self):
        """親の改版を外して子だけを指すと、旧計画で検証されるので何も承認しない。"""
        path, _ = self.pending_revision()
        self.propose("i0001-03-01", child_text("i0001-03-01", "i0001", 3, ("wip/design/*",)))
        self.commit_parent("child of the revised phase")
        result = self.yes(["i0001-03-01"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("親 i0001 の改版が承認待ちなのに承認の対象にありません", result.stderr)
        self.assertFalse(self.copy_exists("i0001-03-01"))

    def test_yes_with_trailing_words_or_a_digest_is_a_usage_error(self):
        """承認の対象は `--yes` だけで指す。後ろの語と `--digest` は受けない。"""
        self.pending_parent_and_child()
        for args in (
            ("--agree", "--yes", "i0001", "--json", "i0001"),
            ("--agree", "--yes", "i0001", "--digest", "0" * 64, "--json"),
        ):
            with self.subTest(" ".join(args)):
                result = self.ccnavi(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(self.copy_exists("i0001"))

    def test_yes_and_preview_together_is_a_usage_error(self):
        self.pending_parent_and_child()
        result = self.ccnavi("--agree", "--preview", "--yes", "i0001", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.copy_exists("i0001"))

    def test_yes_without_json_prints_the_human_lines(self):
        self.pending_parent_and_child()
        result = self.ccnavi("--agree", "--yes", "i0001,i0001-01-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認した", result.stdout)
        self.assertTrue(self.copy_exists("i0001"))

    # ---- 4c. 表示後に変わった中身も、承認するときの中身が置かれる

    def pending_parent_with_issue_and_child(self, issue=4242):
        """`issue:` を持つ親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。"""
        parent = self.propose("i0001", parent_text("i0001", ["research", "design"], issue=issue))
        child = self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        return parent, child

    def pending_revision(self):
        """親 i0001 を承認したあと、計画を足した改版を提案したまま（未承認）にする。

        足すのは `acceptance`。`implement` は `acceptance` を要る（requires）ので、単独で
        足すと改版は拒まれて一括のチケットに入らない。
        """
        self.family(plan=("research", "design"))
        path = self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent("revise")
        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001"])
        self.assertTrue(shown["batch"][0]["revision"], shown["batch"][0])
        return path, shown

    def test_yes_places_the_issue_changed_after_preview(self):
        """見せたあとで親の `issue:` だけが変わっても、承認するときの中身が置かれる。"""
        parent, _ = self.pending_parent_with_issue_and_child(4242)
        self.preview()
        with open(parent, encoding="utf-8") as f:
            text = f.read()
        write(parent, text.replace("issue: 4242", "issue: 4343", 1))
        self.commit_parent("edit after preview")
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn("issue: 4343", f.read())

    def test_yes_applies_the_revision_as_it_is_now(self):
        """親の改版を見せたあとで改版の計画を書き換えても、現在の改版が検査を通れば承認する。"""
        path, _ = self.pending_revision()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        old = "  - {type: acceptance, after: [2]}\n"
        self.assertIn(old, text)
        write(path, text.replace(old, "  - {type: design, after: [2]}\n", 1))
        self.commit_parent("edit revision after preview")
        result = self.yes(["i0001"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn("- type: design\n  after:\n  - 2\n", f.read())

    def test_nul_in_the_markdown_body_still_approves(self):
        """本文に生の NUL があっても承認できる。"""
        self.pending_parent_and_child()
        child = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01-01.md")
        with open(child, encoding="utf-8") as f:
            text = f.read()
        write(child, text.replace("---\n\n本文\n", "---\n\n前\x00後\n", 1))
        self.commit_parent("nul in body")

        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001-01-01"))

    def test_preview_text_shows_the_issue_of_the_parent(self):
        """5. `issue:` を持つ親のプレビューの `text` に、その番号が出る。"""
        self.pending_parent_with_issue_and_child(4242)
        body = self.preview()
        self.assertIn("4242", body["text"])

    # ---- 5. 端末の壁

    def test_yes_does_not_need_a_terminal_but_plain_approve_still_does(self):
        self.pending_parent_and_child()
        # 壁を有効にしたまま。テストの標準入力は端末ではない。
        refused = self.ccnavi("--agree", "--guard-ticket-approval", "enable", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ターミナル", refused.stderr)
        self.assertFalse(self.copy_exists("i0001"))

        result = self.yes(["i0001", "i0001-01-01"], "--guard-ticket-approval", "enable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001"))

    def test_preview_does_not_need_a_terminal(self):
        self.pending_parent_and_child()
        body = self.preview("--guard-ticket-approval", "enable")
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01-01"])

    # ---- 6. フィクスチャ

    def test_shapes_match_the_extension_fixtures(self):
        self.pending_parent_and_child()
        # 定義の範囲を超える子は一覧に載り、`overflow` を持つ。計画に無い番号の子は
        # 承認の対象にしない側に載る。拡張は両方の形を読むので、同じ一覧に並べて書き出す。
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ("wip/design/*",)))
        self.propose("i0001-05-05", child_text("i0001-05-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent()
        preview = self.preview()
        self._check_fixture("approve-preview.json", preview)

        # 承認の答えは親と子 1 枚の形で書き出す。超過のある子は提案を下げてから承認する。
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01-02.md"))
        self.commit_parent()
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._check_fixture("approve-yes.json", json.loads(result.stdout))

    def test_yes_that_stops_partway_says_what_it_placed(self):
        """途中で書けなくなっても、置いたぶんを何も言わずに捨てない
        （README「承認の JSON」の partial）。

        置き場に同じ名前のディレクトリを作って、子の承認済みチケットだけ書けなくする。
        一括のチケットは親 → 子の順なので、親は置かれたあとに止まる。
        フィクスチャには入れない。理由の文面に OS の言い分（「ディレクトリです」など）が
        混じるので、機械によって変わる。
        """
        self.pending_parent_and_child()
        os.makedirs(os.path.join(self.approved, "doing", "i0001-01-01.md"))
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(body["partial"]["placed"], ["i0001"])
        self.assertEqual(body["partial"]["ticket"], "i0001-01-01")
        self.assertIn("保存できませんでした", body["partial"]["reason"])
        self.assertNotIn("approved", body)
        # 置いたものは戻さない。親は承認済みチケットに入ったまま。
        self.assertTrue(self.copy_exists("i0001"))
        # 標準エラーには止まったところが出る。
        self.assertIn("i0001-01-01", result.stderr)

    def test_partial_carries_the_progress_lines(self):
        """止まるまでに出た行も渡す。端末だけが知っていて拡張が知らない状態を作らない。

        レビュー済みのフェーズに子を 2 枚足し、2 枚目だけ書けなくする。1 枚目は置けるので
        そのフェーズのマーカーが消え、その行が `lines` に入る。
        """
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))

        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        self.propose("i0001-01-03", child_text("i0001-01-03", "i0001", 1, ["wip/design/*"]))
        self.commit_parent("propose 02 03")
        os.makedirs(os.path.join(self.approved, "doing", "i0001-01-03.md"))
        result = self.yes(["i0001-01-02", "i0001-01-03"])
        self.assertEqual(result.returncode, 1)
        partial = json.loads(result.stdout)["partial"]
        self.assertEqual(partial["placed"], ["i0001-01-02"])
        self.assertEqual(partial["ticket"], "i0001-01-03")
        self.assertTrue(
            any("マーカー" in line for line in partial["lines"]),
            partial["lines"],
        )

    def test_yes_that_stops_before_placing_anything_says_so(self):
        """1 件目で止まったら placed は空。「一部だけ置かれた」と言わせない。"""
        self.pending_parent_and_child()
        os.makedirs(os.path.join(self.approved, "doing", "i0001.md"))
        result = self.yes(["i0001", "i0001-01-01"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["partial"]["placed"], [])
        self.assertEqual(body["partial"]["ticket"], "i0001")
        self.assertFalse(self.copy_exists("i0001-01-01"))

    def _fixture(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            return json.load(f)

    # 走らせるたびに変わる欄。フィクスチャと比べるときは外す。
    VOLATILE = ("generated_at",)

    def _check_fixture(self, name, body):
        """拡張のフィクスチャと突き合わせる。鍵だけでなく値まで見る。

        鍵の集合だけを比べると、識別子や本文が入れ替わっても気づけない。
        機械に依らない形（`_portable`）に直したうえで、時刻の欄だけ外して比べる。
        """
        portable = _portable(body, self.root)
        if os.environ.get("CCNAVI_BOARD_FIXTURE"):
            # 時刻は固定の文字列で書く。走らせるたびに変わる欄をそのまま置くと、
            # 形が同じでもフィクスチャに差分が出る。
            stable = {k: ("<time>" if k in self.VOLATILE else v) for k, v in portable.items()}
            write(
                os.path.join(FIXTURES, name),
                json.dumps(stable, ensure_ascii=False, indent=1) + "\n",
            )
        fixture = self._fixture(name)
        self.assertEqual(sorted(fixture), sorted(portable))
        for key in sorted(portable):
            if key in self.VOLATILE:
                continue
            self.assertEqual(fixture[key], portable[key], key)


if __name__ == "__main__":
    unittest.main()
