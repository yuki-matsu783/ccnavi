"""承認の画面。候補ごとに、計画・待ち方・フェーズと、改版の差分を並べる文面。

agree から分けた。agree を読まない。
"""

from __future__ import annotations

from ..infra import settings
from ..policy import rules
from . import (
    agree_candidates,
    agree_digest,
    approval,
    phasetypes,
    ticket_model,
    workflow,
)
from . import ticket as ticket_mod


def approved_text(tickets: list[ticket_model.Ticket], revisions: set[str], root: str) -> str:
    """チケットが承認されたことをモデルに伝える文。

    `--agree --yes` の `prompt`（拡張が Claude Code に渡す）がここから出る。hook は承認を
    伝えない。ほかの経路の承認は、エージェントが `ccnavi-ticket.sh status` で聞く。

    tickets は承認済みチケット（`ticket` `title` `parent` `phase` `is_child` を持つもの）。
    revisions は親の改版だった識別子。root はワークスペースルートで、sh のパスに使う。
    """
    lines = ["[ccnavi] チケットが承認され、承認済みチケットの置き場（doing/）へ動いた。"]
    for t in tickets:
        if t.ticket in revisions:
            where = "親の改版。計画が新しくなった"
        elif t.is_child:
            where = f"親 {t.parent}、フェーズ {t.phase}"
        else:
            where = "親"
        title = f": {t.title}" if t.title else ""
        lines.append(f"- {t.ticket}{title}（{where}）")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    # 子の着手は親の着手を前提にする（設計 9.6、REQ-TKT-48）。順をここで言わないと、
    # 最初の子の着手で止まってから読むことになる。ただし勧めるのは、この回に承認された
    # 親が居るときだけ。改版と子だけの回で `start <親>` を勧めると、親は着手済みなので
    # 案内どおりに打つと「着手済み」で終わる。
    guide = "後工程を進める。"
    if any(not t.is_child and t.ticket not in revisions for t in tickets):
        guide += f"親は自分のワークツリーで '{ticket_sh} start <親>' を先に打つ。"
    if any(t.is_child for t in tickets):
        guide += (
            "子はワークツリー .claude/worktrees/<識別子> を親のブランチから切り、"
            f"'{ticket_sh} start <識別子>' で着手する（親が未着手だと止まる）。"
        )
    lines.append(guide)
    return "\n".join(lines)


def _origin_line(t: ticket_model.Ticket) -> str:
    """どのプロジェクトの、どのツリーの、どの提案か（REQ-MLT-11）。

    プロジェクトは提案を置いた場所で決まる。ユーザはここで、書き込みが向かうリポジトリを
    見て承認する。提案はそのツリーからの相対パスで見せる。絶対パスは
    機械ごとに違い、承認のダイジェスト（画面の本文を含む）が Chrome と手元で揃わない。
    """
    return (
        f"■ プロジェクト: {t.project or 'ワークスペース'}"
        f"  ワークツリー: {t.tree or 'ワークスペースルート'}  提案: {approval.source_path(t)}"
    )


def screen(
    batch: list[agree_candidates.Candidate],
    pool: dict[str, ticket_model.Ticket],
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。ユーザに見せるのは「何が新たに書けるようになるか」
    「子が編集可能な範囲（親をどこまで絞ったか）」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。

    定義は候補が持っているものを使う。承認の対象の中でもチケットごとにレイヤーが違いうるので、
    画面の側で 1 つに決めない。
    """
    lines = [f"チケットの承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        cand_types = cand.types
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}  親の改版"]
            lines += _plan_diff_lines(cand.current, t, cand_types)
            lines += _copy_diff_lines(cand.current, t)
            for note in cand.notes:
                lines.append(f"    {note}")
            lines.append(_origin_line(t))
            continue
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (
                f"  親 {t.parent} / フェーズ {_phase_label(t, pool, cand_types)}"
                if t.is_child
                else "  親チケット"
            ),
        ]
        if t.is_child:
            # 親は一緒に承認の対象に入っていることが普通。承認済みチケットだけを引くと
            # 「承認済みチケットが無い」になる。
            parent = pool.get(t.parent)
            lines.append("■ この子チケットで編集可能な範囲")
            lines.append(
                "    子の範囲は親の範囲の中に収まる。下に並ぶのは親から絞った結果で、"
                "親の範囲に無い場所がここで新しく編集できるようになることはない"
            )
            head = "親の範囲: " + (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                if parent
                else "親がまだ承認されていない"
            )
            lines.append(f"    {head}")
            bound = _type_of(t, pool, cand_types)
            if bound is not None and not bound.inherits_scope:
                lines.append(f"    定義「{bound.title}」の範囲: " + ", ".join(bound.scope_globs))
        else:
            lines.append("■ このチケットで編集可能な範囲")
            lines.append(
                "    下に並ぶ場所にだけ、このチケットで編集できるようになる。"
                "allow は無確認で編集できる場所、ask は確認を挟んで編集できる場所、"
                "deny はこのチケットでも編集できない場所"
            )
            # チケットの範囲はルールの allow より強い（設計 7）。承認するユーザは「ルールで
            # 開けてあるから範囲の外でも書ける」と読み違えやすいので、承認の前に言う。
            lines.append(
                "    ルールの allow で許可してある場所も、この範囲の外では止まる。"
                "ルールの deny はこの範囲の中でも止まる"
            )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if cand.overflow:
            # 範囲のすぐ下に置く。承認は止めないが、判定では止まる。判定に影響しない記述の
            # 注意と混ぜると、承認すれば書けると読み違える。
            # **「編集対象」と「書き込めない」は意図して分けてある。** 前半はチケットが宣言した側、
            # 後半は実際の書き込みが止まる側の話で、どちらか一方の語に揃えると、宣言と実行の
            # どちらを指しているのかが読めなくなる。ほかの見出しが「編集」で揃っているのを見て、
            # ここも揃えたくなるが、揃えない。
            lines.append("■ チケットで編集対象としているが、書き込めない場所")
            lines.append(
                "    親の範囲かフェーズ定義の上限を超えている。"
                "承認は可能だが、編集しようとすると判定が止める"
            )
            lines += [f"    {p.detail}" for p in cand.overflow]
        if t.is_child:
            state = "要" if t.review_required else "不要"
            lines.append(f"■ 人間レビュー: {state}")
            if t.review_reason:
                lines.append(f"    理由: {t.review_reason}")
            if t.predecessors:
                lines.append(f"■ 先行: {', '.join(t.predecessors)}")
                lines.append(
                    "    どれも done/ に在って取り消しでないこと。"
                    "満たしていなければ、承認も着手も止まる"
                )
        elif t.has_plan:
            lines.append("■ 全体計画")
            lines.append(
                "    承認すると、この順序で進めることに合意したことになる。"
                "各フェーズの子は、そのフェーズが待つフェーズ（下の待ち方）が閉じてレビューが"
                "済むまで承認できない"
            )
            lines += _plan_lines(t.plan, 1, cand_types)
            if t.feedback is not None:
                lines.append("■ フィードバック計画")
                lines += _plan_lines(t.feedback, len(t.plan) + 1, cand_types) or ["    対応なし"]
            lines += _workflow_lines(t, workflow.compute(t))
            lines += _copy_lines(t, cand_types, cand.config)
        if not t.is_child and t.issue is not None:
            lines.append(f"■ 課題: {ticket_mod.issue_label(t)}")
            lines.append(
                "    この親のマージリクエストの本文に Closes として書く番号。"
                "マージされると、この課題も閉じる"
            )
        if not t.is_child and t.branch:
            lines.append(f"■ ブランチ: {t.branch}")
            if cand.existing_branch:
                lines.append(
                    f"    既存のブランチ {t.branch} を使う。識別子（{t.ticket}）と別の名前で、"
                    f"ワークツリーは .claude/worktrees/{t.ticket}。"
                    "取り込み・送る・マージリクエストはこのブランチで行う"
                )
            else:
                lines.append(
                    f"    新しく切るブランチ。識別子（{t.ticket}）と別の名前で、"
                    f"ワークツリーは .claude/worktrees/{t.ticket}。"
                    "取り込み・送る・マージリクエストはこのブランチで行う"
                )
        if t.rationale.strip():
            lines.append("■ エージェントが書いた理由")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(_origin_line(t))
        warnings = [p for p in cand.complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 判定に効かない記述")
            lines.append(
                "    提案に書いてあっても、判定はこれを読まない。"
                "承認しても、編集できる場所は変わらない"
            )
            lines += [f"    {p.detail}" for p in warnings]
    return "\n".join(lines)


def _plan_lines(items: list[ticket_model.PlanItem], start: int, types: dict | None) -> list[str]:
    lines = []
    for i, item in enumerate(items):
        n = start + i
        pt = (types or {}).get(item.type)
        title = pt.title if pt is not None else item.type
        review = ""
        if item.deferred:
            review = "レビューは次と一緒に"
        elif item.review == ticket_model.PLAN_REVIEW_MR:
            review = "レビュー要: 計画で強めた"
        elif pt is not None:
            review = {
                phasetypes.REVIEW_MR: "レビュー要: マージリクエスト",
                phasetypes.REVIEW_CHAT: "レビュー要: このセッションで",
            }.get(pt.review, "レビュー不要")
        lines.append(f"    {n}. {title} / {item.type}" + (f"  {review}" if review else ""))
    return lines


def _workflow_lines(t: ticket_model.Ticket, wf: ticket_model.Workflow) -> list[str]:
    """計画の待ち。全部の番号と、すぐ始まる項。線の書き漏れをユーザが見つける場所（設計 9.7）。"""
    found = workflow.lines(t, wf)
    if not found:
        return []
    return [
        "■ 待ち方（計画の項の after から決まる。後から phases.yml を直しても変わらない）",
        *("    " + x for x in found),
    ]


def _copy_lines(t: ticket_model.Ticket, types: dict | None, config: dict | None) -> list[str]:
    """親に固定する定義を 3 つに分けて出す（この親が使う定義・使われていない定義・
    `phases.yml` にあってこの親では使わない定義）。

    写しの中身は `phases.yml` と同じ（承認が確かめる）ので、ユーザが読むのは「どの定義をこの親に
    固定し、どれを外したか」。
    """
    types = types or {}
    numbers: dict[str, list[int]] = {}
    for n, item in t.numbered():
        numbers.setdefault(item.type, []).append(n)
    used = [ident for ident in types if ident in numbers]
    lines: list[str] = []
    if used:
        lines.append("■ この親に固定するフェーズ定義（phases:。承認のあとの判定はこれを読む）")
        lines.append(
            "    承認したときの phases.yml と同じ中身を親に写して固定する。"
            "後から phases.yml を直しても、この親の範囲・見る場所・成果物は変わらない"
        )
        for ident in used:
            pt = types[ident]
            scope = (
                "inherit（親の範囲そのまま）" if pt.inherits_scope else ", ".join(pt.scope_globs)
            )
            parts = [f"review: {pt.review}", f"scope: {scope}"]
            if pt.deliverables:
                parts.append(f"deliverables: {', '.join(pt.deliverables)}")
            parts.append(f"使う番号: {', '.join(map(str, numbers[ident]))}")
            lines.append(f"    {pt.title} / {ident}  " + "、".join(parts))
    unused = [ident for ident in types if ident not in numbers]
    if unused:
        lines.append("■ 使われていない定義（phases: にあるが、どの項も使わない。判定は読まない）")
        lines += [f"    {types[ident].title} / {ident}" for ident in unused]
    left = [pt for ident, pt in (config or {}).items() if ident not in types]
    if left:
        lines.append("■ この親では使わない定義（phases.yml にあるが phases: に無い）")
        lines.append("    使わない: " + "、".join(f"{pt.title} / {pt.id}" for pt in left))
    return lines


def _copy_diff_lines(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> list[str]:
    """改版の `phases:` の差分。改版が実際に書く frontmatter（`agree_digest.revised_front` と同じ
    差し替え。写しは提案の値）と承認済みチケットの写しを比べる。足した・外した・変わった定義。"""
    before = phasetypes.types_of(current)
    written = agree_digest.revised_front(current, revised).get(phasetypes.COPY_KEY)
    after = phasetypes.read_copy(written)[0] if written is not None else {}
    lines: list[str] = []
    for ident, pt in after.items():
        if ident not in before:
            lines.append(f"    足した: {pt.title} / {ident}")
        elif not phasetypes.same(pt, before[ident]):
            fields = ", ".join(phasetypes.differing(before[ident], pt))
            lines.append(f"    変わった: {pt.title} / {ident}（{fields}）")
    for ident, pt in before.items():
        if ident not in after:
            lines.append(f"    外した: {pt.title} / {ident}")
    if not lines:
        return []
    return ["■ phases: の差分（改版で親に固定する定義）", *lines]


def _plan_diff_lines(
    current: ticket_model.Ticket, revised: ticket_model.Ticket, types: dict | None
) -> list[str]:
    lines = []
    if approval.plan_signature(current, "plan") != approval.plan_signature(revised, "plan"):
        lines.append("■ 全体計画の変更")
        lines.append("    いま:")
        lines += ["    " + x for x in _plan_lines(current.plan, 1, phasetypes.types_of(current))]
        lines.append("    改版:")
        lines += ["    " + x for x in _plan_lines(revised.plan, 1, types)]
    held, fresh = workflow.compute(current), workflow.compute(revised)
    before, after = workflow.lines(current, held), workflow.lines(revised, fresh)
    if before != after:
        lines.append("■ 待ち方の変更")
        lines.append("    いま:")
        lines += ["        " + x for x in before]
        lines.append("    改版:")
        lines += ["        " + x for x in after]
    if approval.plan_signature(current, "feedback") != approval.plan_signature(revised, "feedback"):
        lines.append("■ フィードバック計画")
        start = len(revised.plan) + 1
        lines += _plan_lines(revised.feedback or [], start, types) or [
            "    対応なし。見たうえで対応しない、という記録になる"
        ]
    return lines


def _phase_label(t: ticket_model.Ticket, pool: dict, types: dict | None) -> str:
    pt = _type_of(t, pool, types)
    return f"{t.phase}: {pt.title}" if pt is not None else str(t.phase)


def _type_of(t: ticket_model.Ticket, pool: dict, types: dict | None):
    parent = pool.get(t.parent) if t.is_child else None
    if parent is None or not parent.has_plan or t.phase is None or not types:
        return None
    item = parent.item_at(t.phase)
    return types.get(item.type) if item is not None else None
