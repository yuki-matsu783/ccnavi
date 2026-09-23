"""全体計画の待ち方（設計 §9.7、ADR-0078）。

`--approve` が全体計画を承認するとき（改版を含む）に、ここで待ち方を計算して親の承認済み
チケットの `workflow:` に写す。判定・延期・フィードバック計画の前提はその写しだけを読む。
計算はこの 1 か所に置き、延期の引き受け手を 2 か所で違って言わないようにする。
"""

from __future__ import annotations

from . import phasetypes, rules
from . import ticket as ticket_mod


def _order(parent: ticket_mod.Ticket, types: dict | None) -> str:
    """使う `order`。計画に読めない種類が 1 つでもあれば一直線。

    祖先が分からないものを並行にしない。
    """
    if types is None or getattr(types, "order", "") != phasetypes.ORDER_DAG:
        return ticket_mod.WORKFLOW_SEQUENTIAL
    if any(types.get(item.type) is None for item in parent.plan):
        return ticket_mod.WORKFLOW_SEQUENTIAL
    return ticket_mod.WORKFLOW_DAG


def _depends(types, later: str, earlier: str) -> bool:
    """`later` の種類が `earlier` の種類を待つか。祖先か同じ種類なら待つ。"""
    return later == earlier or earlier in types.ancestors(later)


def compute(parent: ticket_mod.Ticket, types: dict | None) -> ticket_mod.Workflow:
    """親の全体計画の待ち方を計算する。"""
    order = _order(parent, types)
    wf = ticket_mod.Workflow(order=order)
    items = list(parent.plan)
    for n, item in enumerate(items, start=1):
        mine = (types or {}).get(item.type)
        waits = []
        for m, earlier in enumerate(items[: n - 1], start=1):
            theirs = (types or {}).get(earlier.type)
            if mine is not None and theirs is not None and mine.overlaps(theirs):
                continue
            if order == ticket_mod.WORKFLOW_DAG and not _depends(types, item.type, earlier.type):
                continue
            waits.append(m)
        wf.waits[n] = waits
    for n, item in enumerate(items, start=1):
        if not item.deferred:
            continue
        target = _defer_target(wf, parent, types, n)
        if target is not None:
            wf.review_at[n] = target
    return wf


def _defer_target(
    wf: ticket_mod.Workflow, parent: ticket_mod.Ticket, types: dict | None, n: int
) -> int | None:
    """延期した n 番目を引き受ける番号。後ろの、n を待つ、延期していない最小の番号。

    一直線では後ろの番号はみな n を待つので、次の延期していない番号になる。
    """
    for m in range(n + 1, len(parent.plan) + 1):
        if parent.plan[m - 1].deferred:
            continue
        if wf.order == ticket_mod.WORKFLOW_DAG and not _depends(
            types, parent.plan[m - 1].type, parent.plan[n - 1].type
        ):
            continue
        return m
    return None


def waits_of(parent: ticket_mod.Ticket, number: int, types: dict | None) -> list[int]:
    """N 番目が待つ番号。全体計画は写しで読み、写しが無ければ（承認前の提案）その場で計算する。

    フィードバック計画は一直線で、前の番号を全部待つ（`overlap` の組は待たない）。
    """
    if parent.in_plan(number):
        wf = parent.workflow or compute(parent, types)
        return list(wf.waits.get(number, range(1, number)))
    item = parent.item_at(number)
    mine = (types or {}).get(item.type) if item is not None else None
    waits = []
    for m, earlier in parent.numbered():
        if m >= number:
            break
        theirs = (types or {}).get(earlier.type)
        if mine is not None and theirs is not None and mine.overlaps(theirs):
            continue
        waits.append(m)
    return waits


def problems(parent: ticket_mod.Ticket, types: dict | None) -> list[rules.Problem]:
    """全体計画の待ち方が組めるか。並び、終端、延期の引き受け手（設計 §9.7）。"""
    found: list[rules.Problem] = []
    if types is None or not parent.has_plan:
        return found
    wf = compute(parent, types)
    items = parent.plan
    if wf.order == ticket_mod.WORKFLOW_DAG:
        for i, earlier in enumerate(items):
            for j in range(i + 1, len(items)):
                later = items[j]
                if later.type != earlier.type and later.type in types.ancestors(earlier.type):
                    found.append(
                        rules.Problem(
                            rules.SEVERITY_ERROR,
                            parent.ticket,
                            f"`plan[{j}]` の `{later.type}` は `plan[{i}]` の `{earlier.type}` より"
                            "先に要る（after）。後ろに置くと依存が消えて並行に通る",
                        )
                    )
        last = items[-1]
        loose = [item.type for item in items[:-1] if not _depends(types, last.type, item.type)]
        if loose:
            found.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    parent.ticket,
                    f"最後の項 `{last.type}` が {', '.join(f'`{t}`' for t in dict.fromkeys(loose))}"
                    " を待たない。終端は 1 つにする（合流の種類を最後に置く）",
                )
            )
    for n, item in enumerate(items, start=1):
        if not item.deferred:
            continue
        target = wf.review_at.get(n)
        if target is None:
            found.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    parent.ticket,
                    f"`plan[{n - 1}]` の延期を引き受ける項が無い"
                    "（後ろにこれを待つ、延期していない項が要る）",
                )
            )
            continue
        target_item = items[target - 1]
        tpt = types.get(target_item.type)
        if (
            tpt is not None
            and tpt.review == phasetypes.REVIEW_NONE
            and target_item.review != ticket_mod.PLAN_REVIEW_MR
        ):
            found.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    parent.ticket,
                    f"`plan[{n - 1}]` を延期した先の `{target_item.type}` にレビューが無い",
                )
            )
    return found


def lines(parent: ticket_mod.Ticket, wf: ticket_mod.Workflow) -> list[str]:
    """人向けの待ちの一覧。承認画面と `--explain` に出す。"""
    if wf.order != ticket_mod.WORKFLOW_DAG:
        return []
    out = []
    for n, item in enumerate(parent.plan, start=1):
        waits = wf.waits.get(n, [])
        text = f"待つ: {', '.join(map(str, waits))}" if waits else "何も待たない"
        at = wf.review_at.get(n)
        tail = f"（レビューは {at} と一緒に）" if at is not None else ""
        out.append(f"{n}: {item.type} — {text}{tail}")
    return out
