"""実績で測るリスクの受入テスト。

見るのは 5 つ（定義の検証と差分の数え方の細部は go/internal/tickets/config/risk が見る）。

1. 差分から点を数える（行数・ファイル数・消したファイル）
2. スクリプトの項目（点を受け取る。測れなければ重いほうとして扱う）
3. 定性の項目（判定が揃うまで閉じられない。judge で記録。HEAD が動けば取り直し）
4. HIGH 以上なら宣言に関わらずレビューが要る（レビューで止まり、依頼文にリスクの行が載る）
5. --lint が不正な定義を言い、不正なら組み込みに戻る
"""

from __future__ import annotations

import json
import os
import unittest

from tests import common_path
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, parent_text

RISK = """
version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
  - {id: many-files, points: 15, files_over: 2, message: ファイルが多い}
  - {id: ci, points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes, points: 20, deleted_over: 0, message: 消したファイルがある}
"""


class RiskTest(PhaseHarness):
    """親と子を 1 本ずつ動かして、閉じるときの点を見る。"""

    def setUp(self):
        super().setUp()
        # 配点と定義は共通レイヤーの既定の置き場へ。`--risk` / `--phases` は診断でだけ有効で、
        # `ticket` のサブコマンドには届かない。差し替えるテストはこの形に書き直す。
        self.risk = write(common_path(self.root, "risk"), RISK)
        # 範囲の上限が無く、レビュー不要の定義。宣言では「レビュー不要」な作業を実績で上書きする。
        self.phases_text = (
            "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n"
            "    review: false\n    scope: inherit\n"
        )
        write(self.phases, self.phases_text)

    def one_child(self, review=False):
        scope = ("src/*", "wip/*", ".github/*")
        self.propose("i0001", parent_text("i0001", ["work"], allow=scope, copy=self.phases_text))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, list(scope), review=review)
        )
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        return self.run_child("i0001-01-01", [("wip/research/summary.md", "s\n")])

    def record(self):
        return read_json(os.path.join(self.approved, "phases", "i0001", "i0001-01-01.risk.json"))

    # ---- 2. 差分から数える

    def test_small_change_is_low_and_the_phase_is_skipped(self):
        self.one_child()
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("リスク: 0 (LOW)", closed.stdout)
        self.assertEqual(0, self.record()["points"])
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("レビューは不要", self.reason(said))

    def test_big_change_escalates_to_review_even_when_declared_unneeded(self):
        tree = self.one_child(review=False)
        # 行数と、ファイル数と、CI と、削除。全部ヒットする。
        write(os.path.join(tree, "src", "a.py"), "\n".join(str(i) for i in range(20)) + "\n")
        write(os.path.join(tree, "src", "b.py"), "b\n")
        write(os.path.join(tree, ".github", "ci.yml"), "on: push\n")
        git(tree, "rm", "-q", os.path.join("src", "keep.py"))
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "big")
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("(CRITICAL)", closed.stdout)
        self.assertIn("宣言に関わらず", closed.stdout)
        record = self.record()
        self.assertEqual(95, record["points"])
        self.assertEqual(
            {"big-diff", "many-files", "ci", "deletes"}, {h["id"] for h in record["hits"]}
        )
        # 定義は review: false、子も required: false。それでもレビューで止まる。
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("実績のリスクが高い", self.reason(said))
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
        self.assertIn("実績のリスクが高い", self.reason(spawn))
        explained = self.ccnavi("--explain")
        self.assertIn("実績でレビュー必須", explained.stdout)
        # 依頼文の先頭にリスクの行が載る。
        fixture = self.remote()
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertIn("このレビューのリスク: 95 (CRITICAL)", self.last_request_body)
        self.assertIn("行数が多い", self.last_request_body)

    # ---- 3. スクリプト

    def test_script_factor_adds_its_points_and_fails_heavy(self):
        script = write(
            os.path.join(self.root, ".ccnavi", "config", "common", "scripts", "count.sh"),
            'printf \'{"points": 30, "message": "%s"}\' "$CCNAVI_TICKET"\n',
        )
        self.assertTrue(os.path.exists(script))
        write(
            self.risk,
            "version: 1\nfactors:\n"
            "  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh,"
            " message: 数えた}\n"
            "  - {id: broken, points: 45, script: .ccnavi/config/common/scripts/none.sh, message: 無い}\n",  # noqa: E501
        )
        self.one_child()
        closed = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        record = self.record()
        self.assertEqual(75, record["points"])
        by_id = {h["id"]: h for h in record["hits"]}
        self.assertIn("i0001-01-01", by_id["counted"]["detail"])
        self.assertEqual(45, by_id["broken"]["points"])
        self.assertTrue(record["unmeasured"])
        self.assertIn("測れなかった", closed.stdout)

    # ---- 4. 定性

    def test_judge_factor_blocks_done_until_recorded(self):
        write(
            self.risk,
            "version: 1\nlevels: {high: 30}\nfactors:\n"
            "  - {id: untested, points: 30, judge: テストの無い振る舞いの変更を含むか,"
            " message: テスト無し}\n",
        )
        tree = self.one_child()
        refused = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("untested", refused.stderr)
        self.assertIn("judge", refused.stderr)
        with open(os.path.join(self.state, "risk-judge-i0001-01-01.md"), encoding="utf-8") as f:
            prompt = f.read()
        self.assertIn("テストの無い振る舞いの変更を含むか", prompt)
        self.assertIn("wip/research/summary.md", prompt)
        # 親が記録する。yes で加点。
        judged = self.ccnavi(
            "ticket",
            "record-risk",
            "i0001-01-01",
            "untested",
            "yes",
            "--reason",
            "テストが無い",
        )
        self.assertEqual(judged.returncode, 0, judged.stderr)
        wrong = self.ccnavi("ticket", "record-risk", "i0001-01-01", "nope", "yes", "--reason", "x")
        self.assertNotEqual(wrong.returncode, 0)
        # HEAD が動いたら判定は古い。
        write(os.path.join(tree, "src", "later.py"), "1\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "later")
        stale = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("untested", stale.stderr)
        judged = self.ccnavi(
            "ticket",
            "record-risk",
            "i0001-01-01",
            "untested",
            "no",
            "--reason",
            "テストを足した",
        )
        self.assertEqual(judged.returncode, 0, judged.stderr)
        closed = self.ccnavi("ticket", "finish", "i0001-01-01")
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
                "command": "sh .ccnavi/scripts/ccnavi-ticket.sh record-risk i0001-01-01 "
                "untested yes --reason x"
            },
        }
        denied = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(denied))

    # ---- 5. サブコマンドに配点を渡しても反映されない（`--risk` は診断でだけ有効）

    def test_a_risk_flag_on_ticket_done_does_not_change_the_score(self):
        """`ticket finish <子> --risk <別の配点>` は採点を差し替えない。issue #65。

        sh のラッパー（`.ccnavi/scripts/ccnavi-ticket.sh`）が受け取った引数をそのまま渡すので、
        この形はエージェントが Bash で打てる。通していた頃は、重い変更を軽い配点で
        閉じられた。
        """
        cheap = write(
            os.path.join(self.root, "cheap.yml"),
            "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors: []\n",
        )
        tree = self.one_child(review=False)
        write(os.path.join(tree, "src", "a.py"), "\n".join(str(i) for i in range(20)) + "\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "big")

        closed = self.ccnavi("ticket", "finish", "i0001-01-01", "--risk", cheap)

        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("--risk は診断", closed.stderr, "無視したことを言っていない")
        self.assertEqual(25, self.record()["points"], "渡した配点で採点された")

    def test_an_empty_risk_flag_on_ticket_done_is_dropped_too(self):
        """`--risk ""` も同じ。空は「配点を持たない」の意味で通っていた（issue #65）。"""
        tree = self.one_child(review=False)
        write(os.path.join(tree, "src", "a.py"), "\n".join(str(i) for i in range(20)) + "\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "big")

        closed = self.ccnavi("ticket", "finish", "i0001-01-01", "--risk", "")

        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("--risk は診断", closed.stderr)
        self.assertEqual(25, self.record()["points"])

    def test_a_second_root_on_ticket_done_is_refused(self):
        """`ticket finish <子> --root <偽>` は断る。issue #65 の敵対的レビューで出た形。

        sh は自分の `--root` を先に置き、エージェントの引数を後ろに繋ぐ
        （`exec "$bin" --root "$root" ticket "$@"`）。argparse は後勝ちなので、後ろに
        1 本足すと sh が渡した正しい値を上書きできた。`--root` からは共通レイヤーの 3 本も
        `projects` も `approved` も導かれるので、`--risk` を使わずに同じ差し替えができる。
        実際に確かめると、実際のツリーへシンボリックリンクを張った偽のルートを渡すと、子が
        「リスク 0」で実際の置き場に閉じられた。

        ここでは偽のルートの中身を作り込まない。2 本目が在ること自体を断るので、
        中身に関わらず同じところで止まる。
        """
        tree = self.one_child(review=False)
        write(os.path.join(tree, "src", "a.py"), "\n".join(str(i) for i in range(20)) + "\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "big")

        refused = self.ccnavi(
            "ticket", "finish", "i0001-01-01", "--root", os.path.join(self.root, "x")
        )

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--root は 1 度しか渡せない", refused.stderr)
        # 止まったことを、結末の側でも見る。閉じても記録を残してもいない。
        doing = os.path.join(self.approved, "doing", "i0001-01-01.md")
        self.assertTrue(os.path.exists(doing), "doing/ から動いた")
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "i0001-01-01.risk.json")),
            "採点の記録が残った",
        )

    # ---- 6. lint と組み込みへの退避

    def test_lint_reports_a_broken_definition_and_builtin_takes_over(self):
        write(self.risk, "version: 1\nfactors: 3\n")
        linted = self.ccnavi("--lint")
        self.assertIn("(risk)", linted.stdout + linted.stderr)
        self.one_child()
        closed = self.ccnavi("ticket", "finish", "i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("組み込みの配点", closed.stderr)
        self.assertIn("(builtin)", closed.stdout)


if __name__ == "__main__":
    unittest.main()
