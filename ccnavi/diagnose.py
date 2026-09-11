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


# `--test --json` と `--test-samples --json` の形の版。読み手は VS Code 拡張の
# ルール設定画面。形を変えたら上げる。
TEST_VERSION = 1

# 見本のパスに書く合言葉。走らせた場所に読み替える。見本を絶対パスで
# 書かせておかないと、判定が解いた先が走らせた場所によって変わる。
SAMPLE_PLACEHOLDER = "/repo"


def judge(stderr: TextIO, conf: settings.Settings, root: str, tool: str, subject: str) -> dict:
    """1 件を判定して、結果とすべての理由を 1 つの辞書にする。

    文字で出す `test` と JSON で出す `test_json` の両方がここを読む。読み手ごとに
    判定を呼び直すと、端末で見た答えと画面で見た答えが別物になりうる。

    鍵は README「試験の JSON」に書いてある。`known` が偽なら、そのツールは
    判定が対象を取り出せないもので、他の鍵は空のまま。
    """
    from .cli import DEADLINE_SECONDS, MODE_ENABLE, decide_before, subject_of

    out: dict = {
        "known": tool in KNOWN_TOOLS,
        "tool": tool,
        "subject": subject,
        "resolved": "",
        "verdict": "",
        "code": "",
        "reason": "",
        "degraded": "",
        "fallback": "",
        "rules": [],
        "response": "",
    }
    if not out["known"]:
        return out

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

    out["verdict"] = record.decision or audit.ALLOW
    out["code"] = record.code or ""
    # ファイルのパスは行き着く先まで解いてから当てる。解いた先を見せないと、
    # 当たらなかった理由が「綴りが違う」なのかどうかを人が言えない。
    out["resolved"] = record.subject if record.subject and record.subject != subject else ""
    out["reason"] = record.reason or ""
    out["degraded"] = record.degraded or ""
    out["fallback"] = record.fallback or ""
    out["rules"] = _rules_hit(stderr, conf, root, record)
    out["response"] = _response_text(captured.getvalue())
    return out


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
    out = judge(stderr, conf, root, tool, subject)
    if not out["known"]:
        stdout.write(f"verdict: (判定に入らない)\ntool: {tool}\n")
        stdout.write(
            f"note: {tool} は判定が対象を取り出せないツール。ルールを書いても当たらず、"
            "呼び出しはそのまま通る\n"
        )
        return 0

    code = out["code"]
    stdout.write(f"verdict: {out['verdict']}{f' ({code})' if code else ''}\n")
    stdout.write(f"tool: {tool}\n")
    stdout.write(f"subject: {subject}\n")
    if out["resolved"]:
        stdout.write(f"resolved: {out['resolved']}\n")
    if out["reason"]:
        stdout.write(f"reason: {out['reason']}\n")
    if out["degraded"]:
        stdout.write(f"degraded: {out['degraded']}（生の文字列に当てた）\n")
    if out["fallback"]:
        stdout.write(f"fallback: {out['fallback']}（組み込みの既定で判定した）\n")

    if not out["rules"]:
        stdout.write("rules: (どのルールにも当たらなかった)\n")
    else:
        stdout.write("rules:\n")
        for hit in out["rules"]:
            if hit["source"] == "outside":
                # チケットの範囲のように、ルールファイルの中に無い根拠。
                stdout.write(f"  {hit['id']}（ルールファイルの外から来た根拠）\n")
                continue
            stdout.write(f"  {hit['section']}:{hit['id']}  {hit['kind']} {hit['written']!r}\n")
            stdout.write(f"    -> {hit['pattern'] or '(組み立て失敗)'}\n")

    if not out["response"]:
        stdout.write("response: (何も返さない)\n")
    else:
        stdout.write("response:\n")
        for line in out["response"].splitlines():
            stdout.write(f"  {line}\n")
    return 0


def test_json(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    tool: str,
    subject: str,
) -> int:
    """`--test` と同じ判定を JSON で出す。読み手は VS Code 拡張のルール設定画面。"""
    body = {"version": TEST_VERSION, "root": root, "rules_path": conf.rules}
    body.update(judge(stderr, conf, root, tool, subject))
    stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def _rules_hit(
    stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> list[dict]:
    """当たったルールを、区画と翻訳後の式まで返す。

    翻訳後の式を出すのがこの試験の要。`glob` は正規表現に化けるので、
    書いたものと当たるものの間に見えない層が 1 枚ある。その層を開けないと、
    当たらなかった理由を人が自分で辿れない。

    `source` は `file`（ルールファイルの中）か `outside`（チケットの範囲のように、
    ルールファイルの外から来た根拠）。
    """
    from .cli import load_rules

    if not record.rules:
        return []

    rule_set, _ = load_rules(stderr, conf.rules, audit.Record(), root)
    by_id = {rule.id: rule for rule in rule_set.all() if rule.id}

    hits = []
    for name in record.rules:
        rule = by_id.get(name)
        if rule is None:
            hits.append(
                {
                    "id": name,
                    "source": "outside",
                    "section": "",
                    "kind": "",
                    "written": "",
                    "pattern": "",
                }
            )
            continue
        hits.append(
            {
                "id": name,
                "source": "file",
                "section": rule.decision,
                "kind": "glob" if rule.glob else "regex",
                "written": rule.glob or rule.regex,
                "pattern": rule.compiled.pattern if rule.compiled else "",
            }
        )
    return hits


def _response_text(written: str) -> str:
    """判定が返した文面をそのまま取り出す。

    止めるだけでは足りない、というのがこの道具の目的なので、試験でも
    「何が返るか」まで見せる。文面の無い拒否は、受け取った側に
    次の一手が無い。
    """
    if not written.strip():
        return ""
    try:
        out = json.loads(written)["hookSpecificOutput"]
    except (ValueError, KeyError):
        return written.strip()
    return out.get("permissionDecisionReason") or out.get("additionalContext") or ""


def load_samples(path: str, root: str) -> list[dict]:
    """見本を読んで、区画の順に平らな並びにする。

    区画の名前が期待する判定になる。`deny` なら止まるはず、`allow` なら通るはず。
    `subject` の合言葉 `/repo` は走らせた場所に読み替える。
    """
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"見本の形が違う。最上位は区画の対応表のはず: {path}")
    samples = []
    for want in rules.SECTIONS:
        for case in raw.get(want) or []:
            if not isinstance(case, dict):
                raise ValueError(f"見本の形が違う。区画 {want} の 1 件が対応表ではない: {path}")
            written = str(case.get("subject") or "")
            samples.append(
                {
                    "expected": want,
                    "tool": str(case.get("tool") or ""),
                    "subject": written,
                    "resolved_subject": written.replace(SAMPLE_PLACEHOLDER, root),
                    "why": str(case.get("why") or ""),
                }
            )
    return samples


def run_samples(stderr: TextIO, conf: settings.Settings, root: str, path: str) -> dict:
    """見本をぜんぶ判定に掛けて、期待と突き合わせた結果を 1 つの辞書にする。

    判定は `judge` を通す。ここで判定を作り直さないのが肝で、別の道で確かめると、
    見本が通ったのに実運用で落ちる、という一番まずい形になる（REQ-DIA-03）。

    `ok` は期待どおりか。`skipped` は allow の見本が判定に入らずに通ったもので、
    呼び出しは通るので期待は満たしているが、通した理由が「allow に当たった」では
    ない。ルールを書いても当たらない場所なので、食い違いとは別に数えて必ず見せる。
    """
    results = []
    for sample in load_samples(path, root):
        out = judge(stderr, conf, root, sample["tool"], sample["resolved_subject"])
        want = sample["expected"]
        got = out["verdict"] if out["known"] else audit.SKIP
        skipped = want == rules.ALLOW and got == audit.SKIP
        results.append(
            {
                **sample,
                "known": out["known"],
                "verdict": got,
                "code": out["code"],
                "reason": out["reason"],
                "rules": out["rules"],
                "ok": got == want or skipped,
                "skipped": skipped,
            }
        )
    counts = {
        want: {
            "ok": sum(1 for r in results if r["expected"] == want and r["ok"]),
            "total": sum(1 for r in results if r["expected"] == want),
        }
        for want in rules.SECTIONS
    }
    return {
        "version": TEST_VERSION,
        "root": root,
        "rules_path": conf.rules,
        "samples_path": path,
        "counts": counts,
        "mismatches": sum(1 for r in results if not r["ok"]),
        "skipped": sum(1 for r in results if r["skipped"]),
        "samples": results,
    }


def test_samples(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    path: str,
    as_json: bool,
) -> int:
    """`--test-samples`。見本をぜんぶ回して、食い違いを並べる。

    文字で出すときの終了コードは、食い違いが 1 件でもあれば 1。`/rules-check`
    スキルと `testdata/check_rules.py` がそれを見る。JSON で出すときは常に 0 で、
    食い違いの数は本文の `mismatches` にある。読み手が「食い違った」と
    「試験そのものが失敗した」を終了コードで見分けられるように。
    """
    try:
        body = run_samples(stderr, conf, root, path)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: 見本を読めない: {exc}\n")
        return 1

    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return 0

    for r in body["samples"]:
        if r["ok"]:
            continue
        stdout.write(f"食い違い: {r['expected']} のはずが {r['verdict']}\n")
        stdout.write(f"  {r['tool']}  {r['subject']}\n")
        stdout.write(f"  なぜ deny/ask/allow に置いたか: {r['why']}\n")
        hit = ", ".join(f"{h['section']}:{h['id']}" for h in r["rules"] if h["source"] == "file")
        if hit:
            stdout.write(f"  当たったルール: {hit}\n")
        stdout.write("\n")
    for r in body["samples"]:
        if not r["skipped"]:
            continue
        stdout.write(f"判定に入らずに通った（allow ではない）: {r['tool']}  {r['subject']}\n")
        stdout.write(f"  {r['why']}\n\n")
    for want, c in body["counts"].items():
        stdout.write(f"{want}: {c['ok']}/{c['total']}\n")
    stdout.write(f"食い違い {body['mismatches']} 件、判定に入らなかったもの {body['skipped']} 件\n")
    return 1 if body["mismatches"] else 0


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
