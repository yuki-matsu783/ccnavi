"""できあがりの rules.yml・CCNAVI.md・docs が、直接作業のワークツリーを
ccnavi-worktree.sh で切る案内を書くこと。

保護対象（rules.yml・CCNAVI.md）は、下書きがあれば下書きの、無ければ本物の「できあがりの姿」を読む
（tests/draft.py）。`{script:<名前>}` の差し込みと案内のコマンドの引用は
`go/internal/policy/rules`・`go/internal/infra/config` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

import yaml

from tests import ROOT
from tests.draft import final
from tests.inproc import run_ccnavi


class FinalFilesTest(unittest.TestCase):
    """できあがりの rules.yml・CCNAVI.md・docs の中身。"""

    def rules(self) -> dict:
        with open(final(".ccnavi/config/common/rules.yml"), encoding="utf-8") as f:
            return yaml.safe_load(f)

    def rule(self, rule_id: str) -> dict:
        for group in ("deny", "ask", "allow"):
            for r in self.rules().get(group, []):
                if r["id"] == rule_id:
                    return r
        raise AssertionError(rule_id)

    def test_workspace_root_message_points_at_the_worktree_script_and_the_docs(self):
        text = " ".join(self.rule("workspace-root")["message"].split())
        self.assertIn("{script:ccnavi-worktree.sh} <名前>", text)
        self.assertIn("worktree-merge.md「直接作業のワークツリーを取り込む」", text)
        self.assertIn("worktree.md「直接作業のワークツリーの名前」", text)
        self.assertIn("cd projects/<プロジェクト名> && {script:ccnavi-worktree.sh} <名前>", text)
        self.assertNotIn("ff-only", text)

    def test_the_worktree_add_hint_has_no_ff_only_procedure(self):
        text = " ".join(self.rule("ccnavi-git-worktree-add")["additionalContextOnce"].split())
        self.assertIn("{script:ccnavi-worktree.sh} <名前>", text)
        self.assertNotIn("--no-track", text, "手の worktree add に誘わない")
        self.assertNotIn("ff-only", text)

    def test_no_ff_only_cutting_procedure_is_left_in_the_docs_or_rules(self):
        paths = [final(".ccnavi/config/common/rules.yml")]
        docs = os.path.join(ROOT, ".ccnavi", "docs")
        paths += [os.path.join(docs, n) for n in os.listdir(docs) if n.endswith(".md")]
        for path in paths:
            with self.subTest(path=os.path.basename(path)), open(path, encoding="utf-8") as f:
                self.assertNotIn("merge --ff-only", f.read())

    def test_the_test_command_speaks_the_quoted_script_and_the_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(os.path.realpath(tmp), "a b")
            home = os.path.join(ws, ".ccnavi", "config", "common")
            os.makedirs(home)
            shutil.copy(final(".ccnavi/config/common/rules.yml"), home)
            os.makedirs(os.path.join(ws, "src"))
            done = run_ccnavi(
                ["--root", ws, "--test", "Write", f"{ws}/src/x.py", "--json"], input="", cwd=ws
            )
            response = json.loads(done.stdout)["response"]
        # 空白を含むルートは "..." で引用する（go/internal/infra/config の go test も見る）。
        spelled = ws.replace(os.sep, "/")
        self.assertIn(f'sh "{spelled}/.ccnavi/scripts/ccnavi-worktree.sh"', response)
        self.assertIn("worktree-merge.md", response)
        self.assertNotIn("ff-only", response)

    def test_ccnavi_md_line_is_a_one_line_replacement(self):
        with open(final(".ccnavi/CCNAVI.md"), encoding="utf-8") as f:
            text = f.read()
        line = next(ln for ln in text.splitlines() if "{{worktree_sh}}" in ln)
        old = (
            "  - 編集する前にワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。"
            "切り方は ccnavi-git.sh の --help（worktree add）。直接作業の名前は "
            "<先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。"
        )
        self.assertLess(len(line) - len(old), 40)
        self.assertNotIn("--help（worktree add）", text)
        self.assertEqual(text.count("{{worktree_sh}}"), 1)


if __name__ == "__main__":
    unittest.main()
