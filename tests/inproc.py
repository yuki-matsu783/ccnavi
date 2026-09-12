"""テストから ccnavi を 1 回動かす。プロセスを起こさず、同じプロセスの中で cli.run を呼ぶ。

`python -m ccnavi` は標準入出力を整えて cli.run を呼ぶだけ（ccnavi/__main__.py）。
テストが見るのは引数・標準入力・標準出力・標準エラー・終了コードで、それは
cli.run の入口と出口そのものなので、プロセスを起こさなくても同じものが読める。

1 回の起動が 0.5 秒ほどかかるのは Python の立ち上げと PyYAML の読み込みで、
判定そのものは 0.05 秒に満たない。全件で 700 回起動するので、ここが
テスト時間の半分以上だった。

subprocess.run と同じ形（引数の並び、input、env、cwd）を受け、CompletedProcess を
返す。呼び手の assert は subprocess のときと同じ書き方で通る。

- env は「その起動に見える環境そのもの」として扱う。subprocess.run(env=...) と同じで、
  渡した辞書に無い変数は見えない
- cwd は起動中だけ移る。テストは並行して走らないので、戻し忘れが無ければ他に響かない
- 判定の中で例外が出たら、そのまま伝える。プロセスなら終了コード 1 と traceback に
  なるところだが、テストでは例外として見えたほうが原因に近い

`python -m ccnavi` の入口（標準入出力の付け替え、パッケージとしての起動）は
test_entry.py が本物のプロセスで確かめる。
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from collections.abc import Mapping, Sequence
from unittest import mock

from ccnavi import cli


def run_ccnavi(
    args: Sequence[str],
    *,
    input: str = "",
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`python -m ccnavi *args` を同じプロセスの中で動かし、CompletedProcess の形で返す。"""
    argv = list(args)
    stdin = io.StringIO(input or "")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.ExitStack() as stack:
        if env is not None:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
        if cwd:
            previous = os.getcwd()
            os.chdir(cwd)
            stack.callback(os.chdir, previous)
        returncode = cli.run(stdin, stdout, stderr, argv)
    return subprocess.CompletedProcess(argv, returncode, stdout.getvalue(), stderr.getvalue())
