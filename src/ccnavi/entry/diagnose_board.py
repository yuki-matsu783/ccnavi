"""ボードの中身（`--explain --json`）。

ルール・フェーズの種類・リスク・チケットを 1 つの辞書にまとめる。
"""

from __future__ import annotations

import io
import os
from typing import TextIO

from ..infra import settings, tree
from ..policy import ruleload, rules
from ..tickets import (
    agree,
    agree_candidates,
    approval,
    approval_checks,
    approval_marks,
    approval_times,
    archive,
    flow,
    history,
    phase,
    ticket_fold,
    ticket_model,
)
from ..tickets import ticket as ticket_mod
from . import diagnose_shared

# `--explain --json` の形の版。読み手（VS Code 拡張）が形の違いに気づけるように。
BOARD_VERSION = 1


def board(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> dict:
    """ボードの中身。形は設計 10 と README「ボードの JSON」に書いてある。"""
    problems: list[str] = []
    trees = tree.all_trees(root, conf.projects)
    payload: dict = {
        "version": BOARD_VERSION,
        "root": root,
        "generated_at": approval_marks.now(),
        "settings": {
            "ticket_control": conf.ticket_control or settings.TICKET_CONTROL_ENABLE,
            "tickets": conf.tickets,
            "approved": conf.approved,
            "projects": conf.projects,
        },
        "trees": [
            {"name": t.name, "root": t.root, "project": t.project, "kind": t.kind} for t in trees
        ],
        "projects": [t.name for t in trees if t.kind == tree.KIND_PROJECT],
        "layers": _layers(conf, root, stderr),
        "problems": problems,
        "pending_approval": [],
        "tickets": [],
        "parents": [],
        # 手元の退避（`logs/archive/`）にある閉じたチケット。表示のためだけに載せ、判定（scan・
        # 承認待ち・先行を引く対応表）には混ぜない。置き場に同じプロジェクトの同じ識別子が
        # まだ在るもの（退避の後の push が戻されたなど）は `tickets` の側に出すので、
        # ここには出さない。
        "archived": [],
    }
    if not conf.tickets_enabled:
        problems.append(f"{settings.TICKET_CONTROL_ENV}=disable。チケット制御を使っていない")
        payload["archived"] = _archived_records(root, set(), problems)
        return payload

    everything, scan_problems = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    problems.extend(str(p) for p in scan_problems)
    # 承認済みの識別子の提案は、承認済みチケットと合わせて本物とするツリーを決める
    # （`approval.scan_proposals` と同じまとめ方）。本物とするツリーの外に残った古い提案を
    # 承認待ちや作業中として出さないため。
    # 承認済みチケットの置き場はここで 1 度だけ読み、親ごとの局面とフェーズ
    # （`_parent_record`）まで持ち回る。親ごとに読み直すと、読む回数が親の数とツリーの数の
    # 積で増える。ボードは読むだけで、置き場のファイルを動かさない。
    raw = approval.read_raw(conf, root)
    settled = raw.everything
    proposals = ticket_fold.dedupe(everything, settled)
    # 複数のツリーにあるチケットの一覧（`seen_in` / `scattered`）は、
    # 承認済みチケットの置き場に在るものも数える。チケットは 1 本のファイルで、
    # どの置き場に在っても子のワークツリーにも入る。
    # `review/` は提案の置き場でもあり承認済みチケットでもあるので、2 つの走査が同じファイルを拾う。
    # 同じ実体を 2 つと数えると「複数の場所にある」になるので、パスでまとめる。
    everything = _one_per_file(everything + settled)
    open_copies, notes = approval.scan(conf, root, raw=raw)
    problems.extend(notes)
    closed_copies, notes = approval.scan(conf, root, closed=True, raw=raw)
    problems.extend(notes)
    review_copies, notes = approval.scan_review(conf, root, raw=raw)
    problems.extend(notes)

    pending, revisions = agree.waiting(
        proposals,
        open_copies,
        closed_copies,
        review_copies,
        agree_candidates.types_resolver(conf, root, open_copies),
    )
    # 先行を引く対応表。承認と着手が使うのと同じ集め方。
    preds = approval_checks.predecessor_pool_of(
        open_copies, review_copies, closed_copies, proposals, root
    )
    approval_checks.align_imported(conf, root, preds)
    payload["pending_approval"] = sorted(
        {t.ticket for t in pending} | {t.ticket for t in revisions}
    )

    worktrees = {t.name: t for t in trees if t.kind == tree.KIND_WORKTREE}
    # 提案の欄に出すのは `todo/` と `review/`。`review/` は承認済みチケットでもあるので、
    # 承認の欄（`copy`）には `review` の状態で出す。
    proposal_index = approval_checks.by_id(proposals)
    open_index = approval_checks.by_id(open_copies + review_copies)
    closed_index = approval_checks.by_id(closed_copies)
    # 同じ識別子があるツリーの全部。本物とする側は proposal に、残りは seen_in に出す。
    # 複数のツリーにあること自体は普通（子のワークツリーは親のブランチから切る）なので、数は
    # 食い違いを意味しない。どれが本物か決まらないぶんだけを scattered に出す。数え方は
    # `ticket_fold.collisions` に置いてあり、--lint と同じ関数を通る
    # （同じ答えを 2 か所で出さない）。
    grouped = ticket_fold.by_ticket(everything)
    seen = {tid: [_where(t) for t in hits] for tid, hits in grouped.items()}
    scattered = {
        tid: [_where(t) for t in ticket_fold.collisions(hits)] for tid, hits in grouped.items()
    }

    # 承認の時刻。履歴か git から引く（承認済みチケットには書かない）。git はツリーごとに 1 回まで。
    times = approval_times.approved_times(
        conf, [t for t in (*open_index.values(), *closed_index.values()) if t is not None]
    )
    for ticket_id in sorted(set(proposal_index) | set(open_index) | set(closed_index)):
        payload["tickets"].append(
            _ticket_record(
                conf,
                root,
                ticket_id,
                proposal_index.get(ticket_id),
                open_index,
                closed_index,
                worktrees,
                seen.get(ticket_id, []),
                scattered.get(ticket_id, []),
                problems,
                preds,
                times,
            )
        )

    # 親ごとの局面とフェーズ。承認済みチケットのある親だけ。承認前の親はフェーズを持たない。
    for parent in sorted(open_copies + closed_copies, key=lambda x: x.ticket):
        if parent.is_child:
            continue
        payload["parents"].append(_parent_record(conf, root, parent, closed_index, raw))
    payload["archived"] = _archived_records(
        root, {(t["project"], t["ticket"]) for t in payload["tickets"]}, problems
    )
    return payload


def _archived_records(root: str, skip: set[tuple[str, str]], problems: list[str]) -> list[dict]:
    """手元の退避にある閉じたチケット（表示用）。`skip` の（プロジェクト, 識別子）は出さない。"""
    out = []
    for t in archive.closed_tickets(root):
        if (t.project, t.ticket) in skip:
            continue
        entries, unreadable = history.read(archive.base_dir(root, t.project), t.ticket)
        if unreadable:
            problems.append(f"{t.ticket} の履歴（退避）: {unreadable}")
        out.append(
            {
                "ticket": t.ticket,
                "parent": t.parent,
                "phase": t.phase,
                "title": t.title,
                "project": t.project,
                "path": t.path,
                # 承認は欄を書かない。古い形の欄が無ければ、退避した状態の履歴の承認の時刻。
                "approved_at": t.approved_at
                or approval_times.history_time(archive.base_dir(root, t.project), t.ticket),
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "cancelled_at": t.cancelled_at,
                "cancel_reason": t.cancel_reason,
                "history": entries,
            }
        )
    return out


def _layers(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> list[dict]:
    """レイヤーごとの宣言（設計 11.9）。
    順序は 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）。

    rules は重複を捨てたあとの、そのレイヤーから実際に判定へ入ったぶん。phases と risk は
    そのレイヤーのファイルに書いてあるぶんで、合成はしない（合成の結果は親のフェーズの
    側に出る）。読めないレイヤーは `unreadable` に理由が入り、中身は空になる。
    """
    said = stderr if stderr is not None else io.StringIO()
    out = []
    for view in ruleload.survey(said, conf, root):
        phases_path = diagnose_shared.layer_config(conf, root, view.name, settings.KIND_PHASES)
        risk_path = diagnose_shared.layer_config(conf, root, view.name, settings.KIND_RISK)
        types, phases_unreadable = diagnose_shared.layer_phase_types(phases_path)
        factors, risk_unreadable = diagnose_shared.layer_risk(conf, view.name, risk_path)
        out.append(
            {
                "name": view.name,
                "rules": {
                    "path": view.path,
                    "unreadable": view.unreadable,
                    **{
                        section: [_rule_record(rule) for rule in view.rule_set.section(section)]
                        for section in rules.SECTIONS
                    },
                },
                "phases": [_phase_type_record(view.name, pt) for pt in types],
                "risk": {
                    "path": risk_path,
                    "unreadable": risk_unreadable,
                    "factors": [_factor_record(view.name, f) for f in factors],
                },
                "phases_file": {"path": phases_path, "unreadable": phases_unreadable},
            }
        )
    return out


def _rule_record(rule: rules.Rule) -> dict:
    """ルール 1 件。id はレイヤーの名前付き、書いた表記と翻訳後の式の両方を出す。"""
    return {
        "id": rule.id,
        "section": rule.decision,
        "source": rule.source,
        "match": rule.match,
        **diagnose_shared.rule_form(rule),
        "message": rule.message,
    }


def _phase_type_record(layer: str, pt) -> dict:
    """フェーズの種類 1 つ。id は裸のまま、レイヤーは欄で出す（設計 11.4.1）。"""
    return {
        "id": pt.id,
        "source": layer,
        "kind": pt.kind,
        "title": pt.title,
        "review": pt.review,
        # scope は `inherit`（親の範囲を継ぐ）のとき None。空のリストと区別が付くように、
        # 継ぐことは `inherit` の 1 語で出す。
        "scope": [diagnose_shared.written(e) for e in pt.scope] if pt.scope else ["inherit"],
    }


def _factor_record(layer: str, factor) -> dict:
    """リスクの項目 1 つ。"""
    return {
        "id": factor.id,
        "source": layer,
        "kind": factor.kind,
        "value": factor.value if isinstance(factor.value, (int, str)) else str(factor.value),
        "points": factor.points,
        "message": factor.message,
    }


def _where(t: ticket_model.Ticket) -> dict:
    """チケット 1 つの場所。どのツリーの、どの置き場の、どのファイルか。"""
    return {"tree": t.tree, "state": t.state, "path": t.path}


def _one_per_file(found: list[ticket_model.Ticket]) -> list[ticket_model.Ticket]:
    """同じツリーで同じファイルを 2 度読んだぶんをまとめる。順序は見つけた順で、先を残す。

    鍵にツリーを入れるのは、まとめるのを「1 つの走査の重なり」に限るため。2 つのツリーが
    同じ実体を指す形（`projects/<名前>` がワークスペース自身への symlink など）は
    同じチケットが 2 つのツリーに在るのと同じで、判定の側（`approval._authoritative`）もまとめない。
    ここだけまとめると、ボードに何も出ていないのに操作が止まる。
    """
    kept: list[ticket_model.Ticket] = []
    seen: set[tuple[str, str]] = set()
    for t in found:
        key = (t.tree, os.path.normcase(os.path.abspath(t.path)))
        if key in seen:
            continue
        seen.add(key)
        kept.append(t)
    return kept


def _ticket_record(
    conf: settings.Settings,
    root: str,
    ticket_id: str,
    proposal: ticket_model.Ticket | None,
    open_index: dict,
    closed_index: dict,
    worktrees: dict,
    seen_in: list[dict],
    scattered: list[dict],
    problems: list[str],
    preds: dict[str, list[ticket_model.Ticket]],
    times: dict[str, approval_times.ApprovedTime],
) -> dict:
    """チケット 1 件。提案と承認済みチケットとワークツリーの今を 1 つにまとめる。"""
    copy = open_index.get(ticket_id) or closed_index.get(ticket_id)
    source = proposal or copy
    assert source is not None
    if ticket_id in open_index:
        status = "review" if open_index[ticket_id].state == ticket_model.REVIEW else "open"
    elif ticket_id in closed_index:
        status = "closed"
    else:
        status = "none"
    found = tree.lookup(worktrees, ticket_id)
    record: dict = {
        "ticket": ticket_id,
        "parent": source.parent,
        "phase": source.phase,
        "title": source.title,
        "project": source.project,
        "issue": source.issue,
        "predecessors": list(source.predecessors),
        # 満たしていない先行（`done/` に無いか取り消しのもの）。承認と着手はこれが空でなければ
        # 止まる。閉じたチケットは空。
        # `label` はユーザ向けの言葉で、ボードはそのまま使うだけ。
        "predecessors_unmet": (
            []
            if status == "closed"
            else [
                {"ticket": p.ticket, "state": p.state, "label": p.label}
                for p in approval_checks.unmet_predecessors(source, preds)
            ]
        ),
        "human_review": {
            "required": source.review_required,
            "reason": source.review_reason,
        },
        "proposal": (
            {
                "state": proposal.state,
                "tree": proposal.tree,
                "tree_root": proposal.tree_root,
                "path": proposal.path,
            }
            if proposal is not None
            else None
        ),
        # blocked は「読めるが信頼できない」理由。判定はこのチケットの
        # ワークツリーへの書き込みを全部止めるので、ボードが素の open として見せると、
        # 止まっていること自体がユーザに届かない。
        "blocked": (open_index[ticket_id].blocked if ticket_id in open_index else ""),
        "copy": (
            {
                "status": status,
                # 承認の時刻（`approval_times.approved_times`）。`approved_from` はどこから引いたか:
                # history（状態の履歴）/ commit（doing/ に足したコミット）/
                # uncommitted（履歴もコミットも無い。手で置いてまだコミットしていない）/
                # 空（分からない）。uncommitted と空のとき approved_at は空。
                "approved_at": times.get(copy.path, approval_times.ApprovedTime()).at,
                "approved_from": times.get(copy.path, approval_times.ApprovedTime()).source,
                "source_tree": copy.source_tree,
                "path": copy.path,
            }
            if copy is not None
            else {"status": status}
        ),
        "worktree": (
            {"exists": True, "path": found.root, "project": found.project}
            if found is not None
            else {"exists": False, "path": tree.worktree_path(root, ticket_id)}
        ),
        "started_at": source.started_at,
        "completed_at": source.completed_at,
        "base_sha": source.base_sha,
        "cancelled_at": source.cancelled_at,
        "cancel_reason": source.cancel_reason,
        "seen_in": seen_in,
        "scattered": scattered,
        # 子のフロー（設計 9.3.1。着手中は書き換えを止める）。
        # `{path, rel, tree, exists, linked, locked, draft}`。draft はエージェントの下書きの
        # `{path, rel, exists, linked}`（効力は無い）。親は null。
        # locked は判定がそのフローへの書き込みを止めているか（着手中）。読むのは承認済み
        # チケットがあればその側、無ければ提案。
        "flow": flow.info(conf, root, copy if copy is not None else source),
        "risk": None,
        "judge": None,
        # 状態が動いた履歴の新しい側。追記するだけの補助で、状態の正は上の置き場の欄。
        "history": [],
    }
    where = approval.home_dir(conf, root, ticket_id, source.parent, project=source.project)
    entries, unreadable = history.read(where, ticket_id)
    record["history"] = entries
    if unreadable:
        problems.append(f"{ticket_id} の履歴: {unreadable}")
    if source.parent:
        record["risk"] = approval_marks.read_child_record(
            where, source.parent, ticket_id, approval_marks.CHILD_RECORD_RISK
        )
        record["judge"] = approval_marks.read_child_record(
            where, source.parent, ticket_id, approval_marks.CHILD_RECORD_JUDGE
        )
    return record


def _phase_record(ph: phase.Phase) -> dict:
    """フェーズ 1 つ。判定と同じ Phase から組む。"""
    if not ph.tickets:
        state = "planned"
    elif ph.ended:
        state = "ended"
    else:
        state = "active"
    return {
        "number": ph.number,
        "type": ph.item.type if ph.item is not None else "",
        "title": ph.title,
        "label": ph.label,
        "state": state,
        "tickets": [t.ticket for t in ph.tickets],
        "states": dict(ph.states),
        "marks": ph.marks,
        "review_required": ph.review_required,
        "review_kind": ph.review_kind,
        "gate_closed": ph.gate_closed,
        "review_waiting": ph.review_waiting,
        "deferred": ph.deferred,
        "review_at": ph.review_at,
        "covers": list(ph.covers),
        "risk": ph.risk,
        "risk_escalates": ph.risk_escalates,
        "risk_line": ph.risk_line,
    }


def _parent_record(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    closed_index: dict,
    raw: approval.Raw | None = None,
) -> dict:
    """親 1 件。局面、計画、親のマーカー、フェーズのリスト。`raw` は `phase.phases_of` と同じ。"""
    where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    closed = parent.ticket in closed_index
    return {
        "ticket": parent.ticket,
        "closed": closed,
        # 閉じた親に「クローズ可」と言っても意味が無い。局面は動いている親だけが持つ。
        "stage": "" if closed else phase.stage(root, conf, parent, raw),
        "plan": [item.as_raw() for item in parent.plan],
        "feedback": (
            [item.as_raw() for item in parent.feedback] if parent.feedback is not None else None
        ),
        "close_early": approval_marks.read_parent_mark(
            where, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY
        ),
        "ready": approval_marks.read_parent_mark(
            where, parent.ticket, approval_marks.PARENT_MARK_READY
        ),
        "closed_record": approval_marks.read_parent_mark(
            where, parent.ticket, approval_marks.PARENT_MARK_CLOSED
        ),
        "accepted_threads": sorted(approval_marks.accepted_threads(where, parent.ticket)),
        "phases": [_phase_record(ph) for ph in phase.phases_of(root, conf, parent.ticket, raw=raw)],
    }
