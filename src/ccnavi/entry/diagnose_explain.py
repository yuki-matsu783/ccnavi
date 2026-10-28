"""`--explain` の本文。レイヤーごとに、どのルール・フェーズ・リスクが効いているかを見せる。"""

from __future__ import annotations

import json
import os
import unicodedata
from typing import TextIO

from ..hook import judge
from ..infra import modes, settings, tree
from ..policy import builtin, ruleload, rules, selfguard
from ..tickets import session_rejects
from ..tickets.approval import checks as approval_checks
from ..tickets.approval import marks as approval_marks
from ..tickets.approval import store as approval
from ..tickets.approval import times as approval_times
from ..tickets.config import phasetypes, risk, workflow
from ..tickets.model import ticket as ticket_mod
from ..tickets.model import ticket_model
from ..tickets.phase import gates as phase
from . import diagnose_board, diagnose_shared

# レイヤーの見出し。共通レイヤーと自身のレイヤーだけ日本語の名前で出す。
# プロジェクトは名前そのもので、それが id の前置き（`lib:schema`）と同じ表記になる。
LAYER_LABELS = {
    ruleload.LAYER_COMMON: "共通レイヤー",
    ruleload.LAYER_SELF: "ワークスペースのレイヤー",
}


# レイヤー列の表示幅（全角は 2 桁）。「ワークスペースのレイヤー」が収まる幅
_LAYER_COL = 26


def _pad(text: str, width: int) -> str:
    """表示幅 `width` になるまで右に空白を足す（全角は 2 桁で数える）。"""
    used = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - used)


# 却下の走査に使う時間の上限（秒）。SessionStart と違って期限が無いので長めに取る。
EXPLAIN_REJECTS_BUDGET = 5.0


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
    """レイヤーごとのフェーズ定義。id は裸のまま、レイヤーは欄で出す。"""
    tables = [
        (
            v.name,
            *diagnose_shared.layer_phase_types(
                diagnose_shared.layer_config(conf, root, v.name, settings.KIND_PHASES),
            ),
        )
        for v in views
    ]
    counts = "、".join(f"{layer_label(name)} {len(items)} 種" for name, items, _ in tables)
    stdout.write(
        f"\n■ phases（{counts}。共通レイヤーと、親チケットの project: で"
        "指定されたレイヤーを組み合わせて適用します）\n"
    )
    stdout.write(
        f"  {'id':<16}{_pad('レイヤー', _LAYER_COL)}{'kind':<8}{'title':<16}{'review':<8}scope\n"
    )
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(
                f"  {layer_label(name)}: 読めない: {unreadable}。このレイヤーは空として扱う\n"
            )
        for pt in items:
            scope = (
                ", ".join(diagnose_shared.written(e) for e in pt.scope) if pt.scope else "inherit"
            )
            head = f"  {pt.id:<16}{_pad(layer_label(name), _LAYER_COL)}{pt.kind:<8}{pt.title:<16}"
            stdout.write(f"{head}{pt.review:<8}{scope}\n")


def _explain_risk(
    stdout: TextIO, conf: settings.Settings, root: str, views: list[ruleload.LayerView]
) -> None:
    """レイヤーごとのリスクの配点。境目の点は共通レイヤーのものを出す。"""
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
    # チケットのレイヤーで決まる。ここに出すのは共通レイヤーの側の既定。
    effective = risk.effective_levels(common.levels)
    levels = " / ".join(f"{k} {effective[k]}" for k in ("medium", "high", "critical"))
    stdout.write(f"\n■ risk（levels: {levels}）\n")
    stdout.write(
        f"  {'id':<16}{_pad('レイヤー', _LAYER_COL)}{'加点条件':<20}{'points':<8}message\n"
    )
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(
                f"  {layer_label(name)}: 読めない: {unreadable}。このレイヤーは空として扱う\n"
            )
        for factor in items:
            how = f"{factor.kind} {factor.value}"
            stdout.write(
                f"  {factor.id:<16}{_pad(layer_label(name), _LAYER_COL)}{how:<20}"
                f"{factor.points:<8}{factor.message}\n"
            )


# どのルールも言及しない呼び出しの結末（`judge.undeclared_verdict` の値）を、ユーザ向けの言葉に。
_UNDECLARED_OUTCOME = {
    judge.HANDOVER: "権限モードに委ねる",
    rules.ASK: "ccnavi が確認を出す",
    rules.DENY: "ccnavi が止める",
}
# 委ねた先がモードごとに違うときの補足。auto だけは判断するのが classifier。
_HANDOVER_NOTE = {
    "auto": "（classifier が判断）",
}
# 名前を知らないモードと、payload に権限モードが無い呼び出しの行の見出し。判定はどちらも
# 同じ扱い（知らない名前）にする。
_UNKNOWN_MODE = "(不明・無し)"


def _explain_undeclared(stdout: TextIO, conf: settings.Settings) -> None:
    """どのルールも言及しない呼び出しの結末を、権限モード × 読めたか で表にする。

    表は判定と同じ関数（`judge.undeclared_verdict`）で組む。文で書くと、判定を変えたときに
    ここだけ古い説明が残る。`conf.guard_unwatched` は hook と同じく解いた後の値で渡される
    （cli の `_resolve_switches`）。
    """
    stdout.write("\n■ どのルールにも一致しないツール呼び出し\n")
    stdout.write(
        "  ccnavi は判定を持たない。読めた呼び出しは Claude Code の権限モードに委ねる。"
        "読み切れない呼び出し（実行される部分まで読み解けず、生の文字列に適用したコマンド）は、"
        "読めなかったことを言うためにモードを問わず委ねない\n"
    )
    for name in (*judge.PERMISSION_JUDGED, *judge.PERMISSION_NO_JUDGE, _UNKNOWN_MODE):
        pm = "" if name == _UNKNOWN_MODE else name
        read, unread = (
            judge.undeclared_verdict(pm, degraded, conf.guard_unwatched) for degraded in ("", "x")
        )
        shown = _UNDECLARED_OUTCOME[read] + (
            _HANDOVER_NOTE.get(name, "") if read == judge.HANDOVER else ""
        )
        # 見出しの `(不明・無し)` は全角を含むので、表示の幅で揃える。
        pad = " " * max(
            1, 19 - sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in name)
        )
        stdout.write(
            f"    {name}{pad}読めた: {shown} / 読み切れない: {_UNDECLARED_OUTCOME[unread]}\n"
        )
    stdout.write(
        f"  {settings.GUARD_UNWATCHED_ENV}={conf.guard_unwatched or selfguard.ENABLE}"
        f"（{' / '.join(judge.PERMISSION_NO_JUDGE)} の読めた呼び出しの結末を決める。"
        "disable なら委ね、それ以外は止める）\n"
    )


def _explain_modes(stdout: TextIO, conf: settings.Settings) -> None:
    """dry-run で止めるものと止めないもの、判定に達せなかったときの扱い。

    止める箇所の分類（`modes.GENERAL`・`GATE`・`AUX`）と、ツールごとの扱い（`modes.fail_closed`）は
    判定と同じ定数・関数から組む。チケット制御が無効なら、関門と補助の保護も効かない。
    """
    stdout.write("\n■ dry-run のときに止めるもの\n")
    stdout.write(
        "  一般の保護（ルールの deny・ask）: dry-run は止めずに報告だけにする\n"
        "  チケットの関門（状態の操作、承認済みの置き場、範囲、レビュー待ち）と、その抜け道を"
        "ふさぐ補助の保護（生の git、環境変数の指定など）: dry-run でも止める\n"
    )
    if not conf.tickets_enabled:
        stdout.write(
            f"  {settings.TICKET_CONTROL_ENV}=disable なので、関門と補助の保護も効いていない\n"
        )
    stdout.write("\n■ 判定に達せなかったとき（内部の例外・期限切れ・payload が読めない）\n")
    for tool in ("", "Write", "Read", "Bash", "SubagentHandback", "Foo"):
        label = {
            "": "ツールが分からない",
            "Write": "Write・Edit・NotebookEdit・Agent・Task",
            "Read": "Read・Grep・Glob",
            "SubagentHandback": "サブエージェントの報告用（SubagentHandback・StructuredOutput）",
            "Bash": "Bash",
            "Foo": "知らないツール",
        }[tool]
        enable = "止める" if modes.fail_closed(modes.ENABLE, tool) else "通す"
        dry = "止める" if modes.fail_closed(modes.DRY_RUN, tool) else "通す"
        safe = "止める" if modes.fail_closed(modes.ENABLE, tool, safe=True) else "通す"
        if tool in modes.READ_TOOLS:
            stdout.write(
                f"  {label}: 最低限の deny にヒットしなければ{safe}、ヒットすれば{enable}\n"
            )
        elif tool in modes.SHELL_TOOLS:
            stdout.write(
                f"  {label}: 最低限の許可リストにヒットすれば{safe}、ヒットしなければ{enable}\n"
            )
        else:
            stdout.write(f"  {label}: enable は{enable}、dry-run は{dry}\n")
    stdout.write(
        "  内部の期限には限りがあり、期限切れも「判定に達せなかった」として上の扱いになる\n"
    )


def explain(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """いま有効な宣言を、判定を行わずに一覧する。

    何がどう宣言されているかを見せる。
    実効権限をパスごとに数え上げるには、宣言済み領域という概念が要る。
    それはまだ無いので、ここで言えるのは「どのルールがどのタイプにあるか」と
    「チケットの範囲が有効か」まで。言えないことは言わない。
    """
    # レイヤーごとに、宣言されたルールを書く。
    views = ruleload.survey(stderr, conf, root)
    source = builtin.SOURCE if views[0].unreadable else conf.rules
    stdout.write(f"ccnavi: いま有効な宣言（出所 {source}）\n")
    stdout.write(
        "  パスを持つツールは共通レイヤー + 対象のレイヤー、"
        "持たないツールは全部のレイヤーを組み合わせて判定する"
        "\n"
    )

    for view in views:
        counts = " / ".join(f"{name} {len(view.rule_set.section(name))}" for name in rules.SECTIONS)
        stdout.write(f"\n■ rules {layer_label(view.name)}（{_shown(root, view.path)}、{counts}）\n")
        if view.unreadable:
            stdout.write(f"  読めない: {view.unreadable}。このレイヤーは空として扱う\n")
            continue
        if view.missing:
            stdout.write("  このレイヤーの設定ファイルはありません。設定なしとして扱います。\n")
            continue
        for name in rules.SECTIONS:
            for rule in view.rule_set.section(name):
                written = rule.written_body()
                if rule.excepts:
                    excepts = ", ".join(e.glob or e.regex for e in rule.excepts)
                    written = f"{written}  except: {excepts}"
                stdout.write(f"  {name:<5} {rule.id:<28} {rule.match:<34} {written}\n")
                # 組み合わせ条件は 1 行だと入れ子が読みにくいので、
                # 個別条件が 3 つ以上なら続けて字下げで出す。
                if rule.when is not None and sum(n.is_leaf() for n in rule.when.walk()) >= 3:
                    for line in rule.when.lines():
                        stdout.write(f"        {line}\n")

    _explain_phases(stdout, conf, root, views)
    _explain_risk(stdout, conf, root, views)

    _explain_undeclared(stdout, conf)
    _explain_modes(stdout, conf)

    projectless = tree.worktrees_without_project(root, conf.projects)
    if projectless:
        stdout.write("\n■ 元のプロジェクトが無いワークツリー\n")
        for w in projectless:
            stdout.write(f"  {w.said()}\n")

    stdout.write("\n■ チケットの作業範囲（承認済みチケット）\n")
    stdout.write(
        "  ワークツリーに結び付いたチケットの範囲は、ルールの allow / ask より優先されます。"
        "編集可能範囲の外への書き込みは拒否されます。\n"
    )
    stdout.write(f"  チケット制御: {conf.ticket_control or settings.TICKET_CONTROL_ENABLE}\n")
    stdout.write(f"  学びの蓄積: {'enable' if conf.learning_enabled else 'disable'}\n")
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
        stdout.write("  承認されたチケットがありません。範囲の制限は掛かっていない\n")
        return 0
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    preds = approval_checks.predecessor_pool_of(copies, review, closed, proposals)
    approval_checks.align_imported(conf, root, preds)
    times = approval_times.approved_times(conf, copies + review)
    # チケットを 1 件ずつ、作業範囲（承認済みチケットの欄）として書く。
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
        where = approval.home_dir_read(conf, root, parent.ticket, "", project=parent.project)
        wrapped = approval_marks.read_parent_mark(
            where, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY
        )
        if wrapped:
            stdout.write(
                f"  {parent.ticket} はユーザが早めにクローズした: {wrapped.get('reason', '')}\n"
            )
        if approval_marks.read_parent_mark(where, parent.ticket, approval_marks.PARENT_MARK_READY):
            stdout.write(
                f"  {parent.ticket} のマージリクエストの Draft を解除した。マージはユーザが行う\n"
            )
        for ph in phase.phases_of(root, conf, parent.ticket, raw=raw):
            marks = ", ".join(sorted(ph.marks)) or "マーカーなし"
            if not ph.tickets:
                state = "未計画（子チケットはまだありません）"
            elif ph.ended:
                state = "終了"
            else:
                state = "進行中"
            hold = ph.review_label if ph.gate_closed else "止めていない"
            review = {
                phasetypes.REVIEW_MR: " / レビューはマージリクエストで",
            }.get(ph.review_kind, "")
            if ph.deferred:
                review = f" / レビューは {ph.review_at} と一緒に"
            elif ph.covers:
                review += f" / {', '.join(str(c) for c in ph.covers)} の分も見る"
            if ph.risk_line:
                review += f" / {ph.risk_line}"
                if ph.risk_escalates:
                    review += "（実績でレビュー必須）"
            if ph.planned:
                waits = workflow.waits_of(parent, ph.number)
                review += (
                    f" / 先行 {', '.join(map(str, waits))}" if waits else " / 先行フェーズなし"
                )
            stdout.write(
                f"  {parent.ticket} フェーズ {ph.label}: {state} / {marks} / {hold}{review}\n"
            )
    _explain_rejects(stdout, conf, root, copies)
    return 0


def _explain_rejects(
    stdout: TextIO, conf: settings.Settings, root: str, copies: list[ticket_model.Ticket]
) -> None:
    """上位の設定に却下された要求を全件。SessionStart は数件に絞るので、全部はここで見せる。

    該当が無ければ節を出さない。見せるだけで、判定は変えない。
    """
    try:
        found = session_rejects.find_all(conf, root, budget=EXPLAIN_REJECTS_BUDGET, copies=copies)
    except Exception:  # noqa: BLE001 - 一覧の足しで、--explain を止めない
        return
    if not found.rejects:
        return
    stdout.write("\n■ 上位の設定に却下された要求\n")
    for line in session_rejects.explain_lines(found):
        stdout.write(f"{line}\n")


def explain_json(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    mode_flag: str = "",
    ticket_control_flag: str = "",
) -> int:
    """`--explain` が言うことのうち、チケットに関わる部分を機械可読で出す。

    読み手は VS Code のボード拡張。拡張は自分で解釈せず、ここが
    出した形をそのまま並べる。「レビューで止まっているか」「承認待ちは何か」の答えを
    2 か所で出さないためのもので、判定と同じ関数（phase / approval）で組む。
    ネットワークには出ない。見るのはワークスペースの中のファイルだけ。
    """
    payload = diagnose_board.board(conf, root, stderr, mode_flag, ticket_control_flag)
    stdout.write(json.dumps(payload, ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0
