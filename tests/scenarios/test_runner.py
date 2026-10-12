"""受入シナリオの実行器の単体テスト。claude は呼ばない（呼べない門をここで確かめる）。

シナリオを回すこと自体は、全量テストに入れない（docs/claude/acceptance-scenarios.md）。
ここで確かめるのは、実行器の判定の部分と、シナリオの YAML の形、そして全量テストが
シナリオを拾わないこと。
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import __main__ as full_run
from tests.scenarios import runner

SCENARIOS = sorted(Path(__file__).parent.glob("UC-*.yml"))
EXPECT_KEYS = {
    "decisions",
    "decisions_none",
    "files_unchanged",
    "state",
    "transcript_forbidden",
    "judge",
}


def rec(**kw):
    base = {"event": "PreToolUse", "tool": "Bash", "session": "s1"}
    base.update(kw)
    return base


class CheckDecisionTest(unittest.TestCase):
    def test_pass_when_decision_matches(self):
        rows = [rec(decision="deny", code="DENY_PATH", rules=["workspace-root"], subject="/a/b")]
        cond = {"decision": "deny", "code": "DENY_PATH", "rules_include": ["workspace-root"]}
        self.assertEqual(runner.check_decision(rows, cond)[0], runner.PASS)

    def test_inconclusive_when_call_never_made(self):
        rows = [rec(tool="Read", decision="handover")]
        v, _ = runner.check_decision(rows, {"tool": "Edit", "decision": "deny"})
        self.assertEqual(v, runner.INCONCLUSIVE)

    def test_fail_when_call_made_but_allowed(self):
        rows = [rec(tool="Edit", decision="allow", subject="/x/README.md")]
        v, why = runner.check_decision(rows, {"tool": "Edit", "decision": "deny"})
        self.assertEqual(v, runner.FAIL)
        self.assertIn("allow", why)

    def test_subject_contains_selects_the_call(self):
        rows = [
            rec(decision="allow", subject="cat a.txt"),
            rec(decision="deny", subject="cat .env", rules=["credentials"]),
        ]
        cond = {"subject_contains": "cat .env", "decision": "deny"}
        self.assertEqual(runner.check_decision(rows, cond)[0], runner.PASS)

    def test_min_count(self):
        rows = [rec(decision="deny"), rec(decision="deny")]
        self.assertEqual(
            runner.check_decision(rows, {"decision": "deny", "min_count": 3})[0], runner.FAIL
        )
        self.assertEqual(
            runner.check_decision(rows, {"decision": "deny", "min_count": 2})[0], runner.PASS
        )

    def test_decision_not(self):
        rows = [rec(decision="handover", subject="ccnavi-git.sh status")]
        cond = {"subject_contains": "ccnavi-git.sh status", "decision_not": "deny"}
        self.assertEqual(runner.check_decision(rows, cond)[0], runner.PASS)

    def test_hinted_true_and_false(self):
        rows = [
            rec(decision="allow", subject="cat a", hinted=True),
            rec(decision="allow", subject="cat b"),
        ]
        first = {"subject_contains": "cat a"}
        second = {"subject_contains": "cat b"}
        self.assertEqual(runner.check_decision(rows, {**first, "hinted": True})[0], runner.PASS)
        self.assertEqual(runner.check_decision(rows, {**first, "hinted": False})[0], runner.FAIL)
        self.assertEqual(runner.check_decision(rows, {**second, "hinted": False})[0], runner.PASS)
        self.assertEqual(runner.check_decision(rows, {**second, "hinted": True})[0], runner.FAIL)

    def test_decisions_none(self):
        rows = [rec(decision="deny", rules=["workspace-root"], tool="Edit")]
        cond = {"tool": "Edit", "decision": "deny", "rules_include": ["workspace-root"]}
        self.assertEqual(runner.check_decision_none(rows, cond)[0], runner.FAIL)
        self.assertEqual(runner.check_decision_none([], cond)[0], runner.PASS)

    def test_decisions_none_subject_is_clipped_with_notice(self):
        rows = [rec(decision="deny", tool="Edit", subject="s" * 300)]
        v, why = runner.check_decision_none(rows, {"tool": "Edit", "decision": "deny"})
        self.assertEqual(v, runner.FAIL)
        self.assertIn("全 300 文字のうち先頭 200 文字。全量は logs/decisions.jsonl", why)


class ReadDecisionsTest(unittest.TestCase):
    def test_only_the_given_sessions_are_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            lines = [rec(session="mine"), rec(session="other"), rec(session="mine2")]
            p.write_text("\n".join(json.dumps(r) for r in lines) + "\nnot json\n", encoding="utf-8")
            rows = runner.read_decisions(p, ["mine", "mine2"])
        self.assertEqual([r["session"] for r in rows], ["mine", "mine2"])

    def test_missing_log_is_empty(self):
        self.assertEqual(runner.read_decisions("/no/such/file.jsonl", ["s"]), [])


class ParseStreamTest(unittest.TestCase):
    def test_extracts_evidence_and_skips_bad_lines(self):
        events = [
            {"type": "system", "subtype": "init", "session_id": "abc"},
            {
                "type": "assistant",
                "session_id": "abc",
                "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"x": 1}}]},
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "is_error": True, "content": "no"}]
                },
            },
            {"type": "result", "session_id": "abc", "result": "終わり", "total_cost_usd": 0.01},
        ]
        text = "\n".join(json.dumps(e) for e in events) + "\nnoise\n"
        got = runner.parse_stream(text)
        self.assertEqual(got["session_ids"], ["abc"])
        self.assertEqual(got["tool_calls"][0]["name"], "Edit")
        self.assertTrue(got["tool_results"][0]["is_error"])
        self.assertEqual(got["final_text"], "終わり")
        self.assertAlmostEqual(got["cost_usd"], 0.01)


class EvaluateTest(unittest.TestCase):
    def test_overall_orders_fail_over_inconclusive_over_review(self):
        mk = lambda *vs: [("k", "c", v, "") for v in vs]  # noqa: E731
        self.assertEqual(runner.overall(mk(runner.PASS, runner.PASS)), runner.PASS)
        self.assertEqual(runner.overall(mk(runner.PASS, runner.REVIEW)), runner.REVIEW)
        self.assertEqual(
            runner.overall(mk(runner.REVIEW, runner.INCONCLUSIVE)), runner.INCONCLUSIVE
        )
        self.assertEqual(runner.overall(mk(runner.INCONCLUSIVE, runner.FAIL)), runner.FAIL)

    def test_files_unchanged_and_forbidden_and_judge(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("1", encoding="utf-8")
            pre = {"a.txt": runner.file_digest(Path(d) / "a.txt")}
            expect = {
                "files_unchanged": ["a.txt"],
                "transcript_forbidden": ["secret-value"],
                "judge": ["迂回しなかった"],
            }
            got = runner.evaluate(expect, [], "出力に secret-value がある", d, pre)
            by_kind = {k: v for k, _, v, _ in got}
            self.assertEqual(by_kind["files_unchanged"], runner.PASS)
            self.assertEqual(by_kind["transcript_forbidden"], runner.FAIL)
            self.assertEqual(by_kind["judge"], runner.REVIEW)
            (Path(d) / "a.txt").write_text("2", encoding="utf-8")
            got = runner.evaluate({"files_unchanged": ["a.txt"]}, [], "", d, pre)
            self.assertEqual(got[0][2], runner.FAIL)

    def test_state_command(self):
        with tempfile.TemporaryDirectory() as d:
            ok = {"run": "echo hello", "expect_contains": "hello"}
            ng = {"run": "echo hello", "expect_not_contains": "hello"}
            self.assertEqual(runner.check_state(ok, d)[0], runner.PASS)
            self.assertEqual(runner.check_state(ng, d)[0], runner.FAIL)


class FinishOrderTest(unittest.TestCase):
    """finish は state を確かめてから setup のファイルを消す。"""

    REL = "sub/keep.txt"

    def run_finish(self, root, out, scn):
        st = {
            "setup_made": runner._setup(scn, root),
            "session_ids": [],
            "pre_digests": {},
            "cost_usd": 0.0,
        }
        return runner.finish(scn, out, st, root)

    def scenario(self):
        return {
            "id": "UC-XX",
            "title": "t",
            "setup": {"files": {self.REL: "x\n"}},
            "expect": {"state": [{"run": f"cat {self.REL}", "expect_contains": "x"}]},
        }

    def test_state_sees_setup_files(self):
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "root", Path(d) / "out"
            root.mkdir()
            out.mkdir()
            code = self.run_finish(root, out, self.scenario())
            self.assertEqual(code, 0)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report[0]["verdict"], runner.PASS)


class PreflightTest(unittest.TestCase):
    def test_missing_project_dir_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            facts = runner.preflight_facts(d)
        self.assertIn("置き場が無い", facts)


class PersistTest(unittest.TestCase):
    def test_session_is_kept_only_when_the_next_prompt_resumes(self):
        steps = [{"prompt": "a"}, {"human": True}, {"prompt": "b", "resume": True}, {"prompt": "c"}]
        self.assertTrue(runner._persist_needed(steps, 0))
        self.assertTrue(runner._persist_needed(steps, 1))
        self.assertFalse(runner._persist_needed(steps, 2))
        self.assertFalse(runner._persist_needed(steps, 3))


class EntryGateTest(unittest.TestCase):
    """全量テストなど、`python -m tests.scenarios.runner` 以外から claude を呼べないこと。"""

    def test_run_step_refuses_when_imported(self):
        with mock.patch.object(subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                runner.run_step({}, {"prompt": "x"}, Path("."), 1, "id", None, False, ".")
            run.assert_not_called()

    def test_cmd_run_refuses_when_imported(self):
        with mock.patch.object(subprocess, "run") as run:
            args = mock.Mock(scenario="x", confirmed=True, root=".", out=None)
            with self.assertRaises(RuntimeError):
                runner.cmd_run(args)
            run.assert_not_called()


class FullRunDoesNotPickScenariosTest(unittest.TestCase):
    """`python -m tests`（全量）が拾うのは、この実行器の単体テストだけ。"""

    def test_only_this_module_is_collected_from_scenarios(self):
        mine = [m for m in full_run.modules([]) if m.startswith("tests.scenarios")]
        self.assertEqual(mine, ["tests.scenarios.test_runner"])

    def test_no_other_test_module_in_scenarios(self):
        extra = [
            p.name for p in Path(__file__).parent.glob("test_*.py") if p.name != "test_runner.py"
        ]
        self.assertEqual(extra, [], "シナリオを回す道を test_ のモジュールに置かない")


class ScenarioFilesTest(unittest.TestCase):
    def test_scenarios_exist(self):
        self.assertTrue(SCENARIOS)

    def test_each_scenario_is_well_formed(self):
        for path in SCENARIOS:
            with self.subTest(scenario=path.name):
                scn = runner.load_scenario(path)
                self.assertTrue(path.name.startswith(scn["id"]))
                self.assertIn(scn["status"], {"draft", "verified"})
                self.assertTrue(scn["steps"])
                self.assertFalse(set(scn["expect"]) - EXPECT_KEYS, "知らない期待の欄がある")
                self.assertTrue(any(scn["expect"].get(k) for k in EXPECT_KEYS), "期待が 1 つも無い")
                self.assertNotIn("transcript_required", scn["expect"])
                for step in scn["steps"]:
                    self.assertTrue(
                        step.get("human") or step.get("prompt"), "step に prompt も human も無い"
                    )
                    if step.get("human"):
                        self.assertTrue(step.get("instruction"))


class EvidenceTest(unittest.TestCase):
    def _hook(self, event, name, stdout="", stderr=""):
        return {
            "type": "system",
            "subtype": "hook_response",
            "hook_event": event,
            "hook_name": name,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _write(self, events, results=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        (out / "step-1.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8"
        )
        scn = {"id": "UC-99", "title": "t", "expect": {"judge": ["基準A"]}}
        st = {"cost_usd": 0.0}
        results = results or [("files_unchanged", "README.md", runner.PASS, "変わっていない")]
        runner.write_report(scn, out, st, results, [], "")
        return (out / "evidence.md").read_text(encoding="utf-8")

    def test_deny_reason_is_not_clipped(self):
        reason = "拒否の理由" * 300
        payload = {
            "hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": reason}
        }
        text = self._write(
            [self._hook("PreToolUse", "guard", json.dumps(payload, ensure_ascii=False))]
        )
        self.assertIn(reason, text)

    def test_session_start_and_empty_hooks_are_only_counted(self):
        payload = {"hookSpecificOutput": {"additionalContext": "長い前置き" * 50}}
        text = self._write(
            [
                self._hook("SessionStart", "start", json.dumps(payload, ensure_ascii=False)),
                self._hook("SessionStart", "start2"),
                self._hook("PostToolUse", "empty"),
            ]
        )
        self.assertNotIn("長い前置き", text)
        self.assertIn("PostToolUse 1 件", text)
        self.assertIn("SessionStart 2 件", text)

    def test_additional_context_and_stop_are_kept(self):
        ctx = {"hookSpecificOutput": {"additionalContext": "git の入口は…"}}
        text = self._write(
            [
                self._hook("PreToolUse", "guide", json.dumps(ctx, ensure_ascii=False)),
                self._hook("Stop", "stop", stdout="止める理由"),
            ]
        )
        self.assertIn("案内: git の入口は…", text)
        self.assertIn("止める理由", text)

    def test_long_context_says_it_was_clipped(self):
        ctx = {"hookSpecificOutput": {"additionalContext": "あ" * 5000}}
        text = self._write([self._hook("PreToolUse", "guide", json.dumps(ctx, ensure_ascii=False))])
        self.assertIn("全 5000 文字のうち先頭 2000 文字。全量は step-1.jsonl", text)

    def test_tool_results_include_success_and_machine_results_come_first(self):
        events = [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "is_error": False, "content": "working tree clean"}
                    ]
                },
            }
        ]
        text = self._write(events)
        self.assertIn("- 成功: working tree clean", text)
        self.assertLess(text.index("## 機械判定の結果"), text.index("judge の基準"))
        self.assertIn("| files_unchanged | README.md | ", text)

    def test_machine_result_table_in_evidence_is_not_clipped(self):
        why = "理由" * 100
        text = self._write([], results=[("state", "ls", runner.FAIL, why)])
        self.assertIn(why, text)

    def test_decision_subject_clipped_with_notice(self):
        # write_report の rows 引数を使うため、直接呼ぶ
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            scn = {"id": "UC-99", "title": "t", "expect": {}}
            rows = [
                {"event": "PreToolUse", "tool": "Bash", "decision": "deny", "subject": "s" * 800}
            ]
            runner.write_report(scn, out, {"cost_usd": 0.0}, [], rows, "")
            text = (out / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("全 800 文字のうち先頭 500 文字。全量は logs/decisions.jsonl", text)

    def test_nonzero_exit_stderr_is_kept_unclipped(self):
        ev = self._hook("PreToolUse", "g", stderr="削除は禁止です" * 300)
        ev["exit_code"] = 2
        text = self._write([ev])
        self.assertIn("終了コード 2: " + "削除は禁止です" * 300, text)

    def test_system_message_and_stop_reason_are_kept(self):
        sysmsg = json.dumps({"systemMessage": "保護領域が変わった"}, ensure_ascii=False)
        halt = json.dumps({"continue": False, "stopReason": "止めた理由"}, ensure_ascii=False)
        text = self._write(
            [self._hook("PostToolUse", "p", sysmsg), self._hook("PreToolUse", "q", halt)]
        )
        self.assertIn("通知: 保護領域が変わった", text)
        self.assertIn("停止: 止めた理由", text)

    def test_session_start_system_message_is_only_counted(self):
        sysmsg = json.dumps({"systemMessage": "起動の通知"}, ensure_ascii=False)
        text = self._write([self._hook("SessionStart", "s", sysmsg)])
        self.assertNotIn("起動の通知", text)
        self.assertIn("SessionStart 1 件", text)

    def test_result_table_cells_do_not_break_rows(self):
        why = "出力に「a」が無い: l1\nl2 | l3"
        text = self._write([], results=[("state", "ls", runner.FAIL, why)])
        self.assertIn("l1 l2 \\| l3", text)

    def test_non_text_block_in_tool_result_is_marked(self):
        events = [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "is_error": False,
                            "content": [{"type": "image"}, {"type": "text", "text": "本文"}],
                        }
                    ]
                },
            }
        ]
        text = self._write(events)
        self.assertIn("[image ブロック]", text)
        self.assertIn("本文", text)


class ResultsTableTest(unittest.TestCase):
    def test_limit_cuts_cells_and_none_does_not(self):
        results = [("k", "c" * 300, runner.FAIL, "w" * 300)]
        cut = "\n".join(runner._results_table(results, 100))
        self.assertIn("c" * 100 + " |", cut)
        self.assertNotIn("c" * 101, cut)
        self.assertNotIn("w" * 101, cut)
        full = "\n".join(runner._results_table(results))
        self.assertIn("c" * 300, full)
        self.assertIn("w" * 300, full)


if __name__ == "__main__":
    unittest.main()
