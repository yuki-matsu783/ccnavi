"""Stop の AI 判定: 判定用 Markdown・材料の保存・issue／MR の指定・疑い・集計・後始末。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest

from ccnavi.entry import cli_stopreview
from ccnavi.hook import events
from ccnavi.infra import hookio, modes, settings
from ccnavi.records import prune, stopeval, stopops
from ccnavi.records import stopreview as sr

ROWS = [
    {"type": "user", "message": {"content": "#12 を実装して"}},
    {
        "type": "assistant",
        "uuid": "a1",
        "message": {"content": [{"type": "text", "text": "作る?"}]},
    },
]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.state = os.path.join(self.root, "logs", "state")
        os.makedirs(self.state)
        self.log_file = os.path.join(self.root, "logs", "decisions.jsonl")
        self.transcript = os.path.join(self.root, "t.jsonl")
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in ROWS)
        self.conf = settings.Settings(
            state=self.state,
            log=self.log_file,
            stop_review_agent=True,
            tickets="wip/proposals",
        )
        self.payload = hookio.Input(
            event=hookio.STOP, session_id="s1", transcript_path=self.transcript, cwd=self.root
        )
        self.key = sr.shared_key("s1", self.transcript)

    def plan(self, payload=None, nudged=False, continuation=None):
        events._plan_stop_review(
            io.StringIO(), self.conf, self.root, payload or self.payload,
            modes.ENABLE, nudged, continuation, None,
        )  # fmt: skip
        return sr.read_plan(self.state, self.key)

    def events_text(self):
        path = os.path.join(self.root, "logs", "stop-review", "events.jsonl")
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class CriteriaFileTest(Base):
    def test_default_without_a_file(self):
        self.assertEqual(sr.load_criteria(self.root), (sr.CRITERIA_MD, "default"))

    def test_workspace_file_is_the_definition(self):
        path = os.path.join(self.root, sr.CRITERIA_FILE)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# 独自の基準\n## R5\n新しい基準\n")
        text, source = sr.load_criteria(self.root)
        self.assertEqual((source, "R5" in text), ("file", True))
        plan = self.plan()
        self.assertEqual(plan["material_version"], sr.material_version(text))
        self.assertNotEqual(plan["material_version"], sr.material_version())

    def test_unreadable_definition_is_unchecked_and_not_guessed(self):
        path = os.path.join(self.root, sr.CRITERIA_FILE)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("   \n")
        self.assertEqual(sr.load_criteria(self.root), ("", "file"))
        plan = self.plan()
        self.assertFalse(plan["is_candidate"])
        attempt = sr.read_attempt(self.state, plan["attempt_id"])
        self.assertEqual(attempt["final"], sr.FINAL_UNCHECKED)

    def test_definition_changed_after_the_plan_rejects_the_record(self):
        plan = self.plan()
        sr.begin(self.state, self.key)
        path = os.path.join(self.root, sr.CRITERIA_FILE)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# 途中で変えた\n")
        result = {
            "attempt_id": plan["attempt_id"],
            "classification": sr.CLASS_TICKET_WORK,
            "results": [
                {"criterion": sr.R1, "target": sr.STOP_TARGET, "result": sr.PASS},
                {"criterion": sr.R3, "target": sr.STOP_TARGET, "result": sr.PASS},
            ],
        }
        out = io.StringIO()
        cli_stopreview._record(io.StringIO(json.dumps(result)), out, self.conf, self.key, self.root)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True})
        self.assertEqual(sr.read_attempt(self.state, plan["attempt_id"])["accepted"], False)


class MaterialStoreTest(Base):
    def test_material_is_saved_redacted_and_the_judge_gets_the_raw_text(self):
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"type": "user", "message": {"content": f"GITHUB_TOKEN={secret} で"}})
                + "\n"
            )
        plan = self.plan()
        out = io.StringIO()
        cli_stopreview.run(
            io.StringIO(), out, io.StringIO(), self.conf,
            ["stop-review", "material", "s1", self.transcript], self.root,
        )  # fmt: skip
        self.assertIn(secret, out.getvalue())
        saved = os.path.join(self.root, "logs", sr.MATERIAL_DIR, f"{plan['attempt_id']}.json")
        with open(saved, encoding="utf-8") as handle:
            row = json.load(handle)
        self.assertNotIn(secret, row["material"])
        self.assertGreaterEqual(row["redactions"], 1)
        self.assertEqual(row["extractor"], sr.EXTRACTOR_VERSION)

    def test_events_go_next_to_the_decision_log(self):
        self.plan()
        sr.begin(self.state, self.key)
        self.assertIn('"event": "running"', self.events_text())
        self.assertFalse(os.path.exists(self.log_file))  # decisions.jsonl には書かない


class RefsAndSubmittedTest(Base):
    def _prompt(self, text):
        events._track_proposals(
            self.conf,
            hookio.Input(event=hookio.USER_PROMPT_SUBMIT, session_id="s1", prompt=text),
        )

    def test_issue_and_mr_references_reach_the_material(self):
        self._prompt("#12 と !5 を実装して")
        plan = self.plan()
        self.assertEqual(len(plan["refs"]), 2)
        out = io.StringIO()
        cli_stopreview.run(
            io.StringIO(), out, io.StringIO(), self.conf,
            ["stop-review", "material", "s1", self.transcript], self.root,
        )  # fmt: skip
        self.assertIn("<data kind=issue_mr_refs>", out.getvalue())
        for label in plan["refs"]:
            self.assertIn(label, out.getvalue())

    def test_no_reference_is_said_plainly(self):
        plan = self.plan()
        self.assertEqual(plan["refs"], [])

    def test_submitted_proposals_are_listed_with_their_approval_state(self):
        now = int(time.time())
        directory = os.path.join(self.state, stopops.RESULT_DIR)
        os.makedirs(directory)
        with open(os.path.join(directory, "k.kv"), "w", encoding="utf-8") as handle:
            handle.write(f"id=p1\nroute=sent\nrc=0\nstarted={now}\nended={now}\ncommit=abc\n")
        stopops.register(self.state, "s1", "tu", "p1", now - 1)
        stopops.bind(self.state, "tu", now + 1)
        plan = self.plan()
        self.assertEqual(
            plan["submitted"], [{"ident": "p1", "approved": False, "in_progress": False}]
        )
        out = io.StringIO()
        cli_stopreview.run(
            io.StringIO(), out, io.StringIO(), self.conf,
            ["stop-review", "material", "s1", self.transcript], self.root,
        )  # fmt: skip
        self.assertIn("p1: 承認待ち", out.getvalue())


class SuspicionTest(Base):
    def test_unrecorded_continuation_is_logged_once_per_stop(self):
        self.plan()
        again = hookio.Input(
            event=hookio.STOP, session_id="s1", transcript_path=self.transcript,
            cwd=self.root, stop_hook_active=True,
        )  # fmt: skip
        self.plan(payload=again)
        self.assertEqual(self.events_text().count("suspected_unrecorded_stop"), 1)

    def test_nudge_before_explains_the_continuation(self):
        self.plan(nudged=True)
        again = hookio.Input(
            event=hookio.STOP, session_id="s1", transcript_path=self.transcript,
            cwd=self.root, stop_hook_active=True,
        )  # fmt: skip
        self.plan(payload=again)
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "logs", "stop-review", "events.jsonl"))
            and "suspected" in self.events_text()
        )


class EvalTest(Base):
    def _attempt(self, aid, final, judge=sr.JUDGE_AI, results=None, **extra):
        row = {"attempt_id": aid, "key": f"s1:{aid}", "state": sr.DONE, "final": final,
               "judge": judge, "accepted": True, "results": results or []}  # fmt: skip
        row.update(extra)
        with open(os.path.join(self.state, f"stopreview-attempt-{aid}.json"), "w") as handle:
            json.dump(row, handle)

    def _result(self, criterion, outcome, target="stop"):
        return {"criterion": criterion, "target": target, "result": outcome}

    def _labels(self, *rows):
        directory = os.path.join(self.root, "logs", "stop-review")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, stopeval.LABELS_FILE), "w") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in rows)

    def test_metrics(self):
        flaw = [self._result("R1", "fail", "stop"), self._result("R3", "pass")]
        self._attempt("a1", sr.FINAL_FLAWED, results=flaw)  # 不備あり、検出した
        self._attempt("a2", sr.FINAL_UNCHECKED)  # 不備あり、見逃した（未検査）
        self._attempt("a3", sr.FINAL_FLAWED, results=flaw)  # 不備なしなのに検出した
        self._attempt("a4", sr.FINAL_OK, results=[self._result("R1", "pass")])  # 正常
        self._attempt("a5", sr.FINAL_OUT_OF_SCOPE, judge=sr.JUDGE_CCNAVI)
        self._attempt("a6", sr.FINAL_OUT_OF_SCOPE)
        self._attempt("a7", sr.FINAL_SKIPPED, judge=sr.JUDGE_CCNAVI)
        self._labels(
            {"attempt_id": "a1", "criterion": "R1", "target": "stop", "truth": "flaw"},
            {"attempt_id": "a2", "criterion": "R1", "target": "stop", "truth": "flaw"},
            {"attempt_id": "a3", "criterion": "R1", "target": "stop", "truth": "ok"},
            {"attempt_id": "a4", "criterion": "R1", "target": "stop", "truth": "ok"},
        )
        summary = stopeval.summarize(self.state, os.path.join(self.root, "logs"))
        r1 = summary["criteria"]["R1"]
        self.assertEqual(r1["unit"], "stop")
        self.assertEqual(r1["miss_rate"], 0.5)
        self.assertEqual(r1["false_block_rate"], 0.5)
        self.assertEqual(r1["needless_rate"], 0.5)
        self.assertIsNone(summary["criteria"]["R2"]["miss_rate"])
        self.assertEqual(summary["criteria"]["R4"]["unit"], "proposal_version")
        counts = summary["counts"]
        self.assertEqual(
            (counts["ccnavi_out_of_scope"], counts["ai_out_of_scope"], counts["skipped"]),
            (1, 1, 1),
        )
        self.assertAlmostEqual(summary["coverage"], 3 / 4)

    def test_fix_rate_and_counters(self):
        self._attempt("b1", sr.FINAL_FLAWED, state=sr.CONSUMED, rejudge="resolved")
        self._attempt("b2", sr.FINAL_FLAWED, state=sr.CONSUMED, rejudge="unresolved")
        self._attempt("b3", sr.FINAL_FLAWED, state=sr.SENT_BACK)
        self._attempt("b4", sr.FINAL_UNCHECKED, accepted=False, tainted=True)
        summary = stopeval.summarize(self.state, os.path.join(self.root, "logs"))
        self.assertEqual(
            summary["fix"], {"sent_back": 3, "resolved": 1, "unresolved": 1, "not_rejudged": 1}
        )
        self.assertAlmostEqual(summary["fix_rate"], 1 / 3)
        self.assertEqual((summary["counts"]["rejected"], summary["counts"]["tainted"]), (1, 1))

    def test_render_says_unknown_for_missing_numbers(self):
        text = stopeval.render(stopeval.summarize(self.state, os.path.join(self.root, "logs")))
        self.assertIn("不明", text)
        self.assertIn("費用・待ち時間: 不明", text)

    def test_report_command(self):
        out = io.StringIO()
        code = cli_stopreview.run(
            io.StringIO(), out, io.StringIO(), self.conf, ["stop-review", "report"]
        )
        self.assertEqual(code, 0)
        self.assertIn("試行 0 件", out.getvalue())


class PruneTest(Base):
    def test_stop_review_state_is_cleaned_by_its_own_date(self):
        old = time.time() - 40 * 86400
        names = [
            "stopreview-plan-abc.json", "stopreview-attempt-abc.json", "stopops-pre-t1.json",
            "stopops-op-t1.json", "stopturn-s9.json", "stoprefs-s9.json",
            "stoptaint-s9.json", "stoplast-s9.json",
        ]  # fmt: skip
        os.makedirs(os.path.join(self.state, stopops.RESULT_DIR))
        names.append(os.path.join(stopops.RESULT_DIR, "1-2.kv"))
        for name in names:
            path = os.path.join(self.state, name)
            with open(path, "w") as handle:
                handle.write("{}")
            os.utime(path, (old, old))
        material = os.path.join(self.root, "logs", sr.MATERIAL_DIR)
        os.makedirs(material)
        keep = os.path.join(material, "keep.json")
        with open(keep, "w") as handle:
            handle.write("{}")
        os.utime(keep, (old, old))  # 40 日前: 材料の保持（既定 90 日）の内
        prune.run(self.root, self.log_file, self.state, session="other", now=time.time())
        left = sorted(os.listdir(self.state))
        self.assertEqual([n for n in left if n.startswith("stop") and n != stopops.RESULT_DIR], [])
        self.assertEqual(os.listdir(os.path.join(self.state, stopops.RESULT_DIR)), [])
        self.assertTrue(os.path.exists(keep))
        prune.run(self.root, self.log_file, self.state, now=time.time() + 60 * 86400)
        self.assertFalse(os.path.exists(keep))

    def test_current_session_is_kept(self):
        old = time.time() - 40 * 86400
        path = os.path.join(self.state, "stoptaint-mine.json")
        with open(path, "w") as handle:
            handle.write("{}")
        os.utime(path, (old, old))
        prune.run(self.root, self.log_file, self.state, session="mine", now=time.time())
        self.assertTrue(os.path.exists(path))


class LintTest(Base):
    def _settings_json(self, hooks):
        os.makedirs(os.path.join(self.root, ".claude"), exist_ok=True)
        with open(os.path.join(self.root, ".claude", "settings.json"), "w") as handle:
            json.dump({"hooks": {"Stop": [{"hooks": hooks}]}}, handle)

    def _details(self):
        from ccnavi.entry import lint

        return [p.detail for p in lint.stop_review_problems(self.root, self.conf)]

    def test_quiet_when_not_used(self):
        self.conf.stop_review_agent = False
        self.assertEqual(self._details(), [])

    def test_bad_value_is_named(self):
        self.conf.stop_review_agent = False
        self.conf.stop_review = "enabled"
        self.assertTrue(any("enabled" in d for d in self._details()))

    def test_marker_without_hook_and_hook_without_marker(self):
        self.assertTrue(any("見つからない" in d for d in self._details()))
        self._settings_json([{"type": "agent", "prompt": "判定"}])
        self.assertEqual(self._details(), [])
        self.conf.stop_review_agent = False
        self.assertTrue(any("計画が作られず" in d for d in self._details()))

    def test_duplicate_hooks(self):
        self._settings_json([{"type": "agent", "prompt": "a"}, {"type": "agent", "prompt": "b"}])
        self.assertTrue(any("2 件" in d for d in self._details()))

    def test_other_hooks_are_not_counted(self):
        self._settings_json([{"type": "command", "command": "x"}])
        self.assertTrue(any("見つからない" in d for d in self._details()))

    def test_enable_needs_the_judge_value(self):
        self._settings_json([{"type": "agent", "prompt": "a"}])
        self.conf.stop_review = "enable"
        self.assertTrue(any("JUDGE_TYPE" in d for d in self._details()))
        self.conf.stop_review_judge = "stop-judge"
        self.assertEqual(self._details(), [])


if __name__ == "__main__":
    unittest.main()
