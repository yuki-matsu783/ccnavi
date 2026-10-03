"""同じ理由で同じ呼び出しを繰り返し止めたとき、文面とユーザへの報告で名指しすること
（issue #149 の 3）。

外から道具を動かし、読み返すのは応答の JSON だけ。判定そのもの（deny）は変わらず、
N 回目から拒否の文面の末尾に「言い換えずに相談する」一文が足されることを見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from ccnavi.records import audit, repeat
from tests import fixture_workspace
from tests.inproc import run_ccnavi

FIXTURE = "rules-undeclared.yml"
NOTE = "言い換えて打ち直さず"


def _env(**extra: str) -> dict[str, str]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    # コアファイルの控えと復元は切る。試したいのは拒否の数えだけ。
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.update(extra)
    return environment


def _run(state: str, payload: dict, mode: str = "enable", **env: str):
    ws = fixture_workspace(FIXTURE)
    return run_ccnavi(
        ["--root", ws, "--mode", mode, "--log", "", "--state", state],
        input=json.dumps({"session_id": "s1", "permission_mode": "auto", **payload}),
        env=_env(**env),
        cwd=ws,
    )


def _bash(state: str, command: str, mode: str = "enable", **env: str):
    return _run(
        state,
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}},
        mode,
        **env,
    )


def _verdict(result) -> tuple[str, str]:
    out = json.loads(result.stdout)["hookSpecificOutput"]
    return out.get("permissionDecision", ""), out.get(
        "permissionDecisionReason", out.get("additionalContext", "")
    )


class DenyMessageTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="ccnavi-repeat-")
        self.state = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def test_third_deny_asks_to_consult_without_changing_the_verdict(self):
        """3 回目から一文が付く。引用と空白だけを変えた打ち直しは同じ呼び出しとして数える。"""
        said = []
        for command in (
            "git push origin main",
            "git  push 'origin' main",
            'git push "origin" main',
        ):
            decision, reason = _verdict(_bash(self.state, command))
            self.assertEqual("deny", decision)
            said.append(NOTE in reason)
        self.assertEqual([False, False, True], said)
        _, reason = _verdict(_bash(self.state, "git push origin main"))
        self.assertIn("4 回止めました", reason)

    def test_threshold_follows_the_environment(self):
        """CCNAVI_DENY_REPEAT で N を動かせる。"""
        _bash(self.state, "git push origin main", CCNAVI_DENY_REPEAT="2")
        _, reason = _verdict(_bash(self.state, "git push origin main", CCNAVI_DENY_REPEAT="2"))
        self.assertIn(NOTE, reason)

    def test_other_calls_are_counted_apart(self):
        """別の対象は別に数える。同じルールでも、違う呼び出しの拒否を束ねない。"""
        for target in ("a", "b", "c"):
            _, reason = _verdict(_bash(self.state, f"git push origin {target}"))
            self.assertNotIn(NOTE, reason)

    def test_dry_run_does_not_count(self):
        """dry-run は止めていないので数えない。控えも作らない。"""
        for _ in range(3):
            result = _bash(self.state, "git push origin main", mode="dry-run")
            self.assertNotIn(NOTE, result.stdout)
        self.assertEqual([], [f for f in os.listdir(self.state) if f.startswith("denied-")])

    def test_unwritable_state_still_denies(self):
        """控えを書けなくても拒否は拒否のまま。一文が付かないだけ。"""
        blocked = os.path.join(self.state, "file")
        with open(blocked, "w", encoding="utf-8") as f:
            f.write("x")
        for _ in range(3):
            decision, reason = _verdict(_bash(blocked, "git push origin main"))
            self.assertEqual("deny", decision)
            self.assertNotIn(NOTE, reason)

    def test_turn_end_reports_once(self):
        """ターンの終わりにユーザへ 1 度言う。同じ回数のまま次のターンでは繰り返さない。"""
        for _ in range(3):
            _bash(self.state, "git push origin main")
        stop = {"hook_event_name": "Stop"}
        first = _run(self.state, stop)
        self.assertEqual(0, first.returncode, first.stderr)
        message = json.loads(first.stdout)["systemMessage"]
        self.assertIn("git-push（Bash）: 3 回", message)
        second = _run(self.state, stop)
        self.assertNotIn("git-push", second.stdout)


class NormalizeTest(unittest.TestCase):
    def test_quotes_and_spaces_fold(self):
        self.assertEqual(
            repeat.normalize("git push origin main"),
            repeat.normalize("  git\tpush \"origin\"\n 'main' "),
        )

    def test_case_is_kept(self):
        self.assertNotEqual(repeat.normalize("cat /A"), repeat.normalize("cat /a"))

    def test_threshold_falls_back(self):
        for written in ("", "x", "1", "0", "-3"):
            self.assertEqual(repeat.DEFAULT_THRESHOLD, repeat.threshold(written), written)
        self.assertEqual(5, repeat.threshold("5"))

    def test_no_state_counts_nothing(self):
        record = audit.Record(subject="git push", rules=["git-push"], session="s")
        self.assertEqual(0, repeat.count("", record))

    def test_no_session_counts_nothing(self):
        """セッションを見分けられない payload は数えない。別のセッションの拒否と混ぜない。"""
        with tempfile.TemporaryDirectory(prefix="ccnavi-repeat-") as state:
            record = audit.Record(subject="git push", rules=["git-push"])
            self.assertEqual([0, 0, 0], [repeat.count(state, record) for _ in range(3)])
            self.assertEqual([], os.listdir(state))


if __name__ == "__main__":
    unittest.main()
