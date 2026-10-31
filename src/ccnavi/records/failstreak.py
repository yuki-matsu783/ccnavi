"""ツール呼び出しの失敗や ccnavi の拒否が続いた回数を数える（issue #270）。

エージェントは、コマンドが失敗したり ccnavi に止められたりしても、資料を引かずに試行錯誤を
重ねることがある。失敗が N 回続いたら、資料の索引を引くよう案内する。ここは数えるだけで、
案内の文面は catalog 側（docsearch）が持つ。

数える対象は 2 つ。ツール自体の失敗（PostToolUseFailure）と、ccnavi の拒否（PreToolUse の deny。
dry-run でも数える。dry-run は判定を変えないので、案内を出してよい）。成功（PostToolUse）が
入ったら 0 に戻す。案内は 2 つで、最初の 1 回と、連続が n 回に達するたび（`fail` の説明）。

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
# 案内の種類（`fail` の戻り値）。
FIRST = "first"
STREAK = "streak"


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


def fail(state_dir: str, session: str, agent: str, n: int) -> tuple[str, int]:
    """失敗か拒否を 1 回数え、案内を出す回なら（種類, 今の連続の長さ）を返す。出さない回は空の種類。

    案内は 2 つ。どちらも独立に決める。
    - 最初の 1 回（`FIRST`）: このセッションとエージェントで最初の失敗。その後は出さない。
      成功で連続が戻っても、最初の 1 回の印は残す
    - 連続（`STREAK`）: 連続の長さが n の倍数になった回（n・2n・3n 回目）
    2 つが同じ回に重なれば `STREAK` だけ出す。回数を記録に書けなければ出さない
    （覚えられないまま出すと、失敗のたびに同じ案内が並ぶ）。n が 0 以下（機能を使わない）、
    置き場が無い（`--state ""`）、セッションが空のときは数えない。
    """
    if n <= 0 or not state_dir or not session:
        return "", 0
    try:
        path = _path(state_dir, session, agent)
        data = fsio.read_dict(path) or {}
        count = int(data.get("count") or 0) + 1
        if count % n == 0:
            kind = STREAK
        elif data.get("first") is not True:
            kind = FIRST
        else:
            kind = ""
        if fsio.write_json_atomic(path, {"count": count, "first": True}):
            return "", 0
        return kind, count
    except Exception:  # noqa: BLE001  数えの失敗で処理を止めない
        return "", 0


def reset(state_dir: str, session: str, agent: str, n: int) -> None:
    """成功が入った。連続を 0 に戻す。最初の 1 回を案内した印は残す。"""
    if n <= 0 or not state_dir or not session:
        return
    try:
        path = _path(state_dir, session, agent)
        if (fsio.read_dict(path) or {}).get("count"):
            fsio.write_json_atomic(path, {"first": True})
    except Exception:  # noqa: BLE001  戻せなくても処理は止めない
        return
