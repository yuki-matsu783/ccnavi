"""write-and-push の受入テスト。

write-and-push は、取り込み済みの親子のチケットで状態を書く操作を
「ロック → 取り込み → 書く → コミット → push」の 1 操作にし、push が通るまで完了にしない形。
Chrome が未 push の古い状態で判定しないようにする。

使い捨てのワークスペースと bare のリモートを組み、sh（ccnavi-ticket.sh・ccnavi-review.sh・
ccnavi-user-push-approved.sh・ccnavi-user-decide.sh・ccnavi-user-close-early.sh）を外から呼ぶ。
実行ファイルはこのツリーのソースを `python -m ccnavi` で
起こす（実際の判定・見分け・書いたパスの一覧を通す）。リモートを動かすのは別に clone した押し手で、
ワークスペースからは「他の機械（Chrome）が push した」ように見える。

見るのは次のとおり。

1. 順序: hook のマーカーを先にコミット → 取り込み → 書く →
   書いたパスだけ commit --only → push
2. ロック: 他の操作が持っていれば何も書かずに止まる。write-and-push から起こす sync・承認の push
は入れ子で通る
3. 競合: リモートが進んでいれば取り込んでから書く。衝突したら取りやめて何も書かない
4. 途中の操作（merge など）があれば始めない
5. 戻し: push が通らなければ、自分のコミットを比較つきで戻し、書いたパスの中身も戻して
   1 回だけやり直す
6. 届いていた push（応答だけが失われた）は ls-remote で確かめて成功にする
7. 書いたパスの一覧の基点は親のワークツリー。置き場の外に書けば error（着手で configsync が
   プロジェクトのレイヤーへ写したものは例外。tests/ticket/test_swap_points.py と
   tests/config/ が見る）
8. hook の書きかけ（pending・skipped）はコミットし、
   ユーザの判断（c）と知らない変更（d）は止める
9. ユーザの判断の入口（ccnavi-user-close-early.sh など）は、取り込み済みの親子のチケットなら
   承認の push を自動で呼ぶ
10. origin の無いリポジトリ・
    マージリクエストで見るフェーズが無い親子のチケットは今のまま変えない。
11. 承認の push（ccnavi-user-push-approved.sh <親>）は取り込んでから送り、失敗してもコミットを残す
12. Chrome のレビュー済み: 同じ状態から Chrome の入口が出す書くものと、write-and-push の confirm が
    書いて送ったものが、経路・時刻のほかは同じ

`CCNAVI_SH_DIR` があれば、`.ccnavi/scripts/` の `.sh` を写したあと、その上に `CCNAVI_SH_DIR` の
`.sh` を重ねる。写す前の版（`wip/design/scripts/` など）を確かめるときに使う。写す版は一部の sh
だけを持ち、`ccnavi-ticket.sh` などは持たないので、差し替えではなく重ねる。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from ccnavi.infra import pathcase
from tests import ROOT, can_pass_argument, common_path, config_path, live_sh_pid, requires_symlink
from tests.ticketsample import PHASES, RULES, child_text, parent_text

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
# 写す前の版の sh（`wip/design/scripts/` など）。`.ccnavi/scripts/` の上に重ねる。
OVERLAY_DIR = os.environ.get("CCNAVI_SH_DIR", "")
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)
PARENT = "i0001"
CHILD = f"{PARENT}-01-01"
APPROVED = ".ccnavi/approved"

# 実行ファイルの代わり。このツリーのソースを起こす。
EXE = """#!/bin/sh
# `user` の入口は、ラッパーのオプション（--root など）の後の最初の語。
# 端末要求を切るオプションを前に置くと読み替わらないので、`user` の呼び出しでは後ろに付ける
# （ほかは `--` を挟む呼び出しがあるので前に付ける）。
skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  *) first="$a"; break ;;
  esac
done
if [ "$first" = user ]; then
  PYTHONPATH='{root}/src' exec '{python}' -m ccnavi "$@" --guard-ticket-approval disable
fi
PYTHONPATH='{root}/src' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""

# git の代わり。push だけ、実際の push を済ませてから失敗したふりをする（応答だけが失われた形）。
LOST_REPLY = """#!/bin/sh
for a in "$@"; do
  if [ "$a" = push ]; then
    '{git}' "$@" >/dev/null 2>&1
    echo 'fatal: the remote end hung up unexpectedly' >&2
    exit 128
  fi
done
exec '{git}' "$@"
"""


# ELI5 の HTML の置き場。親のワークツリーの wip/ の下にコミットして push し、
# マージリクエストの差分に載せる
ELI5 = "wip/eli5/phase-1.html"


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


def fields(path):
    with open(path, encoding="utf-8") as f:
        return dict((line.rstrip("\n").split(" ", 1) + [""])[:2] for line in f if line.strip())


def executable(path, text):
    write(path, text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class WriteAndPushHarness(unittest.TestCase):
    """ワークスペース（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。

    親は承認済み（計画は mr で見る 1 フェーズ、子 1 つ）で、送ってある。
    """

    plan = ("design",)

    @classmethod
    def setUpClass(cls):
        # クラスごとに 1 回だけ組み、同じ絶対パスの作り置き（pristine）を取っておく。
        # git のワークツリーとスクリプトは絶対パスを持つので、戻す先は同じパスにする。
        cls._base = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls._base, ignore_errors=True)
        inst = cls("run")
        inst._build(cls._base)
        cls._pristine = cls._base + "-pristine"
        cls.addClassCleanup(shutil.rmtree, cls._pristine, ignore_errors=True)
        shutil.copytree(cls._base, cls._pristine, symlinks=True)
        cls._attrs = {k: v for k, v in inst.__dict__.items() if not k.startswith("_")}

    def setUp(self):
        # テストごとに、作り置きを同じ絶対パスへ戻す。
        self._base = type(self)._base
        shutil.rmtree(self._base, ignore_errors=True)
        shutil.copytree(type(self)._pristine, self._base, symlinks=True)
        self.__dict__.update(type(self)._attrs)

    def _build(self, base):
        self.ws = os.path.join(base, "ws")
        self.scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(self.scripts)
        for name in os.listdir(SH_DIR):
            if name.endswith(".sh"):
                shutil.copy(os.path.join(SH_DIR, name), self.scripts)
        if OVERLAY_DIR:
            overlay = os.path.join(ROOT, OVERLAY_DIR)
            for name in os.listdir(overlay):
                if name.endswith(".sh"):
                    shutil.copy(os.path.join(overlay, name), self.scripts)
        git(base, "init", "-q", "-b", "main", self.ws)
        for key, value in CONFIG:
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
        write(common_path(self.ws, "rules"), json.dumps(RULES))
        write(config_path(self.ws, "phases"), PHASES)
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        git(self.ws, "push", "-q", "-u", "origin", "main")
        git(self.ws, "remote", "set-head", "origin", "main")
        self.bin = executable(
            os.path.join(base, "bin", "ccnavi"),
            EXE.format(root=ROOT, python=sys.executable),
        )
        self.state = os.path.join(self.ws, "logs", "state")
        self.tree = self.parent_tree()
        self.approve_family()
        git(self.tree, "push", "-q", "-u", "origin", PARENT)

    # ---- 道具

    def parent_tree(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", PARENT)
        git(self.ws, "worktree", "add", "-q", tree, "-b", PARENT, "main")
        return tree

    def approve_family(self):
        write(
            os.path.join(self.tree, "wip", "proposals", "todo", PARENT + ".md"),
            parent_text(PARENT, list(self.plan), allow=("src/*", "wip/*")),
        )
        write(
            os.path.join(self.tree, "wip", "proposals", "todo", CHILD + ".md"),
            child_text(CHILD, PARENT, 1, ("wip/design/*",), False),
        )
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "propose")
        digest = json.loads(self.exe("--agree", "--preview", "--json").stdout)["digest"]
        done = self.exe("user", "agree", "--yes", f"{PARENT},{CHILD}", "--digest", digest, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            {
                "CCNAVI_BIN_PATH": self.bin,
                "CCNAVI_LOCK_WAIT": "2",
            }
        )
        env.update(extra)
        return env

    def exe(self, *args, cwd=None, stdin=""):
        return subprocess.run(
            [SHELL, self.bin, "--root", self.ws, *args],
            cwd=cwd or self.ws,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=stdin,
        )

    def sh(self, name, *args, cwd=None, stdin=None, **extra):
        return subprocess.run(
            [SHELL, os.path.join(self.scripts, name), *args],
            cwd=cwd or self.ws,
            env=self.env(**extra),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=stdin if stdin is not None else "",
        )

    def sync(self, *args):
        return self.sh("ccnavi-sync.sh", *args)

    def ticket(self, *args, **extra):
        return self.sh("ccnavi-ticket.sh", *args, **extra)

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def remote_sha(self, branch=PARENT):
        done = git(self.ws, "ls-remote", "origin", f"refs/heads/{branch}")
        return done.stdout.split("\t")[0] if done.stdout else ""

    def pusher(self):
        path = os.path.join(self._base, "pusher")
        if not os.path.isdir(path):
            git(self._base, "clone", "-q", self.remote, path)
            for key, value in CONFIG:
                git(path, "config", key, value)
        git(path, "fetch", "-q", "--prune", "origin")
        return path

    def remote_commit(self, rel, text, branch=PARENT):
        path = self.pusher()
        git(path, "checkout", "-q", "-B", branch, f"origin/{branch}")
        write(os.path.join(path, rel), text)
        git(path, "add", "--", rel)
        git(path, "commit", "-q", "-m", f"remote {rel}")
        git(path, "push", "-q", "origin", branch)
        return self.sha(path, "HEAD")

    def dirty(self, tree=None):
        return git(tree or self.tree, "status", "--porcelain", "--untracked-files=all").stdout

    def committed(self, rev="HEAD", tree=None):
        out = git(tree or self.tree, "show", "--name-only", "--format=", rev).stdout
        return sorted(line for line in out.splitlines() if line)

    def eli5(self):
        """ELI5 の HTML を親のワークツリーの wip/ の下に書き、コミットして push する。

        マージリクエストの差分に載せる置き場。返すのはツリーのルートからの相対。
        """
        write(os.path.join(self.tree, *ELI5.split("/")), "<p>やさしい説明</p>\n")
        git(self.tree, "add", "--", ELI5)
        git(self.tree, "commit", "-q", "-m", "docs: ELI5")
        git(self.tree, "push", "-q", "origin", PARENT)
        return ELI5

    def subjects(self, n=5, tree=None):
        out = git(tree or self.tree, "log", f"-{n}", "--format=%s").stdout
        return out.splitlines()

    def copy(self, ident, where="doing"):
        return os.path.join(self.tree, *APPROVED.split("/"), where, ident + ".md")

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def mark(self, n, kind, data):
        rel = f"{APPROVED}/phases/{PARENT}/{n}.{kind}"
        write(os.path.join(self.tree, rel), json.dumps(data))
        return rel

    def lock_dir(self):
        return os.path.join(self.state, "locks", "self", PARENT)

    def child_tree(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", CHILD)
        git(self.ws, "worktree", "add", "-q", tree, "-b", CHILD, PARENT)
        return tree


class WriteAndPushTicketTest(WriteAndPushHarness):
    # ---- 1. 順序と、書いたパスだけのコミット

    def test_start_commits_only_what_it_wrote_and_pushes(self):
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("に着手した", result.stdout)
        head = self.sha(self.tree, "HEAD")
        self.assertNotEqual(head, before)
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(
            self.committed(),
            [f"{APPROVED}/doing/{PARENT}.md"],
        )
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.dirty(), "")
        # 親子ごとの取り込み状態も、統合先のコピーも書かない。
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_other_staged_changes_stay_out_of_the_commit(self):
        """commit --only: 索引の他の変更はコミットに入らず、索引に残る。"""
        write(os.path.join(self.tree, "src", "other.py"), "x = 1\n")
        git(self.tree, "add", "--", "src/other.py")
        # 取り込みの merge は索引が HEAD と同じときだけなので、リモートと同じ形で見る。
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("src/other.py", self.committed())
        self.assertIn("A  src/other.py", self.dirty())

    def test_a_child_is_carried_on_the_parent_branch(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        self.child_tree()
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{APPROVED}/doing/{CHILD}.md", self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    # ---- 8. hook の書きかけ（b）は先にコミットする。ユーザの判断（c）と知らない変更（d）は止める

    def test_hook_marks_are_committed_before_the_take_in(self):
        pending = self.mark(1, "pending", {"review": "mr", "at": "2026-09-29T00:00:00+0900"})
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.subjects(2),
            [
                f"ccnavi: {PARENT} に着手",
                f"ccnavi: {PARENT} の hook のマーカーをコミットする",
            ],
        )
        self.assertEqual(self.committed("HEAD~1"), [pending])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_a_human_decision_not_yet_sent_stops_without_writing(self):
        self.mark(1, "reviewed", {"by": "decide", "at": "t"})
        copy = self.read(self.copy(PARENT))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)
        self.assertIn(f"ccnavi-user-push-approved.sh {PARENT}", result.stderr)
        self.assertEqual(self.read(self.copy(PARENT)), copy)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_an_unknown_change_in_the_place_stops(self):
        write(os.path.join(self.tree, *APPROVED.split("/"), "notes.txt"), "x\n")
        # 形は hook のマーカーでも、変更前が在る（書き換え）なら hook のものとは読まない。
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi が把握していない変更", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    def test_a_forged_mark_with_other_fields_is_not_carried(self):
        self.mark(1, "skipped", {"by": "agent", "at": "t"})
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)

    def test_an_unsent_commit_in_the_place_stops(self):
        """未送信の置き場のコミットが (b) でなければ止める。"""
        rel = f"{APPROVED}/doing/{CHILD}.md"
        with open(os.path.join(self.tree, rel), "a", encoding="utf-8") as f:
            f.write("tampered\n")
        git(self.tree, "commit", "-q", "-am", "edit a copy")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未送信のコミット", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    # ---- 2. ロック

    def test_a_held_lock_stops_without_writing(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        now = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout)
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {now} {live_sh_pid(self)}-{now} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ロックを他の操作が持っている", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertTrue(os.path.isdir(self.lock_dir()))

    # ---- 3. 競合

    def test_a_remote_ahead_is_taken_in_before_writing(self):
        rel = f"{APPROVED}/doing/i0001-01-02.md"
        remote = self.remote_commit(rel, child_text("i0001-01-02", PARENT, 1, ("wip/design/*",)))
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)))
        self.assertEqual(self.sha(self.tree, "HEAD~1"), remote)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_a_take_in_that_finds_an_error_writes_nothing(self):
        """取り込みの後の検査が error を言えば（ccnavi-sync.sh が 1 で終わる）、何も書かずに止める。
        状態は書かない。入れ子のロックで検査できる。"""
        rel = f"{APPROVED}/doing/i0001-01-02.md"
        self.remote_commit(rel, "---\nticket: i0001-01-02\n---\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("取り込みの後の検査で止める理由がある", result.stderr)
        self.assertIn("取り込めなかった", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_a_diverged_parent_is_merged_before_writing(self):
        self.mark(1, "pending", {"review": "mr", "at": "t"})
        self.remote_commit("src/remote.py", "y = 2\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "src", "remote.py")))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD~1").stdout.split()
        self.assertEqual(len(parents), 3)  # 取り込みの merge

    def test_a_conflicting_take_in_writes_nothing(self):
        pending = f"{APPROVED}/phases/{PARENT}/1.pending"
        self.mark(1, "pending", {"review": "mr", "at": "l"})
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "hook")
        self.remote_commit(pending, json.dumps({"review": "mr", "at": "r"}))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("衝突", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        merge_head = git(self.tree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        self.assertFalse(os.path.exists(os.path.join(self.tree, merge_head)))

    def test_a_hook_writing_between_sorting_and_taking_in_is_retried_once(self):
        """3 と 4 の間に hook が書いて取り込みが重なったら、見分けからもう 1 回だけやり直す。"""
        rel = f"{APPROVED}/phases/{PARENT}/2.pending"
        body = json.dumps({"review": "mr", "at": "t"})
        self.remote_commit(rel, body)
        once = os.path.join(self._base, "once")
        shim = os.path.join(self._base, "hookshim")
        target = os.path.join(self.tree, *rel.split("/"))
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f'*" fetch "*"refs/heads/{PARENT}:"*)\n'
            f"  if [ ! -e '{once}' ]; then : >'{once}'; mkdir -p '{os.path.dirname(target)}';"
            f" printf '%s' '{body}' >'{target}'; fi ;;\n"
            "esac\n"
            f"exec '{GIT}' \"$@\"\n",
        )
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("もう 1 回だけやり直す", result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- 4. 途中の操作

    def test_an_operation_in_progress_stops_before_anything(self):
        path = git(self.tree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        path = path if os.path.isabs(path) else os.path.join(self.tree, path)
        write(path, self.sha(self.tree, "HEAD") + "\n")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        result = self.ticket("start", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("途中の操作", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    # ---- 5. 戻し

    def reject_pushes(self, times):
        """リモートの pre-receive が、最初の <times> 回の push を断る（-1 はずっと）。"""
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\n"
            f"n=$(cat '{counter}'); echo $((n + 1)) > '{counter}'\n"
            f'if [ {times} -lt 0 ] || [ "$n" -lt {times} ]; then echo busy >&2; exit 1; fi\n',
        )

    def test_a_rejected_push_is_undone_and_retried_once(self):
        self.reject_pushes(1)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("戻した", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD~1"), before)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_two_rejected_pushes_leave_nothing_written(self):
        self.reject_pushes(-1)
        before = self.sha(self.tree, "HEAD")
        copy = self.read(self.copy(PARENT))
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi-sync.sh", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.read(self.copy(PARENT)), copy)
        self.assertEqual(self.dirty(), "")

    def test_a_moved_head_is_not_undone(self):
        """戻すのは先頭が自分のコミットのときだけ（比較つきの update-ref）。"""
        # push の前に、別の操作が先頭に積んだ形を pre-push で作る。
        hook = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hook = hook if os.path.isabs(hook) else os.path.join(self.tree, hook)
        executable(
            os.path.join(hook, "pre-push"),
            "#!/bin/sh\ngit commit -q --allow-empty -m other >/dev/null 2>&1\nexit 1\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("戻さずに止めた", result.stderr)
        self.assertEqual(self.subjects(2), ["other", f"ccnavi: {PARENT} に着手"])

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_before_the_push_ends_undoes_the_commit(self):
        """送る前に TERM が来たら、自分のコミットを戻して抜ける。"""
        import signal
        import time

        mark = os.path.join(self._base, "pushing")
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        executable(os.path.join(hooks, "pre-push"), f"#!/bin/sh\n: >'{mark}'\nsleep 5\nexit 1\n")
        before = self.sha(self.tree, "HEAD")
        running = subprocess.Popen(
            [SHELL, os.path.join(self.scripts, "ccnavi-ticket.sh"), "start", PARENT],
            cwd=self.ws,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not os.path.exists(mark) and time.monotonic() < deadline:
            time.sleep(0.1)
        running.send_signal(signal.SIGTERM)
        _, err = running.communicate(timeout=60)
        self.assertIn("送る前に止められた", err.decode("utf-8", "replace"))
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(os.path.exists(self.lock_dir()))

    # ---- 6. 届いていた push

    def test_a_push_whose_reply_was_lost_counts_as_sent(self):
        shim = os.path.join(self._base, "shim")
        executable(os.path.join(shim, "git"), LOST_REPLY.format(git=GIT))
        path = shim + os.pathsep + os.environ.get("PATH", "")
        result = self.ticket("start", PARENT, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("届いていた", result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_a_delivered_push_whose_check_also_failed_is_not_written_twice(self):
        """push の応答も ls-remote も失敗したが届いていた。

        戻して取り込み直すと自分のコミットが戻るので、書き直さずに成功で終える。
        push の関門の ls-remote（push の前）は数えない。push のあとの 1 回目だけ失敗させる。
        """
        count = os.path.join(self._base, "ls-count")
        write(count, "0\n")
        pushed = os.path.join(self._base, "pushed")
        shim = os.path.join(self._base, "shim2")
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = push ]; then\n'
            f"    : > '{pushed}'\n"
            f"    '{GIT}' \"$@\" >/dev/null 2>&1; echo 'fatal: hung up' >&2; exit 128\n"
            "  fi\n"
            f"  if [ \"$a\" = refs/heads/{PARENT} ] && [ -e '{pushed}' ]; then\n"
            f"    n=$(cat '{count}'); echo $((n + 1)) > '{count}'\n"
            "    if [ \"$n\" = 0 ]; then echo 'fatal: unable to access' >&2; exit 128; fi\n"
            "  fi\n"
            "done\n"
            f"exec '{GIT}' \"$@\"\n",
        )
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("書き直さずに終える", result.stderr)
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.subjects(2)[1], "approve")
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- コミット（`--no-verify` とタイムアウト監視の時間）と、失敗したときの索引

    def test_a_failed_commit_leaves_nothing_staged(self):
        """署名に失敗してコミットできなければ、add した新しいファイルも索引から外す。"""
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", "false")
        self.assertEqual(self.ticket("start", PARENT).returncode, 1)
        git(self.ws, "config", "commit.gpgsign", "false")
        self.child_tree()
        before = self.sha(self.tree, "HEAD")
        git(self.ws, "config", "commit.gpgsign", "true")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("コミットできなかった", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(git(self.tree, "diff", "--cached", "--name-only").stdout, "")
        self.assertEqual(self.dirty(), "")

    def test_a_hanging_signer_is_cut_off(self):
        signer = executable(os.path.join(self._base, "slow-gpg"), "#!/bin/sh\nsleep 30\n")
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", signer)
        result = self.ticket("start", PARENT, CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT="2")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("制限時間", result.stderr)
        self.assertEqual(self.dirty(), "")

    def test_the_users_commit_hooks_are_skipped(self):
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        for name in ("pre-commit", "commit-msg"):
            executable(os.path.join(hooks, name), "#!/bin/sh\nexit 1\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    # ---- write-and-push を飛ばさせない

    def test_a_double_dash_does_not_slip_past_write_and_push(self):
        result = self.ticket("start", "--", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_the_executable_refuses_a_state_operation_without_write_and_push(self):
        refused = self.exe("ticket", "start", PARENT)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("write-and-push の対象", refused.stderr)
        self.assertEqual(self.dirty(), "")

    def test_a_long_lock_of_a_live_owner_is_not_taken(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        old = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout) - 3600
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {old} {live_sh_pid(self)}-{old} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("10 分を超えて取られたまま", result.stderr)
        self.assertTrue(os.path.isdir(self.lock_dir()))

    def test_a_long_lock_names_its_owner_and_how_to_stop_it(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        old = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout) - 3600
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {old} {live_sh_pid(self)}-{old} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))
        self.assertIn(f"持ち主は pid {live_sh_pid(self)}・ホスト {host}・開始 {at}", result.stderr)
        self.assertIn("ユーザに終了させてもらってから再実行して", result.stderr)
        self.assertTrue(os.path.isdir(self.lock_dir()))

    # ---- 基点のリンク

    @requires_symlink
    def test_a_linked_workspace_still_carries(self):
        linked = self.ws + "-link"
        os.symlink(self.ws, linked)
        self.addCleanup(os.remove, linked)
        result = subprocess.run(
            [
                SHELL,
                os.path.join(linked, ".ccnavi", "scripts", "ccnavi-ticket.sh"),
                "start",
                PARENT,
            ],
            cwd=linked,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- 止める親子のチケット

    def test_a_parent_gone_from_the_remote_stops_at_the_take_in(self):
        """リモートから消えた親（マージされていない）は、取り込み（ccnavi-sync.sh）が終了コード 1 で
        止める。何も書かない。"""
        write(
            os.path.join(self.scripts, "ccnavi-review.sh"),
            '#!/bin/sh\n[ "$1" = merged ] && echo none\nexit 0\n',
        )
        git(self.ws, "--git-dir", self.remote, "update-ref", "-d", f"refs/heads/{PARENT}")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("取り込めなかった", result.stderr)
        self.assertIn("戻し方 1", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertEqual(self.remote_sha(), "")

    def test_a_parent_gone_from_the_remote_is_refused_by_the_push_gate(self):
        """取り込みが通っても（代役が 0 で終わる）、リモートに無い親のブランチは作り直さない。
        write-and-push の push の前の関門が止める。"""
        write(os.path.join(self.scripts, "ccnavi-sync.sh"), "#!/bin/sh\nexit 0\n")
        git(self.ws, "--git-dir", self.remote, "update-ref", "-d", f"refs/heads/{PARENT}")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"親のブランチ {PARENT} がリモートに無い", result.stderr)
        self.assertIn("何も反映していません", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertEqual(self.remote_sha(), "")

    def test_a_take_in_that_tidied_the_parent_away_writes_nothing(self):
        """取り込み（ccnavi-sync.sh）が、マージ済みの親のワークツリーを片付けて 0 で終わっても、
        存在しないツリーには書かずに止まる。"""
        write(
            os.path.join(self.scripts, "ccnavi-sync.sh"),
            f'#!/bin/sh\ngit -C "{self.ws}" worktree remove --force "{self.tree}"\nexit 0\n',
        )
        before = self.remote_sha()
        result = self.ticket("start", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("閉じた（マージ済み）か、ワークツリーが無い", result.stderr)
        self.assertIn("何も反映していません", result.stderr)
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(self.remote_sha(), before)

    def test_record_risk_is_not_write_and_push_and_rides_on_the_finish(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD).returncode, 0)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        head = self.sha(self.tree, "HEAD")
        judge = os.path.join(
            self.tree, *APPROVED.split("/"), "phases", PARENT, f"{CHILD}.judge.json"
        )
        write(judge, json.dumps({"x": {"hit": False, "reason": "r", "head": "h", "at": "t"}}))
        # 記録（judge.json）が未コミットでも、finish の write-and-push は止めずにコミットして push
        # する。
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(os.path.relpath(judge, self.tree).replace(os.sep, "/"), self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")


CHILD_B = f"{PARENT}-01-02"


class WriteAndPushMergeTest(WriteAndPushHarness):
    """子の finish は、親のブランチへ合流してから push する（合流と状態は 1 つのマージコミット）。

    1. 合流・完了の記録・子の merged_sha が 1 つのマージコミットになり、push される
    2. 記録の無い別の PC でも、子 2 の着手で子 1 のコードが入り、finish の合流まで通る
    3. 置き場の書き換え・範囲外のパス・衝突・未コミット（子も親も）は、何も変えずに止まる
    4. push の失敗・コミットの失敗・シグナル中断は、合流ごと元に戻る。応答が失われた push は成功
    5. 衝突を解くために子へ親を取り込んでも、リスクの計測にほかの子の変更が混ざらない
    """

    def start_parent(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)

    def start_child(self, ident=CHILD):
        started = self.ticket("start", ident)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return os.path.join(self.ws, ".claude", "worktrees", ident)

    def approve_second_child(self):
        write(
            os.path.join(self.tree, "wip", "proposals", "todo", CHILD_B + ".md"),
            child_text(CHILD_B, PARENT, 1, ("wip/design/*",), False),
        )
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "propose b")
        digest = json.loads(self.exe("--agree", "--preview", "--json").stdout)["digest"]
        done = self.exe("user", "agree", "--yes", CHILD_B, "--digest", digest, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve b")
        git(self.tree, "push", "-q", "origin", PARENT)

    def work(self, tree, rel, text, message="work"):
        write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "--", rel)
        git(tree, "commit", "-q", "-m", message)
        return self.sha(tree, "HEAD")

    def merging(self):
        return (
            git(self.tree, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0
        )

    def assert_untouched(self, head, ident=CHILD):
        """止まったあと、親のワークツリー・ブランチ・リモート・チケットが何も変わっていない。"""
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.merging())
        self.assertTrue(os.path.isfile(self.copy(ident)), "doing/ から動いている")
        self.assertFalse(os.path.exists(self.lock_dir()))

    # ---- 1. 合流と状態は 1 つのマージコミット

    def test_finish_merges_the_child_in_one_merge_commit_and_pushes(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [before, child_head])
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.merging())
        changed = git(
            self.tree, "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"
        ).stdout.split()
        self.assertIn("wip/design/a.md", changed)
        self.assertIn(f"{APPROVED}/doing/{CHILD}.md", changed)
        self.assertIn(f"wip/proposals/review/{CHILD}.md", changed)
        review = self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md"))
        self.assertIn(f'merged_sha: "{child_head}"', review)
        # 子の先頭は親の先頭の祖先
        self.assertEqual(
            git(
                self.tree, "merge-base", "--is-ancestor", child_head, "HEAD", check=False
            ).returncode,
            0,
        )

    def test_a_child_already_in_the_parent_is_not_merged_again(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 2, "合流するものが無いので、ふつうのコミット")
        self.assertIn(
            f'merged_sha: "{self.sha(tree, "HEAD")}"',
            self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md")),
        )

    # ---- 2. 別の PC の子 2

    def test_a_second_child_sees_the_first_and_reaches_the_request(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        first_head = self.work(first, "wip/design/a.md", "a\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        second = self.start_child(CHILD_B)
        self.assertTrue(os.path.isfile(os.path.join(second, "wip", "design", "a.md")))
        second_head = self.work(second, "wip/design/b.md", "b\n")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)
        for sha in (first_head, second_head):
            done = git(self.tree, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
            self.assertEqual(done.returncode, 0)
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        prepared = self.exe(
            "--cwd", self.tree, "--phase", "1", "--body-file", body, "review", "prepare"
        )
        self.assertNotIn("合流", prepared.stderr, prepared.stderr)

    def test_a_commit_in_a_review_childs_worktree_stops_the_request(self):
        """review/ の子のワークツリーで後から積んだコミットは合流されないので、依頼を止める。"""
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        self.assertTrue(os.path.isdir(tree), "review/ の子のワークツリーが残っていない")
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        args = ("--cwd", self.tree, "--phase", "1", "--body-file", body, "review", "prepare")
        self.assertNotIn("合流されていない修正", self.exe(*args).stderr)
        late = self.work(tree, "wip/design/a.md", "fixed\n", "fix after finish")
        prepared = self.exe(*args)
        self.assertNotEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn(f"子 {CHILD} の手元のブランチに、合流されていない修正がある", prepared.stderr)
        self.assertIn(late[:12], prepared.stderr)

    # ---- 3. 止まる

    def test_a_ticket_place_in_the_childs_commits_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/proposals/todo/evil.md", "x\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("置き場の変更", result.stderr)
        self.assertIn("wip/proposals/todo/evil.md", result.stderr)
        self.assert_untouched(before)

    def test_a_path_outside_the_scope_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "src/x.py", "x = 1\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("範囲の外のパス", result.stderr)
        self.assertIn("src/x.py", result.stderr)
        self.assert_untouched(before)

    def test_a_conflict_is_aborted_and_changes_nothing(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "child\n")
        remote = self.remote_commit("wip/design/a.md", "remote\n")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("衝突する", result.stderr)
        self.assertIn("wip/design/a.md", result.stderr)
        self.assertIn(f"子のワークツリー（.claude/worktrees/{CHILD}）", result.stderr)
        # 取り込みは済んでいるが、合流は何も残していない
        self.assert_untouched(remote)

    def test_a_conflict_names_the_child_that_merged_first(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        self.work(first, "wip/design/a.md", "first\n")
        self.work(second, "wip/design/a.md", "second\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"先に合流した子 {CHILD}", result.stderr)
        self.assertIn(f"後から finish した {CHILD_B}", result.stderr)
        self.assert_untouched(before, CHILD_B)

    def test_a_conflict_on_paths_with_spaces_and_japanese_names_the_child_that_merged_first(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        rels = ("wip/design/a b.md", "wip/design/日本.md")
        for tree, text in ((first, "first\n"), (second, "second\n")):
            for rel in rels:
                write(os.path.join(tree, *rel.split("/")), text)
            git(tree, "add", "-A")
            git(tree, "commit", "-q", "-m", "work")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("wip/design/a b.md, wip/design/日本.md", result.stderr)
        for rel in rels:
            self.assertIn(f"先に合流した子 {CHILD} の変更と重なる（{rel}）", result.stderr)
        self.assert_untouched(before, CHILD_B)

    def test_a_path_ignored_in_the_parent_stops_without_overwriting_it(self):
        self.start_parent()
        tree = self.start_child()
        common = git(self.tree, "rev-parse", "--git-common-dir").stdout.strip()
        common = common if os.path.isabs(common) else os.path.join(self.tree, common)
        write(os.path.join(common, "info", "exclude"), "wip/design/secret.md\n")
        secret = write(os.path.join(self.tree, "wip", "design", "secret.md"), "USER SECRET\n")
        write(os.path.join(tree, "wip", "design", "secret.md"), "child\n")
        git(tree, "add", "-f", "wip/design/secret.md")
        git(tree, "commit", "-q", "-m", "work")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("git の無視するファイル", result.stderr)
        self.assertIn("wip/design/secret.md", result.stderr)
        self.assertEqual(self.read(secret), "USER SECRET\n")
        self.assert_untouched(before)

    def assert_unrestorable_stops(self, kind):
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("合流を戻せない形の変更がある", result.stderr)
        self.assertIn(kind, result.stderr)
        self.assert_untouched(before)

    def test_a_path_with_a_newline_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a\nb.md", "x\n")
        self.assert_unrestorable_stops("改行を含むパス")

    def test_a_rename_that_only_changes_case_stops_without_merging(self):
        self.start_parent()
        self.work(self.tree, "wip/design/Note.md", "x\n")
        git(self.tree, "push", "-q", "origin", PARENT)
        tree = self.start_child()
        git(tree, "mv", "wip/design/Note.md", "wip/design/note.md")
        git(tree, "commit", "-q", "-m", "rename")
        self.assert_unrestorable_stops("大文字小文字だけが違うパス")

    def test_adding_a_submodule_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        sha = self.sha(tree, "HEAD")
        git(tree, "update-index", "--add", "--cacheinfo", f"160000,{sha},wip/design/sub")
        git(tree, "commit", "-q", "-m", "submodule")
        # 初期化していない submodule と同じ形（空のディレクトリ）にして、未コミットに見せない
        os.makedirs(os.path.join(tree, "wip", "design", "sub"))
        self.assert_unrestorable_stops("submodule の追加・削除")

    def test_an_uncommitted_change_in_the_child_stops(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        write(os.path.join(tree, "wip", "design", "late.md"), "not committed\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("サブエージェントの作業がコミットされていません", result.stderr)
        self.assertIn("wip/design/late.md", result.stderr)
        self.assert_untouched(before)

    def test_an_uncommitted_change_in_the_parent_stops(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        write(os.path.join(self.tree, "src", "keep.py"), "print(2)\n")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("親のワークツリーに未コミットの変更がある", result.stderr)
        self.assertIn("src/keep.py", result.stderr)
        self.assertFalse(self.merging())
        self.assertTrue(os.path.isfile(self.copy(CHILD)))

    # ---- 4. 失敗とシグナルは合流ごと戻す

    def assert_rolled_back(self, before):
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.merging())
        self.assertFalse(os.path.exists(os.path.join(self.tree, "wip", "design", "a.md")))
        self.assertTrue(os.path.isfile(self.copy(CHILD)))
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_two_rejected_pushes_undo_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\necho busy >&2\nexit 1\n",
        )
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)

    def test_a_rejected_push_is_retried_with_the_merge_done_again(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\n"
            f"n=$(cat '{counter}'); echo $((n + 1)) > '{counter}'\n"
            'if [ "$n" -lt 1 ]; then echo busy >&2; exit 1; fi\n',
        )
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("戻した", result.stderr)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [before, child_head])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_a_failed_commit_undoes_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", "false")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("合流のコミットができなかった", result.stderr)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_before_the_push_undoes_the_merge(self):
        import signal

        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        mark = os.path.join(self._base, "pushing")
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        executable(os.path.join(hooks, "pre-push"), f"#!/bin/sh\n: >'{mark}'\nsleep 5\nexit 1\n")
        running = subprocess.Popen(
            [SHELL, os.path.join(self.scripts, "ccnavi-ticket.sh"), "finish", CHILD],
            cwd=self.ws,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not os.path.exists(mark) and time.monotonic() < deadline:
            time.sleep(0.1)
        running.send_signal(signal.SIGTERM)
        _, err = running.communicate(timeout=60)
        self.assertIn("送る前に止められた", err.decode("utf-8", "replace"))
        self.assert_rolled_back(before)

    def test_a_push_whose_reply_was_lost_counts_as_sent_with_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        shim = os.path.join(self._base, "shim")
        executable(os.path.join(shim, "git"), LOST_REPLY.format(git=GIT))
        path = shim + os.pathsep + os.environ.get("PATH", "")
        result = self.ticket("finish", CHILD, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("届いていた", result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[2], child_head)

    # ---- 5. 親を取り込んだ子でも、ほかの子の変更が混ざらない

    def test_taking_the_parent_into_a_child_does_not_mix_in_the_other_childs_changes(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        self.work(first, "wip/design/a.md", "first\n")
        self.work(second, "wip/design/b.md", "second\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        # 衝突を解くつもりで、子 2 へ親を取り込む
        git(second, "merge", "-q", "--no-edit", PARENT)
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        risk = os.path.join(
            self.tree, *APPROVED.split("/"), "phases", PARENT, CHILD_B + ".risk.json"
        )
        summary = json.loads(self.read(risk))["summary"]
        self.assertTrue(summary.startswith("1 ファイル、+1 -0 行"), summary)

    # ---- 6. 検査・合流・シグナルの隙間

    def git_shim(self, body):
        """`git` の前に body を挟む PATH。body の中の "$REAL" は本物の git。"""
        shim = os.path.join(self._base, "shim")
        executable(
            os.path.join(shim, "git"),
            f'#!/bin/sh\nREAL=\'{GIT}\'\n{body}\nexec "$REAL" "$@"\n',
        )
        return shim + os.pathsep + os.environ.get("PATH", "")

    def test_a_commit_after_the_scope_check_is_not_merged(self):
        """範囲の検査の直後に子がコミットしても、検査していないコミットは合流しない。"""
        self.start_parent()
        tree = self.start_child()
        checked = self.work(tree, "wip/design/a.md", "a\n")
        mark = os.path.join(self._base, "checked")
        # 子のワークツリーで範囲の検査（diff -z）が済んだ直後に、範囲外のファイルをコミットする。
        path = self.git_shim(
            'case " $* " in *" diff --name-only --no-renames --ignore-submodules=none -z "*)\n'
            f"  if [ ! -e '{mark}' ] && [ \"$(pwd -P)\" = \"$(cd '{tree}' && pwd -P)\" ]; then\n"
            f'    "$REAL" "$@"; rc=$?; : >\'{mark}\'\n'
            '    mkdir -p src && echo evil > src/evil.py && "$REAL" add src/evil.py >/dev/null &&'
            ' "$REAL" commit -q -m evil >/dev/null\n'
            "    exit $rc\n  fi ;;\nesac",
        )
        result = self.ticket("finish", CHILD, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(mark), "検査の差分を読んでいない")
        self.assertNotEqual(self.sha(tree, "HEAD"), checked)
        self.assertNotEqual(
            git(self.tree, "cat-file", "-e", "HEAD:src/evil.py", check=False).returncode, 0
        )
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[2], checked)

    def test_a_commit_after_the_plan_still_records_the_merged_head(self):
        """検査のあとに子がコミットしても、合流した先頭を merged_sha に書き、それまでを数える。"""
        self.start_parent()
        tree = self.start_child()
        planned = self.work(tree, "wip/design/a.md", "a\n")
        wrap = executable(
            os.path.join(self._base, "bin", "wrap"),
            "#!/bin/sh\n"
            'case " $* " in *" ticket finish "*)\n'
            f"  cd '{tree}' && echo late > wip/design/late.md && '{GIT}' add -A &&"
            f" '{GIT}' commit -q -m late ;;\n"
            "esac\n"
            f"exec '{self.bin}' \"$@\"\n",
        )
        result = self.ticket("finish", CHILD, CCNAVI_BIN_PATH=wrap)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(tree, "HEAD"), planned)
        review = self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md"))
        self.assertIn(f'merged_sha: "{planned}"', review)
        risk = os.path.join(self.tree, *APPROVED.split("/"), "phases", PARENT, CHILD + ".risk.json")
        summary = json.loads(self.read(risk))["summary"]
        self.assertTrue(summary.startswith("1 ファイル、+1 -0 行"), summary)

    def term_after(self, pattern):
        """git の引数が pattern に当たる呼び出しの直後に、finish へ TERM を送る。stderr を返す。"""
        import signal

        mark = os.path.join(self._base, "reached")
        path = self.git_shim(
            f'case " $* " in *"{pattern}"*)\n'
            f'  "$REAL" "$@"; rc=$?; : >\'{mark}\'; sleep 3; exit $rc ;;\nesac',
        )
        running = subprocess.Popen(
            [SHELL, os.path.join(self.scripts, "ccnavi-ticket.sh"), "finish", CHILD],
            cwd=self.ws,
            env=self.env(PATH=path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not os.path.exists(mark) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(mark), "その git の呼び出しまで届かなかった")
        running.send_signal(signal.SIGTERM)
        _, err = running.communicate(timeout=60)
        return err.decode("utf-8", "replace")

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_during_the_merge_undoes_it_and_finish_can_run_again(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        err = self.term_after(" merge --no-ff --no-commit ")
        self.assertIn("合流の途中で止められた", err)
        self.assert_rolled_back(before)
        again = self.ticket("finish", CHILD)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_right_after_the_merge_commit_undoes_it(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        err = self.term_after(" commit --quiet --no-verify -m ")
        self.assertIn("送る前に止められた", err)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)
        again = self.ticket("finish", CHILD)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))


class TidyAfterFinishTest(WriteAndPushHarness):
    """finish が通ったら、sh が実行ファイルに `worktree tidy <識別子>` を頼む。

    write-and-push なら送った後に頼む。
    """

    def test_finish_asks_the_exe_to_tidy_after_sending(self):
        log = os.path.join(self._base, "tidy.log")
        logger = executable(
            os.path.join(self._base, "bin", "logger"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f"*\" worktree tidy \"*) printf '%s\\n' \"$*\" >>'{log}' ;;\n"
            "esac\n"
            f"exec '{self.bin}' \"$@\"\n",
        )
        self.assertEqual(self.ticket("start", PARENT, CCNAVI_BIN_PATH=logger).returncode, 0)
        self.assertFalse(os.path.exists(log))
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD, CCNAVI_BIN_PATH=logger).returncode, 0)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        result = self.ticket("finish", CHILD, CCNAVI_BIN_PATH=logger)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        with open(log, encoding="utf-8") as f:
            called = f.read()
        # 識別子は名前の形で剥がさずにそのまま渡す（親へ割り出すのは実行ファイル）。
        # この子はレビュー待ち（review/）へ動いたので、見返す場所としてワークツリーは残る
        # （消すのは confirm が done/ へ動かした後）
        self.assertIn(f"worktree tidy {CHILD}", called)
        self.assertIn("--cwd", called)
        self.assertTrue(os.path.isdir(tree))

    def test_cancel_drops_the_cancelled_childs_worktree(self):
        """取り消した子は作業中でないので、送った後にそのワークツリーを消す。"""
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD).returncode, 0)
        result = self.ticket("cancel", CHILD, "--reason", "やめた")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertIn(f"ワークツリー {CHILD} を削除しました", result.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.ws, "branch", "--list", CHILD).stdout.strip(), CHILD)


class ProposeTest(WriteAndPushHarness):
    """`ccnavi-ticket.sh propose`: 承認待ちの提案と子のフローの下書きを親のブランチへ送る。"""

    TODO = "wip/proposals/todo"
    FLOWS = "wip/proposals/flows"

    def put(self, rel, text="x\n"):
        write(os.path.join(self.tree, *rel.split("/")), text)
        return rel

    def test_new_proposals_are_committed_and_pushed(self):
        todo = self.put(f"{self.TODO}/{PARENT}.md")
        child = self.put(f"{self.TODO}/{CHILD}.md")
        flow = self.put(f"{self.FLOWS}/{CHILD}.yml")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertNotEqual(head, before)
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(self.committed(), sorted([todo, child, flow]))
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} の提案を送る"])
        self.assertEqual(self.dirty(), "")
        self.assertIn(f"提案: {todo}", result.stdout)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if line.startswith("次: ")],
            ["次: ユーザに承認を頼んで返事を待つ。承認が済むまで実装はしない"],
        )
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_other_families_and_template_drafts_stay_out(self):
        mine = self.put(f"{self.TODO}/{PARENT}.md")
        other = self.put(f"{self.TODO}/i0002.md")
        sibling = self.put(f"{self.TODO}/{PARENT}x.md")
        draft = self.put("wip/proposals/templates/login.yml")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [mine])
        status = self.dirty()
        for rel in (other, sibling, draft):
            self.assertIn(f"?? {rel}", status)

    def test_a_child_id_sends_the_whole_family(self):
        todo = self.put(f"{self.TODO}/{PARENT}.md")
        result = self.ticket("propose", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [todo])

    def test_an_edited_tracked_proposal_is_sent(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put(rel, "v2\n")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [rel])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_an_unchanged_proposal_sends_only_the_unpushed_commit(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", "propose by hand")
        before = self.sha(self.tree, "HEAD")
        self.assertNotEqual(self.remote_sha(), before)
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.remote_sha(), before)

    def results(self):
        """propose の結果ファイル（Stop の AI 判定の操作実績）を、書かれた順に読む。"""
        directory = os.path.join(self.ws, "logs", "state", "stopops-result")
        found = []
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            if not name.endswith(".kv"):
                continue
            text = self.read(os.path.join(directory, name))
            row = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            row["text"] = text
            found.append(row)
        return found

    def test_the_result_file_records_the_route_sent(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        (result,) = self.results()
        self.assertEqual((result["id"], result["route"], result["rc"]), (PARENT, "sent", "0"))
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(result["commit"], head)
        self.assertIn(f"file={self.sha(self.tree, f'HEAD:{rel}')} {rel}", result["text"])

    def test_the_result_file_records_unchanged_unpushed_code_and_failure(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", "propose by hand")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put("src/secret.py", "x = 1\n")
        git(self.tree, "add", "--", "src/secret.py")
        git(self.tree, "commit", "-q", "-m", "wip code")
        self.put(f"{self.TODO}/{PARENT}.md", "v2\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 1)
        routes = [r["route"] for r in self.results()]
        self.assertEqual(routes[-1], "unpushed_code")
        self.assertIn(routes[0], ("sent", "unchanged"))

    def test_a_rejected_push_leaves_a_failed_before_result(self):
        self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.reject_pushes()
        self.assertNotEqual(self.ticket("propose", PARENT).returncode, 0)
        (result,) = self.results()
        self.assertEqual(result["route"], "failed_before")
        self.assertNotEqual(result["rc"], "0")

    def reject_pushes(self):
        """リモートの push をすべて断る（pre-receive が 1 で終わる）。"""
        hook = os.path.join(self.remote, "hooks", "pre-receive")
        write(hook, "#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)

    def test_a_rejected_push_keeps_the_proposals_the_user_wrote(self):
        """送れなかったときに戻すのは自分のコミットだけ。書いた提案は作業ツリーに残る。"""
        new = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        flow = self.put(f"{self.FLOWS}/{CHILD}.yml", "flow\n")
        self.reject_pushes()
        before = self.sha(self.tree, "HEAD")
        remote_before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.remote_sha(), remote_before)
        for rel, text in ((new, "v1\n"), (flow, "flow\n")):
            self.assertEqual(self.read(os.path.join(self.tree, rel)), text)
        self.assertNotIn("次: ", result.stdout)

    def test_a_rejected_push_keeps_an_edit_of_a_sent_proposal(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put(rel, "v2 precious edit\n")
        self.reject_pushes()
        result = self.ticket("propose", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(os.path.join(self.tree, rel)), "v2 precious edit\n")
        self.assertNotIn("次: ", result.stdout)

    def test_unpushed_code_is_not_sent_along_with_the_proposal(self):
        self.put("src/secret.py", "x = 1\n")
        git(self.tree, "add", "--", "src/secret.py")
        git(self.tree, "commit", "-q", "-m", "wip code")
        self.put(f"{self.TODO}/{PARENT}.md")
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("src/secret.py", result.stderr)
        self.assertEqual(self.remote_sha(), before)
        self.assertIn(f"?? {self.TODO}/{PARENT}.md", self.dirty())
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_the_extension_is_compared_without_case(self):
        rel = self.put(f"{self.TODO}/{PARENT}.MD")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [rel])

    def test_no_proposal_stops_without_changing_anything(self):
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("送る提案がありません", result.stderr)
        self.assertEqual(self.remote_sha(), before)
        self.assertEqual(self.dirty(), "")

    def test_it_reads_only_the_parent_worktree(self):
        # ワークスペースルートに写しがあっても、親のワークツリーに無ければ送らない。
        write(os.path.join(self.ws, "wip", "proposals", "todo", PARENT + ".md"), "x\n")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("送る提案がありません", result.stderr)

    def test_the_executable_alone_is_refused_for_a_pushed_family(self):
        self.put(f"{self.TODO}/{PARENT}.md")
        result = self.exe("ticket", "propose", PARENT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ccnavi-ticket.sh", result.stderr)

    def test_a_blocking_user_decision_stops_before_sending(self):
        # 未送信のユーザの判断（review/ から done/ への移動など）があれば、提案も送らない。
        self.put(f"{self.TODO}/{PARENT}.md")
        write(os.path.join(self.tree, *APPROVED.split("/"), "done", "zzz.md"), "x\n")
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), before)


class WriteAndPushChildWorktreeTest(WriteAndPushHarness):
    """子の start で、このPCに子のワークツリーが無ければ、取り込み直後の親ブランチから作る。

    別のPCで承認された子でも、どのPCでも同じ場所に作る。
    """

    def child_path(self):
        return os.path.join(self.ws, ".claude", "worktrees", CHILD)

    def start_parent(self):
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_start_creates_the_missing_child_worktree_from_the_parent_branch(self):
        self.start_parent()
        self.assertFalse(os.path.exists(self.child_path()))
        parent_head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isdir(self.child_path()))
        branch = git(self.child_path(), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(branch, CHILD)
        # 子のワークツリーは、着手を書く前の（取り込み直後の）親ブランチの先頭から切れている
        base = git(self.child_path(), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(base, parent_head)
        self.assertIn("子のワークツリー", result.stderr)

    def test_a_child_branch_left_behind_the_parent_is_reused(self):
        self.start_parent()
        git(self.ws, "branch", CHILD, "main")  # 古い手元の子のブランチ（親の先頭の祖先）
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            git(self.child_path(), "rev-parse", "HEAD").stdout.strip(),
            self.sha(self.tree, "HEAD~1"),
        )

    def test_a_child_branch_with_its_own_commits_is_not_touched(self):
        self.start_parent()
        git(self.ws, "branch", CHILD, "main")
        side = os.path.join(self._base, "side")
        git(self.ws, "worktree", "add", "-q", side, CHILD)
        write(os.path.join(side, "mine.txt"), "x\n")
        git(side, "add", "-A")
        git(side, "commit", "-q", "-m", "mine")
        git(self.ws, "worktree", "remove", "--force", side)
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("に無いコミットがある", result.stderr)
        self.assertFalse(os.path.exists(self.child_path()))


class WriteAndPushNoStateTest(WriteAndPushHarness):
    """10. 親子ごとの取り込み状態を持たなくても、着手は write-and-push
    で取り込み・コミット・push まで行う。

    write-and-push の対象は、親のワークツリーがあり、origin があり、HEAD の確認に通るもの。
    取り込み状態は、どの PC でも作らない。
    """

    def test_start_fetches_commits_and_pushes_without_any_state(self):
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = self.sha(self.tree, "HEAD")
        self.assertNotEqual(after, head)
        self.assertEqual(self.remote_sha(), after)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_finish_pushes_without_any_state_too(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        child_tree = self.child_tree()
        started = self.ticket("start", CHILD)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        write(os.path.join(child_tree, "wip", "design", "a.md"), "a\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "-q", "-m", "work")
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_origin_less_family_is_not_write_and_push(self):
        git(self.ws, "remote", "remove", "origin")
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(f"{APPROVED}/doing/{PARENT}.md", self.dirty())


class WriteAndPushWithoutMrTest(WriteAndPushHarness):
    """10. マージリクエストで見るフェーズが無い親子のチケットも write-and-push
    の対象（外した条件）。"""

    plan = ("research",)

    def test_start_commits_and_pushes(self):
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = self.sha(self.tree, "HEAD")
        self.assertNotEqual(after, head)
        self.assertEqual(self.remote_sha(), after)
        family = self.exe("write-and-push", "family", PARENT)
        self.assertIn("target yes", family.stdout)


class PhaseOne:
    """フェーズ 1 の子を閉じるまでの道具。"""

    def finish_phase_one(self):
        """フェーズ 1 の子を着手して閉じる。"""
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        started = self.ticket("start", CHILD)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        finished = self.ticket("finish", CHILD)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)


class WriteAndPushHumanTest(PhaseOne, WriteAndPushHarness):
    """9. ユーザの判断の入口は、取り込み済みの親子のチケットなら承認の push を自動で呼ぶ。

    11. 承認の push。
    """

    plan = ("chores", "design")

    def test_push_approved_without_names_skips_child_trees(self):
        """名前を省いた承認の push: 親は 1 度だけコミットして push し、
        子のワークツリーの置き場はコミットせずに言う。
        """
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        child = self.child_tree()
        write(os.path.join(child, APPROVED, "doing", "stray.md"), "x\n")
        result = self.sh("ccnavi-user-push-approved.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("子のワークツリー", result.stderr)
        self.assertEqual(
            result.stdout.count("承認済みチケットを取り込んだうえで"), 1, result.stdout
        )
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(CHILD), "")

    def test_push_approved_takes_in_before_sending(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        remote = self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertTrue(
            git(self.tree, "merge-base", "--is-ancestor", remote, "HEAD").returncode == 0
        )

    def test_push_approved_keeps_the_commit_when_the_push_fails(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        executable(os.path.join(self.remote, "hooks", "pre-receive"), "#!/bin/sh\nexit 1\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("コミットはローカルに残っています", result.stderr)
        self.assertIn(rel, self.committed())
        # 次の write-and-push は未送信のユーザの判断を見つけて止まり、承認の push
        # を打ち直すよう言う。
        os.remove(os.path.join(self.remote, "hooks", "pre-receive"))
        stopped = self.ticket("start", PARENT)
        self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
        self.assertIn("未送信のユーザの判断", stopped.stderr)
        again = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)

    def test_push_approved_carries_a_removed_draft_with_the_flow(self):
        """取り込んで消えた下書きは、フローと一緒にコミットして push する。書き直された下書きと
        追跡していない下書きには触れない。"""
        drafts = "wip/proposals/flows"
        taken = f"{drafts}/{CHILD}.yml"
        rewritten = f"{drafts}/{PARENT}-01-02.yml"
        untracked = f"{drafts}/{PARENT}-01-03.yml"
        for rel in (taken, rewritten):
            write(os.path.join(self.tree, rel), "nodes: []\n")
        git(self.tree, "add", "--", taken, rewritten)
        git(self.tree, "commit", "-q", "-m", "drafts")
        git(self.tree, "push", "-q", "origin", PARENT)
        flow_rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, flow_rel), "nodes: []\n")
        os.remove(os.path.join(self.tree, taken))
        write(os.path.join(self.tree, rewritten), "nodes: [x]\n")
        write(os.path.join(self.tree, untracked), "nodes: []\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 同じ中身なので、改名として読まずに 2 つのパスで見る。
        out = git(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow_rel, taken]))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        left = self.dirty()
        self.assertIn(rewritten, left)
        self.assertIn(untracked, left)
        self.assertNotIn(taken, left)
        self.assertEqual(git(self.tree, "diff", "--cached", "--name-only").stdout, "")

    def test_push_approved_carries_a_removed_template_draft_with_the_flow(self):
        """取り込んで消えたテンプレートの下書きも、フローの下書きと同じ形で運ぶ（write-and-push
        の手順）。"""
        drafts = "wip/proposals/templates"
        taken = f"{drafts}/feature.yml"
        rewritten = f"{drafts}/research.yml"
        for rel in (taken, rewritten):
            write(os.path.join(self.tree, rel), "name: x\n")
        git(self.tree, "add", "--", taken, rewritten)
        git(self.tree, "commit", "-q", "-m", "drafts")
        git(self.tree, "push", "-q", "origin", PARENT)
        flow_rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, flow_rel), "nodes: []\n")
        os.remove(os.path.join(self.tree, taken))
        write(os.path.join(self.tree, rewritten), "name: y\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = git(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow_rel, taken]))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        left = self.dirty()
        self.assertIn(rewritten, left)
        self.assertNotIn(taken, left)

    def test_push_approved_without_names_carries_the_family_through_write_and_push(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-user-push-approved.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認済みチケットを取り込んだうえで", result.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))


# 実行ファイルの半分の代役。残った指摘の行き先（`user reviewed ... --yes`）だけを代わりに書き
# （親のワークツリーにレビュー済みマーカーを置き、書いたパスの一覧を出し、
# 答えの JSON と下書きを書く）、
# 残り（`write-and-push family`・`write-and-push sort`・`sync paths` など）は実際のものに渡す。
HALF = """#!/bin/sh
case " $* " in
*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  printf '{{"version":1,"digest":"","threads":[]}}\\n' >"$plan"
  exit 0 ;;
*" user reviewed "*" --yes "*)
  list=""; tree=""; root=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
    --record-writes) list="$2"; shift 2 ;;
    --record-tree) tree="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    *) shift ;;
    esac
  done
  rel=.ccnavi/approved/phases/i0001/1.reviewed
  mkdir -p "$tree/.ccnavi/approved/phases/i0001"
  printf '{{"mr": 1, "accepted": ["u1"], "at": "t"}}' >"$tree/$rel"
  [ -z "$list" ] || printf '%s\\n' "$rel" >"$list"
  mkdir -p "$root/logs/state"
  printf '<!-- ccnavi:decide -->\\n決めた\\n' >"$root/logs/state/review-decide-i0001-1.md"
  printf '{{"version":1,"ok":true,"reviewed":true,"followup":""}}\\n'
  exit 0 ;;
esac
# `user` の入口は、ラッパーのオプション（--root など）の後の最初の語。
# 端末要求を切るオプションを前に置くと読み替わらないので、`user` の呼び出しでは後ろに付ける
# （ほかは `--` を挟む呼び出しがあるので前に付ける）。
skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  *) first="$a"; break ;;
  esac
done
if [ "$first" = user ]; then
  PYTHONPATH='{root}/src' exec '{python}' -m ccnavi "$@" --guard-ticket-approval disable
fi
PYTHONPATH='{root}/src' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""

# git の代役。`remote get-url origin` だけをホストの代役の URL で答え、残りは実際のものに渡す
# （取り込みと push は bare のリモート、ホストの API は代役へ）。
URL_GIT = """#!/bin/sh
case " $* " in
*" remote get-url origin "*) printf '%s\\n' '{url}'; exit 0 ;;
esac
exec '{git}' "$@"
"""


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class WriteAndPushReviewTest(WriteAndPushHarness):
    """レビューの状態を書く sh も write-and-push で回す（ccnavi-user-decide.sh を代表に見る）。"""

    def setUp(self):
        super().setUp()
        import http.server
        import threading

        from tests.sh.test_review_decide import GitLab

        GitLab.posted = []
        GitLab.fail_notes = False
        server = http.server.HTTPServer(("127.0.0.1", 0), GitLab)
        GitLab.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.posted = GitLab.posted
        shim = os.path.join(self._base, "shim")
        url = f"http://127.0.0.1:{GitLab.port}/demo/greeter.git"
        executable(os.path.join(shim, "git"), URL_GIT.format(url=url, git=GIT))
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.half = executable(
            os.path.join(self._base, "bin", "half"),
            HALF.format(root=ROOT, python=sys.executable),
        )

    def decide(self, *args):
        return self.sh(
            "ccnavi-user-decide.sh",
            *args,
            cwd=self.tree,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_BIN_PATH=self.half,
        )

    def test_decide_is_carried_and_the_answer_stays_json(self):
        done = self.decide("1", "--choices", '{"u1":"keep"}', "--digest", "d0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(answer["ok"])
        self.assertEqual(len(done.stdout.strip().splitlines()), 1, done.stdout)
        rel = f"{APPROVED}/phases/{PARENT}/1.reviewed"
        self.assertEqual(self.committed(), [rel])
        self.assertEqual(
            self.subjects(1), [f"ccnavi: {PARENT} のフェーズ 1 の指摘の行き先を決めた"]
        )
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        # 決めた内容のコメントは、送った後に投稿する。
        self.assertEqual([kind for kind, _ in self.posted], ["note"])

    def test_decide_preview_takes_no_lock_and_writes_nothing(self):
        head = self.sha(self.tree, "HEAD")
        os.makedirs(self.lock_dir())
        write(os.path.join(self.lock_dir(), "owner"), "other 1 1 1-1 Other\n")
        shown = self.sh(
            "ccnavi-review.sh",
            "decide",
            "1",
            "--preview",
            cwd=self.tree,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
        )
        self.assertNotIn("ロック", shown.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)


# 実行ファイルの代役（ホストに触る副命令の試験用）。状態を書く副命令だけを代わりに書き
# （親のワークツリーにマーカーを置き、書いたパスの一覧を出す）、`write-and-push`・`sync` などは
# 実際のものに渡す。
# HALF_FAIL にファイル名があれば、`review requested` を最初の 1 回だけ失敗させる（打ち直しの試験）。
HOST_HALF = """#!/bin/sh
list=""; tree="${{HALF_TREE:-}}"; root=""
for a in "$@"; do :; done
set -- "$@"
i=0
for a in "$@"; do
  case "$prev" in
  --record-writes) list="$a" ;;
  --record-tree) tree="$a" ;;
  --root) root="$a" ;;
  esac
  prev="$a"
done
put() {{
  mkdir -p "$tree/$(dirname "$1")"
  printf '%s' "$2" >"$tree/$1"
  [ -z "$list" ] || printf '%s\\n' "$1" >>"$list"
}}
m=.ccnavi/approved/phases/i0001
case " $* " in
*" review prepare "*)
  mkdir -p "$root/logs/state"
  printf '<!-- ccnavi:request i0001:1 key=k1 -->\\n見てほしい\\n' >"$root/logs/state/req.md"
  printf '題\\n\\n本文\\n' >"$root/logs/state/mr.md"
  printf '%s\\n%s\\n' "$root/logs/state/req.md" "$root/logs/state/mr.md"
  exit 0 ;;
*" review requested "*)
  if [ -n "${{HALF_FAIL:-}}" ] && [ ! -e "$HALF_FAIL" ]; then
    : >"$HALF_FAIL"; : >"$list"; exit 1
  fi
  : >"$list"; put "$m/1.requested" '{{"mr": 1, "at": "t"}}'; exit 0 ;;
*" review confirm "*)
  : >"$list"; put "$m/1.reviewed" '{{"mr": 1, "accepted": [], "at": "t"}}'; exit 0 ;;
*" review ready "*) : >"$list"; put "$m/ready.json" '{{"mr": 1, "at": "t"}}'; exit 0 ;;
*" user close-early "*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  printf '{{"version":1,"digest":"","threads":[]}}\\n' >"$plan"
  exit 0 ;;
*" user close-early "*" --accept-check "*) exit 0 ;;
*" user close-early "*) put "$m/2.skipped" '{{"by": "close-early", "at": "t"}}'; exit 0 ;;
esac
# `user` の入口は、ラッパーのオプション（--root など）の後の最初の語。
# 端末要求を切るオプションを前に置くと読み替わらないので、`user` の呼び出しでは後ろに付ける
# （ほかは `--` を挟む呼び出しがあるので前に付ける）。
skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  *) first="$a"; break ;;
  esac
done
if [ "$first" = user ]; then
  PYTHONPATH='{root}/src' exec '{python}' -m ccnavi "$@" --guard-ticket-approval disable
fi
PYTHONPATH='{root}/src' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""


class Host(__import__("http.server").server.BaseHTTPRequestHandler):
    """GitLab の代役。MR 1 つ、コメント（notes）の投稿と一覧、Draft 外し。"""

    notes: list = []
    port = 0
    # 真なら Draft を外す PUT に Draft のままと答える（外し損ねの試験）
    keep_draft = False

    def log_message(self, *args):
        pass

    def reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("/merge_requests"):
            url = f"http://127.0.0.1:{self.port}/demo/greeter/-/merge_requests/1"
            self.reply(
                200,
                [
                    {
                        "iid": 1,
                        "web_url": url,
                        "source_project_id": 1,
                        "source_branch": PARENT,
                        "target_branch": "main",
                        "draft": True,
                    }
                ],
            )
        elif path.endswith("/notes"):
            page = "page=1" in self.path
            self.reply(200, list(Host.notes) if page else [])
        elif path.endswith("/merge_requests/1"):
            self.reply(200, {"iid": 1, "title": "Draft: x"})
        else:
            self.reply(200, [] if "per_page" in self.path else {"id": 1})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        self.reply(200, {"draft": Host.keep_draft})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if self.path.split("?")[0].endswith("/notes"):
            note = {"id": len(Host.notes) + 1, "body": data.get("body", ""), "created_at": "t"}
            Host.notes.append(note)
            self.reply(201, note)
        else:
            self.reply(201, {"iid": 1, "web_url": "u"})


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class WriteAndPushHostHarness(WriteAndPushHarness):
    """ホストの代役（GitLab）と実行ファイルの代役を用意する道具。試験は持たない。"""

    def setUp(self):
        super().setUp()
        import threading

        Host.notes = []
        Host.keep_draft = False
        server = __import__("http.server").server.HTTPServer(("127.0.0.1", 0), Host)
        Host.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        shim = os.path.join(self._base, "shim")
        url = f"http://127.0.0.1:{Host.port}/demo/greeter.git"
        executable(os.path.join(shim, "git"), URL_GIT.format(url=url, git=GIT))
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.half = executable(
            os.path.join(self._base, "bin", "hosthalf"),
            HOST_HALF.format(root=ROOT, python=sys.executable),
        )

    def review(self, *args, stdin="", script="ccnavi-review.sh", **extra):
        env = {
            "PATH": self.path,
            "GITLAB_TOKEN": "t0k",
            "CCNAVI_BIN_PATH": self.half,
            "HALF_TREE": self.tree,
        }
        env.update(extra)
        return self.sh(script, *args, cwd=self.tree, stdin=stdin, **env)

    def carried(self, rel):
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class WriteAndPushHostTest(WriteAndPushHostHarness):
    """ホストに触る副命令（request・confirm・ready）と、ユーザ専用の decide・close-early。"""

    def test_request_confirm_and_ready_are_carried(self):
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", ELI5)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.requested")
        self.assertEqual(len(Host.notes), 1)
        # 依頼文は ELI5 の在りか（差分の中の相対パス）と crit push の送り先を言う。
        # 指摘はユーザが crit push で行のスレッドとして送る
        self.assertIn(f"`crit review {ELI5}`", Host.notes[0]["body"])
        self.assertIn("`crit push 1`", Host.notes[0]["body"])
        log = os.path.join(self._base, "tidy.log")
        logger = executable(
            os.path.join(self._base, "bin", "logger"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f"*\" worktree \"*) printf '%s\\n' \"$*\" >>'{log}' ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        confirmed = self.review("confirm", "--phase", "1", CCNAVI_BIN_PATH=logger)
        self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.reviewed")
        # done/ へ動いた子のワークツリーの片付けを、送った後に実行ファイルへ頼む
        with open(log, encoding="utf-8") as f:
            self.assertIn(f"worktree tidy {PARENT} --phase 1", f.read())
        ready = self.review("ready", CCNAVI_BIN_PATH=logger)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/ready.json")
        # 最後に親のワークツリーを消そうとするが、cwd が中にあるので消さず、外から打つ 1 本を出す
        with open(log, encoding="utf-8") as f:
            logged = f.read()
        self.assertIn(f"worktree retire {PARENT}", logged)
        # retire の後に、残っているワークツリーの一覧を頼む
        self.assertLess(
            logged.index(f"worktree retire {PARENT}"), logged.index(f"worktree remaining {PARENT}")
        )
        self.assertIn(f"cwd がワークツリー {PARENT} の中にあるので削除しませんでした", ready.stdout)
        self.assertIn(f"ccnavi-clean.sh --worktree {PARENT}", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draft を解除した"), ready.stdout.index("cwd がワークツリー")
        )
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_and_pushes_the_archive_removals_before_undrafting(self):
        """ready の退避（閉じたチケットの削除）は、write-and-push がコミットして push してから
        Draft を外す。"""
        old = f"{APPROVED}/phases/old/closed.json"
        write(os.path.join(self.tree, *old.split("/")), "{}\n")
        git(self.tree, "add", old)
        git(self.tree, "commit", "-q", "-m", "old")
        git(self.tree, "push", "-q", "origin", PARENT)
        mover = executable(
            os.path.join(self._base, "bin", "mover"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    prev="$a"; done\n'
            f'  rm "$tree/{old}"\n'
            f"  printf '{old}\\n' >\"$list\"\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        ready = self.review("ready", CCNAVI_BIN_PATH=mover)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.carried(old)
        self.assertFalse(os.path.exists(os.path.join(self.tree, *old.split("/"))))
        self.assertEqual(git(self.tree, "ls-tree", "HEAD", "--", old).stdout, "")

    def test_ready_from_outside_drops_the_parent_worktree_after_undrafting(self):
        """`ready --parent <親>` を親のワークツリーの外（ワークスペースルート）から打つ。
        中から打ったときと同じに Draft を外し、最後に親のワークツリーを消す（ブランチは残す）。"""
        ready = self.sh(
            "ccnavi-review.sh",
            "ready",
            "--parent",
            PARENT,
            cwd=self.ws,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_BIN_PATH=self.half,
            HALF_TREE=self.tree,
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を削除しました", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draft を解除した"),
            ready.stdout.index(f"ワークツリー {PARENT} を削除しました"),
        )
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(git(self.ws, "branch", "--list", PARENT).stdout.strip(), PARENT)
        # 送ったものはリモートに届いている（ready のマーカー）
        shown = git(self.ws, "show", "--name-only", "--format=", f"{PARENT}").stdout
        self.assertIn(f"{APPROVED}/phases/{PARENT}/ready.json", shown)

    def test_ready_keeps_the_parent_worktree_when_the_draft_stays(self):
        """Draft を外し損ねたら、外から打っても親のワークツリーは消さない。

        ready を打ち直せるようにするため。
        """
        Host.keep_draft = True
        ready = self.sh(
            "ccnavi-review.sh",
            "ready",
            "--parent",
            PARENT,
            cwd=self.ws,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_BIN_PATH=self.half,
            HALF_TREE=self.tree,
        )
        self.assertNotEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除できませんでした", ready.stderr)
        self.assertNotIn("を削除した", ready.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_from_outside_by_a_relative_script_path(self):
        """ワークスペースルートから相対パス（`sh .ccnavi/scripts/ccnavi-review.sh`）で打っても、
        親のワークツリーへ移った後に write-and-push の取り込み（ccnavi-sync.sh）を見失わない。

        親のワークツリーにはスクリプトの写しを置かない（相対パスがそこで解かれると、見失うか、
        ワークツリーの古い写しを使う）。
        """
        git(self.tree, "rm", "-r", "-q", ".ccnavi/scripts")
        git(self.tree, "commit", "-q", "-m", "no scripts here")
        git(self.tree, "push", "-q", "origin", PARENT)
        env = self.env(
            PATH=self.path, GITLAB_TOKEN="t0k", CCNAVI_BIN_PATH=self.half, HALF_TREE=self.tree
        )
        ready = subprocess.run(
            [SHELL, ".ccnavi/scripts/ccnavi-review.sh", "ready", "--parent", PARENT],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を削除しました", ready.stdout)
        self.assertFalse(os.path.exists(self.tree))

    def test_ready_parent_refuses_what_is_not_a_parent_worktree(self):
        for args in (
            ("--parent",),
            ("--parent", "../x"),
            ("--parent", "i0009"),
            ("--parent=",),
            ("--parent", ""),
            ("--parent", PARENT, "--parent"),
            ("--parent", PARENT, f"--parent={PARENT}"),
        ):
            with self.subTest(args=args):
                refused = self.sh(
                    "ccnavi-review.sh",
                    "ready",
                    *args,
                    cwd=self.ws,
                    PATH=self.path,
                    GITLAB_TOKEN="t0k",
                    CCNAVI_BIN_PATH=self.half,
                    HALF_TREE=self.tree,
                )
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertNotIn("Draft を解除した", refused.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_the_wip_removals_and_names_them(self):
        """ready が消した wip/ の追跡済みのファイルは、write-and-push
        が退避と一緒にコミットして push する。
        消したものと、戻せる版（消す前の HEAD）を出す。"""
        self.eli5()
        head = self.sha(self.tree, "HEAD")
        mover = executable(
            os.path.join(self._base, "bin", "mover"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    [ "$prev" = --root ] && root="$a"\n'
            '    prev="$a"; done\n'
            f'  rm "$tree/{ELI5}"\n'
            f"  printf '{ELI5}\\n' >\"$list\"\n"
            '  mkdir -p "$root/logs/state"; : >"$root/logs/state/note.md"\n'
            "  printf '%s\\n' \"$root/logs/state/note.md\"\n"
            "  printf 'tree %s\\n' \"$tree\"\n"
            f"  printf 'wip-from {head}\\nwip {ELI5}\\n'\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        ready = self.review("ready", CCNAVI_BIN_PATH=mover)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("wip/ の追跡済みのファイルを削除しました（1 件", ready.stdout)
        self.assertIn(head[:12], ready.stdout)
        self.assertIn(f"  {ELI5}", ready.stdout)
        self.carried(ELI5)
        self.assertEqual(git(self.tree, "ls-tree", "HEAD", "--", ELI5).stdout, "")

    def test_the_real_ready_moves_the_rest_again_after_a_failed_push(self):
        """実際の実行ファイルの ready（退避を始めた後の打ち直し）を write-and-push の中で通す。

        1 回目の push をリモートが断る → write-and-push が戻す → 取り込みからやり直して残りを移し、
        送ってから
        Draft を外す。
        """
        archive_base = os.path.join(self.ws, "logs", "archive", "self")
        # 親はもう退避にだけある（前の回が移した）。ツリーには統合先からの過去の親子が残る。
        for rel in (f"doing/{PARENT}.md", f"doing/{CHILD}.md"):
            git(self.tree, "rm", "-q", "--", f"{APPROVED}/{rel}")
        write(
            os.path.join(archive_base, "done", f"{PARENT}.md"),
            parent_text(PARENT, list(self.plan), allow=("src/*", "wip/*")),
        )
        write(os.path.join(archive_base, "phases", PARENT, "ready.json"), '{"mr": 1}')
        old = (
            "---\nversion: 1\nticket: old\ntitle: 古い親\nrationale: r\n"
            "human_review:\n  required: false\n  reason: t\n"
            'allow:\n  - match: Write|Edit\n    glob: "src/*"\n---\n'
        )
        write(os.path.join(self.tree, APPROVED, "done", "old.md"), old)
        write(os.path.join(self.tree, APPROVED, "phases", "old", "closed.json"), "{}\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "退避の途中")
        git(self.tree, "push", "-q", "origin", PARENT)
        head = self.sha(self.tree, "HEAD")
        write(
            os.path.join(archive_base, "ready", f"{PARENT}.json"),
            json.dumps(
                {"parent": PARENT, "tree": pathcase.fold(os.path.realpath(self.tree)),
                 "head": head, "files": []}
            ),
        )  # fmt: skip
        # リモートは 1 回目の push だけ断る
        once = os.path.join(self._base, "refused-once")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            f"#!/bin/sh\n[ -e '{once}' ] && exit 0\n: >'{once}'\necho refused >&2\nexit 1\n",
        )
        ready = self.review("ready", CCNAVI_BIN_PATH=self.bin)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertTrue(os.path.exists(once))
        self.assertIn("push が通らなかった", ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.carried(f"{APPROVED}/done/old.md")
        for rel in ("done/old.md", "phases/old/closed.json"):
            self.assertEqual(
                git(self.tree, "ls-tree", "HEAD", "--", f"{APPROVED}/{rel}").stdout, ""
            )
            self.assertTrue(os.path.isfile(os.path.join(archive_base, *rel.split("/"))), rel)

    def test_a_request_is_not_posted_twice_across_runs(self):
        """投稿の後に失敗した依頼を打ち直しても、同じ目印の依頼は投稿し直さない。"""
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        once = os.path.join(self._base, "failed-once")
        html = self.eli5()
        first = self.review(
            "request", "--phase", "1", "--body-file", body, "--eli5", html, HALF_FAIL=once
        )
        self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(len(Host.notes), 1)
        again = self.review(
            "request", "--phase", "1", "--body-file", body, "--eli5", html, HALF_FAIL=once
        )
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("投稿済み", again.stdout)
        self.assertEqual(len(Host.notes), 1)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.requested")

    def test_terminal_decide_chooses_outside_write_and_push_and_writes_inside(self):
        """端末の decide は、選ぶのを write-and-push の外で済ませ、write-and-push の中では
        --yes で書くだけ。"""
        chooser = executable(
            os.path.join(self._base, "bin", "chooser"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" --choose-out "*)\n'
            '  prev=""; for a in "$@"; do [ "$prev" = --choose-out ] && out="$a"; prev="$a"; done\n'
            f"  [ -d '{self.lock_dir()}' ] && echo LOCKED-WHILE-CHOOSING >&2\n"
            '  printf \'{"choices": {"u1": "keep"}, "digest": "d0"}\' >"$out"\n'
            "  exit 0 ;;\n"
            # 受け入れる指摘の計画を立てる --yes は write-and-push の外で走り、何も置かない。
            # 受け入れる指摘は無いという計画を返す（実物は依頼の記録を読むので、ここでは使わない）
            '*" --accept-plan "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done\n'
            '  printf \'{"threads": []}\' >"$plan"\n'
            '  printf \'{"version":1,"ok":true}\\n\'; exit 0 ;;\n'
            '*" --yes "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    prev="$a"; done\n'
            '  mkdir -p "$tree/.ccnavi/approved/phases/i0001"\n'
            "  printf '{}' >\"$tree/.ccnavi/approved/phases/i0001/1.reviewed\"\n"
            "  printf '.ccnavi/approved/phases/i0001/1.reviewed\\n' >\"$list\"\n"
            '  printf \'{"version":1,"ok":true}\\n\'; exit 0 ;;\n'
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        done = self.review("1", script="ccnavi-user-decide.sh", CCNAVI_BIN_PATH=chooser)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("行き先を置いて送った", done.stdout)
        self.assertNotIn("LOCKED-WHILE-CHOOSING", done.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.reviewed")

    def test_close_early_hands_over_to_the_carrier(self):
        closed = self.review("--reason", "r", "--no-issue", script="ccnavi-user-close-early.sh")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/2.skipped")


@unittest.skipIf(shutil.which("jq") is None, "jq が必要です")
class WriteAndPushChromeConfirmTest(PhaseOne, WriteAndPushHarness):
    """Chrome のレビュー済みと手元の confirm の突き合わせ。

    取り込み済みの親子のチケットでは、手元の CLI を直に打つと write-and-push に断られる
    （`--record-tree` が無い）。そこで write-and-push と同じ手順
    （`ccnavi-review.sh request` と `confirm`。GitHub の代役は録った見本）で手元を
    回し、同じ状態から Chrome の入口が出す書くものと、write-and-push
    が親のブランチへ書いて送ったものを比べる。
    違ってよいのは経路（`via`）と時刻（`at`）だけ。アカウント（`actor`）は、手元は sh が
    トークンの持ち主を引いたもの、Chrome は PAT の持ち主で、同じ見本なので同じになる。
    """

    SCENE = "resolved"

    def setUp(self):
        super().setUp()
        from tests.sh import github_host

        self.host = github_host
        shim = os.path.join(self._base, "shim")
        executable(
            os.path.join(shim, "git"),
            URL_GIT.format(url="https://github.com/acme/widgets.git", git=GIT),
        )
        fakes = os.path.join(self._base, "fakes")
        github_host.install(fakes, sys.executable)
        self.path = os.pathsep.join([shim, fakes, os.environ.get("PATH", "")])
        self.finish_phase_one()
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)

    def review(self, *args, scene=None):
        return self.sh(
            "ccnavi-review.sh",
            *args,
            cwd=self.tree,
            PATH=self.path,
            GITHUB_TOKEN="t0k",
            FAKE_GITHUB_SCENE=scene or self.SCENE,
            FAKE_GITHUB_STATE=os.path.join(self._base, "comments.json"),
        )

    def files(self, base, prefixes):
        found = {}
        for prefix in prefixes:
            for current, dirs, names in os.walk(os.path.join(base, *prefix.split("/"))):
                dirs[:] = [d for d in dirs if d != ".git"]
                for name in names:
                    full = os.path.join(current, name)
                    rel = os.path.relpath(full, base).replace(os.sep, "/")
                    found[rel] = self.read(full)
        return found

    def host_compare(self, base, head):
        """GitHub の compare API の答えを、拡張の `compareFiles` と同じ規則で一覧にする。

        GitHub は改名を検出して `filename` と `previous_filename` を返し、拡張は両方を入れる。
        ここでは git の改名の検出（`--find-renames`）で同じ組を作り、同じ規則で並べる
        （TS の関数そのものは拡張の試験 CX-T130・139 が見る）。
        """
        out = git(self.tree, "diff", "--name-status", "--find-renames", f"{base}..{head}").stdout
        files = []
        for line in out.splitlines():
            parts = line.split("\t")
            if parts[0].startswith("R"):
                files += [parts[2], parts[1]]
            else:
                files.append(parts[1])
        return files

    def chrome_request(self, scene=None):
        """拡張が組むのと同じ要求（統合先 main、親 i0001、録った見本、
        依頼の後の変更の一覧）。"""
        from tests.ticketsample import load_chrome

        chrome = load_chrome()
        place = chrome._placement()
        keep = tuple(p + "/" for p in place["integration_paths"])
        main = {
            rel: text
            for rel, text in self.files(self.ws, (".ccnavi", ".claude")).items()
            if rel.startswith(keep) or rel in place["integration_files"]
        }
        mine = self.files(self.tree, place["branch_paths"])
        recorded = json.loads(self.read(os.path.join(self.tree, *self.requested.split("/"))))
        head = self.sha(self.tree, "HEAD")
        files = self.host_compare(recorded["head"], head)
        where = os.path.join(self.host.SCENES, scene or self.SCENE, "expected.json")
        with open(where, encoding="utf-8") as f:
            copy = json.load(f)
        request = {
            "schema": chrome.SCHEMA,
            "op": "confirm",
            "family": PARENT,
            "phase": 1,
            "stamp": "2026-09-29T12:00:00+0900",
            "actor": {"account": "octo-reviewer"},
            "result": copy,
            "compare": {"base": recorded["head"], "head": head, "files": files},
            "snapshot": {
                "integration": {"name": "main", "source": "default", "head": "0" * 40},
                "branches": {
                    "main": {"head": "0" * 40, "files": main},
                    PARENT: {"head": head, "files": mine},
                },
                "absent": [],
            },
        }
        answer = json.loads(chrome.handle(json.dumps(request), os.path.join(self._base, "memfs")))
        self.assertNotIn("error", answer, answer)
        return answer

    @property
    def requested(self):
        return f"{APPROVED}/phases/{PARENT}/1.requested"

    @staticmethod
    def normalized(path, text):
        """経路・時刻を除く（違ってよいもの）。"""
        drop = ("via", "at")
        if path.endswith((".reviewed", ".json")):
            return {k: v for k, v in json.loads(text).items() if k not in drop}
        return text

    def request(self):
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        html = self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", html)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.assertIn(self.requested, self.committed())
        return body

    def same_refusal(self, scene):
        """Chrome が通さない理由が、write-and-push で回した手元の confirm
        の標準エラーにそのまま出る。"""
        answer = self.chrome_request(scene)
        self.assertIsNone(answer["changes"], scene)
        self.assertTrue(answer["problems"], scene)
        head, remote = self.sha(self.tree, "HEAD"), self.remote_sha()
        local = self.review("confirm", "--phase", "1", scene=scene)
        self.assertNotEqual(local.returncode, 0, scene)
        said = local.stderr.replace(self.ws + os.sep, "").splitlines()
        for line in answer["problems"]:
            self.assertIn(line, said, scene)
        self.assertEqual((self.sha(self.tree, "HEAD"), self.remote_sha()), (head, remote), scene)
        self.assertEqual(self.dirty(), "", scene)
        return answer

    STOPPING = ("paged", "changes-requested", "cr-commented", "pending")

    def test_unresolved_threads_and_change_requests_stop_both_the_same(self):
        self.request()
        # paged の取ってきた状態は約 50,000 字。1 引数の上限が小さい環境（Windows）では取れない。
        scenes = [s for s in self.STOPPING if s != "paged" or can_pass_argument(60_000)]
        said = {s: self.same_refusal(s)["problems"][0] for s in scenes}
        if "paged" in said:
            self.assertIn("未解決のスレッドが 4 件", said["paged"])
        for scene in ("changes-requested", "cr-commented", "pending"):
            self.assertIn("変更要求のレビューが立っている", said[scene], scene)

    def test_chrome_writes_what_write_and_push_writes_and_sends(self):
        body = self.request()

        answer = self.chrome_request()
        self.assertEqual(answer["problems"], [])
        rows = answer["changes"][PARENT]

        # 手元を直に打つと write-and-push に断られる（だから write-and-push の手順で回す）。
        # 文面まで見る
        direct = self.exe("--cwd", self.tree, "review", "confirm", "--phase", "1", "--result", body)
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn(
            f"親子のチケット {PARENT} は取り込み済み（write-and-push の対象）", direct.stderr
        )
        self.assertIn("何も反映せずに中止しました。", direct.stderr)

        before = self.sha(self.tree, "HEAD")
        confirmed = self.review("confirm", "--phase", "1")
        self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")
        sent = git(self.tree, "diff", "--name-only", "--no-renames", f"{before}..HEAD")
        self.assertEqual(sorted(sent.stdout.split()), sorted(r["path"] for r in rows))
        for row in rows:
            full = os.path.join(self.tree, *row["path"].split("/"))
            if row["op"] == "delete":
                self.assertFalse(os.path.exists(full), row["path"])
                continue
            self.assertEqual(
                self.normalized(row["path"], row["content"]),
                self.normalized(row["path"], self.read(full)),
                row["path"],
            )
        remote = json.loads(next(r["content"] for r in rows if r["path"].endswith("1.reviewed")))
        local = json.loads(
            self.read(os.path.join(self.tree, *f"{APPROVED}/phases/{PARENT}/1.reviewed".split("/")))
        )
        self.assertEqual((remote["actor"], remote["via"]), ("octo-reviewer", "chrome"))
        self.assertEqual((local["actor"], local["via"]), ("octo-reviewer", "cli"))
        self.assertEqual(list(remote), list(local))

        # レビュー済みに重ねて打てば、手元も Chrome も同じ文面で止め、何も送らない
        again = self.same_refusal(self.SCENE)
        self.assertEqual(
            again["problems"], ["ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み"]
        )


if __name__ == "__main__":
    unittest.main()
