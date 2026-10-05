"""`match: Stop` のルール（ターンの終わりに N 回に 1 度止めて文を渡す）の受入テスト。

メインエージェントの Stop は、これまで `finish` の打ち忘れのほかは止めなかった。
`match: Stop` の `allow` のルールを書いたときだけ、渡す回（`every`）に `decision: block` で止め、
ルールの文を `reason` に載せる。Stop でモデルに文を届ける経路は、止めて `reason` に
書くことしか無いため。見るのは次のとおり。

1. 書いていなければ今までどおり止めない
2. `every: N` で N 回に 1 度止める。本文のファイルも載る。数えはセッションごと
3. `stop_hook_active` が真の回は止めず、数えも進めない。サブエージェント（`agent_id`）も同じ
4. 数えを覚えられない（`--state ""`）ときは止めない
5. `dry-run` は止めず、止めたはずの文を `systemMessage` に載せる
6. cwd がプロジェクトの中でも同じルールが当たる。プロジェクトのレイヤーのルールも当たる
7. `--lint` は `deny` / `ask` に置いたもの、`(stop)` に当たらない表記、`every` の無いものを
   warn で言う

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
        self.state = os.path.join(self.root, "state")
        self.addCleanup(self.dir.cleanup)

    def env(self) -> dict:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
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
                "--log",
                log,
                "--state",
                self.state if state is None else state,
                "--approved",
                "",
                "--mode",
                mode,
            ],
            input=json.dumps(payload),
            cwd=ROOT,
            env=self.env(),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout) if done.stdout.strip() else {}

    def blocked(self, body: dict) -> str:
        """止めた文。止めていなければ空。"""
        if body.get("decision") != "block":
            self.assertNotIn(CODE, json.dumps(body, ensure_ascii=False))
            return ""
        return body.get("reason", "")

    def lint(self) -> str:
        done = run_ccnavi(
            ["--root", self.root, "--log", "", "--state", "", "--approved", "", "--lint"],
            cwd=ROOT,
            env=self.env(),
        )
        return done.stdout

    # --- 1. 書かなければ止めない ------------------------------------------------

    def test_no_block_without_a_stop_rule(self):
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        self.assertEqual([self.blocked(self.stop()) for _ in range(3)], ["", "", ""])

    # --- 2. every で N 回に 1 度 -------------------------------------------------

    def test_every_counts_stops(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=3)))
        got = [self.blocked(self.stop()) for _ in range(6)]
        self.assertEqual([bool(g) for g in got], [False, False, True, False, False, True])
        self.assertTrue(got[2].startswith(f"{CODE}: "), got[2])
        self.assertIn(NUDGE, got[2])

    def test_file_body_is_carried(self):
        write(os.path.join(self.root, "docs", "review.md"), "振り返りの手順")
        self.rules(ruleset(stop_rule(additionalContextFile="docs/review.md", every=2)))
        self.assertEqual(self.blocked(self.stop()), "")
        self.assertIn("振り返りの手順", self.blocked(self.stop()))

    def test_the_count_is_per_session(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual(self.blocked(self.stop(session="a")), "")
        self.assertEqual(self.blocked(self.stop(session="b")), "")
        self.assertIn(NUDGE, self.blocked(self.stop(session="a")))

    # --- 3. 連鎖の 2 回目とサブエージェント -----------------------------------------

    def test_stop_hook_active_neither_blocks_nor_counts(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual(self.blocked(self.stop()), "")
        self.assertEqual(self.blocked(self.stop(active=True)), "")
        self.assertEqual(self.blocked(self.stop(active=True)), "")
        self.assertIn(NUDGE, self.blocked(self.stop()))

    def test_subagents_are_not_blocked(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual([self.blocked(self.stop(agent_id="a1")) for _ in range(4)], [""] * 4)

    # --- 4. 覚えられないなら止めない ------------------------------------------------

    def test_no_state_means_no_block(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual([self.blocked(self.stop(state="")) for _ in range(4)], [""] * 4)

    def test_unreadable_count_means_no_block(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        write(os.path.join(self.state, "stop-s1.json"), "{壊れた")
        self.assertEqual([self.blocked(self.stop()) for _ in range(4)], [""] * 4)

    def test_every_below_two_is_ignored_at_runtime(self):
        """`every` が無い（1）ルールは、lint だけでなく実行時にも使わない。毎回止めないため。"""
        self.rules(
            ruleset(
                stop_rule(additionalContext=NUDGE), stop_rule("once", additionalContextOnce=NUDGE)
            )
        )
        self.assertEqual([self.blocked(self.stop()) for _ in range(3)], [""] * 3)

    # --- 5. dry-run --------------------------------------------------------------

    def test_dry_run_reports_instead_of_blocking(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertNotIn("decision", self.stop(mode="dry-run"))
        body = self.stop(mode="dry-run")
        self.assertNotIn("decision", body)
        self.assertIn("would have blocked this stop", body.get("systemMessage", ""))
        self.assertIn(NUDGE, body.get("systemMessage", ""))

    # --- 5b. 止めた回の頭の文と記録 ------------------------------------------------

    def test_the_fixed_preface_comes_before_the_rule_text(self):
        """ルールの文やファイルが何であっても、止めた文は決まった前置きで始まる。"""
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.stop()
        reason = self.blocked(self.stop())
        self.assertTrue(reason.startswith(f"{CODE}: これはタスクの続きではない。"), reason)
        self.assertIn("問いを最後にもう一度書いて", reason)
        self.assertIn("「振り返り: 無し」", reason)
        self.assertLess(reason.index("振り返り: 無し"), reason.index(NUDGE))

    def test_the_record_names_only_delivered_rules(self):
        self.rules(
            ruleset(
                stop_rule("two", additionalContext="二", every=2),
                stop_rule("three", additionalContext="三", every=3),
            )
        )
        log = os.path.join(self.root, "decisions.jsonl")
        for _ in range(2):
            self.stop(log=log)
        with open(log, encoding="utf-8") as f:
            last = json.loads(f.read().splitlines()[-1])
        self.assertEqual(last.get("rules"), ["two"])
        self.assertEqual(last.get("code"), CODE)

    # --- 5c. compact・再開では数えを捨てない -------------------------------------------

    def session_start(self, source: str) -> None:
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": self.root,
            "source": source,
        }
        done = run_ccnavi(
            ["--root", self.root, "--log", "", "--state", self.state, "--approved", ""],
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

    # --- 6. レイヤーとプロジェクト ------------------------------------------------------------

    def project(self, name: str = "lib") -> str:
        home = os.path.join(self.root, "projects", name)
        os.makedirs(os.path.join(home, ".git"), exist_ok=True)
        return home

    def test_same_rule_from_inside_a_project(self):
        home = self.project()
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual(self.blocked(self.stop(cwd=home)), "")
        self.assertIn(NUDGE, self.blocked(self.stop(cwd=self.root)))

    def test_project_layer_rules_do_not_stop_the_main_session(self):
        """プロジェクトのレイヤー（外のリポジトリ）の `match: Stop` は見ない。
        cwd がどこでも同じ。"""
        home = self.project()
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        layer = {
            "version": 1,
            "allow": [stop_rule("lib-review", additionalContext="外の文", every=2)],
        }
        write(os.path.join(home, ".ccnavi", "config", "rules.yml"), json.dumps(layer))
        for cwd in (self.root, home, self.root, home):
            self.assertEqual(self.blocked(self.stop(cwd=cwd)), "")

    def test_a_stale_project_copy_of_the_common_rule_is_not_counted_twice(self):
        """プロジェクトのレイヤーへ写した共通レイヤーが古くなって `every` が違っても、
        数えるのは共通レイヤーの 1 本。

        共通レイヤーは親の着手でプロジェクトのレイヤーへコピーするので、コピーしたレイヤーが古いまま残ることがある。
        """
        home = self.project()
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        stale = {"version": 1, "allow": [stop_rule(additionalContext=NUDGE, every=3)]}
        write(os.path.join(home, ".ccnavi", "config", "rules.yml"), json.dumps(stale))
        got = [bool(self.blocked(self.stop(cwd=home))) for _ in range(6)]
        self.assertEqual(got, [False, True, False, True, False, True])

    def test_the_workspace_own_layer_applies_and_the_same_id_counts_once(self):
        own = {"version": 1, "allow": [stop_rule(additionalContext="自身のレイヤー", every=2)]}
        write(os.path.join(self.root, ".ccnavi", "config", "rules.yml"), json.dumps(own))
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=2)))
        self.stop()
        reason = self.blocked(self.stop())
        self.assertIn(NUDGE, reason)
        self.assertNotIn("自身のレイヤー", reason)

    def test_the_file_is_read_from_the_workspace_root_only(self):
        """本文のファイルはワークスペースルートの版だけ。プロジェクトやワークツリーの版は読まない。"""
        home = self.project()
        write(os.path.join(self.root, "docs", "review.md"), "ワークスペースの手順")
        write(os.path.join(home, "docs", "review.md"), "プロジェクトの手順")
        self.rules(ruleset(stop_rule(additionalContextFile="docs/review.md", every=2)))
        self.stop(cwd=home)
        reason = self.blocked(self.stop(cwd=home))
        self.assertIn("ワークスペースの手順", reason)
        self.assertNotIn("プロジェクトの手順", reason)

    # --- 7. lint -----------------------------------------------------------------

    def said(self, out: str, name: str) -> list[str]:
        return [line for line in out.splitlines() if f": {name}: " in line]

    def test_deny_and_ask_do_not_block_and_lint_warns(self):
        self.rules(
            ruleset(
                deny=(stop_rule("stop-deny", message="止める"),),
                ask=(stop_rule("stop-ask", additionalContext=NUDGE),),
            )
        )
        self.assertEqual(self.blocked(self.stop()), "")
        out = self.lint()
        for name in ("stop-deny", "stop-ask"):
            self.assertTrue(
                any(
                    s.startswith("warn:") and "allow の別のルールに分けて置いて" in s
                    for s in self.said(out, name)
                ),
                f"{name} に warn が無い:\n{out}",
            )

    def test_lint_warns_on_a_pattern_that_never_matches_and_on_missing_every(self):
        self.rules(
            ruleset(
                stop_rule("never", glob="*review*", additionalContext=NUDGE, every=5),
                stop_rule("each-time", additionalContext=NUDGE),
            )
        )
        out = self.lint()
        self.assertTrue(any('glob: "*"' in s for s in self.said(out, "never")), out)
        self.assertTrue(any("every: 10" in s for s in self.said(out, "each-time")), out)

    def test_lint_is_quiet_on_a_well_formed_rule(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=10)))
        self.assertEqual(self.said(self.lint(), "skill-review"), [])

    def test_lint_on_deny_with_bash_and_stop_names_only_the_stop_part(self):
        both = {"id": "both", "match": "Bash|Stop", "glob": "*rm -rf*", "message": "止める"}
        self.rules(ruleset(deny=(both,)))
        said = self.said(self.lint(), "both")
        self.assertTrue(
            any(
                "Stop の部分は何も起きない" in s and "Bash の部分はふつうに効く" in s for s in said
            ),
            said,
        )

    def test_lint_warns_on_a_stop_rule_in_a_project_layer(self):
        home = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(home, ".git"))
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        layer = {"version": 1, "allow": [stop_rule("outer", additionalContext=NUDGE, every=5)]}
        write(os.path.join(home, ".ccnavi", "config", "rules.yml"), json.dumps(layer))
        out = self.lint()
        self.assertTrue(
            any("使われない" in line and "outer" in line for line in out.splitlines()), out
        )

    # --- 8. --test と見本 -----------------------------------------------------------

    def diagnose(self, *args: str) -> str:
        done = run_ccnavi(
            ["--root", self.root, "--log", "", "--state", "", "--approved", "", *args],
            cwd=ROOT,
            env=self.env(),
        )
        return done.stdout

    def test_test_stop_lists_the_rules_that_would_be_used(self):
        self.rules(
            ruleset(
                stop_rule(additionalContext=NUDGE, every=10),
                stop_rule("each-time", additionalContext=NUDGE),
            )
        )
        out = self.diagnose("--test", "Stop", "(stop)")
        self.assertIn("verdict: allow", out)
        self.assertIn("  skill-review", out)
        self.assertNotIn("each-time", out)
        self.assertNotIn("対象を取り出せない", out)

    def test_test_stop_without_rules_says_it_never_stops(self):
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        out = self.diagnose("--test", "Stop", "(stop)")
        self.assertIn("verdict: skip", out)
        self.assertIn("止めない", out)

    def test_samples_can_check_stop(self):
        self.rules(ruleset(stop_rule(additionalContext=NUDGE, every=10)))
        samples = write(
            os.path.join(self.root, "samples.yml"),
            json.dumps(
                {
                    "deny": [],
                    "allow": [{"tool": "Stop", "subject": "(stop)", "why": "振り返り"}],
                }
            ),
        )
        body = json.loads(self.diagnose("--test-samples", samples, "--json"))
        self.assertEqual(body["mismatches"], 0, body)
        self.assertEqual(body["skipped"], 0, body)
        self.assertEqual([h["id"] for h in body["samples"][0]["rules"]], ["skill-review"])


if __name__ == "__main__":
    unittest.main()
