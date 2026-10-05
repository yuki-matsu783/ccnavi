"""`--explain` の本文。レイヤーごとに、どのルール・フェーズ・リスクが効いているかを見せる。"""

from __future__ import annotations

import json
import os
from typing import TextIO

from ..infra import settings, tree
from ..policy import builtin, ruleload, rules, selfguard
from ..tickets import (
    approval,
    approval_checks,
    approval_marks,
    approval_times,
    phase,
    phasetypes,
    risk,
    ticket_model,
    workflow,
)
from ..tickets import ticket as ticket_mod
from . import diagnose_board, diagnose_shared

# レイヤーの見出し。共通レイヤーと自身のレイヤーだけ日本語の名前で出す。
# プロジェクトは名前そのもので、それが id の前置き（`lib:schema`）と同じ表記になる。
LAYER_LABELS = {ruleload.LAYER_COMMON: "共通レイヤー", ruleload.LAYER_SELF: "自身のレイヤー"}


def layer_label(name: str) -> str:
    return LAYER_LABELS.get(name, name)


def _shown(root: str, path: str) -> str:
    """パスをワークスペースルートからの相対で出す。外に在るなら書かれたまま。"""
    if not path:
        return "(無し)"
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    return path if rel.startswith(os.pardir) else rel.replace(os.sep, "/")


def _explain_phases(
    stdout: TextIO, conf: settings.Settings, root: str, views: list[ruleload.LayerView]
) -> None:
    """レイヤーごとのフェーズの種類（設計 11.9）。id は裸のまま、レイヤーは欄で出す。"""
    tables = [
        (
            v.name,
            *diagnose_shared.layer_phase_types(
                diagnose_shared.layer_config(conf, root, v.name, settings.KIND_PHASES)
            ),
        )
        for v in views
    ]
    counts = "、".join(f"{layer_label(name)} {len(items)} 種" for name, items, _ in tables)
    stdout.write(f"\n■ phases（{counts}）\n")
    stdout.write(f"  {'id':<16}{'レイヤー':<10}{'kind':<8}{'title':<16}{'review':<8}scope\n")
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(
                f"  {layer_label(name)}: 読めない: {unreadable}。このレイヤーは空として扱う\n"
            )
        for pt in items:
            scope = (
                ", ".join(diagnose_shared._written(e) for e in pt.scope) if pt.scope else "inherit"
            )
            head = f"  {pt.id:<16}{layer_label(name):<10}{pt.kind:<8}{pt.title:<16}"
            stdout.write(f"{head}{pt.review:<8}{scope}\n")


def _explain_risk(
    stdout: TextIO, conf: settings.Settings, root: str, views: list[ruleload.LayerView]
) -> None:
    """レイヤーごとのリスクの配点（設計 11.9）。境目の点は共通レイヤーのものを出す。"""
    tables = [
        (
            v.name,
            *diagnose_shared.layer_risk(
                conf, v.name, diagnose_shared.layer_config(conf, root, v.name, settings.KIND_RISK)
            ),
        )
        for v in views
    ]
    common, _ = risk.load(conf.risk)
    # 共通レイヤーの境目の点。
    # レイヤーの `levels` はキーごとに小さいほうを採るので、実際に使われる値は
    # チケットのレイヤーで決まる（設計 11.4.2）。ここに出すのは共通レイヤーの側の既定。
    effective = risk.effective_levels(common.levels)
    levels = " / ".join(f"{k} {effective[k]}" for k in ("medium", "high", "critical"))
    stdout.write(f"\n■ risk（levels: {levels}）\n")
    stdout.write(f"  {'id':<16}{'レイヤー':<10}{'加点条件':<20}{'points':<8}message\n")
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(
                f"  {layer_label(name)}: 読めない: {unreadable}。このレイヤーは空として扱う\n"
            )
        for factor in items:
            how = f"{factor.kind} {factor.value}"
            stdout.write(
                f"  {factor.id:<16}{layer_label(name):<10}{how:<20}"
                f"{factor.points:<8}{factor.message}\n"
            )


def explain(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """いま有効な宣言を、判定を行わずに一覧する（REQ-DIA-01）。

    どこが守られているかではなく、何がどう宣言されているかを見せる。
    実効権限をパスごとに数え上げるには、宣言済み領域という概念が要る。
    それはまだ無いので、ここで言えるのは「どのルールがどのタイプにあるか」と
    「チケットの範囲が有効か」まで。言えないことは言わない。
    """
    views = ruleload.survey(stderr, conf, root)
    source = builtin.SOURCE if views[0].unreadable else conf.rules
    stdout.write(f"ccnavi: いま効いている宣言（出所 {source}）\n")
    stdout.write(
        "  パスを持つツールは共通レイヤー + 行き先のレイヤー、"
        "持たないツールは全部のレイヤーの和で判定する"
        "（設計 11.4）\n"
    )

    for view in views:
        counts = " / ".join(f"{name} {len(view.rule_set.section(name))}" for name in rules.SECTIONS)
        stdout.write(f"\n■ rules {layer_label(view.name)}（{_shown(root, view.path)}、{counts}）\n")
        if view.unreadable:
            stdout.write(f"  読めない: {view.unreadable}。このレイヤーは空として扱う\n")
            continue
        if view.missing:
            stdout.write("  このレイヤーは置いていない（無い = 空）\n")
            continue
        for name in rules.SECTIONS:
            for rule in view.rule_set.section(name):
                written = rule.glob or rule.regex
                stdout.write(
                    f"  {name:<5} {rule.id or '(id 無し)':<28} {rule.match:<34} {written}\n"
                )

    _explain_phases(stdout, conf, root, views)
    _explain_risk(stdout, conf, root, views)

    stdout.write("\n■ どのルールも言及しない呼び出し\n")
    stdout.write("  ccnavi は判定を下さず、Claude Code の権限モードに従う\n")
    stdout.write(
        "    auto                          classifier（auto モードで呼び出しを通すかを決める、"
        "Claude Code の判定役のモデル）が判断する\n"
    )
    stdout.write("    default / acceptEdits / plan  Claude Code 自身の権限の仕組みが決める\n")
    stdout.write("    不明なモード                  ユーザに確認が出る\n")
    # ここだけはレイヤーの設定で変わるので、書いてあるとおりの結末を出す。
    if (conf.guard_unwatched or "").strip().lower() == selfguard.DISABLE:
        stdout.write(
            "    dontAsk / bypassPermissions   そのモードに委ねる"
            f"（{settings.GUARD_UNWATCHED_ENV}=disable）\n"
        )
    else:
        stdout.write("    dontAsk / bypassPermissions   確認できる者が居ないので通さない\n")

    stdout.write("\n■ チケットの作業範囲（承認済みチケット）\n")
    stdout.write(
        "  ワークツリーに結び付いたチケットの範囲は、ルールの allow / ask より優先される。"
        "範囲の外は止まる\n"
    )
    stdout.write(f"  チケット制御: {conf.ticket_control or settings.TICKET_CONTROL_ENABLE}\n")
    if not conf.tickets_enabled:
        stdout.write(f"  {settings.TICKET_CONTROL_ENV}=disable。範囲の制限は掛かっていない\n")
        return 0
    # 承認済みチケットの置き場はここで 1 度だけ読み、局面とフェーズ（`phase.stage`・
    # `phase.phases_of`）まで持ち回る。ここは読むだけで、置き場のファイルを動かさない。
    raw = approval.read_raw(conf, root)
    copies, notes = approval.scan(conf, root, raw=raw)
    for note in notes:
        stdout.write(f"  {note}\n")
    if not copies:
        stdout.write("  承認されたチケットが無い。範囲の制限は掛かっていない\n")
        return 0
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    preds = approval_checks.predecessor_pool_of(copies, review, closed, proposals, root)
    approval_checks.align_imported(conf, root, preds)
    times = approval_times.approved_times(conf, copies + review)
    for t in sorted(copies + review, key=lambda x: (x.parent or x.ticket, x.ticket)):
        where = tree.worktree_path(root, t.ticket)
        bound = "ワークツリーあり" if tree.is_worktree_of(root, where) else "ワークツリー無し"
        place = "レビュー待ち" if t.state == ticket_model.REVIEW else "作業中"
        when = times.get(t.path, approval_times.ApprovedTime()).label()
        head = f"{t.ticket}（{t.title}、承認 {when}、{place}、{bound}）"
        if t.is_child:
            unmet = approval_checks.unmet_predecessors(t, preds)
            need = "要" if t.review_required else "不要"
            head += f" 親 {t.parent} フェーズ {t.phase} レビュー{need}"
            if unmet:
                head += " 先行を満たしていない: " + ", ".join(
                    f"{p.ticket}（{p.label}）" for p in unmet
                )
        stdout.write(f"  {head}\n")
        for name in rules.SECTIONS:
            for path in t.paths(name):
                stdout.write(f"    {name:<5} {path}\n")
    for parent in [t for t in copies if not t.is_child]:
        where = phase.stage(root, conf, parent, raw)
        if where:
            stdout.write(f"  {parent.ticket} の局面: {where}\n")
        where = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
        wrapped = approval_marks.read_parent_mark(
            where, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY
        )
        if wrapped:
            stdout.write(f"  {parent.ticket} はユーザが早めに閉じた: {wrapped.get('reason', '')}\n")
        if approval_marks.read_parent_mark(where, parent.ticket, approval_marks.PARENT_MARK_READY):
            stdout.write(
                f"  {parent.ticket} のマージリクエストの Draft を外した。マージはユーザが行う\n"
            )
        for ph in phase.phases_of(root, conf, parent.ticket, raw=raw):
            marks = ", ".join(sorted(ph.marks)) or "マーカーなし"
            if not ph.tickets:
                state = "未計画（子がまだ無い）"
            elif ph.ended:
                state = "終了"
            else:
                state = "進行中"
            hold = ph.review_label if ph.gate_closed else "止めていない"
            review = {
                phasetypes.REVIEW_MR: " / レビューはマージリクエストで",
                phasetypes.REVIEW_CHAT: " / レビューはこのセッションで",
            }.get(ph.review_kind, "")
            if ph.deferred:
                review = f" / レビューは {ph.review_at} と一緒に"
            elif ph.covers:
                review += f" / {', '.join(str(c) for c in ph.covers)} の分も見る"
            if ph.risk_line:
                review += f" / {ph.risk_line}"
                if ph.risk_escalates:
                    review += "（実績でレビュー要）"
            if phase.is_dag(parent) and parent.in_plan(ph.number):
                waits = workflow.waits_of(parent, ph.number, None)
                review += f" / 待つ: {', '.join(map(str, waits))}" if waits else " / 何も待たない"
            stdout.write(
                f"  {parent.ticket} フェーズ {ph.label}: {state} / {marks} / {hold}{review}\n"
            )
    return 0


def explain_json(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """`--explain` が言うことのうち、チケットに関わる部分を機械可読で出す。

    読み手は VS Code のボード拡張。拡張は提案・承認済みチケット・マーカーを自分で解釈せず、ここが
    出した形をそのまま並べる。「レビューで止まっているか」「承認待ちは何か」の答えを
    2 か所で出さないためのもので、判定と同じ関数（phase / approval）で組む。
    ネットワークには出ない。見るのはワークスペースの中のファイルだけ（設計 3 P11）。
    """
    stdout.write(json.dumps(diagnose_board.board(conf, root, stderr), ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0
