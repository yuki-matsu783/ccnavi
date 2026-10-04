"""日本語を含む識別子で、承認 → 着手 → 判定 → 終える の主な経路が通ること（ADR-0100）。

本物の git リポジトリとワークツリーを一時ディレクトリに作る。親 `feature-64-統合先の解決` と
子 `feature-64-統合先の解決-01` を、`test_ticket.py` と同じ道具で通す。見るのは 5 つ。

1. 親のワークツリーの上で書いた提案は、既にある自分のブランチと重なると言われない（lint）
2. 承認で承認済みチケットが置かれ、着手で基準点が書かれる（ワークツリーの名前とブランチ名が日本語）
3. 子のワークツリーへの書き込みが子のチケットで判定される（範囲の外は止まる）
4. 子を終えると `review/` へ移る。git の出力（`status`・`diff --name-only`）の 8 進の引用に
   惑わされず、日本語のファイル名の承認済みチケットを読み戻せる
5. NFD の綴りの提案は読めない（error）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unicodedata
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import RULES, git, ticket_text, write

PARENT = "feature-64-統合先の解決"
CHILD = PARENT + "-01"


class JapaneseIdentifierTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-ja-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        write(common_path(self.root, "rules"), json.dumps(RULES))
        self.state = os.path.join(self.root, "state")
        self.parent_tree = self.worktree(PARENT, "main")
        self.approved = os.path.join(self.parent_tree, ".ccnavi", "approved")

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin=""):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--approved",
                ".ccnavi/approved",
                "--state",
                self.state,
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def propose(self, name, **kw):
        return write(
            os.path.join(self.parent_tree, "wip", "proposals", "todo", name + ".md"),
            ticket_text(name, **kw),
        )

    def hook_write(self, cwd, target):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": {"file_path": target},
        }
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def test_the_main_path_with_a_japanese_identifier(self):
        self.propose(PARENT, allow=("src/*", "wip/*"))
        self.propose(CHILD, parent=PARENT, phase=1, allow=("src/a/*",), review=True)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")

        # 1. 自分のワークツリーの上の提案は、自分のブランチと重なると言われない。形の warn も無い
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("ADR-0100", lint.stdout, lint.stdout)
        self.assertNotIn("識別子に使えない文字", lint.stdout + lint.stderr)

        # 2. 承認と着手
        result = self.ccnavi("--agree", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", PARENT + ".md")))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "approve")
        started = self.ccnavi("ticket", "start", PARENT)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        child_tree = self.worktree(CHILD, PARENT)
        started = self.ccnavi("ticket", "start", CHILD)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "start")
        with open(os.path.join(self.approved, "doing", CHILD + ".md"), encoding="utf-8") as f:
            self.assertIn(git(child_tree, "rev-parse", "HEAD").strip(), f.read())

        # git の既定の出力は日本語のパスを 8 進で引用する（ccnavi は -z か quotePath=false で読む）
        quoted = git(self.parent_tree, "log", "--name-only", "--format=", "-1", "HEAD~1")
        self.assertIn("\\", quoted)

        # 3. 子のツリーへの書き込みは子のチケットで判定される
        inside = self.hook_write(self.parent_tree, os.path.join(child_tree, "src", "a", "x.py"))
        self.assertNotIn("DENY_TICKET_SCOPE", inside, inside)
        outside = self.hook_write(self.parent_tree, os.path.join(child_tree, "src", "b", "x.py"))
        self.assertIn("DENY_TICKET_SCOPE", outside)
        self.assertIn(CHILD, outside)

        # 4. 子を終える
        write(os.path.join(child_tree, "src", "a", "x.py"), "print(2)\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "--quiet", "-m", "work")
        finished = self.ccnavi("ticket", "finish", CHILD)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        review = os.path.join(self.parent_tree, "wip", "proposals", "review", CHILD + ".md")
        self.assertTrue(os.path.exists(review))
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("識別子に使えない文字", lint.stdout + lint.stderr)

    def test_an_nfd_spelling_is_refused(self):
        nfd = unicodedata.normalize("NFD", "feature-65-ガイド")
        self.propose(nfd, allow=("src/*",))
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("NFC でない", lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
