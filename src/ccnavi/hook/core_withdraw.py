"""判定のコアを通す承認の取り下げ。

`withdraw` が条件を見て、通れば書くものを並べる。`withdrawable` は Chrome のボードが出す候補。

"""

from __future__ import annotations

import io
import os

from ..infra import fsio, settings
from ..tickets import (
    agree,
    approval,
    approval_checks,
    approval_marks,
    history,
    ticket_model,
)
from . import core_base

# ---- 承認の取り下げ ----------------------------------------------------------------------------


def withdraw(
    snapshot: core_base.Snapshot,
    ids: list[str],
    prior_proposals: dict[str, bytes],
    reason: str = "",
) -> core_base.Checked:
    """承認を未承認（`todo/`）に戻す。条件は 8.8。通れば書くものを並べる。

    `prior_proposals` は識別子ごとの「承認コミットの親にあった `todo/<識別子>.md` のバイト列」。
    承認コミットを引くのはホストの API を読む側（Chrome）で、引けなかった識別子は渡さない。
    判定は緩めない。どれか 1 つでも条件に当たらなければ何も並べない。

    承認は中身を変えないので、`doing/` の中身が承認コミットの親の提案とバイト単位で同じなら、
    改版も着手もされていない（`_content_problems`）。待ち方は `doing/` の計画から決まるので、
    バイト列が同じなら待ち方も同じ。別に比べる段も、一緒に消すファイルも無い。
    """
    conf, root = snapshot.conf, snapshot.root
    raw = approval.read_raw(conf, root)
    approved, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review_waiting, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    open_index = approval_checks.by_id(approved)
    problems: list[str] = []
    wanted = [i for i in dict.fromkeys(ids) if i]
    if not wanted:
        problems.append("取り下げる識別子が無い")
    for ident in wanted:
        problems += [
            f"{ident}: {p}"
            for p in _withdraw_problems(
                conf,
                root,
                ident,
                open_index.get(ident),
                approved + closed + review_waiting,
                proposals,
                prior_proposals,
                compare=True,
            )
        ]
    if problems:
        return core_base.Checked(problems, None)
    stamp = snapshot.stamp or fsio.stamp()
    actor = snapshot.actor
    notes = io.StringIO()
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        history.session(actor.via or history.via(), notes),
    ):
        stopped = None
        for ident in wanted:
            copy = open_index[ident]
            where = settings.approved_dir(conf, copy.tree_root)
            todo = os.path.join(
                copy.tree_root, conf.tickets.replace("/", os.sep), ticket_model.TODO, ident + ".md"
            )
            with fsio.policy(
                on_fail=fsio.FAIL_STOP, ticket=ident, message="書けない ({reason})", prefix=""
            ):
                fsio.write_bytes_atomic(todo, prior_proposals[ident])
                # 消せなければ戻した提案を消して、両方に残さない（`approval_ops.admit` と同じ）。
                with fsio.policy(
                    message="承認済みチケットを doing/ から消せない ({reason})", undo=(todo,)
                ):
                    fsio.unlink(approval.copy_path(where, ident))
            history.note(
                where,
                ident,
                history.KIND_WITHDRAWN,
                ticket_model.DOING,
                ticket_model.TODO,
                actor=actor.account,
                version=actor.version,
                reason=reason,
            )
            stage.line(f"  {ident} の承認を取り下げた（doing/ → todo/）")
    if notes.getvalue():
        return core_base.Checked(core_base.unwritten(notes.getvalue()), None)
    return core_base.Checked([], core_base.Changes(agree.Planned(stage, stopped), root, conf))


def withdrawable(snapshot: core_base.Snapshot, family: str) -> list[tuple[str, str, list[str]]]:
    """親子のチケットの作業中（`doing/`）のチケットごとの (識別子, 題, 取り下げられない理由)。

    Chrome のボードが「取り下げ」を出すかを決めるのに使う。条件は `withdraw` と同じで、
    承認コミットの親の提案（`prior_proposals`）だけは引ける前提で見る（引くのはホストを読む側。
    引けなければ `withdraw` がその理由で止める）。一覧は承認コミットを引く前に組むので、
    中身の一致（`_content_problems`）は見ない。承認の記録の無い新しい形か、古い形で記録の
    条件を満たすものを出す（粗く出しても、押したときに `withdraw` が新しい Snapshot で
    全部を見直して止める。判定は緩めない）。
    """
    conf, root = snapshot.conf, snapshot.root
    raw = approval.read_raw(conf, root)
    approved, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review_waiting, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    everything = approved + closed + review_waiting
    out = []
    for copy in sorted(approved, key=lambda t: t.ticket):
        if (copy.parent or copy.ticket) != family or copy.tree != family:
            continue
        problems = _withdraw_problems(
            conf, root, copy.ticket, copy, everything, proposals, {copy.ticket: b""}
        )
        out.append((copy.ticket, copy.title, problems))
    return out


def _withdraw_problems(
    conf: settings.Settings,
    root: str,
    ident: str,
    copy: ticket_model.Ticket | None,
    everything: list[ticket_model.Ticket],
    proposals: list[ticket_model.Ticket],
    prior_proposals: dict[str, bytes],
    compare: bool = False,
) -> list[str]:
    """取り下げられない理由。`compare` なら中身の一致（`_content_problems`）も見る（書く側）。"""
    if copy is None:
        return ["承認済みチケットが作業中（doing/）に無い"]
    home = copy.parent or copy.ticket
    if copy.tree != home:
        return [f"親のブランチ {home} の doing/ に無い（{copy.tree or 'ワークスペースルート'}）"]
    found: list[str] = []
    if copy.blocked:
        # 親子のチケットが決まらない・親のブランチの外のチケットなど。
        # 状態の操作と同じく止める
        found.append(copy.blocked)
    if approval_checks.has_record(copy):
        # 承認で記録（`ccnavi_approved`）を書いていた頃の古い形。承認で欄が足されているので
        # 承認コミットの親の提案とは一致しない。子は記録の欄で決める（前の条件のまま）。
        meta = copy.raw[ticket_model.APPROVAL_KEY]
        if not copy.is_child:
            # 親は一律に止める。改版は記録の欄を残したまま計画を書き換えるので、古い形の親が
            # 改版されたかを見分ける印が無い（前は待ち方のファイルの有無で見分けていた）。
            found.append(
                "前の版の形（承認の記録 ccnavi_approved を持つ）の親は取り下げられない"
                "（改版されたかを見分けられない）。取り消して出し直してください"
            )
        elif meta.get("revised_at") or meta.get("feedback_at"):
            found.append("改版した承認は取り下げられない（改版で動いたものを戻せない）")
        if meta.get("followup_of") or not meta.get("source_path"):
            found.append(
                "続きの子（followup）か、承認の記録の無い承認済みチケットは取り下げられない"
            )
    elif copy.raw.get(approval.FOLLOWUP_KEY):
        found.append(
            "続きの子（followup）は提案を経ずに作ったチケットなので、戻す提案が無い。"
            "取り下げられない"
        )
    if copy.started_at:
        found.append(
            "着手済み。取りやめるなら "
            f"`ccnavi-ticket.sh cancel {ident} --reason <理由>` をエージェントに頼んでください"
            "（done/ に取り消しの記録が残る）"
        )
    if not copy.is_child:
        if any(t.parent == ident for t in everything):
            found.append("子の承認済みチケットがある")
        if any(t.parent == ident and t.state == ticket_model.TODO for t in proposals):
            found.append("todo/ に子の提案がある")
        marks_dir = os.path.join(
            settings.approved_dir(conf, copy.tree_root), approval_marks.PHASES_DIR, ident
        )
        try:
            # 書きかけで落ちて残った一時ファイル（`.<名前>.<一意>.part`）はマーカーではない。
            names = fsio.listdir(marks_dir)
            if [n for n in names if not fsio.is_temp_name(n)]:
                found.append(f"{approval_marks.PHASES_DIR}/{ident}/ にマーカーがある")
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 読めないなら、無いとは言えない（取り下げを緩めない）。
            found.append(f"{approval_marks.PHASES_DIR}/{ident}/ を読めない ({exc})")
    todo = os.path.join(
        copy.tree_root, conf.tickets.replace("/", os.sep), ticket_model.TODO, ident + ".md"
    )
    if fsio.lexists(todo):
        found.append("todo/ に同じ識別子の提案がある（戻す先が塞がっている）")
    if ident not in prior_proposals:
        found.append("承認コミットの親に提案が無い（承認コミットを引けない）")
    elif compare and not approval_checks.has_record(copy):
        found += _content_problems(copy, prior_proposals[ident])
    return found


def _content_problems(copy: ticket_model.Ticket, prior: bytes) -> list[str]:
    """新しい形の承認済みチケットが、承認したときのままか。

    承認は提案を中身を変えずに動かすので、`doing/` の中身が承認コミットの親の提案（`prior`）と
    バイト単位で同じなら、改版も着手もされていない。改行を揃えて読む `fsio.read_text` は使わない
    （CRLF と LF の違いも、承認の後の書き換えとして数える）。待ち方は `doing/` の計画から決まる
    ので、バイト列が同じなら待ち方も同じ（別に比べない）。
    """
    current = fsio.read_bytes(copy.path)
    if current is None:
        return ["承認済みチケットを読めない"]
    if current != prior:
        return [
            "承認のあとに承認済みチケットの中身が変わった（改版か着手）。"
            "承認コミットの親の提案と同じでないものは取り下げられない"
        ]
    return []
