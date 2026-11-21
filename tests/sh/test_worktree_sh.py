"""ccnavi-worktree.sh の受入テスト。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
fetch のときだけ origin の URL を bare リポジトリへ向ける git のスタブを PATH の先頭に置く
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
import tempfile
import unittest

from tests import ROOT, common_sh, exe, put_messages
from tests.draft import final
from tests.inproc import run_ccnavi

SHELL = shutil.which("sh") or shutil.which("bash")
REAL_GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
exec '{exe}' "$@"
"""

# fetch のときだけ、origin の URL を bare リポジトリへ向ける git のスタブ。
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
        put_messages(self.ws)
        self.exe = write(os.path.join(base, "exe", "ccnavi"), EXE.format(exe=exe.sh_path()))
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
        # 名前とオプションの間に空白がある（つながると別の名前になる）。
        self.assertRegex(result.stdout, r"ccnavi-worktree\.sh'? '?\w+'? --")
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

    def test_a_removed_first_worktree_is_not_named(self):
        self.assertEqual(0, self.run_sh("first").returncode)
        shutil.rmtree(self.wt("first"))
        result = self.run_sh("second")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)

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
        self.assertIn("案内: 親 ws の承認待ちの提案・MR は", result.stdout)
        self.assertIn(f"案内: 承認待ちの提案 {self.WS_DIR} が", result.stdout)
        self.assertIn("案内: MR #12 が残っている", result.stdout)
        self.assertNotIn("着手済みの子が", result.stdout)
        self.assertEqual(1, result.stdout.count("\n次: "), result.stdout)
        self.assertTrue(result.stdout.rstrip().splitlines()[-1].startswith("次: "))

    PARENT = "p0001"

    def parent_tree(self, **places):
        """実際の親のワークツリー（別ブランチ）を切り、置き場にチケットを置いて、そのパスを返す。

        places は 置き場の相対パス → 本文の frontmatter（ticket 以外の欄）。
        """
        path = os.path.join(self.ws, ".claude", "worktrees", self.PARENT)
        git(self.ws, "worktree", "add", "-q", "-b", self.PARENT, path)
        for rel, extra in places.items():
            write(
                os.path.join(path, *rel.split("/")),
                f"---\nticket: {extra.get('ticket', self.PARENT)}\n"
                + "".join(f"{k}: {v}\n" for k, v in extra.items() if k != "ticket")
                + "---\n本文\n",
            )
        return path

    def assertRefused(self, result, *args):
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertIn("直接作業へは切り替えられない", result.stderr)
        self.assertEqual(1, result.stdout.count("\n次: ") + result.stderr.count("\n次: "))
        self.assertTrue(result.stderr.rstrip().splitlines()[-1].startswith("次: "))
        self.assertFalse(os.path.exists(self.wt("topic")))
        self.assertEqual("", git(self.ws, "for-each-ref", "refs/ccnavi"))
        self.assertEqual("", git(self.ws, "branch", "--list", "topic"))

    def test_an_approved_parent_tree_may_not_switch(self):
        path = self.parent_tree(**{".ccnavi/approved/doing/p0001.md": {"mr": 12}})
        self.assertRefused(self.run_sh("topic", "--dry-run", cwd=path))
        result = self.run_sh("topic", cwd=path)
        self.assertRefused(result)
        self.assertIn("案内: 親を早めに閉じたいなら", result.stderr)
        self.assertIn("ccnavi-user-close-early.sh", result.stderr)
        # 断る文は、報告して待つことを言う。
        self.assertIn("親のワークツリー（p0001）から", result.stderr)
        self.assertIn("次: 状況をユーザに報告し", result.stderr)

    def test_the_record_is_written_and_read_as_valid_by_the_post_check(self):
        """--side-by-side だけでは確認を求めない（確認は ask のルールの仕事）。

        記録は v4 で、実行後チェックの読み取りが有効と読む。
        """
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
        # 書き手の出力を、実行ファイル（lint の直接作業の検査）の読み取りが有効と読む。
        lint = run_ccnavi(["--root", self.ws, "--lint"], cwd=self.ws)
        self.assertIn("横で作業する指定が有効です", lint.stdout + lint.stderr)
        self.assertIn("/topic)", lint.stdout + lint.stderr)
        self.assertNotIn("壊れています", lint.stdout + lint.stderr)

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
        # 案内は次の一手より前。
        lines = result.stdout.splitlines()
        hint = next(i for i, ln in enumerate(lines) if self.PROJECTS_HINT in ln)
        step = next(i for i, ln in enumerate(lines) if ln.startswith("次: "))
        self.assertLess(hint, step)

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
        """--dry-run は projects/ の案内を出さず、名前の警告は出し、何も切らない。"""
        self.add_project()
        result = self.run_sh("topic", "--dry-run")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn(self.PROJECTS_HINT, result.stdout)
        self.assertIn("warn: 直接作業のワークツリー名 topic が", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_a_name_in_the_form_is_not_warned(self):
        result = self.run_sh("feature-12-topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertNotIn("warn:", result.stdout)

    def test_the_last_line_is_the_next_step_with_one_line_commands(self):
        """ずれが無いときの通し。

        確認なしで取り込み先の HEAD から切り、2 回目は切らずに既にあると言う。
        """
        result = self.run_sh("topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(git(self.ws, "rev-parse", "main"), self.head_of("topic"))
        self.assertEqual("topic", git(self.wt("topic"), "branch", "--show-current"))
        self.assertIn("取り込み先: main（", result.stdout)
        self.assertIn("書きかけは含まない", result.stdout)
        self.assertIn("横で作業する: いいえ", result.stdout)
        self.assertEqual("no", self.record("topic")["side_by_side"])
        upstream = subprocess.run(
            [REAL_GIT, "rev-parse", "--abbrev-ref", "topic@{upstream}"],
            cwd=self.ws,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, upstream.returncode, "取り込み先を追跡しない")
        # 親が無いので残り物の話は無く、projects/ が無いので projects/ の案内も無い。
        self.assertNotIn("取り下げ", result.stdout)
        self.assertNotIn("残っている", result.stdout)
        self.assertNotIn(self.PROJECTS_HINT, result.stdout)
        self.assertNotIn("他にある", result.stdout)
        # 名前の形でないので警告し、それでも切る。
        self.assertIn("warn: 直接作業のワークツリー名 topic が", result.stdout)
        last = result.stdout.rstrip("\n").split("\n")[-1]
        self.assertTrue(last.startswith("次: 編集はこのワークツリーの下で行います"), last)
        self.assertIn(f"Write / Edit に渡すパス: {self.wt('topic')}/…", last)
        self.assertIn("が済んでから integrate を打ちます", last)
        # 名前の警告よりも後ろ。
        self.assertGreater(result.stdout.rindex("次:"), result.stdout.index("warn:"))
        # 案内の git は、書いたとおりに新しいシェルで打てる。
        command = re.search(r"`(cd .+? <サブコマンド>)`", last).group(1)
        command = command.replace("<サブコマンド>", "rev-parse --abbrev-ref HEAD")
        # ラッパーは引数の形を実行ファイルに確かめさせる（利用先では settings.json の env が渡す）。
        shell_env = {**os.environ, "CCNAVI_ENTRY_PATH": self.exe}
        done = subprocess.run(
            [SHELL, "-c", command], cwd="/", env=shell_env, capture_output=True, text=True
        )
        self.assertEqual("topic", done.stdout.strip().splitlines()[-1], done.stderr)
        # 既にあるときは切り直さず（中の物は残る）、記録があると言う。最後の行は同じ。
        write(os.path.join(self.wt("topic"), "keep.txt"), "mine")
        again = self.run_sh("topic", "--no-wip")
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertIn("ワークツリーは既にある", again.stdout)
        self.assertIn("取り込みの記録は、ある（ccnavi-direct/topic）", again.stdout)
        self.assertNotIn("--adopt", again.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt("topic"), "keep.txt")))
        self.assertTrue(again.stdout.rstrip("\n").split("\n")[-1].startswith("次: 編集は"))
        # 2 本目は切れて、1 本目を名指しする。
        second = self.run_sh("second")
        self.assertEqual(0, second.returncode, second.stderr + second.stdout)
        self.assertTrue(os.path.isdir(self.wt("second")))
        self.assertIn("作業中の直接作業のワークツリーが他にある: topic", second.stdout)

    # ---- C4: cwd のブランチが取り込み先と違う

    # ---- C5: 取り込み先が統合先でない

    def test_a_base_other_than_the_default_branch_needs_the_accept_code(self):
        self.make_feature_with_origin()
        # 合わないコードは報告からやり直す。
        wrong = self.run_sh("topic", "--accept", "deadbeef")
        self.assertEqual(3, wrong.returncode, wrong.stdout + wrong.stderr)
        self.assertIn("確認コードが今の状態と合わない", wrong.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
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

    def test_an_unreadable_branch_list_of_origin_is_an_unverified_remote(self):
        result = self.run_sh("topic", FAKE_LS_REMOTE_FAIL="1")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn("リモート未確認", result.stdout)
        self.assertFalse(os.path.exists(self.wt("topic")))
        done = self.run_sh("topic", "--accept", self.code_of(result), FAKE_LS_REMOTE_FAIL="1")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    # ---- C7: 書きかけ

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

    # ---- .claude/worktrees/ は書きかけに数えない（.gitignore に行が無い配布先）

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

    def test_a_failing_read_tree_still_stops_with_snapshot_failed(self):
        self.leave_worktrees_ignored()
        write(os.path.join(self.ws, "notes.txt"), "メモ\n")
        code = self.code_of(self.run_sh("second"))
        result = self.run_sh("second", "--accept", code, "--copy-wip", FAKE_READ_TREE_FAIL="1")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("スナップショットを作れませんでした", result.stdout + result.stderr)

    # ---- 確認コードが縛るもの（外すと落ちる）

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

    def test_adopt_refuses_a_name_bound_to_a_ticket(self):
        self.uncorded_worktree()
        git(self.ws, "update-ref", "refs/remotes/origin/topic", "HEAD")
        result = self.run_sh("topic", "--adopt", "--base", "main")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("チケットに結び付く", result.stderr)
        self.assertFalse(os.path.exists(self.record_path("topic")))

    # ---- C1〜C3: 先に断るもの

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
        """引数の誤りの通し。

        ほかの廃止した引数・名前の形・同時に渡せない旗は cut.TestArgs が見る。
        """
        result = self.run_sh("topic", "--from-origin")
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("廃止しました", result.stderr)
        self.assertIn("--accept", result.stderr)
        self.assertFalse(os.path.exists(self.wt("topic")))

    def test_existing_worktree_reports_whether_the_record_exists(self):
        # 手で切ったワークツリーには記録が無いため、引き取る手を言います。
        # 記録があるときは、ずれが無いときの通しが見る。
        git(self.ws, "worktree", "add", "-q", self.wt("manual"), "-b", "manual", "main")
        result = self.run_sh("manual")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("取り込みの記録は、無い（ccnavi-direct/manual）", result.stdout)
        self.assertIn("manual' --adopt --base <取り込み先>", result.stdout)
        self.assertNotIn("確認に答えられません", result.stdout)
        # 最後の行は、従来どおり次の一手です。
        self.assertTrue(result.stdout.rstrip("\n").split("\n")[-1].startswith("次: 編集は"))

    def test_unattended_adds_the_fixed_line_only_when_the_variable_is_zero(self):
        git(self.ws, "branch", "taken")
        git(self.ws, "worktree", "add", "-q", self.wt("manual"), "-b", "manual", "main")
        for name, value, shown in (
            ("taken", "0", True),
            ("manual", "0", True),
            ("manual", "1", False),
        ):
            with self.subTest(name=name, value=value):
                result = self.run_sh(name, CLAUDE_CODE_SESSION_ATTENDED=value)
                said = result.stdout + result.stderr
                self.assertEqual(shown, "このセッションでは確認に答えられません" in said, said)

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

    # ---- 引き取り（--adopt）

    def uncorded_worktree(self, name="topic", commits=2):
        """記録の無い直接作業のワークツリー（手で切ったもの）。"""
        git(self.ws, "worktree", "add", "-q", self.wt(name), "-b", name)
        for index in range(commits):
            write(os.path.join(self.wt(name), f"w{index}.txt"), f"w{index}\n")
            git(self.wt(name), "add", "-A")
            git(self.wt(name), "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"work {index}")

    def record_path(self, name):
        return os.path.join(self.ws, ".git", "ccnavi-direct", name)

    def test_adopt_shows_the_range_and_the_code_first_and_writes_nothing(self):
        self.uncorded_worktree()
        # --dry-run と、合わないコードは何も書かない。
        dry = self.run_sh("topic", "--adopt", "--base", "main", "--dry-run")
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertIn("dry-run", dry.stdout)
        wrong = self.run_sh("topic", "--adopt", "--base", "main", "--accept", "deadbeef")
        self.assertEqual(3, wrong.returncode, wrong.stdout + wrong.stderr)
        self.assertIn("確認コードが今の状態と合わない", wrong.stdout)
        self.assertFalse(os.path.exists(self.record_path("topic")))
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
