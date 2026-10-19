"""フェーズのマーカーと、親・子ごとの記録。承認済みチケットの `phases/<親>/` を読み書きする。

`phases/<親>/<N>.<種類>` に、依頼・レビュー済み・省略・通知済みのマーカーを置く。
レビューが済むまで止める判定（phase.py）はこれを見る。中身は JSON 1 つで、いつ誰が置いたかが入る。
親ごとのマーカー（ready・close-early・closed）、子ごとの記録（実績のリスクと定性項目の判定）も
同じ置き場にある。approval から分けた。

ユーザが受け入れた未解決スレッドは、ホスト側で返信を残して解決する
（`review_decide`・`review_close`）。

ファイル名とマーカーの JSON の形は sh と拡張も読む。値と形を変えない。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from ..infra import fsio

PHASES_DIR = "phases"

# フェーズのマーカーの種類。
MARK_REQUESTED = "requested"
MARK_REVIEWED = "reviewed"
MARK_SKIPPED = "skipped"
# pending は「終わったと 1 度伝えた」のマーカー。同じ文を呼び出しごとに繰り返さないため。
MARK_PENDING = "pending"
MARKS = (MARK_REQUESTED, MARK_REVIEWED, MARK_SKIPPED, MARK_PENDING)


def mark_path(approved_dir: str, parent: str, phase: int, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{phase}.{kind}")


def read_mark(approved_dir: str, parent: str, phase: int, kind: str) -> dict | None:
    return fsio.read_dict(mark_path(approved_dir, parent, phase, kind))


def write_mark(approved_dir: str, parent: str, phase: int, kind: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    return _write(
        mark_path(approved_dir, parent, phase, kind), json.dumps(payload, ensure_ascii=False)
    )


@dataclass
class Cleared:
    """`clear_marks` の答え。`kinds` は消せた種類（書き込みを溜める段では消す種類）。

    `failed` は reviewed を消せなかった理由（空でなければ呼び手は止める）。`warnings` は
    ほかの種類を消せなかった知らせ（残っていて判定に使われる）。
    """

    kinds: list[str]
    failed: str = ""
    warnings: list[str] = field(default_factory=list)


# 消す順。reviewed を先に消す。消せなければ止めるので、ほかの種類に手を付ける前に決める。
_CLEAR_ORDER = (MARK_REVIEWED,) + tuple(k for k in MARKS if k != MARK_REVIEWED)


def clear_marks(
    approved_dir: str,
    parent: str,
    phase: int,
    announce=None,
) -> Cleared:
    """このフェーズのマーカーを全部消す（同じ番号に子を足した）。

    reviewed を消せなければ止める（`failed`）。残ればフェーズは済んだまま読まれ、足した子を
    見ないまま先へ進めてしまう（判定を厳しくする向き）。ほかの種類は
    消せなくても止めず、残っていて判定に使われると言う（`warnings`）。

    書き込みを溜める段（承認の plan）では、消す書き込みに同じ扱いをつけ、
    見せる行は Writer(FS) が書けた種類で書く（`fsio.Call`）。
    `announce` は消せた種類から見せる行を作る関数。
    """
    stage = fsio.current_stage()
    present = [k for k in _CLEAR_ORDER if fsio.lexists(mark_path(approved_dir, parent, phase, k))]
    if stage is not None:
        group = stage.new_group()
        for kind in present:
            path = mark_path(approved_dir, parent, phase, kind)
            with fsio.policy(group=group, tag=kind, **_clear_policy(path, phase, kind)):
                fsio.unlink(path)
        kinds = [k for k in MARKS if k in present]

        def run(done: set[str]) -> list[str]:
            cleared = [k for k in MARKS if k in done]
            return announce(cleared) if announce is not None and cleared else []

        planned = announce(kinds) if announce is not None and kinds else []
        stage.items.append(fsio.Call(run, group, planned))
        return Cleared(kinds)
    cleared: list[str] = []
    warnings: list[str] = []
    failed = ""
    for kind in present:
        path = mark_path(approved_dir, parent, phase, kind)
        reason = fsio.unlink(path)
        if not reason:
            cleared.append(kind)
            continue
        text = fsio.failure_text(fsio.Policy(**_clear_policy(path, phase, kind)), reason)
        if kind == MARK_REVIEWED:
            failed = text
            break
        warnings.append(text)
    cleared = [k for k in MARKS if k in cleared]
    return Cleared(cleared, failed, warnings)


def _clear_policy(path: str, phase: int, kind: str) -> dict:
    if kind == MARK_REVIEWED:
        return {
            "on_fail": fsio.FAIL_STOP,
            "prefix": "",
            "message": (
                f"フェーズ {phase} のレビュー済みのマーカー {path} を削除できません（{{reason}}）。"
                "残るとフェーズは済んだまま読まれるので、ここで止める。ユーザがマーカーを削除します。"
            ),
        }
    return {
        "on_fail": fsio.FAIL_WARN,
        "prefix": "",
        "message": (
            f"フェーズ {phase} のマーカー {path} を削除できませんでした（{{reason}}）。"
            f"{kind} は残っていて有効なまま"
        ),
    }


# 親ごとのマーカー。フェーズの番号に付かないもの。
#   ready.json   Draft を外した（外してよいと確かめた）。「マージに進んでよい」の合図
#   close-early.json  ユーザが「キリの良いところまでやった」と早めに閉じた。残りは別の issue へ
#   closed.json  親を閉じた（`ticket finish <親>`）。どのフェーズをどこで見たかを残す
#
# closed.json が要るのは、提案（wip/）が統合先に取り込む前に消えるから。マージリクエストを
# 作らない進め方（全フェーズが `review: none`）では、親を閉じた事実の残る先がここしか無い。
PARENT_MARK_READY = "ready"
PARENT_MARK_CLOSE_EARLY = "close-early"
PARENT_MARK_CLOSED = "closed"


def parent_mark_path(approved_dir: str, parent: str, name: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{name}.json")


def read_parent_mark(approved_dir: str, parent: str, name: str) -> dict | None:
    return fsio.read_dict(parent_mark_path(approved_dir, parent, name))


def write_parent_mark(approved_dir: str, parent: str, name: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    return _write(
        parent_mark_path(approved_dir, parent, name),
        json.dumps(payload, ensure_ascii=False, indent=1),
    )


# 子ごとの記録。実績のリスク（閉じるときに数えた点）と、定性項目の判定。
#   phases/<親>/<子>.risk.json   {points, level, hits, unmeasured, head, at}
#   phases/<親>/<子>.judge.json  {<項目>: {hit, reason, head, at}}
CHILD_RECORD_RISK = "risk"
CHILD_RECORD_JUDGE = "judge"


def child_record_path(approved_dir: str, parent: str, child: str, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{child}.{kind}.json")


def read_child_record(approved_dir: str, parent: str, child: str, kind: str) -> dict | None:
    return fsio.read_dict(child_record_path(approved_dir, parent, child, kind))


def write_child_record(approved_dir: str, parent: str, child: str, kind: str, data: dict) -> str:
    return _write(
        child_record_path(approved_dir, parent, child, kind),
        json.dumps(data, ensure_ascii=False, indent=1),
    )


# 受け入れ台帳。ホストで未解決のまま受け入れた指摘を、版つきで親ごとに持つ
# （フェーズを開き直しても残る）。
#   phases/<親>/accepted.json  {"version": 1, "entries": [{key, version, last_comment_id,
#                              accepted_by, phase, op, at, account}]}
# accepted_by: manual（手で動かした子の確定）・decide・close-early
# 実際に書くのは今は manual だけ。decide・close-early はホストのスレッドを解決して受け入れ、
# 台帳には書かない。LEDGER_BY の後ろ 2 つは、書く側の無い予約の値
LEDGER_NAME = "accepted"
LEDGER_BY = ("manual", "decide", "close-early")


def read_ledger(approved_dir: str, parent: str) -> tuple[list[dict], str]:
    """台帳の項目と、読めなかった理由（読めれば空）。無ければ空の台帳。"""
    path = parent_mark_path(approved_dir, parent, LEDGER_NAME)
    if not os.path.exists(path):
        return [], ""
    data = fsio.read_dict(path)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return [], f"受け入れ台帳 {path} を読めない"
    return entries, ""


def add_ledger(approved_dir: str, parent: str, new: list[dict]) -> str:
    """台帳に項目を足す（同じ鍵は置き換える）。書けなければ理由。読めない台帳には書かない。"""
    entries, problem = read_ledger(approved_dir, parent)
    if problem:
        return problem
    keys = {str(e.get("key")) for e in new}
    kept = [e for e in entries if str(e.get("key")) not in keys]
    stamp = now()
    merged = kept + [{**e, "at": e.get("at") or stamp} for e in new]
    return write_parent_mark(approved_dir, parent, LEDGER_NAME, {"version": 1, "entries": merged})


def marks(approved_dir: str, parent: str, phase: int) -> dict[str, dict]:
    found = {}
    for kind in MARKS:
        data = read_mark(approved_dir, parent, phase, kind)
        if data is not None:
            found[kind] = data
    return found


def now() -> str:
    return fsio.stamp()


def _write(path: str, text: str) -> str:
    with fsio.policy(message="保存できませんでした ({reason})"):
        failed = fsio.write_text(path, text)
    return f"保存できませんでした ({failed})" if failed else ""


def write_ticket(path: str, text: str) -> str:
    """承認済みチケットを書き直す。返し方は `_write` と同じ。

    途中で中断されても前の中身か新しい中身を残す。一時ファイルに書き切って fsync してから
    差し替える（`fsio.write_text_durable`）。改行は `_write` と同じ。
    """
    with fsio.policy(message="保存できませんでした ({reason})"):
        failed = fsio.write_text_durable(path, text)
    return f"保存できませんでした ({failed})" if failed else ""
