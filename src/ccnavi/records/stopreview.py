"""Stop の AI 判定（agent 型 Stop フック）の中核。判定計画・試行・判定結果の検証と最終分類。

このモジュールは LLM を呼ばない。agent hook が出した判定結果を受けて、構造を確かめ、基準ごとの
結果から Stop 全体の最終分類を一意に作り、agent hook がそのまま返す返り値を作る。
最終分類を書くのは `accept` の 1 か所だけ（試行の書き手を 1 つにするため）。

置くもの（state の置き場）:

- `stopreview-plan-<セッション>-<共有キー>.json`: 同じ Stop の判定計画。同じキーへの書き込みは冪等
- `stopreview-attempt-<試行ID>.json`: 試行。状態遷移は `TRANSITIONS` に限る

試行のイベントは `logs/stop-review/events.jsonl` に追記する。`decisions.jsonl` には書かない。

指摘が正しいことは保証しない。検証するのは構造だけ。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from ..infra import fsio, modes

STOP_REVIEW_ENV = "CCNAVI_STOP_REVIEW"

# 試行の状態。
PLAN_WAIT = "plan_wait"  # 計画より先に material が来た
PENDING = "pending"  # 開始待ち
RUNNING = "running"  # material を渡した
DONE = "done"  # record が受理した（最終分類つき）
EXPIRED = "expired"  # 期限切れ（未検査）
SENT_BACK = "sent_back"  # 差し戻しの返り値を出した
CONSUMED = "consumed"  # AI による継続の Stop で消費した
FINAL_STATES = frozenset({DONE, EXPIRED, CONSUMED})

TRANSITIONS: dict[str, frozenset[str]] = {
    PLAN_WAIT: frozenset({PENDING}),
    PENDING: frozenset({RUNNING, EXPIRED}),
    RUNNING: frozenset({DONE, SENT_BACK, EXPIRED}),
    SENT_BACK: frozenset({CONSUMED, EXPIRED}),
    DONE: frozenset(),
    EXPIRED: frozenset(),
    CONSUMED: frozenset(),
}

# 最終分類。
FINAL_OK = "ok"  # 終了可
FINAL_FLAWED = "flawed"  # 不備検出
FINAL_UNCHECKED = "unchecked"  # 未検査
FINAL_OUT_OF_SCOPE = "out_of_scope"  # 対象外
FINAL_SKIPPED = "skipped"  # 検査省略

JUDGE_AI = "ai"
JUDGE_CCNAVI = "ccnavi"

# 判定基準。R3 は停止理由が正当かの判定で、不備検出の根拠にはしない。
R1, R2, R3, R4 = "R1", "R2", "R3", "R4"
CRITERIA = (R1, R2, R3, R4)
FLAW_CRITERIA = (R1, R2, R4)
STOP_UNIT = frozenset({R1, R3})  # Stop の機会を単位にする基準
STOP_TARGET = "stop"

# 基準ごとの結果。
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
RESULTS = frozenset({PASS, FAIL, UNKNOWN})

CLASS_TICKET_WORK = "ticket_work"
CLASS_NOT_TICKET_WORK = "not_ticket_work"

# 問題箇所・修正の種別。
LOCATION_KINDS = frozenset({"final_answer", "proposal", "material"})
FIX_KINDS = frozenset({"write_proposal", "revise_proposal", "send_proposal"})

REWRITE_QUESTION = (
    "本当にユーザの判断が要る問いなら、修正の後にその問いを最後に書き直して終えてください。"
)

# 判定計画を待つ時間、試行の期限。値は成立性の検証の後に決める（暫定）。
DEFAULT_TTL_SECONDS = 600.0


def setting(mode: str, declared: str) -> str:
    """CCNAVI_STOP_REVIEW に全体のモードを掛けた実効値。既定と未知の値は dry-run。"""
    value = (declared or "").strip().lower()
    if value not in (modes.DISABLE, modes.DRY_RUN, modes.ENABLE):
        value = modes.DRY_RUN
    if mode == modes.DISABLE:
        return modes.DISABLE
    return modes.effective_setting(mode, value)


# --- 共有キー ----------------------------------------------------------------

_TAIL_CHUNK = 64 * 1024
_TAIL_LIMIT = 4 * 1024 * 1024


def shared_key(session_id: str, transcript_path: str) -> str:
    """セッション ID と、transcript の末尾にあるサイドチェーンでないアシスタント行の uuid。

    hook・システムの行は除外し、末尾から読む。導けなければ空文字。
    """
    if not session_id or not transcript_path:
        return ""
    uuid = _last_assistant_uuid(transcript_path)
    return f"{session_id}:{uuid}" if uuid else ""


def _last_assistant_uuid(path: str) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            end, tail = size, b""
            while end > 0 and len(tail) < _TAIL_LIMIT:
                start = max(0, end - _TAIL_CHUNK)
                handle.seek(start)
                tail = handle.read(end - start) + tail
                end = start
                lines = tail.split(b"\n")
                # 先頭は途中で切れているかもしれないので、先頭まで読み切るまでは捨てる。
                complete = lines if end == 0 else lines[1:]
                found = _scan(complete)
                if found:
                    return found
    except OSError:
        return ""
    return ""


def _scan(lines: list[bytes]) -> str:
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") != "assistant" or row.get("isSidechain") is True:
            continue
        uuid = row.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
    return ""


# --- 候補条件 ----------------------------------------------------------------


def candidate(
    *,
    main_agent: bool,
    stop_hook_active: bool,
    ai_continuation: bool,
    already_rejudged: bool,
    effective: str,
    tickets_enabled: bool,
    registered: bool,
    existing_nudge: bool,
    target_unresolvable: bool,
    proposal_activity: bool,
    approved_in_progress: bool,
) -> tuple[bool, str]:
    """AI 判定の候補にするか。(候補か, 理由)。理由は計画に残す。"""
    if not main_agent:
        return False, "not_main_agent"
    if stop_hook_active and not (ai_continuation and not already_rejudged):
        return False, "stop_hook_active"
    if effective == modes.DISABLE or not tickets_enabled or not registered:
        return False, "disabled"
    if existing_nudge:
        return False, "existing_nudge"
    if target_unresolvable:
        return False, "target_unresolvable"
    if proposal_activity or not approved_in_progress:
        return True, "candidate"
    return False, "approved_in_progress"


# --- 計画と試行 ----------------------------------------------------------------


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def attempt_id(key: str, rejudge: bool = False) -> str:
    return _digest(key) + ("-r" if rejudge else "")


def _plan_path(state: str, key: str) -> str:
    return os.path.join(state, f"stopreview-plan-{_digest(key)}.json")


def _attempt_path(state: str, aid: str) -> str:
    return os.path.join(state, f"stopreview-attempt-{fsio.safe_name(aid)}.json")


def read_plan(state: str, key: str) -> dict[str, Any] | None:
    return fsio.read_dict(_plan_path(state, key)) if state and key else None


def read_attempt(state: str, aid: str) -> dict[str, Any] | None:
    return fsio.read_dict(_attempt_path(state, aid)) if state and aid else None


def write_plan(state: str, key: str, plan: dict[str, Any], now: float | None = None) -> str:
    """計画を 1 件書き、試行を作る。試行 ID を返す。同じキーへの再呼び出しは冪等。

    候補でない計画は、作成と同時に ccnavi が最終分類（対象外・検査省略）まで確定する。
    計画より先に material が来ていた（plan_wait）なら、その試行を開始待ちへ進める。
    """
    when = time.time() if now is None else now
    rejudge = bool(plan.get("rejudge"))
    aid = attempt_id(key, rejudge)
    if read_plan(state, key) is None:
        body = dict(plan, key=key, attempt_id=aid)
        fsio.write_json_atomic(_plan_path(state, key), body)
    attempt = read_attempt(state, aid)
    if attempt is None:
        attempt = {"attempt_id": aid, "key": key, "created": when, "state": PENDING}
        attempt["original"] = plan.get("original", "")
        if plan.get("is_candidate"):
            _store(state, attempt)
        else:
            attempt["state"] = DONE
            attempt["final"] = plan.get("final") or FINAL_SKIPPED
            attempt["judge"] = JUDGE_CCNAVI
            attempt["reason"] = plan.get("reason", "")
            _store(state, attempt)
            log_event(state, "final", attempt)
    elif attempt["state"] == PLAN_WAIT:
        _move(state, attempt, PENDING)
    return aid


def _store(state: str, attempt: dict[str, Any]) -> None:
    fsio.write_json_atomic(_attempt_path(state, attempt["attempt_id"]), attempt)


def _move(state: str, attempt: dict[str, Any], target: str) -> bool:
    if target not in TRANSITIONS.get(attempt.get("state", ""), frozenset()):
        return False
    attempt["state"] = target
    _store(state, attempt)
    log_event(state, target, attempt)
    return True


def begin(state: str, key: str, now: float | None = None) -> tuple[str, dict[str, Any] | None]:
    """material の入口。(状態, 計画)。計画が無ければ plan_wait を置いて待たせる。

    状態は "candidate" / "not_candidate" / "wait" / "expired"。
    """
    plan = read_plan(state, key)
    if plan is None:
        aid = attempt_id(key)
        if read_attempt(state, aid) is None:
            _store(
                state,
                {
                    "attempt_id": aid,
                    "key": key,
                    "created": time.time() if now is None else now,
                    "state": PLAN_WAIT,
                },
            )
        return "wait", None
    if not plan.get("is_candidate"):
        return "not_candidate", plan
    attempt = read_attempt(state, plan["attempt_id"])
    if attempt is None:
        return "wait", plan
    if attempt["state"] == PENDING:
        _move(state, attempt, RUNNING)
    elif attempt["state"] == EXPIRED:
        return "expired", plan
    return "candidate", plan


def expire_overdue(
    state: str, now: float | None = None, ttl: float = DEFAULT_TTL_SECONDS
) -> list[str]:
    """期限を過ぎて終わっていない試行を、未検査の期限切れで確定する。

    次の hook 起動時と集計時に呼ぶ。
    """
    when = time.time() if now is None else now
    expired: list[str] = []
    for name in fsio.listdir(state) if state else []:
        if not (name.startswith("stopreview-attempt-") and name.endswith(".json")):
            continue
        attempt = fsio.read_dict(os.path.join(state, name))
        if not attempt or attempt.get("state") in FINAL_STATES:
            continue
        if attempt.get("state") == SENT_BACK:
            continue  # 差し戻しは次の Stop で消費する。期限で落とさない。
        if when - float(attempt.get("created", when)) < ttl:
            continue
        attempt["final"] = FINAL_UNCHECKED
        attempt["judge"] = JUDGE_CCNAVI
        attempt["reason"] = "expired"
        if _move(state, attempt, EXPIRED):
            log_event(state, "final", attempt)
            expired.append(attempt["attempt_id"])
    return expired


def consume_continuation(state: str, session_id: str) -> dict[str, Any] | None:
    """次の Stop が stop_hook_active で、同じセッションに差し戻し送出の試行があれば消費して返す。"""
    found: dict[str, Any] | None = None
    for name in fsio.listdir(state) if state else []:
        if not (name.startswith("stopreview-attempt-") and name.endswith(".json")):
            continue
        attempt = fsio.read_dict(os.path.join(state, name))
        if (
            attempt
            and attempt.get("state") == SENT_BACK
            and str(attempt.get("key", "")).startswith(f"{session_id}:")
            and _move(state, attempt, CONSUMED)
        ):
            found = attempt
    return found


def suspected_unrecorded_stop(
    stop_hook_active: bool, previous_nudge: bool, previous_sent_back: bool, reason_has_attempt: bool
) -> bool:
    """記録外の停止の疑い。ほかの hook による継続を含みうる。"""
    return stop_hook_active and not (previous_nudge or previous_sent_back or reason_has_attempt)


# --- record: 検証・最終分類・返り値 ------------------------------------------------

OK_RETURN: dict[str, Any] = {"ok": True}


class Rejected(Exception):
    """判定結果の構造が不正。理由を持つ。"""


def validate(result: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """判定結果の構造を確かめる。指摘が正しいことは確かめない。

    受け取るのは `{"attempt_id", "classification", "results": [{criterion, target, result,
    locations: [{kind, text}], mismatch, fixes: [{kind, text}]}]}`。
    """
    if not isinstance(result, dict):
        raise Rejected("結果がオブジェクトではない")
    if result.get("attempt_id") != plan.get("attempt_id"):
        raise Rejected("試行 ID が計画と合わない")
    classification = result.get("classification")
    if classification not in (CLASS_TICKET_WORK, CLASS_NOT_TICKET_WORK):
        raise Rejected("classification が不正")
    rows = result.get("results")
    if not isinstance(rows, list):
        raise Rejected("results がリストではない")
    targets = set(plan.get("targets") or [])
    seen: set[tuple[str, str]] = set()
    for row in rows:
        _row(row, targets, seen)
    if classification == CLASS_TICKET_WORK:
        for need in required(plan):
            if need not in seen:
                raise Rejected(f"{need[0]} の結果が足りない（対象 {need[1]}）")
    return result


def required(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """計画から決まる、結果が要る (基準, 対象) の一覧。"""
    needs = [(R1, STOP_TARGET), (R3, STOP_TARGET)]
    for target in plan.get("targets") or []:
        if target != STOP_TARGET:
            needs += [(R2, target), (R4, target)]
    return needs


def _row(row: Any, targets: set[str], seen: set[tuple[str, str]]) -> None:
    if not isinstance(row, dict):
        raise Rejected("結果の行がオブジェクトではない")
    criterion, target, outcome = row.get("criterion"), row.get("target"), row.get("result")
    if criterion not in CRITERIA:
        raise Rejected(f"基準 ID が不正: {criterion!r}")
    unit = STOP_TARGET if criterion in STOP_UNIT else target
    if criterion in STOP_UNIT and target != STOP_TARGET:
        raise Rejected(f"{criterion} の対象は {STOP_TARGET}")
    if criterion not in STOP_UNIT and target not in targets:
        raise Rejected(f"{criterion} の対象が計画に無い: {target!r}")
    if outcome not in RESULTS:
        raise Rejected(f"結果が不正: {outcome!r}")
    if (criterion, unit) in seen:
        raise Rejected(f"{criterion} が対象 {unit} で重複")
    seen.add((criterion, unit))
    if outcome == FAIL and criterion in FLAW_CRITERIA:
        _entries(row.get("locations"), LOCATION_KINDS, "問題箇所")
        _entries(row.get("fixes"), FIX_KINDS, "修正")
    elif outcome == FAIL:
        _entries(row.get("locations"), LOCATION_KINDS, "問題箇所")


def _entries(value: Any, kinds: frozenset[str], label: str) -> None:
    if not isinstance(value, list) or not value:
        raise Rejected(f"{label}が無い")
    for item in value:
        if (
            not isinstance(item, dict)
            or item.get("kind") not in kinds
            or not isinstance(item.get("text"), str)
            or not item["text"].strip()
        ):
            raise Rejected(f"{label}の形が不正")


def classify(result: dict[str, Any], plan: dict[str, Any], insufficient: bool = False) -> str:
    """Stop 全体の最終分類。

    1. チケット作業の依頼ではない → 対象外（判定者: AI）
    2. R1・R2・R4 に受理された不備がある → 不備検出
    3. 必要な基準に判定不能がある、または材料不足 → 未検査
    4. それ以外 → 終了可
    """
    if result["classification"] == CLASS_NOT_TICKET_WORK:
        return FINAL_OUT_OF_SCOPE
    rows = {(r["criterion"], r["target"]): r for r in result["results"]}
    if any(r["result"] == FAIL for r in rows.values() if r["criterion"] in FLAW_CRITERIA):
        return FINAL_FLAWED
    if insufficient or any(rows[need]["result"] == UNKNOWN for need in required(plan)):
        return FINAL_UNCHECKED
    return FINAL_OK


def rebuke(aid: str, result: dict[str, Any]) -> str:
    """差し戻しの文。試行 ID、基準 ID・問題箇所・修正、問いを書き直す一文を必ず含める。"""
    lines = [f"Stop の AI 判定（試行 {aid}）が提案の不備を見つけました。直してから終えてください。"]
    for row in result["results"]:
        if row["criterion"] not in FLAW_CRITERIA or row["result"] != FAIL:
            continue
        lines.append(f"- {row['criterion']}（対象 {row['target']}）")
        for item in row["locations"]:
            lines.append(f"  - 問題箇所（{item['kind']}）: {item['text'].strip()}")
        mismatch = row.get("mismatch")
        if isinstance(mismatch, str) and mismatch.strip():
            lines.append(f"  - 依頼との不一致: {mismatch.strip()}")
        for item in row["fixes"]:
            lines.append(f"  - 修正（{item['kind']}）: {item['text'].strip()}")
    lines.append(REWRITE_QUESTION)
    return "\n".join(lines)


def accept(
    state: str,
    key: str,
    result: Any,
    *,
    effective: str,
    material_version: str,
    still_unapproved: Any = None,
    insufficient: bool = False,
) -> dict[str, Any]:
    """record の本体。受け取った結果を検証し、最終分類を確定して、agent hook の返り値を返す。

    拒否したときは未検査で閉じて `{"ok": true}`。受理して enable で不備検出のときだけ差し戻す。
    再判定の試行は差し戻さず、元の試行に解消か未解消を残す。
    `still_unapproved(target) -> bool` は、承認状態の再確認（承認済みになっていたら材料が変わった
    として拒否する）。
    """
    plan = read_plan(state, key)
    attempt = read_attempt(state, plan["attempt_id"]) if plan else None
    if not plan or not attempt or attempt.get("state") != RUNNING:
        return dict(OK_RETURN)
    try:
        if plan.get("material_version") != material_version:
            raise Rejected("材料の版が変わった")
        valid = validate(result, plan)
        if still_unapproved is not None:
            for target in plan.get("targets") or []:
                if target != STOP_TARGET and not still_unapproved(target):
                    raise Rejected("承認状態が変わった")
    except Rejected as why:
        attempt.update(final=FINAL_UNCHECKED, judge=JUDGE_CCNAVI, reason=f"rejected: {why}")
        attempt["accepted"] = False
        if _move(state, attempt, DONE):
            log_event(state, "final", attempt)
        return dict(OK_RETURN)

    final = classify(valid, plan, insufficient)
    attempt.update(
        final=final,
        judge=JUDGE_AI,
        accepted=True,
        results=valid["results"],
        effective=effective,
    )
    sends_back = final == FINAL_FLAWED and effective == modes.ENABLE and not plan.get("rejudge")
    target_state = SENT_BACK if sends_back else DONE
    _move(state, attempt, target_state)
    log_event(state, "final", attempt)
    if plan.get("rejudge") and attempt.get("original"):
        _resolve_original(state, attempt["original"], final != FINAL_FLAWED)
    if sends_back:
        return {"ok": False, "reason": rebuke(attempt["attempt_id"], valid)}
    return dict(OK_RETURN)


def _resolve_original(state: str, original: str, resolved: bool) -> None:
    attempt = read_attempt(state, original)
    if attempt is not None:
        attempt["rejudge"] = "resolved" if resolved else "unresolved"
        _store(state, attempt)
        log_event(state, "rejudge", attempt)


# --- 専用の記録 --------------------------------------------------------------------


def log_path(log_dir: str) -> str:
    return os.path.join(log_dir, "stop-review", "events.jsonl")


_LOG_STATE: dict[str, str] = {}


def configure_log(state: str, log_dir: str) -> None:
    """試行のイベントを書く先を、state の置き場ごとに結ぶ。呼ばなければ書かない。"""
    if state and log_dir:
        _LOG_STATE[state] = log_dir


def log_event(state: str, event: str, attempt: dict[str, Any]) -> None:
    """試行のイベントを専用の記録に 1 行足す。書けなくても判定は止めない。"""
    log_dir = _LOG_STATE.get(state)
    if not log_dir:
        return
    row = {
        "at": fsio.stamp(),
        "event": event,
        "attempt_id": attempt.get("attempt_id"),
        "state": attempt.get("state"),
        "final": attempt.get("final"),
        "judge": attempt.get("judge"),
        "reason": attempt.get("reason"),
    }
    try:
        path = log_path(log_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
