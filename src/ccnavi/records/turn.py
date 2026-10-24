"""いま、エージェントがターンの途中にいるか。

ターン中は、ユーザが `deny` の場所や ccnavi の設定ファイルを直しても、次のツール呼び出しの
実行後チェックが「この呼び出しが起こした変更」として戻すことがある。ユーザの手による変更を
受け入れるのは次の発言（`selfguard.accept`、`post.at_prompt`）なので、ターンの間の編集は
受け入れられない。VS Code 拡張は、この印を `--explain --json` の `turns` で読んで、設定ファイルを
保存する前にターン中であることを知らせる。

印はセッションごとに 1 ファイル（state の置き場の `turn-<セッション>.json`）で、次のように動く。

- ユーザの発言とバックグラウンド作業の完了通知（ターンを始めるもの）で置く（`begin`）
- ツール呼び出しの前後で更新時刻を新しくする（`touch`）。中身は書き換えない
- メインエージェントの Stop と、ユーザの中断で消す（`end`）
- 起動と再開で消す（`clear`）。新しいプロセスで、前のターンは生きていない

セッションごとに別のファイルなので、並行するセッションは互いの印を書き換えない。読む側は
全部のファイルを数え、更新時刻がいまから `STALE_SECONDS` 以上前のものは、ターンの途中で
プロセスが落ちた・中断で Stop が来なかった印として無視する。読む側は消さない（読むだけで
書かない）。残った古いファイルは、セッションの記録と同じく後始末（`prune`）が消す。

`touch` は存在する印の更新時刻だけを動かし、無い印は作らない。Stop の直後に並行して走った
サブエージェントのツール呼び出しが、消した印を作り直さないため。

どの操作も、失敗してもツールの結果と発言の流れを変えない。書けない・読めないときに
印が無い（ターン中ではない）側に倒れるのは、拡張の警告が出ないだけで、保護の動きは変わらないため。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from ..infra import fsio

# 更新が無いまま、これだけ経った印は古い。ツール 1 回の長さ（ビルドなど）より長く取る。
# 短くすると、長いツール呼び出しの最中に印が古くなり、いちばん危ない時間に警告が消える。
STALE_SECONDS = 30 * 60

PREFIX = "turn-"
SUFFIX = ".json"
_NAME = re.compile(r"^turn-(?P<s>.+)\.json$")


@dataclass(frozen=True)
class Active:
    """ターン中のセッション 1 つ。"""

    session: str
    # since はターンが始まった時刻（UNIX 秒）。
    since: float
    # seen は最後にツールの呼び出しがあった時刻（UNIX 秒）。
    seen: float


def _path(state_dir: str, session: str) -> str:
    return os.path.join(state_dir, f"{PREFIX}{fsio.safe_name(session)}{SUFFIX}")


def begin(state_dir: str, session: str, now: float | None = None) -> None:
    """ターンの始まりを書く。書けなくても何も出さない。"""
    if not state_dir or not session:
        return
    stamp = time.time() if now is None else now
    try:
        fsio.write_json_atomic(_path(state_dir, session), {"session": session, "since": stamp})
    except Exception:  # noqa: BLE001  印の失敗で発言の流れを変えない
        return


def touch(state_dir: str, session: str, now: float | None = None) -> None:
    """あるときだけ、更新時刻を新しくする。無ければ作らない。"""
    if not state_dir or not session:
        return
    stamp = time.time() if now is None else now
    try:
        os.utime(_path(state_dir, session), (stamp, stamp))
    except OSError:
        return


def end(state_dir: str, session: str) -> None:
    """ターンの終わり（Stop・中断）。無くても、消せなくても何も出さない。"""
    if state_dir and session:
        fsio.remove(_path(state_dir, session))


def clear(state_dir: str, session: str) -> None:
    """起動と再開で消す。"""
    end(state_dir, session)


def active(state_dir: str, now: float | None = None) -> list[Active]:
    """いまターン中のセッション。更新時刻の古い印と、読めない印は数えない。

    置き場が無いときは空。読むだけで、何も書かない・消さない。
    """
    if not state_dir:
        return []
    stamp = time.time() if now is None else now
    try:
        names = sorted(os.listdir(state_dir))
    except OSError:
        return []
    found: list[Active] = []
    for name in names:
        match = _NAME.match(name)
        if match is None:
            continue
        path = os.path.join(state_dir, name)
        try:
            seen = os.stat(path).st_mtime
        except OSError:
            continue
        if stamp - seen >= STALE_SECONDS:
            continue
        data, failed = fsio.read_json(path)
        if failed is not None or not isinstance(data, dict):
            # 読めない印は、ターン中の印とも言い切れない。数えない。
            continue
        since = data.get("since")
        session = data.get("session")
        if not isinstance(since, (int, float)) or isinstance(since, bool):
            continue
        found.append(
            Active(
                session=session if isinstance(session, str) and session else match.group("s"),
                since=float(since),
                seen=seen,
            )
        )
    return found
