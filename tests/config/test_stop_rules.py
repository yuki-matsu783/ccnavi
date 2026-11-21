"""`match: Stop` のルールを、hook を通して確かめる数本。

数えの規則・止めた文・記録は go/internal/hook/events/stoprules_test.go、lint の苦情は
go/internal/policy/rulelint の見本、`--test Stop` と期待する判定は
go/internal/entry/diagnose が見る。
ここに残すのは、実行ファイルと hook を通した次の経路だけ。

1. `every: N` で N 回に 1 度、`decision: block` で止め、ルールの文を `reason` に載せる
2. `dry-run` でも同じ回に止める（止めないと文がモデルに届かない）。`disable` では止めない
3. 数えは compact・再開・clear では消えず、起動（startup）でだけ消える

`finish` の促しと重なったときの順は tests/ticket/test_stop_nudge.py。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

NUDGE = "ここまでの作業を振り返り、スキルの候補が要るかを見ること。"
CODE = "NUDGE_STOP_RULE"


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ruleset(*allow: dict, deny: tuple[dict, ...] = (), ask: tuple[dict, ...] = ()) -> str:
    body = {
        "version": 1,
        "deny": [
            {"id": "push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う"},
            *deny,
        ],
        "ask": list(ask),
        "allow": list(allow),
    }
    return json.dumps(body)


def stop_rule(name: str = "skill-review", **extra) -> dict:
    return {"id": name, "match": "Stop", "glob": "*", **extra}


class StopRulesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def env(self) -> dict:
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return environment

    def rules(self, text: str) -> str:
        return write(common_path(self.root, "rules"), text)

    def stop(
        self,
        *,
        cwd: str = "",
        session: str = "s1",
        active: bool = False,
        agent_id: str = "",
        mode: str = "enable",
        state: str | None = None,
        log: str = "",
        env: dict | None = None,
    ) -> dict:
        payload = {
            "hook_event_name": "Stop",
            "session_id": session,
            "cwd": cwd or self.root,
            "stop_hook_active": active,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        done = run_ccnavi(
            [
                "--root",
                self.root,
                *(["--mode", mode] if mode else []),
            ],
            input=json.dumps(payload),
            cwd=ROOT,
            env={**self.env(), **(env or {})},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout) if done.stdout.strip() else {}

    def blocked(self, body: dict) -> str:
        """止めた文。止めていなければ空。"""
        if body.get("decision") != "block":
            self.assertNotIn(CODE, json.dumps(body, ensure_ascii=False))
            return ""
        return body.get("reason", "")

    def test_every_counts_stops(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=3)))
        got = [self.blocked(self.stop()) for _ in range(6)]
        self.assertEqual([bool(g) for g in got], [False, False, True, False, False, True])
        self.assertTrue(got[2].startswith(f"{CODE}: "), got[2])
        self.assertIn(NUDGE, got[2])

    def test_dry_run_blocks_like_enable(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertNotIn("decision", self.stop(mode="dry-run"))
        body = self.stop(mode="dry-run")
        self.assertEqual(body.get("decision"), "block")
        self.assertIn(NUDGE, body["reason"])
        self.assertNotIn("would have blocked", body.get("systemMessage", ""))

    def test_mode_disable_never_blocks(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        # disable を言えるのは起動した環境だけ（フラグの disable は enable になる）。
        off = {"CCNAVI_MODE": "disable"}
        self.assertEqual([self.blocked(self.stop(mode="", env=off)) for _ in range(4)], [""] * 4)

    def session_start(self, source: str) -> None:
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": self.root,
            "source": source,
        }
        done = run_ccnavi(
            ["--root", self.root],
            input=json.dumps(payload),
            cwd=ROOT,
            env=self.env(),
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_count_survives_compact_resume_and_clear(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=3)))
        got = []
        for source in ("compact", "resume", "clear"):
            got.append(self.blocked(self.stop()))
            self.session_start(source)
        self.assertEqual([bool(g) for g in got], [False, False, True])

    def test_the_count_is_dropped_on_startup(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=3)))
        self.stop()
        self.stop()
        self.session_start("startup")
        self.assertEqual([bool(self.blocked(self.stop())) for _ in range(3)], [False, False, True])


if __name__ == "__main__":
    unittest.main()
