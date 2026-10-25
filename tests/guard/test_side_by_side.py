"""横で作業する指定が有効な間の実行後チェックの受入テスト。

ワークスペース 1 つとプロジェクト 2 つ（p と q）を作り、p の作業ワークツリーと
直接作業の記録（`ccnavi-direct/<名前>`）を置く。見るのは次のこと。

- 指定が有効なら、p のユーザのチェックアウトの deny の場所の変更は戻さず、出所不明として報告する
- エージェントが p の作業ワークツリーで動いていても、p は監視される
- ワークスペース自身と q は従来どおり戻る
- 指定が無い・ワークツリーを消した・共通ディレクトリが違う記録は無効で、従来どおり戻る
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import directrec, tree
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

SCHEMA_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "schema",
            "match": "Write|Edit",
            "glob": "*/schema/*",
            "message": "schema is not edited by hand.",
        }
    ],
    "allow": [{"id": "anything", "match": "Bash|Read|Write|Edit", "regex": "."}],
}
WS_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "ws-schema",
            "match": "Write|Edit",
            "glob": "*/schema/*",
            "message": "schema is not edited by hand.",
        }
    ],
    "allow": [{"id": "anything", "match": "Bash|Read|Write|Edit", "regex": "."}],
}
NAME = "fix-1-p-x"


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class SideBySideTest(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-sbs-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        git(self.ws, "init", "--quiet", "-b", "main")
        write(os.path.join(self.ws, ".gitignore"), "/projects/\n/.claude/\n/logs/\n")
        write(os.path.join(self.ws, "schema", "keep.sql"), "-- keep\n")
        write(common_path(self.ws, "rules"), json.dumps(WS_RULES))
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "--quiet", "-m", "init")
        self.p = self.project("p")
        self.q = self.project("q")
        self.wt = os.path.join(self.ws, ".claude", "worktrees", NAME)
        git(self.p, "worktree", "add", "--quiet", self.wt, "-b", NAME)
        self.state = os.path.join(self.ws, "logs", "state")

    def project(self, name):
        root = os.path.join(self.ws, "projects", name)
        os.makedirs(root)
        git(root, "init", "--quiet", "-b", "main")
        write(
            os.path.join(root, ".ccnavi", "config", "project", "rules.yml"),
            json.dumps(SCHEMA_RULES),
        )
        write(os.path.join(root, "schema", "keep.sql"), "-- keep\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def record(self, side="yes", common=None, name=NAME, main=None):
        common = common or os.path.join(self.p, ".git")
        write(
            os.path.join(self.p, ".git", directrec.DIRECT_DIR, name),
            "\n".join(
                [
                    "version 4",
                    "base_branch main",
                    f"side_by_side {side}",
                    f"main_tree {main or self.p}",
                    f"common_dir {common}",
                    "",
                ]
            ),
        )

    def hook(self, cwd, restore="enable", event="PostToolUse", guard_core="disable"):
        body = {
            "hook_event_name": event,
            "tool_name": "Bash",
            "tool_input": {"command": "make"},
            "session_id": "s1",
            "cwd": cwd,
        }
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--mode",
                "enable",
                "--restore-if-deny",
                restore,
                "--guard-core-files",
                guard_core,
                "--guard-ticket-approval",
                "disable",
            ],
            input=json.dumps(body),
            cwd=ROOT,
            env=env,
        )

    def dirty(self, root):
        path = os.path.join(root, "schema", "keep.sql")
        write(path, "-- changed\n")
        return path

    def text(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    # 記録の読み取り

    def test_有効な記録を読む(self):
        self.record()
        main = tree.Tree("p", self.p, project="p", kind=tree.KIND_PROJECT)
        found = directrec.records_of(self.ws, main)
        self.assertEqual([r.name for r in found], [NAME])

    def test_無効な記録は読まない(self):
        main = tree.Tree("p", self.p, project="p", kind=tree.KIND_PROJECT)
        self.record(side="no")
        self.assertEqual(directrec.records_of(self.ws, main), [])
        self.record(common=os.path.join(self.q, ".git"))
        self.assertEqual(directrec.records_of(self.ws, main), [])
        self.record(main=self.q)
        self.assertEqual(directrec.records_of(self.ws, main), [])
        self.record()
        git(self.p, "worktree", "remove", "--force", self.wt)
        self.assertEqual(directrec.records_of(self.ws, main), [])

    def test_別のブランチを開いたワークツリーは無効(self):
        self.record()
        git(self.wt, "checkout", "--quiet", "-b", "other")
        main = tree.Tree("p", self.p, project="p", kind=tree.KIND_PROJECT)
        self.assertEqual(directrec.records_of(self.ws, main), [])

    # 実行後チェック

    def test_指定が有効なら戻さず出所不明として報告する(self):
        self.record()
        self.hook(self.wt)
        path = self.dirty(self.p)

        result = self.hook(self.wt)

        self.assertEqual(self.text(path), "-- changed\n", "戻していない")
        self.assertIn("出所不明の変更", result.stderr)
        self.assertIn("schema/keep.sql", result.stderr)
        self.assertNotIn("restored:", result.stderr)

    def test_作業ワークツリーで動いていてもユーザのチェックアウトを監視する(self):
        self.record()
        self.hook(self.wt)
        self.dirty(self.p)

        result = self.hook(self.wt, restore="disable")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("schema/keep.sql", result.stderr)

    def test_ワークスペース自身とqは従来どおり戻る(self):
        self.record()
        self.hook(self.wt)
        ws_path = self.dirty(self.ws)
        q_path = self.dirty(self.q)

        result = self.hook(self.ws)

        self.assertEqual(self.text(ws_path), "-- keep\n")
        self.assertEqual(self.text(q_path), "-- changed\n", "q は触ったツリーではないので見ない")
        self.assertNotIn("出所不明の変更", result.stderr)

        self.hook(self.q)
        q_path = self.dirty(self.q)
        self.hook(self.q)
        self.assertEqual(self.text(q_path), "-- keep\n")

    def test_指定が無ければ従来どおり戻る(self):
        self.record(side="no")
        self.hook(self.p)
        path = self.dirty(self.p)

        result = self.hook(self.p)

        self.assertEqual(self.text(path), "-- keep\n")
        self.assertNotIn("出所不明の変更", result.stderr)

    def test_ワークツリーを消したら記録が残っていても戻す側に戻る(self):
        self.record()
        git(self.p, "worktree", "remove", "--force", self.wt)
        self.hook(self.p)
        path = self.dirty(self.p)

        self.hook(self.p)

        self.assertEqual(self.text(path), "-- keep\n")

    def test_判定の記録に横で作業した件数が残る(self):
        self.record()
        self.hook(self.wt)
        self.dirty(self.p)
        self.hook(self.wt)

        with open(os.path.join(self.ws, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            last = [json.loads(line) for line in f if line.strip()][-1]
        self.assertIn("side-by-side 1", last.get("detail", ""))

    def test_同名の別ツリーが混ざっても報告と戻す判定が食い違わない(self):
        # q の下に p と同じ名前のワークツリーを切る。
        # 名前で突き合わせると、戻していない p のユーザのチェックアウトを戻したと言う。
        self.record()
        twin = os.path.join(self.ws, ".claude", "worktrees", "p")
        git(self.q, "worktree", "add", "--quiet", twin, "-b", "p")
        self.hook(self.wt)
        p_path = self.dirty(self.p)
        self.dirty(twin)

        result = self.hook(twin)

        self.assertEqual(self.text(p_path), "-- changed\n", "p のユーザのチェックアウトは戻さない")
        self.assertIn("出所不明の変更", result.stderr)

    def test_報告したあとに中身が変わったら言い直す(self):
        self.record()
        self.hook(self.wt)
        path = self.dirty(self.p)
        first = self.hook(self.wt)
        again = self.hook(self.wt)
        write(path, "-- changed again\n")
        changed = self.hook(self.wt)

        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(changed.returncode, 2, changed.stderr)
        self.assertIn("出所不明の変更", changed.stderr)

    def test_報告の文は戻す手順を並べない(self):
        self.record()
        self.hook(self.wt)
        self.dirty(self.p)

        result = self.hook(self.wt)

        self.assertIn("自分で戻さず", result.stderr)
        self.assertNotIn("undo:", result.stderr)

    def test_dry_runでは本番で戻す集合に横で作業する変更を入れない(self):
        self.record()
        self.hook(self.wt, restore="dry-run")
        self.dirty(self.p)

        result = self.hook(self.wt, restore="dry-run")

        self.assertIn("出所不明の変更", result.stderr)
        self.assertNotIn("would-restore", result.stderr)

    def test_ターンの終わりの報告にも出所不明と付く(self):
        self.record()
        self.hook(self.wt, event="UserPromptSubmit")
        self.dirty(self.p)

        result = self.hook(self.wt, event="Stop")

        self.assertIn("出所不明の変更", result.stderr + result.stdout)
        self.assertIn("schema/keep.sql", result.stderr + result.stdout)

    def test_自己防衛の対象は横で作業する間も戻す(self):
        self.record()
        self.hook(self.wt, guard_core="enable")
        rules_path = os.path.join(self.p, ".ccnavi", "config", "project", "rules.yml")
        before = self.text(rules_path)
        write(rules_path, json.dumps({"version": 1, "deny": [], "allow": []}))

        self.hook(self.wt, guard_core="enable")

        self.assertEqual(self.text(rules_path), before)


if __name__ == "__main__":
    unittest.main()
