"""判定のコアを通すレビュー済み（`review confirm`）の判定と手元の入口。

`confirm` が検査と書くものの並べ方、`confirm_local` が手元の入力を読んで書く入口。
`reviewable` / `requested_head` / `moved_on_host` は Chrome のボードが引く。
`--reviewed` の決め方（review_decide）と親を閉じる操作（review_close）は review を読む末端で、
コアはどちらも読まない。

"""

from __future__ import annotations

from typing import TextIO

from ..infra import fsio, settings, tree
from . import (
    agree,
    approval,
    approval_checks,
    approval_marks,
    core_base,
    manual_settle,
    ops,
    ops_close,
    phase,
    review,
    review_host,
    ticket_model,
    ticket_places,
    via,
)

# レビュー済み（`confirm`）が通らなかった根拠の種別。付録 A の `CORE_CONFIRM_*`。
# 親の承認済みチケットが作業中に無い。
CODE_CONFIRM_NO_PARENT = "CORE_CONFIRM_NO_PARENT"
# 親にそのフェーズの子が無い。
CODE_CONFIRM_NO_PHASE = "CORE_CONFIRM_NO_PHASE"
# そのフェーズはもうレビュー済み。
CODE_CONFIRM_ALREADY_REVIEWED = "CORE_CONFIRM_ALREADY_REVIEWED"
# 依頼の記録が無い。
CODE_CONFIRM_NOT_REQUESTED = "CORE_CONFIRM_NOT_REQUESTED"
# 依頼の後に、ユーザが見るものが動いた。
CODE_CONFIRM_MOVED = "CORE_CONFIRM_MOVED"
# 取得した結果が、依頼したマージリクエストのものと照合できない。
CODE_CONFIRM_RESULT_MISMATCH = "CORE_CONFIRM_RESULT_MISMATCH"
# 変更要求のレビューか、未解決のスレッドが残っている。
CODE_CONFIRM_REVIEW_OPEN = "CORE_CONFIRM_REVIEW_OPEN"


def confirm(
    snapshot: core_base.Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
    manual: manual_settle.Manual | None = None,
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
        return core_base.refused(
            CODE_CONFIRM_NO_PARENT,
            [f"ccnavi: 親 {parent_id} の承認済みチケットが作業中にありません。"],
        )
    # フェーズの引き方と「依頼し直す」案内の文面は review の非公開の関数を使う（手元の
    # confirm と同じ文面にするため。review の中だけの約束なので公開名にはしない）。
    ph = review._phase(root, conf, parent, phase_no)
    if ph is None:
        return core_base.refused(
            CODE_CONFIRM_NO_PHASE,
            [f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子がありません。"],
        )
    if approval_marks.MARK_REVIEWED in ph.marks:
        # 重ね打ちでマーカーを書き直さない（`_already_requested` と同じ文面の形）
        return core_base.refused(
            CODE_CONFIRM_ALREADY_REVIEWED, [f"ccnavi: フェーズ {phase_no} はレビュー済み"]
        )
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        return core_base.refused(
            CODE_CONFIRM_NOT_REQUESTED,
            ["ccnavi: 依頼の記録がありません。先に request してください"],
        )
    if changed_since_request:
        return core_base.refused(
            CODE_CONFIRM_MOVED,
            [
                f"ccnavi: {changed_since_request}。ユーザが見たものと今の HEAD が違う。"
                f"{review._redo_request(root, phase_no)}"
            ],
        )
    problems = review.matching_problems(result, requested_mark)
    if problems:
        return core_base.refused(CODE_CONFIRM_RESULT_MISMATCH, problems)
    mark_extra: dict = {}
    ledger: list[dict] = []
    if manual is not None and manual.state == manual_settle.READY:
        # 手で動かした子の確定。未解決の指摘は解決せず、版つきで受け入れ台帳に足す。
        refused = _manual_refusal(root, conf, parent, phase_no, result, manual)
        if refused is not None:
            return refused
        threads = review.unresolved_for(root, conf, parent, phase_no, result)
        ledger = [_ledger_entry(t, phase_no) for t in threads]
        mark_extra = {"via": "manual", "moved": list(manual.children)}
    else:
        problems = review.review_problems(root, conf, parent, phase_no, result)
        if problems:
            return core_base.refused(CODE_CONFIRM_REVIEW_OPEN, problems)
    stamp = snapshot.stamp or fsio.stamp()
    # 経路は Snapshot の経路（Chrome なら chrome）。取り下げと同じ形。
    actor = snapshot.actor
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        via.session(actor.via or via.via(), actor.account),
    ):
        stopped = review.settle_and_mark(
            stage,
            root,
            conf,
            parent,
            ph,
            {**core_base.reviewed_mark(result.mr.number, [], snapshot.actor), **mark_extra},
            ledger,
        )
    return core_base.Checked([], core_base.Changes(agree.Planned(stage, stopped), root, conf))


def _manual_refusal(root, conf, parent, phase_no, result, manual) -> core_base.Checked | None:
    """手動移動の確定を止める理由。通れば None。

    変更要求のレビューがあれば保留（移動は残す）。受け入れるのは、移動のコミットの時刻より余裕幅
    （`manual_settle.MARGIN_SECONDS`）以上前にホストで作られたスレッドだけで、後に付いた指摘や、時刻が
    読めないスレッドが残っていれば確定しない（その分は decide へ）。
    """
    if review.changes_requested(result):
        return core_base.refused(
            CODE_CONFIRM_REVIEW_OPEN,
            [
                f"ccnavi: {review_host.changes_requested_note(result.host)}。"
                "手で動かした移動は保持するが、変更要求があるためレビュー済みにはできません（保留）"
            ],
        )
    late = []
    for t in review.unresolved_for(root, conf, parent, phase_no, result):
        # 最初のコメントだけでなく最後のコメントの時刻も見る（移動の後に付いた返信を受け入れない）
        made = max(review_host.epoch(t.created_at), review_host.epoch(t.last_comment_at))
        if (
            review_host.epoch(t.created_at) <= 0
            or made > manual.moved_at - manual_settle.MARGIN_SECONDS
        ):
            late.append(t)
    if not late:
        return None
    return core_base.refused(
        CODE_CONFIRM_REVIEW_OPEN,
        [
            f"ccnavi: 移動の後に付いた指摘（または時刻を読めない指摘）が {len(late)} 件残っていて、"
            "手で動かした移動を確定しません。decide で行き先を決めてください",
            *[f"  - {review.thread_label(t)}" for t in late],
        ],
    )


def _ledger_entry(t, phase_no: int) -> dict:
    """受け入れ台帳の項目（版つき。受け入れたときの最後のコメントを覚える）。"""
    return {
        "key": review_host.thread_key(t),
        "version": review_host.thread_version(t),
        "last_comment_id": t.last_comment_id,
        "accepted_by": "manual",
        "phase": phase_no,
    }


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
    ならマーカーは前と同じ。
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
    who = core_base.Actor(actor, via.VIA_CLI) if actor else core_base.Actor()
    manual = manual_settle.assess(root, conf, parent, ph) if result is not None else None
    for line in _manual_notes(manual):
        stdout.write(line + "\n")
    checked = confirm(
        core_base.read_fs(conf, root, actor=who), parent.ticket, phase_no, result, moved, manual
    )
    for line in checked.problems:
        stderr.write(line + "\n")
    if checked.changes is None:
        return 1
    code = core_base.write_fs(stdout, stderr, checked.changes.planned).code
    if code == 0:
        after = after_last_review(root, conf, parent.ticket)
        if after:
            stdout.write(after + "\n")
    return code


def manual_for(
    snapshot: core_base.Snapshot, parent_id: str, phase_no: int, moved_at: float | None = None
) -> manual_settle.Manual | None:
    """git を持たない呼び手（Chrome）向けの、手で動かした子の判定。親かフェーズが無ければ None。

    移動の時刻は呼び手がホストの履歴から読んで渡す。`moved_at` が None のうちは `need_time` が真で、
    `paths`（動かした子の done/ のファイル）の時刻を読んでから渡し直す。
    """
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    if parent is None:
        return None
    ph = review._phase(snapshot.root, snapshot.conf, parent, phase_no)
    if ph is None:
        return None
    return manual_settle.assess(
        snapshot.root, snapshot.conf, parent, ph, external=True, moved_at=moved_at
    )


def _manual_notes(manual: manual_settle.Manual | None) -> list[str]:
    """手で動かした移動が、確定できない形のときの知らせ（確定できるときは出さない）。"""
    if manual is None or manual.state in (manual_settle.NONE, manual_settle.READY):
        return []
    line = manual.line()
    return [f"ccnavi: {line}"] if line else []


def after_last_review(root: str, conf: settings.Settings, parent_id: str) -> str:
    """最後のレビューが済んだときに、親を閉じてから Draft を外すまでの流れ。まだ先があれば空。

    「最後」は、親を閉じられる状態（`ops_close.close_problems` が空）になったこと。
    `confirm` の知らせが「次のフェーズへ進める」だけだと、最後のフェーズのあとに何をするか
    （親の finish、push、ready）が、親の finish の出力で初めて出てくる。
    """
    if ops_close.close_problems(root, conf, parent_id):
        return ""
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    return "\n".join(
        [
            f"全部のフェーズのレビューが済み、親 {parent_id} を閉じられる。"
            "ここから Draft を解除するまでの流れ:",
            f"  1. '{ticket_sh} finish {parent_id}' で親を閉じる"
            "（取り込み済みの親子なら、finish が移動をコミットして push する。"
            "そうでなければ、移動をコミットして push する）。"
            "残っている子のワークツリーも片付く",
            f"  2. 親のワークツリーの外（ワークスペースルートなど）に出て、"
            f"'{ops.ready_command(root, parent_id)}' で Draft を解除する。"
            "ready はクローズ済みのチケットの記録と"
            f" `{ticket_places.WIP_ROOT}/` の追跡済みのファイルを削除してコミットし、push してから"
            " Draft を解除し、最後に親のワークツリーを削除します"
            "（中から --parent なしで実行すると消さず、外に出てから実行するコマンドを表示する）",
            "  マージはユーザが squash で行う",
            f"  {ops.READY_FLOW_AGREED}",
        ]
    )


def reviewable(snapshot: core_base.Snapshot, parent_id: str) -> list[dict]:
    """依頼済みで、まだレビュー済みでないフェーズ。Chrome のボードが出す候補。

    並べるだけ（通るかは `confirm` がホストから取得した結果で決める）。
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
    """作業中の親の承認済みチケット（正とする側）。`phase.parent_for_cwd` と同じ引き方。"""
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval_checks.by_id(open_copies), parent_id)
    if found is None or found.is_child:
        return None
    return found
