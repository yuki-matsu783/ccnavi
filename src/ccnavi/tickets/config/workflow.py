"""計画の先行関係。

先行関係は親の計画（`plan:` / `feedback:`）のステップの `after` から都度計算する。
ファイルにもチケットの欄にも持たない。承認済みチケットの計画は承認済みの置き場にあり、変えられるのは改版（`--agree`）と
ユーザの手だけなので、計算の元も固定されている。フェーズ定義（`phases.yml`）もチケットの状態も
計算の入力にならず、提案も承認済みも同じ関数で計算する。

決まりは 2 つだけ。

- ステップの先行は、ステップの `after` を推移的に辿った全部の番号。`after`
を書かないステップは先行なし
- 延期したステップは、それが先行する延期していないステップのうち、
番号のいちばん小さいものが引き受ける

フィードバック計画も同じ決まりで読む。フィードバック計画のステップには、全体計画の番号が全部（全体計画は
フィードバック計画の前提として全部済んでいる）と、フィードバック計画の中で `after` を推移的に辿った
番号が先行する。`after` が指せるのはフィードバック計画の番号だけ。

計算はこの 1 か所に置き、延期の引き受け手を 2 か所で違って言わないようにする。決まりの見張りは
計画と先行関係の見本の表（`tests/fixtures/plan-waits.json`）。
"""

from __future__ import annotations

from collections.abc import Iterable

from ...policy import rules
from ..model import ticket_ids, ticket_model
from . import phasetypes


def _parts(parent: ticket_model.Ticket) -> list[tuple[str, int, list[ticket_model.PlanItem]]]:
    """計画を (欄の名前, 最初の番号, ステップ) に分ける。

    フィードバック計画は全体計画の続きの番号。
    """
    parts = [("plan", 1, list(parent.plan))]
    if parent.feedback:
        parts.append(("feedback", len(parent.plan) + 1, list(parent.feedback)))
    return parts


def compute(parent: ticket_model.Ticket) -> ticket_model.Workflow:
    """親の計画の先行関係を計算する。形の誤った `after`（`after_errors`）は線として数えない。"""
    wf = ticket_model.Workflow(order=ticket_model.WORKFLOW_DAG)
    planned = len(parent.plan)
    for n, item in enumerate(parent.plan, start=1):
        found: set[int] = set()
        # 前の番号の先行は推移的に閉じているので、直接の先行ごとに 1 度足せば済む（O(n²)）。
        for m in item.after:
            if 1 <= m < n:
                found.add(m)
                found.update(wf.waits.get(m, []))
        wf.waits[n] = sorted(found)
    whole = list(range(1, planned + 1))
    for i, item in enumerate(parent.feedback or []):
        n = planned + 1 + i
        inside: set[int] = set()
        for m in item.after:
            if planned < m < n:
                inside.add(m)
                inside.update(w for w in wf.waits.get(m, []) if w > planned)
        wf.waits[n] = whole + sorted(inside)
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
    """延期した n 番目を引き受ける番号。後ろで n が先行する、延期していない最小の番号。

    n が先行していない番号が引き受けると、延期した作業が閉じる前にそのレビューが済んでしまう。
    """
    for m, later in numbered:
        if m <= n or later.deferred:
            continue
        if n in wf.waits.get(m, []):
            return m
    return None


def effective(parent: ticket_model.Ticket) -> ticket_model.Workflow:
    """判定に使う先行関係。入れてあればそれを、無ければその場で計算する。"""
    return parent.workflow if parent.workflow is not None else compute(parent)


def waits_of(parent: ticket_model.Ticket, number: int) -> list[int]:
    """N 番目の先行の番号。計画に無い番号は前の番号を全部先行とする（止まる側）。"""
    return list(effective(parent).waits.get(number, range(1, number)))


def loose(parent: ticket_model.Ticket, wf: ticket_model.Workflow | None = None) -> list[int]:
    """最後のステップの先行になっていないステップの番号（全体計画とフィードバック計画のそれぞれで）。終端の崩れ。"""
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
    """すぐ始まるステップの番号。その計画の中で先行の無いステップ（フィードバック計画は全体計画を除いて）。"""
    wf = wf or effective(parent)
    out: list[int] = []
    for _, first, items in _parts(parent):
        out += [
            n
            for n in range(first, first + len(items))
            if not [m for m in wf.waits.get(n, []) if m >= first]
        ]
    return out


# `childless_ready` の警告を見分ける文言。承認の確認の「次:」は、これを見て子を書くよう言い換える。
CHILDLESS_MARK = "子の提案がありません"


def childless_ready(
    parent: ticket_model.Ticket, child_phases: Iterable[int]
) -> list[rules.Problem]:
    """全体計画ですぐ始まるのに、子の提案が 1 つも無いステップの警告。

    すぐ始まるステップは、子が無いと着手できない。子の提案を後から出すと、親と別にもう 1 回
    承認が要る。止めないのは、親を先に承認して子を後から出す進め方も正規だから。ただ、書き忘れて
    承認の確認と `approval-request` が黙って通ると気づけないので、警告で名指しする。
    先行のあるステップの子は、先行のレビューを見てから書くことがあるので見ない。
    フィードバック計画のステップも、全体計画のレビューのあとに子を足すので見ない。
    `child_phases` は同じ承認で通す子（と書いた子）のフェーズ番号。
    """
    have = set(child_phases)
    found: list[rules.Problem] = []
    for n in ready(parent):
        item = parent.item_at(n)
        if not parent.in_plan(n) or item is None or n in have:
            continue
        found.append(
            rules.Problem(
                rules.SEVERITY_WARN,
                parent.ticket,
                f"計画の {n} 番目（{item.type}）はすぐ始まるのに、{CHILDLESS_MARK}。"
                f"`{ticket_ids.child_id(parent.ticket, n, 1)}` のような子を同じ承認で通さないと、"
                "子の承認がもう 1 回要る",
            )
        )
    return found


def terminal_problems(
    parent: ticket_model.Ticket, wf: ticket_model.Workflow | None = None, part: str = ""
) -> list[rules.Problem]:
    """終端の崩れを返す。最後のステップに全部が先行しているか、最後のステップが延期されていないか。

    送る時点の検査（`approval-request`）が、`after` の形を調べる `after_problems` と合わせて呼ぶ。
    """
    wf = wf or compute(parent)
    gaps = loose(parent, wf)
    found: list[rules.Problem] = []

    def error(text: str) -> None:
        found.append(rules.Problem(rules.SEVERITY_ERROR, parent.ticket, text))

    for key, first, items in [p for p in _parts(parent) if not part or p[0] == part]:
        last = first + len(items) - 1
        cut = [m for m in gaps if first <= m < last]
        if cut:
            error(
                f"`{key}` の最後のステップ（{last}: {items[-1].type}）に "
                f"{', '.join(map(str, cut))} が先行していない。終端は 1 つにしてください"
                "（最後のステップにほかの全部が推移的に先行するように `after` を書く。"
                "独立した作業は最後に 1 ステップで受けるか、一直線に並べる）"
            )
        if items and items[-1].deferred:
            error(f"`{key}` の最後のステップは延期できない。誰も見ないまま終わる")
    return found


def problems(
    parent: ticket_model.Ticket, types: dict | None = None, part: str = ""
) -> list[rules.Problem]:
    """計画の順序が組めるか。`after` の形、終端、最後のステップの延期、延期の引き受け手。

    全体計画とフィードバック計画に同じ規則を適用する。`part`（`plan` か `feedback`）を渡せば、
    その計画の誤りだけを返す（`--plan-order` が計画ごとの図に分けて返すため）。
    `types`（フェーズ定義）を渡したときだけ、引き受け手にレビューがあるかも見る。渡すのは親のコピー
    （`phases:`。`phasetypes.types_of`）で、`phases.yml` ではない。判定の側（`errors`）もコピーを
    渡すので、承認済みチケットだけを読んで済み、`phases.yml` を承認のあとに直しても進行中の親は
    止まらない。
    """
    found: list[rules.Problem] = []
    if not parent.has_plan:
        return found

    def error(text: str) -> None:
        found.append(rules.Problem(rules.SEVERITY_ERROR, parent.ticket, text))

    # 計画の形（after の形と繰り返し）。
    parts = [p for p in _parts(parent) if not part or p[0] == part]
    found.extend(after_problems(parent, part))
    for key, _first, items in parts:
        for i, item in enumerate(items):
            if item.after_repeated:
                found.append(
                    rules.Problem(
                        rules.SEVERITY_WARN,
                        parent.ticket,
                        f"`{key}[{i}]` の `after` に同じ番号を 2 度挙げている"
                        f"（{', '.join(map(str, item.after_repeated))}）。1 つにまとめて読む",
                    )
                )
    # 先行の計算。
    wf = compute(parent)
    found.extend(terminal_problems(parent, wf, part))
    # 延期: 延期を引き受けるステップが在るか、そのステップにレビューが在るか。
    numbered = dict(parent.numbered())
    for key, first, items in parts:
        for i, item in enumerate(items):
            n = first + i
            if not item.deferred or i == len(items) - 1:
                continue
            target = wf.review_at.get(n)
            if target is None:
                error(
                    f"`{key}[{i}]` の延期を引き受けるステップがありません"
                    "（このステップが先行する、延期していない後ろのステップが必要です）"
                )
                continue
            target_item = numbered[target]
            tpt = (types or {}).get(target_item.type)
            if (
                tpt is not None
                and tpt.review == phasetypes.REVIEW_NONE
                and target_item.review != ticket_model.PLAN_REVIEW_MR
            ):
                error(f"`{key}[{i}]` を延期した先の `{target_item.type}` にレビューがありません。")
    return found


def after_problems(parent: ticket_model.Ticket, part: str = "") -> list[rules.Problem]:
    """ステップの `after` の形の誤り（`after_errors`）。`part` を渡せばその計画だけ。

    `--plan-order` は振り直したステップを組み直すので、
    振り直す前のステップの誤りをここで拾って足す。
    """
    found: list[rules.Problem] = []
    for key, _first, items in _parts(parent):
        if part and key != part:
            continue
        for i, item in enumerate(items):
            for value, why in item.after_errors:
                found.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        parent.ticket,
                        f"`{key}[{i}]` の `after` の {value!r} は読めない（{why}）",
                    )
                )
    return found


def errors(parent: ticket_model.Ticket) -> list[rules.Problem]:
    """承認済みチケットだけを読んで済む計画と定義の誤り（判定の側が適用する分）。

    計画の順序（`problems`）に、親のコピー（`phases:`）の誤り（`phasetypes.copy_problems`。コピーが
    無い、ステップの定義がコピーに無い、定義の形・`kind`）を足す。引き受け手のレビューはコピーの定義で見る。
    読むのはチケットだけ。子に書いた `phases:` もここで言う。
    """
    found = phasetypes.copy_problems(parent) + problems(parent, phasetypes.types_of(parent))
    return [p for p in found if p.severity == rules.SEVERITY_ERROR]


def lines(parent: ticket_model.Ticket, wf: ticket_model.Workflow) -> list[str]:
    """ユーザ向けの先行の一覧。承認画面と `--explain` に出す。全部の番号と、すぐ始まるステップ、
    最後のステップの先行になっていないステップ。"""
    out = []
    for n, item in parent.numbered():
        waits = wf.waits.get(n, [])
        text = f"先行 {', '.join(map(str, waits))}" if waits else "先行なし"
        at = wf.review_at.get(n)
        tail = f"（レビューは {at} と一緒に）" if at is not None else ""
        out.append(f"{n}: {item.type} — {text}{tail}")
    starting = ready(parent, wf)
    for label, part in (
        ("", [n for n in starting if parent.in_plan(n)]),
        ("（フィードバック計画）", [n for n in starting if not parent.in_plan(n)]),
    ):
        if len(part) >= 2:
            out.append(f"すぐ始まる{label}: {', '.join(map(str, part))}")
    gaps = loose(parent, wf)
    if gaps:
        out.append(f"最後のステップの先行になっていない: {', '.join(map(str, gaps))}")
    return out
