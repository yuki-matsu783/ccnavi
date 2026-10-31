"""下書きの置き場での階層名の読み替え（src/ccnavi/tickets/model/handoff_names.py）。

見るのは次のとおり。名前を増やすときは、見本（tests/handoff_names_samples.py）に
1 行足すだけでよい。

1. 変換: 元のパス → 下書きのパスを表で読み替える（深さを問わず、大文字小文字を区別しない）。
   戻す向きも同じ規則で行う
2. 予約名: 元のパスに下書きでの階層名があれば断る
3. 往復: stage → 編集 → `user handoff` で、`.claude/settings.json` が元の場所に戻る
4. 拡張: 表に仮の 1 行を足しても、同じ往復が通る
5. 表のファイル: sh と実行ファイルが読む `.ccnavi/scripts/ccnavi-handoff-names.txt` の形
6. 表の探し方: 実行ファイルの展開先・パッケージの直下・リポジトリの 3 つの置き場のどれでも見つかる
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

from ccnavi.tickets.model import handoff_names
from ccnavi.tickets.sync import handoff
from tests import ROOT
from tests.handoff_names_samples import RESERVED, REVERSE_SAMPLES, SAMPLES
from tests.ticket.test_handoff import DOCS, SETTINGS, HandoffCase, put, slurp


class ConvertTest(unittest.TestCase):
    def test_samples_to_draft(self):
        for orig, draft in SAMPLES:
            with self.subTest(orig=orig):
                self.assertEqual(draft, handoff_names.to_draft(orig))

    def test_reverse_samples_from_draft(self):
        for draft, orig in REVERSE_SAMPLES:
            with self.subTest(draft=draft):
                self.assertEqual(orig, handoff_names.from_draft(draft))

    def test_the_round_trip_returns_to_the_same_name_up_to_case(self):
        for orig, _ in SAMPLES:
            with self.subTest(orig=orig):
                back = handoff_names.from_draft(handoff_names.to_draft(orig))
                self.assertEqual(orig.casefold(), back.casefold())

    def test_reserved_names_are_refused(self):
        for rel in RESERVED:
            with self.subTest(rel=rel):
                self.assertIn("予約", handoff_names.reserved_problem(rel))
        for orig, _ in SAMPLES:
            if "handoff-" not in orig.casefold():
                self.assertEqual("", handoff_names.reserved_problem(orig), orig)

    def test_names_with_non_ascii_are_never_matched_by_fold(self):
        """sh は ASCII の大文字小文字しか畳めない。`ﬀ` は fold で `ff` になるが、表には当てない。"""
        weird = "handoﬀ-.claude/x.json"
        self.assertEqual(weird, handoff_names.from_draft(weird))
        self.assertEqual("", handoff_names.reserved_problem(weird))

    def test_the_table_in_the_repo_has_the_claude_row(self):
        self.assertEqual({".claude": "handoff-.claude"}, handoff_names.load())
        self.assertIs(handoff.HANDOFF_ALIASES, handoff_names.ALIASES)


class ParseTest(unittest.TestCase):
    def test_comments_and_blank_lines_are_skipped(self):
        text = "# c\n\n.claude handoff-.claude\n  .vscode   handoff-.vscode  \n"
        self.assertEqual(
            {".claude": "handoff-.claude", ".vscode": "handoff-.vscode"},
            handoff_names.parse(text),
        )

    def test_a_bad_line_is_an_error(self):
        for text in (".claude\n", "a b c\n", "a/b c\n", "日本 x\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                handoff_names.parse(text)

    def test_the_names_file_is_the_one_sh_reads(self):
        path = os.path.join(ROOT, ".ccnavi", "scripts", handoff_names.NAMES_FILE)
        self.assertEqual(os.path.abspath(path), os.path.abspath(handoff_names._names_path()))


class NamesPathTest(unittest.TestCase):
    """表のファイルの探し方。モジュールの深さを変えても、3 つの置き場のどれでも見つかる。"""

    def locate(self, root: str, *, meipass: str = "") -> str:
        """`src/ccnavi/tickets/model/` の位置にあるつもりで探した結果。"""
        module = os.path.join(root, "src", "ccnavi", "tickets", "model", "handoff_names.py")
        patches = [mock.patch.object(handoff_names, "__file__", module)]
        if meipass:
            patches.append(mock.patch.object(sys, "_MEIPASS", meipass, create=True))
        with patches[0]:
            if meipass:
                with patches[1]:
                    return handoff_names._names_path()
            return handoff_names._names_path()

    def put_names(self, *parts: str) -> str:
        path = os.path.join(*parts, handoff_names.NAMES_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("a b\n")
        return path

    def test_the_repo_copy_is_found_from_the_source_tree(self):
        with tempfile.TemporaryDirectory() as root:
            want = self.put_names(root, ".ccnavi", "scripts")
            self.assertEqual(want, self.locate(root))

    def test_the_copy_next_to_the_package_is_found(self):
        with tempfile.TemporaryDirectory() as root:
            self.put_names(root, ".ccnavi", "scripts")
            want = self.put_names(root, "src", "ccnavi")
            self.assertEqual(want, self.locate(root))

    def test_the_bundled_copy_comes_first(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
            self.put_names(root, "src", "ccnavi")
            self.put_names(root, ".ccnavi", "scripts")
            want = self.put_names(bundle)
            self.assertEqual(want, self.locate(root, meipass=bundle))

    def test_the_real_table_loads_from_the_real_location(self):
        self.assertTrue(os.path.isfile(handoff_names._names_path()))


class RoundTripTest(HandoffCase):
    """stage → 編集 → 取り込みで、元の場所に戻る。"""

    NEW = b'{"hooks": {"SessionStart": [{"timeout": 30}]}}\n'

    def test_a_staged_settings_draft_goes_back_to_the_original(self):
        draft = self.stage(SETTINGS, self.NEW)
        self.assertTrue(draft.endswith(os.path.join("handoff-.claude", "settings.json")), draft)
        self.assertFalse(os.path.exists(os.path.join(self.root, "wip", "handoff", ".claude")))
        answer = self.view()
        row = self.entry(answer, SETTINGS)
        self.assertEqual("changed", row["state"])
        self.assertEqual("wip/handoff/handoff-.claude/settings.json", row["draft"])
        self.assertTrue(answer["ok"], answer)
        done = self.take()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(self.NEW, slurp(self.target(SETTINGS)))
        self.assertFalse(os.path.exists(draft))

    def test_either_path_form_selects_the_draft(self):
        self.stage(SETTINGS, self.NEW)
        for arg in (SETTINGS, "wip/handoff/handoff-.claude/settings.json", ".CLAUDE/settings.json"):
            with self.subTest(arg=arg):
                answer = self.view(arg)
                self.assertTrue(answer["ok"], answer)
                self.assertEqual(
                    [SETTINGS], [e["path"] for e in answer["entries"] if e["selected"]]
                )

    def test_hooks_under_the_hidden_directory_are_taken_too(self):
        hook = ".claude/hooks/lint-py.sh"
        put(self.target(hook), b"#!/bin/sh\necho old\n")
        self.stage(hook, b"#!/bin/sh\necho new\n")
        self.assertEqual(0, self.take().returncode)
        self.assertEqual(b"#!/bin/sh\necho new\n", slurp(self.target(hook)))

    def test_a_pending_draft_keeps_its_own_spelling_in_the_report(self):
        self.stage(SETTINGS, self.NEW)
        self.assertEqual(["wip/handoff/handoff-.claude/settings.json"], handoff.pending(self.root))

    def test_a_draft_equal_to_the_original_is_not_pending(self):
        self.stage(SETTINGS)
        self.assertEqual([], handoff.pending(self.root))

    def test_spelling_variants_of_the_draft_directory_are_one_place(self):
        put(os.path.join(self.root, "wip", "handoff", "Handoff-.CLAUDE", "settings.json"), self.NEW)
        plan = self.plan()
        self.assertEqual([SETTINGS], [e.rel for e in plan.entries])
        self.assertTrue(plan.ok)


class ExtensionTest(HandoffCase):
    """表に仮の 1 行を足すだけで、ほかの名前でも同じ往復が通る。"""

    ROWS = [
        (".ccnavi", "handoff-.ccnavi", DOCS),
    ]

    def test_a_row_added_to_the_table_works_the_same(self):
        for before, after, rel in self.ROWS:
            with self.subTest(before=before):
                table = {**handoff_names.ALIASES, before: after}
                with mock.patch.dict(handoff_names.ALIASES, table, clear=True):
                    new = "# 手引き\n\n新しい文\n".encode()
                    draft = self.stage(rel, new)
                    self.assertIn(f"{os.sep}{after}{os.sep}", draft)
                    plan = self.plan()
                    self.assertEqual([rel], [e.rel for e in plan.entries])
                    self.assertTrue(plan.ok)
                    done = handoff.apply(plan, plan.expect)
                    self.assertTrue(done.ok, done.error)
                    self.assertEqual(new, slurp(self.target(rel)))
                    self.assertFalse(os.path.exists(draft))


if __name__ == "__main__":
    unittest.main()
