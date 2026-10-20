"""セッション開始で示す、却下された要求（REQ-SES-02）。

承認済みチケットの allow / ask の項のうち、判定のときに上位に退けられて効かないものを、
要求・理由・出所つきで並べる。退ける側は 3 つで、判定と同じ順に見る。

1. ルールの deny（`rules` > チケット）。出所はルールの id とレイヤー
2. 親の範囲（子だけ）。出所は親の識別子
3. フェーズ定義の上限（子だけ。親に固定した写し `phases:` から引く）。出所は定義の名前

情報源は承認のときの記録ではなく、いまの承認済みチケットとルールからの再計算。承認は提案を
バイト単位で写すだけで、超過をどこにも残さない。記録を足すと、承認のあとにルールや
フェーズ定義が変わったときに古い事実を示すことになる。

glob 同士の包含は一般には決められないので、項を「字義どおりの前置 + 1 文字」のパスにして
当てる（`ticket.subset_problems` と同じプローブ）。近似なので、項の一部だけが退けられる形
（`src/*` のうち `src/secret/*` だけが deny）は拾わない。regex の項は当てるパスが作れないので
「確かめられない」として数えるだけにする。

判定は変えない。ここは見せるだけ。
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field

from ..infra import settings, tree
from ..policy import builtin, ruleload, rules, selfguard, selfguard_shell
from ..records import audit
from . import approval, approval_checks, phase_scope, phasetypes, ticket_guard, ticket_model

# 当てるツール。チケットの範囲が効くのは書き込み系で、項はツールを持たない。
_TOOLS = ("Write", "Edit", "NotebookEdit")
# 文に出す却下の件数の上限。残りは件数で言い、`--explain` へ誘う（常駐の文脈を増やさない）。
SHOWN = 3
# 走査に使う時間の上限（秒）。SessionStart の期限（`judge.DEADLINE_SECONDS`）の残りと
# 小さいほう。索引の更新（docsearch）に残りを渡すため、短く取る。
BUDGET_SECONDS = 0.5
# 1 回で当てる項の数の上限。承認済みチケット数 × 項数 × ルール数で伸びるので、ここで打ち切る。
MAX_ENTRIES = 500

APPROXIMATE_NOTE = (
    "重なりは glob の前置に 1 文字追加したパスで近似している"
    "（項の一部だけが退けられる形は拾わない）。"
)

ORIGIN_RULE = "ルール"
ORIGIN_PARENT = "親"
ORIGIN_TYPE = "フェーズ定義"


@dataclass
class Reject:
    """却下された要求 1 件。"""

    ticket: str
    # 項のタイプ（allow / ask）と、書かれた glob。
    decision: str
    request: str
    # 退けた理由（deny の message、範囲の外であること）。
    reason: str
    # 退けた側の名指し。「ルール no-secret（レイヤー common）」「親 i0001」
    # 「フェーズ定義 research」のどれか。
    origin: str


@dataclass
class Found:
    """走査の結果。"""

    rejects: list[Reject] = field(default_factory=list)
    # regex の項（当てるパスが作れず、確かめられない）の数。
    unchecked: int = 0
    # 期限か件数の上限で打ち切った。
    truncated: bool = False


def probe_of(entry: ticket_model.Entry) -> str:
    """項を当てるパス。ワイルドカードがあれば前置に 1 文字足す（`ticket.subset_problems`）。"""
    prefix = entry.prefix()
    return prefix + "x" if entry.glob != prefix else entry.glob


def _rule_set(conf: settings.Settings, root: str, target: tree.Tree) -> rules.RuleSet:
    """このツリーへの書き込みに当てるルール集合（共通レイヤーに行き先のレイヤーを足した和）。

    苦情は判定の経路が言うので、ここでは読み捨てる。
    """
    quiet = io.StringIO()
    record = audit.Record()
    rule_set, _ = ruleload.load_rules(quiet, conf, record, root)
    if record.fallback != builtin.FALLBACK:
        ruleload.add_layers(quiet, rule_set, ruleload.layer_for(conf, root, target), root, record)
    # ccnavi 自身の保護は組み込みにある（ルールファイルには無い）。実行後の保護範囲
    # （`events.watched_for`）と同じ条件で足す。足さないと、hooks・settings・scripts・共通の設定と
    # 承認済みの置き場が、退けた項として出なくなる。
    if conf.guard_core_files != selfguard.DISABLE:
        selfguard_shell.add_named_rules(rule_set, conf.project_home, root)
    if conf.tickets_enabled:
        rule_set.deny.append(ticket_guard.approved_write_rule(conf.approved, root))
    return rule_set


def _denied_by(rule_set: rules.RuleSet, full: str) -> rules.Rule | None:
    """このパスへの書き込みを止める deny。レイヤーの deny を土台より先に採る（判定の名指し）。"""
    hits = [r for r in rule_set.deny if any(r.matches(t, full) for t in _TOOLS)]
    if not hits:
        return None
    named = [r for r in hits if not r.base]
    return (named or hits)[0]


def _target_of(root: str, conf: settings.Settings, t: ticket_model.Ticket) -> tree.Tree:
    """チケットのワークツリー。まだ無くても、在るはずの場所で組む（ルールは場所で決まる）。"""
    path = tree.worktree_path(root, t.ticket)
    found = tree.tree_of(root, path, conf.projects) if os.path.isdir(path) else None
    if found is not None and not found.is_main:
        return found
    return tree.Tree(t.ticket, path, t.project, tree.KIND_WORKTREE)


def check(
    conf: settings.Settings,
    root: str,
    tickets: list[ticket_model.Ticket],
    index: dict[str, ticket_model.Ticket],
    deadline: float,
) -> Found:
    """チケットごとに、項を退ける上位を判定と同じ順で当てる。"""
    found = Found()
    loaded: dict[str, rules.RuleSet] = {}
    count = 0
    for t in tickets:
        if t.blocked:
            # 範囲そのものが効いていない。名指しは判定と `--lint` が別にする。
            continue
        target = _target_of(root, conf, t)
        if target.project not in loaded:
            loaded[target.project] = _rule_set(conf, root, target)
        rule_set = loaded[target.project]
        parent = index.get(t.parent) if t.is_child else None
        pt = phase_scope.type_for(t, parent)
        for entry in t.entries:
            if entry.decision == rules.DENY:
                continue
            count += 1
            if count > MAX_ENTRIES or time.monotonic() > deadline:
                found.truncated = True
                return found
            if entry.regex:
                found.unchecked += 1
                continue
            reject = _reject_of(t, entry, rule_set, target, parent, pt)
            if reject is not None:
                found.rejects.append(reject)
    return found


def _reject_of(
    t: ticket_model.Ticket,
    entry: ticket_model.Entry,
    rule_set: rules.RuleSet,
    target: tree.Tree,
    parent: ticket_model.Ticket | None,
    pt: phasetypes.PhaseType | None,
) -> Reject | None:
    """1 つの項を、ルールの deny → 親 → 定義の順に当てる。最初に退けた側を名指しする。"""
    probe = probe_of(entry)
    rule = _denied_by(rule_set, os.path.join(target.root, *probe.split("/")))
    if rule is not None:
        origin = f"{ORIGIN_RULE} {rules.record_name(rule)}（レイヤー {rule.source or '?'}）"
        return Reject(t.ticket, entry.decision, entry.glob, rule.spoken_message(), origin)
    if parent is not None and parent.decide(probe) not in (rules.ALLOW, rules.ASK):
        return Reject(
            t.ticket,
            entry.decision,
            entry.glob,
            "親の範囲の外",
            f"{ORIGIN_PARENT} {parent.ticket}",
        )
    if pt is not None and not pt.inherits_scope and pt.decide(probe) != rules.ALLOW:
        return Reject(
            t.ticket,
            entry.decision,
            entry.glob,
            f"定義の範囲 {', '.join(pt.scope_globs)} の外",
            f"{ORIGIN_TYPE} {pt.id}",
        )
    return None


def find_all(
    conf: settings.Settings,
    root: str,
    only: str = "",
    budget: float = BUDGET_SECONDS,
    deadline: float | None = None,
    copies: list[ticket_model.Ticket] | None = None,
) -> Found:
    """開いている承認済みチケットの却下を全件返す（絞りも文面も作らない）。

    `notice`（SessionStart）と `--explain` の両方が呼ぶ。`only` があれば、その
    チケット（ツリーの名前で引く）だけを見る。見つからなければ空。
    `copies` を渡せば置き場を読み直さない（`--explain` は読みを 1 度に保つ）。
    """
    limit = time.monotonic() + budget
    if deadline is not None:
        limit = min(limit, deadline)
    if copies is None:
        copies, _ = approval.scan(conf, root)
    index = approval_checks.by_id(copies)
    if not only:
        return check(conf, root, copies, index, limit)
    bound = tree.lookup(index, only)
    if bound is None:
        return Found()
    return check(conf, root, [bound], index, limit)


def explain_lines(found: Found) -> list[str]:
    """`--explain` の節の本文。1 件 1 行で、末尾に近似であることと確かめられない件数を添える。"""
    lines = [
        f"  {r.ticket} {r.decision} `{r.request}`: {r.reason}（出所: {r.origin}）"
        for r in found.rejects
    ]
    if found.truncated:
        lines.append("  （件数か時間の上限で途中まで数えた）")
    lines.append(f"  {APPROXIMATE_NOTE}" + _unchecked_note(found))
    return lines


def _unchecked_note(found: Found) -> str:
    return f"regex の項 {found.unchecked} 件は確かめられない。" if found.unchecked else ""


def notice(conf: settings.Settings, root: str, cwd: str, deadline: float | None = None) -> str:
    """SessionStart に足す文。却下が無ければ空。

    cwd のツリーに結び付く承認済みチケットだけを見る。ワークスペースルート（チケットを持たない
    ツリー）からは、開いているチケットのうち却下のあるものの件数を 1 行で言う。
    失敗したら何も言わず、セッションの開始は進める。
    """
    try:
        here = tree.tree_of(root, cwd or root, conf.projects)
        if here is None or here.is_main:
            return _count_line(find_all(conf, root, deadline=deadline))
        copies, _ = approval.scan(conf, root)
        bound = tree.lookup(approval_checks.by_id(copies), here.name)
        if bound is None:
            return ""
        found = find_all(conf, root, only=here.name, deadline=deadline, copies=copies)
        return render(bound.ticket, found)
    except Exception:  # noqa: BLE001 - 案内の足しで、セッションの開始を止めない
        return ""


def _count_line(found: Found) -> str:
    names = sorted({r.ticket for r in found.rejects})
    if not names:
        return ""
    cut = "（時間の上限で途中まで数えた）" if found.truncated else ""
    return (
        f"[ccnavi] 承認済みチケット {len(names)} 件に、上位の設定に却下された要求がある{cut}。"
        "そのワークツリーで始めたセッションに詳細を出す。ccnavi --explain でも範囲を見られる。"
    )


def render(ticket: str, found: Found) -> str:
    """却下の段落。件数と先頭の数件。空行を挟まない 1 段落にする（常駐の文脈を短く）。"""
    if not found.rejects:
        return ""
    total = len(found.rejects)
    more = "以上（時間の上限で途中まで数えた）" if found.truncated else ""
    lines = [
        f"[ccnavi] 承認済みチケット {ticket} の要求のうち {total} 件{more}は、"
        "上位の設定に却下されていて、判定のときに退けられる（書き込めない）。"
    ]
    for r in found.rejects[:SHOWN]:
        lines.append(f"- {r.decision} `{r.request}`: {r.reason}（出所: {r.origin}）")
    if total > SHOWN:
        lines.append(f"- ほか {total - SHOWN} 件。全部は ccnavi --explain で範囲とルールを見る。")
    else:
        lines.append("詳しくは ccnavi --explain で範囲とルールを見る。")
    lines.append(APPROXIMATE_NOTE + _unchecked_note(found))
    return "\n".join(lines)
