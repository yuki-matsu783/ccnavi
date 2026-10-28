"""Stop の促しの共通の 1 文（直前にユーザが止めた・待たせた・相談したいと言ったとき）。

状態ごとの次の一手（`ops_stop_request` の各 `STEP_*`）とその前提は変えない。共通の部分にだけ、
ターンを終える 1 文を足した（`finish` の促しにも同じ文）。
"""

from __future__ import annotations

import types
import unittest

from ccnavi.tickets.lifecycle import stop as ops_stop
from ccnavi.tickets.lifecycle import stop_request as ops_stop_request

ROOT = "/tmp/ccnavi-stop-note"
NOTE_PARTS = (
    "直前にユーザが「止めて」「待って」「相談したい」と言った場合は、このまま進めずにターンを終えてください。",
    "止めた理由と、再開するときの次の一手を1行で書きます。",
    "ユーザの判断が本当に要る問いがある場合は、その問いを最後に書き直して終えてください。",
)

# 各ステップの次の一手（変えていないこと）
GUIDE = {
    ops_stop_request.STEP_FINISH: (
        "finish i0001-01-01",
        "ELI5、レビューの依頼（request）へ進む",
    ),
    ops_stop_request.STEP_CONFIRM: ("confirm --phase 1", "decide をユーザに頼む"),
    ops_stop_request.STEP_MERGE: ("status i0001", "合流済みと確かめられたら"),
    ops_stop_request.STEP_LATE_FIX: ("同じフェーズに子を足して", "判断を仰ぐ"),
    ops_stop_request.STEP_ELI5: ("wip/eli5/phase-1.html", "request --phase 1"),
    ops_stop_request.STEP_REQUEST: (
        "未コミットの変更があれば先にコミットして push",
        "request --phase 1",
    ),
}


def pending(step: str) -> ops_stop_request.Pending:
    parent = types.SimpleNamespace(ticket="i0001")
    return ops_stop_request.Pending(parent, 1, step, ROOT, "abc", ["i0001-01-01"], "設計")


class UserStopNoteTest(unittest.TestCase):
    def test_every_step_keeps_its_own_guidance_and_gets_the_common_note(self):
        self.assertEqual(
            set(GUIDE),
            {
                ops_stop_request.STEP_FINISH,
                ops_stop_request.STEP_CONFIRM,
                ops_stop_request.STEP_MERGE,
                ops_stop_request.STEP_LATE_FIX,
                ops_stop_request.STEP_ELI5,
                ops_stop_request.STEP_REQUEST,
            },
        )
        for step, needles in GUIDE.items():
            with self.subTest(step=step):
                text = ops_stop_request.request_nudge(ROOT, pending(step))
                for needle in needles:
                    self.assertIn(needle, text)
                self.assertIn("ユーザの許可は要りません", text)
                for part in NOTE_PARTS:
                    self.assertIn(part, text)
                self.assertIn("この案内は同じ状態では 1 回だけ", text)
                # 共通の文は 1 回だけ
                self.assertEqual(
                    text.count("止めた理由と、再開するときの次の一手を1行で書きます。"), 1
                )

    def test_the_finish_nudge_has_the_same_note(self):
        ticket = types.SimpleNamespace(
            ticket="i0001-01-01", is_child=True, base_sha="0" * 40, project=""
        )
        found = ops_stop.Unfinished(ticket, ROOT, 2, "abc")
        text = ops_stop.finish_nudge(ROOT, found)
        self.assertIn(ops_stop.CODE_FINISH_NUDGE, text)
        self.assertIn("finish i0001-01-01", text)
        self.assertIn("続ける理由（何が残っているか）をユーザに向けて書いてから終える", text)
        for part in NOTE_PARTS:
            self.assertIn(part, text)

    def test_the_note_is_one_constant(self):
        self.assertEqual(ops_stop.USER_STOP_NOTE, "".join(NOTE_PARTS))


if __name__ == "__main__":
    unittest.main()
