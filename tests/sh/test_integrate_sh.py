"""`ccnavi-git.sh integrate` / `integrate --abort` / `integrate-done` の受入テスト。

直接作業（チケットを起こさない）のワークツリーの成果を、切ったときの取り込み先のブランチへ
1 コミットで載せる流れ。確認している間、メインのチェックアウトの index と作業ツリーには書かず、
一時ワークツリーで取り込み結果を作り、最後に `merge --ff-only --no-overwrite-ignore` で載せる。
使い捨てのワークスペースを毎回組み、sh を外から呼ぶ。読み返すのは終了コード・出力と、
git とファイルシステムに残ったものだけ。

記録（`ccnavi-direct/<名前>`）は `ccnavi-worktree.sh` が書く形を、このテストが同じ形で書く。
版の無い marker（旧形式）は、旧来の abort と確定にだけ回る。

実行ファイル（`tree base-bound`・`tree guarded`・`tree handoff-pending`・`worktree drop`）の
代わりに、このツリーのソースを起こす（CCNAVI_BIN_PATH）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-git.sh", "ccnavi-clean.sh", "ccnavi-clean.js", *common_sh(SH_DIR))


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def git_ok(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def git_supports_v4():
    if not GIT:
        return False
    out = subprocess.run(["git", "version"], capture_output=True, text=True).stdout.split()
    try:
        major, minor = (int(x) for x in out[2].split(".")[:2])
    except (IndexError, ValueError):
        return False
    return (major, minor) >= (2, 40)


class Fixture(unittest.TestCase):
    """ワークスペース（ルートは main）と、記録つきの直接作業のワークツリー `wt`。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.bin = os.path.join(self._tmp.name, "bin", "ccnavi")
        write(
            self.bin,
            f"#!/bin/sh\nPYTHONPATH='{ROOT}/src' exec '{sys.executable}' -m ccnavi \"$@\"\n",
        )
        os.chmod(self.bin, 0o755)
        self.path = None
        self.stdin = subprocess.DEVNULL
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.email", "t@example.invalid")
        git(self.ws, "config", "user.name", "t")
        write(
            os.path.join(self.ws, ".gitignore"),
            "/.ccnavi/\n/.claude/worktrees/\n/logs/\n*.local\n",
        )
        write(os.path.join(self.ws, "keep.txt"), "1\n2\n3\n")
        write(os.path.join(self.ws, "gone.txt"), "bye\n")
        write(os.path.join(self.ws, "other.txt"), "x\n")
        write(os.path.join(self.ws, "wip/handoff/d.md"), "draft\n")
        git(self.ws, "add", ".")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.wt = self.cut("wt")

    # ---- 道具

    def common_dir(self, repo=None):
        return os.path.realpath(os.path.join(repo or self.ws, ".git"))

    def cut(self, name, repo=None, base="main"):
        """ccnavi-worktree.sh が切ったのと同じ形（記録・s の ref・ワークツリー）を作る。"""
        repo = repo or self.ws
        head = git(repo, "rev-parse", "HEAD").strip()
        wt = os.path.join(self.ws, ".claude", "worktrees", name)
        write(
            os.path.join(self.common_dir(repo), "ccnavi-direct", name),
            "version 4\n"
            f"base_branch {base}\nbase_sha {head}\nsnap_sha {head}\nsnap_wip no\n"
            f"side_by_side no\nmain_tree {os.path.realpath(repo)}\n"
            f"common_dir {self.common_dir(repo)}\nconfirm -\ncreated 0\n",
        )
        git(repo, "update-ref", f"refs/ccnavi/direct/{name}/s", head)
        git(repo, "worktree", "add", "-q", wt, "-b", name, "--no-track", head)
        return wt

    def run_git_sh(self, *args, cwd=None, env_extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["CCNAVI_BIN_PATH"] = self.bin
        env.update(env_extra or {})
        if self.path is not None:
            env["PATH"] = self.path
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            stdin=self.stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def work_in_branch(self, wt=None):
        """作業ワークツリーで、変更・追加・削除を 2 コミットに分けて積む。"""
        wt = wt or self.wt
        write(os.path.join(wt, "keep.txt"), "1\n2\nthree\n")
        write(os.path.join(wt, "new.txt"), "new\n")
        git(wt, "add", ".")
        git(wt, "commit", "-q", "-m", "w1")
        git(wt, "rm", "-q", "gone.txt")
        git(wt, "commit", "-q", "-m", "w2")

    def advance_main(self, name="other.txt", text="y\n"):
        write(os.path.join(self.ws, name), text)
        git(self.ws, "commit", "-q", "-am", "main moves")

    def marker(self, name="wt"):
        return os.path.join(self.common_dir(), "ccnavi-integrate", name, "marker")

    def marker_value(self, key, name="wt"):
        for line in read(self.marker(name)).splitlines():
            if line.startswith(key + " "):
                return line.split(" ", 1)[1]
        return ""

    def record(self, name="wt"):
        return os.path.join(self.common_dir(), "ccnavi-direct", name)

    def tmp_wt(self, name="wt", repo="self"):
        return os.path.join(self.ws, "logs", "direct-tmp", repo, name)

    def status(self):
        return git(self.ws, "status", "--porcelain")

    def has_branch(self, name="wt"):
        return git_ok(self.ws, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")

    def ref(self, kind, name="wt"):
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/ccnavi/direct/{name}/{kind}"],
            cwd=self.ws,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def head(self):
        return git(self.ws, "rev-parse", "HEAD").strip()

    def snapshot(self):
        """ルートの HEAD・index・作業ツリーの状態。確認の間は変わらない。"""
        return (
            self.head(),
            git(self.ws, "write-tree").strip(),
            git(self.ws, "status", "--porcelain"),
        )

    def hook(self, name, body):
        path = os.path.join(self.common_dir(), "hooks", name)
        write(path, "#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def assertOk(self, result, code=0):
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)


@unittest.skipUnless(SHELL and GIT and git_supports_v4(), "sh と git 2.40 以上が要る")
class IntegrateTest(Fixture):
    # ---- integrate

    def test_prepares_c_in_a_temp_worktree_without_touching_the_root(self):
        self.work_in_branch()
        self.advance_main()
        write(os.path.join(self.ws, "mine.txt"), "user untracked\n")
        write(os.path.join(self.ws, "other.txt"), "y\nedited\n")
        git(self.ws, "add", "other.txt")
        before = self.snapshot()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertEqual(before, self.snapshot())
        self.assertIn("取り込み結果を一時ワークツリーに用意した（3 ファイル", r.stdout)
        self.assertIn("変わるファイル:", r.stdout)
        for path in ("keep.txt", "new.txt", "gone.txt"):
            self.assertIn(f"  {path}", r.stdout)
        self.assertNotIn("  other.txt", r.stdout)
        self.assertIn("保存していない編集", r.stdout)
        self.assertIn("integrate-done wt -m", r.stdout)
        self.assertEqual(1, [ln[:2] for ln in r.stdout.splitlines()].count("次:"))
        tmp = self.tmp_wt()
        self.assertEqual("1\n2\nthree\n", read(os.path.join(tmp, "keep.txt")))
        self.assertFalse(os.path.exists(os.path.join(tmp, "gone.txt")))
        self.assertEqual("y\n", read(os.path.join(tmp, "other.txt")))
        self.assertTrue(os.path.isfile(self.marker()))
        marker = read(self.marker())
        self.assertIn("version 4", marker)
        self.assertEqual(
            git(tmp, "write-tree").strip(),
            self.marker_value("c_tree"),
        )
        self.assertTrue(self.ref("t"))
        self.assertTrue(self.ref("c"))

    def test_the_diff_is_only_the_agents_part(self):
        self.work_in_branch()
        self.advance_main()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        c = self.marker_value("c_tree")
        changed = git(self.ws, "diff-tree", "-r", "--name-only", "HEAD^{tree}", c).split()
        self.assertEqual(["gone.txt", "keep.txt", "new.txt"], sorted(changed))

    def test_a_same_place_commit_by_the_user_is_refused_with_paths(self):
        self.work_in_branch()
        self.advance_main("keep.txt", "1\n2\nTHREE\n")
        before = self.snapshot()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 1)
        self.assertIn("衝突した", r.stdout)
        self.assertIn("keep.txt", r.stdout)
        self.assertIn("merge main", r.stdout)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(self.tmp_wt()))
        self.assertFalse(os.path.exists(self.marker()))

    def test_no_diff_is_reported_and_done_only_cleans(self):
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("取り込む差分は無かった", r.stdout)
        before = self.head()
        done = self.run_git_sh("integrate-done", "wt")
        self.assertOk(done)
        self.assertEqual(before, self.head())
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.wt))
        self.assertFalse(os.path.exists(self.marker()))

    def test_integrate_refuses_when_the_base_branch_changed(self):
        self.work_in_branch()
        git(self.ws, "checkout", "-q", "-b", "elsewhere")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("取り込み先（main）を開いていません", r.stderr)
        self.assertIn("elsewhere", r.stderr)

    def test_integrate_from_a_worktree_is_refused(self):
        self.work_in_branch()
        r = self.run_git_sh("integrate", "wt", cwd=self.wt)
        self.assertOk(r, 2)
        self.assertIn("メインのチェックアウト", r.stderr)

    def test_a_dirty_source_worktree_is_refused(self):
        self.work_in_branch()
        write(os.path.join(self.wt, "wip.txt"), "x\n")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("未コミットの変更", r.stderr)

    def test_a_draft_that_is_not_in_the_start_point_is_refused(self):
        self.work_in_branch()
        write(os.path.join(self.wt, "wip/handoff/extra.md"), "new draft\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "draft")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("handoff", r.stderr)
        self.assertFalse(os.path.exists(self.tmp_wt()))

    def test_a_deleted_draft_is_left_alone_in_the_result(self):
        self.work_in_branch()
        git(self.wt, "rm", "-q", "wip/handoff/d.md")
        git(self.wt, "commit", "-q", "-m", "drop the draft")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertNotIn("d.md", r.stdout)
        self.assertEqual("draft\n", read(os.path.join(self.tmp_wt(), "wip/handoff/d.md")))

    def test_a_ticket_bound_base_branch_is_refused(self):
        os.makedirs(os.path.join(self.ws, "wip", "proposals", "todo"))
        write(
            os.path.join(self.ws, "wip", "proposals", "todo", "main.md"),
            "---\nticket: main\n---\nbody\n",
        )
        self.work_in_branch()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("チケット", r.stderr)

    def test_the_exe_that_cannot_answer_refuses(self):
        self.work_in_branch()
        r = self.run_git_sh("integrate", "wt", env_extra={"CCNAVI_BIN_PATH": "/nonexistent/ccnavi"})
        self.assertOk(r, 2)
        self.assertIn("確かめられない", r.stderr)

    def test_without_a_record_it_points_at_adopt(self):
        self.work_in_branch()
        os.remove(self.record())
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("--adopt", r.stderr)

    def test_a_record_of_another_repository_is_refused(self):
        self.work_in_branch()
        text = read(self.record()).replace(self.common_dir(), "/somewhere/else/.git")
        write(self.record(), text)
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("common_dir", r.stderr)

    def test_names_that_are_paths_are_refused(self):
        r = self.run_git_sh("integrate", "../etc")
        self.assertOk(r, 2)
        r = self.run_git_sh("integrate", "a/b")
        self.assertOk(r, 2)
        self.assertOk(self.run_git_sh("integrate"), 2)

    def test_an_old_git_is_refused(self):
        shim = os.path.join(self._tmp.name, "oldgit")
        write(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'if [ "$1" = version ]; then echo "git version 2.39.0"; exit 0; fi\n'
            f"exec '{GIT}' \"$@\"\n",
        )
        os.chmod(os.path.join(shim, "git"), 0o755)
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.work_in_branch()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("2.40", r.stderr)

    def test_an_ignored_file_overlap_is_named(self):
        write(os.path.join(self.wt, "added.local"), "agent\n")
        git(self.wt, "add", "-f", "added.local")
        git(self.wt, "commit", "-q", "-m", "tracked despite ignore")
        write(os.path.join(self.ws, "added.local"), "user's ignored file\n")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("ignored のファイルと重なっている", r.stdout)
        self.assertIn("added.local", r.stdout)

    def test_a_ready_made_temp_worktree_with_unknown_files_is_not_thrown_away(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "somebody's file\n")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("片付けられません", r.stderr)
        self.assertEqual("somebody's file\n", read(os.path.join(self.tmp_wt(), "mystery.txt")))

    def test_integrate_again_rebuilds_the_temp_worktree(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.wt, "more.txt"), "more\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "more")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertEqual("more\n", read(os.path.join(self.tmp_wt(), "more.txt")))

    def test_the_temp_worktree_can_be_verified_and_generated_files_do_not_block(self):
        self.work_in_branch()
        write(os.path.join(self.wt, "package.json"), "{}\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "package")
        self.assertOk(self.run_git_sh("integrate", "wt"))
        tmp = self.tmp_wt()
        write(os.path.join(tmp, "__pycache__", "x.pyc"), "bytecode")
        write(os.path.join(tmp, "node_modules", "m", "index.js"), "m")
        os.makedirs(os.path.join(tmp, "out"))
        write(os.path.join(tmp, "out", "bundle.js"), "b")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r)
        self.assertFalse(os.path.exists(tmp))

    # ---- integrate-done

    def test_done_lands_one_commit_and_cleans_up(self):
        self.work_in_branch()
        self.advance_main()
        before = self.head()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        no_msg = self.run_git_sh("integrate-done", "wt")
        self.assertOk(no_msg, 2)
        self.assertIn("-m <メッセージ>", no_msg.stderr)
        done = self.run_git_sh("integrate-done", "wt", "-m", "feat: 取り込む")
        self.assertOk(done)
        self.assertIn("取り込みました", done.stdout)
        self.assertIn("ok  git integrate-done  wt を片付けた", done.stdout)
        self.assertEqual([before], git(self.ws, "log", "-1", "--format=%P").split())
        self.assertEqual("feat: 取り込む", git(self.ws, "log", "-1", "--format=%s").strip())
        self.assertEqual("", self.status())
        self.assertEqual("1\n2\nthree\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertEqual("y\n", read(os.path.join(self.ws, "other.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "gone.txt")))
        self.assertFalse(os.path.exists(self.wt))
        self.assertFalse(os.path.exists(self.tmp_wt()))
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.marker()))
        self.assertFalse(os.path.exists(self.record()))
        for kind in ("s", "t", "c", "k"):
            self.assertEqual("", self.ref(kind), kind)
        done_file = os.path.join(self.common_dir(), "ccnavi-integrate-done", "wt")
        text = read(done_file)
        for key in ("version 4", "k ", "s ", "t ", "c_tree ", "head ", "base_branch main"):
            self.assertIn(key, text)
        # origin/main より進んでいるので、push はユーザに頼む。
        self.assertIn("次: ユーザに", done.stdout)
        self.assertIn("git push origin main", done.stdout)
        rerun = self.run_git_sh("integrate-done", "wt")
        self.assertOk(rerun)
        self.assertIn("片付け済み", rerun.stdout)

    def test_the_users_other_work_is_carried_over(self):
        self.work_in_branch()
        write(os.path.join(self.ws, "other.txt"), "x\nuser edit\n")
        write(os.path.join(self.ws, "staged.txt"), "staged\n")
        git(self.ws, "add", "staged.txt")
        write(os.path.join(self.ws, "fresh.txt"), "untracked\n")
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"))
        self.assertEqual("x\nuser edit\n", read(os.path.join(self.ws, "other.txt")))
        self.assertEqual("untracked\n", read(os.path.join(self.ws, "fresh.txt")))
        lines = self.status().splitlines()
        self.assertIn("A  staged.txt", lines)
        self.assertIn(" M other.txt", lines)
        self.assertIn("?? fresh.txt", lines)

    def test_the_same_file_uncommitted_stops_without_changes_and_resumes(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nmine\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("keep.txt", r.stdout)
        self.assertEqual(head, self.head())
        self.assertEqual("1\n2\nmine\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertTrue(self.has_branch())
        self.assertIn("k ", read(self.marker()))
        # 退避してもらって打ち直すと、K から再開して完了する。
        git(self.ws, "checkout", "--", "keep.txt")
        again = self.run_git_sh("integrate-done", "wt")
        self.assertOk(again)
        self.assertEqual("1\n2\nthree\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertEqual(1, len(git(self.ws, "rev-list", f"{head}..HEAD").split()))
        self.assertFalse(self.has_branch())

    def test_an_ignored_file_is_not_overwritten(self):
        write(os.path.join(self.wt, "added.local"), "agent\n")
        git(self.wt, "add", "-f", "added.local")
        git(self.wt, "commit", "-q", "-m", "tracked despite ignore")
        write(os.path.join(self.ws, "added.local"), "user's ignored file\n")
        self.assertOk(self.run_git_sh("integrate", "wt"))
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertEqual(head, self.head())
        self.assertEqual("user's ignored file\n", read(os.path.join(self.ws, "added.local")))
        self.assertTrue(os.path.isfile(self.marker()))

    def test_an_ignored_directory_is_not_overwritten(self):
        write(os.path.join(self.ws, ".git", "info", "exclude"), "gen/\n")
        write(os.path.join(self.wt, "gen", "a.txt"), "agent\n")
        git(self.wt, "add", "-f", "gen/a.txt")
        git(self.wt, "commit", "-q", "-m", "tracked despite ignore")
        write(os.path.join(self.ws, "gen", "a.txt"), "user's\n")
        self.assertOk(self.run_git_sh("integrate", "wt"))
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertEqual("user's\n", read(os.path.join(self.ws, "gen", "a.txt")))

    def test_a_moved_head_asks_to_start_over(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.advance_main()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("integrate --abort wt", r.stderr)
        self.assertTrue(self.has_branch())

    def test_a_moved_branch_tip_asks_to_start_over(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.wt, "late.txt"), "late\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "late")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("動いています", r.stderr)

    def test_a_dirty_source_worktree_stops_done(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.wt, "late.txt"), "late\n")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("未コミットの変更", r.stderr)

    def test_a_branch_switch_is_refused(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        git(self.ws, "checkout", "-q", "-b", "elsewhere")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("取り込み先（main）を開いていません", r.stderr)

    def test_a_hook_that_rejects_the_commit_records_nothing(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.hook("pre-commit", "echo 'no commits today' >&2\nexit 1\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertEqual(head, self.head())
        self.assertNotIn("k ", read(self.marker()))
        self.assertEqual("", self.ref("k"))

    def test_a_hook_that_changes_the_content_records_nothing(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.hook("pre-commit", "echo hook >hooked.txt\ngit add hooked.txt\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("フック", r.stdout)
        self.assertEqual(head, self.head())
        self.assertNotIn("k ", read(self.marker()))
        self.assertEqual("", self.ref("k"))
        # 一時ワークツリーは C に戻してあるので、フックを直せば打ち直せる。
        os.remove(os.path.join(self.common_dir(), "hooks", "pre-commit"))
        ok = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(ok)

    def test_a_k_that_does_not_match_the_record_is_not_adopted(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        stray = git(self.ws, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "stray").strip()
        write(self.marker(), read(self.marker()) + f"k {stray}\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertEqual(head, self.head())

    def test_a_cleanup_that_stops_resumes_even_after_the_user_committed(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "somebody's file\n")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("取り込みました", r.stdout)
        self.assertIn("残したもの", r.stdout)
        self.assertEqual("somebody's file\n", read(os.path.join(self.tmp_wt(), "mystery.txt")))
        # K が載ったあとにユーザがコミットしても、片付けは止まらない。
        self.advance_main("other.txt", "z\n")
        os.remove(os.path.join(self.tmp_wt(), "mystery.txt"))
        again = self.run_git_sh("integrate-done", "wt")
        self.assertOk(again)
        self.assertFalse(os.path.exists(self.tmp_wt()))
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.marker()))

    def test_a_cleanup_after_a_revert_still_completes(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "x\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        git(self.ws, "revert", "--no-edit", "HEAD")
        os.remove(os.path.join(self.tmp_wt(), "mystery.txt"))
        self.assertOk(self.run_git_sh("integrate-done", "wt"))
        self.assertFalse(self.has_branch())

    def test_a_guarded_file_needs_the_terminal(self):
        write(os.path.join(self.wt, ".ccnavi/config/common/rules.yml"), "ask: []\n")
        git(self.wt, "add", "-f", ".ccnavi/config/common/rules.yml")
        git(self.wt, "commit", "-q", "-m", "rules")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("自己防衛", r.stdout)
        head = self.head()
        done = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(done, 2)
        self.assertIn("端末", done.stderr)
        self.assertEqual(head, self.head())

    def test_a_cleanup_keeps_the_branch_when_it_moved(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "x\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        os.remove(os.path.join(self.tmp_wt(), "mystery.txt"))
        # ブランチを動かす（取り込んだあとに積んだコミット）。
        tip = git(self.ws, "rev-parse", "wt").strip()
        late = git(self.ws, "commit-tree", f"{tip}^{{tree}}", "-p", tip, "-m", "late").strip()
        git(self.ws, "update-ref", "refs/heads/wt", late)
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertTrue(self.has_branch())

    # ---- abort

    def test_abort_before_done_removes_the_temp_worktree_and_refs_only(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        tip = git(self.ws, "rev-parse", "wt").strip()
        before = self.snapshot()
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(os.path.exists(self.tmp_wt()))
        self.assertFalse(os.path.exists(self.marker()))
        self.assertEqual("", self.ref("t"))
        self.assertEqual("", self.ref("c"))
        self.assertTrue(self.ref("s"))
        self.assertTrue(os.path.isfile(self.record()))
        self.assertTrue(os.path.isdir(self.wt))
        self.assertEqual(tip, git(self.ws, "rev-parse", "wt").strip())

    def test_abort_refuses_when_k_has_landed(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "x\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r, 2)
        self.assertIn("git revert", r.stderr)

    def test_abort_after_k_was_made_but_not_landed(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nmine\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        self.assertTrue(self.ref("k"))
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r)
        self.assertEqual("", self.ref("k"))
        self.assertEqual("1\n2\nmine\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertFalse(os.path.exists(self.tmp_wt()))

    def test_abort_with_nothing_to_undo_is_refused(self):
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r, 2)

    def test_integrate_is_refused_while_a_k_waits(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nmine\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("integrate-done", r.stderr)

    # ---- 並行

    def test_two_operations_at_once_one_is_refused_by_the_lock(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.hook("pre-commit", "sleep 3\n")
        slow = subprocess.Popen(
            [
                SHELL,
                os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh").replace(os.sep, "/"),
                "integrate-done",
                "wt",
                "-m",
                "feat: x",
            ],
            cwd=self.ws,
            env={**os.environ, "CCNAVI_BIN_PATH": self.bin},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(1.0)
            other = self.run_git_sh("integrate", "wt")
            self.assertOk(other, 2)
            self.assertIn("動いています", other.stderr)
        finally:
            out, err = slow.communicate(timeout=60)
        self.assertEqual(0, slow.returncode, out + err)

    # ---- 境界

    def test_a_project_repository_is_integrated_by_itself(self):
        project = os.path.join(self.ws, "projects", "p")
        os.makedirs(project)
        git(project, "init", "-q", "-b", "main")
        git(project, "config", "user.email", "t@example.invalid")
        git(project, "config", "user.name", "t")
        write(os.path.join(project, "a.txt"), "a\n")
        git(project, "add", ".")
        git(project, "commit", "-q", "-m", "seed p")
        git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        wt = self.cut("pwt", repo=project)
        write(os.path.join(wt, "a.txt"), "a\nagent\n")
        git(wt, "commit", "-q", "-am", "agent")
        ws_before = self.snapshot()
        r = self.run_git_sh("integrate", "pwt", cwd=project)
        self.assertOk(r)
        self.assertTrue(os.path.isdir(self.tmp_wt("pwt", "p")))
        done = self.run_git_sh("integrate-done", "pwt", "-m", "feat: p", cwd=project)
        self.assertOk(done)
        self.assertEqual("a\nagent\n", read(os.path.join(project, "a.txt")))
        self.assertEqual(ws_before[0], self.head())
        self.assertEqual(ws_before[1], git(self.ws, "write-tree").strip())
        self.assertFalse(os.path.exists(wt))

    def test_the_other_repositorys_leftovers_are_not_touched(self):
        project = os.path.join(self.ws, "projects", "q")
        os.makedirs(project)
        git(project, "init", "-q", "-b", "main")
        left = self.tmp_wt("qwt", "q")
        write(os.path.join(left, "x"), "x\n")
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"))
        self.assertEqual("x\n", read(os.path.join(left, "x")))


@unittest.skipUnless(SHELL and GIT and git_supports_v4(), "sh と git 2.40 以上が要る")
class IntegrateReviewTest(Fixture):
    """敵対的レビューの指摘に対する回帰テスト。外すと落ちる条件を 1 つずつ縛る。"""

    def resolve_conflict(self, text="1\n2\nresolved\n"):
        subprocess.run(["git", "merge", "-q", "main"], cwd=self.wt, capture_output=True)
        write(os.path.join(self.wt, "keep.txt"), text)
        git(self.wt, "add", "keep.txt")
        git(self.wt, "commit", "-q", "-m", "resolve")

    def k_made_but_not_landed(self):
        """K を作ったが、ユーザの未コミットの変更で fast-forward が止まった状態にする。"""
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nmine\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        git(self.ws, "checkout", "--", "keep.txt")

    def landed_but_cleanup_stopped(self):
        """K を載せたあと、一時ワークツリーの未知のファイルで片付けが止まった状態にする。"""
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "mystery.txt"), "somebody's file\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        return os.path.join(self.tmp_wt(), "mystery.txt")

    # ---- 衝突を解いたあとの収束

    def test_a_resolved_conflict_converges(self):
        self.work_in_branch()
        self.advance_main("keep.txt", "1\n2\nTHREE\n")
        self.assertOk(self.run_git_sh("integrate", "wt"), 1)
        self.resolve_conflict()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"))
        self.assertEqual("1\n2\nresolved\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.ws, "new.txt")))

    def test_the_base_moving_after_the_resolution_does_not_leak_into_the_result(self):
        self.work_in_branch()
        self.advance_main("keep.txt", "1\n2\nTHREE\n")
        self.assertOk(self.run_git_sh("integrate", "wt"), 1)
        self.resolve_conflict()
        self.advance_main("other.txt", "later\n")
        self.assertOk(self.run_git_sh("integrate", "wt"))
        changed = git(
            self.ws, "diff-tree", "-r", "--name-only", "HEAD^{tree}", self.marker_value("c_tree")
        ).split()
        self.assertEqual(["gone.txt", "keep.txt", "new.txt"], sorted(changed))

    # ---- 名前のほうのチケットの結び付き

    def test_a_name_bound_to_a_ticket_is_refused(self):
        self.work_in_branch()
        git(self.ws, "update-ref", "refs/remotes/origin/wt", "HEAD")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("チケットに結び付く", r.stderr)
        self.assertFalse(os.path.exists(self.tmp_wt()))

    # ---- ルートの index に書かない

    def test_integrate_does_not_rewrite_the_roots_index(self):
        self.work_in_branch()
        path = os.path.join(self.ws, "keep.txt")
        write(path, "1\n2\n3\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        index = os.path.join(self.common_dir(), "index")
        with open(index, "rb") as f:
            before = f.read()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        with open(index, "rb") as f:
            self.assertEqual(before, f.read())

    # ---- 引用符が付く名前

    @unittest.skipIf(os.name == "nt", "ファイル名に使えない文字を含む")
    def test_names_that_git_quotes_are_handled(self):
        draft = "wip/handoff/a\\b.md"
        write(os.path.join(self.ws, draft), "draft two\n")
        git(self.ws, "add", "-f", draft)
        git(self.ws, "commit", "-q", "-m", "draft")
        wt = self.cut("wt2")
        git(wt, "rm", "-q", "--", draft)
        write(os.path.join(wt, "tab\tname.txt"), "t\n")
        git(wt, "add", ".")
        git(wt, "commit", "-q", "-m", "agent")
        r = self.run_git_sh("integrate", "wt2")
        self.assertOk(r)
        self.assertIn("  tab\tname.txt", r.stdout)
        c = self.marker_value("c_tree", "wt2")
        names = git(self.ws, "ls-tree", "-r", "-z", "--name-only", c).split("\0")
        self.assertIn(draft, names)
        self.assertIn("tab\tname.txt", names)

    # ---- K の記録前に落ちた打ち直し

    def test_a_k_committed_before_the_record_is_adopted_on_retry(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        git(self.tmp_wt(), "commit", "-q", "-m", "committed by hand")
        k = git(self.tmp_wt(), "rev-parse", "HEAD").strip()
        self.assertNotIn("k ", read(self.marker()))
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r)
        self.assertEqual(k, self.head())
        self.assertFalse(self.has_branch())

    # ---- 縛られていなかった条件

    def test_a_k_with_the_wrong_parent_is_not_adopted(self):
        self.work_in_branch()
        self.advance_main()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        old = git(self.ws, "rev-parse", "HEAD~1").strip()
        k = git(self.ws, "commit-tree", self.marker_value("c_tree"), "-p", old, "-m", "k").strip()
        write(self.marker(), read(self.marker()) + f"k {k}\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertEqual(head, self.head())

    def test_a_tracked_edit_in_the_temp_worktree_stops_done(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "keep.txt"), "edited while verifying\n")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("違います", r.stderr)
        self.assertEqual(head, self.head())

    def test_a_staged_edit_in_the_temp_worktree_stops_done(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.tmp_wt(), "keep.txt"), "edited while verifying\n")
        git(self.tmp_wt(), "add", "keep.txt")
        head = self.head()
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 2)
        self.assertIn("違います", r.stderr)
        self.assertEqual(head, self.head())

    def test_cleanup_keeps_a_tracked_edit_in_the_temp_worktree(self):
        mystery = self.landed_but_cleanup_stopped()
        os.remove(mystery)
        write(os.path.join(self.tmp_wt(), "keep.txt"), "edited later\n")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertEqual("edited later\n", read(os.path.join(self.tmp_wt(), "keep.txt")))

    def test_cleanup_keeps_a_staged_edit_in_the_temp_worktree(self):
        mystery = self.landed_but_cleanup_stopped()
        os.remove(mystery)
        write(os.path.join(self.tmp_wt(), "keep.txt"), "edited later\n")
        git(self.tmp_wt(), "add", "keep.txt")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertEqual("edited later\n", read(os.path.join(self.tmp_wt(), "keep.txt")))

    def test_cleanup_keeps_the_worktree_when_it_left_the_branch(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        git(self.wt, "checkout", "-q", "-b", "elsewhere")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertTrue(os.path.isdir(self.wt))
        self.assertTrue(self.has_branch())

    def test_cleanup_keeps_the_worktree_with_uncommitted_changes(self):
        self.k_made_but_not_landed()
        write(os.path.join(self.wt, "keep.txt"), "uncommitted\n")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertIn("未コミットの変更があるので、削除していません", r.stdout)
        self.assertEqual("uncommitted\n", read(os.path.join(self.wt, "keep.txt")))
        self.assertTrue(self.has_branch())

    def test_cleanup_keeps_the_worktree_with_a_new_ignored_draft(self):
        self.k_made_but_not_landed()
        write(os.path.join(self.wt, "wip/handoff/note.local"), "not in the start point\n")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertIn("起点に無いか中身の違うファイル", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "wip/handoff/note.local")))

    def test_cleanup_stops_before_deleting_the_worktree_when_the_branch_moved(self):
        self.k_made_but_not_landed()
        tip = git(self.ws, "rev-parse", "wt").strip()
        late = git(self.ws, "commit-tree", f"{tip}^{{tree}}", "-p", tip, "-m", "late").strip()
        git(self.ws, "update-ref", "refs/heads/wt", late)
        git(self.wt, "reset", "-q", "--hard", late)
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertTrue(os.path.isdir(self.wt))
        self.assertTrue(self.has_branch())

    def test_a_head_that_moved_after_k_was_made_asks_to_start_over(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nmine\n")
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"), 1)
        write(os.path.join(self.ws, "other.txt"), "moved\n")
        git(self.ws, "commit", "-q", "-m", "user", "--", "other.txt")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 2)
        self.assertIn("動きました", r.stderr)

    def test_a_done_record_of_another_name_is_not_pruned(self):
        done_dir = os.path.join(self.common_dir(), "ccnavi-integrate-done")
        other = os.path.join(done_dir, "other-name")
        write(other, "version 4\nbase_branch main\n")
        old = time.time() - 30 * 86400
        os.utime(other, (old, old))
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.assertOk(self.run_git_sh("integrate-done", "wt", "-m", "feat: x"))
        self.assertTrue(os.path.exists(other))


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class OldMarkerTest(Fixture):
    """版の無い marker（旧形式）は、旧来の abort と確定にだけ回る。"""

    def old_integrate(self):
        """旧形式の取り込み: squash で staged にして、marker を書く。"""
        self.work_in_branch()
        base = self.head()
        git(self.ws, "merge", "--squash", "wt")
        tree = git(self.ws, "write-tree").strip()
        changed = git(self.ws, "diff", "--cached", "--no-renames", "--name-only", "HEAD")
        write(os.path.join(os.path.dirname(self.marker()), "paths"), changed)
        write(
            self.marker(),
            "state staged\nbranch wt\n"
            f"branch_sha {git(self.ws, 'rev-parse', 'wt').strip()}\n"
            f"base {base}\ntree {tree}\nintegration main\ncreated 0\n",
        )
        return base

    def test_integrate_on_an_old_marker_is_refused_with_guidance(self):
        self.old_integrate()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("旧来の取り込み", r.stderr)
        self.assertIn("integrate --abort wt", r.stderr)

    @unittest.skipUnless(git_supports_v4(), "git 2.40 以上が要る")
    def test_old_done_commits_and_cleans(self):
        base = self.old_integrate()
        done = self.run_git_sh("integrate-done", "wt", "-m", "feat: 旧")
        self.assertOk(done)
        self.assertEqual([base], git(self.ws, "log", "-1", "--format=%P").split())
        self.assertEqual("", self.status())
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.marker()))

    def test_old_abort_restores_the_root(self):
        base = self.old_integrate()
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r)
        self.assertEqual("", self.status())
        self.assertEqual(base, self.head())
        self.assertFalse(os.path.exists(self.marker()))

    def test_done_without_a_marker_is_refused(self):
        self.work_in_branch()
        r = self.run_git_sh("integrate-done", "wt", "-m", "x")
        self.assertOk(r, 2)
        self.assertIn("取り込みの記録がありません", r.stderr)
        self.assertTrue(self.has_branch())

    def test_done_on_a_cleaned_up_name_says_so(self):
        git(self.ws, "worktree", "remove", "--force", self.wt)
        git(self.ws, "branch", "-D", "wt")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r)
        self.assertIn("片付け済み", r.stdout)


@unittest.skipUnless(SHELL and GIT and git_supports_v4(), "sh と git 2.40 以上が必要")
class IntegrateGapsTest(Fixture):
    """ほかのクラスが一部しか見ていなかった条件を扱う。

    グローバルの excludes、片付けの各段での停止と再開、プロジェクトの自己防衛、
    一時ワークツリーに残った ignored のファイル、リポジトリ間の境界。
    """

    # ---- 道具

    def make_project(self, name):
        """projects/<name> に、コミットと origin/HEAD を持つプロジェクトのリポジトリを作る。"""
        project = os.path.join(self.ws, "projects", name)
        os.makedirs(project)
        git(project, "init", "-q", "-b", "main")
        git(project, "config", "user.email", "t@example.invalid")
        git(project, "config", "user.name", "t")
        write(os.path.join(project, "a.txt"), "a\n")
        git(project, "add", ".")
        git(project, "commit", "-q", "-m", f"seed {name}")
        git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return project

    def project_work(self, wt):
        write(os.path.join(wt, "a.txt"), "a\nagent\n")
        git(wt, "commit", "-q", "-am", "agent")

    def repo_state(self, repo):
        """HEAD・index の木・status。そのリポジトリが動いたかを見る。"""
        return (
            git(repo, "rev-parse", "HEAD").strip(),
            git(repo, "write-tree").strip(),
            git(repo, "status", "--porcelain"),
        )

    def install_failing_git(self):
        """環境変数 FAIL_GIT_MATCH の値を引数に含む git の呼び出しだけ、
        終了コード 1 で失敗させる代役を PATH に置く。"""
        shim = os.path.join(self._tmp.name, "failgit")
        write(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'if [ -n "$FAIL_GIT_MATCH" ]; then\n'
            '  case " $* " in\n'
            '    *"$FAIL_GIT_MATCH"*) echo "fake git: failed on request" >&2; exit 1 ;;\n'
            "  esac\n"
            "fi\n"
            f"exec '{GIT}' \"$@\"\n",
        )
        os.chmod(os.path.join(shim, "git"), 0o755)
        self.path = shim + os.pathsep + os.environ.get("PATH", "")

    def done_stopped_at(self, match, name="wt", cwd=None, base=None):
        """integrate まで進めたうえで、match に当たる git が失敗する状態にして、
        integrate-done -m を実行する。

        K は載り、片付けは途中で止まる。戻り値は K が載る前の HEAD で、代役は外してから返す。
        """
        before = base or self.head()
        r = self.run_git_sh(
            "integrate-done", name, "-m", "feat: x", cwd=cwd, env_extra={"FAIL_GIT_MATCH": match}
        )
        self.path = None
        return before, r

    def assert_landed_once(self, before, r, cwd=None):
        self.assertNotEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("取り込みました", r.stdout)
        repo = cwd or self.ws
        head = git(repo, "rev-parse", "HEAD").strip()
        self.assertNotEqual(before, head)
        self.assertEqual([before], git(repo, "log", "-1", "--format=%P").split())
        return head

    def assert_fully_cleaned(self, before, k):
        self.assertEqual(k, self.head())
        self.assertEqual("1", git(self.ws, "rev-list", "--count", f"{before}..HEAD").strip())
        self.assertFalse(os.path.exists(self.wt))
        self.assertFalse(os.path.exists(self.tmp_wt()))
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.marker()))
        self.assertFalse(os.path.exists(self.record()))
        for kind in ("s", "t", "c", "k"):
            self.assertEqual("", self.ref(kind), kind)

    def stage_stops_then_resumes(self, match, wt=True, tmp=True, branch=True):
        """止めた段までの片付けが済み、その先が残っていることを確かめてから、打ち直して完了させる。"""
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.install_failing_git()
        before, r = self.done_stopped_at(match)
        k = self.assert_landed_once(before, r)
        self.assertEqual(wt, os.path.exists(self.wt))
        self.assertEqual(tmp, os.path.exists(self.tmp_wt()))
        self.assertEqual(branch, self.has_branch())
        self.assertTrue(os.path.isfile(self.marker()))
        again = self.run_git_sh("integrate-done", "wt")
        self.assertOk(again)
        self.assert_fully_cleaned(before, k)

    # ---- 2: グローバルの excludes

    def test_a_globally_ignored_file_is_not_overwritten(self):
        excludes = os.path.join(self._tmp.name, "global-excludes")
        write(excludes, "*.gignored\n")
        config = os.path.join(self._tmp.name, "global-gitconfig")
        write(config, f"[core]\n\texcludesFile = {excludes.replace(os.sep, '/')}\n")
        env = {"GIT_CONFIG_GLOBAL": config}
        write(os.path.join(self.wt, "added.gignored"), "agent\n")
        git(self.wt, "add", "-f", "added.gignored")
        git(self.wt, "commit", "-q", "-m", "tracked despite the global ignore")
        write(os.path.join(self.ws, "added.gignored"), "user's globally ignored file\n")
        r = self.run_git_sh("integrate", "wt", env_extra=env)
        self.assertOk(r)
        self.assertIn("ignored のファイルと重なっている", r.stdout)
        self.assertIn("added.gignored", r.stdout)
        head = self.head()
        done = self.run_git_sh("integrate-done", "wt", "-m", "feat: x", env_extra=env)
        self.assertOk(done, 1)
        self.assertEqual(head, self.head())
        self.assertEqual(
            "user's globally ignored file\n", read(os.path.join(self.ws, "added.gignored"))
        )
        self.assertTrue(os.path.isfile(self.marker()))

    # ---- 3: 片付けの各段で止まっても、打ち直せば完了する

    def test_a_stop_at_the_work_worktree_resumes(self):
        self.stage_stops_then_resumes("worktree remove /")

    def test_a_stop_at_the_temp_worktree_resumes(self):
        self.stage_stops_then_resumes("worktree remove --force", wt=False)

    def test_a_stop_at_the_branch_resumes(self):
        self.stage_stops_then_resumes("update-ref -d refs/heads/wt", wt=False, tmp=False)

    # 実装の欠陥: d4_refs_drop は失敗を捨てて先へ進むため、止まらず終了コード 0 になり、ref が残る。
    # 打ち直しも「片付け済み」と答えて、ref を消さない。
    @unittest.expectedFailure
    def test_a_stop_at_the_dedicated_refs_resumes(self):
        self.stage_stops_then_resumes(
            "update-ref -d refs/ccnavi/direct/wt/", wt=False, tmp=False, branch=False
        )

    def test_a_marker_put_back_after_completion_is_dropped_without_moving_head(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.install_failing_git()
        before, r = self.done_stopped_at("worktree remove /")
        k = self.assert_landed_once(before, r)
        saved = read(self.marker())
        self.assertOk(self.run_git_sh("integrate-done", "wt"))
        self.assert_fully_cleaned(before, k)
        write(self.marker(), saved)
        again = self.run_git_sh("integrate-done", "wt")
        self.assertOk(again)
        self.assertFalse(os.path.exists(self.marker()))
        self.assertEqual(k, self.head())

    # ---- 4: プロジェクトの自己防衛の対象

    def test_a_guarded_file_of_a_project_needs_the_terminal(self):
        project = self.make_project("p")
        wt = self.cut("pwt", repo=project)
        rules = ".ccnavi/config/project/rules.yml"
        write(os.path.join(wt, *rules.split("/")), "ask: []\n")
        git(wt, "add", "-f", rules)
        git(wt, "commit", "-q", "-m", "rules")
        r = self.run_git_sh("integrate", "pwt", cwd=project)
        self.assertOk(r)
        self.assertIn("自己防衛", r.stdout)
        self.assertIn(rules, r.stdout)
        head = git(project, "rev-parse", "HEAD").strip()
        done = self.run_git_sh("integrate-done", "pwt", "-m", "feat: p", cwd=project)
        self.assertOk(done, 2)
        self.assertIn("端末", done.stderr)
        self.assertEqual(head, git(project, "rev-parse", "HEAD").strip())

    # ---- 5: 一時ワークツリーに残った ignored のファイル

    def test_done_keeps_an_ignored_file_left_in_the_temp_worktree(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        left = os.path.join(self.tmp_wt(), "residue.local")
        write(left, "somebody's ignored file\n")
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("取り込みました", r.stdout)
        self.assertIn("残したもの", r.stdout)
        self.assertIn("residue.local", r.stdout)
        self.assertEqual("somebody's ignored file\n", read(left))

    def test_abort_keeps_an_ignored_file_left_in_the_temp_worktree(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        left = os.path.join(self.tmp_wt(), "residue.local")
        write(left, "somebody's ignored file\n")
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r)
        self.assertIn("residue.local", r.stdout)
        self.assertEqual("somebody's ignored file\n", read(left))
        self.assertFalse(os.path.exists(self.marker()))

    # ---- 6, 7: 境界

    def test_only_the_project_moves_and_the_workspace_and_the_other_project_stay(self):
        p = self.make_project("p")
        q = self.make_project("q")
        wt = self.cut("pwt", repo=p)
        self.project_work(wt)
        ws_before = self.snapshot()
        q_before = self.repo_state(q)
        p_head = git(p, "rev-parse", "HEAD").strip()
        self.assertOk(self.run_git_sh("integrate", "pwt", cwd=p))
        self.assertOk(self.run_git_sh("integrate-done", "pwt", "-m", "feat: p", cwd=p))
        self.assertEqual("1", git(p, "rev-list", "--count", f"{p_head}..HEAD").strip())
        self.assertEqual("a\nagent\n", read(os.path.join(p, "a.txt")))
        self.assertEqual(ws_before, self.snapshot())
        self.assertEqual(q_before, self.repo_state(q))

    def test_a_stopped_cleanup_of_one_project_does_not_touch_the_other(self):
        p = self.make_project("p")
        q = self.make_project("q")
        qwt = self.cut("qwt", repo=q)
        self.project_work(qwt)
        self.assertOk(self.run_git_sh("integrate", "qwt", cwd=q))
        q_tmp = self.tmp_wt("qwt", "q")
        q_marker = os.path.join(self.common_dir(q), "ccnavi-integrate", "qwt", "marker")
        q_record = os.path.join(self.common_dir(q), "ccnavi-direct", "qwt")

        def q_state():
            return (
                os.path.isdir(q_tmp),
                git(q_tmp, "write-tree").strip(),
                git(q, "for-each-ref", "refs/ccnavi/direct/"),
                read(q_marker),
                read(q_record),
            )

        before_q = q_state()
        self.assertIn("refs/ccnavi/direct/qwt/t", before_q[2])
        self.assertIn("refs/ccnavi/direct/qwt/c", before_q[2])
        pwt = self.cut("pwt", repo=p)
        self.project_work(pwt)
        self.assertOk(self.run_git_sh("integrate", "pwt", cwd=p))
        p_head = git(p, "rev-parse", "HEAD").strip()
        self.install_failing_git()
        _, r = self.done_stopped_at("update-ref -d refs/heads/pwt", "pwt", cwd=p, base=p_head)
        k = self.assert_landed_once(p_head, r, cwd=p)
        self.assertEqual(before_q, q_state())
        again = self.run_git_sh("integrate-done", "pwt", cwd=p)
        self.assertOk(again)
        self.assertEqual(k, git(p, "rev-parse", "HEAD").strip())
        self.assertEqual("1", git(p, "rev-list", "--count", f"{p_head}..HEAD").strip())
        self.assertFalse(os.path.exists(pwt))
        self.assertFalse(os.path.exists(self.tmp_wt("pwt", "p")))
        self.assertEqual(before_q, q_state())


if __name__ == "__main__":
    unittest.main()
