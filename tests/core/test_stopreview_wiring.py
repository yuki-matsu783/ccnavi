"""Stop の AI 判定の接続部分: `stop-review` の入口と、Stop での計画の作成。"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tempfile
import time
import unittest

from ccnavi.entry import cli_stopreview
from ccnavi.hook import events
from ccnavi.infra import hookio, modes, settings
from ccnavi.records import stopops
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
            state=self.state,
            log=os.path.join(tmp.name, "logs"),
            stop_review_agent=True,
            tickets="wip/proposals",
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
        self.assertIsNotNone(plan)
        self.assertTrue(plan["is_candidate"])
        self.assertEqual(plan["targets"], [sr.STOP_TARGET])
        self.assertEqual(plan["effective"], modes.DRY_RUN)

    def test_existing_nudge_is_out_of_scope_by_ccnavi(self):
        self._plan(nudged=True)
        plan = sr.read_plan(self.state, self.key)
        self.assertIsNotNone(plan)
        self.assertFalse(plan["is_candidate"])
        self.assertEqual(plan["reason"], "existing_nudge")
        attempt = sr.read_attempt(self.state, plan["attempt_id"])
        self.assertEqual(
            (attempt["final"], attempt["judge"]), (sr.FINAL_OUT_OF_SCOPE, sr.JUDGE_CCNAVI)
        )

    def test_stop_hook_active_without_continuation_is_skipped(self):
        self._plan(payload=dataclasses.replace(self.payload, stop_hook_active=True))
        plan = sr.read_plan(self.state, self.key)
        self.assertIsNotNone(plan)  # 候補でなくても、検査省略の計画は書く（判定役を待たせない）
        self.assertFalse(plan["is_candidate"])
        self.assertEqual(plan["reason"], "stop_hook_active")
        attempt = sr.read_attempt(self.state, plan["attempt_id"])
        self.assertEqual((attempt["final"], attempt["judge"]), (sr.FINAL_SKIPPED, sr.JUDGE_CCNAVI))

    def test_ai_continuation_requires_registration_and_sent_back(self):
        payload = dataclasses.replace(self.payload, stop_hook_active=True)
        self.assertIsNone(events._ai_continuation(self.conf, payload))
        self.assertIsNone(events._ai_continuation(self.conf, self.payload))


class ProposalPlanTest(PlanAtStopTest):
    """propose の操作実績が、計画の対象になる。"""

    CMD = "sh .ccnavi/scripts/ccnavi-ticket.sh propose p1"

    def _hook(self, event, tool_use_id="tu1", command=None):
        return hookio.Input(
            event=event,
            session_id="s1",
            cwd=self.root,
            tool_name="Bash",
            tool_input={"command": command or self.CMD},
            tool_use_id=tool_use_id,
        )

    def _propose(self, route="sent", commit="c0ffee0123456789", tool_use_id="tu1"):
        now = int(time.time())
        events._track_proposals(self.conf, self._hook(hookio.PRE_TOOL_USE, tool_use_id))
        directory = os.path.join(self.state, stopops.RESULT_DIR)
        os.makedirs(directory, exist_ok=True)
        lines = ["id=p1", f"route={route}", "rc=0", f"started={now}", f"ended={now}"]
        if commit:
            lines += [f"commit={commit}", "file=abc wip/proposals/todo/p1.md"]
        with open(os.path.join(directory, f"{now}-{tool_use_id}.kv"), "w") as handle:
            handle.write("\n".join(lines) + "\n")
        events._track_proposals(self.conf, self._hook(hookio.POST_TOOL_USE, tool_use_id))

    def test_pre_and_post_bind_the_result(self):
        self._propose()
        (op,) = stopops.turn_ops(self.state, "s1", 0)
        self.assertEqual((op["ident"], op["route"], op["tool_use_id"]), ("p1", "sent", "tu1"))

    def test_other_commands_and_subagents_are_not_tracked(self):
        events._track_proposals(
            self.conf, self._hook(hookio.PRE_TOOL_USE, command="sh ccnavi-ticket.sh status")
        )
        sub = dataclasses.replace(self._hook(hookio.PRE_TOOL_USE), agent_id="a1")
        events._track_proposals(self.conf, sub)
        self.assertEqual([n for n in os.listdir(self.state) if n.startswith("stopops-pre-")], [])

    def test_unregistered_workspace_tracks_nothing(self):
        self.conf.stop_review_agent = False
        events._track_proposals(self.conf, self._hook(hookio.PRE_TOOL_USE))
        self.assertEqual(os.listdir(self.state), [])

    def test_sent_proposal_becomes_a_target(self):
        self._propose()
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        self.assertTrue(plan["is_candidate"])
        (proposal,) = plan["proposals"]
        self.assertEqual(proposal["key"], "proposal:p1:c0ffee012345")
        self.assertEqual(plan["targets"], [sr.STOP_TARGET, proposal["key"]])
        self.assertEqual(plan["no_r2"], [])
        self.assertIn((sr.R2, proposal["key"]), sr.required(plan))

    def test_unsent_proposal_needs_r4_only(self):
        self._propose(route="stopped", commit="")
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        key = plan["proposals"][0]["key"]
        self.assertEqual(plan["no_r2"], [key])
        needs = sr.required(plan)
        self.assertIn((sr.R4, key), needs)
        self.assertNotIn((sr.R2, key), needs)

    def _write_post(self, ident="p1", tool="Write"):
        path = os.path.join(self.root, "wip", "proposals", "todo", f"{ident}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# 提案\n目的\n")
        events._track_proposals(
            self.conf,
            hookio.Input(
                event=hookio.POST_TOOL_USE,
                session_id="s1",
                cwd=self.root,
                tool_name=tool,
                tool_input={"file_path": path},
                tool_use_id="w1",
            ),
        )
        return path

    def test_written_but_unsent_proposal_is_a_target(self):
        path = self._write_post()
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        self.assertTrue(plan["is_candidate"])
        (proposal,) = plan["proposals"]
        self.assertEqual((proposal["route"], proposal["key"]), ("unsent", "proposal:p1:unsent"))
        self.assertEqual(plan["no_r2"], [])  # 書いたファイルの本文があるので R2 も判定する
        self.assertIn((sr.R2, proposal["key"]), sr.required(plan))
        body = cli_stopreview._body(self.root, proposal)
        self.assertIn("目的", body)
        self.assertIn(path, body)

    def test_written_then_sent_is_not_unsent(self):
        self._write_post()
        time.sleep(0.01)
        now = int(time.time()) + 5
        directory = os.path.join(self.state, stopops.RESULT_DIR)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "k.kv"), "w") as handle:
            handle.write(
                f"id=p1\nroute=sent\nrc=0\nstarted={now}\nended={now}\ncommit=abc123\n"
                "file=sha wip/proposals/todo/p1.md\n"
            )
        stopops.register(self.state, "s1", "tu1", "p1", now - 1)
        stopops.bind(self.state, "tu1", now + 1)
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        self.assertEqual([p["route"] for p in plan["proposals"]], ["sent"])

    def test_an_operated_proposal_is_not_listed_twice_as_unsent(self):
        self._write_post("p1")
        self._propose(route="stopped", commit="")
        self._plan()
        plan = sr.read_plan(self.state, self.key)
        self.assertEqual([p["route"] for p in plan["proposals"]], ["stopped"])

    def test_non_proposal_writes_and_edit_failures_are_ignored(self):
        events._track_proposals(
            self.conf,
            hookio.Input(
                event=hookio.POST_TOOL_USE,
                session_id="s1",
                cwd=self.root,
                tool_name="Write",
                tool_input={"file_path": os.path.join(self.root, "src", "a.py")},
            ),
        )
        self.assertEqual(stopops.turn_writes(self.state, "s1", 0), [])


if __name__ == "__main__":
    unittest.main()
