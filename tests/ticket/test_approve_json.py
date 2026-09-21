"""`--approve --preview --json` と `--approve --yes <識別子,…> --json`（承認の JSON）の受入テスト。

VS Code のボード拡張がオーバーレイで承認するための経路。設計 wip/design/approve-popup.md §2。
見るのは 6 つ。

1. `--preview` は一覧の本文と識別子、対象外の提案、読めない提案を JSON で返す。
   承認済みチケットは置かない。範囲の超過だけの子は一覧に載り `overflow[]` を持つ
   （設計 wip/design/approve-carry.md §3.3）
2. 承認待ちが無くても `--preview` は `batch: []` で exit 0
3. `--yes` に一覧と同じ識別子を渡すと承認済みチケットが置かれ、
   `prompt`（Claude Code に渡す文）が返る
4. `--yes` の識別子が一覧と違えば承認済みチケットを置かず、`mismatch` で exit 1
5. `--yes` は端末の壁を通らない。素の `--approve` は今までどおり壁で止まる
6. 拡張側のフィクスチャ（vscode-extension/ccnavi-board/test/fixtures/approve-*.json）と同じ形

形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket.test_board import _portable
from tests.ticket.test_phases import PHASES, PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import ROOT, write

FIXTURES = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "test", "fixtures")

APPROVE_VERSION = 1


class ApproveJsonTest(PhaseHarness):
    def preview(self, *extra):
        result = self.ccnavi("--approve", "--preview", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def yes(self, tickets, *extra, digest=None):
        """`--approve --yes <識別子,…> --digest <指紋> --json [<絞り>...]`。extra は絞りかフラグ。

        拡張と同じく、直前に同じ extra でプレビューして、見せた本文の指紋（`digest`）を渡す。
        digest を名指しすると、その値をそのまま渡す（見せたあとに提案が変わった形）。
        """
        if digest is None:
            digest = self.preview(*extra)["digest"]
        return self.ccnavi(
            "--approve", "--yes", ",".join(tickets), "--digest", digest, "--json", *extra
        )

    def copy_exists(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def pending_parent_and_child(self):
        """親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

    # ---- 1. preview は読むだけ

    def test_preview_lists_the_batch_and_does_not_place_copies(self):
        self.pending_parent_and_child()
        # 種類の範囲を超える子。超過は承認を拒まないので一覧に載り、overflow[] を持つ
        # （設計 approve-carry §3.3）。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ("wip/design/*",)))
        # 計画に無い番号の子。形が壊れているので、承認の対象にしない側に載る。
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ("wip/research/*",)))
        # frontmatter の読めない提案。読めない提案の側に載る。
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        body = self.preview()
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01", "i0001-02"])
        parent, child, beyond = body["batch"]
        self.assertIsNone(parent["parent"])
        self.assertIsNone(parent["phase"])
        self.assertFalse(parent["revision"])
        self.assertEqual(parent["overflow"], [])
        self.assertEqual(child["parent"], "i0001")
        self.assertEqual(child["phase"], 1)
        self.assertTrue(child["path"].replace("\\", "/").endswith("wip/proposals/todo/i0001-01.md"))
        self.assertEqual(child["overflow"], [])
        # 超えた項は文字列の並びで、種類の名前と「超えている」を含む。
        self.assertTrue(beyond["overflow"], beyond)
        self.assertTrue(all(isinstance(p, str) for p in beyond["overflow"]), beyond)
        self.assertTrue(
            any("超えている" in p and "調査" in p for p in beyond["overflow"]), beyond["overflow"]
        )
        self.assertIn("チケットの承認リクエスト: 3 件", body["text"])
        self.assertIn("== i0001-01", body["text"])
        self.assertIn("== i0001-02", body["text"])
        self.assertIn("判定で止まる場所", body["text"])
        # rejected[] に残るのは形の壊れた子だけ。
        self.assertEqual([r["ticket"] for r in body["rejected"]], ["i0001-05"])
        self.assertTrue(any("計画に無い" in p for p in body["rejected"][0]["problems"]))
        self.assertTrue(any("broken.md" in p for p in body["problems"]))
        # 見ただけ。承認済みチケットは置かれていない。
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01"))
        self.assertFalse(self.copy_exists("i0001-02"))

    def test_letter_case_alone_is_not_an_overflow(self):
        """綴りの大文字小文字だけが種類の範囲と違う子は、超過にならない（overflow[] が空）。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("WIP/Research/*",), False))
        self.commit_parent()

        body = self.preview()
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01"])
        self.assertEqual(body["batch"][1]["overflow"], [])
        self.assertEqual(body["rejected"], [])
        self.assertNotIn("判定で止まる場所", body["text"])

    # ---- 2. 承認待ちが無い

    def test_preview_with_nothing_pending_is_an_empty_batch(self):
        body = self.preview()
        self.assertEqual(body["batch"], [])
        self.assertEqual(body["rejected"], [])
        self.assertIn("承認待ちのチケットは無い", body["text"])

    # ---- 3. yes は一覧と一致すれば承認する

    def test_yes_with_the_shown_batch_places_copies_and_returns_a_prompt(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001", "i0001-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(body["approved"], ["i0001", "i0001-01"])
        self.assertEqual(len(body["copies"]), 2)
        self.assertTrue(self.copy_exists("i0001"))
        self.assertTrue(self.copy_exists("i0001-01"))
        # Claude Code に渡す文。承認された識別子と、後工程の進め方が入っている。
        self.assertIn("i0001", body["prompt"])
        self.assertIn("i0001-01", body["prompt"])
        self.assertIn("承認", body["prompt"])
        self.assertIn("ccnavi-ticket.sh start", body["prompt"])
        # 承認したので一覧は空になる。
        self.assertEqual(self.preview()["batch"], [])

    def test_yes_order_of_identifiers_does_not_matter(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001-01", "i0001"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001-01"))

    # ---- 4. 見せた一覧と違えば承認しない

    def test_yes_refuses_when_the_batch_changed(self):
        self.pending_parent_and_child()
        # 拡張が見せたあとに子が 1 枚増えた形。
        result = self.yes(["i0001"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(body["mismatch"]["expected"], ["i0001"])
        self.assertEqual(body["mismatch"]["current"], ["i0001", "i0001-01"])
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01"))

    def test_a_filtered_overlay_approves_only_what_it_showed(self):
        """絞り込み中の承認。preview と yes に同じ絞りを渡し、見せた分だけを承認する。"""
        self.pending_parent_and_child()
        # 親だけに絞って見せる。
        body = self.preview("i0001")
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001"])
        # 同じ絞りを添えて承認する。子は承認されない。
        result = self.yes(["i0001"], "i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01"))
        # 絞りを添えずに同じことを頼めば、絞らない一覧と比べて食い違いになる。
        self.assertEqual(self.preview()["batch"][0]["ticket"], "i0001-01")

    def test_yes_without_the_filter_compares_against_the_whole_batch(self):
        """絞りを添えない `--yes` は、絞らない一覧と比べる。部分だけを黙って通さない。"""
        self.pending_parent_and_child()
        result = self.yes(["i0001"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["mismatch"]["current"], ["i0001", "i0001-01"])
        self.assertFalse(self.copy_exists("i0001"))

    def test_yes_refuses_unknown_identifiers(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001", "i0001-01", "i9999"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("i9999", json.loads(result.stdout)["mismatch"]["expected"])
        self.assertFalse(self.copy_exists("i0001"))

    def test_yes_and_preview_together_is_a_usage_error(self):
        self.pending_parent_and_child()
        result = self.ccnavi("--approve", "--preview", "--yes", "i0001", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.copy_exists("i0001"))

    def test_yes_without_json_prints_the_human_lines(self):
        self.pending_parent_and_child()
        digest = self.preview()["digest"]
        result = self.ccnavi("--approve", "--yes", "i0001,i0001-01", "--digest", digest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認した", result.stdout)
        self.assertTrue(self.copy_exists("i0001"))

    # ---- 4b. 見せた本文の指紋を照合する（チケット approve-carry-04 の 1〜5）

    def test_preview_carries_a_stable_digest_of_the_text(self):
        """1. preview の答えに `digest`（SHA-256 の 16 進 64 文字）が載る。

        覆う中身は本文に加えて、承認済みチケットとして書き出す中身（チケット approve-carry-05）。
        値の作り方は実装が決めるので、形と、同じ状態で同じ値になることだけを見る。
        """
        self.pending_parent_and_child()
        first = self.preview()
        self.assertRegex(first["digest"], r"^[0-9a-f]{64}$")
        # 同じ状態でもう一度見せても同じ値になる。
        self.assertEqual(self.preview()["digest"], first["digest"])

    def test_yes_with_the_shown_digest_approves(self):
        """2. 見せた `digest` を `--yes` に渡すと承認できる。"""
        self.pending_parent_and_child()
        shown = self.preview()["digest"]
        result = self.yes(["i0001", "i0001-01"], digest=shown)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["approved"], ["i0001", "i0001-01"])
        self.assertTrue(self.copy_exists("i0001-01"))

    def test_yes_refuses_when_the_shown_text_changed(self):
        """3. 見せたあとで子の中身を書き換えると、識別子が同じでも承認しない。"""
        inside = "wip/research/*"
        beyond = "src/a/*"
        for label, old, new in (
            ("題", "title: 子 i0001-01", "title: 書き換えた題"),
            ("理由", "rationale: r", "rationale: 書き換えた理由"),
            ("範囲（上限の内側）", f'glob: "{inside}"', 'glob: "wip/research/sub/*"'),
            ("範囲（上限の外）", f'glob: "{beyond}"', 'glob: "src/b/*"'),
        ):
            with self.subTest(label):
                self.setUp()
                self.pending_parent_and_child()
                path = self.propose(
                    "i0001-01", child_text("i0001-01", "i0001", 1, (inside, beyond), False)
                )
                self.commit_parent()
                shown = self.preview()
                self.assertIn(beyond, shown["text"])

                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertIn(old, text)
                write(path, text.replace(old, new, 1))
                self.commit_parent("edit after preview")

                result = self.yes(["i0001", "i0001-01"], digest=shown["digest"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                body = json.loads(result.stdout)
                self.assertEqual(body["version"], APPROVE_VERSION)
                mismatch = body["mismatch"]
                self.assertEqual(mismatch["expected"], ["i0001", "i0001-01"])
                self.assertEqual(mismatch["current"], ["i0001", "i0001-01"])
                self.assertEqual(mismatch["digest"]["expected"], shown["digest"])
                self.assertNotEqual(mismatch["digest"]["current"], shown["digest"])
                self.assertEqual(mismatch["digest"]["current"], self.preview()["digest"])
                self.assertFalse(self.copy_exists("i0001"))
                self.assertFalse(self.copy_exists("i0001-01"))

    def test_yes_without_a_digest_is_refused(self):
        """4. `--yes` に `--digest` が無ければ承認しない。誤りとして言う。"""
        self.pending_parent_and_child()
        for args in (
            ("--approve", "--yes", "i0001,i0001-01", "--json"),
            ("--approve", "--yes", "i0001,i0001-01"),
        ):
            with self.subTest(" ".join(args)):
                result = self.ccnavi(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("--digest", result.stderr)
                self.assertFalse(self.copy_exists("i0001"))
                self.assertFalse(self.copy_exists("i0001-01"))

    # ---- 4c. 指紋は承認済みチケットに写る欄も覆う（チケット approve-carry-05 の 1〜5）

    def pending_parent_with_issue_and_child(self, issue=4242):
        """`issue:` を持つ親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。"""
        parent = self.propose("i0001", parent_text("i0001", ["research", "design"], issue=issue))
        child = self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        return parent, child

    def assert_refused_after_edit(self, path, old, new, shown, tickets=("i0001", "i0001-01")):
        """見せたあとで path の old を new に書き換えてコミットすると、見せた指紋では承認しない。

        tickets は見せた束の識別子。承認済みチケットがまだ無いものは、置かれないことも見る。
        """
        tickets = list(tickets)
        missing = [name for name in tickets if not self.copy_exists(name)]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(old, text)
        write(path, text.replace(old, new, 1))
        self.commit_parent("edit after preview")

        result = self.yes(tickets, digest=shown["digest"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        mismatch = body["mismatch"]
        self.assertEqual(mismatch["expected"], tickets)
        self.assertEqual(mismatch["current"], tickets)
        self.assertEqual(mismatch["digest"]["expected"], shown["digest"])
        self.assertNotEqual(mismatch["digest"]["current"], shown["digest"])
        for name in missing:
            self.assertFalse(self.copy_exists(name), name)

    def test_yes_refuses_when_only_the_issue_of_the_parent_changed(self):
        """1. 見せたあとで親の `issue:` だけを書き換えると、見せた指紋では承認しない。"""
        parent, _ = self.pending_parent_with_issue_and_child(4242)
        shown = self.preview()
        self.assert_refused_after_edit(parent, "issue: 4242", "issue: 4343", shown)

    def test_yes_refuses_when_only_the_markdown_body_of_a_child_changed(self):
        """2. 見せたあとで子の Markdown の本文だけを書き換えても、見せた指紋では承認しない。"""
        self.pending_parent_and_child()
        child = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01.md")
        shown = self.preview()
        self.assert_refused_after_edit(child, "---\n\n本文\n", "---\n\n書き換えた本文\n", shown)

    # ---- 4d. 指紋は画面に出ない欄と改版で書く中身も覆う（チケット approve-carry-07）

    def test_yes_refuses_when_only_an_unknown_field_of_a_child_changed(self):
        """見せたあとで、承認画面に出ない frontmatter の欄だけを書き換えると承認しない。

        ccnavi の知らない欄（`note:`）は画面に出ないが、そのまま承認済みチケットに写る。
        """
        self.pending_parent_and_child()
        child = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01.md")
        with open(child, encoding="utf-8") as f:
            text = f.read()
        write(child, text.replace("rationale: r\n", "rationale: r\nnote: x\n", 1))
        self.commit_parent("unknown field")

        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001", "i0001-01"])
        self.assertNotIn("note", shown["text"])
        self.assert_refused_after_edit(child, "note: x", "note: y", shown)

    def pending_revision(self):
        """親 i0001 を承認したあと、計画を足した改版を提案したまま（未承認）にする。

        足すのは `acceptance`。`implement` は `acceptance` を要る（requires）ので、単独で
        足すと改版は拒まれて束に入らない。
        """
        self.family(plan=("research", "design"))
        path = self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent("revise")
        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001"])
        self.assertTrue(shown["batch"][0]["revision"], shown["batch"][0])
        return path, shown

    def test_yes_refuses_when_the_revision_proposal_changed(self):
        """親の改版を見せたあとで、改版の提案の計画を書き換えると、見せた指紋では承認しない。

        書き換えたあとも改版として成り立つ並び（足す項を `design` に）にして、束に残したまま
        書く計画だけを変える。
        """
        path, shown = self.pending_revision()
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            before = f.read()
        self.assert_refused_after_edit(
            path, "  - acceptance\n", "  - design\n", shown, tickets=["i0001"]
        )
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_yes_refuses_when_the_copy_under_revision_changed(self):
        """親の改版を見せたあとで、今の承認済みチケットの frontmatter だけが変わると承認しない。

        改版で書くのは、今の承認済みチケットの frontmatter の計画だけを差し替えた中身
        （`revise_copy`）。提案の frontmatter をそのまま指紋に入れる実装では、この書き換えを見逃す。
        """
        path, shown = self.pending_revision()
        copy = os.path.join(self.approved, "doing", "i0001.md")
        self.assert_refused_after_edit(copy, "---\n", "---\nnote: x\n", shown, tickets=["i0001"])

    def test_nul_in_the_markdown_body_still_approves(self):
        """本文に生の NUL があっても、見せた指紋で承認できる（指紋は区切りの文字に頼らない）。"""
        self.pending_parent_and_child()
        child = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01.md")
        with open(child, encoding="utf-8") as f:
            text = f.read()
        write(child, text.replace("---\n\n本文\n", "---\n\n前\x00後\n", 1))
        self.commit_parent("nul in body")

        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001", "i0001-01"])
        result = self.yes(["i0001", "i0001-01"], digest=shown["digest"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001-01"))

    def test_moving_a_nul_in_the_body_after_preview_is_refused(self):
        """見せたあとで本文の NUL の位置だけをずらすと、見せた指紋では承認しない。

        つなぎ目をずらす形の書き換え。区切りの文字でつないでいた頃の実装でもここは止まる
        （各部分が frontmatter から始まるため）ので、この確かめは振る舞いを固定するためのもの。
        """
        self.pending_parent_and_child()
        child = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-01.md")
        with open(child, encoding="utf-8") as f:
            text = f.read()
        write(child, text.replace("---\n\n本文\n", "---\n\n前\x00後\n", 1))
        self.commit_parent("nul in body")

        shown = self.preview()
        self.assert_refused_after_edit(child, "前\x00後\n", "前後\x00\n", shown)

    def test_digest_changes_when_only_the_overflow_changes(self):
        """3. 提案はそのままで、種類の scope が変わって子の超過が増えると、指紋が変わる。"""
        self.pending_parent_and_child()
        before = self.preview()
        self.assertEqual(before["batch"][1]["overflow"], [])

        # 種類 research の scope を、子の範囲を覆わない綴りに書き換える。
        narrowed = PHASES.replace('scope: ["wip/research/*"]', 'scope: ["wip/elsewhere/*"]', 1)
        self.assertNotEqual(narrowed, PHASES)
        write(self.phases, narrowed)

        after = self.preview()
        self.assertEqual(
            [b["ticket"] for b in after["batch"]], [b["ticket"] for b in before["batch"]]
        )
        self.assertTrue(after["batch"][1]["overflow"], after["batch"][1])
        self.assertNotEqual(after["digest"], before["digest"])

    def test_yes_accepts_the_shown_digest_in_upper_case(self):
        """4. 見せた `digest` を大文字にして `--yes` に渡しても承認できる。"""
        self.pending_parent_and_child()
        shown = self.preview()["digest"]
        result = self.yes(["i0001", "i0001-01"], digest=shown.upper())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["approved"], ["i0001", "i0001-01"])
        self.assertTrue(self.copy_exists("i0001"))
        self.assertTrue(self.copy_exists("i0001-01"))

    def test_preview_text_shows_the_issue_of_the_parent(self):
        """5. `issue:` を持つ親のプレビューの `text` に、その番号が出る。"""
        self.pending_parent_with_issue_and_child(4242)
        body = self.preview()
        self.assertIn("4242", body["text"])

    # ---- 5. 端末の壁

    def test_yes_does_not_need_a_terminal_but_plain_approve_still_does(self):
        self.pending_parent_and_child()
        # 壁を有効にしたまま。テストの標準入力は端末ではない。
        refused = self.ccnavi("--approve", "--guard-ticket-approval", "enable", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("端末", refused.stderr)
        self.assertFalse(self.copy_exists("i0001"))

        result = self.yes(["i0001", "i0001-01"], "--guard-ticket-approval", "enable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001"))

    def test_preview_does_not_need_a_terminal(self):
        self.pending_parent_and_child()
        body = self.preview("--guard-ticket-approval", "enable")
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01"])

    # ---- 6. フィクスチャ

    def test_shapes_match_the_extension_fixtures(self):
        self.pending_parent_and_child()
        # 種類の範囲を超える子は一覧に載り、`overflow` を持つ。計画に無い番号の子は
        # 承認の対象にしない側に載る。拡張は両方の形を読むので、同じ一覧に並べて写す。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ("wip/design/*",)))
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent()
        preview = self.preview()
        self._check_fixture("approve-preview.json", preview)

        # 承認の答えは親と子 1 枚の形で写す。超過のある子は提案を下げてから承認する。
        os.remove(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001-02.md"))
        self.commit_parent()
        result = self.yes(["i0001", "i0001-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._check_fixture("approve-yes.json", json.loads(result.stdout))

        # 食い違いの形も拡張が読むので、同じく写す。
        self.propose("i0001-03", child_text("i0001-03", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        result = self.yes(["i0001-09"])
        self.assertEqual(result.returncode, 1)
        self._check_fixture("approve-mismatch.json", json.loads(result.stdout))

    def test_yes_that_stops_partway_says_what_it_placed(self):
        """途中で書けなくなっても、置いたぶんを黙って捨てない（README「承認の JSON」の partial）。

        置き場に同じ名前のディレクトリを作って、子の承認済みチケットだけ書けなくする。
        束は親 → 子の順なので、親は置かれたあとに止まる。
        フィクスチャには入れない。理由の文面に OS の言い分（「ディレクトリです」など）が
        混じるので、機械によって変わる。
        """
        self.pending_parent_and_child()
        os.makedirs(os.path.join(self.approved, "doing", "i0001-01.md"))
        result = self.yes(["i0001", "i0001-01"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual(body["partial"]["placed"], ["i0001"])
        self.assertEqual(body["partial"]["ticket"], "i0001-01")
        self.assertIn("書けない", body["partial"]["reason"])
        self.assertNotIn("approved", body)
        # 置いたものは戻さない。親は承認済みチケットに入ったまま。
        self.assertTrue(self.copy_exists("i0001"))
        # 標準エラーには止まったところが出る。
        self.assertIn("i0001-01", result.stderr)

    def test_partial_carries_the_progress_lines(self):
        """止まるまでに出た行も渡す。端末だけが知っていて拡張が知らない状態を作らない。

        レビュー済みのフェーズに子を 2 枚足し、2 枚目だけ書けなくする。1 枚目は置けるので
        そのフェーズのマーカーが消え、その行が `lines` に入る。
        """
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.check(fixture, 1).returncode, 0)
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))

        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/design/*"]))
        self.propose("i0001-03", child_text("i0001-03", "i0001", 1, ["wip/design/*"]))
        self.commit_parent("propose 02 03")
        os.makedirs(os.path.join(self.approved, "doing", "i0001-03.md"))
        result = self.yes(["i0001-02", "i0001-03"])
        self.assertEqual(result.returncode, 1)
        partial = json.loads(result.stdout)["partial"]
        self.assertEqual(partial["placed"], ["i0001-02"])
        self.assertEqual(partial["ticket"], "i0001-03")
        self.assertTrue(
            any("マーカー" in line for line in partial["lines"]),
            partial["lines"],
        )

    def test_yes_that_stops_before_placing_anything_says_so(self):
        """1 件目で止まったら placed は空。「一部だけ置かれた」と言わせない。"""
        self.pending_parent_and_child()
        os.makedirs(os.path.join(self.approved, "doing", "i0001.md"))
        result = self.yes(["i0001", "i0001-01"])
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertEqual(body["partial"]["placed"], [])
        self.assertEqual(body["partial"]["ticket"], "i0001")
        self.assertFalse(self.copy_exists("i0001-01"))

    def _fixture(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            return json.load(f)

    # 走らせるたびに変わる欄。フィクスチャと比べるときは外す。
    VOLATILE = ("generated_at",)
    # 本文の指紋。本文は一時ディレクトリのパスを含むので、走らせるたびに値が変わる。
    # 形（SHA-256 の 16 進）だけ確かめて、固定の綴りに置き換えてから比べる。
    DIGEST = "<digest>"

    def _mask_digests(self, portable):
        def mask(value):
            self.assertRegex(value, r"^[0-9a-f]{64}$")
            return self.DIGEST

        masked = dict(portable)
        if "digest" in masked:
            masked["digest"] = mask(masked["digest"])
        mismatch = masked.get("mismatch")
        if isinstance(mismatch, dict) and isinstance(mismatch.get("digest"), dict):
            masked["mismatch"] = {
                **mismatch,
                "digest": {k: mask(v) for k, v in mismatch["digest"].items()},
            }
        return masked

    def _check_fixture(self, name, body):
        """拡張のフィクスチャと突き合わせる。鍵だけでなく値まで見る。

        鍵の集合だけを比べると、識別子や本文が入れ替わっても気づけない。
        機械に依らない形（`_portable`）に直したうえで、時刻の欄だけ外して比べる。
        """
        portable = self._mask_digests(_portable(body, self.root))
        if os.environ.get("CCNAVI_BOARD_FIXTURE"):
            # 時刻は固定の綴りで書く。走らせるたびに変わる欄をそのまま置くと、
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
