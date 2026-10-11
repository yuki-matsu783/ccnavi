"""C1 の受入テスト。

C1 は、取り込み済みの親子のチケットで状態を書く操作を
「ロック → 取り込み → 書く → コミット → push」の 1 操作にし、push が通るまで完了にしない形。
Chrome が未 push の古い状態で判定しないようにする。

使い捨てのワークスペースと bare のリモートを組み、sh（ccnavi-ticket.sh・ccnavi-review.sh・
ccnavi-push-approved.sh）を外から呼ぶ。実行ファイルはこのツリーのソースを `python -m ccnavi` で
起こす（本物の判定・見分け・書いたパスの一覧を通す）。リモートを動かすのは別に clone した押し手で、
ワークスペースからは「他の機械（Chrome）が push した」ように見える。

見るのは次のとおり。

1. 順序: hook のマーカーと状態の履歴を先にコミット → 取り込み → 書く →
   書いたパスだけ commit --only → push。
   統合先の取り込み結果も同じ回で書く
2. ロック: 他の操作が持っていれば何も書かずに止まる。C1 から起こす sync・承認の push は入れ子で通る
3. 競合: リモートが進んでいれば取り込んでから書く。衝突したら取りやめて何も書かない
4. 途中の操作（merge など）があれば始めない
5. 戻し: push が通らなければ、自分のコミットを比較つきで戻し、書いたパスの中身も戻して
   1 回だけやり直す
6. 届いていた push（応答だけ落ちた）は ls-remote で確かめて成功にする
7. 書いたパスの一覧の基点は親のワークツリー。置き場の外に書けば error（着手で configsync が
   プロジェクトのレイヤーへ写したものは例外。tests/ticket/test_core.py と tests/config/ が見る）
8. hook の書きかけ（pending・skipped・状態の履歴の追記）はコミットし、
   ユーザの判断（c）と知らない変更（d）は止める
9. ユーザの判断の入口（ccnavi-review.sh chat など）は、取り込み済みの親子のチケットなら
   承認の push を自動で呼ぶ
10. 取り込み状態の無い親子のチケット・origin の無いリポジトリ・chat だけの親子のチケットは今のまま
    （コミットも push もしない）
11. 承認の push（ccnavi-push-approved.sh <親>）は取り込んでから送り、落ちてもコミットを残す
12. Chrome のレビュー済み: 同じ状態から Chrome の入口が出す書くものと、C1 の confirm が
    書いて送ったものが、経路・時刻・拡張の版のほかは同じ

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

from tests import ROOT, can_pass_argument, common_path, config_path, live_sh_pid, requires_symlink
from tests.ticket.test_phases import PHASES, child_text, parent_text
from tests.ticket.test_ticket import RULES

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
PYTHONPATH='{root}/src' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""

# git の代わり。push だけ、本物の push を済ませてから落ちたふりをする（応答だけが落ちた形）。
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
class C1Harness(unittest.TestCase):
    """ワークスペース（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。

    親は承認済み（計画は mr で見る 1 フェーズ、子 1 つ）で、送ってあり、取り込み済み
    （取り込み状態が present）。
    """

    plan = ("design",)
    imported = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
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
        self.record = os.path.join(self.state, "sync", "self", "families", PARENT)
        self.tree = self.parent_tree()
        self.approve_family()
        git(self.tree, "push", "-q", "-u", "origin", PARENT)
        if self.imported:
            result = self.sync(PARENT)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(fields(self.record)["state"], "present")

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
        preview = self.exe("--agree", "--preview", "--json")
        digest = json.loads(preview.stdout)["digest"]
        done = self.exe("--agree", "--yes", f"{PARENT},{CHILD}", "--digest", digest, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            {
                "CCNAVI_BIN_PATH": self.bin,
                "CCNAVI_SYNC_RETRIES": "0",
                "CCNAVI_SYNC_RETRY_WAIT": "0",
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
        path = os.path.join(self._tmp.name, "pusher")
        if not os.path.isdir(path):
            git(self._tmp.name, "clone", "-q", self.remote, path)
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


class C1TicketTest(C1Harness):
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
            [f"{APPROVED}/doing/{PARENT}.md", f"{APPROVED}/events/{PARENT}.ndjson"],
        )
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.dirty(), "")
        record = fields(self.record)
        self.assertEqual(record["state"], "present")
        self.assertEqual(record["sha"], head)
        # 統合先の取り込み結果も同じ回で書く
        # （2c の相談: 送った直後に取り込み結果が無く承認が止まる件）。
        head_file = os.path.join(self.state, "sync", "self", "integration", "head")
        self.assertTrue(os.path.isfile(head_file))
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

    def test_hook_marks_and_events_are_committed_before_the_take_in(self):
        pending = self.mark(1, "pending", {"review": "mr", "at": "2026-09-29T00:00:00+0900"})
        events = os.path.join(self.tree, *APPROVED.split("/"), "events", PARENT + ".ndjson")
        with open(events, "a", encoding="utf-8", newline="\n") as f:
            row = {"at": "t", "ticket": PARENT, "kind": "phase-mark", "phase": 1, "mark": "pending"}
            f.write(json.dumps(row) + "\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.subjects(2),
            [
                f"ccnavi: {PARENT} に着手",
                f"ccnavi: {PARENT} の hookのマーカーと状態の履歴をコミットする",
            ],
        )
        self.assertEqual(
            self.committed("HEAD~1"), sorted([pending, f"{APPROVED}/events/{PARENT}.ndjson"])
        )
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_a_human_decision_not_yet_sent_stops_without_writing(self):
        self.mark(1, "reviewed", {"by": "chat", "at": "t"})
        copy = self.read(self.copy(PARENT))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)
        self.assertIn(f"ccnavi-push-approved.sh {PARENT}", result.stderr)
        self.assertEqual(self.read(self.copy(PARENT)), copy)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_an_unknown_change_in_the_place_stops(self):
        write(os.path.join(self.tree, *APPROVED.split("/"), "notes.txt"), "x\n")
        # 形は hook のマーカーでも、変更前が在る（書き換え）なら hook のものとは読まない。
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnaviの知らない変更", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    def test_a_forged_mark_with_other_fields_is_not_carried(self):
        self.mark(1, "skipped", {"by": "agent", "at": "t"})
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)

    def test_an_unsent_commit_in_the_place_stops(self):
        """未送信の置き場のコミットが (b) でなければ止める（REQ-APV-11 の補足）。"""
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

    def test_a_take_in_that_blocks_the_family_writes_nothing(self):
        """取り込みの後の検査で親子のチケットが止まれば（blocked）、何も書かない。入れ子のロックで書ける。"""
        rel = f"{APPROVED}/doing/i0001-01-02.md"
        self.remote_commit(rel, "---\nticket: i0001-01-02\n---\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("blocked", result.stderr)
        self.assertEqual(fields(self.record)["state"], "blocked")
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
        events = f"{APPROVED}/events/{PARENT}.ndjson"
        local = json.dumps({"at": "l", "ticket": PARENT, "kind": "phase-mark"}) + "\n"
        with open(os.path.join(self.tree, events), "a", encoding="utf-8", newline="\n") as f:
            f.write(local)
        git(self.tree, "commit", "-q", "-am", "hook")
        with open(os.path.join(self.tree, events), encoding="utf-8") as f:
            base = f.read()[: -len(local)]
        self.remote_commit(
            events, base + json.dumps({"at": "r", "ticket": PARENT, "kind": "x"}) + "\n"
        )
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
        once = os.path.join(self._tmp.name, "once")
        shim = os.path.join(self._tmp.name, "hookshim")
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
        counter = os.path.join(self._tmp.name, "rejected")
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
        self.assertEqual(fields(self.record)["state"], "present")

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

        mark = os.path.join(self._tmp.name, "pushing")
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
        shim = os.path.join(self._tmp.name, "shim")
        executable(os.path.join(shim, "git"), LOST_REPLY.format(git=GIT))
        path = shim + os.pathsep + os.environ.get("PATH", "")
        result = self.ticket("start", PARENT, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("届いていた", result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(fields(self.record)["sha"], self.sha(self.tree, "HEAD"))

    def test_a_delivered_push_whose_check_also_failed_is_not_written_twice(self):
        """push の応答も ls-remote も落ちたが届いていた。

        戻して取り込み直すと自分のコミットが戻るので、書き直さずに成功で終える。
        """
        count = os.path.join(self._tmp.name, "ls-count")
        write(count, "0\n")
        shim = os.path.join(self._tmp.name, "shim2")
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = push ]; then\n'
            f"    '{GIT}' \"$@\" >/dev/null 2>&1; echo 'fatal: hung up' >&2; exit 128\n"
            "  fi\n"
            f'  if [ "$a" = refs/heads/{PARENT} ]; then\n'
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

    # ---- コミット（`--no-verify` とタイムアウト監視の時間）と、落ちたときの索引

    def test_a_failed_commit_leaves_nothing_staged(self):
        """署名に落ちてコミットできなければ、add した新しいファイルも索引から外す（レビュー 1）。"""
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
        signer = executable(os.path.join(self._tmp.name, "slow-gpg"), "#!/bin/sh\nsleep 30\n")
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", signer)
        result = self.ticket("start", PARENT, CCNAVI_C1_COMMIT_TIMEOUT="2")
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

    # ---- C1 を飛ばさせない

    def test_a_double_dash_does_not_slip_past_c1(self):
        result = self.ticket("start", "--", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_the_executable_refuses_a_state_operation_without_c1(self):
        refused = self.exe("ticket", "start", PARENT)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("C1 の対象", refused.stderr)
        self.assertEqual(self.dirty(), "")

    # ---- 改行（autocrlf）と偽の状態の履歴

    def test_autocrlf_hook_events_are_carried(self):
        git(self.ws, "config", "core.autocrlf", "true")
        events = f"{APPROVED}/events/{PARENT}.ndjson"
        path = os.path.join(self.tree, *events.split("/"))
        os.remove(path)
        git(self.tree, "checkout", "--", events)
        with open(path, "rb") as f:
            self.assertIn(b"\r\n", f.read())
        row = {"at": "t", "ticket": PARENT, "kind": "phase-mark", "phase": 1, "mark": "pending"}
        with open(path, "ab") as f:
            f.write(json.dumps(row).encode() + b"\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            f"ccnavi: {PARENT} の hookのマーカーと状態の履歴をコミットする", self.subjects(2)
        )

    def test_a_forged_event_line_stops(self):
        events = f"{APPROVED}/events/{PARENT}.ndjson"
        with open(os.path.join(self.tree, events), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"at": "t", "ticket": PARENT, "kind": "approved"}) + "\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    # ---- ロック（同じ機械で持ち主が生きていれば、10 分を過ぎても強制取得しない）

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
        self.assertIn("ユーザに終了させてもらってから打ち直して", result.stderr)
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

    def test_a_gone_family_is_refused_before_any_network(self):
        with open(self.record, encoding="utf-8") as f:
            text = f.read()
        write(self.record, text.replace("state present", "state gone"))
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("書かずに止めた", result.stderr)
        self.assertIn("ccnavi-sync.sh", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    def test_record_risk_is_not_c1_and_rides_on_the_finish(self):
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
        # 記録（judge.json）が未コミットでも、finish の C1 は止めずにコミットして push する。
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(os.path.relpath(judge, self.tree).replace(os.sep, "/"), self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")


class TidyAfterFinishTest(C1Harness):
    """finish が通ったら、sh が実行ファイルに `worktree tidy <識別子>` を頼む。

    C1 なら送った後に頼む。
    """

    def test_finish_asks_the_exe_to_tidy_after_sending(self):
        log = os.path.join(self._tmp.name, "tidy.log")
        logger = executable(
            os.path.join(self._tmp.name, "bin", "logger"),
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
        # この子はレビュー待ち（review/）へ動いたので、指摘を直す場所としてワークツリーは残る
        # （消すのは confirm が done/ へ動かした後）
        self.assertIn(f"worktree tidy {CHILD}", called)
        self.assertIn("--cwd", called)
        self.assertTrue(os.path.isdir(tree))

    def test_cancel_drops_the_cancelled_childs_worktree(self):
        """取り消した子は作業中でないので、送った後にそのワークツリーを消す（ブランチは残す）。"""
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD).returncode, 0)
        result = self.ticket("cancel", CHILD, "--reason", "やめた")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertIn(f"ワークツリー {CHILD} を消した", result.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.ws, "branch", "--list", CHILD).stdout.strip(), CHILD)


class PlacesAreNotReadTest(C1Harness):
    """控えの置き場を動かす環境変数は読まない（置き場は固定。A9）。

    `.ccnavi/scripts/` が写す版（i0064-10）になる前は落ちる。写す前の sh（`ccnavi-common.sh` の
    `ccnavi_state`）は `CCNAVI_STATE` を読み、ロックと控えを env が指す場所に置くため。
    """

    def hold_lock(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        now = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout)
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {now} {live_sh_pid(self)}-{now} {system}\n",
        )

    def test_the_state_variable_does_not_move_the_lock_or_the_record(self):
        outside = os.path.join(self._tmp.name, "elsewhere-state")
        # 既定の置き場（logs/state/locks/...）のロックを持たれていれば、env を入れても止まる。
        self.hold_lock()
        held = self.ticket("start", PARENT, CCNAVI_STATE=outside)
        self.assertEqual(held.returncode, 1, held.stdout + held.stderr)
        self.assertIn("ロックを他の操作が持っている", held.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        shutil.rmtree(self.lock_dir())
        # ロックが空けば、既定の置き場でロックを取り、家族の控えを既定の置き場に書いて送る。
        result = self.ticket("start", PARENT, CCNAVI_STATE=outside)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(fields(self.record)["sha"], head)
        self.assertFalse(os.path.exists(self.lock_dir()))
        self.assertFalse(os.path.exists(outside), "CCNAVI_STATE を読んでいる")


class C1NotImportedTest(C1Harness):
    """10. 取り込み状態の無い親子のチケットは今のまま（書くだけ。コミットも push もしない）。"""

    imported = False

    def test_start_only_writes(self):
        head = self.sha(self.tree, "HEAD")
        remote = self.remote_sha()
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertEqual(self.remote_sha(), remote)
        self.assertIn(f"{APPROVED}/doing/{PARENT}.md", self.dirty())
        self.assertEqual(result.stderr, "")
        self.assertFalse(os.path.exists(os.path.join(self.state, "c1")))

    def test_the_same_output_as_the_executable(self):
        """sh を通しても、実行ファイルを直に打ったのと同じ出力と終了コード。"""
        direct = self.exe("ticket", "cancel", CHILD, "--reason", "r")
        git(self.tree, "checkout", "-q", "--", ".")
        git(self.tree, "clean", "-qfd")
        via = self.ticket("cancel", CHILD, "--reason", "r")
        self.assertEqual(via.returncode, direct.returncode)
        self.assertEqual(via.stdout, direct.stdout)
        self.assertEqual(via.stderr, direct.stderr)

    def test_origin_less_family_with_a_record_is_not_c1(self):
        write(
            self.record,
            f"remote origin\nbranch {PARENT}\nsha x\nfetched_at 1\nstate present\nreason \n",
        )
        git(self.ws, "remote", "remove", "origin")
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(f"{APPROVED}/doing/{PARENT}.md", self.dirty())


class C1ChatOnlyTest(C1Harness):
    """10. chat だけの親子のチケット（マージリクエストを持たない）は、
    取り込み済みでも
    C1 にしない。
    """

    plan = ("chores",)

    def test_start_only_writes(self):
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(f"{APPROVED}/doing/{PARENT}.md", self.dirty())
        family = self.exe("c1", "family", PARENT)
        self.assertIn("target no", family.stdout)
        self.assertIn("chat だけの親子のチケット", family.stdout)


class PhaseOne:
    """フェーズ 1（chat で見る）の子を閉じるまでの道具。"""

    def finish_phase_one(self):
        """フェーズ 1（chat で見る）の子を着手して閉じる。"""
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        started = self.ticket("start", CHILD)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        finished = self.ticket("finish", CHILD)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)


class C1HumanTest(PhaseOne, C1Harness):
    """9. ユーザの判断の入口は、取り込み済みの親子のチケットなら承認の push を自動で呼ぶ。

    11. 承認の push。
    """

    plan = ("chores", "design")

    def test_chat_review_is_carried(self):
        self.finish_phase_one()
        result = self.sh("ccnavi-review.sh", "chat", "1", cwd=self.tree, stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rel = f"{APPROVED}/phases/{PARENT}/1.reviewed"
        self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertIn(rel, self.committed())
        self.assertEqual(self.dirty(), "")

    def test_a_chat_review_typed_directly_is_named_as_a_human_decision(self):
        """直に打った chat の書き込みは、次の C1 で (c) として止まり、承認の push を案内する。"""
        self.finish_phase_one()
        typed = self.exe("--cwd", self.tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertEqual(typed.returncode, 0, typed.stdout + typed.stderr)
        stopped = self.ticket("finish", CHILD)
        self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
        self.assertIn("ユーザの判断が未送信", stopped.stderr)
        self.assertNotIn("知らない変更", stopped.stderr)
        carried = self.sh("ccnavi-push-approved.sh", PARENT)
        self.assertEqual(carried.returncode, 0, carried.stdout + carried.stderr)
        self.assertEqual(self.dirty(), "")

    def test_push_approved_without_names_skips_child_trees(self):
        """名前を省いた承認の push: 親は 1 度だけコミットして push し、
        子のワークツリーの置き場はコミットせずに言う。
        """
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        child = self.child_tree()
        write(os.path.join(child, APPROVED, "doing", "stray.md"), "x\n")
        result = self.sh("ccnavi-push-approved.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("子のワークツリー", result.stderr)
        self.assertEqual(
            result.stdout.count("承認済みチケットを、取り込んでから"), 1, result.stdout
        )
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(CHILD), "")

    def test_push_approved_takes_in_before_sending(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        remote = self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-push-approved.sh", PARENT)
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
        result = self.sh("ccnavi-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("コミットは残した", result.stderr)
        self.assertIn(rel, self.committed())
        # 次の C1 は未送信のユーザの判断を見つけて止まり、承認の push を打ち直すよう言う。
        os.remove(os.path.join(self.remote, "hooks", "pre-receive"))
        stopped = self.ticket("start", PARENT)
        self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
        self.assertIn("未送信のユーザの判断", stopped.stderr)
        again = self.sh("ccnavi-push-approved.sh", PARENT)
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
        result = self.sh("ccnavi-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 同じ中身なので、名前の付け替えとして読まずに 2 つのパスで見る。
        out = git(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow_rel, taken]))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        left = self.dirty()
        self.assertIn(rewritten, left)
        self.assertIn(untracked, left)
        self.assertNotIn(taken, left)
        self.assertEqual(git(self.tree, "diff", "--cached", "--name-only").stdout, "")

    def test_push_approved_without_names_carries_the_family_through_c1(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-push-approved.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認済みチケットを、取り込んでから", result.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))


class C1NotImportedHumanTest(PhaseOne, C1Harness):
    """10. 取り込み状態の無い親子のチケットでは、
    ユーザの判断の入口は置くだけでコミットしない（今のまま）。"""

    plan = ("chores", "design")
    imported = False

    def test_chat_review_is_not_carried(self):
        self.finish_phase_one()
        head = self.sha(self.tree, "HEAD")
        remote = self.remote_sha()
        result = self.sh("ccnavi-review.sh", "chat", "1", cwd=self.tree, stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rel = f"{APPROVED}/phases/{PARENT}/1.reviewed"
        self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)))
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertEqual(self.remote_sha(), remote)
        self.assertNotIn("push-approved", result.stdout + result.stderr)


# 実行ファイルの半分の代役。残った指摘の行き先（`--reviewed ... --yes`）だけを代わりに書き
# （親のワークツリーにレビュー済みマーカーを置き、書いたパスの一覧を出し、
# 答えの JSON と下書きを書く）、
# 残り（`c1 family`・`c1 sort`・`sync paths` など）は本物に渡す。
HALF = """#!/bin/sh
case " $* " in
*" --reviewed "*" --yes "*)
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
PYTHONPATH='{root}/src' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""

# git の代役。`remote get-url origin` だけをホストの代役の URL で答え、残りは本物に渡す
# （取り込みと push は bare のリモート、ホストの API は代役へ）。
URL_GIT = """#!/bin/sh
case " $* " in
*" remote get-url origin "*) printf '%s\\n' '{url}'; exit 0 ;;
esac
exec '{git}' "$@"
"""


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class C1ReviewTest(C1Harness):
    """ccnavi-review.sh の状態を書く副命令も C1 で回す（decide を代表に見る）。"""

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
        shim = os.path.join(self._tmp.name, "shim")
        url = f"http://127.0.0.1:{GitLab.port}/demo/greeter.git"
        executable(os.path.join(shim, "git"), URL_GIT.format(url=url, git=GIT))
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.half = executable(
            os.path.join(self._tmp.name, "bin", "half"),
            HALF.format(root=ROOT, python=sys.executable),
        )

    def decide(self, *args):
        return self.sh(
            "ccnavi-review.sh",
            "decide",
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
            self.subjects(1), [f"ccnavi: {PARENT}のフェーズ 1 の指摘の行き先を決めた"]
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
# （親のワークツリーにマーカーを置き、書いたパスの一覧を出す）、`c1`・`sync` などは本物に渡す。
# HALF_FAIL にファイル名があれば、`review requested` を最初の 1 回だけ落とす（打ち直しの試験）。
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
*" --close-early "*) put "$m/2.skipped" '{{"by": "close-early", "at": "t"}}'; exit 0 ;;
esac
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
            self.reply(200, [{"iid": 1, "web_url": url, "source_project_id": 1}])
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
class C1HostHarness(C1Harness):
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
        shim = os.path.join(self._tmp.name, "shim")
        url = f"http://127.0.0.1:{Host.port}/demo/greeter.git"
        executable(os.path.join(shim, "git"), URL_GIT.format(url=url, git=GIT))
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.half = executable(
            os.path.join(self._tmp.name, "bin", "hosthalf"),
            HOST_HALF.format(root=ROOT, python=sys.executable),
        )

    def review(self, *args, stdin="", **extra):
        env = {
            "PATH": self.path,
            "GITLAB_TOKEN": "t0k",
            "CCNAVI_BIN_PATH": self.half,
            "HALF_TREE": self.tree,
        }
        env.update(extra)
        return self.sh("ccnavi-review.sh", *args, cwd=self.tree, stdin=stdin, **env)

    def carried(self, rel):
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class C1HostTest(C1HostHarness):
    """ホストに触る副命令（request・confirm・ready・decide・close-early）の C1。"""

    def test_request_confirm_and_ready_are_carried(self):
        body = write(os.path.join(self._tmp.name, "body.md"), "見てほしい\n")
        self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", ELI5)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.requested")
        self.assertEqual(len(Host.notes), 1)
        # 依頼文は ELI5 の在りか（差分の中の相対パス）と crit push の送り先を言う。
        # 指摘はユーザが crit push で行のスレッドとして送る
        self.assertIn(f"`crit review {ELI5}`", Host.notes[0]["body"])
        self.assertIn("`crit push 1`", Host.notes[0]["body"])
        log = os.path.join(self._tmp.name, "tidy.log")
        logger = executable(
            os.path.join(self._tmp.name, "bin", "logger"),
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
            self.assertIn(f"worktree drop {PARENT}", f.read())
        self.assertIn(f"cwd がワークツリー {PARENT} の中にあるので消さなかった", ready.stdout)
        self.assertIn(f"ccnavi-clean.sh --worktree {PARENT}", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draftを外した"), ready.stdout.index("cwd がワークツリー")
        )
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_and_pushes_the_archive_removals_before_undrafting(self):
        """ready の退避（閉じたチケットの削除）は、C1 がコミットして push してから Draft を外す。"""
        old = f"{APPROVED}/events/old.ndjson"
        write(os.path.join(self.tree, *old.split("/")), "{}\n")
        git(self.tree, "add", old)
        git(self.tree, "commit", "-q", "-m", "old")
        git(self.tree, "push", "-q", "origin", PARENT)
        mover = executable(
            os.path.join(self._tmp.name, "bin", "mover"),
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
        self.assertIn("Draftを外した", ready.stdout)
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
        self.assertIn("Draftを外した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を消した", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draftを外した"),
            ready.stdout.index(f"ワークツリー {PARENT} を消した"),
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
        self.assertIn("Draftを外せなかった", ready.stderr)
        self.assertNotIn("を消した", ready.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_from_outside_by_a_relative_script_path(self):
        """ワークスペースルートから相対パス（`sh .ccnavi/scripts/ccnavi-review.sh`）で打っても、
        親のワークツリーへ移った後に C1 の取り込み（ccnavi-sync.sh）を見失わない。

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
        self.assertIn("Draftを外した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を消した", ready.stdout)
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
                self.assertNotIn("Draftを外した", refused.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_the_wip_removals_and_names_them(self):
        """ready が消した wip/ の追跡済みのファイルは、C1 が退避と一緒にコミットして push する。
        消したものと、戻せる版（消す前の HEAD）を出す。"""
        self.eli5()
        head = self.sha(self.tree, "HEAD")
        mover = executable(
            os.path.join(self._tmp.name, "bin", "mover"),
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
        self.assertIn("wip/ の追跡済みのファイルを消した（1 件", ready.stdout)
        self.assertIn(head[:12], ready.stdout)
        self.assertIn(f"  {ELI5}", ready.stdout)
        self.carried(ELI5)
        self.assertEqual(git(self.tree, "ls-tree", "HEAD", "--", ELI5).stdout, "")

    def test_the_real_ready_moves_the_rest_again_after_a_failed_push(self):
        """本物の実行ファイルの ready（退避を始めた後の打ち直し）を C1 の中で通す。

        1 回目の push をリモートが断る → C1 が戻す → 取り込みからやり直して残りを移し、送ってから
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
        write(os.path.join(self.tree, APPROVED, "events", "old.ndjson"), '{"a": 1}\n')
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "退避の途中")
        git(self.tree, "push", "-q", "origin", PARENT)
        head = self.sha(self.tree, "HEAD")
        write(
            os.path.join(archive_base, "ready", f"{PARENT}.json"),
            json.dumps(
                {"parent": PARENT, "tree": os.path.normcase(os.path.realpath(self.tree)),
                 "head": head, "files": []}
            ),
        )  # fmt: skip
        # リモートは 1 回目の push だけ断る
        once = os.path.join(self._tmp.name, "refused-once")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            f"#!/bin/sh\n[ -e '{once}' ] && exit 0\n: >'{once}'\necho refused >&2\nexit 1\n",
        )
        ready = self.review("ready", CCNAVI_BIN_PATH=self.bin)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertTrue(os.path.exists(once))
        self.assertIn("push が通らなかった", ready.stderr)
        self.assertIn("Draftを外した", ready.stdout)
        self.carried(f"{APPROVED}/done/old.md")
        for rel in ("done/old.md", "phases/old/closed.json", "events/old.ndjson"):
            self.assertEqual(
                git(self.tree, "ls-tree", "HEAD", "--", f"{APPROVED}/{rel}").stdout, ""
            )
            self.assertTrue(os.path.isfile(os.path.join(archive_base, *rel.split("/"))), rel)
        with open(os.path.join(archive_base, "events", "old.ndjson"), encoding="utf-8") as f:
            self.assertEqual(f.read().count('"archived"'), 1)

    def test_a_request_is_not_posted_twice_across_runs(self):
        """投稿の後に落ちた依頼を打ち直しても、同じ目印の依頼は投稿し直さない。"""
        body = write(os.path.join(self._tmp.name, "body.md"), "見てほしい\n")
        once = os.path.join(self._tmp.name, "failed-once")
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

    def test_terminal_decide_chooses_outside_c1_and_writes_inside(self):
        """端末の decide は、選ぶのを C1 の外で済ませ、C1 の中では --yes で書くだけ。"""
        chooser = executable(
            os.path.join(self._tmp.name, "bin", "chooser"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" --choose-out "*)\n'
            '  prev=""; for a in "$@"; do [ "$prev" = --choose-out ] && out="$a"; prev="$a"; done\n'
            f"  [ -d '{self.lock_dir()}' ] && echo LOCKED-WHILE-CHOOSING >&2\n"
            '  printf \'{"choices": {"u1": "keep"}, "digest": "d0"}\' >"$out"\n'
            "  exit 0 ;;\n"
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
        done = self.review("decide", "1", CCNAVI_BIN_PATH=chooser)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("行き先を置いて送った", done.stdout)
        self.assertNotIn("LOCKED-WHILE-CHOOSING", done.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.reviewed")

    def test_close_early_hands_over_to_the_carrier(self):
        closed = self.review("close-early", "--reason", "r", "--no-issue")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/2.skipped")


@unittest.skipIf(shutil.which("jq") is None, "jq が要る")
class C1ChromeConfirmTest(PhaseOne, C1Harness):
    """Chrome のレビュー済みと手元の confirm の突き合わせ。

    取り込み済みの親子のチケットでは、手元の CLI を直に打つと C1 に断られる
    （`--record-tree` が無い）。そこで C1 と同じ手順
    （`ccnavi-review.sh request` と `confirm`。GitHub の代役は録った見本）で手元を
    回し、同じ状態から Chrome の入口が出す書くものと、C1 が親のブランチへ書いて送ったものを比べる。
    違ってよいのは経路（`via`）と時刻（`at`）と拡張の版だけ。アカウント（`actor`）は、手元は sh が
    トークンの持ち主を引いたもの、Chrome は PAT の持ち主で、同じ見本なので同じになる。
    """

    SCENE = "resolved"

    def setUp(self):
        super().setUp()
        from tests.sh import github_host

        self.host = github_host
        shim = os.path.join(self._tmp.name, "shim")
        executable(
            os.path.join(shim, "git"),
            URL_GIT.format(url="https://github.com/acme/widgets.git", git=GIT),
        )
        fakes = os.path.join(self._tmp.name, "fakes")
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
            FAKE_GITHUB_STATE=os.path.join(self._tmp.name, "comments.json"),
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
        from tests.ticket.test_core import _chrome

        chrome = _chrome()
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
            "actor": {"account": "octo-reviewer", "version": "9.9.9"},
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
        answer = json.loads(
            chrome.handle(json.dumps(request), os.path.join(self._tmp.name, "memfs"))
        )
        self.assertNotIn("error", answer, answer)
        return answer

    @property
    def requested(self):
        return f"{APPROVED}/phases/{PARENT}/1.requested"

    @staticmethod
    def normalized(path, text):
        """経路・時刻・拡張の版を落とす（違ってよいもの）。"""
        drop = ("via", "at", "version")
        if path.endswith(".ndjson"):
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            return [{k: v for k, v in r.items() if k not in drop} for r in rows]
        if path.endswith((".reviewed", ".json")):
            return {k: v for k, v in json.loads(text).items() if k not in drop}
        return text

    def request(self):
        body = write(os.path.join(self._tmp.name, "body.md"), "見てほしい\n")
        html = self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", html)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.assertIn(self.requested, self.committed())
        return body

    def same_refusal(self, scene):
        """Chrome が通さない理由が、C1 で回した手元の confirm の標準エラーにそのまま出る。"""
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

    def test_chrome_writes_what_c1_writes_and_sends(self):
        body = self.request()

        answer = self.chrome_request()
        self.assertEqual(answer["problems"], [])
        rows = answer["changes"][PARENT]

        # 手元を直に打つと C1 に断られる（だから C1 の手順で回す）。文面まで見る
        direct = self.exe("--cwd", self.tree, "review", "confirm", "--phase", "1", "--result", body)
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn(f"親子のチケット {PARENT} は取り込み済み（C1 の対象）", direct.stderr)
        self.assertIn("何も書かずに止めた", direct.stderr)

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
        self.assertEqual(again["problems"], ["ccnavi: フェーズ 1 はレビュー済み"])


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class C1HostNotImportedTest(C1HostHarness):
    """取り込み済みでない親子のチケットの ready。退避の削除は送らずに止め、Draft を外さない。"""

    imported = False

    def mover(self, tree_line):
        return executable(
            os.path.join(self._tmp.name, "bin", "mover"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            '  root=""; prev=""\n'
            '  for a in "$@"; do [ "$prev" = --root ] && root="$a"; prev="$a"; done\n'
            '  mkdir -p "$root/logs/state"; : >"$root/logs/state/note.md"\n'
            "  printf '%s\\n' \"$root/logs/state/note.md\"\n"
            f"  {tree_line}\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )

    def test_ready_stops_when_the_archived_tree_has_uncommitted_changes(self):
        # 退避したツリー（実行ファイルが答える）に未コミットの削除がある。cwd が綺麗でも止める。
        other = os.path.join(self._tmp.name, "other")
        git(self._tmp.name, "init", "-q", "-b", "main", other)
        for key, value in CONFIG:
            git(other, "config", key, value)
        write(os.path.join(other, APPROVED, "done", "old.md"), "x\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "old")
        os.remove(os.path.join(other, APPROVED, "done", "old.md"))
        ready = self.review("ready", CCNAVI_BIN_PATH=self.mover(f"printf 'tree %s\\n' '{other}'"))
        self.assertNotEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("未コミットの変更がある", ready.stderr)
        self.assertNotIn("Draftを外した", ready.stdout)

    def test_ready_stops_when_wip_removals_are_not_sent(self):
        # ready が消した wip/ の追跡済みのファイル（未コミット）。送るまで Draft を外さない。
        self.eli5()
        os.remove(os.path.join(self.tree, *ELI5.split("/")))
        ready = self.review(
            "ready", CCNAVI_BIN_PATH=self.mover(f"printf 'tree %s\\n' '{self.tree}'")
        )
        self.assertNotEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("wip/ に未コミットの変更がある", ready.stderr)
        self.assertNotIn("Draftを外した", ready.stdout)

    @unittest.skipIf(os.name == "nt", "大文字違いの並存と名前の \\ は Windows では作れない")
    def test_ready_stops_when_wip_removals_in_any_case_are_not_sent(self):
        """実行ファイルが消した `WIP/…` や `wip\\…` の削除も、送るまで Draft を外さない。

        実行ファイルは大文字小文字・\\ を問わず wip/ と見る。確かめるのは実行ファイルが出した
        `wip <パス>` の行そのもの。
        """
        for rel in ("WIP/eli5/a.html", "wip\\eli5\\b.html"):
            with self.subTest(rel=rel):
                write(os.path.join(self.tree, rel), "<p>x</p>\n")
                git(self.tree, "add", "--", rel)
                git(self.tree, "commit", "-q", "-m", f"add {rel}")
                git(self.tree, "push", "-q", "origin", PARENT)
                os.remove(os.path.join(self.tree, rel))
                ready = self.review(
                    "ready",
                    CCNAVI_BIN_PATH=self.mover(
                        f"printf 'tree %s\\nwip-from x\\n' '{self.tree}'; "
                        f"printf '%s\\n' 'wip {rel}'"
                    ),
                )
                self.assertNotEqual(ready.returncode, 0, ready.stdout + ready.stderr)
                self.assertIn("未コミットの変更がある", ready.stderr)
                self.assertNotIn("Draftを外した", ready.stdout)
                git(self.tree, "rm", "-q", "--cached", "--", rel)
                git(self.tree, "commit", "-q", "-m", f"rm {rel}")
                git(self.tree, "push", "-q", "origin", PARENT)

    def test_ready_goes_on_when_the_archived_tree_is_clean(self):
        ready = self.review(
            "ready", CCNAVI_BIN_PATH=self.mover(f"printf 'tree %s\\n' '{self.tree}'")
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draftを外した", ready.stdout)


if __name__ == "__main__":
    unittest.main()
