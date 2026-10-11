"""親を閉じる。`ccnavi review ready`（Draft を外す前）と `ccnavi review close-early`。

`ready` は Draft を外す前に、閉じたチケットとその記録を `logs/archive/` へ退避し、途中の作業の
置き場（`wip/`）の追跡済みのファイルを消す。標準出力の 1 行目にコメントの下書きのパス、2 行目に
`tree <退避したツリーのルート>` を出し、sh はそのツリーで未コミットの変更を確かめてから Draft を
外す。`wip/` から消したものがあれば、続けて `wip-from <消す前の HEAD>` と
`wip <消したパス>`（1 行 1 本）を出す。sh はそれを、履歴から戻す手順と一緒にユーザに見せる。
下書きのファイル名・目印・標準出力の形は sh との契約で、値と形を変えない（足すのは後ろの行だけ）。
review から分けた。review を読む末端で、review からは読まれない。
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, settings, tree
from . import (
    approval,
    approval_marks,
    archive,
    ops,
    ops_close,
    phase,
    review,
    review_host,
    syncstate,
    ticket_model,
    ticket_places,
)

# Draft を外すときにマージリクエストへ残すコメント。sh が Draft を外してから投稿する。
READY_FILE = "review-ready-{parent}.md"
# ユーザが早めに閉じたときの、残りを書き出す issue の下書きと、マージリクエストへ残すコメント。
CLOSE_EARLY_ISSUE_FILE = "review-close-early-issue-{parent}.md"
CLOSE_EARLY_NOTE_FILE = "review-close-early-note-{parent}.md"


def ready(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    result_path: str,
) -> int:
    """Draft を外してよいかを確かめ、マーカーとコメントの下書きを置く。外すのは sh。

    条件は「親を閉じられる」と同じ（ops_close.close_problems）に、「親の承認済みチケットが
    `done/` にある」を足したもの。親を閉じてから打つ（閉じた承認済みチケットも引く）。
    同じ親に 2 度打っても通る。sh が Draft を外し損ねたときに打ち直せるように。
    マージそのものはユーザが行う。

    条件を確かめてマーカーを置いたあと、親のツリーの承認済みの領域から、閉じた親（今回の親と、統合先に
    たまっていた過去の親）の `done/` の親子のチケット・`phases/<親>/`・`events/`・`flows/` を
    手元の `logs/archive/` へ退避し、`wip/` の追跡済みのファイルを消す（`review.remove_wip`）。
    git の上では削除になり、C1 がコミットして push してから sh が Draft を外す。squash で
    マージすると、既定のブランチにチケットは残らない。
    退避した後に打ち直したとき（親が退避にだけある）は、条件の確かめのうちワークツリーの側
    （未コミット・push 済み）だけを見て、下書きを書き直し、前の回が途中で止まった残りを移す。
    """
    archived = _archived_parent(root, conf, cwd, result_path)
    if archived is not None:
        return _ready_again(stdout, stderr, root, conf, archived, result_path)
    parent = review._parent_any(stderr, root, conf, cwd)
    if parent is None:
        return 1
    problems = ops_close.close_problems(root, conf, parent.ticket)
    # 親のチケットが done/ に無いまま Draft を外すと、
    # そのままマージされたときに done/ に親が無いまま親のブランチが消え、
    # 親子のチケットの判定が「決まらない」になる。それを防ぐ（判定を厳しくする向き）。
    if parent.state != ticket_model.DONE:
        problems.append(
            f"親 {parent.ticket} の承認済みチケットが {conf.approved}/{ticket_model.DONE}/ に無い"
            f"（いまは {parent.state}/）。先に "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')} finish {parent.ticket}' で"
            "親を閉じ、コミットして push してから打ち直してください"
        )
    problems += review._merge_problems(tree.worktree_path(root, parent.ticket), conf, root)
    if problems:
        stderr.write("ccnavi: まだ Draft を外せない:\n")
        for p in problems:
            stderr.write(f"  - {p}\n")
        stderr.write(
            "全部片付けてから打ち直してください。"
            "まだ残るものを承知で早めに閉じるなら、ユーザが端末で "
            f"'{settings.script_command(root, 'ccnavi-review.sh')} close-early --reason <理由>' "
            "を打つ\n"
        )
        return 1
    result = review._result_with_mr(stderr, result_path)
    if result is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: state の置き場が空。コメントの下書きを置く場所が無い\n")
        return 1
    where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    # 退避は親のワークツリーの置き場からだけ行う（ワークスペースルートの done/ には
    # 他の親子のチケットも在り、まとめて消すことになる）。
    # マーカーもコメントの下書きも置く前に確かめる。
    misplaced = _archive_place_problem(root, conf, parent, where)
    if misplaced:
        stderr.write(f"ccnavi: {misplaced}\n")
        return 1
    wrapped = approval_marks.read_parent_mark(
        where, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY
    )
    path, failed = _ready_note(conf, parent.ticket, wrapped)
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    if not review._parent_mark(
        stderr,
        where,
        parent.ticket,
        approval_marks.PARENT_MARK_READY,
        {"mr": result.mr.number, "url": result.mr.url},
    ):
        return 1
    # 閉じた親子のチケットを手元の退避へ移す。条件は確かめてあり、今回の親は done/ にある。
    return _archive_closed(stdout, stderr, root, conf, parent, where, path)


def _archive_closed(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    where: str,
    path: str,
) -> int:
    """閉じた親子のチケットを退避へ移し、下書きのパスと退避したツリーを出す。

    拾う親は、`done/` に在る親と、手元の退避に在ってツリーにも何か残る親（前の回が途中で止まった
    残り）。標準出力は 1 行目が下書きのパス、2 行目が `tree <退避したツリーのルート>`（sh が
    そのツリーの未コミットを確かめる）。
    """
    misplaced = _archive_place_problem(root, conf, parent, where)
    if misplaced:
        stderr.write(f"ccnavi: {misplaced}\n")
        return 1
    home = tree.tree_of(root, where, conf.projects)
    tree_root = home.root if home is not None else ""
    parents = sorted(
        set(archive.closed_parents(where)) | set(archive.archived_parents(root, parent.project))
    )
    todo = archive.plan(where, parents, root, parent.project)
    _, failed = archive.move(root, parent.project, where, todo, parent.ticket, tree_root)
    if failed:
        stderr.write(f"ccnavi: 閉じたチケットを logs/archive/ へ退避できなかった: {failed}\n")
        return 1
    # 途中の作業の置き場（wip/）の追跡済みのファイルも、同じ流れで消す（C1 が一緒にコミットする）。
    removed, head, failed = review.remove_wip(tree_root) if tree_root else ([], "", "")
    if failed:
        stderr.write(f"ccnavi: `{ticket_places.WIP_ROOT}/` を片付けられなかった: {failed}\n")
        return 1
    stdout.write(path + "\n")
    if tree_root:
        stdout.write(f"tree {tree_root}\n")
    if removed:
        stdout.write(f"wip-from {head}\n")
        for rel in removed:
            stdout.write(f"wip {rel}\n")
    return 0


def _archive_place_problem(
    root: str, conf: settings.Settings, parent: ticket_model.Ticket, where: str
) -> str:
    """退避の元が親のワークツリー（`.claude/worktrees/<親>`）の承認済みの領域でなければ、その理由。"""
    home = tree.tree_of(root, where, conf.projects)
    expected = tree.worktree_path(root, parent.ticket)
    if (
        home is not None
        and not home.is_main
        and syncstate.same_tree(os.path.realpath(home.root), os.path.realpath(expected))
    ):
        return ""
    shown = home.name if home is not None and home.name else "ワークスペースルート"
    return (
        f"親 {parent.ticket} の承認済みチケットが親のワークツリー（{tree.WORKTREES_DIR}/"
        f"{parent.ticket}）ではなく {shown} に在る。閉じたチケットの退避は親のワークツリーの"
        "置き場からだけ行う（他の親子のチケットまで消さないため）。親のワークツリーで"
        "承認済みチケットを持ってから打ち直してください。Draft はまだ外していない"
    )


def _ready_note(conf: settings.Settings, parent: str, wrapped: dict | None) -> tuple[str, str]:
    """Draft を外したときのコメントの下書きを書く。パスと、書けなかった理由（無ければ空）。"""
    text = [review_host.MARKER_READY, f"チケット `{parent}` の作業は終わり、Draft を外した。"]
    text.append(
        f"`{ticket_places.WIP_ROOT}/` の追跡済みのファイルは ready が消した（履歴から戻せる）。"
        "閉じたチケットとその記録"
        f"（`{conf.approved}/` の done/・phases/・events/・flows/）は手元の `logs/archive/` へ"
        "退避し、このブランチからは消してある。マージするかどうかはユーザが決める。"
        "取り込むときは squash で、途中のコミットを既定のブランチに残さない。"
    )
    if wrapped:
        text.append(f"（ユーザが早めに閉じた: {wrapped.get('reason', '')}）")
    path = os.path.join(conf.state, READY_FILE.format(parent=parent))
    return path, review._write_text(path, "\n".join(text) + "\n")


def _archived_parent(
    root: str, conf: settings.Settings, cwd: str, result_path: str
) -> ticket_model.Ticket | None:
    """cwd のワークツリーの親が、ready の退避を始めた後なら、その親。

    次の 2 つがそろうときだけ。そろわなければ None（通常の条件の確かめへ回る）。

    - ready のマーカー（`ready/<親>.json`）が、今のツリーで書かれたものであること
      （`archive.ready_started`）
    - Draft を外したマーカー（`phases/<親>/ready.json`。ツリーか退避）のマージリクエストの番号が、
      今回の結果のものと同じであること

    そのうえで、承認済みの領域に無く手元の退避にだけある（退避を済ませた後に Draft を外し損ねた）
    か、`done/` に残っている（退避が途中で止まった）とき。前の回が条件を確かめているので、
    打ち直しではワークツリーの側の条件だけを見て、残りを移す。
    """
    t = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if t is None or t.is_main:
        return None
    approved = settings.approved_dir(conf, t.root)
    if os.path.lexists(os.path.join(approved, ticket_model.DOING, f"{t.name}.md")):
        return None
    if not archive.ready_started(root, t.project, t.name, t.root):
        return None
    result = review._result(io.StringIO(), result_path)
    if result is None or result.mr is None:
        return None
    marks = [
        approval_marks.read_parent_mark(where, t.name, approval_marks.PARENT_MARK_READY)
        for where in (approved, archive.base_dir(root, t.project))
    ]
    if not any(isinstance(m, dict) and m.get("mr") == result.mr.number for m in marks):
        return None
    if os.path.lexists(os.path.join(approved, ticket_model.DONE, f"{t.name}.md")):
        copy, _ = approval.load_copy(os.path.join(approved, ticket_model.DONE, f"{t.name}.md"))
        if copy is None or copy.parent:
            return None
        copy.project = t.project
        return copy
    return archive.archived_parent(root, t.project, t.name)


def _ready_again(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    result_path: str,
) -> int:
    """退避を済ませた親に打ち直した `ready`。ワークツリーの側の条件だけを見て、下書きを書き直す。"""
    problems = review._merge_problems(tree.worktree_path(root, parent.ticket), conf, root)
    if problems:
        stderr.write(
            "ccnavi: まだ Draft を外せない（閉じたチケットは logs/archive/ へ退避済み）:\n"
        )
        for p in problems:
            stderr.write(f"  - {p}\n")
        return 1
    if review._result_with_mr(stderr, result_path) is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: state の置き場が空。コメントの下書きを置く場所が無い\n")
        return 1
    # 早めに閉じたマーカーは、途中で止まった回ならツリーに、移し終えていれば退避に在る
    where = settings.approved_dir(conf, tree.worktree_path(root, parent.ticket))
    wrapped = approval_marks.read_parent_mark(
        where, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY
    ) or approval_marks.read_parent_mark(
        archive.base_dir(root, parent.project),
        parent.ticket,
        approval_marks.PARENT_MARK_CLOSE_EARLY,
    )
    path, failed = _ready_note(conf, parent.ticket, wrapped)
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    # 前の回が途中で止まっていれば、残りをここで移す（移すものが無ければ何もしない）。
    return _archive_closed(stdout, stderr, root, conf, parent, where, path)


def close_early(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    reason: str,
    result_path: str,
) -> int:
    """ユーザが端末で打つ。「まだ残っているが、キリの良いところまでやった」と早めに閉じる。

    残っているもの（未着手の子、子の無いフェーズ、終わっていないレビュー、
    未計画のフィードバック、未解決のスレッド）を全部見せてから y/N。y なら、
    未着手の子を取り消し、フェーズに省略とレビュー済みのマーカーを置き、未解決を受け入れ、
    親のマーカー `close-early.json` を置く。残りを別の issue に書き出す下書きを書き、sh がそれで
    issue を作る。ユーザが知らないうちに消えるものは作らない。

    Draft を外すのはここではなく `ready`。早めに閉じたあとに親が状態の移動をコミットして
    push する。それが済んだことを `ready` が確かめ、途中の作業の置き場を片付けてから外す。
    外す経路を `ready` の 1 つにしておくと、外れたマージリクエストは必ず
    「片付いて push 済み」になる。

    作業中の子がいる間は打てない。早めに閉じるのは、手が止まっているときだけ。
    """
    if not reason.strip():
        stderr.write("ccnavi: --close-early には --reason <理由> が要る\n")
        return 1
    parent = review._parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    if not conf.state:
        stderr.write("ccnavi: state の置き場が空。下書きを置く場所が無い\n")
        return 1
    result = review._result_with_mr(stderr, result_path)
    if result is None:
        return 1
    assert result.mr is not None
    if any(
        r.state.upper() == review_host.CHANGES_REQUESTED
        for r in review_host.effective(result.reviews)
    ):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。端末からも通せない。"
            "レビュアーの approve / dismiss を待ってください\n"
        )
        return 1
    phases = phase.phases_of(root, conf, parent.ticket)
    doing = [
        t.ticket
        for ph in phases
        for t in ph.tickets
        if ph.states.get(t.ticket) == ticket_model.DOING and t.started_at
    ]
    if doing:
        stderr.write(
            f"ccnavi: 作業中の子がいる（{', '.join(doing)}）。"
            "子を閉じるか取り消してから、親を早めに閉じてください\n"
        )
        return 1
    home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    left = _leftovers(home, parent, phases, result)
    _show_leftovers(stdout, parent, left)
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 早めに閉じなかった\n")
        return 1
    stamp = approval_marks.now()
    settled = _settle(
        stdout, stderr, root, conf, parent, phases, left, result.mr.number, stamp, reason
    )
    if settled is None:
        return 1
    cancelled, skipped, reviewed_now = settled
    accepted = [review.thread_key(t) for t in left.unresolved]
    failed = approval_marks.remember_accepted(
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
        parent.ticket,
        accepted,
    )
    if failed:
        stderr.write(f"ccnavi: 受け入れを記録できない: {failed}\n")
        return 1
    if not review._parent_mark(
        stderr,
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
        parent.ticket,
        approval_marks.PARENT_MARK_CLOSE_EARLY,
        {
            "reason": reason.strip(),
            "at": stamp,
            "mr": result.mr.number,
            "cancelled": cancelled,
            "skipped": skipped,
            "settled": reviewed_now,
            "accepted": accepted,
        },
    ):
        return 1
    failed = _close_early_drafts(
        conf, parent, result, reason, stamp, left, cancelled, skipped, accepted
    )
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    # 下書きのパスは標準出力に出さない。ここはユーザの端末に向いていて、sh は
    # state の置き場の決まった名前（親の識別子 = ブランチ名）で拾う。
    stdout.write(
        f"OK: {parent.ticket} を早めに閉じた。あとは親に、状態の移動をコミットし、"
        f"'ticket finish {parent.ticket}' で閉じて push し、"
        "'ccnavi-review.sh ready' で Draft を外させる"
        f"（`{ticket_places.WIP_ROOT}/` の追跡済みのファイルは ready が消す）\n"
    )
    return 0


@dataclass
class Leftovers:
    """早めに閉じるときに残っているもの。見せるものと、閉じたあとに issue へ書き出すもの。"""

    # 未着手の子。取り消す。
    todo: list[ticket_model.Ticket]
    # 終わっていないフェーズ。省略のマーカーを置く。
    not_ended: list[phase.Phase]
    # 終わっていないフェーズのうち、手を付けていないもの（子が無いか全部未着手）。
    untouched: list[phase.Phase]
    # 終わったがレビューが済んでいないフェーズ。済んだ扱いにする。
    unreviewed: list[phase.Phase]
    # フィードバック計画がまだ無い。対応なしの扱いにする。
    unplanned: bool
    # 未解決のスレッド。受け入れる。
    unresolved: list[review_host.Thread]

    @property
    def nothing(self) -> bool:
        return not (
            self.todo or self.not_ended or self.unreviewed or self.unresolved or self.unplanned
        )


def _leftovers(
    approved_dir: str,
    parent: ticket_model.Ticket,
    phases: list[phase.Phase],
    result: review_host.Result,
) -> Leftovers:
    not_ended = [ph for ph in phases if not ph.ended]

    def untouched(t: ticket_model.Ticket, ph: phase.Phase) -> bool:
        # 承認されたが着手していない子。`doing/` に在って着手の欄が空。
        return ph.states.get(t.ticket) == ticket_model.DOING and not t.started_at

    return Leftovers(
        todo=[t for ph in phases for t in ph.tickets if untouched(t, ph)],
        not_ended=not_ended,
        untouched=[
            ph for ph in not_ended if not ph.tickets or all(untouched(t, ph) for t in ph.tickets)
        ],
        unreviewed=[ph for ph in phases if ph.ended and not phase.reviewed_or_skipped(ph)],
        unplanned=parent.has_plan and parent.feedback is None,
        unresolved=review_host._unresolved(
            result.threads, approval_marks.accepted_threads(approved_dir, parent.ticket)
        ),
    )


def _show_leftovers(stdout: TextIO, parent: ticket_model.Ticket, left: Leftovers) -> None:
    """残っているものと、早めに閉じたら何が起きるかをユーザに見せて、y/N を促す。"""
    stdout.write(f"{parent.ticket}（{parent.title}）を早めに閉じる。残っているもの:\n")
    for t in left.todo:
        stdout.write(f"  - 未着手の子 {t.ticket}（{t.title}）→ 取り消す\n")
    for ph in left.untouched:
        stdout.write(f"  - フェーズ {ph.label}: 手を付けていない → 省略のマーカー\n")
    for ph in left.unreviewed:
        stdout.write(f"  - フェーズ {ph.label}: レビューが済んでいない → 済んだ扱い\n")
    if left.unplanned:
        stdout.write("  - フィードバック計画: 未計画 → 対応なしの扱い\n")
    for t in left.unresolved:
        stdout.write(f"  - 未解決 {review.thread_label(t)} → 受け入れる\n")
    if left.nothing:
        stdout.write("  （何も残っていない。ready で足りる）\n")
    stdout.write("残りは別の issue に書き出す。早めに閉じてよいなら y、やめるならそれ以外: ")
    stdout.flush()


def _settle(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    phases: list[phase.Phase],
    left: Leftovers,
    mr_number: int,
    stamp: str,
    reason: str,
) -> tuple[list[str], list[int], list[int]] | None:
    """未着手の子を取り消し、フェーズにマーカーを置く。

    返すのは取り消した子、省略のマーカーを置いた番号、済んだ扱いにした番号。
    途中で失敗したら None。そこまでの変更は戻さない（マーカーは次に打てば重ねられる）。
    """
    cancelled: list[str] = []
    for t in left.todo:
        if ops.cancel(stdout, stderr, root, conf, t.ticket, f"close-early: {reason.strip()}") != 0:
            return None
        cancelled.append(t.ticket)
    skipped: list[int] = []
    settled: list[int] = []
    pending = {ph.number for ph in left.not_ended}
    for ph in phases:
        if ph.number in pending:
            if approval_marks.MARK_SKIPPED in ph.marks:
                continue
            if not review._mark(
                stderr,
                approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
                parent.ticket,
                ph.number,
                approval_marks.MARK_SKIPPED,
                {"by": "close-early", "at": stamp},
            ):
                return None
            skipped.append(ph.number)
        elif ph in left.unreviewed:
            if not review._settle_children(stdout, stderr, root, conf, parent, ph):
                return None
            if not review._mark(
                stderr,
                approval.home_dir(conf, root, parent.ticket, "", project=parent.project),
                parent.ticket,
                ph.number,
                approval_marks.MARK_REVIEWED,
                {"by": "close-early", "at": stamp, "mr": mr_number, "accepted": []},
            ):
                return None
            settled.append(ph.number)
    return cancelled, skipped, settled


def _close_early_drafts(
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    result: review_host.Result,
    reason: str,
    stamp: str,
    left: Leftovers,
    cancelled: list[str],
    skipped: list[int],
    accepted: list[str],
) -> str:
    """残りを書き出す issue の下書きと、マージリクエストへ残すコメント の下書き。
    書けなければ理由。"""
    assert result.mr is not None
    # 1 行目が題、空行のあとが本文。
    issue = [f"{parent.title} の残り", ""]
    issue += [
        f"元のマージリクエスト: {result.mr.url}（チケット `{parent.ticket}`）",
        f"早めに閉じた理由: {reason.strip()}",
        "",
        "## 残した作業",
        "",
    ]
    rest = [f"- {t.ticket}: {t.title}" for t in left.todo]
    rest += [f"- フェーズ {ph.label}: 手を付けていない" for ph in left.untouched]
    if left.unplanned:
        rest.append("- フィードバック計画は立てていない")
    issue += rest or ["（残した作業は無い）"]
    issue += ["", "## 引き継ぐ指摘", ""]
    issue += [f"- {review.thread_label(t)}" for t in left.unresolved] or [
        "（未解決のスレッドは残っていない）"
    ]
    issue.append("")
    issue_path = os.path.join(conf.state, CLOSE_EARLY_ISSUE_FILE.format(parent=parent.ticket))
    failed = review._write_text(issue_path, "\n".join(issue))
    if failed:
        return failed
    note = [
        review_host.MARKER_CLOSE_EARLY,
        f"ユーザが早めに閉じた（{stamp}）: {reason.strip()}",
        f"取り消した子: {', '.join(cancelled) or '無し'} / 省略したフェーズ: "
        f"{', '.join(str(n) for n in skipped) or '無し'} / 受け入れた指摘: {len(accepted)} 件",
        "残りは別の issue に書き出す。親が片付けて ready を打てば Draft が外れる。"
        "マージはユーザが行う。",
        "",
    ]
    note_path = os.path.join(conf.state, CLOSE_EARLY_NOTE_FILE.format(parent=parent.ticket))
    return review._write_text(note_path, "\n".join(note))
