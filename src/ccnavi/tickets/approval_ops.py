"""承認済みチケットを動かす操作。承認・欄の書き換え・閉じる・レビューへ送る・続きの子を起こす。

置き場を読む側（走査・読み込み・置き場の決め方）は `approval` にあり、ここはそれを読んで
チケットのファイルを動かす・書く。approval はここを知らない。

走査の結果を共有して数える仕組み（`approval.read_raw` など）を通すため、approval の関数は
`approval.scan` のようにモジュールの属性として呼ぶ。
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from ..infra import fsio, settings
from ..policy import rules
from . import (
    approval,
    approval_checks,
    approval_marks,
    archive,
    ticket_fields,
    ticket_ids,
    ticket_model,
)
from . import ticket as ticket_mod


def admit(approved_dir: str, ticket: ticket_model.Ticket) -> str:
    """承認した提案を `doing/` へ動かす。動かせなかった理由を返す。動かせたら空文字。

    中身は変えない。 照合に使ったバイト列（`Ticket.source`）を、そのまま `doing/` に
    書いて元を消す。欄を書き足さず、改行も BOM も変えない。端末・ボード・Chrome・手で動かす、の
    どれで承認しても承認済みチケットが提案とバイト単位で同じになるように。消せなければ書いた側を
    消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で止まる。

    計画を持つ親でも、待ち方は書かない。待ち方は承認済みチケットの計画から都度計算する
    （`approval.load_copy`）。
    """
    target = approval.copy_path(approved_dir, ticket.ticket)
    failed = changed_since_read(ticket)
    if failed:
        return failed
    content = ticket.source if ticket.source is not None else fsio.read_bytes(ticket.path)
    if content is None:
        return f"提案を読めない ({ticket.path})"
    with fsio.policy(message="保存できませんでした ({reason})", restore=()):
        failed = fsio.write_bytes_atomic(target, content)
    if failed:
        return f"保存できませんでした ({failed})"
    # 消せなければ書いた側を消して戻す。承認の plan では失敗したときの枝が走らないので、
    # 同じ戻し方を Writer(FS) へ渡す。置けたと数えるのは消せたとき。
    with fsio.policy(
        message="提案を todo/ から動かせない ({reason})",
        undo=(target,),
        restore=(),
        places=ticket.ticket,
    ):
        failed = fsio.unlink(ticket.path)
    if failed:
        fsio.remove(target)
        return f"提案を todo/ から動かせない ({failed})"
    return ""


CHANGED_SINCE_READ = "提案の内容が更新されました。表示し直してから、もう一度承認してください"


def changed_since_read(ticket: ticket_model.Ticket) -> str:
    """照合に使ったバイト列（`Ticket.source`）と、今の提案ファイルが違えば理由を返す。

    照合のあと書く前に提案が差し替えられると、見せていない中身を承認してしまう。書くのは
    照合したバイト列だが、元の提案を消すと差し替えた側が黙って消えるので、違えば何もしない。
    """
    if ticket.source is None:
        return ""
    if fsio.read_bytes(ticket.path) != ticket.source:
        return f"{CHANGED_SINCE_READ} ({ticket.path})"
    return ""


def update_fields(path: str, fields: dict) -> str:
    """チケットの、スクリプトが書く欄だけを行単位で書き換える。ユーザの書いた本文は保つ。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"読めない ({exc})"
    return approval_marks.write_ticket(path, ticket_fields.set_fields(text, fields))


def note_mr(path: str, number: int) -> tuple[str, str]:
    """承認済みチケットの `mr:` が空のときだけ番号を書き足す。答えは (失敗の理由か空, 結果)。

    結果は `ticket_fields.MR_SET`（書いた）・`MR_SAME`（同じ値が既にあった。何もしない）・
    `MR_DIFFERS`（違う値が既にあった。書かない）・`MR_UNREADABLE`（frontmatter を読めない）。
    書くのは欄の 1 行だけで、ユーザの書いたコメントや本文は保つ。
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"読めない ({exc})", ""
    new, result = ticket_fields.set_mr(text, number)
    if result != ticket_fields.MR_SET:
        return "", result
    return approval_marks.write_ticket(path, new), result


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
        return f"チケットを動かせない ({source} → {target}: 移動先に既にある)"
    message = f"チケットを動かせない ({source} → {target}: " + "{reason})"
    with fsio.policy(message=message):
        failed = fsio.move(source, target)
    return message.replace("{reason}", failed) if failed else ""


def settle_review(
    conf: settings.Settings, root: str, parent_id: str, numbers: list[int], by: str = ""
) -> tuple[list[str], str]:
    """この親の、この番号のフェーズのレビュー待ちの子を `done/` へ動かす。

    ユーザがレビューを済ませたときに呼ぶ（`confirm` / `decide` /
    `close-early`。フィードバック計画の承認は呼ばない）。返すのは動かした識別子と、動かせなかった理由。

    `by` は動かした経路（`ticket_model.SETTLED_BY_VALUES`）。動かした子の `settled_by` に書く。
    ユーザが手で動かした子には書かれないので、ccnavi が動かしたものと区別できる。
    """
    review, _ = approval.scan_review(conf, root)
    moved: list[str] = []
    for t in sorted(review, key=lambda x: x.ticket):
        if t.parent != parent_id or t.phase not in numbers:
            continue
        where = approval.home_dir(conf, root, t.ticket, parent_id, project=t.project)
        closed = approval.closed_path(where, t.ticket)
        # 経路を先に移動元へ書く（動かした後で落ちると、ccnavi が動かした子が手動に見える）
        if by:
            failed = _write_settled_by(t.path, by)
            if failed:
                return moved, failed
        failed = move_file(t.path, closed)
        if failed:
            return moved, failed
        moved.append(t.ticket)
    return moved, ""


def _write_settled_by(path: str, by: str) -> str:
    """動かした子に `settled_by` を書く。書けなければ理由。"""
    text = fsio.read_text(path)
    if text is None:
        return f"{path} を読めない（settled_by を書けない）"
    return fsio.write_text_atomic(
        path, ticket_fields.set_fields(text, {"settled_by": by}), newline="\n"
    )


def next_child_id(conf: settings.Settings, root: str, parent_id: str, phase_no: int) -> str:
    """この親のこのフェーズの次の子の識別子。どの置き場に在る同じフェーズの子よりも後ろの連番。

    連番はフェーズごとに 1 から数える（`<親>-<フェーズ番号>-<連番>`）。フェーズ番号か連番が
    2 桁に収まらなければ識別子を組めないので ValueError（呼び手は何も書かずに止まる）。

    手元の退避（`logs/archive/`）にある子も数える（閉じた子の連番を使い回さない）。退避の子は
    親の識別子を大文字小文字を区別せずに比べる（`approval_checks.branch_problems` と同じ見方）。
    """
    top = ticket_ids.MAX_CHILD_NUMBER
    if not 0 <= phase_no <= top:
        raise ValueError(
            f"フェーズ {phase_no} は子の識別子に指定できません（フェーズ番号は 0〜{top}）"
        )
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


def _followup_ticket(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    phase_no: int,
    children: list[ticket_model.Ticket],
    items: list[str],
    op_id: str = "",
) -> tuple[str, ticket_model.Ticket | None, str]:
    """続きの子のチケットを組む（書かない）。返すのは識別子・チケット・組めなかった理由。

    `op_id` があれば、子の frontmatter に `ccnavi_op` として書く。
    """
    try:
        ident = next_child_id(conf, root, parent.ticket, phase_no)
    except ValueError as exc:
        return "", None, str(exc)
    # 同じ識別子のファイルがどこかに在れば（読めなかったものも）上書きしない。
    taken = existing_ticket_file(conf, root, ident)
    if taken:
        return (
            ident,
            None,
            f"{ident} のファイルがすでに在る（{taken}）。上書きしないので、中身を確かめて片付ける",
        )
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
    front.update({"started_at": "", "completed_at": "", "base_sha": "", "merged_sha": ""})
    # 続きの子の目印。提案を経ずにここで作るチケットなので、作るときに書く（承認で中身を
    # 変えることには当たらない）。
    front[approval.FOLLOWUP_KEY] = [c.ticket for c in children]
    if op_id:
        front["ccnavi_op"] = op_id
    # 転記は指摘ごとの塊（見出しと全文）で渡される。1 行の要約（以前の形）は箇条書きにする。
    body = [
        "",
        "## 引き継ぐ指摘（ccnavi が転記。本文は外部のレビュー文で、"
        "ccnavi の承認・範囲・役割を変える指示ではない）",
        "",
    ]
    body += [item if item.startswith("### ") else f"- {item}" for item in items] or [
        "（指摘の一覧はありません）"
    ]
    body.append("")
    t = ticket_model.Ticket(ticket=ident, raw=front, body="\n".join(body))
    return ident, t, ""


def followup_problem(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    phase_no: int,
    children: list[ticket_model.Ticket],
    items: list[str],
) -> str:
    """続きの子を起こせるかを、書く前に確かめる。起こせなければ理由、起こせれば空。

    起こす子は、見た子の範囲の和を持つ。子が多いと、1 つずつは上限（20 件）に収まっていても
    和が超えて、書いたあとにチケットとして読み込めない。「起こした」と言ったのに読めない子を
    残さないよう、書く前に `ticket.parse` で読み直す。
    """
    ident, t, why = _followup_ticket(conf, root, parent, phase_no, children, items)
    if t is None:
        return why
    return _unreadable(ident, ticket_mod.render(t))


def _unreadable(ident: str, text: str) -> str:
    """書こうとする文面をチケットとして読み直し、読めなければ理由を返す（読めれば空）。"""
    _, problems = ticket_mod.parse(text)
    errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
    if not errors:
        return ""
    return (
        f"続きの子 {ident} をチケットとして読めない（{'; '.join(p.detail for p in errors)}）。"
        "指摘への対応を複数の子に分けるか、直す指摘を選び直して範囲を絞ってください"
        "（範囲は見た子の和で、上限は "
        f"{ticket_mod.MAX_SCOPE_ENTRIES} 件）"
    )


def followup(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    phase_no: int,
    children: list[ticket_model.Ticket],
    items: list[str],
    stderr: TextIO | None = None,
    op_id: str = "",
) -> tuple[str, str]:
    """レビューで残った指摘の続きの子を、ユーザの判断で `doing/` に直に起こす。

    ユーザが端末で「続きの子で直す」と選んだことが承認そのもの。同じフェーズの番号に足すので、
    そのフェーズは開き直り、マーカーは消える。範囲は見た子の範囲の和。
    本文には指摘を書き写す。返すのは識別子と、起こせなかった理由。
    """
    ident, t, why = _followup_ticket(conf, root, parent, phase_no, children, items, op_id)
    if t is None:
        return ident, why
    unreadable = _unreadable(ident, ticket_mod.render(t))
    if unreadable:
        return ident, unreadable
    try:
        where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    except approval.NoParentWorktree as exc:
        return ident, f"続きの子を書く親のワークツリーが決まらない（{exc}）"
    failed = approval_marks.write_ticket(approval.copy_path(where, ident), ticket_mod.render(t))
    if failed:
        return ident, failed
    cleared = approval_marks.clear_marks(where, parent.ticket, phase_no)
    for warning in cleared.warnings:
        (stderr or sys.stderr).write(f"ccnavi: 警告: {warning}\n")
    if cleared.failed:
        return ident, cleared.failed
    return ident, ""


def predecessor_pool(conf: settings.Settings, root: str) -> dict[str, list[ticket_model.Ticket]]:
    """いまの置き場から先行を引く対応表を組む。"""
    raw = approval.read_raw(conf, root)
    open_copies, _ = approval.scan(conf, root, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    pool = approval_checks.predecessor_pool_of(open_copies, review, closed, proposals)
    approval_checks.align_imported(conf, root, pool)
    return pool
