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
# 既存の「無視されていない」の文面の頭。
NOT_IGNORED = "projects/ がワークスペースの git で無視されていない"


class ProjectsCollisionTest(unittest.TestCase):
    """`projects/` の置き場がワークスペース自身のソースとぶつかったときの `--lint`（A5・A6）。

    ワークスペースの git が `projects/` の下のファイルを追跡していると、名前を逃がす手段が
    無い（置き場は固定）。`.gitignore` に `/projects/` を足すとソースが追跡から
    外れるので、「無視されていない」の案内は誤りになる。代わりに `(projects)` の warn を 1 件だけ
    出す。

    | 追跡がある | プロジェクトがある | 無視されていない | 出すもの |
    |---|---|---|---|
    | はい | どちらでも | どちらでも | ぶつかりの warn だけ（A5） |
    | いいえ | はい | はい | 既存の「無視されていない」（A6） |
    | いいえ | はい | いいえ | 何も言わない |
    | いいえ | いいえ（`projects/` は在る） | はい | 何も言わない |
    | いいえ | いいえ（`projects/` は在る） | いいえ | 既存の「無視されていない」 |
    | いいえ | いいえ（`projects/` が無い） | どちらでも | 何も言わない |

    フラグ（`--projects` など）は渡さず、`--root` の下の既定の置き場を見る。
    A6 は今どおりで緑（回帰の見張り）。
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

    # 追跡があるときのぶつかりの warn 1 件・「無視されていない」を出さないこと・プロジェクトごとの
    # 検査が続くこと・追跡が無くてプロジェクトがあり無視されていないときの案内は、Go の見本
    # （go/internal/entry/lint/testdata/projects*.json）が見る。

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

    def test_what_is_said_about_a_projects_place_nothing_tracks(self):
        """追跡が無いとき。`projects/` の有無・空・リンクと無視の有無で、言うかが決まる。"""
        self.no_tracking()
        self.assertFalse(os.path.exists(self.projects))
        self.assertEqual(self.about_projects(), [])
        # 在るが空で、無視されていなければ言う
        os.makedirs(self.projects)
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")
        self.assertIn(NOT_IGNORED, found[0]["detail"])
        # シンボリックリンクでも言う（`projects/` で問うと git が rc=128 で断る）
        os.rmdir(self.projects)
        target = tempfile.mkdtemp(prefix="ccnavi-linked-")
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        try:
            os.symlink(target, self.projects, target_is_directory=True)
        except (OSError, NotImplementedError):
            os.makedirs(self.projects)
        else:
            found = self.about_projects()
            self.assertEqual(len(found), 1, found)
            self.assertIn(NOT_IGNORED, found[0]["detail"])
            os.remove(self.projects)
            os.makedirs(self.projects)
        # 無視されていれば、空でもプロジェクトがあっても何も言わない
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n/projects/\n")
        self.assertEqual(self.about_projects(), [])
        self.project()
        self.assertEqual(self.about_projects(), [])

    def test_odd_projects_flags_do_not_crash_the_lint(self):
        """`--projects ""` と、ワークスペースの外を指す `--projects`（Windows では別のドライブも
        ありうる）でも、`--lint` は報告を出し、置き場の苦情は出さない。"""
        self.no_tracking()
        elsewhere = tempfile.mkdtemp(prefix="ccnavi-drive-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        for value in ("", elsewhere):
            result = run_ccnavi(
                ["--root", self.ws, "--projects", value, "--lint", "--json", "--mode", "enable"],
                input="",
                env=environment,
            )
            # 終了コードは見ない（この部屋の rules.yml は空で、`deny` が空の error が出る）。
            try:
                problems = json.loads(result.stdout)["problems"]
            except (ValueError, KeyError) as exc:
                self.fail(
                    f"--lint が報告を出さずに異常終了した: {exc}\n{result.stdout}\n{result.stderr}"
                )
            self.assertEqual([p for p in problems if p["where"] == "(projects)"], [])


# 載せ忘れの文面で、先頭の句の直後に来る句。ぶつかりとの見分けに使う（設計 §4.2）。
FORGOT_NEXT = "（入れ子のリポジトリとして"


class ProjectsAddedByMistakeTest(unittest.TestCase):
    """索引の `projects/` の下が gitlink だけのとき（載せ忘れ）の `--lint`（A5b・A5c）。

    `.gitignore` に `/projects/` を入れ忘れたまま `git add -A` した人の索引には、
    `projects/<名前>` が gitlink（mode 160000）で載る。ワークスペース自身のソースが
    `projects/` にあるわけではないので、改名ではなく「索引から外して無視に入れる」を案内する。
    通常のファイルもあるときは、ぶつかりとして改名を案内し、gitlink を名指しする（A5c）。

    gitlink は人が踏むのと同じ手で作る: `projects/<名前>` で `git init` して 1 回コミットし、
    ワークスペースで `-f` を付けずに `git add -A`（設計 §6）。

    ワークスペースの作りと `--lint` の読み方は A5 と同じものを借りる（継ぐと A5 のテストまで
    ここで走り直す）。
    """

    setUp = ProjectsCollisionTest.setUp
    project = ProjectsCollisionTest.project
    lint = ProjectsCollisionTest.lint
    about_projects = ProjectsCollisionTest.about_projects

    def forget(self, *names, commit=True, ignore_after=False):
        """入れ子のリポジトリを置き、`.gitignore` に入れないまま `git add -A` する。"""
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n")
        for name in names or ("lib",):
            self.project(name)
        git(self.ws, "add", "-A")
        if commit:
            git(self.ws, "commit", "--quiet", "-m", "init")
        if ignore_after:
            # 後から `.gitignore` に足しても、索引に載ったものには効かない。
            write(os.path.join(self.ws, ".gitignore"), "/.claude/\n/projects/\n")

    def indexed(self):
        return git(self.ws, "ls-files", "-s", "--", "projects/")

    # ---- A5b. gitlink だけ

    def test_a_gitlink_alone_is_named_as_added_by_mistake(self):
        # コミット前でも言う。名前に空白があっても、`git rm -r --cached projects` は囲まない
        self.forget("my lib", commit=False)
        self.assertIn("160000", self.indexed())
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["severity"], "warn")
        detail = found[0]["detail"]
        self.assertTrue(detail.startswith(TRACKED_LEAD + FORGOT_NEXT), detail)
        self.assertIn("`projects/my lib`", detail)
        self.assertIn("`git rm -r --cached projects`", detail)
        self.assertIn("git reset -- projects", detail)
        self.assertIn("/projects/", detail)
        self.assertNotIn("git mv", detail)
        self.assertNotIn("ほか", detail)
        # 「無視されていない」は出さない（その案内は誤り）
        details = [p["detail"] for p in self.lint()]
        self.assertFalse(any(NOT_IGNORED in d for d in details), details)
        # 2 つめの gitlink は、最初のものを名指しして残りを数える
        self.project("app")
        git(self.ws, "add", "-A")
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        self.assertIn("`projects/app` ほか 1 件", found[0]["detail"])
        git(self.ws, "commit", "--quiet", "-m", "init")
        # 後から `.gitignore` に足しても、入れ子のリポジトリが消えても、索引に載っている限り言う
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n/projects/\n")
        for name in ("my lib", "app"):
            os.replace(os.path.join(self.projects, name), os.path.join(self.ws, "moved-" + name))
            found = self.about_projects()
            self.assertEqual(len(found), 1, found)
            self.assertTrue(found[0]["detail"].startswith(TRACKED_LEAD + FORGOT_NEXT))

    def test_a_project_with_a_claude_dir_is_not_warned_but_a_worktree_inside_is(self):
        self.forget()
        os.makedirs(os.path.join(self.projects, "lib", ".claude"))
        details = [p["detail"] for p in self.lint() if p["where"] == "(projects/lib)"]
        self.assertEqual(details, [])
        os.makedirs(os.path.join(self.projects, "lib", ".claude", "worktrees", "feature-1-memo"))
        details = [p["detail"] for p in self.lint() if p["where"] == "(projects/lib)"]
        self.assertTrue(
            any("プロジェクトの中にワークツリーがある（feature-1-memo）" in d for d in details),
            details,
        )

    # ---- A5c. 通常のファイルと gitlink の両方

    def test_a_file_and_gitlinks_are_named_once_as_a_collision(self):
        write(os.path.join(self.ws, "projects", "foo.txt"), "ワークスペース自身のソース\n")
        self.forget("my lib", "app")
        self.assertIn("160000", self.indexed())
        self.assertIn("projects/foo.txt", self.indexed())
        found = self.about_projects()
        self.assertEqual(len(found), 1, found)
        detail = found[0]["detail"]
        self.assertTrue(detail.startswith(TRACKED_LEAD), detail)
        self.assertFalse(detail.startswith(TRACKED_LEAD + FORGOT_NEXT), detail)
        self.assertIn("git mv projects apps", detail)
        self.assertNotIn("git rm -r --cached projects", detail)
        # `git rm --cached` に載せるパスは、空白を含むときだけ囲む。名指しのほうは囲まない
        self.assertIn("`projects/app`、`projects/my lib`", detail)
        self.assertIn("`git rm --cached projects/app 'projects/my lib'`", detail)
        details = [p["detail"] for p in self.lint()]
        self.assertFalse(any(NOT_IGNORED in d for d in details), details)


if __name__ == "__main__":
    unittest.main()
