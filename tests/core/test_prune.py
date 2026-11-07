"""記録と state の後始末（prune）の入口の受入テスト。

`ccnavi --prune` は `--preview` なら動かさずに並べ、そうでなければターミナルを求める。
セッションの開始でも走り、動かした数を記録に残す。
エージェントが Bash から打つ形は実行前チェックで止まる。
ローテート・保持日数・セッションごとの判断は `go/internal/records/prune` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from tests.inproc import run_ccnavi

DAY = 86400
NOW = time.time()
S_OLD = "11111111-aaaa-bbbb-cccc-000000000001"
S_ME = "33333333-aaaa-bbbb-cccc-000000000003"


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
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.logs = os.path.join(self.root, "logs")
        self.log = os.path.join(self.logs, "decisions.jsonl")
        self.state = os.path.join(self.logs, "state")
        os.makedirs(self.state)


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
        self.assertIn("削除する  logs/decisions.20260101-000000.jsonl", proc.stdout)
        self.assertTrue(os.path.exists(old))

    def test_prune_needs_a_terminal(self):
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        proc = run_ccnavi(["--entry", "sh", "user", "prune"], env=self.env(), cwd=self.root)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(
            "ccnavi-user-prune.sh はユーザがターミナルから実行する必要があります", proc.stderr
        )
        self.assertTrue(os.path.exists(old))

    def test_prune_removes_and_says_so(self):
        old = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=30)
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable")
        proc = run_ccnavi(["user", "prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("削除した  logs/decisions.20260101-000000.jsonl", proc.stdout)
        self.assertFalse(os.path.exists(old))

    def test_prune_with_nothing_to_do(self):
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable")
        proc = run_ccnavi(["user", "prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("動かすものはありません。", proc.stdout)

    def test_tiny_keep_days_from_the_command_line_fall_back(self):
        # 1 日より短い保持は受けない。いま書いたばかりの記録が消える形になる。
        fresh = _write(os.path.join(self.logs, "decisions.20260101-000000.jsonl"), age_days=0.5)
        env = self.env(CCNAVI_GUARD_TICKET_APPROVAL="disable", CCNAVI_LOG_KEEP_DAYS="0.0001")
        proc = run_ccnavi(["user", "prune"], env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("下限", proc.stderr)
        self.assertTrue(os.path.exists(fresh))

    def session_start(self, session: str = S_ME):
        payload = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}
        return run_ccnavi(
            [],
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
        # 読めなかったツリーの注記（この一時リポジトリは git のワークツリーではない）は残し、
        # 後始末の結果はそのあとに続く。
        self.assertTrue(line["detail"].endswith("pruned rotated=0 logs=1 state=1"))


class HookTest(_Base):
    """エージェントが Bash から `ccnavi --prune` を打つ形は、実行前チェックで止まること。

    実行ファイルのターミナル要求は、擬似ターミナル（`script -qc`）でも、チケット制御を切った
    ワークスペースでターミナル要求を切る変数を並べても抜けられる。チケット制御に依らず止める。
    """

    def pre_tool_use(self, command: str, tool: str = "Bash", **env: str):
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": tool,
            "tool_input": {"command": command},
        }
        return run_ccnavi(
            [],
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
