"""記録と state の後始末（prune）の受入テスト。

見るのは 4 つ。

1. 記録は上限を超えたときだけ `decisions.<日時>.jsonl` へ名前を変え、中身を捨てない
2. ローテートした記録は保持日数を過ぎたものだけを消し、いま書いている記録は消さない
3. 記録はセッションごとにまとめて判断する。どれか 1 つでも新しければ全部を残し、
   いま始まったセッションと、セッションを名前に持たないものは消さない
4. 入口の 2 つ。`ccnavi --prune` は `--preview` なら動かさずに並べ、そうでなければ端末を
   求める。セッションの開始でも走り、動かした数を記録に残し、失敗しても開始を止めない
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from ccnavi.policy import selfguard
from ccnavi.records import prune
from tests.inproc import run_ccnavi

DAY = 86400
NOW = time.time()
S_OLD = "11111111-aaaa-bbbb-cccc-000000000001"
S_LIVE = "22222222-aaaa-bbbb-cccc-000000000002"
S_ME = "33333333-aaaa-bbbb-cccc-000000000003"
# ローテートの下限（1 MB）を超える記録。
BIG = "a" * (1024 * 1024 + 100)


def _write(path: str, text: str = "x", age_days: float = 0.0) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    _age(path, age_days)
    return path


def _age(path: str, age_days: float) -> None:
    t = NOW - age_days * DAY
    os.utime(path, (t, t))


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-prune-")
        self.logs = os.path.join(self.root, "logs")
        self.log = os.path.join(self.logs, "decisions.jsonl")
        self.state = os.path.join(self.logs, "state")
        os.makedirs(self.state)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name, _ in prune.LIMITS:
            os.environ.pop(name, None)

    def run_prune(self, session: str = "", dry_run: bool = False) -> prune.Report:
        return prune.run(self.root, self.log, self.state, session, dry_run=dry_run, now=NOW)

    def exists(self, *parts: str) -> bool:
        return os.path.exists(os.path.join(self.root, *parts))


class RotateTest(_Base):
    def test_small_log_is_left_alone(self):
        _write(self.log, "{}\n")
        report = self.run_prune()
        self.assertEqual(report.rotated, [])
        self.assertTrue(os.path.exists(self.log))

    def test_log_over_the_limit_is_renamed_with_its_content(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"  # 下限
        body = '{"decision":"allow"}\n' * 60000
        _write(self.log, body)
        report = self.run_prune()
        self.assertEqual(len(report.rotated), 1)
        src, dst = report.rotated[0]
        self.assertEqual(src, "logs/decisions.jsonl")
        self.assertRegex(dst, r"^logs/decisions\.\d{8}-\d{6}\.jsonl$")
        self.assertFalse(os.path.exists(self.log))
        with open(os.path.join(self.root, dst), encoding="utf-8") as f:
            self.assertEqual(f.read(), body)

    def test_same_second_does_not_overwrite(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        first = self.run_prune  # 同じ now で 2 回
        _write(self.log, BIG)
        first()
        _write(self.log, "b" * len(BIG))
        report = first()
        names = sorted(n for n in os.listdir(self.logs) if n.startswith("decisions."))
        self.assertEqual(len(names), 2, names)
        self.assertRegex(report.rotated[0][1], r"-2\.jsonl$")

    def test_same_second_race_does_not_overwrite(self):
        # 同じ秒に始まった 2 つのセッションが、どちらも行き先がまだ無いと見てから名前を変える形。
        # 行き先を先に押さえないと、後の 1 本が先にローテートした記録を上書きする。
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        _write(self.log, BIG)
        self.run_prune()
        _write(self.log, "b" * len(BIG))
        with mock.patch.object(prune.os.path, "exists", return_value=False):
            report = self.run_prune()
        names = sorted(n for n in os.listdir(self.logs) if n.startswith("decisions."))
        self.assertEqual(len(names), 2, names)
        self.assertRegex(report.rotated[0][1], r"-2\.jsonl$")
        (first,) = [n for n in names if not n.endswith("-2.jsonl")]
        with open(os.path.join(self.logs, first), encoding="utf-8") as f:
            self.assertEqual(f.read(1), "a")

    def test_default_limit_is_10mb(self):
        self.assertEqual(prune.limits()[:3], (10.0, 14.0, 14.0))

    def test_zero_turns_the_step_off_and_bad_values_fall_back(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "0"
        _write(self.log, BIG)
        self.assertEqual(self.run_prune().rotated, [])
        os.environ[prune.LOG_KEEP_DAYS_ENV] = "many"
        _, days, _, problems = prune.limits()
        self.assertEqual(days, 14.0)
        self.assertIn(prune.LOG_KEEP_DAYS_ENV, problems[0])

    def test_tiny_and_non_finite_values_fall_back(self):
        # 0 に近づけるだけで保持日数のうちの記録を消せないよう、0 のほかは下限を置く。
        # 有限でない値は、ローテートの大きさを整数にするところで失敗する。
        for env, default in prune.LIMITS:
            for raw in ("0.001", "0.5", "inf", "-inf", "nan", "1e400"):
                with self.subTest(env=env, raw=raw):
                    os.environ[env] = raw
                    values = prune.limits()
                    self.assertEqual(values[[e for e, _ in prune.LIMITS].index(env)], default)
                    self.assertTrue(any(env in p for p in values[3]), values[3])
            os.environ.pop(env)

    def test_zero_and_floor_are_taken(self):
        for env, _ in prune.LIMITS:
            for raw in ("0", "1", "2.5"):
                with self.subTest(env=env, raw=raw):
                    os.environ[env] = raw
                    values = prune.limits()
                    self.assertEqual(values[[e for e, _ in prune.LIMITS].index(env)], float(raw))
                    self.assertEqual(values[3], [])
            os.environ.pop(env)

    def test_infinite_rotate_does_not_crash(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "inf"
        _write(self.log, "{}\n")
        report = self.run_prune()
        self.assertEqual(report.rotated, [])
        self.assertIn(prune.LOG_ROTATE_MB_ENV, report.problems[0])

    def test_dry_run_moves_nothing(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        _write(self.log, BIG)
        report = self.run_prune(dry_run=True)
        self.assertEqual(len(report.rotated), 1)
        self.assertTrue(os.path.exists(self.log))


class LogPruneTest(_Base):
    def test_only_old_rotated_logs_are_removed(self):
        _write(self.log, "{}\n", age_days=100)
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=15)
        old2 = _write(os.path.join(self.logs, "decisions.20260101-000000-2.jsonl"), age_days=15)
        fresh = _write(os.path.join(self.logs, "decisions.20260920-000000.jsonl"), age_days=13)
        other = _write(os.path.join(self.logs, "git-20260101-000000-1.log"), age_days=100)
        stray = _write(os.path.join(self.logs, "decisions.backup.jsonl"), age_days=100)
        report = self.run_prune()
        self.assertEqual(
            report.logs,
            ["logs/decisions.20260101-000000-2.jsonl", "logs/decisions.20260101-000000.jsonl"],
        )
        for path in (old, old2):
            self.assertFalse(os.path.exists(path))
        for path in (self.log, fresh, other, stray):
            self.assertTrue(os.path.exists(path), path)

    def test_keep_days_can_be_moved(self):
        path = _write(os.path.join(self.logs, "decisions.20260920-000000.jsonl"), age_days=3)
        os.environ[prune.LOG_KEEP_DAYS_ENV] = "2"
        self.run_prune()
        self.assertFalse(os.path.exists(path))

    def test_empty_log_setting_touches_nothing(self):
        path = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        prune.run(self.root, "", self.state, now=NOW)
        self.assertTrue(os.path.exists(path))


class StatePruneTest(_Base):
    def session_files(self, s: str, age: float) -> list[str]:
        """1 つのセッションが置く記録を全部置く。"""
        paths = [
            _write(os.path.join(self.state, f"{s}.json"), age_days=age),
            _write(os.path.join(self.state, f"{s}.turn.json"), age_days=age),
            _write(os.path.join(self.state, f"nudged-{s}.json"), age_days=age),
            _write(os.path.join(self.state, f"denied-{s}.json"), age_days=age),
            _write(os.path.join(self.state, f"once-{s}-main.json"), age_days=age),
            _write(os.path.join(self.state, f"approved-{s}-agent-1.json"), age_days=age),
            _write(os.path.join(self.state, f"subagent-{s}-guard-records.bounced"), age_days=age),
        ]
        guard = os.path.join(self.state, "selfguard", s)
        _write(os.path.join(guard, "rules_common"), age_days=age)
        _age(guard, age)
        return [*paths, guard]

    def test_finished_session_is_removed_whole(self):
        doomed = self.session_files(S_OLD, 20)
        report = self.run_prune()
        for path in doomed:
            self.assertFalse(os.path.exists(path), path)
        self.assertEqual(len(report.state), len(doomed))
        self.assertIn(f"logs/state/selfguard/{S_OLD}", report.state)

    def test_one_fresh_file_keeps_the_whole_session(self):
        kept = self.session_files(S_LIVE, 20)
        # 動いているセッションは、ターンの基準をプロンプトのたびに書き直す。
        _age(os.path.join(self.state, f"{S_LIVE}.turn.json"), 0.1)
        self.run_prune()
        for path in kept:
            self.assertTrue(os.path.exists(path), path)

    def test_selfguard_backup_counts_as_a_sign_of_life(self):
        kept = self.session_files(S_LIVE, 20)
        _write(os.path.join(self.state, "selfguard", S_LIVE, "settings"), age_days=0.1)
        self.run_prune()
        for path in kept:
            self.assertTrue(os.path.exists(path), path)

    def test_starting_session_is_never_removed(self):
        kept = self.session_files(S_ME, 20)
        self.run_prune(session=S_ME)
        for path in kept:
            self.assertTrue(os.path.exists(path), path)

    def test_leftover_temporary_file_goes_with_its_session(self):
        """失敗して残った `.once-<セッション>-….part.json` も、そのセッションと一緒に消える。"""
        doomed = self.session_files(S_OLD, 20)
        leftover = _write(
            os.path.join(self.state, f".once-{S_OLD}-main.abc12345.part.json"), age_days=20
        )
        kept = self.session_files(S_LIVE, 20)
        _age(os.path.join(self.state, f"{S_LIVE}.turn.json"), 0.1)
        live_leftover = _write(
            os.path.join(self.state, f".once-{S_LIVE}-main.abc12345.part.json"), age_days=20
        )
        self.run_prune()
        for path in [*doomed, leftover]:
            self.assertFalse(os.path.exists(path), path)
        for path in [*kept, live_leftover]:
            self.assertTrue(os.path.exists(path), path)

    def test_leftover_temporary_files_of_other_shapes(self):
        """`.nudged-…` などはそれ自身の日付で消え、seen と turn の一時ファイルは消さずに残る。"""
        self.session_files(S_OLD, 20)
        own_date = _write(
            os.path.join(self.state, f".nudged-{S_OLD}.abc12345.part.json"), age_days=20
        )
        fresh = _write(os.path.join(self.state, f".denied-{S_OLD}.zz998877.part.json"))
        kept = [
            _write(os.path.join(self.state, f".{S_OLD}.abc12345.part.json"), age_days=20),
            _write(os.path.join(self.state, f".{S_OLD}.turn.abc12345.part.json"), age_days=20),
        ]
        self.run_prune()
        self.assertFalse(os.path.exists(own_date))
        for path in [fresh, *kept]:
            self.assertTrue(os.path.exists(path), path)

    def test_files_without_a_session_are_left_alone(self):
        keep = [
            _write(os.path.join(self.state, "review-request-i0001-1.md"), age_days=90),
            _write(os.path.join(self.state, "risk-judge-i0001-01-01.md"), age_days=90),
            _write(os.path.join(self.state, "aside", "20260101-000000", "new.txt"), age_days=90),
            _write(os.path.join(self.state, "selfguard", "store", "abc"), age_days=90),
        ]
        self.run_prune()
        for path in keep:
            self.assertTrue(os.path.exists(path), path)

    def test_denied_file_of_a_live_session_is_kept(self):
        # 拒否の数え（repeat）の記録は、1 度書いたきりで長く続くセッションがある。
        # 名前をセッションと読めないと、それ自身の日付で消える。
        kept = self.session_files(S_LIVE, 20)
        _age(os.path.join(self.state, f"{S_LIVE}.turn.json"), 0.1)
        self.run_prune()
        self.assertTrue(os.path.exists(os.path.join(self.state, f"denied-{S_LIVE}.json")))
        for path in kept:
            self.assertTrue(os.path.exists(path), path)

    def test_unknown_json_is_left_alone(self):
        # `<セッション>.json` と読むのは UUID の形だけ。
        keep = [
            _write(os.path.join(self.state, name), age_days=90)
            for name in (
                "package.json",
                "tsconfig.json",
                "review-result-123.json",
                "risk-judge-i0001.json",
                "notes.json",
            )
        ]
        report = self.run_prune()
        self.assertEqual(report.state, [])
        for path in keep:
            self.assertTrue(os.path.exists(path), path)

    def link(self, target: str, name: str) -> None:
        try:
            os.symlink(target, name, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"リンクを作れない: {exc}")

    def test_linked_state_dir_is_not_followed(self):
        elsewhere = tempfile.mkdtemp(prefix="ccnavi-prune-elsewhere-")
        victim = _write(os.path.join(elsewhere, f"{S_OLD}.turn.json"), age_days=90)
        linked = os.path.join(self.logs, "state-link")
        self.link(elsewhere, linked)
        report = prune.run(self.root, self.log, linked, now=NOW)
        self.assertTrue(os.path.exists(victim))
        self.assertEqual(report.state, [])
        self.assertTrue(any("リンク" in p for p in report.problems), report.problems)

    def test_linked_selfguard_is_not_followed(self):
        elsewhere = tempfile.mkdtemp(prefix="ccnavi-prune-elsewhere-")
        victim = _write(os.path.join(elsewhere, S_OLD, "settings"), age_days=90)
        _age(os.path.dirname(victim), 90)
        self.link(elsewhere, os.path.join(self.state, "selfguard"))
        report = self.run_prune()
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(any("リンク" in p for p in report.problems), report.problems)

    def test_loose_keyed_file_goes_by_its_own_date(self):
        # セッションの表記が他に無く、切れ目が決まらない。それ自身の日付で見る。
        old = _write(os.path.join(self.state, f"subagent-{S_OLD}-x.bounced"), age_days=20)
        new = _write(os.path.join(self.state, f"subagent-{S_LIVE}-x.bounced"), age_days=1)
        self.run_prune()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_dry_run_lists_but_keeps(self):
        doomed = self.session_files(S_OLD, 20)
        report = self.run_prune(dry_run=True)
        self.assertEqual(len(report.state), len(doomed))
        for path in doomed:
            self.assertTrue(os.path.exists(path), path)

    def test_state_keep_days_can_be_moved(self):
        doomed = self.session_files(S_OLD, 3)
        self.run_prune()
        self.assertTrue(all(os.path.exists(p) for p in doomed))
        os.environ[prune.STATE_KEEP_DAYS_ENV] = "2"
        self.run_prune()
        self.assertFalse(any(os.path.exists(p) for p in doomed))

    def test_selfguard_names_match(self):
        self.assertEqual(prune.SELFGUARD_DIR, selfguard.BACKUP_DIR)
        self.assertEqual(prune.SELFGUARD_STORE, selfguard.STORE_DIR)


class EntryTest(_Base):
    def env(self, **extra: str) -> dict[str, str]:
        return {
            "CLAUDE_PROJECT_DIR": self.root,
            "CCNAVI_MODE": "enable",
            "CCNAVI_TICKET_CONTROL": "disable",
            **extra,
        }

    def test_prune_preview_lists_without_a_terminal(self):
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        proc = run_ccnavi(["--prune", "--preview"], env=self.env(), cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("消す  logs/decisions.20260101-000000.jsonl", proc.stdout)
        self.assertTrue(os.path.exists(old))

    def test_prune_needs_a_terminal(self):
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        proc = run_ccnavi(["--prune"], env=self.env(), cwd=self.root)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--prune は端末から打つもの", proc.stderr)
        self.assertTrue(os.path.exists(old))

    def test_prune_removes_and_says_so(self):
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable")
        proc = run_ccnavi(["--prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("消した  logs/decisions.20260101-000000.jsonl", proc.stdout)
        self.assertFalse(os.path.exists(old))

    def test_prune_with_nothing_to_do(self):
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable")
        proc = run_ccnavi(["--prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("動かすものは無い", proc.stdout)

    def test_tiny_keep_days_from_the_command_line_fall_back(self):
        # 1 日より短い保持は受けない。いま書いたばかりの記録が消える形になる。
        fresh = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=0.5)
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable", CCNAVI_LOG_KEEP_DAYS="0.0001")
        proc = run_ccnavi(["--prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("下限", proc.stderr)
        self.assertTrue(os.path.exists(fresh))

    def session_start(self, session: str = S_ME):
        payload = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}
        return run_ccnavi(
            ["--log", self.log, "--state", self.state],
            input=json.dumps(payload),
            env=self.env(),
            cwd=self.root,
        )

    def test_session_start_prunes_and_records_what_moved(self):
        old_log = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        old_state = _write(os.path.join(self.state, f"{S_OLD}.turn.json"), age_days=30)
        mine = _write(os.path.join(self.state, f"{S_ME}.turn.json"), age_days=30)
        proc = self.session_start()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(old_log))
        self.assertFalse(os.path.exists(old_state))
        self.assertTrue(os.path.exists(mine))
        with open(self.log, encoding="utf-8") as f:
            (line,) = [json.loads(x) for x in f]
        self.assertEqual(line["event"], "SessionStart")
        self.assertEqual(line["detail"], "pruned rotated=0 logs=1 state=1")

    def test_session_start_survives_a_failing_prune(self):
        with mock.patch.object(prune, "run", side_effect=RuntimeError("boom")):
            proc = self.session_start()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("後始末に失敗した: boom", proc.stderr)
        with open(self.log, encoding="utf-8") as f:
            (line,) = [json.loads(x) for x in f]
        self.assertEqual(line["decision"], "allow")
        self.assertNotIn("detail", line)


class HookTest(_Base):
    """エージェントが Bash から `ccnavi --prune` を打つ形は、実行前チェックで止まること。

    実行ファイルの端末要求は、擬似端末（`script -qc`）でも、チケット制御を切った
    ワークスペースで端末要求を切る変数を並べても抜けられる。チケット制御に依らず止める。
    """

    def pre_tool_use(self, command: str, tool: str = "Bash", **env: str):
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": tool,
            "tool_input": {"command": command},
        }
        return run_ccnavi(
            ["--log", self.log, "--state", self.state],
            input=json.dumps(payload),
            env={
                "CLAUDE_PROJECT_DIR": self.root,
                "CCNAVI_MODE": "enable",
                "CCNAVI_TICKET_CONTROL": "disable",
                **env,
            },
            cwd=self.root,
        )

    def test_prune_is_denied_however_it_is_called(self):
        for command in (
            "ccnavi --prune",
            "script -qc 'ccnavi --prune' /dev/null",
            "CCNAVI_GUARD_TICKET_APPROVAL=disable CCNAVI_LOG_KEEP_DAYS=0 ccnavi --prune",
            "uv run python -m ccnavi --prune",
            ".ccnavi/bin/linux-x64/ccnavi --json --prune",
            'bash -c "ccnavi --prune" x --preview',
            'bash -c "ccnavi --prune" --preview',
            "ccnavi --prune --preview; ccnavi --prune",
            'ccnavi --pr""une',
            "ccnavi --prune --reason=--preview",
            "ccnavi --prune | tail -3",
        ):
            with self.subTest(command=command):
                proc = self.pre_tool_use(command)
                self.assertIn("DENY_RECORDS_PRUNE", proc.stdout)
                self.assertIn('"deny"', proc.stdout)

    def test_powershell_form_is_denied(self):
        proc = self.pre_tool_use("& C:\\tools\\ccnavi.exe --prune", tool="PowerShell")
        self.assertIn("DENY_RECORDS_PRUNE", proc.stdout)

    def test_preview_and_other_prunes_pass(self):
        for command in (
            "ccnavi --prune --preview",
            "ccnavi --preview --prune",
            'bash -c "ccnavi --prune --preview"',
            "git fetch --prune",
            "sh .ccnavi/scripts/ccnavi-git.sh fetch origin --prune",
            "echo ccnavi --prune",
            "git remote prune origin",
        ):
            with self.subTest(command=command):
                proc = self.pre_tool_use(command)
                self.assertNotIn("DENY_RECORDS_PRUNE", proc.stdout)

    def test_guard_core_files_disable_turns_it_off(self):
        proc = self.pre_tool_use("ccnavi --prune", CCNAVI_GUARD_CORE_FILES="disable")
        self.assertNotIn("DENY_RECORDS_PRUNE", proc.stdout)


if __name__ == "__main__":
    unittest.main()
