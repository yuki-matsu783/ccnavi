"""サブエージェントの終了結果の記録。SubagentStop が、終わり方を 5 つに分けて残す。

| 結果 | 意味 |
|---|---|
| `ok` | 範囲外の変更が無い |
| `fixed` | 差し戻した後に直った |
| `outside` | 範囲外の変更が残っている |
| `unchecked` | 読めない・内部エラーで検査できなかった |
| `unattributed` | 担当の子が分からない（親のツリーから起動された） |

記録は `subagent-<セッション>-<agent_id>.outcome`（JSON）。差し戻した記録（`.bounced`）とは別に、
こちらは「終わったときにどうだったか」を残す。差し戻して続けさせた回は `final: false` で書き、次の
SubagentStop（直ったか、まだ残っているか）で結果を確定する。

親への知らせは、確定した結果を 1 度だけ。出力に書き終えてから「通知済み」の印を付けるので、書く前に
止まったら次の機会に同じ結果IDで出し直す。
Stop が促すのは `outside` と `unchecked` だけ（`unattributed` と正常な終了では促さない）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from ..infra import fsio

OK = "ok"
FIXED = "fixed"
OUTSIDE = "outside"
UNCHECKED = "unchecked"
UNATTRIBUTED = "unattributed"
OUTCOMES = (OK, FIXED, OUTSIDE, UNCHECKED, UNATTRIBUTED)

# Stop が促す結果。
NUDGE = (OUTSIDE, UNCHECKED)

SUFFIX = ".outcome"


@dataclass
class Outcome:
    id: str
    session: str
    agent_id: str
    child: str
    outcome: str
    bounces: int
    paths: list[str]
    final: bool
    notified: bool
    nudged: bool
    at: float
    file: str = ""


def path(state_dir: str, session: str, who: str) -> str:
    where = fsio.safe_name(session) or "unknown"
    safe = fsio.safe_name(who) or "unknown"
    return os.path.join(state_dir, f"subagent-{where}-{safe}{SUFFIX}")


def write(
    state_dir: str,
    session: str,
    who: str,
    outcome: str,
    child: str = "",
    bounces: int = 0,
    paths: list[str] | None = None,
    final: bool = True,
) -> str:
    """結果を書く。書けなければ理由（呼び手は言うだけで止まらない）。"""
    if not state_dir:
        return ""
    if outcome not in OUTCOMES:
        raise ValueError(f"知らない結果: {outcome}")
    data = {
        "id": f"{session}:{who}:{int(time.time() * 1000)}",
        "session": session,
        "agent_id": who,
        "child": child,
        "outcome": outcome,
        "bounces": bounces,
        "paths": list(paths or []),
        "final": final,
        "notified": False,
        "nudged": False,
        "at": time.time(),
    }
    return fsio.write_json_atomic(path(state_dir, session, who), data)


def read(file: str, session: str) -> Outcome | None:
    """このセッションの結果を読む。違うセッション・壊れたものは None。"""
    text = fsio.read_text(file, errors="replace")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("session") != session:
        return None
    outcome = str(data.get("outcome") or "")
    if outcome not in OUTCOMES:
        return None
    try:
        return Outcome(
            id=str(data.get("id") or ""),
            session=session,
            agent_id=str(data.get("agent_id") or ""),
            child=str(data.get("child") or ""),
            outcome=outcome,
            bounces=int(data.get("bounces") or 0),
            paths=[str(p) for p in data.get("paths") or []],
            final=bool(data.get("final", True)),
            notified=bool(data.get("notified")),
            nudged=bool(data.get("nudged")),
            at=float(data.get("at") or 0),
            file=file,
        )
    except (ValueError, TypeError):
        # 手で直した・版が違うなどで型が合わない記録は、読めないものとして破棄する
        # （hook を止めない）
        return None


def of(state_dir: str, session: str, who: str) -> Outcome | None:
    """この相手の結果。無ければ None。"""
    if not state_dir or not who:
        return None
    return read(path(state_dir, session, who), session)


def all_of(state_dir: str, session: str) -> list[Outcome]:
    """このセッションの結果（確定したものだけ。古い順）。"""
    if not state_dir:
        return []
    try:
        names = sorted(os.listdir(state_dir))
    except OSError:
        return []
    found = []
    for name in names:
        if not (name.startswith("subagent-") and name.endswith(SUFFIX)):
            continue
        one = read(os.path.join(state_dir, name), session)
        if one is not None and one.final:
            found.append(one)
    return sorted(found, key=lambda o: o.at)


def _mark(one: Outcome, field: str) -> str:
    """結果の印（notified / nudged）を付ける。保存できなければ理由。"""
    data = fsio.read_dict(one.file)
    if not isinstance(data, dict) or data.get("id") != one.id:
        # 読み直したら別の結果になっていた（新しい終了で書き直された）。付けない。
        return ""
    data[field] = True
    return fsio.write_json_atomic(one.file, data)


def line(one: Outcome) -> str:
    """親に言う 1 行。"""
    who = one.child or "（担当の子が分からない）"
    shown = "、".join(one.paths[:5]) + (
        f" ほか {len(one.paths) - 5} 件" if len(one.paths) > 5 else ""
    )
    if one.outcome == OUTSIDE:
        if one.bounces == 0:
            return (
                f"サブエージェント {one.agent_id} は、子 {who} の範囲外の変更を"
                "残したまま終わっている"
                f"（差し戻していない。dry-run か、差し戻しの対象外）: {shown}"
            )
        return (
            f"サブエージェント {one.agent_id} は、子 {who} の範囲外の変更を"
            f"差し戻された（{one.bounces} 回）後も残したまま終わっている: {shown}"
        )
    if one.outcome == UNCHECKED:
        target = f"子 {one.child}" if one.child else "子"
        return (
            f"サブエージェント {one.agent_id} の終わりに、{target}の範囲を確かめられていない"
            "（ワークツリーを読めないか、ccnavi の内部エラー）"
        )
    return (
        f"サブエージェント {one.agent_id} は親のツリーから起動されたため、担当の子が分からない。"
        f"範囲外の変更が見つかっているが差し戻していない: {shown}。"
        "どの子に残っているかを確かめる（最終的には finish の範囲検査が止める）"
    )


def notices(state_dir: str, session: str) -> tuple[str, list[Outcome]]:
    """まだ言っていない確定した結果を、親に言う文にする。`unattributed` はここが担当する。

    `outside` と `unchecked` は従来の経路（差し戻しを無視した知らせと、確かめられなかった知らせ）が
    親に届けるので、ここで言うのは `unattributed` だけ。書き終えたあとに `confirm` で印を付ける。
    """
    todo = [o for o in all_of(state_dir, session) if o.outcome == UNATTRIBUTED and not o.notified]
    if not todo:
        return "", []
    text = "\n\n".join(f"[ccnavi] SUBAGENT_UNATTRIBUTED: {line(o)}" for o in todo)
    return text, todo


def confirm(state_dir: str, done: list[Outcome]) -> str:
    """書き終えた知らせの印を付ける。付けられなければ理由（次の機会に出し直すだけ）。"""
    failed = ""
    for one in done:
        failed = _mark(one, "notified") or failed
    return failed


def mark_nudged(state_dir: str, session: str, who: str) -> None:
    """この相手の結果を「促した」ことにする（別の経路で同じ内容を親に言ったとき）。"""
    one = of(state_dir, session, who)
    if one is not None and one.final and not one.nudged:
        _mark(one, "nudged")


def stop_pending(state_dir: str, session: str) -> list[Outcome]:
    """Stop が促す結果（`outside`・`unchecked`。まだ促していないもの）。"""
    return [o for o in all_of(state_dir, session) if o.outcome in NUDGE and not o.nudged]


def stop_nudge(state_dir: str, session: str) -> str:
    """Stop を止めて渡す文。促すものが無ければ空。促したら印を付ける（同じ結果は 1 回だけ）。"""
    todo = stop_pending(state_dir, session)
    if not todo:
        return ""
    # 印を付けられないものは促さない（付けないと毎回止めてしまう）。付けられたものだけ促す。
    todo = [o for o in todo if not _mark(o, "nudged")]
    if not todo:
        return ""
    lines = [f"  - {line(o)}" for o in todo]
    return "\n".join(
        [
            "[ccnavi] SUBAGENT_OUTCOME: サブエージェントが範囲外の変更を残したか、"
            "範囲を確かめられないまま終わっています。",
            *lines,
            "合流（finish）の前に、その子のワークツリーの範囲外の変更を確かめて直すか、"
            "ユーザに伝えてください。この案内は同じ結果では 1 回だけです。",
        ]
    )
