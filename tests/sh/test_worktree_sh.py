"""ccnavi-worktree.sh の受入テスト。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
fetch のときだけ origin の URL を bare リポジトリへ向ける git の代役を PATH の先頭に置く
（tests/sh/test_start_sh.py と同じ作り）。

見るのは次のとおり。

1. ずれが無ければ確認なしで、取り込み先（ユーザのチェックアウトのブランチ）の HEAD から切ること
2. C4〜C7 のずれがあれば、切らずに終了コード 3 で止まり、報告と確認コードを出すこと。確認コードが
   今の状態と合わなければ報告からやり直すこと
3. 書きかけは --no-wip なら入らず、--copy-wip なら確認した時点のものが S になり、ユーザの
   index とワークツリーは変わらないこと
4. detached・チケットに結び付く・結び付くかを確かめられない・git 2.39 では断ること
5. 記録（version 4）が実行後チェックの読み取り（infra/directrec）で有効と読めること
6. 既にあるワークツリーは何も切らずにパスと取り込みの記録の有無を出すこと（冪等）、
   既にあるブランチ・使えない名前は断ること
7. --dry-run は何も作らないこと。廃止した --offline / --from-origin は終了コード 2
8. --adopt は、記録の無い既存のワークツリーを 2 回の呼び出し（確認コード）で v4 の記録に
   引き取ること。merge-base が複数なら断ること。記録の無いワークツリーの integrate は
   --adopt を案内して断ること
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

from tests import ROOT, SRC, common_sh, put_messages
from tests.draft import final

SHELL = shutil.which("sh") or shutil.which("bash")
REAL_GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
PYTHONPATH='{src}' exec '{python}' -m ccnavi "$@"
"""

# fetch のときだけ、origin の URL を bare リポジトリへ向ける git の代役。
# FAKE_FETCH_FAIL があれば失敗させる。FAKE_LS_REMOTE_FAIL があれば ls-remote だけ失敗させる。
# FAKE_GIT_VERSION があれば `git version` をその版で答える。
# FAKE_READ_TREE_FAIL があれば read-tree だけ失敗させる。
GIT = """#!/bin/sh
if [ "${{1:-}}" = version ] && [ -n "${{FAKE_GIT_VERSION:-}}" ]; then
  echo "git version $FAKE_GIT_VERSION"
  exit 0
fi
for a in "$@"; do
  case "$a" in
  read-tree)
    if [ -n "${{FAKE_READ_TREE_FAIL:-}}" ]; then
      echo "fatal: failed to read tree" >&2
      exit 128
    fi ;;
  ls-remote)
    if [ -n "${{FAKE_LS_REMOTE_FAIL:-}}" ]; then
      echo "fatal: unable to access 'https://example.invalid/': timed out" >&2
      exit 128
    fi ;;
  esac
done
for a in "$@"; do
  case "$a" in
  fetch | push | pull | ls-remote)
    if [ -n "${{FAKE_FETCH_FAIL:-}}" ]; then
      if [ "$FAKE_FETCH_FAIL" = 1 ]; then
        echo "fatal: unable to access 'https://example.invalid/': Could not resolve host" >&2
      else
        echo "$FAKE_FETCH_FAIL" >&2
      fi
      exit 128
    fi
    exec '{git}' -c "url.{bare}.insteadOf=$FAKE_ORIGIN_URL" "$@" ;;
  esac
done
exec '{git}' "$@"
"""

ORIGIN_URL = "https://github.com/acme/widgets.git"


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args):
    done = subprocess.run(
        [REAL_GIT, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout.strip()


@unittest.skipIf(SHELL is None or REAL_GIT is None, "sh か git が無い")
class WorktreeShTest(unittest.TestCase):
    WS_DIR = "ws"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.bare = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.bare)
        self.ws = os.path.join(base, self.WS_DIR)
        os.makedirs(self.ws)
        git(base, "init", "-q", "-b", "main", self.ws)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), "logs/\n.ccnavi/\n.claude/\n")
        git(self.ws, "add", ".gitignore")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "push", "-q", self.bare, "main")
        git(self.ws, "remote", "add", "origin", ORIGIN_URL)
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-worktree.sh", "ccnavi-git.sh", *common_sh(SH_DIR)):
            shutil.copy(final(f".ccnavi/scripts/{name}"), scripts)
        self.exe = write(
            os.path.join(base, "exe", "ccnavi"), EXE.format(src=SRC, python=sys.executable)
        )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.bin = os.path.join(base, "bin")
        os.chmod(
            write(os.path.join(self.bin, "git"), GIT.format(git=REAL_GIT, bare=self.bare)), 0o755
        )

    def run_sh(self, *args, fail_fetch=False, cwd=None, **extra):
        """fail_fetch が文字列なら、fetch はその文で失敗する。真なら名前が引けない失敗。"""
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        # 実行側が非対話（claude -p）でも、対話の出力を見る。非対話は extra で渡す。
        env.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
        env.update(
            {
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_ENTRY_PATH": self.exe,
                "FAKE_ORIGIN_URL": ORIGIN_URL,
            }
        )
        if fail_fetch:
            env["FAKE_FETCH_FAIL"] = fail_fetch if isinstance(fail_fetch, str) else "1"
        env.update(extra)
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-worktree.sh"), *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def wt(self, name):
        return os.path.join(self.ws, ".claude", "worktrees", name)

    def advance_origin(self):
        """origin の main に 1 コミット足す（手元の main はそのまま）。"""
        other = os.path.join(self._tmp.name, "other")
        if os.path.isdir(other):
            git(other, "pull", "-q", "--ff-only", "origin", "main")
        else:
            git(self._tmp.name, "clone", "-q", self.bare, other)
            for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
                git(other, "config", key, value)
        write(os.path.join(other, "new.txt"), "n" + str(len(os.listdir(other))))
        git(other, "add", "new.txt")
        git(other, "commit", "-q", "-m", "ahead")
        git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")

    def head_of(self, name):
        return git(self.wt(name), "rev-parse", "HEAD")

    # ---- 道具

    def snapshot_of_root(self):
        """ユーザの index とワークツリーの状態（変わらないことを確かめるため）。"""
        return (
            git(self.ws, "status", "--porcelain=v1", "--untracked-files=all"),
            git(self.ws, "diff", "--cached"),
            git(self.ws, "diff"),
            git(self.ws, "rev-parse", "HEAD"),
        )

    def record(self, name):
        path = os.path.join(self.ws, ".git", "ccnavi-direct", name)
        with open(path, encoding="utf-8") as f:
            return dict(line.split(" ", 1) for line in f.read().splitlines() if " " in line)

    def code_of(self, result):
        found = re.search(r"--accept ([0-9a-f]{8})", result.stdout)
        self.assertIsNotNone(found, result.stdout)
        return found.group(1)

    def make_feature_with_origin(self, branch="feature/x"):
        """統合先でないブランチへ移し、origin にも同じ位置で置く（C5 だけにヒットする）。"""
        git(self.ws, "checkout", "-q", "-b", branch)
        git(self.ws, "push", "-q", self.bare, f"{branch}:refs/heads/{branch}")
        git(self.ws, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")

    def dirty_root(self):
        """追跡済みの変更 1・新規 1・staged 1 の書きかけ。"""
        write(os.path.join(self.ws, "tracked.txt"), "v1\n")
        git(self.ws, "add", "tracked.txt")
        git(self.ws, "commit", "-q", "-m", "tracked")
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "push", "-q", self.bare, "main")
        write(os.path.join(self.ws, "tracked.txt"), "v2 書きかけ\n")
        write(os.path.join(self.ws, "fresh.txt"), "新規\n")
        write(os.path.join(self.ws, "staged.txt"), "staged\n")
        git(self.ws, "add", "staged.txt")

    # ---- ずれが無いとき

    def test_cuts_from_the_base_head_without_asking_when_nothing_is_off(self):
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertEqual("topic", git(self.wt("topic"), "branch", "--show-current"))
        self.assertIn("取り込み先: main（", result.stdout)
        self.assertIn("書きかけは含まない", result.stdout)
        self.assertIn("横で作業する: いいえ", result.stdout)
        upstream = subprocess.run(
            [REAL_GIT, "rev-parse", "--abbrev-ref", "topic@{upstream}"],
            cwd=self.ws,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, upstream.returncode, "取り込み先を追跡しない")

    def test_a_parent_tree_is_told_how_to_withdraw_what_stays_after_the_switch(self):
        # 親のワークツリーの名前（ws）の提案が承認待ちで、MR もある。直接作業へ切り替えても
        # 取り下げられないので、手順を 案内: で出す。
        put_messages(self.ws)
        write(
            os.path.join(self.ws, "wip", "proposals", "todo", f"{self.WS_DIR}.md"),
            f"---\nticket: {self.WS_DIR}\nissue: 5\nmr: 12\n---\n本文\n",
        )
        git(self.ws, "add", "wip")
        git(self.ws, "commit", "-q", "-m", "proposal")
        git(self.ws, "push", "-q", self.bare, "main")
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("案内: 親 ws の承認待ちの提案・着手中の子・MR は", result.stdout)
        self.assertIn(f"案内: 承認待ちの提案 {self.WS_DIR} が", result.stdout)
        self.assertIn("案内: MR #12 が残っている", result.stdout)
        self.assertNotIn("着手済みの子が", result.stdout)
        self.assertEqual(1, result.stdout.count("\n次: "), result.stdout)
        self.assertTrue(result.stdout.rstrip().splitlines()[-1].startswith("次: "))

    def test_nothing_is_said_about_leftovers_without_a_parent(self):
        put_messages(self.ws)
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertNotIn("取り下げ", result.stdout)
        self.assertNotIn("残っている", result.stdout)

    def test_the_record_is_written_and_read_as_valid_by_the_post_check(self):
        result = self.run_sh("topic", "--side-by-side")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        rec = self.record("topic")
        head = git(self.ws, "rev-parse", "HEAD")
        self.assertEqual("4", rec["version"])
        self.assertEqual("main", rec["base_branch"])
        self.assertEqual(head, rec["base_sha"])
        self.assertEqual(head, rec["snap_sha"])
        self.assertEqual("no", rec["snap_wip"])
        self.assertEqual("yes", rec["side_by_side"])
        self.assertEqual("-", rec["confirm"])
        self.assertEqual(head, git(self.ws, "rev-parse", "refs/ccnavi/direct/topic/s"))
        self.assertNotEqual(
            0,
            subprocess.run(
                [REAL_GIT, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/pending"],
                cwd=self.ws,
            ).returncode,
        )
        self.assertIn("横で作業する: はい", result.stdout)
        # 書き手の出力を、実行後チェックの読み取りが有効と読む。
        sys.path.insert(0, SRC)
        try:
            from ccnavi.infra import directrec, tree

            found = directrec.records_of(self.ws, tree.main_tree(self.ws))
        finally:
            sys.path.remove(SRC)
        self.assertEqual(["topic"], [r.name for r in found])
        self.assertEqual("main", found[0].base_branch)

    def test_side_by_side_alone_does_not_ask_here_the_ask_rule_does(self):
        result = self.run_sh("topic", "--side-by-side")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)

    def test_without_the_flag_the_record_says_no(self):
        self.assertEqual(0, self.run_sh("topic").returncode)
        self.assertEqual("no", self.record("topic")["side_by_side"])

    def test_a_project_is_cut_from_its_own_main_checkout_branch(self):
        """projects/app の取り込み先は、そのユーザのチェックアウトのブランチ（work）。
        デフォルトブランチ（origin/HEAD の trunk）と違うので、確認が要る。"""
        project = os.path.join(self.ws, "projects", "app")
        git(self._tmp.name, "init", "-q", "-b", "work", project)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(project, "config", key, value)
        git(project, "commit", "-q", "--allow-empty", "-m", "seed")
        git(project, "update-ref", "refs/remotes/origin/trunk", "HEAD")
        git(project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        result = self.run_sh("topic", "--dry-run", cwd=project)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("取り込み先: work（", result.stdout)
        self.assertIn("リモートのデフォルトは trunk", result.stdout)
        self.assertIn("デフォルトブランチ（trunk）でない", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    # ---- projects/ の変更をワークスペースルートから頼まれたときの案内

    PROJECTS_HINT = "projects/ の下のプロジェクトの変更を頼まれたなら"

    def add_project(self, name="app"):
        """ワークスペースの git が無視する projects/<名前> に、独立したリポジトリを置く。"""
        with open(os.path.join(self.ws, ".git", "info", "exclude"), "a", encoding="utf-8") as f:
            f.write("projects/\n")
        project = os.path.join(self.ws, "projects", name)
        git(self._tmp.name, "init", "-q", "-b", "work", project)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(project, "config", key, value)
        git(project, "commit", "-q", "--allow-empty", "-m", "seed")
        git(project, "update-ref", "refs/remotes/origin/work", "HEAD")
        git(project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/work")
        return project

    def test_a_cut_from_the_root_with_projects_points_at_cd(self):
        self.add_project()
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(self.PROJECTS_HINT, result.stdout)
        self.assertEqual(1, result.stdout.count("案内: projects/ の下"))
        self.assertTrue(os.path.isdir(self.wt("topic")))

    def test_the_hint_comes_before_the_next_step(self):
        self.add_project()
        lines = self.run_sh("topic").stdout.splitlines()
        hint = next(i for i, ln in enumerate(lines) if self.PROJECTS_HINT in ln)
        step = next(i for i, ln in enumerate(lines) if ln.startswith("次: "))
        self.assertLess(hint, step)

    def test_a_workspace_without_projects_gets_no_hint(self):
        """projects/ が無い形（単体 clone のように、ルートがそのリポジトリ）では出さない。"""
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn(self.PROJECTS_HINT, result.stdout)

    def test_a_directory_in_projects_without_a_repository_gets_no_hint(self):
        os.makedirs(os.path.join(self.ws, "projects", "notes"))
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn(self.PROJECTS_HINT, result.stdout)

    def test_a_cut_from_inside_the_project_gets_no_hint(self):
        project = self.add_project()
        first = self.run_sh("topic", cwd=project)
        self.assertEqual(3, first.returncode, first.stdout + first.stderr)
        done = self.run_sh("topic", "--accept", self.code_of(first), cwd=project)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn(self.PROJECTS_HINT, done.stdout)

    def test_the_hint_is_not_printed_by_dry_run(self):
        self.add_project()
        result = self.run_sh("topic", "--dry-run")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn(self.PROJECTS_HINT, result.stdout)

    def test_a_free_name_is_warned_by_dry_run_too(self):
        result = self.run_sh("topic", "--dry-run")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("warn: 直接作業のワークツリー名 topic が", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_a_free_name_is_warned_before_the_cut_and_still_cut(self):
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("warn: 直接作業のワークツリー名 topic が", result.stdout)
        self.assertEqual("topic", git(self.wt("topic"), "branch", "--show-current"))

    def test_a_name_in_the_form_is_not_warned(self):
        result = self.run_sh("feature-12-topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertNotIn("warn:", result.stdout)

    def test_the_last_line_is_the_next_step_with_one_line_commands(self):
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        last = result.stdout.rstrip("\n").split("\n")[-1]
        self.assertTrue(last.startswith("次: 編集はこのワークツリーの下で行います"), last)
        self.assertIn(f"Write / Edit に渡すパス: {self.wt('topic')}/…", last)
        self.assertIn("ターンの終わりの integrate の案内に従います", last)
        # 名前の警告よりも後ろ。
        self.assertGreater(result.stdout.rindex("次:"), result.stdout.index("warn:"))
        # 案内の git は、書いたとおりに新しいシェルで打てる。
        command = re.search(r"`(cd .+? <サブコマンド>)`", last).group(1)
        command = command.replace("<サブコマンド>", "rev-parse --abbrev-ref HEAD")
        done = subprocess.run([SHELL, "-c", command], cwd="/", capture_output=True, text=True)
        self.assertEqual("topic", done.stdout.strip().splitlines()[-1], done.stderr)
        # 既にあるときも、最後の行は同じ。
        again = self.run_sh("topic")
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertTrue(again.stdout.rstrip("\n").split("\n")[-1].startswith("次: 編集は"))

    # ---- C4: cwd のブランチが取り込み先と違う

    def test_a_cwd_branch_that_differs_from_the_base_needs_the_base_flag(self):
        git(self.ws, "worktree", "add", "-q", self.wt("other"), "-b", "other")
        result = self.run_sh("topic", cwd=self.wt("other"))
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("cwd のブランチは other で、取り込み先と違う", result.stdout)
        self.assertIn("--base 'main'", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        self.assertEqual("", git(self.ws, "branch", "--list", "topic"))
        done = self.run_sh("topic", "--base", "main", cwd=self.wt("other"))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))

    def test_a_base_that_is_not_the_current_base_is_refused(self):
        result = self.run_sh("topic", "--base", "feature/y")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("今の取り込み先（main", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    # ---- C5: 取り込み先が統合先でない

    def test_a_base_other_than_the_default_branch_needs_the_accept_code(self):
        self.make_feature_with_origin()
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("取り込み先 feature/x (", result.stdout)
        self.assertIn("リモートのデフォルトは main", result.stdout)
        self.assertIn("取り込み先がデフォルトブランチ（main）でない", result.stdout)
        self.assertNotIn("書きかけ:", result.stdout)
        code = self.code_of(result)
        self.assertFalse(os.path.exists(self.wt("topic")))
        done = self.run_sh("topic", "--accept", code)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(git(self.ws, "rev-parse", "feature/x"), self.head_of("topic"))
        self.assertEqual(code, self.record("topic")["confirm"])
        self.assertEqual("feature/x", self.record("topic")["base_branch"])

    def test_a_wrong_accept_code_starts_the_report_over(self):
        self.make_feature_with_origin()
        result = self.run_sh("topic", "--accept", "deadbeef")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("確認コードが今の状態と合わない", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_the_code_changes_when_the_state_moves(self):
        self.make_feature_with_origin()
        code = self.code_of(self.run_sh("topic"))
        write(os.path.join(self.ws, "more.txt"), "m")
        git(self.ws, "add", "more.txt")
        git(self.ws, "commit", "-q", "-m", "moved")
        git(self.ws, "update-ref", "refs/remotes/origin/feature/x", "HEAD")
        git(self.ws, "push", "-q", self.bare, "feature/x")
        result = self.run_sh("topic", "--accept", code)
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("合わない", result.stdout)

    # ---- C6: origin/<取り込み先> との関係

    def test_local_behind_origin_is_reported_and_cut_from_local_after_accept(self):
        ahead = self.advance_origin()
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("origin/main より 1 件遅れている", result.stdout)
        self.assertIn("git pull --ff-only origin 'main'", result.stdout)
        self.assertIn("古いコードの上で作業する", result.stdout)
        code = self.code_of(result)
        done = self.run_sh("topic", "--accept", code)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotEqual(ahead, self.head_of("topic"), "origin からは切らない")
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertNotEqual(ahead, git(self.ws, "rev-parse", "main"), "手元の取り込み先は進めない")
        self.assertIn("案内: 手元の main は origin/main より古い", done.stdout)

    def test_local_ahead_of_origin_needs_the_accept_code(self):
        write(os.path.join(self.ws, "local.txt"), "l")
        git(self.ws, "add", "local.txt")
        git(self.ws, "commit", "-q", "-m", "local only")
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("origin/main より 1 件進んでいる", result.stdout)
        done = self.run_sh("topic", "--accept", self.code_of(result))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))

    def diverge(self):
        """手元の main にだけ 1 コミット足し、origin にも別の 1 コミットを足す（分岐）。"""
        write(os.path.join(self.ws, "local.txt"), "l")
        git(self.ws, "add", "local.txt")
        git(self.ws, "commit", "-q", "-m", "local only")
        return self.advance_origin()

    def test_diverged_local_offers_both_ways_to_match(self):
        self.diverge()
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("分岐している（先行 1 件・遅れ 1 件）", result.stdout)
        self.assertIn("git pull --rebase origin 'main'", result.stdout)
        self.assertIn("git pull --no-rebase origin 'main'", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_a_branch_missing_on_origin_needs_the_accept_code(self):
        git(self.ws, "checkout", "-q", "-b", "local-only")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("origin に local-only が無い", result.stdout)
        done = self.run_sh("topic", "--accept", self.code_of(result))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_fetch_failure_is_an_unverified_remote_and_asks_for_the_accept_code(self):
        result = self.run_sh("topic", fail_fetch=True)
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")), "切らずに止まる")
        self.assertIn("リモート未確認", result.stdout)
        self.assertIn("Could not resolve host", result.stderr, "原因の出力を見せる")
        done = self.run_sh("topic", "--accept", self.code_of(result), fail_fetch=True)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertIn("リモート未確認で切った", done.stdout)
        self.assertIn("ユーザにも伝える", done.stdout)

    def test_missing_remote_branch_is_called_out(self):
        result = self.run_sh("topic", fail_fetch="fatal: couldn't find remote ref main")
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("origin に main が無い", result.stdout)

    def test_an_unreadable_branch_list_of_origin_is_an_unverified_remote(self):
        result = self.run_sh("topic", FAKE_LS_REMOTE_FAIL="1")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("リモート未確認", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        done = self.run_sh("topic", "--accept", self.code_of(result), FAKE_LS_REMOTE_FAIL="1")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    # ---- C7: 書きかけ

    def test_wip_without_a_choice_stops_even_with_the_code(self):
        self.dirty_root()
        result = self.run_sh("topic")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("書きかけ: 変更 2・新規 1（staged 1", result.stdout)
        self.assertIn("--no-wip", result.stdout)
        self.assertIn("--copy-wip", result.stdout)
        code = self.code_of(result)
        again = self.run_sh("topic", "--accept", code)
        self.assertEqual(3, again.returncode, "書きかけの選択が無ければ、コードがあっても止まる")
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_no_wip_cuts_from_head_and_leaves_the_wip_only_in_the_root(self):
        self.dirty_root()
        code = self.code_of(self.run_sh("topic"))
        before = self.snapshot_of_root()
        done = self.run_sh("topic", "--accept", code, "--no-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(git(self.ws, "rev-parse", "HEAD"), self.head_of("topic"))
        self.assertFalse(os.path.exists(os.path.join(self.wt("topic"), "fresh.txt")))
        with open(os.path.join(self.wt("topic"), "tracked.txt"), encoding="utf-8") as f:
            self.assertEqual("v1\n", f.read())
        self.assertEqual(before, self.snapshot_of_root())
        self.assertEqual("no", self.record("topic")["snap_wip"])
        self.assertIn("書きかけは含まない", done.stdout)

    def test_copy_wip_takes_the_wip_at_the_time_of_confirmation(self):
        self.dirty_root()
        first = self.run_sh("topic")
        code = self.code_of(first)
        # 候補は確認の時点で pending に置かれる。ユーザの index とワークツリーは変わらない。
        pending = git(self.ws, "rev-parse", "refs/ccnavi/direct/topic/pending")
        before = self.snapshot_of_root()
        done = self.run_sh("topic", "--accept", code, "--copy-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(pending, self.head_of("topic"))
        self.assertEqual(pending, git(self.ws, "rev-parse", "refs/ccnavi/direct/topic/s"))
        self.assertEqual(
            git(self.ws, "rev-parse", "HEAD"), git(self.ws, "rev-parse", f"{pending}^")
        )
        for rel, want in (
            ("tracked.txt", "v2 書きかけ\n"),
            ("fresh.txt", "新規\n"),
            ("staged.txt", "staged\n"),
        ):
            with open(os.path.join(self.wt("topic"), rel), encoding="utf-8") as f:
                self.assertEqual(want, f.read(), rel)
        self.assertEqual("yes", self.record("topic")["snap_wip"])
        self.assertEqual(pending, self.record("topic")["snap_sha"])
        self.assertEqual(
            before, self.snapshot_of_root(), "ユーザの index とワークツリーは変わらない"
        )
        self.assertNotEqual(
            0,
            subprocess.run(
                [REAL_GIT, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/pending"],
                cwd=self.ws,
            ).returncode,
            "切ったら pending は消える",
        )
        self.assertIn("確認した時点の書きかけを含む", done.stdout)

    def test_an_edit_after_the_confirmation_is_not_copied(self):
        self.dirty_root()
        code = self.code_of(self.run_sh("topic"))
        write(os.path.join(self.ws, "fresh.txt"), "確認のあとの編集\n")
        result = self.run_sh("topic", "--accept", code, "--copy-wip")
        self.assertEqual(3, result.returncode, "確認のあとに動いたら、報告からやり直す")
        self.assertIn("合わない", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_the_candidate_does_not_touch_the_index_even_when_only_asking(self):
        self.dirty_root()
        before = self.snapshot_of_root()
        self.run_sh("topic")
        self.assertEqual(before, self.snapshot_of_root())

    # ---- .claude/worktrees/ は書きかけに数えない（.gitignore に行が無い配布先）

    def leave_worktrees_untracked(self):
        """.gitignore から .claude/ を外し、README.md を足す。

        最初のワークツリーを切ると、置き場が未追跡で残る。
        """
        write(os.path.join(self.ws, ".gitignore"), "logs/\n.ccnavi/\n")
        write(os.path.join(self.ws, "README.md"), "# readme\n")
        git(self.ws, "add", ".gitignore", "README.md")
        git(self.ws, "commit", "-q", "-m", "no claude ignore")
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "push", "-q", self.bare, "main")
        first = self.run_sh("first")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertIn(
            "?? .claude/worktrees/",
            git(self.ws, "status", "--porcelain", "--untracked-files=all"),
            "前提: 置き場が未追跡で残っている",
        )

    def test_the_worktrees_dir_alone_is_not_wip(self):
        self.leave_worktrees_untracked()
        dry = self.run_sh("second", "--dry-run")
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertNotIn("書きかけ", dry.stdout)
        done = self.run_sh("second")
        self.assertEqual(0, done.returncode, "確認なしで切れる: " + done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt("second")))

    def test_real_wip_still_needs_the_confirmation(self):
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, "README.md"), "# readme 書きかけ\n")
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        result = self.run_sh("second")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("書きかけ: 変更 1・新規 1（staged 0", result.stdout)
        self.assertFalse(os.path.exists(self.wt("second")))

    def test_the_count_leaves_out_the_worktrees_dir(self):
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        dry = self.run_sh("second", "--dry-run")
        self.assertIn("書きかけ 変更 0・新規 1（staged 0）", dry.stdout)

    def test_staged_files_in_the_worktrees_dir_are_not_counted(self):
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, ".claude", "worktrees", "stray.txt"), "s\n")
        git(self.ws, "add", "-f", ".claude/worktrees/stray.txt")
        dry = self.run_sh("second", "--dry-run")
        self.assertNotIn("書きかけ", dry.stdout)

    def test_other_untracked_places_still_count(self):
        """除くのは .claude/worktrees/ だけ。wip/ の新規などは今のまま数える。"""
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, "wip", "handoff", "a.md"), "a\n")
        dry = self.run_sh("second", "--dry-run")
        self.assertIn("書きかけ 変更 0・新規 1", dry.stdout)

    def test_the_snapshot_does_not_take_the_other_worktrees(self):
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        done = self.run_sh("second", "--accept", code, "--copy-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        tree = git(self.wt("second"), "ls-tree", "-r", "HEAD")
        self.assertIn("notes.txt", tree)
        self.assertNotIn(".claude/worktrees", tree)
        self.assertNotIn("160000", tree)

    # ---- 置き場が .gitignore 済みでも、書きかけを含むスナップショットを作れる

    def leave_worktrees_ignored(self, line="/.claude/worktrees/"):
        """置き場の行を .gitignore に入れてから、最初のワークツリーを切る。"""
        write(os.path.join(self.ws, ".gitignore"), f"logs/\n.ccnavi/\n{line}\n")
        write(os.path.join(self.ws, "README.md"), "# readme\n")
        git(self.ws, "add", ".gitignore", "README.md")
        git(self.ws, "commit", "-q", "-m", "ignore worktrees")
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "push", "-q", self.bare, "main")
        first = self.run_sh("first")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertTrue(os.path.isdir(self.wt("first")), "前提: 他のワークツリーが 1 本ある")
        self.assertNotIn(
            ".claude/worktrees",
            git(self.ws, "status", "--porcelain", "--untracked-files=all"),
            "前提: 置き場は無視されている",
        )

    def assert_snapshot_tree_clean(self, name):
        tree = git(self.wt(name), "ls-tree", "-r", "HEAD")
        self.assertNotIn(".claude/worktrees", tree)
        self.assertNotIn("160000", tree)

    def test_ignored_worktrees_dir_and_a_new_file_copy_wip(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        done = self.run_sh("second", "--accept", code, "--copy-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("作れませんでした", done.stdout + done.stderr)
        with open(os.path.join(self.wt("second"), "notes.txt"), encoding="utf-8") as f:
            self.assertEqual("メモ\n", f.read())
        self.assert_snapshot_tree_clean("second")

    def test_ignored_worktrees_dir_and_a_new_file_no_wip(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        done = self.run_sh("second", "--accept", code, "--no-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt("second")))
        self.assertFalse(os.path.exists(os.path.join(self.wt("second"), "notes.txt")))
        self.assert_snapshot_tree_clean("second")

    def test_ignored_worktrees_dir_and_a_new_file_asks_for_confirmation(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        result = self.run_sh("second")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("作れませんでした", result.stdout + result.stderr)
        self.assertIn("書きかけ: 変更 0・新規 1", result.stdout)
        self.code_of(result)
        self.assertFalse(os.path.exists(self.wt("second")))

    def test_ignored_worktrees_dir_and_a_tracked_change_asks_for_confirmation(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "README.md"), "# readme 書きかけ\n")
        result = self.run_sh("second")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("作れませんでした", result.stdout + result.stderr)
        self.assertIn("書きかけ: 変更 1・新規 0", result.stdout)
        code = self.code_of(result)
        done = self.run_sh("second", "--accept", code, "--copy-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        with open(os.path.join(self.wt("second"), "README.md"), encoding="utf-8") as f:
            self.assertEqual("# readme 書きかけ\n", f.read())
        self.assert_snapshot_tree_clean("second")

    def test_untracked_worktrees_dir_keeps_gitlinks_out_of_the_snapshot(self):
        self.leave_worktrees_untracked()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        done = self.run_sh("second", "--accept", code, "--copy-wip")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assert_snapshot_tree_clean("second")

    def test_a_failing_read_tree_still_stops_with_snapshot_failed(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        result = self.run_sh("second", "--accept", code, "--copy-wip", FAKE_READ_TREE_FAIL="1")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("スナップショットを作れませんでした", result.stdout + result.stderr)

    # ---- 確認コードが縛るもの（外すと落ちる）

    def test_the_code_depends_on_the_head_when_origin_has_no_such_branch(self):
        git(self.ws, "checkout", "-q", "-b", "local-only")
        code = self.code_of(self.run_sh("topic"))
        write(os.path.join(self.ws, "more.txt"), "m")
        git(self.ws, "add", "more.txt")
        git(self.ws, "commit", "-q", "-m", "moved")
        result = self.run_sh("topic", "--accept", code)
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("合わない", result.stdout)

    def test_the_code_depends_on_the_origin_sha(self):
        self.advance_origin()
        code = self.code_of(self.run_sh("topic"))
        self.advance_origin()
        result = self.run_sh("topic", "--accept", code)
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("合わない", result.stdout)

    def test_the_code_depends_on_where_it_was_run_from(self):
        self.make_feature_with_origin()
        a = os.path.join(self._tmp.name, "side-a")
        b = os.path.join(self._tmp.name, "side-b")
        git(self.ws, "worktree", "add", "-q", a, "-b", "side-a")
        git(self.ws, "worktree", "add", "-q", b, "-b", "side-b")
        first = self.run_sh("topic", "--base", "feature/x", cwd=a, CCNAVI_WORKSPACE=self.ws)
        self.assertEqual(3, first.returncode, first.stdout + first.stderr)
        result = self.run_sh(
            "topic",
            "--base",
            "feature/x",
            "--accept",
            self.code_of(first),
            cwd=b,
            CCNAVI_WORKSPACE=self.ws,
        )
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("合わない", result.stdout)

    def test_the_code_depends_on_whether_the_remote_was_verified(self):
        self.make_feature_with_origin()
        unverified = self.run_sh("topic", FAKE_LS_REMOTE_FAIL="1")
        self.assertEqual(3, unverified.returncode, unverified.stdout + unverified.stderr)
        self.assertIn("リモート未確認", unverified.stdout)
        result = self.run_sh("topic", "--accept", self.code_of(unverified))
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn("合わない", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    # ---- ルートを読むだけで書かない・終わっていない取り込みを取り違えない

    def test_reading_the_root_does_not_rewrite_its_index(self):
        self.dirty_root()
        # 中身は同じで stat だけ違う追跡済みのファイル。git status が index を書き直す元になる。
        path = os.path.join(self.ws, ".gitignore")
        later = os.stat(path).st_mtime + 5
        os.utime(path, (later, later))
        index = os.path.join(self.ws, ".git", "index")
        with open(index, "rb") as f:
            before = f.read()
        self.run_sh("topic")
        with open(index, "rb") as f:
            self.assertEqual(before, f.read())

    def test_a_marker_that_is_left_stops_the_cut(self):
        marker = os.path.join(self.ws, ".git", "ccnavi-integrate", "topic", "marker")
        write(marker, "version 4\nk abc\n")
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("取り込みの記録", result.stderr)
        self.assertIn("integrate-done topic", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_adopt_refuses_a_name_bound_to_a_ticket(self):
        self.uncorded_worktree()
        git(self.ws, "update-ref", "refs/remotes/origin/topic", "HEAD")
        result = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("チケットに結び付く", result.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    # ---- C1〜C3: 先に断るもの

    def test_a_detached_main_checkout_is_refused(self):
        git(self.ws, "checkout", "-q", "--detach")
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("detached HEAD", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_a_base_tied_to_a_ticket_is_refused(self):
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", "t1.md"),
            "---\nticket: t1\nbranch: main\n---\n本文\n",
        )
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("チケットに結び付く", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_an_unanswered_base_bound_is_refused(self):
        result = self.run_sh("topic", CCNAVI_ENTRY_PATH=os.path.join(self._tmp.name, "none"))
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_git_before_2_40_is_refused(self):
        result = self.run_sh("topic", FAKE_GIT_VERSION="2.39.5")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("2.40 未満", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))
        ok = self.run_sh("topic", FAKE_GIT_VERSION="2.40.0")
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)

    # ---- 引数

    def test_retired_arguments_exit_2_with_guidance(self):
        for arg, why in (
            ("--offline", "廃止しました"),
            ("--from-origin", "廃止しました"),
        ):
            with self.subTest(arg=arg):
                result = self.run_sh("topic", arg)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(why, result.stderr)
                self.assertIn("--accept", result.stderr)
                self.assertFalse(os.path.exists(self.wt("topic")))

    def test_copy_wip_and_no_wip_together_are_refused(self):
        result = self.run_sh("topic", "--copy-wip", "--no-wip")
        self.assertEqual(2, result.returncode, result.stdout)

    def test_existing_worktree_is_reported_not_recreated(self):
        self.run_sh("topic")
        write(os.path.join(self.wt("topic"), "keep.txt"), "mine")
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("既にある", result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt("topic"), "keep.txt")))

    def test_existing_worktree_reports_whether_the_record_exists(self):
        # 切ったワークツリーには記録があり、integrate に進めます。
        self.run_sh("topic")
        result = self.run_sh("topic")
        self.assertIn("取り込みの記録は、ある（ccnavi-direct/topic）", result.stdout)
        self.assertNotIn("--adopt", result.stdout)
        # 手で切ったワークツリーには記録が無いため、引き取る手を言います。
        git(self.ws, "worktree", "add", "-q", self.wt("manual"), "-b", "manual", "main")
        result = self.run_sh("manual")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("取り込みの記録は、無い（ccnavi-direct/manual）", result.stdout)
        self.assertIn("manual' --adopt --base <取り込み先>", result.stdout)
        self.assertNotIn("確認に答えられません", result.stdout)
        # 最後の行は、従来どおり次の一手です。
        self.assertTrue(result.stdout.rstrip("\n").split("\n")[-1].startswith("次: 編集は"))

    def test_existing_branch_points_to_adopt_as_the_second_step(self):
        git(self.ws, "branch", "taken")
        result = self.run_sh("taken")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("1 段目: sh ", result.stderr)
        self.assertIn("worktree add", result.stderr)
        self.assertIn("2 段目: sh ", result.stderr)
        self.assertIn("taken' --adopt --base <統合先>", result.stderr)
        self.assertIn("ユーザの確認が要ります", result.stderr)
        self.assertNotIn("確認に答えられません", result.stderr)

    def test_unattended_adds_the_fixed_line_only_when_the_variable_is_zero(self):
        git(self.ws, "branch", "taken")
        git(self.ws, "worktree", "add", "-q", self.wt("manual"), "-b", "manual", "main")
        for name in ("taken", "manual"):
            for value, shown in (("0", True), ("1", False), (None, False)):
                with self.subTest(name=name, value=value):
                    with mock.patch.dict(os.environ):
                        os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
                        extra = {} if value is None else {"CLAUDE_CODE_SESSION_ATTENDED": value}
                        result = self.run_sh(name, **extra)
                    said = result.stdout + result.stderr
                    self.assertEqual(shown, "このセッションでは確認に答えられません" in said, said)

    def test_refuses_bad_names_and_existing_branch(self):
        git(self.ws, "branch", "taken")
        for args, code, why in (
            (("a/b",), 2, "/ は書けません"),
            (("taken",), 1, "既にあります"),
            ((), 2, "名前を渡してください"),
            (("a", "b"), 2, "1 本だけ"),
            (("--bogus",), 2, "未対応の引数"),
        ):
            with self.subTest(args=args):
                result = self.run_sh(*args)
                self.assertEqual(code, result.returncode, result.stdout)
                self.assertIn(why, result.stderr)

    def test_same_name_branch_on_origin_stops_and_points_to_start(self):
        # origin にだけ同名のブランチがある（MR の元ブランチかもしれない）。
        # 新しく切らず ccnavi-start.sh へ案内する
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic")
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("origin にブランチ topic が既にある", result.stdout)
        self.assertIn("ccnavi-start.sh' --mr", result.stdout)
        self.assertIn("ccnavi-start.sh' --issue", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        # 別の名前なら切れる
        self.assertEqual(0, self.run_sh("topic2").returncode)

    def test_a_branch_of_the_same_identifier_on_origin_stops(self):
        """origin に同じ識別子になるブランチ（feature/7-x、大文字小文字違い）があれば切らない。"""
        for other, name in (("feature/7-x", "feature-7-x"), ("Topic", "topic")):
            with self.subTest(other=other):
                git(self.ws, "push", "-q", self.bare, f"main:refs/heads/{other}")
                result = self.run_sh(name)
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIn(other, result.stdout)
                self.assertIn("ccnavi-start.sh' --mr", result.stdout)
                self.assertFalse(os.path.exists(self.wt(name)))

    def test_a_local_branch_of_the_same_identifier_stops(self):
        git(self.ws, "branch", "feature/7-x")
        result = self.run_sh("feature-7-x")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("feature/7-x", result.stdout)
        self.assertFalse(os.path.exists(self.wt("feature-7-x")))

    def test_the_same_identifier_is_refused_before_the_existing_directory_check(self):
        """大文字小文字を区別しない機械では、綴り違いのパスが既にあるように見える。
        ここでは同じ綴りのただのディレクトリで再現し、同じ識別子だという案内で断ることを確かめる。"""
        git(self.ws, "branch", "Topic")
        stray = write(os.path.join(self.wt("topic"), "stray.txt"), "x")
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("Topic", result.stdout)
        self.assertIn("同じ識別子", result.stdout)
        self.assertNotIn("既にあります", result.stderr)
        self.assertTrue(os.path.isfile(stray))

    def test_rerunning_the_same_name_still_reports_the_worktree_exists(self):
        self.assertEqual(0, self.run_sh("topic", "--no-wip").returncode)
        result = self.run_sh("topic", "--no-wip")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("ワークツリーは既にある", result.stdout)

    def test_names_the_executable_refuses_are_not_cut(self):
        """全角の字・NFD・65 文字以上の名前は、実行ファイルの検査で断る（sh の検査は通る）。"""
        nfd = unicodedata.normalize("NFD", "feature-が")
        for name in ("feature-１２３", nfd, "topic-" + "a" * 59):
            with self.subTest(name=name):
                result = self.run_sh(name)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn("指定できません", result.stderr)
                self.assertFalse(os.path.exists(self.wt(name)))

    def test_existing_directory_that_is_not_the_worktree_is_refused(self):
        write(os.path.join(self.wt("topic"), "stray.txt"), "x")
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("ワークツリーではありません", result.stderr)
        git(self.ws, "worktree", "add", "-q", self.wt("other"), "-b", "other")
        os.rename(self.wt("other"), self.wt("renamed-away"))
        git(self.ws, "worktree", "repair", self.wt("renamed-away"))
        result = self.run_sh("renamed-away")
        self.assertEqual(1, result.returncode, "別のブランチを開いたワークツリーは成功にしない")
        self.assertIn("other", result.stderr)

    def test_a_refused_worktree_add_removes_the_record_and_the_ref(self):
        """worktree add が通らなければ、記録と S の ref を残さない。"""
        os.makedirs(os.path.join(self.ws, ".claude", "worktrees"), exist_ok=True)
        # 行き先の親をファイルにして worktree add を失敗させる。
        shutil.rmtree(os.path.join(self.ws, ".claude", "worktrees"))
        write(os.path.join(self.ws, ".claude", "worktrees"), "file")
        result = self.run_sh("topic")
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".git", "ccnavi-direct", "topic")))
        self.assertNotEqual(
            0,
            subprocess.run(
                [REAL_GIT, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/s"],
                cwd=self.ws,
            ).returncode,
        )

    def test_dry_run_creates_nothing(self):
        result = self.run_sh("topic", "--dry-run", fail_fetch=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        self.assertEqual("", git(self.ws, "branch", "--list", "topic"))
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".git", "ccnavi-direct")))

    def test_dry_run_reports_the_wip_and_what_would_be_needed(self):
        self.dirty_root()
        result = self.run_sh("topic", "--dry-run")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("書きかけ 変更 2・新規 1", result.stdout)
        self.assertIn("--copy-wip か --no-wip", result.stdout)
        self.assertNotEqual(
            0,
            subprocess.run(
                [REAL_GIT, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/pending"],
                cwd=self.ws,
            ).returncode,
        )

    # ---- 引き取り（--adopt）

    def uncorded_worktree(self, name="topic", commits=2):
        """記録の無い直接作業のワークツリー（更新前に切った・手で切ったもの）。"""
        git(self.ws, "worktree", "add", "-q", self.wt(name), "-b", name)
        for index in range(commits):
            write(os.path.join(self.wt(name), f"w{index}.txt"), f"w{index}\n")
            git(self.wt(name), "add", "-A")
            git(self.wt(name), "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"work {index}")

    def record_path(self, name):
        return os.path.join(self.ws, ".git", "ccnavi-direct", name)

    def test_adopt_shows_the_range_and_the_code_first_and_writes_nothing(self):
        self.uncorded_worktree()
        first = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(3, first.returncode, first.stdout + first.stderr)
        self.assertIn("この範囲全体をエージェントの成果として扱う", first.stdout)
        self.assertIn("work 0", first.stdout)
        self.assertIn("work 1", first.stdout)
        self.assertIn("w1.txt", first.stdout)
        self.assertIn(git(self.ws, "rev-parse", "--short", "main"), first.stdout)
        self.code_of(first)
        self.assertFalse(os.path.exists(self.record_path("topic")))
        self.assertEqual("", git(self.ws, "for-each-ref", "refs/ccnavi/direct/topic"))

    def test_adopt_with_the_code_writes_the_record_and_the_ref(self):
        self.uncorded_worktree()
        before = self.snapshot_of_root()
        code = self.code_of(self.run_sh("topic", "--adopt", "--base", "main"))
        done = self.run_sh("topic", "--adopt", "--base", "main", "--accept", code)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        base = git(self.ws, "rev-parse", "main")
        record = self.record("topic")
        self.assertEqual("4", record["version"])
        self.assertEqual("main", record["base_branch"])
        self.assertEqual(base, record["snap_sha"])
        self.assertEqual("no", record["snap_wip"])
        self.assertEqual("no", record["side_by_side"])
        self.assertEqual("adopt", record["confirm"])
        self.assertEqual(base, git(self.ws, "rev-parse", "refs/ccnavi/direct/topic/s"))
        self.assertIn("取り込み先: main", done.stdout)
        self.assertEqual(before, self.snapshot_of_root())

    def test_adopt_side_by_side_is_only_set_when_given(self):
        self.uncorded_worktree()
        code = self.code_of(self.run_sh("topic", "--adopt", "--base", "main"))
        done = self.run_sh("topic", "--adopt", "--base", "main", "--accept", code, "--side-by-side")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("yes", self.record("topic")["side_by_side"])

    def test_adopt_with_a_wrong_code_starts_over(self):
        self.uncorded_worktree()
        wrong = self.run_sh("topic", "--adopt", "--base", "main", "--accept", "deadbeef")
        self.assertEqual(3, wrong.returncode, wrong.stdout + wrong.stderr)
        self.assertIn("確認コードが今の状態と合わない", wrong.stdout)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_adopt_code_changes_when_the_branch_moves(self):
        self.uncorded_worktree()
        code = self.code_of(self.run_sh("topic", "--adopt", "--base", "main"))
        write(os.path.join(self.wt("topic"), "more.txt"), "m\n")
        git(self.wt("topic"), "add", "-A")
        git(self.wt("topic"), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "more")
        moved = self.run_sh("topic", "--adopt", "--base", "main", "--accept", code)
        self.assertEqual(3, moved.returncode, moved.stdout + moved.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_adopt_needs_the_base_and_it_must_be_the_current_base(self):
        self.uncorded_worktree()
        none = self.run_sh("topic", "--adopt")
        self.assertEqual(2, none.returncode, none.stdout + none.stderr)
        self.assertIn("--base", none.stderr)
        git(self.ws, "branch", "other")
        wrong = self.run_sh("topic", "--adopt", "--base", "other")
        self.assertEqual(1, wrong.returncode, wrong.stdout + wrong.stderr)
        self.assertIn("今の取り込み先", wrong.stderr)

    def test_adopt_refuses_when_there_are_several_merge_bases(self):
        self.uncorded_worktree(commits=0)
        write(os.path.join(self.ws, "x.txt"), "x\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "x on main")
        write(os.path.join(self.wt("topic"), "y.txt"), "y\n")
        git(self.wt("topic"), "add", "-A")
        git(self.wt("topic"), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "y on topic")
        # 互いを merge して、共通の祖先が 2 つになる（交差する merge）。
        topic_y = git(self.wt("topic"), "rev-parse", "HEAD")
        main_x = git(self.ws, "rev-parse", "HEAD")
        git(self.ws, "-c", "commit.gpgsign=false", "merge", "-q", "--no-edit", topic_y)
        git(self.wt("topic"), "-c", "commit.gpgsign=false", "merge", "-q", "--no-edit", main_x)
        self.assertEqual(2, len(git(self.ws, "merge-base", "--all", "topic", "main").split()))
        result = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("merge-base が複数", result.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_adopt_refuses_an_already_recorded_worktree_and_a_missing_one(self):
        cut = self.run_sh("topic")
        self.assertEqual(0, cut.returncode, cut.stdout + cut.stderr)
        again = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(1, again.returncode, again.stdout + again.stderr)
        self.assertIn("記録が既にあります", again.stderr)
        missing = self.run_sh("nothing", "--adopt", "--base", "main")
        self.assertEqual(1, missing.returncode, missing.stdout + missing.stderr)
        self.assertIn("ワークツリー", missing.stderr)

    def test_adopt_refuses_a_worktree_that_opens_another_branch(self):
        self.uncorded_worktree()
        git(self.wt("topic"), "checkout", "-q", "-b", "elsewhere")
        result = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_adopt_is_refused_when_the_base_bound_cannot_be_answered(self):
        self.uncorded_worktree()
        result = self.run_sh("topic", "--adopt", "--base", "main", CCNAVI_ENTRY_PATH="/nonexistent")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_adopt_dry_run_writes_nothing(self):
        self.uncorded_worktree()
        result = self.run_sh("topic", "--adopt", "--base", "main", "--dry-run")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    def test_integrate_without_a_record_points_to_adopt_and_it_works_after_adopting(self):
        self.uncorded_worktree()
        gitsh = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh")
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env["CCNAVI_ENTRY_PATH"] = self.exe
        env["PATH"] = os.pathsep.join([self.bin, os.environ["PATH"]])

        def integrate():
            return subprocess.run(
                [SHELL, gitsh, "integrate", "topic"],
                cwd=self.ws,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        refused = integrate()
        self.assertNotEqual(0, refused.returncode, refused.stdout)
        self.assertIn("--adopt", refused.stdout + refused.stderr)
        code = self.code_of(self.run_sh("topic", "--adopt", "--base", "main"))
        self.assertEqual(
            0, self.run_sh("topic", "--adopt", "--base", "main", "--accept", code).returncode
        )
        done = integrate()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


@unittest.skipIf(SHELL is None or REAL_GIT is None, "sh か git が無い")
class WorktreeShQuotedPathTest(WorktreeShTest):
    """空白と ' を含むワークスペースでも、案内のコマンドが打てること。"""

    WS_DIR = "a b's"


def load_tests(loader, tests, pattern):
    """引用を確かめる版は、案内の行を見るテストだけを走らせる（残りは同じ中身の繰り返し）。"""
    suite = unittest.TestSuite()
    for case in (WorktreeShTest, WorktreeShQuotedPathTest):
        for name in loader.getTestCaseNames(case):
            if case is WorktreeShQuotedPathTest and name != (
                "test_the_last_line_is_the_next_step_with_one_line_commands"
            ):
                continue
            suite.addTest(case(name))
    return suite


if __name__ == "__main__":
    unittest.main()
