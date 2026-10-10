"""承認済みチケットを動かす操作。承認・欄の書き換え・閉じる・レビューへ送る・続きの子を起こす。

置き場を読む側（走査・読み込み・置き場の決め方）は `approval` にあり、ここはそれを読んで
チケットのファイルを動かす・書く。approval はここを知らない。

走査の結果を共有して数える仕組み（`approval.read_raw` など）を通すため、approval の関数は
`approval.scan` のようにモジュールの属性として呼ぶ。
"""

from __future__ import annotations

import os

from ..infra import fsio, settings
from ..policy import rules
from . import (
    approval,
    approval_checks,
    approval_marks,
    archive,
    flow,
    history,
    ticket_fields,
    ticket_ids,
    ticket_model,
)
from . import ticket as ticket_mod


def admit(
    approved_dir: str,
    ticket: ticket_model.Ticket,
    source_tree: str,
    wf: ticket_model.Workflow | None = None,
) -> str:
    """承認した提案を `doing/` へ動かす。動かせなかった理由を返す。動かせたら空文字。

    中身は変えない。 提案をバイト列のまま読み（`fsio.read_bytes`）、同じバイト列を `doing/` に
    書いて元を消す。欄を書き足さず、改行も BOM も変えない。端末・ボード・Chrome・手で動かす、の
    どれで承認しても承認済みチケットが提案とバイト単位で同じになるように。消せなければ書いた側を
    消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で止まる。

    `wf` は計画を持つ親の待ち方。`phases/<親>/workflow.yml` に固定する（チケットには書かない）。
    待ち方は提案を動かす前に書き、そのあとの段で失敗したら前の中身へ戻す。承認済みチケットだけが
    置かれて待ち方が無い形は、手で動かした承認と同じに一直線で読まれ、取り下げの検査も通って
    しまうので、待ち方だけが残る側（承認済みチケットが無いので効かない）にしておく。
    `source_tree` は提案が乗っていたブランチの名前で、状態の履歴に残す。
    """
    target = approval.copy_path(approved_dir, ticket.ticket)
    content = fsio.read_bytes(ticket.path)
    if content is None:
        return f"提案を読めない ({ticket.path})"
    restore: tuple[tuple[str, bytes | None], ...] = ()
    if wf is not None:
        held = approval.workflow_path(approved_dir, ticket.ticket)
        restore = ((held, fsio.read_bytes(held)),)
        failed = approval.write_workflow(approved_dir, ticket.ticket, wf)
        if failed:
            return failed
    with fsio.policy(message="書けない ({reason})", restore=restore):
        failed = fsio.write_bytes_atomic(target, content)
    if failed:
        fsio.put_back(restore)
        return f"書けない ({failed})"
    # 消せなければ書いた側を消して戻す。承認の plan では失敗したときの枝が走らないので、
    # 同じ戻し方を Writer(FS) へ渡す。置けたと数えるのは消せたとき。
    with fsio.policy(
        message="提案を todo/ から動かせない ({reason})",
        undo=(target,),
        restore=restore,
        places=ticket.ticket,
    ):
        failed = fsio.unlink(ticket.path)
    if failed:
        fsio.remove(target)
        fsio.put_back(restore)
        return f"提案を todo/ から動かせない ({failed})"
    history.note(
        approved_dir,
        ticket.ticket,
        history.KIND_APPROVED,
        ticket_model.TODO,
        ticket_model.DOING,
        tree=source_tree,
    )
    return ""


def update_fields(path: str, fields: dict) -> str:
    """チケットの、スクリプトが書く欄だけを行単位で書き換える。ユーザの書いた本文は保つ。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"読めない ({exc})"
    return approval_marks.write_ticket(path, ticket_fields.set_fields(text, fields))


def close_copy(approved_dir: str, ticket_id: str) -> str:
    """承認済みチケットを `doing/` から `done/` へ動かす。"""
    return move_file(
        approval.copy_path(approved_dir, ticket_id), approval.closed_path(approved_dir, ticket_id)
    )


def to_review(approved_dir: str, tree_root: str, tickets_rel: str, ticket_id: str) -> str:
    """承認済みチケットを `doing/` から提案の置き場の `review/` へ動かす。"""
    return move_file(
        approval.copy_path(approved_dir, ticket_id),
        approval.review_path(tree_root, tickets_rel, ticket_id),
    )


def carry_flow(
    conf: settings.Settings, root: str, proposal: ticket_model.Ticket, approved_dir: str
) -> list[str]:
    """承認した子のフローを、提案のツリーから承認済みチケットのツリーへ動かす。知らせる行を返す。

    フローの置き場は承認済みチケットと同じツリー（設計 9.3.1）。ユーザは承認の前に、提案が在る
    ツリーの置き場へボードで保存する。承認で子が別のツリー（親のワークツリーなど）へ動くと、
    フローだけが元のツリーに残り、読まれなくなる（M-3）。承認はユーザの操作なので、ここで一緒に
    動かす。行き先に違う中身のフローが既に在れば上書きせず、そう言う（元のほうも残す）。
    リンク・ふつうのファイルでないもの・ハードリンクは移さない（`flow.load` と同じ読み方）。
    """
    source_root = proposal.tree_root or root
    source = flow.flow_file(conf, source_root, proposal.ticket)
    target = os.path.normpath(
        os.path.join(approved_dir, flow.FLOWS_DIR, f"{proposal.ticket}{flow.SUFFIX}")
    )
    try:
        if not fsio.lexists(source):
            return []
        if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(target)):
            return []
    except (OSError, ValueError):
        return []
    raw, why = flow.read_bytes(source, source_root)
    if raw is None:
        return [
            f"{proposal.ticket} のフロー {source} を移さなかった: {why}。"
            "ユーザが確かめて置き直してください"
        ]
    if fsio.lexists(target):
        held, _ = flow.read_bytes(target)
        if held != raw:
            return [
                f"{proposal.ticket} のフローを {target} へ移さなかった: 行き先に違う中身のフローが"
                f"既に在る（上書きしない）。{source} と見比べて、ユーザが 1 本に決める"
            ]
        fsio.remove(source)
        return []
    # 失敗したときの行は承認の plan でも同じものを出せるよう、書き込みにつける（`FAIL_LINE`）。
    cannot = f"{proposal.ticket} のフローを {target} へ移せない ({{reason}})。{source} に残っている"
    with fsio.policy(message=cannot):
        # 途中で中断されても書きかけを残さない書き方で置く。在るかを確かめてから置くまでの間に
        # ボードの保存が割り込むと上書きしうるが、書きかけのフローを残すよりよいと採った。
        failed = fsio.write_new_durable(target, raw)
    if failed:
        return [cannot.replace("{reason}", failed)]
    kept = (
        f"{proposal.ticket} のフローを {target} へコピーした。"
        f"元の {source} は消せなかった ({{reason}})"
    )
    with fsio.policy(message=kept):
        failed = fsio.unlink(source)
    if failed:
        return [kept.replace("{reason}", failed)]
    return [f"{proposal.ticket} のフローを {source} から {target} へ動かした"]


def move_file(source: str, target: str) -> str:
    """チケットを置き場から置き場へ動かす。動かせなかった理由を返す。

    行き先に同じ名前が既に在れば動かさない。何も出さずに上書きすると、閉じた側の記録
    （取り消しの欄など）が消える。同じ識別子が 2 つ在るのは `--lint` が名指しする。

    同じファイルシステムの中なら rename 1 回で済む。またぐとき（EXDEV）だけコピーして消す。
    消せなければコピーした側を消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で
    止まる（`admit` と同じ）。Windows は開かれているファイルを消させないので、現実に起きる。

    コピーして消す処理に回すのは EXDEV に限る。rename が他の理由（元が無い、など）で失敗した
    ときまで回すと、コピーできずに戻す処理が、その間に別のプロセスが置いた行き先を消す。
    """
    if fsio.exists(target):
        return f"チケットを動かせない ({source} → {target}: 行き先に既に在る)"
    message = f"チケットを動かせない ({source} → {target}: " + "{reason})"
    with fsio.policy(message=message):
        failed = fsio.move(source, target)
    return message.replace("{reason}", failed) if failed else ""


def settle_review(
    conf: settings.Settings, root: str, parent_id: str, numbers: list[int]
) -> tuple[list[str], str]:
    """この親の、この番号のフェーズのレビュー待ちの子を `done/` へ動かす。

    ユーザがレビューを済ませたときに呼ぶ（`confirm` / `decide` / `--reviewed --chat` /
    `close-early` と、フィードバック計画の承認）。返すのは動かした識別子と、動かせなかった理由。
    """
    review, _ = approval.scan_review(conf, root)
    moved: list[str] = []
    for t in sorted(review, key=lambda x: x.ticket):
        if t.parent != parent_id or t.phase not in numbers:
            continue
        where = approval.home_dir(conf, root, t.ticket, parent_id, project=t.project)
        failed = move_file(t.path, approval.closed_path(where, t.ticket))
        if failed:
            return moved, failed
        moved.append(t.ticket)
        history.note(
            where,
            t.ticket,
            history.KIND_SETTLED,
            ticket_model.REVIEW,
            ticket_model.DONE,
            phase=t.phase,
        )
    return moved, ""


def next_child_id(conf: settings.Settings, root: str, parent_id: str, phase_no: int) -> str:
    """この親のこのフェーズの次の子の識別子。どの置き場に在る同じフェーズの子よりも後ろの連番。

    連番はフェーズごとに 1 から数える（`<親>-<フェーズ番号>-<連番>`）。フェーズ番号か連番が
    2 桁に収まらなければ識別子を組めないので ValueError（呼び手は何も書かずに止まる）。

    手元の退避（`logs/archive/`）にある子も数える（閉じた子の連番を使い回さない）。退避の子は
    親の識別子を大文字小文字を区別せずに比べる（`integration_problems` と同じ見方）。
    """
    top = ticket_ids.MAX_CHILD_NUMBER
    if not 0 <= phase_no <= top:
        raise ValueError(f"フェーズ {phase_no} は子の識別子に書けない（フェーズ番号は 0〜{top}）")
    seen = approval.all_tickets(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    # 退避は親のリポジトリ（プロジェクト）のものだけを見る。親が置き場に無ければ全部を見る。
    projects = {t.project for t in seen + proposals if t.ticket == parent_id}
    archived: set[str] = set()
    for project in sorted(projects) if projects else [None]:
        archived |= archive.ids(root, project)
    used = 0
    for t in seen + proposals:
        m = ticket_ids.child_pattern().match(t.ticket)
        if m and m.group("parent") == parent_id and int(m.group("phase")) == phase_no:
            used = max(used, int(m.group("seq")))
    for ident in sorted(archived):
        m = ticket_ids.child_pattern().match(ident)
        if (
            m
            and archive.same_id(m.group("parent"), parent_id)
            and int(m.group("phase")) == phase_no
        ):
            used = max(used, int(m.group("seq")))
    if used >= top:
        raise ValueError(
            f"親 {parent_id} のフェーズ {phase_no} の子が連番 {top} まで埋まっている。"
            "子の識別子の連番は 2 桁なので、続きの子を起こせない"
        )
    return ticket_ids.child_id(parent_id, phase_no, used + 1)


def existing_ticket_file(conf: settings.Settings, root: str, ident: str) -> str:
    """この識別子のファイルが、どこかの置き場にすでに在ればそのパス。無ければ空。

    読めないファイル（不正な frontmatter など）も名前で拾う。大文字小文字は区別しない
    （区別しないファイルシステムでは同じファイルになる）。見るのは全ツリーの承認済みの
    `doing/` `done/` と、提案の `todo/` `review/`。
    """
    want = (ident + ".md").casefold()
    for t in approval.trees(conf, root):
        approved = settings.approved_dir(conf, t.root)
        proposals = os.path.join(t.root, conf.tickets.replace("/", os.sep))
        places = [
            os.path.join(approved, approval.DOING_DIR),
            os.path.join(approved, approval.DONE_DIR),
        ]
        places += [os.path.join(proposals, state) for state in ticket_model.STATES]
        for place in places:
            try:
                names = os.listdir(place)
            except OSError:
                continue
            for name in names:
                if name.casefold() == want:
                    return os.path.join(place, name)
    return ""


def followup(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    phase_no: int,
    children: list[ticket_model.Ticket],
    items: list[str],
) -> tuple[str, str]:
    """レビューで残った指摘の続きの子を、ユーザの判断で `doing/` に直に起こす。

    ユーザが端末で「続きの子で直す」と選んだことが承認そのもの。同じフェーズの番号に足すので、
    そのフェーズは開き直り、マーカーは消える（REQ-TKT-21）。範囲は見た子の範囲の和。
    本文には指摘を書き写す。返すのは識別子と、起こせなかった理由。
    """
    try:
        ident = next_child_id(conf, root, parent.ticket, phase_no)
    except ValueError as exc:
        return "", str(exc)
    # 同じ識別子のファイルがどこかに在れば（読めなかったものも）上書きしない。
    taken = existing_ticket_file(conf, root, ident)
    if taken:
        return (
            ident,
            f"{ident} のファイルがすでに在る（{taken}）。上書きしないので、中身を確かめて片付ける",
        )
    where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    front: dict = {
        "version": ticket_model.VERSION,
        "ticket": ident,
        "parent": parent.ticket,
        "phase": phase_no,
    }
    if parent.project:
        front["project"] = parent.project
    # 先行は、見た子のうち取り消しでないもの。取り消した子は満たせないので、先行に入れると
    # 続きの子が着手できなくなる（満たすのは `done/` に在って取り消しでないものだけ）。
    # 範囲の和には入れる（見たのは同じフェーズの全部）。
    front["predecessors"] = [c.ticket for c in children if not c.cancelled_at]
    front["human_review"] = {"required": True, "reason": "レビューで残った指摘への対応"}
    front["title"] = f"フェーズ {phase_no} のレビューの指摘に応える"
    front["rationale"] = (
        f"フェーズ {phase_no} のレビューで残った指摘に応える。"
        "ユーザが端末で起こした続きの子で、承認はその判断で済んでいる。\n"
    )
    for name in rules.SECTIONS:
        entries = []
        seen: set[tuple] = set()
        for c in children:
            for raw in c.raw.get(name) or []:
                key = (
                    tuple(sorted((str(k), str(v)) for k, v in raw.items()))
                    if isinstance(raw, dict)
                    else (str(raw),)
                )
                if key not in seen:
                    seen.add(key)
                    entries.append(raw)
        if entries:
            front[name] = entries
    front.update({"started_at": "", "completed_at": "", "base_sha": ""})
    # 続きの子の目印。提案を経ずにここで作るチケットなので、作るときに書く（承認で中身を
    # 変えることには当たらない）。時刻は状態の履歴の `raised` に残る。
    front[approval.FOLLOWUP_KEY] = [c.ticket for c in children]
    body = ["", "## 引き継ぐ指摘", ""]
    body += [f"- {item}" for item in items] or ["（指摘の一覧は無い）"]
    body.append("")
    t = ticket_model.Ticket(ticket=ident, raw=front, body="\n".join(body))
    failed = approval_marks.write_ticket(approval.copy_path(where, ident), ticket_mod.render(t))
    if failed:
        return ident, failed
    history.note(
        where,
        ident,
        history.KIND_RAISED,
        None,
        ticket_model.DOING,
        phase=phase_no,
        followup_of=[c.ticket for c in children],
    )
    cleared = approval_marks.clear_marks(where, parent.ticket, phase_no)
    for warning in cleared.warnings:
        history.failed_to_write(warning)
    if cleared.failed:
        return ident, cleared.failed
    return ident, ""


def predecessor_pool(conf: settings.Settings, root: str) -> dict[str, list[ticket_model.Ticket]]:
    """いまの置き場から先行を引く対応表を組む。"""
    open_copies, _ = approval.scan(conf, root)
    review, _ = approval.scan_review(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    pool = approval_checks.predecessor_pool_of(open_copies, review, closed, proposals, root)
    approval_checks.align_imported(conf, root, pool)
    return pool
