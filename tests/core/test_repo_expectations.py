"""リポジトリの期待する判定が、いまのルールで期待どおりに判定されること。

リポジトリの期待する判定（`.ccnavi/config/common/rule-expectations.yml`）をいまのルール
（`.ccnavi/config/common/rules.yml`）で判定し、食い違いが 0 件であることを見る。CI が無いので、
ここで見ないと `/ccnavi-config` か `ccnavi --test-expectations` を手で打つまで食い違いに気づけない。
判定は `--test-expectations` そのものに任せる。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

# 共通レイヤーのルールと期待する判定の置き場。
RULES = os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml")
EXPECTATIONS = os.path.join(ROOT, ".ccnavi", "config", "common", "rule-expectations.yml")


class RepoExpectationsTest(unittest.TestCase):
    def test_the_repo_expectations_match_the_rules(self):
        # 走らせたユーザの dry-run や設定の差し替え（CCNAVI_*）と、hook のルート
        # （CLAUDE_PROJECT_DIR）を判定に入れない。tests/guard/test_repo_rules.py と同じ外し方。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # 期待の `/repo` は --root に読み替わる。ルートがこのリポジトリのままだと、ワークツリー
        # （`.claude/worktrees/<名前>` の下）で回したときに `Write /repo/wip/...` が
        # allow:worktrees にヒットし、本流と答えが割れる。ルートだけを、ワークツリーの下にならない
        # 使い捨ての置き場に替える。ルールと期待はこのリポジトリのものを読む。
        #
        # --root の区切りは `/` に揃える。Windows の `\` が残ると、Bash の期待する判定で
        # エスケープとして読まれる。
        with tempfile.TemporaryDirectory(prefix="ccnavi-expectations-root-") as scratch_root:
            arguments = [
                "--root",
                os.path.realpath(scratch_root).replace("\\", "/"),
                "--rules",
                RULES,
                # 見るのはルールだけ。承認済みチケットと state は外し、記録も残さない。
                "--approved",
                "",
                "--state",
                "",
                "--log",
                "",
                "--test-expectations",
                EXPECTATIONS,
                "--json",
            ]
            done = run_ccnavi(arguments, cwd=ROOT, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertGreater(len(body["expectations"]), 0, "期待する判定が 1 件も読めていない")
        wrong = [
            (
                "{expected} のはずが {verdict}: {tool}  {subject}\n"
                "    なぜ: {why}\n"
                "    ヒットしたルール: {hit}"
            ).format(
                hit=", ".join(f"{h['section']}:{h['id']}" for h in r["rules"]) or "(無し)",
                **r,
            )
            for r in body["expectations"]
            if not r["ok"]
        ]
        self.assertEqual(
            body["mismatches"],
            0,
            "期待する判定と判定が食い違った（期待する判定とルールのどちらを直すかはユーザが決める。/ccnavi-config）:\n"
            + "\n".join(wrong),
        )


if __name__ == "__main__":
    unittest.main()
