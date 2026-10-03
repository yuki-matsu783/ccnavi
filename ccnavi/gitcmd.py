"""git を 1 回起こして、終わり方をそのまま返す。

git を起こす場所は 1 つにする。呼ぶ側はそれぞれ「読めなければ何も言わない」「理由を
文にする」「終了コードで分ける」と受け方が違うが、起こし方（引数、文字コード、
期限、起こせなかったときの捕まえ方）は全部同じで、別々に書くと片方だけが
古くなる。ここは起こすだけで、何も判断しない。

期限は呼ぶ側が渡す。実行前の判定の中で起こすものは短く、ユーザが端末で打つ
操作は長く、と場所ごとに違うので、ここで 1 つに決めない。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

# 渡されなかったときの期限。
TIMEOUT_SECONDS = 5.0
# pathspec の読み方を変える環境変数。
PATHSPEC_ENV = (
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)


def _env() -> dict[str, str]:
    """git に渡す環境。親の環境に `GIT_OPTIONAL_LOCKS=0` を足す。

    `status` は index のついでの更新のために `index.lock` を取る。期限で殺すとそれが
    残り、以後の add や commit が止まる。この設定が止めるのは `status` のついでの lock
    だけで、作業ツリーと比べる `diff` の更新や、restore のように index を書き換える
    操作の lock は取られる。

    `GIT_NO_LAZY_FETCH=1` は partial clone の遅延取得を止める。無い blob を読むとき git は
    promisor のリモートへ取りに行くので、実行ファイルがネットワークに出ることになる
    （docs/claude/exe-boundary.md。ADR-0093 の段階 2d のレビュー）。取れない blob は
    「読めない」になる。
    """
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}


@dataclass
class Done:
    """git が走った結果。起こせなかったときは failure にその説明が入る。"""

    code: int = 1
    out: str = ""
    err: str = ""
    # 起こせなかった理由。空なら git は走って終わった（成否は code が言う）。
    failure: str = ""
    # 起こせなかった理由の種類。git が見つからなかったか、期限に達したか。
    missing: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.failure and self.code == 0


def run(
    cwd: str,
    args: list[str],
    timeout: float = TIMEOUT_SECONDS,
    raw_paths: bool = False,
    input: str | None = None,
    plain_pathspecs: bool = False,
) -> Done:
    """git を起こす。

    raw_paths は `core.quotePath=false` を掛ける。既定の出力は非 ASCII を含む
    パスを引用して 8 進の表記に置き換えるので、パスをそのまま突き合わせる側はこれを立てる。
    input は標準入力に渡す文字列（`check-ignore --stdin` など）。無ければ何も渡さない。
    plain_pathspecs はユーザの環境の pathspec の読み方（`GIT_LITERAL_PATHSPECS` など）を外し、
    git の既定の読み方に戻す。`check-ignore` はそれらの magic を受けず 128 で止まる。
    """
    env = _env()
    if plain_pathspecs:
        for name in PATHSPEC_ENV:
            env.pop(name, None)
    command = ["git"]
    if raw_paths:
        command += ["-c", "core.quotePath=false"]
    command += args
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            input=input,
        )
    except FileNotFoundError as exc:
        return Done(failure=f"{exc}", missing=True)
    except subprocess.TimeoutExpired as exc:
        return Done(failure=f"{exc}", timed_out=True)
    except OSError as exc:
        return Done(failure=f"{exc}")
    return Done(done.returncode, done.stdout, done.stderr)


def output(
    cwd: str, args: list[str], timeout: float = TIMEOUT_SECONDS, raw_paths: bool = False
) -> tuple[int, str]:
    """終了コードと標準出力。起こせなければ (1, "")。"""
    done = run(cwd, args, timeout, raw_paths)
    if done.failure:
        return 1, ""
    return done.code, done.out


def blob(
    cwd: str, rev: str, path: str, timeout: float = TIMEOUT_SECONDS
) -> tuple[bytes | None, bool]:
    """その版に入っている 1 本の中身を、バイト列のまま返す。`(中身, 読めた)`。

    文字列で読むと、UTF-8 でない中身（Shift_JIS のコメントを持つスクリプトなど）と単独の CR が
    読み替えられ、中身の突き合わせが食い違う。その版にその綴りが無ければ `(None, True)`、
    git を起こせない・期限に達した・版が無いときは `(None, False)`。
    """
    listed = run(cwd, ["ls-tree", "-z", rev, "--", path], timeout)
    if not listed.ok:
        return None, False
    if not listed.out.strip("\0").strip():
        return None, True
    try:
        done = subprocess.run(
            ["git", "cat-file", "blob", f"{rev}:{path}"],
            cwd=cwd,
            capture_output=True,
            env=_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if done.returncode != 0:
        return None, False
    return done.stdout, True
