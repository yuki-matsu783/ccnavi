"""ボードの中身（`--explain --json`）。

ルール・フェーズ定義・リスク・チケットを 1 つのマップにまとめる。
"""

from __future__ import annotations

import dataclasses
import io
import os
from typing import TextIO

from ..infra import modes, pathcase, settings, tree
from ..policy import ruleload, rules, selfguard
from ..records import turn
from ..tickets import agree, manual_settle, overview, phase
from ..tickets.approval import checks as approval_checks
from ..tickets.approval import marks as approval_marks
from ..tickets.approval import store as approval
from ..tickets.approval import times as approval_times
from ..tickets.config import risk
from ..tickets.flow import graph as flow
from ..tickets.model import archive, oplog, standing, ticket_fold, ticket_model
from ..tickets.model import ticket as ticket_mod
from . import diagnose_shared

# `--explain --json` の形の版。読み手（VS Code 拡張）が形の違いに気づけるように。
BOARD_VERSION = 1


def board(
    conf: settings.Settings,
    root: str,
    stderr: TextIO | None = None,
    mode_flag: str = "",
    ticket_control_flag: str = "",
) -> dict:
    """ボードの中身。形は docs/11_requirements/json-shapes.md「ボードの JSON」に書いてある。

    処理フロー:
    1. `hook_settings` でモードとチケット制御を解き、その値で設定を組み直す。
    2. ツリーとレイヤーを集め、送信の途中の操作を `problems` に入れる。チケット制御が無効なら、
       閉じたものの退避（`_archived_records`）だけを載せて返す。
    3. `ticket_mod.scan_all` で全チケットを読み、承認済み・提案・承認待ちの索引に分ける。同じ
       識別子が複数の場所にあるものは `ticket_fold.collisions` で数える。
    4. 承認の時刻を git から引き、チケットごとに `_ticket_record` で行を組む。
    5. 親ごとに `_parent_record` で局面とフェーズを組み、最後に `_mark_attention` で不備と要対応を
       決める。全チケットと親が揃ってから決めるのは、親の有無やフェーズの状態を見るため。
    """
    mode, ticket_control = hook_settings(conf, root, stderr, mode_flag, ticket_control_flag)
    # ボードの中身も、hook が受け取るはずのチケット制御で組む。settings に出す値と食い違わせない
    conf = dataclasses.replace(conf, ticket_control=ticket_control)
    problems: list[str] = []
    trees = tree.all_trees(root, conf.projects)
    payload: dict = {
        "version": BOARD_VERSION,
        "root": root,
        "generated_at": approval_marks.now(),
        "settings": {
            "mode": mode,
            "ticket_control": ticket_control,
            "tickets": conf.tickets,
            "approved": conf.approved,
            "projects": conf.projects,
        },
        "trees": [
            {"name": t.name, "root": t.root, "project": t.project, "kind": t.kind} for t in trees
        ],
        "projects": [t.name for t in trees if t.kind == tree.KIND_PROJECT],
        "layers": _layers(conf, root, stderr),
        "sums": _sums(conf, root, stderr),
        "problems": problems,
        "pending_approval": [],
        "tickets": [],
        "parents": [],
        # 手元の退避（`logs/archive/`）にある閉じたチケット。表示のためだけに載せ、判定（scan・
        # 承認待ち・先行を引く対応表）には混ぜない。置き場に同じプロジェクトの同じ識別子が
        # まだ在るもの（退避の後の push が戻されたなど）は `tickets` の側に出すので、
        # ここには出さない。
        "archived": [],
        # いまターンの途中にいるセッション。ターン中に設定ファイルを直すと、次のツール呼び出しで
        # 戻されることがある。拡張は保存の前にこれで知らせる。チケット制御の有無によらず出す。
        "turns": [
            {"session": t.session, "since": int(t.since), "seen": int(t.seen)}
            for t in turn.active(conf.state)
        ],
    }
    # 送信の途中で止まった操作(引き継ぎ未完了)。ボードは problems の行として並べる。
    if conf.state:
        problems.extend(f"引き継ぎ未完了: {oplog.summary(r)}" for r in oplog.pending(conf.state))
    if not conf.tickets_enabled:
        problems.append(f"{settings.TICKET_CONTROL_ENV}=disable。チケット制御を使っていない")
        payload["archived"] = _archived_records(root, set())
        return payload

    everything, scan_problems = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    problems.extend(str(p) for p in scan_problems)
    # 承認済みの識別子の提案は、承認済みチケットと合わせて正とするツリーを決める
    # （`approval.scan_proposals` と同じまとめ方）。正とするツリーの外に残った古い提案を
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
    )
    # 先行を引く対応表。承認と着手が使うのと同じ集め方。
    preds = approval_checks.predecessor_pool_of(
        open_copies, review_copies, closed_copies, proposals
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
    # 同じ識別子があるツリーの全部。正とする側は proposal に、残りは seen_in に出す。
    # 複数のツリーにあること自体は普通（子のワークツリーは親のブランチから切る）なので、数は
    # 食い違いを意味しない。どれが正か決まらないぶんだけを scattered に出す。数え方は
    # `ticket_fold.collisions` に置いてあり、--lint と同じ関数を通る
    # （同じ答えを 2 か所で出さない）。
    grouped = ticket_fold.by_ticket(everything)
    seen = {tid: [_where(t) for t in hits] for tid, hits in grouped.items()}
    scattered = {
        tid: [_where(t) for t in ticket_fold.collisions(hits, across_trees=True)]
        for tid, hits in grouped.items()
    }

    # 承認の時刻。git から引く。git はツリーごとに 1 回まで。
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
    # 不備と要対応は全チケットと親が揃ってから決める（親が在るか、親のフェーズの状態を見る）。
    _mark_attention(payload)
    payload["archived"] = _archived_records(
        root, {(t["project"], t["ticket"]) for t in payload["tickets"]}
    )
    return payload


def _archived_records(root: str, skip: set[tuple[str, str]]) -> list[dict]:
    """手元の退避にある閉じたチケット（表示用）。`skip` の（プロジェクト, 識別子）は出さない。"""
    out = []
    for t in archive.closed_tickets(root):
        if (t.project, t.ticket) in skip:
            continue
        out.append(
            {
                "ticket": t.ticket,
                "parent": t.parent,
                "phase": t.phase,
                "title": t.title,
                "project": t.project,
                "path": t.path,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                # finish が親のブランチへ合流した子の先頭（表示だけ。閉じた子の「合流」を出す）
                "merged_sha": t.merged_sha,
                "cancelled_at": t.cancelled_at,
                "cancel_reason": t.cancel_reason,
            }
        )
    return out


def _layers(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> list[dict]:
    """レイヤーごとの宣言。
    順序は 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）。

    rules は重複を破棄したあとの、そのレイヤーから実際に判定へ入ったぶん。phases と risk は
    そのレイヤーのファイルに書いてあるぶんで、合成の結果は `sums` と親のフェーズの
    側に出る。読めないレイヤーは `unreadable` に理由が入り、中身は空になる。
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


def _sums(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> list[dict]:
    """ワークスペースとプロジェクトごとの「共通 + 1 レイヤー」の和。

    `layers` はレイヤーごとの宣言で、重ねの途中経過（先のレイヤーと全欄が同じ定義は後ろから外れる）
    を含む。拡張はそれを足し直さず、ここの和をそのまま見せる。順は 自身のレイヤー → プロジェクト
    （名前順）。1 件の形:

        name     "self" か プロジェクトの名前
        layers   ["common", <name>]
        rules    path・unreadable・missing と、deny / ask / allow（共通レイヤーに足した和）
        risk     共通 + そのレイヤーの配点。levels（実際に使う境目の点）・factors・
                 problems・fallback
        phases   共通 + そのレイヤーの定義。path・unreadable（そのレイヤーのファイル）・types
                 （各定義の source が出どころのレイヤー。定義は順序を持たないので、先行関係の欄は
                 無い。順序は親の計画のステップの `after`）。足すときの衝突は `--lint` が言う

    判定の合成と同じ関数（`ruleload.sum_view` / `risk.layer_definition` /
    `phase.layer_types`）で組む。
    """
    said = stderr if stderr is not None else io.StringIO()
    out = []
    for layer in ruleload.layers(conf, root):
        project = "" if layer.name == ruleload.LAYER_SELF else layer.name
        view = ruleload.sum_view(said, conf, root, layer)
        definition, notes = risk.layer_definition(conf, root, project)
        phases_path = diagnose_shared.layer_config(conf, root, layer.name, settings.KIND_PHASES)
        _, phases_unreadable = diagnose_shared.layer_phase_set(phases_path)
        types, _ = phase.layer_types(conf, root, project)
        out.append(
            {
                "name": layer.name,
                "layers": [ruleload.LAYER_COMMON, layer.name],
                "rules": {
                    "path": layer.path,
                    "unreadable": view.unreadable,
                    "missing": view.missing,
                    **{
                        section: [_rule_record(rule) for rule in view.rule_set.section(section)]
                        for section in rules.SECTIONS
                    },
                },
                "risk": {
                    "levels": risk.effective_levels(definition.levels),
                    "factors": [
                        _factor_record(f.source or layer.name, f) for f in definition.factors
                    ],
                    "fallback": definition.fallback,
                    "problems": [str(p) for p in notes],
                },
                "phases": {
                    "path": phases_path,
                    "unreadable": phases_unreadable,
                    "types": [
                        _phase_type_record(pt.source or layer.name, pt)
                        for pt in (types.values() if types is not None else [])
                    ],
                },
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
    """フェーズ定義 1 つ。id は裸のまま、レイヤーは欄で出す。"""
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

    キーにツリーを入れるのは、まとめるのを「1 つの走査の重なり」に限るため。2 つのツリーが
    同じ実体を指す形（`projects/<名前>` がワークスペース自身への symlink など）は
    同じチケットが 2 つのツリーに在るのと同じで、判定の側（`approval._authoritative`）もまとめない。
    ここだけまとめると、ボードに何も出ていないのに操作が止まる。
    """
    kept: list[ticket_model.Ticket] = []
    seen: set[tuple[str, str]] = set()
    for t in found:
        key = (t.tree, pathcase.fold(os.path.abspath(t.path)))
        if key in seen:
            continue
        seen.add(key)
        kept.append(t)
    return kept


def hook_settings(
    conf: settings.Settings,
    root: str,
    stderr: TextIO | None = None,
    mode_flag: str = "",
    ticket_control_flag: str = "",
) -> tuple[str, str]:
    """hook がいま使うはずの動作モードとチケット制御を、判定と同じ解決の関数で決める。

    VS Code 拡張が起こした実行ファイルには `.claude/settings*.json` の `env` が渡らないので、
    `settings.hook_view` がそれを重ね直す。解決（読めない値は enable、設定ファイルから来た
    disable の扱い）は `modes.resolve_mode` と `selfguard.resolve` のまま。フラグが渡されれば
    それが勝つのも判定と同じ。読めない値の苦情は標準エラーに出す（標準出力は JSON だけ）。
    """
    view = settings.hook_view(root)
    complaints = io.StringIO()
    seen = dataclasses.replace(
        conf,
        mode=conf.mode_declared_in_file or view.mode,
        mode_from_environment=view.mode,
    )
    mode = modes.resolve_mode(complaints, mode_flag, seen)
    ticket_control = selfguard.resolve(
        complaints,
        ticket_control_flag,
        view.ticket_control,
        settings.TICKET_CONTROL_ENV,
        selfguard.GATE_SETTINGS,
    )
    if stderr is not None:
        stderr.write(complaints.getvalue())
    return mode, ticket_control


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
    """チケット 1 件。提案と承認済みチケットとワークツリーの今を 1 つにまとめる。

    処理フロー:
    1. 状態（`status`）は承認済みチケットの置き場で決める（review・open・closed、どちらにも無ければ
       none）。項目は提案があれば提案を、無ければ承認済みのコピー（`copy`）を使う。
    2. `tree.lookup` でワークツリーを引き、親なら MR の番号・ブランチ・リモートとの差を載せる。
    3. 満たしていない先行（`approval_checks.unmet_predecessors`）と、ブロックの理由を載せる。
    4. 承認の時刻（`approval_times`）、フロー（`flow.info`）、完了した親の下書きの入口を載せる。
    5. 子なら、親のワークツリーから `approval_marks.read_child_record` でリスクと判定を読む。
    """
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
        # 親を載せる MR の番号（親チケットの `mr:`）と、親のブランチ名。リモートとの差は
        # 親のワークツリーがあるときの `remote`（子は null）。
        "mr": source.mr,
        "branch": (
            standing.head_branch(found.root, ticket_id)
            if found is not None and not source.is_child
            else ""
        ),
        "remote": (
            overview.remote_info(found.root) if found is not None and not source.is_child else None
        ),
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
                # 親の提案が全体計画（`plan:`）を持つか。承認前の親は `parents[]` に出ないので、
                # ボードが計画の順序を直すタブの入口を出し分けるのに使う。タブ（`--plan-order`）は
                # `plan:` を持たない親を開けないので、`feedback:` だけの親は偽。子も偽。
                "has_plan": not proposal.is_child and proposal.has_plan,
            }
            if proposal is not None
            else None
        ),
        # 完了した親から下書きを作れるか（`ccnavi --templates --draft <親>` が通る形）。
        # ボードの親のカードが「テンプレートの下書きを作る」入口を出し分けるのに使う。
        "template_draft": (
            status == "closed"
            and not source.is_child
            and source.has_plan
            and source.state != ticket_model.CANCELLED
        ),
        # blocked は「読めるが信頼できない」理由。判定はこのチケットの
        # ワークツリーへの書き込みを全部止めるので、ボードが素の open として見せると、
        # 止まっていること自体がユーザに届かない。
        "blocked": (open_index[ticket_id].blocked if ticket_id in open_index else ""),
        "copy": (
            {
                "status": status,
                # 承認の時刻（`approval_times.approved_times`）。`approved_from` はどこから引いたか:
                # commit（doing/ に足したコミット）/
                # uncommitted（コミットが無い。手で置いてまだコミットしていない）/
                # 空（分からない）。uncommitted と空のとき approved_at は空。
                "approved_at": times.get(copy.path, approval_times.ApprovedTime()).at,
                "approved_from": times.get(copy.path, approval_times.ApprovedTime()).source,
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
        # finish が親のブランチへ合流した子の先頭（表示だけ。閉じた子の「合流」を出す）
        "merged_sha": source.merged_sha,
        "cancelled_at": source.cancelled_at,
        "cancel_reason": source.cancel_reason,
        # 作業中か。承認済みチケットが `doing/` にあり、着手していて、終わってもいないし
        # 取り消されてもいない。フローの `locked`（着手中・レビュー中は書き込みを止める）とは
        # 別の欄で、拡張は保存を止めるかをこれで決め、`started_at` などから組み直さない。
        "in_progress": (
            ticket_id in open_index
            and open_index[ticket_id].state == ticket_model.DOING
            and open_index[ticket_id].in_progress
        ),
        "seen_in": seen_in,
        "scattered": scattered,
        # 子のフロー（着手中は書き換えを止める）。
        # `{path, rel, tree, exists, linked, locked, lock_kind, draft}`。
        # draft はエージェントの下書きの `{path, rel, exists, linked}`（効力は無い）。親は null。
        # locked は判定がそのフローへの書き込みを止めているか。lock_kind はその種類
        # （`doing` は着手中、`review` はレビュー中、止めていなければ null）。読むのは承認済み
        # チケットがあればその側、無ければ提案。
        "flow": flow.info(conf, root, copy if copy is not None else source),
        "risk": None,
        "judge": None,
    }
    if source.parent:
        where = approval.home_dir_read(conf, root, ticket_id, source.parent, project=source.project)
        record["risk"] = approval_marks.read_child_record(
            where, source.parent, ticket_id, approval_marks.CHILD_RECORD_RISK
        )
        record["judge"] = approval_marks.read_child_record(
            where, source.parent, ticket_id, approval_marks.CHILD_RECORD_JUDGE
        )
    return record


def _mark_attention(payload: dict) -> None:
    """各チケットに `issues`（不備の文）と `attention`（ユーザが動く必要があるか）を足す。

    ボードはこの 2 つを読むだけにする（判定と同じ答えを 2 か所で出さないため）。
    `issues` はこの順に並ぶ。

    1. `blocked` が空でない: `書き込みが止まっています: <blocked>`
    2. 提案が無く、承認済みチケットも `open`（`doing/`）・`closed`（`done/`）に無い:
       `提案が見つかりません（承認済みチケットだけがあります）`
    3. 子で、親の識別子のチケットが `tickets` に無い: `親 <親> が見つかりません`

    `attention` は次のどれかで真。

    - `pending_approval` に入っている（新規の未承認と親の改版）
    - 子で、自分のフェーズが `gate_closed`（レビュー準備中／レビュー待ち）
    - 子で、自分のフェーズが `review_waiting`
    - 未着手・作業中の列（提案が無く `closed` のもの以外）なのに、ワークツリーが無い
    - 自分の実績のリスクが HIGH 以上（`risk.ESCALATE_FROM`）
    - `scattered` が空でない（どれが正か決まらない）
    - `issues` が空でない
    - 親で、フェーズのどれかが `gate_closed` / `review_waiting` / `risk_escalates`
    """
    ids = {t["ticket"] for t in payload["tickets"]}
    parents = {p["ticket"]: p for p in payload["parents"]}
    pending = set(payload["pending_approval"])
    for record in payload["tickets"]:
        record["issues"] = _issues(record, ids)
        record["attention"] = _attention(record, parents, pending)


def _issues(record: dict, ids: set[str]) -> list[str]:
    """チケット 1 件の不備の文（`_mark_attention`）。"""
    out = []
    if record["blocked"]:
        out.append(f"書き込みが止まっています: {record['blocked']}")
    if record["proposal"] is None and record["copy"]["status"] not in ("open", "closed"):
        out.append("提案が見つかりません（承認済みチケットだけがあります）")
    if record["parent"] and record["parent"] not in ids:
        out.append(f"親 {record['parent']} が見つかりません")
    return out


def _attention(record: dict, parents: dict[str, dict], pending: set[str]) -> bool:
    """チケット 1 件が要対応か（`_mark_attention`）。`issues` を足した後に呼ぶ。"""
    is_parent = not record["parent"]
    family = parents.get(record["ticket"] if is_parent else record["parent"])
    phases = family["phases"] if family is not None else []
    own = (
        next((p for p in phases if p["number"] == record["phase"]), None)
        if not is_parent and record["phase"] is not None
        else None
    )
    # 提案が無く閉じた承認済みチケットは完了・取り消しの列。それ以外は未着手か作業中の列
    open_column = not (record["proposal"] is None and record["copy"]["status"] == "closed")
    return (
        record["ticket"] in pending
        or (own is not None and own["gate_closed"])
        or (own is not None and own["review_waiting"])
        or (open_column and not record["worktree"]["exists"])
        or _high_risk(record["risk"])
        or bool(record["scattered"])
        or bool(record["issues"])
        or (
            is_parent
            and any(p["gate_closed"] or p["review_waiting"] or p["risk_escalates"] for p in phases)
        )
    )


def _high_risk(record: object) -> bool:
    """実績のリスクの記録が HIGH 以上か。フェーズの `risk_escalates` と同じ水準で見る。"""
    return isinstance(record, dict) and record.get("level") in risk.ESCALATE_FROM


def _phase_record(ph: phase.Phase, manual: str = "") -> dict:
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
        # 手で done/ に動かされた子の確認待ち・未共有など（`manual_settle`）。無ければ空
        "manual": manual,
    }


def _phase_records(
    root: str, conf: settings.Settings, parent: ticket_model.Ticket, raw: approval.Raw | None
) -> list[dict]:
    phases = phase.phases_of(root, conf, parent.ticket, raw=raw)
    out = []
    for ph in phases:
        line = ""
        if ph.review_required:
            line = manual_settle.assess(root, conf, parent, ph, phases).line()
        out.append(_phase_record(ph, line))
    return out


def _parent_record(
    conf: settings.Settings,
    root: str,
    parent: ticket_model.Ticket,
    closed_index: dict,
    raw: approval.Raw | None = None,
) -> dict:
    """親 1 件。局面、計画、親のマーカー、フェーズのリスト。`raw` は `phase.phases_of` と同じ。"""
    where = approval.home_dir_read(conf, root, parent.ticket, "", project=parent.project)
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
        "phases": _phase_records(root, conf, parent, raw),
    }
