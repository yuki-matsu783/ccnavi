"""フェーズのマーカーと、親・子ごとの記録。承認済みチケットの `phases/<親>/` を読み書きする。

`phases/<親>/<N>.<種類>` に、依頼・レビュー済み・省略・通知済みのマーカーを置く。
レビューが済むまで止める判定（phase.py）はこれを見る。中身は JSON 1 つで、いつ誰が置いたかが入る。
親ごとのマーカー（ready・close-early・closed）、子ごとの記録（実績のリスクと定性項目の判定）、
ユーザが受け入れたスレッドの記録も同じ置き場にある。approval から分けた。

ファイル名とマーカーの JSON の形は sh と拡張も読む。値と形を変えない。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from ..infra import fsio
from . import history, workflow
from . import ticket as ticket_mod

PHASES_DIR = "phases"

# フェーズのマーカーの種類。
MARK_REQUESTED = "requested"
MARK_REVIEWED = "reviewed"
MARK_SKIPPED = "skipped"
# pending は「終わったと 1 度伝えた」のマーカー。同じ文を呼び出しごとに繰り返さないため。
MARK_PENDING = "pending"
MARKS = (MARK_REQUESTED, MARK_REVIEWED, MARK_SKIPPED, MARK_PENDING)
# 全体計画の待ち方を固定するファイル（`phases/<親>/workflow.yml`）。
WORKFLOW_FILE = "workflow.yml"


def mark_path(approved_dir: str, parent: str, phase: int, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{phase}.{kind}")


def read_mark(approved_dir: str, parent: str, phase: int, kind: str) -> dict | None:
    return fsio.read_dict(mark_path(approved_dir, parent, phase, kind))


def write_mark(approved_dir: str, parent: str, phase: int, kind: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    failed = _write(
        mark_path(approved_dir, parent, phase, kind), json.dumps(payload, ensure_ascii=False)
    )
    if not failed:
        history.note(
            approved_dir, parent, history.KIND_PHASE_MARK, None, None, phase=phase, mark=kind
        )
    return failed


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
    """このフェーズのマーカーを全部消す（同じ番号に子を足した。REQ-TKT-21）。

    reviewed を消せなければ止める（`failed`）。残ればフェーズは済んだまま読まれ、足した子を
    見ないまま先へ進めてしまう（判定を厳しくする向き）。ほかの種類は
    消せなくても止めず、残っていて判定に使われると言う（`warnings`）。
    履歴（phase-reopened の `cleared`）には実際に消せた種類だけを書く。

    書き込みを溜める段（承認の plan）では、消す書き込みに同じ扱いをつけ、
    履歴と見せる行は Writer(FS) が書けた種類で書く（`fsio.Call`）。
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
        if kinds:
            # 見え方（Changes）には全部消えた後の履歴を載せる。ディスクへは Call が書く。
            with fsio.view_only():
                _note_reopened(approved_dir, parent, phase, kinds)

        # 履歴の時刻と経路は並べたときのもの（書く時に時計を読み直すと、Changes と食い違う）。
        at, via = fsio.stamp(), history.via()

        def run(done: set[str]) -> list[str]:
            cleared = [k for k in MARKS if k in done]
            if cleared:
                before = history.via()
                history.set_via(via)
                try:
                    with fsio.clock(at):
                        _note_reopened(approved_dir, parent, phase, cleared)
                finally:
                    history.set_via(before)
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
    if cleared:
        _note_reopened(approved_dir, parent, phase, cleared)
    return Cleared(cleared, failed, warnings)


def _clear_policy(path: str, phase: int, kind: str) -> dict:
    if kind == MARK_REVIEWED:
        return {
            "on_fail": fsio.FAIL_STOP,
            "prefix": "",
            "message": (
                f"フェーズ {phase} のレビュー済みのマーカー {path} を消せない（{{reason}}）。"
                "残るとフェーズは済んだまま読まれるので、ここで止める。ユーザがマーカーを消す"
            ),
        }
    return {
        "on_fail": fsio.FAIL_WARN,
        "prefix": "",
        "message": (
            f"フェーズ {phase} のマーカー {path} を消せなかった（{{reason}}）。"
            f"{kind} は残っていて効く"
        ),
    }


def _note_reopened(approved_dir: str, parent: str, phase: int, cleared: list[str]) -> None:
    history.note(
        approved_dir,
        parent,
        history.KIND_PHASE_REOPENED,
        None,
        None,
        phase=phase,
        cleared=cleared,
    )


# 親ごとのマーカー。フェーズの番号に付かないもの。
#   ready.json   Draft を外した（外してよいと確かめた）。「マージに進んでよい」の合図
#   close-early.json  ユーザが「キリの良いところまでやった」と早めに閉じた。残りは別の issue へ
#   closed.json  親を閉じた（`ticket finish <親>`）。どのフェーズをどこで見たかを残す
#
# closed.json が要るのは、提案（wip/）が統合先に取り込む前に消えるから。マージリクエストを
# 作らない進め方（全フェーズが `review: chat`）では、親を閉じた事実の残る先がここしか無い。
PARENT_MARK_READY = "ready"
PARENT_MARK_CLOSE_EARLY = "close-early"
PARENT_MARK_CLOSED = "closed"


def parent_mark_path(approved_dir: str, parent: str, name: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{name}.json")


def read_parent_mark(approved_dir: str, parent: str, name: str) -> dict | None:
    return fsio.read_dict(parent_mark_path(approved_dir, parent, name))


# 親のマーカーのうち、状態の履歴（history）に残すもの。親の閉じ方と Draft を外したこと。上書きの記録
# （configsync）は状態ではないので残さない。
PARENT_MARKS_IN_HISTORY = (PARENT_MARK_READY, PARENT_MARK_CLOSE_EARLY, PARENT_MARK_CLOSED)


def write_parent_mark(approved_dir: str, parent: str, name: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    failed = _write(
        parent_mark_path(approved_dir, parent, name),
        json.dumps(payload, ensure_ascii=False, indent=1),
    )
    if not failed and name in PARENT_MARKS_IN_HISTORY:
        history.note(approved_dir, parent, history.KIND_PARENT_MARK, None, None, mark=name)
    return failed


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


# ユーザが受け入れたスレッドの記録。フェーズのマーカーとは別の場所に、親ごとに 1 つ置く。
ACCEPTED_FILE = "accepted.json"


def accepted_path(approved_dir: str, parent: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, ACCEPTED_FILE)


def accepted_threads(
    approved_dir: str,
    parent: str,
    phase: int | None = None,
    owner: ticket_mod.Ticket | None = None,
) -> set[str]:
    """この親で、ユーザが「未解決のまま進める」と受け入れたスレッドの識別。

    `phase` を渡すと、その番号のレビューで受け入れ済みと数えてよいものだけを返す。
    受け入れはそのフェーズと、それを待つ番号（コピーした待ち方の `waits`）にだけ当てはまる（設計
    9.8）。並行した別の枝のレビューには当てはまらない。番号を持たない受け入れ（`close-early`）は
    親全体に当てはまる。
    """
    data = fsio.read_dict(accepted_path(approved_dir, parent))
    if not data:
        return set()
    threads = {str(x) for x in data.get("threads") or [] if str(x)}
    if phase is None or owner is None:
        return threads
    at = data.get("phases") if isinstance(data.get("phases"), dict) else {}
    reach = {phase, *workflow.waits_of(owner, phase, None)}
    kept = set()
    for thread in threads:
        where = at.get(thread)
        scoped = isinstance(where, list)
        if not scoped or any(isinstance(n, int) and n in reach for n in where):
            kept.add(thread)
    return kept


def remember_accepted(
    approved_dir: str, parent: str, threads: list[str], phase: int | None = None
) -> str:
    """受け入れたスレッドを記録に足す。失敗したら、その説明を返す。

    フェーズのマーカーとは別の場所に置く。マーカーは 2 つの理由で消える。同じ番号のマーカーは
    `confirm` が通るたびに上書きされ、その番号に子が足されると `clear_marks` が
    丸ごと消す。どちらでも受け入れの記録が消え、ユーザがもう一度同じスレッドを
    受け入れることになる。ユーザが 1 度言った「これは承知で進める」は、
    取り消されるまで残す。
    """
    if not threads:
        return ""
    path = accepted_path(approved_dir, parent)
    data = fsio.read_dict(path) or {}
    at = dict(data.get("phases")) if isinstance(data.get("phases"), dict) else {}
    added = {str(t) for t in threads if str(t)}
    threads_before = accepted_threads(approved_dir, parent)
    keep = sorted(threads_before | added)
    for thread in added:
        if phase is None:
            # 番号を持たない受け入れ（`close-early`）は親全体に当てはまる。
            at.pop(thread, None)
        elif thread in at or thread not in threads_before:
            # 受け入れた番号を足していく。別の枝で受け入れ直した分も数に入れる。
            known = [n for n in at.get(thread, []) if isinstance(n, int)]
            at[thread] = sorted(set(known) | {phase})
    # 読んで、足して、書き戻す形。同じファイルの `_write_known` と同じく、
    # 途中を見せない書き方で置く。
    body = {"threads": keep, "phases": dict(sorted(at.items())), "at": now()}
    failed = fsio.write_json_atomic(path, body, indent=1)
    return f"{path} ({failed})" if failed else ""


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
    with fsio.policy(message="書けない ({reason})"):
        failed = fsio.write_text(path, text)
    return f"書けない ({failed})" if failed else ""
