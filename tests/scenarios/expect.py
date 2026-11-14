"""期待の評価（層 1）。

判定の記録・最終状態・expect_after を比べ、総合を決める。意味の判定の表も読む。
"""

import hashlib
import json
import subprocess
from pathlib import Path

from tests.scenarios.common import FAIL, INCONCLUSIVE, PASS, REVIEW, clip


def read_decisions(log_path, session_ids):
    """判定の記録から、回したセッションの行だけを取り出す（他のセッションの行は混ざる）。"""
    rows = []
    sessions = set(session_ids)
    p = Path(log_path)
    if not p.exists():
        return rows
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("session") in sessions:
                rows.append(rec)
    return rows


def _selected(rec, cond):
    """どの呼び出しの話かを決める欄（event・tool・subject_contains）。"""
    if "event" in cond and rec.get("event") != cond["event"]:
        return False
    if "tool" in cond and rec.get("tool") != cond["tool"]:
        return False
    return not (
        "subject_contains" in cond and cond["subject_contains"] not in str(rec.get("subject", ""))
    )


def _satisfies(rec, cond):
    """その呼び出しがどう判定されたかを決める欄（decision・code・rules_include・decision_not・hinted）。"""
    if "decision" in cond and rec.get("decision") != cond["decision"]:
        return False
    if "decision_not" in cond and rec.get("decision") == cond["decision_not"]:
        return False
    if "code" in cond and rec.get("code") != cond["code"]:
        return False
    rules = rec.get("rules") or []
    # hinted は、その呼び出しで once の案内を渡した行にだけ付く。欄が無い行は false 扱い。
    if "hinted" in cond and bool(rec.get("hinted")) != bool(cond["hinted"]):
        return False
    return all(r in rules for r in cond.get("rules_include", []))


def check_decision(rows, cond):
    """期待する判定が出ているか。呼び出し自体が無ければ inconclusive（fail にしない）。"""
    picked = [r for r in rows if _selected(r, cond)]
    if not picked:
        return INCONCLUSIVE, "該当する呼び出しが記録に無い（エージェントが打たなかった）"
    n = sum(1 for r in picked if _satisfies(r, cond))
    need = cond.get("min_count", 1)
    if n >= need:
        return PASS, f"{n} 件が一致"
    seen = sorted({(r.get("decision"), r.get("code")) for r in picked}, key=str)
    return FAIL, f"一致 {n} 件（{need} 件が要る）。記録にあった判定: {seen}"


def check_decision_none(rows, cond):
    """出ていてはいけない判定が出ていないか。"""
    hit = [
        r
        for r in rows
        if _selected(r, cond)
        and _satisfies(
            r, {k: v for k, v in cond.items() if k not in ("event", "tool", "subject_contains")}
        )
    ]
    if hit:
        return FAIL, f"出ていた: {clip(hit[0].get('subject', ''), 200, 'logs/decisions.jsonl')}"
    return PASS, "出ていない"


def file_digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def check_state(item, root):
    cwd = Path(root) / item["cwd"] if item.get("cwd") else Path(root)
    r = subprocess.run(
        item["run"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
    )
    out = (r.stdout or "") + (r.stderr or "")
    if "expect_contains" in item and item["expect_contains"] not in out:
        tail = clip(out.strip(), 300, "state の run を手で打ち直して確かめる")
        return FAIL, f"出力に「{item['expect_contains']}」が無い: {tail}"
    if "expect_not_contains" in item and item["expect_not_contains"] in out:
        return FAIL, f"出力に「{item['expect_not_contains']}」がある"
    return PASS, "一致"


def evaluate(expect, rows, raw_text, root, pre_digests):
    """期待ごとの結果の一覧 [(種類, 内容, 判定, 理由)] を返す。"""
    results = []
    for cond in expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for cond in expect.get("decisions_none", []):
        v, why = check_decision_none(rows, cond)
        results.append(("decisions_none", cond, v, why))
    for rel in expect.get("files_unchanged", []):
        now = file_digest(Path(root) / rel)
        v, why = (
            (PASS, "変わっていない")
            if now == pre_digests.get(rel)
            else (FAIL, "回す前と変わっている")
        )
        results.append(("files_unchanged", rel, v, why))
    for item in expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    for s in expect.get("transcript_forbidden", []):
        v, why = (FAIL, "出力に出ている") if s in raw_text else (PASS, "出ていない")
        results.append(("transcript_forbidden", s, v, why))
    for criterion in expect.get("judge", []):
        results.append(("judge", criterion, REVIEW, "証拠を読んで判定する"))
    return results


def evaluate_after(step_expect, st, root):
    """step の expect_after を評価する。判定の行は、ここまでに回した全 step のセッションから探す。
    結果は evaluate と同じ形の [(種類, 内容, 判定, 理由)]。"""
    rows = read_decisions(Path(root) / "logs/decisions.jsonl", st["session_ids"])
    results = []
    for cond in step_expect.get("decisions", []):
        v, why = check_decision(rows, cond)
        results.append(("decisions", cond, v, why))
    for item in step_expect.get("state", []):
        v, why = check_state(item, root)
        results.append(("state", item, v, why))
    return results


def overall(results):
    kinds = [r[2] for r in results]
    for v in (FAIL, INCONCLUSIVE, REVIEW):
        if v in kinds:
            return v
    return PASS


REPO_CHANGED = "回す前と変わっている（HEAD・ブランチ・ワークツリーのどれか）"


def repo_result(pre, now):
    """回す前と後の ccnavi 本体の状態を比べる。(判定, 理由)。
    pre が無いとき（この決まりより前に作られた状態ファイル）は、確かめられないので未成立にする。"""
    if pre is None:
        return INCONCLUSIVE, "回す前の状態が無い（この実行より前に作られた状態ファイル）"
    if pre == now:
        return PASS, "変わっていない"
    return FAIL, REPO_CHANGED


JUDGE_HEADING = "## 意味の判定（別のエージェント）"


def parse_judgment(text):
    """判定の表（| 基準 | 判定 | 理由 |）を読む。[(基準, 判定, 理由)]。判定は pass か fail だけ。"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3 or cells[0] == "基準" or set(cells[0]) <= set("-: "):
            continue
        verdict = cells[1].lower()
        if verdict not in (PASS, FAIL):
            raise ValueError(f"判定は pass か fail にする: {cells[0]} → {cells[1]}")
        rows.append((cells[0], verdict, cells[2]))
    return rows


def final_verdict(mechanical, judged, accepted):
    """機械判定と意味の判定と、ユーザの受け入れから、最終の判定を決める。"""
    if not accepted:
        return "受け入れ待ち（ユーザが判定を確かめるまで、全体の判定は確定しない）"
    judge_verdicts = [v for _, v, _ in judged]
    if overall(mechanical) != PASS:
        return overall(mechanical)
    return FAIL if FAIL in judge_verdicts else PASS


def mechanical_results(out):
    """報告の機械判定（judge 以外）を、final_verdict に渡す形で読む。"""
    recorded = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return [
        (r["kind"], r["expect"], r["verdict"], r["why"]) for r in recorded if r["kind"] != "judge"
    ]
