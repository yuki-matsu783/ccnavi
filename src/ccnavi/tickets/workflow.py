"""計画の待ち方（設計 9.7）。

待ち方は親の計画（`plan:` / `feedback:`）の項の `after` から都度計算する。ファイルにもチケットの欄
にも持たない。承認済みチケットの計画は承認済みの置き場にあり、変えられるのは改版（`--agree`）と
ユーザの手だけなので、計算の元も固定されている。フェーズ定義（`phases.yml`）もチケットの状態も
計算の入力にならず、提案も承認済みも同じ関数で計算する。

決まりは 2 つだけ。

- 項の待ちは、項の `after` を推移的に辿った全部の番号。`after` を書かない項は何も待たない
- 延期した項は、それを待つ延期していない項のうち、番号のいちばん小さいものが引き受ける

フィードバック計画は、いまは一直線（全体計画の番号を全部と、前のフィードバックの番号を全部待つ）
で読む。延期の引き受け手は同じ決まりで、一直線なら次の延期していない番号になる。

計算はこの 1 か所に置き、延期の引き受け手を 2 か所で違って言わないようにする。決まりの見張りは
計画と待ち方の見本の表（`tests/fixtures/plan-waits.json`）。
"""

from __future__ import annotations

from ..policy import rules
from . import phasetypes, ticket_model


def _parts(parent: ticket_model.Ticket) -> list[tuple[str, int, list[ticket_model.PlanItem]]]:
    """計画を (欄の名前, 最初の番号, 項) に分ける。フィードバック計画は全体計画の続きの番号。"""
    parts = [("plan", 1, list(parent.plan))]
    if parent.feedback:
        parts.append(("feedback", len(parent.plan) + 1, list(parent.feedback)))
    return parts


def compute(parent: ticket_model.Ticket) -> ticket_model.Workflow:
    """親の計画の待ち方を計算する。形の誤った `after`（`after_errors`）は線として数えない。"""
    wf = ticket_model.Workflow(order=ticket_model.WORKFLOW_DAG)
    planned = len(parent.plan)
    for n, item in enumerate(parent.plan, start=1):
        found: set[int] = set()
        # 前の番号の待ちは推移的に閉じているので、直接の先行ごとに 1 度足せば済む（O(n²)）。
        for m in item.after:
            if 1 <= m < n:
                found.add(m)
                found.update(wf.waits.get(m, []))
        wf.waits[n] = sorted(found)
    for i, _ in enumerate(parent.feedback or []):
        n = planned + 1 + i
        # いまは一直線。前の番号（全体計画とフィードバック計画）を全部待つ。
        wf.waits[n] = list(range(1, n))
    numbered = parent.numbered()
    for n, item in numbered:
        if not item.deferred:
            continue
        target = _defer_target(wf, numbered, n)
        if target is not None:
            wf.review_at[n] = target
    return wf


def _defer_target(
    wf: ticket_model.Workflow, numbered: list[tuple[int, ticket_model.PlanItem]], n: int
) -> int | None:
    """延期した n 番目を引き受ける番号。後ろで n を待つ、延期していない最小の番号。

    待たない番号が引き受けると、延期した作業が閉じる前にそのレビューが済んでしまう。
    """
    for m, later in numbered:
        if m <= n or later.deferred:
            continue
        if n in wf.waits.get(m, []):
            return m
    return None


def effective(parent: ticket_model.Ticket) -> ticket_model.Workflow:
    """判定に使う待ち方。入れてあればそれを、無ければその場で計算する。"""
    return parent.workflow if parent.workflow is not None else compute(parent)


def waits_of(parent: ticket_model.Ticket, number: int, types: dict | None = None) -> list[int]:
    """N 番目が待つ番号。計画に無い番号は前の番号を全部待つ（止まる側）。

    `types` は前の呼び方の名残りで、読まない（待ち方はフェーズ定義を読まない）。
    """
    del types
    return list(effective(parent).waits.get(number, range(1, number)))


def loose(parent: ticket_model.Ticket, wf: ticket_model.Workflow | None = None) -> list[int]:
    """最後の項が待たない項の番号（全体計画とフィードバック計画のそれぞれで）。終端の崩れ。"""
    wf = wf or effective(parent)
    out: list[int] = []
    for _, first, items in _parts(parent):
        if len(items) < 2:
            continue
        last = first + len(items) - 1
        waited = set(wf.waits.get(last, []))
        out += [m for m in range(first, last) if m not in waited]
    return out


def ready(parent: ticket_model.Ticket, wf: ticket_model.Workflow | None = None) -> list[int]:
    """すぐ始まる項の番号。その計画の中で何も待たない項（フィードバック計画は全体計画を除いて）。"""
    wf = wf or effective(parent)
    out: list[int] = []
    for _, first, items in _parts(parent):
        out += [
            n
            for n in range(first, first + len(items))
            if not [m for m in wf.waits.get(n, []) if m >= first]
        ]
    return out


def problems(parent: ticket_model.Ticket, types: dict | None = None) -> list[rules.Problem]:
    """計画の順序が組めるか。`after` の形、終端、最後の項の延期、延期の引き受け手（設計 9.7）。

    全体計画とフィードバック計画に同じ規則を当てる。`types`（フェーズ定義）を渡したときだけ、
    引き受け手にレビューがあるかも見る。渡すのは親の写し（`phases:`。`phasetypes.types_of`）で、
    `phases.yml` ではない。判定の側（`errors`）も写しを渡すので、承認済みチケットだけを読んで済み、
    `phases.yml` を承認のあとに直しても進行中の親は止まらない。
    """
    found: list[rules.Problem] = []
    if not parent.has_plan:
        return found

    def error(text: str) -> None:
        found.append(rules.Problem(rules.SEVERITY_ERROR, parent.ticket, text))

    for key, _first, items in _parts(parent):
        for i, item in enumerate(items):
            for value, why in item.after_errors:
                error(f"`{key}[{i}]` の `after` の {value!r} は読めない（{why}）")
            if item.after_repeated:
                found.append(
                    rules.Problem(
                        rules.SEVERITY_WARN,
                        parent.ticket,
                        f"`{key}[{i}]` の `after` に同じ番号を 2 度挙げている"
                        f"（{', '.join(map(str, item.after_repeated))}）。1 つにまとめて読む",
                    )
                )
    wf = compute(parent)
    gaps = loose(parent, wf)
    for key, first, items in _parts(parent):
        last = first + len(items) - 1
        cut = [m for m in gaps if first <= m < last]
        if cut:
            error(
                f"`{key}` の最後の項（{last}: {items[-1].type}）が "
                f"{', '.join(map(str, cut))} を待たない。終端は 1 つにしてください"
                "（最後の項がほかの全部を推移的に待つように `after` を書く。独立した作業は"
                "最後に 1 項で受けるか、一直線に並べる）"
            )
        if items and items[-1].deferred:
            error(f"`{key}` の最後の項は延期できない。誰も見ないまま終わる")
    numbered = dict(parent.numbered())
    for key, first, items in _parts(parent):
        for i, item in enumerate(items):
            n = first + i
            if not item.deferred or i == len(items) - 1:
                continue
            target = wf.review_at.get(n)
            if target is None:
                error(
                    f"`{key}[{i}]` の延期を引き受ける項が無い"
                    "（後ろにこれを待つ、延期していない項が要る）"
                )
                continue
            target_item = numbered[target]
            tpt = (types or {}).get(target_item.type)
            if (
                tpt is not None
                and tpt.review == phasetypes.REVIEW_NONE
                and target_item.review != ticket_model.PLAN_REVIEW_MR
            ):
                error(f"`{key}[{i}]` を延期した先の `{target_item.type}` にレビューが無い")
    return found


def errors(parent: ticket_model.Ticket) -> list[rules.Problem]:
    """承認済みチケットだけを読んで済む計画と定義の誤り（判定の側が当てる分）。

    計画の順序（`problems`）に、親の写し（`phases:`）の誤り（`phasetypes.copy_problems`。写しが
    無い、項の定義が写しに無い、定義の形・`kind`）を足す。引き受け手のレビューは写しの定義で見る。
    読むのはチケットだけで、`phases.yml` は読まない。子に書いた `phases:` もここで言う。
    """
    found = phasetypes.copy_problems(parent) + problems(parent, phasetypes.types_of(parent))
    return [p for p in found if p.severity == rules.SEVERITY_ERROR]


def lines(parent: ticket_model.Ticket, wf: ticket_model.Workflow) -> list[str]:
    """ユーザ向けの待ちの一覧。承認画面と `--explain` に出す。全部の番号と、すぐ始まる項、
    最後の項が待たない項。"""
    out = []
    for n, item in parent.numbered():
        waits = wf.waits.get(n, [])
        text = f"待つ: {', '.join(map(str, waits))}" if waits else "何も待たない"
        at = wf.review_at.get(n)
        tail = f"（レビューは {at} と一緒に）" if at is not None else ""
        out.append(f"{n}: {item.type} — {text}{tail}")
    starting = [n for n in ready(parent, wf) if parent.in_plan(n)]
    if len(starting) >= 2:
        out.append(f"すぐ始まる: {', '.join(map(str, starting))}")
    gaps = loose(parent, wf)
    if gaps:
        out.append(f"最後の項が待たない: {', '.join(map(str, gaps))}")
    return out
