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

from tests import ROOT, common_path, config_path
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


CHILD_B = f"{PARENT}-01-02"


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


class WriteAndPushMergeHarness(WriteAndPushHarness):
    """子の finish は、親のブランチへ合流してから push する（合流と状態は 1 つのマージコミット）。

    1. 合流・完了の記録・子の merged_sha が 1 つのマージコミットになり、push される
    2. 記録の無い別の PC でも、子 2 の着手で子 1 のコードが入り、finish の合流まで通る
    3. 置き場の書き換え・範囲外のパス・衝突・未コミット（子も親も）は、何も変えずに止まる
    4. push の失敗・コミットの失敗・シグナル中断は、合流ごと元に戻る。応答が失われた push は成功
    5. 衝突を解くために子へ親を取り込んでも、リスクの計測にほかの子の変更が混ざらない"""

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

    def assert_unrestorable_stops(self, kind):
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("合流を戻せない形の変更がある", result.stderr)
        self.assertIn(kind, result.stderr)
        self.assert_untouched(before)

    # ---- 4. 失敗とシグナルは合流ごと戻す

    def assert_rolled_back(self, before):
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.merging())
        self.assertFalse(os.path.exists(os.path.join(self.tree, "wip", "design", "a.md")))
        self.assertTrue(os.path.isfile(self.copy(CHILD)))
        self.assertFalse(os.path.exists(self.lock_dir()))

    # ---- 6. 検査・合流・シグナルの隙間

    def git_shim(self, body):
        """`git` の前に body を挟む PATH。body の中の "$REAL" は本物の git。"""
        shim = os.path.join(self._base, "shim")
        executable(
            os.path.join(shim, "git"),
            f'#!/bin/sh\nREAL=\'{GIT}\'\n{body}\nexec "$REAL" "$@"\n',
        )
        return shim + os.pathsep + os.environ.get("PATH", "")

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
