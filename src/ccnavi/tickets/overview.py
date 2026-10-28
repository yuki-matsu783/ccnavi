"""このPCで作業中の親の一覧（セッション開始の表示と `ticket status` の頭）。

親 1 本を 1 行で言う。親・ブランチ・MR・次の子・未送信の有無。
`ticket status`（引数なし）はさらに、親ごとに子のワークツリーの状態
（合流済み・未合流・未コミットあり）を言う。

読むのは手元のファイルと git の ref だけで、ネットワークには出ない。MR の番号は親チケットの `mr:`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..infra import gitcmd, settings, tree
from . import approval, approval_checks
from .model import ticket_model

TIMEOUT_SECONDS = 5.0

NO_BRANCH = "（ブランチの外）"

CHILD_MERGED = "合流済み"
CHILD_UNMERGED = "未合流"
CHILD_DIRTY = "未コミットあり"


@dataclass
class Row:
    """このPCの親 1 本。"""

    ident: str
    path: str
    branch: str
    mr: str = ""
    next_child: str = ""
    unsent: str = ""
    project: str = ""


def collect(
    conf: settings.Settings,
    root: str,
    doing: list[ticket_model.Ticket],
    review: list[ticket_model.Ticket],
    closed: list[ticket_model.Ticket],
    proposals: list[ticket_model.Ticket],
) -> list[Row]:
    """このPCに親のワークツリーがある、まだ閉じていない親の一覧。識別子の順。"""
    parents: dict[str, ticket_model.Ticket] = {}
    for t in [*doing, *review]:
        if not t.is_child:
            parents.setdefault(t.ticket, t)
    for t in proposals:
        if not t.is_child and t.state == ticket_model.TODO:
            parents.setdefault(t.ticket, t)
    pool = approval_checks.predecessor_pool_of(doing, review, closed, proposals)
    # 承認と lint と同じ引き方にする。
    approval_checks.align_imported(conf, root, pool)
    rows = []
    for ident in sorted(parents):
        path = tree.worktree_path(root, ident)
        if not os.path.isdir(path):
            continue
        parent = parents[ident]
        branch = tree.branch_of(path) or ""
        rows.append(
            Row(
                ident=ident,
                path=path,
                branch=branch,
                mr=str(parent.mr) if parent.mr else "",
                next_child=next_child(ident, doing, pool),
                unsent=unsent_of(path, branch),
                project=parent.project,
            )
        )
    return rows


def next_child(
    ident: str,
    doing: list[ticket_model.Ticket],
    pool: dict[str, list[ticket_model.Ticket]],
) -> str:
    """次に着手できる子。承認済みで未着手、先行がすべて満たされているもののうち、フェーズ・識別子の最初。"""
    ready = [
        t
        for t in doing
        if t.parent == ident
        and not t.started_at
        and not t.blocked
        and not approval_checks.unmet_predecessors(t, pool)
    ]
    ready.sort(key=lambda t: (t.phase or 0, t.ticket))
    return ready[0].ticket if ready else ""


def unsent_of(path: str, branch: str) -> str:
    """origin に送っていないものの言い方。無ければ空。

    手元のリモート追跡の ref と比べる（ネットワークには出ない）。
    ref が無ければ、一度も送っていない。
    承認済みチケットの未コミットは、`ticket status` が置き場のファイルごとに言う。
    """
    if not branch:
        return ""
    ref = f"refs/remotes/origin/{branch}"
    done = gitcmd.run(path, ["rev-parse", "--verify", "--quiet", ref], TIMEOUT_SECONDS)
    if done.failure:
        return ""
    if done.code != 0:
        return "一度も送っていない"
    counted = gitcmd.run(path, ["rev-list", "--count", f"{ref}..HEAD"], TIMEOUT_SECONDS)
    if not counted.ok:
        return ""
    count = counted.out.strip()
    return f"{count} コミット" if count.isdigit() and int(count) > 0 else ""


def divergence(path: str, branch: str) -> tuple[int, int] | None:
    """手元の先頭と手元のリモート追跡の ref の差。(未送信, 遅れ)。ref が無い・読めなければ None。

    ネットワークには出ない。最後に取ってきた時点の origin との差で、古いかもしれない。
    """
    if not branch:
        return None
    ref = f"refs/remotes/origin/{branch}"
    done = gitcmd.run(
        path, ["rev-list", "--left-right", "--count", f"HEAD...{ref}"], TIMEOUT_SECONDS
    )
    if not done.ok:
        return None
    parts = done.out.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def remote_info(path: str) -> dict[str, object]:
    """ボードの親のカードに出す、リモートとの差（`ccnavi --explain --json` の `remote`）。

    `branch` は親のワークツリーが今いるブランチ。
    `ahead` は未送信のコミット数、`behind` は遅れ（取り込んでいないコミット数）。
    どちらも最後に取ってきた origin との差で、読めなければ null。
    MR の番号は親チケットの `mr:`（ボードが別に持つ）。
    """
    branch = tree.branch_of(path) or ""
    diff = divergence(path, branch)
    return {
        "branch": branch,
        "ahead": diff[0] if diff else None,
        "behind": diff[1] if diff else None,
    }


def line(row: Row) -> str:
    """親 1 本を 1 行で言う。"""
    return (
        f"- 親 {row.ident}"
        f"  ブランチ {row.branch or NO_BRANCH}"
        f"  MR {'!' + row.mr if row.mr else 'なし'}"
        f"  次の子 {row.next_child or 'なし'}"
        f"  未送信 {row.unsent + 'あり' if row.unsent else 'なし'}"
    )


def child_states(
    root: str,
    row: Row,
    doing: list[ticket_model.Ticket],
    review: list[ticket_model.Ticket],
) -> list[tuple[str, str]]:
    """親の子のうち、ワークツリーがあるものの (識別子, 状態)。

    状態は合流済み・未合流・未コミットあり。
    未コミットがあれば「未コミットあり」（作業が残っているので、合流の有無より先に言う）。
    そうでなければ、子のブランチの先頭が親のワークツリーの HEAD の祖先なら「合流済み」、
    でなければ「未合流」。
    読めなければ「未合流」と言う（合流済みと言い切らない側）。
    """
    out = []
    seen = set()
    for t in sorted([*doing, *review], key=lambda x: (x.phase or 0, x.ticket)):
        if t.parent != row.ident or t.ticket in seen:
            continue
        seen.add(t.ticket)
        path = tree.worktree_path(root, t.ticket)
        if not os.path.isdir(path):
            continue
        out.append((t.ticket, child_state(path, tree.branch_of(path) or t.ticket, row.path)))
    return out


def child_state(path: str, branch: str, parent_path: str) -> str:
    dirty = gitcmd.run(path, ["status", "--porcelain"], TIMEOUT_SECONDS)
    if dirty.ok and dirty.out.strip():
        return CHILD_DIRTY
    merged = gitcmd.run(
        parent_path,
        ["merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD"],
        TIMEOUT_SECONDS,
    )
    return CHILD_MERGED if merged.ok else CHILD_UNMERGED


def session_text(conf: settings.Settings, root: str) -> str:
    """セッション開始で渡す、このPCの作業中の親の一覧。無ければ空。

    例外は呼ぶ側が握り、セッションの開始は止めない。
    """
    if not conf.tickets_enabled:
        return ""
    raw = approval.read_raw(conf, root)
    doing, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    rows = collect(conf, root, doing, review, closed, proposals)
    if not rows:
        return ""
    lines = ["[ccnavi] このPCで作業中の親:"]
    lines += [line(r) for r in rows]
    return "\n".join(lines)
