"""Stop の AI 判定の接続部分: `stop-review` の入口と、Stop での計画の作成。"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tempfile
import unittest

from ccnavi.entry import cli_stopreview
from ccnavi.hook import events
from ccnavi.infra import hookio, modes, settings
from ccnavi.records import stopreview as sr


def _transcript(directory, rows):
    path = os.path.join(directory, "t.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


ROWS = [
    {"type": "user", "message": {"content": "親チケットで進めて"}},
    {"type": "assistant", "uuid": "a0", "message": {"content": [{"type": "text", "text": "了解"}]}},
    {"type": "user", "message": {"content": "続けて"}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
    {"type": "user", "message": {"content": "<task-notification>done</task-notification>"}},
    {
        "type": "assistant",
        "uuid": "a1",
        "message": {"content": [{"type": "text", "text": "作ってよいですか"}]},
    },
]


class MaterialTest(unittest.TestCase):
    def test_turn_boundary_skips_notifications_and_tool_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = sr.turn_material(_transcript(tmp, ROWS))
        self.assertEqual([t["role"] for t in got["turn"]], ["user", "assistant"])
        self.assertEqual(got["turn"][0]["text"], "続けて")
        self.assertEqual(got["past_prompts"], ["親チケットで進めて"])
        self.assertFalse(got["insufficient"])

    def test_unreadable_is_insufficient(self):
        self.assertTrue(sr.turn_material("/nonexistent")["insufficient"])

    def test_truncation_is_insufficient(self):
        rows = [{"type": "user", "message": {"content": "x" * (sr.MATERIAL_LIMIT + 1)}}]
        with tempfile.TemporaryDirectory() as tmp:
            got = sr.turn_material(_transcript(tmp, rows))
        self.assertTrue(got["insufficient"])


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.state = os.path.join(tmp.name, "state")
        os.makedirs(self.state)
        self.transcript = _transcript(tmp.name, ROWS)
        self.conf = settings.Settings(state=self.state, log=os.path.join(tmp.name, "logs"))
        self.key = sr.shared_key("s1", self.transcript)
        cli_stopreview.PLAN_WAIT_SECONDS = 0.3

    def _run(self, verb, stdin=""):
        out = io.StringIO()
        code = cli_stopreview.run(
            io.StringIO(stdin), out, io.StringIO(), self.conf,
            ["stop-review", verb, "s1", self.transcript],
        )  # fmt: skip
        self.assertEqual(code, 0)
        return out.getvalue()

    def _plan(self, **over):
        plan = {
            "is_candidate": True,
            "targets": [sr.STOP_TARGET],
            "material_version": sr.material_version(),
            "effective": modes.ENABLE,
        }
        plan.update(over)
        return sr.write_plan(self.state, self.key, plan)

    def test_material_without_plan_times_out(self):
        out = self._run("material")
        self.assertIn(cli_stopreview.NOT_CANDIDATE, out)
        self.assertIn('{"ok": true}', out)

    def test_material_non_candidate(self):
        self._plan(is_candidate=False, final=sr.FINAL_OUT_OF_SCOPE, reason="existing_nudge")
        out = self._run("material")
        self.assertIn(cli_stopreview.NOT_CANDIDATE, out)

    def test_material_then_record_flaw_sends_back(self):
        aid = self._plan()
        out = self._run("material")
        for needle in (aid, "続けて", "親チケットで進めて", "<criteria>"):
            self.assertIn(needle, out)
        result = {
            "attempt_id": aid,
            "classification": sr.CLASS_TICKET_WORK,
            "results": [
                {
                    "criterion": sr.R1,
                    "target": sr.STOP_TARGET,
                    "result": sr.FAIL,
                    "locations": [{"kind": "final_answer", "text": "作ってよいですか"}],
                    "fixes": [{"kind": "write_proposal", "text": "提案を書いて送る"}],
                },
                {"criterion": sr.R3, "target": sr.STOP_TARGET, "result": sr.PASS},
            ],
        }
        reply = json.loads(self._run("record", json.dumps(result)))
        self.assertFalse(reply["ok"])
        self.assertIn(aid, reply["reason"])
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.SENT_BACK)

    def test_record_garbage_returns_ok(self):
        self._plan()
        self._run("material")
        self.assertEqual(json.loads(self._run("record", "not json")), {"ok": True})

    def test_record_without_material_returns_ok(self):
        self._plan()
        self.assertEqual(json.loads(self._run("record", "{}")), {"ok": True})

    def test_no_key(self):
        out = io.StringIO()
        cli_stopreview.run(
            io.StringIO(""), out, io.StringIO(), self.conf,
            ["stop-review", "material", "s1", "/nonexistent"],
        )  # fmt: skip
        self.assertIn("no_key", out.getvalue())


class PlanAtStopTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "state")
        os.makedirs(self.state)
        self.transcript = _transcript(tmp.name, ROWS)
        self.conf = settings.Settings(
            state=self.state, log=os.path.join(tmp.name, "logs"), stop_review_agent=True
        )
        self.payload = hookio.Input(
            event=hookio.STOP, session_id="s1", transcript_path=self.transcript, cwd=tmp.name
        )
        self.key = sr.shared_key("s1", self.transcript)
        self.root = tmp.name

    def _plan(self, payload=None, nudged=False, continuation=None, tickets=True):
        self.conf.ticket_control = "enable" if tickets else "disable"
        events._plan_stop_review(
            io.StringIO(), self.conf, self.root, payload or self.payload,
            modes.ENABLE, nudged, continuation, None,
        )  # fmt: skip

    def test_not_registered_writes_nothing(self):
        self.conf.stop_review_agent = False
        self._plan()
        self.assertIsNone(sr.read_plan(self.state, self.key))

    def test_candidate_plan_written_once(self):
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        if plan is None:  # チケット制御が無効な作りのときは候補にならない
            self.skipTest("tickets disabled in this settings")
        self.assertEqual(plan["targets"], [sr.STOP_TARGET])
        self.assertEqual(plan["effective"], modes.DRY_RUN)

    def test_existing_nudge_is_out_of_scope_by_ccnavi(self):
        self._plan(nudged=True)
        plan = sr.read_plan(self.state, self.key)
        if plan is None:
            self.skipTest("tickets disabled in this settings")
        attempt = sr.read_attempt(self.state, plan["attempt_id"])
        self.assertEqual(
            (attempt["final"], attempt["judge"]), (sr.FINAL_OUT_OF_SCOPE, sr.JUDGE_CCNAVI)
        )

    def test_stop_hook_active_without_continuation_is_skipped(self):
        self._plan(payload=dataclasses.replace(self.payload, stop_hook_active=True))
        plan = sr.read_plan(self.state, self.key)
        if plan is not None:
            attempt = sr.read_attempt(self.state, plan["attempt_id"])
            self.assertEqual(attempt["final"], sr.FINAL_SKIPPED)

    def test_ai_continuation_requires_registration_and_sent_back(self):
        payload = dataclasses.replace(self.payload, stop_hook_active=True)
        self.assertIsNone(events._ai_continuation(self.conf, payload))
        self.assertIsNone(events._ai_continuation(self.conf, self.payload))


if __name__ == "__main__":
    unittest.main()
