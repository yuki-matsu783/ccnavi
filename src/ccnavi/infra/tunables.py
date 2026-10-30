"""環境変数で動かせる、秒数のしきい値。

名前と既定をここに並べ、使う側は `value()` で読む。値は `settings.json` の `env` で渡す
（Claude Code が起こしたプロセスの環境に入る）。読めない値・0 以下・有限でない値は既定に戻す。
判定の答えは変えず、待つ長さと数える長さだけを動かす。
"""

from __future__ import annotations

import math
import os
from typing import NamedTuple


class Tunable(NamedTuple):
    env: str
    default: float

    def value(self) -> float:
        """環境変数の秒数。読めない値と 0 以下は既定。"""
        try:
            seconds = float(os.environ.get(self.env, "").strip())
        except ValueError:
            return self.default
        return seconds if math.isfinite(seconds) and seconds > 0 else self.default


# 1 回の hook の判定に張る期限（hook.judge）。
HOOK_DEADLINE = Tunable("CCNAVI_HOOK_DEADLINE_SECONDS", 3.0)
# SessionStart で、終わっていない integrate の記録を確かめる時間の上限（hook.events）。
INTEGRATE_PENDING = Tunable("CCNAVI_INTEGRATE_PENDING_SECONDS", 2.0)
# 実行中のサブエージェントの印を「実行中」と数える長さの上限（hook.subagent）。
SUBAGENT_RUNNING_MAX = Tunable("CCNAVI_SUBAGENT_RUNNING_MAX_SECONDS", 4 * 3600.0)
# 更新が無いまま、ターンの印を古いと数えるまでの長さ（records.turn）。
TURN_STALE = Tunable("CCNAVI_TURN_STALE_SECONDS", 30 * 60.0)
# 停止レビューの判定計画を待つ時間と、試行の期限（records.stopreview）。
STOP_REVIEW_TTL = Tunable("CCNAVI_STOP_REVIEW_TTL_SECONDS", 600.0)
