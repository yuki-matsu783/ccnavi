"""Stop の AI 判定の中核（records.stopreview）の単体テスト。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from ccnavi.infra import modes
from ccnavi.records import stopreview as sr

KEY = "s1:u-last"
VERSION = "m1"


def _plan(**over):
    plan = {
        "is_candidate": True,
        "targets": [sr.STOP_TARGET, "p1"],
        "material_version": VERSION,
    }
    plan.update(over)
    return plan


def _row(criterion, target, result, **extra):
    row = {"criterion": criterion, "target": target, "result": result}
    row.update(extra)
    return row


def _passing(aid, include_p1=True):
    rows = [_row(sr.R1, sr.STOP_TARGET, sr.PASS), _row(sr.R3, sr.STOP_TARGET, sr.PASS)]
    if include_p1:
        rows += [_row(sr.R2, "p1", sr.PASS), _row(sr.R4, "p1", sr.PASS)]
    return {"attempt_id": aid, "classification": sr.CLASS_TICKET_WORK, "results": rows}


def _flawed(aid):
    result = _passing(aid)
    result["results"][2] = _row(
        sr.R2,
        "p1",
        sr.FAIL,
        locations=[{"kind": "proposal", "text": "完了の確かめ方が無い"}],
        fixes=[{"kind": "revise_proposal", "text": "確認の手順を足す"}],
        mismatch="依頼は結合の確認を求めている",
    )
    return result


class SharedKeyTest(unittest.TestCase):
    def _transcript(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "t.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
        return path

    def test_last_main_assistant_uuid(self):
        path = self._transcript(
            [
                {"type": "assistant", "uuid": "a1"},
                {"type": "assistant", "uuid": "a2"},
                {"type": "assistant", "uuid": "side", "isSidechain": True},
                {"type": "system", "uuid": "sys"},
                "not json",
                {"type": "user", "uuid": "u"},
            ]
        )
        self.assertEqual(sr.shared_key("s1", path), "s1:a2")

    def test_unfinished_last_line_gives_no_key(self):
        path = self._transcript([{"type": "assistant", "uuid": "a1"}, '{"type": "assist'])
        self.assertEqual(sr.shared_key("s1", path), "")

    def test_same_input_same_key(self):
        path = self._transcript([{"type": "assistant", "uuid": "a1"}])
        self.assertEqual(sr.shared_key("s1", path), sr.shared_key("s1", path))

    def test_cannot_derive(self):
        self.assertEqual(sr.shared_key("s1", "/nonexistent"), "")
        self.assertEqual(sr.shared_key("", "x"), "")
        self.assertEqual(sr.shared_key("s1", self._transcript([{"type": "user"}])), "")

    def test_reads_from_tail_across_chunks(self):
        filler = [{"type": "user", "text": "x" * 1000}] * 200
        path = self._transcript([{"type": "assistant", "uuid": "old"}] + filler)
        self.assertEqual(sr.shared_key("s1", path), "s1:old")


class SettingTest(unittest.TestCase):
    def test_combination(self):
        self.assertEqual(sr.setting(modes.ENABLE, "enable"), modes.ENABLE)
        self.assertEqual(sr.setting(modes.ENABLE, ""), modes.DRY_RUN)
        self.assertEqual(sr.setting(modes.ENABLE, "garbage"), modes.DRY_RUN)
        self.assertEqual(sr.setting(modes.DRY_RUN, "enable"), modes.DRY_RUN)
        self.assertEqual(sr.setting(modes.ENABLE, "disable"), modes.DISABLE)
        self.assertEqual(sr.setting(modes.DISABLE, "enable"), modes.DISABLE)


class CandidateTest(unittest.TestCase):
    BASE = dict(
        main_agent=True,
        stop_hook_active=False,
        ai_continuation=False,
        already_rejudged=False,
        effective=modes.ENABLE,
        tickets_enabled=True,
        registered=True,
        existing_nudge=False,
        target_unresolvable=False,
        proposal_activity=True,
        approved_in_progress=False,
    )

    def _c(self, **over):
        return sr.candidate(**dict(self.BASE, **over))

    def test_candidate(self):
        self.assertEqual(self._c(), (True, "candidate"))
        self.assertTrue(self._c(proposal_activity=False)[0])  # 着手済みのチケットが無い

    def test_not_candidate(self):
        for over, why in [
            ({"main_agent": False}, "not_main_agent"),
            ({"stop_hook_active": True}, "stop_hook_active"),
            ({"effective": modes.DISABLE}, "disabled"),
            ({"tickets_enabled": False}, "disabled"),
            ({"registered": False}, "disabled"),
            ({"existing_nudge": True}, "existing_nudge"),
            ({"target_unresolvable": True}, "target_unresolvable"),
            (
                {"proposal_activity": False, "approved_in_progress": True},
                "approved_in_progress",
            ),
        ]:
            with self.subTest(over=over):
                self.assertEqual(self._c(**over), (False, why))

    def test_ai_continuation_once(self):
        self.assertTrue(self._c(stop_hook_active=True, ai_continuation=True)[0])
        self.assertFalse(
            self._c(stop_hook_active=True, ai_continuation=True, already_rejudged=True)[0]
        )

    def test_unrecorded_stop_suspicion(self):
        self.assertTrue(sr.suspected_unrecorded_stop(True, False, False, False))
        self.assertFalse(sr.suspected_unrecorded_stop(True, True, False, False))
        self.assertFalse(sr.suspected_unrecorded_stop(True, False, True, False))
        self.assertFalse(sr.suspected_unrecorded_stop(True, False, False, True))
        self.assertFalse(sr.suspected_unrecorded_stop(False, False, False, False))


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name
        self.log = os.path.join(tmp.name, "logs")
        sr.configure_log(self.state, self.log)

    def test_write_plan_idempotent(self):
        first = sr.write_plan(self.state, KEY, _plan(), now=1.0)
        second = sr.write_plan(self.state, KEY, _plan(targets=["other"]), now=2.0)
        self.assertEqual(first, second)
        self.assertEqual(sr.read_plan(self.state, KEY)["targets"], [sr.STOP_TARGET, "p1"])
        self.assertEqual(sr.read_attempt(self.state, first)["state"], sr.PENDING)

    def test_non_candidate_finalized_by_ccnavi(self):
        aid = sr.write_plan(
            self.state,
            KEY,
            {"is_candidate": False, "final": sr.FINAL_OUT_OF_SCOPE, "reason": "existing_nudge"},
        )
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["state"], attempt["judge"]), (sr.DONE, sr.JUDGE_CCNAVI))
        self.assertEqual(attempt["final"], sr.FINAL_OUT_OF_SCOPE)
        self.assertEqual(sr.begin(self.state, KEY)[0], "not_candidate")

    def test_material_before_plan(self):
        state, plan = sr.begin(self.state, KEY, now=1.0)
        self.assertEqual((state, plan), ("wait", None))
        aid = sr.attempt_id(KEY)
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.PLAN_WAIT)
        sr.write_plan(self.state, KEY, _plan(), now=2.0)
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.PENDING)
        self.assertEqual(sr.begin(self.state, KEY)[0], "candidate")
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.RUNNING)

    def test_illegal_transition_refused(self):
        aid = sr.write_plan(self.state, KEY, _plan())
        attempt = sr.read_attempt(self.state, aid)
        self.assertFalse(sr._move(self.state, attempt, sr.DONE))  # pending → done は不可

    def test_expiry(self):
        aid = sr.write_plan(self.state, KEY, _plan(), now=0.0)
        self.assertEqual(sr.expire_overdue(self.state, now=10.0, ttl=60.0), [])
        self.assertEqual(sr.expire_overdue(self.state, now=100.0, ttl=60.0), [aid])
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["state"], attempt["final"]), (sr.EXPIRED, sr.FINAL_UNCHECKED))
        self.assertEqual(sr.begin(self.state, KEY)[0], "expired")

    def test_a_running_attempt_expires_as_unchecked_and_a_late_record_changes_nothing(self):
        aid = sr.write_plan(self.state, KEY, _plan(), now=0.0)
        self.assertEqual(sr.begin(self.state, KEY)[0], "candidate")
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.RUNNING)
        ttl = sr.DEFAULT_TTL_SECONDS
        self.assertEqual(sr.expire_overdue(self.state, now=ttl - 1.0), [])  # 期限の内
        self.assertEqual(sr.read_attempt(self.state, aid)["state"], sr.RUNNING)
        self.assertEqual(sr.expire_overdue(self.state, now=ttl + 1.0), [aid])
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual(
            (attempt["state"], attempt["final"], attempt["judge"], attempt["reason"]),
            (sr.EXPIRED, sr.FINAL_UNCHECKED, sr.JUDGE_CCNAVI, "expired"),
        )
        result = {
            "attempt_id": aid,
            "classification": sr.CLASS_TICKET_WORK,
            "results": [
                _row(sr.R1, sr.STOP_TARGET, sr.PASS),
                _row(sr.R3, sr.STOP_TARGET, sr.PASS),
                _row(sr.R2, "p1", sr.PASS),
                _row(sr.R4, "p1", sr.PASS),
            ],
        }
        flawed = _flawed(aid)
        for late in (result, flawed):  # 終了可でも不備検出でも、遅れた record は受理しない
            out = sr.accept(self.state, KEY, late, effective=modes.ENABLE, material_version=VERSION)
            self.assertEqual(out, {"ok": True})
            attempt = sr.read_attempt(self.state, aid)
            self.assertEqual((attempt["state"], attempt["final"]), (sr.EXPIRED, sr.FINAL_UNCHECKED))
            self.assertNotIn("accepted", attempt)

    def test_events_logged_separately(self):
        sr.write_plan(self.state, KEY, _plan())
        sr.begin(self.state, KEY)
        with open(sr.log_path(self.log), encoding="utf-8") as handle:
            events = [json.loads(line)["event"] for line in handle]
        self.assertEqual(events, [sr.RUNNING])
        self.assertFalse(os.path.exists(os.path.join(self.log, "decisions.jsonl")))


class AcceptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name

    def _running(self, **plan):
        aid = sr.write_plan(self.state, KEY, _plan(**plan), now=0.0)
        sr.begin(self.state, KEY)
        return aid

    def _accept(self, result, effective=modes.ENABLE, **kw):
        return sr.accept(
            self.state, KEY, result, effective=effective, material_version=VERSION, **kw
        )

    def test_ok(self):
        aid = self._running()
        self.assertEqual(self._accept(_passing(aid)), {"ok": True})
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["state"], attempt["final"]), (sr.DONE, sr.FINAL_OK))

    def test_flawed_enable_sends_back_with_required_text(self):
        aid = self._running()
        out = self._accept(_flawed(aid))
        self.assertFalse(out["ok"])
        for needle in (aid, "R2", "完了の確かめ方が無い", "確認の手順を足す", sr.REWRITE_QUESTION):
            self.assertIn(needle, out["reason"])
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["state"], attempt["final"]), (sr.SENT_BACK, sr.FINAL_FLAWED))

    def test_flawed_dry_run_does_not_stop(self):
        aid = self._running()
        self.assertEqual(self._accept(_flawed(aid), effective=modes.DRY_RUN), {"ok": True})
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["state"], attempt["final"]), (sr.DONE, sr.FINAL_FLAWED))

    def test_not_ticket_work_is_out_of_scope_by_ai(self):
        aid = self._running()
        result = {"attempt_id": aid, "classification": sr.CLASS_NOT_TICKET_WORK, "results": []}
        self.assertEqual(self._accept(result), {"ok": True})
        attempt = sr.read_attempt(self.state, aid)
        self.assertEqual((attempt["final"], attempt["judge"]), (sr.FINAL_OUT_OF_SCOPE, sr.JUDGE_AI))

    def test_unknown_is_unchecked(self):
        aid = self._running()
        result = _passing(aid)
        result["results"][0] = _row(sr.R1, sr.STOP_TARGET, sr.UNKNOWN)
        self.assertEqual(self._accept(result), {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, aid)["final"], sr.FINAL_UNCHECKED)

    def test_flaw_wins_over_unknown(self):
        aid = self._running()
        result = _flawed(aid)
        result["results"][0] = _row(sr.R1, sr.STOP_TARGET, sr.UNKNOWN)
        self.assertFalse(self._accept(result)["ok"])

    def test_insufficient_material_is_unchecked(self):
        aid = self._running()
        self._accept(_passing(aid), insufficient=True)
        self.assertEqual(sr.read_attempt(self.state, aid)["final"], sr.FINAL_UNCHECKED)

    def test_r3_fail_alone_is_not_a_flaw(self):
        aid = self._running()
        result = _passing(aid)
        result["results"][1] = _row(
            sr.R3, sr.STOP_TARGET, sr.FAIL, locations=[{"kind": "final_answer", "text": "x"}]
        )
        self.assertEqual(self._accept(result), {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, aid)["final"], sr.FINAL_OK)

    def test_rejections_close_unchecked_and_return_ok(self):
        cases = {
            "wrong attempt": lambda a: dict(_passing(a), attempt_id="zzz"),
            "bad classification": lambda a: dict(_passing(a), classification="?"),
            "missing criterion": lambda a: _passing(a, include_p1=False),
            "unknown target": lambda a: dict(
                _passing(a), results=_passing(a)["results"] + [_row(sr.R2, "nope", sr.PASS)]
            ),
            "fail without locations": lambda a: dict(
                _passing(a),
                results=[_row(sr.R4, "p1", sr.FAIL)] + _passing(a)["results"][:3],
            ),
            "not an object": lambda a: "text",
        }
        for name, build in cases.items():
            with self.subTest(name):
                aid = self._running()
                self.assertEqual(self._accept(build(aid)), {"ok": True})
                attempt = sr.read_attempt(self.state, aid)
                self.assertEqual(attempt["final"], sr.FINAL_UNCHECKED)
                self.assertFalse(attempt["accepted"])
                for name_ in os.listdir(self.state):
                    os.unlink(os.path.join(self.state, name_))

    def test_approved_meanwhile_is_rejected(self):
        aid = self._running()
        out = self._accept(_flawed(aid), still_unapproved=lambda target: False)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, aid)["final"], sr.FINAL_UNCHECKED)

    def test_material_version_change_is_rejected(self):
        aid = self._running()
        out = sr.accept(
            self.state, KEY, _flawed(aid), effective=modes.ENABLE, material_version="m2"
        )
        self.assertEqual(out, {"ok": True})

    def test_double_record_is_ignored(self):
        aid = self._running()
        self._accept(_passing(aid))
        self.assertEqual(self._accept(_flawed(aid)), {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, aid)["final"], sr.FINAL_OK)

    def test_non_candidate_or_no_plan_returns_ok(self):
        self.assertEqual(self._accept(_passing("x")), {"ok": True})

    def test_rejudge_resolves_original_and_never_sends_back(self):
        first = self._running()
        self.assertFalse(self._accept(_flawed(first))["ok"])
        consumed = sr.consume_continuation(self.state, "s1")
        self.assertEqual(consumed["attempt_id"], first)
        self.assertIsNone(sr.consume_continuation(self.state, "s1"))

        key2 = "s1:u-next"
        aid = sr.write_plan(self.state, key2, _plan(rejudge=True, original=first), now=1.0)
        sr.begin(self.state, key2)
        out = sr.accept(
            self.state, key2, _passing(aid), effective=modes.ENABLE, material_version=VERSION
        )
        self.assertEqual(out, {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, first)["rejudge"], "resolved")

        key3 = "s1:u-third"
        aid3 = sr.write_plan(self.state, key3, _plan(rejudge=True, original=first), now=2.0)
        sr.begin(self.state, key3)
        out = sr.accept(
            self.state, key3, _flawed(aid3), effective=modes.ENABLE, material_version=VERSION
        )
        self.assertEqual(out, {"ok": True})  # 再判定では差し戻さない
        self.assertEqual(sr.read_attempt(self.state, first)["rejudge"], "unresolved")

    def test_sent_back_not_expired_and_other_session_not_consumed(self):
        first = self._running()
        self._accept(_flawed(first))
        self.assertEqual(sr.expire_overdue(self.state, now=10**9), [])
        self.assertIsNone(sr.consume_continuation(self.state, "other"))


if __name__ == "__main__":
    unittest.main()
