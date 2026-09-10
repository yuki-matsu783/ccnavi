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

    _rules_hit(stdout, stderr, conf, record)
    _response(stdout, captured.getvalue())
    return 0


def _rules_hit(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, record: audit.Record
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

    rule_set, _ = load_rules(stderr, conf.rules, audit.Record())
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

    rule_set, source = load_rules(stderr, conf.rules, audit.Record())
    stdout.write(f"ccnavi: いま効いている宣言（出所 {source}）\n")

    for name in rules.SECTIONS:
        section = rule_set.section(name)
        stdout.write(f"\n■ {name}（{len(section)} 件）\n")
        for rule in section:
            written = rule.glob or rule.regex
            stdout.write(f"  {rule.id or '(id 無し)':<28} {rule.match:<34} {written}\n")

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
            stdout.write(
                f"  {parent.ticket} フェーズ {ph.label}: {state} / {marks} / {gate}{review}\n"
            )
    return 0
