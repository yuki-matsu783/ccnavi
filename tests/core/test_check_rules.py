"""scripts/check_rules.py が既定で指す置き場。

既定のパスが `.ccnavi/config/common/` の実物と食い違うと、引数なしで打ったときに
期待する判定を読めずに失敗する。ツールを最後まで回すと、期待する判定の食い違いの有無で終了コードが変わるので、
前半では既定のパスが実在することだけを見る。

後半は、リポジトリの期待する判定（`.ccnavi/config/common/rule-expectations.yml`）をいまのルールで判定し、
食い違いが 0 件であることを見る。CI が無いので、ここで見ないと `/ccnavi-config` か
`scripts/check_rules.py` を手で打つまで食い違いに気づけない。引数はツールと同じもの
（`arguments()`）を使い、判定は `--test-expectations` そのものに任せる。
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi


def _load_check_rules():
    """scripts/ はパッケージではないので、名前でなく場所で読む。"""
    spec = importlib.util.spec_from_file_location(
        "ccnavi_check_rules", os.path.join(ROOT, "scripts", "check_rules.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DefaultPlacesTest(unittest.TestCase):
    def setUp(self):
        self.tool = _load_check_rules()

    def test_the_default_rules_exist(self):
        self.assertTrue(os.path.isfile(self.tool.RULES), self.tool.RULES)

    def test_the_default_expectations_exist(self):
        self.assertTrue(os.path.isfile(self.tool.EXPECTATIONS), self.tool.EXPECTATIONS)


class RepoExpectationsTest(unittest.TestCase):
    """リポジトリの期待する判定が、いまのルールで期待どおりに判定されること。"""

    def test_the_repo_expectations_match_the_rules(self):
        tool = _load_check_rules()
        # 走らせたユーザの dry-run や設定の差し替え（CCNAVI_*）と、hook のルート
        # （CLAUDE_PROJECT_DIR）を判定に入れない。tests/guard/test_repo_rules.py と同じ外し方。
        environment = tool.environment()
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # --root の区切りを `/` に揃えるのは arguments() の側（Windows の `\` を期待に残さない）。
        #
        # 期待の `/repo` は --root に読み替わる。ルートがこのリポジトリのままだと、ワークツリー
        # （`.claude/worktrees/<名前>` の下）で回したときに `Write /repo/wip/...` が
        # allow:worktrees にヒットし、本流と答えが割れる。ルートだけを、ワークツリーの下にならない
        # 使い捨ての置き場に替える。ルールと期待は arguments() のとおりこのリポジトリのものを読む。
        with tempfile.TemporaryDirectory(prefix="ccnavi-expectations-root-") as scratch_root:
            arguments = tool.arguments()
            arguments[arguments.index("--root") + 1] = os.path.realpath(scratch_root).replace(
                "\\", "/"
            )
            done = run_ccnavi([*arguments, "--json"], cwd=ROOT, env=environment)
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
