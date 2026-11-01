"""SessionStart で、`scratchpad/` の中身が大きくなったことをユーザに尋ねる文にして渡す。

`scratchpad/`（ワークスペースルート直下の一時の置き場。git に追跡されない）は、ccnavi が消さない。
中身の合計が `CCNAVI_SCRATCHPAD_WARN_MB`（既定 500 MB。0 で知らせない）を超えたら、消してよい
ものをユーザに尋ねる文を渡す。消すのはユーザが了承したものだけで、ここでは消さない。
`logs/` は既存の自動後始末（records/prune）に任せ、数えない。

合計は通常ファイルの大きさの和で、シンボリックリンクは辿らず数えない。読めないものは飛ばす。
"""

from __future__ import annotations

import math
import os
import time

from ...policy import texts
from ..model import ticket_places

WARN_MB_ENV = "CCNAVI_SCRATCHPAD_WARN_MB"
DEFAULT_WARN_MB = 500.0
_MB = 1024 * 1024


def warn_mb() -> float:
    """しきい値（MB）。空・読めない・負・無限大・nan は既定。0 は知らせない。"""
    raw = os.environ.get(WARN_MB_ENV, "").strip()
    if not raw:
        return DEFAULT_WARN_MB
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_WARN_MB
    if not math.isfinite(value) or value < 0:
        return DEFAULT_WARN_MB
    return value


def scratch_bytes(root: str, until: float | None = None) -> int:
    """`scratchpad/` の中の通常ファイルの大きさの合計。無ければ 0。

    `until`（`time.monotonic()` の時刻）を過ぎたら、そこまでの合計を返す。
    シンボリックリンクと junction は辿らず、数えない。
    """
    total = 0
    stack = [ticket_places.scratch_dir(root)]
    while stack and (until is None or time.monotonic() < until):
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def session_text(root: str, until: float | None = None) -> str:
    """しきい値を超えたときの文。超えない・止めている・失敗したときは空。

    `until` は走査を打ち切る時刻（`time.monotonic()`）。"""
    try:
        limit = warn_mb()
        if limit <= 0:
            return ""
        size = scratch_bytes(root, until)
        if size <= limit * _MB:
            return ""
        values = {
            "size": f"{size / _MB:.0f} MB",
            "limit": f"{limit:g} MB",
            "path": ticket_places.scratch_dir(root),
        }
        return texts.say(root, "NOTICE_SCRATCHPAD_LARGE", values)
    except Exception:  # noqa: BLE001 - 容量の知らせの失敗でセッションの開始を止めない
        return ""
