"""ccnavi-worktree.sh の受入テスト。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
fetch のときだけ origin の URL を bare リポジトリへ向ける git の代役を PATH の先頭に置く
（tests/sh/test_start_sh.py と同じ作り）。

見るのは次のとおり。

1. 遅れていなければ手元の統合先から、遅れていれば origin/<統合先> から --no-track で切ること
2. 既にあるワークツリーは何もせずパスを出すこと（冪等）、既にあるブランチ・使えない名前は断ること
3. fetch が失敗したとき、ワークツリーを切らずに終了コード 4 で止まり、次の手（直して打ち直す・
   --offline）を出すこと。--offline なら手元の統合先から切り、古いかもしれないと案内すること
4. --dry-run は何も作らないこと
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT, SRC, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
REAL_GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
PYTHONPATH='{src}' exec '{python}' -m ccnavi "$@"
"""

# fetch のときだけ、origin の URL を bare リポジトリへ向ける git の代役。
# FAKE_FETCH_FAIL があれば失敗させる。
GIT = """#!/bin/sh
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
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.bare = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.bare)
        self.ws = os.path.join(base, "ws")
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
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.exe = write(
            os.path.join(base, "exe", "ccnavi"), EXE.format(src=SRC, python=sys.executable)
        )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.bin = os.path.join(base, "bin")
        os.chmod(
            write(os.path.join(self.bin, "git"), GIT.format(git=REAL_GIT, bare=self.bare)), 0o755
        )

    def run_sh(self, *args, fail_fetch=False, cwd=None):
        """fail_fetch が文字列なら、fetch はその文で失敗する。真なら名前が引けない失敗。"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            {
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_BIN_PATH": self.exe,
                "FAKE_ORIGIN_URL": ORIGIN_URL,
            }
        )
        if fail_fetch:
            env["FAKE_FETCH_FAIL"] = fail_fetch if isinstance(fail_fetch, str) else "1"
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
        git(self._tmp.name, "clone", "-q", self.bare, other)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(other, "config", key, value)
        write(os.path.join(other, "new.txt"), "n")
        git(other, "add", "new.txt")
        git(other, "commit", "-q", "-m", "ahead")
        git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")

    def head_of(self, name):
        return git(self.wt(name), "rev-parse", "HEAD")

    def test_cuts_from_local_integration_when_up_to_date(self):
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertIn("起点 main", result.stdout)
        self.assertEqual("topic", git(self.wt("topic"), "branch", "--show-current"))

    def test_a_free_name_is_warned_after_the_cut_and_still_cut(self):
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("warn: 直接作業のワークツリー topic の名前が", result.stdout)
        self.assertEqual("topic", git(self.wt("topic"), "branch", "--show-current"))

    def test_a_name_in_the_form_is_not_warned(self):
        result = self.run_sh("feature-12-topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertNotIn("warn:", result.stdout)

    def test_cuts_from_origin_when_local_is_behind(self):
        ahead = self.advance_origin()
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(ahead, self.head_of("topic"))
        self.assertIn("起点 origin/main、--no-track", result.stdout)
        self.assertIn("1 件遅れて", result.stdout)
        self.assertNotEqual(ahead, git(self.ws, "rev-parse", "main"), "手元の統合先は進めない")
        upstream = subprocess.run(
            [REAL_GIT, "rev-parse", "--abbrev-ref", "topic@{upstream}"],
            cwd=self.ws,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, upstream.returncode, "統合先を追跡しない")

    def diverge(self):
        """手元の main にだけ 1 コミット足し、origin にも別の 1 コミットを足す（分岐）。"""
        write(os.path.join(self.ws, "local.txt"), "l")
        git(self.ws, "add", "local.txt")
        git(self.ws, "commit", "-q", "-m", "local only")
        return self.advance_origin()

    def test_diverged_local_stops_with_guidance(self):
        self.diverge()
        result = self.run_sh("topic")
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertIn("分岐している", result.stdout)
        self.assertIn("--from-origin", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_from_origin_cuts_from_origin_with_warning_when_diverged(self):
        origin_head = self.diverge()
        result = self.run_sh("topic", "--from-origin")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(origin_head, self.head_of("topic"))
        self.assertIn("分岐している", result.stdout)
        self.assertNotIn("件遅れていたので", result.stdout)

    def test_dry_run_when_diverged_creates_nothing_and_says_it_would_stop(self):
        self.diverge()
        git(self.ws, "fetch", "-q", self.bare, "main:refs/remotes/origin/main")
        result = self.run_sh("topic", "--dry-run")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        self.assertIn("切らずに終了コード 1 で止まる", result.stdout)

    def test_offline_when_diverged_does_not_stop_because_origin_may_be_stale(self):
        self.diverge()
        git(self.ws, "fetch", "-q", self.bare, "main:refs/remotes/origin/main")
        result = self.run_sh("topic", "--offline", fail_fetch=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertIn("止めなかった", result.stdout)

    def test_local_ahead_only_cuts_from_local(self):
        write(os.path.join(self.ws, "local.txt"), "l")
        git(self.ws, "add", "local.txt")
        git(self.ws, "commit", "-q", "-m", "local only")
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertNotIn("分岐", result.stdout)

    def test_existing_worktree_is_reported_not_recreated(self):
        self.run_sh("topic")
        write(os.path.join(self.wt("topic"), "keep.txt"), "mine")
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("既にある", result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt("topic"), "keep.txt")))

    def test_refuses_bad_names_and_existing_branch(self):
        git(self.ws, "branch", "taken")
        for args, code, why in (
            (("a/b",), 2, "/ は書けません"),
            (("taken",), 1, "既にあります"),
            ((), 2, "名前を渡してください"),
            (("a", "b"), 2, "1 本だけ"),
            (("--bogus",), 2, "知らない引数"),
        ):
            with self.subTest(args=args):
                result = self.run_sh(*args)
                self.assertEqual(code, result.returncode, result.stdout)
                self.assertIn(why, result.stderr)

    def test_fetch_failure_stops_with_guidance(self):
        result = self.run_sh("topic", fail_fetch=True)
        self.assertEqual(4, result.returncode, result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")), "切らずに止まる")
        self.assertIn("Could not resolve host", result.stderr, "原因の出力を見せる")
        self.assertIn("同じコマンドを打ち直す", result.stdout)
        self.assertIn("topic --offline", result.stdout)
        self.assertIn("ユーザに", result.stdout)

    def test_missing_remote_branch_is_called_out(self):
        result = self.run_sh(
            "topic", fail_fetch="fatal: couldn't find remote ref main", cwd=self.ws
        )
        self.assertEqual(4, result.returncode, result.stderr)
        self.assertIn("origin に統合先 main が無い", result.stdout)

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

    def test_offline_cuts_from_local_and_says_it_may_be_stale(self):
        result = self.run_sh("topic", "--offline", fail_fetch=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertIn("古いかもしれない", result.stdout)
        self.assertIn("ユーザにも伝える", result.stdout)

    def test_dry_run_creates_nothing(self):
        result = self.run_sh("topic", "--dry-run", fail_fetch=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        self.assertEqual("", git(self.ws, "branch", "--list", "topic"))


if __name__ == "__main__":
    unittest.main()
