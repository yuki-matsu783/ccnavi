"""元のプロジェクトが無いワークツリーを --lint と --explain が名指しすることの受入テスト。

元のプロジェクトが無いワークツリーとは、`.claude/worktrees/<名前>/.git` が `gitdir:` の行を持つ
ファイルで、元のプロジェクトを見つけられないもの。次の 2 つの形を試す。

- gitdir が指す先が無い（プロジェクトのディレクトリごと消した）
- 先は在るが、そこの `gitdir` ファイルが戻ってこない（相互参照が成り立たない）

見るのは外から観測できるものだけ。

- `--lint --json` の苦情（深刻度と文面）
- `--explain` の節

中のパスの判定と一覧の作り方は `go/internal/infra/tree` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import GIT_ENV, ROOT
from tests.inproc import run_ccnavi

HEADING = "■ 元のプロジェクトが無いワークツリー"

# 先が無い形、相互参照が成り立たない形、正常なワークツリー、ふつうのディレクトリ。
GONE = "gone-target"
BROKEN = "broken-backref"
NORMAL = "normal-tree"
PLAIN = "plain-dir"


def git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )


def init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    git(path, "init", "--quiet")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
        f.write("x\n")
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", "init")


def gitdir_of(worktree: str) -> str:
    """ワークツリーの `.git` ファイルに書かれた gitdir（書かれたとおり）。"""
    with open(os.path.join(worktree, ".git"), encoding="utf-8") as f:
        return f.read().strip()[len("gitdir:") :].strip()


def ccnavi(root: str, *args: str) -> subprocess.CompletedProcess:
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    return run_ccnavi(["--root", root, *args], input="", cwd=ROOT, env=environment)


def section(text: str) -> list[str]:
    """`--explain` の出力から、見出しの下の行（次の空行か次の見出しまで）。見出しが無ければ空。"""
    lines = text.splitlines()
    if HEADING not in lines:
        return []
    body = []
    for line in lines[lines.index(HEADING) + 1 :]:
        if not line.strip() or line.startswith("■"):
            break
        body.append(line)
    return body


class WorktreeWithoutProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = tempfile.mkdtemp(prefix="ccnavi-orphan-wt-")
        cls.addClassCleanup(shutil.rmtree, base, ignore_errors=True)
        cls.root = os.path.join(base, "ws")
        cls.projects = os.path.join(cls.root, "projects")
        init_repo(cls.root)
        wt = os.path.join(cls.root, ".claude", "worktrees")

        # 先が無い形: プロジェクトから切って、プロジェクトのディレクトリごと消す。
        gone_project = os.path.join(cls.projects, "gone")
        init_repo(gone_project)
        cls.gone_path = os.path.join(wt, GONE)
        git(gone_project, "worktree", "add", "--quiet", cls.gone_path, "-b", GONE)
        cls.gone_gitdir = gitdir_of(cls.gone_path)
        shutil.rmtree(gone_project)

        # 相互参照が成り立たない形: プロジェクトは在るが、戻りの `gitdir` ファイルが別を指す。
        broken_project = os.path.join(cls.projects, "broken")
        init_repo(broken_project)
        cls.broken_path = os.path.join(wt, BROKEN)
        git(broken_project, "worktree", "add", "--quiet", cls.broken_path, "-b", BROKEN)
        cls.broken_gitdir = gitdir_of(cls.broken_path)
        with open(os.path.join(cls.broken_gitdir, "gitdir"), "w", encoding="utf-8") as f:
            f.write(os.path.join(base, "elsewhere", ".git") + "\n")

        # 正常なワークツリーと、ふつうのディレクトリ。
        git(cls.root, "worktree", "add", "--quiet", os.path.join(wt, NORMAL), "-b", NORMAL)
        os.makedirs(os.path.join(wt, PLAIN, "src"))

    def lint_problems(self) -> list[dict]:
        result = ccnavi(self.root, "--lint", "--json", "--mode", "enable")
        return json.loads(result.stdout)["problems"]

    def mentions(self, name: str) -> list[dict]:
        # 直接作業の記録の検査（`(direct/…)`）は、元のプロジェクトの有無とは別の話なので数えない。
        return [
            p
            for p in self.lint_problems()
            if name in p["where"] + p["detail"] and not p["where"].startswith("(direct/")
        ]

    # ---- --lint

    def test_lint_先が無いワークツリーを名指しして_warnを1件出す(self):
        found = self.mentions(GONE)

        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")

    def test_lint_相互参照が成り立たないワークツリーも名指しして_warnを1件出す(self):
        found = self.mentions(BROKEN)

        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")

    def test_lint_文面に元のgitdirと影響と対処が入る(self):
        for name, gitdir in ((GONE, self.gone_gitdir), (BROKEN, self.broken_gitdir)):
            with self.subTest(name=name):
                found = self.mentions(name)
                self.assertEqual(len(found), 1, found)
                detail = found[0]["where"] + found[0]["detail"]
                self.assertIn(gitdir, detail)
                self.assertIn("元のプロジェクトがありません", detail)
                self.assertIn("deny が効いていない", detail)
                self.assertIn("ワークスペースルートとして判定", detail)
                self.assertIn("ワークツリーを削除するか、プロジェクトを戻", detail)
                self.assertNotIn("孤児", detail)

    def test_lint_正常なワークツリーとふつうのディレクトリは名指ししない(self):
        self.assertEqual(self.mentions(NORMAL), [])
        self.assertEqual(self.mentions(PLAIN), [])

    # ---- --explain

    def test_explain_見出し1つの節で同じ内容を1件1行で出す(self):
        result = ccnavi(self.root, "--explain", "--mode", "enable")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines().count(HEADING), 1, result.stdout)
        lines = section(result.stdout)
        self.assertEqual(len(lines), 2, lines)
        for name, gitdir in ((GONE, self.gone_gitdir), (BROKEN, self.broken_gitdir)):
            with self.subTest(name=name):
                (line,) = [x for x in lines if name in x]
                self.assertIn(gitdir, line)
                self.assertIn("deny が効いていない", line)
                self.assertIn("ワークツリーを削除するか、プロジェクトを戻", line)
                self.assertNotIn("孤児", line)
        self.assertFalse([x for x in lines if NORMAL in x or PLAIN in x], lines)

    def test_explain_json_に欄は足さない(self):
        result = ccnavi(self.root, "--explain", "--json", "--mode", "enable")

        self.assertNotIn(GONE, result.stdout)
        self.assertNotIn("元のプロジェクトがありません", result.stdout)

    # ---- 該当が無いとき

    def test_該当が無ければlintもexplainも何も出さない(self):
        with tempfile.TemporaryDirectory() as base:
            root = os.path.join(base, "ws")
            init_repo(root)
            git(
                root, "worktree", "add", "--quiet", os.path.join(root, ".claude", "worktrees", "ok")
            )
            os.makedirs(os.path.join(root, ".claude", "worktrees", "plain"))

            problems = json.loads(ccnavi(root, "--lint", "--json", "--mode", "enable").stdout)
            explained = ccnavi(root, "--explain", "--mode", "enable").stdout

        said = [p for p in problems["problems"] if "元のプロジェクトがありません" in p["detail"]]
        self.assertEqual(said, [])
        self.assertNotIn(HEADING, explained)


if __name__ == "__main__":
    unittest.main()
