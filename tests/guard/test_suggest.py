"""記録からルールの候補を起こすこと（`ccnavi --suggest`、issue #149 の 4）。

記録を手で書いたワークスペースに対して道具を動かし、出た候補を読む。候補は
`--lint` と `--test-samples` と同じ検査を通ったものだけで、allow は出さない。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

import yaml

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# VS Code 拡張が読む `--suggest --json` の例。形を変えたらここも書き直す。
FIXTURE = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "test", "fixtures", "suggest.json")

RULES = """\
version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push しません。利用者に頼んでください。
allow:
  - id: listing
    match: Bash
    regex: '\\bls\\b'
"""


def _line(**fields) -> str:
    return json.dumps({"ts": "2026-09-01T00:00:00+00:00", "mode": "enable", **fields})


def _handover(tool: str, subject: str) -> str:
    return _line(
        event="PreToolUse",
        tool=tool,
        subject=subject,
        decision="handover",
        enforced=False,
        code="UNDECLARED",
    )


def _deny(subject: str, rule: str = "git-push") -> str:
    return _line(
        event="PreToolUse",
        tool="Bash",
        subject=subject,
        decision="deny",
        enforced=True,
        code="DENY_COMMAND_PATTERN",
        rules=[rule],
    )


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-suggest-ws-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        rules_path = common_path(self.ws, "rules")
        os.makedirs(os.path.dirname(rules_path))
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(RULES)
        self.logs = os.path.join(self.ws, "logs")
        os.makedirs(self.logs)

    def _write(self, name: str, lines: list[str]) -> None:
        with open(os.path.join(self.logs, name), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _suggest(self, *extra: str):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return run_ccnavi(["--root", self.ws, "--suggest", *extra], env=env, cwd=self.ws)

    def _json(self) -> dict:
        result = self._suggest("--json")
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_handover_becomes_an_ask_rule(self):
        """同じ形で何度も渡った呼び出しは ask のルールの候補になる。回した記録も数える。"""
        self._write(
            "decisions.jsonl", [_handover("Bash", f"npm install p{i % 2}") for i in range(3)]
        )
        self._write(
            "decisions.20260901-000000.jsonl",
            [_handover("Bash", "npm install p0") for _ in range(3)],
        )
        body = self._json()
        self.assertEqual(2, len(body["logs"]))
        (found,) = body["candidates"]
        self.assertEqual(("rule", "ask", 6), (found["kind"], found["section"], found["count"]))
        self.assertEqual("suggest-npm-install", found["rule"]["id"])
        self.assertEqual({"ask"}, set(yaml.safe_load(found["yaml"])["samples"]))

    def test_few_handovers_are_not_candidates(self):
        """数回だけなら候補にしない。"""
        self._write("decisions.jsonl", [_handover("Bash", "docker run x") for _ in range(4)])
        self.assertEqual([], self._json()["candidates"])

    def test_paths_are_written_from_root(self):
        """パスのツールはディレクトリの下を `{root}` から書き、見本は `/repo` で書く。"""
        where = os.path.realpath(self.ws)
        self._write(
            "decisions.jsonl", [_handover("Write", f"{where}/docs/n{i}.md") for i in range(5)]
        )
        (found,) = self._json()["candidates"]
        self.assertEqual("{root}/docs/*", found["rule"]["glob"])
        self.assertTrue(all(s["subject"].startswith("/repo/docs/") for s in found["samples"]))

    def test_candidates_that_fail_the_samples_are_dropped(self):
        """いまのルールで ask にならない形（deny に当たる）は、検証で落として数だけ言う。"""
        self._write("decisions.jsonl", [_handover("Bash", "git push --force") for _ in range(5)])
        body = self._json()
        self.assertEqual([], body["candidates"])
        self.assertEqual(1, body["dropped"])

    def test_repeated_denies_point_at_the_message(self):
        """同じ呼び出しを繰り返し止めたルールは、文面を見直す候補になる。"""
        self._write(
            "decisions.jsonl",
            [_deny("git push origin main"), _deny("git  push 'origin' main")]
            + [_deny('git push "origin" main')]
            + [_deny("git push origin x")],
        )
        body = self._json()
        (found,) = body["candidates"]
        self.assertEqual(
            ("message", "deny", "git-push"), (found["kind"], found["section"], found["id"])
        )
        self.assertEqual("push しません。利用者に頼んでください。", found["rule"]["message"])
        self.assertEqual(["git push origin main"], [s["subject"] for s in found["samples"]])

    def test_denies_from_outside_the_rules_are_not_candidates(self):
        """ルールファイルに無い根拠の拒否は、直す文面が無いので候補にしない。"""
        self._write("decisions.jsonl", [_deny("rm x", rule="builtin-guard-x") for _ in range(3)])
        body = self._json()
        self.assertEqual([], body["candidates"])
        self.assertEqual(1, body["dropped"])

    def test_never_allow(self):
        """どの候補も deny か ask。"""
        self._write(
            "decisions.jsonl",
            [_handover("Bash", "npm ci") for _ in range(5)] + [_deny("git push") for _ in range(3)],
        )
        body = self._json()
        self.assertEqual(2, len(body["candidates"]))
        for c in body["candidates"]:
            self.assertIn(c["section"], ("deny", "ask"))
            self.assertNotIn("allow", yaml.safe_load(c["yaml"])["rules"])

    def test_text_output_is_yaml_documents(self):
        """文字で出すときは、候補ごとに YAML の文書を 1 つずつ並べる。"""
        self._write("decisions.jsonl", [_handover("Bash", "npm ci") for _ in range(5)])
        result = self._suggest()
        self.assertEqual(0, result.returncode, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual("suggest-npm-ci", docs[0]["rules"]["ask"][0]["id"])

    def test_extension_fixture_has_the_same_keys(self):
        """VS Code 拡張が読む例（test/fixtures/suggest.json）は、いまの出力と同じ鍵を持つ。"""
        self._write(
            "decisions.jsonl",
            [_handover("Bash", "npm ci") for _ in range(5)] + [_deny("git push") for _ in range(3)],
        )
        body = self._json()
        with open(FIXTURE, encoding="utf-8") as f:
            example = json.load(f)
        self.assertEqual(sorted(body), sorted(example))
        self.assertEqual(example["version"], body["version"])
        for got, want in zip(body["candidates"], example["candidates"], strict=True):
            self.assertEqual(sorted(got), sorted(want))
            self.assertEqual(sorted(got["samples"][0]), sorted(want["samples"][0]))

    def test_no_log(self):
        """記録が無ければそう言って 0 で終わる。"""
        result = self._suggest()
        self.assertEqual(0, result.returncode)
        self.assertIn("記録が無い", result.stdout)


if __name__ == "__main__":
    unittest.main()
