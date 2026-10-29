"""`hookio.awaiting_user`（ユーザの返事を待つ目印で終えた Stop か）と、最後の発言の読み取り。

目印は最後の発言の、空でない最後の行に目印だけがあるときに数える。欄が無い・形が違うときは
目印が無いものとして読む（促しは今までどおり出る）。
"""

from __future__ import annotations

import io
import json
import unittest

from ccnavi.infra import hookio


def stop_payload(**fields) -> hookio.Input:
    body = {"hook_event_name": "Stop", "session_id": "s1"}
    body.update(fields)
    return hookio.decode(io.StringIO(json.dumps(body)))


class AwaitingUserTest(unittest.TestCase):
    def test_最後の行が目印だけなら真(self):
        for text in (
            "<!-- awaiting-user -->",
            "どちらで進めますか。\n\n<!-- awaiting-user -->",
            "どちらで進めますか。\n  <!-- awaiting-user -->  \n\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(hookio.awaiting_user(stop_payload(last_assistant_message=text)))

    def test_最後の行でなければ偽(self):
        for text in (
            "<!-- awaiting-user -->\n終わりました。",
            "最後の行に <!-- awaiting-user --> と書きます",
            "<!-- awaiting-user --> 待ちます",
            "<!-- Awaiting-User -->",
            "",
        ):
            with self.subTest(text=text):
                self.assertFalse(hookio.awaiting_user(stop_payload(last_assistant_message=text)))

    def test_欄が無い_形が違うときは目印が無いものとして読む(self):
        self.assertFalse(hookio.awaiting_user(stop_payload()))
        payload = stop_payload(last_assistant_message=["<!-- awaiting-user -->"])
        self.assertEqual(payload.last_assistant_message, "")
        self.assertFalse(hookio.awaiting_user(payload))


if __name__ == "__main__":
    unittest.main()
