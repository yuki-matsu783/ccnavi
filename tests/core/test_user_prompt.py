"""`hookio.is_user_prompt`（ユーザの発言と言い切れるか）と、ユーザの発言の記録（`records/userprompt.py`）。

関門の記録（ユーザの発言の時刻）だけがこの判定を使う。ターンの印と、ユーザの手による変更の受け入れは
`from_background_task` のままで、ここでは変わらないことも確かめる。

見るのは次の 3 つ。

- 数える形（ユーザが打った文）と数えない形（完了通知・ccnavi が起こす指示・外の出来事の封筒・空・
  サブエージェント・ほかのイベント）。形ごとに 1 件ずつ
- 見分けられない形（予約した実行の prompt など）は、数える側に残ること
- 記録の読み書き（無い・壊れたとき None、起動と再開で消える）
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

from ccnavi.infra import hookio
from ccnavi.records import idlehandoff, userprompt


def prompt_payload(prompt: str, **fields) -> hookio.Input:
    body = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": prompt}
    body.update(fields)
    return hookio.decode(io.StringIO(json.dumps(body)))


class UserPromptFormsTest(unittest.TestCase):
    def test_ユーザが打った文は数える(self):
        for text in (
            "README.md の末尾に 1 行足してください",
            "ok",
            "取り込んでください",
            "  先頭に空白のある文",
            "issue #12 を見て",
        ):
            with self.subTest(text=text):
                self.assertTrue(hookio.is_user_prompt(prompt_payload(text)))

    def test_バックグラウンド作業の完了通知は数えない(self):
        text = "<task-notification>\n<status>completed</status>\n</task-notification>"
        self.assertFalse(hookio.is_user_prompt(prompt_payload(text)))

    def test_完了通知は先頭でなくても含めば数えない(self):
        # from_background_task と同じ「含む」。先頭に限ると、前に文を足した通知が数えられる。
        text = "<system-reminder>x</system-reminder>\n<task-notification>done</task-notification>"
        self.assertFalse(hookio.is_user_prompt(prompt_payload(text)))
        self.assertFalse(hookio.is_user_prompt(prompt_payload("経過です。<task-notification>")))

    def test_ユーザがその文字列を自分で打っても数えない(self):
        # 開かないのは安全側。普通に打ち直せば数える。
        self.assertFalse(
            hookio.is_user_prompt(prompt_payload("<task-notification> と打ってみました"))
        )
        self.assertTrue(hookio.is_user_prompt(prompt_payload("task-notification と打ってみました")))

    def test_ccnavi_が起こす指示は数えない(self):
        self.assertFalse(hookio.is_user_prompt(prompt_payload("[idle-handoff] 引き継ぎを書いて")))
        self.assertFalse(hookio.is_user_prompt(prompt_payload("  [IDLE-HANDOFF] 大文字でも")))

    def test_目印の値は放置の指示と同じ(self):
        self.assertIn(idlehandoff.MARKER.lower(), hookio.NOT_USER_STARTS)

    def test_外の出来事がセッションを起こす封筒は数えない(self):
        for text in (
            '<wake reason="external-event"><event source="github" kind="check_run"></event></wake>',
            "<webhook-payload>{}</webhook-payload>",
            "<child-session-event>failed</child-session-event>",
            '<event source="github">x</event>',
            "[SYSTEM NOTIFICATION] 通知です",
            "<system-reminder>x</system-reminder>",
        ):
            with self.subTest(text=text):
                self.assertFalse(hookio.is_user_prompt(prompt_payload(text)))

    def test_文中に目印があるだけなら数える(self):
        # 先頭が目印の形だけを外す。文中の言及でユーザの発言を落とさない。
        self.assertTrue(hookio.is_user_prompt(prompt_payload("wake の封筒の形について教えて")))
        self.assertTrue(hookio.is_user_prompt(prompt_payload("説明: <wake reason=...> は何?")))

    def test_空の発言は数えない(self):
        for text in ("", "   ", "\n"):
            with self.subTest(text=text):
                self.assertFalse(hookio.is_user_prompt(prompt_payload(text)))

    def test_サブエージェントの中は数えない(self):
        self.assertFalse(hookio.is_user_prompt(prompt_payload("お願いします", agent_id="sub1")))

    def test_ほかのイベントは数えない(self):
        for event in ("PreToolUse", "Stop", "SessionStart", "PostToolUse"):
            with self.subTest(event=event):
                got = prompt_payload("お願いします", hook_event_name=event)
                self.assertFalse(hookio.is_user_prompt(got))

    def test_予約した実行の_prompt_は見分けられないので数える側に残る(self):
        # 保存した文そのままで来る。ユーザの文と見分ける欄が payload に無い。
        self.assertTrue(hookio.is_user_prompt(prompt_payload("5 分ごとにデプロイを見て")))

    def test_動かしている作業が残っていても発言そのものは数える(self):
        # busy は「次の発言までの変更がユーザの手か」を決める材料で、発言かどうかではない。
        # 数えないと、予約が残る限りユーザの返事で関門が開かなくなる。
        got = prompt_payload("続けてください", background_tasks=[{"id": "b1"}])
        self.assertTrue(hookio.is_user_prompt(got))

    def test_完了通知の判定は変わらない(self):
        # ターンの印と変更の受け入れは、これまでどおり from_background_task で決める。
        self.assertTrue(prompt_payload("<task-notification>x").from_background_task())
        self.assertFalse(prompt_payload("[idle-handoff] x").from_background_task())
        self.assertFalse(prompt_payload("お願いします").from_background_task())


class UserPromptRecordTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ccnavi-userprompt-")
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def path(self, session="s1"):
        return os.path.join(self.state, f"userprompt-{session}.json")

    def test_書いた時刻を読める(self):
        userprompt.note(self.state, "s1", now=1000.0)

        self.assertEqual(userprompt.last(self.state, "s1"), 1000.0)

    def test_書き直すと新しい時刻になる(self):
        userprompt.note(self.state, "s1", now=1000.0)
        userprompt.note(self.state, "s1", now=2000.0)

        self.assertEqual(userprompt.last(self.state, "s1"), 2000.0)

    def test_セッションごとに別の記録(self):
        userprompt.note(self.state, "s1", now=1000.0)

        self.assertIsNone(userprompt.last(self.state, "s2"))

    def test_無いときと壊れたときは_None(self):
        self.assertIsNone(userprompt.last(self.state, "s1"))
        for text in ("{not json", "[]", json.dumps({"at": "yesterday"}), json.dumps({"at": True})):
            with self.subTest(text=text):
                with open(self.path(), "w", encoding="utf-8") as f:
                    f.write(text)
                self.assertIsNone(userprompt.last(self.state, "s1"))

    def test_消せる_無くても何も起きない(self):
        userprompt.note(self.state, "s1", now=1000.0)

        userprompt.clear(self.state, "s1")
        userprompt.clear(self.state, "nobody")

        self.assertIsNone(userprompt.last(self.state, "s1"))

    def test_置き場やセッションが空でも例外にしない(self):
        for state, session in (("", "s1"), (self.state, ""), (os.path.join(self.state, "x"), "s1")):
            with self.subTest(state=state, session=session):
                userprompt.note(state, session)
                userprompt.clear(state, session)
                self.assertIsNone(userprompt.last(state, session))


if __name__ == "__main__":
    unittest.main()
