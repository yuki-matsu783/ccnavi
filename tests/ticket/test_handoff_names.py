"""下書きの置き場での階層名の読み替え（`.ccnavi/scripts/ccnavi-handoff-names.txt` の表）。

stage → 編集 → `user handoff` で、`.claude/settings.json` が元の場所に戻ることを見る。変換・予約名・
表の形・表に仮の 1 行を足したときの往復は go の handoffnames・handoff のテストが見る（見本は
tests/handoff_names_samples.py）。
"""

from __future__ import annotations

import os
import unittest

from tests.ticket.test_handoff import SETTINGS, HandoffCase, put, slurp


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

    def test_spelling_variants_of_the_draft_directory_are_one_place(self):
        put(os.path.join(self.root, "wip", "handoff", "Handoff-.CLAUDE", "settings.json"), self.NEW)
        answer = self.view()
        self.assertEqual([SETTINGS], [e["path"] for e in answer["entries"]])
        self.assertTrue(answer["ok"], answer)


if __name__ == "__main__":
    unittest.main()
