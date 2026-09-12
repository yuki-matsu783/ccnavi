"""`--approve --preview --json` と `--approve --yes <識別子,…> --json`（承認の JSON）の受入テスト。

VS Code のボード拡張がオーバーレイで承認するための経路。設計 wip/design/approve-popup.md §2。
見るのは 6 つ。

1. `--preview` は束の本文と識別子、対象外の提案、読めない提案を JSON で返し、写しを置かない
2. 承認待ちが無くても `--preview` は `batch: []` で exit 0
3. `--yes` に束と同じ識別子を渡すと写しが置かれ、`prompt`（Claude Code に渡す文）が返る
4. `--yes` の識別子が束と違えば写しを置かず、`mismatch` で exit 1
5. `--yes` は端末の壁を通らない。素の `--approve` は今までどおり壁で止まる
6. 拡張側のフィクスチャ（vscode-extension/ccnavi-board/test/fixtures/approve-*.json）と同じ形

形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.test_board import _portable
from tests.test_phases import PhaseHarness, child_text, parent_text
from tests.test_ticket import ROOT, write

FIXTURES = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "test", "fixtures")

APPROVE_VERSION = 1


class ApproveJsonTest(PhaseHarness):
    def preview(self, *extra):
        result = self.ccnavi("--approve", "--preview", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def yes(self, tickets, *extra):
        """`--approve --yes <識別子,…> --json [<絞り>...]`。extra は絞りかフラグ。"""
        return self.ccnavi("--approve", "--yes", ",".join(tickets), "--json", *extra)

    def copy_exists(self, name):
        return os.path.exists(os.path.join(self.approved, name + ".md"))

    def pending_parent_and_child(self):
        """親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

    # ---- 1. preview は読むだけ

    def test_preview_lists_the_batch_and_does_not_place_copies(self):
        self.pending_parent_and_child()
        # 種類の範囲を超える子。承認の対象にしない側に載る。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ("wip/design/*",)))
        # frontmatter の読めない提案。読めない提案の側に載る。
        write(os.path.join(self.parent_tree, "wip", "tickets", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        body = self.preview()
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001", "i0001-01"])
        parent, child = body["batch"]
        self.assertIsNone(parent["parent"])
        self.assertIsNone(parent["phase"])
        self.assertFalse(parent["revision"])
        self.assertEqual(child["parent"], "i0001")
        self.assertEqual(child["phase"], 1)
        self.assertTrue(child["path"].replace("\\", "/").endswith("wip/tickets/todo/i0001-01.md"))
        self.assertIn("Ticket 承認リクエスト: 2 件", body["text"])
        self.assertIn("== i0001-01", body["text"])
        self.assertEqual([r["ticket"] for r in body["rejected"]], ["i0001-02"])
        self.assertTrue(any("超えている" in p for p in body["rejected"][0]["problems"]))
        self.assertTrue(any("broken.md" in p for p in body["problems"]))
        # 見ただけ。写しは置かれていない。
        self.assertFalse(self.copy_exists("i0001"))
        self.assertFalse(self.copy_exists("i0001-01"))

    # ---- 2. 承認待ちが無い

    def test_preview_with_nothing_pending_is_an_empty_batch(self):
        body = self.preview()
        self.assertEqual(body["batch"], [])
        self.assertEqual(body["rejected"], [])
        self.assertIn("承認待ちのチケットは無い", body["text"])

    # ---- 3. yes は束と一致すれば承認する

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
        # 承認したので束は空になる。
        self.assertEqual(self.preview()["batch"], [])

    def test_yes_order_of_identifiers_does_not_matter(self):
        self.pending_parent_and_child()
        result = self.yes(["i0001-01", "i0001"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.copy_exists("i0001-01"))

    # ---- 4. 見せた束と違えば承認しない

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
        # 絞りを添えずに同じことを頼めば、絞らない束と比べて食い違いになる。
        self.assertEqual(self.preview()["batch"][0]["ticket"], "i0001-01")

    def test_yes_without_the_filter_compares_against_the_whole_batch(self):
        """絞りを添えない `--yes` は、絞らない束と比べる。部分だけを黙って通さない。"""
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
        result = self.ccnavi("--approve", "--yes", "i0001,i0001-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認した", result.stdout)
        self.assertTrue(self.copy_exists("i0001"))

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
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ("wip/design/*",)))
        self.commit_parent()
        preview = self.preview()
        self._check_fixture("approve-preview.json", preview)

        result = self.yes(["i0001", "i0001-01"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self._check_fixture("approve-yes.json", json.loads(result.stdout))

        # 食い違いの形も拡張が読むので、同じく写す。
        self.propose("i0001-03", child_text("i0001-03", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        result = self.yes(["i0001-09"])
        self.assertEqual(result.returncode, 1)
        self._check_fixture("approve-mismatch.json", json.loads(result.stdout))

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
