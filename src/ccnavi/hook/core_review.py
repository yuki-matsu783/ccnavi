"""判定のコアを通すレビュー済み（`review confirm`）の判定と手元の入口。

`confirm` が検査と書くものの並べ方、`confirm_local` が手元の入力を読んで書く入口。
`reviewable` / `requested_head` / `moved_on_host` は Chrome のボードが引く。
`--reviewed` の決め方（review_decide）と親を閉じる操作（review_close）は review を読む末端で、
コアはどちらも読まない。

"""

from __future__ import annotations

import io
from typing import TextIO

from ..infra import fsio, settings, tree
from ..tickets import (
    agree,
    approval,
    approval_checks,
    approval_marks,
    history,
    phase,
    review,
    ticket_model,
)
from . import core_base


def confirm(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
) -> core_base.Checked:
    """レビュー済みにしてよいかを見て、通れば書くもの（子を `done/` へ、
    レビュー済みのマーカー）を並べる。

    `result` はホストから取得した結果（`review_host.Result`、`ccnavi-review.sh` が組む形）。
    読めなかったときの扱い（`--result` が無い、読めない）は読む側（手元は `confirm_local`）が持つ。
    `changed_since_request` は依頼の後にユーザが見るものが動いたかの説明（空なら動いていない）。
    手元は git の差分、Chrome は compare API から作る。
    """
    conf, root = snapshot.conf, snapshot.root
    parent = _open_parent(root, conf, parent_id)
    if parent is None:
        return core_base.Checked([f"ccnavi: 親 {parent_id} の承認済みチケットが作業中に無い"], None)
    # フェーズの引き方と「依頼し直す」案内の文面は review の非公開の関数を使う（手元の
    # confirm と同じ文面にするため。review の中だけの約束なので公開名にはしない）。
    ph = review._phase(root, conf, parent, phase_no)
    if ph is None:
        return core_base.Checked(
            [f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子が無い"], None
        )
    if approval_marks.MARK_REVIEWED in ph.marks:
        # 重ね打ちでマーカーと履歴を書き直さない（`_already_requested` と同じ文面の形）
        return core_base.Checked([f"ccnavi: フェーズ {phase_no} はレビュー済み"], None)
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        return core_base.Checked(["ccnavi: 依頼の記録が無い。先に request してください"], None)
    if changed_since_request:
        return core_base.Checked(
            [
                f"ccnavi: {changed_since_request}。ユーザが見たものと今の HEAD が違う。"
                f"{review._redo_request(root, phase_no)}"
            ],
            None,
        )
    problems = review.matching_problems(result, requested_mark)
    if problems:
        return core_base.Checked(problems, None)
    problems = review.review_problems(root, conf, parent, phase_no, result)
    if problems:
        return core_base.Checked(problems, None)
    stamp = snapshot.stamp or fsio.stamp()
    notes = io.StringIO()
    # 履歴の経路は Snapshot の経路（Chrome なら chrome）。取り下げと同じ形。アカウントと
    # 拡張の版も履歴に足す（分かるときだけ。手元で引けなかったときは前と同じ中身）。
    actor = snapshot.actor
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        history.session(actor.via or history.via(), notes, actor.account, actor.version),
    ):
        stopped = review.settle_and_mark(
            stage,
            root,
            conf,
            parent,
            ph,
            core_base.reviewed_mark(result.mr.number, [], snapshot.actor),
        )
    if notes.getvalue():
        return core_base.Checked(core_base._unwritten(notes.getvalue()), None)
    return core_base.Checked([], core_base.Changes(agree.Planned(stage, stopped), root, conf))


# `confirm_local` は前の `review.confirm` を移したもの。手元の入力の読み方（cwd の親、git の
# 差分、`--result` で取得した結果）は review の非公開の関数をそのまま使う。
# review だけが持つ読み方で、外に出す値打ちが無いので公開名にはしない。
def confirm_local(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    actor: str = "",
) -> int:
    """依頼の後を見る。通ればマーカーを置いて先へ進めるようになる。

    検査と書くものの並べ方はコア（`confirm`）。ここは手元の入力
    （cwd の親、git の差分、`--result` で取得した結果）を読んで渡し、
    並べたものを書く（Writer(FS)）。Chrome の「レビュー済み」も同じコアを通る。
    前は review.py にあった（コアより下の段に置くと review → core → review の循環になる）。

    `actor` は `ccnavi-review.sh` がトークンの持ち主を引いて `--actor` で渡すアカウント。
    あればマーカーに `actor` と `via: cli` を書く。空（引けなかった）
    ならマーカーも履歴も前と同じ。
    """
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    moved = ""
    result = None
    # 読む順は前と同じ。依頼の記録が無ければ差分も取得した結果も見ない。
    # 動いていれば取得した結果を読まない。レビュー済みなら差分も取得した結果も読まない
    # （コアが「レビュー済み」で止める。Chrome と同じ文面）
    if requested_mark is not None and approval_marks.MARK_REVIEWED not in ph.marks:
        moved = review._moved_since_request(
            tree.worktree_path(root, parent.ticket), conf, requested_mark
        )
        if not moved:
            result = review._result(stderr, result_path)
            if result is None:
                return 1
    who = core_base.Actor(actor, history.VIA_CLI) if actor else core_base.Actor()
    checked = confirm(
        core_base.read_fs(conf, root, actor=who), parent.ticket, phase_no, result, moved
    )
    for line in checked.problems:
        stderr.write(line + "\n")
    if checked.changes is None:
        return 1
    return core_base.write_fs(stdout, stderr, checked.changes.planned).code


def reviewable(snapshot: core_base.Snapshot, parent_id: str) -> list[dict]:
    """依頼済みで、まだレビュー済みでないフェーズ。Chrome のボードが出す候補。

    並べるだけで、通るかは見ない（通るかは `confirm` がホストから取得した結果で決める）。
    答えは `{phase, mr, host, children}` のリスト。`mr` と `host` は依頼のマーカーの値。
    """
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    if parent is None:
        return []
    out = []
    for ph in phase.phases_of(snapshot.root, snapshot.conf, parent.ticket):
        mark = ph.marks.get(approval_marks.MARK_REQUESTED)
        if mark is None or approval_marks.MARK_REVIEWED in ph.marks:
            continue
        try:
            mr = int(mark.get("mr") or 0)
        except (TypeError, ValueError):
            mr = 0
        out.append(
            {
                "phase": ph.number,
                "mr": mr,
                "host": str(mark.get("host") or ""),
                "children": [t.ticket for t in ph.tickets],
            }
        )
    return out


def _requested_mark(snapshot: core_base.Snapshot, parent_id: str, phase_no: int) -> dict | None:
    """依頼のマーカー。親・フェーズ・依頼の記録のどれかが無ければ None。"""
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    ph = review._phase(snapshot.root, snapshot.conf, parent, phase_no) if parent else None
    return ph.marks.get(approval_marks.MARK_REQUESTED) if ph is not None else None


def requested_head(snapshot: core_base.Snapshot, parent_id: str, phase_no: int) -> str | None:
    """依頼のマーカーに記録された親の先頭。依頼の記録（か親・フェーズ）が無ければ None。

    Chrome は依頼の後に親のブランチが動いたかを compare API で確かめる。比べる相手は
    TS に読ませず、ここが出す。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    return None if mark is None else str(mark.get("head") or "")


def moved_on_host(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    head: str,
    changed: list[str] | None,
) -> str:
    """ホストで読んだ親の先頭 `head` と変更の一覧から、依頼の後にユーザが見るものが動いたかを言う。

    手元の confirm と同じ関数（`review.moved_since`）で決める。依頼の記録が無ければ空を返し、
    `confirm` が「依頼の記録が無い」で止める。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    return "" if mark is None else review.moved_since(snapshot.conf, mark, head, changed)


def _open_parent(root: str, conf: settings.Settings, parent_id: str) -> ticket_model.Ticket | None:
    """作業中の親の承認済みチケット（本物とする側）。`phase.parent_for_cwd` と同じ引き方。"""
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval_checks.by_id(open_copies), parent_id)
    if found is None or found.is_child:
        return None
    return found
