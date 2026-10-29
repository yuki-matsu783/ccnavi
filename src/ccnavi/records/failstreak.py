"""ツール呼び出しの失敗や ccnavi の拒否が続いた回数を数える（issue #270）。

エージェントは、コマンドが失敗したり ccnavi に止められたりしても、資料を引かずに試行錯誤を
重ねることがある。失敗が N 回続いたら、資料の索引を引くよう案内する。ここは数えるだけで、
案内の文面は catalog 側（docsearch）が持つ。

数える対象は 2 つ。ツール自体の失敗（PostToolUseFailure）と、ccnavi の拒否（PreToolUse の deny。
dry-run でも数える。dry-run は判定を変えないので、案内を出してよい）。成功（PostToolUse）が
入ったら 0 に戻す。案内は同じ連続の中で 1 回だけ出す。案内済みの印を記録に持ち、リセットで戻す。

判定は変えない。数えられなかった（記録を読めない・書けない）ときは何も出さない。
数えの失敗が判定に影響する経路は作らない。

記録は state の置き場の `failstreak-<セッション>-<main|エージェント>.json`。サブエージェントの中の
呼び出しは、そのサブエージェントの連続として別に数える（`once-*` と同じ分け方）。案内は
その文脈のモデルにしか届かないので、メインの成功が子の連続を消したり、子の失敗がメインの案内を
出したりしないようにする。セッションが空なら数えない。
"""

from __future__ import annotations

import os

from ..infra import fsio

# 回数の既定。環境変数 CCNAVI_FAIL_STREAK_COUNT で動かせる（settings.FAIL_STREAK_COUNT_ENV）。
DEFAULT_THRESHOLD = 3


def threshold(written: str) -> int:
    """書かれた回数。0 は機能を使わない。読めない値と負数は既定に戻す。"""
    try:
        n = int(written.strip())
    except ValueError:
        return DEFAULT_THRESHOLD
    return n if n >= 0 else DEFAULT_THRESHOLD


def _path(state_dir: str, session: str, agent: str) -> str:
    agent_part = fsio.safe_name(agent) or "main"
    return os.path.join(state_dir, f"failstreak-{fsio.safe_name(session)}-{agent_part}.json")


def fail(state_dir: str, session: str, agent: str, n: int) -> int:
    """失敗か拒否を 1 回数え、案内を出す回なら今の連続の長さを返す。出さない回は 0。

    出すのは、連続が n に達して、まだ案内していないとき 1 回だけ。案内したことを記録に
    書けなければ出さない（覚えられないまま出すと、失敗のたびに同じ案内が並ぶ）。
    n が 0 以下（機能を使わない）、置き場が無い（`--state ""`）、セッションが空のときは数えない。
    """
    if n <= 0 or not state_dir or not session:
        return 0
    try:
        path = _path(state_dir, session, agent)
        data = fsio.read_dict(path) or {}
        count = int(data.get("count") or 0) + 1
        told = data.get("told") is True
        tell = count >= n and not told
        data = {"count": count, "told": told or tell}
        if fsio.write_json_atomic(path, data):
            return 0
        return count if tell else 0
    except Exception:  # noqa: BLE001  数えの失敗で処理を止めない
        return 0


def reset(state_dir: str, session: str, agent: str, n: int) -> None:
    """成功が入った。連続を 0 に戻し、案内済みの印も戻す。"""
    if n <= 0 or not state_dir or not session:
        return
    try:
        fsio.remove(_path(state_dir, session, agent))
    except Exception:  # noqa: BLE001  戻せなくても処理は止めない
        return
