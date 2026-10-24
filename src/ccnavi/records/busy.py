"""ターンの終わりに、エージェントが動かしている作業が残っていたか。

ユーザの発言で変更を受け入れる処理（`selfguard.accept`、`post.at_prompt`）が、受け入れてよいかを
決めるために読む。ターンの終わりのあとにワークツリーを変えられるのは、ユーザの手と、エージェントが
動かしたもの（バックグラウンドのコマンド、予約した繰り返し）の 2 つだけ。後者が残っているなら、
次の発言までの変更はユーザの手によるものと言い切れないので、受け入れない。

印は Stop の payload（`background_tasks`・`session_crons`）から、メインエージェントの Stop で
書き換える。残っていなければ消す。起動と再開でも消す（新しいプロセスで、前の作業は生きていない）。

読めない・書けないときは「残っている」として扱う。受け入れない側が、これまでの戻す動きと同じで、
覚えられないまま受け入れて保護を外すより安全なため。ただし置き場が無い（`--state ""`）、セッションが
空のときは、そもそも受け入れる側のバックアップが無いので、どちらでも結果は変わらない。

記録は state の置き場の `busy-<セッション>.json`。
"""

from __future__ import annotations

import os

from ..infra import fsio


def _path(state_dir: str, session: str) -> str:
    return os.path.join(state_dir, f"busy-{fsio.safe_name(session)}.json")


def record(state_dir: str, session: str, busy: bool) -> None:
    """ターンの終わりの状態を書く。残っていれば印を置き、残っていなければ消す。"""
    if not state_dir or not session:
        return
    if busy:
        fsio.write_json_atomic(_path(state_dir, session), {"busy": True})
    else:
        fsio.remove(_path(state_dir, session))


def clear(state_dir: str, session: str) -> None:
    """印を消す。起動と再開で呼ぶ。"""
    if state_dir and session:
        fsio.remove(_path(state_dir, session))


def is_busy(state_dir: str, session: str) -> bool:
    """前のターンの終わりに、動かしている作業が残っていたか。"""
    if not state_dir or not session:
        return False
    path = _path(state_dir, session)
    if not os.path.exists(path):
        return False
    data, failed = fsio.read_json(path)
    if failed is not None:
        # 在るのに読めない。残っているものとして扱う。
        return True
    return not isinstance(data, dict) or data.get("busy") is not False
