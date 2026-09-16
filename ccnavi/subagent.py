"""サブエージェントの始まりと終わり。

始まりには、cwd のワークツリーに関わる開いている子の一覧を渡す。終わりには、
範囲外の変更が残っていれば 1 回だけ差し戻す。差し戻しを無視して終わったことは
親に言う。どちらも止められないイベントなので、判定はしない。
"""

from __future__ import annotations

import os
from typing import TextIO

from . import (
    approval,
    audit,
    fsio,
    hookio,
    judge,
    modes,
    phase,
    post,
    reasons,
    rules,
    settings,
    tree,
)
from . import ticket as ticket_mod
from .modes import EXIT_BLOCK, EXIT_OK


def at_start(
    stdout: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが始まったとき。cwd のワークツリーに関わる、開いている子の一覧を渡す。

    止められないイベントなので判定はしない。判定はファイルの行き先で決まるので、
    ここで渡す文は案内でしかない。親のプロンプトに書き忘れがあっても、
    サブエージェントが自分のツリーと範囲を知れるようにする。

    渡すのは cwd で決める。親のワークツリーならその親の開いている子、子のワークツリーなら
    その子自身。main と、チケットの無いワークツリーからの起動には何も渡さない。
    全部の子を渡すと、別のセッションが main で調査を委譲したときにも無関係な
    子の範囲を案内し、調査役が自分の居場所を迷う（SubagentStop と同じ絞り方）。
    """
    record.decision, record.enforced = audit.ALLOW, True
    if not conf.tickets_enabled:
        return EXIT_OK
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    if t is None or t.is_main:
        return EXIT_OK
    # 権威のある側（親のツリー）の写しを読む。着手で書かれる基準点は親のツリーの
    # 写しにだけ入るので、子のツリーに checkout されている版では足りない。
    copies, _ = approval.scan(conf, root)
    index = approval.by_id(copies)
    bound = tree.lookup(index, t.name)
    if bound is None:
        return EXIT_OK
    children = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    if not children:
        return EXIT_OK
    closed, _ = approval.scan(conf, root, closed=True)
    done = {t.ticket for t in closed}
    lines = [
        "[ccnavi] 承認済みで開いている子チケット。"
        "書き込みは行き先のワークツリーのチケットで判定される。"
    ]
    parents = approval.by_id(copies)
    types = phase.load_types(conf, root, bound.project) or {}
    for t in sorted(children, key=lambda x: x.ticket):
        where = tree.worktree_path(root, t.ticket)
        state = "ワークツリーあり" if os.path.isdir(where) else "ワークツリー無し（効かない）"
        waiting = [p for p in t.predecessors if p not in done]
        label = str(t.phase)
        hint = ""
        parent = parents.get(t.parent)
        item = parent.item_at(t.phase) if parent is not None and t.phase is not None else None
        pt = types.get(item.type) if item is not None else None
        if pt is not None:
            label = f"{t.phase}: {pt.title}"
            if pt.agent:
                hint = f" 種類の案内: エージェント {pt.agent}"
        lines.append(
            f"  {t.ticket}（親 {t.parent}、フェーズ {label}）: {where} [{state}]"
            + (f" 先行が未完了: {', '.join(waiting)}" if waiting else "")
            + hint
        )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
    hookio.write_context(stdout, hookio.SUBAGENT_START, "\n".join(lines))
    return EXIT_OK


def at_stop(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが終わろうとしたとき。範囲外の変更が残っていれば 1 回だけ差し戻す。

    見るのは、cwd が子のワークツリーならその子、親のワークツリーならその親の開いている
    子の全部。`base_sha..HEAD` のコミット済みの差分と未コミットの両方を見る。
    範囲は実行前の判定と同じく親の範囲と種類の上限で切り詰め、子の範囲の中でも
    上限の外なら、どの上限かをパスの後ろに添える。
    """
    record.decision, record.enforced = audit.ALLOW, True
    if not conf.tickets_enabled:
        return EXIT_OK
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    copies, _ = approval.scan(conf, root)
    index = approval.by_id(copies)
    targets: list[ticket_mod.Ticket] = []
    bound = tree.lookup(index, t.name) if t is not None and not t.is_main else None
    if bound is not None:
        targets = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    findings = []
    for child in targets:
        outside, unreadable = phase.scope_findings(root, conf, child, index.get(child.parent))
        if unreadable:
            stderr.write(f"ccnavi: {child.ticket} のワークツリーを読めない: {unreadable}\n")
            continue
        for rel, found in outside:
            findings.append((child, rel, found))
    if not findings:
        return EXIT_OK

    record.decision, record.paths = audit.DENY, [f"{c.ticket}:{rel}" for c, rel, _ in findings]
    record.rules, record.code = [reasons.TICKET_RULE], post.CODE_TICKET_SCOPE
    lines = [
        f"[ccnavi] {post.CODE_TICKET_SCOPE}: 子チケットの範囲の外に変更が残っています（"
        f"{len(findings)} 件）。範囲の中へ戻すか、要るなら親に伝えて次のチケットにしてください。"
    ]
    for child, rel, found in findings[: post.REPORT_LIMIT]:
        area = ", ".join(child.paths(rules.ALLOW) + child.paths(rules.ASK)) or "(空)"
        lines.append(f"  {child.ticket}: {rel}{_limit_note(child, found)}  範囲は {area}")
    text = "\n".join(lines)

    already = _bounced(conf.state, payload.agent_id)
    if mode == modes.ENABLE and not already:
        _remember_bounce(stderr, conf.state, payload.agent_id)
        record.enforced = True
        stderr.write(text + "\n")
        return EXIT_BLOCK
    record.enforced = False
    hookio.write_context(stdout, hookio.SUBAGENT_STOP, text)
    return EXIT_OK


def _limit_note(child: ticket_mod.Ticket, found: phase.ScopeVerdict) -> str:
    """子の範囲の中なのに外とされたパスに添える、止めた上限の名指し。子の範囲の外なら空。

    添えないと、承認で見た範囲の中を書いたのに差し戻された理由が読めず、範囲の中へ
    戻せと言われても戻し先が分からない。
    """
    if found.limit == phase.LIMIT_TYPE and found.type is not None:
        return f"（種類 {found.type.title} の上限の外）"
    if found.limit == phase.LIMIT_PARENT:
        return f"（親 {child.parent} の範囲の外）"
    return ""


def ignored_bounce(state_dir: str, payload: hookio.Input) -> str:
    """終わったサブエージェントが差し戻しを受けていたなら、その旨。マーカーは消す。"""
    if payload.tool_name != judge.AGENT_TOOL:
        return ""
    agent_id = str(payload.tool_response.get("agentId") or "")
    if not agent_id or not _bounced(state_dir, agent_id):
        return ""
    fsio.remove(_bounce_path(state_dir, agent_id))
    return (
        f"[ccnavi] {post.CODE_TICKET_SCOPE}: サブエージェント {agent_id} は範囲外の変更を"
        "差し戻されたまま終わっています。合流する前に、その子のワークツリーの範囲外の"
        "変更を確かめてください。"
    )


def _bounce_path(state_dir: str, agent_id: str) -> str:
    safe = fsio.safe_name(agent_id) or "unknown"
    return os.path.join(state_dir, f"subagent-{safe}.bounced")


def _bounced(state_dir: str, agent_id: str) -> bool:
    return bool(state_dir) and os.path.exists(_bounce_path(state_dir, agent_id))


def _remember_bounce(stderr: TextIO, state_dir: str, agent_id: str) -> None:
    if not state_dir:
        return
    failed = fsio.write_text(_bounce_path(state_dir, agent_id), fsio.stamp())
    if failed:
        stderr.write(f"ccnavi: 差し戻しの回数を書けない: {failed}\n")
