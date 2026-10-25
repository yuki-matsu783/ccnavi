"""承認の画面。候補ごとに、計画・先行関係・フェーズと、改版の差分を並べる文面。

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


def approved_text(
    tickets: list[ticket_model.Ticket],
    revisions: set[str],
    root: str,
    worktrees: list[dict[str, str]] | None = None,
    notices: list[str] | None = None,
) -> str:
    """チケットが承認されたことをモデルに伝える文。

    `--agree --yes` の `prompt`（拡張が Claude Code に渡す）がここから出る。hook は承認を
    伝えない。ほかの経路の承認は、エージェントが `ccnavi-ticket.sh status` で聞く。

    tickets は承認済みチケット（`ticket` `title` `parent` `phase` `is_child` を持つもの）。
    revisions は親の改版だった識別子。root はワークスペースルートで、sh のパスに使う。
    worktrees は子のワークツリーの計画（`worktrees.child_worktrees`）。
    無ければ従来の案内だけを出す。
    notices は補足と注意（承認した親のワークツリーに残る、提案以外の変更など）。1 件ごとに 1 行の
    `案内:` として添える。次の一手ではない。
    """
    lines = ["[ccnavi] チケットが承認され、承認済みチケットの置き場（doing/）へ動いた。"]
    for t in tickets:
        if t.ticket in revisions:
            where = "親の改版。計画・コピー・範囲のどれかが新しくなった"
        elif t.is_child:
            where = f"親 {t.parent}、フェーズ {t.phase}"
        else:
            where = "親"
        title = f": {t.title}" if t.title else ""
        lines.append(f"- {t.ticket}{title}（{where}）")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    lines.append(
        "承認はユーザが済ませた。承認を頼み直さない。前の会話が無くても、この文と "
        f"'{ticket_sh} status' で状況は分かる。承認済みかどうかや置き場は、"
        "ファイルを自分で探さず status で確かめる。"
    )
    # 子の着手は親の着手を前提にする。順をここで言わないと、
    # 最初の子の着手で止まってから読むことになる。ただし勧めるのは、この回に承認された
    # 親が居るときだけ。改版と子だけの回で `start <親>` を勧めると、親は着手済みなので
    # 案内どおりに打つと「着手済み」で終わる。
    guide = "次: 後工程を進める。"
    if any(not t.is_child and t.ticket not in revisions for t in tickets):
        guide += f"親は自分のワークツリーで '{ticket_sh} start <親>' を先に実行してください。"
    if any(t.is_child for t in tickets) and worktrees is None:
        guide += (
            "子はワークツリー .claude/worktrees/<識別子> を親のブランチから切り、"
            f"'{ticket_sh} start <識別子>' で開始してください（親が未着手だと止まります）。"
        )
    elif any(t.is_child for t in tickets):
        guide += (
            f"子は'{ticket_sh} start <識別子>' で開始してください（親が未着手だと止まります）。"
        )
    lines.append(guide)
    if worktrees is not None:
        git_sh = settings.script_command(root, "ccnavi-git.sh")
        lines += _child_worktree_lines(tickets, worktrees, git_sh)
    lines += [f"案内: {text}" for text in notices or []]
    # 承認の前に計画編集タブ（`--plan-order --write`）で計画の順序が書き換わると、
    # エージェントが書いた番号と承認された番号が違うことがある。書き換えられたかを見分ける材料が
    # 無い（コミットしていない編集と見分けられない）ので、計画を持つ親とその子があればいつも添える。
    if any((not t.is_child and t.has_plan) or (t.is_child and t.phase) for t in tickets):
        lines.append(
            "番号は承認済みチケットで読み直す（承認の前に計画編集タブで計画の順序と番号が"
            "書き換わっていることがある）。"
        )
    return "\n".join(lines)


def _child_worktree_lines(
    tickets: list[ticket_model.Ticket], worktrees: list[dict[str, str]], git_sh: str
) -> list[str]:
    """子のワークツリーの状態を 1 子 1 行で言う。作れていない子は、名前と手で打つコマンドを出す。

    拡張は `create` の子を承認の後に作る（作れなかった子を名指しで知らせる）。ここで言うのは、
    エージェントが後から読んでも、どのワークツリーをどう用意するかが分かるようにするため。
    """
    by_ticket = {w["ticket"]: w for w in worktrees}
    lines = []
    for t in tickets:
        w = by_ticket.get(t.ticket)
        if not t.is_child or w is None:
            continue
        path = w["path"]
        tid = t.ticket
        if w["state"] == "create":
            base = w["base"]
            lines.append(
                f"子 {tid} のワークツリー {path} は、親のブランチ {base} から"
                "承認の後に作られる予定（作れなければ案内の警告に理由が出る）。"
                f"無ければ '{git_sh} worktree add {path} -b {tid} {base}' で作る"
            )
        elif w["state"] == "exists":
            lines.append(f"子 {tid} のワークツリー {path} は既にある")
        else:
            lines.append(
                f"子 {tid} のワークツリー {path} は、まだありません（親 {t.parent}"
                " のワークツリーがない、"
                "またはプロジェクトの子）。親のワークツリーを用意し、そのブランチで "
                f"'{git_sh} worktree add {path} -b {tid} <親のブランチ>' で作る"
            )
    return lines


def _origin_line(t: ticket_model.Ticket) -> str:
    """どのプロジェクトの、どのツリーの、どの提案か。

    プロジェクトは提案を置いた場所で決まる。ユーザはここで、書き込みが向かうリポジトリを
    見て承認する。提案はそのツリーからの相対パスで見せる。絶対パスは
    機械ごとに違い、画面の本文が Chrome と手元で揃わない。
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

    ユーザに見せるのは「何が新たに書けるようになるか」
    「子が編集可能な範囲（親をどこまで絞ったか）」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く。

    定義は候補が持っているものを使う。承認の対象の中でもチケットごとにレイヤーが違いうるので、
    画面の側で 1 つに決めない。

    処理フロー:
    1. 候補ごとに、親の改版なら差分だけを出して次の候補へ進む（`_scope_diff_lines`・
       `_plan_diff_lines`・`_copy_diff_lines`）
    2. 子なら、親の範囲から絞った結果と定義の範囲を出す（`_type_of`）。
       親がまだ承認されていなければ、そう書く
    3. 親なら、編集できる範囲（allow・ask・deny の意味を添える）を出す
    4. 範囲の下に、チケットが宣言したが書き込めない場所（`cand.overflow`）を出す
    5. 子は人間レビューと先行、親は計画・フェーズ間の先行（`_plan_lines`・
       `_workflow_lines`）・コピーの定義（`_copy_lines`）を出す
    6. 課題・理由・出どころ（`_origin_line`）を出し、判定に使われない記述（警告）を末尾に置く
    """
    lines = [f"チケットの承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        cand_types = cand.types
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}  親の改版"]
            lines += _scope_diff_lines(cand.current, t)
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
                "親の範囲にない場所が新たに編集可能になることはありません"
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
                "    このチケットでは、以下に示す場所のみ編集できるようになります。"
                "allow は確認なしで編集できる場所、ask は実行時に確認が必要な場所、"
                "deny はこのチケットでも編集できない場所"
            )
            # チケットの範囲はルールの allow より強い。承認するユーザは「ルールで
            # 開けてあるから範囲の外でも書ける」と読み違えやすいので、承認の前に言う。
            lines.append(
                "    ルールの allow で許可されていても、このチケットの範囲外では編集できません。"
                "ルールの deny はこの範囲の中でも止まる"
            )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if cand.overflow:
            # 範囲のすぐ下に置く。判定では止まる。判定に影響しない記述の
            # 注意と混ぜると、承認すれば書けると読み違える。
            # 「編集対象」と「書き込めない」は意図して分けてある。 前半はチケットが宣言した側、
            # 後半は実際の書き込みが止まる側の話で、どちらか一方の語に揃えると、宣言と実行の
            # どちらを指しているのかが読めなくなる。ほかの見出しが「編集」で揃っているのを見て、
            # ここも揃えたくなるが、揃えない。
            lines.append("■ チケットで編集対象としているが、書き込めない場所")
            lines.append(
                "    親の範囲かフェーズ定義の上限を超えているか、計画のどの定義の範囲とも"
                "重ならない。承認はできますが、これらの場所への書き込みは拒否されます。"
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
                    "    すべての先行チケットが、取り消されずに完了し、"
                    "done/ に移動している必要があります。"
                    "条件を満たすまで、承認・開始はできません。"
                )
        elif t.has_plan:
            lines.append("■ 全体計画")
            lines.append(
                "    承認すると、この順序で進めることに合意したことになる。"
                "先行フェーズの終了と必要なレビューの完了を確認するまで、このフェーズの子チケットは承認できません。"
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
                "    この親のマージリクエストの本文に Closes として記載する番号。"
                "マージされると、このIssueもクローズされます。"
            )
        if t.rationale.strip():
            lines.append("■ エージェントが記載した理由")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(_origin_line(t))
        warnings = [p for p in cand.complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 判定に使われない記述")
            lines.append(
                "    提案に記載があっても、判定はこれを読みません。"
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
            review = "次のフェーズとまとめてレビュー"
        elif item.review == ticket_model.PLAN_REVIEW_MR:
            review = "レビュー必須：計画で追加指定"
        elif pt is not None:
            review = {
                phasetypes.REVIEW_MR: "レビュー必須: マージリクエスト",
            }.get(pt.review, "レビュー不要")
        lines.append(f"    {n}. {title} / {item.type}" + (f"  {review}" if review else ""))
    return lines


def _workflow_lines(t: ticket_model.Ticket, wf: ticket_model.Workflow) -> list[str]:
    """計画の先行。全部の番号と、すぐ始まるステップ。線の書き漏れをユーザが見つける場所。"""
    found = workflow.lines(t, wf)
    if not found:
        return []
    return [
        "■ フェーズ間の依存関係（計画の各ステップの after で決まります。"
        "承認後に phases.yml を変更しても変わりません）",
        *("    " + x for x in found),
    ]


def _copy_lines(t: ticket_model.Ticket, types: dict | None, config: dict | None) -> list[str]:
    """親に固定する定義を 3 つに分けて出す（この親が使う定義・使われていない定義・
    `phases.yml` にあってこの親では使わない定義）。

    コピーの中身は `phases.yml` と同じ（承認が確かめる）ので、ユーザが読むのは「どの定義をこの親に
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
            "    承認したときの phases.yml と同じ中身を親にコピーして固定する。"
            "承認後に phases.yml を変更しても、この親チケットの編集可能範囲・"
            "確認対象・成果物は変わりません。"
        )
        for ident in used:
            pt = types[ident]
            scope = (
                "inherit（親チケットの編集可能範囲を引き継ぐ）"
                if pt.inherits_scope
                else ", ".join(pt.scope_globs)
            )
            parts = [f"review: {pt.review}", f"scope: {scope}"]
            if pt.deliverables:
                parts.append(f"deliverables: {', '.join(pt.deliverables)}")
            if pt.learning == phasetypes.LEARNING_OFF:
                parts.append("learning: off")
            parts.append(f"使う番号: {', '.join(map(str, numbers[ident]))}")
            lines.append(f"    {pt.title} / {ident}  " + "、".join(parts))
    unused = [ident for ident in types if ident not in numbers]
    if unused:
        lines.append("■ 使われていない定義（phases: にあるが、どのステップも使わない）")
        lines += [f"    {types[ident].title} / {ident}" for ident in unused]
    left = [pt for ident, pt in (config or {}).items() if ident not in types]
    if left:
        lines.append("■ この親では使わない定義（phases.yml にあるが phases: にない）")
        lines.append("    使わない: " + "、".join(f"{pt.title} / {pt.id}" for pt in left))
    return lines


def _scope_diff_lines(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> list[str]:
    """改版の範囲の差分。足した項・外した項を、deny / ask / allow ごとに並べる。

    古い範囲は承認のあとに上書きされ、ccnavi の側には残らない。承認する人が、
    新たに書けるようになる範囲と書けなくなる範囲を、ここで見られるようにする。
    """
    lines: list[str] = []
    for name in rules.SECTIONS:
        before = _scope_items(current, name)
        after = _scope_items(revised, name)
        lines += [f"    {name} 追加: {_shown(i)}" for i in after if i not in before]
        lines += [f"    {name} 削除: {_shown(i)}" for i in before if i not in after]
    if not lines:
        return []
    return [
        "■ 範囲の差分（改版で親に固定する範囲）",
        "    allow は確認なしで編集できる場所、ask は実行時に確認が必要な場所、"
        "deny はこのチケットでも編集できない場所。承認すると、古い範囲は上書きされる",
        *lines,
        "    作業中の子は居ない（居れば改版は通らない）。新しい範囲の子は、新しい子として提案する。"
        "狭めた場合、完了済み・レビュー待ちの子の範囲が親の外に出ることがあるが、"
        "書き込みは親の範囲で切り詰められる",
    ]


def _scope_items(t: ticket_model.Ticket, name: str) -> list[tuple[str, str]]:
    """差分で比べる項。`(種別, 式)` の組で、同じ項の重複は 1 つにまとめる。

    glob `src/app` と regex `src/app` は文字列が同じでもヒットする範囲が違う（regex は先頭一致で、
    `src/application.py` にもヒットする）。文字列だけで比べると、署名が違うのに差分が空になり、
    承認する人には範囲の広がりが見えなくなる。種別を組に入れて比べ、表示のときだけ書き分ける。
    """
    items = [
        ("regex", e.regex) if e.regex else ("glob", e.glob) for e in t.entries if e.decision == name
    ]
    return list(dict.fromkeys(items))


def _shown(item: tuple[str, str]) -> str:
    kind, expr = item
    return f"regex({expr})" if kind == "regex" else expr


def _copy_diff_lines(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> list[str]:
    """改版の `phases:` の差分。改版が実際に書く frontmatter（`agree_digest.revised_front` と同じ
    差し替え。コピーは提案の値）と承認済みチケットのコピーを比べる。足した・外した・変わった定義。"""
    before = phasetypes.types_of(current)
    written = agree_digest.revised_front(current, revised).get(phasetypes.COPY_KEY)
    after = phasetypes.read_copy(written)[0] if written is not None else {}
    lines: list[str] = []
    for ident, pt in after.items():
        if ident not in before:
            lines.append(f"    追加: {pt.title} / {ident}")
        elif not phasetypes.same(pt, before[ident]):
            fields = ", ".join(phasetypes.differing(before[ident], pt))
            lines.append(f"    変更: {pt.title} / {ident}（{fields}）")
    for ident, pt in before.items():
        if ident not in after:
            lines.append(f"    削除: {pt.title} / {ident}")
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
        lines.append("■ フェーズ間の依存関係の変更")
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
