"""受入シナリオの実行器の単体テスト。claude は呼ばない（呼べない門をここで確かめる）。

シナリオを回すこと自体は、全量テストに入れない（docs/42_claude/acceptance-scenarios.md）。
ここで確かめるのは、実行器の判定の部分と、シナリオの YAML の形、そして全量テストが
シナリオを拾わないこと。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

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


class IssueCommandTest(unittest.TestCase):
    ISSUE = {"title": "題", "body": "本文", "repo": "projects/test-repo"}

    def test_gitlab_uses_glab(self):
        self.assertEqual(
            runner.issue_create_args("gitlab", self.ISSUE),
            ["glab", "issue", "create", "--title", "題", "--description", "本文", "--yes"],
        )
        self.assertEqual(runner.issue_close_args("gitlab", 12), ["glab", "issue", "close", "12"])

    def test_github_uses_gh(self):
        self.assertEqual(
            runner.issue_create_args("github", self.ISSUE),
            ["gh", "issue", "create", "--title", "題", "--body", "本文"],
        )
        self.assertEqual(runner.issue_close_args("github", 12), ["gh", "issue", "close", "12"])

    def test_missing_body_is_empty(self):
        args = runner.issue_create_args("gitlab", {"title": "t"})
        self.assertEqual(args[args.index("--description") + 1], "")

    def test_unreadable_origin_is_an_error(self):
        with self.assertRaises(RuntimeError):
            runner.issue_create_args("", self.ISSUE)
        with self.assertRaises(RuntimeError):
            runner.issue_close_args("", 1)

    def test_issue_number_from_github_url(self):
        self.assertEqual(runner.issue_number("https://github.com/o/r/issues/7\n"), 7)

    def test_issue_number_from_gitlab_url(self):
        url = "https://gitlab.example.com/g/p/-/issues/12"
        self.assertEqual(runner.issue_number(url + "\n"), 12)

    def test_issue_number_finds_the_url_line(self):
        out = "Creating issue in g/p\n\nhttps://gitlab.example.com/g/p/-/issues/34\nopen it later\n"
        self.assertEqual(runner.issue_number(out), 34)

    def test_issue_number_ignores_other_urls(self):
        out = "https://gl.example.com/g/p/-/issues/12\nsee https://docs.example.com/9\n"
        self.assertEqual(runner.issue_number(out), 12)

    def test_issue_number_rejects_output_without_url(self):
        with self.assertRaises(RuntimeError):
            runner.issue_number("created 5\n")


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
        files, dirs = runner._setup(scn, root)
        st = {
            "setup_made": files,
            "setup_dirs": dirs,
            "session_ids": [],
            "pre_digests": {},
            "pre_repo": runner.repo_snapshot(root),
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


class ExpectAfterTest(unittest.TestCase):
    """step の expect_after を満たさなければ、後続の step に進まず止める。claude は呼ばない。"""

    STEP_OUT = {"cost_usd": 0.0, "session_ids": []}

    def scenario(self, steps):
        return {"steps": steps, "_prompt_steps": len(steps)}

    def state(self):
        return {"next_step": 0, "session_ids": [], "cost_usd": 0.0}

    def test_evaluate_after_inconclusive_when_call_missing(self):
        with tempfile.TemporaryDirectory() as d:
            expect = {"decisions": [{"event": "PreToolUse", "tool": "Edit", "decision": "allow"}]}
            got = runner.evaluate_after(expect, {"session_ids": ["s1"]}, d)
        self.assertEqual(got[0][2], runner.INCONCLUSIVE)

    def test_drive_halts_and_skips_later_steps(self):
        with tempfile.TemporaryDirectory() as d:
            out, root = Path(d) / "out", Path(d) / "root"
            out.mkdir()
            root.mkdir()
            scn = self.scenario(
                [
                    {
                        "prompt": "a",
                        "expect_after": {"state": [{"run": "true", "expect_contains": "zzz"}]},
                    },
                    {"prompt": "b"},
                ]
            )
            st = self.state()
            with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT) as run:
                rc = runner.drive(scn, out, st, root)
        self.assertEqual(rc, 11)
        self.assertEqual(st["halt"]["step"], 1)
        self.assertEqual(run.call_count, 1)

    def test_drive_without_expect_after_runs_all(self):
        with tempfile.TemporaryDirectory() as d:
            out, root = Path(d) / "out", Path(d) / "root"
            out.mkdir()
            root.mkdir()
            scn = self.scenario([{"prompt": "a"}, {"prompt": "b"}])
            with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT) as run:
                rc = runner.drive(scn, out, self.state(), root)
        self.assertIsNone(rc)
        self.assertEqual(run.call_count, 2)

    def test_drive_halts_when_evaluation_raises(self):
        with tempfile.TemporaryDirectory() as d:
            out, root = Path(d) / "out", Path(d) / "root"
            out.mkdir()
            root.mkdir()
            scn = self.scenario(
                [
                    {
                        "prompt": "a",
                        "expect_after": {"state": [{"run": "true", "expect_contains": "zzz"}]},
                    },
                    {"prompt": "b"},
                ]
            )
            st = self.state()
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT) as run,
                mock.patch.object(
                    runner,
                    "check_state",
                    side_effect=subprocess.TimeoutExpired("x", 1),
                ),
            ):
                rc = runner.drive(scn, out, st, root)
        self.assertEqual(rc, 11)
        self.assertIn("評価で例外", st["halt"]["reasons"][0])
        self.assertEqual(run.call_count, 1)

    def test_finish_drops_decisions_and_state_when_halted_and_reports_inconclusive(self):
        scn = {
            "id": "T",
            "title": "t",
            "steps": [],
            "expect": {"state": [{"run": "true", "expect_contains": "zzz"}]},
        }
        st = {
            "setup_made": [],
            "session_ids": [],
            "pre_digests": {},
            "cost_usd": 0.0,
            "halt": {"step": 1, "reasons": ["r"]},
        }
        with tempfile.TemporaryDirectory() as d:
            root, out = Path(d) / "root", Path(d) / "out"
            root.mkdir()
            out.mkdir()
            st["pre_repo"] = runner.repo_snapshot(root)
            rc = runner.finish(scn, out, st, root)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 2)
        self.assertEqual([r["kind"] for r in report], ["expect_after", "repo"])
        self.assertEqual(report[0]["verdict"], runner.INCONCLUSIVE)

    def load_yml(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scn.yml"
            path.write_text(text, encoding="utf-8")
            return runner.load_scenario(path)

    def test_load_scenario_rejects_expect_after_on_human_step(self):
        text = (
            "id: T\n"
            "steps:\n"
            "  - human: true\n"
            "    instruction: x\n"
            "    expect_after:\n"
            "      decisions: []\n"
        )
        with self.assertRaisesRegex(ValueError, "人間の操作の step には expect_after"):
            self.load_yml(text)

    def test_load_scenario_rejects_unknown_expect_after_key(self):
        text = "id: T\nsteps:\n  - prompt: a\n    expect_after:\n      files_unchanged: [x]\n"
        with self.assertRaisesRegex(ValueError, "files_unchanged"):
            self.load_yml(text)


class PreflightTest(unittest.TestCase):
    def test_missing_project_dir_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            facts = runner.preflight_facts(d)
        self.assertIn("置き場が無い", facts)


class ModeCheckTest(unittest.TestCase):
    def _root(self, settings_env):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / ".claude").mkdir()
        body = {"env": settings_env} if settings_env is not None else {}
        (Path(d.name) / ".claude/settings.json").write_text(json.dumps(body), encoding="utf-8")
        return d.name

    def _check(self, settings_env, environ=None):
        root = self._root(settings_env)
        with mock.patch.dict("os.environ", environ or {}, clear=True):
            return runner.mode_check(root)[0]

    def test_enable_in_settings_passes(self):
        self.assertTrue(self._check({"CCNAVI_MODE": "enable"}))

    def test_dry_run_in_settings_stops(self):
        self.assertFalse(self._check({"CCNAVI_MODE": "dry-run"}))

    def test_dry_run_in_environment_stops_even_when_settings_enable(self):
        self.assertFalse(self._check({"CCNAVI_MODE": "enable"}, {"CCNAVI_MODE": "dry-run"}))

    def test_missing_value_counts_as_enable(self):
        self.assertTrue(self._check({}))

    def test_unreadable_settings_stops(self):
        root = self._root({"CCNAVI_MODE": "enable"})
        (Path(root) / ".claude/settings.json").write_text("{", encoding="utf-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(runner.mode_check(root)[0])


class SetupSafetyTest(unittest.TestCase):
    def scenario(self, rel):
        return {"setup": {"files": {rel: "x\n"}}}

    def test_existing_file_is_a_clash_and_not_touched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "keep.txt").write_text("元の中身\n", encoding="utf-8")
            self.assertEqual(runner.setup_clashes(self.scenario("keep.txt"), root), ["keep.txt"])

    def test_cleanup_removes_only_dirs_setup_created(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "keepdir").mkdir()
            files, dirs = runner._setup(self.scenario("keepdir/a/b.txt"), root)
            self.assertEqual(dirs, ["keepdir/a"])
            runner._cleanup(files, dirs, root)
            self.assertFalse((root / "keepdir/a").exists())
            self.assertTrue((root / "keepdir").is_dir())


class StepFailureTest(unittest.TestCase):
    def test_nonzero_exit_is_an_error(self):
        proc = subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="x")
        scn = {"model": "haiku", "budget_usd": 1, "_prompt_steps": 1, "permission_mode": "auto"}
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(runner, "_ENTRY", True),
            mock.patch.object(runner.subprocess, "run", return_value=proc),
            self.assertRaises(RuntimeError),
        ):
            runner.run_step(scn, {"prompt": "x"}, Path(d), 1, "s", None, False, Path(d))

    def test_failed_step_halts_with_run_kind_and_later_steps_do_not_run(self):
        scn = {
            "steps": [{"prompt": "a"}, {"prompt": "b"}],
            "_prompt_steps": 2,
        }
        st = {"next_step": 0, "session_ids": [], "cost_usd": 0.0}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with mock.patch.object(
                runner, "run_step", side_effect=RuntimeError("起動できない")
            ) as m:
                rc = runner.drive(scn, out, st, Path(d))
        self.assertEqual(rc, 11)
        self.assertEqual(st["halt"]["kind"], "run")
        self.assertEqual(m.call_count, 1)


class ScenarioSummaryTest(unittest.TestCase):
    def test_summary_shows_title_steps_and_expectations(self):
        path = Path(__file__).parent / "UC-01-direct-edit-denied.yml"
        text = runner.scenario_summary(runner.load_scenario(path))
        self.assertIn("UC-01", text)
        self.assertIn("ワークツリーを切らずに", text)
        self.assertIn("DENY_PATH", text)
        self.assertIn("projects/test-repo/README.md", text)
        self.assertIn("ccnavi 本体", text)

    def test_human_step_and_unconfirmed_are_shown(self):
        scn = {
            "id": "UC-XX",
            "title": "t",
            "layer": 3,
            "steps": [
                {"prompt": "a"},
                {"human": True, "instruction": "確認して"},
            ],
            "expect": {"state": [{"run": "true", "expect_contains": "x"}]},
            "unconfirmed": ["点"],
            "test_repo": True,
        }
        text = runner.scenario_summary(scn)
        self.assertIn("2. 人間の操作: 確認して", text)
        self.assertIn("projects/test-repo にブランチ・コミット・MR", text)
        self.assertIn("「x」が出る", text)
        self.assertIn("まだ実ログと突き合わせていない点", text)


class StepAnswerTest(unittest.TestCase):
    def test_report_shows_each_step_input_and_final_output(self):
        scn = {
            "steps": [{"prompt": "一つ目の質問"}, {"human": True, "instruction": "手で確かめて"}],
        }
        line = '{"type": "result", "result": "一つ目の答え", "session_id": "s1"}\n'
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "step-1.jsonl").write_text(line, encoding="utf-8")
            text = "\n".join(runner.step_answer_lines(scn, out))
        self.assertIn("【入力プロンプト】\n一つ目の質問", text)
        self.assertIn("【最終出力】\n一つ目の答え", text)
        self.assertIn("### step 2（人間の操作）", text)
        self.assertIn("手で確かめて", text)

    def test_missing_step_output_is_marked_not_run(self):
        scn = {"steps": [{"prompt": "a"}]}
        with tempfile.TemporaryDirectory() as d:
            text = "\n".join(runner.step_answer_lines(scn, Path(d)))
        self.assertIn("【最終出力】\n未実行", text)


class ConfirmationTextTest(unittest.TestCase):
    def test_text_has_summary_mode_facts_and_questions(self):
        scn = {"id": "UC-XX", "title": "t", "steps": [{"prompt": "a"}], "test_repo": True}
        with tempfile.TemporaryDirectory() as d:
            text = runner.confirmation_text(scn, Path(d), "MODE OK")
        self.assertIn("UC-XX", text)
        self.assertIn("MODE OK", text)
        self.assertIn("## ワークスペースルート: git status", text)
        self.assertIn("確認事項 1", text)
        self.assertIn("確認事項 2", text)
        order = [
            text.index(h) for h in ("# 1. 事実", "# 2. ワークスペース状態", "# 3. ユーザ確認事項")
        ]
        self.assertEqual(order, sorted(order))

    def test_question_2_only_for_test_repo_scenarios(self):
        scn = {"id": "UC-XX", "title": "t", "steps": [{"prompt": "a"}]}
        with tempfile.TemporaryDirectory() as d:
            text = runner.confirmation_text(scn, Path(d), "MODE OK")
        self.assertNotIn("確認事項 2", text)

    def test_preflight_command_does_not_run_claude(self):
        args = [
            "preflight",
            str(Path(__file__).parent / "UC-01-direct-edit-denied.yml"),
            "--root",
            str(Path(__file__).parents[2]),
        ]
        with (
            mock.patch.object(runner, "run_step") as m,
            mock.patch.object(runner, "preflight_facts", return_value="FACTS"),
        ):
            rc = runner.main(args)
        self.assertEqual(rc, 3)
        m.assert_not_called()


class JudgeTest(unittest.TestCase):
    TABLE = "| 基準 | 判定 | 理由 |\n|---|---|---|\n| a | pass | r1 |\n| b | pass | r2 |\n"

    def test_parse_reads_rows_and_skips_header(self):
        rows = runner.parse_judgment(self.TABLE)
        self.assertEqual(rows, [("a", "pass", "r1"), ("b", "pass", "r2")])

    def test_parse_rejects_a_verdict_that_is_not_pass_or_fail(self):
        with self.assertRaises(ValueError):
            runner.parse_judgment("| a | needs-review | r |\n")

    def test_final_verdict_waits_until_user_accepts(self):
        mech = [("decisions", {}, runner.PASS, "")]
        judged = [("a", runner.PASS, "")]
        self.assertIn("受け入れ待ち", runner.final_verdict(mech, judged, False))

    def test_final_verdict_after_acceptance(self):
        mech = [("decisions", {}, runner.PASS, "")]
        self.assertEqual(runner.final_verdict(mech, [("a", runner.PASS, "")], True), runner.PASS)
        self.assertEqual(runner.final_verdict(mech, [("a", runner.FAIL, "")], True), runner.FAIL)
        bad = [("decisions", {}, runner.FAIL, "")]
        self.assertEqual(runner.final_verdict(bad, [("a", runner.PASS, "")], True), runner.FAIL)

    def test_cmd_judge_appends_once_and_records_acceptance(self):
        uc01 = str(Path(__file__).parent / "UC-01-direct-edit-denied.yml")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "report.md").write_text("# UC-01\n\n全体: needs-review\n", encoding="utf-8")
            (out / "state.json").write_text(json.dumps({"scenario": uc01}), encoding="utf-8")
            recorded = [
                {"kind": "decisions", "expect": {}, "verdict": "pass", "why": "x"},
                {"kind": "judge", "expect": "a", "verdict": "needs-review", "why": ""},
                {"kind": "judge", "expect": "b", "verdict": "needs-review", "why": ""},
            ]
            (out / "report.json").write_text(json.dumps(recorded), encoding="utf-8")
            table = Path(d) / "judge.md"
            table.write_text(self.TABLE, encoding="utf-8")
            ns = runner.argparse.Namespace(out=str(out), file=str(table), accept=True)
            self.assertEqual(runner.cmd_judge(ns), 0)
            text = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn(runner.JUDGE_HEADING, text)
            self.assertIn("ユーザの受け入れ: あり", text)
            self.assertIn("全体（機械判定と意味の判定の合わせ）: pass", text)
            self.assertEqual(runner.cmd_judge(ns), 1)

    def test_acceptance_can_be_recorded_after_the_table_was_added(self):
        uc01 = str(Path(__file__).parent / "UC-01-direct-edit-denied.yml")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "report.md").write_text("# UC-01\n\n全体: needs-review\n", encoding="utf-8")
            (out / "state.json").write_text(json.dumps({"scenario": uc01}), encoding="utf-8")
            recorded = [
                {"kind": "decisions", "expect": {}, "verdict": "pass", "why": "x"},
                {"kind": "judge", "expect": "a", "verdict": "needs-review", "why": ""},
                {"kind": "judge", "expect": "b", "verdict": "needs-review", "why": ""},
            ]
            (out / "report.json").write_text(json.dumps(recorded), encoding="utf-8")
            table = Path(d) / "judge.md"
            table.write_text(self.TABLE, encoding="utf-8")
            first = runner.argparse.Namespace(out=str(out), file=str(table), accept=False)
            self.assertEqual(runner.cmd_judge(first), 0)
            text = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("ユーザの受け入れ: なし", text)
            later = runner.argparse.Namespace(out=str(out), file=str(table), accept=True)
            self.assertEqual(runner.cmd_judge(later), 0)
            text = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("ユーザの受け入れ: あり", text)
            self.assertIn("全体（機械判定と意味の判定の合わせ）: pass", text)
            self.assertEqual(text.count(runner.JUDGE_HEADING), 1)
            self.assertEqual(runner.cmd_judge(later), 1)


class AcceptWithoutTableFileTest(unittest.TestCase):
    def test_accept_needs_no_table_file_once_the_table_is_added(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            report = (
                "# UC-XX\n\n全体: needs-review\n\n"
                f"{runner.JUDGE_HEADING}\n\n| 基準 | 判定 | 理由 |\n|---|---|---|\n"
                "| a | pass | r |\n\nユーザの受け入れ: なし\n"
                "全体（機械判定と意味の判定の合わせ）: 受け入れ待ち\n"
            )
            (out / "report.md").write_text(report, encoding="utf-8")
            recorded = [{"kind": "decisions", "expect": {}, "verdict": "pass", "why": ""}]
            (out / "report.json").write_text(json.dumps(recorded), encoding="utf-8")
            ns = runner.argparse.Namespace(out=str(out), file=None, accept=True)
            self.assertEqual(runner.cmd_judge(ns), 0)
            text = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("ユーザの受け入れ: あり", text)
            self.assertIn("全体（機械判定と意味の判定の合わせ）: pass", text)


class RepoResultTest(unittest.TestCase):
    def test_same_state_passes(self):
        self.assertEqual(runner.repo_result("a", "a")[0], runner.PASS)

    def test_changed_state_fails(self):
        self.assertEqual(runner.repo_result("a", "b"), (runner.FAIL, runner.REPO_CHANGED))

    def test_missing_before_state_is_not_passed(self):
        self.assertEqual(runner.repo_result(None, "a")[0], runner.INCONCLUSIVE)


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


class IssueSetupTest(unittest.TestCase):
    def test_issue_number_from_url(self):
        self.assertEqual(runner.issue_number("https://github.com/o/r/issues/34\n"), 34)

    def test_issue_number_rejects_non_url(self):
        with self.assertRaises(RuntimeError):
            runner.issue_number("作成できませんでした")

    def test_fill_issue_replaces_placeholder(self):
        text = "issue #{issue} で `--issue {issue}` を打つ"
        self.assertEqual(runner.fill_issue(text, 34), "issue #34 で `--issue 34` を打つ")

    def test_fill_issue_keeps_text_without_number(self):
        self.assertEqual(runner.fill_issue("issue #{issue}", None), "issue #{issue}")

    def test_uc09_creates_issue_per_run(self):
        scn = runner.load_scenario(Path(__file__).parent / "UC-09-quick-ticket.yml")
        issue = scn["setup"]["issue"]
        self.assertEqual(issue["repo"], "projects/test-repo")
        self.assertTrue(issue["close"])
        self.assertIn("{issue}", scn["steps"][0]["prompt"])
        self.assertNotIn("#34", scn["steps"][0]["prompt"])


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
        self.assertIn("### ツール結果（成功）\n- working tree clean", text)
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

    def test_error_duplicating_hook_denial_points_to_hook(self):
        payload = {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "DENY_PATH 本文",
            }
        }
        events = [
            self._hook("PreToolUse", "guard", json.dumps(payload, ensure_ascii=False)),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "is_error": True,
                            "content": "DENY_PATH 本文",
                        }
                    ]
                },
            },
        ]
        text = self._write(events)
        self.assertIn("hook の応答を参照", text)
        self.assertEqual(text.count("DENY_PATH 本文"), 1)

    def test_short_error_contained_in_hook_reason_is_not_collapsed(self):
        payload = {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "ファイル /a/b.txt への書き込みは禁止です",
            }
        }
        events = [
            self._hook("PreToolUse", "guard", json.dumps(payload, ensure_ascii=False)),
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "is_error": True, "content": "禁止"}]
                },
            },
        ]
        text = self._write(events)
        self.assertNotIn("hook の応答を参照", text)
        self.assertIn("### ツール結果（エラー）\n- 禁止", text)

    def test_error_with_extra_text_after_hook_reason_is_kept(self):
        payload = {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "DENY本文",
            }
        }
        events = [
            self._hook("PreToolUse", "guard", json.dumps(payload, ensure_ascii=False)),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "is_error": True,
                            "content": "DENY本文\n別の原因 exit 1",
                        }
                    ]
                },
            },
        ]
        text = self._write(events)
        self.assertIn("別の原因 exit 1", text)
        self.assertNotIn("hook の応答を参照", text)

    def test_whitespace_only_hook_reason_does_not_collapse_errors(self):
        payload = {
            "hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "  "}
        }
        events = [
            self._hook("PreToolUse", "guard", json.dumps(payload, ensure_ascii=False)),
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "is_error": True, "content": "全然別のエラー"}
                    ]
                },
            },
        ]
        text = self._write(events)
        self.assertIn("- 全然別のエラー", text)
        self.assertNotIn("hook の応答を参照", text)

    def test_success_and_error_are_under_separate_headings(self):
        events = [
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "is_error": False, "content": "ok本文"}]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "is_error": True, "content": "別のエラー"}]
                },
            },
        ]
        text = self._write(events)
        self.assertIn("### ツール結果（成功）\n- ok本文", text)
        self.assertIn("### ツール結果（エラー）\n- 別のエラー", text)

    def test_input_prompt_is_in_each_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "step-1.jsonl").write_text("", encoding="utf-8")
            scn = {
                "id": "UC-99",
                "title": "t",
                "expect": {},
                "steps": [{"prompt": "  ワークツリーは切らない  "}],
            }
            runner.write_report(scn, out, {"cost_usd": 0.0}, [], [], "")
            text = (out / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("### 入力プロンプト\nワークツリーは切らない", text)

    def test_decisions_table_has_headers_and_step_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for num, sid in ((1, "S1"), (2, "S2")):
                line = {"type": "result", "session_id": sid, "result": "x"}
                (out / f"step-{num}.jsonl").write_text(
                    json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            rows = [
                {"event": "SessionStart", "session": "S2", "decision": "allow"},
                {
                    "event": "PreToolUse",
                    "tool": "Read",
                    "session": "S1",
                    "decision": "deny",
                    "code": "UNDECLARED",
                    "subject": "a|b",
                },
            ]
            runner.write_report(
                {"id": "UC-99", "title": "t", "expect": {}}, out, {"cost_usd": 0.0}, [], rows, ""
            )
            text = (out / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("| step | event | tool | decision | code | rules | subject |", text)
        self.assertIn("| 2 | SessionStart |", text)
        self.assertIn("| 1 | PreToolUse | Read | deny | UNDECLARED |", text)


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


# ---- 使い捨てのリポジトリの事前状態と後片付け（setup.git・setup.reset・append・ロック） ----

GIT_USER = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    r = subprocess.run(["git", *GIT_USER, *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def make_repo(path, origin=None):
    """小さな git リポジトリ。README.md と .gitignore を持つ最初のコミットの SHA を返す。"""
    Path(path).mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if origin:
        git(path, "remote", "add", "origin", origin)
    (Path(path) / "README.md").write_text("元の中身\n", encoding="utf-8")
    (Path(path) / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "base")
    return git(path, "rev-parse", "HEAD")


class Workspace:
    """ワークスペースルート（本体の代わりの小さなリポジトリ）と、その下の使い捨てのリポジトリ。"""

    def __init__(self, tmp, *, origin_root=None, origin_target=None):
        self.root = Path(tmp) / "ws"
        make_repo(self.root, origin_root)
        self.target = self.root / "projects" / "test-repo"
        self.base = make_repo(self.target, origin_target)
        self.out = Path(tmp) / "out"

    def dirty(self):
        """回したあとの、戻したい散らかり方を一通り作る。"""
        t = self.target
        (t / "README.md").write_text("元の中身\n追記\n", encoding="utf-8")
        (t / "untracked.txt").write_text("x\n", encoding="utf-8")
        (t / "ignored.log").write_text("残る\n", encoding="utf-8")
        git(t, "branch", "feat")
        git(t, "tag", "v1")
        (t / "other.txt").write_text("o\n", encoding="utf-8")
        git(t, "add", "other.txt")
        git(t, "commit", "-q", "-m", "ahead")
        (t / "README.md").write_text("元の中身\nstash\n", encoding="utf-8")
        git(t, "stash")
        git(t, "worktree", "add", "-q", str(t.parent / "wt"), "-b", "wtbranch")
        git(t, "update-ref", "refs/remotes/origin/keep", "HEAD")
        (t / "README.md").write_text("元の中身\n未コミット\n", encoding="utf-8")

    def scenario_file(self, setup, steps=None, name="UC-99-x.yml"):
        return write_scenario(self.root.parent, setup, steps, name)


def write_scenario(directory, setup, steps=None, name="UC-99-x.yml"):
    data = {
        "id": "UC-99",
        "title": "t",
        "steps": steps or [{"prompt": "a"}],
        "expect": {},
        "setup": setup,
    }
    path = Path(directory) / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class DisposableRepoTest(unittest.TestCase):
    def test_workspace_root_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, ws.root, allowed=ws.root)
        self.assertIn("ccnavi 本体", str(cm.exception))

    def test_dir_inside_root_without_own_repo_is_the_root(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            plain = ws.root / "projects" / "plain"
            plain.mkdir()
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, plain, allowed=plain)
        self.assertIn("ccnavi 本体", str(cm.exception))

    def test_default_allowed_is_projects_test_repo(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = runner.require_disposable_repo(ws.root, ws.target)
        self.assertEqual(top, str(ws.target.resolve()))

    def test_other_repo_than_allowed_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            other = Path(d) / "other"
            make_repo(other)
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, other, allowed=ws.target)
        self.assertIn("許可された", str(cm.exception))

    def test_case_differing_spelling_is_the_same_target(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            shouting = Path(str(ws.target).swapcase())
            top = runner.require_disposable_repo(ws.root, ws.target, allowed=shouting)
        self.assertEqual(top, str(ws.target.resolve()))

    def test_symlink_to_the_allowed_repo_is_the_same_target(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            link = Path(d) / "link"
            try:
                link.symlink_to(ws.target, target_is_directory=True)
            except OSError:
                self.skipTest("シンボリックリンクを作れない")
            top = runner.require_disposable_repo(ws.root, link, allowed=ws.target)
        self.assertEqual(top, str(ws.target.resolve()))

    def test_same_origin_as_the_body_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(
                d,
                origin_root="https://example.com/o/r.git",
                origin_target="https://EXAMPLE.com/o/r/",
            )
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, ws.target)
        self.assertIn("origin", str(cm.exception))

    def test_different_origin_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(
                d,
                origin_root="https://example.com/o/r.git",
                origin_target="https://example.com/o/t.git",
            )
            runner.require_disposable_repo(ws.root, ws.target)

    def test_missing_origin_on_either_side_compares_the_toplevel_only(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d, origin_target="https://example.com/o/t.git")
            runner.require_disposable_repo(ws.root, ws.target)

    def test_linked_worktree_of_the_body_is_refused_even_without_origin(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            shutil.rmtree(ws.target)
            git(ws.root, "worktree", "add", "-q", str(ws.target), "-b", "linked")
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, ws.target)
            self.assertIn("同じリポジトリ", str(cm.exception))

    def test_not_a_repo_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal):
                runner.require_disposable_repo(ws.root, Path(d) / "nothing")

    def test_refusal_text_ends_with_one_next_line(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal) as cm:
                runner.require_disposable_repo(ws.root, ws.root)
        lines = str(cm.exception).splitlines()
        self.assertEqual([x for x in lines if x.startswith("次: ")], [lines[-1]])
        self.assertTrue(any(x.startswith("案内: ") for x in lines))

    def test_run_refuses_the_body_with_exit_3_before_anything_is_made(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            (ws.root / "projects" / "test-repo" / ".git").rename(Path(d) / "gone-git")
            path = ws.scenario_file({"reset": {"base": ws.base}})
            args = ["run", str(path), "--confirmed", "--root", str(ws.root), "--out", str(ws.out)]
            with (
                mock.patch.object(runner, "_ENTRY", True),
                mock.patch.object(runner, "drive") as drive,
                mock.patch("builtins.print") as shown,
            ):
                rc = runner.main(args)
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
        self.assertEqual(rc, 3)
        drive.assert_not_called()
        self.assertFalse(ws.out.exists())
        self.assertIn("次: ", text)


class SetupGitTest(unittest.TestCase):
    def test_argv_rules(self):
        for bad in (
            ["cd", "x"],
            ["sh", "-c", "git status"],
            ["git", "-C", "..", "status"],
            ["git", "-C../x", "status"],
            ["git", "--git-dir=/x", "status"],
            ["git", "--git-dir", "/x", "status"],
            ["git", "--work-tree=/x", "status"],
            ["git", "--work-tree", "/x", "status"],
            ["git", "-c", "alias.x=!sh", "x"],
            ["git", "--exec-path=/x", "status"],
            "git status",
            [],
            ["git", ""],
        ):
            with self.subTest(argv=bad), self.assertRaises(ValueError):
                runner.check_git_argv(bad)
        runner.check_git_argv(["git", "switch", "-c", "feat"])
        runner.check_git_argv(["git", "commit", "-m", "x"])

    def test_git_without_reset_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_scenario(d, {"git": [["git", "status"]]})
            with self.assertRaises(ValueError) as cm:
                runner.load_scenario(path)
        self.assertIn("setup.reset", str(cm.exception))

    def test_load_scenario_rejects_a_bad_command(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = ws.scenario_file({"git": [["git", "-C", "/", "status"]]})
            with self.assertRaises(ValueError):
                runner.load_scenario(path)

    def test_runs_in_the_target_not_the_body_and_logs_it(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            before = runner.repo_snapshot(ws.root)
            runner.run_setup_git(
                ws.root, ws.target, [["git", "switch", "-q", "-c", "feat"], ["git", "branch", "b2"]]
            )
            self.assertEqual(git(ws.target, "branch", "--show-current"), "feat")
            self.assertIn("b2", git(ws.target, "branch"))
            self.assertEqual(runner.repo_snapshot(ws.root), before)
            log = (ws.root / "logs" / "diag" / "scenario-runner.log").read_text(encoding="utf-8")
        self.assertIn("git switch -q -c feat", log)
        self.assertIn("rc=0", log)

    def test_cwd_is_the_checked_toplevel_even_when_git_dir_is_in_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            env = {"GIT_DIR": str(ws.root / ".git"), "GIT_WORK_TREE": str(ws.root)}
            with mock.patch.dict(os.environ, env):
                runner.run_setup_git(ws.root, ws.target, [["git", "branch", "only-in-target"]])
            self.assertIn("only-in-target", git(ws.target, "branch"))
            self.assertNotIn("only-in-target", git(ws.root, "branch"))

    def test_refuses_the_body_as_target(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal):
                runner.run_setup_git(ws.root, ws.root, [["git", "branch", "x"]], allowed=ws.root)
            self.assertNotIn("x", git(ws.root, "branch"))

    def test_stops_when_the_body_changed(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            snaps = iter(["a", "b", "b", "b"])
            with (
                mock.patch.object(runner, "repo_snapshot", side_effect=lambda r: next(snaps)),
                self.assertRaises(runner.Refusal) as cm,
            ):
                runner.run_setup_git(
                    ws.root, ws.target, [["git", "branch", "x"], ["git", "branch", "y"]]
                )
            self.assertIn("ccnavi 本体", str(cm.exception))
            self.assertNotIn("y", git(ws.target, "branch", "--format=%(refname:short)").split())

    def test_failed_command_stops_with_the_command_named(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal) as cm:
                runner.run_setup_git(ws.root, ws.target, [["git", "checkout", "nonexistent"]])
        self.assertIn("git checkout nonexistent", str(cm.exception))


class ScenarioFieldsTest(unittest.TestCase):
    def load(self, setup):
        with tempfile.TemporaryDirectory() as d:
            return runner.load_scenario(write_scenario(d, setup))

    def test_dict_form_is_accepted(self):
        base = "a" * 40
        scn = self.load(
            {"files": {"x.md": {"content": "y", "append": True}, "z": "s"}, "reset": {"base": base}}
        )
        self.assertEqual(runner.file_spec(scn["setup"]["files"]["x.md"]), ("y", True))
        self.assertEqual(runner.file_spec(scn["setup"]["files"]["z"]), ("s", False))

    def test_bad_shapes_are_rejected(self):
        base = {"base": "a" * 40}
        for setup in (
            {"files": {"x": {"append": True}}, "reset": base},
            {"files": {"x": {"content": 1, "append": True}}, "reset": base},
            {"files": {"x": {"content": "y", "append": "yes"}}, "reset": base},
            {"files": {"x": {"content": "y", "mode": "a"}}, "reset": base},
            {"files": {"x": 1}},
            {"reset": {"base": "abc1234"}},
            {"reset": {"base": "main"}},
            {"reset": {"base": "v1.0"}},
            {"reset": {"base": "g" * 40}},
            {"reset": {"base": "a" * 40, "force": True}},
            {"reset": "a" * 40},
            {"git": "git status"},
            {"reeset": base},
        ):
            with self.subTest(setup=setup), self.assertRaises(ValueError):
                self.load(setup)

    def test_append_without_reset_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.load({"files": {"x.md": {"content": "y", "append": True}}})
        self.assertIn("setup.reset", str(cm.exception))

    def test_clashes_are_inverted_for_append(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "have.txt").write_text("x", encoding="utf-8")
            scn = {
                "setup": {
                    "files": {
                        "have.txt": "新規",
                        "missing.txt": "新規",
                        "app-have.txt": {"content": "a", "append": True},
                        "app-missing.txt": {"content": "a", "append": True},
                    }
                }
            }
            (root / "app-have.txt").write_text("x", encoding="utf-8")
            self.assertEqual(runner.setup_clashes(scn, root), ["have.txt", "app-missing.txt"])

    def test_summary_separates_created_and_appended_files(self):
        scn = {
            "id": "UC-XX",
            "title": "t",
            "steps": [{"prompt": "a"}],
            "setup": {
                "files": {
                    "new.txt": "x",
                    "projects/test-repo/README.md": {"content": "y", "append": True},
                },
                "git": [["git", "switch", "-c", "feat"]],
                "reset": {"base": "a" * 40},
            },
        }
        text = runner.scenario_summary(scn)
        self.assertIn("回す前に作り、回したあとに消すファイル: new.txt", text)
        self.assertIn("回す前に追記する、対象にある管理下のファイル", text)
        self.assertIn("projects/test-repo/README.md", text.split("追記する", 1)[1])
        self.assertIn("git switch -c feat", text)
        self.assertIn("a" * 40, text)
        self.assertIn("人間の操作で止めた回は、戻さずに残す", text)


class AppendTest(unittest.TestCase):
    REL = "projects/test-repo/README.md"

    def scenario(self, rel=None, body="追記\n"):
        return {"setup": {"files": {rel or self.REL: {"content": body, "append": True}}}}

    def test_appends_to_the_tracked_file_and_cleanup_keeps_it(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = runner.require_disposable_repo(ws.root, ws.target)
            files, dirs = runner._setup(self.scenario(), ws.root, top)
            self.assertEqual((files, dirs), ([], []))
            self.assertEqual(
                (ws.target / "README.md").read_text(encoding="utf-8"), "元の中身\n追記\n"
            )
            runner._cleanup(files, dirs, ws.root)
            self.assertTrue((ws.target / "README.md").is_file())

    def test_appended_file_is_not_removed_by_finish(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = runner.require_disposable_repo(ws.root, ws.target)
            files, dirs = runner._setup(self.scenario(), ws.root, top)
            ws.out.mkdir()
            st = {
                "setup_made": files,
                "setup_dirs": dirs,
                "session_ids": [],
                "pre_digests": {},
                "pre_repo": runner.repo_snapshot(ws.root),
                "cost_usd": 0.0,
            }
            runner.finish({"id": "UC-XX", "title": "t", "expect": {}}, ws.out, st, ws.root)
            self.assertTrue((ws.target / "README.md").is_file())

    def refused(self, rel):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = runner.require_disposable_repo(ws.root, ws.target)
            (ws.target / "untracked.txt").write_text("u", encoding="utf-8")
            (ws.target / "sub").mkdir()
            (ws.root / "outside.txt").write_text("o", encoding="utf-8")
            before = (ws.target / "README.md").read_text(encoding="utf-8")
            with self.assertRaises(runner.Refusal):
                runner._setup(self.scenario(rel), ws.root, top)
            self.assertEqual((ws.target / "README.md").read_text(encoding="utf-8"), before)

    def test_outside_missing_untracked_and_directory_are_refused(self):
        for rel in (
            "projects/test-repo/../../outside.txt",
            "outside.txt",
            "projects/test-repo/none.md",
            "projects/test-repo/untracked.txt",
            "projects/test-repo/sub",
            "README.md",
        ):
            with self.subTest(rel=rel):
                self.refused(rel)

    def test_absolute_path_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = runner.require_disposable_repo(ws.root, ws.target)
            with self.assertRaises(runner.Refusal):
                runner._setup(self.scenario(str(ws.target / "README.md")), ws.root, top)

    def test_symlink_that_leaves_the_target_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            (ws.root / "outside.txt").write_text("o", encoding="utf-8")
            try:
                (ws.target / "escape.txt").symlink_to(ws.root / "outside.txt")
            except OSError:
                self.skipTest("シンボリックリンクを作れない")
            git(ws.target, "add", "escape.txt")
            top = runner.require_disposable_repo(ws.root, ws.target)
            with self.assertRaises(runner.Refusal) as cm:
                runner._setup(self.scenario("projects/test-repo/escape.txt"), ws.root, top)
            self.assertIn("外", str(cm.exception))
            self.assertEqual((ws.root / "outside.txt").read_text(encoding="utf-8"), "o")

    def test_path_is_matched_literally_not_as_a_glob(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            (ws.target / "docs1.md").write_text("t", encoding="utf-8")
            git(ws.target, "add", "docs1.md")
            try:
                (ws.target / "docs*.md").write_text("u", encoding="utf-8")
            except OSError:
                self.skipTest("ファイル名に * を使えない")
            top = runner.require_disposable_repo(ws.root, ws.target)
            with self.assertRaises(runner.Refusal):
                runner._setup(self.scenario("projects/test-repo/docs*.md"), ws.root, top)

    def test_append_needs_the_checked_target(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with self.assertRaises(runner.Refusal):
                runner._setup(self.scenario(), ws.root)


class ResetTest(unittest.TestCase):
    def test_restores_everything_but_ignored_files_and_remote_refs(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            t = ws.target
            runner.reset_repo(ws.root, t, ws.base)
            self.assertEqual(git(t, "rev-parse", "HEAD"), ws.base)
            self.assertEqual(git(t, "rev-parse", "main"), ws.base)
            self.assertEqual(git(t, "branch", "--show-current"), "main")
            self.assertEqual(
                git(t, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "main"
            )
            self.assertEqual(git(t, "tag", "-l"), "")
            self.assertEqual(git(t, "stash", "list"), "")
            self.assertEqual(len(git(t, "worktree", "list").splitlines()), 1)
            self.assertEqual(git(t, "status", "--porcelain"), "")
            self.assertEqual((t / "README.md").read_text(encoding="utf-8"), "元の中身\n")
            self.assertFalse((t / "untracked.txt").exists())
            self.assertTrue((t / "ignored.log").exists(), "clean に -x を付けない")
            self.assertEqual(
                git(t, "for-each-ref", "--format=%(refname)", "refs/remotes"),
                "refs/remotes/origin/keep",
            )

    def test_main_pointing_elsewhere_is_moved_to_base(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            t = ws.target
            git(t, "switch", "-q", "-c", "side")
            (t / "s.txt").write_text("s", encoding="utf-8")
            git(t, "add", "s.txt")
            git(t, "commit", "-q", "-m", "side")
            git(t, "branch", "-f", "main", "side")
            runner.reset_repo(ws.root, t, ws.base)
            self.assertEqual(git(t, "rev-parse", "main"), ws.base)
            self.assertEqual(git(t, "branch", "--show-current"), "main")

    def test_order_and_no_x_flag(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            with mock.patch.object(runner, "_target_git", side_effect=runner._target_git) as spy:
                runner.reset_repo(ws.root, ws.target, ws.base)
            calls = [c.args[2] for c in spy.call_args_list]
            kinds = []
            for c in calls:
                key = c[:2] if c[0] in ("worktree", "stash") else c[:1]
                if c[0] == "reset":
                    key = ["reset", c[1]]
                if c[0] == "clean":
                    key = ["clean", c[1]]
                kinds.append(" ".join(key))
            order = [
                "worktree remove",
                "worktree prune",
                "checkout",
                "branch",
                "tag",
                "stash clear",
                "reset --hard",
                "clean -fd",
            ]
            for name in order:
                self.assertIn(name, kinds)
            first = [kinds.index(n) for n in order]
            self.assertEqual(first, sorted(first))
            checkout = next(c for c in calls if c[0] == "checkout")
            self.assertEqual(checkout[-3:], ["-B", "main", ws.base])
            clean = next(c for c in calls if c[0] == "clean")
            self.assertEqual(clean, ["clean", "-fd"])
            self.assertNotIn("-x", [a for c in calls for a in c])

    def test_unknown_base_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            with self.assertRaises(runner.Refusal):
                runner.reset_repo(ws.root, ws.target, "f" * 40)
            self.assertEqual(
                (ws.target / "README.md").read_text(encoding="utf-8"), "元の中身\n未コミット\n"
            )

    def test_refuses_the_body(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            git(ws.root, "branch", "keep-me")
            with self.assertRaises(runner.Refusal):
                runner.reset_repo(ws.root, ws.root, ws.base, allowed=ws.root)
            self.assertIn("keep-me", git(ws.root, "branch"))


class PreRunCheckTest(unittest.TestCase):
    def test_unknown_base_is_refused_before_anything_is_changed(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            scn = {"setup": {"reset": {"base": "0" * 40}}}
            with self.assertRaises(runner.Refusal) as cm:
                runner.check_before_run(scn, ws.root, str(ws.target.resolve()))
            self.assertIn("次: ", str(cm.exception))
            self.assertEqual(git(ws.target, "branch", "--show-current"), "main")
            self.assertNotIn(runner.LEFT_NAME, {p.name for p in (ws.target / ".git").iterdir()})

    def test_unusable_append_target_is_refused_before_the_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = ws.scenario_file(
                {
                    "files": {"projects/test-repo/untracked.txt": {"content": "x", "append": True}},
                    "reset": {"base": ws.base},
                }
            )
            (ws.target / "untracked.txt").write_text("u", encoding="utf-8")
            with mock.patch.object(runner, "_ENTRY", True), mock.patch("builtins.print"):
                rc = runner.main(["run", str(path), "--root", str(ws.root)])  # --confirmed なし
            self.assertEqual(rc, 3)
            self.assertFalse(ws.out.exists())


class SetupFailureTest(unittest.TestCase):
    def test_partial_failure_removes_what_was_made(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            scn = {"setup": {"files": {"a/one.txt": "1", "a/two.txt": "2"}}}
            real = Path.write_text

            def flaky(self, *a, **k):
                if self.name == "two.txt":
                    raise OSError("書けない")
                return real(self, *a, **k)

            with mock.patch.object(Path, "write_text", flaky), self.assertRaises(OSError):
                runner._setup(scn, root)
            self.assertFalse((root / "a").exists())
            self.assertEqual(runner.setup_clashes(scn, root), [])


class CleanupRobustnessTest(unittest.TestCase):
    def test_close_issue_failure_does_not_stop_the_restore(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            top = str(ws.target.resolve())
            lock = runner.acquire_target(top, ws.out)
            st = {
                "target": {"path": top, "base": ws.base, "lock": lock},
                "out": str(ws.out),
                "setup_made": [],
                "issue_no": 7,
            }
            ws.out.mkdir()
            scn = {"setup": {"issue": {"repo": "projects/test-repo", "close": True}}}
            with (
                mock.patch.object(runner, "repo_host_kind", side_effect=FileNotFoundError("gh")),
                mock.patch("builtins.print"),
            ):
                runner._abort(scn, ws.out, st, ws.root)
            self.assertEqual(git(ws.target, "status", "--porcelain"), "")
            self.assertFalse(Path(lock).exists())

    def test_restore_after_finish_does_not_delete_a_file_made_later(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = ws.scenario_file(
                {"files": {"projects/test-repo/new.txt": "x\n"}, "reset": {"base": ws.base}}
            )
            with (
                mock.patch.object(runner, "_ENTRY", True),
                mock.patch.object(
                    runner, "run_step", return_value={"cost_usd": 0.0, "session_ids": []}
                ),
                mock.patch("builtins.print"),
            ):
                args = ["run", str(path), "--confirmed", "--root", str(ws.root)]
                self.assertEqual(runner.main([*args, "--out", str(ws.out)]), 0)
                later = ws.target / "new.txt"
                later.write_text("あとから作った\n", encoding="utf-8")
                self.assertEqual(runner.main(["restore", str(ws.out)]), 0)
            self.assertTrue(later.exists())

    def test_relative_out_is_stored_as_an_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}]
            path = ws.scenario_file({"reset": {"base": ws.base}}, steps)
            before = os.getcwd()
            os.chdir(d)
            self.addCleanup(os.chdir, before)
            with (
                mock.patch.object(runner, "_ENTRY", True),
                mock.patch.object(
                    runner, "run_step", return_value={"cost_usd": 0.0, "session_ids": []}
                ),
                mock.patch("builtins.print"),
            ):
                args = ["run", str(path), "--confirmed", "--root", str(ws.root), "--out", "rel"]
                self.assertEqual(runner.main(args), 10)
                marker = ws.target / ".git" / runner.LEFT_NAME
                self.assertEqual(
                    marker.read_text(encoding="utf-8"), str((Path(d) / "rel").resolve())
                )
                os.chdir(ws.root)  # 別の cwd からでも、同じ回として続けられる
                self.assertEqual(runner.main(["restore", str(Path(d) / "rel")]), 0)


class LockTest(unittest.TestCase):
    def test_second_run_is_refused_and_release_lets_it_in(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = str(ws.target.resolve())
            lock = runner.acquire_target(top, ws.out)
            self.assertEqual(Path(lock).read_text(encoding="utf-8"), str(os.getpid()))
            with self.assertRaises(runner.Refusal) as cm:
                runner.acquire_target(top, ws.out)
            self.assertIn("別の回が使っている", str(cm.exception))
            st = {"target": {"path": top, "base": None}, "out": str(ws.out)}
            runner.release_target(st, ws.root)
            runner.acquire_target(top, ws.out)

    def test_lock_lives_in_dot_git_and_survives_clean(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            lock = runner.acquire_target(str(ws.target.resolve()), ws.out)
            self.assertEqual(Path(lock).parent, (ws.target / ".git").resolve())
            self.assertEqual(git(ws.target, "status", "--porcelain"), "")
            git(ws.target, "clean", "-fd")
            self.assertTrue(Path(lock).exists())

    def test_dead_owner_is_taken_over(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            proc = subprocess.Popen([sys.executable, "-c", "pass"])
            proc.wait()
            lock = ws.target / ".git" / runner.LOCK_NAME
            lock.write_text(str(proc.pid), encoding="utf-8")
            runner.acquire_target(str(ws.target.resolve()), ws.out)
            self.assertEqual(lock.read_text(encoding="utf-8"), str(os.getpid()))

    def test_unreadable_owner_counts_as_alive(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            (ws.target / ".git" / runner.LOCK_NAME).write_text("", encoding="utf-8")
            with self.assertRaises(runner.Refusal):
                runner.acquire_target(str(ws.target.resolve()), ws.out)

    def test_failed_liveness_check_counts_as_alive(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            (ws.target / ".git" / runner.LOCK_NAME).write_text("999999", encoding="utf-8")
            with (
                mock.patch.object(runner.os, "kill", side_effect=PermissionError),
                self.assertRaises(runner.Refusal),
            ):
                runner.acquire_target(str(ws.target.resolve()), ws.out)

    def test_pid_alive(self):
        self.assertTrue(runner._pid_alive(os.getpid()))
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(runner._pid_alive(proc.pid))


class LifecycleTest(unittest.TestCase):
    """cmd_run から finish / 中断 / 人間の操作の step までを、claude を呼ばずに回す。"""

    STEP_OUT = {"cost_usd": 0.0, "session_ids": []}

    def setup_for(self, ws, steps=None):
        return ws.scenario_file(
            {
                "files": {"projects/test-repo/README.md": {"content": "追記\n", "append": True}},
                "git": [["git", "switch", "-q", "-c", "feat"]],
                "reset": {"base": ws.base},
            },
            steps,
        )

    def run_cmd(self, ws, path, *extra):
        args = [
            "run",
            str(path),
            "--confirmed",
            "--root",
            str(ws.root),
            "--out",
            str(ws.out),
            *extra,
        ]
        with mock.patch.object(runner, "_ENTRY", True):
            return runner.main(args)

    def restored(self, ws):
        t = ws.target
        return (
            git(t, "rev-parse", "main") == ws.base
            and git(t, "branch", "--show-current") == "main"
            and git(t, "status", "--porcelain") == ""
            and (t / "README.md").read_text(encoding="utf-8") == "元の中身\n"
        )

    def git_dir_names(self, ws):
        return {p.name for p in (ws.target / ".git").iterdir()}

    def test_pre_state_is_made_and_finish_restores_it(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            seen = {}

            def run_step(*a, **k):
                seen["branch"] = git(ws.target, "branch", "--show-current")
                seen["readme"] = (ws.target / "README.md").read_text(encoding="utf-8")
                seen["lock"] = runner.LOCK_NAME in self.git_dir_names(ws)
                return self.STEP_OUT

            with mock.patch.object(runner, "run_step", side_effect=run_step):
                rc = self.run_cmd(ws, self.setup_for(ws))
            self.assertEqual(seen, {"branch": "feat", "readme": "元の中身\n追記\n", "lock": True})
            self.assertIn(rc, (0, 2))
            self.assertTrue(self.restored(ws))
            names = self.git_dir_names(ws)
            self.assertNotIn(runner.LOCK_NAME, names)
            self.assertNotIn(runner.LEFT_NAME, names)

    def test_keyboard_interrupt_restores_and_reraises(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with (
                mock.patch.object(runner, "run_step", side_effect=KeyboardInterrupt),
                self.assertRaises(KeyboardInterrupt),
            ):
                self.run_cmd(ws, self.setup_for(ws))
            self.assertTrue(self.restored(ws))
            self.assertNotIn(runner.LOCK_NAME, self.git_dir_names(ws))

    def test_failed_step_restores(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")):
                rc = self.run_cmd(ws, self.setup_for(ws))
            self.assertEqual(rc, 2)
            self.assertTrue(self.restored(ws))

    def test_human_step_leaves_the_state_until_next_finishes(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}]
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = self.setup_for(ws, steps)
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT),
                mock.patch("builtins.print") as shown,
            ):
                rc = self.run_cmd(ws, path)
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
            self.assertEqual(rc, 10)
            self.assertEqual(git(ws.target, "branch", "--show-current"), "feat")
            self.assertIn("元の中身\n追記\n", (ws.target / "README.md").read_text(encoding="utf-8"))
            names = self.git_dir_names(ws)
            self.assertNotIn(runner.LOCK_NAME, names)
            self.assertIn(runner.LEFT_NAME, names)
            self.assertIn(f"{ws.base} に戻していない", text)
            self.assertIn(f"次: 続けるなら `{runner.RUNNER} next {ws.out}`", text)
            self.assertIn(f"やめるなら `{runner.RUNNER} restore {ws.out}`", text)

            # 残した状態に、別の回は入れない
            other = ws.scenario_file({"reset": {"base": ws.base}}, name="UC-98-y.yml")
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT),
                mock.patch("builtins.print"),
            ):
                other_rc = self.run_cmd(ws, other, "--out", str(Path(d) / "out2"))
            self.assertEqual(other_rc, 3)
            self.assertEqual(git(ws.target, "branch", "--show-current"), "feat")

            # next が最後まで終わると戻る
            with mock.patch("builtins.print"):
                done = runner.main(["next", str(ws.out)])
            self.assertEqual(done, 0)
            self.assertTrue(self.restored(ws))
            names = self.git_dir_names(ws)
            self.assertNotIn(runner.LEFT_NAME, names)
            self.assertNotIn(runner.LOCK_NAME, names)

    def test_restore_abandons_a_held_run(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}]
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = self.setup_for(ws, steps)
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(self.run_cmd(ws, path), 10)
            with mock.patch("builtins.print"):
                self.assertEqual(runner.main(["restore", str(ws.out)]), 0)
            self.assertTrue(self.restored(ws))
            self.assertNotIn(runner.LEFT_NAME, self.git_dir_names(ws))

    def test_halted_run_restores_and_next_afterwards_is_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            with mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")):
                self.assertEqual(self.run_cmd(ws, self.setup_for(ws)), 2)
            self.assertTrue(self.restored(ws))
            with mock.patch("builtins.print"):
                self.assertEqual(runner.main(["next", str(ws.out)]), 2)
            self.assertTrue(self.restored(ws))

    def test_next_after_a_halt_that_never_reached_finish_restores(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = self.setup_for(ws)
            with (
                mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")),
                mock.patch.object(runner, "finish", return_value=2),
            ):
                self.assertEqual(self.run_cmd(ws, path), 2)
            self.assertFalse(self.restored(ws), "finish を通っていないので残っている")
            with mock.patch("builtins.print"):
                self.assertEqual(runner.main(["next", str(ws.out)]), 2)
            self.assertTrue(self.restored(ws))

    def test_unrestorable_target_gives_exit_3_after_the_report(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            fail = runner.Refusal("失敗", "手で戻す")
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT),
                mock.patch.object(runner, "reset_repo", side_effect=[None, fail]),
                mock.patch("builtins.print") as shown,
            ):
                rc = self.run_cmd(ws, self.setup_for(ws))
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
            self.assertEqual(rc, 3)
            self.assertTrue((ws.out / "report.md").is_file(), "レポートは書いてから 3 を返す")
            self.assertIn("手作業で戻す必要がある", text)
            self.assertIn(str(ws.target.resolve()), text)
            self.assertIn("次: ", text)
            self.assertIn(runner.LEFT_NAME, self.git_dir_names(ws))
            self.assertNotIn(runner.LOCK_NAME, self.git_dir_names(ws))
            # 戻せていない対象では、次の回を始めさせない
            other = ws.scenario_file({"reset": {"base": ws.base}}, name="UC-98-y.yml")
            with mock.patch("builtins.print"):
                self.assertEqual(self.run_cmd(ws, other, "--out", str(Path(d) / "out2")), 3)

    def test_interrupt_with_unrestorable_target_reports_and_reraises_the_original(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            fail = runner.Refusal("失敗", "手で戻す")
            with (
                mock.patch.object(runner, "run_step", side_effect=KeyboardInterrupt),
                mock.patch.object(runner, "reset_repo", side_effect=[None, fail]),
                mock.patch("builtins.print") as shown,
                self.assertRaises(KeyboardInterrupt),
            ):
                self.run_cmd(ws, self.setup_for(ws))
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
            self.assertIn("手作業で戻す必要がある", text)

    def test_second_run_on_a_locked_target_is_refused_before_touching_it(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.dirty()
            runner.acquire_target(str(ws.target.resolve()), ws.out)
            with (
                mock.patch.object(runner, "run_step", return_value=self.STEP_OUT) as step,
                mock.patch("builtins.print"),
            ):
                rc = self.run_cmd(ws, self.setup_for(ws), "--out", str(Path(d) / "out2"))
            self.assertEqual(rc, 3)
            step.assert_not_called()
            self.assertEqual(
                (ws.target / "README.md").read_text(encoding="utf-8"), "元の中身\n未コミット\n"
            )

    def test_append_to_a_missing_file_stops_before_anything_is_touched(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = ws.scenario_file(
                {
                    "files": {"projects/test-repo/none.md": {"content": "x", "append": True}},
                    "reset": {"base": ws.base},
                }
            )
            with mock.patch("builtins.print"):
                rc = self.run_cmd(ws, path)
            self.assertEqual(rc, 1)  # 追記の対象が無い。clash の判定で、何も触る前に止まる
            self.assertNotIn(runner.LOCK_NAME, self.git_dir_names(ws))

    def test_failed_setup_git_rolls_back_and_exits_3(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            path = ws.scenario_file(
                {
                    "files": {"projects/test-repo/README.md": {"content": "x\n", "append": True}},
                    "git": [["git", "checkout", "nonexistent"]],
                    "reset": {"base": ws.base},
                }
            )
            with (
                mock.patch.object(runner, "drive") as drive,
                mock.patch("builtins.print") as shown,
            ):
                rc = self.run_cmd(ws, path)
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
            self.assertEqual(rc, 3)
            drive.assert_not_called()
            self.assertIn("次: ", text)
            self.assertTrue(self.restored(ws))
            self.assertNotIn(runner.LOCK_NAME, self.git_dir_names(ws))

    def test_confirmation_shows_the_target_and_asks_about_the_reset(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn = runner.load_scenario(self.setup_for(ws))
            text = runner.confirmation_text(scn, ws.root, "MODE OK")
            self.assertIn(f"対象のリポジトリ: {ws.target.resolve()}（使い捨てと確かめた）", text)
            self.assertIn("確認事項 3", text)
            self.assertIn(ws.base, text)
            body = runner.confirmation_text(scn, ws.target, "MODE OK")
            self.assertIn("対象のリポジトリを使えない", body)


if __name__ == "__main__":
    unittest.main()
