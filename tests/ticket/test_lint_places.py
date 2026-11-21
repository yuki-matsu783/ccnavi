"""「複数の場所にある」の数え方の受入テスト（リポジトリをまたぐ側）。

同じ識別子が複数のツリーに在るのは普通の形。承認済みチケットは git に入れて共有するので、
切ったワークツリーの数だけ同じチケットができ、しかもツリーはそれぞれ別のコミットを指すから
状態も食い違う。そこを error にすると、ワークツリーを 2 本持つだけで `--lint` が常に
非ゼロで終わる。同じリポジトリの中での carry-over は `tests/ticket/test_ticket.py` が見る。

ここで見るのは逆側で、リポジトリをまたいだ衝突は免除してはいけないこと。
プロジェクトは自分の git を持つ。識別子はユーザが選ぶ短い連番なので、
プロジェクトが独立に振ればぶつかる。これは同じチケットではなく違うものなので、コミットの遅れでは
説明が付かない。ツリーごとの免除をここに適用すると、承認がどちらの実体のものか
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
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--rules",
                self.rules,
                "--projects",
                self.projects,
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
        """別々のプロジェクトが同じ識別子を使っていたら報告する。

        ツリーをまたぐ免除をここへ適用すると、状態が揃っているぶん「同じもののコピー」に
        見えてしまう。別のリポジトリなら同じチケットではないので、状態が同じでも違うもの。
        """
        self.project("app", "done")
        self.project("lib", "done")

        result = self.lint()

        self.assertIn("i0001 が複数のリポジトリにある", result.stdout)
        self.assertIn("app:done", result.stdout)
        self.assertIn("lib:done", result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_a_copy_in_another_projects_worktree_is_still_an_error(self):
        """衝突した片方がワークツリーの側にあっても、1 つにまとめて何も言わない形にしない。

        承認済みチケットは親のブランチに乗るので、プロジェクトのチケットは合流するまで
        親のワークツリーにしか無い。ワークツリーの名前は識別子と同じなので、ここで
        「正とするツリー」の規則を適用すると、もう片方のプロジェクトの実体が気づかないうちに消え、
        「複数のリポジトリにある」も出なくなる。違うものは 1 つにまとめない。
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


# `projects/` のぶつかりの知らせの先頭の句。VS Code 拡張（`projects-render.ts`）が
# この句で始まる `(projects)` の苦情を見分けてバナーに出す。`lint.py` の文面と揃える
# （設計 wip/design/i0064-fixed-places.md §4.2）。
TRACKED_LEAD = "`projects/` はワークスペースの git が追跡している"


class ProjectsCollisionTest(unittest.TestCase):
    """`projects/` の置き場がワークスペース自身のソースとぶつかったときの `--lint --json` の通し。

    ワークスペースの git が `projects/` の下のファイルを追跡していると、名前を逃がす手段が無い
    （置き場は固定）。`(projects)` の warn を 1 件だけ出す。場面の表は Go が持つ。
    """

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-collision-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        git(self.ws, "init", "--quiet", "-b", "main")
        write(
            os.path.join(self.ws, ".ccnavi", "config", "common", "rules.yml"),
            json.dumps({"version": 1}),
        )
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        self.projects = os.path.join(self.ws, "projects")

    def track(self, path="projects/foo.txt", ignore=""):
        """ワークスペースのソースとして `projects/` の下のファイルを追跡させる。"""
        write(os.path.join(self.ws, ".gitignore"), f"/.claude/\n{ignore}")
        write(os.path.join(self.ws, *path.split("/")), "ワークスペース自身のソース\n")
        # `-f`: `.gitignore` に入れた後でも追跡させる（追跡は無視の設定より先に決まる）。
        git(self.ws, "add", "-f", "--", ".gitignore", "src/keep.py", path)
        git(self.ws, "commit", "--quiet", "-m", "init")

    def lint(self):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.ws, "--lint", "--json", "--mode", "enable"],
            input="",
            env=environment,
        )
        try:
            return json.loads(result.stdout)["problems"]
        except (ValueError, KeyError) as exc:
            self.fail(f"--lint --json が読めない: {exc}\n{result.stdout}\n{result.stderr}")

    def about_projects(self):
        """`(projects)` の名前で出す苦情。プロジェクトごとの `(projects/<名前>)` は含めない。"""
        return [p for p in self.lint() if p["where"] == "(projects)"]

    # 置き場の苦情の場面（追跡・載せ忘れ・無視の有無・置き場の形・変な `--projects`）は、Go の見本
    # （go/internal/entry/lint/testdata/projects*.json）と
    # `places_test.go` の表が見る。ここは通しで 1 本。

    def test_a_tracked_file_is_named_without_a_project_and_even_when_ignored(self):
        """プロジェクトが 0 件でも、`.gitignore` に入っていても出す（追跡が先に決まる）。"""
        self.track()
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n/projects/\n")
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])


if __name__ == "__main__":
    unittest.main()
