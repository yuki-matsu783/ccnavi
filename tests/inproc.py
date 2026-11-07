"""テストから ccnavi を 1 回動かす。実行ファイル（tests/exe.py が用意する Go のもの）を起こす。

subprocess.run と同じ形（引数のリスト、input、env、cwd）を受け、CompletedProcess を
返す。返す args は ccnavi に渡した引数だけ（実行ファイルのパスは入れない）。

- env は「その起動に見える環境そのもの」として扱う。subprocess.run(env=...) と同じで、
  渡したマップに無い変数は見えない。例外は走った機械の git の設定を締め出す分
  （tests.GIT_ENV）で、これは空にしても残す。判定の中で起こす git がホストの
  `~/.gitconfig` を読み直さないようにするため。呼び手が同じ名前を渡したときは呼び手を
  優先する（締め出し方そのものを試すテストのため）
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

from tests import GIT_ENV, exe


def run_ccnavi(
    args: Sequence[str],
    *,
    input: str = "",
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`ccnavi *args` を起こし、CompletedProcess の形で返す。"""
    argv = list(args)
    # 学びの蓄積の既定は enable。学びを扱わないテストの子の finish が記録で止まらないよう、呼び手が
    # 指定しなければ切る（扱うテストは env で CCNAVI_LEARNING を渡す）。
    if env is None:
        merged = {"CCNAVI_LEARNING": "disable", **os.environ, **GIT_ENV}
    else:
        merged = {"CCNAVI_LEARNING": "disable", **GIT_ENV, **env}
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
