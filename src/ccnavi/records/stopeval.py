"""Stop の AI 判定の評価の集計。採用を判断する材料を、基準ごとの単位で偏りなく出す。

単位は、R1・R3 が Stop の機会、R2・R4 が提案の版。読むのは state の試行
（`stopreview-attempt-*.json`）、
専用の記録（`logs/stop-review/events.jsonl`）、独立した評価（`logs/stop-review/labels.jsonl`）。

独立した評価は 1 行 1 件の JSON: `{"attempt_id", "criterion", "target", "truth": "flaw"|"ok"}`。
人かシナリオの期待結果が書く。見逃し率・誤差し戻し率・不要介入率はこれが無いと出せない（None）。

指標（基準ごと）:

- 検査カバー率: 対象と判断した機会（対象外・検査省略を除く）のうち、有効な検査が完了した（終了可か
  不備検出）割合
- 見逃し率: 評価で不備がある単位のうち、その基準で不備検出にならなかった割合。未検査と、AI の誤った
  対象外を含める
- 誤差し戻し率: その基準で不備検出になった単位のうち、評価で不備が無い割合
- 不要介入率: 評価で不備が無い単位のうち、その基準で不備検出になった割合
- 修正率: 差し戻し送出のうち、再判定で解消した割合（再判定できなかったものは別に数える）

分けて数えるもの: ccnavi が決めた対象外・AI が決めた対象外・検査省略・期限切れ・出力不正（拒否）・
未連携・記録外の停止の疑い・汚染・計画待ちのまま残ったもの（計画なし）。
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

from ..infra import fsio, pathcase
from . import stopops, stopreview

LABELS_FILE = "labels.jsonl"


def _attempts(state: str) -> list[dict[str, Any]]:
    found = []
    try:
        names = sorted(fsio.listdir(state)) if state else []
    except OSError:
        names = []
    for name in names:
        if name.startswith("stopreview-attempt-") and name.endswith(".json"):
            row = fsio.read_dict(os.path.join(state, name))
            if row:
                found.append(row)
    return found


def _jsonl(path: str) -> list[dict[str, Any]]:
    text = fsio.read_text(path, errors="replace") if os.path.isfile(path) else None
    rows = []
    for line in (text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _all_events(log_dir: str) -> list[dict[str, Any]]:
    """試行のイベント。ローテートした `events.<日時>.jsonl` も読む。"""
    current = stopreview.log_path(log_dir)
    directory = os.path.dirname(current)
    paths = [current]
    with contextlib.suppress(OSError):
        paths += [
            os.path.join(directory, name)
            for name in sorted(fsio.listdir(directory))
            if name.startswith("events.") and name.endswith(".jsonl") and name != "events.jsonl"
        ]
    return [row for path in paths for row in _jsonl(path)]


def _latency(attempts: list[dict[str, Any]]) -> dict[str, float] | None:
    """計画の作成から判定の完了まで（秒）。完了した試行だけ。"""
    waits = sorted(
        float(a["done_at"]) - float(a["created"])
        for a in attempts
        if isinstance(a.get("done_at"), (int, float)) and isinstance(a.get("created"), (int, float))
    )
    if not waits:
        return None
    return {"n": len(waits), "median": waits[len(waits) // 2], "max": waits[-1]}


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(state: str, log_dir: str) -> dict[str, Any]:
    """集計。期限切れの確定を先に行う。

    処理フロー:
    1. stopreview.expire_overdue で期限切れの試行を確定する。
    2. _attempts で試行を読み、再判定の試行（original が付くもの）を除く。
    3. _all_events と、ラベルの記録（LABELS_FILE）を読む。
    4. 試行ごとに、最終分類・差し戻し・再判定の結果を counts と修正の数に振り分ける。
    5. 集計の値（修正率・遅延・基準ごとの内訳）を _rate・_latency・_by_criterion で組み、返す。
    """
    stopreview.expire_overdue(state)
    every = _attempts(state)
    # 再判定の試行は元の試行の修正率にだけ使う。Stop の機会を二重に数えない。
    attempts = [a for a in every if not a.get("original")]
    events = _all_events(log_dir)
    labels = _jsonl(os.path.join(log_dir, "stop-review", LABELS_FILE))

    # 試行の分類ごとの件数。分類は最初にヒットした 1 つを数える（期限切れは未検査にも数える）。
    # 拒否と汚染は、分類とは別に数える。
    counts = {
        "ccnavi_out_of_scope": 0,
        "ai_out_of_scope": 0,
        "skipped": 0,
        "expired": 0,
        "rejected": 0,
        "plan_missing": 0,
        "tainted": 0,
        "checked_ok": 0,
        "flawed": 0,
        "unchecked": 0,
        "in_progress": 0,
    }
    resolved = unresolved = inconclusive = not_rejudged = sent_back = 0
    for a in attempts:
        final, judge = a.get("final"), a.get("judge")
        if a.get("sent_at"):
            counts["flawed"] += 1  # 差し戻した試行は、期限で閉じても最終分類（不備検出）で数える
        elif a.get("state") == stopreview.PLAN_WAIT:
            counts["plan_missing"] += 1
        elif final == stopreview.FINAL_OUT_OF_SCOPE:
            counts[
                "ai_out_of_scope" if judge == stopreview.JUDGE_AI else "ccnavi_out_of_scope"
            ] += 1
        elif final == stopreview.FINAL_SKIPPED:
            counts["skipped"] += 1
        elif a.get("state") == stopreview.EXPIRED:
            counts["expired"] += 1
            counts["unchecked"] += 1
        elif final == stopreview.FINAL_OK:
            counts["checked_ok"] += 1
        elif final == stopreview.FINAL_FLAWED:
            counts["flawed"] += 1
        elif final == stopreview.FINAL_UNCHECKED:
            counts["unchecked"] += 1
        else:
            counts["in_progress"] += 1  # 開始待ち・実行中（まだ最終分類が無い）
        if a.get("accepted") is False:
            counts["rejected"] += 1
        if a.get("tainted"):
            counts["tainted"] += 1
        if a.get("sent_at"):
            sent_back += 1
            if a.get("rejudge") == "resolved":
                resolved += 1
            elif a.get("rejudge") == "unresolved":
                unresolved += 1
            elif a.get("rejudge") == "inconclusive":
                inconclusive += 1
            else:
                not_rejudged += 1

    judged = counts["checked_ok"] + counts["flawed"] + counts["unchecked"]
    summary: dict[str, Any] = {
        "attempts": len(attempts),
        "counts": counts,
        "suspected_unrecorded_stops": sum(
            1 for e in events if e.get("event") == "suspected_unrecorded_stop"
        ),
        "fix_rate": _rate(resolved, sent_back),
        "fix": {
            "sent_back": sent_back,
            "resolved": resolved,
            "unresolved": unresolved,
            "inconclusive": inconclusive,
            "not_rejudged": not_rejudged,
        },
        "latency": _latency(attempts),
        "key_failures": {
            "no_key": sum(1 for e in events if e.get("event") == "no_key"),
            "plan_timeout": sum(1 for e in events if e.get("event") == "plan_timeout"),
        },
        "coverage": _rate(counts["checked_ok"] + counts["flawed"], judged),
        "criteria": _by_criterion(attempts, labels),
    }
    return summary


def _units(attempts: list[dict[str, Any]]) -> dict[tuple[str, str, str], str]:
    """(試行, 基準, 対象) → 検出の結果。不備検出は "flawed"、そうでなければ "clear"。

    結果の行が無い単位（未検査・AI の誤った対象外・期限切れ）は "missed"（検出できなかった）とする。
    """
    units: dict[tuple[str, str, str], str] = {}
    for a in attempts:
        for row in a.get("results") or []:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(k), str) for k in ("criterion", "target", "result")
            ):
                continue
            flawed = (
                a.get("accepted") is not False
                and row["criterion"] in stopreview.FLAW_CRITERIA
                and row["result"] == stopreview.FAIL
            )
            units[(a["attempt_id"], row["criterion"], row["target"])] = (
                "flawed" if flawed else "clear"
            )
    return units


def _clean_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """独立した評価を整える。形の違う行・不正な truth を破棄し、同じ単位の重複を除く。

    対象と試行 ID は大文字小文字を区別せずに比べる。
    """
    seen: set[tuple[str, str, str]] = set()
    kept = []
    for label in labels:
        values = [label.get(k) for k in ("attempt_id", "criterion", "target")]
        if not all(isinstance(v, str) for v in values) or label.get("truth") not in ("flaw", "ok"):
            continue
        key = (values[0], values[1], pathcase.fold(values[2]))
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(label, target=pathcase.fold(values[2])))
    return kept


def _by_criterion(
    attempts: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    units = {(a, c, pathcase.fold(t)): o for (a, c, t), o in _units(attempts).items()}
    labels = _clean_labels(labels)
    out: dict[str, dict[str, Any]] = {}
    for criterion in stopreview.CRITERIA:
        flagged = sum(
            1 for (_, crit, _t), o in units.items() if crit == criterion and o == "flawed"
        )
        rows = [x for x in labels if x.get("criterion") == criterion]
        flaw_total = missed = ok_total = ok_flagged = labeled_flagged = 0
        for label in rows:
            outcome = units.get((label.get("attempt_id"), criterion, label.get("target")), "missed")
            labeled_flagged += outcome == "flawed"
            if label.get("truth") == "flaw":
                flaw_total += 1
                missed += outcome != "flawed"
            elif label.get("truth") == "ok":
                ok_total += 1
                ok_flagged += outcome == "flawed"
        out[criterion] = {
            "unit": "stop" if criterion in stopreview.STOP_UNIT else "proposal_version",
            "flagged": flagged,
            "labeled": len(rows),
            "miss_rate": _rate(missed, flaw_total),
            "false_block_rate": _rate(ok_flagged, labeled_flagged),
            "needless_rate": _rate(ok_flagged, ok_total),
        }
    return out


def render(summary: dict[str, Any], unlinked: int = 0) -> str:
    """人が読む形。取れない指標は「不明」。

    処理フロー:
    1. pct で割合を小数 1 桁の `%` にする。取れなければ「不明」。
    2. 試行数・検査カバー率・修正率の行を作る。
    3. 基準ごとに、不備検出・評価・見逃し率などの行を足す。
    4. 分けて数える件数（対象外・省略・期限切れなど）の行を足す。
    5. 判定の待ち時間（latency）の行を足す。無ければ「不明」。
    """

    def pct(value: float | None) -> str:
        return "不明" if value is None else f"{value * 100:.1f}%"

    c = summary["counts"]
    lines = [
        f"試行 {summary['attempts']} 件。検査カバー率 {pct(summary['coverage'])}。"
        f"修正率 {pct(summary['fix_rate'])}"
        f"（差し戻し {summary['fix']['sent_back']} 件、解消 {summary['fix']['resolved']}、"
        f"未解消 {summary['fix']['unresolved']}、判定不能 {summary['fix']['inconclusive']}、"
        f"再判定なし {summary['fix']['not_rejudged']}）",
        "基準ごと（Stop の機会: R1・R3 / 提案の版: R2・R4）:",
    ]
    for name, row in summary["criteria"].items():
        lines.append(
            f"  {name}（{row['unit']}）: 不備検出 {row['flagged']} 件、評価 {row['labeled']} 件、"
            f"見逃し率 {pct(row['miss_rate'])}、誤差し戻し率 {pct(row['false_block_rate'])}、"
            f"不要介入率 {pct(row['needless_rate'])}"
        )
    lines.append(
        f"分けて数えるもの: ccnavi の対象外 {c['ccnavi_out_of_scope']}、AI の対象外 "
        f"{c['ai_out_of_scope']}、検査省略 {c['skipped']}、計画なし {c['plan_missing']}、"
        f"期限切れ {c['expired']}、出力不正 {c['rejected']}、未連携 {unlinked}、"
        f"記録外の停止の疑い {summary['suspected_unrecorded_stops']}、汚染 {c['tainted']}"
    )
    wait = summary.get("latency")
    shown = (
        "不明"
        if not wait
        else f"中央値 {wait['median']:.1f} 秒、最大 {wait['max']:.1f} 秒（{wait['n']} 件）"
    )
    lines.append(f"待ち時間（計画の作成から判定の完了まで）: {shown}")
    lines.append("費用: 不明（hook からは取れない。claude -p の total_cost_usd で測る）")
    lines.append(
        f"共有キーを導けなかった Stop {summary['key_failures']['no_key']} 件、"
        f"判定役が計画を待ち切れなかった {summary['key_failures']['plan_timeout']} 件"
    )
    return "\n".join(lines)


def unlinked_total(state: str) -> int:
    """未連携の propose 呼び出しの数（全セッション）。"""
    total = 0
    try:
        names = sorted(fsio.listdir(state)) if state else []
    except OSError:
        return 0
    for name in names:
        if name.startswith("stopops-pre-") and name.endswith(".json"):
            row = fsio.read_dict(os.path.join(state, name))
            if row and row.get("bound") == stopops.UNLINKED:
                total += 1
    return total
