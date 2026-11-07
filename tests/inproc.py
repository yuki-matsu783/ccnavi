"""テストから ccnavi を 1 回動かす。実行ファイル（tests/exe.py が用意する Go のもの）を起こす。

subprocess.run と同じ形（引数のリスト、input、env、cwd）を受け、CompletedProcess を
返す。返す args は ccnavi に渡した引数だけ（実行ファイルのパスは入れない）。

- env は「その起動に見える環境そのもの」として扱う。subprocess.run(env=...) と同じで、
  渡したマップに無い変数は見えない。例外は走った機械の git の設定を締め出す分
  （tests.GIT_ENV）で、これは空にしても残す。判定の中で起こす git がホストの
  `~/.gitconfig` を読み直さないようにするため。呼び手が同じ名前を渡したときは呼び手を
  優先する（締め出し方そのものを試すテストのため）
- 起こす前に、実行ファイルが使うワークスペースルートに文面ファイル（`.ccnavi/messages/`）が
  無ければ、このリポジトリの文面をコピーする（`put_messages`）。文を見るテストの大半は、文面を
  気にしない使い捨ての木を組むので、木ごとにコピーさせない。文面ファイルを置いた木（壊れた文面を
  試す木を含む）には触らない。木が git のリポジトリなら、コピーした文面を `.git/info/exclude` に
  書いて git から見えなくする（未追跡の保護対象として、判定や報告に混ざらないように）。
  `.ccnavi/` の無い木そのものを試すテストは `messages=False` を渡す
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

from tests import GIT_ENV, ROOT, exe, put_messages

ROOT_OPTION = "--root"
CLAUDE_DIR = ".claude"
WORKTREES = os.path.join(CLAUDE_DIR, "worktrees")


def run_ccnavi(
    args: Sequence[str],
    *,
    input: str = "",
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    messages: bool = True,
) -> subprocess.CompletedProcess[str]:
    """`ccnavi *args` を起こし、CompletedProcess の形で返す。"""
    argv = list(args)
    # 学びの蓄積の既定は enable。学びを扱わないテストの子の finish が記録で止まらないよう、呼び手が
    # 指定しなければ切る（扱うテストは env で CCNAVI_LEARNING を渡す）。
    if env is None:
        merged = {"CCNAVI_LEARNING": "disable", **os.environ, **GIT_ENV}
    else:
        merged = {"CCNAVI_LEARNING": "disable", **GIT_ENV, **env}
    if messages:
        _put_messages_when_absent(_root_of(argv, merged, cwd))
    done = subprocess.run(
        [exe.path(), *argv],
        input=input or "",
        env=merged,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return subprocess.CompletedProcess(argv, done.returncode, done.stdout, done.stderr)


def _root_of(argv: list[str], env: Mapping[str, str], cwd: str | None) -> str:
    """実行ファイルが使うワークスペースルート。`--root`、CLAUDE_PROJECT_DIR、作業ディレクトリから上って
    `.claude` を持つ所（ワークツリーは飛ばす）の順。実行ファイルの既定のルートと同じ決め方。"""
    for at, word in enumerate(argv):
        if word == ROOT_OPTION and at + 1 < len(argv):
            return argv[at + 1]
        if word.startswith(ROOT_OPTION + "="):
            return word.partition("=")[2]
    if env.get("CLAUDE_PROJECT_DIR"):
        return env["CLAUDE_PROJECT_DIR"]
    directory = os.path.abspath(cwd or os.getcwd())
    while True:
        holder = os.path.dirname(directory)
        if os.path.isdir(os.path.join(directory, CLAUDE_DIR)) and not same_path(
            holder, os.path.join(os.path.dirname(holder), WORKTREES)
        ):
            return directory
        if holder == directory:
            return ""
        directory = holder


def _fold(path: str) -> str:
    """パスを比べる形。どの機械でも大文字小文字を区別しない（Go の pathcase と同じ）。"""
    return os.path.normcase(os.path.abspath(path)).casefold()


def same_path(a: str, b: str) -> bool:
    return _fold(a) == _fold(b)


def _put_messages_when_absent(root: str) -> None:
    if not root or not os.path.isdir(root) or same_path(root, ROOT):
        return
    if os.path.lexists(os.path.join(root, ".ccnavi", "messages")):
        return
    put_messages(root)
    info = os.path.join(root, ".git", "info")
    if os.path.isdir(os.path.join(root, ".git")):
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "a", encoding="utf-8", newline="\n") as f:
            f.write("\n/.ccnavi/messages/\n")
