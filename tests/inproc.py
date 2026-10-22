"""テストから ccnavi を 1 回動かす。プロセスを起こさず、同じプロセスの中で cli.run を呼ぶ。

`python -m ccnavi` は標準入出力を整えて cli.run を呼ぶだけ（src/ccnavi/__main__.py）。
テストが見るのは引数・標準入力・標準出力・標準エラー・終了コードで、それは
cli.run の入口と出口そのものなので、プロセスを起こさなくても同じものが読める。

1 回の起動が 0.5 秒ほどかかるのは Python の立ち上げと PyYAML の読み込みで、
判定そのものは 0.05 秒に満たない。全件で 700 回起動するので、プロセスを
起こすとそこがテスト時間の半分以上を占める。

subprocess.run と同じ形（引数のリスト、input、env、cwd）を受け、CompletedProcess を
返す。呼び手の assert は subprocess のときと同じ書き方で通る。

- env は「その起動に見える環境そのもの」として扱う。subprocess.run(env=...) と同じで、
  渡した辞書に無い変数は見えない。例外は走った機械の git の設定を締め出す分
  （tests.GIT_ENV）で、これは空にしても残す。判定の中で起こす git がホストの
  `~/.gitconfig` を読み直さないようにするため
- cwd は起動中だけ移る。テストは並行して走らないので、戻し忘れが無ければ他に影響しない
- hook の判定の中で出た例外は、本番と同じく cli が受けて内部エラーとして終える
  （標準エラーに `[ccnavi] internal-error`）。それ以外の経路の例外は、そのまま伝える

`python -m ccnavi` の入口（標準入出力の切り替え、パッケージとしての起動）は
test_entry.py が実際のプロセスで確かめる。
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from collections.abc import Mapping, Sequence
from unittest import mock

from ccnavi.entry import cli
from tests import GIT_ENV


def run_ccnavi(
    args: Sequence[str],
    *,
    input: str = "",
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`python -m ccnavi *args` を同じプロセスの中で動かし、CompletedProcess の形で返す。"""
    argv = list(args)
    # 学びの蓄積の既定は enable。学びを扱わないテストの子の finish が記録で止まらないよう、呼び手が
    # 指定しなければ切る（扱うテストは env で CCNAVI_LEARNING を渡す。既定そのものは settings を
    # 直に読んで見る）。
    if env is not None and "CCNAVI_LEARNING" not in env:
        env = {**env, "CCNAVI_LEARNING": "disable"}
    built = os.environ.get("CCNAVI_TEST_EXE", "")
    if built:
        # 組み立て済みの実行ファイルで動かす（`CCNAVI_TEST_EXE=<パス>`）。配る物を測る。
        merged = (
            {"CCNAVI_LEARNING": "disable", **os.environ, **GIT_ENV}
            if env is None
            else {**GIT_ENV, **env}
        )
        done = subprocess.run(
            [built, *argv],
            input=input or "",
            env=merged,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        return subprocess.CompletedProcess(argv, done.returncode, done.stdout, done.stderr)
    stdin = io.StringIO(input or "")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.ExitStack() as stack:
        if env is None:
            stack.enter_context(
                mock.patch.dict(
                    os.environ, {"CCNAVI_LEARNING": os.environ.get("CCNAVI_LEARNING", "disable")}
                )
            )
        if env is not None:
            # 走った機械の git の設定を締め出す分（tests.GIT_ENV）は、環境を空にしても
            # 残す。ccnavi は判定の中で git を起こす（src/ccnavi/infra/gitcmd.py）ので、ここで
            # 消すと、この経路だけがホストの `~/.gitconfig` を読み直す。
            # 呼び手が同じ名前を渡したときは呼び手を優先する。締め出し方そのものを
            # 試すテストが、ここでの締め出しに上書きされないようにするため。
            stack.enter_context(mock.patch.dict(os.environ, {**GIT_ENV, **env}, clear=True))
        if cwd:
            previous = os.getcwd()
            os.chdir(cwd)
            stack.callback(os.chdir, previous)
        returncode = cli.run(stdin, stdout, stderr, argv)
    return subprocess.CompletedProcess(argv, returncode, stdout.getvalue(), stderr.getvalue())
