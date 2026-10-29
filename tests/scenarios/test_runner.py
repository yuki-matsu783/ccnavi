"""受入シナリオの実行器の単体テスト。claude は呼ばない（呼べない門をここで確かめる）。

シナリオを回すこと自体は、全量テストに入れない（.agents/docs/acceptance-scenarios.md）。
ここで確かめるのは、実行器の判定の部分と、シナリオの YAML の形、そして全量テストが
シナリオを拾わないこと。
"""

import contextlib
import io
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

from ccnavi.entry import version
from tests import ROOT, common_sh, fake_gitlab, host_stub
from tests import __main__ as full_run
from tests.scenarios import hostenv, runner

SCENARIOS = sorted(Path(__file__).parent.glob("UC-*.yml"))
EXPECT_KEYS = {
    "decisions",
    "decisions_none",
    "files_unchanged",
    "state",
    "transcript_forbidden",
    "judge",
}


def setUpModule():
    # dry-run のセッションからテストを回しても、mode_check が実行を拒まないようにする
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    os.environ.pop("CCNAVI_MODE", None)


class IssueCommandTest(unittest.TestCase):
    ISSUE = {"title": "タイトル", "body": "本文", "repo": "projects/test-repo"}

    def test_gitlab_uses_glab(self):
        self.assertEqual(
            runner.issue_create_args("gitlab", self.ISSUE),
            ["glab", "issue", "create", "--title", "タイトル", "--description", "本文", "--yes"],
        )
        self.assertEqual(runner.issue_close_args("gitlab", 12), ["glab", "issue", "close", "12"])

    def test_github_uses_gh(self):
        self.assertEqual(
            runner.issue_create_args("github", self.ISSUE),
            ["gh", "issue", "create", "--title", "タイトル", "--body", "本文"],
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
        self.assertEqual([r["kind"] for r in report], ["expect_after", "repo", "host", "cost"])
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


def stop_block(reason):
    return hook_line("Stop", "Stop", {"decision": "block", "reason": reason})


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

    def flagged(self, *lines):
        tmp, out = make_out(["\n".join(lines) + "\n"])
        self.addCleanup(tmp.cleanup)
        return [text for _, _, text in runner.unguided_denials(runner.step_streams(out))]

    def test_denial_without_next_step_is_flagged(self):
        got = self.flagged(
            deny_line("[ccnavi] DENY_PATH\n編集しません。"),
            deny_line("[ccnavi] DENY_PATH\n理由\n  次: sh x.sh を打つ"),
            deny_line("理由\n案内: 補足"),
            deny_line("理由\n必須: 項目"),
            deny_line("本文の途中に 次: と書いても行頭ではない"),
            deny_line("[ccnavi] DENY_PATH"),
        )
        self.assertEqual(
            got,
            [
                "[ccnavi] DENY_PATH\n編集しません。",
                "本文の途中に 次: と書いても行頭ではない",
                "[ccnavi] DENY_PATH",
            ],
        )

    def test_denial_with_empty_reason_is_flagged(self):
        empty = hook_line(
            "PreToolUse",
            "PreToolUse:Bash",
            {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": ""}},
        )
        block = hook_line("Stop", "Stop", {"decision": "block"})
        self.assertEqual(self.flagged(empty, block), [runner.EMPTY_REASON] * 2)

    def test_ask_is_not_a_denial(self):
        ask = hook_line(
            "PreToolUse",
            "PreToolUse:Bash",
            {"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "x"}},
        )
        self.assertEqual(self.flagged(ask), [])

    def test_real_denials_with_a_next_step_are_not_flagged(self):
        # issue #570 の実データ 4 件。文面は実コードから取る（文面が変わっても追従する）
        from ccnavi.policy import builtin
        from ccnavi.policy import texts as ptexts
        from ccnavi.tickets.lifecycle import stop_integrate as stop
        from ccnavi.tickets.model import ticket_guard

        root = str(ROOT)
        raw_git = ptexts.spoken(ticket_guard.raw_git_rule(root))
        by_id = {r.id: ptexts.spoken(r) for r in builtin.load_base(root)[0].deny}
        heredoc = next(
            r["message"]
            for r in yaml.safe_load(
                (Path(ROOT) / ".ccnavi/config/common/rules.yml").read_text("utf-8")
            )["deny"]
            if r["id"] == "heredoc"
        )
        found = stop.Direct(
            name="work1",
            worktree=f"{root}/.claude/worktrees/work1",
            head="work1",
            dirty=False,
            ahead=1,
            integration="main",
            integ_tree=root,
            base="main",
        )
        nudge = stop.integrate_nudge(root, found)
        waiting = stop.integrate_nudge(root, stop.Direct(**{**found.__dict__, "marker": "v4"}))
        texts = [raw_git, by_id["builtin:recursive-delete"], heredoc, nudge, waiting]
        self.assertTrue(all(texts))
        self.assertEqual(
            self.flagged(*[deny_line(t) for t in texts[:3]], *[stop_block(t) for t in texts[3:]]),
            [],
        )

    def test_every_shipped_deny_message_has_a_next_step(self):
        from ccnavi.policy import builtin
        from ccnavi.policy import texts as ptexts

        rules = yaml.safe_load((Path(ROOT) / ".ccnavi/config/common/rules.yml").read_text("utf-8"))
        texts = [r["message"] for r in rules["deny"]]
        texts += [ptexts.spoken(r) for r in builtin.load_base(str(ROOT))[0].deny]
        with tempfile.TemporaryDirectory() as d:  # git の入口が無いワークスペースの文面
            shutil.copytree(Path(ROOT) / ".ccnavi/messages", Path(d) / ".ccnavi/messages")
            texts += [ptexts.spoken(r) for r in builtin.load_base(d)[0].deny]
        self.assertGreater(len(texts), 10)
        self.assertEqual([t for t in texts if not runner.has_guide(t)], [])

    def test_report_names_the_empty_and_unguided(self):
        deny = deny_line("[ccnavi] DENY_PATH\n編集しません。")
        tmp, out = make_out([deny + "\n"])
        self.addCleanup(tmp.cleanup)
        report = write_all({"id": "UC-99", "title": "t", "expect": {}}, out, [])["report.md"]
        self.assertIn("### 案内の無い拒否（理由文に次の手を示す語", report)
        self.assertIn("- step 1 [PreToolUse:Edit]: [ccnavi] DENY_PATH 編集しません。", report)

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

    def ccnavi_state(self, ws, name="work1"):
        """ccnavi が対象の .git に書く記録・marker・ref を作る（ブランチも切る）。"""
        t = ws.target
        common = t / ".git"
        (common / "ccnavi-direct").mkdir()
        (common / "ccnavi-direct" / name).write_text("version 4\n", encoding="utf-8")
        (common / "ccnavi-integrate" / name).mkdir(parents=True)
        (common / "ccnavi-integrate" / name / "marker").write_text("m\n", encoding="utf-8")
        (common / "ccnavi-integrate-done").mkdir()
        (common / "ccnavi-integrate-done" / name).write_text("done\n", encoding="utf-8")
        git(t, "branch", name)
        git(t, "update-ref", f"refs/ccnavi/direct/{name}/s", ws.base)
        git(t, "update-ref", f"refs/ccnavi/direct/{name}/pending", ws.base)
        return common

    def test_ccnavi_own_records_markers_and_refs_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            common = self.ccnavi_state(ws)
            (ws.target / "logs").mkdir()
            (ws.target / "logs" / "keep.log").write_text("判定の記録\n", encoding="utf-8")
            (ws.target / ".gitignore").write_text("logs/\n", encoding="utf-8")
            git(ws.target, "add", ".gitignore")
            git(ws.target, "commit", "-q", "-m", "ignore logs")
            base = git(ws.target, "rev-parse", "HEAD")
            runner.reset_repo(ws.root, ws.target, base)
            self.assertFalse((common / "ccnavi-direct").exists())
            self.assertFalse((common / "ccnavi-integrate").exists())
            self.assertFalse((common / "ccnavi-integrate-done").exists())
            self.assertEqual(git(ws.target, "for-each-ref", "refs/ccnavi"), "")
            self.assertEqual(
                git(ws.target, "for-each-ref", "--format=%(refname:short)", "refs/heads"), "main"
            )
            self.assertTrue((ws.target / "logs" / "keep.log").exists(), "ignore 済みは残す")

    def test_temp_worktree_with_a_record_leaves_nothing_prunable(self):
        # 一時ワークツリーは本体の logs/direct-tmp/<repo>/<名前> にある（対象の外）。
        # 登録が生きているものと、ディレクトリだけ先に消えたもの（prunable）の両方
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            self.ccnavi_state(ws)
            base_dir = ws.root / "logs" / "direct-tmp" / "test-repo"
            alive, gone = base_dir / "work1", base_dir / "work2"
            git(ws.target, "worktree", "add", "-q", "--detach", str(alive), ws.base)
            git(ws.target, "worktree", "add", "-q", "--detach", str(gone), ws.base)
            shutil.rmtree(gone)
            self.assertIn("prunable", git(ws.target, "worktree", "list", "--porcelain"))
            runner.reset_repo(ws.root, ws.target, ws.base)
            self.assertEqual(len(git(ws.target, "worktree", "list").splitlines()), 1)
            self.assertNotIn("prunable", git(ws.target, "worktree", "list", "--porcelain"))
            self.assertFalse(alive.exists())

    def test_failed_worktree_removal_keeps_the_records(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            common = self.ccnavi_state(ws)
            locked = ws.root / "logs" / "direct-tmp" / "test-repo" / "work1"
            git(ws.target, "worktree", "add", "-q", "--detach", str(locked), ws.base)
            git(ws.target, "worktree", "lock", str(locked))
            with self.assertRaises(runner.Refusal):
                runner.reset_repo(ws.root, ws.target, ws.base)
            self.assertTrue((common / "ccnavi-direct" / "work1").exists())
            refs = git(ws.target, "for-each-ref", "refs/ccnavi")
            self.assertIn("refs/ccnavi/direct/work1/s", refs)

    def test_ccnavi_state_of_the_body_is_not_touched(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            common = ws.root / ".git"
            (common / "ccnavi-direct").mkdir()
            (common / "ccnavi-direct" / "mine").write_text("x\n", encoding="utf-8")
            git(ws.root, "update-ref", "refs/ccnavi/direct/mine/s", "HEAD")
            with self.assertRaises(runner.Refusal):
                runner.reset_repo(ws.root, ws.root, ws.base, allowed=ws.root)
            self.assertTrue((common / "ccnavi-direct" / "mine").exists())
            self.assertIn("refs/ccnavi/direct/mine/s", git(ws.root, "for-each-ref", "refs/ccnavi"))

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

    def test_resuming_matches_the_same_out_in_any_spelling(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = str(ws.target.resolve())
            ws.out.mkdir()
            link = Path(d) / "link"
            try:
                link.symlink_to(ws.out.resolve(), target_is_directory=True)
            except OSError:
                self.skipTest("symlink を作れない環境")
            before = os.getcwd()
            os.chdir(ws.out.parent)
            self.addCleanup(os.chdir, before)
            spellings = [
                os.path.relpath(ws.out),
                str(ws.out.resolve()),
                str(ws.out.resolve()) + "/",
                str(link),
            ]
            for held in (os.path.relpath(ws.out), str(ws.out.resolve())):
                (ws.target / ".git" / runner.LEFT_NAME).write_text(held, encoding="utf-8")
                for spelling in spellings:
                    runner.acquire_target(top, spelling, resuming=True)
                    (ws.target / ".git" / runner.LOCK_NAME).unlink()

    def test_left_marker_holds_realpath_not_folded(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            top = str(ws.target.resolve())
            ws.out.mkdir()
            runner.acquire_target(top, ws.out)
            st = {"target": {"path": top, "base": None}, "out": str(ws.out)}
            runner.release_target(st, ws.root, hold=True)
            marker = ws.target / ".git" / runner.LEFT_NAME
            self.assertEqual(marker.read_text(encoding="utf-8"), os.path.realpath(ws.out))

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


class RunAllTest(unittest.TestCase):
    """run-all: 使い捨てのリポジトリのシナリオを、1 回の確認で直列に回す。claude は呼ばない。"""

    def make(self, ws, sid, *, budget=1.0, reset=True, human=False, body=False):
        """シナリオを 1 本書く。reset=False は host: bare だけ（対象は触るが戻さない）、
        body=True は setup なし（使い捨てのリポジトリを触らない）。"""
        if body:
            setup = {}
        elif reset:
            setup = {
                "git": [["git", "switch", "-q", "-c", f"feat-{sid.lower()}"]],
                "reset": {"base": ws.base},
            }
        else:
            setup = {"host": "bare"}
        steps = [{"human": True, "instruction": "打つ"}] if human else [{"prompt": f"{sid} をやる"}]
        data = {
            "id": sid,
            "title": f"タイトル {sid}",
            "model": "haiku",
            "budget_usd": budget,
            "steps": steps,
            "expect": {},
            "setup": setup,
        }
        d = ws.root.parent / "scn"
        d.mkdir(exist_ok=True)
        (d / f"{sid}.yml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return d

    def main(self, ws, *args):
        with (
            mock.patch.object(runner, "_ENTRY", True),
            mock.patch.object(runner, "mode_check", return_value=(True, "## モード: enable")),
        ):
            return runner.main(["run-all", *args, "--root", str(ws.root)])

    def digest(self, ws, scn_dir, cap):
        paths = runner.expand_scenario_paths([str(scn_dir)])
        eligible, _ = runner.run_all_plan(paths, ws.root)
        return runner.run_all_digest(eligible, cap)

    def fake_step(self, costs, seen=None):
        calls = []

        def run_step(scn, step, out, idx, sid, resume_id, persist, root, issue_no=None, env=None):
            calls.append((scn["id"], sid, resume_id, persist))
            if seen is not None:
                seen.append(scn["id"])
            cost = costs.get(scn["id"], 0.0)
            if isinstance(cost, Exception):
                raise cost
            return {"cost_usd": cost, "session_ids": []}

        return run_step, calls

    def test_without_confirmation_it_prints_the_list_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            self.make(ws, "UC-92", budget=0.5)
            step, calls = self.fake_step({})
            buf = io.StringIO()
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(buf),
            ):
                rc = self.main(ws, str(scn_dir), "--max-total-usd", "3")
            text = buf.getvalue()
            self.assertEqual(rc, 3)
            self.assertEqual(calls, [])
            self.assertIn("| UC-91 |", text)
            self.assertIn("| UC-92 |", text)
            self.assertIn(ws.base[:12], text)
            self.assertIn("3.0 USD", text)  # 費用の上限
            self.assertIn(f"--confirmed {self.digest(ws, scn_dir, 3.0)}", text)
            self.assertTrue(git(ws.target, "branch", "--show-current") == "main")

    def test_only_disposable_repo_scenarios_are_run_and_the_rest_are_listed_with_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            self.make(ws, "UC-92", body=True)
            self.make(ws, "UC-93", reset=False)
            self.make(ws, "UC-94", human=True)
            (scn_dir / "broken.yml").write_text("id: UC-95\n", encoding="utf-8")
            eligible, skipped = runner.run_all_plan(
                runner.expand_scenario_paths([str(scn_dir)]), ws.root
            )
            self.assertEqual([s["id"] for _, s in eligible], ["UC-91"])
            why = dict(skipped)
            self.assertIn("ccnavi 本体に作用しうる", why["UC-92"])
            self.assertIn("setup.reset が無い", why["UC-93"])
            self.assertIn("人間の操作", why["UC-94"])
            self.assertTrue(any("読み込めない" in y for w, y in skipped if "broken" in w))

    def test_the_body_is_refused_as_a_target(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            shutil.rmtree(ws.target / ".git")  # 対象がリポジトリでなくなると、本体のものになる
            eligible, skipped = runner.run_all_plan(
                runner.expand_scenario_paths([str(scn_dir)]), ws.root
            )
            self.assertEqual(eligible, [])
            self.assertIn("ccnavi 本体", dict(skipped)["UC-91"])

    def test_confirmed_run_goes_in_order_and_continues_after_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-92")
            self.make(ws, "UC-91")
            self.make(ws, "UC-93")
            seen = []
            step, calls = self.fake_step(
                {"UC-91": 0.25, "UC-92": RuntimeError("起動できない")}, seen
            )
            batch = Path(d) / "batch"
            buf = io.StringIO()
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(buf),
            ):
                rc = self.main(
                    ws,
                    str(scn_dir),
                    "--max-total-usd",
                    "5",
                    "--confirmed",
                    self.digest(ws, scn_dir, 5.0),
                    "--out",
                    str(batch),
                )
            self.assertEqual(seen, ["UC-91", "UC-92", "UC-93"])  # ID 順で直列。失敗しても続く
            self.assertEqual(rc, 2)
            summary = json.loads((batch / "summary.json").read_text(encoding="utf-8"))
            by_id = {r["id"]: r for r in summary["scenarios"]}
            self.assertEqual(by_id["UC-91"]["cost_usd"], 0.25)
            self.assertEqual(summary["total_cost_usd"], 0.25)
            self.assertIn(by_id["UC-92"]["verdict"], (runner.INCONCLUSIVE,))
            text = (batch / "summary.md").read_text(encoding="utf-8")
            self.assertIn("| ID | 判定 | 実費 (USD) | ツール呼び出し | 拒否 | ask |", text)
            self.assertIn("| UC-91 |", text)
            self.assertEqual(
                [k for k in ("tool_calls", "denies", "asks") if k not in by_id["UC-91"]], []
            )
            # 実費は各回の report.json にも残る
            report = json.loads((batch / "UC-91" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual([r["cost_usd"] for r in report if r["kind"] == "cost"], [0.25])
            # どの回も、対象は base に戻っている
            self.assertEqual(git(ws.target, "rev-parse", "main"), ws.base)
            self.assertEqual(git(ws.target, "branch", "--show-current"), "main")
            self.assertEqual(len(calls), 3)

    def test_each_scenario_starts_a_fresh_session_without_resume(self):
        # hook の once の案内（hinted）の記憶はセッションごと。前のシナリオの session を
        # 持ち越さない（新しい --session-id、resume なし、persist なし）ことを見る
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            self.make(ws, "UC-92")
            step, calls = self.fake_step({})
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.main(
                    ws,
                    str(scn_dir),
                    "--max-total-usd",
                    "5",
                    "--confirmed",
                    self.digest(ws, scn_dir, 5.0),
                    "--out",
                    str(Path(d) / "batch"),
                )
            sessions = [sid for _, sid, _, _ in calls]
            self.assertEqual(len(set(sessions)), 2)
            self.assertEqual([r for _, _, r, _ in calls], [None, None])
            self.assertEqual(
                [p for _, _, _, p in calls], [False, False]
            )  # --no-session-persistence

    def test_hinted_state_is_not_carried_over_to_another_session(self):
        from ccnavi.infra import hookio
        from ccnavi.policy import ctxfile, rules

        rule = rules.Rule(id="once", match="Write", additional_context_once="案内")
        with tempfile.TemporaryDirectory() as state:

            def told(session):
                payload = hookio.Input(event="PreToolUse", tool_name="Write", session_id=session)
                return ctxfile.deliver(io.StringIO(), state, payload, [rule])[1]

            self.assertTrue(told("scn-1"))
            self.assertFalse(told("scn-1"))  # 同じセッションの 2 回目は渡さない
            self.assertTrue(told("scn-2"))  # 次のシナリオ（別のセッション）には、また渡る

    def test_a_changed_set_stops_before_running(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            confirmed = self.digest(ws, scn_dir, 5.0)
            self.make(ws, "UC-92")  # 確認のあとに増えた
            step, calls = self.fake_step({})
            buf = io.StringIO()
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(buf),
            ):
                rc = self.main(ws, str(scn_dir), "--max-total-usd", "5", "--confirmed", confirmed)
            self.assertEqual(rc, 3)
            self.assertEqual(calls, [])
            self.assertIn("確認した内容と今回の対象が違う", buf.getvalue())
            self.assertIn("UC-91, UC-92", buf.getvalue())

    def test_a_removed_scenario_a_new_cap_and_a_new_base_each_invalidate_the_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            self.make(ws, "UC-92")
            confirmed = self.digest(ws, scn_dir, 5.0)
            # 費用の上限が違う
            self.assertNotEqual(confirmed, self.digest(ws, scn_dir, 4.0))
            # base が違う
            git(ws.target, "commit", "--allow-empty", "-q", "-m", "next")
            other = git(ws.target, "rev-parse", "HEAD")
            path = scn_dir / "UC-92.yml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["setup"]["reset"]["base"] = other
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            self.assertNotEqual(confirmed, self.digest(ws, scn_dir, 5.0))
            # 減った
            path.unlink()
            self.assertNotEqual(confirmed, self.digest(ws, scn_dir, 5.0))

    def test_it_stops_when_the_next_budget_exceeds_what_is_left(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            self.make(ws, "UC-92")
            self.make(ws, "UC-93")
            step, calls = self.fake_step({"UC-91": 1.0, "UC-92": 1.0})
            batch = Path(d) / "batch"
            buf = io.StringIO()
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(buf),
            ):
                rc = self.main(
                    ws,
                    str(scn_dir),
                    "--max-total-usd",
                    "2.5",
                    "--confirmed",
                    self.digest(ws, scn_dir, 2.5),
                    "--out",
                    str(batch),
                )
            self.assertEqual(rc, 3)
            self.assertEqual([c[0] for c in calls], ["UC-91", "UC-92"])  # UC-93 は回さない
            summary = json.loads((batch / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["remaining"], ["UC-93"])
            self.assertIn("UC-93", summary["stopped"])
            self.assertIn("回した本数 2", (batch / "summary.md").read_text(encoding="utf-8"))
            self.assertIn("残りの ID: UC-93", buf.getvalue())

    def test_a_single_budget_over_the_cap_runs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91", budget=3.0)
            step, calls = self.fake_step({})
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = self.main(
                    ws,
                    str(scn_dir),
                    "--max-total-usd",
                    "2",
                    "--confirmed",
                    self.digest(ws, scn_dir, 2.0),
                    "--out",
                    str(Path(d) / "batch"),
                )
            self.assertEqual(rc, 3)
            self.assertEqual(calls, [])

    def test_non_positive_cap_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.main(ws, str(scn_dir), "--max-total-usd", "0"), 2)

    def test_nan_and_inf_caps_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            step, calls = self.fake_step({})
            with (
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                for bad in ("nan", "inf"):
                    self.assertEqual(self.main(ws, str(scn_dir), "--max-total-usd", bad), 2)
            self.assertEqual(calls, [])

    def test_editing_the_prompt_or_permission_mode_invalidates_the_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            confirmed = self.digest(ws, scn_dir, 5.0)
            path = scn_dir / "UC-91.yml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["permission_mode"] = "bypassPermissions"
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            self.assertNotEqual(confirmed, self.digest(ws, scn_dir, 5.0))
            data["permission_mode"] = "auto"
            data["steps"][0]["prompt"] += "x" * 200  # 概要は 120 字で切るので出ない後ろ
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            self.assertNotEqual(confirmed, self.digest(ws, scn_dir, 5.0))

    def test_failed_step_cost_counts_toward_the_total(self):
        scn = {"budget_usd": 1.0, "_prompt_steps": 2}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            timeout = subprocess.TimeoutExpired("claude", 1)
            self.assertEqual(runner._failed_step_cost(scn, out, 1, timeout), 0.5)  # step の上限
            self.assertEqual(runner._failed_step_cost(scn, out, 1, RuntimeError("x")), 0.0)
            line = json.dumps({"type": "result", "total_cost_usd": 0.4, "session_id": "s"})
            (out / "step-1.jsonl").write_text(line + "\n", encoding="utf-8")
            self.assertEqual(runner._failed_step_cost(scn, out, 1, RuntimeError("x")), 0.4)

    def test_issue_scenarios_raise_the_host_question(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            path = scn_dir / "UC-91.yml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["setup"]["issue"] = {"repo": "projects/test-repo", "title": "t", "body": "b"}
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            eligible, skipped = runner.run_all_plan(
                runner.expand_scenario_paths([str(path)]), ws.root
            )
            text = runner.run_all_confirmation(
                eligible, skipped, 5.0, "d", "m", ws.root, [str(path)]
            )
            self.assertIn("本物のホストに issue も作る", text)

    def test_not_enable_mode_runs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            scn_dir = self.make(ws, "UC-91")
            step, calls = self.fake_step({})
            with (
                mock.patch.object(runner, "_ENTRY", True),
                mock.patch.object(runner, "mode_check", return_value=(False, "dry-run")),
                mock.patch.object(runner, "run_step", side_effect=step),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = runner.main(
                    ["run-all", str(scn_dir), "--max-total-usd", "5", "--root", str(ws.root)]
                )
            self.assertEqual(rc, 3)
            self.assertEqual(calls, [])

    def test_run_all_is_closed_when_imported(self):
        args = runner.argparse.Namespace(
            scenarios=["x"], max_total_usd=1.0, confirmed=None, root=".", out=None
        )
        with self.assertRaises(RuntimeError):
            runner.cmd_run_all(args)


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


# ---- setup.host（評価の相手: bare / fake-gitlab / docker-gitlab / real） ----

# 別のグループ（tests/sh）の部品は import できないので、読むのに要るぶんだけここに持つ
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
REVIEW_SH = ("ccnavi-review.sh", "ccnavi-user-decide.sh", "ccnavi-user-lib-review.sh")
FAKE_PROJECT = "ns/proj"
# 実行ファイルの代役。版の問い合わせにだけ答える
EXE_STUB = """#!/bin/sh
case " $* " in
*" --version --json "*)
  printf '{{"compat": {compat}, "flags": ["--accept-unresolved", "--actor", "--via"]}}\\n'
  exit 0 ;;
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
esac
exit 0
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def seeded():
    """未解決のスレッドを 1 つ持つ MR が 1 件ある、模擬の初期状態。"""
    note = {
        "id": 101,
        "body": "ここを直してください",
        "resolvable": True,
        "resolved": False,
        "created_at": "2026-10-25T00:00:00Z",
        "author": {"id": 2, "username": "reviewer"},
        "position": {"new_path": "a.txt", "new_line": 3},
    }
    mr = {
        "iid": 1,
        "state": "opened",
        "source_branch": "i0001",
        "target_branch": "main",
        "title": "i0001",
        "web_url": f"http://127.0.0.1/{FAKE_PROJECT}/-/merge_requests/1",
        "discussions": [{"id": "d101", "individual_note": False, "notes": [note]}],
        "reviewers": [],
        "notes": [],
        "created_at": "2026-10-25T00:00:00Z",
    }
    return {"project": FAKE_PROJECT, "seq": 100, "issues": [], "mrs": [mr]}


REAL_ORIGIN = "https://github.com/acme/real.git"
BODY_ORIGIN = "https://github.com/acme/body.git"
HAS_TOOLS = all(shutil.which(t) for t in ("sh", "git", "jq", "curl"))


def host_workspace(tmp):
    return Workspace(tmp, origin_root=BODY_ORIGIN, origin_target=REAL_ORIGIN)


def private_tempdir(test, root):
    """ccnavi-host-* の置き場を、このテストだけのディレクトリにする（並列の他の回と混ざらない）。"""
    place = Path(root) / "tmp"
    place.mkdir()
    patcher = mock.patch.object(tempfile, "tempdir", str(place))
    patcher.start()
    test.addCleanup(patcher.stop)
    return place


def tokens_dropped_env():
    return {k: v for k, v in os.environ.items() if not hostenv.TOKEN_VAR.match(k)}


class HostFieldTest(unittest.TestCase):
    def load(self, setup):
        with tempfile.TemporaryDirectory() as d:
            return runner.load_scenario(write_scenario(d, setup))

    def test_values_are_checked_and_the_default_is_real(self):
        for host in ("bare", "fake-gitlab", "real"):
            with self.subTest(host=host):
                self.assertEqual(runner.scenario_host(self.load({"host": host})), host)
        self.assertEqual(runner.scenario_host(self.load({"files": {}})), "real")
        for bad in ("mock", "", None, True, "Bare"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.load({"host": bad})

    def test_it_is_not_combined_with_issue(self):
        issue = {"repo": "projects/test-repo", "title": "t"}
        for host in ("bare", "fake-gitlab"):
            with self.subTest(host=host), self.assertRaises(ValueError) as cm:
                self.load({"host": host, "issue": issue})
            self.assertIn("setup.issue", str(cm.exception))
        self.load({"host": "real", "issue": issue})

    def test_host_state_is_only_for_fake_gitlab_and_needs_a_project_path(self):
        self.load({"host": "fake-gitlab", "host_state": {"project": "a/b", "issues": []}})
        for setup in (
            {"host": "bare", "host_state": {}},
            {"host_state": {}},
            {"host": "fake-gitlab", "host_state": []},
            {"host": "fake-gitlab", "host_state": {"project": "proj"}},
        ):
            with self.subTest(setup=setup), self.assertRaises(ValueError):
                self.load(setup)

    def test_a_fake_host_uses_the_disposable_target(self):
        self.assertTrue(runner.uses_target({"setup": {"host": "bare"}}))
        self.assertTrue(runner.uses_target({"setup": {"host": "fake-gitlab"}}))
        self.assertFalse(runner.uses_target({"setup": {"host": "real"}}))
        self.assertFalse(runner.uses_target({}))

    def test_a_target_that_is_ccnavi_itself_is_refused_before_anything_is_made(self):
        with tempfile.TemporaryDirectory() as d:
            scratch = private_tempdir(self, d)
            ws = Workspace(d, origin_root=REAL_ORIGIN, origin_target=REAL_ORIGIN)
            path = ws.scenario_file({"host": "bare"})
            args = ["run", str(path), "--confirmed", "--root", str(ws.root), "--out", str(ws.out)]
            with (
                mock.patch.object(runner, "_ENTRY", True),
                mock.patch.object(runner, "run_step") as step,
                mock.patch("builtins.print") as shown,
            ):
                rc = runner.main(args)
            text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
            self.assertEqual(rc, 3)
            self.assertIn("origin が ccnavi 本体の origin と同じ", text)
            self.assertIn("次: ", text)
            step.assert_not_called()
            self.assertEqual(list(scratch.glob("ccnavi-host-*")), [])


class StepEnvTest(unittest.TestCase):
    def host(self, kind, **extra):
        return {
            "kind": kind,
            "bin": "/stub/bin",
            "routes": "/stub/routes.json",
            "log": "/stub/log",
            "target_common": "/t/.git",
            "bare": "/stub/origin.git",
            "real_origin": REAL_ORIGIN,
            "project": "ns/proj",
            "port": 4321,
            **extra,
        }

    def test_real_tokens_are_dropped_and_everything_else_is_inherited(self):
        base = {
            "PATH": "/usr/bin",
            "HOME": "/home/u",
            "HTTPS_PROXY": "http://p",
            "NODE_EXTRA_CA_CERTS": "/ca",
            "GITHUB_TOKEN": "real-gh",
            "GH_TOKEN": "real-gh2",
            "GITLAB_TOKEN": "real-gl",
            "CCNAVI_PROBE_REVIEWER_TOKEN": "real-probe",
            "github_token": "lower",
        }
        env = hostenv.step_env(self.host("bare"), base)
        for name in ("HOME", "HTTPS_PROXY", "NODE_EXTRA_CA_CERTS"):
            self.assertEqual(env[name], base[name])
        for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "CCNAVI_PROBE_REVIEWER_TOKEN"):
            self.assertNotIn(name, env)
        self.assertNotIn("github_token", env)
        self.assertEqual(env["PATH"], os.pathsep.join(["/stub/bin", "/usr/bin"]))
        self.assertEqual(env["FAKE_ORIGIN_URL"], host_stub.ORIGIN_URL)
        self.assertEqual(env["FAKE_BARE"], "/stub/origin.git")
        self.assertEqual(env["FAKE_REAL_ORIGIN"], REAL_ORIGIN)
        self.assertEqual(env["FAKE_TARGET_COMMON"], "/t/.git")

    def test_fake_gitlab_gets_only_the_fake_token_and_a_local_origin(self):
        base = {"PATH": "/usr/bin", "GITLAB_TOKEN": "real-gl", "GITHUB_TOKEN": "real-gh"}
        env = hostenv.step_env(self.host("fake-gitlab", token="fake-token"), base)
        self.assertEqual(env["GITLAB_TOKEN"], "fake-token")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["FAKE_ORIGIN_URL"], "http://127.0.0.1:4321/ns/proj.git")

    def test_a_scenario_without_host_passes_no_env(self):
        self.assertIsNone(runner._host_env({}))
        self.assertIsNone(runner._host_env({"host": {"down": True}}))

    def test_run_step_inherits_the_environment_when_env_is_none(self):
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        scn = {"model": "haiku", "budget_usd": 1.0, "_prompt_steps": 1, "permission_mode": "auto"}
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(runner, "_ENTRY", True),
            mock.patch.object(runner.subprocess, "run", return_value=done) as call,
        ):
            runner.run_step(scn, {"prompt": "a"}, Path(d), 1, "sid", None, False, d)
            self.assertIsNone(call.call_args.kwargs["env"])
            runner.run_step(scn, {"prompt": "a"}, Path(d), 1, "sid", None, False, d, env={"A": "1"})
            self.assertEqual(call.call_args.kwargs["env"], {"A": "1"})


@unittest.skipUnless(HAS_TOOLS, "sh・git・jq・curl のどれかが無い")
class BareHostTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = private_tempdir(self, self._tmp.name)
        self.ws = host_workspace(self._tmp.name)
        self.top = str(self.ws.target.resolve())
        self.root = str(self.ws.root.resolve())

    def prepare(self, kind="bare", **setup):
        host = hostenv.prepare(self.ws.root, self.top, {"host": kind, **setup}, runner._git)
        self.addCleanup(hostenv.teardown, self.ws.root, host, runner._git)
        return host, hostenv.step_env(host, tokens_dropped_env())

    def git(self, env, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)

    def test_the_stub_answers_a_host_shaped_origin_for_the_target_only(self):
        git(self.ws.target, "worktree", "add", "-q", str(self.ws.root.parent / "wt"), "-b", "w")
        host, env = self.prepare()
        for cwd in (self.top, str(self.ws.root.parent / "wt")):
            got = self.git(env, cwd, "remote", "get-url", "origin")
            self.assertEqual(got.stdout.strip(), host_stub.ORIGIN_URL, cwd)
        self.assertEqual(
            self.git(env, self.top, "-C", self.top, "remote", "get-url", "origin").stdout.strip(),
            host_stub.ORIGIN_URL,
        )
        # ccnavi 本体のリポジトリには、本物の答えを返す
        body = self.git(env, self.root, "remote", "get-url", "origin")
        self.assertEqual(body.stdout.strip(), BODY_ORIGIN)
        body = self.git(env, self.top, "-C", self.root, "remote", "get-url", "origin")
        self.assertEqual(body.stdout.strip(), BODY_ORIGIN)

    def test_a_users_insteadof_does_not_send_the_traffic_to_the_real_origin(self):
        config = Path(self._tmp.name) / "global.gitconfig"
        config.write_text(
            '[url "git@example.invalid:"]\n\tinsteadOf = https://github.com/\n', encoding="utf-8"
        )
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.assertTrue(
                git(self.ws.target, "remote", "get-url", "origin").startswith("git@example.invalid")
            )
            host, env = self.prepare()
            self.assertEqual(host["real_origin"], REAL_ORIGIN)
            git(self.ws.target, "switch", "-q", "-c", "feat")
            git(self.ws.target, "commit", "-q", "--allow-empty", "-m", "e")
            pushed = self.git(env, self.top, "push", "-q", "origin", "feat")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        sha = git(self.ws.target, "rev-parse", "feat")
        self.assertEqual(git(host["bare"], "rev-parse", "refs/heads/feat"), sha)

    def test_push_and_fetch_reach_the_bare_and_not_the_real_origin(self):
        host, env = self.prepare()
        git(self.ws.target, "switch", "-q", "-c", "feat")
        (self.ws.target / "new.txt").write_text("x\n", encoding="utf-8")
        git(self.ws.target, "add", "new.txt")
        git(self.ws.target, "commit", "-q", "-m", "new")
        pushed = self.git(env, self.top, "push", "-q", "origin", "feat")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        sha = git(self.ws.target, "rev-parse", "feat")
        self.assertEqual(git(host["bare"], "rev-parse", "refs/heads/feat"), sha)
        self.assertEqual(self.git(env, self.top, "fetch", "-q", "origin").returncode, 0)
        listed = self.git(env, self.top, "ls-remote", "origin")
        self.assertIn(f"{sha}\trefs/heads/feat", listed.stdout)
        # .git/config の origin は書き換えない
        self.assertEqual(git(self.ws.target, "config", "remote.origin.url"), REAL_ORIGIN)

    def test_ccnavi_git_sh_goes_through_the_stub_too(self):
        host, env = self.prepare()
        scripts = Path(self.top) / ".ccnavi" / "scripts"
        scripts.mkdir(parents=True)
        for name in os.listdir(SH_DIR):
            if name.startswith("ccnavi-") and name.endswith(".sh"):
                shutil.copy(os.path.join(SH_DIR, name), scripts)
        git(self.ws.target, "switch", "-q", "-c", "feat")
        done = subprocess.run(
            ["sh", str(scripts / "ccnavi-git.sh"), "fetch", "origin"],
            cwd=self.top,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_bare_is_made_from_the_origin_remote_refs(self):
        git(self.ws.target, "update-ref", "refs/remotes/origin/main", self.ws.base)
        git(self.ws.target, "update-ref", "refs/remotes/origin/side", self.ws.base)
        host, _ = self.prepare()
        heads = git(host["bare"], "for-each-ref", "--format=%(refname)", "refs/heads").split()
        self.assertEqual(sorted(heads), ["refs/heads/main", "refs/heads/side"])
        self.assertEqual(git(host["bare"], "symbolic-ref", "HEAD"), "refs/heads/main")

    def test_the_api_stubs_fail_and_curl_is_stubbed_for_bare(self):
        host, env = self.prepare()
        for tool in ("gh", "glab"):
            done = subprocess.run([tool, "x"], env=env, capture_output=True)
            self.assertEqual(done.returncode, 1, tool)
        self.assertTrue(os.path.isfile(os.path.join(host["bin"], "curl")))
        done = subprocess.run(
            ["curl", "-s", "https://api.github.com/x"], env=env, capture_output=True
        )
        self.assertEqual(done.returncode, 22)

    def test_teardown_removes_the_place_and_puts_the_remote_refs_back(self):
        git(self.ws.target, "update-ref", "refs/remotes/origin/main", self.ws.base)
        host, env = self.prepare()
        git(self.ws.target, "switch", "-q", "-c", "feat")
        git(self.ws.target, "commit", "-q", "--allow-empty", "-m", "e")
        self.assertEqual(self.git(env, self.top, "push", "-q", "origin", "feat").returncode, 0)
        self.assertIn("refs/remotes/origin/feat", git(self.ws.target, "for-each-ref"))
        hostenv.teardown(self.ws.root, host, runner._git)
        self.assertFalse(os.path.exists(host["dir"]))
        self.assertNotIn("refs/remotes/origin/feat", git(self.ws.target, "for-each-ref"))
        self.assertEqual(git(self.ws.target, "rev-parse", "refs/remotes/origin/main"), self.ws.base)
        hostenv.teardown(self.ws.root, host, runner._git)  # 2 回目は何もしない

    def test_a_target_without_origin_is_refused_and_leaves_nothing(self):
        git(self.ws.target, "remote", "remove", "origin")
        with self.assertRaises(RuntimeError):
            hostenv.prepare(self.ws.root, self.top, {"host": "bare"}, runner._git)
        self.assertEqual(list(self.scratch.glob("ccnavi-host-*")), [])


@unittest.skipUnless(HAS_TOOLS, "sh・git・jq・curl のどれかが無い")
class FakeGitlabHostTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = host_workspace(self._tmp.name)
        self.top = str(self.ws.target.resolve())

    def prepare(self, **setup):
        host = hostenv.prepare(
            self.ws.root, self.top, {"host": "fake-gitlab", **setup}, runner._git
        )
        self.addCleanup(hostenv.teardown, self.ws.root, host, runner._git)
        return host

    def test_review_sh_reads_the_fake_mr_and_thread_through_the_stubs(self):
        host = self.prepare(host_state=seeded())
        env = hostenv.step_env(host, tokens_dropped_env())
        self.assertNotIn("curl", os.listdir(host["bin"]))
        scripts = Path(self.top) / ".ccnavi" / "scripts"
        for name in (*REVIEW_SH, *common_sh(SH_DIR)):
            os.makedirs(scripts, exist_ok=True)
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        git(self.ws.target, "switch", "-q", "-c", "i0001")
        exe = write(
            os.path.join(self._tmp.name, "exe", "ccnavi"),
            EXE_STUB.format(compat=version.COMPAT, log=os.path.join(self._tmp.name, "args.txt")),
        )
        os.chmod(exe, 0o755)
        env.update({"CCNAVI_ENTRY_PATH": exe, "CCNAVI_LEARNING": "disable"})
        done = subprocess.run(
            ["sh", str(scripts / "ccnavi-review.sh"), "fetch"],
            cwd=self.top,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = json.loads(done.stdout)
        self.assertEqual(got["host"], "gitlab")
        self.assertEqual(got["mr"]["number"], 1)
        self.assertIn("ここを直してください", got["threads"][0]["body"])
        server = hostenv._SERVERS[host["dir"]]
        self.assertIn(f"GET /api/v4/projects/{FAKE_PROJECT.replace('/', '%2F')}", server.requests)

    def test_hold_stops_the_server_and_resume_keeps_the_state(self):
        host = self.prepare(host_state=seeded())
        port = host["port"]
        hostenv.hold(self.ws.root, host)
        self.assertTrue(os.path.isfile(host["state_path"]))
        with self.assertRaises(OSError):
            socket_connect(port)
        # 止めている間に模擬の状態を直接変える。作った MR が残ったときの代わり
        state = fake_gitlab.load(host["state_path"])
        state["seq"] = 777
        fake_gitlab.save(host["state_path"], state)
        self.assertEqual(hostenv.resume(self.ws.root, host), [])
        self.assertEqual(host["port"], port)
        self.assertEqual(fake_gitlab.load(host["state_path"])["seq"], 777)
        self.assertEqual(len(fake_gitlab.load(host["state_path"])["mrs"]), 1)
        socket_connect(port)

    def test_resume_on_another_port_says_so(self):
        host = self.prepare()
        hostenv.hold(self.ws.root, host)
        taken = socket_listen(host["port"])
        self.addCleanup(taken.close)
        notes = hostenv.resume(self.ws.root, host)
        self.assertNotEqual(host["port"], taken.getsockname()[1])
        self.assertEqual(len(notes), 1)
        self.assertIn("ポートが", notes[0])

    def test_teardown_stops_the_server(self):
        host = self.prepare()
        port = host["port"]
        hostenv.teardown(self.ws.root, host, runner._git)
        with self.assertRaises(OSError):
            socket_connect(port)
        self.assertFalse(os.path.exists(host["dir"]))


def socket_connect(port):
    import socket

    socket.create_connection(("127.0.0.1", port), timeout=2).close()


def socket_listen(port):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen()
    return sock


@unittest.skipUnless(HAS_TOOLS, "sh・git・jq・curl のどれかが無い")
class HostLifecycleTest(unittest.TestCase):
    """cmd_run から finish / 中断 / 人間の操作の step までを、claude を呼ばずに回す。"""

    STEP_OUT = {"cost_usd": 0.0, "session_ids": []}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scratch = private_tempdir(self, self._tmp.name)
        self.ws = host_workspace(self._tmp.name)

    def scenario(self, host, steps=None, **extra):
        setup = {"host": host, "reset": {"base": self.ws.base}, **extra}
        return self.ws.scenario_file(setup, steps)

    def run_cmd(self, path):
        args = ["run", str(path), "--confirmed", "--root", str(self.ws.root)]
        args += ["--out", str(self.ws.out)]
        with mock.patch.object(runner, "_ENTRY", True), mock.patch("builtins.print"):
            return runner.main(args)

    def places(self):
        return set(self.scratch.glob("ccnavi-host-*"))

    def host_in_state(self):
        return json.loads((self.ws.out / "state.json").read_text(encoding="utf-8"))["host"]

    def test_the_steps_get_the_stub_env_and_everything_is_gone_after_finish(self):
        seen = {}

        def run_step(*a, **k):
            seen["env"] = k["env"]
            seen["place"] = Path(k["env"]["PATH"].split(os.pathsep)[0]).parent
            seen["exists"] = seen["place"].is_dir()
            return self.STEP_OUT

        before = self.places()
        with mock.patch.object(runner, "run_step", side_effect=run_step):
            rc = self.run_cmd(self.scenario("bare"))
        self.assertIn(rc, (0, 2))
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["env"]["FAKE_ORIGIN_URL"], host_stub.ORIGIN_URL)
        self.assertEqual(self.places(), before)
        report = (self.ws.out / "report.md").read_text(encoding="utf-8")
        self.assertIn("相手（setup.host）: bare", report)
        self.assertIn("模擬のずれで見逃した問題がありうる", report)
        rows = json.loads((self.ws.out / "report.json").read_text(encoding="utf-8"))
        host_row = next(r for r in rows if r["kind"] == "host")
        self.assertEqual(host_row["expect"], "setup.host")
        self.assertIn("bare", host_row["why"])
        self.assertIn("模擬のずれ", host_row["why"])

    def test_real_leaves_the_run_unchanged(self):
        seen = {}

        def run_step(*a, **k):
            seen["env"] = k.get("env", "missing")
            return self.STEP_OUT

        before = self.places()
        with mock.patch.object(runner, "run_step", side_effect=run_step):
            self.run_cmd(self.scenario("real"))
        self.assertIsNone(seen["env"])
        self.assertEqual(self.places(), before)
        report = (self.ws.out / "report.md").read_text(encoding="utf-8")
        self.assertIn("相手（setup.host）: real: 本物", report)
        self.assertNotIn("模擬のずれ", report)

    def test_keyboard_interrupt_stops_the_server_and_leaves_no_place(self):
        before = self.places()
        ports = []

        def run_step(*a, **k):
            ports.append(self.host_in_state()["port"])
            raise KeyboardInterrupt

        with (
            mock.patch.object(runner, "run_step", side_effect=run_step),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.run_cmd(self.scenario("fake-gitlab"))
        with self.assertRaises(OSError):
            socket_connect(ports[0])
        self.assertEqual(self.places(), before)

    def test_setup_git_push_reaches_the_bare_and_not_the_real_origin(self):
        git_setup = [["git", "push", "-q", "origin", "main:refs/heads/feat"]]
        seen = {}

        def run_step(*a, **k):
            bare = self.host_in_state()["bare"]
            seen["bare"] = git(bare, "rev-parse", "--verify", "-q", "refs/heads/feat")
            return self.STEP_OUT

        with mock.patch.object(runner, "run_step", side_effect=run_step):
            self.run_cmd(self.scenario("bare", git=git_setup))
        self.assertTrue(seen["bare"])

    def test_a_failed_step_leaves_no_place(self):
        before = self.places()
        with mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")):
            self.assertEqual(self.run_cmd(self.scenario("fake-gitlab")), 2)
        self.assertEqual(self.places(), before)

    def test_a_setup_failure_after_the_host_is_made_removes_it(self):
        before = self.places()
        with (
            mock.patch.object(runner, "_setup", side_effect=RuntimeError("壊れた")),
            self.assertRaises(RuntimeError),
        ):
            self.run_cmd(self.scenario("fake-gitlab"))
        self.assertEqual(self.places(), before)

    def test_a_target_without_origin_is_refused_with_next(self):
        git(self.ws.target, "remote", "remove", "origin")
        before = self.places()
        with (
            mock.patch.object(runner, "run_step") as step,
            mock.patch("builtins.print") as shown,
        ):
            args = ["run", str(self.scenario("bare")), "--confirmed", "--root", str(self.ws.root)]
            with mock.patch.object(runner, "_ENTRY", True):
                rc = runner.main([*args, "--out", str(self.ws.out)])
        text = "\n".join(str(c.args[0]) for c in shown.call_args_list if c.args)
        self.assertEqual(rc, 3)
        self.assertIn("origin が無い", text)
        self.assertIn("次: ", text)
        step.assert_not_called()
        self.assertEqual(self.places(), before)

    def test_a_human_step_stops_the_server_and_keeps_the_place_until_next_ends(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}, {"prompt": "b"}]
        made = {}

        def first(*a, **k):
            made["port"] = self.host_in_state()["port"]
            state_path = self.host_in_state()["state_path"]
            made["state_path"] = state_path
            state = fake_gitlab.load(state_path)
            state["seq"] = 555  # この step で模擬の状態が動いたものとして扱う
            fake_gitlab.save(state_path, state)
            return self.STEP_OUT

        before = self.places()
        with mock.patch.object(runner, "run_step", side_effect=first):
            self.assertEqual(self.run_cmd(self.scenario("fake-gitlab", steps)), 10)
        host = self.host_in_state()
        self.assertTrue(os.path.isdir(host["dir"]), "置き場は残す")
        with self.assertRaises(OSError):
            socket_connect(made["port"])
        self.assertEqual(len(self.places() - before), 1)

        seen = {}

        def second(*a, **k):
            seen["seq"] = fake_gitlab.load(made["state_path"])["seq"]
            seen["port"] = self.host_in_state()["port"]
            socket_connect(seen["port"])
            seen["url"] = k["env"]["FAKE_ORIGIN_URL"]
            return self.STEP_OUT

        with (
            mock.patch.object(runner, "run_step", side_effect=second),
            mock.patch("builtins.print"),
            mock.patch.object(runner, "mode_check", return_value=(True, "")),
        ):
            rc = runner.main(["next", str(self.ws.out)])
        self.assertIn(rc, (0, 2))
        self.assertEqual(seen["seq"], 555, "next は状態を初期値で上書きしない")
        self.assertEqual(seen["url"], f"http://127.0.0.1:{seen['port']}/ns/proj.git")
        self.assertEqual(self.places(), before)
        with self.assertRaises(OSError):
            socket_connect(seen["port"])

    def test_next_after_a_halt_records_that_the_place_is_gone(self):
        before = self.places()
        with (
            mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")),
            mock.patch.object(runner, "finish", return_value=2),
        ):
            self.run_cmd(self.scenario("bare"))
        fail = runner.Refusal("失敗", "手で戻す")
        with (
            mock.patch.object(runner, "reset_repo", side_effect=fail),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(runner.main(["next", str(self.ws.out)]), 3)
        self.assertTrue(self.host_in_state()["down"])
        self.assertEqual(self.places(), before)

    def test_next_after_restore_does_not_run_the_steps(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}, {"prompt": "b"}]
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT) as step:
            self.assertEqual(self.run_cmd(self.scenario("bare", steps)), 10)
            with mock.patch("builtins.print"):
                self.assertEqual(runner.main(["restore", str(self.ws.out)]), 0)
                self.assertEqual(runner.main(["next", str(self.ws.out)]), 2)
        self.assertEqual(step.call_count, 1)

    def test_restore_removes_the_held_place(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "打つ"}]
        before = self.places()
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            self.assertEqual(self.run_cmd(self.scenario("bare", steps)), 10)
        self.assertEqual(len(self.places() - before), 1)
        with mock.patch("builtins.print"):
            self.assertEqual(runner.main(["restore", str(self.ws.out)]), 0)
        self.assertEqual(self.places(), before)

    def test_next_after_a_halt_that_never_reached_finish_removes_the_place(self):
        before = self.places()
        with (
            mock.patch.object(runner, "run_step", side_effect=RuntimeError("起動できない")),
            mock.patch.object(runner, "finish", return_value=2),
        ):
            self.assertEqual(self.run_cmd(self.scenario("bare")), 2)
        self.assertEqual(len(self.places() - before), 1)
        with mock.patch("builtins.print"):
            self.assertEqual(runner.main(["next", str(self.ws.out)]), 2)
        self.assertEqual(self.places(), before)

    def test_the_summary_says_who_the_other_side_is(self):
        scn = {"id": "U", "title": "t", "steps": [{"prompt": "a"}], "setup": {"host": "bare"}}
        text = runner.scenario_summary(scn)
        self.assertIn("相手（setup.host）: bare", text)
        self.assertIn("本物のトークンは claude に渡さない", text)
        scn["setup"] = {}
        self.assertNotIn("setup.host", runner.scenario_summary(scn))


# ---- setup.host: docker-gitlab（Docker 不要。GitLab の API は偽の Client で受ける） ----

ROOT_PAT = "glpat-root-agent-secret"
REVIEWER_TOKEN = "glpat-human-reviewer-secret"
BASE = "http://127.0.0.1:8929"


class FakeClient:
    """DELETE を遅延削除（1 回目は印を付けるだけ）で受ける。fail=True なら消えない。"""

    def __init__(self, fail=False, version=200):
        self.fail, self.version_status = fail, version
        self.calls = []
        self.human = []  # (呼び出し, token, 引数)。人間役の操作
        self.mrs = [{"iid": 3}]
        self.discussion_status = 201
        self.marked = False
        self.gone = False

    def version(self, token):
        return self.version_status, {"version": "19.4.1"}

    def delete_project(self, token, pid):
        return self.call("DELETE", f"/projects/{pid}", token)

    def call(self, method, path, token, payload=None, tag=""):
        self.calls.append((method, path))
        assert token == ROOT_PAT
        if method == "DELETE":
            if "permanently_remove=true" in path and not self.fail:
                self.gone = True
            elif not self.marked:
                self.marked = True
            return 202, {"message": "202 Accepted"}
        if self.gone:
            return 404, {}
        body = {"path_with_namespace": "root/sc-x-deletion_scheduled-7"}
        if self.marked:
            body["marked_for_deletion_on"] = "2026-10-26"
        return 200, body

    def deletes(self):
        return [c for c in self.calls if c[0] == "DELETE"]

    def list_mrs(self, token, pid, *, state="all"):
        self.human.append(("list_mrs", token, (pid,)))
        return 200, list(self.mrs)

    def approve(self, token, pid, iid):
        self.human.append(("approve", token, (pid, iid)))
        return 201, {}

    def create_discussion(self, token, pid, iid, body, tag=""):
        self.human.append(("create_discussion", token, (pid, iid, body)))
        return self.discussion_status, {"id": f"d{len(self.human)}"}

    def resolve_discussion(self, token, pid, iid, discussion_id, resolved=True):
        self.human.append(("resolve_discussion", token, (pid, iid, discussion_id)))
        return 200, {}


def fake_project(client):
    """_make_docker_project の代わり。識別子を記録し、通信先をローカルの bare にする。"""

    def make(root, host, top, scenario_id, git):
        host["version"] = "19.4.1"
        host.update(project_id=7, project="root/sc-x", base=BASE, url=f"{BASE}/root/sc-x")
        host["bare"] = os.path.join(host["dir"], "origin.git")
        hostenv._make_bare(top, host["bare"], git)

    return make


class DockerHostBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = FakeClient()
        values = {"GITLAB_TOKEN": ROOT_PAT, "CCNAVI_PROBE_REVIEWER_TOKEN": REVIEWER_TOKEN}
        for patcher in (
            mock.patch.object(hostenv, "_connect", lambda: (self.client, ROOT_PAT, BASE, values)),
            mock.patch.object(hostenv, "_sleep", lambda s: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class FieldAndNameTest(DockerHostBase):
    def load(self, setup):
        with tempfile.TemporaryDirectory() as d:
            return runner.load_scenario(write_scenario(d, setup))

    def test_host_is_valid_and_is_not_a_fake(self):
        scn = self.load({"host": "docker-gitlab"})
        self.assertEqual(runner.scenario_host(scn), "docker-gitlab")
        self.assertTrue(runner.uses_target(scn))
        self.assertFalse(hostenv.is_fake("docker-gitlab"))
        self.assertTrue(hostenv.uses_stub_git("docker-gitlab"))
        self.assertIn("docker-gitlab", runner.HOST_NOTES)
        self.assertNotIn("模擬のずれ", runner.host_note("docker-gitlab"))

    def test_it_is_not_combined_with_issue_or_host_state(self):
        issue = {"repo": "projects/test-repo", "title": "t"}
        with self.assertRaises(ValueError) as cm:
            self.load({"host": "docker-gitlab", "issue": issue})
        self.assertIn("setup.issue", str(cm.exception))
        with self.assertRaises(ValueError):
            self.load({"host": "docker-gitlab", "host_state": {}})

    def test_project_names_are_prefixed_folded_and_unique(self):
        a = hostenv.project_name("UC-08")
        b = hostenv.project_name("uc-08")
        self.assertTrue(a.startswith("sc-uc-08-"))
        self.assertTrue(b.startswith("sc-uc-08-"))
        self.assertNotEqual(a, hostenv.project_name("UC-08"))


@unittest.skipUnless(HAS_TOOLS, "sh・git・jq・curl のどれかが無い")
class DockerLifecycleTest(DockerHostBase):
    STEP_OUT = {"cost_usd": 0.0, "session_ids": []}

    def setUp(self):
        super().setUp()
        self.scratch = private_tempdir(self, self._tmp.name)
        self.ws = host_workspace(self._tmp.name)
        patcher = mock.patch.object(hostenv, "_make_docker_project", fake_project(self.client))
        patcher.start()
        self.addCleanup(patcher.stop)

    def scenario(self, steps=None):
        return self.ws.scenario_file(
            {"host": "docker-gitlab", "reset": {"base": self.ws.base}}, steps
        )

    def run_cmd(self, path, *extra):
        args = ["run", str(path), "--confirmed", "--root", str(self.ws.root)]
        args += ["--out", str(self.ws.out), *extra]
        with mock.patch.object(runner, "_ENTRY", True), mock.patch("builtins.print"):
            return runner.main(args)

    def state_text(self):
        return (self.ws.out / "state.json").read_text(encoding="utf-8")

    def test_finish_deletes_the_project_and_no_token_reaches_the_records(self):
        seen = {}

        def run_step(*a, **k):
            seen["env"] = k["env"]
            return self.STEP_OUT

        parent = {"CCNAVI_PROBE_REVIEWER_TOKEN": REVIEWER_TOKEN, "GITLAB_TOKEN": "parent-token"}
        with (
            mock.patch.dict(os.environ, parent),
            mock.patch.object(runner, "run_step", side_effect=run_step),
        ):
            self.run_cmd(self.scenario())
        env = seen["env"]
        self.assertEqual(env["GITLAB_TOKEN"], ROOT_PAT)
        self.assertNotIn(REVIEWER_TOKEN, json.dumps(env))
        self.assertNotIn("CCNAVI_PROBE_REVIEWER_TOKEN", env)
        self.assertEqual(env["FAKE_CRED_URL"], BASE)
        self.assertTrue(self.client.gone)
        self.assertEqual(len(self.client.deletes()), 2)  # 遅延削除なので 2 回目で確定する
        for path in self.ws.out.iterdir():
            if path.name.startswith("step-"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(ROOT_PAT, text, path.name)
            self.assertNotIn(REVIEWER_TOKEN, text, path.name)
        report = (self.ws.out / "report.md").read_text(encoding="utf-8")
        self.assertIn("docker-gitlab", report)
        self.assertIn("GitLab 19.4.1", report)

    def test_a_delete_failure_is_exit_3_and_names_the_project(self):
        self.client.fail = True
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            rc = self.run_cmd(self.scenario())
        self.assertEqual(rc, 3)
        rows = json.loads((self.ws.out / "report.json").read_text(encoding="utf-8"))
        row = next(r for r in rows if r["kind"] == "host-delete")
        self.assertIn(f"{BASE}/root/sc-x", row["expect"])
        self.assertEqual(row["verdict"], "fail")

    def test_interrupt_and_setup_failure_delete_the_project(self):
        with (
            mock.patch.object(runner, "run_step", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.run_cmd(self.scenario())
        self.assertTrue(self.client.gone)
        self.client.gone = self.client.marked = False
        with (
            mock.patch.object(runner, "_setup", side_effect=RuntimeError("壊れた")),
            self.assertRaises(RuntimeError),
        ):
            self.run_cmd(self.scenario())
        self.assertTrue(self.client.gone)

    def test_a_human_step_keeps_the_project_until_restore(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "i", "check": "c"}]
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            rc = self.run_cmd(self.scenario(steps))
        self.assertEqual(rc, 10)
        self.assertEqual(self.client.deletes(), [])
        self.assertNotIn(ROOT_PAT, self.state_text())
        with mock.patch("builtins.print"):
            self.assertEqual(runner.main(["restore", str(self.ws.out)]), 0)
        self.assertTrue(self.client.gone)

    def test_restore_reports_a_delete_failure(self):
        steps = [{"prompt": "a"}, {"human": True, "instruction": "i", "check": "c"}]
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            self.run_cmd(self.scenario(steps))
        self.client.fail = True
        with mock.patch("builtins.print"):
            self.assertEqual(runner.main(["restore", str(self.ws.out)]), 3)

    def test_a_stopped_docker_refuses_only_that_scenario(self):
        self.client.version_status = 0
        root, top = self.ws.root, self.ws.target
        docker = runner.load_scenario(self.scenario())
        bare = runner.load_scenario(
            self.ws.scenario_file({"host": "bare", "reset": {"base": self.ws.base}}, None)
        )
        with self.assertRaises(runner.Refusal) as cm:
            runner.check_before_run(docker, root, top)
        self.assertIn("docker_gitlab.py", str(cm.exception))
        self.assertIn("次:", str(cm.exception))
        runner.check_before_run(bare, root, top)


@unittest.skipUnless(HAS_TOOLS, "sh・git・jq・curl のどれかが無い")
class DockerGitTest(DockerHostBase):
    def test_git_via_resets_the_helper_before_setting_it(self):
        host = {
            "kind": "docker-gitlab",
            "bare": f"{BASE}/root/p.git",
            "real_origin": "o",
            "base": BASE,
        }
        via = hostenv.git_via(host)
        key = f"credential.{BASE}.helper"
        self.assertEqual(via.count(f"{key}="), 1)
        self.assertLess(via.index(f"{key}="), via.index(f"{key}={hostenv.CRED_HELPER}"))
        self.assertNotIn(ROOT_PAT, " ".join(via))
        env = hostenv.git_env(host)
        self.assertEqual(env, {"GITLAB_TOKEN": ROOT_PAT, "GIT_TERMINAL_PROMPT": "0"})
        self.assertEqual(hostenv.git_env({"kind": "bare"}), {})

    def test_the_stub_git_carries_the_scoped_helper(self):
        self.assertIn("credential.${FAKE_CRED_URL}.helper=", Path(host_stub.__file__).read_text())


class HumanApiTest(DockerLifecycleTest):
    """human step の human_api（人間役の API 操作）。DockerLifecycleTest の道具を使う。"""

    API = [
        {"op": "thread", "body": "直してください", "as": "t1"},
        {"op": "resolve", "thread": "t1"},
    ]

    def steps(self, api=None, extra=()):
        return [
            {"prompt": "a"},
            {"human": True, "human_api": self.API if api is None else api},
            *extra,
            {"prompt": "b"},
        ]

    def run_steps(self, steps):
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            return self.run_cmd(self.scenario(steps))

    def halt_kind(self):
        return json.loads(self.state_text()).get("halt", {}).get("kind")

    def out_text(self):
        texts = []
        for p in self.ws.out.iterdir():
            if not p.name.startswith("step-") and p.is_file():
                texts.append(p.read_text(encoding="utf-8"))
        for p in (self.ws.root / "logs" / "diag").glob("*"):
            texts.append(p.read_text(encoding="utf-8"))
        return "\n".join(texts)

    def test_field_is_checked(self):
        def load(setup, steps):
            with tempfile.TemporaryDirectory() as d:
                return runner.load_scenario(write_scenario(d, setup, steps))

        docker = {"host": "docker-gitlab"}
        load(docker, self.steps())
        bad = {
            "kind": [{"op": "comment", "body": "x"}],
            "empty": [],
            "no body": [{"op": "thread"}],
            "unresolved": [{"op": "resolve", "thread": "t9"}],
            "resolve before thread": [
                {"op": "resolve", "thread": "t1"},
                {"op": "thread", "body": "x", "as": "t1"},
            ],
        }
        for name, api in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                load(docker, self.steps(api))
        with self.assertRaises(ValueError) as cm:
            load({"host": "fake-gitlab"}, self.steps())
        self.assertIn("docker-gitlab", str(cm.exception))
        with self.assertRaises(ValueError):
            load(docker, [{"prompt": "a", "human_api": self.API}])

    def test_ops_run_in_order_with_the_human_token_and_do_not_stop(self):
        rc = self.run_steps(self.steps())
        self.assertIn(rc, (0, 2))
        names = [c[0] for c in self.client.human]
        self.assertEqual(names, ["list_mrs", "create_discussion", "list_mrs", "resolve_discussion"])
        self.assertEqual({c[1] for c in self.client.human}, {REVIEWER_TOKEN})
        self.assertEqual(self.client.human[1][2], (7, 3, "直してください"))
        self.assertEqual(self.client.human[3][2], (7, 3, "d2"))
        state = json.loads(self.state_text())
        self.assertEqual([o["op"] for o in state["human_ops"]], ["thread", "resolve"])
        self.assertEqual(state["threads"]["t1"]["iid"], 3)
        evidence = (self.ws.out / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("## 人間役の操作", evidence)
        self.assertIn("thread（t1） → HTTP 201", evidence)
        rows = json.loads((self.ws.out / "report.json").read_text(encoding="utf-8"))
        row = next(r for r in rows if r["kind"] == "human-api")
        self.assertEqual(row["verdict"], "pass")
        self.assertTrue(self.client.gone)

    def test_approve_uses_the_human_token_on_the_only_mr(self):
        self.run_steps(self.steps([{"op": "approve"}]))
        self.assertEqual(
            self.client.human,
            [("list_mrs", REVIEWER_TOKEN, (7,)), ("approve", REVIEWER_TOKEN, (7, 3))],
        )

    def test_the_human_token_is_not_in_the_records(self):
        with mock.patch.dict(os.environ, {"CCNAVI_PROBE_REVIEWER_TOKEN": REVIEWER_TOKEN}):
            self.run_steps(self.steps())
        text = self.out_text()
        self.assertNotIn(REVIEWER_TOKEN, text)
        self.assertNotIn(ROOT_PAT, text)
        self.assertNotIn("CCNAVI_PROBE_REVIEWER_TOKEN", text)

    def test_zero_or_two_mrs_halt_the_run(self):
        for mrs in ([], [{"iid": 1}, {"iid": 2}]):
            with self.subTest(count=len(mrs)):
                self.client.mrs = mrs
                self.client.gone = self.client.marked = False
                self.client.human.clear()
                self.run_steps(self.steps())
                self.assertEqual(self.halt_kind(), "human_api")
                self.assertEqual([c[0] for c in self.client.human], ["list_mrs"])
                self.assertTrue(self.client.gone)  # 後片付けは通る

    def test_an_http_failure_halts_the_run_and_is_not_retried(self):
        self.client.discussion_status = 500
        self.run_steps(self.steps())
        self.assertEqual(self.halt_kind(), "human_api")
        self.assertEqual([c[0] for c in self.client.human], ["list_mrs", "create_discussion"])
        rows = json.loads((self.ws.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(next(r for r in rows if r["kind"] == "human-api")["verdict"], "fail")

    def test_resume_does_not_run_the_ops_twice(self):
        human = {"human": True, "instruction": "手で操作する"}
        with mock.patch.object(runner, "run_step", return_value=self.STEP_OUT):
            rc = self.run_cmd(self.scenario(self.steps(extra=[human])))
        self.assertEqual(rc, 10)
        before = list(self.client.human)
        self.assertEqual(len(before), 4)
        with (
            mock.patch.object(runner, "run_step", return_value=self.STEP_OUT),
            mock.patch.object(runner, "_ENTRY", True),
            mock.patch("builtins.print"),
        ):
            runner.main(["next", str(self.ws.out)])
        self.assertEqual(self.client.human, before)
        self.assertTrue(self.client.gone)


# 親の試験は繰り返さない（道具だけ使う）
for _name in [n for n in dir(DockerLifecycleTest) if n.startswith("test_")]:
    setattr(HumanApiTest, _name, None)


if __name__ == "__main__":
    unittest.main()
