"""`--agree --preview --json` と `--agree --yes <識別子,…> --json`（承認の JSON）の受入テスト。

VS Code のボード拡張がオーバーレイで承認するための経路。設計 wip/design/approve-popup.md 2。
見るのは 6 つ。

1. `--preview` は一覧の本文と識別子、対象外の提案、読めない提案を JSON で返す。
   承認済みチケットは置かない。範囲の超過だけの子は一覧に載り `overflow[]` を持つ
   （設計 wip/design/approve-carry.md 3.3）
2. 承認待ちが無くても `--preview` は `batch: []` で exit 0
3. `--yes` に一覧と同じ識別子を渡すと承認済みチケットが置かれ、
   `prompt`（Claude Code に渡す文）が返る
4. `--yes` の識別子の一覧、または見せた各提案ファイルのバイト列（本文・改行・BOM を含む）が
   今と違えば、承認済みチケットを置かず `mismatch` で exit 1。判定が読んだ中身（承認済みチケット・
   マーカー・設定）と承認画面の本文は照合しない
5. `--yes` はターミナルの壁を通らない。素の `--agree` は今までどおり壁で止まる
6. 拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/approve-*.json）と同じ形

ターミナルの `--agree` で確認を待つ間に提案が変わったときの扱い（見せ直し・取り直し）は、
答えを返す前に提案を書き換える必要があるので go の core のテストが見る。

形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket.test_board import _portable
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import ROOT, write
from tests.ticketsample import child_text
from tests.ticketsample import parent_text as sample_parent_text

FIXTURES = os.path.join(ROOT, "extensions", "vscode", "ccnavi-board", "test", "fixtures")

APPROVE_VERSION = 1


def parent_text(*args, allow=("wip/*",), **kwargs):
    """計画（research, design）の範囲と重なる allow の親。重ならないと親にも超過が出る。"""
    return sample_parent_text(*args, allow=allow, **kwargs)


class ApproveJsonTest(PhaseHarness):
    def preview(self, *extra):
        result = self.ccnavi("--agree", "--preview", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def yes(self, tickets, *extra, digest=None):
        """`--agree --yes <識別子,…> --digest <ダイジェスト> --json [<絞り>...]`。
        extra は絞りかフラグ。

        拡張と同じく、直前に同じ extra でプレビューして、見せた本文のダイジェスト（`digest`）
        を渡す。
        digest を名指しすると、その値をそのまま渡す（見せたあとに提案が変わった形）。
        """
        if digest is None:
            digest = self.preview(*extra)["digest"]
        return self.ccnavi(
            "--entry",
            "board",
            "user",
            "agree",
            "--yes",
            ",".join(tickets),
            "--digest",
            digest,
            "--json",
            *extra,
        )

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

    # ---- 3. yes は一覧と一致すれば承認する

    # ---- 4. 見せた一覧と違えば承認しない

    def test_yes_without_json_prints_the_human_lines(self):
        self.pending_parent_and_child()
        digest = self.preview()["digest"]
        result = self.ccnavi("user", "agree", "--yes", "i0001,i0001-01-01", "--digest", digest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認した", result.stdout)
        self.assertTrue(self.copy_exists("i0001"))

    # ---- 4d. ダイジェストは画面に出ない欄と改版で書く中身も覆う（チケット approve-carry-07）

    def pending_revision(self):
        """親 i0001 を承認したあと、計画を足した改版を提案したまま（未承認）にする。

        足すのは `acceptance`。`implement` は `acceptance` を要る（requires）ので、単独で
        足すと改版は拒まれて一括のチケットに入らない。
        """
        self.family(plan=("research", "design"))
        path = self.propose(
            "i0001",
            parent_text(
                "i0001", ["research", "design", "acceptance"], allow=("src/*", "wip/*", "tests/*")
            ),
        )
        self.commit_parent("revise")
        shown = self.preview()
        self.assertEqual([b["ticket"] for b in shown["batch"]], ["i0001"])
        self.assertTrue(shown["batch"][0]["revision"], shown["batch"][0])
        return path, shown

    def test_a_change_of_the_approved_copy_alone_is_not_collated(self):
        """親の改版を見せたあとで、今の承認済みチケットだけが変わっても、照合では止めない。

        照合するのは提案の識別子の一覧と各提案ファイルのバイト列だけ。承認済みチケットが
        変わった影響は、承認の直前の判定の計算し直しで見る。
        """
        path, shown = self.pending_revision()
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        write(copy, text + "\n")
        self.assertEqual(self.preview()["digest"], shown["digest"])
        result = self.yes(["i0001"], digest=shown["digest"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- 4d2. 照合と書き込みは同じ読みを使う（読み直しの間に差し替えられても通さない）

    # ---- 4e. ターミナルの `--agree` も、確認の入力のあとで同じ照合をする

    def test_yes_does_not_need_a_terminal_but_plain_approve_still_does(self):
        self.pending_parent_and_child()
        # 壁を有効にしたまま。テストの標準入力はターミナルではない。
        refused = self.ccnavi(
            "--entry", "sh", "user", "agree", "--guard-ticket-approval", "enable", stdin="y\n"
        )
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

        # 食い違いの形も拡張が読むので、同じく書き出す。
        self.propose("i0001-02-03", child_text("i0001-02-03", "i0001", 2, ("wip/design/*",)))
        self.commit_parent()
        result = self.yes(["i0001-01-09"])
        self.assertEqual(result.returncode, 1)
        self._check_fixture("approve-mismatch.json", json.loads(result.stdout))

    def test_partial_carries_the_progress_lines(self):
        """止まるまでに出た行も渡す。ターミナルだけが知っていて拡張が知らない状態を作らない。

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
        self.commit_parent("approval-request 02 03")
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

    def _fixture(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
            return json.load(f)

    # 走らせるたびに変わる欄。フィクスチャと比べるときは外す。
    VOLATILE = ("generated_at",)
    # 本文のダイジェスト。本文は一時ディレクトリのパスを含むので、走らせるたびに値が変わる。
    # 形（SHA-256 の 16 進）だけ確かめて、固定の文字列に置き換えてから比べる。
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
        """拡張のフィクスチャと突き合わせる。キーだけでなく値まで見る。

        キーの集合だけを比べると、識別子や本文が入れ替わっても気づけない。
        機械に依らない形（`_portable`）に直したうえで、時刻の欄だけ外して比べる。
        """
        portable = self._mask_digests(_portable(body, self.root))
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
