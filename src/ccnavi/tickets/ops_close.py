"""チケットを引いて、動かしてよいかを検査する。`ops.py` の `start` / `finish` / `cancel` が使う。

置き場を全部引いて 1 つに決める（`_find`）、取り込み済みの親子のチケットが決まらない・閉じて
いるときに止める（`family_stopped`）、子の着手の前（親の着手・先行）と終了の前（親を閉じられる・
成果物が揃う）の検査。`close_problems` は `review ready` と Stop の促しも同じ条件で使う。
ここは置き場を読むだけで動かさない。
"""

from __future__ import annotations

from typing import TextIO

from ..infra import gitcmd, settings, tree
from . import (
    approval,
    approval_checks,
    approval_marks,
    configsync,
    phase,
    syncstate,
    ticket_model,
)
from . import ticket as ticket_mod

# git を読むときの待ち時間（秒）。`ops_stop.py` の git の読み取りも使う。
TIMEOUT_SECONDS = 5.0


def _places(
    root: str, conf: settings.Settings, ticket_id: str
) -> tuple[list[ticket_model.Ticket], list[str], list[ticket_mod.Problem]]:
    """この識別子のチケットが在る置き場を全部引く。読めなかった理由と提案の不備も返す。

    本物とするツリーの側だけを読む（approval.scan / ticket.scan のまとめ方）。
    """
    hits: list[ticket_model.Ticket] = []
    copies, notes = approval.scan(conf, root)
    hits += [t for t in copies if t.ticket == ticket_id]
    review, more = approval.scan_review(conf, root)
    hits += [t for t in review if t.ticket == ticket_id]
    closed, _ = approval.scan(conf, root, closed=True)
    hits += [t for t in closed if t.ticket == ticket_id]
    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    # `todo/` は承認の前の状態。承認済みチケット（作業中・レビュー待ち・閉じた）が在れば、同じ
    # 識別子の `todo/` は改版の候補か書き損じで、状態の操作の相手ではない（`--lint` が言う）。
    if not hits:
        hits += [t for t in proposals if t.ticket == ticket_id and t.state == ticket_model.TODO]
    return hits, notes + more, problems


def _where(hits: list[ticket_model.Ticket]) -> str:
    """複数の置き場に在るときに、その在り処を並べた文言。"""
    return ", ".join(f"{t.tree or '(ワークスペースルート)'}:{t.state}" for t in hits)


def _undecided(
    stderr: TextIO,
    head: str,
    hits: list[ticket_model.Ticket],
    root: str = "",
    conf: settings.Settings | None = None,
) -> None:
    """どれが本物か決まらないときの文面。次にすることまで書く。

    「1 つにしてから」だけだと、どのツリー上のチケットも追跡されたファイルなので、受け取った側に
    できることが読めない。本物とする側の決まり方（親のツリー → 元ツリー）と、この場面で
    それが決まらない理由を名指しする。取り込み済みの親子のチケットでは、本物とする側が親のブランチに決まって
    いるので、取り込み状態から引いた解き方（`syncstate.guidance`）を出す。
    """
    home = hits[0].parent or hits[0].ticket
    stderr.write(head + f"が複数の場所にある: {_where(hits)}。1 つに決まるまで動かさない\n")
    st = approval_checks.family_standing(conf, root, hits[0]) if conf is not None else None
    if st is not None and st.imported:
        stderr.write(
            f"  本物は、親のブランチ {home} のワークツリー（.claude/worktrees/{home}）"
            "上のチケットだけ"
            "（取り込み済みの親子のチケット）。ほかのツリー上のチケットは読まない\n"
        )
        for line in syncstate.guidance(root, st) if st.stop else []:
            stderr.write(f"  {line}\n")
        if not st.stop:
            stderr.write(
                "  親のワークツリーの外のチケットは、親のブランチへ移してコミットしてから消すか、"
                "残ったワークツリーを片付けてから打ち直してください\n"
            )
        return
    stderr.write(
        f"  本物は、親 {home} のワークツリー上のチケット。無ければ元ツリー"
        "（ワークスペースルート、プロジェクトのチケットならそのプロジェクト）上のチケット\n"
    )
    stderr.write(
        "  どちらにも無いか、元ツリーより先の置き場に在るチケットがあると、どれが本物か決まらない。"
        "先に進んだ側を合流させるか、残ったワークツリーを片付けてから打ち直してください\n"
    )


def _find(
    stderr: TextIO, root: str, conf: settings.Settings, ticket_id: str
) -> ticket_model.Ticket | None:
    """この識別子のチケットを、どの置き場に在っても 1 つ引く。`state` に置き場が入る。

    2 つ以上残れば、どれが本物か決まらないので止める。
    """
    hits, notes, problems = _places(root, conf, ticket_id)
    if not hits:
        stderr.write(
            f"ccnavi: チケット {ticket_id} が見つからない"
            f"（{conf.approved}/ と {conf.tickets}/ の下を全ツリーで探した）\n"
        )
        for p in problems:
            stderr.write(f"  {p}\n")
        for note in notes:
            stderr.write(f"  {note}\n")
        return None
    if len(hits) > 1:
        _undecided(stderr, f"ccnavi: {ticket_id} ", hits, root, conf)
        return None
    if family_stopped(stderr, root, conf, hits[0]):
        return None
    return hits[0]


def family_stopped(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """取り込み済みの親子のチケットが決まらない・閉じているなら、言って True。

    その親子のチケットの状態の操作（着手・終了・取り消し・記録・レビューのマーカー）は止める。引いたチケットが
    親のワークツリーの外にしか無いとき（元ツリーに未コミットで残ったチケットなど）も、信頼しないチケットを
    動かさないように止める。取り込み状態の無い親子のチケットには何も言わない（今の動きのまま）。
    """
    st = approval_checks.family_standing(conf, root, found)
    if not st.imported:
        return False
    if st.stop:
        stderr.write(f"ccnavi: {found.ticket}: {st.stop}。この親子のチケットの状態は動かさない\n")
        for line in syncstate.guidance(root, st):
            stderr.write(f"  {line}\n")
        return True
    if st.home is not None and not syncstate.same_tree(found.tree_root, st.home.root):
        stderr.write(
            f"ccnavi: {found.ticket}: チケットが親のブランチ {st.family} のワークツリーの外"
            f"（{found.tree or 'ワークスペースルート'}）にしか無い。"
            "取り込み済みの親子のチケットでは親のブランチ上のチケットだけが本物なので、このチケットは動かさない\n"
            f"  ユーザがそのチケットを親のワークツリー（.claude/worktrees/{st.family}）へ移して"
            "コミットと push をしてから打ち直す\n"
        )
        return True
    return False


def _parent_not_started(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """子に着手してよいか。親が作業中で着手済みでなければ止める（設計 9.6、REQ-TKT-48）。

    親の `start` を飛ばしても途中では何も壊れず、親を閉じるときだけが通らない。壊れない
    ので気付けず、気付くのがいちばん遅い場所になる。親の作業が実際に始まる時点
    （最初の子の着手）で止めれば、いちばん早い場所で言える。

    親は別の置き場に在ることもある（未承認、閉じた）。どれも子に着手してよい状態では
    ないので、そのまま置き場を名指しして止める。
    """
    if not found.is_child:
        return False
    hits, notes, _ = _places(root, conf, found.parent)
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    head = f"ccnavi: {found.ticket} の親 {found.parent} "
    if not hits:
        stderr.write(
            head + f"が見つからない（{conf.approved}/ と {conf.tickets}/ を全ツリーで探した）\n"
        )
        for note in notes:
            stderr.write(f"  {note}\n")
        return True
    if len(hits) > 1:
        _undecided(stderr, head, hits, root, conf)
        return True
    parent = hits[0]
    if parent.state == ticket_model.TODO:
        stderr.write(
            head + "がまだ承認されていない（todo/）。先にユーザに 'ccnavi --agree' を通してもらい、"
            f"'{ticket_sh} start {found.parent}' で着手してください\n"
        )
        return True
    if parent.state != ticket_model.DOING:
        # 置き場だけを言う。`review/` に親があるのは壊れたデータのときだけだが、そこで
        # 「閉じた」と言うと、文面が事実と違う。
        stderr.write(head + f"は作業中ではない（いまは {parent.state}/）。子を足す相手ではない\n")
        return True
    if not parent.started_at:
        stderr.write(
            head + f"が未着手（{parent.state}/）。子より先に親に着手してください。\n"
            f"  '{ticket_sh} start {found.parent}'\n"
        )
        return True
    return False


def _predecessors_unmet(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """子の先行が全部 `done/` に在って取り消しでないか。欠けていれば止めて言う。

    承認でも同じ検査を当てるが、承認のあとに先行が動くこと（ユーザが `done/` から戻す）と、置き場を
    手で動かして承認する進め方があるので、着手の手前でもう一度見る。どの先行が何の
    状態か、どうすればよいかを 1 本ずつ言う。
    """
    unmet = approval_checks.unmet_predecessors(found, approval.predecessor_pool(conf, root))
    if not unmet:
        return False
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    stderr.write(
        f"ccnavi: {found.ticket} の先行が満たされていないので着手しない"
        f"（先行は {conf.approved}/{ticket_model.DONE}/ に在って取り消しでないこと）:\n"
    )
    for p in unmet:
        stderr.write(f"  - 先行 {p.ticket}: {p.label}\n")
    if any(p.waiting for p in unmet):
        stderr.write(
            f"  先行を先に閉じてください（作業中なら '{ticket_sh} finish <先行>'。"
            "レビューが要るならユーザのレビューが済んで done/ に入るまで待つ）\n"
        )
    if any(not p.waiting for p in unmet):
        stderr.write(
            "  取り消した・どこにも無い・自分自身や自分の親・輪になった先行は、待っても満たせない。"
            "複数の場所にある先行は、先に 1 つに決めてください\n"
        )
    stderr.write(
        "  先行が要らないなら、ユーザに承認済みチケットの predecessors から外してもらうか、"
        f"'{ticket_sh} cancel {found.ticket} --reason <理由>' で取り消し、"
        "先行を外した提案を出し直して承認を受けてください\n"
    )
    return True


def _parent_still_busy(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """親を閉じてよいか。開いている子やレビュー待ちのフェーズがある間は閉じさせない。

    親の承認済みチケットが閉じると止めるときの鍵（cwd から引く親）が消え、レビュー要の
    フェーズが終わっていても誰も止めなくなる。
    """
    if found.is_child:
        return False
    problems = close_problems(root, conf, found.ticket)
    for p in problems:
        stderr.write(f"ccnavi: {p}\n")
    return bool(problems)


def close_problems(
    root: str, conf: settings.Settings, parent_id: str, raw: approval.Raw | None = None
) -> list[str]:
    """親を閉じられない理由の一覧。空なら閉じてよい。

    `review ready`（Draft を外す）も同じ条件を見る。閉じてよい状態と、マージに
    進んでよい状態は同じもの。ユーザが close-early で早めに閉じていれば、
    開いている子以外は問わない。ユーザが早めに閉じたあとに残っているものは、
    閉じたときに別の issue へ書き出してある。

    `raw` は呼び手が `approval.read_raw` で読んだ置き場。読んでから置き場のファイルを
    動かしていないときだけ渡す。無ければここで読む。
    """
    copies, _ = approval.scan(conf, root, raw=raw)
    open_children = [t.ticket for t in copies if t.parent == parent_id]
    if open_children:
        return [
            f"{parent_id} には開いている子がある（{', '.join(open_children)}）。"
            "子を先に閉じてください"
        ]
    # 着手で共通レイヤーをコピーした親は、それをユーザに知らせるまで閉じず、
    # Draft も外させない（設計 11.12）。知らせるのは最初のレビュー。レビューの無い親（計画が無い、
    # 全部 `review: none`、早めに閉じた）はそこを通らないので、ユーザが端末で見たことを残させる。
    # 早めに閉じた親でも問うので、この下の早い return より前に置く。
    if configsync.pending(approval.home_dir(conf, root, parent_id, ""), parent_id):
        return [
            f"{parent_id} は着手のときに共通レイヤーで設定を上書きしたが、"
            "まだユーザに知らせていない"
            "（レビューを通っていない）。閉じる前に、ユーザに端末で "
            f"'{settings.script_command(root, 'ccnavi-review.sh')} config-synced {parent_id}' を"
            "打って見てもらってください"
        ]
    if approval_marks.read_parent_mark(
        approval.home_dir(conf, root, parent_id, ""),
        parent_id,
        approval_marks.PARENT_MARK_CLOSE_EARLY,
    ):
        return []
    problems: list[str] = []
    held = phase.held_phase(root, conf, parent_id, raw)
    if held is not None:
        problems.append(
            f"{parent_id} のフェーズ {held.label} は{held.review_label}。レビューを済ませてから"
        )
    closed_copies, _ = approval.scan(conf, root, closed=True, raw=raw)
    copy = approval_checks.by_id(copies + closed_copies).get(parent_id)
    if copy is not None and copy.has_plan:
        if copy.feedback is None:
            problems.append(
                f"{parent_id} はフィードバック計画がまだ。対応が無くても "
                "`feedback: []` を改版で出して承認を受けてから"
            )
        unfinished = [
            p.label for p in phase.phases_of(root, conf, parent_id, raw=raw) if not p.ended
        ]
        if unfinished:
            problems.append(
                f"{parent_id} には終わっていないフェーズがある（{', '.join(unfinished)}）。"
                "全部閉じてから"
            )
    return problems


def _deliverables_missing(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """フェーズの最後の子を閉じる前に、種類の成果物が揃っているか（設計 9.8）。

    在って追跡されていることだけを見る。中身は見ない。空でも在ることは分かるので、
    「調査したことにする」は防げる。
    """
    if not found.is_child or found.phase is None:
        return False
    copies, _ = approval.scan(conf, root)
    parent = approval_checks.by_id(copies).get(found.parent)
    if parent is None or not parent.has_plan:
        return False
    item = parent.item_at(found.phase)
    types = phase.load_types(conf, root, parent.project) or {}
    pt = types.get(item.type) if item is not None else None
    if pt is None or not pt.deliverables:
        return False
    # 同じフェーズに、まだ開いている別の子があれば、成果物はその子が出すかもしれない。
    for ph in phase.phases_of(root, conf, found.parent):
        if ph.number != found.phase:
            continue
        others = [
            t.ticket
            for t in ph.tickets
            if t.ticket != found.ticket and ph.states.get(t.ticket) == ticket_model.DOING
        ]
        if others:
            return False
    worktree = tree.worktree_path(root, found.parent)
    child_tree = tree.worktree_path(root, found.ticket)
    missing = [
        g for g in pt.deliverables if not _tracked(worktree, g) and not _tracked(child_tree, g)
    ]
    if not missing:
        return False
    stderr.write(
        f"ccnavi: フェーズ {found.phase}（{pt.title}）の成果物が無い: {', '.join(missing)}。"
        "親か子のワークツリーに置いて追跡（git add）してから閉じてください\n"
    )
    return True


def _tracked(worktree: str, glob: str) -> bool:
    """この glob に当たる追跡済みのファイルが 1 つでもあるか。ワークツリーが無ければ無い。"""
    rc, out = gitcmd.output(worktree, ["ls-files", "--", glob], TIMEOUT_SECONDS)
    return rc == 0 and bool(out.strip())
