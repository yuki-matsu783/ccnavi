"""ユーザが最後に発言した時刻。

ターン分離の関門（`hook/turngate.py`）が、「前の段のあとにユーザの発言が 1 回あったか」を
決めるために読む。ターンの印（`records/turn.py`）は Stop で消えるので、ターンをまたぐ事実は
持てない。ここはその別の記録で、ユーザの発言（`hookio.is_user_prompt`）の時刻だけを書く。
バックグラウンド作業の完了通知や、ccnavi が起こす指示は書かない。

記録はセッションごとに 1 ファイル（state の置き場の `userprompt-<セッション>.json`）。
起動と再開で消す（新しいプロセスで、前の発言は今のターンと関係しない）。compact と clear では
消さない。ターンの途中で compact が走っても、発言の事実は変わらないため。後始末（`prune`）は
ほかのセッションの記録と同じ。

読めない・無いときは `None`（発言が無いものとして読む）。関門はそれを「まだ発言が無い」と読み、
閉じる側に倒す。次のユーザの発言が書き直すので、閉じたままにはならない。書けないときは何も出さない。
"""

from __future__ import annotations

import os
import time

from ..infra import fsio

PREFIX = "userprompt-"
SUFFIX = ".json"


def _path(state_dir: str, session: str) -> str:
    return os.path.join(state_dir, f"{PREFIX}{fsio.safe_name(session)}{SUFFIX}")


def note(state_dir: str, session: str, now: float | None = None) -> None:
    """ユーザの発言の時刻を書く。書けなくても何も出さない。"""
    if not state_dir or not session:
        return
    stamp = time.time() if now is None else now
    try:
        fsio.write_json_atomic(_path(state_dir, session), {"session": session, "at": stamp})
    except Exception:  # noqa: BLE001  記録の失敗で発言の流れを変えない
        return


def last(state_dir: str, session: str) -> float | None:
    """最後のユーザの発言の時刻（UNIX 秒）。無い・読めない・形が違うときは None。"""
    if not state_dir or not session:
        return None
    data, failed = fsio.read_json(_path(state_dir, session))
    if failed is not None or not isinstance(data, dict):
        return None
    at = data.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    return float(at)


def clear(state_dir: str, session: str) -> None:
    """記録を消す。起動と再開で呼ぶ。無くても、消せなくても何も出さない。"""
    if state_dir and session:
        fsio.remove(_path(state_dir, session))
