"""判定を実行せずに試す。人が端末から叩く経路。

## なぜ要るか

ルールを 1 件書いたとき、それが何に当たるのかは走らせるまで分からない。
`glob` は正規表現に翻訳されるし、Bash のコマンドは実行される部分まで
絞られてから当たる。書いた人の頭の中の当たり方と、実際の当たり方がずれても、
ずれたことに気づく手立てが無かった。気づかないルールは、足したつもりで
何も止めていない 1 行になる。

## 判定と同じ道を通る

ここは判定を作り直さない。payload を組み立てて `cli.decide_before` を
そのまま呼び、返った応答と記録を読んで人に見せる。別の道で判定すると、
試験で通ったものが実運用で落ちる、という一番まずい形になる（REQ-DIA-03）。

モードは常に enable で動かす。試験は「止まるかどうか」を問うものなので、
呼び出しに手を出さないモードの結果を見せても答えになっていない。
実際に走っているセッションが dry-run でも、ここは enable の判定を返す。
"""

from __future__ import annotations

import io
import json
import time
from typing import TextIO

from . import audit, hookio, rules, settings

# `--explain --json` の形の版。読み手（VS Code 拡張）が形の違いに気づけるように。
BOARD_VERSION = 1

# 対象を取り出せるツール。ここに無いツールは判定に届かないまま通るので、
# 試したい人には「当たらない」ではなく「そもそも見ていない」と言う。
KNOWN_TOOLS = ("Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Agent")


def test(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    tool: str,
    subject: str,
) -> int:
    """1 件を判定して、結果とすべての理由を書く。終了コードは常に 0。

    判定の内容を終了コードで表さない。deny を非ゼロにすると、試験を回す側が
    「拒否された」と「試験そのものが失敗した」を見分けられなくなる。
    結果は 1 行目の `verdict:` で読む。
    """
    from .cli import DEADLINE_SECONDS, MODE_ENABLE, decide_before, subject_of

    if tool not in KNOWN_TOOLS:
        stdout.write(f"verdict: (判定に入らない)\ntool: {tool}\n")
        stdout.write(
            f"note: {tool} は判定が対象を取り出せないツール。ルールを書いても当たらず、"
            "呼び出しはそのまま通る\n"
        )
        return 0

    field = {"Bash": "command", "Agent": "description"}.get(tool, "file_path")
    payload = hookio.Input(
        event=hookio.PRE_TOOL_USE,
        tool_name=tool,
        tool_input={field: subject},
        cwd=root,
    )
    record = audit.Record(
        mode=MODE_ENABLE,
        event=payload.event,
        tool=tool,
        subject=subject_of(payload),
    )

    # 応答は捨てずに拾う。判定が返す文面そのものを見せたいので、
    # ここで文を組み直さない。組み直すと、試験で読んだ文と
    # エージェントに届く文が別物になる。
    captured = io.StringIO()
    decide_before(
        captured,
        stderr,
        MODE_ENABLE,
        conf,
        root,
        payload,
        record,
        time.monotonic() + DEADLINE_SECONDS,
    )

    verdict = record.decision or audit.ALLOW
    stdout.write(f"verdict: {verdict}{f' ({record.code})' if record.code else ''}\n")
    stdout.write(f"tool: {tool}\n")
    stdout.write(f"subject: {subject}\n")
    if record.subject and record.subject != subject:
        # ファイルのパスは行き着く先まで解いてから当てる。解いた先を見せないと、
        # 当たらなかった理由が「綴りが違う」なのかどうかを人が言えない。
        stdout.write(f"resolved: {record.subject}\n")
    if record.reason:
        stdout.write(f"reason: {record.reason}\n")
    if record.degraded:
        stdout.write(f"degraded: {record.degraded}（生の文字列に当てた）\n")
    if record.fallback:
        stdout.write(f"fallback: {record.fallback}（組み込みの既定で判定した）\n")

    _rules_hit(stdout, stderr, conf, root, record)
    _response(stdout, captured.getvalue())
    return 0


def _rules_hit(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> None:
    """当たったルールを、区画と翻訳後の式まで見せる。

    翻訳後の式を出すのがこの試験の要。`glob` は正規表現に化けるので、
    書いたものと当たるものの間に見えない層が 1 枚ある。その層を開けないと、
    当たらなかった理由を人が自分で辿れない。
    """
    from .cli import load_rules

    if not record.rules:
        stdout.write("rules: (どのルールにも当たらなかった)\n")
        return

    rule_set, _ = load_rules(stderr, conf.rules, audit.Record(), root)
    by_id = {rule.id: rule for rule in rule_set.all() if rule.id}

    stdout.write("rules:\n")
    for name in record.rules:
        rule = by_id.get(name)
        if rule is None:
            # チケットの範囲のように、ルールファイルの中に無い根拠。
            stdout.write(f"  {name}（ルールファイルの外から来た根拠）\n")
            continue
        written = f"glob {rule.glob!r}" if rule.glob else f"regex {rule.regex!r}"
        stdout.write(f"  {rule.decision}:{name}  {written}\n")
        stdout.write(f"    -> {rule.compiled.pattern if rule.compiled else '(組み立て失敗)'}\n")


def _response(stdout: TextIO, written: str) -> None:
    """判定が返した文面をそのまま見せる。

    止めるだけでは足りない、というのがこの道具の目的なので、試験でも
    「何が返るか」まで見せる。文面の無い拒否は、受け取った側に
    次の一手が無い。
    """
    if not written.strip():
        stdout.write("response: (何も返さない)\n")
        return
    try:
        out = json.loads(written)["hookSpecificOutput"]
    except (ValueError, KeyError):
        stdout.write(f"response: {written.strip()}\n")
        return
    text = out.get("permissionDecisionReason") or out.get("additionalContext") or ""
    stdout.write("response:\n")
    for line in text.splitlines():
        stdout.write(f"  {line}\n")


def explain(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """いま効いている宣言を、判定を行わずに一覧する（REQ-DIA-01）。

    どこが守られているかではなく、何がどう宣言されているかを見せる。
    実効権限をパスごとに数え上げるには、宣言済み領域という概念が要る。
    それはまだ無いので、ここで言えるのは「どのルールがどの区画にあるか」と
    「チケットの範囲が効いているか」まで。言えないことは言わない。
    """
    from . import approval, phase, tree
    from .cli import load_rules

    rule_set, source = load_rules(stderr, conf.rules, audit.Record(), root)
    stdout.write(f"ccnavi: いま効いている宣言（出所 {source}）\n")

    for name in rules.SECTIONS:
        section = rule_set.section(name)
        stdout.write(f"\n■ {name}（{len(section)} 件）\n")
        for rule in section:
            written = rule.glob or rule.regex
            stdout.write(f"  {rule.id or '(id 無し)':<28} {rule.match:<34} {written}\n")

    projects = tree.projects(conf.projects)
    if projects:
        stdout.write(f"\n■ プロジェクト（{len(projects)} 件、置き場 {conf.projects}）\n")
        stdout.write(
            "  パスを持つツールは行き先のプロジェクトのルールで判定し、Bash は"
            "ワークスペースと全プロジェクトのルールの和で判定する（設計 §25.4）\n"
        )
        for p in projects:
            path = settings.project_rules_path(conf, tree.project_root(conf.projects, p.name))
            try:
                extra, _ = rules.load(path, root)
            except (OSError, ValueError) as exc:
                stdout.write(f"  {p.name:<28} 読めない: {exc}\n")
                stdout.write("    書き込みは組み込みの既定で判定し、Bash の和からは外れる\n")
                continue
            counts = " ".join(f"{name} {len(extra.section(name))}" for name in rules.SECTIONS)
            stdout.write(f"  {p.name:<28} {path}  {counts}\n")

    stdout.write("\n■ どのルールも言及しない呼び出し\n")
    stdout.write("  ccnavi は判定を持たず、Claude Code の権限モードに従う\n")
    stdout.write("    auto                          classifier が判断する\n")
    stdout.write("    default / acceptEdits / plan  人に確認が出る\n")
    stdout.write("    dontAsk / bypassPermissions   確認できる者が居ないので通さない\n")

    stdout.write("\n■ チケットの作業範囲（承認済みの写し）\n")
    if not conf.approved:
        stdout.write("  写しの置き場が空。範囲の制限は掛かっていない\n")
        return 0
    copies, notes = approval.copies(conf.approved)
    for note in notes:
        stdout.write(f"  {note}\n")
    if not copies:
        stdout.write("  承認されたチケットが無い。範囲の制限は掛かっていない\n")
        return 0
    closed, _ = approval.copies(conf.approved, closed=True)
    done = {t.ticket for t in closed}
    for t in sorted(copies, key=lambda x: (x.parent or x.ticket, x.ticket)):
        where = tree.worktree_path(root, t.ticket)
        bound = "作業ツリーあり" if tree.is_worktree_of(root, where) else "作業ツリー無し"
        head = f"{t.ticket}（{t.title}、承認 {t.approved_at}、{bound}）"
        if t.is_child:
            waiting = [p for p in t.predecessors if p not in done]
            review = "要" if t.review_required else "不要"
            head += f" 親 {t.parent} フェーズ {t.phase} レビュー{review}"
            if waiting:
                head += f" 先行が閉じていない: {', '.join(waiting)}"
        stdout.write(f"  {head}\n")
        for name in rules.SECTIONS:
            for path in t.paths(name):
                stdout.write(f"    {name:<5} {path}\n")
    for parent in [t for t in copies if not t.is_child]:
        where = phase.stage(root, conf, parent)
        if where:
            stdout.write(f"  {parent.ticket} の段階: {where}\n")
        wrapped = approval.read_parent_mark(
            conf.approved, parent.ticket, approval.PARENT_MARK_WRAPUP
        )
        if wrapped:
            stdout.write(f"  {parent.ticket} は利用者が締めた: {wrapped.get('reason', '')}\n")
        if approval.read_parent_mark(conf.approved, parent.ticket, approval.PARENT_MARK_READY):
            stdout.write(f"  {parent.ticket} は Draft を外した。マージは利用者が行う\n")
        for ph in phase.phases_of(root, conf, parent.ticket):
            marks = ", ".join(sorted(ph.marks)) or "印なし"
            if not ph.tickets:
                state = "未計画（子がまだ無い）"
            elif ph.ended:
                state = "終了"
            else:
                state = "進行中"
            gate = "ゲート閉" if ph.gate_closed else "ゲート開"
            review = ""
            if ph.deferred:
                review = f" / レビューは {ph.review_at} と一緒に"
            elif ph.covers:
                review = f" / {', '.join(str(c) for c in ph.covers)} の分も見る"
            if ph.risk_line:
                review += f" / {ph.risk_line}"
                if ph.risk_escalates:
                    review += "（実績でレビュー要）"
            stdout.write(
                f"  {parent.ticket} フェーズ {ph.label}: {state} / {marks} / {gate}{review}\n"
            )
    return 0


def explain_json(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """`--explain` が言うことのうち、チケットに関わる部分を機械可読で出す。

    読み手は VS Code のボード拡張。拡張は提案・写し・印を自分で解釈せず、ここが
    出した形をそのまま並べる。「ゲートが閉じているか」「承認待ちは何か」の答えを
    2 か所で出さないための口で、判定と同じ関数（phase / approval）で組む。
    ネットワークには出ない。見るのはワークスペースの中のファイルだけ（設計 §4 P11）。
    """
    stdout.write(json.dumps(board(conf, root), ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def board(conf: settings.Settings, root: str) -> dict:
    """ボードの中身。形は設計 §24.10 と README「ボードの JSON」に書いてある。"""
    from . import approval, phase, tree
    from . import ticket as ticket_mod

    problems: list[str] = []
    trees = tree.all_trees(root, conf.projects)
    payload: dict = {
        "version": BOARD_VERSION,
        "root": root,
        "generated_at": approval.now(),
        "settings": {
            "tickets": conf.tickets,
            "approved": conf.approved,
            "projects": conf.projects,
        },
        "trees": [
            {"name": t.name, "root": t.root, "project": t.project, "kind": t.kind} for t in trees
        ],
        "projects": [t.name for t in trees if t.kind == tree.KIND_PROJECT],
        "problems": problems,
        "pending_approval": [],
        "tickets": [],
        "parents": [],
    }
    if not conf.approved:
        problems.append("写しの置き場が空。チケットによる制御を使っていない")
        return payload

    everything, scan_problems = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    problems.extend(str(p) for p in scan_problems)
    proposals = ticket_mod.dedupe(everything)
    open_copies, notes = approval.copies(conf.approved)
    problems.extend(notes)
    closed_copies, notes = approval.copies(conf.approved, closed=True)
    problems.extend(notes)

    pending, revisions = approval.waiting(proposals, open_copies, closed_copies)
    payload["pending_approval"] = sorted(
        {t.ticket for t in pending} | {t.ticket for t in revisions}
    )

    worktrees = {t.name: t for t in trees if t.kind == tree.KIND_WORKTREE}
    proposal_index = approval.by_id(proposals)
    open_index = approval.by_id(open_copies)
    closed_index = approval.by_id(closed_copies)
    # 同じ識別子が写っている場所の全部。権威の側は proposal に、残りは seen_in に出す。
    seen: dict[str, list[dict]] = {}
    for t in everything:
        seen.setdefault(t.ticket, []).append({"tree": t.tree, "state": t.state, "path": t.path})

    ids = sorted(set(proposal_index) | set(open_index) | set(closed_index))
    for ticket_id in ids:
        proposal = proposal_index.get(ticket_id)
        copy = open_index.get(ticket_id) or closed_index.get(ticket_id)
        source = proposal or copy
        assert source is not None
        if ticket_id in open_index:
            status = "open"
        elif ticket_id in closed_index:
            status = "closed"
        else:
            status = "none"
        found = worktrees.get(ticket_id) or tree.lookup(worktrees, ticket_id)
        record: dict = {
            "ticket": ticket_id,
            "parent": source.parent,
            "phase": source.phase,
            "title": source.title,
            "project": source.project,
            "issue": source.issue,
            "predecessors": list(source.predecessors),
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
            "copy": (
                {
                    "status": status,
                    "approved_at": copy.approved_at,
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
            "seen_in": seen.get(ticket_id, []),
            "risk": None,
            "judge": None,
        }
        if source.parent:
            record["risk"] = approval.read_child_record(
                conf.approved, source.parent, ticket_id, approval.CHILD_RECORD_RISK
            )
            record["judge"] = approval.read_child_record(
                conf.approved, source.parent, ticket_id, approval.CHILD_RECORD_JUDGE
            )
        payload["tickets"].append(record)

    # 親ごとの段階とフェーズ。写しのある親だけ。承認前の親はフェーズを持たない。
    for parent in sorted(open_copies + closed_copies, key=lambda x: x.ticket):
        if parent.is_child:
            continue
        phases = []
        for ph in phase.phases_of(root, conf, parent.ticket):
            if not ph.tickets:
                state = "planned"
            elif ph.ended:
                state = "ended"
            else:
                state = "active"
            phases.append(
                {
                    "number": ph.number,
                    "type": ph.item.type if ph.item is not None else "",
                    "title": ph.title,
                    "label": ph.label,
                    "state": state,
                    "tickets": [t.ticket for t in ph.tickets],
                    "states": dict(ph.states),
                    "marks": ph.marks,
                    "review_required": ph.review_required,
                    "gate_closed": ph.gate_closed,
                    "deferred": ph.deferred,
                    "review_at": ph.review_at,
                    "covers": list(ph.covers),
                    "risk": ph.risk,
                    "risk_escalates": ph.risk_escalates,
                    "risk_line": ph.risk_line,
                }
            )
        payload["parents"].append(
            {
                "ticket": parent.ticket,
                "closed": parent.ticket in closed_index,
                "stage": phase.stage(root, conf, parent),
                "plan": [item.as_raw() for item in parent.plan],
                "feedback": (
                    [item.as_raw() for item in parent.feedback]
                    if parent.feedback is not None
                    else None
                ),
                "wrapup": approval.read_parent_mark(
                    conf.approved, parent.ticket, approval.PARENT_MARK_WRAPUP
                ),
                "ready": approval.read_parent_mark(
                    conf.approved, parent.ticket, approval.PARENT_MARK_READY
                ),
                "accepted_threads": sorted(approval.accepted_threads(conf.approved, parent.ticket)),
                "phases": phases,
            }
        )
    return payload
