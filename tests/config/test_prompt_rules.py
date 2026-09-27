"""`match: UserPromptSubmit` のルール（利用者の発言に当てる文）の受入テスト。

利用者が何か言った回（UserPromptSubmit）は、これまで承認の知らせのほかは何も返さなかった。
`match: UserPromptSubmit` の `allow` のルールを書いたときだけ、その文を渡す（ADR-0090）。
見るのは 5 つ。

1. 書いていなければ今までどおり何も返さない
2. `every: N` で N 回の発言に 1 度だけ渡る。本文のファイルも渡る
3. 当てる先は発言の本文。`regex` で発言を選べる
4. cwd がプロジェクトの中でも同じルールが当たる。プロジェクトの層に書いたルールも当たる
5. `deny` / `ask` に書いたものは渡らず、`--lint` が warn で言う。
   `allow` なら「当てる対象が無い」とは言わない

数えと控えは `every` と同じもの（tests/config/test_rule_every.py）。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

NUDGE = "ここまでの作業を振り返り、スキルの提案が要るかを見ること。"


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ruleset(*allow: dict, deny: tuple[dict, ...] = (), ask: tuple[dict, ...] = ()) -> str:
    body = {
        "version": 1,
        "deny": [
            {"id": "push", "match": "Bash", "glob": "*git push*", "message": "push は人が行う"},
            *deny,
        ],
        "ask": list(ask),
        "allow": list(allow),
    }
    return json.dumps(body)


def prompt_rule(name: str = "skill-review", **extra) -> dict:
    return {"id": name, "match": "UserPromptSubmit", "glob": "*", **extra}


class PromptRulesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.state = os.path.join(self.root, "state")
        self.addCleanup(self.dir.cleanup)

    def env(self) -> dict:
        return {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}

    def rules(self, text: str) -> str:
        return write(common_path(self.root, "rules"), text)

    def say(self, prompt: str = "次へ進めて", *, cwd: str = "", session: str = "s1") -> str:
        """利用者が 1 回発言して、モデルへ渡った文。渡らなければ空。"""
        payload = json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session,
                "cwd": cwd or self.root,
                "prompt": prompt,
            }
        )
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--log",
                "",
                "--state",
                self.state,
                "--approved",
                "",
                "--mode",
                "enable",
            ],
            input=payload,
            cwd=ROOT,
            env=self.env(),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        out = json.loads(done.stdout)["hookSpecificOutput"]
        self.assertEqual(out.get("hookEventName"), "UserPromptSubmit")
        return out.get("additionalContext", "")

    def lint(self) -> subprocess.CompletedProcess:
        return run_ccnavi(
            ["--root", self.root, "--log", "", "--state", "", "--approved", "", "--lint"],
            cwd=ROOT,
            env=self.env(),
        )

    # --- 1. 書かなければ何も返さない ----------------------------------------------

    def test_nothing_without_a_prompt_rule(self):
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        self.assertEqual([self.say() for _ in range(3)], ["", "", ""])

    # --- 2. every で N 回に 1 度 -------------------------------------------------

    def test_every_counts_prompts(self):
        self.rules(ruleset(prompt_rule(additionalContext=NUDGE, every=3)))
        self.assertEqual([self.say() for _ in range(6)], ["", "", NUDGE, "", "", NUDGE])

    def test_file_body_is_delivered(self):
        write(os.path.join(self.root, "docs", "review.md"), "振り返りの手順")
        self.rules(ruleset(prompt_rule(additionalContextFile="docs/review.md", every=2)))
        self.assertEqual([self.say() for _ in range(2)], ["", "振り返りの手順"])

    def test_the_count_is_per_session(self):
        self.rules(ruleset(prompt_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual(self.say(session="a"), "")
        self.assertEqual(self.say(session="b"), "")
        self.assertEqual(self.say(session="a"), NUDGE)

    # --- 3. 当てる先は発言の本文 ----------------------------------------------------

    def test_regex_selects_prompts(self):
        self.rules(
            ruleset(prompt_rule(glob="", regex="違う|そうじゃない", additionalContext=NUDGE))
        )
        self.assertEqual(self.say("ありがとう"), "")
        self.assertEqual(self.say("そうじゃない、先に読んで"), NUDGE)

    def test_empty_prompt_still_counts(self):
        self.rules(ruleset(prompt_rule(additionalContext=NUDGE)))
        self.assertEqual(self.say(""), NUDGE)

    # --- 4. cwd に依らない。プロジェクトの層も当たる --------------------------------

    def project(self, name: str = "lib") -> str:
        home = os.path.join(self.root, "projects", name)
        os.makedirs(os.path.join(home, ".git"), exist_ok=True)
        return home

    def test_same_rule_from_inside_a_project(self):
        home = self.project()
        self.rules(ruleset(prompt_rule(additionalContext=NUDGE, every=2)))
        self.assertEqual(self.say(cwd=home), "")
        self.assertEqual(self.say(cwd=self.root), NUDGE)

    def test_project_layer_rule_applies(self):
        home = self.project()
        self.rules(ruleset({"id": "src", "match": "Write", "glob": "*/src/*"}))
        write(
            os.path.join(home, ".ccnavi", "config", "rules.yml"),
            json.dumps(
                {"version": 1, "allow": [prompt_rule("lib-nudge", additionalContext=NUDGE)]}
            ),
        )
        self.assertEqual(self.say(cwd=self.root), NUDGE)

    def test_file_is_looked_up_in_the_project_first(self):
        home = self.project()
        write(os.path.join(self.root, "docs", "review.md"), "ワークスペースの手順")
        write(os.path.join(home, "docs", "review.md"), "プロジェクトの手順")
        self.rules(ruleset(prompt_rule(additionalContextFile="docs/review.md")))
        self.assertEqual(self.say(cwd=home), "プロジェクトの手順")
        self.assertEqual(self.say(cwd=self.root), "ワークスペースの手順")

    # --- 5. deny / ask では渡らない。lint -----------------------------------------

    def test_deny_and_ask_do_not_deliver_and_lint_warns(self):
        self.rules(
            ruleset(
                deny=(prompt_rule("stop-prompt", message="止める"),),
                ask=(prompt_rule("ask-prompt", additionalContext=NUDGE),),
            )
        )
        self.assertEqual(self.say(), "")
        out = self.lint().stdout
        for name in ("stop-prompt", "ask-prompt"):
            said = [line for line in out.splitlines() if f": {name}: " in line]
            self.assertTrue(
                any(s.startswith("warn:") and "allow に置く" in s for s in said),
                f"{name} に warn が無い:\n{out}",
            )

    def test_lint_does_not_call_the_allow_rule_inert(self):
        self.rules(ruleset(prompt_rule(additionalContext=NUDGE, every=10)))
        out = self.lint().stdout
        said = [line for line in out.splitlines() if ": skill-review: " in line]
        self.assertEqual(said, [], out)


if __name__ == "__main__":
    unittest.main()
