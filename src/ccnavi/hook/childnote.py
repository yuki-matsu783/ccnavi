"""子チケットの担当の案内を組む部品。

サブエージェントの始まり（`subagent.at_start`）と、起動の PreToolUse が起動前のプロンプトに
書き足す案内（`agentlaunch`）が同じ文を使う。どちらも判定ではなく、案内でしかない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..infra import settings, tree
from ..policy import rules
from ..tickets import (
    approval,
    approval_checks,
    flow,
    phase,
    phasetypes,
    ticket_model,
)
from ..tickets import ticket as ticket_mod

# 担当の案内に必ず足す 3 行。サブエージェントの Bash は cd がコマンドをまたいで続かず、
# 担当外のワークツリーと、push・チケットの状態の操作は触らせない（メインが行う）。
RULE_LINES = (
    "  Bash の cd はコマンドをまたいで続かない。絶対パスか `cd <パス> && …` の形で打つ。",
    "  担当外のワークツリーに触れない。",
    "  push とチケットの状態を動かす操作はしない（メインが行う）。",
)


@dataclass
class Block:
    """子 1 本ぶんの案内。lines は全部、brief はそのうちフローの行、notice は変更の知らせ。"""

    lines: list[str] = field(default_factory=list)
    brief: list[str] = field(default_factory=list)
    notice: str = ""


def predecessors(
    conf: settings.Settings,
    root: str,
    copies: list[ticket_model.Ticket],
    review: list[ticket_model.Ticket] | None = None,
    closed: list[ticket_model.Ticket] | None = None,
) -> dict:
    """先行を満たしたかを引く表。満たしたとみなすのは、承認と着手と同じく、
    `done/` の取り消しでないものだけ。
    """
    if closed is None:
        closed, _ = approval.scan(conf, root, closed=True)
    if review is None:
        review, _ = approval.scan_review(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    preds = approval_checks.predecessor_pool_of(copies, review, closed, proposals)
    approval_checks.align_imported(conf, root, preds)
    return preds


def child_block(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    index: dict[str, ticket_model.Ticket],
    preds: dict,
    budget: int,
    full: bool,
    *,
    brief_room: int | None = None,
) -> Block:
    """子 1 本について渡す行。ワークツリーの場所と状態、先行、範囲、フロー。

    `brief_room` があれば、フローの手順に使う文字数をそれまでに絞る（プロンプトに書き足す案内は
    上限が小さい）。

    処理フロー:
    1. ワークツリーの場所と有無を求め（`tree.worktree_path`）、未完了の先行を集める
       （`approval_checks.unmet_predecessors`）。
    2. 親の局面の定義から、フェーズの名前と案内するエージェントを引く（`phasetypes.types_of`）。
    3. 子の 1 行目を作り、範囲（禁止・確認・許可の欄）を 1 行ずつ足す（`rules.SECTIONS`）。
    4. フローを `flow.briefing` で読み、失敗したら 1 行の知らせにする。
    5. フローの変更の知らせ（`flow.changed_notice`）を添えて返す。
    """
    where = tree.worktree_path(root, t.ticket)
    state = (
        "ワークツリーあり" if os.path.isdir(where) else "ワークツリー無し（範囲は有効にならない）"
    )
    waiting = [f"{p.ticket}（{p.label}）" for p in approval_checks.unmet_predecessors(t, preds)]
    label = str(t.phase)
    hint = ""
    parent = index.get(t.parent)
    item = parent.item_at(t.phase) if parent is not None and t.phase is not None else None
    # 定義は親に固定したコピー（`phases:`）から引く。`phases.yml` は読まない。
    pt = phasetypes.types_of(parent).get(item.type) if item is not None else None
    if pt is not None:
        label = f"{t.phase}: {pt.title}"
        if pt.agent:
            hint = f" 定義の案内: エージェント {pt.agent}"
    block = Block()
    block.lines.append(
        f"  {t.ticket}（親 {t.parent}、フェーズ {label}）: {where} [{state}]"
        + (f" 先行が未完了: {', '.join(waiting)}" if waiting else "")
        + hint
    )
    for name in rules.SECTIONS:
        paths = t.paths(name)
        if paths:
            block.lines.append(f"    {name}: " + ", ".join(paths))
    # 子のフロー。在ればファイルを名指しし、手順を並べる。
    # フローは人やエージェントが書くデータで、不正でも 1 行の知らせにして、
    # 残りの子と範囲は渡す。
    scope = ", ".join(t.paths(rules.ALLOW) + t.paths(rules.ASK))
    room = budget if brief_room is None else min(budget, brief_room)
    try:
        block.brief = flow.briefing(conf, root, t, scope, room, full=full)
    except Exception as exc:  # noqa: BLE001  不正なフローで案内を失敗させない
        block.brief = [f"    フローを読めない（{type(exc).__name__}）。ユーザが確かめてください"]
    block.lines.extend(block.brief)
    block.notice = flow.changed_notice(conf, root, t)
    return block


def stage_lines(
    conf: settings.Settings,
    root: str,
    index: dict[str, ticket_model.Ticket],
    children: list[ticket_model.Ticket],
) -> list[str]:
    """子の親ごとに、親の局面を 1 行ずつ返す（局面の名前は `phase.stage` のまま）。

    局面は案内であって判定ではない。局面を組めない（置き場を読めないなど）ときや、
    計画の無い親（空文字）には何も出さず、案内は失敗させない。
    """
    parents: list[ticket_model.Ticket] = []
    for child in sorted(children, key=lambda x: x.ticket):
        parent = index.get(child.parent)
        if parent is not None and parent not in parents:
            parents.append(parent)
    lines: list[str] = []
    try:
        # 承認済みチケットの置き場は 1 度だけ読む。
        raw = approval.read_raw(conf, root)
        for parent in parents:
            text = phase.stage(root, conf, parent, raw)
            if text:
                lines.append(f"  親 {parent.ticket} の局面: {text}")
    except Exception:  # noqa: BLE001  局面を組めなくても案内を失敗させない
        return []
    return lines
