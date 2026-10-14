"""このPCで作業中の親の一覧（セッション開始の表示と `ticket status` の頭）。

親 1 本を 1 行で言う。親・ブランチ・MR・次の子・最終取り込み時刻・未送信の有無。
`ticket status`（引数なし）はさらに、親ごとに子のワークツリーの状態
（合流済み・未合流・未コミットあり）を言う。

読むのは手元のファイルと git の ref だけで、ネットワークには出ない。「最終取り込み」は
`ccnavi-sync.sh` が取り込み状態に書いた時刻で、取り込み状態が無い親は「リモート未確認」と言う
（一度もリモートと突き合わせていない）。MR の番号は親チケットの `mr:`、無ければ取り込み状態の `mr`
（`ccnavi-start.sh` が保存する）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from ..infra import gitcmd, settings, tree
from . import approval, approval_checks, syncstate, ticket_model

TIMEOUT_SECONDS = 5.0

UNCHECKED = "リモート未確認"
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
    fetched_at: int = 0
    unsent: str = ""
    project: str = ""

    def synced(self) -> str:
        return sync_label(self.fetched_at)


def sync_label(fetched_at: int) -> str:
    """最終取り込みの時刻（現地時刻）。取り込み状態が無ければ「リモート未確認」。"""
    if not fetched_at:
        return UNCHECKED
    try:
        return datetime.fromtimestamp(fetched_at).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return UNCHECKED


def record_of(conf: settings.Settings, ident: str, project: str = "") -> syncstate.Family | None:
    """この親の取り込み状態。無い・読めなければ None。"""
    found = syncstate.family(conf.state, syncstate.repo_key(project), ident)
    return found if found is not None and not found.broken else None


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
    # 承認と lint と同じ引き方にする（閉じた別の親子の先行は統合先の done/ と取り込み状態で読む）。
    approval_checks.align_imported(conf, root, pool)
    rows = []
    for ident in sorted(parents):
        path = tree.worktree_path(root, ident)
        if not tree.exact_name(root, ident) or not os.path.isdir(path):
            continue
        parent = parents[ident]
        branch = tree.branch_of(path) or ""
        record = record_of(conf, ident, parent.project)
        mr = str(parent.mr) if parent.mr else (record.mr if record else "")
        fetched = int(record.fetched_at) if record and record.fetched_at else 0
        rows.append(
            Row(
                ident=ident,
                path=path,
                branch=branch,
                mr=mr,
                next_child=next_child(ident, doing, pool),
                fetched_at=fetched,
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
    承認済みチケットの未コミットは、ここでは数えない
    （`ticket status` が置き場のファイルごとに言う）。
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


def remote_info(
    conf: settings.Settings, ident: str, path: str, project: str = ""
) -> dict[str, object]:
    """ボードの親のカードに出す、リモートとの差（`ccnavi --explain --json` の `remote`）。

    `branch` は親のワークツリーが今いるブランチ。
    `ahead` は未送信のコミット数、`behind` は遅れ（取り込んでいないコミット数）。
    どちらも最後に取ってきた origin との差で、読めなければ null。
    `fetched_at` は最終同期（UNIX 秒。取り込み状態が無ければ 0 で「リモート未確認」）。
    `mr` は取り込み状態の MR の番号（親チケットの `mr:` はボードが別に持つ）。
    """
    branch = tree.branch_of(path) or ""
    record = record_of(conf, ident, project)
    diff = divergence(path, branch)
    return {
        "branch": branch,
        "ahead": diff[0] if diff else None,
        "behind": diff[1] if diff else None,
        "fetched_at": int(record.fetched_at) if record and record.fetched_at else 0,
        "mr": int(record.mr) if record and record.mr else None,
    }


def line(row: Row) -> str:
    """親 1 本を 1 行で言う。"""
    return (
        f"- 親 {row.ident}"
        f"  ブランチ {row.branch or NO_BRANCH}"
        f"  MR {'!' + row.mr if row.mr else 'なし'}"
        f"  次の子 {row.next_child or 'なし'}"
        f"  最終取り込み {row.synced()}"
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
        if not tree.exact_name(root, t.ticket) or not os.path.isdir(path):
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

    読めなくても開始は止めない（例外は呼ぶ側が握る）。
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
    if any(r.fetched_at == 0 for r in rows):
        sync = settings.script_command(root, "ccnavi-sync.sh")
        lines.append(f"（リモート未確認の親は '{sync} <親>' で取り込むと最終取り込みの時刻が付く）")
    return "\n".join(lines)
