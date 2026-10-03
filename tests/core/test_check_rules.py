"""tools/check_rules.py が既定で指す置き場。

既定の綴りが `.ccnavi/common/`（ADR-0042）の実物と食い違うと、引数なしで打ったときに
見本を読めずに落ちる。ツールを最後まで回すと、見本の食い違いの有無で終了コードが変わるので、
前半では既定の綴りが実在することだけを見る。

後半は、リポジトリの見本（`.ccnavi/common/rule-samples.yml`）をいまのルールで判定し、
食い違いが 0 件であることを見る。CI が無いので、ここで見ないと `/ccnavi-config` か
`tools/check_rules.py` を手で打つまで食い違いに気づけない。引数はツールと同じもの
（`arguments()`）を使い、判定は `--test-samples` そのものに任せる。
"""

from __future__ import annotations

import importlib.util
import json
import os
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi


def _load_check_rules():
    """tools/ はパッケージではないので、名前でなく場所で読む。"""
    spec = importlib.util.spec_from_file_location(
        "ccnavi_check_rules", os.path.join(ROOT, "tools", "check_rules.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DefaultPlacesTest(unittest.TestCase):
    def setUp(self):
        self.tool = _load_check_rules()

    def test_the_default_rules_exist(self):
        self.assertTrue(os.path.isfile(self.tool.RULES), self.tool.RULES)

    def test_the_default_samples_exist(self):
        self.assertTrue(os.path.isfile(self.tool.SAMPLES), self.tool.SAMPLES)


class RepoSamplesTest(unittest.TestCase):
    """リポジトリの見本が、いまのルールで期待どおりに判定されること。"""

    def test_the_repo_samples_match_the_rules(self):
        tool = _load_check_rules()
        # 走らせた人の dry-run や設定の差し替え（CCNAVI_*）と、hook のルート
        # （CLAUDE_PROJECT_DIR）を判定に入れない。tests/guard/test_repo_rules.py と同じ外し方。
        environment = tool.environment()
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # 見本の /repo は --root の綴りにそのまま置き換わる。Windows の `\` は Bash の見本で
        # エスケープとして読まれるので、tests/guard/test_repo_rules.py と同じく `/` に揃えて渡す。
        arguments = tool.arguments()
        at = arguments.index("--root") + 1
        arguments[at] = arguments[at].replace("\\", "/")
        done = run_ccnavi([*arguments, "--json"], cwd=ROOT, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertGreater(len(body["samples"]), 0, "見本が 1 件も読めていない")
        wrong = [
            (
                "{expected} のはずが {verdict}: {tool}  {subject}\n"
                "    なぜ: {why}\n"
                "    当たったルール: {hit}"
            ).format(
                hit=", ".join(f"{h['section']}:{h['id']}" for h in r["rules"]) or "(無し)",
                **r,
            )
            for r in body["samples"]
            if not r["ok"]
        ]
        self.assertEqual(
            body["mismatches"],
            0,
            "見本と判定が食い違った（見本とルールのどちらを直すかは人が決める。/ccnavi-config）:\n"
            + "\n".join(wrong),
        )


if __name__ == "__main__":
    unittest.main()
