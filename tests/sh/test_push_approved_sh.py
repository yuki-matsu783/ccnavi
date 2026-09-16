"""ccnavi-push-approved.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から叩く。

設計 wip/design/approve-carry.md §1 と §6.3。確かめるのは次のとおり。

17. 置き場（`.ccnavi/tickets`）の変更だけをコミットし、同じツリーの他の未コミットは運ばない
18. 運ぶものが無ければ 0 で `運ぶ承認済みチケットは無い。`
19. `main` の上のツリーはコミットして push しない（0、標準エラーに綴り）
20. push が落ちると 1、コミットは残る
21. detached のツリーは飛ばす
22. `ccnavi-approve.sh` が承認のあと運ぶ
23. `ccnavi-approve.sh` は並べた識別子を `--approve` の後ろに渡し、`-` で始まる語と空の語は断る

ワークスペースは一時ディレクトリに git と bare のリモートで作る。承認そのものは
ccnavi の実行ファイルの代わりに、承認済みチケットを 1 枚置くだけの sh（stub）で済ませる。
承認の中身は他のテストが見ていて、ここで見るのは sh のつなぎ方だけ。

読み返すのは終了コード・出力と、git に残ったものだけ。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
PUSH_SCRIPTS = ("ccnavi-push-approved.sh", "ccnavi-common.sh")
APPROVE_SCRIPTS = (*PUSH_SCRIPTS, "ccnavi-approve.sh")
APPROVED = ".ccnavi/tickets"
MESSAGE = "ccnavi: 承認済みチケットを更新"
NOTHING = "運ぶ承認済みチケットは無い。"

# 承認の代わり。STUB_ARGS があれば受けた引数を 1 行ずつ書き、STUB_EXIT が 0 でなければ落ち、
# STUB_TREE があればそこに承認済みチケットを置く。
STUB = """#!/bin/sh
[ -z "${STUB_ARGS:-}" ] || printf '%s\\n' "$@" > "$STUB_ARGS"
[ "${STUB_EXIT:-0}" = 0 ] || exit "$STUB_EXIT"
[ -n "${STUB_TREE:-}" ] || exit 0
mkdir -p "$STUB_TREE/.ccnavi/tickets"
printf 'approved\\n' > "$STUB_TREE/.ccnavi/tickets/i0001.md"
"""


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args, check=True):
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done


def posix(path):
    return path.replace(os.sep, "/")


class Workspace(unittest.TestCase):
    """使い捨てのワークスペースと bare のリモート。テストは持たない（下の 2 つが継いで使う）。"""

    scripts = PUSH_SCRIPTS

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in self.scripts:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        git(self._tmp.name, "init", "-q", "-b", "main", self.ws)
        for key, value in (
            ("user.email", "t@example.invalid"),
            ("user.name", "t"),
            ("commit.gpgsign", "false"),
        ):
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), ".claude/worktrees/\n")
        write(os.path.join(self.ws, "README.md"), "seed\n")
        git(self.ws, "add", "--", ".gitignore", "README.md", ".ccnavi/scripts")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(self._tmp.name, "origin.git")
        git(self._tmp.name, "init", "-q", "--bare", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)

    # ---- 道具

    def worktree(self, name, detach=False):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        if detach:
            git(self.ws, "worktree", "add", "-q", "--detach", path, "main")
        else:
            git(self.ws, "worktree", "add", "-q", path, "-b", name, "main")
        return path

    def place(self, tree, ticket="i0001"):
        return write(os.path.join(tree, *APPROVED.split("/"), ticket + ".md"), "approved\n")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.update(extra)
        return env

    def run_sh(self, name, *args, cwd=None, env=None):
        script = posix(os.path.join(self.ws, ".ccnavi", "scripts", name))
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env or self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def push(self, *args, cwd=None, env=None):
        return self.run_sh("ccnavi-push-approved.sh", *args, cwd=cwd, env=env)

    def repository(self, path, branch):
        """ワークスペースの下に別のリポジトリを置く。bare のリモートを付けて返す。"""
        git(self._tmp.name, "init", "-q", "-b", "main", path)
        for key, value in (
            ("user.email", "t@example.invalid"),
            ("user.name", "t"),
            ("commit.gpgsign", "false"),
        ):
            git(path, "config", key, value)
        write(os.path.join(path, "README.md"), "project\n")
        git(path, "add", "--", "README.md")
        git(path, "commit", "-q", "-m", "seed")
        git(path, "checkout", "-q", "-b", branch)
        remote = os.path.join(self._tmp.name, os.path.basename(path) + ".git")
        git(self._tmp.name, "init", "-q", "--bare", remote)
        git(path, "remote", "add", "origin", remote)
        return remote

    def head_of(self, remote, branch):
        done = git(
            self._tmp.name,
            "--git-dir",
            remote,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/heads/{branch}",
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    def staged(self, tree):
        return git(tree, "diff", "--cached", "--name-only").stdout.strip()

    def head(self, tree):
        return git(tree, "rev-parse", "HEAD").stdout.strip()

    def subject(self, tree):
        return git(tree, "log", "-1", "--format=%s").stdout.strip()

    def committed(self, tree):
        out = git(tree, "show", "--name-only", "--format=", "HEAD").stdout
        return sorted(line for line in out.splitlines() if line)

    def dirty(self, tree, *paths):
        return git(tree, "status", "--porcelain", "--", *paths).stdout.strip()

    def remote_head(self, branch):
        done = git(
            self._tmp.name,
            "--git-dir",
            self.remote,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/heads/{branch}",
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class PushApprovedTest(Workspace):
    # ---- 17. 置き場だけを運ぶ

    def test_commits_only_the_approved_place_and_pushes(self):
        tree = self.worktree("i0001")
        self.place(tree)
        # 同じツリーの書きかけ。追跡しているファイルの変更と、未追跡のファイル。
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        write(os.path.join(tree, "src", "draft.py"), "x\n")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertTrue(self.dirty(tree, "README.md"))
        self.assertTrue(self.dirty(tree, "src"))
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertIn("i0001", result.stdout)
        self.assertNotIn(NOTHING, result.stdout)

    def test_does_not_carry_changes_someone_else_staged(self):
        """先にステージされていた他人の変更も運ばない。パスを限るのはコミットまで。"""
        tree = self.worktree("i0001")
        self.place(tree)
        write(os.path.join(tree, "README.md"), "ステージ済みの書きかけ\n")
        git(tree, "add", "--", "README.md")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertTrue(self.dirty(tree, "README.md"))

    # ---- 18. 運ぶものが無い

    def test_nothing_to_carry_says_so(self):
        tree = self.worktree("i0001")
        # 置き場の外の書きかけは運ぶものに数えない。
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        before = self.head(tree)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)
        self.assertEqual(self.head(tree), before)
        self.assertEqual(self.remote_head("i0001"), "")

    # ---- 19. 保護されたブランチ

    def test_main_is_committed_but_not_pushed(self):
        self.place(self.ws)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(self.ws), MESSAGE)
        self.assertEqual(self.committed(self.ws), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("main"), "")
        self.assertIn("main", result.stderr)

    # ---- 20. push が落ちる

    def test_failed_push_exits_1_and_keeps_the_commit(self):
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        tree = self.worktree("i0001")
        self.place(tree)
        result = self.push()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.dirty(tree, APPROVED), "")
        self.assertIn("i0001", result.stderr)

    # ---- 21. detached

    def test_detached_tree_is_skipped_and_others_are_carried(self):
        loose = self.worktree("loose", detach=True)
        self.place(loose)
        before = self.head(loose)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")

        result = self.push()
        # 飛ばしたツリーがあるときの終了コードは設計（§1.3）で決まっていないので見ない。
        self.assertIn("loose", result.stderr)
        self.assertEqual(self.head(loose), before)
        self.assertTrue(self.dirty(loose, APPROVED))
        # 飛ばしても、他のツリーは運ぶ。
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    # ---- チケット approve-carry-04 の 11〜14

    def test_carries_a_tree_under_projects(self):
        """11. `projects/<名前>/` の下のツリーの置き場も、コミットして push する。"""
        project = os.path.join(self.ws, "projects", "app")
        remote = self.repository(project, "work")
        self.place(project)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(project), MESSAGE)
        self.assertEqual(self.committed(project), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.head_of(remote, "work"), self.head(project))
        self.assertNotIn(NOTHING, result.stdout)

    def test_carries_the_place_named_by_ccnavi_approved(self):
        """12. `CCNAVI_TICKETS_APPROVED` を既定と違う綴りにすると、その置き場を運ぶ。

        既定の置き場（`.ccnavi/tickets`）は運ばない。環境変数の名前は `ccnavi/settings.py` の
        `APPROVED_ENV` と同じ（チケット approve-carry-05 の 6）。
        """
        other = "approved/tickets"
        tree = self.worktree("i0001")
        write(os.path.join(tree, *other.split("/"), "i0001.md"), "approved\n")
        self.place(tree, "i0002")

        result = self.push(env=self.env(CCNAVI_TICKETS_APPROVED=other))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{other}/i0001.md"])
        self.assertTrue(self.dirty(tree, APPROVED))
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    # ---- チケット approve-carry-05 の 7・8

    def said(self, result, name):
        """sh 自身が標準エラーに name を名指ししたか。git のエラー文に紛れた綴りは数えない。"""
        return any(
            line.startswith("ccnavi-push-approved:") and name in line
            for line in result.stderr.splitlines()
        )

    def test_a_failed_add_in_one_tree_does_not_stop_the_others(self):
        """7. 1 本のツリーで `git add` が落ちても、もう 1 本は運ぶ。

        終了コードは 1 で、落ちたツリーを標準エラーで名指しする。
        """
        locked = self.worktree("locked")
        self.place(locked)
        before = self.head(locked)
        # そのツリーのインデックスを他のプロセスが握っている形。`git add` が落ちる。
        gitdir = git(locked, "rev-parse", "--absolute-git-dir").stdout.strip()
        write(os.path.join(gitdir, "index.lock"))
        tree = self.worktree("i0002")
        self.place(tree, "i0002")

        result = self.push()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.said(result, "locked"), result.stderr)
        self.assertEqual(self.head(locked), before)
        self.assertEqual(self.remote_head("locked"), "")
        # 落ちたツリーのあとでも、他のツリーは運ぶ。
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0002.md"])
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    def test_a_symlink_under_worktrees_is_not_followed(self):
        """8. `.claude/worktrees/` の下のシンボリックリンクは辿らない。標準エラーに言う。

        リンク先はワークスペースの外のリポジトリ。本物の作業ツリーは運ぶ。
        """
        outside = os.path.join(self._tmp.name, "outside")
        outside_remote = self.repository(outside, "work")
        self.place(outside)
        before = self.head(outside)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")
        link = os.path.join(self.ws, ".claude", "worktrees", "linked")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"シンボリックリンクが作れない: {error}")

        result = self.push()
        # 飛ばしたリンクがあるときの終了コードはチケットで決まっていないので見ない。
        self.assertTrue(self.said(result, "linked"), result.stderr)
        self.assertEqual(self.head(outside), before)
        self.assertTrue(self.dirty(outside, APPROVED))
        self.assertEqual(self.head_of(outside_remote, "work"), "")
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    # ---- チケット approve-carry-07（3 回目の敵対的レビュー）

    def symlink_or_skip(self, target, link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"シンボリックリンクが作れない: {error}")

    def outside_repository(self, holder):
        """ワークスペースの外の holder/app にリポジトリを置き、承認済みチケットを 1 枚置く。"""
        app = os.path.join(self._tmp.name, holder, "app")
        remote = self.repository(app, "work")
        self.place(app)
        return app, remote

    def assert_not_carried(self, app, remote, before):
        self.assertEqual(self.head(app), before)
        self.assertTrue(self.dirty(app, APPROVED))
        self.assertEqual(self.head_of(remote, "work"), "")

    def test_projects_itself_as_a_symlink_is_not_followed(self):
        """`projects/` そのものがシンボリックリンクなら、その下のリポジトリにコミットしない。

        リンク先の中の 1 件ずつはリンクではないので、置き場の段で確かめないと辿ってしまう。
        飛ばしたことは標準エラーに言う。本物の作業ツリーは運ぶ。
        """
        app, remote = self.outside_repository("elsewhere-projects")
        before = self.head(app)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")
        self.symlink_or_skip(os.path.dirname(app), os.path.join(self.ws, "projects"))

        result = self.push()
        self.assertTrue(self.said(result, "projects"), result.stderr)
        self.assert_not_carried(app, remote, before)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    def test_worktrees_itself_as_a_symlink_is_not_followed(self):
        """`.claude/worktrees/` そのものがシンボリックリンクなら、その下にコミットしない。"""
        app, remote = self.outside_repository("elsewhere-worktrees")
        before = self.head(app)
        link = os.path.join(self.ws, ".claude", "worktrees")
        self.symlink_or_skip(os.path.dirname(app), link)

        result = self.push()
        self.assertTrue(self.said(result, "worktrees"), result.stderr)
        self.assert_not_carried(app, remote, before)

    def test_projects_that_names_the_workspace_root_falls_back_to_the_default(self):
        """`CCNAVI_PROJECTS=/` は末尾の `/` を落とすと空になり、ルートの直下を全部数えることになる。

        既定の `projects` に戻すので、ルートの直下に置いた別のリポジトリは運ばない。
        本物の作業ツリーは運ぶ。
        """
        app = os.path.join(self.ws, "stray")
        remote = self.repository(app, "work")
        self.place(app)
        before = self.head(app)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")

        result = self.push(env=self.env(CCNAVI_PROJECTS="/"))
        self.assert_not_carried(app, remote, before)
        self.assertEqual(self.subject(tree), MESSAGE, result.stderr)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    def test_approved_place_that_names_the_tree_root_falls_back_to_the_default(self):
        """`CCNAVI_TICKETS_APPROVED=.` はツリー全体を指す。

        既定の置き場に戻し、書きかけは運ばない。
        """
        tree = self.worktree("i0001")
        self.place(tree)
        write(os.path.join(tree, "README.md"), "書きかけ\n")

        result = self.push(env=self.env(CCNAVI_TICKETS_APPROVED="."))
        self.assertEqual(self.subject(tree), MESSAGE, result.stderr)
        self.assertTrue(self.dirty(tree, "README.md"))
        self.assertFalse(self.dirty(tree, APPROVED))

    def test_leaves_nothing_of_others_in_the_index(self):
        """13. 実行後、同じツリーの他人の変更がステージ（インデックス）に載っていない。

        書きかけは書きかけのまま（ステージしていない変更と未追跡のファイル）で残す。
        """
        tree = self.worktree("i0001")
        self.place(tree)
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        write(os.path.join(tree, "src", "draft.py"), "x\n")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.staged(tree), "")
        self.assertEqual(self.dirty(tree, "README.md"), "M README.md")
        self.assertTrue(self.dirty(tree, "src").startswith("??"), self.dirty(tree, "src"))

    def test_only_a_detached_tree_is_skipped_without_saying_nothing(self):
        """14. detached のツリーにしか変更が無いときは 0 で終わる。

        「運ぶ承認済みチケットは無い。」とは言わず、飛ばしたことを標準エラーに言う。
        """
        loose = self.worktree("loose", detach=True)
        self.place(loose)
        before = self.head(loose)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(NOTHING, result.stdout)
        self.assertNotIn(NOTHING, result.stderr)
        self.assertIn("loose", result.stderr)
        self.assertEqual(self.head(loose), before)
        self.assertTrue(self.dirty(loose, APPROVED))

    # ---- 1.2・1.3 引数と置き場

    def test_takes_no_arguments_and_needs_the_workspace(self):
        self.assertEqual(self.push("--help").returncode, 0)
        wrong = self.push("i0001")
        self.assertEqual(wrong.returncode, 2, wrong.stdout + wrong.stderr)
        outside = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(outside)
        lost = self.push(cwd=outside)
        self.assertEqual(lost.returncode, 2, lost.stdout + lost.stderr)


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class ApproveCarriesTest(Workspace):
    """22. `ccnavi-approve.sh` は承認のあと `ccnavi-push-approved.sh` で運ぶ（設計 §1.4）。"""

    scripts = APPROVE_SCRIPTS

    def setUp(self):
        super().setUp()
        self.stub = write(os.path.join(self._tmp.name, "bin", "ccnavi"), STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def approve(self, *ids, **extra):
        env = self.env(
            CCNAVI_BIN_PATH=posix(self.stub),
            **{k: posix(v) if k in ("STUB_TREE", "STUB_ARGS") else v for k, v in extra.items()},
        )
        return self.run_sh("ccnavi-approve.sh", *ids, env=env)

    def received(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_approve_passes_ids_after_approve(self):
        """23. 並べた識別子だけを承認の対象にする。

        同じ親の承認待ちのうち 1 本だけを先に承認する形（#31）。
        """
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve("i0002-03", "i0002-04", STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        got = self.received(args)
        self.assertEqual(got[:1], ["--root"], got)
        self.assertEqual(got[2:], ["--approve", "i0002-03", "i0002-04"], got)

    def test_approve_without_ids_takes_all_pending(self):
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve(STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.received(args)[2:], ["--approve"])

    def test_approve_refuses_words_that_are_not_ids(self):
        """識別子でない語は断り、実行ファイルを呼ばない。

        `--yes` や `--root` を混ぜると、端末の y/N を経ない経路や別のワークスペースに化ける。
        """
        tree = self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        for ids in (("--yes", "i0002"), ("i0002", "--root", "/elsewhere"), ("-x",), ("",)):
            with self.subTest(ids=ids):
                result = self.approve(*ids, STUB_ARGS=args, STUB_TREE=tree)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("識別子でない引数", result.stderr)
                self.assertFalse(os.path.exists(args))
                self.assertEqual(self.remote_head("i0001"), "")

    def test_approve_help_still_shows_usage(self):
        for word in ("-h", "--help", "help"):
            with self.subTest(word=word):
                result = self.approve(word)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("<識別子>", result.stdout)

    def test_approve_help_next_to_ids_is_an_id(self):
        """使い方を出すのは語が 1 つのときだけ。識別子と並んだ `help` を黙って捨てない。"""
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve("help", "i0002-01", STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.received(args)[2:], ["--approve", "help", "i0002-01"])
        refused = self.approve("--help", "i0002-01")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)

    def test_approve_carries_after_approval(self):
        tree = self.worktree("i0001")
        result = self.approve(STUB_TREE=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_approve_says_when_there_is_nothing_to_carry(self):
        """運ぶ段は ccnavi-push-approved.sh に任せる。運ぶものが無ければその 1 行が出る。"""
        self.worktree("i0001")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)

    def test_failed_approval_carries_nothing(self):
        tree = self.worktree("i0001")
        self.place(tree)
        before = self.head(tree)
        result = self.approve(STUB_EXIT="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.head(tree), before)
        self.assertEqual(self.remote_head("i0001"), "")

    def test_failed_push_does_not_fail_the_approval(self):
        """運ぶ失敗で承認が失敗に見えないように、承認が通れば 0。コミットは残る。"""
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        tree = self.worktree("i0001")
        result = self.approve(STUB_TREE=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)


if __name__ == "__main__":
    unittest.main()
