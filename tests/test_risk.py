"""実績で測るリスク（設計 §17.3、REQ-RSK）の受入テスト。

見るのは 6 つ。

1. 定義の検証（id の重複、points、当て方は 1 つ、script の置き場、levels の順）
2. 差分から点を数える（行数・ファイル数・消したファイル・glob）
3. スクリプトの項目（点を受け取る。測れなければ重い側に倒す）
4. 定性の項目（判定が揃うまで閉じられない。judge で記録。HEAD が動けば取り直し）
5. HIGH 以上なら宣言に関わらずレビューが要る（ゲートが閉じ、依頼文にリスクの行が載る）
6. --lint が壊れた定義を言い、壊れていれば組み込みに落ちる
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import risk
from tests.test_phases import PhaseHarness, child_text, parent_text
from tests.test_ticket import git, read_json, write

RISK = """
version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
  - {id: many-files, points: 15, files_over: 2, message: ファイルが多い}
  - {id: ci, points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes, points: 20, deleted_over: 0, message: 消したファイルがある}
"""


class DefinitionTest(unittest.TestCase):
    def test_builtin_is_used_when_there_is_no_file(self):
        definition, problems = risk.load(os.path.join(os.sep, "no", "such", "risk.yml"))
        self.assertEqual([], problems)
        self.assertEqual(risk.BUILTIN, definition.source)
        self.assertEqual(4, len(definition.factors))

    def test_definition_is_validated(self):
        cases = {
            "重複": "version: 1\nfactors:\n  - {id: a, points: 1, lines_over: 1}\n"
            "  - {id: a, points: 1, files_over: 1}\n",
            "points": "version: 1\nfactors:\n  - {id: a, points: -1, lines_over: 1}\n",
            "当て方は 1 つ": "version: 1\nfactors:\n"
            "  - {id: a, points: 1, lines_over: 1, glob: x}\n",
            "script": "version: 1\nfactors:\n  - {id: a, points: 1, script: tools/x.sh}\n",
            "levels": "version: 1\nlevels: {medium: 50, high: 40}\n",
            "版": "version: 2\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                definition, problems = risk.parse(text)
                self.assertIsNone(definition, name)
                self.assertTrue(problems, name)

    def test_levels_have_fixed_names(self):
        definition, problems = risk.parse("version: 1\nlevels: {medium: 5, high: 10, severe: 99}\n")
        self.assertIsNotNone(definition)
        self.assertEqual(risk.LEVEL_HIGH, definition.level_of(10))
        self.assertEqual(risk.LEVEL_MEDIUM, definition.level_of(9))
        self.assertEqual(risk.LEVEL_LOW, definition.level_of(4))
        self.assertTrue(any("severe" in p.detail for p in problems))


class RiskTest(PhaseHarness):
    """親と子を 1 本ずつ動かして、閉じるときの点を見る。"""

    def setUp(self):
        super().setUp()
        self.risk = write(os.path.join(self.root, "risk.yml"), RISK)
        # 範囲の上限が無く、レビュー不要の種類。宣言では「レビュー不要」な作業を実績で上書きする。
        self.phases = write(
            os.path.join(self.root, "phases-risk.yml"),
            "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n"
            "    review: none\n    scope: inherit\n",
        )

    def ccnavi(self, *args, stdin="", phases=None, risk_file=None):
        return super().ccnavi("--risk", risk_file or self.risk, *args, stdin=stdin, phases=phases)

    def one_child(self, review=False):
        scope = ("src/*", "wip/*", ".github/*")
        self.propose("i0001", parent_text("i0001", ["work"], allow=scope))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, list(scope), review=review))
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        return self.run_child("i0001-01", [("wip/research/summary.md", "s\n")])

    def record(self):
        return read_json(os.path.join(self.approved, "phases", "i0001", "i0001-01.risk.json"))

    # ---- 2. 差分から数える

    def test_small_change_is_low_and_the_phase_is_skipped(self):
        self.one_child()
        closed = self.close_child("i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("リスク: 0 (LOW)", closed.stdout)
        self.assertEqual(0, self.record()["points"])
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("レビューは不要", self.reason(said))

    def test_big_change_escalates_to_review_even_when_declared_unneeded(self):
        tree = self.one_child(review=False)
        # 行数と、ファイル数と、CI と、削除。全部当たる。
        write(os.path.join(tree, "src", "a.py"), "\n".join(str(i) for i in range(20)) + "\n")
        write(os.path.join(tree, "src", "b.py"), "b\n")
        write(os.path.join(tree, ".github", "ci.yml"), "on: push\n")
        git(tree, "rm", "-q", os.path.join("src", "keep.py"))
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "big")
        closed = self.close_child("i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("(CRITICAL)", closed.stdout)
        self.assertIn("宣言に関わらず", closed.stdout)
        record = self.record()
        self.assertEqual(95, record["points"])
        self.assertEqual(
            {"big-diff", "many-files", "ci", "deletes"}, {h["id"] for h in record["hits"]}
        )
        # 種類は review: none、子も required: false。それでもゲートが閉じる。
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("実績のリスクが高い", self.reason(said))
        self.commit_parent("close 01")
        self.merge("i0001-01")
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_GATE", self.reason(spawn))
        self.assertIn("実績のリスクが高い", self.reason(spawn))
        explained = self.ccnavi("--explain")
        self.assertIn("実績でレビュー要", explained.stdout)
        # 依頼文の先頭にリスクの行が載る。
        fixture = self.remote()
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertIn("このレビューのリスク: リスク: 95 (CRITICAL)", self.last_request_body)
        self.assertIn("行数が多い", self.last_request_body)

    # ---- 3. スクリプト

    def test_script_factor_adds_its_points_and_fails_heavy(self):
        script = write(
            os.path.join(self.root, ".claude", "ccnavi", "risk", "count.sh"),
            'printf \'{"points": 30, "message": "%s"}\' "$CCNAVI_TICKET"\n',
        )
        self.assertTrue(os.path.exists(script))
        risk_file = write(
            os.path.join(self.root, "risk2.yml"),
            "version: 1\nfactors:\n"
            "  - {id: counted, points: 5, script: .claude/ccnavi/risk/count.sh, message: 数えた}\n"
            "  - {id: broken, points: 45, script: .claude/ccnavi/risk/none.sh, message: 無い}\n",
        )
        self.one_child()
        closed = self.ccnavi("ticket", "done", "i0001-01", risk_file=risk_file)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        record = self.record()
        self.assertEqual(75, record["points"])
        by_id = {h["id"]: h for h in record["hits"]}
        self.assertIn("i0001-01", by_id["counted"]["detail"])
        self.assertEqual(45, by_id["broken"]["points"])
        self.assertTrue(record["unmeasured"])
        self.assertIn("測れなかった", closed.stdout)

    # ---- 4. 定性

    def test_judge_factor_blocks_done_until_recorded(self):
        risk_file = write(
            os.path.join(self.root, "risk3.yml"),
            "version: 1\nlevels: {high: 30}\nfactors:\n"
            "  - {id: untested, points: 30, judge: テストの無い振る舞いの変更を含むか,"
            " message: テスト無し}\n",
        )
        tree = self.one_child()
        refused = self.ccnavi("ticket", "done", "i0001-01", risk_file=risk_file)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("untested", refused.stderr)
        self.assertIn("judge", refused.stderr)
        with open(os.path.join(self.state, "risk-judge-i0001-01.md"), encoding="utf-8") as f:
            prompt = f.read()
        self.assertIn("テストの無い振る舞いの変更を含むか", prompt)
        self.assertIn("wip/research/summary.md", prompt)
        # 親が記録する。yes で加点。
        judged = self.ccnavi(
            "ticket",
            "judge",
            "i0001-01",
            "untested",
            "yes",
            "--reason",
            "テストが無い",
            risk_file=risk_file,
        )
        self.assertEqual(judged.returncode, 0, judged.stderr)
        wrong = self.ccnavi(
            "ticket", "judge", "i0001-01", "nope", "yes", "--reason", "x", risk_file=risk_file
        )
        self.assertNotEqual(wrong.returncode, 0)
        # HEAD が動いたら判定は古い。
        write(os.path.join(tree, "src", "later.py"), "1\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "later")
        stale = self.ccnavi("ticket", "done", "i0001-01", risk_file=risk_file)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("untested", stale.stderr)
        judged = self.ccnavi(
            "ticket",
            "judge",
            "i0001-01",
            "untested",
            "no",
            "--reason",
            "テストを足した",
            risk_file=risk_file,
        )
        self.assertEqual(judged.returncode, 0, judged.stderr)
        closed = self.ccnavi("ticket", "done", "i0001-01", risk_file=risk_file)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(0, self.record()["points"])
        # サブエージェントは judge を打てない。
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "agent_id": "sub-1",
            "tool_input": {
                "command": "sh .claude/scripts/ccnavi-ticket.sh judge i0001-01 untested yes "
                "--reason x"
            },
        }
        denied = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(denied))

    # ---- 6. lint と組み込みへの退避

    def test_lint_reports_a_broken_definition_and_builtin_takes_over(self):
        broken = write(os.path.join(self.root, "broken.yml"), "version: 1\nfactors: 3\n")
        linted = self.ccnavi("--lint", risk_file=broken)
        self.assertIn("(risk)", linted.stdout + linted.stderr)
        self.one_child()
        closed = self.ccnavi("ticket", "done", "i0001-01", risk_file=broken)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("組み込みの配点", closed.stderr)
        self.assertIn(risk.BUILTIN, closed.stdout)


if __name__ == "__main__":
    unittest.main()
