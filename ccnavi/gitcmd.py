"""git を 1 回起こして、終わり方をそのまま返す。

git を起こす場所は 1 つにする。呼ぶ側はそれぞれ「読めなければ黙る」「理由を
文にする」「終了コードで分ける」と受け方が違うが、起こし方（引数、文字コード、
期限、起こせなかったときの捕まえ方）は全部同じで、別々に書くと片方だけが
古くなる。ここは起こすだけで、何も判断しない。

期限は呼ぶ側が渡す。実行前の判定の中で起こすものは短く、人が端末で打つ
操作は長く、と場所ごとに違うので、ここで 1 つに決めない。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# 渡されなかったときの期限。
TIMEOUT_SECONDS = 5.0


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
    cwd: str, args: list[str], timeout: float = TIMEOUT_SECONDS, raw_paths: bool = False
) -> Done:
    """git を起こす。

    raw_paths は `core.quotePath=false` を掛ける。既定の出力は非 ASCII を含む
    パスを引用して 8 進に逃がすので、パスをそのまま突き合わせる側はこれを立てる。
    """
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
            timeout=timeout,
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
