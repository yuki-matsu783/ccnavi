"""「複数の場所にある」の数え方の受入テスト（リポジトリをまたぐ側）。

同じ識別子が複数のツリーに在るのは普通の形。承認済みチケットは git に入れて運ぶので、
切ったワークツリーの数だけ写しができ、しかもツリーはそれぞれ別のコミットを指すから
状態も食い違う。そこを咎めると、ワークツリーを 2 本持つだけで `--lint` が常に
非ゼロで終わる。同じリポジトリの中での carry-over は `tests/ticket/test_ticket.py` が見る。

ここで見るのは逆側で、**リポジトリをまたいだ衝突は免除してはいけない**こと。
プロジェクトは自分の git を持つ（設計 11）。識別子は人が選ぶ短い連番なので、
プロジェクトが独立に振ればぶつかる。これは写しではなく別物なので、コミットの遅れでは
説明が付かない。ツリーごとの免除をここに当てると、承認がどちらの実体のものか
分からないまま誰も気づかなくなる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

# 承認済みチケット 1 枚。`ccnavi_approved` が無いと承認済みチケットとして読まれない。
TICKET = (
    "---\n"
    "version: 1\n"
    "ticket: i0001\n"
    "title: {title}\n"
    'ccnavi_approved: {{approved_at: "2026-01-01T00:00:00Z", source_tree: "", source_path: p}}\n'
    "{extra}"
    "allow:\n"
    '- {{glob: "src/*", match: "Write|Edit"}}\n'
    "---\n"
)


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


class CrossRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-places-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        git(self.ws, "init", "--quiet", "-b", "main")
        write(os.path.join(self.ws, ".gitignore"), "/projects/\n/.claude/\n")
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "--quiet", "-m", "init")
        self.rules = write(os.path.join(self.ws, "rules.yml"), json.dumps({"version": 1}))
        self.projects = os.path.join(self.ws, "projects")

    def project(self, name, state, *, extra=""):
        """プロジェクトを 1 つ置く。自分の git を持ち、承認済みチケットを 1 枚抱える。"""
        root = os.path.join(self.projects, name)
        os.makedirs(root)
        git(root, "init", "--quiet", "-b", "main")
        write(os.path.join(root, "src", "keep.py"), "print(1)\n")
        write(
            os.path.join(root, ".ccnavi", "approved", state, "i0001.md"),
            TICKET.format(title=f"{name} のチケット", extra=extra),
        )
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def lint(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--rules",
                self.rules,
                "--projects",
                self.projects,
                "--approved",
                ".ccnavi/approved",
                "--state",
                os.path.join(self.ws, "state"),
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                "--lint",
                "--mode",
                "enable",
            ],
            input="",
            env=environment,
        )

    def test_the_same_id_in_two_projects_is_an_error(self):
        """別々のプロジェクトが同じ識別子を使っていたら咎める。

        ツリーをまたぐ免除をここへ当てると、状態が揃っているぶん「同じものの写し」に
        見えてしまう。別のリポジトリなら写しではないので、状態が同じでも別物。
        """
        self.project("app", "done")
        self.project("lib", "done")

        result = self.lint()

        self.assertIn("i0001 が複数のリポジトリにある", result.stdout)
        self.assertIn("app:done", result.stdout)
        self.assertIn("lib:done", result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_copy_in_another_projects_worktree_is_still_an_error(self):
        """衝突した片方がワークツリーの側にあっても、畳んで黙らせない。

        承認済みチケットは親のブランチに乗るので、プロジェクトのチケットは合流するまで
        親のワークツリーにしか無い。ワークツリーの名前は識別子と同じなので、ここで
        「権威のツリー」の規則を当てると、もう片方のプロジェクトの実体が黙って消え、
        「複数のリポジトリにある」も出なくなる。別物は畳まない。
        """
        self.project("app", "doing")
        lib = self.project("lib", "doing")
        # lib の側は、親のワークツリーにだけ在る形にする（合流前）。
        os.remove(os.path.join(lib, ".ccnavi", "approved", "doing", "i0001.md"))
        git(lib, "add", "-A")
        git(lib, "commit", "--quiet", "-m", "not merged yet")
        worktree = os.path.join(self.ws, ".claude", "worktrees", "i0001")
        git(lib, "worktree", "add", "--quiet", worktree, "-b", "i0001", "HEAD~1")

        result = self.lint()

        self.assertIn("i0001 が複数のリポジトリにある", result.stdout)
        self.assertIn("app:doing", result.stdout)
        self.assertIn("lib/i0001:doing", result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_one_project_alone_is_not_an_error(self):
        """1 つのプロジェクトに 1 枚在るだけの、いちばん普通の形。"""
        self.project("app", "done")

        result = self.lint()

        self.assertNotIn("複数のリポジトリにある", result.stdout)
        self.assertNotIn("複数の場所にある", result.stdout)


if __name__ == "__main__":
    unittest.main()
