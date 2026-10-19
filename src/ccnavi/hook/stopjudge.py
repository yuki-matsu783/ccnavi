"""Stop の AI 判定の判定役（agent hook）の呼び出しを見分け、ccnavi の各 hook から除く。

判定役は独立したコンテキストで `ccnavi stop-review material|record` を打つ。その呼び出しが、
作業者のものと同じに扱われると、失敗の連続・範囲の読み取り・フェーズの知らせ・実行中の印・
範囲の差し戻しが動いて、既存の挙動が変わる。そこで、見分けられた判定役の呼び出しは次のように扱う。

| hook | 判定役の呼び出し |
|---|---|
| PreToolUse | `stop-review material|record` の Bash だけ通し、ほかは止める |
| PostToolUse・PostToolUseFailure | 何もしない（失敗の連続・範囲・知らせ・報告を動かさない） |
| SubagentStart・SubagentStop | 何もしない（実行中の印を置かず、範囲の検査・差し戻しをしない） |
| Stop | 何もしない |

作業者が `stop-review` を打ったときは止めて、汚染の印を付ける（試行の横取り・偽の記録・
評価の汚染・差し戻しの回避を防ぐ。dry-run では止めず、印だけ付ける）。

見分ける値は `CCNAVI_STOP_REVIEW_JUDGE_TYPE`（判定役の呼び出しの payload の `agent_type`）。
どの値で来るかは実機で確かめるまで分からないので、既定は空で、空のうちは見分けない。
見分けられないまま作業者を止めると判定役自身を止めてしまうので、止めるのも印を付けるのも、
見分ける値が設定されているときだけ。
"""

from __future__ import annotations

import os
import time
from typing import TextIO

from ..infra import fsio, hookio, modes, pathcase, settings, shellread
from ..records import audit, redact

EXIT_OK = modes.EXIT_OK

VERBS = ("material", "record")


def is_judge(conf: settings.Settings, payload: hookio.Input) -> bool:
    """この呼び出しが判定役のものか。見分ける値が設定されていなければ偽。"""
    mark = conf.stop_review_judge.strip().casefold()
    return bool(conf.stop_review_agent and mark and payload.agent_type.strip().casefold() == mark)


def stop_review_call(command: str) -> bool:
    """`ccnavi stop-review material|record …` を打つコマンドか（外側のコマンドのどれか）。"""
    for words, _place in shellread.placed(command):
        for at, word in enumerate(words[:-1]):
            if word != "stop-review" or words[at + 1] not in VERBS or at == 0:
                continue
            prev = os.path.basename(pathcase.fold(words[at - 1]).replace("\\", "/"))
            if prev.startswith("ccnavi"):
                return True
    return False


def _taint_path(state: str, session: str) -> str:
    return os.path.join(state, f"stoptaint-{fsio.safe_name(session)}.json")


def mark_taint(state: str, session: str, command: str, now: float) -> None:
    """作業者が `stop-review` を打った印。時刻とコマンドを残す（コマンドは伏せ字にして保つ）。"""
    row = fsio.read_dict(_taint_path(state, session)) or {"calls": []}
    calls = row["calls"][-19:] + [{"t": now, "command": redact.redact(command)[:300]}]
    fsio.write_json_atomic(_taint_path(state, session), {"calls": calls})


def tainted(state: str, session: str) -> bool:
    return bool((fsio.read_dict(_taint_path(state, session)) or {}).get("calls"))


def before(
    stdout: TextIO,
    mode: str,
    conf: settings.Settings,
    payload: hookio.Input,
    record: audit.Record,
) -> bool:
    """PreToolUse の入口。通常の判定へ回さないなら真（応答は書き終えた）。"""
    if not conf.stop_review_agent or not conf.stop_review_judge.strip():
        return False
    command = payload.tool_input.get("command")
    asks = payload.tool_name == "Bash" and isinstance(command, str) and stop_review_call(command)
    if is_judge(conf, payload):
        record.decision, record.reason = audit.SKIP, "stop-review-judge"
        if asks:
            return True
        hookio.write_verdict(
            stdout,
            hookio.DENY,
            "ccnavi: Stop の判定役が使えるのは `ccnavi stop-review material|record` だけです。",
        )
        record.decision, record.enforced = audit.DENY, True
        return True
    if not asks or payload.agent_id:
        return False
    if conf.state and payload.session_id:
        mark_taint(conf.state, payload.session_id, command, time.time())
    if mode == modes.DRY_RUN:
        return False
    record.decision, record.enforced = audit.DENY, True
    record.reason = "stop-review-worker"
    hookio.write_verdict(
        stdout,
        hookio.DENY,
        "ccnavi: `stop-review` は Stop の判定役が使う入口で、作業では使いません。",
    )
    return True
