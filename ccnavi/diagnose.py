"""判定を実行せずに試す。人が端末から叩く経路。

## なぜ要るか

ルールを 1 件書いたとき、それが何に当たるのかは走らせるまで分からない。
`glob` は正規表現に翻訳されるし、Bash のコマンドは実行される部分まで
絞られてから当たる。書いた人の頭の中の当たり方と、実際の当たり方がずれても、
ずれたことに気づく手立てが無かった。気づかないルールは、足したつもりで
何も止めていない 1 行になる。

## 判定と同じ道を通る

ここは判定を作り直さない。payload を組み立てて `judge.decide_before` を
そのまま呼び、返った応答と記録を読んで人に見せる。別の道で判定すると、
試験で通ったものが実運用で落ちる、という一番まずい形になる（REQ-DIA-03）。

モードは常に enable で動かす。試験は「止まるかどうか」を問うものなので、
呼び出しに手を出さないモードの結果を見せても答えになっていない。
実際に走っているセッションが dry-run でも、ここは enable の判定を返す。
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import time
from typing import TextIO

import yaml

from . import (
    approval,
    audit,
    builtin,
    hookio,
    judge,
    modes,
    phase,
    phasetypes,
    risk,
    ruleload,
    rules,
    settings,
    tree,
)
from . import ticket as ticket_mod

# `--explain --json` の形の版。読み手（VS Code 拡張）が形の違いに気づけるように。
BOARD_VERSION = 1

# 対象を取り出せるツール。ここに無いツールは判定に届かないまま通るので、
# 試したい人には「当たらない」ではなく「そもそも見ていない」と言う。
# 一覧は judge の表そのもの。VS Code 拡張の KNOWN_TOOLS はこれと同じ並び。
KNOWN_TOOLS = tuple(judge.SUBJECT_FIELDS)


# `--test --json` と `--test-samples --json` の形の版。読み手は VS Code 拡張の
# ルール設定画面。形を変えたら上げる。
TEST_VERSION = 1

# 見本のパスに書く合言葉。走らせた場所に読み替える。見本を絶対パスで
# 書かせておかないと、判定が解いた先が走らせた場所によって変わる。
SAMPLE_PLACEHOLDER = "/repo"


def try_one(stderr: TextIO, conf: settings.Settings, root: str, tool: str, subject: str) -> dict:
    """1 件を判定して、結果とすべての理由を 1 つの辞書にする。

    文字で出す `test` と JSON で出す `test_json` の両方がここを読む。読み手ごとに
    判定を呼び直すと、端末で見た答えと画面で見た答えが別物になりうる。

    鍵は README「試験の JSON」に書いてある。`known` が偽なら、そのツールは
    判定が対象を取り出せないもので、他の鍵は空のまま。
    """
    # 試験は控えを持たない。「1 度だけ渡す文」を試しで消費すると、本番の最初の
    # 1 回で届かなくなる。控えを外すと selfguard の写しも取らないが、試験は
    # 実行しないのでそもそも戻すものが無い。
    conf = dataclasses.replace(conf, state="")

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

    field = judge.SUBJECT_FIELDS[tool]
    payload = hookio.Input(
        event=hookio.PRE_TOOL_USE,
        tool_name=tool,
        tool_input={field: subject},
        cwd=root,
    )
    record = audit.Record(
        mode=modes.ENABLE,
        event=payload.event,
        tool=tool,
        subject=judge.subject_of(payload),
    )

    # 応答は捨てずに拾う。判定が返す文面そのものを見せたいので、
    # ここで文を組み直さない。組み直すと、試験で読んだ文と
    # エージェントに届く文が別物になる。
    captured = io.StringIO()
    judge.decide_before(
        captured,
        stderr,
        modes.ENABLE,
        conf,
        root,
        payload,
        record,
        time.monotonic() + judge.DEADLINE_SECONDS,
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
    out = try_one(stderr, conf, root, tool, subject)
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
    body.update(try_one(stderr, conf, root, tool, subject))
    stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def _rules_hit(
    stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> list[dict]:
    """当たったルールを、タイプと翻訳後の式まで返す。

    翻訳後の式を出すのがこの試験の要。`glob` は正規表現に化けるので、
    書いたものと当たるものの間に見えない層が 1 枚ある。その層を開けないと、
    当たらなかった理由を人が自分で辿れない。

    `source` は `file`（ルールファイルの中）か `outside`（チケットの範囲のように、
    ルールファイルの外から来た根拠）。層のルールは `self:docs` / `lib:source` の形の
    id で当たるので（REQ-MLT-07）、同じ綴りで引けるように層ごと並べる。
    """
    if not record.rules:
        return []

    by_id = {}
    for view in ruleload.survey(stderr, conf, root):
        by_id.update({rule.id: rule for rule in view.rule_set.all() if rule.id})

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
    # 理由と additionalContext は別の鍵で、deny と ask では両方が届く。
    # 両方あるときは届く順に並べる。
    parts = [out.get("permissionDecisionReason") or "", out.get("additionalContext") or ""]
    return "\n\n".join(p for p in parts if p)


def load_samples(path: str, root: str) -> list[dict]:
    """見本を読んで、タイプの順に平らな並びにする。

    タイプの名前が期待する判定になる。`deny` なら止まるはず、`allow` なら通るはず。
    `subject` の合言葉 `/repo` は走らせた場所に読み替える。
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"見本の形が違う。最上位はタイプの対応表のはず: {path}")
    samples = []
    for want in rules.SECTIONS:
        for case in raw.get(want) or []:
            if not isinstance(case, dict):
                raise ValueError(f"見本の形が違う。タイプ {want} の 1 件が対応表ではない: {path}")
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
        out = try_one(stderr, conf, root, sample["tool"], sample["resolved_subject"])
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

    文字で出すときの終了コードは、食い違いが 1 件でもあれば 1。`/ccnavi-config`
    スキルと `tools/check_rules.py` がそれを見る。JSON で出すときは常に 0 で、
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


# 層の見出し。共通層と自身の層だけ日本語で名乗る。プロジェクトは名前そのもので、
# それが id の前置き（`lib:schema`）と同じ綴りになる。
LAYER_LABELS = {ruleload.LAYER_COMMON: "共通層", ruleload.LAYER_SELF: "自身の層"}


def layer_label(name: str) -> str:
    return LAYER_LABELS.get(name, name)


def _shown(root: str, path: str) -> str:
    """綴りをワークスペースルートからの相対で出す。外に在るなら書かれたまま。"""
    if not path:
        return "(無し)"
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    return path if rel.startswith(os.pardir) else rel.replace(os.sep, "/")


def layer_home(conf: settings.Settings, root: str, name: str) -> str:
    """その層の git プロジェクトルート。共通層は持たない（ワークスペースルートを返す）。"""
    if name in (ruleload.LAYER_COMMON, ruleload.LAYER_SELF):
        return root
    return tree.project_root(conf.projects, name)


def layer_config(conf: settings.Settings, root: str, name: str, kind: str) -> str:
    """その層の phases / risk の綴り。共通層は今までどおり `CCNAVI_PHASES` / `CCNAVI_RISK`。"""
    if name == ruleload.LAYER_COMMON:
        return conf.phases if kind == settings.KIND_PHASES else conf.risk
    return settings.layer_path(conf, layer_home(conf, root, name), kind, name)


def _written(entry) -> str:
    """範囲の 1 件を、書かれた綴りで出す。翻訳後の式ではなく、人が書いたほう。"""
    return entry.glob or entry.regex


def layer_phase_types(path: str) -> tuple[list, str]:
    """その層のフェーズの種類と、読めなかった理由。無い層は空。

    合成はしない。ここで出すのは「どの層に何が書いてあるか」で、id ごとに
    合わせた結果は判定の側（phase）が持つ。
    """
    if not path or not os.path.isfile(path):
        return [], ""
    types, notes = phasetypes.load(path)
    if types is None:
        return [], "; ".join(str(n) for n in notes) or "読めない"
    return list(types.values()), ""


def layer_risk(path: str) -> tuple[list, str]:
    """その層のリスクの項目と、読めなかった理由。無い層は空（組み込みへは落とさない）。"""
    if not path or not os.path.isfile(path):
        return [], ""
    definition, notes = risk.load(path)
    if definition.fallback:
        return [], definition.fallback or "; ".join(str(n) for n in notes)
    return list(definition.factors), ""


def _explain_phases(
    stdout: TextIO, conf: settings.Settings, root: str, views: list[ruleload.LayerView]
) -> None:
    """層ごとのフェーズの種類（設計 §25.9）。id は裸のまま、層は欄で出す。"""
    tables = [
        (v.name, *layer_phase_types(layer_config(conf, root, v.name, settings.KIND_PHASES)))
        for v in views
    ]
    counts = "、".join(f"{layer_label(name)} {len(items)} 種" for name, items, _ in tables)
    stdout.write(f"\n■ phases（{counts}）\n")
    stdout.write(f"  {'id':<16}{'層':<10}{'kind':<8}{'title':<16}{'review':<8}scope\n")
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(f"  {layer_label(name)}: 読めない: {unreadable}。この層は空として扱う\n")
        for pt in items:
            scope = ", ".join(_written(e) for e in pt.scope) if pt.scope else "inherit"
            head = f"  {pt.id:<16}{layer_label(name):<10}{pt.kind:<8}{pt.title:<16}"
            stdout.write(f"{head}{pt.review:<8}{scope}\n")


def _explain_risk(
    stdout: TextIO, conf: settings.Settings, root: str, views: list[ruleload.LayerView]
) -> None:
    """層ごとのリスクの配点（設計 §25.9）。閾値は共通層のものを出す。"""
    tables = [
        (v.name, *layer_risk(layer_config(conf, root, v.name, settings.KIND_RISK))) for v in views
    ]
    common, _ = risk.load(conf.risk)
    levels = " / ".join(f"{k} {common.levels.get(k)}" for k in ("medium", "high", "critical"))
    stdout.write(f"\n■ risk（levels: {levels}）\n")
    stdout.write(f"  {'id':<16}{'層':<10}{'当て方':<20}{'points':<8}message\n")
    for name, items, unreadable in tables:
        if unreadable:
            stdout.write(f"  {layer_label(name)}: 読めない: {unreadable}。この層は空として扱う\n")
        for factor in items:
            how = f"{factor.kind} {factor.value}"
            stdout.write(
                f"  {factor.id:<16}{layer_label(name):<10}{how:<20}"
                f"{factor.points:<8}{factor.message}\n"
            )


def explain(stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str) -> int:
    """いま効いている宣言を、判定を行わずに一覧する（REQ-DIA-01）。

    どこが守られているかではなく、何がどう宣言されているかを見せる。
    実効権限をパスごとに数え上げるには、宣言済み領域という概念が要る。
    それはまだ無いので、ここで言えるのは「どのルールがどのタイプにあるか」と
    「チケットの範囲が効いているか」まで。言えないことは言わない。
    """
    views = ruleload.survey(stderr, conf, root)
    source = builtin.SOURCE if views[0].unreadable else conf.rules
    stdout.write(f"ccnavi: いま効いている宣言（出所 {source}）\n")
    stdout.write(
        "  書き込み系は 共通層 + 行き先の層、Bash は全部の層の和で判定する（設計 §25.4）\n"
    )

    for view in views:
        counts = " / ".join(f"{name} {len(view.rule_set.section(name))}" for name in rules.SECTIONS)
        stdout.write(f"\n■ rules {layer_label(view.name)}（{_shown(root, view.path)}、{counts}）\n")
        if view.unreadable:
            stdout.write(f"  読めない: {view.unreadable}。この層は空として扱う\n")
            continue
        if view.missing:
            stdout.write("  この層は置いていない（無い = 空）\n")
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
    stdout.write("  ccnavi は判定を持たず、Claude Code の権限モードに従う\n")
    stdout.write("    auto                          classifier が判断する\n")
    stdout.write("    default / acceptEdits / plan  人に確認が出る\n")
    stdout.write("    dontAsk / bypassPermissions   確認できる者が居ないので通さない\n")

    stdout.write("\n■ チケットの作業範囲（承認済みの写し）\n")
    stdout.write(f"  チケット制御: {conf.ticket_control or settings.TICKET_CONTROL_ENABLE}\n")
    if not conf.tickets_enabled:
        stdout.write(f"  {settings.TICKET_CONTROL_ENV}=disable。範囲の制限は掛かっていない\n")
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
    stdout.write(json.dumps(board(conf, root, stderr), ensure_ascii=True, indent=1))
    stdout.write("\n")
    return 0


def board(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> dict:
    """ボードの中身。形は設計 §24.10 と README「ボードの JSON」に書いてある。"""
    problems: list[str] = []
    trees = tree.all_trees(root, conf.projects)
    payload: dict = {
        "version": BOARD_VERSION,
        "root": root,
        "generated_at": approval.now(),
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
    }
    if not conf.tickets_enabled:
        problems.append(f"{settings.TICKET_CONTROL_ENV}=disable。チケット制御を使っていない")
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
            )
        )

    # 親ごとの段階とフェーズ。写しのある親だけ。承認前の親はフェーズを持たない。
    for parent in sorted(open_copies + closed_copies, key=lambda x: x.ticket):
        if parent.is_child:
            continue
        payload["parents"].append(_parent_record(conf, root, parent, closed_index))
    return payload


def _layers(conf: settings.Settings, root: str, stderr: TextIO | None = None) -> list[dict]:
    """層ごとの宣言（設計 §25.9）。並びは 共通層 → 自身の層 → プロジェクト（名前順）。

    rules は重複を捨てたあとの、その層から実際に判定へ入ったぶん。phases と risk は
    その層のファイルに書いてあるぶんで、合成はしない（合成の結果は親のフェーズの
    側に出る）。読めない層は `unreadable` に理由が入り、中身は空になる。
    """
    said = stderr if stderr is not None else io.StringIO()
    out = []
    for view in ruleload.survey(said, conf, root):
        phases_path = layer_config(conf, root, view.name, settings.KIND_PHASES)
        risk_path = layer_config(conf, root, view.name, settings.KIND_RISK)
        types, phases_unreadable = layer_phase_types(phases_path)
        factors, risk_unreadable = layer_risk(risk_path)
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
    """ルール 1 件。id は層の名前付き、書いた綴りと翻訳後の式の両方を出す。"""
    return {
        "id": rule.id,
        "section": rule.decision,
        "source": rule.source,
        "match": rule.match,
        "kind": "glob" if rule.glob else "regex",
        "written": rule.glob or rule.regex,
        "pattern": rule.compiled.pattern if rule.compiled else "",
        "message": rule.message,
    }


def _phase_type_record(layer: str, pt) -> dict:
    """フェーズの種類 1 つ。id は裸のまま、層は欄で出す（設計 §25.4.1）。"""
    return {
        "id": pt.id,
        "source": layer,
        "kind": pt.kind,
        "title": pt.title,
        "review": pt.review,
        # scope は `inherit`（親の範囲を継ぐ）のとき None。空の並びと区別が付くように、
        # 継ぐことは `inherit` の 1 語で出す。
        "scope": [_written(e) for e in pt.scope] if pt.scope else ["inherit"],
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


def _ticket_record(
    conf: settings.Settings,
    root: str,
    ticket_id: str,
    proposal: ticket_mod.Ticket | None,
    open_index: dict,
    closed_index: dict,
    worktrees: dict,
    seen_in: list[dict],
) -> dict:
    """チケット 1 件。提案と写しと作業ツリーの今を 1 つにまとめる。"""
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
        "seen_in": seen_in,
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
        "gate_closed": ph.gate_closed,
        "deferred": ph.deferred,
        "review_at": ph.review_at,
        "covers": list(ph.covers),
        "risk": ph.risk,
        "risk_escalates": ph.risk_escalates,
        "risk_line": ph.risk_line,
    }


def _parent_record(
    conf: settings.Settings, root: str, parent: ticket_mod.Ticket, closed_index: dict
) -> dict:
    """親 1 件。段階、計画、親の印、フェーズの並び。"""
    return {
        "ticket": parent.ticket,
        "closed": parent.ticket in closed_index,
        "stage": phase.stage(root, conf, parent),
        "plan": [item.as_raw() for item in parent.plan],
        "feedback": (
            [item.as_raw() for item in parent.feedback] if parent.feedback is not None else None
        ),
        "wrapup": approval.read_parent_mark(
            conf.approved, parent.ticket, approval.PARENT_MARK_WRAPUP
        ),
        "ready": approval.read_parent_mark(
            conf.approved, parent.ticket, approval.PARENT_MARK_READY
        ),
        "accepted_threads": sorted(approval.accepted_threads(conf.approved, parent.ticket)),
        "phases": [_phase_record(ph) for ph in phase.phases_of(root, conf, parent.ticket)],
    }
