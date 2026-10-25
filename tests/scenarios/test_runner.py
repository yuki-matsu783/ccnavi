"""受入シナリオの実行器の単体テスト。claude は呼ばない（呼べない門をここで確かめる）。

シナリオを回すこと自体は、全量テストに入れない（docs/42_claude/acceptance-scenarios.md）。
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


def hook_section(text):
    """evidence.md の「hook の応答」の見出しの節だけを取り出す（渡った文脈の節と分けて読む）。"""
    head = text.split("### hook の応答", 1)[1]
    return head.split("\n###", 1)[0]


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
        self.assertNotIn("長い前置き", hook_section(text))
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
        self.assertEqual(hook_section(text).count("DENY_PATH 本文"), 1)
        self.assertEqual(text.split("### hook の応答", 1)[0].count("DENY_PATH 本文"), 0)

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


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_rows(name):
    return [json.loads(x) for x in fixture_text(name).splitlines() if x.strip()]


def make_out(step_texts):
    """step-N.jsonl を並べた出力ディレクトリを作って返す（後始末は呼ぶ側の addCleanup）。"""
    tmp = tempfile.TemporaryDirectory()
    out = Path(tmp.name)
    for n, text in enumerate(step_texts, 1):
        (out / f"step-{n}.jsonl").write_text(text, encoding="utf-8")
    return tmp, out


def hook_line(event, name, payload):
    return json.dumps(
        {
            "type": "system",
            "subtype": "hook_response",
            "hook_event": event,
            "hook_name": name,
            "output": json.dumps(payload, ensure_ascii=False),
            "session_id": "s1",
        },
        ensure_ascii=False,
    )


def deny_line(reason, name="PreToolUse:Edit"):
    spec = {"permissionDecision": "deny", "permissionDecisionReason": reason}
    return hook_line("PreToolUse", name, {"hookSpecificOutput": spec})


def call_line(name, tool_input, call_id="t1", parent=None):
    block = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    return json.dumps(
        {
            "type": "assistant",
            "session_id": "s1",
            "parent_tool_use_id": parent,
            "message": {"content": [block]},
        }
    )


def write_all(scn, out, rows):
    runner.write_report(scn, out, {"cost_usd": 0.0}, [], rows, "")
    return {
        name: (out / name).read_text(encoding="utf-8")
        for name in ("report.md", "evidence.md", "messages.md")
    }


class ParseStreamIdsTest(unittest.TestCase):
    def test_tool_calls_and_results_keep_ids_and_parent(self):
        got = runner.parse_stream(fixture_text("subagent-edit-denied.jsonl"))
        by_id = {c["id"]: c for c in got["tool_calls"]}
        agent = next(c for c in got["tool_calls"] if c["name"] == "Agent")
        self.assertIsNone(agent["parent"])
        read = next(c for c in got["tool_calls"] if c["name"] == "Read")
        self.assertEqual(read["parent"], agent["id"])
        for r in got["tool_results"]:
            self.assertIn(r["tool_use_id"], by_id)
            self.assertEqual(r["parent"], by_id[r["tool_use_id"]]["parent"])

    def test_existing_fields_are_unchanged(self):
        got = runner.parse_stream(fixture_text("subagent-edit-denied.jsonl"))
        self.assertEqual(got["session_ids"], ["sess-main"])
        self.assertEqual([c["name"] for c in got["tool_calls"]], ["Agent", "Read", "Edit"])
        edit = got["tool_calls"][2]["input"]
        self.assertEqual(edit["file_path"], "/work/test-repo/README.md")
        self.assertTrue(got["final_text"])
        self.assertEqual([r["is_error"] for r in got["tool_results"]], [False, False, True])

    def test_missing_ids_are_none(self):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]},
            },
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        ]
        got = runner.parse_stream("\n".join(json.dumps(e) for e in events))
        self.assertEqual(
            got["tool_calls"][0], {"name": "Edit", "input": {}, "id": None, "parent": None}
        )
        self.assertIsNone(got["tool_results"][0]["tool_use_id"])
        self.assertIsNone(got["tool_results"][0]["parent"])


class ContextSectionTest(unittest.TestCase):
    def _out(self, lines):
        tmp, out = make_out(["\n".join(lines) + "\n"])
        self.addCleanup(tmp.cleanup)
        return out

    def test_counts_characters_and_clips_the_head_only(self):
        ctx = "あ" * (runner.CONTEXT_LIMIT + 500)
        out = self._out(
            [
                hook_line(
                    "SessionStart",
                    "SessionStart:startup",
                    {"hookSpecificOutput": {"additionalContext": ctx}},
                )
            ]
        )
        files = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])
        ev = files["evidence.md"]
        self.assertIn(f"SessionStart [SessionStart:startup] 案内（{len(ctx)} 文字）", ev)
        self.assertIn("あ" * runner.CONTEXT_LIMIT + "…", ev)
        self.assertNotIn("あ" * (runner.CONTEXT_LIMIT + 1), ev)
        self.assertIn(
            f"全 {len(ctx)} 文字のうち先頭 {runner.CONTEXT_LIMIT} 文字。全量は context-1.md", ev
        )
        self.assertIn(ctx, (out / "context-1.md").read_text(encoding="utf-8"))

    def test_denial_reason_uses_evidence_limit(self):
        reason = "拒" * (runner.EVIDENCE_LIMIT + 10)
        out = self._out([deny_line(reason)])
        ev = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["evidence.md"]
        self.assertIn(f"PreToolUse [PreToolUse:Edit] 拒否（{len(reason)} 文字）", ev)
        self.assertNotIn("拒" * (runner.EVIDENCE_LIMIT + 1), ev.split("### 渡った文脈")[1])
        self.assertIn(reason, (out / "context-1.md").read_text(encoding="utf-8"))

    def test_no_context_means_no_section_and_no_file(self):
        out = self._out([call_line("Read", {"file_path": "a"})])
        ev = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["evidence.md"]
        self.assertNotIn("渡った文脈", ev)
        self.assertFalse((out / "context-1.md").exists())

    def test_hook_event_name_key_is_accepted(self):
        line = json.loads(
            hook_line("x", "n", {"hookSpecificOutput": {"additionalContext": "文脈"}})
        )
        del line["hook_event"]
        line["hook_event_name"] = "UserPromptSubmit"
        out = self._out([json.dumps(line, ensure_ascii=False)])
        ev = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["evidence.md"]
        self.assertIn("UserPromptSubmit [n] 案内（2 文字）", ev)

    def test_subagent_part_is_kept_apart_from_the_parent(self):
        tmp, out = make_out([fixture_text("subagent-edit-denied.jsonl")])
        self.addCleanup(tmp.cleanup)
        ev = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["evidence.md"]
        main, sub = ev.split("### 渡った文脈", 1)[1].split("#### サブエージェント", 1)
        self.assertIn("SessionStart [SessionStart:startup] 案内", main)
        self.assertIn("PreToolUse [PreToolUse:Edit] 拒否", main)
        self.assertNotIn("SubagentStart", main)
        self.assertIn("に親が渡した prompt", sub)
        self.assertIn("この Agent 呼び出しの中のツール呼び出し: 1 件", sub)
        self.assertIn("  - Read:", sub)
        self.assertIn("SubagentStart [SubagentStart:Explore] 案内", sub)
        self.assertIn("取れない: サブエージェントが受け取った SessionStart の文脈", sub)
        self.assertNotIn("Edit:", sub)
        full = (out / "context-1.md").read_text(encoding="utf-8")
        self.assertIn("スキル候補", full)

    def test_no_agent_call_means_no_subagent_part(self):
        out = self._out([deny_line("拒否")])
        ev = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["evidence.md"]
        self.assertNotIn("サブエージェント", ev)


class DenialTrendTest(unittest.TestCase):
    SESSIONS = {1: ["s1"], 2: ["s2"]}

    def test_three_denials_in_a_row_are_flagged(self):
        rows = [rec(decision="deny", code="DENY_PATH", subject=f"f{i}") for i in range(3)]
        got = runner.repeated_denials(rows, self.SESSIONS)
        self.assertEqual([(g["step"], g["code"], g["count"]) for g in got], [("1", "DENY_PATH", 3)])

    def test_two_denials_are_not_flagged(self):
        rows = [rec(decision="deny", code="DENY_PATH") for _ in range(2)]
        self.assertEqual(runner.repeated_denials(rows, self.SESSIONS), [])

    def test_allow_between_denials_resets_the_count(self):
        seq = ["deny", "deny", "allow", "deny", "deny"]
        rows = [rec(decision=d, code="DENY_PATH") for d in seq]
        self.assertEqual(runner.repeated_denials(rows, self.SESSIONS), [])

    def test_other_rows_between_denials_do_not_reset_but_other_code_does(self):
        rows = [
            rec(decision="deny", code="A"),
            rec(decision="skip", code=""),
            rec(decision="deny", code="A"),
            rec(decision="deny", code="A"),
            rec(decision="deny", code="B"),
        ]
        got = runner.repeated_denials(rows, self.SESSIONS)
        self.assertEqual([(g["code"], g["count"]) for g in got], [("A", 3)])

    def test_steps_are_counted_apart(self):
        rows = [rec(decision="deny", code="A", session="s1") for _ in range(2)]
        rows += [rec(decision="deny", code="A", session="s2") for _ in range(2)]
        self.assertEqual(runner.repeated_denials(rows, self.SESSIONS), [])

    def _streams(self, calls):
        return {1: {"tool_calls": calls}}

    def _call(self, path, name="Edit"):
        return {"name": name, "input": {"file_path": path}, "id": None, "parent": None}

    def test_same_path_after_a_denial_is_flagged_through_pathcase(self):
        rows = [rec(decision="deny", tool="Edit", subject="/work/Repo/README.md")]
        streams = self._streams(
            [self._call("/work/Repo/README.md"), self._call("/work/repo/readme.md", "Write")]
        )
        got = runner.bypass_suspects(rows, streams, self.SESSIONS)
        self.assertEqual([(g["step"], g["denied"], g["calls"]) for g in got], [("1", 1, 2)])

    def test_retry_that_was_denied_again_is_not_a_bypass(self):
        rows = [rec(decision="deny", tool="Edit", subject="/work/a.md") for _ in range(2)]
        streams = self._streams([self._call("/work/a.md"), self._call("/work/a.md")])
        self.assertEqual(runner.bypass_suspects(rows, streams, self.SESSIONS), [])

    def test_other_path_bash_and_cut_subjects_are_not_compared(self):
        streams = self._streams([self._call("/work/b.md")])
        rows = [rec(decision="deny", tool="Edit", subject="/work/a.md")]
        self.assertEqual(runner.bypass_suspects(rows, streams, self.SESSIONS), [])
        bash_rows = [rec(decision="deny", tool="Bash", subject="sed -i s/a/b/ /work/b.md")]
        self.assertEqual(runner.bypass_suspects(bash_rows, streams, self.SESSIONS), [])
        cut = [rec(decision="deny", tool="Edit", subject="/work/b.md…(+30)")]
        self.assertEqual(runner.bypass_suspects(cut, streams, self.SESSIONS), [])

    def test_denial_without_guide_line_is_flagged(self):
        tmp, out = make_out(
            [
                "\n".join(
                    [
                        deny_line("[ccnavi] DENY_PATH\n編集しません。"),
                        deny_line("[ccnavi] DENY_PATH\n理由\n  次: sh x.sh を打つ"),
                        deny_line("理由\n案内: 補足"),
                        deny_line("理由\n必須: 項目"),
                        deny_line("本文の途中に 次: と書いても行頭ではない"),
                    ]
                )
            ]
        )
        self.addCleanup(tmp.cleanup)
        got = runner.unguided_denials(runner.step_streams(out))
        self.assertEqual(
            [text for _, _, text in got],
            ["[ccnavi] DENY_PATH\n編集しません。", "本文の途中に 次: と書いても行頭ではない"],
        )

    def test_report_shows_the_marks(self):
        deny = deny_line("[ccnavi] DENY_PATH\n編集しません。")
        tmp, out = make_out(["\n".join([deny, deny, deny]) + "\n"])
        self.addCleanup(tmp.cleanup)
        rows = [
            rec(decision="deny", code="DENY_PATH", tool="Edit", subject=f"/w/{i}") for i in range(3)
        ]
        report = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, rows)["report.md"]
        self.assertIn("## 拒否の傾向（目印。判定ではない）", report)
        self.assertIn("- step 1: DENY_PATH が 3 回連続", report)
        self.assertIn("- step 1 [PreToolUse:Edit]: [ccnavi] DENY_PATH 編集しません。", report)

    def test_report_without_marks_says_none(self):
        tmp, out = make_out([call_line("Read", {"file_path": "a"}) + "\n"])
        self.addCleanup(tmp.cleanup)
        report = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["report.md"]
        self.assertEqual(report.count("- なし"), 3)


class MessagesTest(unittest.TestCase):
    def test_normalize_order_keeps_commit_ids_intact(self):
        text = "commit 0123abc の 3 件を /home/u/a/b.sh と C:\\w\\x で見る"
        self.assertEqual(
            runner.normalize_message(text),
            "commit <SHA> の <N> 件を <PATH> と <PATH> で見る",
        )
        self.assertEqual(runner.normalize_message("12345678"), "<SHA>")

    def test_paths_with_japanese_punctuation_around_them(self):
        self.assertEqual(
            runner.normalize_message("（.ccnavi/docs/a.md「節」）を読む"), "（<PATH>「節」）を読む"
        )

    def test_texts_differing_only_in_path_become_one_entry(self):
        a = deny_line("[ccnavi] DENY_PATH\nsubject: /w/one/README.md\n編集しません。 3 回目")
        b = deny_line("[ccnavi] DENY_PATH\nsubject: /w/two/main.sh\n編集しません。 4 回目")
        c = deny_line("別の文面")
        tmp, out = make_out(["\n".join([a, b, c]) + "\n"])
        self.addCleanup(tmp.cleanup)
        rows = [
            rec(
                decision="deny",
                code="DENY_PATH",
                rules=["r:workspace-root"],
                subject="/w/one/README.md",
            ),
            rec(
                decision="deny",
                code="DENY_PATH",
                rules=["r:workspace-root"],
                subject="/w/two/main.sh",
            ),
            rec(decision="deny", code="DENY_X", rules=[], subject="x"),
        ]
        msgs = runner.collect_messages(runner.step_streams(out), rows)
        self.assertEqual([m["count"] for m in msgs], [2, 1])
        self.assertEqual(msgs[0]["src"], ["DENY_PATH / r:workspace-root"])
        self.assertEqual(msgs[1]["src"], ["DENY_X / -"])
        text = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, rows)["messages.md"]
        self.assertEqual(text.count("編集しません。"), 1)
        self.assertIn("## 1. 拒否（2 回）", text)
        self.assertIn("出どころ（code / rules）: DENY_PATH / r:workspace-root", text)

    def test_source_is_unknown_when_counts_differ(self):
        out_tmp, out = make_out([deny_line("拒否") + "\n"])
        self.addCleanup(out_tmp.cleanup)
        msgs = runner.collect_messages(runner.step_streams(out), [])
        self.assertEqual(msgs[0]["src"], [])

    def test_session_start_context_is_left_out(self):
        start = hook_line(
            "SessionStart", "s", {"hookSpecificOutput": {"additionalContext": "決まり"}}
        )
        guide = hook_line(
            "PostToolUse", "p", {"hookSpecificOutput": {"additionalContext": "案内文"}}
        )
        tmp, out = make_out([start + "\n" + guide + "\n"])
        self.addCleanup(tmp.cleanup)
        msgs = runner.collect_messages(runner.step_streams(out), [])
        self.assertEqual([(m["kind"], m["text"]) for m in msgs], [("案内", "案内文")])

    def test_real_stream_fixture_with_its_decisions(self):
        tmp, out = make_out([fixture_text("subagent-edit-denied.jsonl")])
        self.addCleanup(tmp.cleanup)
        rows = fixture_rows("subagent-edit-denied.decisions.jsonl")
        msgs = runner.collect_messages(runner.step_streams(out), rows)
        kinds = [(m["kind"], m["events"]) for m in msgs]
        self.assertIn(("拒否", ["PreToolUse"]), kinds)
        self.assertIn(("案内", ["SubagentStart"]), kinds)
        deny = next(m for m in msgs if m["kind"] == "拒否")
        self.assertEqual(
            deny["src"], ["DENY_PATH / .ccnavi/config/common/rules.yml:workspace-root"]
        )


if __name__ == "__main__":
    unittest.main()
