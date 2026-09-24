"""「複数の場所にある」の数え方の受入テスト（リポジトリをまたぐ側）。

同じ識別子が複数のツリーに在るのは普通の形。承認済みチケットは git に入れて運ぶので、
切ったワークツリーの数だけ写しができ、しかもツリーはそれぞれ別のコミットを指すから
状態も食い違う。そこを咎めると、ワークツリーを 2 本持つだけで `--lint` が常に
非ゼロで終わる。同じリポジトリの中での carry-over は `tests/ticket/test_ticket.py` が見る。

ここで見るのは逆側で、**リポジトリをまたいだ衝突は免除してはいけない**こと。
プロジェクトは自分の git を持つ（設計 §11）。識別子は人が選ぶ短い連番なので、
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


# `projects/` のぶつかりの知らせの先頭の句。VS Code 拡張（`projects-render.ts`）が
# この句で始まる `(projects)` の苦情を見分けてバナーに出す。`lint.py` の文面と揃える
# （設計 wip/design/i0064-fixed-places.md §4.2）。
TRACKED_LEAD = "`projects/` はワークスペースの git が追跡している"
# 既存の「無視されていない」の文面の頭。
NOT_IGNORED = "projects/ がワークスペースの git で無視されていない"


class ProjectsCollisionTest(unittest.TestCase):
    """`projects/` の置き場がワークスペース自身のソースとぶつかったときの `--lint`（A5・A6）。

    ワークスペースの git が `projects/` の下のファイルを追跡していると、名前を逃がす手段が
    無い（置き場は固定。ADR-0084）。`.gitignore` に `/projects/` を足すとソースが追跡から
    外れるので、「無視されていない」の案内は誤りになる。代わりに `(projects)` の warn を 1 件だけ
    出す。

    | 追跡がある | プロジェクトがある | 無視されていない | 出すもの |
    |---|---|---|---|
    | はい | どちらでも | どちらでも | ぶつかりの warn だけ（A5） |
    | いいえ | はい | はい | 既存の「無視されていない」（A6） |
    | いいえ | はい | いいえ | 何も言わない |
    | いいえ | いいえ | — | 何も言わない |

    フラグ（`--projects` など）は渡さず、`--root` の下の既定の置き場を見る。
    A5 は実装前は赤（ぶつかりの知らせがまだ無い）。A6 は今どおりで緑（回帰の見張り）。
    """

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-collision-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        git(self.ws, "init", "--quiet", "-b", "main")
        write(os.path.join(self.ws, ".ccnavi", "common", "rules.yml"), json.dumps({"version": 1}))
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        self.projects = os.path.join(self.ws, "projects")

    def track(self, path="projects/foo.txt", ignore=""):
        """ワークスペースのソースとして `projects/` の下のファイルを追跡させる。"""
        write(os.path.join(self.ws, ".gitignore"), f"/.claude/\n{ignore}")
        write(os.path.join(self.ws, *path.split("/")), "ワークスペース自身のソース\n")
        # `-f`: `.gitignore` に入れた後でも追跡させる（追跡は無視の設定より先に決まる）。
        git(self.ws, "add", "-f", "--", ".gitignore", "src/keep.py", path)
        git(self.ws, "commit", "--quiet", "-m", "init")

    def no_tracking(self, ignore=""):
        write(os.path.join(self.ws, ".gitignore"), f"/.claude/\n{ignore}")
        git(self.ws, "add", "--", ".gitignore", "src/keep.py")
        git(self.ws, "commit", "--quiet", "-m", "init")

    def project(self, name="lib"):
        """`projects/<名前>/` に自分の git を持つプロジェクトを 1 つ置く（追跡はさせない）。"""
        root = os.path.join(self.projects, name)
        write(os.path.join(root, "src", "keep.py"), "print(1)\n")
        git(root, "init", "--quiet", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def lint(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
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
        """`(projects)` の名札の苦情。プロジェクトごとの `(projects/<名前>)` は含めない。"""
        return [p for p in self.lint() if p["where"] == "(projects)"]

    # ---- A5. 追跡があるとき

    def test_a_tracked_file_under_projects_is_named_once_with_the_lead_phrase(self):
        """追跡があれば、先頭の句つきの warn を 1 件だけ出す。例のファイルも添える。"""
        self.track()
        self.project()

        found = self.about_projects()

        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")
        self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])
        self.assertIn("projects/foo.txt", found[0]["detail"])

    def test_the_collision_replaces_the_not_ignored_warning(self):
        """「無視されていない」は出さない。この場合、その案内（`.gitignore` に入れる）は誤り。"""
        self.track()
        self.project()

        details = [p["detail"] for p in self.lint()]

        self.assertFalse(any(NOT_IGNORED in d for d in details), details)

    def test_the_collision_is_named_even_without_any_project(self):
        """プロジェクトが 0 件でも出す。これからプロジェクトを置こうとした人に要る知らせ。"""
        self.track()

        found = self.about_projects()

        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])

    def test_the_collision_is_named_when_projects_is_ignored_too(self):
        """追跡されているものは `.gitignore` に入っていても追跡のまま。無視の有無は問わない。"""
        self.track(ignore="/projects/\n")

        found = self.about_projects()

        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])

    def test_the_other_project_checks_still_run_beside_the_collision(self):
        """予約名と `.claude/` の検査（各プロジェクトごと）は、ぶつかりがあっても今どおり出す。"""
        self.track()
        lib = self.project("lib")
        os.makedirs(os.path.join(lib, ".claude"))

        details = [p["detail"] for p in self.lint() if p["where"] == "(projects/lib)"]

        self.assertTrue(any(".claude/ を持つ" in d for d in details), details)

    # ---- A6. 追跡が無いとき（今どおり）

    def test_no_tracking_and_not_ignored_still_says_not_ignored(self):
        """追跡が無く、プロジェクトがあり、無視されていなければ、今どおり「無視されていない」。"""
        self.no_tracking()
        self.project()

        found = self.about_projects()

        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")
        self.assertIn(NOT_IGNORED, found[0]["detail"])
        self.assertFalse(found[0]["detail"].startswith(TRACKED_LEAD), found[0]["detail"])

    def test_no_tracking_and_ignored_says_nothing_about_projects(self):
        self.no_tracking(ignore="/projects/\n")
        self.project()

        self.assertEqual(self.about_projects(), [])

    def test_no_tracking_and_no_project_says_nothing_about_projects(self):
        self.no_tracking()

        self.assertEqual(self.about_projects(), [])


if __name__ == "__main__":
    unittest.main()
