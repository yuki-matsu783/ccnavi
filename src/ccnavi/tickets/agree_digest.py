"""承認の改版の書き込み（承認済みチケットの計画の差し替え）。

承認で書く本文（改版の写し）を組む。agree から分けた。agree を読まない。
"""

from __future__ import annotations

from . import (
    approval,
    approval_marks,
    history,
    ticket_model,
)
from . import ticket as ticket_mod


def revise_copy(
    approved_dir: str,
    current: ticket_model.Ticket,
    revised: ticket_model.Ticket,
    feedback_planned: bool,
) -> str:
    """承認済みチケットの計画を差し替える。

    範囲と本文はそのまま（差し替えるのは計画と `phases:`。`revised_front`）。改版の時刻は
    チケットに書かない（状態の履歴の `revised` に残る。フィードバック計画の改版は
    `feedback: true` を添える）。待ち方は書かない（承認済みチケットの
    計画から都度計算する）。
    """
    current.raw = revised_front(current, revised)
    failed = approval_marks.write_ticket(
        approval.copy_path(approved_dir, current.ticket), ticket_mod.render(current)
    )
    if failed:
        return failed
    history.note(
        approved_dir,
        current.ticket,
        history.KIND_REVISED,
        ticket_model.DOING,
        ticket_model.DOING,
        feedback=True if feedback_planned else None,
    )
    return ""


def revised_front(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> dict:
    """改版で書く frontmatter。承認済みチケットの計画と `phases:` を差し替えたもの。

    `current` は書き換えない。承認画面の「`phases:` の差分」も同じものから組むので、
    画面に出した中身と書く中身が食い違わない。項の `after` も書く（`PlanItem.as_raw`）。
    `phases:`（計画が使う定義の写し）は提案の値に差し替える。差し替えないと
    改版で足した項（フィードバック計画の立案は必ず改版）が `phases:` に無い定義名を使うことになり、
    承認したとたんに親と子が全部止まる。提案に `phases:` が無ければ外す。
    """
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    if ticket_mod.PHASES_KEY in revised.raw:
        front[ticket_mod.PHASES_KEY] = revised.raw[ticket_mod.PHASES_KEY]
    else:
        front.pop(ticket_mod.PHASES_KEY, None)
    return front
