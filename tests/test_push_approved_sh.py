"""ccnavi-push-approved.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から叩く。

設計 wip/design/approve-carry.md §1 と §6.3。確かめるのは次のとおり。

17. 置き場（`.ccnavi/tickets`）の変更だけをコミットし、同じツリーの他の未コミットは運ばない
18. 運ぶものが無ければ 0 で `運ぶ承認済みチケットは無い。`
19. `main` の上のツリーはコミットして push しない（0、標準エラーに綴り）
20. push が落ちると 1、コミットは残る
21. detached のツリーは飛ばす
22. `ccnavi-approve.sh` が承認のあと運ぶ

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
PUSH_SCRIPTS = ("ccnavi-push-approved.sh", "ccnavi-common.sh")
APPROVE_SCRIPTS = (*PUSH_SCRIPTS, "ccnavi-approve.sh")
APPROVED = ".ccnavi/tickets"
MESSAGE = "ccnavi: 承認済みチケットを更新"
NOTHING = "運ぶ承認済みチケットは無い。"

# 承認の代わり。STUB_EXIT が 0 でなければ落ち、STUB_TREE があればそこに承認済みチケットを置く。
STUB = """#!/bin/sh
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

    def push(self, *args, cwd=None):
        return self.run_sh("ccnavi-push-approved.sh", *args, cwd=cwd)

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

    def approve(self, **extra):
        env = self.env(
            CCNAVI_BIN_PATH=posix(self.stub),
            **{k: posix(v) if k == "STUB_TREE" else v for k, v in extra.items()},
        )
        return self.run_sh("ccnavi-approve.sh", env=env)

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
